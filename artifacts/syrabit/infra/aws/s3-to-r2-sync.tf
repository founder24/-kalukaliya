# S3 → R2 nightly EventBridge sync (Task #489 §D row "S3 → R2 nightly
# EventBridge sync"). Closes the matrix §A "R2 buckets" + "S3 (temp
# dumps)" pair: S3 is for the day's temp work; the night promotes
# every object under the `/finals/` prefix to Cloudflare R2 and
# deletes the S3 source on confirmed write.
#
# Lambda implementation lives in
# `artifacts/syrabit/services/backend/s3_to_r2_sync.py` (small enough
# to be a single file). Cloudflare R2 credentials are pulled from
# Secrets Manager (sourced from AKV per V4 §6 — AWS SM is a read-only
# replica). The Lambda uses the S3-compatible R2 endpoint via boto3,
# so no Cloudflare SDK is needed.

resource "aws_iam_role" "s3_to_r2" {
  name = "${local.lz_project}-s3-to-r2-${local.lz_env}"

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

resource "aws_iam_role_policy_attachment" "s3_to_r2_logs" {
  role       = aws_iam_role.s3_to_r2.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "s3_to_r2_inline" {
  name = "${local.lz_project}-s3-to-r2-${local.lz_env}"
  role = aws_iam_role.s3_to_r2.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetObject",
          "s3:DeleteObject",
        ]
        Resource = [
          "arn:aws:s3:::${var.s3_finals_bucket}",
          "arn:aws:s3:::${var.s3_finals_bucket}/finals/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [data.aws_secretsmanager_secret.r2_access_key.arn]
      },
    ]
  })
}

data "aws_secretsmanager_secret" "r2_access_key" {
  name = "syrabit/r2/finals-writer"
}

resource "aws_lambda_function" "s3_to_r2_sync" {
  function_name = "${local.lz_project}-s3-to-r2-${local.lz_env}"

  package_type  = "Image"
  image_uri     = local.sqs_consumer_image_uri
  architectures = ["arm64"]

  # Multi-entrypoint container image (matches `lambda-workers.tf`):
  # handler dispatch is via `image_config.command`. `HANDLER_NAME` env
  # alone is NOT sufficient — without `command` the Lambda runtime
  # falls back to the image's default CMD.
  image_config {
    command = ["s3_to_r2_sync.handler"]
  }

  role        = aws_iam_role.s3_to_r2.arn
  memory_size = 1024
  timeout     = 600 # 10 min — enough headroom for a day's promotions

  environment {
    # Task #489 — OTEL_* matches `lambda-otel.tf` so cross-cloud trace
    # canary (`.github/workflows/cross-cloud-trace-canary.yml`) sees
    # AWS spans land alongside ACA spans for the same `traceparent`.
    variables = merge(local.otel_env, {
      OTEL_SERVICE_NAME = "${local.lz_project}-s3-to-r2-sync"

      # Kept for in-image observability/parity; dispatch is via `image_config.command`.
      HANDLER_NAME             = "s3_to_r2_sync.handler"
      S3_FINALS_BUCKET         = var.s3_finals_bucket
      R2_ENDPOINT_URL          = var.r2_endpoint_url
      R2_FINALS_BUCKET         = var.r2_finals_bucket
      R2_ACCESS_KEY_SECRET_ARN = data.aws_secretsmanager_secret.r2_access_key.arn
    })
  }

  tracing_config { mode = "Active" }

  tags = merge(local.lz_common_tags, {
    purpose = "s3-to-r2-nightly-sync"
  })
}

# Nightly EventBridge schedule — 02:11 UTC = 07:41 IST (off the 04:11
# UTC CF edge-cache smoke matrix from Task #456).
resource "aws_scheduler_schedule" "s3_to_r2_nightly" {
  name                = "${local.lz_project}-s3-to-r2-nightly-${local.lz_env}"
  schedule_expression = "cron(11 2 * * ? *)"
  flexible_time_window { mode = "OFF" }

  target {
    arn      = aws_lambda_function.s3_to_r2_sync.arn
    role_arn = aws_iam_role.s3_to_r2_invoker.arn
    input    = jsonencode({ scheduled = true })
  }
}

resource "aws_iam_role" "s3_to_r2_invoker" {
  name = "${local.lz_project}-s3-to-r2-inv-${local.lz_env}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "s3_to_r2_invoker_inline" {
  name = "${local.lz_project}-s3-to-r2-inv-${local.lz_env}"
  role = aws_iam_role.s3_to_r2_invoker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [aws_lambda_function.s3_to_r2_sync.arn]
    }]
  })
}

# Failure alarm — wired into the existing ops_alerts SNS topic that
# `sqs-reembed.tf` already subscribes oncall to.
resource "aws_cloudwatch_metric_alarm" "s3_to_r2_failures" {
  alarm_name          = "${local.lz_project}-s3-to-r2-errors-${local.lz_env}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0

  dimensions = {
    FunctionName = aws_lambda_function.s3_to_r2_sync.function_name
  }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]
  tags          = local.lz_common_tags
}

variable "s3_finals_bucket" {
  description = "S3 bucket holding the day's /finals/* objects to promote to R2."
  type        = string
  default     = "syrabit-prod-finals-staging"
}

variable "r2_endpoint_url" {
  description = "Cloudflare R2 S3-compatible endpoint URL (per-account)."
  type        = string
  # default = "" so CI runs without the gitignored terraform.tfvars pass.
  # Real value supplied via tfvars in local / prod applies; the Lambda
  # environment variable is populated from SSM at runtime anyway.
  default = ""
}

variable "r2_finals_bucket" {
  description = "Cloudflare R2 bucket that holds canonical finals."
  type        = string
  default     = "syrabit-finals"
}
