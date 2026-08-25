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
