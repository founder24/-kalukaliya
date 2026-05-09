# infra/aws/lambda-otel.tf
#
# Phase 5 — Observability rewire (Task #333).
#
# Locals + alarms for OTel-on-Lambda. The OTLP env vars are added
# inline to each Lambda function resource in `lambda-workers.tf`,
# `lambda-email-worker.tf`, and `lambda-bedrock-proxy.tf` (Terraform
# requires `environment` to be declared on the function itself).
#
# **No `layers = [...]`**: every worker Lambda is a container-image
# Lambda (`package_type = "Image"`), and AWS does not support layers
# on container Lambdas. The ADOT collector is therefore BAKED INTO
# each worker image (see the `services/backend/sqs_consumers/`,
# `services/backend/email-worker/`, and `services/backend/bedrock-proxy/`
# Dockerfiles, which `COPY --from=public.ecr.aws/aws-observability/aws-otel-collector`
# the prebuilt collector binary into `/opt/otel-collector` and start
# it as a sidecar process before the handler).
#
# What this file owns:
#
#   1. A reusable `otel_env` map of OTLP-exporter env vars sourced
#      by every function. The exporter endpoint points at the
#      in-image collector on localhost; the collector then fans out
#      to App Insights + Axiom in parallel based on credentials
#      loaded from SSM at cold-start (see the `APP_INSIGHTS_SSM_PARAM`
#      / `AXIOM_TOKEN_SSM_PARAM` references in each function's env).
#   2. CloudWatch metric filter + alarm that pages via the existing
#      `ops_alerts` SNS topic when the in-image collector's own
#      export is failing.
#
# X-Ray is RETAINED on every function (`tracing_config { mode = "Active" }`
# in each function definition) — it stays the AWS-native fast-path
# for in-console flame graphs while triaging a Lambda incident, but
# it is no longer the source of truth (Application Insights is).

locals {
  # Shared OTLP exporter env. The collector baked into each worker
  # image reads these and fans out to both sinks in parallel.
  # `OTEL_RESOURCE_ATTRIBUTES` pins every span to AWS so App Insights
  # KQL queries can slice by `cloud.provider` and answer "is this a
  # DO problem, AWS problem, or Azure problem?" in one query.
  otel_env = {
    OTEL_RESOURCE_ATTRIBUTES    = "cloud.provider=aws,cloud.platform=aws_lambda,cloud.region=ap-south-1,deployment.environment=production,service.namespace=syrabit"
    OTEL_TRACES_EXPORTER        = "otlp"
    OTEL_METRICS_EXPORTER       = "none" # CloudWatch keeps native metrics; OTel here is traces-only
    OTEL_LOGS_EXPORTER          = "otlp"
    OTEL_EXPORTER_OTLP_PROTOCOL = "http/protobuf"
    OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4318"
  }

  # Function names covered by the OTel exporter-error alarm in the
  # default region (ap-south-1). bedrock-proxy is created under
  # `provider = aws.us_east_1` and is alarmed separately below to
  # avoid the cross-region log-group lookup error that would
  # otherwise fail terraform apply.
  otel_lambda_function_names_default_region = concat(
    [for k, _ in local.sqs_worker_lambdas : "${local.lz_project}-${k}-consumer"],
    ["${local.lz_project}-email-worker"],
  )
  otel_lambda_function_names_us_east_1 = [
    "${local.lz_project}-bedrock-proxy",
  ]
}

# ─── Default region (ap-south-1) ────────────────────────────────────────
# CloudWatch metric filter: the ADOT collector baked into each worker
# image logs `OTLP exporter failed` to its function's own log group
# when an export POST fails. Counting those across the worker fleet
# gives us a single signal for "App Insights or Axiom ingest is
# silently dropping AWS spans".

resource "aws_cloudwatch_log_metric_filter" "otel_exporter_errors" {
  for_each = toset(local.otel_lambda_function_names_default_region)

  name           = "${each.value}-otel-exporter-errors"
  log_group_name = "/aws/lambda/${each.value}"
  pattern        = "\"OTLP exporter failed\""

  metric_transformation {
    name          = "OtelExporterErrors"
    namespace     = "Syrabit/Workers"
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }

  # Explicit dependency on every covered log group so first-apply
  # cannot race the metric filter ahead of the group it targets.
  # Each log group is owned by the respective Lambda's TF file:
  # `aws_cloudwatch_log_group.sqs_consumer` (lambda-workers.tf)
  # and `aws_cloudwatch_log_group.email_worker` (lambda-email-worker.tf).
  depends_on = [
    aws_cloudwatch_log_group.sqs_consumer,
    aws_cloudwatch_log_group.email_worker,
  ]
}

resource "aws_cloudwatch_metric_alarm" "otel_exporter_errors" {
  alarm_name          = "${local.lz_project}-otel-exporter-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "OtelExporterErrors"
  namespace           = "Syrabit/Workers"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_description   = "AWS Lambda OTel exporter is failing repeatedly — App Insights + Axiom may be missing AWS spans. Runbook: docs/infra/observability.md."
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]

  tags = local.lz_common_tags
}

# ─── us-east-1 sibling for bedrock-proxy ────────────────────────────────
# bedrock-proxy lives in us-east-1 (Bedrock model availability), so its
# log group + metric filter + alarm must be declared under the
# `aws.us_east_1` provider alias. The SNS topic ARN is cross-region
# referenceable because CloudWatch alarms accept cross-region SNS
# targets.

resource "aws_cloudwatch_log_metric_filter" "otel_exporter_errors_us_east_1" {
  provider = aws.us_east_1
  for_each = toset(local.otel_lambda_function_names_us_east_1)

  name           = "${each.value}-otel-exporter-errors"
  log_group_name = "/aws/lambda/${each.value}"
  pattern        = "\"OTLP exporter failed\""

  metric_transformation {
    name          = "OtelExporterErrors"
    namespace     = "Syrabit/Workers"
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }

  # Same first-apply ordering guarantee as the default-region filter
  # above, scoped to the us-east-1 bedrock-proxy log group.
  depends_on = [
    aws_cloudwatch_log_group.bedrock_proxy,
  ]
}

resource "aws_cloudwatch_metric_alarm" "otel_exporter_errors_us_east_1" {
  provider            = aws.us_east_1
  alarm_name          = "${local.lz_project}-otel-exporter-errors-us-east-1"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "OtelExporterErrors"
  namespace           = "Syrabit/Workers"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_description   = "AWS Lambda OTel exporter is failing repeatedly in us-east-1 (bedrock-proxy) — App Insights + Axiom may be missing AWS spans. Runbook: docs/infra/observability.md."
  # NOTE (2026-05-09): CloudWatch alarms can only fire SNS topics in their
  # own region. ops_alerts lives in ap-south-1; cross-region notifications
  # are deferred until a us-east-1 ops_alerts mirror is provisioned.
  # alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  # ok_actions          = [aws_sns_topic.ops_alerts.arn]

  tags = local.lz_common_tags
}

output "otel_lambda_function_names" {
  value = concat(
    local.otel_lambda_function_names_default_region,
    local.otel_lambda_function_names_us_east_1,
  )
  description = "Lambdas that publish OTel spans to App Insights + Axiom (across all regions)."
}
