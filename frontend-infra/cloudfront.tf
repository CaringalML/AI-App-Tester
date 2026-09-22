# Deliberately no aliases and no ACM certificate on this distribution.
#
# Cloudflare terminates TLS for the browser using its own edge certificate, and
# CloudFront answers Cloudflare on its default *.cloudfront.net certificate.
# CloudFront routes requests by Host header, so it would reject traffic arriving
# as nodepulsecaringal.xyz. The Cloudflare origin rule in cloudflare.tf rewrites
# the Host header, and optionally the SNI, to the distribution domain below.

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${var.project_name}-oac"
  description                       = "Lets the distribution read the private site bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

resource "aws_cloudfront_response_headers_policy" "site" {
  name    = "${var.project_name}-security-headers"
  comment = "Baseline security headers. HSTS is set at Cloudflare, which owns the public TLS."

  security_headers_config {
    content_type_options {
      override = true
    }

    frame_options {
      frame_option = "DENY"
      override     = true
    }

    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }

    xss_protection {
      mode_block = true
      protection = true
      override   = true
    }
  }
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${var.project_name} static site, fronted by Cloudflare"
  default_root_object = "index.html"
  price_class         = var.cloudfront_price_class

  origin {
    origin_id                = "s3-${aws_s3_bucket.site.id}"
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id       = "s3-${aws_s3_bucket.site.id}"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.site.id
  }

  # The app is a single page app with client side routing, so any unknown path
  # has to return index.html rather than an S3 error document. The 200 is what
  # makes deep links work instead of showing the browser an error page.
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    # This is the whole point of the design. No ACM certificate is issued or
    # validated, so there is no DNS validation dance and nothing to renew here.
    cloudfront_default_certificate = true
  }
}
