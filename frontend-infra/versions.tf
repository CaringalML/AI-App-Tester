terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }

    # Pinned to v4. The v5 provider renamed most resources, for example
    # cloudflare_record became cloudflare_dns_record, so do not float this
    # to v5 without rewriting cloudflare.tf.
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.40"
    }
  }

  # Remote state is deliberately left unconfigured. For a single operator
  # prototype, local state is fine and one less thing to stand up. Add an S3
  # backend here the moment a second person needs to run an apply.
}
