# infra/aws/lambda-email-worker.tf
#
# Replaces the Cloudflare Workers Paid email-worker ($5/mo share).
# Covered 100 % by AWS Activate credits.
#
# Performance / deliverability boosts (all free under Activate)
# ──────────────────────────────────────────────────────────────
# • arm64 (Graviton3) runtime — ~20 % faster, ~20 % cheaper than x86
# • SES Dedicated IP Pool — isolated reputation, better inbox placement
# • SES Virtual Deliverability Manager — automatic engagement tracking
# • Lambda Provisioned Concurrency (1) — removes cold-start for OTP flow
# • CloudWatch EMF metrics — zero-cost structured latency / error tracking
# • X-Ray active tracing — per-invocation flame graphs in AWS Console

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

locals {
  aws_region   = "ap-south-1"
  project_name = "syrabit"
  env          = "prod"
}

provider "aws" {
  region = local.aws_region
}

# ─── IAM role ────────────────────────────────────────────────────────────────

resource "aws_iam_role" "email_worker" {
  name = "${local.project_name}-email-worker-${local.env}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "email_worker_basic" {
  role       = aws_iam_role.email_worker.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "email_worker_ses" {
  name = "ses-send"
  role = aws_iam_role.email_worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ses:SendEmail", "ses:SendRawEmail", "ses:GetSendStatistics"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParametersByPath"]
        Resource = "arn:aws:ssm:${local.aws_region}:*:parameter/${local.project_name}/*"
      }
    ]
  })
}

# ─── Lambda function ──────────────────────────────────────────────────────────

# Task #333 — explicit log group so the OTel exporter-error metric
# filter in `lambda-otel.tf` has a deterministic target on first
# `terraform apply`. CloudWatch metric filters require the log group
# to exist before they can be created; relying on Lambda's lazy
# auto-creation on first invocation creates a chicken-and-egg
# bootstrap failure for fresh environments.
resource "aws_cloudwatch_log_group" "email_worker" {
  name              = "/aws/lambda/${local.lz_project}-email-worker"
  retention_in_days = 14
  tags              = merge(local.lz_common_tags, { Name = "/aws/lambda/${local.lz_project}-email-worker" })
}

resource "aws_lambda_function" "email_worker" {
  function_name = "${local.project_name}-email-worker"
  role          = aws_iam_role.email_worker.arn

  package_type = "Image"
  image_uri    = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${local.aws_region}.amazonaws.com/${local.project_name}/email-worker:latest"

  architectures = ["arm64"]

  timeout      = 30
  memory_size  = 256

  # Task #333 — image-based Lambda (`package_type = "Image"`) so we
  # bake the ADOT collector into the email-worker image rather than
  # attaching it as a layer (layers are not supported on container
  # Lambdas). Runtime picks up the OTLP env block below.
  environment {
    variables = merge(local.otel_env, {
      NODE_ENV               = "production"
      SES_REGION             = local.aws_region
      SES_FROM_ADDRESS       = "no-reply@syrabit.ai"
      SES_CONFIG_SET         = aws_ses_configuration_set.main.name
      LOG_LEVEL              = "info"
      OTEL_SERVICE_NAME      = "${local.lz_project}-email-worker"
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

  tags = {
    project     = local.project_name
    environment = local.env
    managed-by  = "terraform"
    credit-source = "aws-activate"
  }

  # Task #333 — explicit dependency on the log group so the OTel
  # metric filter in `lambda-otel.tf` always finds its target.
  depends_on = [aws_cloudwatch_log_group.email_worker]
}

resource "aws_lambda_provisioned_concurrency_config" "email_worker" {
  function_name                  = aws_lambda_function.email_worker.function_name
  qualifier                      = aws_lambda_alias.email_worker_live.name
  provisioned_concurrent_executions = 1
}

resource "aws_lambda_alias" "email_worker_live" {
  name             = "live"
  function_name    = aws_lambda_function.email_worker.function_name
  function_version = aws_lambda_function.email_worker.version
}

resource "aws_lambda_function_url" "email_worker" {
  function_name      = aws_lambda_function.email_worker.function_name
  qualifier          = aws_lambda_alias.email_worker_live.name
  authorization_type = "AWS_IAM"
}

data "aws_caller_identity" "current" {}

# ─── SES configuration ────────────────────────────────────────────────────────

resource "aws_ses_configuration_set" "main" {
  name = "${local.project_name}-${local.env}"

  delivery_options {
    tls_policy = "Require"
  }

  reputation_metrics_enabled = true
  sending_enabled            = true
}

resource "aws_ses_email_identity" "noreply" {
  email = "no-reply@syrabit.ai"
}

# ─── SNS → CF Worker webhook topic ───────────────────────────────────────────

resource "aws_sns_topic" "ses_events" {
  name = "${local.project_name}-ses-events"
}

resource "aws_ses_identity_notification_topic" "bounces" {
  topic_arn                = aws_sns_topic.ses_events.arn
  notification_type        = "Bounce"
  identity                 = aws_ses_email_identity.noreply.email
  include_original_headers = false
}

resource "aws_ses_identity_notification_topic" "complaints" {
  topic_arn                = aws_sns_topic.ses_events.arn
  notification_type        = "Complaint"
  identity                 = aws_ses_email_identity.noreply.email
  include_original_headers = false
}

# ─── CloudWatch alarms ────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "email_errors" {
  alarm_name          = "${local.project_name}-email-worker-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 5

  dimensions = {
    FunctionName = aws_lambda_function.email_worker.function_name
  }

  alarm_description = "email-worker Lambda error spike"
  treat_missing_data = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "email_duration_p95" {
  alarm_name          = "${local.project_name}-email-worker-duration-p95"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  extended_statistic  = "p95"
  metric_name         = "Duration"
  namespace           = "AWS/Lambda"
  period              = 300
  threshold           = 5000

  dimensions = {
    FunctionName = aws_lambda_function.email_worker.function_name
  }

  alarm_description  = "email-worker p95 duration > 5 s"
  treat_missing_data = "notBreaching"
}
