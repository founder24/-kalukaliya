# Production environment-variable contract

**GENERATED — do not hand-edit.** Regenerate with
`python scripts/ci/check_env_vars_doc.py --write`. CI runs the same
script in check mode and fails if this file drifts from the code.

## Why this file exists

Task #87's code-review found that the `replit.md` env list is only
the narrow CI-enforced subset and several genuinely-required
secrets (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_ANON_KEY`,
`ORIGIN_SHARED_SECRET`, `D1_SYNC_SECRET`) live only inside
`infra/azure/aca-syrabit-backend.bicep` + worker bindings. New
on-call / new-environment bring-up had no single source of truth.
This doc fills that gap by extracting env references from every
deploy unit and cross-referencing them against the bicep / TF /
wrangler wiring that actually exists in the repo.

## Conventions

| symbol | meaning |
|---|---|
| 🔒 secret | sensitive — must come from a secret store (Key Vault / Secrets Manager / wrangler secret) |
| ⚙️ config | non-sensitive (URLs, region names, feature flags) — safe to commit |
| ✅ wired | declared in the deploy infra file (bicep env, TF env block, wrangler.toml `[vars]`) |
| ❌ not wired | code references the var but no deploy infra binds it — operator must set it manually OR the code path is dead |

## Sources scanned

- ACA backend: `artifacts/syrabit-backend/**/*.py` (excluding `tests/`, `scripts/`, `__pycache__`)
- Background jobs: `artifacts/syrabit-backend/aca_jobs/`, `artifacts/syrabit/services/backend/lambda_batch/`
- Workers: `workers/edge-proxy/src/`, `artifacts/syrabit/workers/embed-worker/src/`, `workers/email-worker/src/`
- Deploy infra: `infra/azure/aca-syrabit-backend.bicep`, `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf`, the three wrangler.toml files

## Limitations

- The script does AST-free regex extraction; vars built from
  `f"PREFIX_{var}"` strings are NOT captured.
- One-off operator scripts under `artifacts/syrabit-backend/scripts/`
  are intentionally excluded — they don't run in production.
- `secret?` classification is heuristic (name-suffix + bicep
  `secretRef` wiring); see `NON_SECRET_OVERRIDES` in the script for
  the explicit non-secret allowlist.
- A `❌ not wired` row in the ACA-backend table can mean either (a)
  the operator is expected to inject it via the ACA env block by
  hand, (b) the code path is dead, or (c) the value is sourced
  from another infra file (`infra/aws/account-billing.tf`,
  upstream Key Vault) — review case-by-case.

## ACA backend (`syrabit-backend`)

FastAPI runtime in `artifacts/syrabit-backend/`, deployed to Azure Container Apps via `infra/azure/aca-syrabit-backend.bicep`. Env section in the bicep file is the canonical wiring.

**Deploy file(s):** `infra/azure/aca-syrabit-backend.bicep`

| env var | type | wired in deploy infra? | notes |
|---|---|---|---|
| `ADMIN_JWT_SECRET` | 🔒 secret | ✅ secretRef `admin-jwt-secret` | wired but no code reference found (deploy-time only) |
| `AWS_ACCESS_KEY_ID` | 🔒 secret | ✅ secretRef `aws-access-key-id` | code-referenced + wired |
| `AWS_SECRET_ACCESS_KEY` | 🔒 secret | ✅ secretRef `aws-secret-access-key` | code-referenced + wired |
| `D1_SYNC_SECRET` | 🔒 secret | ✅ secretRef `d1-sync-secret` | code-referenced + wired |
| `EMAIL_FROM` | ⚙️ config | ✅ literal value | code-referenced + wired |
| `EMBED_PROVIDER_PRIMARY` | ⚙️ config | ✅ literal value | code-referenced + wired |
| `ENV` | ⚙️ config | ✅ literal value | code-referenced + wired |
| `JWT_SECRET` | 🔒 secret | ✅ secretRef `jwt-secret` | wired but no code reference found (deploy-time only) |
| `MONGO_URL` | 🔒 secret | ✅ secretRef `mongo-uri` | code-referenced + wired |
| `ORIGIN_SHARED_SECRET` | 🔒 secret | ✅ secretRef `origin-shared-secret` | code-referenced + wired |
| `OTEL_EXPORTER_GCP_PROJECT_ID` | ⚙️ config | ✅ literal value | code-referenced + wired |
| `OTEL_SERVICE_NAME` | ⚙️ config | ✅ literal value | code-referenced + wired |
| `OTEL_TRACES_EXPORTER` | ⚙️ config | ✅ literal value | wired but no code reference found (deploy-time only) |
| `RAZORPAY_KEY_SECRET` | 🔒 secret | ✅ secretRef `razorpay-key-secret` | code-referenced + wired |
| `SARVAM_API_KEY` | 🔒 secret | ✅ secretRef `sarvam-api-key` | code-referenced + wired |
| `SENTRY_DSN` | 🔒 secret | ✅ secretRef `sentry-dsn` | code-referenced + wired |
| `SENTRY_ENVIRONMENT` | ⚙️ config | ✅ literal value | code-referenced + wired |
| `SES_REGION` | ⚙️ config | ✅ literal value | code-referenced + wired |
| `WEB_PUSH_CONTACT` | ⚙️ config | ✅ literal value | code-referenced + wired |
| `WEB_PUSH_VAPID_PRIVATE_KEY` | 🔒 secret | ✅ secretRef `web-push-vapid-private-key` | code-referenced + wired |
| `WORKERS_EMBED_SECRET` | 🔒 secret | ✅ secretRef `workers-embed-secret` | code-referenced + wired |
| `WORKERS_EMBED_URL` | ⚙️ config | ✅ literal value | code-referenced + wired |

## ACA / Lambda batch jobs

Background jobs that run inside the ACA backend container (`aca_jobs/*.py`) AND, increasingly, on AWS Lambda (`artifacts/syrabit/services/backend/lambda_batch/*.py`). Lambda wiring lives in `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf`.

**Deploy file(s):** `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf`

| env var | type | wired in deploy infra? | notes |
|---|---|---|---|
| `ACA_JOB_BATCHES_DISABLED` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `ADMIN_JWT_SECRET` | 🔒 secret | ❌ in-process / ACA-only | code-only |
| `ADMIN_JWT_SECRET_ARN` | 🔒 secret | ✅ Lambda env (TF) | Lambda + ACA |
| `AS_BACKFILL_BATCH_SIZE` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `AS_BACKFILL_INTER_DOC_SLEEP_S` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `AS_BACKFILL_MAX_CHUNK_CHARS` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `AS_BACKFILL_METRIC_JOB` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `AS_BACKFILL_METRIC_NAMESPACE` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `AS_BACKFILL_PER_CALL_LIMIT` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `AS_BACKFILL_TRANSLATE_TIMEOUT_S` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `AS_COVERAGE_INLINE_BACKFILL_LIMIT` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `AS_COVERAGE_METRIC_NAMESPACE` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `BACKEND_URL` | ⚙️ config | ✅ Lambda env (TF) | Lambda + ACA |
| `BATCH_JOB_DRIVER` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `CF_AI_GATEWAY_ACCOUNT_ID_SECRET` | 🔒 secret | ✅ Lambda env (TF) | TF-wired only |
| `CF_API_TOKEN` | 🔒 secret | ❌ in-process / ACA-only | code-only |
| `CF_ZONE_ID` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `CLOUDFLARE_API_TOKEN_SECRET_ARN` | 🔒 secret | ✅ Lambda env (TF) | TF-wired only |
| `COMPREHEND_RESCORE_AFTER_DAYS` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `COMPREHEND_SAMPLE_INTERVAL_S` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `COMPREHEND_SAMPLE_SIZE` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `EMBED_BACKFILL_ALERT_FAILED_THRESHOLD` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `EMBED_BACKFILL_ALERT_INTERVAL_S` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `EMBED_BACKFILL_ALERT_STALL_MINUTES` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `EMBED_BACKFILL_AUTOSTART` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `EMBED_BACKFILL_BATCH_SIZE` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `EMBED_BACKFILL_INTERVAL_S` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `EMBED_BACKFILL_MAX_RPM` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `EMBED_BACKFILL_PER_CALL_LIMIT` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `EMBED_BACKFILL_THROUGHPUT_WINDOW_S` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `GCP_BILLING_DATASET` | ⚙️ config | ✅ Lambda env (TF) | Lambda + ACA |
| `GCP_BILLING_PROJECT` | ⚙️ config | ✅ Lambda env (TF) | Lambda + ACA |
| `GCP_BILLING_TABLE_PREFIX` | ⚙️ config | ✅ Lambda env (TF) | Lambda + ACA |
| `GCP_CREDITS_START_DATE` | ⚙️ config | ✅ Lambda env (TF) | Lambda + ACA |
| `GCP_TOTAL_CREDITS_USD` | ⚙️ config | ✅ Lambda env (TF) | Lambda + ACA |
| `GEMINI_API_KEY_SECRET_ARN` | 🔒 secret | ✅ Lambda env (TF) | TF-wired only |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | 🔒 secret | ❌ in-process / ACA-only | code-only |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON_SECRET_ARN` | 🔒 secret | ✅ Lambda env (TF) | TF-wired only |
| `GOOGLE_RR_API_KEY` | 🔒 secret | ❌ in-process / ACA-only | code-only |
| `HANDLER_NAME` | ⚙️ config | ✅ Lambda env (TF) | TF-wired only |
| `LAMBDA_RELEASE` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `LZ_ENV` | ⚙️ config | ✅ Lambda env (TF) | TF-wired only |
| `LZ_PROJECT` | ⚙️ config | ✅ Lambda env (TF) | TF-wired only |
| `MATERIALIZE_FAQ_INTERVAL_S` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `MAX_DOCS_PER_RUN` | ⚙️ config | ✅ Lambda env (TF) | Lambda + ACA |
| `MONGO_DB_NAME` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `MONGO_URL` | 🔒 secret | ❌ in-process / ACA-only | code-only |
| `MONGO_URL_SECRET_ARN` | 🔒 secret | ✅ Lambda env (TF) | Lambda + ACA |
| `OTEL_SERVICE_NAME` | ⚙️ config | ✅ Lambda env (TF) | TF-wired only |
| `PINECONE_API_KEY_SECRET` | 🔒 secret | ✅ Lambda env (TF) | TF-wired only |
| `PREWARM_AUTH_TOKEN` | 🔒 secret | ❌ in-process / ACA-only | code-only |
| `PREWARM_AUTH_TOKEN_SECRET_ARN` | 🔒 secret | ✅ Lambda env (TF) | TF-wired only |
| `PREWARM_CONCURRENCY` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `PREWARM_EXAM_LOOKAHEAD_DAYS` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `PREWARM_HTTP_TIMEOUT_S` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `PREWARM_INTERVAL_S` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `PREWARM_TOP_N` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `PUBLIC_BASE_URL` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `RUNWAY_FRESHNESS_THRESHOLD_S` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `RUNWAY_REDIS_KEY` | 🔒 secret | ❌ in-process / ACA-only | code-only |
| `RUNWAY_REDIS_TTL_S` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `SENTRY_DSN` | 🔒 secret | ❌ in-process / ACA-only | code-only |
| `SENTRY_DSN_SECRET_ARN` | 🔒 secret | ✅ Lambda env (TF) | TF-wired only |
| `SEO_BASELINE_BOARDS` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `SEO_BASELINE_CHAPTERS_PER_BOARD` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `SEO_BASELINE_PAGE_TYPE` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `SUPABASE_ANON_KEY` | 🔒 secret | ❌ in-process / ACA-only | code-only |
| `SUPABASE_CANARY_EMAIL` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `SUPABASE_CANARY_PASSWORD` | 🔒 secret | ❌ in-process / ACA-only | code-only |
| `SUPABASE_URL` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `UPSTASH_REDIS_REST_TOKEN` | 🔒 secret | ❌ in-process / ACA-only | code-only |
| `UPSTASH_REDIS_REST_TOKEN_SECRET_ARN` | 🔒 secret | ✅ Lambda env (TF) | TF-wired only |
| `UPSTASH_REDIS_REST_URL` | ⚙️ config | ❌ in-process / ACA-only | code-only |
| `UPSTASH_REDIS_REST_URL_SECRET_ARN` | 🔒 secret | ✅ Lambda env (TF) | TF-wired only |
| `WORKERS_EMBED_SECRET_ARN` | 🔒 secret | ✅ Lambda env (TF) | TF-wired only |
| `WORKERS_EMBED_URL` | ⚙️ config | ✅ Lambda env (TF) | TF-wired only |

## Cloudflare Worker — `syrabit-edge` (edge proxy)

Routes `api.syrabit.ai/*` and friends. Bindings + plaintext vars in `workers/edge-proxy/wrangler.toml`; secrets via `wrangler secret put` (not in this repo).

**Deploy file(s):** `workers/edge-proxy/wrangler.toml`

| env var | type | wired in deploy infra? | notes |
|---|---|---|---|
| `AI_GATEWAY_ANALYTICS_TOKEN` | 🔒 secret | ❌ wrangler secret (operator-set) |  |
| `AI_GATEWAY_CACHE_ALERT_DISABLED` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `AI_GATEWAY_CACHE_ALERT_EMBED_TAG` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `AI_GATEWAY_CACHE_ALERT_MIN_SAMPLE` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `AI_GATEWAY_CACHE_ALERT_QUERY_FAIL_THRESHOLD` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `AI_GATEWAY_CACHE_HIT_RATE_FLOOR_PCT` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `BACKEND_ORIGIN_SECRET` | 🔒 secret | ❌ wrangler secret (operator-set) |  |
| `BACKEND_URL` | ⚙️ config | ✅ wrangler [vars] |  |
| `BOT_CACHE_ALERT_DISABLED` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `BOT_CACHE_ALERT_DROP_PCT` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `BOT_CACHE_ALERT_FALLBACK_PCT` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `BOT_CACHE_ALERT_MIN_SAMPLE` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `BOT_CACHE_ALERT_WINDOW_BUCKETS` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `CF_ANALYTICS_TOKEN` | 🔒 secret | ❌ wrangler secret (operator-set) |  |
| `CF_BLOCK_PROBE_DISABLED` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `CF_BLOCK_PROBE_TARGET_URL` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `CF_BLOCK_PROBE_THRESHOLD` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `D1_SYNC_SECRET` | 🔒 secret | ❌ wrangler secret (operator-set) |  |
| `D1_WARM_ON_STARTUP` | ⚙️ config | ✅ wrangler [vars] |  |
| `EDGE_AI_FALLBACK_SECRET` | 🔒 secret | ❌ wrangler secret (operator-set) |  |
| `EDGE_LOG_DEFERRED_FLUSH_MS` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `EDGE_LOG_SAMPLE_RATE` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `KV_ALERT_SECRET` | 🔒 secret | ❌ wrangler secret (operator-set) |  |
| `KV_QUOTA` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `KV_WARNING_PCT` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `LOG_INGEST_TOKEN` | 🔒 secret | ❌ wrangler secret (operator-set) |  |
| `MTLS_REQUIRED` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `PAGES_ORIGIN` | ⚙️ config | ✅ wrangler [vars] |  |
| `R2_LIFECYCLE_RULES_APPLIED_AT` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `R2_STORAGE_ALERT_BUCKETS` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `R2_STORAGE_ALERT_DISABLED` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `R2_STORAGE_ALERT_LOGPUSH_CAP_GB` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `R2_STORAGE_ALERT_QUERY_FAIL_THRESHOLD` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `R2_STORAGE_ANALYTICS_TOKEN` | 🔒 secret | ❌ wrangler secret (operator-set) |  |
| `RATE_LIMIT` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `SYNTHETIC_PROBE_ADMIN_JWT` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `SYNTHETIC_PROBE_CF_ACCESS_CLIENT_ID` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `SYNTHETIC_PROBE_CF_ACCESS_CLIENT_SECRET` | 🔒 secret | ❌ wrangler secret (operator-set) |  |
| `SYNTHETIC_PROBE_DISABLED` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `SYNTHETIC_PROBE_TARGET_URL` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `SYNTHETIC_PROBE_WATCHDOG_THRESHOLD_MIN` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `WORKERS_AI_GATEWAY_ID` | ⚙️ config | ❌ wrangler secret (operator-set) |  |

## Cloudflare Worker — `syrabit-embed-worker`

Custom Workers-AI embedding endpoint at `embed.syrabit.ai`. Bindings in `artifacts/syrabit/workers/embed-worker/wrangler.toml`.

**Deploy file(s):** `artifacts/syrabit/workers/embed-worker/wrangler.toml`

| env var | type | wired in deploy infra? | notes |
|---|---|---|---|
| `EMBED_DIMS` | ⚙️ config | ✅ wrangler [vars] |  |
| `EMBED_MAX_BATCH` | ⚙️ config | ✅ wrangler [vars] |  |
| `EMBED_MAX_CHARS` | ⚙️ config | ✅ wrangler [vars] |  |
| `EMBED_MODELS` | ⚙️ config | ✅ wrangler [vars] |  |
| `EMBED_RATE_RPM` | ⚙️ config | ✅ wrangler [vars] |  |
| `EMBED_SHARED_SECRET` | 🔒 secret | ❌ wrangler secret (operator-set) |  |
| `EMBED_WORKER_VERSION` | ⚙️ config | ✅ wrangler [vars] |  |
| `NODE_ENV` | ⚙️ config | ✅ wrangler [vars] |  |

## Cloudflare Worker — `syrabit-email` (410 stub)

Task #556 retired transport — only `/email/health` is live; every other route returns HTTP 410. Kept on the deploy manifest so stale callers fail loud.

**Deploy file(s):** `workers/email-worker/wrangler.toml`

| env var | type | wired in deploy infra? | notes |
|---|---|---|---|
