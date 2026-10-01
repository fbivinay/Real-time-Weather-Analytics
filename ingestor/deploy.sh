#!/usr/bin/env bash
# Deploys the ingestor: shared weatherops package + ingestor source as
# ConfigMaps, then the Deployment.
#   ./deploy.sh              # node IP from terraform output
#   ./deploy.sh 1.2.3.4      # explicit node IP
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/infra/lib.sh" "$@"

echo "== Node $NODE_IP: ingestor =="
push_configmap weatherops-lib weatherops
push_configmap ingestor-code ingestor
apply_manifest "$REPO_ROOT/ingestor/k8s-deployment.yaml"
kube rollout restart deployment/weather-ingestor -n "$NAMESPACE"
