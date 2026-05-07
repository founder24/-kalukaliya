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
- **Assamese content backfill:** A resumable driver translates English content fields into Assamese using Workers-AI IndicTrans2 primary → Vertex/Gemini polish.

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

## Pointers

- **V4 spec:** `infra/v4-locked-architecture.md`
- **Four-cloud delegation matrix:** `infra/four-cloud-delegation.md`
- **GCP landing zone:** `artifacts/syrabit/docs/infra/gcp-landing-zone.md`
- **Azure landing zone:** `artifacts/syrabit/docs/infra/azure-landing-zone.md`
- **AWS landing zone:** `artifacts/syrabit/docs/infra/aws-landing-zone.md`
- **ACA cutover runbook:** `artifacts/syrabit/docs/infra/aca-cutover.md`
- **Provider decommission rationale (#347):** `artifacts/syrabit/docs/infra/providers-task-347-decommission.md`
- **Skills index:** `.local/skills/`