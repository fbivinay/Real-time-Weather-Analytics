# Infra — EC2 + k3s + Kafka + Redis (sub-project 1 of 5)

## Context

Part of the Real-Time Weather Analytics Pipeline. Full data flow:

```
Weather Generator
      │
      ▼
Kafka topic: weather-data
      │
      ▼
Databricks Structured Streaming
      │
      ▼
Kafka topic: weather-processed  (aggregates + alerts)
      │
      ▼
Internal K8s consumer
      │
      ▼
Redis (cluster-internal)
      │
      ▼
FastAPI backend
      │
      ▼
Next.js dashboard (Vercel)
```

Raw/processed data also lands in S3 (history) directly from the
Databricks job, in parallel with the `weather-processed` topic.

This spec covers only the infrastructure layer: the K8s cluster and the
two stateful platform services (Kafka, Redis) that later sub-projects
deploy producer/consumer/backend workloads onto. **Infra owns the Kafka
platform; it does not create application topics** (`weather-data`,
`weather-processed`) — those are created by the sub-projects that
produce/consume them (2 and 3), keeping topic ownership with the code
that uses them.

Later sub-projects (not this spec):
2. Data pipeline (generator + Kafka producer, as K8s pods) — creates `weather-data`
3. Databricks Structured Streaming job — creates `weather-processed`
4. Serving layer (FastAPI backend on K8s + Next.js frontend on Vercel)
5. `deploy.sh` orchestration tying 1-4 together

## Goals

- One Terraform apply stands up a single EC2 instance running k3s with
  Kafka and Redis installed and healthy.
- Kafka reachable from outside AWS at a **stable address** (Databricks
  will produce/consume against it in sub-project 3).
- Redis stays cluster-internal only, authenticated.
- Repeatable and destroyable (`terraform destroy`) since this runs on
  paid instance time, not free tier.
- Instance type and region are configurable, not hardcoded.

## Non-goals

- Multi-node HA cluster.
- TLS/SASL on Kafka (deferred — flagged as a hardening follow-up).
- Databricks-side configuration (sub-project 3).
- Any application workload (producer, backend) — sub-projects 2 and 4.

## Architecture

```
Terraform
  │
  ├── SSH key pair (public key only — see Key handling)
  ├── security group
  │     ├── 22/tcp   ← your IP only
  │     └── 9094/tcp ← 0.0.0.0/0 (temporary, see Networking)
  ├── Elastic IP  ── associated to the instance
  ├── EBS volume (gp3, 40GB) as instance root volume
  └── EC2 instance (var.instance_type, default m7i-flex.large; Ubuntu 22.04)
        └── minimal user-data: install k3s + Helm only
              │
              ▼
        bootstrap.sh (run over SSH, idempotent)
              1. helm install kafka (Bitnami, KRaft mode, 1 broker,
                 internal + external listeners configured)
              2. helm install redis (Bitnami, standalone, auth enabled)
              3. verification checks (pods Running, Kafka reachable)
```

Variables (`variables.tf`): `aws_region`, `instance_type` (default
`m7i-flex.large`), `root_volume_size_gb` (default `40`), `ssh_public_key_path`,
`allowed_ssh_cidr`.

## Networking

Only Kafka is exposed externally. Redis is ClusterIP-only.

Rationale: Databricks needs a two-way path (consume raw
`weather-data`, later produce processed results back). Exposing one
Kafka listener covers both directions. Exposing Redis publicly would
add a second, harder-to-secure surface for no benefit — sub-project 4
will run an internal consumer that drains the `weather-processed` Kafka
topic into cluster-internal Redis, so nothing outside the cluster ever
talks to Redis directly.

**Elastic IP.** Terraform allocates an EIP and associates it to the
instance, rather than relying on the ephemeral public IP EC2 assigns
by default. Ephemeral IPs change on stop/start, which would silently
break the Databricks bootstrap config every time the instance restarts.
The EIP is destroyed along with the rest of the infra on
`terraform destroy` (EIPs incur a small charge only while *unassociated*,
so this is a non-issue as long as apply/destroy stay paired).

**Kafka listener configuration.** This is the part that actually makes
external access work, not just "port open":

```
listeners:
  INTERNAL: PLAINTEXT://0.0.0.0:9092
  EXTERNAL: PLAINTEXT://0.0.0.0:9094

advertised.listeners:
  INTERNAL: PLAINTEXT://kafka.<namespace>.svc.cluster.local:9092
  EXTERNAL: PLAINTEXT://<ELASTIC_IP>:9094

listener.security.protocol.map:
  INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT
```

Bitnami's Kafka chart exposes this via `listeners.external.*` /
`externalAccess.*` values, service type `NodePort` pinned to `9094`,
and `externalAccess.autoDiscovery` disabled in favor of explicitly
setting the advertised address to the Elastic IP (autodiscovery would
pick up the instance's own view of its address, which isn't reliably
the EIP). Without this, clients can open a TCP connection to
`9094` (bootstrap succeeds) but then get handed an unreachable internal
address for the actual produce/consume connection — the classic
"bootstrap connects, everything after fails" failure mode. Getting this
right is why the external verification step below exists.

Security group opens 9094 to `0.0.0.0/0` initially because Databricks'
NAT egress IPs aren't known until the workspace/cluster is created.
**Follow-up hardening task (post-setup):** once the Databricks cluster
exists, fetch its NAT IP(s) and restrict the security group's 9094
ingress rule to that CIDR. Not blocking for functionality, tracked so
it doesn't get forgotten. No other inbound rules are defined (default
deny covers everything else); outbound stays open.

SSH (22) is restricted to `var.allowed_ssh_cidr` (the operator's
current public IP, passed via `tfvars`, not auto-detected — avoids a
Terraform provider dependency on an external IP-lookup service).

## Key handling

The SSH key pair is generated **locally**, outside Terraform
(`ssh-keygen`), and only the **public** key is passed to Terraform
(`aws_key_pair` from `var.ssh_public_key_path`). The private key never
enters Terraform state or the repo. `.gitignore` at repo root excludes:

```
*.pem
*.key
*.tfstate
*.tfstate.*
.terraform/
```

## Provisioning

- **Terraform** (`infra/` directory): `main.tf`, `variables.tf`,
  `outputs.tf`. Provider: `aws`. Resources: `aws_key_pair` (public key
  only), `aws_security_group`, `aws_eip` (with an inline `instance =`
  argument to associate it — no separate `aws_eip_association`
  resource needed), `aws_instance` (with `root_block_device` sized via
  `var.root_volume_size_gb`, type `gp3`). Output: Elastic IP, SSH
  command.
- **k3s install**: via EC2 user-data (`curl -sfL https://get.k3s.io |
  sh -`) plus Helm install — kept deliberately minimal (cluster
  bootstrap only). Single binary, no external etcd needed at this
  scale.
- **bootstrap.sh**: a separate, idempotent script (run over SSH after
  user-data completes, not embedded in user-data) that installs the
  Kafka and Redis Helm charts and runs the in-cluster verification
  checks. Idempotent so a failed or partial run can just be re-run
  rather than requiring a full teardown/recreate — this is where
  actual debugging happens if something's wrong, and a giant
  all-in-one user-data script is miserable to iterate on.
- **Redis config**: `architecture=standalone`, `replica.replicaCount=0`,
  `service.type=ClusterIP`, `auth.enabled=true`. Password generated
  (random) and stored as a Kubernetes Secret, not committed to git.
  Later sub-projects reference it via the Secret and connect using the
  cluster DNS name `redis-master.<namespace>.svc.cluster.local`, never
  a hardcoded IP.
- **Kafka retention**: `log.retention.hours` set to a bounded value
  (e.g. 24h) so the temporary instance's disk doesn't fill up during a
  multi-day demo window.
- **kubeconfig**: pulled back to the operator's machine via
  `scp ubuntu@<eip>:~/.kube/config`, accessed through an SSH tunnel
  (`ssh -L 6443:127.0.0.1:6443 ... ubuntu@<eip>`) rather than a direct
  server-field rewrite to the Elastic IP — port 6443 is deliberately not
  opened in the security group (only 22 and 9094 are, per the Networking
  section), so a kubeconfig pointing straight at `https://<eip>:6443`
  would time out. The kubeconfig's `server: https://127.0.0.1:6443`
  (k3s's own default) already works unmodified once the tunnel is up, so
  `kubectl`/`helm` from your machine work against the cluster directly
  for later sub-projects with no rewrite needed.

## Verification (definition of done for this sub-project)

1. `terraform apply` completes, outputs the Elastic IP.
2. `kubectl get nodes` shows the node `Ready`.
3. `kubectl get pods -A` shows Kafka and Redis pods `Running`.
4. **In-cluster** check: create a test topic, produce a message,
   consume it back, from a pod inside the cluster.
5. **External** check: from the operator's laptop (outside AWS, outside
   the cluster), create/produce/consume against
   `<elastic-ip>:9094`. This is the test that actually validates the
   advertised-listener config — an in-cluster-only check would pass
   even with broken external listener config.
6. `terraform destroy` cleanly tears everything down, including the
   Elastic IP (cost control).

## Open follow-ups (tracked, not blocking)

- Restrict Kafka security-group ingress to Databricks NAT CIDR once
  known.
- Kafka has no TLS/SASL at this stage (auth deferred, but Redis auth
  is in scope now) — acceptable for a demo/course project on a
  temporary instance, revisit if this becomes long-lived.
