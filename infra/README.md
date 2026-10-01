# Infra runbook

`../deploy.sh` drives all of this; this page explains what it does and how
to do the steps by hand.

## Prerequisites

- SSH key pair for the instance: `ssh-keygen -t ed25519 -f ~/.ssh/weather-pipeline -N ""`
- AWS credentials configured (`aws configure` or environment variables)
- A default VPC in the target region (the security group and instance use it)
- Optional, for the public HTTPS/WebSocket endpoint: a free
  [DuckDNS](https://www.duckdns.org/) name pointed at the Elastic IP

## What Terraform manages

| Resource | Lifetime |
|---|---|
| EC2 node (`m7i-flex.large`, k3s via user-data) + EIP association | on demand: `node_enabled=false` destroys them |
| Elastic IP | kept, so the API address and DuckDNS name never change |
| S3 bucket + IAM user for Spark's data-lake writes | kept |
| Security group: 22 from `allowed_ssh_cidr`, 80/443 from anywhere | kept |

Kafka and Redis are cluster-internal. Port 80 exists for Let's Encrypt's
HTTP-01 challenge; 443 carries the read-only API and WebSocket.

## Steps

1. Copy `terraform.tfvars.example` to `terraform.tfvars`; set `allowed_ssh_cidr`
   to your current public IP (`curl https://checkip.amazonaws.com`).
2. `terraform init && terraform apply`
3. Wait for user-data: `ssh -i ~/.ssh/weather-pipeline ubuntu@$(terraform output -raw public_ip) 'test -f /home/ubuntu/user-data-complete && echo ready'`
4. Copy and run the bootstrap (Kafka, Redis, cert-manager, Let's Encrypt issuer):
   ```
   scp -i ~/.ssh/weather-pipeline -r helm k8s bootstrap.sh ubuntu@$(terraform output -raw public_ip):~
   ssh -i ~/.ssh/weather-pipeline ubuntu@$(terraform output -raw public_ip) \
     'chmod +x bootstrap.sh && ./bootstrap.sh '"$(terraform output -raw public_ip)"
   ```
5. Applications, topics and ingress: `../deploy.sh --apps` (or the whole
   bring-up with `../deploy.sh`).
6. `kubectl` from your machine: the Kubernetes API (6443) is not exposed, so
   tunnel it:
   ```
   ssh -i ~/.ssh/weather-pipeline -L 6443:127.0.0.1:6443 -N ubuntu@$(terraform output -raw public_ip) &
   scp -i ~/.ssh/weather-pipeline ubuntu@$(terraform output -raw public_ip):~/.kube/config ./kubeconfig
   export KUBECONFIG=$PWD/kubeconfig
   ```
7. Pause: `../deploy.sh --down` (saves the TLS certificate, destroys the node).
   Remove everything: `../deploy.sh --destroy` (empty the S3 bucket first).

If your IP changes, update `allowed_ssh_cidr` and `terraform apply`: only the
security group rule changes.
