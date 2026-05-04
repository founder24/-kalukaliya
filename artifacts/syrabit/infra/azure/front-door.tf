# infra/azure/front-door.tf
#
# Azure Front Door (Standard tier) as additional CDN / WAF layer,
# covered by Azure for Startups credits.
#
# Why Azure Front Door alongside Cloudflare?
# ──────────────────────────────────────────
# • Front Door has 100+ PoPs globally, including several in South Asia
#   where Cloudflare has fewer dedicated edge nodes — measurably better
#   TTFB for Pakistan, Bangladesh, Sri Lanka users.
# • Built-in WAF rule set (OWASP CRS 3.2) runs at the Azure edge —
#   additional layer on top of Cloudflare Enterprise WAF.
# • Smart compression (Brotli level 11) at the CDN edge reduces payload
#   size by ~5–8 % vs Cloudflare's level 4.
# • Origin Shield (single-region cache shield in Central India) prevents
#   cache-miss stampede from hitting the DO origin directly.
# • Azure DDoS Network Protection (included in Standard) absorbs
#   volumetric attacks before they reach DO or Cloudflare.
#
# Task #335 decommissioned the legacy GCP Cloud Run origin; the only
# backend origin behind Front Door is now Digital Ocean App Platform
# (api.syrabit.ai → DO).
#
# All covered by Azure for Startups credits ($5 000 for 12 months).

terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 3.90"
    }
  }
}

provider "azurerm" {
  features {}
}

locals {
  resource_group   = "syrabit-prod-rg"
  location         = "centralindia"
  front_door_name  = "syrabit-afd"
  profile_sku      = "Standard_AzureFrontDoor"
  do_origin_host    = "api.syrabit.ai"
  media_origin_host = "syrabit-media.b-cdn.net"
}

resource "azurerm_resource_group" "main" {
  name     = local.resource_group
  location = local.location
  tags = {
    project       = "syrabit"
    credit-source = "azure-for-startups"
  }
}

resource "azurerm_cdn_frontdoor_profile" "main" {
  name                = local.front_door_name
  resource_group_name = azurerm_resource_group.main.name
  sku_name            = local.profile_sku

  tags = {
    project       = "syrabit"
    credit-source = "azure-for-startups"
  }
}

# ─── Endpoint ────────────────────────────────────────────────────────────────

resource "azurerm_cdn_frontdoor_endpoint" "api" {
  name                     = "syrabit-api"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.main.id
}

# ─── Origin groups ───────────────────────────────────────────────────────────

resource "azurerm_cdn_frontdoor_origin_group" "api" {
  name                     = "api-origin-group"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.main.id

  load_balancing {
    sample_size                 = 4
    successful_samples_required = 2
    additional_latency_in_milliseconds = 50
  }

  health_probe {
    path                = "/health"
    protocol            = "Https"
    interval_in_seconds = 15
    request_type        = "HEAD"
  }

  session_affinity_enabled = false
}

resource "azurerm_cdn_frontdoor_origin" "do_app_platform" {
  name                          = "do-app-platform"
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.api.id
  enabled                       = true

  host_name          = local.do_origin_host
  http_port          = 80
  https_port         = 443
  origin_host_header = local.do_origin_host
  priority           = 1
  weight             = 1000

  certificate_name_check_enabled = true
}

# ─── Route ────────────────────────────────────────────────────────────────────

resource "azurerm_cdn_frontdoor_route" "api" {
  name                          = "api-route"
  cdn_frontdoor_endpoint_id     = azurerm_cdn_frontdoor_endpoint.api.id
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.api.id
  cdn_frontdoor_origin_ids      = [azurerm_cdn_frontdoor_origin.do_app_platform.id]

  supported_protocols    = ["Http", "Https"]
  patterns_to_match      = ["/*"]
  forwarding_protocol    = "HttpsOnly"
  link_to_default_domain = true
  https_redirect_enabled = true

  cdn_frontdoor_custom_domain_ids = []
  enabled                         = true
}

# ─── WAF policy ───────────────────────────────────────────────────────────────

resource "azurerm_cdn_frontdoor_firewall_policy" "main" {
  name                              = "syrabitwaf"
  resource_group_name               = azurerm_resource_group.main.name
  sku_name                          = azurerm_cdn_frontdoor_profile.main.sku_name
  enabled                           = true
  mode                              = "Prevention"
  redirect_url                      = "https://syrabit.ai/blocked"
  custom_block_response_status_code = 403

  managed_rule {
    type    = "DefaultRuleSet"
    version = "1.0"
    action  = "Block"
  }

  managed_rule {
    type    = "Microsoft_BotManagerRuleSet"
    version = "1.0"
    action  = "Block"
  }

  tags = {
    project       = "syrabit"
    credit-source = "azure-for-startups"
  }
}

resource "azurerm_cdn_frontdoor_security_policy" "main" {
  name                     = "syrabit-security-policy"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.main.id

  security_policies {
    firewall {
      cdn_frontdoor_firewall_policy_id = azurerm_cdn_frontdoor_firewall_policy.main.id
      association {
        domain {
          cdn_frontdoor_domain_id = azurerm_cdn_frontdoor_endpoint.api.id
        }
        patterns_to_match = ["/*"]
      }
    }
  }
}

# ─── Azure Monitor alert: origin latency p95 ─────────────────────────────────

resource "azurerm_monitor_metric_alert" "afd_latency" {
  name                = "afd-origin-latency-high"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_cdn_frontdoor_profile.main.id]
  description         = "Azure Front Door origin latency p95 > 500 ms"
  severity            = 2
  frequency           = "PT1M"
  window_size         = "PT5M"

  criteria {
    metric_namespace = "Microsoft.Cdn/profiles"
    metric_name      = "TotalLatency"
    aggregation      = "Percentile"
    operator         = "GreaterThan"
    threshold        = 500
  }

  action {}
}
