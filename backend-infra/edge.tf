# Public entry point: Cloudflare (browser-facing TLS) -> ALB (ACM cert for the
# api host, so Cloudflare's strict SSL mode can verify it) -> Fargate task.
# The same pattern as frontend-infra, pointed at a load balancer instead of CloudFront.

resource "aws_acm_certificate" "api" {
  domain_name       = local.api_host
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "cloudflare_record" "api_cert_validation" {
  for_each = {
    for opt in aws_acm_certificate.api.domain_validation_options : opt.domain_name => opt
  }

  zone_id = data.cloudflare_zone.this.id
  name    = trimsuffix(each.value.resource_record_name, ".")
  type    = each.value.resource_record_type
  content = trimsuffix(each.value.resource_record_value, ".")
  proxied = false
  ttl     = 60
}

resource "aws_acm_certificate_validation" "api" {
  certificate_arn         = aws_acm_certificate.api.arn
  validation_record_fqdns = [for r in cloudflare_record.api_cert_validation : r.hostname]
}

resource "aws_lb" "api" {
  name                       = local.name
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = aws_subnet.public[*].id
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  idle_timeout               = 60
}

resource "aws_lb_target_group" "api" {
  name                 = local.name
  port                 = local.api_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.this.id
  deregistration_delay = 15

  health_check {
    path                = "/healthz"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.api.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.api.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

# Proxied, so browsers only ever see Cloudflare's certificate. The zone's SSL
# mode (strict) is owned by frontend-infra; it is a zone-wide setting and is
# deliberately not managed twice.
resource "cloudflare_record" "api" {
  zone_id = data.cloudflare_zone.this.id
  name    = var.api_subdomain
  type    = "CNAME"
  content = aws_lb.api.dns_name
  proxied = true
  ttl     = 1
}
