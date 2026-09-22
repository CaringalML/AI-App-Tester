variable "project_name" {
  description = "Short slug used to name and tag every resource."
  type        = string
  default     = "ai-app-tester"
}

variable "aws_region" {
  description = "Region for the S3 bucket. CloudFront itself is global."
  type        = string
  default     = "ap-southeast-2"
}

variable "domain_name" {
  description = "Apex domain, which must match an existing Cloudflare zone."
  type        = string
  default     = "nodepulsecaringal.xyz"
}

variable "create_www" {
  description = "Also serve the site on www, as a proxied CNAME."
  type        = bool
  default     = true
}

variable "cloudflare_api_token" {
  description = <<-EOT
    Cloudflare API token. Needs Zone.DNS edit, Zone.Zone Settings edit and
    Zone.Zone read, scoped to this one zone. Earlier versions of this stack
    also needed Zone.Origin Rules edit for a Host header rewrite; that rule
    is gone now (see acm.tf) because Origin Rules' HostHeader override is a
    paid-plan feature and this zone is on Free.
    Supply it with TF_VAR_cloudflare_api_token rather than a tfvars file.
  EOT
  type        = string
  sensitive   = true
}

variable "github_repo" {
  description = "owner/name of the repository allowed to assume the deploy role."
  type        = string
  default     = "CaringalML/AI-App-Tester"
}

variable "github_environment" {
  description = <<-EOT
    Must match the `environment:` key on the deploy job in
    frontend-deploy.yml. Once a job declares an environment, GitHub's OIDC
    token subject switches entirely to repo:<org>/<repo>:environment:<name>
    and drops the branch-based form — the branch that triggered the run no
    longer appears in the subject at all. Branch restriction instead comes
    from the workflow's own `on.push.branches` filter, and optionally from
    required reviewers or deployment branch rules on the GitHub environment
    itself (Settings → Environments), not from this trust policy.
  EOT
  type        = string
  default     = "production"
}

variable "create_github_oidc_provider" {
  description = <<-EOT
    Create the GitHub OIDC provider in this account. An account can hold only
    one provider per URL, so set this to false if another stack already made it.
  EOT
  type        = bool
  default     = true
}

variable "cloudfront_price_class" {
  description = <<-EOT
    PriceClass_All keeps Australia and New Zealand edges in play, which matters
    because Cloudflare fetches from the nearest CloudFront edge on a cache miss.
    Drop to PriceClass_100 to trim cost at the expense of origin fetch latency.
  EOT
  type        = string
  default     = "PriceClass_All"

  validation {
    condition     = contains(["PriceClass_All", "PriceClass_200", "PriceClass_100"], var.cloudfront_price_class)
    error_message = "Must be PriceClass_All, PriceClass_200 or PriceClass_100."
  }
}

variable "cloudflare_ssl_mode" {
  description = <<-EOT
    How Cloudflare talks to CloudFront. strict verifies that the origin's
    certificate covers this domain, which it does now that CloudFront holds an
    ACM certificate for it (acm.tf). Only fall back to full if the certificate
    is ever removed and CloudFront goes back to answering on its own
    *.cloudfront.net certificate.
  EOT
  type        = string
  default     = "strict"

  validation {
    condition     = contains(["full", "strict"], var.cloudflare_ssl_mode)
    error_message = "Must be full or strict. flexible would leave the origin leg unencrypted."
  }
}
