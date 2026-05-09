# Syrabit.ai

Syrabit.ai is an AI-powered educational platform providing bilingual localized learning for students in Assam across 55 subjects.

## Run & Operate

- **Frontend dev:** `cd artifacts/syrabit && PORT=5000 pnpm dev`
- **Backend dev:** `cd artifacts/syrabit-backend && gunicorn server:app -c gunicorn.conf.py`
- **Mockup sandbox:** `pnpm --filter @workspace/mockup-sandbox run dev`
- **Health check:** `https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io/api/health`
- **Required env vars (ACA, from Azure KV):** `MONGO_URL`, `JWT_SECRET`, `ADMIN_JWT_SECRET`, `GOOGLE_APPLICATION_CREDENTIALS_JSON`, `SARVAM_API_KEY`, `RAZORPAY_KEY_SECRET`, `WORKERS_EMBED_SECRET`, `WEB_PUSH_VAPID_PRIVATE_KEY`, `WEB_PUSH_CONTACT`, `SENTRY_DSN`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `SES_REGION`, `EMBED_PROVIDER_PRIMARY`, `WORKERS_EMBED_URL`. Operator override knobs (optional): `CHAT_PRIMARY_OVERRIDE`, `RAG_EMBEDDING_PROVIDER_FORCE`, `EMBED_DEGRADED_MODE`, `BULK_EMAIL_WORKER_URL`, `BULK_EMAIL_WORKER_AUTH_KEY`. *(Task #556 done — SES is the sole transactional path; the previously-required `EMAIL_PROVIDER` / `EMAIL_FALLBACK` flags and the SendGrid / Resend keys are retired. Task #557 done — self-hosted web-push (`pywebpush` + `py-vapid`) is the sole web-push path; ACA now mounts `WEB_PUSH_VAPID_PRIVATE_KEY` (Azure KV `WEB-PUSH-VAPID-PRIVATE-KEY` → ACA secretRef) plus `WEB_PUSH_CONTACT` (RFC-8292 `sub` claim). The matching VAPID public key is *derived* from the private PEM at request time so there is no second secret to keep in sync. `routes/admin_notifications.py:push_subscribe` enforces the full W3C `PushSubscription` shape (`endpoint` + `keys.p256dh` + `keys.auth`) — partial blobs return 400. The 30-day FCM → VAPID rollout is owned by `scripts/migrate_fcm_to_vapid.py` (idempotent `pending → tombstoned → purged` state machine, addresses token-only legacy rows via `_id`); the admin endpoint `GET /api/admin/push/migration-status` exposes per-bucket counts + percentages for the AdminHealth panel. The Service Worker (`public/sw.js` v15) drops a stale legacy push subscription on `activate` so the next page load auto-resubscribes via VAPID. The legacy FCM `firebase_admin` / `FCM_SERVER_KEY` / `FIREBASE_SERVICE_ACCOUNT` env knobs are retired and now banned by the umbrella CI guard's `TODO_557_PATTERN` row.)*

## Stack

- **Frontend:** React 18, Vite, React Router, Tailwind CSS, Drizzle ORM
- **Backend:** Python 3.11, FastAPI, Gunicorn (Uvicorn workers)
- **Rust core:** `async-batch` worker
- **Validation/Codegen:** Zod, Orval
- **Build:** pnpm monorepo, esbuild, Docker

## Where things live

- **Frontend:** `artifacts/syrabit/`
- **Backend:** `artifacts/syrabit-backend/`
- **Embed worker (Cloudflare):** `artifacts/syrabit/workers/embed-worker/`
- **Edge proxy (Cloudflare):** `workers/edge-proxy/`
- **Bicep + ACA deploy:** `infra/azure/aca-syrabit-backend.bicep`, `.github/workflows/azure-container-apps-deploy.yml`
- **AWS Lambda batch jobs (Task #551):** `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf` + adapters in `artifacts/syrabit/services/backend/lambda_batch/`; migrated-jobs registry at `infra/aws/lambda/manifest.json`. Replaces the in-process `aca_jobs/*` loops on a 7-day shadow → cutover protocol.
- **AWS S3 Glacier Deep Archive (Task #551):** `artifacts/syrabit/infra/aws/glacier-archive.tf` (3 compliance buckets, 7-year retention) + restore endpoint `routes/admin_archive.py` + runbook `artifacts/syrabit/docs/infra/glacier-restore-runbook.md`.
- **Threat model:** `threat_model.md`
- **V4 architecture (source of truth):** `infra/v4-locked-architecture.md`
- **Four-cloud delegation matrix:** `infra/four-cloud-delegation.md`

## Architecture decisions

> **Architecture lock (Task #5, 2026-05-09):** [`infra/architecture-locked-2026.md`](infra/architecture-locked-2026.md) is the canonical implementation map of the 2026 blueprint — every section of the source spec is mirrored as `IMPLEMENTED | PARTIAL | MISSING | RETIRED` rows with source paths. The machine-readable companion is [`infra/architecture-matrix.json`](infra/architecture-matrix.json); CI guard [`scripts/check_architecture_lock.py`](scripts/check_architecture_lock.py) (wired into the umbrella `canonical_delegation_gate`) fails the build on source-path drift, retired-provider active reintroduction, or matrix schema drift. Future agents should read the lock doc first; this short index remains for at-a-glance navigation.

Short index — full text in [`docs/architecture/decisions.md`](docs/architecture/decisions.md).

- [Voice canonical specialists (#552 §G, reversed by §G-R)](docs/architecture/decisions.md#voice-canonical-specialists-task-552-g-2026-05-07-reversed-by-g-r-2026-05-09) — **Deepgram Aura-2 (English TTS primary) + ElevenLabs (named fallback)** + Google Neural2 (Indic TTS) + Deepgram Nova-3 (English STT) + Google Chirp_2 (Indic STT); AssemblyAI retired. (§G-R 2026-05-09 reversed the original §G Aura-2 retirement after the ElevenLabs free-plan API gate forced a $5/mo upgrade requirement.)
- [Sarvam Assamese-chat facade (#553)](docs/architecture/decisions.md#sarvam-assamese-chat-facade-task-553-2026-05-07) — Typed `providers/sarvam.py:chat()` with per-user monthly cap, success-rate health tile, and `<95%/1h` Sentry alert.
- [Cost split snapshot (#559)](docs/architecture/decisions.md#cost-split-post-task-559-snapshot-2026-05-07) — 40 % CF / 30 % GCP / 15 % Az / 10 % AWS / 5 % other (informational outcome of the canonical map, not a routing target).
- [Canonical specialist delegation (#559)](docs/architecture/decisions.md#canonical-specialist-delegation-task-559-2026-05-07) — One canonical primary + at most one named fallback per feature, enforced by the umbrella CI guard `check_canonical_delegation.py`.
- [Embedding strategy](docs/architecture/decisions.md#embedding-strategy) — Cloudflare Workers AI Gemma-300M + Qwen3-0.6B (1024-dim) → Pinecone; cache-only degraded mode on outage.
- [GCP credit-runway publisher (#565)](docs/architecture/decisions.md#gcp-credit-runway-publisher-task-565-2026-05-08) — Daily Lambda computes runway from BigQuery billing export, publishes to Upstash Redis; independent hourly freshness probe + CW alarms.
- [Chat dispatch (#554, #559, Task #2 2026 blueprint)](docs/architecture/decisions.md#chat-dispatch-task-554-2026-05-07-canonical-row-reaffirmed-by-task-559) — **Strict 3-position English chain `vertex → vertex_flash_lite → workers_ai_llama32_3b`** (flips on ≤90d runway to `workers_ai_llama32_3b → vertex_flash_lite → vertex`). **Strict 3-position Assamese chain `sarvam → vertex_assamese → retrieval_only`** (Vertex Flash + Assamese system-prompt as named fallback; deterministic retrieval-only tail when both LLM legs are exhausted). Azure OpenAI fully retired.
- [Voice canonical (Task #2 2026 blueprint)](docs/architecture/decisions.md#voice-canonical-specialists-task-552-g-2026-05-07-reversed-by-g-r-2026-05-09) — **ElevenLabs restored as English-TTS PRIMARY** (richest voice library; $5/mo Starter budgeted into the $100 ceiling); Deepgram Aura-2 named fallback; workers_ai weight-0 last-resort tail. STT chain unchanged (Deepgram Nova-3 → workers_ai).
- [Assamese-aware regional cache (Task #2)](docs/architecture/decisions.md#assamese-aware-regional-cache-task-2-2026) — Edge proxy stamps `X-Cache-Region: ne-india` for Assam + NE-India geo (`AS|ML|TR|MN|MZ|NL|AR`); falls back to `global` everywhere else. `ai_input_cache._key`, `kv_cache.get/set`, and `cf_tiered_cache` all fold the region into their cache keys + per-region hit/miss counters; admin cache panel renders a `per_region` tile rolling all three layers.
- [Admin Ops Console (Task #2)](docs/architecture/decisions.md#admin-ops-console-task-2-2026) — `GET /api/admin/ops/console` returns SLA ledger (rolling 24h + 7d success rate, p50/p95 latency, locked latency target, breach count per canonical-specialist chain), outage map (circuit-breaker state per provider), and read-only toggle viewer (operator env knobs + founder-locked degradation thresholds) in a single round-trip. AdminOpsConsole.jsx is wired as the `ops` section in AdminPage and polls every 30 s.
- [Content formatter (#494)](docs/architecture/decisions.md#content-formatter-15-6-task-494) — All polish flows go through `content_formatter.format_content` (Vertex 2.5 Flash → Llama-3.3-70b → passthrough); every doc carries a `formatted_by` audit field.
- [Vectorless RAG](docs/architecture/decisions.md#vectorless-rag) — Three-tier router (D1 tree-walk → Mongo BM25 → vector pass) fused with RRF before Pinecone rerank.
- [Secrets management](docs/architecture/decisions.md#secrets-management) — Azure Key Vault is source of truth; AWS Secrets Manager + Cloudflare Secrets are read-only replicas.
- [Observability (#558)](docs/architecture/decisions.md#observability-task-558-2026-05-07) — Errors-only Sentry Developer free tier + OTEL → GCP Cloud Trace as sole tracing exporter; Sentry Performance retired. PostHog Cloud product analytics: LCP-gated browser SDK in `artifacts/syrabit/index.html` (loads on LCP or 5s timeout, never blocks paint); server-side capture via `providers/posthog.py` for events the browser can't observe (Razorpay webhook `purchase_verified`, voice paywall, ACA-job emissions). No-op when `POSTHOG_API_KEY` unset.
- [PG to Mongo migration](docs/architecture/decisions.md#pg-to-mongo-migration) — Phase 2 (dual-write) complete on key user collections; Phase 3 (read-shadow) and 4 (cutover) pending.
- [Provider chain (#491, updated #554/#559)](docs/architecture/decisions.md#provider-chain-task-491-2026-05-07-updated-by-tasks-554-559) — Cerebras / Cohere / Voyage-AI retired; embed is single-source `workers_ai_custom`; rerank is Pinecone-only.
- [Free-tier cost minimization (#581)](docs/architecture/decisions.md#free-tier-cost-minimization-task-581-2026-05-07) — Ten levers (turn ladder, retrieval-first, Assamese gate, output sub-caps, OCR/long-context split, voice preview, free-tier-first MeterD ladder) to push free-cohort cost to ₹0.50–3/mo.
- [Perpetual $100/month budget (#549)](docs/architecture/decisions.md#perpetual-100month-budget-task-549-2026-05-07) — `MONTHLY_TOTAL_USD_CAP = $100`; voice paywall on `/tts` `/stt` `/voice/voice`; 60/80/95 % degradation ladder; CI guard `check_budget_ceiling.py`.
- [Cost minimization (#513)](docs/architecture/decisions.md#cost-minimization-task-513-2026-05-07) — Edge chat caps (30/mo + 3/day per anon-id) + locked `TOKEN_BUDGETS` + tier-routed dispatch + ACA right-sizing + MeterD `chat:cheaponly` flip.
- [Assamese content backfill](docs/architecture/decisions.md#assamese-content-backfill) — Nightly EventBridge Lambda translates English content → Assamese via IndicTrans2 + Vertex polish; CW alarms on stuck/failed passes.
- [Cost & runway model (#550)](docs/architecture/decisions.md#cost-runway-model-task-550-2026-05-07) — Phased credit-on infra value memo (P1 $120–320 → P4 $4–12k/mo); founder-locks (the $100 cap, voice paywall, degradation ladder) always win over memo numbers.


## Product

Syrabit.ai is an AI-powered educational platform for AHSEC Class 11/12 and Degree students in Assam. It offers bilingual (English + Assamese) localized learning across 55 subjects, including RAG notes, MCQs, flashcards, PYQ OCR, an in-app browser with grounded chat, admin CMS, credit-based monetization (Razorpay INR-only), and DPDP-compliance.

## User preferences

- Iterative development with clear communication on major changes.
- Detailed explanations for complex features and architectural decisions.
- Prioritize modularity and maintainability.
- Treat vectorless RAG and vector RAG as **complementary layers** — vector for semantic/paraphrased exam-Q search, vectorless (BM25 + tree-walk) for exact-term/formula/navigation queries.
- **No silent fallbacks** — fail loud, document trade-offs explicitly (V4 §12).
- **Supabase is the sole auth provider** — student, staff, and admin identity (sign-up, sign-in, OAuth, password reset, email verification, MFA) all flow through Supabase Auth. The legacy email/password JWT path in `routes/auth.py` and the standalone Google OAuth flow are retired in favor of Supabase. Backend verifies Supabase JWTs via the Supabase JWKS; `JWT_SECRET` / `ADMIN_JWT_SECRET` remain only for short-lived internal service-to-service tokens (e.g. edge-proxy → backend), never for user sessions. The `GOOGLE_OAUTH_CLIENT_SECRET` knob is no longer required since Supabase brokers the Google IdP.

## Gotchas

- **Backend import check:** Always run `python -c "import server"` from `artifacts/syrabit-backend/` before pushing.
- **ACA Deploy config:** Bicep ARM PATCH must include `properties.configuration.ingress.traffic = [{latestRevision: true, weight: 100}]` and `targetPort: 8000`.
- **Bicep template drift:** The Bicep template (`infra/azure/aca-syrabit-backend.bicep`) must precisely mirror the runtime contract.
- **Pinecone dimension incompatibility:** Pinecone embedding dimension is 1024. Future embed providers must match this dimension or be quarantined.
- **`OriginGate` lock-step rotation:** `ORIGIN_SHARED_SECRET` (ACA env) and `BACKEND_ORIGIN_SECRET` (syrabitworker binding) MUST be equal. Same for `D1_SYNC_SECRET`.
- **Token budgets are LOCKED:** `cost_caps.TOKEN_BUDGETS` ceilings are founder-locked (Task #513 §B). Raising any value requires a `# COST-CAP-OVERRIDE: <reason>` comment on the changed line AND a Sentry-annotated changelog entry; `tests/test_cost_caps.py` walks the source file and fails CI when either signal is missing. Same applies to bumping the edge chat caps (`CHAT_CAP_MONTHLY=30`, `CHAT_CAP_DAILY=3`) in `workers/edge-proxy/src/index.ts`.
- **Monthly USD ceiling is LOCKED at $100 (Task #549):** `_DEFAULT_MONTHLY_TOTAL_USD_CAP` (cost_caps.py) and `MeterDConfig.cap_usd` (credit_burn_meter.py) defaults must remain ≤ $100 unless the changed line carries a `# COST-CAP-OVERRIDE: <reason>` marker. `scripts/check_budget_ceiling.py` enforces this in CI and additionally validates that the three degradation thresholds (60 / 80 / 95 %) stay strictly increasing inside (0.0, 1.0).
- **`/api/me/quota` edge cache TTL = 5s (intentional, not 60s):** Task #513 §A originally suggested ~60s for the edge `/api/me/quota` response cache, but we ship `Cache-Control: private, max-age=5, s-maxage=5` because the chat post-bump is the only writer of the displayed counters and a 5-second window keeps the SPA's "remaining turns" banner from showing a stale value across more than one tick. This is implementation drift accepted by the round-7 code review; raising the TTL needs a corresponding UX review of banner staleness.
- **K.2 deterministic cache scope:** see [Extended Gotchas](docs/architecture/decisions.md#k2-deterministic-cache-scope-chat-adjacent) — `ai_input_cache` covers formatter/translate/OCR/MCQ/flashcard/definition; live `routes/ai_chat.py` is excluded by policy.
- **Cache-effectiveness observability (#571):** see [Extended Gotchas](docs/architecture/decisions.md#cache-effectiveness-observability-task-571) — admin `/api/health/cache` + nightly Lambda → CloudWatch alarms (hit-ratio < 0.30, cardinality 3× spike).

## Cache calendar

- **All Cache calendar details** moved to [Extended Gotchas](docs/architecture/decisions.md#cache-calendar-knob-task-575) — exam/results-mode TTL stretch (30d→90d for mcq/flashcard/definition/pyq), `/api/health/season` edge wiring, PYQ wiring scope, window-adding rules, admin banner, and unchanged founder locks.

## Pointers

- **2026 architecture lock (Task #5):** `infra/architecture-locked-2026.md` (+ `infra/architecture-matrix.json`)
- **V4 spec:** `infra/v4-locked-architecture.md`
- **Four-cloud delegation matrix:** `infra/four-cloud-delegation.md`
- **GCP landing zone:** `artifacts/syrabit/docs/infra/gcp-landing-zone.md`
- **Azure landing zone:** `artifacts/syrabit/docs/infra/azure-landing-zone.md`
- **AWS landing zone:** `artifacts/syrabit/docs/infra/aws-landing-zone.md`
- **ACA cutover runbook:** `artifacts/syrabit/docs/infra/aca-cutover.md`
- **Provider decommission rationale (#347):** `artifacts/syrabit/docs/infra/providers-task-347-decommission.md`
- **Phased credit-runway cost model (Task #550):** `artifacts/syrabit/docs/infra/credit-runway-cost-model.md`
- **Cache-effectiveness audit (Task #571):** `artifacts/syrabit/docs/infra/cache-effectiveness-audit.md`
- **Exam-window cache calendar (Task #575):** `artifacts/syrabit-backend/config/exam_calendar.yaml` (data) + `artifacts/syrabit-backend/cache_calendar.py` (loader/classifier) + `routes/admin_season.py` (`GET /api/health/season`)
- **AWS Glacier restore runbook (Task #551):** `artifacts/syrabit/docs/infra/glacier-restore-runbook.md`
- **AWS Lambda batch-jobs manifest (Task #551 CI guard):** `infra/aws/lambda/manifest.json`
- **Skills index:** `.local/skills/`