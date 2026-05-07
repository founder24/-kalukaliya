# infra/aws/secrets.tf
#
# Phase 1b — AWS landing zone (Task #328).
#
# AWS Secrets Manager entries that the worker tier needs at runtime.
# Mirrored from the current Railway / GCP Secret Manager stores; values
# are populated out-of-band (1Password → AWS console / CLI). This file
# only declares the secret containers and their access scope — never
# the plaintext values.
#
# Rotation: each secret has a 90-day rotation reminder via
# `recovery_window_in_days = 7` on delete and an explicit
# `lifecycle.ignore_changes = [secret_string]` so out-of-band rotations
# do not drift Terraform state.

locals {
  # name → human description. The IAM runtime policy above grants
  # GetSecretValue on exactly this set (see iam-github-oidc.tf).
  lz_worker_secrets = {
    "supabase/service-role-key" = "Supabase service-role key (DB writes from workers)."
    "upstash/redis-rest-token"  = "Upstash Redis REST token (rate-limit + 429 counter)."
    # Task #556 — "resend/api-key" Secrets Manager entry retired; SES is the
    # sole transactional email path (no fallback, V4 §12 — no silent fallbacks).
    "stripe/webhook-secret"     = "Stripe webhook signing secret (payment workers)."
    "razorpay/webhook-secret"   = "Razorpay webhook signing secret (payment workers, IN)."
    "sentry/dsn-workers"        = "Sentry DSN scoped to the workers project."
    "axiom/ingest-token"        = "Axiom ingest token (parallel log destination)."
    "slack/ops-webhook"         = "Slack incoming webhook for #infra-alerts."
    "pinecone/api-key"          = "Pinecone API key (embedding refresh worker)."
    "cohere/api-key"            = "Cohere API key (re-rank worker)."
    "workers-embed/secret"      = "Cloudflare Workers-AI embed-worker shared secret (deferred-embed Lambda calls embed.syrabit.ai with this on the X-Origin-Auth header). Task #489."
    "mongo/url"                 = "Mongo Atlas SRV connection string used by the Lambda batch jobs (`lambda_batch/*` per Task #551 §B). Same value as the ACA backend's `MONGO_URL`."
    "cloudflare/api-token"      = "Cloudflare API token used by Workers-AI translate calls (`providers/workers_indic.py`) inside the `as-translation-backfill` Lambda (Task #551 §B)."
    "cf-ai-gateway/account-id"  = "Cloudflare AI Gateway account-id used alongside the API token by `providers/workers_indic.py` (Task #551 §B)."
    "gemini/api-key"            = "Vertex/Gemini polish key used by the Assamese translate chain (`routes/ai_chat._assamese_translate_*`) inside the `as-translation-backfill` Lambda (Task #551 §B)."
    "gcp/sa-json"               = "GCP service-account JSON for Vertex polish fallback (`GOOGLE_APPLICATION_CREDENTIALS_JSON`) used by `as-translation-backfill` (Task #551 §B)."
  }
}

resource "aws_secretsmanager_secret" "workers" {
  for_each = local.lz_worker_secrets

  name        = "${local.lz_project}/${local.lz_env}/${each.key}"
  description = each.value

  recovery_window_in_days = 7

  tags = merge(local.lz_common_tags, {
    Name   = "${local.lz_project}/${local.lz_env}/${each.key}"
    secret = each.key
  })
}

# Placeholder version so `terraform apply` does not leave the secret in
# the "no current version" state. The placeholder is a JSON object with
# a single `_placeholder` key — workers must fail loudly if they ever
# read this value, never silently fall back.
resource "aws_secretsmanager_secret_version" "placeholder" {
  for_each = aws_secretsmanager_secret.workers

  secret_id     = each.value.id
  secret_string = jsonencode({ _placeholder = "set-via-1password-rotation" })

  lifecycle {
    ignore_changes = [secret_string]
  }
}

output "worker_secret_arns" {
  value       = { for k, s in aws_secretsmanager_secret.workers : k => s.arn }
  description = "ARN per logical secret name; mirror into env-var mapping in the runbook."
}
