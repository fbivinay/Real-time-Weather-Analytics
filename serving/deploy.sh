#!/usr/bin/env bash
# Deploys the serving layer: the risk engine and the REST/WebSocket API.
#   ./deploy.sh              # node IP from terraform output
#   ./deploy.sh 1.2.3.4      # explicit node IP
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/infra/lib.sh" "$@"

echo "== Node $NODE_IP: engine + API =="
push_configmap weatherops-lib weatherops
push_configmap serving-code serving
if [ -d "$REPO_ROOT/ml/artifacts" ]; then
  push_configmap engine-models ml/artifacts
fi

# The pre-WeatherOps consumer and its ConfigMap.
kube delete deployment weather-consumer -n "$NAMESPACE" --ignore-not-found
kube delete configmap weather-serving-code -n "$NAMESPACE" --ignore-not-found

apply_manifest "$REPO_ROOT/serving/k8s-deployment.yaml"
kube rollout restart deployment/weather-engine deployment/weather-api -n "$NAMESPACE"
