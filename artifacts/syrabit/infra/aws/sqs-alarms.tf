# infra/aws/sqs-alarms.tf
#
# Phase 4 — Async worker port (Task #332).
#
# CloudWatch alarms for the SQS + Lambda worker tier. Every alarm
# routes to the existing `aws_sns_topic.ops_alerts` topic created in
# `observability.tf`; that topic already has the cross-account Slack
# subscriber (commented out until the webhook is provisioned) and
# email subscribers wired up, so we re-use it instead of creating a
# parallel notification path.
#
# Three alarm classes per queue:
#   1. Queue-depth backlog  — `ApproximateNumberOfMessagesVisible`
#                             above N for 3 consecutive 1-min periods
#                             (catches stuck consumer / cold-start
#                             storms before SLO breach).
#   2. DLQ non-empty        — any message in the DLQ pages on-call.
#                             DLQ depth must be 0 in steady state, so
#                             threshold = 0 with a 1-min evaluation.
#   3. Lambda errors        — absolute Errors count >= 5 across 5
#                             consecutive 1-min periods (Sum
#                             statistic). Count-based, not rate-based:
#                             low-volume queues (`bing-keyword-refresh`,
#                             `unified-logs-cf-pull`) fire only a few
#                             invocations per hour, so a percentage
#                             threshold would either be jittery on
#                             single-invocation noise or never trip on
#                             genuine consumer bugs. We trade some
#                             alarm sensitivity at very high TPS for
#                             a uniform threshold across all eight
#                             queues — acceptable because the SQS
#                             redrive policy already routes truly
#                             repeating failures to the DLQ alarm.

locals {
  # Per-queue backlog threshold. Tuned to the inventory's expected
  # peak rate × visibility-timeout headroom — anything above this is
  # almost certainly a stuck consumer, not legitimate traffic.
  sqs_backlog_thresholds = {
    "seo-indexnow"            = 500
    "seo-internal-linker"     = 200
    "discovery-engine-ingest" = 200
    "bing-keyword-refresh"    = 50
    "bing-submit"             = 200
    "cf-bot-crosscheck"       = 100
    "unified-logs-cf-pull"    = 50
    "email-fallback"          = 100
  }
}

# ─── Backlog alarms ──────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "sqs_backlog" {
  for_each = local.sqs_worker_queues

  alarm_name          = "${each.value.aws}-backlog"
  alarm_description   = "SQS queue ${each.value.aws} backlog above ${local.sqs_backlog_thresholds[each.key]} for 3m — consumer Lambda likely stuck or throttled."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = local.sqs_backlog_thresholds[each.key]
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.worker[each.key].name
  }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]

  tags = merge(local.lz_common_tags, {
    Name      = "${each.value.aws}-backlog"
    component = "sqs-alarm"
    gcp-key   = each.key
  })
}

# ─── DLQ depth alarms ────────────────────────────────────────────────────────
# Any message in the DLQ is, by definition, a failure that exhausted
# `max_receive_count`. Threshold = 0 with statistic=Maximum so even a
# single message pages.

resource "aws_cloudwatch_metric_alarm" "sqs_dlq_depth" {
  for_each = local.sqs_worker_queues

  alarm_name          = "${each.value.aws}-dlq-not-empty"
  alarm_description   = "DLQ ${each.value.aws}-dlq has at least one message — consumer drained max_receive_count without success."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.worker_dlq[each.key].name
  }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]

  tags = merge(local.lz_common_tags, {
    Name      = "${each.value.aws}-dlq-not-empty"
    component = "sqs-dlq-alarm"
    gcp-key   = each.key
  })
}

# ─── Lambda error-rate alarms ────────────────────────────────────────────────
# Email-fallback piggy-backs on the existing email-worker Lambda
# which has its own alarms (see lambda-email-worker.tf), so it's
# excluded from this loop to avoid double-paging.

locals {
  sqs_consumer_lambdas_for_alarms = {
    for k, v in local.sqs_worker_lambdas : k => v
  }
}

resource "aws_cloudwatch_metric_alarm" "sqs_consumer_errors" {
  for_each = local.sqs_consumer_lambdas_for_alarms

  alarm_name          = "${local.lz_project}-${each.key}-consumer-errors"
  alarm_description   = "Consumer Lambda ${each.key} raised Errors >= 5 (Sum) in each of 5 consecutive 1-min windows — likely consumer bug, not transient downstream failure. Count-based threshold; see header comment in sqs-alarms.tf for rationale."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 5
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.sqs_consumer[each.key].function_name
  }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]

  tags = merge(local.lz_common_tags, {
    Name      = "${local.lz_project}-${each.key}-consumer-errors"
    component = "lambda-error-alarm"
    gcp-key   = each.key
  })
}

# ─── Composite "any worker degraded" alarm ───────────────────────────────────
# Single boolean signal the admin panel polls (cheaper than fanning
# eight alarm states across the tile). Fires if ANY of the per-queue
# DLQ alarms or backlog alarms are in ALARM state.

resource "aws_cloudwatch_composite_alarm" "workers_degraded" {
  alarm_name        = "${local.lz_project}-workers-degraded"
  alarm_description = "Composite — any AWS async worker queue is backlogged or has a non-empty DLQ. Consumed by AdminAwsInfraCard."

  alarm_rule = join(" OR ", concat(
    [for k, v in local.sqs_worker_queues : "ALARM(${aws_cloudwatch_metric_alarm.sqs_backlog[k].alarm_name})"],
    [for k, v in local.sqs_worker_queues : "ALARM(${aws_cloudwatch_metric_alarm.sqs_dlq_depth[k].alarm_name})"],
  ))

  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]

  tags = merge(local.lz_common_tags, {
    Name      = "${local.lz_project}-workers-degraded"
    component = "composite-alarm"
  })
}

output "workers_degraded_composite_alarm_arn" {
  description = "Composite alarm ARN polled by the admin panel for the AWS Infra card."
  value       = aws_cloudwatch_composite_alarm.workers_degraded.arn
}
