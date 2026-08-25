# Infra — EC2 + k3s + Kafka + Redis (sub-project 1 of 5)

## Context

Part of the Real-Time Weather Analytics Pipeline. Full project: weather
data generator → Kafka → Databricks Structured Streaming → S3 (history)
+ Redis (live) → FastAPI backend → Next.js dashboard on Vercel.

This spec covers only the infrastructure layer: the K8s cluster and the
two stateful services (Kafka, Redis) that later sub-projects deploy
producer/consumer/backend workloads onto.

Later sub-projects (not this spec):
2. Data pipeline (generator + Kafka producer, as K8s pods)
3. Databricks Structured Streaming job
4. Serving layer (FastAPI backend on K8s + Next.js frontend on Vercel)
5. `deploy.sh` orchestration tying 1-4 together

## Goals

- One Terraform apply stands up a single EC2 instance running k3s with
  Kafka and Redis installed and healthy.
- Kafka reachable from outside AWS (Databricks will produce/consume
  against it in sub-project 3).
- Redis stays cluster-internal only.
- Repeatable and destroyable (`terraform destroy`) since this runs on
  paid instance time, not free tier.

## Non-goals

- Multi-node HA cluster.
- TLS/SASL on Kafka (deferred — flagged as a hardening follow-up).
- Databricks-side configuration (sub-project 3).
- Any application workload (producer, backend) — sub-projects 2 and 4.

## Architecture

```
Terraform
  │
  ├── key pair (generated, private key saved locally, gitignored)
  ├── security group
  │     ├── 22/tcp  ← your IP only
  │     └── 9094/tcp ← 0.0.0.0/0 (temporary, see Networking)
  └── EC2 instance (m7i-flex.large, Ubuntu 22.04)
        └── user-data script:
              1. install k3s (single-node server)
              2. install Helm
              3. helm install kafka (Bitnami, KRaft mode, 1 broker)
              4. helm install redis (Bitnami, standalone)
```

## Networking

Only Kafka is exposed externally. Redis is ClusterIP-only.

Rationale: Databricks needs a two-way path (consume raw
`weather-data`, later produce processed results back). Exposing one
Kafka listener covers both directions. Exposing Redis publicly would
add a second, harder-to-secure surface for no benefit — sub-project 4
will run an internal consumer that drains a `weather-processed` Kafka
topic into cluster-internal Redis, so nothing outside the cluster ever
talks to Redis directly.

Kafka external listener uses NodePort 9094, advertised as the EC2
public IP, so Databricks can connect with a plain
`bootstrap.servers=<public-ip>:9094`.

Security group opens 9094 to `0.0.0.0/0` initially because Databricks'
NAT egress IPs aren't known until the workspace/cluster is created.
**Follow-up hardening task (post-setup):** once the Databricks cluster
exists, fetch its NAT IP(s) and restrict the security group's 9094
ingress rule to that CIDR. Not blocking for functionality, tracked so
it doesn't get forgotten.

SSH (22) is restricted to the operator's current public IP, captured
via Terraform's `chomp(data.http...)` or passed as a `tfvars` variable.

## Provisioning

- **Terraform** (`infra/` directory): `main.tf`, `variables.tf`,
  `outputs.tf`. Provider: `aws`. Resources: `aws_key_pair`,
  `aws_security_group`, `aws_instance`. Output: public IP, SSH command.
- **k3s install**: via EC2 user-data (`curl -sfL https://get.k3s.io |
  sh -`), single binary, no external etcd needed at this scale.
- **Helm charts**: Bitnami `kafka` and `redis`, installed via
  `helm install` in the same user-data script (or a follow-up
  `bootstrap.sh` run over SSH if user-data ordering with k3s readiness
  proves flaky — decided during implementation, not a design-level
  concern).
- **kubeconfig**: pulled back to the operator's machine via
  `scp ubuntu@<ip>:/etc/rancher/k3s/k3s.yaml`, server field rewritten
  to the public IP, so `kubectl`/`helm` from your machine work against
  the cluster directly for later sub-projects.

## Verification (definition of done for this sub-project)

1. `terraform apply` completes, outputs a public IP.
2. `kubectl get nodes` shows the node `Ready`.
3. `kubectl get pods -A` shows Kafka and Redis pods `Running`.
4. From the operator's machine: create a test topic, produce a
   message, consume it back, against `<public-ip>:9094`.
5. `terraform destroy` cleanly tears everything down (cost control).

## Open follow-ups (tracked, not blocking)

- Restrict Kafka security-group ingress to Databricks NAT CIDR once
  known.
- Kafka has no auth/TLS at this stage — acceptable for a demo/course
  project on a temporary instance, revisit if this becomes long-lived.
