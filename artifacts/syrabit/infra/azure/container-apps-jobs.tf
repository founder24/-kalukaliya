# infra/azure/container-apps-jobs.tf
#
# Phase 4 — Cron port (Task #332).
#
# Azure Container Apps Jobs that replace the 38 in-process asyncio
# loops marked `landing=aca-job` in
# `docs/infra/inventory/asyncio-loops.md`. Each loop becomes a
# scheduled Container Apps Job firing on the loop's original cadence
# instead of running forever inside the API process.
#
# All jobs share:
#   • one Container Apps Environment (`syrabit-cron-env`)
#   • one user-assigned managed identity
#     (`azurerm_user_assigned_identity.cron_jobs_runtime` from
#      `iam-github-oidc.tf`) so each job can pull from ACR + read
#     Key Vault secrets without per-job credential plumbing.
#   • the same container image (`syrabit-cron-jobs:latest`) — the
#     entrypoint script `services/cron-jobs/run.py` dispatches on the
#     `JOB_NAME` env var to the matching loop coroutine in
#     artifacts/syrabit-backend/. The DISPATCH table in run.py and
#     the `cron_jobs` map below MUST share the same key set; a CI
#     check (`services/cron-jobs/tests/test_dispatch_imports.py`)
#     enforces this so a job added in one place without the other
#     fails the build.

# ─── Container Apps Environment ──────────────────────────────────────────────

resource "azurerm_container_app_environment" "cron" {
  name                       = "${local.lz_project}-cron-env"
  resource_group_name        = azurerm_resource_group.cron_obs.name
  location                   = azurerm_resource_group.cron_obs.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.cron_obs.id

  # Workload profile = Consumption keeps us inside the Azure for
  # Startups credit envelope; jobs only pay for the seconds they run.
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }

  # Pin to the same VNet the Phase 1c landing zone created so the
  # jobs can reach the private Cosmos endpoint without traversing
  # the public internet.
  infrastructure_subnet_id       = azurerm_subnet.cron_jobs.id
  internal_load_balancer_enabled = false

  tags = local.lz_common_tags
}

locals {
  # The single image baked from `services/cron-jobs/Dockerfile`. The
  # GitHub Actions release workflow tags `:latest` after every merge
  # to main; Terraform pins to that tag so routine code-only deploys
  # do not require an apply.
  cron_image = "${azurerm_container_registry.cron_obs.login_server}/syrabit-cron-jobs:latest"

  # ─── Job catalogue ─────────────────────────────────────────────
  # Keys map 1:1 to `services/cron-jobs/run.py:DISPATCH`. Each entry
  # below corresponds to a loop in `docs/infra/inventory/asyncio-loops.md`
  # (rows marked `landing=aca-job`). Cron expressions are derived from
  # the inventory's "Cadence (today)" column:
  #
  #   "every N min"   → "*/N * * * *"
  #   "every N s"     → "*/1 * * * *" (1-min floor; CAJ has no sub-min)
  #   "every N h"     → "0 */N * * *"
  #   "hourly"        → "0 * * * *"
  #   "nightly"       → "0 2 * * *"   (02:00 UTC, off-peak window)
  #   "weekly"        → "0 9 * * 1"   (Mon 09:00 IST = 03:30 UTC; close
  #                                    enough — exact IST conversion is
  #                                    handled by job-local code that
  #                                    short-circuits on weekday mismatch)
  #   "monthly"       → "0 4 1 * *"
  #   "every 6 h"     → "0 */6 * * *"
  #   "once at boot"  → "0 0 1 1 *"   (essentially never; one-shot
  #                                    jobs are triggered manually
  #                                    from the deploy pipeline)
  #
  # `kind` is purely descriptive (read by the AdminCronJobsCard
  # filter) — `loop` for ex-asyncio loops, `scheduler` for jobs that
  # were originally Cloud Scheduler entries.
  cron_jobs = {
    "seed-syllabus-embeddings"        = { kind = "loop", cron = "0 0 1 1 *",  parallel = 1, replica_timeout = 600,  cpu = 0.5,  mem = "1Gi"   }
    "exam-reminder"                   = { kind = "loop", cron = "*/5 * * * *", parallel = 1, replica_timeout = 120, cpu = 0.25, mem = "0.5Gi" }
    "push-prune"                      = { kind = "loop", cron = "0 3 * * *",   parallel = 1, replica_timeout = 1800, cpu = 0.5,  mem = "1Gi"  }
    "ensure-synthetic-alerts-ttl"     = { kind = "loop", cron = "0 0 1 1 *",  parallel = 1, replica_timeout = 120, cpu = 0.25, mem = "0.5Gi" }
    "synthetic-alert-cleanup"         = { kind = "loop", cron = "0 * * * *",  parallel = 1, replica_timeout = 300, cpu = 0.5,  mem = "1Gi"   }
    "cf-access-silent-lockout"        = { kind = "loop", cron = "*/5 * * * *", parallel = 1, replica_timeout = 120, cpu = 0.25, mem = "0.5Gi" }
    "endpoint-health-alert"           = { kind = "loop", cron = "*/5 * * * *", parallel = 1, replica_timeout = 120, cpu = 0.25, mem = "0.5Gi" }
    "hydrate-alert"                   = { kind = "loop", cron = "*/10 * * * *", parallel = 1, replica_timeout = 240, cpu = 0.25, mem = "0.5Gi" }
    "review-prompt-alert"             = { kind = "loop", cron = "*/15 * * * *", parallel = 1, replica_timeout = 240, cpu = 0.25, mem = "0.5Gi" }
    "review-prompt-weekly-digest"     = { kind = "loop", cron = "30 3 * * 1", parallel = 1, replica_timeout = 600, cpu = 0.5,  mem = "1Gi"   }
    "sitemap-indexnow-diff"           = { kind = "loop", cron = "0 * * * *",   parallel = 1, replica_timeout = 600, cpu = 0.5,  mem = "1Gi"   }
    "bing-submit-daily"               = { kind = "loop", cron = "0 4 * * *",   parallel = 1, replica_timeout = 1200, cpu = 1.0, mem = "2Gi"   }
    "bing-keyword-refresh"            = { kind = "loop", cron = "0 5 1 * *",   parallel = 1, replica_timeout = 1800, cpu = 1.0, mem = "2Gi"   }
    "seo-health-alert"                = { kind = "loop", cron = "*/15 * * * *", parallel = 1, replica_timeout = 240, cpu = 0.5,  mem = "1Gi"  }
    "seo-weekly-digest"               = { kind = "loop", cron = "30 3 * * 1", parallel = 1, replica_timeout = 600, cpu = 0.5,  mem = "1Gi"   }
    "entity-seo"                      = { kind = "loop", cron = "0 1 * * 0",   parallel = 1, replica_timeout = 1800, cpu = 1.0, mem = "2Gi"   }
    "topic-discovery"                 = { kind = "loop", cron = "0 2 * * *",   parallel = 1, replica_timeout = 1800, cpu = 1.0, mem = "2Gi"   }
    "internal-linker"                 = { kind = "loop", cron = "0 2 * * *",   parallel = 1, replica_timeout = 1800, cpu = 1.0, mem = "2Gi"   }
    "grounded-recall-nightly"         = { kind = "loop", cron = "0 2 * * *",   parallel = 1, replica_timeout = 1800, cpu = 1.0, mem = "2Gi"   }
    "grounded-recall-as"              = { kind = "loop", cron = "30 2 * * *",  parallel = 1, replica_timeout = 1800, cpu = 1.0, mem = "2Gi"   }
    "grounded-recall-hi"              = { kind = "loop", cron = "0 3 * * *",   parallel = 1, replica_timeout = 1800, cpu = 1.0, mem = "2Gi"   }
    "grounded-recall-bn"              = { kind = "loop", cron = "30 3 * * *",  parallel = 1, replica_timeout = 1800, cpu = 1.0, mem = "2Gi"   }
    "seo-auto-publish"                = { kind = "loop", cron = "*/15 * * * *", parallel = 1, replica_timeout = 600, cpu = 0.5, mem = "1Gi"   }
    "seo-auto-publish-staleness"      = { kind = "loop", cron = "0 * * * *",   parallel = 1, replica_timeout = 600, cpu = 0.5,  mem = "1Gi"   }
    "seo-staleness-heartbeat"         = { kind = "loop", cron = "0 */6 * * *", parallel = 1, replica_timeout = 600, cpu = 0.5,  mem = "1Gi"   }
    "ci-alert"                        = { kind = "loop", cron = "*/10 * * * *", parallel = 1, replica_timeout = 240, cpu = 0.25, mem = "0.5Gi" }
    "trustpilot-feed-alert"           = { kind = "loop", cron = "0 * * * *",   parallel = 1, replica_timeout = 240, cpu = 0.25, mem = "0.5Gi" }
    "trustpilot-refresh-cron-alert"   = { kind = "loop", cron = "0 * * * *",   parallel = 1, replica_timeout = 240, cpu = 0.25, mem = "0.5Gi" }
    "cf-waf-drift-cron-alert"         = { kind = "loop", cron = "0 * * * *",   parallel = 1, replica_timeout = 240, cpu = 0.25, mem = "0.5Gi" }
    "cf-pull-silence-alert"           = { kind = "loop", cron = "*/10 * * * *", parallel = 1, replica_timeout = 240, cpu = 0.25, mem = "0.5Gi" }
    "edge-proxy-deploy-cron-alert"    = { kind = "loop", cron = "0 * * * *",   parallel = 1, replica_timeout = 240, cpu = 0.25, mem = "0.5Gi" }
    "slack-webhook-missing-alert"     = { kind = "loop", cron = "0 */6 * * *", parallel = 1, replica_timeout = 240, cpu = 0.25, mem = "0.5Gi" }
    "cf-bot-report"                   = { kind = "loop", cron = "*/5 * * * *", parallel = 1, replica_timeout = 600, cpu = 0.5,  mem = "1Gi"   }
    "bot-traffic-report"              = { kind = "loop", cron = "*/15 * * * *", parallel = 1, replica_timeout = 600, cpu = 0.5,  mem = "1Gi"   }
    "pages-deploy-nightly"            = { kind = "loop", cron = "0 2 * * *",   parallel = 1, replica_timeout = 1800, cpu = 1.0, mem = "2Gi"   }
    "collection-size-snapshot"        = { kind = "loop", cron = "0 * * * *",   parallel = 1, replica_timeout = 600, cpu = 0.5,  mem = "1Gi"   }
    "cache-warm"                      = { kind = "loop", cron = "0 */6 * * *", parallel = 1, replica_timeout = 900, cpu = 0.5,  mem = "1Gi"   }
    "vertex-startup-probe"            = { kind = "loop", cron = "0 0 1 1 *",  parallel = 1, replica_timeout = 120, cpu = 0.25, mem = "0.5Gi" }
    "vertex-periodic-probe"           = { kind = "loop", cron = "*/5 * * * *", parallel = 1, replica_timeout = 120, cpu = 0.25, mem = "0.5Gi" }
    "unified-logs-cf-pull"            = { kind = "loop", cron = "*/2 * * * *", parallel = 1, replica_timeout = 240, cpu = 0.5,  mem = "1Gi"   }
    # Task #434 — alert on-call when the embed backfill stalls or
    # starts failing. Cheap one-doc poll; cadence matches the in-process
    # ALERT_LOOP_INTERVAL_S default (300s).
    "embed-backfill-alert"            = { kind = "loop", cron = "*/5 * * * *", parallel = 1, replica_timeout = 120, cpu = 0.25, mem = "0.5Gi" }
    # Task #332 — 3 additional periodic loops that were previously
    # `asyncio.create_task(...)` calls in server.py with no aca-job
    # mapping. Migrated here so `_aca_jobs_takeover() == True` does
    # not regress alerting / chat speedup metric flushing /
    # SEO-remediation processing.
    "alerting"                        = { kind = "loop", cron = "*/2 * * * *",  parallel = 1, replica_timeout = 240,  cpu = 0.5, mem = "1Gi"   }
    "chat-speedup-flush"              = { kind = "loop", cron = "*/1 * * * *",  parallel = 1, replica_timeout = 90,   cpu = 0.25, mem = "0.5Gi" }
    "seo-remediation"                 = { kind = "loop", cron = "*/5 * * * *",  parallel = 1, replica_timeout = 900,  cpu = 1.0, mem = "2Gi"   }
    # Task #332 reviewer rev #10 — final batch of API-tier loops
    # gated through `_aca_create_task` so `_aca_jobs_takeover()`
    # leaves the API loop-free for periodic work.
    "rate-limiter-cleanup"            = { kind = "loop", cron = "*/2 * * * *",  parallel = 1, replica_timeout = 120,  cpu = 0.25, mem = "0.5Gi" }
    "bg-health"                       = { kind = "loop", cron = "*/2 * * * *",  parallel = 1, replica_timeout = 120,  cpu = 0.25, mem = "0.5Gi" }
    "library-prewarm"                 = { kind = "loop", cron = "0 */6 * * *",  parallel = 1, replica_timeout = 600,  cpu = 0.5,  mem = "1Gi"   }
    "assamese-purity-refresh"         = { kind = "loop", cron = "*/1 * * * *",  parallel = 1, replica_timeout = 90,   cpu = 0.25, mem = "0.5Gi" }
  }

  # Runtime secret bundle injected into every cron job container via
  # Key Vault references resolved by the user-assigned managed
  # identity. `env_name` is what the legacy backend module reads from
  # ``os.environ``; `secret_name` is the Container Apps Job-local
  # alias; `kv_secret_name` is the Key Vault secret the alias points
  # at. Keep `kv_secret_name` aligned with the Google Secret Manager
  # ID used by the API tier today so the mirror script in
  # ``services/cron-jobs/scripts/mirror_secrets.sh`` round-trips
  # without per-secret mapping.
  cron_runtime_secrets = [
    { env_name = "MONGO_URL",                 secret_name = "mongo-url",                 kv_secret_name = "syrabit-mongo-url" },
    { env_name = "DB_NAME",                   secret_name = "mongo-db-name",             kv_secret_name = "syrabit-mongo-db-name" },
    { env_name = "OPENAI_API_KEY",            secret_name = "openai-api-key",            kv_secret_name = "syrabit-openai-api-key" },
    { env_name = "GEMINI_API_KEY",            secret_name = "gemini-api-key",            kv_secret_name = "syrabit-gemini-api-key" },
    # Task #556 — RESEND_API_KEY retired; cron jobs read AWS_ACCESS_KEY_ID +
    # AWS_SECRET_ACCESS_KEY + SES_REGION (declared elsewhere in this list)
    # and email through Amazon SES via boto3.
    { env_name = "SLACK_WEBHOOK_URL",         secret_name = "slack-webhook-url",         kv_secret_name = "syrabit-slack-webhook-url" },
    { env_name = "CF_API_TOKEN",              secret_name = "cf-api-token",              kv_secret_name = "syrabit-cf-api-token" },
    { env_name = "CF_ACCOUNT_ID",             secret_name = "cf-account-id",             kv_secret_name = "syrabit-cf-account-id" },
    { env_name = "CF_ZONE_ID",                secret_name = "cf-zone-id",                kv_secret_name = "syrabit-cf-zone-id" },
    { env_name = "BING_API_KEY",              secret_name = "bing-api-key",              kv_secret_name = "syrabit-bing-api-key" },
    { env_name = "INDEXNOW_KEY",              secret_name = "indexnow-key",              kv_secret_name = "syrabit-indexnow-key" },
    { env_name = "TRUSTPILOT_API_KEY",        secret_name = "trustpilot-api-key",        kv_secret_name = "syrabit-trustpilot-api-key" },
    { env_name = "VERTEX_PROJECT",            secret_name = "vertex-project",            kv_secret_name = "syrabit-vertex-project" },
    { env_name = "VERTEX_LOCATION",           secret_name = "vertex-location",           kv_secret_name = "syrabit-vertex-location" },
    { env_name = "GOOGLE_APPLICATION_CREDENTIALS_JSON", secret_name = "gcp-sa-json",     kv_secret_name = "syrabit-gcp-sa-json" },
    { env_name = "UPSTASH_REDIS_REST_URL",    secret_name = "upstash-redis-url",         kv_secret_name = "syrabit-upstash-redis-url" },
    { env_name = "UPSTASH_REDIS_REST_TOKEN",  secret_name = "upstash-redis-token",       kv_secret_name = "syrabit-upstash-redis-token" },
    # AWS access intentionally NOT injected as long-lived static
    # keys. Cron jobs that need to talk to SQS use Azure Workload
    # Identity Federation → AWS STS AssumeRoleWithWebIdentity (the
    # `infra/aws/iam-azure-federation.tf` module mirrors the
    # GitHub-OIDC setup and trusts the cron-jobs user-assigned
    # managed identity's federated subject). Each job's runtime
    # boto3 client picks up the federated session from
    # `AWS_WEB_IDENTITY_TOKEN_FILE` + `AWS_ROLE_ARN` env vars
    # populated by the `aca-aws-federation` init container in the
    # job pod template (see `init-containers.tf`). Long-lived
    # `aws-access-key-id` / `aws-secret-access-key` Key Vault
    # secrets were intentionally REMOVED in Task #332 reviewer
    # rev #10 to keep the cross-cloud blast radius minimal.
    { env_name = "AWS_REGION",                secret_name = "aws-region",                kv_secret_name = "syrabit-aws-region" },
    # Task #332 reviewer rev #13 — server-backed job targets (e.g.
    # `server:_vertex_periodic_probe_loop`, `server:_seed_syllabus_embeddings`)
    # import `server.py`, which calls `_validate_env()` at module
    # load and HARD-FAILS when JWT_SECRET / ADMIN_JWT_SECRET are
    # missing. Inject both so the cron pod boots cleanly.
    { env_name = "JWT_SECRET",                secret_name = "jwt-secret",                kv_secret_name = "syrabit-jwt-secret" },
    { env_name = "ADMIN_JWT_SECRET",          secret_name = "admin-jwt-secret",          kv_secret_name = "syrabit-admin-jwt-secret" },
  ]
}

# ─── ACR pull — managed-identity grant ───────────────────────────────────────

resource "azurerm_role_assignment" "cron_jobs_acr_pull" {
  scope                = azurerm_container_registry.cron_obs.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.cron_jobs_runtime.principal_id
}

# ─── Jobs ────────────────────────────────────────────────────────────────────

resource "azurerm_container_app_job" "cron" {
  for_each = local.cron_jobs

  name                         = "aca-job-${each.key}"
  location                     = azurerm_resource_group.cron_obs.location
  resource_group_name          = azurerm_resource_group.cron_obs.name
  container_app_environment_id = azurerm_container_app_environment.cron.id

  workload_profile_name = "Consumption"

  replica_timeout_in_seconds = each.value.replica_timeout
  replica_retry_limit        = 2

  schedule_trigger_config {
    cron_expression          = each.value.cron
    parallelism              = each.value.parallel
    replica_completion_count = each.value.parallel
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.cron_jobs_runtime.id]
  }

  registry {
    server   = azurerm_container_registry.cron_obs.login_server
    identity = azurerm_user_assigned_identity.cron_jobs_runtime.id
  }

  template {
    container {
      name   = "runner"
      image  = local.cron_image
      cpu    = each.value.cpu
      memory = each.value.mem

      env {
        name  = "JOB_NAME"
        value = each.key
      }
      env {
        name  = "JOB_KIND"
        value = each.value.kind
      }
      env {
        name  = "LZ_PROJECT"
        value = local.lz_project
      }
      env {
        name  = "LZ_ENV"
        value = local.lz_env
      }
      env {
        name  = "OTEL_SERVICE_NAME"
        value = "cron-${each.key}"
      }
      env {
        name        = "APP_INSIGHTS_CONNECTION_STRING"
        secret_name = "app-insights-conn-string"
      }
      # Phase 5b — Task #338. Azure AI wrappers in
      # `services/backend/azure_ai/_resolver.py` resolve their per-
      # service endpoint URLs from Key Vault at first call. Pass the
      # vault URI as plain env so the resolver can construct a
      # SecretClient with the cron-tier managed identity. The
      # endpoint URLs themselves are not pre-injected here because
      # they are looked up lazily — most jobs never touch an Azure AI
      # service so injecting all ten as secrets per-job would waste
      # cold-start time.
      env {
        name  = "AZURE_CRON_OBS_KV_URI"
        value = azurerm_key_vault.cron_obs.vault_uri
      }
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.cron_jobs_runtime.client_id
      }
      # ─── Runtime secrets pulled from Key Vault via managed identity ──
      # The legacy backend modules (server.py, seo_engine.py, routes/*)
      # read these env vars on import. The list mirrors the API tier's
      # runtime config so a job that imports any backend module gets
      # the same secrets the API gets — no per-job secret allow-list.
      # Each Key Vault secret is created out-of-band by the platform
      # team and mirrored from Google Secret Manager during the
      # transition window (see docs/infra/cron-on-azure.md, "Secret
      # bootstrap" section). Adding a new runtime secret = add an
      # entry to `local.cron_runtime_secrets` below.
      dynamic "env" {
        for_each = local.cron_runtime_secrets
        content {
          name        = env.value.env_name
          secret_name = env.value.secret_name
        }
      }
    }
  }

  secret {
    name                = "app-insights-conn-string"
    key_vault_secret_id = "${azurerm_key_vault.cron_obs.vault_uri}secrets/app-insights-connection-string"
    identity            = azurerm_user_assigned_identity.cron_jobs_runtime.id
  }
  # Runtime secret bundle. Each entry resolves to a Key Vault secret
  # the cron jobs' user-assigned managed identity has Get permission
  # on (granted by the access policy in `key-vault.tf`).
  dynamic "secret" {
    for_each = local.cron_runtime_secrets
    content {
      name                = secret.value.secret_name
      key_vault_secret_id = "${azurerm_key_vault.cron_obs.vault_uri}secrets/${secret.value.kv_secret_name}"
      identity            = azurerm_user_assigned_identity.cron_jobs_runtime.id
    }
  }

  tags = merge(local.lz_common_tags, {
    job-kind = each.value.kind
    job-key  = each.key
  })

  depends_on = [azurerm_role_assignment.cron_jobs_acr_pull]
}

# ─── Per-job failure alerts ──────────────────────────────────────────────────

resource "azurerm_monitor_metric_alert" "cron_job_failed" {
  for_each = local.cron_jobs

  name                = "aca-job-${each.key}-failed"
  resource_group_name = azurerm_resource_group.cron_obs.name
  scopes              = [azurerm_container_app_environment.cron.id]
  description         = "Container Apps Job aca-job-${each.key} reported a failed replica."
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  enabled             = true

  criteria {
    metric_namespace = "Microsoft.App/managedEnvironments"
    metric_name      = "JobExecutionsFailedCount"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 0

    dimension {
      name     = "JobName"
      operator = "Include"
      values   = ["aca-job-${each.key}"]
    }
  }

  action {
    action_group_id = azurerm_monitor_action_group.ops_alerts.id
  }

  tags = merge(local.lz_common_tags, {
    job-kind = each.value.kind
    job-key  = each.key
  })
}

output "cron_job_names" {
  description = "Map of job key → ACA Job resource name. Polled by the admin Cron Health card."
  value       = { for k, j in azurerm_container_app_job.cron : k => j.name }
}

output "cron_environment_id" {
  description = "Container Apps Environment hosting all cron jobs."
  value       = azurerm_container_app_environment.cron.id
}
