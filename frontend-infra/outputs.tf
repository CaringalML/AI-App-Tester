output "site_url" {
  description = "The public address, served through Cloudflare."
  value       = "https://${var.domain_name}"
}

output "s3_bucket_name" {
  description = "Target for aws s3 sync in the deploy workflow."
  value       = aws_s3_bucket.site.id
}

output "cloudfront_distribution_id" {
  description = "Target for the cache invalidation step in the deploy workflow."
  value       = aws_cloudfront_distribution.site.id
}

output "cloudfront_domain_name" {
  description = "CloudFront's own hostname. Cloudflare's origin rule rewrites the Host header and SNI to this value on the origin leg."
  value       = aws_cloudfront_distribution.site.domain_name
}

output "github_deploy_role_arn" {
  description = "Paste into the AWS_DEPLOY_ROLE_ARN repository variable for frontend-deploy.yml."
  value       = aws_iam_role.github_deploy.arn
}
