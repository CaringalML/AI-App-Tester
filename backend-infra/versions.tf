terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }

    # Same pin as frontend-infra. v5 renamed most resources.
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.40"
    }
  }

  # Local state, same reasoning as frontend-infra: one operator, one machine.
}
