output "public_ip" {
  description = "Elastic IP of the weather-pipeline node (stable across --down/up)"
  value       = aws_eip.weather_pipeline.public_ip
}

output "ssh_command" {
  description = "SSH command to reach the instance"
  value       = "ssh -i ${replace(pathexpand(var.ssh_public_key_path), ".pub", "")} ubuntu@${aws_eip.weather_pipeline.public_ip}"
}

output "node_running" {
  description = "Whether the EC2 node currently exists"
  value       = var.node_enabled
}

output "s3_bucket_name" {
  description = "S3 bucket for the data lake (raw/quarantine/features/decisions)"
  value       = aws_s3_bucket.weather_pipeline.bucket
}

output "databricks_s3_access_key_id" {
  description = "AWS access key ID for the processor's S3 IAM user"
  value       = aws_iam_access_key.databricks_s3.id
}

output "databricks_s3_secret_key" {
  description = "AWS secret access key for the processor's S3 IAM user"
  value       = aws_iam_access_key.databricks_s3.secret
  sensitive   = true
}
