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
# The secret never appears in any process's argv (world-readable via `ps`):
# terraform writes it to a pipe, base64 reads the pipe, and the manifest
# reaches the node's kubectl on SSH stdin. Built by hand rather than with
# `kubectl create --from-file=/dev/stdin`, which Windows kubectl cannot read.
ACCESS_KEY_B64="$("${TF[@]}" output -raw databricks_s3_access_key_id | base64 | tr -d '\n')"
SECRET_KEY_B64="$("${TF[@]}" output -raw databricks_s3_secret_key | base64 | tr -d '\n')"
"${SSH[@]}" "$KUBECTL apply -f -" <<MANIFEST
apiVersion: v1
kind: Secret
metadata:
  name: aws-s3-creds
  namespace: $NAMESPACE
type: Opaque
data:
  access-key-id: $ACCESS_KEY_B64
  secret-access-key: $SECRET_KEY_B64
MANIFEST
unset ACCESS_KEY_B64 SECRET_KEY_B64

echo "== Source ConfigMaps =="
push_configmap weatherops-lib weatherops
push_configmap weather-processor-code spark_processor

echo "== Deployment + PVC =="
sed "s|weather-pipeline-021448121627|$S3_BUCKET|" "$REPO_ROOT/spark_processor/k8s-deployment.yaml" \
  | "${SSH[@]}" "$KUBECTL apply -f -"
kube rollout restart deployment/weather-processor -n "$NAMESPACE"

echo "First start downloads connector jars (~2-3 min). Watch:"
echo "  ssh -i $SSH_KEY ubuntu@$NODE_IP \"$KUBECTL logs -n $NAMESPACE -l app=weather-processor -f\""
