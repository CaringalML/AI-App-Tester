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
  description = "CloudFront's own hostname. Not needed by Cloudflare for routing since the distribution answers to the real domain directly — kept for reference and debugging."
  value       = aws_cloudfront_distribution.site.domain_name
}

output "acm_certificate_arn" {
  description = "The us-east-1 certificate CloudFront presents on the Cloudflare-to-CloudFront hop. Never seen by a browser."
  value       = aws_acm_certificate_validation.site.certificate_arn
}

output "github_deploy_role_arn" {
  description = "Paste into the AWS_DEPLOY_ROLE_ARN repository variable for frontend-deploy.yml."
  value       = aws_iam_role.github_deploy.arn
}
