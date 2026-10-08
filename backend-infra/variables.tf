variable "project_name" {
  description = "Short slug used to name and tag every resource."
  type        = string
  default     = "ai-app-tester"
}

variable "aws_region" {
  description = "Region for everything in this stack. Sydney keeps latency low from New Zealand."
  type        = string
  default     = "ap-southeast-2"
}

variable "domain_name" {
  description = "Apex domain of the existing Cloudflare zone."
  type        = string
  default     = "nodepulsecaringal.xyz"
}

variable "api_subdomain" {
  description = "The API is served at <api_subdomain>.<domain_name>."
  type        = string
  default     = "api"
}

variable "cloudflare_api_token" {
  description = <<-EOT
    Same token as frontend-infra: Zone.DNS edit, Zone.Zone Settings edit and
    Zone.Zone read on this zone. Supply it with TF_VAR_cloudflare_api_token.
  EOT
  type        = string
  sensitive   = true
}

variable "github_repo" {
  description = "owner/name of the repository allowed to deploy the API."
  type        = string
  default     = "CaringalML/AI-App-Tester"
}

variable "github_environment" {
  description = "Must match the environment: key on the deploy job in backend-deploy.yml."
  type        = string
  default     = "production"
}

variable "task_cpu" {
  description = "Fargate CPU units. Chromium plus a Python API is comfortable at 1 vCPU."
  type        = number
  default     = 1024
}

variable "task_memory" {
  description = "Fargate memory in MiB. Headless Chromium on a heavy page can use over 1 GB."
  type        = number
  default     = 2048
}

variable "image_tag" {
  description = <<-EOT
    Tag Terraform puts in the task definition it registers. The deploy workflow
    replaces it with the commit SHA on every deploy, so this only matters for the
    very first revision.
  EOT
  type        = string
  default     = "latest"
}

variable "anthropic_model" {
  description = "Claude model the scanner uses."
  type        = string
  default     = "claude-opus-5-5"
}

variable "anthropic_workspace_id" {
  description = <<-EOT
    Only needed when the API key is an organization-level key that is not scoped
    to a workspace; the Claude API rejects those without this header. Leave empty
    for a workspace-scoped key. Not a secret: it identifies, it does not authorize.
  EOT
  type        = string
  default     = ""
}

variable "agent_effort" {
  description = "Claude effort for the exploration loop: low, medium, high, xhigh or max."
  type        = string
  default     = "medium"

  # The API accepts only these exact lowercase names; "Max" would fail every scan.
  validation {
    condition     = contains(["low", "medium", "high", "xhigh", "max"], var.agent_effort)
    error_message = "agent_effort must be one of: low, medium, high, xhigh, max (lowercase)."
  }
}

variable "rate_limit_scans" {
  description = "Scans one client address may start per hour. Raise it for a demo where a whole room shares one office address."
  type        = number
  default     = 6
}

variable "max_agent_steps" {
  description = "Browser actions Claude may take in a quick scan (the default). Caps both time and cost."
  type        = number
  default     = 30
}

variable "thorough_agent_steps" {
  description = "Browser actions Claude may take in a thorough scan, chosen per scan in the form."
  type        = number
  default     = 100
}

variable "thorough_timeout_seconds" {
  description = "Wall-clock limit for a thorough scan, in seconds."
  type        = number
  default     = 1800

  # The website stops waiting for a scan after 40 minutes (GIVE_UP_MS in src/lib/api.ts).
  validation {
    condition     = var.thorough_timeout_seconds >= 60 && var.thorough_timeout_seconds <= 2400
    error_message = "thorough_timeout_seconds must be between 60 and 2400; the website waits at most 40 minutes."
  }
}
