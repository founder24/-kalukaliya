# infra/aws/observability.tf
#
# Phase 1b — AWS landing zone (Task #328).
#
# Observability hooks for the worker tier:
#
# • Shared CloudWatch log group for ad-hoc worker logs that aren't tied
#   to a specific Lambda's auto-created /aws/lambda/* group.
# • Custom metric namespace `Syrabit/Workers` (declared via the IAM
#   policy in iam-github-oidc.tf — no resource needed; namespaces are
#   created on first PutMetricData call).
# • SNS topic `syrabit-ops-alerts` with the existing Slack incoming
#   webhook subscribed via the `slack/ops-webhook` Secrets Manager
#   entry (subscription URL is set out-of-band; we don't put the
#   webhook URL into Terraform state).

resource "aws_cloudwatch_log_group" "workers" {
  name              = "/${local.lz_project}/workers"
  retention_in_days = 30

  tags = merge(local.lz_common_tags, {
    Name = "/${local.lz_project}/workers"
  })
}

resource "aws_sns_topic" "ops_alerts" {
  name = "syrabit-ops-alerts"

  tags = merge(local.lz_common_tags, {
    Name = "syrabit-ops-alerts"
  })
}

# Topic policy: allow CloudWatch alarms in this account to publish.
data "aws_iam_policy_document" "ops_alerts_topic" {
  statement {
    sid    = "AllowCloudWatchAlarms"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.ops_alerts.arn]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [data.aws_caller_identity.lz.account_id]
    }
  }

  statement {
    sid    = "AllowBudgetsAndCostAnomaly"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com", "costalerts.amazonaws.com"]
    }

    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.ops_alerts.arn]
  }
}

resource "aws_sns_topic_policy" "ops_alerts" {
  arn    = aws_sns_topic.ops_alerts.arn
  policy = data.aws_iam_policy_document.ops_alerts_topic.json
}

# Slack subscription. The HTTPS endpoint URL is the Slack incoming
# webhook stored in `slack/ops-webhook` (Secrets Manager). It is set
# via `aws sns subscribe` in the runbook so the URL never lands in
# Terraform state. Terraform tracks the existence of the subscription
# only; the endpoint is treated as out-of-band config.
#
# Reference subscription (do not uncomment until the webhook URL is
# confirmed; left as documentation):
#
# resource "aws_sns_topic_subscription" "ops_alerts_slack" {
#   topic_arn              = aws_sns_topic.ops_alerts.arn
#   protocol               = "https"
#   endpoint               = "https://hooks.slack.com/services/REDACTED"
#   endpoint_auto_confirms = true
# }

# ─── Secondary-region log destination (DR landing pad) ──────────────────────
# Pre-creates the destination log group in the DR region so workers
# failed over to us-east-1 have somewhere to write immediately. This is
# a *landing pad*, not an automatic cross-region replica — actual
# cross-region log forwarding (CloudWatch subscription → Kinesis Data
# Stream → cross-region put) is intentionally deferred to the Phase 4
# worker tier task, where the volume / cost trade-off can be evaluated
# against real worker traffic.

resource "aws_cloudwatch_log_group" "workers_dr_landing" {
  provider          = aws.us_east_1
  name              = "/${local.lz_project}/workers-dr-landing"
  retention_in_days = 14

  tags = merge(local.lz_common_tags, {
    Name = "/${local.lz_project}/workers-dr-landing"
    role = "dr-landing-pad"
  })
}

# ─── Outputs ─────────────────────────────────────────────────────────────────

output "ops_alerts_topic_arn" {
  value       = aws_sns_topic.ops_alerts.arn
  description = "Set as alarm action on every CloudWatch metric alarm in this account."
}

output "workers_log_group_name" {
  value       = aws_cloudwatch_log_group.workers.name
  description = "Shared log group for non-Lambda worker output."
}

output "workers_metric_namespace" {
  value       = "Syrabit/Workers"
  description = "PutMetricData namespace; runtime role above is scoped to this exactly."
}
