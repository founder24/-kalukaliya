# infra/aws/sqs-config-ssm.tf
#
# Phase 4 — Async worker port (Task #332).
#
# Single SSM parameter that publishes the {gcp_key: queue_url} map
# of every SQS queue created by `sqs.tf`. The DO API producers read
# this parameter on cold-start via `services/backend/sqs_fanout.py`
# (`_queue_url_map`) and cache it for the process lifetime, so a
# region failover or queue rename rolls forward simply by re-applying
# Terraform — no producer redeploy is required.
#
# Why one composite parameter rather than one-per-queue:
#   • Atomic read — a single `GetParameter` call returns the entire
#     map; we never see a half-applied state where (say) seo-indexnow
#     resolves but bing-keyword does not.
#   • Lower SSM API call rate — Activate-credit-friendly, and the
#     producer code does not need to know the parameter naming
#     convention for each individual queue.
#   • Mirrors the existing `worker_secret_arns` output convention in
#     `secrets.tf`.

resource "aws_ssm_parameter" "sqs_worker_queue_urls" {
  name        = "/${local.lz_project}/${local.lz_env}/sqs-worker-queue-urls"
  description = "JSON object mapping cloud-tasks.json queue keys → SQS queue URL. Read by services/backend/sqs_fanout.py at process cold-start."
  type        = "String"
  tier        = "Standard"

  # Same map shape `services/backend/sqs_fanout.py:_queue_url_map`
  # expects: {"seo-indexnow": "https://sqs...", ...}.
  value = jsonencode({ for k, q in aws_sqs_queue.worker : k => q.url })

  tags = merge(local.lz_common_tags, {
    Name      = "/${local.lz_project}/${local.lz_env}/sqs-worker-queue-urls"
    component = "sqs-config"
  })
}

# Mirror as a Secrets Manager entry as well — backend code that
# already reads Secrets Manager (per the worker IAM contract) can
# consume this without growing a parallel SSM-reader code path. The
# two stores are kept in lockstep here because the Phase 1b worker
# IAM role intentionally only grants Secrets Manager (see
# `iam-github-oidc.tf`); the SSM grant added in `lambda-workers.tf`
# is for Lambda's own runtime config (handler hints, log levels)
# rather than for the queue URL map.
resource "aws_secretsmanager_secret" "sqs_worker_queue_urls" {
  name                    = "${local.lz_project}/${local.lz_env}/sqs-worker-queue-urls"
  description             = "Mirror of /sqs-worker-queue-urls SSM parameter for callers that read Secrets Manager."
  recovery_window_in_days = 7

  tags = merge(local.lz_common_tags, {
    Name      = "${local.lz_project}/${local.lz_env}/sqs-worker-queue-urls"
    component = "sqs-config"
  })
}

resource "aws_secretsmanager_secret_version" "sqs_worker_queue_urls" {
  secret_id     = aws_secretsmanager_secret.sqs_worker_queue_urls.id
  secret_string = jsonencode({ for k, q in aws_sqs_queue.worker : k => q.url })
}

output "sqs_worker_queue_urls_ssm_param" {
  description = "SSM parameter name carrying the {gcp_key: queue_url} JSON. Producer-side env: SQS_QUEUE_URL_SSM_PARAM."
  value       = aws_ssm_parameter.sqs_worker_queue_urls.name
}

output "sqs_worker_queue_urls_secret_arn" {
  description = "Secrets Manager mirror of the same JSON map."
  value       = aws_secretsmanager_secret.sqs_worker_queue_urls.arn
}
