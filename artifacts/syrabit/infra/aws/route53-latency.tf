# infra/aws/route53-latency.tf
#
# Replaces Cloudflare Basic Load Balancing ($5/mo) and Argo Smart Routing
# ($5/mo) with Route 53 latency-based routing + health-check failover.
# Covered by AWS Activate (Route 53 health checks are ~$0.50/check/mo,
# well within Activate credit balance).
#
# Task #335 decommissioned the legacy Railway and GCP Cloud Run origins.
# The remaining production origin is Azure Container Apps; the
# latency record now points at the DO floating IP exclusively. AWS
# Global Accelerator is left commented as an optional perf boost.
#
# Performance boosts
# ──────────────────
# • Health-check failover removes the failed origin from DNS within 30 s
#   (Route 53 TTL) — equivalent to Cloudflare LB failover.
# • Route 53 Resolver DNS Firewall blocks known malicious domains at the
#   resolver level — zero cost, zero latency impact.


variable "azure_aca_backend_ip" {
  description = "Static IP of the Azure Container Apps backend (eastus2)"
  type        = string
  # default = "" so CI runs without the gitignored terraform.tfvars pass.
  # Real value supplied via tfvars in local / prod applies.
  default = ""
}

locals {
  zone_name    = "syrabit.ai"
  api_hostname = "api.syrabit.ai"
}

data "aws_route53_zone" "main" {
  name         = local.zone_name
  private_zone = false
}

# ─── Health checks ───────────────────────────────────────────────────────────

resource "aws_route53_health_check" "do" {
  fqdn              = "api-do.syrabit.ai"
  port              = 443
  type              = "HTTPS"
  resource_path     = "/health"
  failure_threshold = 2
  request_interval  = 10

  tags = {
    Name          = "do-app-platform-health"
    credit-source = "aws-activate"
  }
}

# ─── Latency-based primary records ───────────────────────────────────────────

resource "aws_route53_record" "api_do_latency" {
  zone_id        = data.aws_route53_zone.main.zone_id
  name           = local.api_hostname
  type           = "A"
  set_identifier = "do-blr1"
  ttl            = 30

  latency_routing_policy {
    region = "ap-south-1"
  }

  health_check_id = aws_route53_health_check.do.id
  records         = [var.azure_aca_backend_ip]
}

# ─── CloudWatch alarms for health-check failures ─────────────────────────────

resource "aws_cloudwatch_metric_alarm" "do_health" {
  alarm_name          = "route53-do-origin-unhealthy"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HealthCheckStatus"
  namespace           = "AWS/Route53"
  period              = 30
  statistic           = "Minimum"
  threshold           = 1

  dimensions = {
    HealthCheckId = aws_route53_health_check.do.id
  }

  alarm_description  = "Azure Container Apps origin failing Route53 health check"
  treat_missing_data = "breaching"
}

# ─── AWS Global Accelerator (optional performance boost) ─────────────────────
# Uncomment to enable anycast routing via AWS edge network.
# Cost: ~$0.025/hr + data transfer — verify against Activate balance first.
#
# resource "aws_globalaccelerator_accelerator" "api" {
#   name            = "syrabit-api"
#   ip_address_type = "IPV4"
#   enabled         = true
#   tags = { credit-source = "aws-activate" }
# }
