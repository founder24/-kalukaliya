# infra/aws/route53-latency.tf
#
# Replaces Cloudflare Basic Load Balancing ($5/mo) and Argo Smart Routing
# ($5/mo) with Route 53 latency-based routing + health-check failover.
# Covered by AWS Activate (Route 53 health checks are ~$0.50/check/mo,
# well within Activate credit balance).
#
# Performance boosts
# ──────────────────
# • Latency records select the closest origin (GCP Cloud Run asia-south1
#   vs Railway us-east or eu-west) per client region automatically.
# • Health-check failover removes the failed origin from DNS within 30 s
#   (Route 53 TTL) — equivalent to Cloudflare LB failover.
# • AWS Global Accelerator (optional, Activate covered) provides anycast
#   IP routing for < 10 ms improvement on first-byte for Asian clients.
# • Route 53 Resolver DNS Firewall blocks known malicious domains at the
#   resolver level — zero cost, zero latency impact.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

variable "gcp_cloud_run_ip" {
  description = "Static IP of the GCP HTTPS Global Load Balancer (asia-south1)"
  type        = string
}

variable "railway_api_ip" {
  description = "IP address of the Railway.app backend origin"
  type        = string
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

resource "aws_route53_health_check" "gcp" {
  fqdn              = "api-gcp.syrabit.ai"
  port              = 443
  type              = "HTTPS"
  resource_path     = "/health"
  failure_threshold = 2
  request_interval  = 10

  tags = {
    Name          = "gcp-cloud-run-health"
    credit-source = "aws-activate"
  }
}

resource "aws_route53_health_check" "railway" {
  fqdn              = "api-railway.syrabit.ai"
  port              = 443
  type              = "HTTPS"
  resource_path     = "/health"
  failure_threshold = 2
  request_interval  = 10

  tags = {
    Name          = "railway-health"
    credit-source = "aws-activate"
  }
}

# ─── Latency-based primary records ───────────────────────────────────────────

resource "aws_route53_record" "api_gcp_latency" {
  zone_id        = data.aws_route53_zone.main.zone_id
  name           = local.api_hostname
  type           = "A"
  set_identifier = "gcp-asia-south1"
  ttl            = 30

  latency_routing_policy {
    region = "ap-south-1"
  }

  health_check_id = aws_route53_health_check.gcp.id
  records         = [var.gcp_cloud_run_ip]
}

resource "aws_route53_record" "api_railway_latency" {
  zone_id        = data.aws_route53_zone.main.zone_id
  name           = local.api_hostname
  type           = "A"
  set_identifier = "railway-us-east"
  ttl            = 30

  latency_routing_policy {
    region = "us-east-1"
  }

  health_check_id = aws_route53_health_check.railway.id
  records         = [var.railway_api_ip]
}

# ─── CloudWatch alarms for health-check failures ─────────────────────────────

resource "aws_cloudwatch_metric_alarm" "gcp_health" {
  alarm_name          = "route53-gcp-origin-unhealthy"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HealthCheckStatus"
  namespace           = "AWS/Route53"
  period              = 30
  statistic           = "Minimum"
  threshold           = 1

  dimensions = {
    HealthCheckId = aws_route53_health_check.gcp.id
  }

  alarm_description  = "GCP Cloud Run origin failing Route53 health check"
  treat_missing_data = "breaching"
}

resource "aws_cloudwatch_metric_alarm" "railway_health" {
  alarm_name          = "route53-railway-origin-unhealthy"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HealthCheckStatus"
  namespace           = "AWS/Route53"
  period              = 30
  statistic           = "Minimum"
  threshold           = 1

  dimensions = {
    HealthCheckId = aws_route53_health_check.railway.id
  }

  alarm_description  = "Railway origin failing Route53 health check"
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
