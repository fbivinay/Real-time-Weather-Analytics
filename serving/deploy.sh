#!/usr/bin/env bash
# Deploys the serving layer: Kafka -> Redis consumer, and the read-only API.
#   ./deploy.sh              # node IP from terraform output
#   ./deploy.sh 1.2.3.4      # explicit node IP
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DIR/.." && pwd)"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/weather-pipeline}"

NODE_IP="${1:-$(terraform -chdir="$REPO_ROOT/infra" output -raw public_ip)}"

# accept-new trusts a first-time host but refuses a changed key; see the note
# in spark_processor/deploy.sh.
SSH=(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "ubuntu@$NODE_IP")
KUBECTL="sudo kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml"

echo "== Node $NODE_IP =="

echo "== Source ConfigMap =="
tar -C "$REPO_ROOT" -cf - serving/consumer.py serving/api.py \
  | "${SSH[@]}" "rm -rf ~/serving-src && mkdir -p ~/serving-src && tar -C ~/serving-src -xf -"
"${SSH[@]}" "$KUBECTL create configmap weather-serving-code \
    --namespace weather-pipeline \
    --from-file=consumer.py=/home/ubuntu/serving-src/serving/consumer.py \
    --from-file=api.py=/home/ubuntu/serving-src/serving/api.py \
    --dry-run=client -o yaml | $KUBECTL apply -f -"

echo "== Deployments + Service =="
"${SSH[@]}" "$KUBECTL apply -f -" < "$DIR/k8s-deployment.yaml"

echo "== Restart to pick up the current code =="
"${SSH[@]}" "$KUBECTL rollout restart deployment/weather-consumer deployment/weather-api -n weather-pipeline"

echo
echo "Deployed. Containers pip-install on start (~30s). Then:"
echo "  curl http://$NODE_IP:30080/api/stations"
