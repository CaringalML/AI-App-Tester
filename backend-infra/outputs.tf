output "api_url" {
  description = "Public API address. Set it as the API_BASE_URL repository variable for the frontend build."
  value       = "https://${local.api_host}"
}

output "github_deploy_role_arn" {
  description = "Set as the BACKEND_DEPLOY_ROLE_ARN repository variable."
  value       = aws_iam_role.github_deploy.arn
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "ecs_cluster" {
  value = aws_ecs_cluster.this.name
}

output "ecs_service" {
  value = aws_ecs_service.api.name
}

output "task_family" {
  value = aws_ecs_task_definition.api.family
}

output "anthropic_secret_name" {
  description = "Put the Claude API key here with the AWS CLI; see README."
  value       = aws_secretsmanager_secret.anthropic_api_key.name
}

output "scans_table" {
  value = aws_dynamodb_table.scans.name
}

output "artifacts_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}

output "log_group" {
  value = aws_cloudwatch_log_group.api.name
}
