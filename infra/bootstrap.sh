#!/usr/bin/env bash
set -euo pipefail
export KUBECONFIG="$HOME/.kube/config"

NAMESPACE=weather-pipeline
ELASTIC_IP="${1:?Usage: bootstrap.sh <elastic-ip>}"
CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/helm" && pwd)"

kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || kubectl create namespace "$NAMESPACE"

helm repo add bitnami https://charts.bitnami.com/bitnami >/dev/null 2>&1 || true
helm repo update >/dev/null

echo "== Installing Kafka =="
helm upgrade --install kafka bitnami/kafka \
  --namespace "$NAMESPACE" \
  --version 32.4.3 \
  -f "$CHART_DIR/kafka-values.yaml" \
  --set externalAccess.controller.service.domain="${ELASTIC_IP}" \
  --wait --timeout 10m

echo "== Verifying Kafka advertised listener =="
kubectl exec kafka-controller-0 -n "$NAMESPACE" -- grep -q "${ELASTIC_IP}" /opt/bitnami/kafka/config/server.properties \
  && echo "Advertised listener correctly contains ${ELASTIC_IP}" \
  || { echo "ERROR: advertised listener does not contain ${ELASTIC_IP}" >&2; exit 1; }

echo "== Setting up Redis auth secret =="
if ! kubectl get secret redis-auth -n "$NAMESPACE" >/dev/null 2>&1; then
  REDIS_PASSWORD=$(openssl rand -base64 24)
  kubectl apply -n "$NAMESPACE" -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: redis-auth
type: Opaque
stringData:
  redis-password: ${REDIS_PASSWORD}
EOF
  echo "Generated new redis-auth secret."
else
  echo "redis-auth secret already exists, reusing it."
fi

echo "== Installing Redis =="
helm upgrade --install redis bitnami/redis \
  --namespace "$NAMESPACE" \
  --version 28.0.10 \
  -f "$CHART_DIR/redis-values.yaml" \
  --set auth.existingSecret=redis-auth \
  --set auth.existingSecretPasswordKey=redis-password \
  --wait --timeout 10m

echo "== Installing cert-manager =="
helm repo add jetstack https://charts.jetstack.io >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install cert-manager jetstack/cert-manager   --namespace cert-manager --create-namespace   --version v1.18.2   --set crds.enabled=true   --wait --timeout 10m
kubectl apply -f "$CHART_DIR/../k8s/cluster-issuer.yaml"

echo "== Pod status =="
kubectl get pods -n "$NAMESPACE"

echo "== In-cluster Kafka verification =="
TOPIC="infra-verify-incluster-$(date +%s)"
kubectl exec kafka-controller-0 -n "$NAMESPACE" -- bash -c "
  echo hello-from-cluster | kafka-console-producer.sh --bootstrap-server kafka.${NAMESPACE}.svc.cluster.local:9092 --topic ${TOPIC} &&
  kafka-console-consumer.sh --bootstrap-server kafka.${NAMESPACE}.svc.cluster.local:9092 --topic ${TOPIC} --from-beginning --max-messages 1 --timeout-ms 15000
"

echo "Bootstrap complete."
