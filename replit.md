# Syrabit.ai

Syrabit.ai is an AI-powered educational platform delivering bilingual (English + Assamese) localized learning for AHSEC Class 11/12 and Degree students in Assam across 55 subjects: RAG notes, MCQs, flashcards, PYQ OCR, in-app browser with grounded chat, admin CMS, credit-based monetization (Razorpay INR-only), DPDP-compliant.

## Source of truth

This README is a **navigation index**. The canonical implementation map of the 2026 architecture lives in [`infra/architecture-locked-2026.md`](infra/architecture-locked-2026.md) (human) + [`infra/architecture-matrix.json`](infra/architecture-matrix.json) (machine) and is enforced by [`scripts/check_architecture_lock.py`](scripts/check_architecture_lock.py). When this README and the lock disagree, **the lock wins**.

## Run & operate

- **Frontend dev:** `cd artifacts/syrabit && PORT=5000 pnpm dev`
- **Backend dev:** `cd artifacts/syrabit-backend && gunicorn server:app -c gunicorn.conf.py`
- **Mockup sandbox:** `pnpm --filter @workspace/mockup-sandbox run dev`
- **Health check:** `https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io/api/health`
- **Backend import smoke test:** `cd artifacts/syrabit-backend && python -c "import server"` before pushing.

## Stack

- **Frontend:** React 18, Vite, React Router, Tailwind CSS, Drizzle ORM
- **Backend:** Python 3.11, FastAPI, Gunicorn (Uvicorn workers)
- **Validation/Codegen:** Zod, Orval
- **Build:** pnpm monorepo, esbuild, Docker

## Where things live

- **Frontend:** `artifacts/syrabit/`
- **Backend:** `artifacts/syrabit-backend/`
- **Embed worker (Cloudflare):** `artifacts/syrabit/workers/embed-worker/`
- **Edge proxy (Cloudflare):** `workers/edge-proxy/`
- **Bicep + ACA deploy:** `infra/azure/aca-syrabit-backend.bicep`, `.github/workflows/azure-container-apps-deploy.yml`
- **AWS Lambda batch jobs:** `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf` + `artifacts/syrabit/services/backend/lambda_batch/` + `infra/aws/lambda/manifest.json`
- **AWS Glacier Deep Archive (7-yr DPDP):** `artifacts/syrabit/infra/aws/glacier-archive.tf` + `routes/admin_archive.py` + runbook `artifacts/syrabit/docs/infra/glacier-restore-runbook.md`
- **Threat model:** `threat_model.md`
- **2026 architecture lock:** `infra/architecture-locked-2026.md`, `infra/architecture-matrix.json`, `scripts/check_architecture_lock.py`
- **Four-cloud delegation matrix:** `infra/four-cloud-delegation.md`

## Architecture summary (mirrors `infra/architecture-locked-2026.md` section-by-section)

Section headers below mirror the lock document 1:1. For per-row status (`IMPLEMENTED | PARTIAL | MISSING | RETIRED`) and source paths, read the lock.

### §2 Core principles

Edge-first delivery, retrieval-heavy AI, deterministic caching, multi-provider orchestration, curriculum grounding, progressive degradation, materialized educational outputs, SEO-driven distribution.

### §3 High-level architecture

PWA → CF Edge (Pages + CDN + Workers + KV + D1) → Edge Gateway (auth / quota / trace / budget) → ACA FastAPI core (eastus2) → RAG pipeline (vector + BM25 + graph) → provider delegation → response formatter → SSE streaming.

### §4.1 Cloudflare layer

Pages (PWA), Workers (edge compute), CDN + tiered cache, KV (deterministic AI cache), D1 (syllabus graph), R2 (assets + OCR + final backups), WAF + Bot Management, Turnstile, Cron Triggers.

### §4.2 Azure layer

Container Apps (FastAPI runtime, eastus2 → westus3 DR), Key Vault (secrets source of truth — AWS SM + CF Secrets are read-only replicas), Blob (OCR scratch).

### §4.3 AWS layer

SQS (async queues), Lambda (scheduled jobs), Glacier Deep Archive (7-yr DPDP), SES (sole transactional email tier-1).

### §5.1 Chat providers (canonical)

English: strict 3-position chain `vertex → vertex_flash_lite → workers_ai_llama32_3b` (head flips on ≤90d runway). Assamese: strict 3-position chain `sarvam → vertex_assamese → retrieval_only`. Content formatter: Vertex 2.5 Flash → Llama-3.3-70b → passthrough; every doc carries `formatted_by` audit field.

### §5.2 OCR

Indic OCR via Google Vision; general OCR via Workers AI Vision.

### §5.3 Voice

STT English: Deepgram Nova-3. STT Assamese: Google Chirp_2 → Workers AI Whisper. TTS English: ElevenLabs primary, Deepgram Aura-2 fallback. TTS Assamese: Google Neural2. Voice paywall on `/voice/tts /voice/stt /voice/voice`.

### §5.4 Payments

Razorpay INR-only subscriptions; Glacier 7-yr receipt archive.

### §5.5 Analytics & monitoring

PostHog (LCP-gated SDK); Cloudflare Analytics; Sentry errors-only Developer tier (Performance retired); GCP Cloud Trace as sole tracing exporter.

### §6 Databases

MongoDB Atlas (users / chat / content / OCR / metadata); Cloudflare D1 (syllabus graph + edge metadata); Pinecone (1024-dim, `aws-ap-south-1`); Cloudflare KV (deterministic AI cache: MCQs / flashcards / definitions / translations / precomputed notes); Upstash Redis (rate limit + hot retrieval cache + quota).

### §7 AI pipeline (8 stages)

Request intake → intent resolution → 8-source retrieval dispatch → RRF fusion → prompt synthesis → model delegation → response formatting → deterministic materialization.

### §8 Cache architecture (5 layers)

L1 browser → L2 CF CDN → L3 KV AI cache → L4 Redis hot cache → L5 D1 metadata cache.

### §9 Advanced cache optimization (#571 → #577)

Cache intelligence (#571) live; semantic fingerprinting (#572) and prewarming engine (#574) MISSING (Tasks #10 / #13); deterministic rendering (#573) PARTIAL (Task #10); dynamic-TTL exam-window stretch (#575) live; regional cache `X-Cache-Region: ne-india` (#576) live; retrieval-result cache (#577) live.

### §10 SEO

Programmatic `/board/class/subject/chapter/type` routes; generated content types PARTIAL (Task #11 lifts H1=chapter-topic + schema.org + hreflang `as-IN/en-IN` + `geo.region=IN-AS`); IndexNow PARTIAL (Task #11 extends to Yandex + verifies Google Indexing API); internal linker + entity SEO health live; AEO answer cards + FAQ JSON-LD PARTIAL (Task #12).

### §11 Voice flow

Student Voice → STT → Intent Resolution → Retrieval → Model Delegation → TTS → Streamed Audio (bilingual + Assamese explanations + exam revision + spoken summaries + accessibility).

### §12 OCR flow

PDF/Image upload → OCR detection → Indic routing → text extraction → structure parsing → retrieval indexing → PYQ materialization.

### §13 Security

Auth: **Supabase sole IdP** (Supabase JWKS verification + OIDC broker live; legacy email/password endpoints in `routes/auth.py` and the `JWT_SECRET` user-session path PARTIAL pending cutover; `JWT_SECRET` / `ADMIN_JWT_SECRET` remain only for short-lived service-to-service tokens, never for user sessions). Edge security: WAF + DDoS + rate-limit + bot detection (Task #9 splits verified-bot KV fast path). Secrets: Azure KV primary (AWS SM + CF Secrets read-only replicas). OriginGate `X-Origin-Auth` lock-step rotation.

### §14 Observability (#569, #570)

W3C tracing + provider spans + latency + cost telemetry + cache metrics + circuit-state + canaries; synthetic user journeys + SLA ledger + blast-radius + provider outage map + admin Ops Console.

### §15 Cost governance

`MONTHLY_TOTAL_USD_CAP = $100` (founder-locked); progressive degradation 60 / 80 / 95 % ladder + paywall + cache-only at 100 %; heavy-free user flow PARTIAL (Task #13 closes prewarm leg).

### §16 PWA

Installable, offline shell, standalone, app shortcuts, low-data, bilingual UI, offline revision notes.

### §19 Positioning

*“Curriculum-constrained educational intelligence infrastructure”* — retrieval compounds, cache compounds, SEO compounds, educational artifacts compound, inference dependency decreases over time.

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
- **Supabase is the sole auth provider** for student / staff / admin (sign-up, sign-in, OAuth, password reset, email verification, MFA). Backend verifies Supabase JWTs via JWKS; `GOOGLE_OAUTH_CLIENT_SECRET` is no longer required.

## Gotchas

- **Backend import check:** `cd artifacts/syrabit-backend && python -c "import server"` before pushing.
- **ACA deploy config:** Bicep ARM PATCH must include `properties.configuration.ingress.traffic = [{latestRevision: true, weight: 100}]` and `targetPort: 8000`. Bicep template (`infra/azure/aca-syrabit-backend.bicep`) must precisely mirror the runtime contract.
- **Pinecone dimension incompatibility:** dimension is 1024. Future embed providers must match or be quarantined.
- **`OriginGate` lock-step rotation:** `ORIGIN_SHARED_SECRET` (ACA env) and `BACKEND_ORIGIN_SECRET` (syrabitworker binding) MUST be equal. Same for `D1_SYNC_SECRET`.
- **Token budgets are LOCKED:** `cost_caps.TOKEN_BUDGETS` ceilings are founder-locked. Raising any value requires a `# COST-CAP-OVERRIDE: <reason>` comment on the changed line AND a Sentry-annotated changelog entry; `tests/test_cost_caps.py` walks the source file and fails CI when either signal is missing. Same applies to bumping the edge chat caps (`CHAT_CAP_MONTHLY=30`, `CHAT_CAP_DAILY=3`) in `workers/edge-proxy/src/index.ts`.
- **Monthly USD ceiling LOCKED at $100:** `_DEFAULT_MONTHLY_TOTAL_USD_CAP` (`cost_caps.py`) and `MeterDConfig.cap_usd` (`credit_burn_meter.py`) defaults must remain ≤ $100 unless the changed line carries a `# COST-CAP-OVERRIDE: <reason>` marker. `scripts/check_budget_ceiling.py` enforces this in CI and validates that the three degradation thresholds (60 / 80 / 95 %) stay strictly increasing inside (0.0, 1.0).
- **`/api/me/quota` edge cache TTL = 5s (intentional, not 60s):** `Cache-Control: private, max-age=5, s-maxage=5` keeps the SPA's "remaining turns" banner from showing a stale value across more than one tick. Raising the TTL requires a UX review of banner staleness.
- **K.2 deterministic cache scope:** `ai_input_cache` covers formatter / translate / OCR / MCQ / flashcard / definition; live `routes/ai_chat.py` is excluded by policy. Task #10 layered the **semantic fingerprint** on top: callers may pass `fingerprint=<32-hex>` (computed via `cache_fingerprint.fingerprint(...)`) so paraphrased / bilingual variants ("Explain photosynthesis" + "ফটোসিন্থেসিস কি") collapse onto a single `aic:fp:<region>:<model>:<fp>` key. The literal SHA256 keys are read-through for 30 days under `CACHE_FINGERPRINT_DUAL_READ` (default `true`); writes go ONLY to the fingerprint key. Per-content-type `fingerprint_hit_ratio` + `legacy_hit_ratio` are surfaced in `/api/health/cache`. Materialization-eligible content types (`definition` / `mcq` / `flashcard` / `glossary` / `chapter_summary`) can also short-circuit the LLM via `content_formatter.format_content(query_type=..., template_data=...)` which renders `templates/deterministic/<type>.md` and emits `formatted_by="deterministic_template"`. See [Extended decisions](docs/architecture/decisions.md#k2-deterministic-cache-scope-chat-adjacent).
- **Cache calendar:** exam/results-mode TTL stretch (30d→90d for mcq/flashcard/definition/pyq) via `artifacts/syrabit-backend/cache_calendar.py` + `config/exam_calendar.yaml`; `/api/health/season` exposes the active mode. `cache_calendar.recommended_ttl_seconds(content_type, route, today)` is the single source of truth for both the prewarm Lambda and the Cloudflare worker per-route override pass — change a TTL here and it propagates to both call-sites in the next deploy. See [Extended decisions](docs/architecture/decisions.md#cache-calendar-knob-task-575).
- **Prewarm engine (Task #13):** `aca_jobs/prewarm_seo_routes.py` runs nightly at 01:00 UTC via Lambda (Terraform: `prewarm-seo-routes` in `lambda-batch-jobs.tf`). Selects target chapters as `top_n` by 7-day `db.page_views` traffic UNION every chapter under a subject whose exam window starts within `PREWARM_EXAM_LOOKAHEAD_DAYS` (default 30), walks all 7 SEO `PAGE_TYPES` per chapter, HEADs each URL through Cloudflare so the worker fills its tiered cache, and persists the per-board summary to `db.seo_prewarm_runs` (consumed by admin tile `/api/admin/seo/prewarm-coverage`). Emits `Syrabit/Cache::PrewarmSuccessRate` per pass; CloudWatch alarm `cache-prewarm-success-rate-low` fires at <0.90 (or when the Lambda misses a publish window). Knobs: `PREWARM_TOP_N=5000`, `PREWARM_CONCURRENCY=32`, `PREWARM_HTTP_TIMEOUT_S=10`, `PREWARM_EXAM_LOOKAHEAD_DAYS=30`, `PUBLIC_BASE_URL=https://syrabit.ai`. Targets ≥95% KV hit-ratio during exam windows for materialization-eligible content types.
- **Cache-effectiveness observability (#571):** admin `/api/health/cache` + nightly Lambda → CloudWatch alarms (hit-ratio < 0.30, cardinality 3× spike).

## Pointers

- **2026 architecture lock:** `infra/architecture-locked-2026.md` (+ `infra/architecture-matrix.json`)
- **Extended decision log:** `docs/architecture/decisions.md`
- **Four-cloud delegation matrix:** `infra/four-cloud-delegation.md`
- **GCP landing zone:** `artifacts/syrabit/docs/infra/gcp-landing-zone.md`
- **Azure landing zone:** `artifacts/syrabit/docs/infra/azure-landing-zone.md`
- **AWS landing zone:** `artifacts/syrabit/docs/infra/aws-landing-zone.md`
- **ACA cutover runbook:** `artifacts/syrabit/docs/infra/aca-cutover.md`
- **Cache-effectiveness audit (#571):** `artifacts/syrabit/docs/infra/cache-effectiveness-audit.md`
- **AWS Glacier restore runbook:** `artifacts/syrabit/docs/infra/glacier-restore-runbook.md`
- **AWS Lambda batch-jobs manifest:** `infra/aws/lambda/manifest.json`
- **2026 cleanup purge log:** `docs/cleanup/2026-purge-log.md`
- **Skills index:** `.local/skills/`
