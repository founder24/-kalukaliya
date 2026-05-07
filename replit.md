# Syrabit.ai

Syrabit.ai is an AI-powered educational platform providing bilingual localized learning for students in Assam across 55 subjects.

## Run & Operate

- **Frontend dev:** `cd artifacts/syrabit && PORT=5000 pnpm dev`
- **Backend dev:** `cd artifacts/syrabit-backend && gunicorn server:app -c gunicorn.conf.py`
- **Mockup sandbox:** `pnpm --filter @workspace/mockup-sandbox run dev`
- **Health check:** `https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io/api/health`
- **Required env vars (ACA, from Azure KV):** `MONGO_URL`, `JWT_SECRET`, `ADMIN_JWT_SECRET`, `AZURE_OPENAI_API_KEY`, `RAZORPAY_KEY_SECRET`, `WORKERS_EMBED_SECRET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `EMAIL_PROVIDER`, `EMAIL_FALLBACK`, `EMBED_PROVIDER_PRIMARY`, `WORKERS_EMBED_URL`.

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
- **Threat model:** `threat_model.md`
- **V4 architecture (source of truth):** `infra/v4-locked-architecture.md`
- **Four-cloud delegation matrix:** `infra/four-cloud-delegation.md`

## Architecture decisions

- **Cost split:** 40% Cloudflare, 30% Azure, 20% AWS, 10% GCP.
- **Embedding strategy:** Primary is Gemma-300M + Qwen3-0.6B on Cloudflare Workers AI (1024-dim) to Pinecone. On primary outage, system enters cache-only degraded mode, queuing fresh content for replay.
- **Chat dispatch:** Azure `gpt-4.1-nano` is primary, falling back to Workers-AI Mistral-7B, then Llama-3.2-3B. Vertex is for `content_format` only.
- **Content formatter (§15 §6, Task #494):** All notebook/study/exam polish flows route through `content_formatter.format_content` — Vertex Gemini 2.5 Flash primary → Workers-AI Llama-3.3-70b fallback → passthrough on dual outage / Assamese purity-gate rejection. Every polished Mongo doc carries a `formatted_by` audit field; admin health panel reports per-formatter rolling counts.
- **Vectorless RAG:** A three-tier router performs tree-walk on D1 syllabus, then BM25 on Mongo, then a vector pass. Results are fused with RRF before Pinecone rerank.
- **Secrets management:** Azure Key Vault is the source of truth, with AWS Secrets Manager and Cloudflare Secrets as read-only replicas.
- **Observability:** Sentry Performance for tracing, OTEL to GCP Cloud Trace for long-term retention.
- **PG to Mongo Migration:** Phase 2 complete for key user data; Phase 3 (read-shadow) and 4 (cutover) are pending.
- **Provider chain (Task #491, 2026-05-07):** Cerebras, Cohere, and Voyage-AI fully retired. Embedding stack is single-source `workers_ai_custom` (Gemma-300M + Qwen3-0.6B, 1024-dim) with Azure OpenAI / Workers-AI as fallback; rerank is Pinecone-only. CI guard: `artifacts/syrabit-backend/scripts/check_dead_providers.py` bans `cerebras|cohere|voyage_ai` (plus the pre-existing `cartesia|groq|openrouter|quge5`).
- **Perpetual $100/month budget (Task #549, 2026-05-07):** Default `MONTHLY_TOTAL_USD_CAP` lowered from $500 → **$100** in both `cost_caps._DEFAULT_MONTHLY_TOTAL_USD_CAP` and `credit_burn_meter.MeterDConfig.cap_usd`. `PROVIDER_PRIORITY["english_rag_chat"]` now starts with `workers_ai_llama32_3b` (Cloudflare free tier) and `cost_caps._select_chat_primary()` is the runway-aware head selector. `CHAT_PRIMARY_OVERRIDE` is reserved for the vertex re-enable work (sub-task #555/#556) — until that lands the helper logs and ignores any non-workers override (V4 §12 no-silent-fallbacks) so a misconfig surfaces loudly instead of routing through a non-existent dispatch branch. `routes/voice.py` (`/tts`, `/stt`, `/voice/voice`) requires the new `auth_deps.require_paid_plan` dep (returns 402 for free users; admin/staff/educator bypass). The three-stage degradation ladder constants live in `cost_caps.DEGRADATION_PCT_PAUSE_BATCH/VOICE_OFF/FREE_503` (60 / 80 / 95 %); MeterD still LOCKS chat:cheaponly at 100 %. CI guard `artifacts/syrabit-backend/scripts/check_budget_ceiling.py` fails the build when either default is raised above $100 without a `# COST-CAP-OVERRIDE: <reason>` marker. Sarvam stays the Assamese-chat primary unchanged. Deep Azure/SES/web-push/observability removals are split into sub-tasks #553–#558.
- **Cost minimization (Task #513, 2026-05-07):** Browser-heavy traffic is now capped at the edge (30 chat turns/month + 3/day per anon-id, enforced in `workers/edge-proxy/src/index.ts` via the `RATE_LIMIT` KV namespace). Backend dispatch clamps every LLM call against the locked `TOKEN_BUDGETS` table in `artifacts/syrabit-backend/cost_caps.py` (chat 3000/800, content 4000/2000, formatter 4500/2500, translate 2000/2000, OCR 1500/800, STT 2000/500); a budget bump requires a `# COST-CAP-OVERRIDE: <reason>` comment + Sentry-annotated changelog. Tier-routing in `_select_chat_model` keeps free-user turns 1-2 on Workers-AI Mistral-7B and clamps free-user turns >15 to a 600-token output ceiling (paid plans bypass). ACA right-sized to 0.25 vCPU / 0.5 GiB × min 2 / max 30 replicas at 30 concurrent requests/pod (~75 % idle-baseline saving). Rule D (`MeterD`, default $500/month) flips `chat:cheaponly=1` in Redis when tripped — `_select_chat_model` reads the flag on every dispatch.
- **Assamese content backfill:** A resumable driver translates English content fields into Assamese using Workers-AI IndicTrans2 primary → Vertex/Gemini polish.
- **Cost & runway model (Task #550, 2026-05-07):** Phased credit-runway memo at [`artifacts/syrabit/docs/infra/credit-runway-cost-model.md`](artifacts/syrabit/docs/infra/credit-runway-cost-model.md). Headline **credit-on infra value** (cash + credit drawdown) per phase: **P1 (1k–5k DAU) = $120–$320 / mo**, **P2 (5k–10k DAU) = $300–$800 / mo** (cash side held at the $100 cap), **P3 (10k–50k DAU) = $1,200–$3,500 / mo** (requires `# COST-CAP-OVERRIDE` cap raise), **P4 (50k–100k DAU) = $4k–$12k / mo** (credits exhausted, revenue-positive at ~$97k gross). Coexists with Task #549 founder-locks: the $100 cap, the workers_ai chat head, the voice paywall, and the 60/80/95 % degradation ladder are **superior** to anything in the memo — if a memo number ever requires breaching one of those locks, the lock wins and the memo must be re-derived. Quarterly review cadence; next review **2026-08-07** OR sooner whenever any credit pool changes by ≥ 20 %.

## Product

Syrabit.ai is an AI-powered educational platform for AHSEC Class 11/12 and Degree students in Assam. It offers bilingual (English + Assamese) localized learning across 55 subjects, including RAG notes, MCQs, flashcards, PYQ OCR, an in-app browser with grounded chat, admin CMS, credit-based monetization (Razorpay INR-only), and DPDP-compliance.

## User preferences

- Iterative development with clear communication on major changes.
- Detailed explanations for complex features and architectural decisions.
- Prioritize modularity and maintainability.
- Treat vectorless RAG and vector RAG as **complementary layers** — vector for semantic/paraphrased exam-Q search, vectorless (BM25 + tree-walk) for exact-term/formula/navigation queries.
- **No silent fallbacks** — fail loud, document trade-offs explicitly (V4 §12).

## Gotchas

- **Backend import check:** Always run `python -c "import server"` from `artifacts/syrabit-backend/` before pushing.
- **ACA Deploy config:** Bicep ARM PATCH must include `properties.configuration.ingress.traffic = [{latestRevision: true, weight: 100}]` and `targetPort: 8000`.
- **Bicep template drift:** The Bicep template (`infra/azure/aca-syrabit-backend.bicep`) must precisely mirror the runtime contract.
- **Pinecone dimension incompatibility:** Pinecone embedding dimension is 1024. Future embed providers must match this dimension or be quarantined.
- **`OriginGate` lock-step rotation:** `ORIGIN_SHARED_SECRET` (ACA env) and `BACKEND_ORIGIN_SECRET` (syrabitworker binding) MUST be equal. Same for `D1_SYNC_SECRET`.
- **Token budgets are LOCKED:** `cost_caps.TOKEN_BUDGETS` ceilings are founder-locked (Task #513 §B). Raising any value requires a `# COST-CAP-OVERRIDE: <reason>` comment on the changed line AND a Sentry-annotated changelog entry; `tests/test_cost_caps.py` walks the source file and fails CI when either signal is missing. Same applies to bumping the edge chat caps (`CHAT_CAP_MONTHLY=30`, `CHAT_CAP_DAILY=3`) in `workers/edge-proxy/src/index.ts`.
- **Monthly USD ceiling is LOCKED at $100 (Task #549):** `_DEFAULT_MONTHLY_TOTAL_USD_CAP` (cost_caps.py) and `MeterDConfig.cap_usd` (credit_burn_meter.py) defaults must remain ≤ $100 unless the changed line carries a `# COST-CAP-OVERRIDE: <reason>` marker. `scripts/check_budget_ceiling.py` enforces this in CI and additionally validates that the three degradation thresholds (60 / 80 / 95 %) stay strictly increasing inside (0.0, 1.0).
- **`/api/me/quota` edge cache TTL = 5s (intentional, not 60s):** Task #513 §A originally suggested ~60s for the edge `/api/me/quota` response cache, but we ship `Cache-Control: private, max-age=5, s-maxage=5` because the chat post-bump is the only writer of the displayed counters and a 5-second window keeps the SPA's "remaining turns" banner from showing a stale value across more than one tick. This is implementation drift accepted by the round-7 code review; raising the TTL needs a corresponding UX review of banner staleness.
- **K.2 deterministic cache scope (chat-adjacent):** The deterministic-input AI cache (`ai_input_cache.py`) is wired into formatter / translate / OCR paths AND into `pipeline.stage3_polish`. Stage-3 polish runs on already-generated notes (not the live chat hot path), is keyed by the exact prompt text + model + max_tokens, and never serves a cached completion across users for streaming or temperature>0 calls. This was accepted in the round-7 review as "chat-adjacent but safe"; do NOT extend `is_deterministic(...)` to live `routes/ai_chat.py` dispatch — the live chat hot path is excluded by policy and any change there requires a new task and a fresh threat-model pass.

## Pointers

- **V4 spec:** `infra/v4-locked-architecture.md`
- **Four-cloud delegation matrix:** `infra/four-cloud-delegation.md`
- **GCP landing zone:** `artifacts/syrabit/docs/infra/gcp-landing-zone.md`
- **Azure landing zone:** `artifacts/syrabit/docs/infra/azure-landing-zone.md`
- **AWS landing zone:** `artifacts/syrabit/docs/infra/aws-landing-zone.md`
- **ACA cutover runbook:** `artifacts/syrabit/docs/infra/aca-cutover.md`
- **Provider decommission rationale (#347):** `artifacts/syrabit/docs/infra/providers-task-347-decommission.md`
- **Phased credit-runway cost model (Task #550):** `artifacts/syrabit/docs/infra/credit-runway-cost-model.md`
- **Skills index:** `.local/skills/`