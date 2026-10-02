#!/usr/bin/env bash
# Deploys the serving layer: the operations engine and the REST/WebSocket API.
#   ./deploy.sh              # node IP from terraform output
#   ./deploy.sh 1.2.3.4      # explicit node IP
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/infra/lib.sh" "$@"

echo "== Node $NODE_IP: engine + API =="
ship_data
push_configmap weatherops-lib weatherops
push_configmap serving-code serving
apply_manifest "$REPO_ROOT/serving/k8s-deployment.yaml"
kube rollout restart deployment/weather-engine deployment/weather-api -n "$NAMESPACE"
