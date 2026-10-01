resource "aws_key_pair" "weather_pipeline" {
  key_name   = "weather-pipeline-key"
  public_key = file(pathexpand(var.ssh_public_key_path))

  tags = {
    Project = "weather-pipeline"
  }
}

resource "aws_security_group" "weather_pipeline" {
  name = "weather-pipeline-sg"
  # Historical wording, kept on purpose: description is ForceNew, and
  # replacing a security group attached to the running node fails. The
  # rules below are what matter (SSH + HTTPS; Kafka is no longer exposed).
  description = "SSH + external Kafka listener for the weather pipeline k3s node"

  ingress {
    description = "SSH from operator"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ssh_cidr]
  }

  # Traefik (bundled with k3s) serves the API and WebSocket over TLS. Port 80
  # is needed for Let's Encrypt's HTTP-01 challenge. Kafka and Redis stay
  # cluster-internal; the dashboard only ever talks to 443.
  ingress {
    description = "HTTP for ACME challenges"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS API and WebSocket for the dashboard"
    from_port   = 443
    to_port     = 443
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

# On demand: `./deploy.sh --down` sets node_enabled=false, which destroys the
# node (and its root volume) but keeps the Elastic IP, S3 bucket and IAM user,
# so the API address and the data lake survive between sessions.
resource "aws_instance" "weather_pipeline" {
  count                  = var.node_enabled ? 1 : 0
  ami                    = data.aws_ami.ubuntu_2204.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.weather_pipeline.key_name
  vpc_security_group_ids = [aws_security_group.weather_pipeline.id]
  user_data              = file("${path.module}/user-data.sh")

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_size_gb
    encrypted   = true
  }

  tags = {
    Name    = "weather-pipeline-node"
    Project = "weather-pipeline"
  }

  # data.aws_ami.ubuntu_2204 is most_recent, so it re-resolves to whatever
  # Canonical published last. Without this, an unrelated apply (a changed SSH
  # CIDR, say) silently destroys and recreates the node - taking k3s, Kafka,
  # Redis and every running workload with it. Rebuilding on a newer AMI has to
  # be an explicit `terraform taint` / destroy, never a side effect.
  lifecycle {
    ignore_changes = [ami]
  }
}

moved {
  from = aws_instance.weather_pipeline
  to   = aws_instance.weather_pipeline[0]
}

# Kept while the node is down (~$3.65/month) so the DuckDNS name and the
# dashboard's API host never change.
resource "aws_eip" "weather_pipeline" {
  domain = "vpc"

  tags = {
    Project = "weather-pipeline"
    Name    = "weather-pipeline-eip"
  }
}

resource "aws_eip_association" "weather_pipeline" {
  count         = var.node_enabled ? 1 : 0
  instance_id   = aws_instance.weather_pipeline[0].id
  allocation_id = aws_eip.weather_pipeline.id
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "weather_pipeline" {
  bucket = "weather-pipeline-${data.aws_caller_identity.current.account_id}"

  tags = {
    Project = "weather-pipeline"
  }
}

resource "aws_s3_bucket_public_access_block" "weather_pipeline" {
  bucket = aws_s3_bucket.weather_pipeline.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_iam_user" "databricks_s3" {
  name = "weather-pipeline-databricks-s3"

  tags = {
    Project = "weather-pipeline"
  }
}

resource "aws_iam_user_policy" "databricks_s3" {
  name = "weather-pipeline-databricks-s3-access"
  user = aws_iam_user.databricks_s3.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ListBucket"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:ListBucketMultipartUploads",
        ]
        Resource = [aws_s3_bucket.weather_pipeline.arn]
      },
      {
        # Spark's file sink commits by writing a temp object and renaming it,
        # and S3A implements rename as copy + delete. A write-only policy
        # therefore fails the stream with a 403 on _spark_metadata rather than
        # simply skipping cleanup. Multipart permissions are required for the
        # same commit path once a part file exceeds the single-PUT threshold.
        Sid    = "ReadWriteObjects"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:DeleteObject",
          "s3:AbortMultipartUpload",
          "s3:ListMultipartUploadParts",
        ]
        Resource = ["${aws_s3_bucket.weather_pipeline.arn}/*"]
      }
    ]
  })
}

resource "aws_iam_access_key" "databricks_s3" {
  user = aws_iam_user.databricks_s3.name
}
