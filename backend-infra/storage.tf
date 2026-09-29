# Container images, scan records, and evidence screenshots. All three are
# scratch data for a prototype, so everything expires on its own.

resource "aws_ecr_repository" "api" {
  name                 = local.name
  image_tag_mutability = "MUTABLE" # :latest moves; every deploy also pushes an immutable SHA tag
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 15 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 15
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_dynamodb_table" "scans" {
  name         = "${var.project_name}-scans"
  billing_mode = "PAY_PER_REQUEST" # nothing to pay while idle
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  # The API writes expiresAt as epoch seconds; DynamoDB deletes the item after it.
  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }
}

resource "aws_s3_bucket" "artifacts" {
  bucket        = "${var.project_name}-artifacts-${local.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "expire-screenshots"
    status = "Enabled"
    filter {}
    expiration {
      days = 7
    }
  }
}

# The secret's container only. Its value is set out of band with the AWS CLI
# (see README) so the key never passes through Terraform state or this repo.
resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name                    = "${var.project_name}/anthropic-api-key"
  description             = "Claude API key for the AI App Tester scanner"
  recovery_window_in_days = 7
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.name}"
  retention_in_days = 14
}
