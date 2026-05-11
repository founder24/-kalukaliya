# Syrabit.ai

Syrabit.ai is an AI-powered educational platform delivering bilingual (English + Assamese) localized learning for AHSEC Class 11/12 and Degree students in Assam across 55 subjects: RAG notes, MCQs, flashcards, PYQ OCR, in-app browser with grounded chat, admin CMS, credit-based monetization (Razorpay INR-only), DPDP-compliant.

## Source of truth

This README is a **navigation index**. The canonical implementation map of the 2026 architecture lives in [`infra/architecture-locked-2026.md`](infra/architecture-locked-2026.md) (human) + [`infra/architecture-matrix.json`](infra/architecture-matrix.json) (machine) and is enforced by [`scripts/check_architecture_lock.py`](scripts/check_architecture_lock.py). When this README and the lock disagree, **the lock wins**.

## Run & operate

Workflows are driven by per-artifact `.replit-artifact/artifact.toml` files (see `artifacts/syrabit/`, `artifacts/syrabit-backend/`, `artifacts/mockup-sandbox/`). Workflow-contract / browser-console triage history: [`docs/dev/task-14-workflow-triage-2026-05-09.md`](docs/dev/task-14-workflow-triage-2026-05-09.md).

- **Frontend dev** (`artifacts/syrabit: web`, port `25144`): `pnpm --filter @workspace/syrabit run dev`
- **Backend dev** (`artifacts/syrabit: api`, port `8080`): `cd artifacts/syrabit-backend && gunicorn server:app -c gunicorn.conf.py`
- **Mockup sandbox** (port `8081`): `pnpm --filter @workspace/mockup-sandbox run dev`
- **Local health check:** `bash scripts/dev_health_check.sh` (also the `dev_health` validation step). Skip the build leg with `DEV_HEALTH_SKIP_BUILD=1`.
- **Backend import smoke test:** `cd artifacts/syrabit-backend && python -c "import server"` before pushing.
- **Production health:** `https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io/api/health`
- **Full env-var contract:** [`docs/infra/env-vars.md`](docs/infra/env-vars.md) — auto-generated; regenerate with `python scripts/ci/check_env_vars_doc.py --write` (Task #89, gated by `dev_health`).
- **Required env vars** (canonical subset enforced by `scripts/ci/check_canonical_delegation.py::_check_replit_required_env_vars`; see env-vars.md for the full prod inventory): `SARVAM_API_KEY` `WEB_PUSH_VAPID_PRIVATE_KEY` `WEB_PUSH_CONTACT` `SENTRY_DSN` `MONGO_URL` `PINECONE_API_KEY` `JWT_SECRET` `ADMIN_JWT_SECRET` `CLOUDFLARE_API_TOKEN` `R2_ACCESS_KEY_ID` `R2_SECRET_ACCESS_KEY` `R2_BUCKET_NAME` `R2_ENDPOINT_URL` `RAZORPAY_KEY_ID` `RAZORPAY_KEY_SECRET` `RAZORPAY_WEBHOOK_SECRET` `DEEPGRAM_API_KEY` `ELEVENLABS_API_KEY` `UPSTASH_REDIS_REST_URL` `UPSTASH_REDIS_REST_TOKEN` `GOOGLE_APPLICATION_CREDENTIALS_JSON` `VERTEX_PROJECT_ID` `VERTEX_LOCATION` `WORKERS_EMBED_SECRET` `WORKERS_EMBED_URL` `AWS_ACCESS_KEY_ID` `AWS_SECRET_ACCESS_KEY` `AWS_REGION` `SES_REGION` `PINECONE_INDEX` `POSTHOG_API_KEY` `POSTHOG_HOST`. Retired: SendGrid/Resend (#556 → SES), FCM/Firebase (#557 → web-push), Azure-OpenAI/AssemblyAI (#559).

## Stack

React 18 + Vite + Tailwind frontend; Python 3.11 + FastAPI + Gunicorn backend; pnpm monorepo; Cloudflare Workers edge; Zod + Orval validation/codegen.

## Where things live

- **Frontend:** `artifacts/syrabit/` — **Backend:** `artifacts/syrabit-backend/`
- **Edge proxy:** `workers/edge-proxy/` — **Embed worker:** `artifacts/syrabit/workers/embed-worker/`
- **Bicep + ACA deploy:** `infra/azure/aca-syrabit-backend.bicep`, `.github/workflows/azure-container-apps-deploy.yml`
- **AWS Lambda batch jobs:** `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf` + `artifacts/syrabit/services/backend/lambda_batch/` + `infra/aws/lambda/manifest.json`
- **AWS Glacier (7-yr DPDP):** `artifacts/syrabit/infra/aws/glacier-archive.tf` + `routes/admin_archive.py`
- **Threat model:** `threat_model.md`
- **2026 architecture lock:** `infra/architecture-locked-2026.md`, `infra/architecture-matrix.json`
- **Four-cloud delegation matrix:** `infra/four-cloud-delegation.md`

## Architecture summary

Read [`infra/architecture-locked-2026.md`](infra/architecture-locked-2026.md) — it has the full §2 → §19 breakdown (core principles, CF/Azure/AWS layers, chat/OCR/voice/payments providers, databases, AI pipeline, 5-layer cache, SEO, security, observability, cost governance, PWA, positioning) with per-row `IMPLEMENTED | PARTIAL | MISSING | RETIRED` status and source paths. The lock wins on any disagreement.

One-line shape: PWA → CF Edge (Pages + CDN + Workers + KV + D1) → Edge Gateway (auth/quota/trace/budget) → ACA FastAPI core (eastus2) → RAG (vector + BM25 + graph) → provider delegation → response formatter → SSE streaming. English chat chain `vertex → vertex_flash_lite → workers_ai_llama32_3b`; Assamese chain `sarvam → vertex_assamese → retrieval_only`. Pinecone 1024-dim. 5-layer cache `browser → CF CDN → KV → Redis → D1`. Auth: Supabase sole IdP. Cost cap $100/mo with 60/80/95 % degradation ladder.

## Founder locks (always win)

- `MONTHLY_TOTAL_USD_CAP = $100` — `cost_caps.py` + `credit_burn_meter.py`. CI guard `scripts/check_budget_ceiling.py`.
- Voice paywall on `/voice/tts /voice/stt /voice/voice`.
- 60/80/95 % degradation ladder strictly increasing inside (0.0, 1.0).
- Sarvam = sole Assamese head; Pinecone dimension = 1024; Supabase = sole auth.
- V4 §12 *no silent fallbacks* — fail loud, document trade-offs explicitly.

## User preferences

- Iterative development with clear communication on major changes.
- Detailed explanations for complex features and architectural decisions.
- Prioritize modularity and maintainability.
- Treat vectorless RAG and vector RAG as **complementary layers** — vector for semantic/paraphrased exam-Q search, vectorless (BM25 + tree-walk) for exact-term/formula/navigation queries.
- **No silent fallbacks** — fail loud (V4 §12).
- **Supabase is the sole auth provider** for student/staff/admin (sign-up, sign-in, OAuth, password reset, email verification, MFA). Backend verifies Supabase JWTs via JWKS; `GOOGLE_OAUTH_CLIENT_SECRET` is no longer required.

## Gotchas

Headline operational traps. The full multi-paragraph rationale lives in [`docs/architecture/decisions.md`](docs/architecture/decisions.md) under the matching anchor.

- **Backend import check:** `cd artifacts/syrabit-backend && python -c "import server"` before pushing.
- **ACA deploy config:** Bicep ARM PATCH must include `properties.configuration.ingress.traffic = [{latestRevision: true, weight: 100}]` and `targetPort: 8000`. Bicep template (`infra/azure/aca-syrabit-backend.bicep`) must precisely mirror the runtime contract.
- **Pinecone dimension = 1024.** Future embed providers must match or be quarantined.
- **`OriginGate` lock-step rotation:** `ORIGIN_SHARED_SECRET` (ACA env) and `BACKEND_ORIGIN_SECRET` (syrabitworker binding) MUST be equal. Same for `D1_SYNC_SECRET`.
- **Token + USD budgets are LOCKED.** `cost_caps.TOKEN_BUDGETS`, `_DEFAULT_MONTHLY_TOTAL_USD_CAP`, `MeterDConfig.cap_usd`, edge `CHAT_CAP_MONTHLY=30`/`CHAT_CAP_DAILY=3` (`workers/edge-proxy/src/index.ts`), and the AWS-side `aws_budgets_budget.monthly_cost` (Task #4 cloud mirror, `account-billing.tf`) all share the $100/mo ceiling and the 60/80/95 % thresholds. Any raise needs a `# COST-CAP-OVERRIDE: <reason>` marker on the changed line. Enforced by `scripts/check_budget_ceiling.py` + `tests/test_cost_caps.py`. See [Extended decisions — Cost caps & cloud budget mirror](docs/architecture/decisions.md#cost-caps--cloud-budget-mirror-tasks-4--549).
- **`/api/me/quota` edge cache TTL = 5s (intentional, not 60s):** `Cache-Control: private, max-age=5, s-maxage=5` keeps the SPA's "remaining turns" banner from showing a stale value across more than one tick.
- **K.2 deterministic cache scope + semantic fingerprint:** `ai_input_cache` covers formatter/translate/OCR/MCQ/flashcard/definition; live `routes/ai_chat.py` excluded by policy. Callers may pass `fingerprint=<32-hex>` (via `cache_fingerprint.fingerprint(...)`) so paraphrased / bilingual variants collapse onto one `aic:fp:<region>:<model>:<fp>` key. Materialization-eligible types short-circuit the LLM via `content_formatter.format_content(...)` rendering `templates/deterministic/<type>.md`. See [Extended decisions — K.2](docs/architecture/decisions.md#k2-deterministic-cache-scope-chat-adjacent).
- **Cache calendar:** exam/results-mode TTL stretch via `artifacts/syrabit-backend/cache_calendar.py` + `config/exam_calendar.yaml`; `cache_calendar.recommended_ttl_seconds(...)` is the SSOT for prewarm Lambda + CF worker overrides. See [Extended decisions — Cache calendar knob](docs/architecture/decisions.md#cache-calendar-knob-task-575).
- **Prewarm engine (Task #13):** `aca_jobs/prewarm_seo_routes.py` runs nightly 01:00 UTC via `prewarm-seo-routes` Lambda, walks all 7 SEO `PAGE_TYPES` per chapter, fills KV (`aic:fp:*`) + Mongo `ai_input_cache`. CloudWatch alarms `cache-prewarm-success-rate-low` + `cache-kv-prewarm-success-rate-low` fire at <0.90. Targets ≥95 % KV hit-ratio in exam windows. Full knob/auth/observability detail: [Extended decisions — Prewarm engine](docs/architecture/decisions.md#prewarm-engine-task-13).
- **Cache-effectiveness observability (#571):** admin `/api/health/cache` + nightly Lambda → CloudWatch alarms (hit-ratio < 0.30, cardinality 3× spike).
- **Backend test gates (Tasks #85, #86):** pytest promotes the *"coroutine ... was never awaited"* `RuntimeWarning` to a hard failure — fix the missing `await`, do not suppress. Use `asyncio.run(coro)` not `asyncio.get_event_loop().run_until_complete(...)`. Chat-provider chains in tests are canonical (lock §5.1) — `azure_openai`/`workers_ai_indic`(chat)/`workers_ai_mistral_7b`(chat) are retired from chat chains and seeding their RPM windows raises `KeyError`; the same providers DO remain in `assamese_content`/`content` pools. Full gate semantics: [Extended decisions — Backend test gates](docs/architecture/decisions.md#backend-test-gates-tasks-85--86).
- **Indic embed via AWS Bedrock (Task #27, partial reversal of #491):** Assamese / Indic-tagged retrievals route through `cohere.embed-multilingual-v3` on AWS Bedrock (no `COHERE_API_KEY`, no `cohere` SDK — both banned by `check_canonical_delegation.py`); English uses Workers-AI custom embed. Both 1024-dim into the same Pinecone index, isolated by `embed_provider` metadata. Sub-cap `INDIC_EMBED_MONTHLY_USD_SUBCAP=$5/mo` inside the global $100 cap. Kill-switches: `EMBED_INDIC_PROVIDER=workers_ai_custom`, `RAG_EMBEDDING_PROVIDER_FORCE=workers_ai_custom`. Admin tile: `/admin/health/embed-stack`. See [Extended decisions — Task #27](docs/architecture/decisions.md#task-27--cohere-embed-multilingual-v3-via-aws-bedrock-2026-05-09).

## Pointers

- **2026 architecture lock:** `infra/architecture-locked-2026.md` (+ `infra/architecture-matrix.json`)
- **Extended decision log:** `docs/architecture/decisions.md`
- **Four-cloud delegation matrix:** `infra/four-cloud-delegation.md`
- **Landing zones:** `artifacts/syrabit/docs/infra/{gcp,azure,aws}-landing-zone.md`
- **ACA cutover runbook:** `artifacts/syrabit/docs/infra/aca-cutover.md`
- **Cache-effectiveness audit (#571):** `artifacts/syrabit/docs/infra/cache-effectiveness-audit.md`
- **Ranking playbook (#15):** `docs/architecture/ranking-playbook.md`
- **Glacier restore runbook:** `artifacts/syrabit/docs/infra/glacier-restore-runbook.md`
- **Lambda batch-jobs manifest:** `infra/aws/lambda/manifest.json`
- **2026 cleanup purge log:** `docs/cleanup/2026-purge-log.md`
- **Skills index:** `.local/skills/`
