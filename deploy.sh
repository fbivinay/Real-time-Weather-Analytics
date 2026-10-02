#!/usr/bin/env bash
# WeatherOps on one on-demand k3s node.
#
#   ./deploy.sh                       bring everything up (node, Kafka, Redis, cert-manager, apps)
#   ./deploy.sh --apps                redeploy application code only
#   ./deploy.sh --live                ingestor on real Open-Meteo data (default)
#   ./deploy.sh --replay <event> [speed]   replay a historical event, e.g. michaung-2023
#   ./deploy.sh --sim <scenario> [speed]   synthetic scenario, e.g. storm-chennai
#   ./deploy.sh --tunnel              forward the API to http://localhost:8000 over SSH
#   ./deploy.sh --status              what is running
#   ./deploy.sh --down                stop billing for the node; keep IP, S3, IAM
#   ./deploy.sh --destroy             remove everything (S3 must be emptied first)
#
# WEATHEROPS_HOST=<name>.duckdns.org enables the TLS ingress the dashboard uses.
# TF_AUTO_APPROVE=1 skips Terraform's confirmation prompt (unattended runs).
# Every step is idempotent: re-running after a failure resumes.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/weather-pipeline}"
NS=weather-pipeline
TLS_BACKUP="$HOME/.weatherops/tls-secret.json"

# A function, not a string: the repo path contains spaces.
tf() { terraform -chdir="$REPO_ROOT/infra" "$@"; }
tf_apply() { tf apply ${TF_AUTO_APPROVE:+-auto-approve} "$@"; }
say() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
node_ip() { tf output -raw public_ip; }
ssh_node() { ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "ubuntu@$(node_ip)" "$@"; }
kube() { ssh_node "sudo kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml $*"; }

backup_tls() {
  # Let's Encrypt allows 5 certificates per exact name per week; an on-demand
  # node that re-issued on every bring-up would hit that. Keep the issued
  # certificate (private key included - hence 600) outside the repo. Server
  # fields are stripped on the node so the file applies to a fresh cluster.
  mkdir -p "$(dirname "$TLS_BACKUP")"
  if ssh_node "sudo kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml get secret weatherops-tls -n $NS -o json       > /tmp/tls.json 2>/dev/null && python3 - /tmp/tls.json; rc=\$?; rm -f /tmp/tls.json; exit \$rc"       >"$TLS_BACKUP.tmp" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
for k in ("uid", "resourceVersion", "creationTimestamp", "managedFields", "ownerReferences"):
    s["metadata"].pop(k, None)
print(json.dumps(s))
PY
  then
    mv "$TLS_BACKUP.tmp" "$TLS_BACKUP"
    chmod 600 "$TLS_BACKUP"
    echo "TLS certificate saved to $TLS_BACKUP"
  else
    rm -f "$TLS_BACKUP.tmp"
    echo "No TLS certificate to save."
  fi
}

restore_tls() {
  if [ -f "$TLS_BACKUP" ]; then
    ssh_node "sudo kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml apply -f -" <"$TLS_BACKUP"
    echo "Restored TLS certificate from $TLS_BACKUP"
  fi
}

ingress() {
  if [ -z "${WEATHEROPS_HOST:-}" ]; then
    echo "WEATHEROPS_HOST not set: no public HTTPS endpoint. Use ./deploy.sh --tunnel."
    return
  fi
  sed "s|WEATHEROPS_HOST|$WEATHEROPS_HOST|g" "$REPO_ROOT/serving/ingress.yaml" \
    | ssh_node "sudo kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml apply -f -"
}

apps() {
  local ip
  ip="$(node_ip)"
  say "Ingestor"
  kube delete deployment weather-generator -n "$NS" --ignore-not-found
  bash "$REPO_ROOT/ingestor/deploy.sh" "$ip"
  say "Spark processor"
  bash "$REPO_ROOT/spark_processor/deploy.sh" "$ip"
  say "Engine + API"
  bash "$REPO_ROOT/serving/deploy.sh" "$ip"
  say "Ingress"
  ingress
}

topics() {
  # Created here, once, so every app can start in any order.
  for topic in weather-data weather-quarantine weather-features weather-decisions; do
    kube exec kafka-controller-0 -n "$NS" -- kafka-topics.sh \
      --bootstrap-server localhost:9092 --create --if-not-exists --topic "$topic" \
      --partitions 1 --replication-factor 1 --config retention.ms=86400000
  done
}

bring_up() {
  say "1/5  Node (Terraform)"
  # Review before applying: an AMI or user-data change replaces the node.
  tf_apply -var node_enabled=true
  local ip
  ip="$(node_ip)"
  echo "Node: $ip"

  say "2/5  Waiting for k3s"
  # A node rebuilt behind the same Elastic IP has a new host key, which
  # accept-new rightly refuses. Forget the old key only on that exact
  # mismatch - right after Terraform recreated the node - never blindly.
  local out
  until out=$(ssh_node 'test -f /home/ubuntu/user-data-complete && echo ready' 2>&1) && [ "$out" = ready ]; do
    if grep -q "HOST IDENTIFICATION HAS CHANGED" <<<"$out"; then
      echo "  node was rebuilt: replacing its old SSH host key"
      ssh-keygen -R "$ip" >/dev/null 2>&1
    else
      echo "  ...still installing"
      sleep 15
    fi
  done

  say "3/5  Kafka, Redis, cert-manager"
  if kube get statefulset kafka-controller -n "$NS" >/dev/null 2>&1 \
     && kube get deployment cert-manager -n cert-manager >/dev/null 2>&1; then
    echo "Already installed."
  else
    scp -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -r \
      "$REPO_ROOT/infra/helm" "$REPO_ROOT/infra/k8s" "$REPO_ROOT/infra/bootstrap.sh" "ubuntu@$ip:~/"
    # Git for Windows may hand over CRLF despite .gitattributes; strip it.
    ssh_node "sed -i 's/\r\$//' bootstrap.sh && chmod +x bootstrap.sh && ./bootstrap.sh $ip"
  fi

  say "4/5  Topics + TLS certificate"
  topics
  restore_tls

  say "5/5  Applications"
  apps

  say "Up"
  cat <<EOF
Node      $ip   (ssh: $(tf output -raw ssh_command))
API       $([ -n "${WEATHEROPS_HOST:-}" ] && echo "https://$WEATHEROPS_HOST/api/snapshot" || echo "not public - ./deploy.sh --tunnel")
S3        $(tf output -raw s3_bucket_name)

Pods settle in 2-3 minutes (Spark downloads connector jars; Python pods
pip-install on start). Then:  ./deploy.sh --status
Switch data:  ./deploy.sh --replay michaung-2023   |   --sim storm-chennai   |   --live
Stop billing: ./deploy.sh --down
EOF
}

set_mode() {
  local mode=$1 scenario=${2:-} speed=${3:-}
  kube set env deployment/weather-ingestor -n "$NS" MODE="$mode" SCENARIO="$scenario" REPLAY_SPEED="$speed"
  echo "Ingestor switched to $mode ${scenario}${speed:+ at ${speed}x}; the engine resets when the new data arrives."
}

status() {
  say "Terraform"
  tf output
  say "Pods"
  kube get pods -A -o wide | grep -E "NAMESPACE|$NS|cert-manager" || true
  say "Health"
  if [ -n "${WEATHEROPS_HOST:-}" ]; then
    curl -sf --max-time 10 "https://$WEATHEROPS_HOST/api/health" && echo || echo "API not reachable over HTTPS"
  else
    kube exec deploy/weather-api -n "$NS" -- python -c \
      "\"import urllib.request; print(urllib.request.urlopen('http://localhost:8000/api/health').read().decode())\"" \
      || echo "API not reachable"
  fi
}

tunnel() {
  local cluster_ip
  cluster_ip="$(kube get svc weather-api -n "$NS" -o jsonpath='{.spec.clusterIP}')"
  echo "API on http://localhost:8000 (Ctrl-C to stop)"
  ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -N -L "8000:$cluster_ip:8000" "ubuntu@$(node_ip)"
}

down() {
  say "Saving TLS certificate"
  backup_tls || true
  say "Destroying the node (Elastic IP, S3 and IAM stay)"
  tf_apply -var node_enabled=false
}

destroy() {
  say "Destroying all AWS resources"
  echo "This removes the node, Elastic IP, S3 bucket and IAM user. Terraform"
  echo "refuses to delete a non-empty bucket: empty it first if you mean it:"
  echo "  aws s3 rm s3://$(tf output -raw s3_bucket_name) --recursive"
  tf destroy
}

case "${1:-}" in
  "")        bring_up ;;
  --apps)    apps ;;
  --live)    set_mode live ;;
  --replay)  set_mode replay "${2:?event id, e.g. michaung-2023}" "${3:-}" ;;
  --sim)     set_mode sim "${2:?scenario id, e.g. storm-chennai}" "${3:-}" ;;
  --tunnel)  tunnel ;;
  --status)  status ;;
  --down)    down ;;
  --destroy) destroy ;;
  *)         sed -n '2,15p' "$0" >&2; exit 1 ;;
esac
