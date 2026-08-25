# Infra: EC2 + k3s + Kafka + Redis — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `terraform apply` stands up a single EC2 node running k3s
with Kafka (KRaft, externally reachable via a stable Elastic IP) and
Redis (internal-only, authenticated), verified end-to-end from both
inside the cluster and from the operator's laptop.

**Architecture:** Terraform provisions networking + compute (key pair,
security group, Elastic IP, EBS-backed EC2 instance) and a minimal
user-data script that installs k3s + Helm only. A separate idempotent
`bootstrap.sh`, run over SSH after the instance is up, installs Kafka
and Redis via Helm and runs in-cluster verification. An
operator-run script validates external Kafka access from outside AWS.

**Tech Stack:** Terraform (`hashicorp/aws` ~> 5.0), Ubuntu 22.04, k3s,
Helm 3, Bitnami `kafka` and `redis` charts, bash, `kcat` (for external
Kafka verification).

**Spec:** `docs/superpowers/specs/2026-08-25-infra-design.md`

## Global Constraints

- Instance type default `m7i-flex.large`, configurable via
  `var.instance_type` — never hardcoded in resources.
- AWS region configurable via `var.aws_region` — never hardcoded.
- Root volume: `gp3`, default 40GB, configurable via
  `var.root_volume_size_gb`.
- Only port 22 (from `var.allowed_ssh_cidr`) and port 9094 (from
  `0.0.0.0/0`, temporary — see spec's Networking section) are opened
  inbound. No other inbound rules.
- SSH private key never touches Terraform state or git. Only the
  public key path is a Terraform variable. `.gitignore` (already
  committed at repo root) excludes `*.pem`, `*.key`, `*.tfstate`,
  `*.tfstate.*`, `.terraform/`.
- Redis: `auth.enabled=true`, password in a Kubernetes Secret, never
  in a values file or git.
- Kafka: KRaft mode, no ZooKeeper, single broker, internal listener on
  9092 (cluster DNS), external listener on NodePort 9094 advertised as
  the Elastic IP — not the instance's default public IP.
- Infra does not create the `weather-data` or `weather-processed`
  topics — that's sub-projects 2 and 3.
- All resources tagged `Project = "weather-pipeline"` for easy
  identification/cleanup.

---

## Task 1: Terraform scaffolding — provider, variables, AMI lookup

**Files:**
- Create: `infra/versions.tf`
- Create: `infra/variables.tf`

**Interfaces:**
- Produces: `var.aws_region`, `var.instance_type`,
  `var.root_volume_size_gb`, `var.ssh_public_key_path`,
  `var.allowed_ssh_cidr`, `var.kafka_external_nodeport` — all
  referenced by Task 2/3.
- Produces: `data.aws_ami.ubuntu_2204` — consumed by Task 3.

- [x] **Step 1: Write `infra/versions.tf`**

```hcl
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}
```

- [x] **Step 2: Write `infra/variables.tf`**

```hcl
variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "EC2 instance type for the k3s node"
  type        = string
  default     = "m7i-flex.large"
}

variable "root_volume_size_gb" {
  description = "Root EBS volume size in GB"
  type        = number
  default     = 40
}

variable "ssh_public_key_path" {
  description = "Path to the local SSH public key file to authorize on the instance"
  type        = string
  default     = "~/.ssh/weather-pipeline.pub"
}

variable "allowed_ssh_cidr" {
  description = "CIDR allowed to SSH into the instance, e.g. 1.2.3.4/32"
  type        = string
}

variable "kafka_external_nodeport" {
  description = "NodePort used for the Kafka external listener"
  type        = number
  default     = 9094
}
```

- [x] **Step 3: Add the Ubuntu 22.04 AMI data source to `infra/versions.tf`**

Append to the same file (data sources don't need their own file at this size):

```hcl
data "aws_ami" "ubuntu_2204" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}
```

- [x] **Step 4: Validate**

Run: `cd infra && terraform init && terraform validate`
Expected: `Success! The configuration is valid.`

- [x] **Step 5: Commit**

```bash
git add infra/versions.tf infra/variables.tf
git commit -m "infra: Terraform scaffolding (provider, variables, AMI lookup)"
```

---

## Task 2: Key pair + security group

**Files:**
- Create: `infra/main.tf`

**Interfaces:**
- Consumes: `var.ssh_public_key_path`, `var.allowed_ssh_cidr`,
  `var.kafka_external_nodeport` (Task 1).
- Produces: `aws_key_pair.weather_pipeline`,
  `aws_security_group.weather_pipeline` — consumed by Task 3.

- [x] **Step 1: Generate a local SSH key pair (if you don't already have one for this project)**

Run: `ssh-keygen -t ed25519 -f ~/.ssh/weather-pipeline -N ""`
Expected: creates `~/.ssh/weather-pipeline` (private, keep local) and
`~/.ssh/weather-pipeline.pub` (public, this is what Terraform reads).

- [x] **Step 2: Write `infra/main.tf` with the key pair and security group resources**

```hcl
resource "aws_key_pair" "weather_pipeline" {
  key_name   = "weather-pipeline-key"
  public_key = file(pathexpand(var.ssh_public_key_path))

  tags = {
    Project = "weather-pipeline"
  }
}

resource "aws_security_group" "weather_pipeline" {
  name        = "weather-pipeline-sg"
  description = "SSH + external Kafka listener for the weather pipeline k3s node"

  ingress {
    description = "SSH from operator"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ssh_cidr]
  }

  ingress {
    description = "Kafka external listener (temporary open — see spec Networking follow-up)"
    from_port   = var.kafka_external_nodeport
    to_port     = var.kafka_external_nodeport
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Project = "weather-pipeline"
    Name    = "weather-pipeline-sg"
  }
}
```

- [x] **Step 3: Validate**

Run: `cd infra && terraform validate`
Expected: `Success! The configuration is valid.`

- [x] **Step 4: Commit**

```bash
git add infra/main.tf
git commit -m "infra: key pair and security group"
```

---

## Task 3: EC2 instance, EBS root volume, Elastic IP, minimal user-data

**Files:**
- Create: `infra/user-data.sh`
- Modify: `infra/main.tf` (append instance + EIP resources)

**Interfaces:**
- Consumes: `data.aws_ami.ubuntu_2204` (Task 1),
  `aws_key_pair.weather_pipeline`, `aws_security_group.weather_pipeline`
  (Task 2).
- Produces: `aws_instance.weather_pipeline`,
  `aws_eip.weather_pipeline` — consumed by Task 4 (outputs).

- [x] **Step 1: Write `infra/user-data.sh` — minimal: k3s + Helm only**

```bash
#!/bin/bash
set -euxo pipefail

curl -sfL https://get.k3s.io | sh -

curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

mkdir -p /home/ubuntu/.kube
cp /etc/rancher/k3s/k3s.yaml /home/ubuntu/.kube/config
chown ubuntu:ubuntu /home/ubuntu/.kube/config
echo "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml" >> /home/ubuntu/.bashrc

touch /home/ubuntu/user-data-complete
```

The `user-data-complete` marker file lets the operator poll for
completion over SSH before running `bootstrap.sh` (Task 5).

- [x] **Step 2: Validate script syntax**

Run: `bash -n infra/user-data.sh`
Expected: no output, exit code 0.

- [x] **Step 3: Append instance and Elastic IP resources to `infra/main.tf`**

```hcl
resource "aws_instance" "weather_pipeline" {
  ami                    = data.aws_ami.ubuntu_2204.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.weather_pipeline.key_name
  vpc_security_group_ids = [aws_security_group.weather_pipeline.id]
  user_data              = file("${path.module}/user-data.sh")

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_size_gb
  }

  tags = {
    Name    = "weather-pipeline-node"
    Project = "weather-pipeline"
  }
}

resource "aws_eip" "weather_pipeline" {
  domain   = "vpc"
  instance = aws_instance.weather_pipeline.id

  tags = {
    Project = "weather-pipeline"
    Name    = "weather-pipeline-eip"
  }
}
```

- [x] **Step 4: Validate**

Run: `cd infra && terraform validate`
Expected: `Success! The configuration is valid.`

- [x] **Step 5: Commit**

```bash
git add infra/user-data.sh infra/main.tf
git commit -m "infra: EC2 instance, EBS root volume, Elastic IP, minimal user-data"
```

---

## Task 4: Terraform outputs

**Files:**
- Create: `infra/outputs.tf`

**Interfaces:**
- Consumes: `aws_eip.weather_pipeline`, `var.ssh_public_key_path`,
  `var.kafka_external_nodeport` (Tasks 1-3).
- Produces: `terraform output public_ip`, `terraform output ssh_command`,
  `terraform output kafka_bootstrap` — used manually by the operator in
  Tasks 5-8.

- [x] **Step 1: Write `infra/outputs.tf`**

```hcl
output "public_ip" {
  description = "Elastic IP of the weather-pipeline node"
  value       = aws_eip.weather_pipeline.public_ip
}

output "ssh_command" {
  description = "SSH command to reach the instance"
  value       = "ssh -i ${replace(pathexpand(var.ssh_public_key_path), ".pub", "")} ubuntu@${aws_eip.weather_pipeline.public_ip}"
}

output "kafka_bootstrap" {
  description = "External Kafka bootstrap address for Databricks / verification"
  value       = "${aws_eip.weather_pipeline.public_ip}:${var.kafka_external_nodeport}"
}
```

- [x] **Step 2: Validate**

Run: `cd infra && terraform validate`
Expected: `Success! The configuration is valid.`

- [x] **Step 3: Commit**

```bash
git add infra/outputs.tf
git commit -m "infra: Terraform outputs (public IP, SSH command, Kafka bootstrap address)"
```

---

## Task 5: Kafka + Redis Helm values

**Files:**
- Create: `infra/helm/kafka-values.yaml`
- Create: `infra/helm/redis-values.yaml`

**Interfaces:**
- Produces: values files referenced by `bootstrap.sh` (Task 6) via
  `-f infra/helm/kafka-values.yaml` / `-f infra/helm/redis-values.yaml`.

- [x] **Step 1: Write `infra/helm/kafka-values.yaml`**

```yaml
# KRaft mode, single broker, no ZooKeeper.
kraft:
  enabled: true

controller:
  replicaCount: 1

broker:
  replicaCount: 0

listeners:
  client:
    protocol: PLAINTEXT
  controller:
    protocol: PLAINTEXT
  interbroker:
    protocol: PLAINTEXT
  external:
    protocol: PLAINTEXT

# advertisedAddress for the external listener is set at install time
# via `--set` in bootstrap.sh, using the Elastic IP (not known here).
externalAccess:
  enabled: true
  autoDiscovery:
    enabled: false
  controller:
    service:
      type: NodePort
      nodePorts:
        - "9094"

# Bounded retention so the demo instance's disk doesn't fill up.
extraConfig: |
  log.retention.hours=24
```

**Note for the executor:** Bitnami chart value paths shift between
versions. Before running `bootstrap.sh` for real, run
`helm show values bitnami/kafka > /tmp/kafka-chart-defaults.yaml` on
the instance and diff the field names above (`controller.*`,
`broker.*`, `externalAccess.*`, `listeners.*`) against whatever chart
version resolves at install time. Adjust this file if names differ —
the intent (KRaft, single node, external NodePort 9094 advertised as
the Elastic IP, 24h retention) is what must be preserved, not the
literal YAML paths.

- [x] **Step 2: Write `infra/helm/redis-values.yaml`**

```yaml
architecture: standalone

replica:
  replicaCount: 0

auth:
  enabled: true
  # existingSecret + existingSecretPasswordKey are set at install time
  # via --set in bootstrap.sh, once the Secret has been created.

master:
  persistence:
    enabled: true
    size: 4Gi

service:
  type: ClusterIP
```

- [x] **Step 3: Lint both files as YAML**

Run: `python -c "import yaml,sys; [yaml.safe_load(open(f)) for f in ['infra/helm/kafka-values.yaml','infra/helm/redis-values.yaml']]; print('valid')"`
Expected: `valid`

- [x] **Step 4: Commit**

```bash
git add infra/helm/kafka-values.yaml infra/helm/redis-values.yaml
git commit -m "infra: Kafka (KRaft) and Redis (auth-enabled) Helm values"
```

---

## Task 6: bootstrap.sh — idempotent Kafka/Redis install + in-cluster verification

**Files:**
- Create: `infra/bootstrap.sh`

**Interfaces:**
- Consumes: `infra/helm/kafka-values.yaml`, `infra/helm/redis-values.yaml`
  (Task 5). Takes the Elastic IP as `$1` (from `terraform output
  public_ip`, Task 4).
- Produces: `weather-pipeline` namespace, `kafka` and `redis` Helm
  releases, `redis-auth` Secret — all consumed by later sub-projects
  (2, 3, 4) via the same namespace and service DNS names
  (`kafka.weather-pipeline.svc.cluster.local:9092`,
  `redis-master.weather-pipeline.svc.cluster.local:6379`).

- [x] **Step 1: Write `infra/bootstrap.sh`**

```bash
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
```

This script is idempotent: `helm upgrade --install` is safe to re-run,
the namespace/secret checks skip re-creation, and a failed run can
just be re-invoked rather than requiring `terraform destroy` +
recreate.

- [x] **Step 2: Validate script syntax**

Run: `bash -n infra/bootstrap.sh`
Expected: no output, exit code 0.

- [x] **Step 3: Commit**

```bash
git add infra/bootstrap.sh
git commit -m "infra: idempotent bootstrap.sh (Kafka + Redis Helm install, in-cluster verification)"
```

---

## Task 7: External Kafka verification script (run from operator's laptop)

**Files:**
- Create: `infra/verify-external-kafka.sh`

**Interfaces:**
- Consumes: `terraform output kafka_bootstrap` (Task 4) as `$1`.
- Produces: pass/fail exit code, used manually as the last gate in
  Task 8 before declaring the sub-project done.

- [x] **Step 1: Write `infra/verify-external-kafka.sh`**

```bash
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
```

This is what actually validates the advertised-listener configuration
from Task 6 — an in-cluster-only check (Task 6, Step 1's final block)
would still pass even if the external advertised address were wrong,
since that check never leaves the cluster network.

- [x] **Step 2: Validate script syntax**

Run: `bash -n infra/verify-external-kafka.sh`
Expected: no output, exit code 0.

- [x] **Step 3: Commit**

```bash
git add infra/verify-external-kafka.sh
git commit -m "infra: external Kafka verification script (run from operator's machine)"
```

---

## Task 8: End-to-end run and teardown (real AWS resources — costs money while running)

**Files:**
- Create: `infra/terraform.tfvars.example`
- Create: `infra/README.md`

No new Terraform/script logic — this task is the actual integration
test: stand the real infra up, verify it fully, tear it down. Do this
task deliberately, not as a background step, since it incurs AWS
charges while running.

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces: a working, externally-verified cluster (transient), and
  the operator runbook that sub-project 2 onward will reuse.

- [x] **Step 1: Write `infra/terraform.tfvars.example`**

```hcl
aws_region           = "us-east-1"
instance_type        = "m7i-flex.large"
root_volume_size_gb  = 40
ssh_public_key_path  = "~/.ssh/weather-pipeline.pub"
allowed_ssh_cidr      = "YOUR.IP.ADDR.ESS/32"
kafka_external_nodeport = 9094
```

- [x] **Step 2: Write `infra/README.md` — the operator runbook**

```markdown
# Infra runbook

1. Copy `terraform.tfvars.example` to `terraform.tfvars`, fill in
   `allowed_ssh_cidr` with your current public IP (`curl ifconfig.me`).
2. `terraform init && terraform apply`
3. Wait ~2 min for user-data to finish, then check:
   `ssh -i ~/.ssh/weather-pipeline ubuntu@$(terraform output -raw public_ip) 'test -f /home/ubuntu/user-data-complete && echo ready'`
4. Copy the bootstrap files up and run them:
   ```
   scp -i ~/.ssh/weather-pipeline -r helm bootstrap.sh ubuntu@$(terraform output -raw public_ip):~
   ssh -i ~/.ssh/weather-pipeline ubuntu@$(terraform output -raw public_ip) \
     'chmod +x bootstrap.sh && ./bootstrap.sh '"$(terraform output -raw public_ip)"
   ```
5. From your own machine: `./verify-external-kafka.sh "$(terraform output -raw kafka_bootstrap)"`
6. When done: `terraform destroy` (tears down instance, EIP, security group, key pair).
```

- [ ] **Step 3: Run the full cycle**

Partial: apply, bootstrap, in-cluster verify, and pod-Running checks
(sub-steps 1-5) all done — cluster is live at the recorded public IP,
Kafka/Redis pods Running, in-cluster verify passed. `kcat`-based
external verify (sub-step 6) and `terraform destroy` (sub-step 7) not
run — cluster intentionally kept up for sub-projects 2/3.

Run, in order:
1. `cd infra && terraform init && terraform apply` — confirm when prompted.
2. `terraform output` — record `public_ip`.
3. Poll until `user-data-complete` marker exists (Task 3, Step 1),
   then `kubectl get nodes` via SSH — expect the node `Ready`.
4. Copy up and run `bootstrap.sh` per the README — expect
   "Bootstrap complete." and the in-cluster verification step to
   print the consumed message back.
5. `kubectl get pods -n weather-pipeline` — expect Kafka and Redis
   pods `Running`.
6. From your laptop: `./verify-external-kafka.sh "$(terraform output -raw kafka_bootstrap)"` — expect `PASS`.
7. `terraform destroy` — confirm when prompted, expect all resources
   removed including the Elastic IP.

Expected overall: every step above succeeds. This is the sub-project's
definition of done from the spec's Verification section.

- [x] **Step 4: Commit the runbook files**

```bash
git add infra/terraform.tfvars.example infra/README.md
git commit -m "infra: operator runbook and tfvars example"
```

---

## Self-Review Notes

- Spec coverage: Elastic IP (Task 3), explicit Kafka listeners (Task 5
  + 6), EBS root volume (Task 3), Kafka retention (Task 5), idempotent
  bootstrap.sh separate from user-data (Task 6 vs Task 3), external
  verification (Task 7 + Task 8), Redis auth via Secret (Task 6),
  key-pair public-key-only handling (Task 2), configurable
  region/instance type (Task 1), security group rules limited to
  22 + 9094 (Task 2), topic ownership left to sub-projects 2/3 (not
  created anywhere in this plan) — all covered.
- No placeholders: all code blocks are complete, runnable content; the
  one caveat (Task 5's chart-version note) is a real verification
  instruction with an exact command, not a TBD.
- Naming consistency checked: namespace `weather-pipeline` used
  identically in Task 6's bootstrap.sh and referenced in Task 6's
  Interfaces block for later sub-projects; service DNS names
  (`kafka.weather-pipeline.svc.cluster.local`,
  `redis-master.weather-pipeline.svc.cluster.local`) stated once and
  reused.
