# This file is what makes "Cloudflare HTTPS, not CloudFront HTTPS" true.
#
# The browser only ever sees a Cloudflare certificate for nodepulsecaringal.xyz.
# Cloudflare then opens its own HTTPS connection to CloudFront's default
# *.cloudfront.net certificate, which is why the CloudFront viewer_certificate
# block in cloudfront.tf stays on cloudfront_default_certificate rather than an
# ACM cert for this domain.

# DNS records for the site itself. Proxied (orange-clouded), so Cloudflare
# terminates TLS at its edge instead of passing the connection straight through.
resource "cloudflare_record" "root" {
  zone_id = data.cloudflare_zone.this.id
  name    = "@"
  type    = "CNAME"
  content = aws_cloudfront_distribution.site.domain_name
  proxied = true
  ttl     = 1 # Automatic. Required by the provider whenever proxied is true.
}

resource "cloudflare_record" "www" {
  count = var.create_www ? 1 : 0

  zone_id = data.cloudflare_zone.this.id
  name    = "www"
  type    = "CNAME"
  content = aws_cloudfront_distribution.site.domain_name
  proxied = true
  ttl     = 1
}

# Rewrites what Cloudflare sends to CloudFront on the origin leg, so a request
# that arrived as https://nodepulsecaringal.xyz reaches CloudFront looking like
# a request for its own domain. Without this CloudFront returns 403, because it
# routes purely on the Host header and this domain is not one of its aliases.
resource "cloudflare_ruleset" "origin_host_rewrite" {
  zone_id = data.cloudflare_zone.this.id
  name    = "${var.project_name}-origin-rewrite"
  kind    = "zone"
  phase   = "http_request_origin"

  rules {
    description = "Present CloudFront's own hostname on the origin connection"
    expression  = "true"
    action      = "route"

    action_parameters {
      host_header = aws_cloudfront_distribution.site.domain_name

      origin {
        host = aws_cloudfront_distribution.site.domain_name
      }

      dynamic "sni" {
        for_each = var.override_origin_sni ? [1] : []
        content {
          value = aws_cloudfront_distribution.site.domain_name
        }
      }
    }
  }
}

# full: Cloudflare encrypts the hop to CloudFront but does not check that the
# certificate name matches nodepulsecaringal.xyz, which it never will, since
# CloudFront answers on its own shared certificate. strict would fail closed.
resource "cloudflare_zone_settings_override" "this" {
  zone_id = data.cloudflare_zone.this.id

  settings {
    ssl                      = var.cloudflare_ssl_mode
    always_use_https         = "on"
    automatic_https_rewrites = "on"
    min_tls_version          = "1.2"
  }
}
