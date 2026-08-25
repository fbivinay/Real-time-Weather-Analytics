#!/usr/bin/env bash
set -euo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

NAMESPACE=weather-pipeline
ELASTIC_IP="${1:?Usage: bootstrap.sh <elastic-ip>}"
CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/helm" && pwd)"

kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || kubectl create namespace "$NAMESPACE"

helm repo add bitnami https://charts.bitnami.com/bitnami >/dev/null 2>&1 || true
helm repo update >/dev/null

echo "== Installing Kafka =="
helm upgrade --install kafka bitnami/kafka \
  --namespace "$NAMESPACE" \
  -f "$CHART_DIR/kafka-values.yaml" \
  --set externalAccess.controller.service.advertisedHost.controller-0="${ELASTIC_IP}" \
  --wait --timeout 10m

echo "== Setting up Redis auth secret =="
if ! kubectl get secret redis-auth -n "$NAMESPACE" >/dev/null 2>&1; then
  REDIS_PASSWORD=$(openssl rand -base64 24)
  kubectl create secret generic redis-auth \
    --namespace "$NAMESPACE" \
    --from-literal=redis-password="$REDIS_PASSWORD"
  echo "Generated new redis-auth secret."
else
  echo "redis-auth secret already exists, reusing it."
fi

echo "== Installing Redis =="
helm upgrade --install redis bitnami/redis \
  --namespace "$NAMESPACE" \
  -f "$CHART_DIR/redis-values.yaml" \
  --set auth.existingSecret=redis-auth \
  --set auth.existingSecretPasswordKey=redis-password \
  --wait --timeout 10m

echo "== Pod status =="
kubectl get pods -n "$NAMESPACE"

echo "== In-cluster Kafka verification =="
TOPIC="infra-verify-incluster-$(date +%s)"
kubectl run kafka-verify --rm --restart=Never \
  --namespace "$NAMESPACE" \
  --image docker.io/bitnami/kafka:3.7 \
  --command -- bash -c "
    echo hello-from-cluster | kafka-console-producer.sh --bootstrap-server kafka.${NAMESPACE}.svc.cluster.local:9092 --topic ${TOPIC} &&
    kafka-console-consumer.sh --bootstrap-server kafka.${NAMESPACE}.svc.cluster.local:9092 --topic ${TOPIC} --from-beginning --max-messages 1 --timeout-ms 15000
  "

echo "Bootstrap complete."
