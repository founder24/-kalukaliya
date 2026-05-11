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

## Column semantics

- **required?** — `required` if at least one call site uses the
  raise-on-missing form (`os.environ["X"]`); `optional` if every
  call site uses `os.environ.get(...)` / `os.getenv(...)`.
  Worker (TS) refs are reported as `optional` because TS defaults
  typically live next to the access via `?? "x"` and aren't
  parsed by the regex extractor — treat the deploy-infra wiring
  as the source of truth for required-on-deploy.
- **default** — the literal default expression seen in the first
  call site that supplies one. If the code didn't supply a
  default but the deploy infra hard-codes a literal (bicep
  `value:` / TF / wrangler `[vars]`), that value is shown with a
  `(deploy)` suffix. `—` means the var has no default and must
  be supplied at runtime.
- **source** — `relpath:line` of the first reference. Code refs
  win over deploy refs; deploy refs are shown with a `(deploy)`
  suffix when the var has no code reference.

## Sources scanned

- ACA backend: `artifacts/syrabit-backend/**/*.py` (excluding `tests/`, `scripts/`, `__pycache__`)
- Background jobs: `artifacts/syrabit-backend/aca_jobs/`, `artifacts/syrabit/services/backend/lambda_batch/`
- Workers: `workers/edge-proxy/src/`, `artifacts/syrabit/workers/embed-worker/src/`, `workers/email-worker/src/`
- Deploy infra: `infra/azure/aca-syrabit-backend.bicep`, `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf`, the three wrangler.toml files

## Limitations

- Regex extraction (no AST) — vars built from `f"PREFIX_{var}"`
  strings or destructured TS objects are NOT captured.
- One-off operator scripts under `artifacts/syrabit-backend/scripts/`
  are intentionally excluded — they don't run in production.
- `secret?` classification is heuristic (name-suffix + bicep
  `secretRef` wiring); see `NON_SECRET_OVERRIDES` in the script.
- A `❌ not wired` row in the ACA-backend table can mean: (a) the
  operator is expected to inject it via the ACA env block by hand,
  (b) the code path is dead, or (c) the value is sourced from
  another infra file (`infra/aws/account-billing.tf`, upstream
  Key Vault) — review case-by-case.
- TS `required?` is always `optional` because the regex extractor
  does not parse `?? "x"` / `|| "x"` defaults next to access.
  Confirm runtime-required workers vars from the deploy infra and
  the worker source itself.

## ACA backend (`syrabit-backend`)

FastAPI runtime in `artifacts/syrabit-backend/`, deployed to Azure Container Apps via `infra/azure/aca-syrabit-backend.bicep`. The bicep `env:` array is the canonical wiring.

**Deploy file(s):** `infra/azure/aca-syrabit-backend.bicep`

| env var | type | required? | default | wired in deploy infra? | source | notes |
|---|---|---|---|---|---|---|
| `ACA_JOB_BATCHES_DISABLED` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/materialize_chapter_faqs.py:871` | code-referenced only |
| `ADMIN_EMAILS` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_auth_users.py:76` | code-referenced only |
| `ADMIN_JWT_SECRET` | 🔒 secret | — | — | ✅ secretRef `admin-jwt-secret` | `infra/azure/aca-syrabit-backend.bicep:170` (deploy) | wired but no code reference found (deploy-time only) |
| `ADMIN_LLM_MAX_CONCURRENT` | ⚙️ config | optional | `30` | ❌ code-only | `artifacts/syrabit-backend/llm.py:121` | code-referenced only |
| `ADMIN_NAMES` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_auth_users.py:78` | code-referenced only |
| `ADMIN_PASSWORDS` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_auth_users.py:77` | code-referenced only |
| `ADSENSE_ACCOUNT_ID` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_ads.py:451` | code-referenced only |
| `ADSENSE_CLIENT_ID` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_ads.py:449` | code-referenced only |
| `ADSENSE_CLIENT_SECRET` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_ads.py:450` | code-referenced only |
| `ADSENSE_REFRESH_TOKEN` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_ads.py:448` | code-referenced only |
| `AIG_GUARDRAIL_BLOCK_RATIO_THRESHOLD` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_aig_guardrail_alerts.py:61` | code-referenced only |
| `AIG_GUARDRAIL_LOOP_SLEEP_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_aig_guardrail_alerts.py:75` | code-referenced only |
| `AIG_GUARDRAIL_MIN_SAMPLES` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_aig_guardrail_alerts.py:68` | code-referenced only |
| `AIG_GUARDRAIL_REALERT_INTERVAL_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_aig_guardrail_alerts.py:72` | code-referenced only |
| `AIG_GUARDRAIL_WARMUP_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_aig_guardrail_alerts.py:76` | code-referenced only |
| `AI_RESPONSE_CACHE_KV_ID` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/ai_input_cache.py:650` | code-referenced only |
| `AI_RESPONSE_CACHE_KV_ID_NE_INDIA` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/ai_input_cache.py:659` | code-referenced only |
| `ALERT_EMAIL` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/metrics.py:1939` | code-referenced only |
| `ALERT_WEBHOOK_URL` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/metrics.py:1991` | code-referenced only |
| `APPRUNNER_SERVICE_URL` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:1016` | code-referenced only |
| `AS_BACKFILL_BATCH_SIZE` | ⚙️ config | optional | `"5"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/as_translation_backfill.py:95` | code-referenced only |
| `AS_BACKFILL_INTER_DOC_SLEEP_S` | ⚙️ config | optional | `"0.25"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/as_translation_backfill.py:97` | code-referenced only |
| `AS_BACKFILL_MAX_CHUNK_CHARS` | ⚙️ config | optional | `"1500"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/as_translation_backfill.py:105` | code-referenced only |
| `AS_BACKFILL_PER_CALL_LIMIT` | ⚙️ config | optional | `"200"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/as_translation_backfill.py:93` | code-referenced only |
| `AS_BACKFILL_TRANSLATE_TIMEOUT_S` | ⚙️ config | optional | `"45"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/as_translation_backfill.py:100` | code-referenced only |
| `AS_COVERAGE_INLINE_BACKFILL_LIMIT` | ⚙️ config | optional | `"2000"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/as_translation_backfill.py:603` | code-referenced only |
| `ATLAS_VS_COLLECTION` | ⚙️ config | optional | `"chunks"` | ❌ code-only | `artifacts/syrabit-backend/retrievers/mongodb_vector.py:86` | code-referenced only |
| `ATLAS_VS_DIMENSIONS` | ⚙️ config | optional | `"1024"` | ❌ code-only | `artifacts/syrabit-backend/retrievers/mongodb_vector.py:88` | code-referenced only |
| `ATLAS_VS_ENABLED` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/startup_checks.py:34` | code-referenced only |
| `ATLAS_VS_FILTER_FIELDS` | ⚙️ config | optional | `"subject_id,board_id,class_id,chunk_type"` | ❌ code-only | `artifacts/syrabit-backend/retrievers/mongodb_vector.py:92` | code-referenced only |
| `ATLAS_VS_INDEX_NAME` | ⚙️ config | optional | `"vector_index"` | ❌ code-only | `artifacts/syrabit-backend/retrievers/mongodb_vector.py:87` | code-referenced only |
| `ATLAS_VS_METRIC` | ⚙️ config | optional | `"cosine"` | ❌ code-only | `artifacts/syrabit-backend/retrievers/mongodb_vector.py:89` | code-referenced only |
| `AWS_ACCESS_KEY_ID` | 🔒 secret | optional | `''` | ✅ secretRef `aws-access-key-id` | `artifacts/syrabit-backend/config.py:630` | code-referenced + wired |
| `AWS_ACCOUNT_ALIAS` | ⚙️ config | optional | `"AWS Activate Portfolio"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_credits.py:902` | code-referenced only |
| `AWS_ACTIVATE_CUMULATIVE_SPEND_USD` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_credits.py:886` | code-referenced only |
| `AWS_ACTIVATE_EXPIRY` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_billing.py:115` | code-referenced only |
| `AWS_ACTIVATE_GRANT_USD` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_billing.py:114` | code-referenced only |
| `AWS_ACTIVATE_REMAINING_USD` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_credits.py:892` | code-referenced only |
| `AWS_ACTIVATE_SPEND_MTD` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_credits.py:894` | code-referenced only |
| `AWS_DEFAULT_REGION` | ⚙️ config | optional | `"us-east-1"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_credits.py:759` | code-referenced only |
| `AWS_FRAUD_DETECTOR_ID` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_monetization.py:250` | code-referenced only |
| `AWS_FRAUD_DETECTOR_PAYMENT_ID` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_monetization.py:250` | code-referenced only |
| `AWS_FRAUD_DETECTOR_PAYMENT_VERSION` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_monetization.py:251` | code-referenced only |
| `AWS_FRAUD_DETECTOR_VERSION` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_monetization.py:251` | code-referenced only |
| `AWS_GLACIER_REGION` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_archive.py:113` | code-referenced only |
| `AWS_NATIVE_PRIMARY_REGION` | ⚙️ config | optional | `"ap-south-1"` | ❌ code-only | `artifacts/syrabit-backend/providers/aws_native.py:69` | code-referenced only |
| `AWS_NATIVE_SECONDARY_REGION` | ⚙️ config | optional | `"us-east-1"` | ❌ code-only | `artifacts/syrabit-backend/providers/aws_native.py:70` | code-referenced only |
| `AWS_PERSONALIZE_CAMPAIGN_ARN` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/edu_study.py:3107` | code-referenced only |
| `AWS_REGION` | ⚙️ config | optional | `os.environ.get('AWS_DEFAULT_REGION', 'us-east-1'` | ❌ code-only | `artifacts/syrabit-backend/config.py:632` | code-referenced only |
| `AWS_SECRET_ACCESS_KEY` | 🔒 secret | optional | `''` | ✅ secretRef `aws-secret-access-key` | `artifacts/syrabit-backend/config.py:631` | code-referenced + wired |
| `AWS_SES_REGION` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/email_templates.py:76` | code-referenced only |
| `AWS_TRANSCRIBE_TMP_BUCKET` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/voice.py:142` | code-referenced only |
| `AXIOM_API_TOKEN` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_billing.py:419` | code-referenced only |
| `AXIOM_INGEST_GB_MTD` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_credits.py:1035` | code-referenced only |
| `AXIOM_INGEST_LIMIT_GB` | ⚙️ config | optional | `"500"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_credits.py:1021` | code-referenced only |
| `AXIOM_ORG_ID` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_billing.py:420` | code-referenced only |
| `AXIOM_RETENTION_DAYS` | ⚙️ config | optional | `"30"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_credits.py:1022` | code-referenced only |
| `AZURE_ACTIVATE_CUMULATIVE_SPEND_USD` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_credits.py:957` | code-referenced only |
| `AZURE_ACTIVATE_EXPIRY` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_billing.py:262` | code-referenced only |
| `AZURE_ACTIVATE_GRANT_USD` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_billing.py:261` | code-referenced only |
| `AZURE_ACTIVATE_REMAINING_USD` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_credits.py:962` | code-referenced only |
| `AZURE_ACTIVATE_SPEND_MTD` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_credits.py:964` | code-referenced only |
| `AZURE_CLIENT_ID` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_billing.py:257` | code-referenced only |
| `AZURE_CLIENT_SECRET` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_billing.py:258` | code-referenced only |
| `AZURE_CRON_RG` | ⚙️ config | optional | `"syrabit-cron-obs-rg"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_azure_cron.py:45` | code-referenced only |
| `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` | ⚙️ config | optional | `os.environ.get('AZURE_FORM_RECOGNIZER_ENDPOINT', ''` | ❌ code-only | `artifacts/syrabit-backend/config.py:642` | code-referenced only |
| `AZURE_DOCUMENT_INTELLIGENCE_KEY` | 🔒 secret | optional | `os.environ.get('AZURE_FORM_RECOGNIZER_KEY', ''` | ❌ code-only | `artifacts/syrabit-backend/config.py:638` | code-referenced only |
| `AZURE_SUBSCRIPTION_ID` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_azure_cron.py:44` | code-referenced only |
| `AZURE_SUBSCRIPTION_NAME` | ⚙️ config | optional | `"Azure for Startups"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_credits.py:972` | code-referenced only |
| `AZURE_TENANT_ID` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_billing.py:259` | code-referenced only |
| `BACKEND_PORT` | ⚙️ config | optional | `os.environ.get("PORT", "7766"` | ❌ code-only | `artifacts/syrabit-backend/gunicorn.conf.py:3` | code-referenced only |
| `BATCH_JOB_DRIVER` | ⚙️ config | optional | `"aca",` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/as_translation_backfill.py:429` | code-referenced only |
| `BEDROCK_EMBED_REGION` | ⚙️ config | optional | `'us-east-1'` | ❌ code-only | `artifacts/syrabit-backend/config.py:565` | code-referenced only |
| `BENCH_OUT` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/bench/retriever_bench.py:260` | code-referenced only |
| `BENCH_QUERIES` | ⚙️ config | optional | `"0"` | ❌ code-only | `artifacts/syrabit-backend/bench/retriever_bench.py:255` | code-referenced only |
| `BENCH_QUERIES_FILE` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/bench/retriever_bench.py:257` | code-referenced only |
| `BENCH_RETRIEVERS` | ⚙️ config | optional | `"pinecone,vectorize"` | ❌ code-only | `artifacts/syrabit-backend/bench/retriever_bench.py:259` | code-referenced only |
| `BENCH_TOP_K` | ⚙️ config | optional | `"10"` | ❌ code-only | `artifacts/syrabit-backend/bench/retriever_bench.py:258` | code-referenced only |
| `BILLING_WEBHOOK_AUDIENCE` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_billing.py:773` | code-referenced only |
| `BING_KEYWORD_API_KEY` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/bing_keyword_client.py:354` | code-referenced only |
| `BING_SITE_URL` | ⚙️ config | optional | `"https://syrabit.ai"` | ❌ code-only | `artifacts/syrabit-backend/bing_submit_client.py:67` | code-referenced only |
| `BING_WEBMASTER_API_KEY` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/bing_submit_client.py:63` | code-referenced only |
| `BULK_EMAIL_WORKER_AUTH_KEY` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/bulk_email.py:63` | code-referenced only |
| `BULK_EMAIL_WORKER_URL` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/bulk_email.py:59` | code-referenced only |
| `CACHE_CARDINALITY_MULTIPLIER` | ⚙️ config | optional | `"3.0"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_cache.py:307` | code-referenced only |
| `CACHE_FINGERPRINT_DUAL_READ` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/ai_input_cache.py:538` | code-referenced only |
| `CACHE_HIT_RATIO_FLOOR` | ⚙️ config | optional | `"0.30"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_cache.py:304` | code-referenced only |
| `CF_ACCESS_AUD_ADMIN` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/cf_access.py:79` | code-referenced only |
| `CF_ACCESS_AUD_INTERNAL` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/cf_access.py:80` | code-referenced only |
| `CF_ACCESS_BREAK_GLASS` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/cf_access.py:117` | code-referenced only |
| `CF_ACCESS_BREAK_GLASS_TOKEN` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/cf_access.py:122` | code-referenced only |
| `CF_ACCESS_ENFORCE` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/cf_access.py:89` | code-referenced only |
| `CF_ACCESS_SILENT_LOCKOUT_INTERVAL_S` | ⚙️ config | optional | `"1800"` | ❌ code-only | `artifacts/syrabit-backend/server.py:482` | code-referenced only |
| `CF_ACCESS_TEAM_DOMAIN` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/cf_access.py:78` | code-referenced only |
| `CF_ACCOUNT_ID` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/ai_input_cache.py:648` | code-referenced only |
| `CF_AI_GATEWAY_ACCOUNT_ID` | ⚙️ config | required | `""` | ❌ code-only | `artifacts/syrabit-backend/cf_enterprise.py:50` | code-referenced only |
| `CF_AI_GATEWAY_CACHE_TTL` | ⚙️ config | optional | `'86400'` | ❌ code-only | `artifacts/syrabit-backend/config.py:355` | code-referenced only |
| `CF_AI_GATEWAY_ID` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:335` | code-referenced only |
| `CF_AI_GATEWAY_TOKEN` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:345` | code-referenced only |
| `CF_AI_GATEWAY_URL` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/healthz.py:133` | code-referenced only |
| `CF_AI_POST_MAX_RETRIES` | ⚙️ config | optional | `"3"` | ❌ code-only | `artifacts/syrabit-backend/providers/cloudflare_ai.py:122` | code-referenced only |
| `CF_AI_POST_RETRY_BASE_S` | ⚙️ config | optional | `"1.0"` | ❌ code-only | `artifacts/syrabit-backend/providers/cloudflare_ai.py:123` | code-referenced only |
| `CF_ANALYTICS_API_TOKEN` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/cf_tiered_cache.py:74` | code-referenced only |
| `CF_API_DOMAIN` | ⚙️ config | optional | `"https://api.syrabit.ai"` | ❌ code-only | `artifacts/syrabit-backend/cloudflare_client.py:1002` | code-referenced only |
| `CF_API_TOKEN` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/ai_input_cache.py:649` | code-referenced only |
| `CF_AUDIT_STALE_THRESHOLD_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_health.py:684` | code-referenced only |
| `CF_AUDIT_WORKFLOW` | ⚙️ config | optional | `"cloudflare-weekly-audit.yml"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_health.py:681` | code-referenced only |
| `CF_BOT_REPORT_DIR` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/cf_bot_crosscheck.py:88` | code-referenced only |
| `CF_EDGE_KV_CACHE_URL` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_kv_health.py:53` | code-referenced only |
| `CF_EDGE_PROXY_URL` | ⚙️ config | optional | `"https://api.syrabit.ai"` | ❌ code-only | `artifacts/syrabit-backend/cloudflare_client.py:1089` | code-referenced only |
| `CF_PAGES_API_TOKEN` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/wai_chapter_index.py:54` | code-referenced only |
| `CF_PAGES_DEPLOY_HOOK_URL` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/pages_deploy.py:46` | code-referenced only |
| `CF_POLISH_SMOKE_URL` | ⚙️ config | optional | `"https://syrabit.ai/opengraph.jpg",` | ❌ code-only | `artifacts/syrabit-backend/cf_speed_smoke.py:20` | code-referenced only |
| `CF_TUNNEL_ALLOWED_IPS` | ⚙️ config | optional | `# IPv4 — https://www.cloudflare.com/ips-v4 '173.245.48.0/20…` | ❌ code-only | `artifacts/syrabit-backend/config.py:305` | code-referenced only |
| `CF_WAF_DRIFT_CRON_BOOTSTRAP_GRACE_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_cf_waf_drift_cron_alerts.py:78` | code-referenced only |
| `CF_WAF_DRIFT_CRON_LOOP_SLEEP_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_cf_waf_drift_cron_alerts.py:68` | code-referenced only |
| `CF_WAF_DRIFT_CRON_REALERT_INTERVAL_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_cf_waf_drift_cron_alerts.py:64` | code-referenced only |
| `CF_WAF_DRIFT_CRON_SILENT_THRESHOLD_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_cf_waf_drift_cron_alerts.py:60` | code-referenced only |
| `CF_WAF_DRIFT_CRON_WARMUP_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_cf_waf_drift_cron_alerts.py:71` | code-referenced only |
| `CF_WAF_DRIFT_HEARTBEAT_SECRET` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_slack_webhook_missing_alerts.py:449` | code-referenced only |
| `CF_WEB_ANALYTICS_SITE_TAG` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/cf_web_analytics.py:86` | code-referenced only |
| `CF_WEB_ANALYTICS_TOKEN` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:297` | code-referenced only |
| `CF_ZONE_ID` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/cf_cache_rules.py:129` | code-referenced only |
| `CF_ZONE_SETTINGS_TOKEN` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/cf_enterprise.py:41` | code-referenced only |
| `CHAT_CREDIT_RUNWAY_DAYS` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/cost_caps.py:199` | code-referenced only |
| `CHAT_DEFAULT_MODEL` | ⚙️ config | optional | `'openai/gpt-oss-20b',` | ❌ code-only | `artifacts/syrabit-backend/config.py:596` | code-referenced only |
| `CHAT_DEV_FASTPATH` | ⚙️ config | optional | `"1"` | ❌ code-only | `artifacts/syrabit-backend/auth_deps.py:579` | code-referenced only |
| `CHAT_ENHANCE_ENABLED` | ⚙️ config | optional | `'1'` | ❌ code-only | `artifacts/syrabit-backend/config.py:329` | code-referenced only |
| `CHAT_PRIMARY_OVERRIDE` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/cost_caps.py:243` | code-referenced only |
| `CHAT_ROUTER_TOPIC_THRESHOLD` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/chat_router.py:43` | code-referenced only |
| `CHAT_RPM_SOFT_SHED_THRESHOLD` | ⚙️ config | optional | `"0.70"` | ❌ code-only | `artifacts/syrabit-backend/llm.py:1867` | code-referenced only |
| `CI_ALERT_LOOP_SLEEP_S` | ⚙️ config | optional | `"600"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_ci_alerts.py:41` | code-referenced only |
| `CI_ALERT_REALERT_HOURS` | ⚙️ config | optional | `"6"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_ci_alerts.py:43` | code-referenced only |
| `CI_ALERT_WARMUP_S` | ⚙️ config | optional | `"600"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_ci_alerts.py:42` | code-referenced only |
| `CLOUDFLARE_ACCOUNT_ID` | ⚙️ config | required | `''` | ❌ code-only | `artifacts/syrabit-backend/ai_input_cache.py:648` | code-referenced only |
| `CLOUDFLARE_ANALYTICS_TOKEN` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/cf_enterprise.py:43` | code-referenced only |
| `CLOUDFLARE_API_TOKEN` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/ai_input_cache.py:649` | code-referenced only |
| `COMPREHEND_RESCORE_AFTER_DAYS` | ⚙️ config | optional | `"7"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/comprehend_sampler.py:28` | code-referenced only |
| `COMPREHEND_SAMPLE_INTERVAL_S` | ⚙️ config | optional | `"3600"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/comprehend_sampler.py:26` | code-referenced only |
| `COMPREHEND_SAMPLE_SIZE` | ⚙️ config | optional | `"25"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/comprehend_sampler.py:27` | code-referenced only |
| `CONTENT_BATCH_WINDOW_MS` | ⚙️ config | optional | `300` | ❌ code-only | `artifacts/syrabit-backend/llm.py:760` | code-referenced only |
| `CONTENT_RPM_MAX_WAIT` | ⚙️ config | optional | `30` | ❌ code-only | `artifacts/syrabit-backend/llm.py:764` | code-referenced only |
| `CONTENT_WAVE_SIZE` | ⚙️ config | optional | `3` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_pipeline.py:1273` | code-referenced only |
| `COOKIE_DOMAIN` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:973` | code-referenced only |
| `CORS_ORIGINS` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:975` | code-referenced only |
| `CREDIT_APPLICATIONS_PATH` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_vertex.py:65` | code-referenced only |
| `CRON_HEARTBEAT_TTL_DAYS` | ⚙️ config | optional | `"30"` | ❌ code-only | `artifacts/syrabit-backend/cron_heartbeats.py:24` | code-referenced only |
| `CURRICULUM_VERSION` | ⚙️ config | optional | `"v0"` | ❌ code-only | `artifacts/syrabit-backend/grounded_answer.py:600` | code-referenced only |
| `D1_MIRROR_LAG_LOOP_SLEEP_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_d1_mirror_lag_alerts.py:118` | code-referenced only |
| `D1_MIRROR_LAG_REALERT_INTERVAL_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_d1_mirror_lag_alerts.py:111` | code-referenced only |
| `D1_MIRROR_LAG_REQUIRED_STREAK` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_d1_mirror_lag_alerts.py:97` | code-referenced only |
| `D1_MIRROR_LAG_THRESHOLD_S` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_d1_mirror_lag_alerts.py:82` | code-referenced only |
| `D1_MIRROR_LAG_WARMUP_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_d1_mirror_lag_alerts.py:121` | code-referenced only |
| `D1_READ_BASE_URL` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/d1_mirror.py:233` | code-referenced only |
| `D1_READ_SHARED_SECRET` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/d1_mirror.py:237` | code-referenced only |
| `D1_READ_TIMEOUT_S` | ⚙️ config | optional | `"1.5"` | ❌ code-only | `artifacts/syrabit-backend/d1_mirror.py:240` | code-referenced only |
| `D1_SYNC_SECRET` | 🔒 secret | optional | `""` | ✅ secretRef `d1-sync-secret` | `artifacts/syrabit-backend/cloudflare_client.py:1090` | code-referenced + wired |
| `D1_SYNC_SECRET_PREVIEW` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/d1_sync.py:58` | code-referenced only |
| `DATABASE_URL` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/config.py:1035` | code-referenced only |
| `DB_NAME` | ⚙️ config | optional | `'test_database'` | ❌ code-only | `artifacts/syrabit-backend/config.py:71` | code-referenced only |
| `DEEPGRAM_API_KEY` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:514` | code-referenced only |
| `DEEPGRAM_STT_MODEL` | ⚙️ config | optional | `"nova-3"` | ❌ code-only | `artifacts/syrabit-backend/providers/deepgram.py:50` | code-referenced only |
| `DEEPGRAM_TTS_MODEL` | ⚙️ config | optional | `"aura-2-thalia-en"` | ❌ code-only | `artifacts/syrabit-backend/providers/deepgram.py:54` | code-referenced only |
| `DEPLOYMENT_ENV` | ⚙️ config | optional | `"production"` | ❌ code-only | `artifacts/syrabit-backend/observability/sentry_setup.py:222` | code-referenced only |
| `DEVICE_COOKIE_MINTS_PER_MIN` | ⚙️ config | optional | `"5"` | ❌ code-only | `artifacts/syrabit-backend/config.py:1111` | code-referenced only |
| `DISPATCH_SHARED_SECRET` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/d1_mirror.py:238` | code-referenced only |
| `DO_CHAT_BASE_URL` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/do_chat.py:203` | code-referenced only |
| `DO_CHAT_SHARED_SECRET` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/do_chat.py:207` | code-referenced only |
| `DO_CHAT_TIMEOUT_S` | ⚙️ config | optional | `"3.0"` | ❌ code-only | `artifacts/syrabit-backend/do_chat.py:210` | code-referenced only |
| `EDGE_PROXY_DEPLOY_LOOP_SLEEP_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_edge_proxy_deploy_cron_alerts.py:59` | code-referenced only |
| `EDGE_PROXY_DEPLOY_REALERT_INTERVAL_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_edge_proxy_deploy_cron_alerts.py:52` | code-referenced only |
| `EDGE_PROXY_DEPLOY_STALE_THRESHOLD_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_health.py:73` | code-referenced only |
| `EDGE_PROXY_DEPLOY_WARMUP_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_edge_proxy_deploy_cron_alerts.py:62` | code-referenced only |
| `EDGE_PROXY_DEPLOY_WORKFLOW` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_health.py:82` | code-referenced only |
| `EDGE_WORKER_PREVIEW_URL` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/d1_sync.py:57` | code-referenced only |
| `EDGE_WORKER_URL` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/d1_mirror.py:234` | code-referenced only |
| `ELEVENLABS_API_KEY` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:513` | code-referenced only |
| `ELEVENLABS_MODEL_ID` | ⚙️ config | optional | `'eleven_multilingual_v2'` | ❌ code-only | `artifacts/syrabit-backend/config.py:525` | code-referenced only |
| `ELEVENLABS_VOICE_ID` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:524` | code-referenced only |
| `EMAIL_FROM` | ⚙️ config | optional | `"Syrabit.ai <noreply@syrabit.ai>"` | ✅ literal value | `artifacts/syrabit-backend/bulk_email.py:97` | code-referenced + wired |
| `EMBED_BACKFILL_ALERT_FAILED_THRESHOLD` | ⚙️ config | optional | `"50"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:696` | code-referenced only |
| `EMBED_BACKFILL_ALERT_INTERVAL_S` | ⚙️ config | optional | `"300"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:693` | code-referenced only |
| `EMBED_BACKFILL_ALERT_STALL_MINUTES` | ⚙️ config | optional | `"30"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:699` | code-referenced only |
| `EMBED_BACKFILL_AUTOSTART` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:60` | code-referenced only |
| `EMBED_BACKFILL_BATCH_SIZE` | ⚙️ config | optional | `"32"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:50` | code-referenced only |
| `EMBED_BACKFILL_INTERVAL_S` | ⚙️ config | optional | `"900"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:59` | code-referenced only |
| `EMBED_BACKFILL_MAX_RPM` | ⚙️ config | optional | `"600"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:52` | code-referenced only |
| `EMBED_BACKFILL_PER_CALL_LIMIT` | ⚙️ config | optional | `"5000"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:56` | code-referenced only |
| `EMBED_BACKFILL_THROUGHPUT_WINDOW_S` | ⚙️ config | optional | `"3600"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:74` | code-referenced only |
| `EMBED_CACHE_ENABLED` | ⚙️ config | optional | `"1"` | ❌ code-only | `artifacts/syrabit-backend/embed_cache.py:37` | code-referenced only |
| `EMBED_CACHE_TTL_S` | ⚙️ config | optional | `str(24 * 3600` | ❌ code-only | `artifacts/syrabit-backend/embed_cache.py:36` | code-referenced only |
| `EMBED_DEGRADED_MODE` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/embed_degraded_controller.py:157` | code-referenced only |
| `EMBED_DEGRADED_PROBE_WINDOW` | ⚙️ config | optional | `"5"` | ❌ code-only | `artifacts/syrabit-backend/embed_degraded_controller.py:41` | code-referenced only |
| `EMBED_DEGRADED_RESET_STREAK` | ⚙️ config | optional | `"5"` | ❌ code-only | `artifacts/syrabit-backend/embed_degraded_controller.py:44` | code-referenced only |
| `EMBED_DEGRADED_TRIP_FAILURES` | ⚙️ config | optional | `"3"` | ❌ code-only | `artifacts/syrabit-backend/embed_degraded_controller.py:42` | code-referenced only |
| `EMBED_DEGRADED_TRIP_P95_MS` | ⚙️ config | optional | `"2000"` | ❌ code-only | `artifacts/syrabit-backend/embed_degraded_controller.py:43` | code-referenced only |
| `EMBED_INDIC_PROVIDER` | ⚙️ config | optional | `'cohere_multilingual_v3_bedrock'` | ❌ code-only | `artifacts/syrabit-backend/config.py:557` | code-referenced only |
| `EMBED_MAX_CONCURRENT` | ⚙️ config | optional | `"16"` | ❌ code-only | `artifacts/syrabit-backend/vertex_services.py:229` | code-referenced only |
| `EMBED_PROVIDER_PRIMARY` | ⚙️ config | optional | `'workers_ai_custom'` | ✅ literal value | `artifacts/syrabit-backend/config.py:541` | code-referenced + wired |
| `ENABLE_E2E_ADMIN` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_auth_users.py:63` | code-referenced only |
| `ENABLE_LLM_SAFETY_CHECK` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/guardrails/prompt_safety.py:128` | code-referenced only |
| `ENABLE_PARALLEL_LLM_RACE` | ⚙️ config | optional | `'true'` | ❌ code-only | `artifacts/syrabit-backend/config.py:738` | code-referenced only |
| `ENTITY_SEO_CRUNCHBASE_PERMALINK` | ⚙️ config | optional | `"syrabit-ai"` | ❌ code-only | `artifacts/syrabit-backend/entity_seo_health.py:82` | code-referenced only |
| `ENTITY_SEO_WIKIDATA_QID` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/entity_seo_health.py:79` | code-referenced only |
| `ENTITY_SEO_WIKIPEDIA_TITLE` | ⚙️ config | optional | `"Syrabit.ai"` | ❌ code-only | `artifacts/syrabit-backend/entity_seo_health.py:80` | code-referenced only |
| `ENV` | ⚙️ config | optional | `production` (deploy) | ✅ literal value | `artifacts/syrabit-backend/chat_turn_context.py:112` | code-referenced + wired |
| `ENVIRONMENT` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/chat_turn_context.py:112` | code-referenced only |
| `EXAM_CALENDAR_PATH` | ⚙️ config | optional | `str(Path(__file__` | ❌ code-only | `artifacts/syrabit-backend/cache_calendar.py:109` | code-referenced only |
| `EXA_API_KEY` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:520` | code-referenced only |
| `FRONTEND_URL` | ⚙️ config | optional | `'https://syrabit.ai'` | ❌ code-only | `artifacts/syrabit-backend/config.py:151` | code-referenced only |
| `GCP_CREDITS_REMAINING_USD` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/cost_caps.py:208` | code-referenced only |
| `GCP_DISCOVERY_COLLECTION` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/discovery_engine_client.py:45` | code-referenced only |
| `GCP_DISCOVERY_DATA_STORE` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/discovery_engine_client.py:41` | code-referenced only |
| `GCP_DISCOVERY_LOCATION` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/discovery_engine_client.py:44` | code-referenced only |
| `GCP_DISCOVERY_SERVING_CONFIG` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/discovery_engine_client.py:47` | code-referenced only |
| `GCP_OIDC_ALLOWED_EMAILS` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/oidc_auth.py:44` | code-referenced only |
| `GCP_OIDC_DEV_BYPASS` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/oidc_auth.py:68` | code-referenced only |
| `GCP_OIDC_DEV_SECRET` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/oidc_auth.py:69` | code-referenced only |
| `GCP_OIDC_REQUIRED_AUDIENCE` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/oidc_auth.py:81` | code-referenced only |
| `GCP_PROJECT_ID` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:687` | code-referenced only |
| `GCP_SCHEDULER_TAKEOVER` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/internal_jobs.py:45` | code-referenced only |
| `GITHUB_CI_BRANCH` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_ci_status.py:49` | code-referenced only |
| `GITHUB_CI_WORKFLOW` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_ci_status.py:48` | code-referenced only |
| `GITHUB_REPO` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_ci_status.py:46` | code-referenced only |
| `GITHUB_TOKEN` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_ci_status.py:47` | code-referenced only |
| `GLACIER_ARCHIVE_BUCKETS` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_archive.py:50` | code-referenced only |
| `GOOGLE_APPLICATION_CREDENTIALS` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/gcp_auth.py:39` | code-referenced only |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:680` | code-referenced only |
| `GOOGLE_BILLING_ACCOUNT_ID` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:703` | code-referenced only |
| `GOOGLE_BILLING_ALERT` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:693` | code-referenced only |
| `GOOGLE_BILLING_BIGQUERY_DATASET` | ⚙️ config | optional | `'billing_export'` | ❌ code-only | `artifacts/syrabit-backend/config.py:713` | code-referenced only |
| `GOOGLE_BILLING_BIGQUERY_LOCATION` | ⚙️ config | optional | `'US'` | ❌ code-only | `artifacts/syrabit-backend/config.py:728` | code-referenced only |
| `GOOGLE_BILLING_BIGQUERY_PROJECT` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:710` | code-referenced only |
| `GOOGLE_BILLING_BIGQUERY_TABLE` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:721` | code-referenced only |
| `GOOGLE_BOOKS_API_KEY` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/books_client.py:28` | code-referenced only |
| `GOOGLE_CLOUD_PROJECT` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:686` | code-referenced only |
| `GOOGLE_FACT_CHECK_API_KEY` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/fact_check_client.py:29` | code-referenced only |
| `GOOGLE_INDEXING_DAILY_LIMIT` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/google_indexing_client.py:77` | code-referenced only |
| `GOOGLE_INDEXING_ENABLED` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/google_indexing_client.py:90` | code-referenced only |
| `GOOGLE_INDEXING_PERSIST_DISABLED` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/google_indexing_client.py:1022` | code-referenced only |
| `GOOGLE_INDEXING_PERSIST_IN_TESTS` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/google_indexing_client.py:1026` | code-referenced only |
| `GOOGLE_INDEXING_QUOTA_ALERT_DISABLED` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/google_indexing_client.py:608` | code-referenced only |
| `GOOGLE_INDEXING_QUOTA_ALERT_IN_TESTS` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/google_indexing_client.py:612` | code-referenced only |
| `GOOGLE_INDEXING_SERVICE_ACCOUNT` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/google_indexing_client.py:263` | code-referenced only |
| `GOOGLE_INDEXING_TIER1_SUBJECTS` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/google_indexing_client.py:131` | code-referenced only |
| `GOOGLE_KG_API_KEY` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/books_client.py:29` | code-referenced only |
| `GOOGLE_NLP_API_KEY` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/nlp_client.py:32` | code-referenced only |
| `GOOGLE_OAUTH_CLIENT_ID` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:143` | code-referenced only |
| `GOOGLE_OAUTH_CLIENT_SECRET` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:144` | code-referenced only |
| `GOOGLE_PAGESPEED_API_KEY` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/pagespeed_service.py:35` | code-referenced only |
| `GOOGLE_RR_API_KEY` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/seo_baseline.py:163` | code-referenced only |
| `GOOGLE_WEB_RISK_API_KEY` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/web_risk_client.py:29` | code-referenced only |
| `GROUNDED_RECALL_LEAK_GATE` | ⚙️ config | optional | `"0.5"` | ❌ code-only | `artifacts/syrabit-backend/bench/grounded_recall.py:652` | code-referenced only |
| `GROUNDED_RECALL_NIGHTLY_ENABLED` | ⚙️ config | optional | `"true"` | ❌ code-only | `artifacts/syrabit-backend/bench/grounded_recall.py:522` | code-referenced only |
| `GROUNDED_RECALL_NIGHTLY_GATE` | ⚙️ config | optional | `str(_GROUNDED_RECALL_DEFAULT_GATE` | ❌ code-only | `artifacts/syrabit-backend/bench/grounded_recall.py:536` | code-referenced only |
| `GROUNDED_RECALL_NIGHTLY_HOUR_UTC` | ⚙️ config | optional | `str(_GROUNDED_RECALL_DEFAULT_HOUR_UTC` | ❌ code-only | `artifacts/syrabit-backend/bench/grounded_recall.py:528` | code-referenced only |
| `GSC_LOOKBACK_DAYS` | ⚙️ config | optional | `fetch_kwargs.pop("lookback_days", 7` | ❌ code-only | `artifacts/syrabit-backend/gsc_search_console_client.py:205` | code-referenced only |
| `GSC_NEAR_MISS_MAX_POS` | ⚙️ config | optional | `fetch_kwargs.pop("max_pos", 20` | ❌ code-only | `artifacts/syrabit-backend/gsc_search_console_client.py:209` | code-referenced only |
| `GSC_NEAR_MISS_MIN_POS` | ⚙️ config | optional | `fetch_kwargs.pop("min_pos", 11` | ❌ code-only | `artifacts/syrabit-backend/gsc_search_console_client.py:207` | code-referenced only |
| `GSC_ROW_LIMIT` | ⚙️ config | optional | `fetch_kwargs.pop("row_limit", 5000` | ❌ code-only | `artifacts/syrabit-backend/gsc_search_console_client.py:211` | code-referenced only |
| `GSC_SITE_URL` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/gsc_search_console_client.py:202` | code-referenced only |
| `GUNICORN_THREADS` | ⚙️ config | optional | `"4"` | ❌ code-only | `artifacts/syrabit-backend/gunicorn.conf.py:6` | code-referenced only |
| `GUNICORN_WORKERS` | ⚙️ config | optional | `"3"` | ❌ code-only | `artifacts/syrabit-backend/gunicorn.conf.py:4` | code-referenced only |
| `HEALTH_SNAPSHOT_PROBE_TIMEOUT_S` | ⚙️ config | optional | `"4"` | ❌ code-only | `artifacts/syrabit-backend/health_snapshot_cache.py:62` | code-referenced only |
| `HEALTH_SNAPSHOT_TTL_S` | ⚙️ config | optional | `"7"` | ❌ code-only | `artifacts/syrabit-backend/health_snapshot_cache.py:57` | code-referenced only |
| `HOSTNAME` | ⚙️ config | optional | `"unknown"` | ❌ code-only | `artifacts/syrabit-backend/background_lease.py:121` | code-referenced only |
| `INDEXNOW_ENABLED` | ⚙️ config | optional | `"1"` | ❌ code-only | `artifacts/syrabit-backend/routes/bot_discovery.py:36` | code-referenced only |
| `INDEXNOW_KEY` | 🔒 secret | optional | `hashlib.sha256(b"syrabit-indexnow-2026"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_advanced.py:3745` | code-referenced only |
| `IP_COARSE_DAILY_CAP` | ⚙️ config | optional | `"1500"` | ❌ code-only | `artifacts/syrabit-backend/config.py:1094` | code-referenced only |
| `IP_HASH_SALT` | ⚙️ config | optional | `"syrabit-ss-tracking-2026"` | ❌ code-only | `artifacts/syrabit-backend/middleware.py:758` | code-referenced only |
| `JWT_ACCESS_EXPIRE_MINUTES` | ⚙️ config | optional | `'60'` | ❌ code-only | `artifacts/syrabit-backend/config.py:128` | code-referenced only |
| `JWT_REFRESH_EXPIRE_MINUTES` | ⚙️ config | optional | `str(60 * 24 * 30` | ❌ code-only | `artifacts/syrabit-backend/config.py:129` | code-referenced only |
| `JWT_SECRET` | 🔒 secret | — | — | ✅ secretRef `jwt-secret` | `infra/azure/aca-syrabit-backend.bicep:169` (deploy) | wired but no code reference found (deploy-time only) |
| `KID_SAFE_EXTRA_PATTERNS` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/guardrails/web_safety.py:55` | code-referenced only |
| `KID_SAFE_THRESHOLD` | ⚙️ config | optional | `"1.5"` | ❌ code-only | `artifacts/syrabit-backend/guardrails/web_safety.py:32` | code-referenced only |
| `KV_ALERT_SECRET` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_kv_health.py:68` | code-referenced only |
| `LLM_BATCH_WINDOW_MS` | ⚙️ config | optional | `5` | ❌ code-only | `artifacts/syrabit-backend/llm.py:759` | code-referenced only |
| `LLM_MAX_CONCURRENT` | ⚙️ config | optional | `200` | ❌ code-only | `artifacts/syrabit-backend/llm.py:120` | code-referenced only |
| `LLM_MODEL` | ⚙️ config | optional | `'@cf/meta/llama-3.3-70b-instruct-fp8-fast'` | ❌ code-only | `artifacts/syrabit-backend/config.py:746` | code-referenced only |
| `LLM_PRIMARY_PROVIDER` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:627` | code-referenced only |
| `LLM_PROVIDER` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:628` | code-referenced only |
| `LOGS_INGEST_MAX_BATCH` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/unified_logs_dao.py:74` | code-referenced only |
| `LOGS_PAUSED` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/unified_logs_dao.py:731` | code-referenced only |
| `LOG_INGEST_TOKEN` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_logs.py:131` | code-referenced only |
| `LOG_LEVEL` | ⚙️ config | optional | `"warning"` | ❌ code-only | `artifacts/syrabit-backend/gunicorn.conf.py:14` | code-referenced only |
| `LOG_RETENTION_DAYS` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/unified_logs_dao.py:98` | code-referenced only |
| `MATERIALIZE_FAQ_INTERVAL_S` | ⚙️ config | optional | `"86400"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/materialize_chapter_faqs.py:874` | code-referenced only |
| `MAX_CONCURRENT_RACE_PROVIDERS` | ⚙️ config | optional | `'3'` | ❌ code-only | `artifacts/syrabit-backend/config.py:741` | code-referenced only |
| `MAX_DOCS_PER_RUN` | ⚙️ config | optional | `"0"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/materialize_chapter_faqs.py:803` | code-referenced only |
| `MEMORYSTORE_REDIS_URL` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:911` | code-referenced only |
| `MEMORY_BRAIN_CHAT_ENABLED` | ⚙️ config | optional | `"1"` | ❌ code-only | `artifacts/syrabit-backend/memory_brain_chat.py:56` | code-referenced only |
| `MEMORY_BRAIN_COLLECTION` | ⚙️ config | optional | `'memory_brain'` | ❌ code-only | `artifacts/syrabit-backend/config.py:574` | code-referenced only |
| `MEMORY_BRAIN_DIMS` | ⚙️ config | optional | `"1024"` | ❌ code-only | `artifacts/syrabit-backend/providers/memory_brain.py:57` | code-referenced only |
| `MEMORY_BRAIN_ENSURE_INDEX` | ⚙️ config | optional | `"1"` | ❌ code-only | `artifacts/syrabit-backend/server.py:807` | code-referenced only |
| `MEMORY_BRAIN_FILTER_FIELDS` | ⚙️ config | optional | `"user_id,kind",` | ❌ code-only | `artifacts/syrabit-backend/providers/memory_brain.py:64` | code-referenced only |
| `MEMORY_BRAIN_FLEET_ROLLUP` | ⚙️ config | optional | `"1"` | ❌ code-only | `artifacts/syrabit-backend/memory_brain_metrics.py:203` | code-referenced only |
| `MEMORY_BRAIN_INDEX_NAME` | ⚙️ config | optional | `"memory_brain_vector_index"` | ❌ code-only | `artifacts/syrabit-backend/providers/memory_brain.py:56` | code-referenced only |
| `MEMORY_BRAIN_METRIC` | ⚙️ config | optional | `"cosine"` | ❌ code-only | `artifacts/syrabit-backend/providers/memory_brain.py:58` | code-referenced only |
| `MEMORY_BRAIN_PROVIDER` | ⚙️ config | optional | `'workers_ai_custom'` | ❌ code-only | `artifacts/syrabit-backend/config.py:571` | code-referenced only |
| `MEMORY_BRAIN_QUERY_MIN_SCORE` | ⚙️ config | optional | `"0.55"` | ❌ code-only | `artifacts/syrabit-backend/memory_brain_chat.py:71` | code-referenced only |
| `MEMORY_BRAIN_QUERY_TIMEOUT_S` | ⚙️ config | optional | `"0.6"` | ❌ code-only | `artifacts/syrabit-backend/memory_brain_chat.py:61` | code-referenced only |
| `MEMORY_BRAIN_QUERY_TOP_K` | ⚙️ config | optional | `"3"` | ❌ code-only | `artifacts/syrabit-backend/memory_brain_chat.py:64` | code-referenced only |
| `MEMORY_BRAIN_WORKER_STALE_SECONDS` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/memory_brain_metrics.py:124` | code-referenced only |
| `MIN_PROVIDERS_TO_RACE` | ⚙️ config | optional | `'2'` | ❌ code-only | `artifacts/syrabit-backend/config.py:740` | code-referenced only |
| `MONGODB_URI` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/config.py:70` | code-referenced only |
| `MONGO_URL` | 🔒 secret | optional | `""` | ✅ secretRef `mongo-uri` | `artifacts/syrabit-backend/config.py:70` | code-referenced + wired |
| `MONITORED_URLS_PATH` | ⚙️ config | optional | `str(Path(__file__` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_cache.py:56` | code-referenced only |
| `MONTHLY_TOTAL_USD_CAP` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/cost_caps.py:115` | code-referenced only |
| `OBSERVABILITY_DIGEST_TO` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_health.py:1464` | code-referenced only |
| `OCR_IP_DAILY_CAP` | ⚙️ config | optional | `"5000"` | ❌ code-only | `artifacts/syrabit-backend/auth_deps.py:823` | code-referenced only |
| `ORIGIN_SHARED_SECRET` | 🔒 secret | optional | `""` | ✅ secretRef `origin-shared-secret` | `artifacts/syrabit-backend/middleware.py:80` | code-referenced + wired |
| `ORIGIN_SHARED_SECRET_HEADER` | 🔒 secret | optional | `"X-Origin-Auth"` | ❌ code-only | `artifacts/syrabit-backend/middleware.py:81` | code-referenced only |
| `OTEL_EXPORTER_GCP_PROJECT_ID` | ⚙️ config | optional | `syrabit-prod` (deploy) | ✅ literal value | `artifacts/syrabit-backend/tracing.py:243` | code-referenced + wired |
| `OTEL_SERVICE_NAME` | ⚙️ config | optional | `"syrabit-backend-do"` | ✅ literal value | `artifacts/syrabit-backend/healthz.py:229` | code-referenced + wired |
| `OTEL_SERVICE_NAMESPACE` | ⚙️ config | optional | `"syrabit"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_observability_canary.py:63` | code-referenced only |
| `OTEL_SERVICE_VERSION` | ⚙️ config | optional | `"2.0.0"` | ❌ code-only | `artifacts/syrabit-backend/healthz.py:230` | code-referenced only |
| `OTEL_TRACES_EXPORTER` | ⚙️ config | — | `googlecloud` (deploy) | ✅ literal value | `infra/azure/aca-syrabit-backend.bicep:201` (deploy) | wired but no code reference found (deploy-time only) |
| `PAGES_SSR_ENABLED` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_cf_health.py:147` | code-referenced only |
| `PARALLEL_RACE_TIMEOUT` | ⚙️ config | optional | `'8.0'` | ❌ code-only | `artifacts/syrabit-backend/config.py:739` | code-referenced only |
| `PINECONE_API_KEY` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/providers/pinecone_ai.py:40` | code-referenced only |
| `PINECONE_EMBED_MODEL` | ⚙️ config | optional | `"multilingual-e5-large"` | ❌ code-only | `artifacts/syrabit-backend/providers/pinecone_ai.py:41` | code-referenced only |
| `PINECONE_INDEX` | ⚙️ config | optional | `"syrabit-ahsec"` | ❌ code-only | `artifacts/syrabit-backend/retrievers/pinecone_vector.py:50` | code-referenced only |
| `PINECONE_INDEX_DIMS` | ⚙️ config | optional | `"1024"` | ❌ code-only | `artifacts/syrabit-backend/retrievers/pinecone_vector.py:51` | code-referenced only |
| `PINECONE_INDEX_METRIC` | ⚙️ config | optional | `"cosine"` | ❌ code-only | `artifacts/syrabit-backend/retrievers/pinecone_vector.py:52` | code-referenced only |
| `PINECONE_KEY` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/retrievers/pinecone_vector.py:47` | code-referenced only |
| `PINECONE_NAMESPACE` | ⚙️ config | optional | `"cached_gemma_today"` | ❌ code-only | `artifacts/syrabit-backend/llm.py:4172` | code-referenced only |
| `PINECONE_RERANK_MODEL` | ⚙️ config | optional | `"bge-reranker-v2-m3"` | ❌ code-only | `artifacts/syrabit-backend/providers/pinecone_ai.py:42` | code-referenced only |
| `PINECONE_SKIP_MONGO_EMBED` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/providers/chunk_embedder.py:364` | code-referenced only |
| `PINECONE_TIMEOUT_S` | ⚙️ config | optional | `"12"` | ❌ code-only | `artifacts/syrabit-backend/providers/pinecone_ai.py:43` | code-referenced only |
| `PINECONE_WRITE` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/providers/chunk_embedder.py:358` | code-referenced only |
| `PIPELINE_LLM_CONCURRENCY` | ⚙️ config | optional | `4` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_pipeline.py:1271` | code-referenced only |
| `PIPELINE_QUIZ_PREGEN_CONCURRENCY` | ⚙️ config | optional | `2` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_pipeline.py:1282` | code-referenced only |
| `PORT` | ⚙️ config | optional | `"5000"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_advanced.py:3208` | code-referenced only |
| `POSTHOG_API_KEY` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/providers/posthog.py:55` | code-referenced only |
| `POSTHOG_HOST` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/providers/posthog.py:56` | code-referenced only |
| `PREWARM_CONCURRENCY` | ⚙️ config | optional | `"32"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/prewarm_seo_routes.py:84` | code-referenced only |
| `PREWARM_EXAM_LOOKAHEAD_DAYS` | ⚙️ config | optional | `"30"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/prewarm_seo_routes.py:86` | code-referenced only |
| `PREWARM_HTTP_TIMEOUT_S` | ⚙️ config | optional | `"10"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/prewarm_seo_routes.py:85` | code-referenced only |
| `PREWARM_INTERVAL_S` | ⚙️ config | optional | `"86400"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/prewarm_seo_routes.py:786` | code-referenced only |
| `PREWARM_TOP_N` | ⚙️ config | optional | `"5000"` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/prewarm_seo_routes.py:83` | code-referenced only |
| `PRODUCTION_ORIGINS` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:1000` | code-referenced only |
| `PUBLIC_BASE_URL` | ⚙️ config | optional | `"https://syrabit.ai",` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/prewarm_seo_routes.py:92` | code-referenced only |
| `PUBLIC_ORIGIN` | ⚙️ config | optional | `"https://syrabit.ai"` | ❌ code-only | `artifacts/syrabit-backend/cloudflare_client.py:1143` | code-referenced only |
| `PYTEST_CURRENT_TEST` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/google_indexing_client.py:93` | code-referenced only |
| `R2_ACCESS_KEY_ID` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:251` | code-referenced only |
| `R2_BUCKET_NAME` | ⚙️ config | optional | `'syrabit-media'` | ❌ code-only | `artifacts/syrabit-backend/config.py:253` | code-referenced only |
| `R2_ENDPOINT_URL` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:258` | code-referenced only |
| `R2_PUBLIC_URL` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:254` | code-referenced only |
| `R2_SECRET_ACCESS_KEY` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:252` | code-referenced only |
| `RAG_CACHE_ENABLED` | ⚙️ config | optional | `"1"` | ❌ code-only | `artifacts/syrabit-backend/rag_cache.py:41` | code-referenced only |
| `RAG_CACHE_TTL_S` | ⚙️ config | optional | `str(6 * 3600` | ❌ code-only | `artifacts/syrabit-backend/rag_cache.py:40` | code-referenced only |
| `RAG_EMBEDDING_PROVIDER_FORCE` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/llm.py:4107` | code-referenced only |
| `RAG_RETRIEVER` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/retrievers/factory.py:116` | code-referenced only |
| `RAILWAY_ENVIRONMENT_NAME` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/vectorize_client.py:82` | code-referenced only |
| `RAILWAY_LOGS_URL` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/vectorize_client.py:77` | code-referenced only |
| `RAILWAY_PROJECT_NAME` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/vectorize_client.py:80` | code-referenced only |
| `RAILWAY_SERVICE_NAME` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/vectorize_client.py:81` | code-referenced only |
| `RAZORPAY_KEY_ID` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/health_snapshot_cache.py:291` | code-referenced only |
| `RAZORPAY_KEY_SECRET` | 🔒 secret | optional | `""` | ✅ secretRef `razorpay-key-secret` | `artifacts/syrabit-backend/health_snapshot_cache.py:292` | code-referenced + wired |
| `RAZORPAY_WEBHOOK_SECRET` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_monetization.py:118` | code-referenced only |
| `REDIS_AI_CACHE_CONNECT_TIMEOUT_MS` | ⚙️ config | optional | `'300'` | ❌ code-only | `artifacts/syrabit-backend/config.py:924` | code-referenced only |
| `REDIS_AI_CACHE_MAX_ENTRY_BYTES` | ⚙️ config | optional | `str(128 * 1024` | ❌ code-only | `artifacts/syrabit-backend/config.py:922` | code-referenced only |
| `REDIS_AI_CACHE_NAMESPACE` | ⚙️ config | optional | `'ai_cache'` | ❌ code-only | `artifacts/syrabit-backend/config.py:920` | code-referenced only |
| `REDIS_AI_CACHE_OP_TIMEOUT_MS` | ⚙️ config | optional | `'200'` | ❌ code-only | `artifacts/syrabit-backend/config.py:925` | code-referenced only |
| `REDIS_AI_CACHE_TTL` | ⚙️ config | optional | `'3600'` | ❌ code-only | `artifacts/syrabit-backend/cache.py:231` | code-referenced only |
| `REDIS_CASUAL_CACHE_TTL` | ⚙️ config | optional | `'300'` | ❌ code-only | `artifacts/syrabit-backend/cache.py:232` | code-referenced only |
| `REDIS_CHAT_CACHE_TTL` | ⚙️ config | optional | `'600'` | ❌ code-only | `artifacts/syrabit-backend/cache.py:233` | code-referenced only |
| `REDIS_GET_CACHE_TTL_MS` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/deps.py:166` | code-referenced only |
| `REPLIT_DEPLOYMENT` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/server.py:2165` | code-referenced only |
| `REPLIT_DEV_DOMAIN` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/deps.py:172` | code-referenced only |
| `REPLIT_DOMAINS` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:978` | code-referenced only |
| `REPL_ID` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/auth_deps.py:578` | code-referenced only |
| `RERANK_PROVIDER` | ⚙️ config | optional | `'pinecone_only'` | ❌ code-only | `artifacts/syrabit-backend/config.py:568` | code-referenced only |
| `RUN_LEGACY_LOOPS` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/server.py:2116` | code-referenced only |
| `S3_FINALS_BUCKET` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_archive.py:66` | code-referenced only |
| `SARVAM_API_KEY` | 🔒 secret | optional | `''` | ✅ secretRef `sarvam-api-key` | `artifacts/syrabit-backend/config.py:502` | code-referenced + wired |
| `SARVAM_API_KEY_2` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:503` | code-referenced only |
| `SARVAM_API_KEY_3` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:504` | code-referenced only |
| `SARVAM_PER_USER_MONTHLY_CAP` | ⚙️ config | optional | `"30"` | ❌ code-only | `artifacts/syrabit-backend/cost_caps.py:792` | code-referenced only |
| `SECURE_COOKIES` | ⚙️ config | optional | `'true'` | ❌ code-only | `artifacts/syrabit-backend/config.py:971` | code-referenced only |
| `SENTRY_AUTH_TOKEN` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_billing.py:568` | code-referenced only |
| `SENTRY_DSN` | 🔒 secret | optional | — | ✅ secretRef `sentry-dsn` | `artifacts/syrabit-backend/observability/sentry_setup.py:195` | code-referenced + wired |
| `SENTRY_ENVIRONMENT` | ⚙️ config | optional | `production` (deploy) | ✅ literal value | `artifacts/syrabit-backend/observability/sentry_setup.py:221` | code-referenced + wired |
| `SENTRY_ERRORS_LIMIT` | ⚙️ config | optional | `"50000"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_credits.py:1088` | code-referenced only |
| `SENTRY_ERRORS_USED_MTD` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_credits.py:1101` | code-referenced only |
| `SENTRY_ORG` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_billing.py:569` | code-referenced only |
| `SENTRY_PLAN` | ⚙️ config | optional | `"Team"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_credits.py:1089` | code-referenced only |
| `SENTRY_PROJECT_ID` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_health.py:1256` | code-referenced only |
| `SENTRY_RELEASE` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/observability/sentry_setup.py:226` | code-referenced only |
| `SEO_ALERT_DEEP_SCAN_MAX_SITEMAPS` | ⚙️ config | optional | `"3"` | ❌ code-only | `artifacts/syrabit-backend/routes/bot_discovery.py:3940` | code-referenced only |
| `SEO_AUTO_PUBLISH_ENABLED` | ⚙️ config | optional | `"true"` | ❌ code-only | `artifacts/syrabit-backend/seo_engine.py:6976` | code-referenced only |
| `SEO_AUTO_PUBLISH_FREQUENCY` | ⚙️ config | optional | `"daily"` | ❌ code-only | `artifacts/syrabit-backend/seo_engine.py:6982` | code-referenced only |
| `SEO_AUTO_PUBLISH_HOUR_UTC` | ⚙️ config | optional | `"2"` | ❌ code-only | `artifacts/syrabit-backend/seo_engine.py:6989` | code-referenced only |
| `SEO_AUTO_PUBLISH_PAGE_TYPES` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/seo_engine.py:7007` | code-referenced only |
| `SEO_AUTO_PUBLISH_WEEKDAY` | ⚙️ config | optional | `"0"` | ❌ code-only | `artifacts/syrabit-backend/seo_engine.py:6999` | code-referenced only |
| `SEO_FANOUT_ENABLED` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/seo_fanout.py:53` | code-referenced only |
| `SEO_LINKER_AUTO_PER_DAY` | ⚙️ config | optional | `"100"` | ❌ code-only | `artifacts/syrabit-backend/seo_internal_linker.py:110` | code-referenced only |
| `SEO_LINKER_AUTO_THRESHOLD` | ⚙️ config | optional | `"0.75"` | ❌ code-only | `artifacts/syrabit-backend/seo_internal_linker.py:103` | code-referenced only |
| `SEO_LINKER_ENABLED` | ⚙️ config | optional | `"1"` | ❌ code-only | `artifacts/syrabit-backend/seo_internal_linker.py:116` | code-referenced only |
| `SEO_LINKER_MAX_PER_TARGET` | ⚙️ config | optional | `"5"` | ❌ code-only | `artifacts/syrabit-backend/seo_internal_linker.py:106` | code-referenced only |
| `SEO_LINKER_MIN_PER_TARGET` | ⚙️ config | optional | `"3"` | ❌ code-only | `artifacts/syrabit-backend/seo_internal_linker.py:105` | code-referenced only |
| `SEO_LINKER_NIGHTLY_IDLE_SECS` | ⚙️ config | optional | `"3600"` | ❌ code-only | `artifacts/syrabit-backend/seo_internal_linker.py:114` | code-referenced only |
| `SEO_LINKER_NIGHTLY_TOP_N` | ⚙️ config | optional | `"50"` | ❌ code-only | `artifacts/syrabit-backend/seo_internal_linker.py:112` | code-referenced only |
| `SEO_LINKER_POOL_SIZE` | ⚙️ config | optional | `"30"` | ❌ code-only | `artifacts/syrabit-backend/seo_internal_linker.py:108` | code-referenced only |
| `SEO_REMEDIATION_AUTOPUBLISH_PER_DAY` | ⚙️ config | optional | `"5"` | ❌ code-only | `artifacts/syrabit-backend/seo_remediation_service.py:110` | code-referenced only |
| `SEO_REMEDIATION_CIRCUIT_COOLDOWN_H` | ⚙️ config | optional | `"24"` | ❌ code-only | `artifacts/syrabit-backend/seo_remediation_service.py:121` | code-referenced only |
| `SEO_REMEDIATION_CIRCUIT_RATIO` | ⚙️ config | optional | `"0.5"` | ❌ code-only | `artifacts/syrabit-backend/seo_remediation_service.py:120` | code-referenced only |
| `SEO_REMEDIATION_CIRCUIT_WINDOW` | ⚙️ config | optional | `"10"` | ❌ code-only | `artifacts/syrabit-backend/seo_remediation_service.py:119` | code-referenced only |
| `SEO_REMEDIATION_DRAFT_PER_DAY` | ⚙️ config | optional | `"20"` | ❌ code-only | `artifacts/syrabit-backend/seo_remediation_service.py:111` | code-referenced only |
| `SEO_REMEDIATION_ENABLED` | ⚙️ config | optional | `"1"` | ❌ code-only | `artifacts/syrabit-backend/seo_remediation_service.py:130` | code-referenced only |
| `SEO_REMEDIATION_FANOUT_CAP` | ⚙️ config | optional | `"5"` | ❌ code-only | `artifacts/syrabit-backend/seo_remediation_service.py:125` | code-referenced only |
| `SEO_REMEDIATION_IDLE_BACKOFF` | ⚙️ config | optional | `"5"` | ❌ code-only | `artifacts/syrabit-backend/seo_remediation_service.py:127` | code-referenced only |
| `SEO_REMEDIATION_MIN_DELTA` | ⚙️ config | optional | `"2"` | ❌ code-only | `artifacts/syrabit-backend/seo_remediation_service.py:116` | code-referenced only |
| `SESSION_FALLBACK_HERD_PCT` | ⚙️ config | optional | `"0.05"` | ❌ code-only | `artifacts/syrabit-backend/session_fallback.py:53` | code-referenced only |
| `SESSION_FALLBACK_K` | ⚙️ config | optional | `"3"` | ❌ code-only | `artifacts/syrabit-backend/session_fallback.py:49` | code-referenced only |
| `SESSION_FALLBACK_PROVIDER` | ⚙️ config | optional | `"vertex"` | ❌ code-only | `artifacts/syrabit-backend/session_fallback.py:51` | code-referenced only |
| `SESSION_FALLBACK_TTFB_MS` | ⚙️ config | optional | `"2400"` | ❌ code-only | `artifacts/syrabit-backend/session_fallback.py:50` | code-referenced only |
| `SESSION_FALLBACK_TTL_S` | ⚙️ config | optional | `"7200"` | ❌ code-only | `artifacts/syrabit-backend/session_fallback.py:52` | code-referenced only |
| `SESSION_TTFB_KEY_TTL_S` | 🔒 secret | optional | `str(24 * 3600` | ❌ code-only | `artifacts/syrabit-backend/session_fallback.py:54` | code-referenced only |
| `SES_REGION` | ⚙️ config | optional | `""` | ✅ literal value | `artifacts/syrabit-backend/email_templates.py:75` | code-referenced + wired |
| `SLACK_WEBHOOK_DEFAULT_CHANNEL` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/slack_notifier.py:49` | code-referenced only |
| `SLACK_WEBHOOK_MISSING_BOOTSTRAP_GRACE_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_slack_webhook_missing_alerts.py:151` | code-referenced only |
| `SLACK_WEBHOOK_MISSING_LEASE_TTL_CEILING_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_slack_webhook_missing_alerts.py:181` | code-referenced only |
| `SLACK_WEBHOOK_MISSING_LOOP_SLEEP_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_slack_webhook_missing_alerts.py:168` | code-referenced only |
| `SLACK_WEBHOOK_MISSING_REALERT_INTERVAL_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_slack_webhook_missing_alerts.py:158` | code-referenced only |
| `SLACK_WEBHOOK_MISSING_SNOOZE_MAX_HOURS` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_slack_webhook_missing_alerts.py:199` | code-referenced only |
| `SLACK_WEBHOOK_MISSING_SNOOZE_MIN_HOURS` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_slack_webhook_missing_alerts.py:196` | code-referenced only |
| `SLACK_WEBHOOK_MISSING_WARMUP_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_slack_webhook_missing_alerts.py:171` | code-referenced only |
| `SLACK_WEBHOOK_URL` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/slack_notifier.py:31` | code-referenced only |
| `SLOW_QUERY_THRESHOLD_MS` | ⚙️ config | optional | `"200"` | ❌ code-only | `artifacts/syrabit-backend/config.py:928` | code-referenced only |
| `SQS_QUEUE_URL_SSM_PARAM` | ⚙️ config | optional | `"/syrabit/prod/sqs-worker-queue-urls",` | ❌ code-only | `artifacts/syrabit-backend/sqs_fanout.py:61` | code-referenced only |
| `SSR_PROBE_URLS` | ⚙️ config | optional | `",".join([ "https://syrabit.ai/", "https://syrabit.ai/about…` | ❌ code-only | `artifacts/syrabit-backend/cf_ssr_health.py:72` | code-referenced only |
| `SUPABASE_ANON_KEY` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/supabase_auth_canary.py:76` | code-referenced only |
| `SUPABASE_CANARY_EMAIL` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/supabase_auth_canary.py:65` | code-referenced only |
| `SUPABASE_CANARY_PASSWORD` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/supabase_auth_canary.py:66` | code-referenced only |
| `SUPABASE_DB_URL` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:965` | code-referenced only |
| `SUPABASE_JWKS_HTTP_TIMEOUT_S` | ⚙️ config | optional | `"5"` | ❌ code-only | `artifacts/syrabit-backend/supabase_jwks.py:80` | code-referenced only |
| `SUPABASE_JWKS_STALE_GRACE_SECONDS` | ⚙️ config | optional | `"300"` | ❌ code-only | `artifacts/syrabit-backend/supabase_jwks.py:78` | code-referenced only |
| `SUPABASE_JWKS_TTL_SECONDS` | ⚙️ config | optional | `"3600"` | ❌ code-only | `artifacts/syrabit-backend/supabase_jwks.py:76` | code-referenced only |
| `SUPABASE_JWT_AUD` | 🔒 secret | optional | `"authenticated"` | ❌ code-only | `artifacts/syrabit-backend/supabase_jwks.py:81` | code-referenced only |
| `SUPABASE_KEY` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:967` | code-referenced only |
| `SUPABASE_SERVICE_KEY` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:967` | code-referenced only |
| `SUPABASE_URL` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/aca_jobs/supabase_auth_canary.py:75` | code-referenced only |
| `SYNTHETIC_PROBE_SECRETS_CHECK_TOKEN` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/synthetic_probe_secret_alert.py:118` | code-referenced only |
| `TAVILY_API_KEY` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:521` | code-referenced only |
| `TOPIC_DISCOVERY_BING_SEED_LIMIT` | ⚙️ config | optional | `seed_limit` | ❌ code-only | `artifacts/syrabit-backend/topic_discovery_service.py:413` | code-referenced only |
| `TOPIC_DISCOVERY_GRADER_MODEL` | ⚙️ config | optional | `"meta-llama/llama-4-scout-17b-16e-instruct",` | ❌ code-only | `artifacts/syrabit-backend/topic_discovery_service.py:93` | code-referenced only |
| `TOPIC_DISCOVERY_LOOP_SLEEP_S` | ⚙️ config | optional | `"1800"` | ❌ code-only | `artifacts/syrabit-backend/topic_discovery_service.py:1115` | code-referenced only |
| `TOPIC_DISCOVERY_LOOP_WARMUP_S` | ⚙️ config | optional | `"300"` | ❌ code-only | `artifacts/syrabit-backend/topic_discovery_service.py:1183` | code-referenced only |
| `TOPIC_DISCOVERY_RSS_FEEDS` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/trending_rss_client.py:95` | code-referenced only |
| `TOPIC_DISCOVERY_SUGGEST_SEED_LIMIT` | ⚙️ config | optional | `seed_limit` | ❌ code-only | `artifacts/syrabit-backend/topic_discovery_service.py:319` | code-referenced only |
| `TRANSLATE_PROVIDER` | ⚙️ config | optional | `'workers_indic'` | ❌ code-only | `artifacts/syrabit-backend/config.py:825` | code-referenced only |
| `TRUSTPILOT_AGGREGATE_OVERRIDE_JSON` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/config.py:287` | code-referenced only |
| `TRUSTPILOT_AGGREGATE_TTL_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/config.py:215` | code-referenced only |
| `TRUSTPILOT_API_KEY` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/config.py:96` | code-referenced only |
| `TRUSTPILOT_BUSINESS_UNIT_ID` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/config.py:37` | code-referenced only |
| `TRUSTPILOT_DOMAIN` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/config.py:38` | code-referenced only |
| `TRUSTPILOT_FEED_LOOP_SLEEP_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_trustpilot_alerts.py:76` | code-referenced only |
| `TRUSTPILOT_FEED_REALERT_INTERVAL_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_trustpilot_alerts.py:72` | code-referenced only |
| `TRUSTPILOT_FEED_STALE_THRESHOLD_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_trustpilot_alerts.py:68` | code-referenced only |
| `TRUSTPILOT_FEED_WARMUP_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_trustpilot_alerts.py:77` | code-referenced only |
| `TRUSTPILOT_INVITE_DAILY_LIMIT` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/config.py:64` | code-referenced only |
| `TRUSTPILOT_PROFILE_URL` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/config.py:40` | code-referenced only |
| `TRUSTPILOT_REFRESH_CRON_BOOTSTRAP_GRACE_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_trustpilot_cron_alerts.py:65` | code-referenced only |
| `TRUSTPILOT_REFRESH_CRON_LOOP_SLEEP_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_trustpilot_cron_alerts.py:55` | code-referenced only |
| `TRUSTPILOT_REFRESH_CRON_REALERT_INTERVAL_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_trustpilot_cron_alerts.py:51` | code-referenced only |
| `TRUSTPILOT_REFRESH_CRON_SILENT_THRESHOLD_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_trustpilot_cron_alerts.py:47` | code-referenced only |
| `TRUSTPILOT_REFRESH_CRON_WARMUP_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_trustpilot_cron_alerts.py:58` | code-referenced only |
| `TRUSTPILOT_REFRESH_SECRET` | 🔒 secret | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/config.py:661` | code-referenced only |
| `TRUSTPILOT_REVIEW_URL` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/config.py:44` | code-referenced only |
| `TURNSTILE_SECRET_KEY` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:291` | code-referenced only |
| `TURNSTILE_SITE_KEY` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:290` | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_INTERVAL_S` | ⚙️ config | optional | `"60"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_logs.py:49` | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_LIMIT` | ⚙️ config | optional | `"200"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_logs.py:52` | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_LOOKBACK_MIN` | ⚙️ config | optional | `"5"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_logs.py:50` | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_MAX_SUBDIVISIONS` | ⚙️ config | optional | `"12"` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_logs.py:70` | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_REALERT_INTERVAL_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_logs_cf_pull_silence_alerts.py:87` | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_SILENCE_BOOTSTRAP_GRACE_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_logs_cf_pull_silence_alerts.py:105` | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_SILENCE_LOOP_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_logs_cf_pull_silence_alerts.py:93` | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_SILENCE_WARMUP_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_logs_cf_pull_silence_alerts.py:96` | code-referenced only |
| `UNIFIED_LOGS_CF_PULL_SILENT_THRESHOLD_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/admin_logs_cf_pull_silence_alerts.py:74` | code-referenced only |
| `UNIFIED_LOGS_MAX_INGEST_BATCH` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/unified_logs_dao.py:75` | code-referenced only |
| `UNIFIED_LOGS_PAUSE` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/unified_logs_dao.py:732` | code-referenced only |
| `UNIFIED_LOGS_TTL_DAYS` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/unified_logs_dao.py:99` | code-referenced only |
| `UPSTASH_REDIS_REST_TOKEN` | 🔒 secret | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:803` | code-referenced only |
| `UPSTASH_REDIS_REST_URL` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:802` | code-referenced only |
| `VALIDATION_SAMPLE_RATE` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/validation_sampler.py:63` | code-referenced only |
| `VECTORIZE_INDEX_NAME` | ⚙️ config | optional | `"syllabus-index-v2"` | ❌ code-only | `artifacts/syrabit-backend/vectorize_client.py:33` | code-referenced only |
| `VECTORIZE_SHADOW_SAMPLE_RATE` | ⚙️ config | optional | `"1.0"` | ❌ code-only | `artifacts/syrabit-backend/vectorize_shadow.py:277` | code-referenced only |
| `VERTEX_BREAKER_COOLDOWN_S` | ⚙️ config | optional | `"300"` | ❌ code-only | `artifacts/syrabit-backend/vertex_services.py:82` | code-referenced only |
| `VERTEX_BREAKER_THRESHOLD` | ⚙️ config | optional | `"5"` | ❌ code-only | `artifacts/syrabit-backend/vertex_services.py:81` | code-referenced only |
| `VERTEX_FORMAT_BREAKER_COOLDOWN_S` | ⚙️ config | optional | `"180"` | ❌ code-only | `artifacts/syrabit-backend/vertex_format.py:54` | code-referenced only |
| `VERTEX_FORMAT_BREAKER_THRESHOLD` | ⚙️ config | optional | `"3"` | ❌ code-only | `artifacts/syrabit-backend/vertex_format.py:53` | code-referenced only |
| `VERTEX_GEMINI_MODEL` | ⚙️ config | optional | `'gemini-2.5-flash'` | ❌ code-only | `artifacts/syrabit-backend/config.py:586` | code-referenced only |
| `VERTEX_HEALTH_TTL_S` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/vertex_health_cache.py:37` | code-referenced only |
| `VERTEX_LOCATION` | ⚙️ config | optional | `'us-central1'` | ❌ code-only | `artifacts/syrabit-backend/config.py:585` | code-referenced only |
| `VERTEX_PROBE_INTERVAL_S` | ⚙️ config | optional | `"600"` | ❌ code-only | `artifacts/syrabit-backend/server.py:340` | code-referenced only |
| `VERTEX_PROJECT_ID` | ⚙️ config | optional | `''` | ❌ code-only | `artifacts/syrabit-backend/config.py:584` | code-referenced only |
| `VERTEX_REGION` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/healthz.py:161` | code-referenced only |
| `VERTEX_STARTUP_PROBE_TIMEOUT_S` | ⚙️ config | optional | `"15"` | ❌ code-only | `artifacts/syrabit-backend/server.py:220` | code-referenced only |
| `WEB_PUSH_CONTACT` | ⚙️ config | optional | `'mailto:admin@syrabit.ai'` | ✅ literal value | `artifacts/syrabit-backend/config.py:783` | code-referenced + wired |
| `WEB_PUSH_VAPID_PRIVATE_KEY` | 🔒 secret | optional | `''` | ✅ secretRef `web-push-vapid-private-key` | `artifacts/syrabit-backend/config.py:782` | code-referenced + wired |
| `WORKERS_AI_EDGE_URL` | ⚙️ config | optional | `"https://api.syrabit.ai"` | ❌ code-only | `artifacts/syrabit-backend/providers/workers_ai.py:49` | code-referenced only |
| `WORKERS_AI_FALLBACK_ENABLED` | ⚙️ config | optional | `"1"` | ❌ code-only | `artifacts/syrabit-backend/providers/workers_ai.py:64` | code-referenced only |
| `WORKERS_AI_FALLBACK_SECRET` | 🔒 secret | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/providers/workers_ai.py:53` | code-referenced only |
| `WORKERS_AI_TIMEOUT_SEC` | ⚙️ config | optional | `"20"` | ❌ code-only | `artifacts/syrabit-backend/providers/workers_ai.py:309` | code-referenced only |
| `WORKERS_BACKEND` | ⚙️ config | optional | — | ❌ code-only | `artifacts/syrabit-backend/routes/bot_discovery.py:824` | code-referenced only |
| `WORKERS_EMBED_DIMS` | ⚙️ config | optional | `"1024"` | ❌ code-only | `artifacts/syrabit-backend/providers/workers_embed.py:47` | code-referenced only |
| `WORKERS_EMBED_MAX_BATCH` | ⚙️ config | optional | `"32"` | ❌ code-only | `artifacts/syrabit-backend/providers/workers_embed.py:48` | code-referenced only |
| `WORKERS_EMBED_RETRIES` | ⚙️ config | optional | `"2"` | ❌ code-only | `artifacts/syrabit-backend/providers/workers_embed.py:50` | code-referenced only |
| `WORKERS_EMBED_SECRET` | 🔒 secret | optional | `''` | ✅ secretRef `workers-embed-secret` | `artifacts/syrabit-backend/config.py:578` | code-referenced + wired |
| `WORKERS_EMBED_STAGING_URL` | ⚙️ config | optional | `""` | ❌ code-only | `artifacts/syrabit-backend/routes/admin_embed_stack_health.py:236` | code-referenced only |
| `WORKERS_EMBED_TIMEOUT_S` | ⚙️ config | optional | `"20"` | ❌ code-only | `artifacts/syrabit-backend/providers/workers_embed.py:49` | code-referenced only |
| `WORKERS_EMBED_URL` | ⚙️ config | optional | `''` | ✅ literal value | `artifacts/syrabit-backend/config.py:577` | code-referenced + wired |

## ACA / Lambda batch jobs

Background jobs that run inside the ACA backend container (`aca_jobs/*.py`) AND, increasingly, on AWS Lambda (`artifacts/syrabit/services/backend/lambda_batch/*.py`). Lambda wiring lives in `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf`.

**Deploy file(s):** `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf`

| env var | type | required? | default | wired in deploy infra? | source | notes |
|---|---|---|---|---|---|---|
| `ACA_JOB_BATCHES_DISABLED` | ⚙️ config | optional | `""` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/materialize_chapter_faqs.py:871` | code-only |
| `ADMIN_JWT_SECRET` | 🔒 secret | optional | `""` | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/cache_effectiveness.py:43` | code-only |
| `ADMIN_JWT_SECRET_ARN` | 🔒 secret | optional | `""` | ✅ Lambda env (TF) | `artifacts/syrabit/services/backend/lambda_batch/cache_effectiveness.py:39` | Lambda + ACA |
| `AS_BACKFILL_BATCH_SIZE` | ⚙️ config | optional | `"5"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/as_translation_backfill.py:95` | code-only |
| `AS_BACKFILL_INTER_DOC_SLEEP_S` | ⚙️ config | optional | `"0.25"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/as_translation_backfill.py:97` | code-only |
| `AS_BACKFILL_MAX_CHUNK_CHARS` | ⚙️ config | optional | `"1500"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/as_translation_backfill.py:105` | code-only |
| `AS_BACKFILL_METRIC_JOB` | ⚙️ config | optional | `"as-translation-backfill"` | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/as_translation_backfill.py:50` | code-only |
| `AS_BACKFILL_METRIC_NAMESPACE` | ⚙️ config | optional | `"Syrabit/BatchJobs"` | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/as_translation_backfill.py:40` | code-only |
| `AS_BACKFILL_PER_CALL_LIMIT` | ⚙️ config | optional | `"200"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/as_translation_backfill.py:93` | code-only |
| `AS_BACKFILL_TRANSLATE_TIMEOUT_S` | ⚙️ config | optional | `"45"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/as_translation_backfill.py:100` | code-only |
| `AS_COVERAGE_INLINE_BACKFILL_LIMIT` | ⚙️ config | optional | `"2000"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/as_translation_backfill.py:603` | code-only |
| `AS_COVERAGE_METRIC_NAMESPACE` | ⚙️ config | optional | `"Syrabit/Corpus"` | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/as_translation_backfill.py:47` | code-only |
| `BACKEND_URL` | ⚙️ config | optional | `"https://syrabit-backend.lemonstone-ce3c87e1.eastus.azureco…` | ✅ Lambda env (TF) | `artifacts/syrabit/services/backend/lambda_batch/cache_effectiveness.py:35` | Lambda + ACA |
| `BATCH_JOB_DRIVER` | ⚙️ config | required | `"aca",` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/as_translation_backfill.py:429` | code-only |
| `CF_AI_GATEWAY_ACCOUNT_ID_SECRET` | 🔒 secret | — | `data.aws_secretsmanager_secret.cf_ai_gateway_account_id.arn` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf:423` (deploy) | TF-wired only |
| `CF_API_TOKEN` | 🔒 secret | optional | `""` | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/cache_effectiveness.py:188` | code-only |
| `CF_ZONE_ID` | ⚙️ config | optional | `""` | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/cache_effectiveness.py:189` | code-only |
| `CLOUDFLARE_API_TOKEN_SECRET_ARN` | 🔒 secret | — | `data.aws_secretsmanager_secret.cloudflare_api_token.arn` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf:422` (deploy) | TF-wired only |
| `COMPREHEND_RESCORE_AFTER_DAYS` | ⚙️ config | optional | `"7"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/comprehend_sampler.py:28` | code-only |
| `COMPREHEND_SAMPLE_INTERVAL_S` | ⚙️ config | optional | `"3600"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/comprehend_sampler.py:26` | code-only |
| `COMPREHEND_SAMPLE_SIZE` | ⚙️ config | optional | `"25"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/comprehend_sampler.py:27` | code-only |
| `EMBED_BACKFILL_ALERT_FAILED_THRESHOLD` | ⚙️ config | optional | `"50"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:696` | code-only |
| `EMBED_BACKFILL_ALERT_INTERVAL_S` | ⚙️ config | optional | `"300"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:693` | code-only |
| `EMBED_BACKFILL_ALERT_STALL_MINUTES` | ⚙️ config | optional | `"30"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:699` | code-only |
| `EMBED_BACKFILL_AUTOSTART` | ⚙️ config | optional | — | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:60` | code-only |
| `EMBED_BACKFILL_BATCH_SIZE` | ⚙️ config | optional | `"32"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:50` | code-only |
| `EMBED_BACKFILL_INTERVAL_S` | ⚙️ config | optional | `"900"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:59` | code-only |
| `EMBED_BACKFILL_MAX_RPM` | ⚙️ config | optional | `"600"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:52` | code-only |
| `EMBED_BACKFILL_PER_CALL_LIMIT` | ⚙️ config | optional | `"5000"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:56` | code-only |
| `EMBED_BACKFILL_THROUGHPUT_WINDOW_S` | ⚙️ config | optional | `"3600"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/embed_backfill.py:74` | code-only |
| `GCP_BILLING_DATASET` | ⚙️ config | optional | `""` | ✅ Lambda env (TF) | `artifacts/syrabit/services/backend/lambda_batch/chat_credit_runway.py:151` | Lambda + ACA |
| `GCP_BILLING_PROJECT` | ⚙️ config | optional | `""` | ✅ Lambda env (TF) | `artifacts/syrabit/services/backend/lambda_batch/chat_credit_runway.py:139` | Lambda + ACA |
| `GCP_BILLING_TABLE_PREFIX` | ⚙️ config | optional | `var.gcp_billing_table_prefix` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/services/backend/lambda_batch/chat_credit_runway.py:153` | Lambda + ACA |
| `GCP_CREDITS_START_DATE` | ⚙️ config | optional | `var.gcp_credits_start_date` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/services/backend/lambda_batch/chat_credit_runway.py:270` | Lambda + ACA |
| `GCP_TOTAL_CREDITS_USD` | ⚙️ config | optional | `tostring(var.gcp_total_credits_usd)` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/services/backend/lambda_batch/chat_credit_runway.py:260` | Lambda + ACA |
| `GEMINI_API_KEY_SECRET_ARN` | 🔒 secret | — | `data.aws_secretsmanager_secret.gemini_api_key.arn` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf:424` (deploy) | TF-wired only |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | 🔒 secret | optional | — | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/chat_credit_runway.py:127` | code-only |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON_SECRET_ARN` | 🔒 secret | — | `data.aws_secretsmanager_secret.gcp_sa_json.arn` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf:425` (deploy) | TF-wired only |
| `GOOGLE_RR_API_KEY` | 🔒 secret | optional | — | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/seo_baseline.py:163` | code-only |
| `HANDLER_NAME` | ⚙️ config | — | `each.value.handler` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf:402` (deploy) | TF-wired only |
| `LAMBDA_RELEASE` | ⚙️ config | optional | `"chat-credit-runway"` | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/chat_credit_runway.py:93` | code-only |
| `LZ_ENV` | ⚙️ config | — | `local.lz_env` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf:404` (deploy) | TF-wired only |
| `LZ_PROJECT` | ⚙️ config | — | `local.lz_project` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf:403` (deploy) | TF-wired only |
| `MATERIALIZE_FAQ_INTERVAL_S` | ⚙️ config | optional | `"86400"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/materialize_chapter_faqs.py:874` | code-only |
| `MAX_DOCS_PER_RUN` | ⚙️ config | optional | `"0"` | ✅ Lambda env (TF) | `artifacts/syrabit-backend/aca_jobs/materialize_chapter_faqs.py:803` | Lambda + ACA |
| `MONGO_DB_NAME` | ⚙️ config | optional | `"syrabit"` | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/_db.py:44` | code-only |
| `MONGO_URL` | 🔒 secret | optional | `""` | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/_db.py:19` | code-only |
| `MONGO_URL_SECRET_ARN` | 🔒 secret | optional | `""` | ✅ Lambda env (TF) | `artifacts/syrabit/services/backend/lambda_batch/_db.py:22` | Lambda + ACA |
| `OTEL_SERVICE_NAME` | ⚙️ config | — | `"${local.lz_project}-${each.key}"` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf:405` (deploy) | TF-wired only |
| `PINECONE_API_KEY_SECRET` | 🔒 secret | — | `data.aws_secretsmanager_secret.pinecone.arn` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf:417` (deploy) | TF-wired only |
| `PREWARM_AUTH_TOKEN` | 🔒 secret | optional | — | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/prewarm_seo_routes.py:45` | code-only |
| `PREWARM_AUTH_TOKEN_SECRET_ARN` | 🔒 secret | — | `data.aws_secretsmanager_secret.origin_shared_secret.arn` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf:444` (deploy) | TF-wired only |
| `PREWARM_CONCURRENCY` | ⚙️ config | optional | `"32"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/prewarm_seo_routes.py:84` | code-only |
| `PREWARM_EXAM_LOOKAHEAD_DAYS` | ⚙️ config | optional | `"30"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/prewarm_seo_routes.py:86` | code-only |
| `PREWARM_HTTP_TIMEOUT_S` | ⚙️ config | optional | `"10"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/prewarm_seo_routes.py:85` | code-only |
| `PREWARM_INTERVAL_S` | ⚙️ config | optional | `"86400"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/prewarm_seo_routes.py:786` | code-only |
| `PREWARM_TOP_N` | ⚙️ config | optional | `"5000"` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/prewarm_seo_routes.py:83` | code-only |
| `PUBLIC_BASE_URL` | ⚙️ config | optional | `"https://syrabit.ai",` | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/prewarm_seo_routes.py:92` | code-only |
| `RUNWAY_FRESHNESS_THRESHOLD_S` | ⚙️ config | optional | — | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/chat_credit_runway.py:410` | code-only |
| `RUNWAY_REDIS_KEY` | 🔒 secret | optional | — | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/chat_credit_runway.py:297` | code-only |
| `RUNWAY_REDIS_TTL_S` | ⚙️ config | optional | — | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/chat_credit_runway.py:298` | code-only |
| `SENTRY_DSN` | 🔒 secret | optional | — | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/chat_credit_runway.py:84` | code-only |
| `SENTRY_DSN_SECRET_ARN` | 🔒 secret | — | `data.aws_secretsmanager_secret.sentry_dsn_workers.arn` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf:439` (deploy) | TF-wired only |
| `SEO_BASELINE_BOARDS` | ⚙️ config | optional | — | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/seo_baseline.py:156` | code-only |
| `SEO_BASELINE_CHAPTERS_PER_BOARD` | ⚙️ config | optional | `str(DEFAULT_CHAPTERS_PER_BOARD` | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/seo_baseline.py:161` | code-only |
| `SEO_BASELINE_PAGE_TYPE` | ⚙️ config | optional | `DEFAULT_PAGE_TYPE` | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/seo_baseline.py:165` | code-only |
| `SUPABASE_ANON_KEY` | 🔒 secret | optional | — | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/supabase_auth_canary.py:76` | code-only |
| `SUPABASE_CANARY_EMAIL` | ⚙️ config | optional | — | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/supabase_auth_canary.py:65` | code-only |
| `SUPABASE_CANARY_PASSWORD` | 🔒 secret | optional | — | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/supabase_auth_canary.py:66` | code-only |
| `SUPABASE_URL` | ⚙️ config | optional | — | ❌ in-process / ACA-only | `artifacts/syrabit-backend/aca_jobs/supabase_auth_canary.py:75` | code-only |
| `UPSTASH_REDIS_REST_TOKEN` | 🔒 secret | optional | — | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/chat_credit_runway.py:212` | code-only |
| `UPSTASH_REDIS_REST_TOKEN_SECRET_ARN` | 🔒 secret | — | `data.aws_secretsmanager_secret.upstash_redis_rest_token.arn` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf:438` (deploy) | TF-wired only |
| `UPSTASH_REDIS_REST_URL` | ⚙️ config | optional | — | ❌ in-process / ACA-only | `artifacts/syrabit/services/backend/lambda_batch/chat_credit_runway.py:211` | code-only |
| `UPSTASH_REDIS_REST_URL_SECRET_ARN` | 🔒 secret | — | `data.aws_secretsmanager_secret.upstash_redis_rest_url.arn` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf:437` (deploy) | TF-wired only |
| `WORKERS_EMBED_SECRET_ARN` | 🔒 secret | — | `data.aws_secretsmanager_secret.workers_embed.arn` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf:418` (deploy) | TF-wired only |
| `WORKERS_EMBED_URL` | ⚙️ config | — | `"https://embed.syrabit.ai"` (deploy) | ✅ Lambda env (TF) | `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf:428` (deploy) | TF-wired only |

## Cloudflare Worker — `syrabit-edge` (edge proxy)

Routes `api.syrabit.ai/*` and friends. Bindings + plaintext vars in `workers/edge-proxy/wrangler.toml`; secrets via `wrangler secret put` (not in this repo).

**Deploy file(s):** `workers/edge-proxy/wrangler.toml`

| env var | type | required? | default | wired in deploy infra? | source | notes |
|---|---|---|---|---|---|---|
| `AI_GATEWAY_ANALYTICS_TOKEN` | 🔒 secret | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/ai-gateway-cache-alert.ts:419` |  |
| `AI_GATEWAY_CACHE_ALERT_DISABLED` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/ai-gateway-cache-alert.ts:407` |  |
| `AI_GATEWAY_CACHE_ALERT_EMBED_TAG` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/ai-gateway-cache-alert.ts:438` |  |
| `AI_GATEWAY_CACHE_ALERT_MIN_SAMPLE` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/ai-gateway-cache-alert.ts:429` |  |
| `AI_GATEWAY_CACHE_ALERT_QUERY_FAIL_THRESHOLD` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/ai-gateway-cache-alert.ts:433` |  |
| `AI_GATEWAY_CACHE_HIT_RATE_FLOOR_PCT` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/ai-gateway-cache-alert.ts:388` |  |
| `BACKEND_ORIGIN_SECRET` | 🔒 secret | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:1563` |  |
| `BACKEND_URL` | ⚙️ config | optional | `"https://syrabit-backend.lemonstone-ce3c87e1.eastus.azureco…` (deploy) | ✅ wrangler [vars] | `workers/edge-proxy/src/index.ts:316` |  |
| `BOT_CACHE_ALERT_DISABLED` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/bot-cache-alert.ts:334` |  |
| `BOT_CACHE_ALERT_DROP_PCT` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/bot-cache-alert.ts:343` |  |
| `BOT_CACHE_ALERT_FALLBACK_PCT` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/bot-cache-alert.ts:344` |  |
| `BOT_CACHE_ALERT_MIN_SAMPLE` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/bot-cache-alert.ts:345` |  |
| `BOT_CACHE_ALERT_WINDOW_BUCKETS` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/bot-cache-alert.ts:174` |  |
| `CF_ANALYTICS_TOKEN` | 🔒 secret | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:3505` |  |
| `CF_BLOCK_PROBE_DISABLED` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/cf-block-probe.ts:303` |  |
| `CF_BLOCK_PROBE_TARGET_URL` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/cf-block-probe.ts:330` |  |
| `CF_BLOCK_PROBE_THRESHOLD` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/cf-block-probe.ts:139` |  |
| `D1_SYNC_SECRET` | 🔒 secret | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:346` |  |
| `D1_WARM_ON_STARTUP` | ⚙️ config | optional | `"true"` (deploy) | ✅ wrangler [vars] | `workers/edge-proxy/src/index.ts:4580` |  |
| `EDGE_AI_FALLBACK_SECRET` | 🔒 secret | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:3147` |  |
| `EDGE_LOG_DEFERRED_FLUSH_MS` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/log-shipper.ts:327` |  |
| `EDGE_LOG_FLUSH_AGE_MS` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/log-shipper.ts:56` |  |
| `EDGE_LOG_FLUSH_BATCH` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/log-shipper.ts:55` |  |
| `EDGE_LOG_SAMPLE_RATE` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/log-shipper.ts:157` |  |
| `JWT_SECRET` | 🔒 secret | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:85` |  |
| `KV_ALERT_SECRET` | 🔒 secret | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:317` |  |
| `KV_QUOTA` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:307` |  |
| `KV_WARNING_PCT` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:311` |  |
| `LOG_INGEST_TOKEN` | 🔒 secret | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/log-shipper.ts:150` |  |
| `MTLS_REQUIRED` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:1938` |  |
| `PAGES_ORIGIN` | ⚙️ config | optional | `"https://syrabitfrontend.pages.dev"` (deploy) | ✅ wrangler [vars] | `workers/edge-proxy/src/index.ts:4275` |  |
| `R2_LIFECYCLE_RULES_APPLIED_AT` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:450` |  |
| `R2_STORAGE_ALERT_BUCKETS` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:458` |  |
| `R2_STORAGE_ALERT_DISABLED` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:460` |  |
| `R2_STORAGE_ALERT_LOGPUSH_CAP_GB` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:446` |  |
| `R2_STORAGE_ALERT_QUERY_FAIL_THRESHOLD` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:466` |  |
| `R2_STORAGE_ANALYTICS_TOKEN` | 🔒 secret | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:243` |  |
| `SYNTHETIC_PROBE_ADMIN_JWT` | 🔒 secret | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:158` |  |
| `SYNTHETIC_PROBE_CF_ACCESS_CLIENT_ID` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:156` |  |
| `SYNTHETIC_PROBE_CF_ACCESS_CLIENT_SECRET` | 🔒 secret | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:157` |  |
| `SYNTHETIC_PROBE_DISABLED` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:154` |  |
| `SYNTHETIC_PROBE_TARGET_URL` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:155` |  |
| `SYNTHETIC_PROBE_WATCHDOG_THRESHOLD_MIN` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/index.ts:160` |  |
| `SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/ai-gateway-cache-alert.ts:338` |  |
| `WORKERS_AI_GATEWAY_ID` | ⚙️ config | optional | — | ❌ wrangler secret (operator-set) | `workers/edge-proxy/src/ai-gateway-cache-alert.ts:414` |  |

## Cloudflare Worker — `syrabit-embed-worker`

Custom Workers-AI embedding endpoint at `embed.syrabit.ai`. Bindings in `artifacts/syrabit/workers/embed-worker/wrangler.toml`.

**Deploy file(s):** `artifacts/syrabit/workers/embed-worker/wrangler.toml`

| env var | type | required? | default | wired in deploy infra? | source | notes |
|---|---|---|---|---|---|---|
| `EMBED_DIMS` | ⚙️ config | optional | `"1024"` (deploy) | ✅ wrangler [vars] | `artifacts/syrabit/workers/embed-worker/src/index.ts:100` |  |
| `EMBED_MAX_BATCH` | ⚙️ config | optional | `"32"` (deploy) | ✅ wrangler [vars] | `artifacts/syrabit/workers/embed-worker/src/index.ts:101` |  |
| `EMBED_MAX_CHARS` | ⚙️ config | optional | `"4096"` (deploy) | ✅ wrangler [vars] | `artifacts/syrabit/workers/embed-worker/src/index.ts:102` |  |
| `EMBED_MODELS` | ⚙️ config | optional | `"@cf/google/embeddinggemma-300m,@cf/qwen/qwen3-embedding-0.…` (deploy) | ✅ wrangler [vars] | `artifacts/syrabit/workers/embed-worker/src/index.ts:103` |  |
| `EMBED_RATE_RPM` | ⚙️ config | optional | `"600"` (deploy) | ✅ wrangler [vars] | `artifacts/syrabit/workers/embed-worker/src/index.ts:107` |  |
| `EMBED_SHARED_SECRET` | 🔒 secret | optional | — | ❌ wrangler secret (operator-set) | `artifacts/syrabit/workers/embed-worker/src/index.ts:265` |  |
| `EMBED_WORKER_VERSION` | ⚙️ config | optional | `"1.1.0-staging"` (deploy) | ✅ wrangler [vars] | `artifacts/syrabit/workers/embed-worker/src/index.ts:108` |  |
| `NODE_ENV` | ⚙️ config | — | `"production"` (deploy) | ✅ wrangler [vars] | `artifacts/syrabit/workers/embed-worker/wrangler.toml:78` (deploy) |  |

## Cloudflare Worker — `syrabit-email` (410 stub)

Task #556 retired transport — only `/email/health` is live; every other route returns HTTP 410. Kept on the deploy manifest so stale callers fail loud.

**Deploy file(s):** `workers/email-worker/wrangler.toml`

| env var | type | required? | default | wired in deploy infra? | source | notes |
|---|---|---|---|---|---|---|
| `BACKEND_AUTH_KEY` | 🔒 secret | optional | — | ❌ wrangler secret (operator-set) | `workers/email-worker/src/index.ts:28` |  |
