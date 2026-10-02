#!/usr/bin/env bash
# Deploys the ShopFlow operations simulator.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/infra/lib.sh" "$@"

echo "== Node $NODE_IP: simulator =="
ship_data
push_configmap weatherops-lib weatherops
push_configmap simulator-code simulator
apply_manifest "$REPO_ROOT/simulator/k8s-deployment.yaml"
kube rollout restart deployment/shopflow-simulator -n "$NAMESPACE"
