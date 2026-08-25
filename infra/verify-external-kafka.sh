#!/usr/bin/env bash
set -euo pipefail

BOOTSTRAP="${1:?Usage: verify-external-kafka.sh <elastic-ip>:9094}"
TOPIC="infra-verify-external-$(date +%s)"
MESSAGE="hello-from-laptop"

if ! command -v kcat >/dev/null 2>&1; then
  echo "kcat not found. Install it (e.g. 'brew install kcat' or 'apt install kafkacat') and re-run." >&2
  exit 1
fi

echo "Producing to ${BOOTSTRAP} topic ${TOPIC}..."
echo "$MESSAGE" | kcat -P -b "$BOOTSTRAP" -t "$TOPIC"

echo "Consuming back..."
RESULT=$(kcat -C -b "$BOOTSTRAP" -t "$TOPIC" -c 1 -o beginning -e)

if [ "$RESULT" = "$MESSAGE" ]; then
  echo "PASS: external produce/consume against ${BOOTSTRAP} works."
  exit 0
else
  echo "FAIL: expected '${MESSAGE}', got '${RESULT}'."
  exit 1
fi
