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
    Cloudflare API token. Needs Zone.DNS edit, Zone.Zone Settings edit,
    Zone.Origin Rules edit and Zone.Zone read, scoped to this one zone.
    Origin Rules, not Config Rules — Config Rules is a different ruleset
    product (http_config_settings) and will not authorize the
    http_request_origin ruleset this stack creates.
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

variable "github_deploy_branch" {
  description = "Only this branch may assume the deploy role. Use * to allow any ref."
  type        = string
  default     = "main"
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

variable "override_origin_sni" {
  description = <<-EOT
    Also rewrite the TLS SNI that Cloudflare sends to CloudFront, not just the
    Host header. Leave this on where the plan supports it. If an apply fails
    saying the sni field is unavailable, set it to false and keep the Cloudflare
    SSL mode at full rather than full (strict), which is what ssl_mode does.
  EOT
  type        = bool
  default     = true
}

variable "cloudflare_ssl_mode" {
  description = <<-EOT
    How Cloudflare talks to CloudFront. full encrypts but does not verify the
    certificate name, which is required here because CloudFront answers with its
    own *.cloudfront.net certificate and knows nothing about this domain.
  EOT
  type        = string
  default     = "full"

  validation {
    condition     = contains(["full", "strict"], var.cloudflare_ssl_mode)
    error_message = "Must be full or strict. flexible would leave the origin leg unencrypted."
  }
}
