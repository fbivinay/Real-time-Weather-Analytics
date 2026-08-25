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
