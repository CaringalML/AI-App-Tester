# Why this file exists at all, given the goal is "Cloudflare handles HTTPS":
#
# The first version of this stack avoided an ACM certificate entirely by
# rewriting the Host header Cloudflare sends to CloudFront, so CloudFront
# would answer on its own default *.cloudfront.net certificate and never need
# to know this domain by name. That rewrite lives behind Cloudflare's Origin
# Rules "HostHeader override" capability, which is gated to paid plans — this
# zone is on Free, and the apply failed with "not entitled to use the
# HostHeader override".
#
# This file is the standard alternative: register the domain as a CloudFront
# alias with a matching certificate, so CloudFront accepts the request under
# its real Host header with no rewrite needed. It does not change what the
# browser sees. The Cloudflare DNS record for the domain stays proxied
# (orange-clouded) in cloudflare.tf, so Cloudflare still terminates the public
# connection with its own edge certificate — this ACM certificate is only ever
# presented on the private Cloudflare-to-CloudFront hop, which no visitor's
# browser ever touches. It also means the zone's SSL mode can move from full to
# full (strict), since the origin now answers with a certificate that
# genuinely matches its name.

resource "aws_acm_certificate" "site" {
  provider = aws.us_east_1

  domain_name               = var.domain_name
  subject_alternative_names = var.create_www ? ["www.${var.domain_name}"] : []
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# One DNS-only record per name ACM needs proven. Left unproxied (grey-clouded)
# on purpose — this record only has to be reachable by ACM's validator, and
# proxying it would route that check through Cloudflare's edge for no benefit.
resource "cloudflare_record" "cert_validation" {
  for_each = {
    for opt in aws_acm_certificate.site.domain_validation_options : opt.domain_name => opt
  }

  zone_id = data.cloudflare_zone.this.id
  name    = trimsuffix(each.value.resource_record_name, ".")
  type    = each.value.resource_record_type
  content = trimsuffix(each.value.resource_record_value, ".")
  proxied = false
  ttl     = 60
}

resource "aws_acm_certificate_validation" "site" {
  provider = aws.us_east_1

  certificate_arn         = aws_acm_certificate.site.arn
  validation_record_fqdns = [for r in cloudflare_record.cert_validation : r.hostname]
}
