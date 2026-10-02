#!/usr/bin/env bash
# Runs the seed Job (idempotent: a seeded database is left alone unless
# SEED_FORCE=1 is set in the Job). Waits for it to finish.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/infra/lib.sh" "$@"

echo "== Node $NODE_IP: seed =="
ship_data
push_configmap weatherops-lib weatherops
push_configmap seed-code seed
push_configmap db-schema db
kube delete job weatherops-seed -n "$NAMESPACE" --ignore-not-found
apply_manifest "$REPO_ROOT/seed/k8s-job.yaml"
kube wait --for=condition=complete job/weatherops-seed -n "$NAMESPACE" --timeout=900s
kube logs job/weatherops-seed -n "$NAMESPACE" --tail=5
