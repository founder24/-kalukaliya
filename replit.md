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

## Architecture decisions

- **Cost split:** 40% Cloudflare, 30% Azure, 20% AWS, 10% GCP.
- **Embedding strategy:** Primary is Gemma-300M + Qwen3-0.6B on Cloudflare Workers AI (1024-dim, mean-pooled) to Pinecone (`aws-ap-south-1`, `cached_gemma_today`). Failover uses Vertex multilingual embedding to a separate Pinecone namespace (`fallback_vertex_pending_reembed`) with re-embedding back to primary via AWS SQS Lambda.
- **Chat dispatch:** Azure `gpt-4.1-nano` is the sole primary, falling back to Workers-AI Mistral-7B, then Llama-3.2-3B, then generic Workers-AI. Vertex is used for `content` pool and safety/validation, not the chat hot path.
- **Vectorless RAG:** A three-tier router (`artifacts/syrabit-backend/rag.py`) performs tree-walk on D1 syllabus, then BM25 on Mongo, then a vector pass. Results are fused with RRF before Pinecone rerank to reduce embed calls.
- **Secrets management:** Azure Key Vault is the source of truth, with AWS Secrets Manager and Cloudflare Secrets as read-only replicas synced daily.
- **Observability:** Sentry Performance for end-to-end tracing, with `traceparent`/`baggage` propagation. OTEL to GCP Cloud Trace for long-term retention.
- **PG to Mongo Migration:** Phase 2 complete for `users`, `conversations`, `edu_notes`, `edu_flashcards`, `edu_study_settings`, `activity_log`, and `notifications` via dual-write mirroring (`db_dualwrite.py`). Phase 3 (read-shadow) and 4 (cutover) are pending.
- **CF edge cache smoke (Task #456, 2026-05-06):** `.github/workflows/cf-edge-cache-smoke.yml` is now a `[staging, production]` matrix that fires on every successful `azure-container-apps-deploy` `workflow_run` plus the 04:11 UTC nightly cron. Production leg requires repo var `PROD_BASE_URL` + repo secret `PROD_ADMIN_JWT`; missing config no-ops with a notice instead of failing.
- **Cloudflare edge (Task #472, 2026-05-06):** Live edge worker is `syrabitworker` (deployed via `workers/edge-proxy/wrangler.syrabitworker.toml`, 13 bindings inc. `BACKEND_ORIGIN_SECRET`/`D1_SYNC_SECRET`/`EDGE_AI_FALLBACK_SECRET`). Live frontend is Pages project `syrabitfrontend` (prod branch `main` of `founder24/-kalukaliya`, build → `artifacts/syrabit/dist`). Routes `syrabit.ai/*`, `www.syrabit.ai/*`, `api.syrabit.ai/*` all point at `syrabitworker`; `embed.syrabit.ai/*` stays on `syrabit-embed-worker`. Old `syrabit-edge` worker + `syrabit-analytics` Pages project deleted. Leftover to clean: `syrabit-edge-preview` worker.

## Product

Syrabit.ai is an AI-powered educational platform for AHSEC Class 11/12 and Degree students in Assam. It offers bilingual (English + Assamese) localized learning across 55 subjects, including RAG notes, MCQs, flashcards, PYQ OCR, an in-app browser with grounded chat, admin CMS, credit-based monetization (Razorpay INR-only), and DPDP-compliance.

## User preferences

- Iterative development with clear communication on major changes.
- Detailed explanations for complex features and architectural decisions.
- Prioritize modularity and maintainability.
- Treat vectorless RAG and vector RAG as **complementary layers** — vector for semantic/paraphrased exam-Q search, vectorless (BM25 + tree-walk) for exact-term/formula/navigation queries.
- **No silent fallbacks** — fail loud, document trade-offs explicitly (V4 §12).

## Gotchas

- **Backend import check:** Always run `python -c "import server"` from `artifacts/syrabit-backend/` before pushing to prevent silent missing-file deployment issues.
- **ACA Deploy config:** Bicep ARM PATCH must include `properties.configuration.ingress.traffic = [{latestRevision: true, weight: 100}]` and `targetPort: 8000`. Missing these strands traffic on fallback.
- **Bicep template drift:** The Bicep template (`infra/azure/aca-syrabit-backend.bicep`) must precisely mirror the runtime contract (e.g., probe path, env vars) to avoid deployment regressions.
- **Pinecone dimension incompatibility:** Pinecone embedding dimension is 1024. Vertex `text-embedding-004` (768-dim) is incompatible and must be used in a separate namespace for fallbacks.
- **`OriginGate` lock-step rotation:** `ORIGIN_SHARED_SECRET` (ACA env, sourced from KV `ORIGIN-SHARED-SECRET`) MUST equal the syrabitworker `BACKEND_ORIGIN_SECRET` binding — the worker injects it as `X-Origin-Auth` on every backend fetch. Rotating one side without the other 403s every proxied request. Same rule for `D1_SYNC_SECRET` ↔ worker `D1_SYNC_SECRET`. Verified active 2026-05-06: direct ACA hits to gated paths return 403 "Direct origin access denied".

## Pointers

- **V4 spec:** `infra/v4-locked-architecture.md`
- **Azure landing zone:** `artifacts/syrabit/docs/infra/azure-landing-zone.md`
- **AWS landing zone:** `artifacts/syrabit/docs/infra/aws-landing-zone.md`
- **ACA cutover runbook:** `artifacts/syrabit/docs/infra/aca-cutover.md`
- **Provider decommission rationale (#347):** `artifacts/syrabit/docs/infra/providers-task-347-decommission.md`
- **Skills index:** `.local/skills/`