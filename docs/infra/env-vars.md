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
| `ACA_JOB_BATCHES_DISABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `ADMIN_EMAILS` | ⚙️ config | ❌ code-only | code-referenced only |
| `ADMIN_JWT_SECRET` | 🔒 secret | ✅ secretRef `admin-jwt-secret` | wired but no code reference found (deploy-time only) |
| `ADMIN_LLM_MAX_CONCURRENT` | ⚙️ config | ❌ code-only | code-referenced only |
| `ADMIN_NAMES` | ⚙️ config | ❌ code-only | code-referenced only |
| `ADMIN_PASSWORDS` | 🔒 secret | ❌ code-only | code-referenced only |
| `ADSENSE_ACCOUNT_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `ADSENSE_CLIENT_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `ADSENSE_CLIENT_SECRET` | 🔒 secret | ❌ code-only | code-referenced only |
| `ADSENSE_REFRESH_TOKEN` | 🔒 secret | ❌ code-only | code-referenced only |
| `AIG_GUARDRAIL_BLOCK_RATIO_THRESHOLD` | ⚙️ config | ❌ code-only | code-referenced only |
| `AIG_GUARDRAIL_LOOP_SLEEP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `AIG_GUARDRAIL_MIN_SAMPLES` | ⚙️ config | ❌ code-only | code-referenced only |
| `AIG_GUARDRAIL_REALERT_INTERVAL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `AIG_GUARDRAIL_WARMUP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `AI_RESPONSE_CACHE_KV_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `AI_RESPONSE_CACHE_KV_ID_NE_INDIA` | ⚙️ config | ❌ code-only | code-referenced only |
| `ALERT_EMAIL` | ⚙️ config | ❌ code-only | code-referenced only |
| `ALERT_WEBHOOK_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `APPRUNNER_SERVICE_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `AS_BACKFILL_BATCH_SIZE` | ⚙️ config | ❌ code-only | code-referenced only |
| `AS_BACKFILL_INTER_DOC_SLEEP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `AS_BACKFILL_MAX_CHUNK_CHARS` | ⚙️ config | ❌ code-only | code-referenced only |
| `AS_BACKFILL_PER_CALL_LIMIT` | ⚙️ config | ❌ code-only | code-referenced only |
| `AS_BACKFILL_TRANSLATE_TIMEOUT_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `AS_COVERAGE_INLINE_BACKFILL_LIMIT` | ⚙️ config | ❌ code-only | code-referenced only |
| `ATLAS_VS_COLLECTION` | ⚙️ config | ❌ code-only | code-referenced only |
| `ATLAS_VS_DIMENSIONS` | ⚙️ config | ❌ code-only | code-referenced only |
| `ATLAS_VS_ENABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `ATLAS_VS_FILTER_FIELDS` | ⚙️ config | ❌ code-only | code-referenced only |
| `ATLAS_VS_INDEX_NAME` | ⚙️ config | ❌ code-only | code-referenced only |
| `ATLAS_VS_METRIC` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_ACCESS_KEY_ID` | 🔒 secret | ✅ secretRef `aws-access-key-id` | code-referenced + wired |
| `AWS_ACCOUNT_ALIAS` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_ACTIVATE_CUMULATIVE_SPEND_USD` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_ACTIVATE_EXPIRY` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_ACTIVATE_GRANT_USD` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_ACTIVATE_REMAINING_USD` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_ACTIVATE_SPEND_MTD` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_DEFAULT_REGION` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_FRAUD_DETECTOR_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_FRAUD_DETECTOR_PAYMENT_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_FRAUD_DETECTOR_PAYMENT_VERSION` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_FRAUD_DETECTOR_VERSION` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_GLACIER_REGION` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_NATIVE_PRIMARY_REGION` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_NATIVE_SECONDARY_REGION` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_PERSONALIZE_CAMPAIGN_ARN` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_REGION` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_SECRET_ACCESS_KEY` | 🔒 secret | ✅ secretRef `aws-secret-access-key` | code-referenced + wired |
| `AWS_SES_REGION` | ⚙️ config | ❌ code-only | code-referenced only |
| `AWS_TRANSCRIBE_TMP_BUCKET` | ⚙️ config | ❌ code-only | code-referenced only |
| `AXIOM_API_TOKEN` | 🔒 secret | ❌ code-only | code-referenced only |
| `AXIOM_INGEST_GB_MTD` | ⚙️ config | ❌ code-only | code-referenced only |
| `AXIOM_INGEST_LIMIT_GB` | ⚙️ config | ❌ code-only | code-referenced only |
| `AXIOM_ORG_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `AXIOM_RETENTION_DAYS` | ⚙️ config | ❌ code-only | code-referenced only |
| `AZURE_ACTIVATE_CUMULATIVE_SPEND_USD` | ⚙️ config | ❌ code-only | code-referenced only |
| `AZURE_ACTIVATE_EXPIRY` | ⚙️ config | ❌ code-only | code-referenced only |
| `AZURE_ACTIVATE_GRANT_USD` | ⚙️ config | ❌ code-only | code-referenced only |
| `AZURE_ACTIVATE_REMAINING_USD` | ⚙️ config | ❌ code-only | code-referenced only |
| `AZURE_ACTIVATE_SPEND_MTD` | ⚙️ config | ❌ code-only | code-referenced only |
| `AZURE_CLIENT_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `AZURE_CLIENT_SECRET` | 🔒 secret | ❌ code-only | code-referenced only |
| `AZURE_CRON_RG` | ⚙️ config | ❌ code-only | code-referenced only |
| `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` | ⚙️ config | ❌ code-only | code-referenced only |
| `AZURE_DOCUMENT_INTELLIGENCE_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `AZURE_FORM_RECOGNIZER_ENDPOINT` | ⚙️ config | ❌ code-only | code-referenced only |
| `AZURE_FORM_RECOGNIZER_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `AZURE_SUBSCRIPTION_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `AZURE_SUBSCRIPTION_NAME` | ⚙️ config | ❌ code-only | code-referenced only |
| `AZURE_TENANT_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `BACKEND_PORT` | ⚙️ config | ❌ code-only | code-referenced only |
| `BATCH_JOB_DRIVER` | ⚙️ config | ❌ code-only | code-referenced only |
| `BEDROCK_EMBED_REGION` | ⚙️ config | ❌ code-only | code-referenced only |
| `BENCH_OUT` | ⚙️ config | ❌ code-only | code-referenced only |
| `BENCH_QUERIES` | ⚙️ config | ❌ code-only | code-referenced only |
| `BENCH_QUERIES_FILE` | ⚙️ config | ❌ code-only | code-referenced only |
| `BENCH_RETRIEVERS` | ⚙️ config | ❌ code-only | code-referenced only |
| `BENCH_TOP_K` | ⚙️ config | ❌ code-only | code-referenced only |
| `BILLING_WEBHOOK_AUDIENCE` | ⚙️ config | ❌ code-only | code-referenced only |
| `BING_KEYWORD_API_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `BING_SITE_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `BING_WEBMASTER_API_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `BULK_EMAIL_WORKER_AUTH_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `BULK_EMAIL_WORKER_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `CACHE_CARDINALITY_MULTIPLIER` | ⚙️ config | ❌ code-only | code-referenced only |
| `CACHE_FINGERPRINT_DUAL_READ` | ⚙️ config | ❌ code-only | code-referenced only |
| `CACHE_HIT_RATIO_FLOOR` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_ACCESS_AUD_ADMIN` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_ACCESS_AUD_INTERNAL` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_ACCESS_BREAK_GLASS` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_ACCESS_BREAK_GLASS_TOKEN` | 🔒 secret | ❌ code-only | code-referenced only |
| `CF_ACCESS_ENFORCE` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_ACCESS_SILENT_LOCKOUT_INTERVAL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_ACCESS_TEAM_DOMAIN` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_ACCOUNT_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_AI_GATEWAY_ACCOUNT_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_AI_GATEWAY_CACHE_TTL` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_AI_GATEWAY_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_AI_GATEWAY_TOKEN` | 🔒 secret | ❌ code-only | code-referenced only |
| `CF_AI_GATEWAY_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_AI_POST_MAX_RETRIES` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_AI_POST_RETRY_BASE_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_ANALYTICS_API_TOKEN` | 🔒 secret | ❌ code-only | code-referenced only |
| `CF_API_DOMAIN` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_API_TOKEN` | 🔒 secret | ❌ code-only | code-referenced only |
| `CF_AUDIT_STALE_THRESHOLD_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_AUDIT_WORKFLOW` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_BOT_REPORT_DIR` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_EDGE_KV_CACHE_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_EDGE_PROXY_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_PAGES_API_TOKEN` | 🔒 secret | ❌ code-only | code-referenced only |
| `CF_PAGES_DEPLOY_HOOK_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_POLISH_SMOKE_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_TUNNEL_ALLOWED_IPS` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_WAF_DRIFT_CRON_BOOTSTRAP_GRACE_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_WAF_DRIFT_CRON_LOOP_SLEEP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_WAF_DRIFT_CRON_REALERT_INTERVAL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_WAF_DRIFT_CRON_SILENT_THRESHOLD_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_WAF_DRIFT_CRON_WARMUP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_WAF_DRIFT_HEARTBEAT_SECRET` | 🔒 secret | ❌ code-only | code-referenced only |
| `CF_WEB_ANALYTICS_SITE_TAG` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_WEB_ANALYTICS_TOKEN` | 🔒 secret | ❌ code-only | code-referenced only |
| `CF_ZONE_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `CF_ZONE_SETTINGS_TOKEN` | 🔒 secret | ❌ code-only | code-referenced only |
| `CHAT_CREDIT_RUNWAY_DAYS` | ⚙️ config | ❌ code-only | code-referenced only |
| `CHAT_DEFAULT_MODEL` | ⚙️ config | ❌ code-only | code-referenced only |
| `CHAT_DEV_FASTPATH` | ⚙️ config | ❌ code-only | code-referenced only |
| `CHAT_ENHANCE_ENABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `CHAT_PRIMARY_OVERRIDE` | ⚙️ config | ❌ code-only | code-referenced only |
| `CHAT_ROUTER_TOPIC_THRESHOLD` | ⚙️ config | ❌ code-only | code-referenced only |
| `CHAT_RPM_SOFT_SHED_THRESHOLD` | ⚙️ config | ❌ code-only | code-referenced only |
| `CI_ALERT_LOOP_SLEEP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `CI_ALERT_REALERT_HOURS` | ⚙️ config | ❌ code-only | code-referenced only |
| `CI_ALERT_WARMUP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `CLOUDFLARE_ACCOUNT_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `CLOUDFLARE_ANALYTICS_TOKEN` | 🔒 secret | ❌ code-only | code-referenced only |
| `CLOUDFLARE_API_TOKEN` | 🔒 secret | ❌ code-only | code-referenced only |
| `COMPREHEND_RESCORE_AFTER_DAYS` | ⚙️ config | ❌ code-only | code-referenced only |
| `COMPREHEND_SAMPLE_INTERVAL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `COMPREHEND_SAMPLE_SIZE` | ⚙️ config | ❌ code-only | code-referenced only |
| `CONTENT_BATCH_WINDOW_MS` | ⚙️ config | ❌ code-only | code-referenced only |
| `CONTENT_RPM_MAX_WAIT` | ⚙️ config | ❌ code-only | code-referenced only |
| `CONTENT_WAVE_SIZE` | ⚙️ config | ❌ code-only | code-referenced only |
| `COOKIE_DOMAIN` | ⚙️ config | ❌ code-only | code-referenced only |
| `CORS_ORIGINS` | ⚙️ config | ❌ code-only | code-referenced only |
| `CREDIT_APPLICATIONS_PATH` | ⚙️ config | ❌ code-only | code-referenced only |
| `CRON_HEARTBEAT_TTL_DAYS` | ⚙️ config | ❌ code-only | code-referenced only |
| `CURRICULUM_VERSION` | ⚙️ config | ❌ code-only | code-referenced only |
| `D1_MIRROR_LAG_LOOP_SLEEP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `D1_MIRROR_LAG_REALERT_INTERVAL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `D1_MIRROR_LAG_REQUIRED_STREAK` | ⚙️ config | ❌ code-only | code-referenced only |
| `D1_MIRROR_LAG_THRESHOLD_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `D1_MIRROR_LAG_WARMUP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `D1_READ_BASE_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `D1_READ_SHARED_SECRET` | 🔒 secret | ❌ code-only | code-referenced only |
| `D1_READ_TIMEOUT_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `D1_SYNC_SECRET` | 🔒 secret | ✅ secretRef `d1-sync-secret` | code-referenced + wired |
| `D1_SYNC_SECRET_PREVIEW` | 🔒 secret | ❌ code-only | code-referenced only |
| `DATABASE_URL` | 🔒 secret | ❌ code-only | code-referenced only |
| `DB_NAME` | ⚙️ config | ❌ code-only | code-referenced only |
| `DEEPGRAM_API_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `DEEPGRAM_STT_MODEL` | ⚙️ config | ❌ code-only | code-referenced only |
| `DEEPGRAM_TTS_MODEL` | ⚙️ config | ❌ code-only | code-referenced only |
| `DEPLOYMENT_ENV` | ⚙️ config | ❌ code-only | code-referenced only |
| `DEVICE_COOKIE_MINTS_PER_MIN` | ⚙️ config | ❌ code-only | code-referenced only |
| `DISPATCH_SHARED_SECRET` | 🔒 secret | ❌ code-only | code-referenced only |
| `DO_CHAT_BASE_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `DO_CHAT_SHARED_SECRET` | 🔒 secret | ❌ code-only | code-referenced only |
| `DO_CHAT_TIMEOUT_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `EDGE_PROXY_DEPLOY_LOOP_SLEEP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `EDGE_PROXY_DEPLOY_REALERT_INTERVAL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `EDGE_PROXY_DEPLOY_STALE_THRESHOLD_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `EDGE_PROXY_DEPLOY_WARMUP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `EDGE_PROXY_DEPLOY_WORKFLOW` | ⚙️ config | ❌ code-only | code-referenced only |
| `EDGE_WORKER_PREVIEW_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `EDGE_WORKER_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `ELEVENLABS_API_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `ELEVENLABS_MODEL_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `ELEVENLABS_VOICE_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMAIL_FROM` | ⚙️ config | ✅ literal value | code-referenced + wired |
| `EMBED_BACKFILL_ALERT_FAILED_THRESHOLD` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_BACKFILL_ALERT_INTERVAL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_BACKFILL_ALERT_STALL_MINUTES` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_BACKFILL_AUTOSTART` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_BACKFILL_BATCH_SIZE` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_BACKFILL_INTERVAL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_BACKFILL_MAX_RPM` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_BACKFILL_PER_CALL_LIMIT` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_BACKFILL_THROUGHPUT_WINDOW_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_CACHE_ENABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_CACHE_TTL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_DEGRADED_MODE` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_DEGRADED_PROBE_WINDOW` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_DEGRADED_RESET_STREAK` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_DEGRADED_TRIP_FAILURES` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_DEGRADED_TRIP_P95_MS` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_INDIC_PROVIDER` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_MAX_CONCURRENT` | ⚙️ config | ❌ code-only | code-referenced only |
| `EMBED_PROVIDER_PRIMARY` | ⚙️ config | ✅ literal value | code-referenced + wired |
| `ENABLE_E2E_ADMIN` | ⚙️ config | ❌ code-only | code-referenced only |
| `ENABLE_LLM_SAFETY_CHECK` | ⚙️ config | ❌ code-only | code-referenced only |
| `ENABLE_PARALLEL_LLM_RACE` | ⚙️ config | ❌ code-only | code-referenced only |
| `ENTITY_SEO_CRUNCHBASE_PERMALINK` | ⚙️ config | ❌ code-only | code-referenced only |
| `ENTITY_SEO_WIKIDATA_QID` | ⚙️ config | ❌ code-only | code-referenced only |
| `ENTITY_SEO_WIKIPEDIA_TITLE` | ⚙️ config | ❌ code-only | code-referenced only |
| `ENV` | ⚙️ config | ✅ literal value | code-referenced + wired |
| `ENVIRONMENT` | ⚙️ config | ❌ code-only | code-referenced only |
| `EXAM_CALENDAR_PATH` | ⚙️ config | ❌ code-only | code-referenced only |
| `EXA_API_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `FRONTEND_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `GCP_CREDITS_REMAINING_USD` | ⚙️ config | ❌ code-only | code-referenced only |
| `GCP_DISCOVERY_COLLECTION` | ⚙️ config | ❌ code-only | code-referenced only |
| `GCP_DISCOVERY_DATA_STORE` | ⚙️ config | ❌ code-only | code-referenced only |
| `GCP_DISCOVERY_LOCATION` | ⚙️ config | ❌ code-only | code-referenced only |
| `GCP_DISCOVERY_SERVING_CONFIG` | ⚙️ config | ❌ code-only | code-referenced only |
| `GCP_OIDC_ALLOWED_EMAILS` | ⚙️ config | ❌ code-only | code-referenced only |
| `GCP_OIDC_DEV_BYPASS` | ⚙️ config | ❌ code-only | code-referenced only |
| `GCP_OIDC_DEV_SECRET` | 🔒 secret | ❌ code-only | code-referenced only |
| `GCP_OIDC_REQUIRED_AUDIENCE` | ⚙️ config | ❌ code-only | code-referenced only |
| `GCP_PROJECT_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `GCP_SCHEDULER_TAKEOVER` | ⚙️ config | ❌ code-only | code-referenced only |
| `GITHUB_CI_BRANCH` | ⚙️ config | ❌ code-only | code-referenced only |
| `GITHUB_CI_WORKFLOW` | ⚙️ config | ❌ code-only | code-referenced only |
| `GITHUB_REPO` | ⚙️ config | ❌ code-only | code-referenced only |
| `GITHUB_TOKEN` | 🔒 secret | ❌ code-only | code-referenced only |
| `GLACIER_ARCHIVE_BUCKETS` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_APPLICATION_CREDENTIALS` | 🔒 secret | ❌ code-only | code-referenced only |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | 🔒 secret | ❌ code-only | code-referenced only |
| `GOOGLE_BILLING_ACCOUNT_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_BILLING_ALERT` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_BILLING_BIGQUERY_DATASET` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_BILLING_BIGQUERY_LOCATION` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_BILLING_BIGQUERY_PROJECT` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_BILLING_BIGQUERY_TABLE` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_BOOKS_API_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `GOOGLE_CLOUD_PROJECT` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_FACT_CHECK_API_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `GOOGLE_INDEXING_DAILY_LIMIT` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_INDEXING_ENABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_INDEXING_PERSIST_DISABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_INDEXING_PERSIST_IN_TESTS` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_INDEXING_QUOTA_ALERT_DISABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_INDEXING_QUOTA_ALERT_IN_TESTS` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_INDEXING_SERVICE_ACCOUNT` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_INDEXING_TIER1_SUBJECTS` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_KG_API_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `GOOGLE_NLP_API_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `GOOGLE_OAUTH_CLIENT_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `GOOGLE_OAUTH_CLIENT_SECRET` | 🔒 secret | ❌ code-only | code-referenced only |
| `GOOGLE_PAGESPEED_API_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `GOOGLE_RR_API_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `GOOGLE_WEB_RISK_API_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `GROUNDED_RECALL_LEAK_GATE` | ⚙️ config | ❌ code-only | code-referenced only |
| `GROUNDED_RECALL_NIGHTLY_ENABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `GROUNDED_RECALL_NIGHTLY_GATE` | ⚙️ config | ❌ code-only | code-referenced only |
| `GROUNDED_RECALL_NIGHTLY_HOUR_UTC` | ⚙️ config | ❌ code-only | code-referenced only |
| `GSC_LOOKBACK_DAYS` | ⚙️ config | ❌ code-only | code-referenced only |
| `GSC_NEAR_MISS_MAX_POS` | ⚙️ config | ❌ code-only | code-referenced only |
| `GSC_NEAR_MISS_MIN_POS` | ⚙️ config | ❌ code-only | code-referenced only |
| `GSC_ROW_LIMIT` | ⚙️ config | ❌ code-only | code-referenced only |
| `GSC_SITE_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `GUNICORN_THREADS` | ⚙️ config | ❌ code-only | code-referenced only |
| `GUNICORN_WORKERS` | ⚙️ config | ❌ code-only | code-referenced only |
| `HEALTH_SNAPSHOT_PROBE_TIMEOUT_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `HEALTH_SNAPSHOT_TTL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `HOSTNAME` | ⚙️ config | ❌ code-only | code-referenced only |
| `INDEXNOW_ENABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `INDEXNOW_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `IP_COARSE_DAILY_CAP` | ⚙️ config | ❌ code-only | code-referenced only |
| `IP_HASH_SALT` | ⚙️ config | ❌ code-only | code-referenced only |
| `JWT_ACCESS_EXPIRE_MINUTES` | ⚙️ config | ❌ code-only | code-referenced only |
| `JWT_REFRESH_EXPIRE_MINUTES` | ⚙️ config | ❌ code-only | code-referenced only |
| `JWT_SECRET` | 🔒 secret | ✅ secretRef `jwt-secret` | wired but no code reference found (deploy-time only) |
| `KID_SAFE_EXTRA_PATTERNS` | ⚙️ config | ❌ code-only | code-referenced only |
| `KID_SAFE_THRESHOLD` | ⚙️ config | ❌ code-only | code-referenced only |
| `KV_ALERT_SECRET` | 🔒 secret | ❌ code-only | code-referenced only |
| `LLM_BATCH_WINDOW_MS` | ⚙️ config | ❌ code-only | code-referenced only |
| `LLM_MAX_CONCURRENT` | ⚙️ config | ❌ code-only | code-referenced only |
| `LLM_MODEL` | ⚙️ config | ❌ code-only | code-referenced only |
| `LLM_PRIMARY_PROVIDER` | ⚙️ config | ❌ code-only | code-referenced only |
| `LLM_PROVIDER` | ⚙️ config | ❌ code-only | code-referenced only |
| `LOGS_INGEST_MAX_BATCH` | ⚙️ config | ❌ code-only | code-referenced only |
| `LOGS_PAUSED` | ⚙️ config | ❌ code-only | code-referenced only |
| `LOG_INGEST_TOKEN` | 🔒 secret | ❌ code-only | code-referenced only |
| `LOG_LEVEL` | ⚙️ config | ❌ code-only | code-referenced only |
| `LOG_RETENTION_DAYS` | ⚙️ config | ❌ code-only | code-referenced only |
| `MATERIALIZE_FAQ_INTERVAL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `MAX_CONCURRENT_RACE_PROVIDERS` | ⚙️ config | ❌ code-only | code-referenced only |
| `MAX_DOCS_PER_RUN` | ⚙️ config | ❌ code-only | code-referenced only |
| `MEMORYSTORE_REDIS_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `MEMORY_BRAIN_CHAT_ENABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `MEMORY_BRAIN_COLLECTION` | ⚙️ config | ❌ code-only | code-referenced only |
| `MEMORY_BRAIN_DIMS` | ⚙️ config | ❌ code-only | code-referenced only |
| `MEMORY_BRAIN_ENSURE_INDEX` | ⚙️ config | ❌ code-only | code-referenced only |
| `MEMORY_BRAIN_FILTER_FIELDS` | ⚙️ config | ❌ code-only | code-referenced only |
| `MEMORY_BRAIN_FLEET_ROLLUP` | ⚙️ config | ❌ code-only | code-referenced only |
| `MEMORY_BRAIN_INDEX_NAME` | ⚙️ config | ❌ code-only | code-referenced only |
| `MEMORY_BRAIN_METRIC` | ⚙️ config | ❌ code-only | code-referenced only |
| `MEMORY_BRAIN_PROVIDER` | ⚙️ config | ❌ code-only | code-referenced only |
| `MEMORY_BRAIN_QUERY_MIN_SCORE` | ⚙️ config | ❌ code-only | code-referenced only |
| `MEMORY_BRAIN_QUERY_TIMEOUT_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `MEMORY_BRAIN_QUERY_TOP_K` | ⚙️ config | ❌ code-only | code-referenced only |
| `MEMORY_BRAIN_WORKER_STALE_SECONDS` | ⚙️ config | ❌ code-only | code-referenced only |
| `MIN_PROVIDERS_TO_RACE` | ⚙️ config | ❌ code-only | code-referenced only |
| `MONGODB_URI` | ⚙️ config | ❌ code-only | code-referenced only |
| `MONGO_URL` | 🔒 secret | ✅ secretRef `mongo-uri` | code-referenced + wired |
| `MONITORED_URLS_PATH` | ⚙️ config | ❌ code-only | code-referenced only |
| `MONTHLY_TOTAL_USD_CAP` | ⚙️ config | ❌ code-only | code-referenced only |
| `OBSERVABILITY_DIGEST_TO` | ⚙️ config | ❌ code-only | code-referenced only |
| `OCR_IP_DAILY_CAP` | ⚙️ config | ❌ code-only | code-referenced only |
| `ORIGIN_SHARED_SECRET` | 🔒 secret | ✅ secretRef `origin-shared-secret` | code-referenced + wired |
| `ORIGIN_SHARED_SECRET_HEADER` | 🔒 secret | ❌ code-only | code-referenced only |
| `OTEL_EXPORTER_GCP_PROJECT_ID` | ⚙️ config | ✅ literal value | code-referenced + wired |
| `OTEL_SERVICE_NAME` | ⚙️ config | ✅ literal value | code-referenced + wired |
| `OTEL_SERVICE_NAMESPACE` | ⚙️ config | ❌ code-only | code-referenced only |
| `OTEL_SERVICE_VERSION` | ⚙️ config | ❌ code-only | code-referenced only |
| `OTEL_TRACES_EXPORTER` | ⚙️ config | ✅ literal value | wired but no code reference found (deploy-time only) |
| `PAGES_SSR_ENABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `PARALLEL_RACE_TIMEOUT` | ⚙️ config | ❌ code-only | code-referenced only |
| `PINECONE_API_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `PINECONE_EMBED_MODEL` | ⚙️ config | ❌ code-only | code-referenced only |
| `PINECONE_INDEX` | ⚙️ config | ❌ code-only | code-referenced only |
| `PINECONE_INDEX_DIMS` | ⚙️ config | ❌ code-only | code-referenced only |
| `PINECONE_INDEX_METRIC` | ⚙️ config | ❌ code-only | code-referenced only |
| `PINECONE_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `PINECONE_NAMESPACE` | ⚙️ config | ❌ code-only | code-referenced only |
| `PINECONE_RERANK_MODEL` | ⚙️ config | ❌ code-only | code-referenced only |
| `PINECONE_SKIP_MONGO_EMBED` | ⚙️ config | ❌ code-only | code-referenced only |
| `PINECONE_TIMEOUT_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `PINECONE_WRITE` | ⚙️ config | ❌ code-only | code-referenced only |
| `PIPELINE_LLM_CONCURRENCY` | ⚙️ config | ❌ code-only | code-referenced only |
| `PIPELINE_QUIZ_PREGEN_CONCURRENCY` | ⚙️ config | ❌ code-only | code-referenced only |
| `PORT` | ⚙️ config | ❌ code-only | code-referenced only |
| `POSTHOG_API_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `POSTHOG_HOST` | ⚙️ config | ❌ code-only | code-referenced only |
| `PREWARM_CONCURRENCY` | ⚙️ config | ❌ code-only | code-referenced only |
| `PREWARM_EXAM_LOOKAHEAD_DAYS` | ⚙️ config | ❌ code-only | code-referenced only |
| `PREWARM_HTTP_TIMEOUT_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `PREWARM_INTERVAL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `PREWARM_TOP_N` | ⚙️ config | ❌ code-only | code-referenced only |
| `PRODUCTION_ORIGINS` | ⚙️ config | ❌ code-only | code-referenced only |
| `PUBLIC_BASE_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `PUBLIC_ORIGIN` | ⚙️ config | ❌ code-only | code-referenced only |
| `PYTEST_CURRENT_TEST` | ⚙️ config | ❌ code-only | code-referenced only |
| `R2_ACCESS_KEY_ID` | 🔒 secret | ❌ code-only | code-referenced only |
| `R2_BUCKET_NAME` | ⚙️ config | ❌ code-only | code-referenced only |
| `R2_ENDPOINT_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `R2_PUBLIC_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `R2_SECRET_ACCESS_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `RAG_CACHE_ENABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `RAG_CACHE_TTL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `RAG_EMBEDDING_PROVIDER_FORCE` | ⚙️ config | ❌ code-only | code-referenced only |
| `RAG_RETRIEVER` | ⚙️ config | ❌ code-only | code-referenced only |
| `RAILWAY_ENVIRONMENT_NAME` | ⚙️ config | ❌ code-only | code-referenced only |
| `RAILWAY_LOGS_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `RAILWAY_PROJECT_NAME` | ⚙️ config | ❌ code-only | code-referenced only |
| `RAILWAY_SERVICE_NAME` | ⚙️ config | ❌ code-only | code-referenced only |
| `RAZORPAY_KEY_ID` | 🔒 secret | ❌ code-only | code-referenced only |
| `RAZORPAY_KEY_SECRET` | 🔒 secret | ✅ secretRef `razorpay-key-secret` | code-referenced + wired |
| `RAZORPAY_WEBHOOK_SECRET` | 🔒 secret | ❌ code-only | code-referenced only |
| `REDIS_AI_CACHE_CONNECT_TIMEOUT_MS` | ⚙️ config | ❌ code-only | code-referenced only |
| `REDIS_AI_CACHE_MAX_ENTRY_BYTES` | ⚙️ config | ❌ code-only | code-referenced only |
| `REDIS_AI_CACHE_NAMESPACE` | ⚙️ config | ❌ code-only | code-referenced only |
| `REDIS_AI_CACHE_OP_TIMEOUT_MS` | ⚙️ config | ❌ code-only | code-referenced only |
| `REDIS_AI_CACHE_TTL` | ⚙️ config | ❌ code-only | code-referenced only |
| `REDIS_CASUAL_CACHE_TTL` | ⚙️ config | ❌ code-only | code-referenced only |
| `REDIS_CHAT_CACHE_TTL` | ⚙️ config | ❌ code-only | code-referenced only |
| `REDIS_GET_CACHE_TTL_MS` | ⚙️ config | ❌ code-only | code-referenced only |
| `REPLIT_DEPLOYMENT` | ⚙️ config | ❌ code-only | code-referenced only |
| `REPLIT_DEV_DOMAIN` | ⚙️ config | ❌ code-only | code-referenced only |
| `REPLIT_DOMAINS` | ⚙️ config | ❌ code-only | code-referenced only |
| `REPL_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `RERANK_PROVIDER` | ⚙️ config | ❌ code-only | code-referenced only |
| `RUN_LEGACY_LOOPS` | ⚙️ config | ❌ code-only | code-referenced only |
| `S3_FINALS_BUCKET` | ⚙️ config | ❌ code-only | code-referenced only |
| `SARVAM_API_KEY` | 🔒 secret | ✅ secretRef `sarvam-api-key` | code-referenced + wired |
| `SARVAM_API_KEY_2` | 🔒 secret | ❌ code-only | code-referenced only |
| `SARVAM_API_KEY_3` | 🔒 secret | ❌ code-only | code-referenced only |
| `SARVAM_PER_USER_MONTHLY_CAP` | ⚙️ config | ❌ code-only | code-referenced only |
| `SECURE_COOKIES` | ⚙️ config | ❌ code-only | code-referenced only |
| `SENTRY_AUTH_TOKEN` | 🔒 secret | ❌ code-only | code-referenced only |
| `SENTRY_DSN` | 🔒 secret | ✅ secretRef `sentry-dsn` | code-referenced + wired |
| `SENTRY_ENVIRONMENT` | ⚙️ config | ✅ literal value | code-referenced + wired |
| `SENTRY_ERRORS_LIMIT` | ⚙️ config | ❌ code-only | code-referenced only |
| `SENTRY_ERRORS_USED_MTD` | ⚙️ config | ❌ code-only | code-referenced only |
| `SENTRY_ORG` | ⚙️ config | ❌ code-only | code-referenced only |
| `SENTRY_PLAN` | ⚙️ config | ❌ code-only | code-referenced only |
| `SENTRY_PROJECT_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `SENTRY_RELEASE` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_ALERT_DEEP_SCAN_MAX_SITEMAPS` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_AUTO_PUBLISH_ENABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_AUTO_PUBLISH_FREQUENCY` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_AUTO_PUBLISH_HOUR_UTC` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_AUTO_PUBLISH_PAGE_TYPES` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_AUTO_PUBLISH_WEEKDAY` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_FANOUT_ENABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_LINKER_AUTO_PER_DAY` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_LINKER_AUTO_THRESHOLD` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_LINKER_ENABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_LINKER_MAX_PER_TARGET` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_LINKER_MIN_PER_TARGET` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_LINKER_NIGHTLY_IDLE_SECS` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_LINKER_NIGHTLY_TOP_N` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_LINKER_POOL_SIZE` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_REMEDIATION_AUTOPUBLISH_PER_DAY` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_REMEDIATION_CIRCUIT_COOLDOWN_H` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_REMEDIATION_CIRCUIT_RATIO` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_REMEDIATION_CIRCUIT_WINDOW` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_REMEDIATION_DRAFT_PER_DAY` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_REMEDIATION_ENABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_REMEDIATION_FANOUT_CAP` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_REMEDIATION_IDLE_BACKOFF` | ⚙️ config | ❌ code-only | code-referenced only |
| `SEO_REMEDIATION_MIN_DELTA` | ⚙️ config | ❌ code-only | code-referenced only |
| `SESSION_FALLBACK_HERD_PCT` | ⚙️ config | ❌ code-only | code-referenced only |
| `SESSION_FALLBACK_K` | ⚙️ config | ❌ code-only | code-referenced only |
| `SESSION_FALLBACK_PROVIDER` | ⚙️ config | ❌ code-only | code-referenced only |
| `SESSION_FALLBACK_TTFB_MS` | ⚙️ config | ❌ code-only | code-referenced only |
| `SESSION_FALLBACK_TTL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `SESSION_TTFB_KEY_TTL_S` | 🔒 secret | ❌ code-only | code-referenced only |
| `SES_REGION` | ⚙️ config | ✅ literal value | code-referenced + wired |
| `SLACK_WEBHOOK_DEFAULT_CHANNEL` | ⚙️ config | ❌ code-only | code-referenced only |
| `SLACK_WEBHOOK_MISSING_BOOTSTRAP_GRACE_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `SLACK_WEBHOOK_MISSING_LEASE_TTL_CEILING_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `SLACK_WEBHOOK_MISSING_LOOP_SLEEP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `SLACK_WEBHOOK_MISSING_REALERT_INTERVAL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `SLACK_WEBHOOK_MISSING_SNOOZE_MAX_HOURS` | ⚙️ config | ❌ code-only | code-referenced only |
| `SLACK_WEBHOOK_MISSING_SNOOZE_MIN_HOURS` | ⚙️ config | ❌ code-only | code-referenced only |
| `SLACK_WEBHOOK_MISSING_WARMUP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `SLACK_WEBHOOK_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `SLOW_QUERY_THRESHOLD_MS` | ⚙️ config | ❌ code-only | code-referenced only |
| `SQS_QUEUE_URL_SSM_PARAM` | ⚙️ config | ❌ code-only | code-referenced only |
| `SSR_PROBE_URLS` | ⚙️ config | ❌ code-only | code-referenced only |
| `SUPABASE_ANON_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `SUPABASE_CANARY_EMAIL` | ⚙️ config | ❌ code-only | code-referenced only |
| `SUPABASE_CANARY_PASSWORD` | 🔒 secret | ❌ code-only | code-referenced only |
| `SUPABASE_DB_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `SUPABASE_JWKS_HTTP_TIMEOUT_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `SUPABASE_JWKS_STALE_GRACE_SECONDS` | ⚙️ config | ❌ code-only | code-referenced only |
| `SUPABASE_JWKS_TTL_SECONDS` | ⚙️ config | ❌ code-only | code-referenced only |
| `SUPABASE_JWT_AUD` | 🔒 secret | ❌ code-only | code-referenced only |
| `SUPABASE_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `SUPABASE_SERVICE_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `SUPABASE_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `SYNTHETIC_PROBE_SECRETS_CHECK_TOKEN` | 🔒 secret | ❌ code-only | code-referenced only |
| `TAVILY_API_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `TOPIC_DISCOVERY_BING_SEED_LIMIT` | ⚙️ config | ❌ code-only | code-referenced only |
| `TOPIC_DISCOVERY_GRADER_MODEL` | ⚙️ config | ❌ code-only | code-referenced only |
| `TOPIC_DISCOVERY_LOOP_SLEEP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `TOPIC_DISCOVERY_LOOP_WARMUP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `TOPIC_DISCOVERY_RSS_FEEDS` | ⚙️ config | ❌ code-only | code-referenced only |
| `TOPIC_DISCOVERY_SUGGEST_SEED_LIMIT` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRANSLATE_PROVIDER` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRUSTPILOT_AGGREGATE_OVERRIDE_JSON` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRUSTPILOT_AGGREGATE_TTL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRUSTPILOT_API_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `TRUSTPILOT_BUSINESS_UNIT_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRUSTPILOT_DOMAIN` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRUSTPILOT_FEED_LOOP_SLEEP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRUSTPILOT_FEED_REALERT_INTERVAL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRUSTPILOT_FEED_STALE_THRESHOLD_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRUSTPILOT_FEED_WARMUP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRUSTPILOT_INVITE_DAILY_LIMIT` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRUSTPILOT_PROFILE_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRUSTPILOT_REFRESH_CRON_BOOTSTRAP_GRACE_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRUSTPILOT_REFRESH_CRON_LOOP_SLEEP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRUSTPILOT_REFRESH_CRON_REALERT_INTERVAL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRUSTPILOT_REFRESH_CRON_SILENT_THRESHOLD_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRUSTPILOT_REFRESH_CRON_WARMUP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `TRUSTPILOT_REFRESH_SECRET` | 🔒 secret | ❌ code-only | code-referenced only |
| `TRUSTPILOT_REVIEW_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `TURNSTILE_SECRET_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `TURNSTILE_SITE_KEY` | 🔒 secret | ❌ code-only | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_INTERVAL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_LIMIT` | ⚙️ config | ❌ code-only | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_LOOKBACK_MIN` | ⚙️ config | ❌ code-only | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_MAX_SUBDIVISIONS` | ⚙️ config | ❌ code-only | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_REALERT_INTERVAL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_SILENCE_BOOTSTRAP_GRACE_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_SILENCE_LOOP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_SILENCE_WARMUP_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_SILENT_THRESHOLD_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `UNIFIED_LOGS_MAX_INGEST_BATCH` | ⚙️ config | ❌ code-only | code-referenced only |
| `UNIFIED_LOGS_PAUSE` | ⚙️ config | ❌ code-only | code-referenced only |
| `UNIFIED_LOGS_TTL_DAYS` | ⚙️ config | ❌ code-only | code-referenced only |
| `UPSTASH_REDIS_REST_TOKEN` | 🔒 secret | ❌ code-only | code-referenced only |
| `UPSTASH_REDIS_REST_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `VALIDATION_SAMPLE_RATE` | ⚙️ config | ❌ code-only | code-referenced only |
| `VECTORIZE_INDEX_NAME` | ⚙️ config | ❌ code-only | code-referenced only |
| `VECTORIZE_SHADOW_SAMPLE_RATE` | ⚙️ config | ❌ code-only | code-referenced only |
| `VERTEX_BREAKER_COOLDOWN_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `VERTEX_BREAKER_THRESHOLD` | ⚙️ config | ❌ code-only | code-referenced only |
| `VERTEX_FORMAT_BREAKER_COOLDOWN_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `VERTEX_FORMAT_BREAKER_THRESHOLD` | ⚙️ config | ❌ code-only | code-referenced only |
| `VERTEX_GEMINI_MODEL` | ⚙️ config | ❌ code-only | code-referenced only |
| `VERTEX_HEALTH_TTL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `VERTEX_LOCATION` | ⚙️ config | ❌ code-only | code-referenced only |
| `VERTEX_PROBE_INTERVAL_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `VERTEX_PROJECT_ID` | ⚙️ config | ❌ code-only | code-referenced only |
| `VERTEX_REGION` | ⚙️ config | ❌ code-only | code-referenced only |
| `VERTEX_STARTUP_PROBE_TIMEOUT_S` | ⚙️ config | ❌ code-only | code-referenced only |
| `WEB_PUSH_CONTACT` | ⚙️ config | ✅ literal value | code-referenced + wired |
| `WEB_PUSH_VAPID_PRIVATE_KEY` | 🔒 secret | ✅ secretRef `web-push-vapid-private-key` | code-referenced + wired |
| `WORKERS_AI_EDGE_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `WORKERS_AI_FALLBACK_ENABLED` | ⚙️ config | ❌ code-only | code-referenced only |
| `WORKERS_AI_FALLBACK_SECRET` | 🔒 secret | ❌ code-only | code-referenced only |
| `WORKERS_AI_TIMEOUT_SEC` | ⚙️ config | ❌ code-only | code-referenced only |
| `WORKERS_BACKEND` | ⚙️ config | ❌ code-only | code-referenced only |
| `WORKERS_EMBED_DIMS` | ⚙️ config | ❌ code-only | code-referenced only |
| `WORKERS_EMBED_MAX_BATCH` | ⚙️ config | ❌ code-only | code-referenced only |
| `WORKERS_EMBED_RETRIES` | ⚙️ config | ❌ code-only | code-referenced only |
| `WORKERS_EMBED_SECRET` | 🔒 secret | ✅ secretRef `workers-embed-secret` | code-referenced + wired |
| `WORKERS_EMBED_STAGING_URL` | ⚙️ config | ❌ code-only | code-referenced only |
| `WORKERS_EMBED_TIMEOUT_S` | ⚙️ config | ❌ code-only | code-referenced only |
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
| `EDGE_LOG_FLUSH_AGE_MS` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `EDGE_LOG_FLUSH_BATCH` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `EDGE_LOG_SAMPLE_RATE` | ⚙️ config | ❌ wrangler secret (operator-set) |  |
| `JWT_SECRET` | 🔒 secret | ❌ wrangler secret (operator-set) |  |
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
| `SYNTHETIC_PROBE_ADMIN_JWT` | 🔒 secret | ❌ wrangler secret (operator-set) |  |
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
| `BACKEND_AUTH_KEY` | 🔒 secret | ❌ wrangler secret (operator-set) |  |
