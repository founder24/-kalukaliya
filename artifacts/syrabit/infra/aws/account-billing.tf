# infra/aws/account-billing.tf
#
# Phase 1b — AWS landing zone (Task #328).
#
# Account-level guardrails for the AWS landing zone that hosts the async
# worker tier (SQS + Lambda + SES) under ADR-0001. AWS Activate credits
# cover the expected steady-state spend; these budgets exist to catch
# runaway cost before it eats the credit balance.
#
# Scope: account hardening only. No application workload is deployed by
# this file.

locals {
  lz_project       = "syrabit"
  lz_env           = "prod"
  lz_primary_region   = "ap-south-1"  # workers + SES live closest to users (IN)
  lz_secondary_region = "us-east-1"   # DR region; also where Bedrock proxy lives
  lz_ops_email     = "ops@syrabit.ai"

  lz_common_tags = {
    project       = "syrabit"
    environment   = "prod"
    managed-by    = "terraform"
    landing-zone  = "aws-async-workers"
    credit-source = "aws-activate"
    owner         = "infra"
  }
}

# ─── Monthly cost budget ─────────────────────────────────────────────────────
# Activate balance is $1 000; budget is intentionally low so any drift trips
# the alarm long before the credit pool is at risk.

# Thresholds 60 / 80 / 95 mirror the founder-locked degradation ladder
# (`cost_caps.DEGRADATION_PCT_PAUSE_BATCH` / `_VOICE_OFF` / `_FREE_503`)
# so an AWS-side budget breach lands in the same operator inbox as the
# in-app cost-cap trip — see Task #4 and the "Cloud budget mirror"
# gotcha in the root `replit.md`. Email goes to `lz_ops_email`; SNS
# topic notification feeds the existing `syrabit-ops-alerts` Slack
# subscription so the on-call sees both channels light up at once.

resource "aws_budgets_budget" "monthly_cost" {
  name              = "${local.lz_project}-${local.lz_env}-monthly"
  budget_type       = "COST"
  limit_amount      = "100"
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = "2026-05-01_00:00"

  # 60 % — pause batch jobs (Meter §J first ladder rung).
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 60
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [local.lz_ops_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.ops_alerts.arn]
  }

  # 80 % — voice off.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [local.lz_ops_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.ops_alerts.arn]
  }

  # 95 % — free tier 503.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 95
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [local.lz_ops_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.ops_alerts.arn]
  }

  # Forecast 80 % — early warning before the actual ladder fires.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [local.lz_ops_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.ops_alerts.arn]
  }
}

# ─── Free-tier / credit-burn anomaly detector ───────────────────────────────
# Catches the case where Activate credits are silently being consumed faster
# than the steady-state worker tier should burn them.

# NOTE (deferred 2026-05-09): AWS account already has a default
# DIMENSIONAL+SERVICE Anomaly Monitor ("Default-Services-Monitor") which
# uses up the per-account dimensional-monitor slot. Re-enable after
# subscribing to the existing Default monitor or migrating it to TF.
# resource "aws_ce_anomaly_monitor" "account_wide" {
#   name              = "${local.lz_project}-account-wide"
#   monitor_type      = "DIMENSIONAL"
#   monitor_dimension = "SERVICE"
# }
#
# resource "aws_ce_anomaly_subscription" "account_wide" {
#   name             = "${local.lz_project}-account-wide-anomaly"
#   monitor_arn_list = [aws_ce_anomaly_monitor.account_wide.arn]
#   frequency        = "DAILY"
#   threshold_expression {
#     dimension {
#       key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
#       values        = ["25"]
#       match_options = ["GREATER_THAN_OR_EQUAL"]
#     }
#   }
#   subscriber {
#     type    = "EMAIL"
#     address = local.lz_ops_email
#   }
# }
