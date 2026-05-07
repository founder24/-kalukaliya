# infra/aws/lambda-workers.tf
#
# Phase 4 — Async worker port (Task #332).
#
# Consumer Lambda functions that drain the SQS queues defined in
# `sqs.tf`. Each Lambda corresponds 1-for-1 to a queue and executes
# the same business logic that previously ran inside a Cloud Tasks
# HTTP target on the FastAPI backend.
#
# Image-based deploy (not zip): the consumer code lives in the
# `services/backend/sqs_consumers/` tree and is built into a single
# multi-entrypoint container image pushed to the existing ECR repo
# (`ecr.tf`). Each Lambda picks its handler via the `HANDLER_NAME`
# env var so we ship one image, not eight.
#
# All Lambdas use:
#   • arm64 (Graviton2)               — ~20 % cheaper, ~20 % faster
#   • Reserved concurrency caps       — protect downstream APIs from
#                                       runaway fanout
#   • SQS event-source mapping with
#     batch_size + maximum_batching   — amortises invocation overhead
#   • Active X-Ray tracing            — flame graphs in AWS Console
#   • CloudWatch Logs (14-day TTL)    — cost-bounded under Activate

locals {
  # Per-queue Lambda config. `concurrency` is the *reserved* (and
  # therefore maximum) concurrent execution cap. Email-fallback reuses
  # the existing `aws_lambda_function.email_worker` (see
  # `lambda-email-worker.tf`) — it is the only entry that does NOT
  # create a new function, just a new event-source mapping.
  sqs_worker_lambdas = {
    "seo-indexnow"            = { handler = "sqs_consumers.seo_indexnow.handler",         memory_mb = 512,  timeout = 30,  concurrency = 20, batch_size = 10 }
    "seo-internal-linker"     = { handler = "sqs_consumers.seo_internal_linker.handler",  memory_mb = 1024, timeout = 90,  concurrency = 10, batch_size = 5  }
    "discovery-engine-ingest" = { handler = "sqs_consumers.discovery_engine.handler",     memory_mb = 1024, timeout = 120, concurrency = 5,  batch_size = 5  }
    "bing-keyword-refresh"    = { handler = "sqs_consumers.bing_keyword.handler",         memory_mb = 512,  timeout = 240, concurrency = 2,  batch_size = 1  }
    "bing-submit"             = { handler = "sqs_consumers.bing_submit.handler",          memory_mb = 512,  timeout = 30,  concurrency = 5,  batch_size = 10 }
    "cf-bot-crosscheck"       = { handler = "sqs_consumers.cf_bot_crosscheck.handler",    memory_mb = 512,  timeout = 30,  concurrency = 5,  batch_size = 10 }
    "unified-logs-cf-pull"    = { handler = "sqs_consumers.unified_logs_pull.handler",    memory_mb = 1024, timeout = 240, concurrency = 2,  batch_size = 1  }
  }

  # Image URI used by every consumer. The existing GitHub Actions
  # release workflow tags `:latest` after each successful merge to
  # main; Terraform pins to that tag deliberately so we do not need
  # an apply for routine code-only deploys (the ops_alerts SNS will
  # page if a release breaks SQS drain rate, and `terraform apply`
  # is the documented rollback path).
  sqs_consumer_image_uri = "${aws_ecr_repository.workers.repository_url}:sqs-consumers-latest"
}

# ─── Shared IAM role ─────────────────────────────────────────────────────────
# One role across all consumers — every queue is in the same account,
# the only sensitive grant is "consume my own queue", and a per-queue
# inline policy is attached below scoping `sqs:*Message*` to that
# queue's ARN only.

resource "aws_iam_role" "sqs_consumer" {
  name = "${local.lz_project}-sqs-consumer-${local.lz_env}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.lz_common_tags
}

resource "aws_iam_role_policy_attachment" "sqs_consumer_basic" {
  role       = aws_iam_role.sqs_consumer.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "sqs_consumer_xray" {
  role       = aws_iam_role.sqs_consumer.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

resource "aws_iam_role_policy" "sqs_consumer_queue_access" {
  name = "sqs-consume"
  role = aws_iam_role.sqs_consumer.id

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
        # Both primary and DLQ ARNs — consumers occasionally re-drive
        # from the DLQ via the admin panel "Replay DLQ" button.
        Resource = concat(
          [for q in aws_sqs_queue.worker : q.arn],
          [for q in aws_sqs_queue.worker_dlq : q.arn],
        )
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParametersByPath"]
        Resource = "arn:aws:ssm:${local.lz_primary_region}:*:parameter/${local.lz_project}/*"
      },
      # Workers read runtime config (3rd-party API keys, downstream
      # endpoints) from Secrets Manager — the Phase 1b convention
      # established in `secrets.tf`. Scoped to this project's prefix
      # so a misconfigured handler cannot enumerate unrelated
      # secrets in the account.
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = "arn:aws:secretsmanager:${local.lz_primary_region}:*:secret:${local.lz_project}/${local.lz_env}/*"
      },
    ]
  })
}

# ─── CloudWatch log groups ───────────────────────────────────────────────────
# Pre-created (rather than letting Lambda auto-create them) so the
# 14-day retention is set on day one — auto-created groups default to
# *Never Expire* and silently bleed Activate credit.

resource "aws_cloudwatch_log_group" "sqs_consumer" {
  for_each = local.sqs_worker_lambdas

  name              = "/aws/lambda/${local.lz_project}-${each.key}-consumer"
  retention_in_days = 14
  tags              = merge(local.lz_common_tags, { Name = "/aws/lambda/${local.lz_project}-${each.key}-consumer" })
}

# ─── Consumer Lambdas ────────────────────────────────────────────────────────

resource "aws_lambda_function" "sqs_consumer" {
  for_each = local.sqs_worker_lambdas

  function_name = "${local.lz_project}-${each.key}-consumer"
  role          = aws_iam_role.sqs_consumer.arn
  package_type  = "Image"
  image_uri     = local.sqs_consumer_image_uri
  architectures = ["arm64"]
  memory_size   = each.value.memory_mb
  timeout       = each.value.timeout

  reserved_concurrent_executions = each.value.concurrency

  image_config {
    # Single image, per-Lambda command override — picks the right
    # entry from `services/backend/sqs_consumers/` at cold-start.
    command = [each.value.handler]
  }

  # Task #333 — observability rewire. AWS Lambda layers are NOT
  # supported on container-image Lambdas (`package_type = "Image"`),
  # so the ADOT collector is BAKED INTO the worker image instead —
  # see `services/backend/sqs_consumers/Dockerfile`. The runtime
  # picks up the OTLP env block below and ships spans to App
  # Insights + Axiom in parallel via the in-image collector.
  environment {
    variables = merge(local.otel_env, {
      HANDLER_NAME      = each.value.handler
      LZ_PROJECT        = local.lz_project
      LZ_ENV            = local.lz_env
      OTEL_SERVICE_NAME = "${each.key}-consumer"
      # App Insights + Axiom credentials live in SSM; the ADOT
      # collector reads them on cold-start so key-rotation does not
      # require a redeploy.
      APP_INSIGHTS_SSM_PARAM = "/${local.lz_project}/${local.lz_env}/app-insights-conn-string"
      AXIOM_TOKEN_SSM_PARAM  = "/${local.lz_project}/${local.lz_env}/axiom-api-token"
      AXIOM_DATASET          = "syrabit-aws-lambda-prod"
    })
  }

  tracing_config {
    # X-Ray retained as the AWS-Console fast-path; App Insights is
    # the cross-cloud source of truth (Task #333).
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.sqs_consumer]

  tags = merge(local.lz_common_tags, {
    Name      = "${local.lz_project}-${each.key}-consumer"
    component = "sqs-consumer"
    gcp-key   = each.key
  })
}

# ─── Event-source mappings (queue → Lambda) ──────────────────────────────────
# `function_response_types = ["ReportBatchItemFailures"]` is critical:
# it lets the Lambda return per-message failures so a single bad
# message in a batch of 10 does not force the whole batch back onto
# the queue (and eventually into the DLQ).

resource "aws_lambda_event_source_mapping" "sqs_consumer" {
  for_each = local.sqs_worker_lambdas

  event_source_arn                   = aws_sqs_queue.worker[each.key].arn
  function_name                      = aws_lambda_function.sqs_consumer[each.key].arn
  batch_size                         = each.value.batch_size
  maximum_batching_window_in_seconds = 5
  function_response_types            = ["ReportBatchItemFailures"]
  enabled                            = true
}

# ─── SES retry-queue wiring (NOT a provider fallback) ──────────────────────
# Task #556 (2026-05-07) — Amazon SES is the SOLE transactional path,
# no fallback, no break-glass (V4 §12). This SQS-backed trigger lets
# producers re-drive transient SES errors (throttle / 5xx / network
# blip) against the same SES endpoint asynchronously instead of
# dropping the message. There is no second provider involved at any
# point — both the synchronous call and this consumer talk to SES.
# The legacy "email-fallback" SQS queue key + resource label are
# retained because renaming SQS queues forces a destructive replace;
# operator-facing semantics are documented in
# `services/backend/sqs_consumers/email_fallback.py` and
# `infra/four-cloud-delegation.md`.

resource "aws_lambda_event_source_mapping" "ses_retry_queue" {
  event_source_arn                   = aws_sqs_queue.worker["email-fallback"].arn
  function_name                      = aws_lambda_function.email_worker.arn
  batch_size                         = 5
  maximum_batching_window_in_seconds = 5
  function_response_types            = ["ReportBatchItemFailures"]
  enabled                            = true
}

# Grant the existing email-worker role permission to drain the SES
# retry queue. Inline policy keeps the grant scoped to that queue
# only — the role still cannot touch the other seven worker queues.
resource "aws_iam_role_policy" "email_worker_ses_retry_queue" {
  name = "sqs-ses-retry"
  role = aws_iam_role.email_worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
        "sqs:ChangeMessageVisibility",
      ]
      Resource = [
        aws_sqs_queue.worker["email-fallback"].arn,
        aws_sqs_queue.worker_dlq["email-fallback"].arn,
      ]
    }]
  })
}

output "sqs_consumer_function_names" {
  description = "Map of GCP Cloud Tasks key → consumer Lambda function name."
  value       = { for k, fn in aws_lambda_function.sqs_consumer : k => fn.function_name }
}
