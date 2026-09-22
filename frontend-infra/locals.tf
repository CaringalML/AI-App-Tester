data "aws_caller_identity" "current" {}

data "cloudflare_zone" "this" {
  name = var.domain_name
}

locals {
  # Bucket names are globally unique, so the account id keeps this collision
  # free without dragging a random provider into the stack.
  bucket_name = "${var.project_name}-site-${data.aws_caller_identity.current.account_id}"

  site_hostnames = var.create_www ? [var.domain_name, "www.${var.domain_name}"] : [var.domain_name]

  # Matches the OIDC subject GitHub issues for a job that declares
  # `environment: <name>` — see github_environment in variables.tf for why
  # this isn't ref/branch-based.
  github_sub = "repo:${var.github_repo}:environment:${var.github_environment}"

  tags = {
    Project   = var.project_name
    ManagedBy = "terraform"
    Repo      = var.github_repo
  }
}
