#!/usr/bin/env bash
# Brings the whole pipeline up from nothing, in dependency order.
#
#   ./deploy.sh            # full bring-up
#   ./deploy.sh --status   # what is running right now
#   ./deploy.sh --destroy  # tear it all down and stop the billing
#
# Every step is idempotent, so re-running after a failure resumes rather than
# duplicating. This script exists because rebuilding by hand after an
# accidental teardown took 15 minutes of remembering which order things go in.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/weather-pipeline}"

# A function, not a string: the repo path contains spaces, and an unquoted
# "$TF" would word-split it into a bogus -chdir target.
tf() { terraform -chdir="$REPO_ROOT/infra" "$@"; }

say() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

node_ip() { tf output -raw public_ip; }
ssh_node() { ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "ubuntu@$(node_ip)" "$@"; }
kube() { ssh_node "sudo kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml $*"; }

status() {
  say "Terraform"
  tf output
  say "Pods"
  kube get pods -n weather-pipeline
  say "API"
  curl -sf --max-time 10 "$(tf output -raw api_url)/api/stats" && echo || echo "API not reachable"
}

destroy() {
  say "Destroying all AWS resources"
  echo "This removes the EC2 node, Elastic IP, S3 bucket and IAM user."
  tf destroy
}

bring_up() {
  say "1/6  Provisioning AWS"
  # Review before applying: an AMI or user-data change replaces the node and
  # takes the whole cluster with it. That has happened once already.
  tf apply

  local ip
  ip="$(node_ip)"
  echo "Node: $ip"

  say "2/6  Waiting for k3s (user-data marker)"
  until ssh_node 'test -f /home/ubuntu/user-data-complete' 2>/dev/null; do
    echo "  ...still installing"
    sleep 15
  done
  kube get nodes

  say "3/6  Kafka + Redis"
  if kube get statefulset kafka-controller -n weather-pipeline >/dev/null 2>&1; then
    echo "Already installed, skipping bootstrap."
  else
    scp -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -r \
      "$REPO_ROOT/infra/helm" "$REPO_ROOT/infra/bootstrap.sh" "ubuntu@$ip:~/"
    # Git for Windows may still hand over CRLF despite .gitattributes if the
    # file was checked out before it existed; stripping is cheap insurance.
    ssh_node "sed -i 's/\r\$//' bootstrap.sh && chmod +x bootstrap.sh && ./bootstrap.sh $ip"
  fi

  say "4/6  Producer"
  scp -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new \
    "$REPO_ROOT/producer/k8s-deployment.yaml" "ubuntu@$ip:~/producer-deploy.yaml"
  kube apply -f '~/producer-deploy.yaml'

  say "5/6  Spark processor"
  bash "$REPO_ROOT/spark_processor/deploy.sh" "$ip"

  say "6/6  Serving layer"
  bash "$REPO_ROOT/serving/deploy.sh" "$ip"

  say "Up"
  cat <<EOF
Kafka (external)  $(tf output -raw kafka_bootstrap)
API               $(tf output -raw api_url)/api/stations
S3 bucket         $(tf output -raw s3_bucket_name)

Pods take a couple of minutes to settle - Spark pulls connector jars and the
serving containers pip-install on start. Check with:
  ./deploy.sh --status

The dashboard reads the API above; deploy it with:
  cd dashboard && vercel deploy --prod

Remember to ./deploy.sh --destroy when you are done - the node bills by the hour.
EOF
}

case "${1:-}" in
  --status)  status ;;
  --destroy) destroy ;;
  "")        bring_up ;;
  *)         echo "Usage: $0 [--status|--destroy]" >&2; exit 1 ;;
esac
