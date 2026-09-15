#!/usr/bin/env bash
# Deploys the in-cluster Spark Structured Streaming processor.
#   ./deploy.sh              # node IP from terraform output
#   ./deploy.sh 1.2.3.4      # explicit node IP
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DIR/.." && pwd)"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/weather-pipeline}"

NODE_IP="${1:-$(terraform -chdir="$REPO_ROOT/infra" output -raw public_ip)}"
S3_BUCKET="$(terraform -chdir="$REPO_ROOT/infra" output -raw s3_bucket_name)"

# accept-new, not `no`: a first-time host is trusted, but a CHANGED host key
# aborts. This script pipes an AWS secret over the connection, and
# StrictHostKeyChecking=no would hand that secret to anyone able to answer on
# this address. Recreating the node legitimately changes the key - clear the
# stale entry deliberately with `ssh-keygen -R <ip>` rather than disabling
# the check.
SSH=(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "ubuntu@$NODE_IP")
KUBECTL="sudo kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml"

echo "== Node $NODE_IP, bucket $S3_BUCKET =="

echo "== AWS credentials Secret =="
# The secret reaches kubectl on stdin, never in argv: --from-file=<key>=
# /dev/stdin keeps it out of the local process list, and piping the rendered
# manifest over SSH keeps it out of the remote one. Both halves matter -
# argv is world-readable via `ps` on a shared machine.
ACCESS_KEY_ID="$(terraform -chdir="$REPO_ROOT/infra" output -raw databricks_s3_access_key_id)"

{
  printf '%s' "$(terraform -chdir="$REPO_ROOT/infra" output -raw databricks_s3_secret_key)" \
    | kubectl create secret generic aws-s3-creds \
        --namespace weather-pipeline \
        --from-literal=access-key-id="$ACCESS_KEY_ID" \
        --from-file=secret-access-key=/dev/stdin \
        --dry-run=client -o yaml
} | "${SSH[@]}" "$KUBECTL apply -f -"

echo "== Job source ConfigMap =="
# Built from the real .py files, so they stay the single source of truth.
tar -C "$REPO_ROOT" -cf - spark_processor/streaming_job.py databricks/transforms.py \
  | "${SSH[@]}" "rm -rf ~/processor-src && mkdir -p ~/processor-src && tar -C ~/processor-src -xf -"
"${SSH[@]}" "$KUBECTL create configmap weather-processor-code \
    --namespace weather-pipeline \
    --from-file=streaming_job.py=/home/ubuntu/processor-src/spark_processor/streaming_job.py \
    --from-file=transforms.py=/home/ubuntu/processor-src/databricks/transforms.py \
    --dry-run=client -o yaml | $KUBECTL apply -f -"

echo "== Deployment + PVC =="
sed "s|weather-pipeline-021448121627|$S3_BUCKET|" "$DIR/k8s-deployment.yaml" \
  | "${SSH[@]}" "$KUBECTL apply -f -"

echo "== Restart to pick up the current code =="
"${SSH[@]}" "$KUBECTL rollout restart deployment/weather-processor -n weather-pipeline"

echo
echo "Deployed. First start downloads connector jars (~2-3 min). Watch:"
echo "  ssh -i $SSH_KEY ubuntu@$NODE_IP \"$KUBECTL logs -n weather-pipeline -l app=weather-processor -f\""
