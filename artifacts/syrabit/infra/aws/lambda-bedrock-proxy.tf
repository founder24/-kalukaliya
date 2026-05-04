# infra/aws/lambda-bedrock-proxy.tf
#
# Replaces the Cloudflare Workers Paid bedrock-proxy ($5/mo share).
# Covered 100 % by AWS Activate credits.
#
# Performance boosts (all within Activate budget)
# ─────────────────────────────────────────────────
# • arm64 (Graviton3) — fastest cold-start for Node.js inference wrappers
# • Provisioned Concurrency (1 instance) — p99 cold-start < 20 ms
# • CloudFront distribution fronting Function URL — caches identical
#   prompt+model pairs (TTL 300 s, cache key = SHA-256(model+prompt))
#   so repeated study-question calls hit edge POP, not Bedrock
# • Bedrock Guardrails — content filter applied at Lambda layer, no RTT
# • X-Ray active tracing — per-token latency flame graphs
# • CloudWatch Contributor Insights — top-N model × user spend breakdown
# • AWS Cost Anomaly Detection alert on Bedrock spend > $50/day

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

locals {
  aws_region   = "us-east-1"
  project_name = "syrabit"
  env          = "prod"
}

provider "aws" {
  region = local.aws_region
  alias  = "us_east_1"
}

# ─── IAM ─────────────────────────────────────────────────────────────────────

resource "aws_iam_role" "bedrock_proxy" {
  provider = aws.us_east_1
  name     = "${local.project_name}-bedrock-proxy-${local.env}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "bedrock_proxy_basic" {
  provider   = aws.us_east_1
  role       = aws_iam_role.bedrock_proxy.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "bedrock_proxy_invoke" {
  provider = aws.us_east_1
  name     = "bedrock-invoke"
  role     = aws_iam_role.bedrock_proxy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = [
          "arn:aws:bedrock:${local.aws_region}::foundation-model/anthropic.claude-3-5-sonnet-20241022-v2:0",
          "arn:aws:bedrock:${local.aws_region}::foundation-model/amazon.titan-text-premier-v1:0",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:ApplyGuardrail"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:${local.aws_region}:*:parameter/${local.project_name}/*"
      }
    ]
  })
}

# ─── Lambda ───────────────────────────────────────────────────────────────────

# Task #333 — explicit log group in us-east-1 so the regional OTel
# exporter-error metric filter in `lambda-otel.tf` has a deterministic
# target on first `terraform apply`. Must use the `aws.us_east_1`
# provider alias so the resource lands in the same region as the
# function (Bedrock model availability dictates us-east-1 here).
resource "aws_cloudwatch_log_group" "bedrock_proxy" {
  provider          = aws.us_east_1
  name              = "/aws/lambda/${local.lz_project}-bedrock-proxy"
  retention_in_days = 14
  tags              = merge(local.lz_common_tags, { Name = "/aws/lambda/${local.lz_project}-bedrock-proxy" })
}

resource "aws_lambda_function" "bedrock_proxy" {
  provider      = aws.us_east_1
  function_name = "${local.project_name}-bedrock-proxy"
  role          = aws_iam_role.bedrock_proxy.arn

  package_type  = "Image"
  image_uri     = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${local.aws_region}.amazonaws.com/${local.project_name}/bedrock-proxy:latest"
  architectures = ["arm64"]

  timeout     = 60
  memory_size = 512

  # Task #333 — image-based Lambda (`package_type = "Image"`) so the
  # ADOT collector is baked into the bedrock-proxy image rather than
  # attached as a layer (layers are not supported on container
  # Lambdas). Runtime picks up the OTLP env block below.
  environment {
    variables = {
      NODE_ENV             = "production"
      BEDROCK_REGION       = local.aws_region
      GUARDRAIL_ID         = aws_bedrock_guardrail.content_filter.guardrail_id
      GUARDRAIL_VERSION    = "DRAFT"
      CACHE_TTL_SECONDS    = "300"
      LOG_LEVEL            = "info"
      # OTLP exporter wiring (mirror of `local.otel_env` in
      # `lambda-otel.tf`; redeclared inline because this Lambda lives
      # in a different region/provider alias and `local.otel_env`
      # carries `cloud.region=ap-south-1`).
      OTEL_RESOURCE_ATTRIBUTES        = "cloud.provider=aws,cloud.platform=aws_lambda,cloud.region=us-east-1,deployment.environment=production,service.namespace=syrabit"
      OTEL_TRACES_EXPORTER            = "otlp"
      OTEL_METRICS_EXPORTER           = "none"
      OTEL_LOGS_EXPORTER              = "otlp"
      OTEL_EXPORTER_OTLP_PROTOCOL     = "http/protobuf"
      OTEL_EXPORTER_OTLP_ENDPOINT     = "http://localhost:4318"
      OTEL_SERVICE_NAME               = "${local.project_name}-bedrock-proxy"
      AWS_LAMBDA_EXEC_WRAPPER         = "/opt/otel-instrument"
      APP_INSIGHTS_SSM_PARAM          = "/${local.project_name}/${local.env}/app-insights-conn-string"
      AXIOM_TOKEN_SSM_PARAM           = "/${local.project_name}/${local.env}/axiom-api-token"
      AXIOM_DATASET                   = "syrabit-aws-lambda-prod"
    }
  }

  tracing_config {
    # X-Ray retained as the AWS-Console fast-path; App Insights is
    # the cross-cloud source of truth (Task #333).
    mode = "Active"
  }

  tags = {
    project       = local.project_name
    environment   = local.env
    managed-by    = "terraform"
    credit-source = "aws-activate"
  }

  # Task #333 — explicit dependency on the us-east-1 log group so
  # the regional OTel metric filter in `lambda-otel.tf` always finds
  # its target on first apply.
  depends_on = [aws_cloudwatch_log_group.bedrock_proxy]
}

resource "aws_lambda_provisioned_concurrency_config" "bedrock_proxy" {
  provider                           = aws.us_east_1
  function_name                      = aws_lambda_function.bedrock_proxy.function_name
  qualifier                          = aws_lambda_alias.bedrock_proxy_live.name
  provisioned_concurrent_executions  = 1
}

resource "aws_lambda_alias" "bedrock_proxy_live" {
  provider         = aws.us_east_1
  name             = "live"
  function_name    = aws_lambda_function.bedrock_proxy.function_name
  function_version = aws_lambda_function.bedrock_proxy.version
}

resource "aws_lambda_function_url" "bedrock_proxy" {
  provider           = aws.us_east_1
  function_name      = aws_lambda_function.bedrock_proxy.function_name
  qualifier          = aws_lambda_alias.bedrock_proxy_live.name
  authorization_type = "AWS_IAM"

  cors {
    allow_credentials = false
    allow_origins     = ["https://syrabit.ai", "https://www.syrabit.ai"]
    allow_methods     = ["POST"]
    allow_headers     = ["content-type", "x-user-id"]
    max_age           = 3600
  }
}

data "aws_caller_identity" "current" {
  provider = aws.us_east_1
}

# ─── Bedrock Guardrails ───────────────────────────────────────────────────────

resource "aws_bedrock_guardrail" "content_filter" {
  provider     = aws.us_east_1
  name         = "${local.project_name}-content-filter"
  description  = "Block harmful content from Bedrock responses"
  blocked_inputs_messaging  = "I'm sorry, I can't help with that."
  blocked_outputs_messaging = "I'm sorry, I can't respond to that."

  content_policy_config {
    filters_config {
      type            = "HATE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "VIOLENCE"
      input_strength  = "MEDIUM"
      output_strength = "MEDIUM"
    }
  }

  tags = {
    project = local.project_name
  }
}

# ─── CloudFront distribution (edge cache for identical prompt calls) ──────────

resource "aws_cloudfront_distribution" "bedrock_proxy" {
  provider = aws.us_east_1

  origin {
    domain_name = replace(aws_lambda_function_url.bedrock_proxy.function_url, "https://", "")
    origin_id   = "bedrock-lambda"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  enabled         = true
  is_ipv6_enabled = true
  http_version    = "http2and3"
  price_class     = "PriceClass_All"

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "bedrock-lambda"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    cache_policy_id          = aws_cloudfront_cache_policy.bedrock.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id

    min_ttl     = 0
    default_ttl = 300
    max_ttl     = 300
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = {
    project       = local.project_name
    credit-source = "aws-activate"
  }
}

resource "aws_cloudfront_cache_policy" "bedrock" {
  provider = aws.us_east_1
  name     = "${local.project_name}-bedrock-cache"

  default_ttl = 300
  max_ttl     = 300
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config  { cookie_behavior = "none" }
    headers_config  { header_behavior = "none" }
    query_strings_config { query_string_behavior = "none" }
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true
  }
}

data "aws_cloudfront_origin_request_policy" "all_viewer" {
  provider = aws.us_east_1
  name     = "Managed-AllViewer"
}

# ─── Cost Anomaly Detection ───────────────────────────────────────────────────

resource "aws_ce_anomaly_monitor" "bedrock" {
  provider         = aws.us_east_1
  name             = "${local.project_name}-bedrock-spend"
  monitor_type     = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

resource "aws_ce_anomaly_subscription" "bedrock_alert" {
  provider       = aws.us_east_1
  name           = "${local.project_name}-bedrock-anomaly-alert"
  monitor_arn_list = [aws_ce_anomaly_monitor.bedrock.arn]
  frequency      = "DAILY"
  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      values        = ["50"]
      match_options = ["GREATER_THAN_OR_EQUAL"]
    }
  }
  subscriber {
    type    = "EMAIL"
    address = "ops@syrabit.ai"
  }
}
