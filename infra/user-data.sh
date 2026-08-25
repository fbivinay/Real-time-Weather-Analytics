#!/bin/bash
set -euxo pipefail

mkdir -p /etc/rancher/k3s
cat > /etc/rancher/k3s/config.yaml <<'EOF'
kube-apiserver-arg:
  - "service-node-port-range=9094-32767"
EOF

curl -sfL https://get.k3s.io | sh -

curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

mkdir -p /home/ubuntu/.kube
timeout 60 bash -c 'until [ -f /etc/rancher/k3s/k3s.yaml ]; do sleep 2; done'
cp /etc/rancher/k3s/k3s.yaml /home/ubuntu/.kube/config
chown ubuntu:ubuntu /home/ubuntu/.kube/config
echo "export KUBECONFIG=/home/ubuntu/.kube/config" >> /home/ubuntu/.bashrc

touch /home/ubuntu/user-data-complete
