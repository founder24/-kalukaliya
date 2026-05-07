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
      # Account-id placeholder ("*") works because the IAM policy is
      # already scoped to this project's Secrets Manager prefix.
      MONGO_URL_SECRET_ARN     = "arn:aws:secretsmanager:${local.lz_primary_region}:*:secret:${local.lz_project}/${local.lz_env}/mongo-url"
      PINECONE_API_KEY_SECRET  = "arn:aws:secretsmanager:${local.lz_primary_region}:*:secret:${local.lz_project}/${local.lz_env}/pinecone-api-key"
      WORKERS_EMBED_SECRET_ARN = "arn:aws:secretsmanager:${local.lz_primary_region}:*:secret:${local.lz_project}/${local.lz_env}/workers-embed-secret"
      WORKERS_EMBED_URL_SSM    = "arn:aws:secretsmanager:${local.lz_primary_region}:*:secret:${local.lz_project}/${local.lz_env}/workers-embed-url"
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
