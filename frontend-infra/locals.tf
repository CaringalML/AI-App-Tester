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
  #
  # The plain repo:owner/repo:environment:name form documented by GitHub is
  # NOT what actually got issued here. The real token decoded from a live run
  # was repo:CaringalML@75959399/AI-App-Tester@1379702685:environment:production
  # — GitHub inserts the numeric owner and repository IDs alongside the
  # names, as owner@ownerID/repo@repoID, evidently to stop a trust policy
  # matching by name from silently surviving a repo rename or ownership
  # transfer to a different underlying repository. The wildcards below match
  # that shape without hardcoding IDs that have nothing to do with this
  # Terraform config and could look like magic numbers to a future reader.
  github_repo_parts  = split("/", var.github_repo)
  github_sub_pattern = "repo:${local.github_repo_parts[0]}@*/${local.github_repo_parts[1]}@*:environment:${var.github_environment}"

  tags = {
    Project   = var.project_name
    ManagedBy = "terraform"
    Repo      = var.github_repo
  }
}
