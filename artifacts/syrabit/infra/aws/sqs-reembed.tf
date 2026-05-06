# infra/aws/sqs-reembed.tf
#
# Task #489 — Deferred-embed replay queue (V4 §15 cache-only Option D).
#
# When EMBED_DEGRADED_MODE=true, the FastAPI backend STOPS calling any
# third-party embedder (no Vertex multilingual, no Cohere, no Voyage —
# all retired by sibling tasks #490/#491). New chunks that would have
# been embedded are enqueued here instead. On reset, this Lambda
# replays each message against embed.syrabit.ai (Cloudflare Workers AI
# EmbeddingGemma + Qwen3) and writes to the primary Pinecone namespace
# `cached_gemma_today`. Messages are deleted only on confirmed write,
# so a Worker AI outage during drain leaves the message intact.
#
# This is a REPURPOSE of the prior `vertex-fallback-reembed-queue`
# concept: same name shape, but the semantic is "deferred replay during
# cache-only degraded mode", not "drain Vertex fallback namespace"
# (since Vertex no longer writes to Pinecone).

resource "aws_sqs_queue" "reembed_dlq" {
  name                      = "${local.lz_project}-reembed-dlq-${local.lz_env}"
  message_retention_seconds = 1209600 # 14 days
  tags = merge(local.lz_common_tags, {
    purpose = "deferred-embed-replay-dlq"
    v4_ref  = "section-15-amendment"
  })
}

resource "aws_sqs_queue" "reembed" {
  name                       = "${local.lz_project}-reembed-${local.lz_env}"
  visibility_timeout_seconds = 180  # > Lambda timeout below
  message_retention_seconds  = 1209600 # 14 days
  receive_wait_time_seconds  = 20      # long-poll (20 s max)

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.reembed_dlq.arn
    maxReceiveCount     = 5
  })

  tags = merge(local.lz_common_tags, {
    purpose = "deferred-embed-replay"
    v4_ref  = "section-15-amendment"
  })
}

# Per-queue inline scoping for the shared SQS-consumer role (matches the
# pattern in lambda-workers.tf for the existing 7 consumers).
resource "aws_iam_role_policy" "sqs_consumer_reembed" {
  role = aws_iam_role.sqs_consumer.id
  name = "consume-reembed"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility",
        ]
        Resource = aws_sqs_queue.reembed.arn
      },
    ]
  })
}

# TASK-489-FOLLOWUP: the consumer Lambda + event-source mapping are
# intentionally commented out until the handler lands.
#
# Status: see `infra/four-cloud-delegation.md` §D row "Deferred-embed
# reembed Lambda handler". The Python module
# `services/backend/sqs_consumers/reembed.py` does not yet exist, so
# provisioning the Lambda here would create a function whose every
# invocation fails at HANDLER_NAME resolution. The producer-side
# enqueue path + queue/DLQ/alarms (above + below) ARE live so the
# follow-up PR only needs to:
#   1. Add `services/backend/sqs_consumers/reembed.py` to the consumer
#      multi-entrypoint container image (matches the pattern in
#      `lambda-workers.tf`).
#   2. Uncomment the two blocks below.
#   3. Run `terraform apply` — alarms are already wired to ops_alerts.
#
# resource "aws_lambda_function" "reembed_consumer" {
#   function_name = "${local.lz_project}-reembed-${local.lz_env}"
#
#   package_type  = "Image"
#   image_uri     = local.sqs_consumer_image_uri
#   architectures = ["arm64"]
#
#   role        = aws_iam_role.sqs_consumer.arn
#   memory_size = 512
#   timeout     = 120
#
#   reserved_concurrent_executions = 5
#
#   environment {
#     variables = {
#       HANDLER_NAME             = "sqs_consumers.reembed.handler"
#       EMBED_WORKER_URL         = "https://embed.syrabit.ai"
#       PINECONE_INDEX           = "syrabit-prod"
#       PINECONE_NAMESPACE       = "cached_gemma_today"
#       WORKERS_EMBED_SECRET_ARN = data.aws_secretsmanager_secret.workers_embed.arn
#       PINECONE_API_KEY_ARN     = data.aws_secretsmanager_secret.pinecone.arn
#     }
#   }
#
#   tracing_config { mode = "Active" }
#
#   tags = merge(local.lz_common_tags, {
#     purpose = "deferred-embed-replay"
#   })
# }
#
# resource "aws_lambda_event_source_mapping" "reembed" {
#   event_source_arn                   = aws_sqs_queue.reembed.arn
#   function_name                      = aws_lambda_function.reembed_consumer.arn
#   batch_size                         = 5
#   maximum_batching_window_in_seconds = 5
#
#   function_response_types = ["ReportBatchItemFailures"]
# }

# ─── CloudWatch alarms ─────────────────────────────────────────────────────
# The matrix's acceptance criterion C5 (deferred-embed replay) requires
# operators to see drain activity at a glance. Three alarms cover the
# common failure modes.

resource "aws_cloudwatch_metric_alarm" "reembed_dlq_depth" {
  alarm_name          = "${local.lz_project}-reembed-dlq-depth"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 2
  period              = 300
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  alarm_description   = "DLQ has any messages — deferred-embed Lambda is failing > 5 retries. Check Lambda logs + Workers AI status."
  treat_missing_data  = "notBreaching"

  dimensions = { QueueName = aws_sqs_queue.reembed_dlq.name }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]

  tags = local.lz_common_tags
}

resource "aws_cloudwatch_metric_alarm" "reembed_queue_depth" {
  alarm_name          = "${local.lz_project}-reembed-queue-depth"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 5000
  evaluation_periods  = 6
  period              = 300
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  alarm_description   = "Reembed backlog > 5k for 30 min — degraded mode is on for a long time, or drain throughput is too low."
  treat_missing_data  = "notBreaching"

  dimensions = { QueueName = aws_sqs_queue.reembed.name }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]

  tags = local.lz_common_tags
}

resource "aws_cloudwatch_metric_alarm" "reembed_age_seconds" {
  alarm_name          = "${local.lz_project}-reembed-oldest-age"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 86400 # 24 h
  evaluation_periods  = 1
  period              = 900
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  statistic           = "Maximum"
  alarm_description   = "Oldest reembed message > 24 h — degraded mode forgotten in the on position."
  treat_missing_data  = "notBreaching"

  dimensions = { QueueName = aws_sqs_queue.reembed.name }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]

  tags = local.lz_common_tags
}

# ─── Outputs (consumed by SSM publishing in sqs.tf) ───────────────────────
output "reembed_queue_url" {
  description = "Producer-side enqueue URL for the deferred-embed replay queue."
  value       = aws_sqs_queue.reembed.url
}

output "reembed_queue_arn" {
  description = "ARN of the deferred-embed replay queue (V4 §15 cache-only Option D)."
  value       = aws_sqs_queue.reembed.arn
}
