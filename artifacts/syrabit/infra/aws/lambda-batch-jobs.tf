## Task #551 §B — ACA Jobs → AWS Lambda + EventBridge migration.
##
## Three Lambda functions corresponding 1:1 to the existing
## `artifacts/syrabit-backend/aca_jobs/` modules. The Python source
## stays in-tree (Lambda imports it via the same multi-entrypoint
## container image used by `lambda-workers.tf`); the only
## Lambda-specific code is `lambda_batch/<job>.py` which adapts the
## existing `run_backfill` / `_sample_once` async API to a Lambda
## handler signature.
##
## Cutover: 7-day shadow period (Lambda + ACA-loop run side-by-side),
## then the in-process loops in `server.py:_start_aca_jobs` flip OFF
## via env var `ACA_JOB_BATCHES_DISABLED=1` and Lambda becomes the
## sole driver. Rollback = unset the env var.

# Reuse the Secrets Manager entries already declared in `secrets.tf`
# (under `aws_secretsmanager_secret.workers`) — keeps the Lambda batch
# jobs on the same naming convention as `sqs-reembed.tf` rather than
# inventing a parallel `pinecone-api-key` / `workers-embed-secret`
# layout that would diverge on rotation.
data "aws_secretsmanager_secret" "mongo_url" {
  name       = "${local.lz_project}/${local.lz_env}/mongo/url"
  depends_on = [aws_secretsmanager_secret.workers]
}

# Translation-provider credentials needed by
# `as-translation-backfill` (the underlying `aca_jobs` module reaches
# into `providers/workers_indic.py` and the Vertex polish chain).
# Round-3 reviewer fix: previously omitted, which would have left the
# translation Lambda no-op-ing in production.
data "aws_secretsmanager_secret" "cloudflare_api_token" {
  name       = "${local.lz_project}/${local.lz_env}/cloudflare/api-token"
  depends_on = [aws_secretsmanager_secret.workers]
}

data "aws_secretsmanager_secret" "cf_ai_gateway_account_id" {
  name       = "${local.lz_project}/${local.lz_env}/cf-ai-gateway/account-id"
  depends_on = [aws_secretsmanager_secret.workers]
}

data "aws_secretsmanager_secret" "gemini_api_key" {
  name       = "${local.lz_project}/${local.lz_env}/gemini/api-key"
  depends_on = [aws_secretsmanager_secret.workers]
}

data "aws_secretsmanager_secret" "gcp_sa_json" {
  name       = "${local.lz_project}/${local.lz_env}/gcp/sa-json"
  depends_on = [aws_secretsmanager_secret.workers]
}

# `pinecone` and `workers_embed` data sources are already declared in
# `sqs-reembed.tf`. We reference them directly below.

locals {
  batch_jobs = {
    "as-translation-backfill" = {
      handler           = "lambda_batch.as_translation_backfill.handler"
      memory_mb         = 512
      timeout_s         = 900
      schedule          = "cron(0 3 * * ? *)"   # daily 03:00 UTC
      max_docs_per_run  = 1000
      description       = "Task #551 — Daily English→Assamese translation backfill (IndicTrans2 → Vertex polish)."
    }
    "embed-backfill" = {
      handler           = "lambda_batch.embed_backfill.handler"
      memory_mb         = 512
      timeout_s         = 900
      schedule          = "cron(0 */6 * * ? *)" # every 6h
      max_docs_per_run  = 500
      description       = "Task #551 — Re-embed legacy chunks via Workers-AI Gemma+Qwen3 (1024-dim → Pinecone)."
    }
    "comprehend-sampler" = {
      handler           = "lambda_batch.comprehend_sampler.handler"
      memory_mb         = 128
      timeout_s         = 300
      schedule          = "cron(0 4 ? * SUN *)" # weekly Sun 04:00 UTC
      max_docs_per_run  = 25
      description       = "Task #551 — Weekly AWS Comprehend sentiment + PII sampler over chapters."
    }
  }
}

# ── Shared IAM role ──────────────────────────────────────────────────────────
resource "aws_iam_role" "batch_job" {
  name = "${local.lz_project}-batch-job-${local.lz_env}"

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

resource "aws_iam_role_policy_attachment" "batch_job_basic" {
  role       = aws_iam_role.batch_job.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "batch_job_vpc" {
  role       = aws_iam_role.batch_job.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "batch_job_inline" {
  name = "batch-job-runtime"
  role = aws_iam_role.batch_job.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Secrets: Mongo URI, Pinecone key, Workers-AI embed key.
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = "arn:aws:secretsmanager:${local.lz_primary_region}:*:secret:${local.lz_project}/${local.lz_env}/*"
      },
      # SSM for runtime config (queue URLs, glacier bucket names).
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParametersByPath"]
        Resource = "arn:aws:ssm:${local.lz_primary_region}:*:parameter/${local.lz_project}/*"
      },
      # AWS Comprehend (sampler only — but a single role is simpler).
      {
        Effect   = "Allow"
        Action   = ["comprehend:DetectSentiment", "comprehend:DetectPiiEntities"]
        Resource = "*"
      },
      # X-Ray tracing.
      {
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = "*"
      },
      # Task #516 — handlers PutMetricData into `Syrabit/BatchJobs` so the
      # leftover-doc-count alarm below can detect a stuck pass that the
      # built-in Lambda `Errors` metric would never see (a clean run that
      # produces zero translations is silent at the Lambda layer).
      {
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "Syrabit/BatchJobs"
          }
        }
      },
    ]
  })
}

# ── Pre-create log groups so 14-day retention is set on day one ──────────────
resource "aws_cloudwatch_log_group" "batch_job" {
  for_each = local.batch_jobs

  name              = "/aws/lambda/${local.lz_project}-${each.key}"
  retention_in_days = 14
  tags              = merge(local.lz_common_tags, { Name = "/aws/lambda/${local.lz_project}-${each.key}" })
}

# ── Lambda functions ─────────────────────────────────────────────────────────
resource "aws_lambda_function" "batch_job" {
  for_each = local.batch_jobs

  function_name = "${local.lz_project}-${each.key}"
  role          = aws_iam_role.batch_job.arn
  package_type  = "Image"
  image_uri     = local.sqs_consumer_image_uri
  architectures = ["arm64"]
  memory_size   = each.value.memory_mb
  timeout       = each.value.timeout_s

  image_config {
    command = [each.value.handler]
  }

  # Same VPC + SG as the SQS consumers — keeps Mongo/Pinecone egress
  # off the public NAT and reuses the interface VPC endpoints.
  vpc_config {
    subnet_ids         = aws_subnet.workers_private[*].id
    security_group_ids = [aws_security_group.workers_egress.id]
  }

  environment {
    variables = merge(local.otel_env, {
      HANDLER_NAME       = each.value.handler
      LZ_PROJECT         = local.lz_project
      LZ_ENV             = local.lz_env
      OTEL_SERVICE_NAME  = "${local.lz_project}-${each.key}"
      MAX_DOCS_PER_RUN   = tostring(each.value.max_docs_per_run)
      # Secret ARNs fetched at cold-start by `lambda_batch._db.bootstrap_env`,
      # which hydrates os.environ with the underlying values BEFORE any
      # aca_jobs module is imported — otherwise the provider modules
      # (workers_embed, pinecone client) boot in a misconfigured state.
      #
      # Reuses the same Secrets Manager entries already wired by
      # `sqs-reembed.tf` (`pinecone/api-key`, `workers-embed/secret`)
      # rather than introducing a parallel naming convention.
      # `mongo/url` is registered alongside in `secrets.tf`.
      MONGO_URL_SECRET_ARN              = data.aws_secretsmanager_secret.mongo_url.arn
      PINECONE_API_KEY_SECRET           = data.aws_secretsmanager_secret.pinecone.arn
      WORKERS_EMBED_SECRET_ARN          = data.aws_secretsmanager_secret.workers_embed.arn
      # Translation-provider creds (consumed by `as-translation-backfill`).
      # Setting them on every job in the family is harmless — the
      # other two handlers ignore the env vars.
      CLOUDFLARE_API_TOKEN_SECRET_ARN   = data.aws_secretsmanager_secret.cloudflare_api_token.arn
      CF_AI_GATEWAY_ACCOUNT_ID_SECRET   = data.aws_secretsmanager_secret.cf_ai_gateway_account_id.arn
      GEMINI_API_KEY_SECRET_ARN         = data.aws_secretsmanager_secret.gemini_api_key.arn
      GOOGLE_APPLICATION_CREDENTIALS_JSON_SECRET_ARN = data.aws_secretsmanager_secret.gcp_sa_json.arn
      # Embed worker URL is not a secret — same value used by the
      # deferred-embed Lambda in `sqs-reembed.tf` (line 122).
      WORKERS_EMBED_URL                 = "https://embed.syrabit.ai"
    })
  }

  tracing_config { mode = "Active" }

  depends_on = [aws_cloudwatch_log_group.batch_job]

  tags = merge(local.lz_common_tags, {
    Name      = "${local.lz_project}-${each.key}"
    component = "batch-job"
    aca_jobs_replacement = replace(each.key, "-", "_")
  })
}

# ── EventBridge Scheduler (one schedule per Lambda) ──────────────────────────
resource "aws_iam_role" "batch_job_scheduler" {
  name = "${local.lz_project}-batch-job-scheduler-${local.lz_env}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.lz_common_tags
}

resource "aws_iam_role_policy" "batch_job_scheduler" {
  role = aws_iam_role.batch_job_scheduler.id
  name = "invoke-batch-jobs"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = [for f in aws_lambda_function.batch_job : f.arn]
    }]
  })
}

resource "aws_scheduler_schedule" "batch_job" {
  for_each = local.batch_jobs

  name                = "${local.lz_project}-${each.key}-${local.lz_env}"
  schedule_expression = each.value.schedule
  description         = each.value.description
  flexible_time_window { mode = "OFF" }

  target {
    arn      = aws_lambda_function.batch_job[each.key].arn
    role_arn = aws_iam_role.batch_job_scheduler.arn
    input    = jsonencode({ scheduled = true, source = "eventbridge" })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }
  }
}

# ── CloudWatch alarms: Errors > 0 over 1 h → ops_alerts SNS ──────────────────
resource "aws_cloudwatch_metric_alarm" "batch_job_errors" {
  for_each = local.batch_jobs

  alarm_name          = "${local.lz_project}-${each.key}-errors-${local.lz_env}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 3600
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_description   = "Task #551 — ${each.key} Lambda invocation errored in the last hour. Check CloudWatch logs at /aws/lambda/${local.lz_project}-${each.key}."

  dimensions = {
    FunctionName = aws_lambda_function.batch_job[each.key].function_name
  }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]
  tags          = local.lz_common_tags
}

# ── Task #516 — leftover-doc / failure alarms for as-translation-backfill ────
# The Lambda Errors alarm above only fires on hard crashes. A clean run
# that produces zero translations because every translate call returned
# empty (Workers-AI quota exhausted, Vertex outage, …) leaves Errors=0
# and the SSR `/as/...` corpus quietly stops getting fresh content.
# These alarms watch the custom CloudWatch metrics emitted by
# `lambda_batch.as_translation_backfill._emit_metrics` so on-call gets
# paged when the leftover-doc count refuses to drain or per-run failures
# spike. Both target the same `ops_alerts` SNS topic the existing
# `batch_job_errors` alarm uses.

resource "aws_cloudwatch_metric_alarm" "as_backfill_stuck" {
  alarm_name          = "${local.lz_project}-as-translation-backfill-stuck-${local.lz_env}"
  comparison_operator = "GreaterThanThreshold"
  # Daily cron → one datapoint per day. 3 consecutive days of non-zero
  # leftover means the job is making no headway across multiple passes;
  # at the typical IndicTrans2 throughput a healthy backlog drains in
  # well under 24 h.
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  metric_name         = "RemainingTotal"
  namespace           = "Syrabit/BatchJobs"
  period              = 86400
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "breaching"
  alarm_description   = "Task #516 — as-translation-backfill RemainingTotal > 0 for 3 consecutive daily passes. The /as/... SSR corpus is falling behind English; check IndicTrans2 / Vertex polish health."

  dimensions = {
    Job = "as-translation-backfill"
  }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]
  tags          = local.lz_common_tags
}

resource "aws_cloudwatch_metric_alarm" "as_backfill_failed_spike" {
  alarm_name          = "${local.lz_project}-as-translation-backfill-failed-${local.lz_env}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  # Use the Job-only `FailedTotal` rollup (not the per-Collection
  # `Failed` series) so the alarm metric identity actually exists in
  # CloudWatch — (MetricName, Dimensions) is the identity key, and a
  # Job-only alarm cannot resolve a Job+Collection series.
  metric_name         = "FailedTotal"
  namespace           = "Syrabit/BatchJobs"
  period              = 86400
  statistic           = "Sum"
  # `Failed` counts docs where every field translation came back empty
  # or below the Bengali-script ratio. 50/day is a sustained provider
  # problem rather than the occasional Sarvam blip.
  threshold           = 50
  treat_missing_data  = "notBreaching"
  alarm_description   = "Task #516 — as-translation-backfill Failed > 50 in a single daily pass. Likely Workers-AI IndicTrans2 quota exhaustion or a Vertex polish outage; docs auto-retry on next pass but corpus freshness is degraded."

  dimensions = {
    Job = "as-translation-backfill"
  }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]
  tags          = local.lz_common_tags
}
