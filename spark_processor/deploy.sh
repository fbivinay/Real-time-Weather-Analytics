#!/usr/bin/env bash
# Deploys the in-cluster Spark Structured Streaming processor.
#   ./deploy.sh              # node IP from terraform output
#   ./deploy.sh 1.2.3.4      # explicit node IP
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/infra/lib.sh" "$@"
TF=(terraform -chdir="$REPO_ROOT/infra")
S3_BUCKET="$("${TF[@]}" output -raw s3_bucket_name)"

echo "== Node $NODE_IP, bucket $S3_BUCKET: processor =="

echo "== AWS credentials Secret =="
# The secret reaches kubectl on stdin, never in argv: --from-file=<key>=
# /dev/stdin keeps it out of the local process list, and piping the rendered
# manifest over SSH keeps it out of the remote one. Both halves matter -
# argv is world-readable via `ps` on a shared machine.
ACCESS_KEY_ID="$("${TF[@]}" output -raw databricks_s3_access_key_id)"
{
  printf '%s' "$("${TF[@]}" output -raw databricks_s3_secret_key)" \
    | kubectl create secret generic aws-s3-creds \
        --namespace "$NAMESPACE" \
        --from-literal=access-key-id="$ACCESS_KEY_ID" \
        --from-file=secret-access-key=/dev/stdin \
        --dry-run=client -o yaml
} | "${SSH[@]}" "$KUBECTL apply -f -"

echo "== Source ConfigMaps =="
push_configmap weatherops-lib weatherops
push_configmap weather-processor-code spark_processor

echo "== Deployment + PVC =="
sed "s|weather-pipeline-021448121627|$S3_BUCKET|" "$REPO_ROOT/spark_processor/k8s-deployment.yaml" \
  | "${SSH[@]}" "$KUBECTL apply -f -"
kube rollout restart deployment/weather-processor -n "$NAMESPACE"

echo "First start downloads connector jars (~2-3 min). Watch:"
echo "  ssh -i $SSH_KEY ubuntu@$NODE_IP \"$KUBECTL logs -n $NAMESPACE -l app=weather-processor -f\""
