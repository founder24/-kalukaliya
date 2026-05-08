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

# Task #571 — `cache-effectiveness` Lambda mints a short-lived admin
# JWT to call /api/health/cache. The signing secret lives alongside
# the other Mongo / Pinecone replicas in Secrets Manager (canonical
# source = Azure Key Vault per V4 §6).
data "aws_secretsmanager_secret" "admin_jwt" {
  name       = "${local.lz_project}/${local.lz_env}/admin-jwt/secret"
  depends_on = [aws_secretsmanager_secret.workers]
}

# Task #565 — `chat-credit-runway` Lambda writes the integer runway
# estimate to Upstash Redis (selector reads it via the backend's
# `deps.redis_client`) and captures Sentry events on compute /
# publish failure. The REST URL + token come from the existing
# `upstash/redis-rest-token` secret entry plus the new
# `upstash/redis-rest-url` entry declared in `secrets.tf`. SENTRY_DSN
# is the workers-scoped DSN already present at `sentry/dsn-workers`.
data "aws_secretsmanager_secret" "upstash_redis_rest_url" {
  name       = "${local.lz_project}/${local.lz_env}/upstash/redis-rest-url"
  depends_on = [aws_secretsmanager_secret.workers]
}

data "aws_secretsmanager_secret" "upstash_redis_rest_token" {
  name       = "${local.lz_project}/${local.lz_env}/upstash/redis-rest-token"
  depends_on = [aws_secretsmanager_secret.workers]
}

data "aws_secretsmanager_secret" "sentry_dsn_workers" {
  name       = "${local.lz_project}/${local.lz_env}/sentry/dsn-workers"
  depends_on = [aws_secretsmanager_secret.workers]
}

# Task #565 — GCP billing-export coordinates and credit-pool size for the
# `chat-credit-runway` Lambda. Defaults are placeholder values; production
# overrides via tfvars (e.g. infra/aws/terraform.tfvars) so a Lambda
# redeploy is not required to roll a new credit grant.
variable "gcp_billing_project" {
  type        = string
  description = "GCP project that owns the BigQuery billing export dataset (Task #565)."
  default     = "syrabit-prod"
}

variable "gcp_billing_dataset" {
  type        = string
  description = "BigQuery dataset name containing the gcp_billing_export_v1_* tables (Task #565)."
  default     = "billing_export"
}

variable "gcp_billing_table_prefix" {
  type        = string
  description = "Prefix of the per-billing-account export table; the Lambda wildcards on `<prefix>_*` (Task #565)."
  default     = "gcp_billing_export_v1"
}

variable "gcp_total_credits_usd" {
  type        = number
  description = "Total GCP startup-credit pool size in USD; the Lambda computes remaining = this − cumulative_cost (Task #565)."
  default     = 0
}

variable "gcp_credits_start_date" {
  type        = string
  description = "YYYY-MM-DD when the credit pool started accumulating burn (Task #565)."
  default     = "2025-08-01"
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
    # Task #571 — daily AI-cache-effectiveness shipper. Scrapes
    # /api/health/cache (admin-only, JWT minted from ADMIN_JWT_SECRET)
    # and emits per-content-type counters to the `Syrabit/Cache`
    # CloudWatch namespace. Two alarms below ride on the (ContentType=Total)
    # dimension: hit-ratio floor (<30 %) + cardinality spike (>3x 7-day MA).
    "cache-effectiveness" = {
      handler           = "lambda_batch.cache_effectiveness.handler"
      memory_mb         = 128
      timeout_s         = 120
      schedule          = "cron(15 3 * * ? *)" # daily 03:15 UTC (after as-translation-backfill 03:00)
      max_docs_per_run  = 0
      description       = "Task #571 — Daily AI-input-cache effectiveness shipper to Syrabit/Cache namespace."
    }
    # Task #565 — daily GCP credit-runway snapshot. Reads the GCP
    # Billing BigQuery export, computes
    # `remaining_credits / (trailing_30d_burn / 30)`, writes the
    # integer to Upstash Redis at `chat:credit_runway_days` (TTL 48h)
    # so `cost_caps._select_chat_primary`'s 60s in-process cache picks
    # it up on the next refresh — flips the Vertex ↔ Workers-AI
    # Llama-3.2-3B chain head when projected runway ≤ 90 days
    # without a backend redeploy. CW alarm `chat-credit-runway-stale`
    # below pages on-call when the metric is missing >24h.
    "chat-credit-runway" = {
      handler           = "lambda_batch.chat_credit_runway.handler"
      memory_mb         = 256
      timeout_s         = 300
      schedule          = "cron(30 3 * * ? *)" # daily 03:30 UTC (after cache-effectiveness 03:15)
      max_docs_per_run  = 0
      description       = "Task #565 — Daily GCP credit-runway snapshot publisher (BigQuery → Upstash Redis + Syrabit/Cost CW namespace)."
    }

    # Task #565 — Sentry-backed freshness probe for the runway value.
    # Acceptance criterion explicitly requires a Sentry alert when the
    # Redis value is missing >24h. The CloudWatch `chat-credit-runway-stale`
    # alarm catches the same condition on the SNS side, but Sentry is the
    # founder's primary on-call channel (Task #558 — errors-only Sentry),
    # so we run an *independent* hourly probe that reads the Redis key
    # directly and `sentry_sdk.capture_message`s when the value is missing
    # or has aged past `RUNWAY_FRESHNESS_THRESHOLD_S` (default 24h). Hourly
    # cadence × Sentry first-event dedup → on-call sees the stale-runway
    # condition within ~1h, well inside the 24h SLO. Independence from
    # the publisher matters: if the publisher Lambda fails to even
    # invoke, this probe is the only thing that detects it.
    "chat-credit-runway-freshness" = {
      handler           = "lambda_batch.chat_credit_runway.freshness_handler"
      memory_mb         = 128
      timeout_s         = 60
      schedule          = "rate(1 hour)"
      max_docs_per_run  = 0
      description       = "Task #565 — Hourly Sentry-backed freshness probe for chat:credit_runway_days (>24h missing → Sentry alert)."
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
      # Task #571 — additionally allow `Syrabit/Cache` so the nightly
      # `cache-effectiveness` Lambda can publish the AI-input-cache /
      # ai_response_cache / rag_cache / L1 / edge hit-rate rows it
      # collects from /api/health/cache.
      # Task #565 — additionally allow `Syrabit/Cost` so the daily
      # `chat-credit-runway` Lambda can publish the
      # `ChatCreditRunwayDays` metric the freshness alarm rides on.
      # All three namespaces are pinned via `StringEquals` so the role
      # cannot drift to broader CloudWatch write access — adding a
      # fourth namespace requires another cap-policy review.
      {
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = ["Syrabit/BatchJobs", "Syrabit/Cache", "Syrabit/Cost"]
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
      # Task #571 — only `cache-effectiveness` reads ADMIN_JWT_SECRET to
      # mint the short-lived JWT it presents to /api/health/cache, but
      # injecting the ARN on every job is harmless (handlers ignore env
      # vars they do not consume) and keeps the env block uniform.
      ADMIN_JWT_SECRET_ARN              = data.aws_secretsmanager_secret.admin_jwt.arn
      BACKEND_URL                       = "https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io"
      # Task #565 — `chat-credit-runway` consumes Upstash REST creds +
      # Sentry DSN. Other handlers harmlessly ignore them.
      UPSTASH_REDIS_REST_URL_SECRET_ARN   = data.aws_secretsmanager_secret.upstash_redis_rest_url.arn
      UPSTASH_REDIS_REST_TOKEN_SECRET_ARN = data.aws_secretsmanager_secret.upstash_redis_rest_token.arn
      SENTRY_DSN_SECRET_ARN               = data.aws_secretsmanager_secret.sentry_dsn_workers.arn
      # GCP billing-export coordinates. Operator overrides via Terraform
      # tfvars or by editing the Lambda env directly post-apply.
      GCP_BILLING_PROJECT                 = var.gcp_billing_project
      GCP_BILLING_DATASET                 = var.gcp_billing_dataset
      GCP_BILLING_TABLE_PREFIX            = var.gcp_billing_table_prefix
      GCP_TOTAL_CREDITS_USD               = tostring(var.gcp_total_credits_usd)
      GCP_CREDITS_START_DATE              = var.gcp_credits_start_date
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

# ── Task #571 — Cache-effectiveness alarms (Syrabit/Cache namespace) ────────
# Both alarms target the (ContentType=Total) dimension that the
# cache_effectiveness Lambda emits. Per-content-type rows are still
# published to CloudWatch so the admin Observability panel can chart
# them; we just don't page on per-row noise.

resource "aws_cloudwatch_metric_alarm" "cache_ai_hitratio_low" {
  # Round-8 — alarm switched from `HitRatio` (lifetime cumulative) to
  # `HitRatio24h` (rolling 24h, computed from Redis hourly buckets).
  # Lifetime ratios warm to a stable value and never cross a threshold
  # again; the 24h ratio is what actually catches a fresh regression.
  # Source-of-truth for the underlying counters: Redis `aic:hr24:*`
  # hourly buckets aggregated across all backend replicas (see
  # ai_input_cache._record_24h_event). The Lambda only mirrors the
  # already-aggregated value to CloudWatch.
  alarm_name          = "${local.lz_project}-cache-ai-hitratio-low-${local.lz_env}"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  metric_name         = "HitRatio24h"
  namespace           = "Syrabit/Cache"
  period              = 86400
  statistic           = "Average"
  threshold           = 0.30
  treat_missing_data  = "breaching"
  alarm_description   = "Task #571 — AI-input-cache fleet-wide HitRatio24h (rolling 24h, aggregated across all backend replicas via Redis hourly buckets) dropped below 30%. Likely causes: a prompt-template bump that has not yet refilled, a normalizer regression that fragments keys, or a TTL that is too short for the call volume. Inspect /admin/observability cache panel + miss_reasons_24h breakdown."

  dimensions = {
    ContentType = "Total"
  }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]
  tags          = local.lz_common_tags
}

# Cardinality spike — detected via Metric Math: today's UniqueKeys24h
# > 3x the trailing 7-day moving average. Catches the runaway-key
# pattern (e.g. a generator started embedding a wall-clock timestamp
# into the prompt, fragmenting the keyspace).
#
# Task #571 round-3: declared per content type via `for_each` so a
# regression in one generator (e.g. flashcard suddenly fragmenting)
# pages on-call instead of being averaged out by the Total row. The
# `Total` row is included in the set so the rollup keeps its alarm
# too — matches the pattern used by the Assamese-translation backfill
# alarms.
locals {
  cache_cardinality_alarm_dims = toset([
    "Total",
    "mcq", "flashcard", "definition", "formatter",
    "translate", "ocr", "stage3_polish",
  ])
}

resource "aws_cloudwatch_metric_alarm" "cache_cardinality_spike" {
  for_each            = local.cache_cardinality_alarm_dims
  alarm_name          = "${local.lz_project}-cache-cardinality-spike-${each.key}-${local.lz_env}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_description   = "Task #571 — UniqueKeys24h for ContentType=${each.key} spiked above 3x the trailing 7-day moving average. Almost always means a generator is fragmenting cache keys (timestamp/uuid leaked into prompt, normalizer regression). Inspect /admin/observability cache panel + miss_reasons for this content_type."

  metric_query {
    id          = "today"
    return_data = false
    metric {
      namespace   = "Syrabit/Cache"
      metric_name = "UniqueKeys24h"
      period      = 86400
      stat        = "Maximum"
      dimensions  = { ContentType = each.key }
    }
  }
  # Trailing 7-day moving average. The metric's period must equal the
  # window length we want to average over — period=604800 + stat=Average
  # over the alarm's evaluation window collapses into "average value of
  # UniqueKeys24h across the last 7 days", which is the moving-average
  # signal we need. (CloudWatch fetches `evaluation_periods × period`
  # of data; with evaluation_periods=1 + period=604800 the alarm will
  # always look at the last 7 days of data when computing `ma7`.)
  metric_query {
    id          = "ma7"
    return_data = false
    metric {
      namespace   = "Syrabit/Cache"
      metric_name = "UniqueKeys24h"
      period      = 604800
      stat        = "Average"
      dimensions  = { ContentType = each.key }
    }
  }
  metric_query {
    id          = "spike"
    return_data = true
    expression  = "today - 3 * ma7"
    label       = "UniqueKeys24h(${each.key}) - 3x 7d MA"
  }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]
  tags          = local.lz_common_tags
}

# ── Task #565 — chat-credit-runway freshness + low-runway alarms ────────────
# `chat-credit-runway-stale` rides on Syrabit/Cost::ChatCreditRunwayDays
# with treat_missing_data=breaching so a >24h gap in the daily Lambda's
# publish (Lambda failed to invoke, BQ outage, Upstash outage, …) pages
# on-call via the existing ops_alerts SNS topic. The Lambda also captures
# Sentry events directly on each compute / publish failure, but the CW
# alarm is the safety net that catches the "Lambda silently never even
# started" case the in-handler Sentry path cannot.
resource "aws_cloudwatch_metric_alarm" "chat_credit_runway_stale" {
  alarm_name          = "${local.lz_project}-chat-credit-runway-stale-${local.lz_env}"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  metric_name         = "ChatCreditRunwayDays"
  namespace           = "Syrabit/Cost"
  period              = 86400
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "breaching"
  alarm_description   = "Task #565 — Syrabit/Cost::ChatCreditRunwayDays has not been published for >24h. Either the daily `chat-credit-runway` Lambda failed to invoke, the GCP Billing BigQuery export query failed, or Upstash publish failed. The Vertex ↔ Workers-AI Llama-3.2-3B chain head will silently stay on the env-derived path until this is resolved (V4 §12 — fail loud)."

  dimensions = {
    Source = "lambda"
  }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]
  tags          = local.lz_common_tags
}

# Early-warning: runway <60d is well past the 90d flip threshold; if
# we see this without the chain having flipped, something is wrong
# with the selector wiring.
resource "aws_cloudwatch_metric_alarm" "chat_credit_runway_low" {
  alarm_name          = "${local.lz_project}-chat-credit-runway-low-${local.lz_env}"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  metric_name         = "ChatCreditRunwayDays"
  namespace           = "Syrabit/Cost"
  period              = 86400
  statistic           = "Maximum"
  threshold           = 60
  treat_missing_data  = "notBreaching"
  alarm_description   = "Task #565 — Projected GCP credit runway has dropped below 60 days for 2 consecutive daily publishes. The Task #554 chain should already have flipped to Workers-AI Llama-3.2-3B head at the 90d threshold; verify the flip via /api/admin/health and inspect cost_caps._select_chat_primary cache."

  dimensions = {
    Source = "lambda"
  }

  alarm_actions = [aws_sns_topic.ops_alerts.arn]
  ok_actions    = [aws_sns_topic.ops_alerts.arn]
  tags          = local.lz_common_tags
}
