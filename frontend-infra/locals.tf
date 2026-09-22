data "aws_caller_identity" "current" {}

data "cloudflare_zone" "this" {
  name = var.domain_name
}

locals {
  # Bucket names are globally unique, so the account id keeps this collision
  # free without dragging a random provider into the stack.
  bucket_name = "${var.project_name}-site-${data.aws_caller_identity.current.account_id}"

  site_hostnames = var.create_www ? [var.domain_name, "www.${var.domain_name}"] : [var.domain_name]

  # Restricts which GitHub ref may assume the deploy role.
  github_sub = var.github_deploy_branch == "*" ? "repo:${var.github_repo}:*" : "repo:${var.github_repo}:ref:refs/heads/${var.github_deploy_branch}"

  tags = {
    Project   = var.project_name
    ManagedBy = "terraform"
    Repo      = var.github_repo
  }
}
