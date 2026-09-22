# This file is what makes "Cloudflare HTTPS, not CloudFront HTTPS" true.
#
# The browser only ever sees a Cloudflare certificate for nodepulsecaringal.xyz,
# because these records stay proxied (orange-clouded) rather than DNS-only.
# CloudFront also holds a certificate for this domain now (see acm.tf), but
# that one is presented only on the private Cloudflare-to-CloudFront hop —
# no visitor's browser ever negotiates TLS with CloudFront directly.

# DNS records for the site itself.
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

# No origin rule needed to make CloudFront accept the request: it now
# recognizes nodepulsecaringal.xyz directly as a configured alias with a
# matching certificate (acm.tf), so Cloudflare's ordinary reverse-proxy
# behaviour — forwarding the request's real Host header and SNI unmodified —
# already works. That is a direct consequence of the HostHeader override
# entitlement failure above; see acm.tf for the full explanation.

# strict: CloudFront now answers with a certificate that genuinely covers this
# domain, so Cloudflare can verify it instead of merely encrypting blindly.
resource "cloudflare_zone_settings_override" "this" {
  zone_id = data.cloudflare_zone.this.id

  settings {
    ssl                      = var.cloudflare_ssl_mode
    always_use_https         = "on"
    automatic_https_rewrites = "on"
    min_tls_version          = "1.2"
  }
}
