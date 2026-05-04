> **v3 SUPERSEDES (2026-05-04):** the canonical infra spec now lives at
> [`infra/per-cloud-feature-delegation.md`](../../infra/per-cloud-feature-delegation.md)
> with [`infra/provider-priority-map.md`](../../infra/provider-priority-map.md)
> and [`infra/credit-burn-runbook.md`](../../infra/credit-burn-runbook.md).
> v3 four-cloud delegation in summary:
>
> - **Cloudflare** — edge, routing, RAG-primary (Workers-AI exam-Q&A +
>   fast-mode + IndicTrans2; Vectorize edge cache; AI Gateway BYOK to
>   Google AI Studio + Azure OpenAI).
> - **Azure** — Python FastAPI + Rust core on two ACAs; Azure AI
>   Content Safety as moderation-secondary; Azure Translator
>   Indic→English-only fallback; SendGrid (Pro 100k via Marketplace).
> - **Google Cloud / Vertex** — validation-only and safety-only
>   (Gemini 2.5 Flash on a 10% sample post-response; RAI sync only for
>   `exam_model_paper`). **Vertex Vector / Discovery Engine retired**
>   from primary chains (rollback only).
> - **AWS** — serverless workers, S3 / SQS / Lambda / EventBridge /
>   DynamoDB / SNS / Step Functions / CloudWatch (free-tier-friendly at
>   100k DAU). Cloudflare fronts all user-facing traffic.
>
> Provider removals (OpenAI, Anthropic, Bedrock, Stripe, Quge5, Resend,
> Grok, Railway, DigitalOcean) are tracked in Task #347. If anything
> below disagrees with the v3 spec, the v3 docs win.
>
> ---
>
> **Authority sync (2026-05-04):** `docs/infra/provider-priority-map.md`
> is the canonical PROVIDER_PRIORITY map. Binding constraints carried
> across every plan in this folder:
> 1. **Cerebras + Groq** — absent from every chain.
> 2. **Sarvam** — only in `assamese_rag_chat`, `assamese_content`, `translate` (not in tts/voice/stt/vision).
> 3. **Bedrock direct (Claude / Titan / Jamba)** — removed from chat; **Bedrock is Cohere‑only** (embed + rerank, keyed `bedrock_cohere`).
> 4. **`embed`** — Cohere via Bedrock → Voyage → CF Workers AI bge-m3 (Vertex `text-embedding-004` removed).
> 5. **`rerank`** — Cohere via Bedrock → Voyage → CF bge-reranker-base.
> 6. **Pinecone** — THE RAG vector store of record (`syrabit-rag`, 1024-dim cosine, aws-us-west-2). Vertex Vector / CF Vectorize are Tier-2/3 fallback only.
> 7. **MongoDB Atlas** — canonical chat history (`conversations` collection) + canonical analytics + all application state (notes, flashcards, streaks, leaderboards, quizzes, CMS, SEO topics, push tokens, audit logs). Redis/Momento/CF KV are TTL cache only.
> 8. **AWS S3** — sole object store. CF R2 is cold archive only.
> 9. **Cron** — Azure Container Apps Jobs canonical (Founders Hub credit). DO cron used for backend-resident jobs after Task #333 observability rewire — see `feature-deep-dive.md` §7.3 drift register.
> 10. **APM** — Azure App Insights canonical sink; Axiom parallel for long-retention logs; CloudWatch for AWS-native alarms only.

# Syrabit — Three-Cloud Hosting & Infra Delegation Plan

**Last updated:** 2026-05-04
**Status:** Active plan — supersedes ad-hoc allocations in `AWS-DEPLOYMENT.md` /
`DEPLOYMENT.md` / `RAILWAY-DEPLOYMENT.md` / any `*CLOUDRUN*` /
`*DIGITAL-OCEAN*` doc for the steady-state architecture.
**Owner:** founder@syrabit.ai
**Related:** Tasks #327 (cloud rebalance), #328 (AWS landing zone),
#329 (Azure landing zone), #335 (decommission Railway), #340 (Cohere routing),
`credit-applications.md` (credit pool source of truth).

> **Scope of this doc:** *hosting and infrastructure* delegation only — where
> code runs, where blobs and emails live, what handles cron + observability.
> AI **inference** routing (which model serves which feature pool) is owned
> by the dispatcher in `artifacts/syrabit-backend/config.py`
> (`PROVIDER_PRIORITY` + `POOL_WEIGHTS`); this plan does not duplicate it.
> See §6 for the inference-vs-hosting separation.

> ⚠️ **What changed in this revision:** Digital Ocean has been **dropped
> entirely**. The backend canonical origin moves to **AWS App Runner**;
> long-running workers + the Rust core + cron move to **Azure Container
> Apps**. Cloudflare keeps frontend + edge. Vertex remains inference-only
> (Gemini API + retained legacy AI APIs). Three hosting clouds, not four.
>
> **For the per-feature cost-minimization comparison** (3-cloud-only vs
> 3-cloud + Vertex, cash leakage, credit headroom by feature), see the
> companion analysis at
> [`docs/infra/cost-per-feature-comparison.md`](./cost-per-feature-comparison.md).
>
> **For the 10k DAU per-provider audit** (each provider's monthly burn
> sized to fit inside its own startup-credit annualized headroom, with
> the two tightest pools explicitly mitigated), see
> [`docs/infra/10k-dau-cost-audit.md`](./10k-dau-cost-audit.md).
>
> **For the auxiliary-provider delegation** (Mongo, Pinecone, **Azure
> Cache for Redis (cache primary)**, **Momento (cache Tier-2)**, Axiom,
> Sentry, Deepgram, ElevenLabs, Cartesia, Voyage,
> Cohere-direct, Resend, GitHub, etc. — what role each plays in the
> dispatcher and whether each is covered by free tier or credit at 10k
> DAU), see
> [`docs/infra/auxiliary-providers-delegation.md`](./auxiliary-providers-delegation.md).
>
> **Note on Upstash:** Upstash Redis was previously the cache primary.
> It has been **replaced** in the strategic plan by Azure Cache for
> Redis Basic C0 (within Azure credit) + Momento (free tier). Operational
> code/env-var cutover is a separate task; deployment docs and
> `cache.py` still reference Upstash env vars at the time of writing.

---

## 1. Guiding principles

1. **Frontend on Cloudflare. Backend on AWS. Workers + cron on Azure.** That's the entire plan in one sentence; the rest of this doc is the per-service breakdown.
2. **One cloud, one job.** Each cloud owns workloads that play to its structural advantage (latency, free tier, credit pool). No cloud holds two competing copies of the same workload.
3. **Credit-funded first, free-tier second, paid last.** Spend startup credits before they expire; fall back to always-free quotas; only pay cash for things no credit covers.
4. **Egress is the silent killer.** Keep request paths inside one cloud per hop wherever possible. Cloudflare → AWS Worker fetch is paid but tiny (text). Browser → S3 presigned URL bypasses every other hop. Architect the data plane around that.
5. **Single front door (Cloudflare).** Every public URL terminates at Cloudflare first — TLS, WAF, Turnstile, mTLS to origin, rate limit, Pages, edge worker proxy. AWS and Azure origins are never directly exposed to the internet.

---

## 2. Three-cloud responsibility matrix

| Cloud | Owns (hosting + infra) | Why this cloud | Funded by |
|---|---|---|---|
| **Cloudflare** | **Frontend** (Pages SPA), **edge proxy worker** (mTLS, WAF, Turnstile, rate-limit, D1 read-cache), **R2** (cold blob storage), **KV** (sessions + rate-limit counters), **Workers AI** (edge inference for embed/translate/last-resort chat), **Vectorize** (vector index failover) | Free unlimited bandwidth, only cloud with TLS + WAF + CDN + edge compute + edge AI under one bill. Already-approved $5k Enterprise credit. | $5,000 Cloudflare for Startups (approved, exp 2026-09) |
| **AWS** | **Backend canonical API origin** (App Runner, FastAPI), **S3** (audio notes, PDF uploads, generated content backups, sole object store), **SES** (transactional email), **Lambda + SQS** (heavy async fan-out), **Bedrock** (Cohere embed + rerank API only — see §6), **CloudWatch** (App Runner + Lambda + Bedrock logs), **Secrets Manager** (runtime secrets), **IAM** (per-feature roles, OIDC for GitHub Actions) | App Runner is the cheapest fully-managed FastAPI host AWS offers ($25–50/mo for 1 vCPU/2GB), autoscale-to-zero outside business hours. Best-in-class object store + transactional email at the free tier. Lambda's 1M-req/mo always-free quota covers async fan-out at zero marginal cost. Activate $1k credit covers App Runner + Bedrock spend. | $1,000 AWS Activate (approved); free tier covers S3/SES/Lambda/SQS for year 1 |
| **Azure** | **Container Apps** (background workers, Rust core service, optional secondary backend region for failover), **Container Apps Jobs** (scheduled cron), **Logic Apps** (alerting), **Application Insights** (distributed tracing, error-rate alarms — central APM for ALL three clouds), **Log Analytics**, **Azure OpenAI** (GPT-4.1-mini API only — see §6), **Key Vault** (secrets for Container Apps), **Entra ID** (tenant identity) | Azure Container Apps is cheaper than App Runner for long-running workers and supports gRPC (which App Runner does not — required for the Rust core). Microsoft for Startups credit pool is the largest of the three (>2× AWS Activate) and is dedicated to Azure-only spend, so not using Azure forfeits the credit. | $2,500 Azure for Startups (approved) |

**Not in this plan:** GCP / Vertex AI is **not** a hosting cloud for Syrabit. It's an AI inference provider only — Vertex Gemini is called via API from the AWS backend. See §6 for the inference-vs-hosting separation.

---

## 3. Request-path topology (the actual data plane)

```
                       ┌────────── browser / mobile ──────────┐
                       │                                       │
                       ▼                                       ▼
            https://syrabit.ai                       https://api.syrabit.ai
                       │                                       │
                       ▼                                       ▼
          ┌────────────────────┐                  ┌──────────────────────┐
          │ Cloudflare Pages   │                  │ Cloudflare Worker    │
          │ (React+Vite SPA)   │                  │ (edge proxy)         │
          │ — 0 egress cost    │                  │  • mTLS to origin    │
          └────────────────────┘                  │  • WAF / Turnstile   │
                                                  │  • KV rate limit     │
                                                  │  • D1 read-cache     │
                                                  │  • Workers AI        │
                                                  │    (edge embed/      │
                                                  │     translate/chat)  │
                                                  └──────────┬───────────┘
                                                             │
                                                             ▼
                                              ┌──────────────────────────┐
                                              │ AWS App Runner           │
                                              │  syrabit-backend         │
                                              │  • FastAPI canonical API │
                                              │  • us-west-2             │
                                              │  • Auto-scale 1→10       │
                                              └──┬─────────────┬──────┬──┘
                                                 │             │      │
                                                 │             │      │ enqueue
                              ┌──────────────────┤             │      ▼
                              │                  │             │  ┌─────────────┐
                              ▼                  ▼             │  │ AWS SQS     │
                  ┌──────────────┐    ┌──────────────┐         │  │  → Lambda   │
                  │ Mongo Atlas  │    │ Azure Redis  │         │  │  (heavy     │
                  │ (M0 + $500)  │    │ Redis REST   │         │  │   async)    │
                  │ — primary    │    │ — sessions,  │         │  └─────────────┘
                  │   store      │    │   rate-lim,  │         │
                  └──────────────┘    │   queues     │         │ gRPC / HTTP
                                      └──────────────┘         ▼
                                                       ┌──────────────────┐
                                                       │ Azure Container  │
                                                       │  Apps            │
                                                       │  • rust-core     │
                                                       │    (gRPC)        │
                                                       │  • workers       │
                                                       │  • cron jobs     │
                                                       │  • centralindia  │
                                                       └────────┬─────────┘
                                                                │
                          ┌─────────────────────────────────────┼─────────────────────────┐
                          │                                     │                         │
                          ▼                                     ▼                         ▼
                ┌──────────────┐                    ┌──────────────────┐      ┌──────────────────┐
                │ AWS S3       │                    │ Azure            │      │ AI inference     │
                │  • prod-     │                    │  • App Insights  │      │  (called by      │
                │    assets    │                    │  • Logic Apps    │      │   backend +      │
                │  • prod-     │                    │  • OpenAI        │      │   workers)       │
                │    public    │                    │    (GPT-4.1)     │      │  • CF Workers AI │
                │ AWS SES      │                    │  • Key Vault     │      │  • AWS Bedrock   │
                │  • email     │                    └──────────────────┘      │    (Cohere only) │
                └──────────────┘                                              │  • Azure OpenAI  │
                                                                              │  • Vertex Gemini │
                                                                              │  • direct vendor │
                                                                              │    APIs          │
                                                                              └──────────────────┘
```

**Key paths:**
- **Static assets:** browser → CF Pages. Never touches AWS or Azure.
- **API request:** browser → CF Worker → AWS App Runner → (Mongo / Azure Cache for Redis / AI providers / S3 / Azure rust-core via gRPC).
- **Large blob upload:** browser → CF Worker → **presigned S3 URL** → S3 directly. Never proxies through App Runner (saves egress + RAM).
- **Cron / scheduled jobs:** Azure Container Apps Job runs on schedule, calls AWS App Runner API or directly invokes AWS Lambda for memory-heavy work. App Runner never runs its own cron daemon.
- **Heavy async:** App Runner enqueues to AWS SQS → Lambda processes (PDF→MCQ extraction, batch audio synthesis, daily Mongo→S3 backup).

---

## 4. Per-cloud workload breakdown

### 4.1 Cloudflare — frontend + edge

| Workload | Service | Detail |
|---|---|---|
| Static SPA hosting | Cloudflare Pages | React + Vite build, output `artifacts/syrabit/dist/`. Build cmd: `pnpm install --frozen-lockfile && cd artifacts/syrabit && pnpm run build`. Deploy on every push to `main`. |
| Edge proxy / API gateway | Cloudflare Worker | mTLS to AWS App Runner origin, WAF rules, Turnstile challenge for write paths, KV-backed sliding-window rate limit, D1 read-cache for hot GETs (chapter content, MCQ lists). |
| Cold blob storage | R2 | Archived generated content, public-readable image assets too large for the static build, Cache Reserve backing for the API edge. |
| Edge state | KV | Session tokens (5-min TTL), rate-limit counters, feature flags. |
| Edge inference | Workers AI | bge-m3 embed (1024-dim), IndicTrans2 EN↔Indic translate, gpt-oss-20b last-resort chat, whisper-large-v3-turbo STT fallback. Saves a full inter-cloud hop on ~30% of requests. |
| Hot row cache | D1 | Read-replica of frequently-fetched content rows; eventual consistency, 60s TTL. |
| Vector failover | Vectorize | Secondary vector index when Pinecone is throttled or down. |
| TLS / WAF / DDoS | Cloudflare zone | Shared across `syrabit.ai`, `api.syrabit.ai`, future subdomains. Turnstile gates signup/login. |

**Cost shape:** $0 marginal — everything fits inside the $5k Enterprise credit.

### 4.2 AWS — backend canonical origin + storage + email + async + Cohere

| Workload | Service | Sizing / config | Cost shape |
|---|---|---|---|
| **FastAPI backend (canonical origin)** | **App Runner** (`syrabit-backend`) | Built from `artifacts/syrabit-backend/Dockerfile`. 1 vCPU / 2 GB RAM. Auto-scale 1 → 10 instances at 80% CPU. Health check `/api/health`. Custom domain `api.syrabit.ai` fronted by CF Worker. mTLS-only ingress. | ~$25–50/mo within $1k Activate credit |
| User-content blob storage | S3 (`s3://syrabit-prod-assets`, us-west-2) | Audio notes, PDF uploads, generated content backups, embed cache (hash → vector), daily Mongo backup dumps. Versioning ON, lifecycle to Glacier Deep Archive at 90d. Browser uploads via presigned URL — never proxy through App Runner. | ~$1–3/mo at MVP scale (5GB free year 1) |
| Public assets | S3 (`s3://syrabit-prod-public`, us-west-2) | Cover images, sample audio. Served via CF Cache Reserve to dodge S3 egress. | <$1/mo |
| Transactional email | SES (us-west-2) | Signup confirmation, password reset, study-streak digests, billing receipts. Verified `noreply@syrabit.ai`. Out of sandbox. | $0 within 62k/mo Lambda free tier |
| Heavy async fan-out | Lambda + SQS | PDF→MCQ extraction (high RAM), batch audio synthesis, daily Mongo→S3 backup (triggered from Azure cron). App Runner enqueues; Lambda consumes. DLQ on each queue. | $0 within 1M req + 400k GB-sec free tier |
| Async logs | CloudWatch Logs | App Runner + Lambda + Bedrock invocation logs. | 5GB/mo free, $0 expected |
| Cohere inference (API only — not hosting) | Bedrock (`cohere.embed-multilingual-v3` + `cohere.rerank-v3-5`) | Called from App Runner via boto3. IAM role grants `bedrock:InvokeModel` on those two model ARNs only. | ~$50/mo within $1k Activate credit |
| Runtime secrets | Secrets Manager | DB URIs, downstream API keys for App Runner + Lambda. Rotated quarterly. | ~$2/mo total |
| Identity / deploy | IAM (account `926046660612`) | Per-feature roles + OIDC trust for GitHub Actions deploy (no static keys). IAM user `SYRABIT` for break-glass only. | $0 |
| ~~EC2 / ECS / Fargate / EKS / RDS / DynamoDB / ElastiCache~~ | **Not used** | App Runner is the canonical compute; Mongo Atlas is the DB; Azure Cache for Redis is the cache (within Azure credit). Adding equivalents would dilute the $1k Activate credit. | — |

### 4.3 Azure — workers + Rust core + cron + observability + GPT

| Workload | Service | Sizing / config | Cost shape |
|---|---|---|---|
| Background workers | Container Apps (`syrabit-workers`) | Same Docker image as the backend, entrypoint `python -m workers.queue_runner`. Polls Azure Cache for Redis queue + SQS bridge. Min 1, max 3 replicas. | ~$15/mo within credit |
| Rust "Neural Mesh Core" service | Container Apps (`rust-core`) | Internal-only, called from AWS App Runner over public mTLS endpoint (gRPC port 50051 + HTTP 3000). Lives on Azure because App Runner does not support gRPC. | ~$10/mo within credit |
| Scheduled jobs | Container Apps Jobs (cron) | Daily Mongo→S3 backup (invokes AWS Lambda), weekly Pinecone re-index, weekly cost-report email, hourly SEO auto-publish, IndexNow batches, dead-endpoint pruner, Trustpilot refresh, CF log pull, weekly digest. KEDA cron triggers; scale-to-zero between runs. | $0 within credit |
| Optional secondary backend region | Container Apps (`syrabit-backend-failover`) | Standby replica of the FastAPI backend image, scale-to-zero, only spun up if AWS App Runner us-west-2 is degraded. CF Worker can fail-over by env flag. | $0 idle, ~$25/mo if active |
| Alerting | Logic Apps | App Insights anomaly → Telegram bot + founder email; spend alarms (AWS > $5/day, Azure > $10/day, Vertex > $8/day) → Telegram. | $0 within credit |
| Distributed tracing + APM | Application Insights | **Central APM for all three hosting clouds.** Python OpenTelemetry SDK on AWS App Runner backend exports spans here. AWS Lambda exports via otel-lambda layer. Azure Container Apps export natively. Dashboards: per-pool latency (p50/p95/p99), per-provider error rate, daily $$ burn per provider. | $0 within 5GB/mo ingest free + credit |
| Log Analytics Workspace | Log Analytics | Backing store for App Insights + Container Apps logs + Logic Apps run history. | $0 within credit |
| GPT-4.1-mini inference (API only — not hosting) | Azure OpenAI (`syrabit-chat` deployment) | Called from AWS App Runner backend; primary for `english_rag_chat` + `content` pools. PTU = none, pay-as-you-go. eastus region for cheapest tokens. | ~$150/mo within $2.5k credit |
| Secrets | Key Vault | Secrets for Container Apps + Azure OpenAI proxy. Managed identity binding (no static keys). | <$1/mo |
| Tenant identity | Entra ID | Founder + 1 staff identity, app registrations for managed identity. | $0 free tier |
| ~~App Service / Functions / VMs / Blob Storage~~ | **Not used** | App Runner owns the canonical API; S3 is the sole object store. | — |

---

## 5. Egress topology (where the money quietly leaks)

| Hop | Volume estimate | Cost |
|---|---|---|
| Browser ↔ Cloudflare | Unlimited | $0 |
| Cloudflare ↔ AWS App Runner | All API requests | ~$0.09/GB out from AWS — small (text only); est. <$5/mo at MVP |
| AWS App Runner ↔ Mongo Atlas (same region) | All DB ops | $0 (peering — Atlas in us-west-2) |
| AWS App Runner ↔ Azure Cache for Redis | Session, rate-limit, queue | tiny, within Azure credit |
| AWS App Runner ↔ AI providers (text) | All LLM/embed/rerank calls | <$1/mo |
| AWS App Runner ↔ Azure rust-core (gRPC) | Per-request enrichment calls | small (text); ~$2/mo at MVP |
| AWS App Runner ↔ S3 (same region) | Internal blob ops | $0 (intra-region) |
| Azure cron ↔ AWS Lambda webhook | Trigger only | $0 (text payload) |
| Browser ↔ S3 (presigned URL) | Audio + PDF uploads | $0 in (uploads); $0.09/GB out (downloads) — mitigated via CF cache |
| Cloudflare R2 ↔ anyone | All R2 reads | $0 (CF guarantee) |

**Rule of thumb:** any blob > 1 MB goes through a Cloudflare R2 or AWS S3 *presigned URL* — never proxy it through App Runner.

---

## 6. Hosting vs. inference — the separation

This is a **hosting** plan. AI inference is a separate concern owned by the dispatcher in `artifacts/syrabit-backend/config.py` (`PROVIDER_PRIORITY` + `POOL_WEIGHTS`). Some clouds wear both hats; some wear only one:

| Cloud | Hosting role | Inference role |
|---|---|---|
| Cloudflare | ✅ frontend + edge + R2 + KV + D1 + Vectorize | ✅ Workers AI (bge-m3 embed, IndicTrans2 translate, gpt-oss-20b last-resort chat, Whisper STT fallback) |
| AWS | ✅ backend canonical origin (App Runner) + S3 + SES + Lambda + SQS + CloudWatch | ✅ Bedrock (Cohere embed + rerank only — **never** Anthropic/Nova/Titan/Mistral/Llama) |
| Azure | ✅ Container Apps workers + rust-core + cron + Logic Apps + AppInsights + Key Vault | ✅ Azure OpenAI (GPT-4.1-mini for english chat + content fallback) |
| GCP / Vertex | ❌ not used for hosting | ✅ Four API surfaces (all called from the backend): **(A)** Vertex AI Platform — Vector Search / Matching Engine (`retrievers/vertex.py`, Tier-2 only — Pinecone is the RAG store of record), Gemini streaming chat direct-SA path (`vertex_chat.py`, rollback only). `providers/vertex_embed.py` is **NOT** in the embed chain (rollback-only); embed is Cohere → Voyage → CF; **(B)** Discovery Engine API (`discovery_engine_client.py`); **(C)** Generative Language / Gemini via **Cloudflare AI Gateway BYOK → google-ai-studio** as the prod default for `vertex_services.py` (translation, MCQ/flashcards, content enhancement, SEO meta, gap analysis, long-doc reader); **(D)** Cloud Vision API for OCR. Auth priority: `VERTEX_SERVICE_ACCOUNT` → `GEMINI_API_KEY` (legacy) → `CF_AI_GATEWAY_*` (prod default). Plus retained Cloud STT/TTS/Web Risk free-tier APIs. |

**Why call this out:** the previous plan blurred the two and treated Vertex like a hosting cloud. It isn't — it's only an API endpoint we hit. Same for the inference-side use of AWS Bedrock and Azure OpenAI: they're API services on otherwise-hosting-focused clouds, called by the AWS App Runner backend.

---

## 7. Credit burn schedule (12-month plan)

| Cloud | Credit | Burn target | When dry |
|---|---:|---|---|
| Cloudflare | $5,000 | $200/mo (mostly Workers AI + R2 above free tier as we grow) | month 25+ |
| Azure | $2,500 | $200/mo (GPT-4.1-mini + Container Apps workers + cron + AppInsights) | month 12+ |
| AWS Activate | $1,000 | $80/mo (App Runner + Bedrock Cohere; S3/SES/Lambda all free tier) | month 12+ |
| Mongo Atlas | $500 | M0 free tier covers MVP; credit applies after upgrade | month 24+ |
| Vertex (inference, not hosting) | $2,000 | $120/mo (Gemini Flash API) | month 16+ |
| **Total runway** | **~$11,000 base** | **~$600/mo all-in cloud** | **~18 months at MVP scale** |

Add credits being chased (`credit-applications.md`): OpenRouter $5k + ElevenLabs $4k + AssemblyAI $1.5k + Deepgram $1k = +$11.5k → potential 30-month runway.

---

## 8. What to do next (concrete, ordered)

1. **Spin up AWS App Runner service `syrabit-backend`** in us-west-2 from `artifacts/syrabit-backend/Dockerfile`. 1 vCPU / 2 GB. Custom domain `api.syrabit.ai` once CF mTLS cert is bound. Health check `/api/health`.
2. **Re-enable Cohere via direct boto3** to AWS Bedrock (verified working). 1-day task, high-leverage for Assamese embed quality.
3. **Stand up `s3://syrabit-prod-assets`** in us-west-2 with versioning + 90d → Glacier Deep Archive lifecycle. Wire from backend via boto3. Add `AWS_S3_BUCKET` secret.
4. **Wire AWS SES** — verify `noreply@syrabit.ai` domain. Free tier covers ≤62k/mo.
5. **Move workers + Rust core to Azure Container Apps** (centralindia). Same Docker image as the API; different entrypoints. mTLS-only ingress. Wire Key Vault for secrets.
6. **Move daily Mongo→S3 backup** to AWS Lambda triggered by Azure Container Apps Job (Azure has more credit headroom than AWS EventBridge billing).
7. **Wire Application Insights** as the central APM — install Azure Monitor OpenTelemetry SDK on AWS App Runner backend + Lambda otel layer + native Azure Container Apps export. First dashboards: per-pool latency p50/p95/p99 + per-provider error rate + daily $$ burn.
8. **Set spend alarms** via Logic Apps: AWS > $5/day, Azure > $10/day, Vertex > $8/day → Telegram bot.
9. **Decommission Railway** (Task #335) and **drop all Digital Ocean / Cloud Run / App Service backend paths** from docs (Task #335 follow-up). Replace with pointers to this plan.

---

## 9. What NOT to do (explicit guardrails)

- **Do not** put the backend API on multiple clouds simultaneously. **One canonical origin: AWS App Runner.** Azure Container Apps `syrabit-backend-failover` is standby (scale-to-zero), not active.
- **Do not** introduce Digital Ocean, Railway, Cloud Run, App Service, or Fly.io as the backend home. Those are all retired or rejected for this architecture.
- **Do not** use Azure Blob Storage, GCS, or DO Spaces — **S3 is the chosen object store**. Mixing storage backends is a footgun for SDK churn and lifecycle policy drift.
- **Do not** use AWS Bedrock for chat (Anthropic, Nova, Mistral, Titan, Llama). Azure GPT-4.1-mini and Vertex Gemini cover those roles cheaper. Bedrock is **Cohere-only** in this architecture.
- **Do not** use Cloudflare Workers for long-running backend logic (>10s CPU). Use AWS App Runner for that; the worker is a proxy, not the app.
- **Do not** spend cash on a managed Postgres while Mongo Atlas + Azure Cache for Redis + Pinecone cover all current needs.
- **Do not** deploy backend code on GCP — Vertex is API-only in this plan; adding compute there would dilute the $2k Vertex credit pool that's already fully booked for Gemini inference.
- **Do not** edit `PROVIDER_CREDITS` / `POOL_WEIGHTS` based on aspirational credit grants — gated checklist in `credit-applications.md` §"When a grant is approved" applies.

---

## 10. Single sentence summary

> **Frontend on Cloudflare, backend API on AWS App Runner, workers + Rust
> core + cron + APM + GPT on Azure, blob + email + async + Cohere on AWS** —
> and all five AI providers (Cloudflare Workers AI, AWS Bedrock Cohere,
> Azure OpenAI, Vertex Gemini, direct vendor APIs) are called from the AWS
> App Runner backend via the dispatcher.
