# Workspace — Syrabit.ai

> **V4 LOCKED (2026-05-05):** the canonical infra spec is
> [`infra/v4-locked-architecture.md`](infra/v4-locked-architecture.md).
> v3 docs (`per-cloud-feature-delegation.md`, `provider-priority-map.md`,
> `credit-burn-runbook.md`) are superseded and carry V4 back-pointers.
> If anything below disagrees with V4, **V4 wins**.

## Run & Operate

- **Frontend dev:** `cd artifacts/syrabit && PORT=5000 pnpm dev`
- **Backend dev:** `cd artifacts/syrabit-backend && gunicorn server:app -c gunicorn.conf.py`
- **Mockup sandbox:** `pnpm --filter @workspace/mockup-sandbox run dev`
- **Production health:** `https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io/api/health`
- **Required env vars (ACA, sourced from Azure KV):** `MONGO_URL`, `JWT_SECRET`, `ADMIN_JWT_SECRET`, `AZURE_OPENAI_API_KEY`, `RAZORPAY_KEY_SECRET`, `WORKERS_EMBED_SECRET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `EMAIL_PROVIDER=sendgrid`, `EMAIL_FALLBACK=ses`, `EMBED_PROVIDER_PRIMARY=workers_ai_custom`, `WORKERS_EMBED_URL=https://embed.syrabit.ai`.

## Stack

- **Frontend:** React 18 + Vite + React Router + Tailwind CSS + Drizzle ORM (where used).
- **Backend:** Python 3.11 + FastAPI + Gunicorn (Uvicorn workers).
- **Rust core:** async-batch worker on a separate ACA app.
- **Validation/codegen:** Zod + Orval.
- **Build:** pnpm monorepo + esbuild + Docker.

## Where things live

- **Frontend:** `artifacts/syrabit/`
- **Backend:** `artifacts/syrabit-backend/`
- **Embed worker (Cloudflare):** `artifacts/syrabit/workers/embed-worker/`
- **Edge proxy (Cloudflare):** `workers/edge-proxy/`
- **Bicep + ACA deploy:** `infra/azure/aca-syrabit-backend.bicep`, `.github/workflows/azure-container-apps-deploy.yml`
- **Threat model:** `threat_model.md`
- **V4 architecture (source of truth):** `infra/v4-locked-architecture.md`

## Architecture decisions (V4 highlights)

- **Cost split locked:** 40 % Cloudflare / 30 % Azure / 20 % AWS / 10 % GCP. Single integers, no ranges.
- **Embedding primary:** EmbeddingGemma-300M + Qwen3-0.6B on Cloudflare Workers AI, mean-pooled to 1024-dim, written to Pinecone (`aws-ap-south-1`, namespace `cached_gemma_today`).
- **Embed-failover:** Vertex multilingual embedding writes to a **separate** Pinecone namespace (`fallback_vertex_pending_reembed`); an AWS SQS-backed Lambda re-embeds back to the primary namespace when Cloudflare returns. **Zero index-mix corruption.**
- **Chat dispatch:** token-length + risk-score router. Short/low-risk → Workers-AI Qwen3-0.6B. Long/high-risk → Vertex Gemini 2.5 Flash (co-primary) → Azure OpenAI gpt-4.1-mini → Workers-AI Mistral-7B/Llama-3.2-3B. Assamese path = Sarvam primary → IndicTrans2 fallback.
- **Moderation:** Llama-Guard-2 self-hosted on ACA (primary) → Azure AI Content Safety (secondary). Gemini RAI is batch-only for `exam_model_paper`, never blocks chat.
- **Vectorless RAG (new in V4):** three-tier router in `artifacts/syrabit-backend/rag.py` — tree-walk over D1 syllabus map, then BM25 on Mongo `$text`, then vector pass. Results fused with RRF before Pinecone rerank. Target: ≥25 % of chat turns served without an embed call.
- **Secrets:** Azure Key Vault is source of truth; AWS Secrets Manager + Cloudflare Secrets are read-only replicas synced daily via Terraform-CI with SHA-256 hash validation.
- **Observability:** Sentry Performance is the end-to-end trace owner; `traceparent`/`baggage` propagate CF Worker → Azure ACA → Lambda → Vertex/Pinecone/Mongo. OTEL → GCP Cloud Trace as the long-retention backstop.
- **DR:** RTO = 4 h, RPO = 15 min, quarterly restore drill. **Azure `eastus2` is an explicit accepted SPOF** — manual `westus3` re-deploy from Bicep on regional outage.
- **Latency:** p95 chat turn budget = 2.5 s. Pinecone in `ap-south-1` keeps the RAG hop <50 ms inside India.
- **Hosting:** Azure Container Apps `syrabit-backend` (`eastus2`) is the live HTTP face. DigitalOcean and Railway hosting are fully decommissioned (Tasks #336, #347).
- **Storage roles:** D1 = SEO meta + audit logs + syllabus map. Mongo Atlas = conversations + profiles + chunk **metadata** (Pinecone IDs only — never re-embedded from Mongo). Pinecone = embeddings. Vectorize = edge cache. R2 = canonical assets + final backups. S3 = temp dumps.
- **Removed providers (Task #347):** OpenAI, Anthropic, AWS Bedrock direct, Stripe, Quge5, Resend, xAI/Grok, Railway, DigitalOcean, **Groq (purged 2026-05-06)**. See `artifacts/syrabit/docs/infra/providers-task-347-decommission.md`.
- **Cerebras retained as CF-Gateway-only fallback** (V4 §1, §4) — re-instated by Task #420 for telemetry parity; direct (non-gateway) calls remain blocked by `scripts/check_dead_providers.py`. Never primary, never used for content-gen.
- **Cohere retained as embed-failover specialist** (V4 §1) — BYOK via CF AI Gateway slug `cohere/v1`; not on the chat hot-path.
- **PG → Mongo migration** (V4 §13): NOT STARTED. ADR → dual-write → read-shadow → cutover → rip-out, gated on Supabase OAuth replacement. Until Phase 4 completes, V4 is *aspirational* on the user-data SoT axis.

## Product

Syrabit.ai is an AI-powered educational platform for AHSEC Class 11/12 and Degree students in Assam. Bilingual (English + Assamese) localized learning across 55 subjects: chapter-level RAG notes, MCQs, flashcards, PYQ OCR pipeline, in-app educational browser with grounded chat, admin CMS for content/SEO/QA, credit-based monetization (Razorpay INR-only), DPDP-compliant.

## User preferences

- Iterative development with clear communication on major changes.
- Detailed explanations for complex features and architectural decisions.
- Prioritize modularity and maintainability.
- Treat vectorless RAG and vector RAG as **complementary layers** — vector for semantic/paraphrased exam-Q search, vectorless (BM25 + tree-walk) for exact-term/formula/navigation queries.
- **No silent fallbacks** — fail loud, document trade-offs explicitly (V4 §12).

## Gotchas

- Always run `python -c "import server"` from `artifacts/syrabit-backend/` before pushing — silent missing-file drift between local FS and `main` has broken the live deploy 5 times. Pre-deploy import smoke gate is tracked in follow-up Task #439.
- The deploy workflow's single ARM PATCH **must** include `properties.configuration.ingress.traffic = [{latestRevision: true, weight: 100}]` and `targetPort: 8000`. Removing either strands traffic on the helloworld fallback revision.
- Bicep template (`infra/azure/aca-syrabit-backend.bicep`) must mirror the runtime contract enforced by the workflow (probe path `/api/health`, `ADMIN_JWT_SECRET` wired). A drift here regresses the running revision on next `az deployment group create`.
- Pinecone embedding dimension is **1024**. Vertex `text-embedding-004` (768-dim) is dimension-incompatible — never force it into the primary chain. The fallback path uses Vertex multilingual embedding **into a separate namespace** to avoid corruption.
- Multi-revision mode + healthy old revisions = traffic-split drift hazard. Follow-up Task #440 will deactivate non-latest active revisions or flip to single-revision mode.

## Pointers

- **V4 spec:** `infra/v4-locked-architecture.md`
- **v3 (historical):** `infra/per-cloud-feature-delegation.md`, `infra/provider-priority-map.md`, `infra/credit-burn-runbook.md`
- **Azure landing zone:** `artifacts/syrabit/docs/infra/azure-landing-zone.md`
- **AWS landing zone:** `artifacts/syrabit/docs/infra/aws-landing-zone.md`
- **ACA cutover runbook:** `artifacts/syrabit/docs/infra/aca-cutover.md`
- **Provider decommission rationale (#347):** `artifacts/syrabit/docs/infra/providers-task-347-decommission.md`
- **Skills index:** `.local/skills/` (deployment, environment-secrets, integrations, workflows, validation, code_review, follow-up-tasks)
