# infra/gcp/budget.tf — monthly budget + threshold-based alerts that
# feed the V4 §10 Rule C credit-burn meter.
#
# Notify-only (no auto-flip): the alert lands in #syrabit-oncall via
# Pub/Sub + slack_notifier. Spend levers stay manual per V4 §10.

variable "gcp_monthly_budget_usd" {
  description = "Monthly budget cap for the GCP AI-API + observability surface (post-Vertex-removal, post-Cohere/Cerebras/Voyage purge per sibling tasks #490/#491)."
  type        = number
  default     = 200
}

resource "google_billing_budget" "monthly" {
  billing_account = var.gcp_billing_account_id

  display_name = "syrabit-prod-monthly"

  budget_filter {
    projects = ["projects/${var.gcp_project_id}"]

    # Credit treatment: include credits in the spend basis so the alert
    # fires on net-of-credit burn, matching the meter math in
    # `credit_burn_meter.py`.
    credit_types_treatment = "INCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = var.gcp_monthly_budget_usd
    }
  }

  # 50 / 80 / 100 % current-spend thresholds. The 80 % threshold is the
  # one V4 §10 Rule C uses to fire the Slack alert.
  threshold_rules {
    threshold_percent = 0.5
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 0.8
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }

  # 100 % forecast threshold catches a runaway month before it lands.
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  # Email channel only here; the Pub/Sub → Slack path is wired out of
  # band by the meter pipeline so this Terraform stays self-contained.
  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.budget_email.id]
    disable_default_iam_recipients   = true
  }
}

resource "google_monitoring_notification_channel" "budget_email" {
  display_name = "Syrabit ops budget alerts"
  type         = "email"
  project      = var.gcp_project_id

  labels = {
    email_address = var.ops_alert_email
  }
}
