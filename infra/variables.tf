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

variable "node_enabled" {
  description = "false destroys only the EC2 node; the Elastic IP, S3 bucket and IAM user stay"
  type        = bool
  default     = true
}
