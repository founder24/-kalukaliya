# infra/azure/account-billing.tf
#
# Phase 1c — Azure landing zone (Task #329).
#
# Account-level guardrails for the Azure landing zone that hosts the
# cron tier (Container Apps Jobs) and the unified observability sink
# (Log Analytics + Application Insights) under ADR-0001. Azure for
# Startups credits cover the expected steady-state spend; the budget
# below exists to catch runaway cost before it eats the credit balance.
#
# Scope: subscription hardening + shared locals only. No application
# workload is deployed by this file. The cron job container images and
# the cron-jobs themselves land in Phase 4 (downstream task: "Port
# async workers to AWS (SQS+Lambda) and cron jobs to Azure Container
# Apps").

locals {
  lz_project          = "syrabit"
  lz_env              = "prod"
  lz_primary_region   = "centralindia" # cron + observability live closest to users (IN)
  lz_secondary_region = "eastasia"     # documented DR pairing; not active today
  lz_ops_email        = "ops@syrabit.ai"

  # Dedicated resource group for cron + observability, kept separate
  # from the existing front-door / cosmos resources so the blast radius
  # of a cron-tier teardown does not touch the edge / cache stack.
  lz_resource_group = "syrabit-cron-obs-rg"

  lz_common_tags = {
    project       = "syrabit"
    environment   = "prod"
    managed-by    = "terraform"
    landing-zone  = "azure-cron-observability"
    credit-source = "azure-for-startups"
    owner         = "infra"
  }
}

data "azurerm_subscription" "current" {}

resource "azurerm_resource_group" "cron_obs" {
  name     = local.lz_resource_group
  location = local.lz_primary_region
  tags     = local.lz_common_tags
}

# ─── Monthly cost budget ─────────────────────────────────────────────────────
# Azure for Startups balance is $2 500 over 12 months; budget is
# intentionally low so any drift trips the alarm long before the credit
# pool is at risk. Budget is scoped to the subscription rather than the
# RG so it also catches the existing front-door / cosmos resources.

resource "azurerm_consumption_budget_subscription" "monthly_cost" {
  name            = "${local.lz_project}-${local.lz_env}-monthly"
  subscription_id = data.azurerm_subscription.current.id

  amount     = 200
  time_grain = "Monthly"

  time_period {
    start_date = "2026-05-01T00:00:00Z"
  }

  notification {
    enabled        = true
    threshold      = 50
    threshold_type = "Actual"
    operator       = "GreaterThan"

    contact_emails = [local.lz_ops_email]
  }

  notification {
    enabled        = true
    threshold      = 80
    threshold_type = "Forecasted"
    operator       = "GreaterThan"

    contact_emails = [local.lz_ops_email]
  }

  notification {
    enabled        = true
    threshold      = 100
    threshold_type = "Actual"
    operator       = "GreaterThan"

    contact_emails = [local.lz_ops_email]
  }
}

output "cron_obs_resource_group_name" {
  value       = azurerm_resource_group.cron_obs.name
  description = "Resource group hosting the cron + observability landing zone."
}

output "cron_obs_resource_group_id" {
  value       = azurerm_resource_group.cron_obs.id
  description = "Resource ID of the cron + observability RG."
}
