# Infra runbook

## Prerequisites

- SSH key pair for the instance: `ssh-keygen -t ed25519 -f ~/.ssh/weather-pipeline -N ""`
- AWS credentials configured (`aws configure` or environment variables)
- `kcat` installed locally (needed for the external verification step —
  `brew install kcat` or `apt install kafkacat`)
- A default VPC must exist in the target AWS region — this Terraform
  config doesn't create one; `aws_security_group` and `aws_instance` both
  rely on the account's default VPC/subnet.

## Steps

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
6. To use `kubectl`/`helm` from your own machine (optional): port 6443
   (the Kubernetes API) isn't open in the security group (only 22 and
   9094 are), so reach it through an SSH tunnel instead of a direct
   kubeconfig rewrite:
   ```
   ssh -i ~/.ssh/weather-pipeline -L 6443:127.0.0.1:6443 -N ubuntu@$(terraform output -raw public_ip) &
   scp -i ~/.ssh/weather-pipeline ubuntu@$(terraform output -raw public_ip):~/.kube/config ./kubeconfig
   export KUBECONFIG=$PWD/kubeconfig
   kubectl get nodes   # should work through the tunnel
   ```
   The kubeconfig copied from the instance already has
   `server: https://127.0.0.1:6443` (k3s's own default), so with the
   tunnel active no server-field rewrite is needed.
7. When done: `terraform destroy` (tears down instance, EIP, security group, key pair).
