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
