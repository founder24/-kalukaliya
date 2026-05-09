# Syrabit 2026 Architecture — LOCKED

> **Status: LOCKED — 2026-05-09** (Task #5 — companion to `infra/four-cloud-delegation.md` and `infra/v4-locked-architecture.md`.)
> **Owner:** founder@syrabit.ai
> **Source blueprint:** [`attached_assets/Pasted--Syrabit-Full-Updated-Architecture-Provider-Breakdown-C_1778322896768.txt`](../attached_assets/Pasted--Syrabit-Full-Updated-Architecture-Provider-Breakdown-C_1778322896768.txt)
> **Machine-readable matrix:** [`infra/architecture-matrix.json`](./architecture-matrix.json)
> **CI guard:** [`scripts/check_architecture_lock.py`](../scripts/check_architecture_lock.py)
> **Founder locks (always win over anything in this doc):** `$100/mo` USD cap • voice paywall on `/voice/tts /voice/stt /voice/voice` • `60/80/95 %` degradation ladder • Sarvam = sole Assamese head • Supabase = sole auth • Pinecone dim = 1024 • V4 §12 *no silent fallbacks*.

This document is the **canonical implementation map** of the 2026 blueprint. Every section below mirrors a section of the source blueprint and lists each row's status (`IMPLEMENTED | PARTIAL | MISSING | RETIRED`) and the responsible source path(s). Downstream cleanup, SEO, and bot-management tasks (#6 → #11) walk this matrix instead of greppimg the codebase blind.

When the blueprint and this matrix disagree, **this matrix wins** and the blueprint is annotated. When this matrix and `infra/four-cloud-delegation.md` disagree on a per-feature canonical owner, the four-cloud delegation map wins (it is the routing contract).

---

## Status legend

| Status | Meaning |
|---|---|
| `IMPLEMENTED` | Code path exists, is wired into the production hot path, and is covered by at least one CI guard / test. |
| `PARTIAL` | Code path exists but the blueprint requires an extension that is owned by a downstream task in this plan. The "note" column names the task. |
| `MISSING` | Blueprint row has no code path yet. Owned by a downstream task. |
| `RETIRED` | Blueprint row was once true but has been decommissioned per a Task # decision. The "note" column names the decision. |

---

## §1 — Vision

| Item | Status | Source path(s) |
|---|---|---|
| AI-native educational infrastructure (retrieval > inference, curriculum constrains entropy, materialization compounds) | IMPLEMENTED | `replit.md`, `infra/v4-locked-architecture.md` |

---

## §2 — Core Architectural Principles

| Item | Status | Source path(s) | Note |
|---|---|---|---|
| Edge-first delivery (CF Pages + Workers + CDN) | IMPLEMENTED | `workers/edge-proxy/src/index.ts`, `artifacts/syrabit/workers/embed-worker/` | |
| Retrieval-heavy AI (3-tier router + RRF fusion) | IMPLEMENTED | `artifacts/syrabit-backend/rag_router.py`, `rag.py`, `retrieval_first.py` | |
| Deterministic caching (`ai_input_cache`, `kv_cache`) | IMPLEMENTED | `artifacts/syrabit-backend/ai_input_cache.py`, `kv_cache.py`, `cache.py` | |
| Multi-provider orchestration (canonical map) | IMPLEMENTED | `artifacts/syrabit-backend/config.py`, `infra/four-cloud-delegation.md` | |
| Curriculum grounding (syllabus graph + linker) | IMPLEMENTED | `artifacts/syrabit-backend/syllabus_linker.py`, `syllabus_embedder.py`, `routes/syllabus.py` | |
| Progressive degradation (60/80/95 ladder) | IMPLEMENTED | `artifacts/syrabit-backend/cost_caps.py`, `credit_burn_meter.py` | |
| Materialized educational outputs | IMPLEMENTED | `artifacts/syrabit-backend/seo_engine.py`, `seo_writes.py`, `seo_fanout.py` | |
| SEO-driven distribution | PARTIAL | `artifacts/syrabit-backend/seo_engine.py`, `seo_internal_linker.py` | Task #11 lifts `H1=chapter-topic` + schema.org coverage. |

---

## §3 — High-Level Architecture

| Item | Status | Source path(s) |
|---|---|---|
| PWA (React + Vite, service worker) | IMPLEMENTED | `artifacts/syrabit/`, `artifacts/syrabit/public/sw.js` |
| CF Edge Layer (Pages + CDN + Workers + KV + D1) | IMPLEMENTED | `workers/edge-proxy/src/index.ts`, `artifacts/syrabit-backend/d1_mirror.py`, `d1_sync.py` |
| Edge Gateway (auth, quota, traceparent, budget) | IMPLEMENTED | `workers/edge-proxy/src/index.ts`, `artifacts/syrabit-backend/middleware.py`, `auth_deps.py` |
| ACA FastAPI Core (eastus2) | IMPLEMENTED | `artifacts/syrabit-backend/server.py`, `infra/azure/aca-syrabit-backend.bicep` |
| RAG Pipeline (vector + BM25 + graph) | IMPLEMENTED | `artifacts/syrabit-backend/rag.py`, `rag_router.py` |
| Provider Delegation Layer | IMPLEMENTED | `artifacts/syrabit-backend/llm.py`, `assamese_dispatch.py`, `free_tier_dispatch.py` |
| Response Formatter + Materializer | IMPLEMENTED | `artifacts/syrabit-backend/content_formatter.py` |
| Streaming SSE chat | IMPLEMENTED | `artifacts/syrabit-backend/routes/ai_chat.py`, `do_chat.py` |

---

## §4.1 — Cloudflare Layer (Primary Edge Infrastructure)

| Item | Status | Source path(s) | Note |
|---|---|---|---|
| CF Pages — PWA hosting | IMPLEMENTED | `artifacts/syrabit/CLOUDFLARE_PAGES.md` | |
| Workers — edge compute | IMPLEMENTED | `workers/edge-proxy/src/index.ts`, `artifacts/syrabit/workers/embed-worker/`, `artifacts/syrabit/workers/email-worker/` | |
| CDN + tiered cache | IMPLEMENTED | `artifacts/syrabit-backend/cf_tiered_cache.py`, `cf_cache_rules.py` | |
| KV — deterministic AI cache | IMPLEMENTED | `artifacts/syrabit-backend/kv_cache.py` | |
| D1 — syllabus graph + metadata | IMPLEMENTED | `artifacts/syrabit-backend/d1_mirror.py`, `d1_sync.py` | |
| R2 — assets + OCR documents + final backups | IMPLEMENTED | `artifacts/syrabit-backend/r2_storage.py` | |
| WAF + Bot Management | PARTIAL | `workers/edge-proxy/src/index.ts`, `artifacts/syrabit-backend/cf_bot_report.py`, `cf_bot_crosscheck.py` | Task #9: split verified-bot KV fast path so Googlebot/Perplexity/OAI-SearchBot don't share the 3000-RPM bucket. |
| Turnstile — anti-abuse | IMPLEMENTED | `artifacts/syrabit-backend/turnstile.py`, `routes/turnstile_config.py` | |
| Cron Triggers — prewarming + scheduled tasks | PARTIAL | `artifacts/syrabit-backend/cron_heartbeats.py` | Task #13 adds prewarm cron for chapter MCQs/notes. |

---

## §4.2 — Azure Layer (Primary Backend Compute)

| Item | Status | Source path(s) | Note |
|---|---|---|---|
| Azure Container Apps — FastAPI runtime | IMPLEMENTED | `infra/azure/aca-syrabit-backend.bicep`, `.github/workflows/azure-container-apps-deploy.yml` | DR cutover `eastus2 → westus3` per V4 §8. |
| Azure Key Vault — secrets source of truth | IMPLEMENTED | `docs/architecture/decisions.md` | AWS SM + CF Secrets are read-only replicas. |
| OCR scratch + warm media (Cloudflare R2 — Azure Blob retired) | IMPLEMENTED | `artifacts/syrabit-backend/r2_storage.py`, `artifacts/syrabit-backend/routes/ai_chat.py`, `artifacts/syrabit-backend/routes/pyq.py` | Task #46 (2026-05-09) — Path A: **R2 is canonical** for warm media (chapter PDFs / audio / admin uploads via `r2_storage.r2_upload/r2_presign/r2_delete`) and is the **named scratch surface for OCR if scratch is ever persisted**. The OCR endpoints (`POST /api/ai/ocr-image`, `POST /api/admin/pyq/agentic-process`) currently run as in-memory Vertex Vision round-trips (`_OCR_MAX_BYTES=8MB` bounded buffer → magic-byte sniff → Vertex; nothing persists), so the storage call-site is wired but unused; any future "save scratch" feature MUST land on R2 (not Azure Blob). Azure Blob OCR/media is **retired** for this row by the same task. Doc-closure follow-up Task #48 (2026-05-09) updated `replit.md` §4.2 + the 2026-05-09 baseline-audit row to match this state. |
| Azure Monitor + Application Insights | RETIRED | — | Sentry + GCP Cloud Trace replace App Insights (Task #558). |

---

## §4.3 — AWS Layer (Cold + Async)

| Item | Status | Source path(s) |
|---|---|---|
| SQS — async queues | IMPLEMENTED | `artifacts/syrabit-backend/sqs_fanout.py` |
| Lambda — scheduled jobs | IMPLEMENTED | `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf`, `infra/aws/lambda/manifest.json`, `artifacts/syrabit/services/backend/lambda_batch/` |
| Glacier Deep Archive — 7-yr DPDP retention | IMPLEMENTED | `artifacts/syrabit/infra/aws/glacier-archive.tf`, `artifacts/syrabit-backend/routes/admin_archive.py` |
| SES — transactional email (sole tier-1) | IMPLEMENTED | `artifacts/syrabit-backend/email_templates.py`, `bulk_email.py` |

---

## §5.1 — AI Inference Providers (Chat)

| Item | Status | Source path(s) |
|---|---|---|
| English chat: `vertex → vertex_flash_lite → workers_ai_llama32_3b` (head flips on ≤90d runway) | IMPLEMENTED | `artifacts/syrabit-backend/config.py`, `cost_caps.py`, `providers/vertex_chat.py`, `providers/workers_ai.py` |
| Assamese chat: `sarvam → vertex_assamese → retrieval_only` | IMPLEMENTED | `artifacts/syrabit-backend/assamese_dispatch.py`, `providers/sarvam.py` |
| Content formatter: Vertex 2.5 Flash → Llama-3.3-70b → passthrough | IMPLEMENTED | `artifacts/syrabit-backend/content_formatter.py`, `vertex_format.py` |

---

## §5.2 — OCR Providers

| Item | Status | Source path(s) | Note |
|---|---|---|---|
| Indic OCR — Google Vision | IMPLEMENTED | `artifacts/syrabit-backend/providers/google_vision.py` | |
| General OCR — Workers AI Vision | IMPLEMENTED | `artifacts/syrabit-backend/providers/workers_ai.py` | |
| Vertex Vision | RETIRED | — | Retired by Task #554 — Workers AI sole vision. |

---

## §5.3 — Voice Stack

| Item | Status | Source path(s) |
|---|---|---|
| STT English — Deepgram Nova-3 | IMPLEMENTED | `artifacts/syrabit-backend/providers/deepgram.py`, `routes/voice.py` |
| STT Assamese — Google Chirp_2 → Workers AI Whisper | IMPLEMENTED | `artifacts/syrabit-backend/providers/google_stt.py` |
| TTS English — ElevenLabs primary, Deepgram Aura-2 fallback | IMPLEMENTED | `artifacts/syrabit-backend/providers/elevenlabs.py`, `providers/deepgram.py` |
| TTS Assamese — Google Neural2 | IMPLEMENTED | `artifacts/syrabit-backend/providers/google_tts.py` |
| Voice paywall on `/voice/tts /voice/stt /voice/voice` | IMPLEMENTED | `artifacts/syrabit-backend/routes/voice.py`, `auth_deps.py` |

---

## §5.4 — Payments

| Item | Status | Source path(s) |
|---|---|---|
| Razorpay subscriptions (INR-only) | IMPLEMENTED | `artifacts/syrabit-backend/routes/admin_monetization.py`, `routes/admin_billing.py` |
| Glacier — receipt archive (7-yr DPDP) | IMPLEMENTED | `artifacts/syrabit/infra/aws/glacier-archive.tf` |

---

## §5.5 — Analytics & Monitoring

| Item | Status | Source path(s) |
|---|---|---|
| PostHog — product analytics (LCP-gated SDK) | IMPLEMENTED | `artifacts/syrabit-backend/providers/posthog.py`, `artifacts/syrabit/index.html` |
| Cloudflare Analytics | IMPLEMENTED | `artifacts/syrabit-backend/cf_web_analytics.py`, `routes/cf_web_analytics_config.py` |
| Sentry — errors-only Developer tier | IMPLEMENTED | `artifacts/syrabit-backend/server.py` |
| GCP Cloud Trace — sole tracing exporter | IMPLEMENTED | `artifacts/syrabit-backend/tracing.py`, `routes/health_otel.py` |

---

## §6 — Database Architecture

### §6.1 MongoDB Atlas
Users, chat history, generated content, OCR results, metadata, moderation logs.
**Status:** IMPLEMENTED — `artifacts/syrabit-backend/db_ops.py`, `db_dualwrite.py`, `models.py`.

### §6.2 Cloudflare D1
Syllabus graph + chapter metadata + edge lookup tables + SEO route metadata + cache indexes.
**Status:** IMPLEMENTED — `artifacts/syrabit-backend/d1_mirror.py`, `d1_sync.py`.

### §6.3 Pinecone
Embeddings + semantic retrieval + rerank candidates (1024-dim, `aws-ap-south-1`).
**Status:** IMPLEMENTED — `artifacts/syrabit-backend/providers/pinecone_ai.py`, `providers/workers_embed.py`.

### §6.4 Cloudflare KV — Deterministic AI Cache
MCQs, flashcards, definitions, translation cache, precomputed notes.
**Status:** IMPLEMENTED — `artifacts/syrabit-backend/kv_cache.py`, `ai_input_cache.py`.

### §6.5 Upstash Redis — Hot Cache
Rate limiting + hot retrieval cache + quota tracking + session coordination.
**Status:** IMPLEMENTED — `artifacts/syrabit-backend/cache.py`, `rag_cache.py`.

---

## §7 — Full AI Pipeline (8 stages)

| Stage | Item | Status | Source path(s) | Note |
|---|---|---|---|---|
| 1 | Request intake (auth/quota/trace/cache/budget at edge) | IMPLEMENTED | `workers/edge-proxy/src/index.ts`, `artifacts/syrabit-backend/middleware.py` | |
| 2 | Intent resolution (board/class/subject/chapter/lang) | IMPLEMENTED | `artifacts/syrabit-backend/subject_router.py`, `recall_intent.py`, `prompt_normalizer.py` | |
| 3 | Retrieval dispatch (8 sources: syllabus graph, KV, Mongo, Pinecone, BM25, PYQ, textbook, materialized SEO) | IMPLEMENTED | `artifacts/syrabit-backend/rag_router.py` | |
| 4 | Retrieval fusion (RRF + syllabus weighting + recency + trust) | IMPLEMENTED | `artifacts/syrabit-backend/rag.py`, `grounded_answer.py` | |
| 5 | Prompt synthesis | IMPLEMENTED | `artifacts/syrabit-backend/prompts.py`, `chat_turn_context.py` | |
| 6 | Model delegation (dynamic routing matrix) | IMPLEMENTED | `artifacts/syrabit-backend/llm.py`, `free_tier_dispatch.py` | |
| 7 | Response formatting (concise/exam/markdown/MCQs/flashcards/summaries/PYQ/bilingual) | IMPLEMENTED | `artifacts/syrabit-backend/content_formatter.py` | |
| 8 | Deterministic materialization (definitions, MCQs, chapter notes, flashcards, PYQ, glossary) | PARTIAL | `artifacts/syrabit-backend/seo_engine.py`, `seo_writes.py` | Task #13 prewarm + Task #12 FAQ panels close the loop. |

---

## §8 — Cache Architecture (5 layers)

| Layer | Stores | Status | Source path(s) |
|---|---|---|---|
| L1 — Browser | JS bundles, CSS, fonts, static assets, offline shell | IMPLEMENTED | `artifacts/syrabit/public/sw.js`, `artifacts/syrabit/vite.config.js` |
| L2 — CF CDN | SEO pages, chapter notes, static APIs, syllabus endpoints | IMPLEMENTED | `artifacts/syrabit-backend/cf_cache_rules.py`, `cf_tiered_cache.py` |
| L3 — KV AI Cache | Deterministic AI outputs, translations, MCQs, flashcards, definitions | IMPLEMENTED | `artifacts/syrabit-backend/kv_cache.py`, `ai_input_cache.py` |
| L4 — Redis Hot Cache | Hot retrieval, quotas, translation hot paths, sessions | IMPLEMENTED | `artifacts/syrabit-backend/cache.py`, `rag_cache.py` |
| L5 — D1 Metadata Cache | Syllabus lookup, chapter relationships, SEO metadata, retrieval indexes | IMPLEMENTED | `artifacts/syrabit-backend/d1_mirror.py` |

---

## §9 — Advanced Cache Optimization (Tasks #571 → #577)

| Task | Item | Status | Source path(s) | Note |
|---|---|---|---|---|
| #571 | Cache intelligence (`/api/health/cache` + nightly Lambda → CW alarms) | IMPLEMENTED | `artifacts/syrabit-backend/routes/admin_cache.py`, `artifacts/syrabit/docs/infra/cache-effectiveness-audit.md` | |
| #572 | Semantic query fingerprinting (paraphrase → shared cache key, multilingual) | MISSING | — | Task #10 ships fingerprint normalizer for chat-adjacent caches. |
| #573 | Deterministic educational rendering (templates, fixed answer structures) | PARTIAL | `artifacts/syrabit-backend/content_formatter.py` | Task #10 wires fixed-structure answer templates per type. |
| #574 | Prewarming engine (chapters / trending exams / PYQ frequency / search analytics / exam calendar) | MISSING | — | Task #13 adds CF Cron prewarm of chapter MCQs/notes. |
| #575 | Dynamic TTL engine (30→90→120 days exam-window stretch) | IMPLEMENTED | `artifacts/syrabit-backend/cache_calendar.py`, `config/exam_calendar.yaml`, `routes/admin_season.py` | |
| #576 | Regional cache localization (`X-Cache-Region: ne-india` for Assam, `global` elsewhere) | IMPLEMENTED | `workers/edge-proxy/src/index.ts`, `artifacts/syrabit-backend/ai_input_cache.py`, `kv_cache.py` | |
| #577 | Retrieval result cache (vector + BM25 + fusion outputs) | IMPLEMENTED | `artifacts/syrabit-backend/rag_cache.py` | |

---

## §10 — SEO Architecture

| Item | Status | Source path(s) | Note |
|---|---|---|---|
| Programmatic route `/board/class/subject/chapter/type` | IMPLEMENTED | `artifacts/syrabit-backend/seo_engine.py`, `seo_fanout.py` | |
| Generated content types (notes, MCQs, flashcards, PYQs, summaries, definitions, revision sheets) | PARTIAL | `artifacts/syrabit-backend/seo_engine.py` | Task #11 lifts H1=chapter-topic + schema.org `LearningResource` / `Course` / `FAQPage` / `Quiz` and `hreflang as-IN/en-IN` + `geo.region=IN-AS`. |
| IndexNow submission (Bing wired) | PARTIAL | `artifacts/syrabit-backend/bing_submit_client.py`, `google_indexing_client.py` | Task #11 extends IndexNow to Yandex + verifies Google Indexing API. |
| Internal linker + entity SEO health | IMPLEMENTED | `artifacts/syrabit-backend/seo_internal_linker.py`, `entity_seo_health.py` | |
| AEO answer cards + FAQ JSON-LD | PARTIAL | `artifacts/syrabit-backend/routes/topic_answer_cards.py`, `routes/topic_faq_jsonld.py` | Task #12 materializes 40-60 word Quick-Answer + FAQ panel per chapter. |

---

## §11 — Voice Architecture

Voice flow: Student Voice → STT → Intent Resolution → Retrieval → Model Delegation → TTS → Streamed Audio.

| Item | Status | Source path(s) |
|---|---|---|
| Voice flow end-to-end | IMPLEMENTED | `artifacts/syrabit-backend/routes/voice.py` |
| Bilingual tutoring + Assamese explanations + exam revision mode + spoken summaries + accessibility | IMPLEMENTED | `artifacts/syrabit-backend/routes/voice.py`, `assamese_dispatch.py` |

---

## §12 — OCR Architecture

OCR flow: PDF/Image upload → OCR detection → Indic routing → text extraction → structure parsing → retrieval indexing → PYQ materialization.

| Item | Status | Source path(s) |
|---|---|---|
| OCR end-to-end (textbooks, PYQs, handwritten, school notes, Assamese docs) | IMPLEMENTED | `artifacts/syrabit-backend/routes/pyq.py`, `providers/google_vision.py` |

---

## §13 — Security Architecture

| Item | Status | Source path(s) | Note |
|---|---|---|---|
| Authentication — Supabase sole IdP (sign-up, sign-in, OAuth, password reset, MFA) | PARTIAL | `artifacts/syrabit-backend/auth_deps.py`, `oidc_auth.py`, `routes/auth.py`, `supabase_jwks.py`, `aca_jobs/supabase_auth_canary.py`, `scripts/verify_supabase_mirror.py` | Task #47 (2026-05-09) prep PR landed: JWKS local verifier with 1h fresh + 5min stale-on-error cache (`supabase_jwks.verify_supabase_jwt`), the synthetic canary (`aca_jobs/supabase_auth_canary.py`), and the `db.users` ↔ Supabase Auth reconciliation script (`scripts/verify_supabase_mirror.py`) all live. **Nothing in the request hot path calls the new verifier yet** — `routes/auth.py:supabase_session` still HTTPs `_supa_client.auth.get_user(token)` and still mints `JWT_SECRET`-signed `syrabit_session` cookies via `create_access_token`. Destructive cutover (delete the four legacy `/auth/*` endpoints, replace `auth.get_user` with `verify_supabase_jwt`, rotate cookie to `syrabit_session_v2`, gate the whole flip behind `SUPABASE_ONLY_AUTH=1` for 48h rollback grace) runs in the weeknight 23:00–01:00 IST maintenance window per `docs/runbooks/task-47-supabase-auth-cutover.md`. Row stays PARTIAL until that destructive PR merges. `JWT_SECRET` is retained ONLY for short-lived service-to-service tokens after cutover. |
| Edge security — WAF + DDoS + rate limit + bot detection + abuse scoring | PARTIAL | `workers/edge-proxy/src/index.ts`, `artifacts/syrabit-backend/cf_bot_report.py` | Task #9 adds verified-bot KV fast path so Googlebot/Perplexity/OAI-SearchBot don't share the 3000-RPM ban-bucket. |
| Secrets management — Azure KV primary, AWS SM + CF Secrets read-only replicas | IMPLEMENTED | `docs/architecture/decisions.md` | |
| OriginGate (`X-Origin-Auth` lock-step rotation) | IMPLEMENTED | `workers/edge-proxy/src/index.ts`, `artifacts/syrabit-backend/middleware.py` | |

---

## §14 — Observability

| Task | Item | Status | Source path(s) |
|---|---|---|---|
| #569 | W3C tracing + provider spans + latency + cost telemetry + cache metrics + circuit-state + canaries | IMPLEMENTED | `artifacts/syrabit-backend/tracing.py`, `ai_gateway_observability.py`, `vertex_breaker.py` |
| #570 | Synthetic user journeys + SLA ledger + blast-radius + provider outage map + ops console | IMPLEMENTED | `artifacts/syrabit-backend/routes/admin_ops_console.py`, `slo_emitter.py`, `routes/admin_observability_canary.py` |
| Core | Cache hit ratio, retrieval latency, provider spend, token usage, fallback frequency, hallucination rate, OCR accuracy, Assamese quality | IMPLEMENTED | `artifacts/syrabit-backend/metrics.py`, `chat_speedup_metrics.py`, `memory_brain_metrics.py` |

---

## §15 — Cost Governance

| Item | Status | Source path(s) | Note |
|---|---|---|---|
| Hard budget controls — RPM caps + token caps + provider quotas + emergency degradation | IMPLEMENTED | `artifacts/syrabit-backend/cost_caps.py` | `MONTHLY_TOTAL_USD_CAP = $100` (founder-locked). |
| Progressive degradation 60/80/95 % ladder + paywall + cache-only at 100 % | IMPLEMENTED | `artifacts/syrabit-backend/credit_burn_meter.py`, `credit_burn_meter_runtime.py` | |
| Heavy-free user flow (chat → retrieval → cache → prewarm → edge) | PARTIAL | `artifacts/syrabit-backend/free_tier_dispatch.py`, `retrieval_first.py` | Task #13 prewarm closes the prewarm leg. |

---

## §16 — PWA Experience

| Item | Status | Source path(s) |
|---|---|---|
| Installable + offline shell + standalone + app shortcuts + low-data + bilingual UI + offline revision notes | IMPLEMENTED | `artifacts/syrabit/public/sw.js`, `artifacts/syrabit/public/manifest.json` |

---

## §17 — Build Phases

| Phase | Items | Status | Note |
|---|---|---|---|
| Phase 1 — MVP (CF Pages + Workers + KV + FastAPI + Mongo + Gemini + simple RAG + syllabus graph) | All shipped | IMPLEMENTED | |
| Phase 2 — Scale (Pinecone + Sarvam routing + Redis hot cache + prewarming + OCR + SEO) | Prewarm pending | PARTIAL | Task #13 ships prewarm engine; everything else live. |
| Phase 3 — AI Infra (observability intelligence + dynamic provider scoring + advanced cache analytics + synthetic journeys + ops console + multilingual optimization) | Live | IMPLEMENTED | `routes/admin_ops_console.py`, `cliffhanger_engine.py`. |

---

## §18 — Long-Term Strategic Moats

| Moat | Strength | Status | Source path(s) | Note |
|---|---|---|---|---|
| Syllabus graph | high | IMPLEMENTED | `artifacts/syrabit-backend/syllabus_linker.py` | |
| Assamese educational corpus | extremely high | PARTIAL | `artifacts/syrabit-backend/aca_jobs/as_translation_backfill.py` | Backfill nightly; corpus growth ongoing. |
| Deterministic educational cache | very high | IMPLEMENTED | `artifacts/syrabit-backend/ai_input_cache.py`, `kv_cache.py` | |
| PYQ archive | high | IMPLEMENTED | `artifacts/syrabit-backend/routes/pyq.py` | |
| Multilingual educational retrieval | high | IMPLEMENTED | `artifacts/syrabit-backend/rag_router.py`, `providers/workers_indic.py` | |
| Edge materialization | extremely high | IMPLEMENTED | `artifacts/syrabit-backend/seo_engine.py`, `seo_writes.py` | |

---

## §19 — Final Architectural Positioning

> **"Curriculum-constrained educational intelligence infrastructure"** — retrieval compounds, cache compounds, SEO compounds, educational artifacts compound, inference dependency decreases over time.

This positioning is encoded in `replit.md`, `infra/v4-locked-architecture.md`, and this matrix.

---

## CI guard contract (`scripts/check_architecture_lock.py`)

The guard reads `infra/architecture-matrix.json` on every PR and fails the build when:

1. **Source-path drift** — any path listed in a `source_paths` array no longer exists (catches accidental file deletions during cleanup tasks).
2. **Retired-provider regression** — any token from `retired_providers` reappears in active code outside an `retired_provider_allowlist_dirs` directory or a removal-note line (`# Task #XYZ`, `removed`, `retired`, `deprecated`, `legacy`, `decommission`).
3. **Schema drift** — the JSON file is missing required keys (`founder_locks`, `sections[*].rows[*].status`, `sections[*].rows[*].source_paths`).

The guard is invoked from `artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py:main()` so the `canonical_delegation_gate` job in `.github/workflows/azure-container-apps-deploy.yml` enforces it pre-deploy.

---

## Decision log

- **2026-05-09 (Task #5)** — Document created. 19 sections of the source blueprint mapped to source paths and status. Three rows annotated as `RETIRED` against blueprint defaults: Vertex Vision (#554), App Insights (#558), and the blueprint's "JWT + Google OAuth + bcrypt + session rotation" auth row (replaced by Supabase per `replit.md` user-preferences). Five rows annotated as `PARTIAL` or `MISSING` and explicitly tied to the downstream task that closes them (Tasks #9 / #10 / #11 / #12 / #13).
