#!/usr/bin/env bash
# Deploys PostgreSQL. The password is generated on the node into a file and
# loaded into the pg-auth Secret from there: it never crosses SSH or argv.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/infra/lib.sh" "$@"

echo "== Node $NODE_IP: postgres =="
"${SSH[@]}" "$KUBECTL get secret pg-auth -n $NAMESPACE >/dev/null 2>&1 || {
  umask 077; openssl rand -hex 24 | tr -d '\n' > /tmp/pg-pass
  $KUBECTL create secret generic pg-auth -n $NAMESPACE --from-file=password=/tmp/pg-pass
  rm -f /tmp/pg-pass; }"
apply_manifest "$REPO_ROOT/db/k8s.yaml"
kube rollout status statefulset/postgres -n "$NAMESPACE" --timeout=300s
