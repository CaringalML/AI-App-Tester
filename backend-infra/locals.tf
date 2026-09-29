data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

data "cloudflare_zone" "this" {
  name = var.domain_name
}

# Cloudflare publishes the address ranges its edge connects to origins from.
# The load balancer only accepts those, so nobody can reach the API around
# Cloudflare, and the cf-connecting-ip header the rate limiter trusts cannot
# be forged by a caller talking to the ALB directly.
data "cloudflare_ip_ranges" "this" {}

locals {
  name        = "${var.project_name}-api"
  api_host    = "${var.api_subdomain}.${var.domain_name}"
  account_id  = data.aws_caller_identity.current.account_id
  azs         = slice(data.aws_availability_zones.available.names, 0, 2)
  vpc_cidr    = "10.40.0.0/16"
  container   = "api"
  api_port    = 8000
  cors_origin = "https://${var.domain_name},https://www.${var.domain_name}"

  github_repo_parts = split("/", var.github_repo)
  # GitHub issues ID-qualified subjects (owner@id/repo@id); see frontend-infra/locals.tf.
  github_sub_pattern = "repo:${local.github_repo_parts[0]}@*/${local.github_repo_parts[1]}@*:environment:${var.github_environment}"

  tags = {
    Project   = var.project_name
    Component = "api"
    ManagedBy = "terraform"
    Repo      = var.github_repo
  }
}
