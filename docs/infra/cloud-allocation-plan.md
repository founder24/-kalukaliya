# Syrabit — Four-Cloud Hosting & Infra Delegation Plan

**Last updated:** 2026-05-04
**Status:** Active plan — supersedes ad-hoc allocations in `AWS-DEPLOYMENT.md` /
`DEPLOYMENT.md` / `RAILWAY-DEPLOYMENT.md` for the steady-state architecture.
**Owner:** founder@syrabit.ai
**Related:** Tasks #327 (4-way rebalance), #328 (AWS landing zone),
#329 (Azure landing zone), #330 (DO App Platform), #331 (DO port),
#340 (Cohere routing), `credit-applications.md` (credit pool source of truth).

> **Scope of this doc:** *hosting and infrastructure* delegation only — where
> code runs, where blobs and emails live, what handles cron + observability.
> AI **inference** routing (which model serves which feature pool) is owned
> by the dispatcher in `artifacts/syrabit-backend/config.py`
> (`PROVIDER_PRIORITY` + `POOL_WEIGHTS`); this plan does not duplicate it.
> See §6 for the inference-vs-hosting separation.

---

## 1. Guiding principles

1. **Frontend on Cloudflare. Backend on Digital Ocean. Everything else on AWS or Azure.** That's the entire plan in one sentence; the rest of this doc is the per-service breakdown.
2. **One cloud, one job.** Each cloud owns workloads that play to its structural advantage (latency, free tier, credit pool). No cloud holds two competing copies of the same workload.
3. **Credit-funded first, free-tier second, paid last.** Spend startup credits before they expire; fall back to always-free quotas; only pay cash for things no credit covers.
4. **Egress is the silent killer.** Keep request paths inside one cloud per hop wherever possible. Cloudflare → DO costs $0 (Bandwidth Alliance). DO → AWS costs $0.09/GB. Architect the data plane around that ratio.
5. **Single front door (Cloudflare).** Every public URL terminates at Cloudflare first — TLS, WAF, Turnstile, mTLS to origin, rate limit, Pages, edge worker proxy. Origin clouds are never directly exposed to the internet.

---

## 2. Four-cloud responsibility matrix

| Cloud | Owns (hosting + infra) | Why this cloud | Funded by |
|---|---|---|---|
| **Cloudflare** | **Frontend** (Pages SPA), **edge proxy worker** (mTLS, WAF, Turnstile, rate-limit, D1 read-cache), **R2** (cold blob storage), **KV** (sessions + rate-limit counters), **Workers AI** (edge inference for embed/translate/last-resort chat) | Free unlimited bandwidth, $0 egress to DO (Bandwidth Alliance), only cloud with TLS + WAF + CDN + edge compute + edge AI under one bill. Already-approved $5k Enterprise credit. | $5,000 Cloudflare for Startups (approved, exp 2026-09) |
| **Digital Ocean** | **Backend API** (FastAPI on App Platform), **background workers**, **Rust core service**, **scheduled cron container**, **dev/staging environments** | Cheapest predictable compute ($12/mo for 1GB app vs $30+ on AWS Fargate), $0 egress to Cloudflare (Bandwidth Alliance), zero AWS-config tax. Already ported in Task #331. | DO Hatch credit (apply) → ~$25–40/mo cash steady state |
| **AWS** | **S3** (audio notes, PDF uploads, generated content backups), **SES** (transactional email), **Lambda + SQS** (heavy async fan-out), **Bedrock** (Cohere embed + rerank API only — see §6), **CloudWatch** (Lambda + Bedrock logs only) | Best-in-class object store + transactional email at the free tier. Lambda's 1M-req/mo always-free quota covers async fan-out at zero marginal cost. Activate $1k credit covers Bedrock spend. | $1,000 AWS Activate (approved); free tier covers S3/SES/Lambda/SQS for year 1 |
| **Azure** | **Container Apps cron** (scheduled cleanups, daily Mongo→S3 backup trigger), **Logic Apps** (alerting, Telegram + email), **Application Insights** (distributed tracing, error-rate alarms), **Azure OpenAI** (GPT-4.1-mini API only — see §6) | Azure Container Apps cron is cheaper than AWS EventBridge+Lambda for KEDA-style scheduled containers. Microsoft for Startups credit pool is large and dedicated to Azure-only spend, so not using Azure forfeits the credit. | $2,500 Azure for Startups (approved) |

**Not in this plan:** GCP / Vertex AI is **not** a hosting cloud for Syrabit. It's an AI inference provider only — Vertex Gemini is called via API from the DO backend. See §6 for the inference-vs-hosting separation.

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
                                                             │ Bandwidth
                                                             │ Alliance
                                                             │ ($0 egress)
                                                             ▼
                                              ┌──────────────────────────┐
                                              │ Digital Ocean App Platform│
                                              │  • FastAPI backend        │
                                              │  • Background workers     │
                                              │  • Rust core service      │
                                              │  • Cron container         │
                                              └──────┬─────────────┬──────┘
                                                     │             │
                          ┌──────────────────────────┤             ├──────────────────┐
                          │                          │             │                  │
                          ▼                          ▼             ▼                  ▼
              ┌──────────────┐          ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
              │ Mongo Atlas  │          │ Upstash      │    │ AWS          │    │ Azure        │
              │ (M0 + $500)  │          │ Redis REST   │    │  • S3        │    │  • Container │
              │ — primary    │          │  — sessions, │    │  • SES       │    │    Apps cron │
              │   store      │          │    rate-lim, │    │  • Lambda+SQS│    │  • Logic Apps│
              └──────────────┘          │    queues    │    │  • Bedrock   │    │  • AppInsights│
                                        └──────────────┘    │    (Cohere)  │    │  • OpenAI    │
                                                            │  • CloudWatch│    │    (GPT-4.1) │
                                                            └──────────────┘    └──────────────┘
                                                                  ▲                    ▲
                                                                  │                    │
                                                                  └─── AI inference ───┘
                                                                       (also called from DO
                                                                        per-pool dispatcher;
                                                                        see §6)
```

**Key paths:**
- **Static assets:** browser → CF Pages. Never touches DO or any other cloud.
- **API request:** browser → CF Worker → DO backend → (Mongo / Upstash / AI providers / S3). Single inter-cloud hop, $0 egress on the long leg.
- **Large blob upload:** browser → CF Worker → **presigned S3 URL** → S3 directly. Never proxies through DO (saves DO egress + RAM).
- **Cron / scheduled jobs:** Azure Container Apps cron triggers a webhook into DO backend, OR triggers an AWS Lambda for memory-heavy work. DO never runs its own cron daemon.

---

## 4. Per-cloud workload breakdown

### 4.1 Cloudflare — frontend + edge

| Workload | Service | Detail |
|---|---|---|
| Static SPA hosting | Cloudflare Pages | React + Vite build, output `artifacts/syrabit/dist/`. Build cmd: `pnpm install --frozen-lockfile && cd artifacts/syrabit && pnpm run build`. Deploy on every push to `main`. |
| Edge proxy / API gateway | Cloudflare Worker | mTLS to DO origin, WAF rules, Turnstile challenge for write paths, KV-backed sliding-window rate limit, D1 read-cache for hot GETs (chapter content, MCQ lists). |
| Cold blob storage | R2 | User-uploaded PDFs (lecture notes), generated content archives, image assets too large for the static build. $0 egress anywhere. |
| Edge state | KV | Session tokens (5-min TTL), rate-limit counters, feature flags. |
| Edge inference | Workers AI | bge-m3 embed (1024-dim), IndicTrans2 EN↔Indic translate, gpt-oss-20b last-resort chat. Saves a full inter-cloud hop on ~30% of requests. |
| Hot row cache | D1 | Read-replica of frequently-fetched content rows; eventual consistency, 60s TTL. |
| TLS / WAF / DDoS | Cloudflare zone | Shared across `syrabit.ai`, `api.syrabit.ai`, future subdomains. Turnstile gates signup/login. |

**Cost shape:** $0 marginal — everything fits inside the $5k Enterprise credit (which is mostly bandwidth + Workers requests headroom).

### 4.2 Digital Ocean — backend home

| Workload | Service | Sizing |
|---|---|---|
| FastAPI backend (canonical origin) | App Platform | Start `basic-xs` ($12/mo, 1GB RAM, 1 vCPU). Scale to `basic-s` ($25/mo, 2GB) at >1k DAU. App Platform auto-scaling at 80% CPU. |
| Background workers | App Platform worker component | Same Docker image as the API, different entrypoint (`python -m workers.queue_runner`). Polls Upstash Redis queue. |
| Rust core service | App Platform service | Per Task #331 — separate component, internal-only port, called by the FastAPI backend over the App Platform internal network. |
| Cron container | App Platform job | Hourly health-check publisher to Upstash. Heavier crons live on Azure (§4.4). |
| Dev / staging | Separate App Platform app | Same image, different env vars + Mongo cluster. Sleeps when not in use. |

**Connects to:** Mongo Atlas (driver), Upstash (REST), Pinecone (REST), all AI providers via the dispatcher.

**Egress:** to Cloudflare = $0 (Bandwidth Alliance). To AWS/Azure/AI providers = paid but tiny (text only). Large blob writes go browser → S3 directly via presigned URL (never touches DO).

**Cost shape:** $12–25/mo cash steady state. Apply for [DO Hatch](https://www.digitalocean.com/hatch) — gives $25k credit if accepted; even rejection gives 12 months $200 credit.

### 4.3 AWS — blob, email, async, and Cohere transport

| Workload | Service | Detail | Cost shape |
|---|---|---|---|
| User-content blob storage | **S3** (`s3://syrabit-prod-assets`, us-west-2) | Audio notes, PDF uploads, generated content backups, Cohere embed cache (hash → vector). Versioning ON, lifecycle to Glacier Deep Archive after 90d for backups. Browser uploads via presigned URL — never proxy through DO. | ~$0.02/GB-mo after 5GB free tier; expected ≤ $1/mo at MVP scale |
| Transactional email | **SES** (us-west-2) | Signup confirmation, password reset, study-streak digests, billing receipts. Verify `noreply@syrabit.ai` domain. | 62k emails/mo free *from Lambda*; $0.10 per 1k otherwise. ~$0–2/mo |
| Heavy async fan-out | **Lambda + SQS** | PDF→MCQ extraction (high RAM), batch audio synthesis, daily Mongo→S3 backup (triggered from Azure cron — §4.4). DO workers handle the lightweight queue; Lambda handles anything > 512 MB RAM or > 60s wall time. | Lambda: 1M req + 400k GB-sec/mo always-free. SQS: 1M req/mo always-free. ~$0/mo at MVP |
| Async logs | **CloudWatch Logs** | Lambda + Bedrock invocation logs only. Everything else logs to DO/CF. | 5GB/mo free, $0.50/GB after — expected $0/mo |
| Cohere inference (API only — not hosting) | **Bedrock** (Cohere embed-multilingual-v3 + rerank-v3.5) | Called from DO backend via direct boto3 (verified working today). See §6 for AI inference scoping. | ~$50/mo within $1k Activate credit |
| ~~EC2 / RDS / ECS / Fargate / EKS / DynamoDB~~ | **Not used** | DO is cheaper for predictable compute; Mongo Atlas is the DB; Upstash is the cache. Adding AWS equivalents would dilute the $1k Activate credit. | — |

### 4.4 Azure — cron, observability, and OpenAI inference

| Workload | Service | Detail | Cost shape |
|---|---|---|---|
| Scheduled jobs | **Container Apps (cron)** | Daily Mongo→S3 backup (triggers AWS Lambda which does the actual export), weekly Pinecone re-index, weekly cost-report email. KEDA cron schedule. | $0 within $2.5k credit |
| Alerting | **Logic Apps** | Webhooks: AppInsights anomaly → Telegram bot + founder email. Spend alarm: AWS > $5/day OR Azure > $10/day OR Vertex > $8/day → Telegram. | $0 within credit |
| Distributed tracing + APM | **Application Insights** | Python OpenTelemetry SDK on the DO backend exports spans here. Dashboards: per-pool latency (p50/p95/p99), per-provider error rate, daily $$ burn per provider. | $0 within credit (5GB/mo ingest free) |
| GPT-4.1-mini inference (API only — not hosting) | **Azure OpenAI** | Called from DO backend; primary for `english_rag_chat` + `content` pools. See §6. | ~$150/mo within $2.5k credit |
| ~~App Service / Functions / VMs / Storage~~ | **Not used** for backend or blob storage | DO does backend; S3 does blobs. Avoid double-booking workloads across credit pools. | — |

---

## 5. Egress topology (where the money quietly leaks)

| Hop | Volume estimate | Cost |
|---|---|---|
| Browser ↔ Cloudflare | Unlimited | $0 |
| Cloudflare ↔ DO backend | All API requests | **$0** (Bandwidth Alliance) |
| DO ↔ Mongo Atlas (same region) | All DB ops | $0 (peering) |
| DO ↔ Upstash REST | Session, rate-limit, queue | tiny, $0 effectively |
| DO ↔ AI providers (text) | All LLM/embed/rerank calls | <$1/mo |
| DO ↔ AWS S3 | **Avoid** — writes go browser → S3 directly via presigned URL | $0 (bypassed) |
| Azure cron ↔ AWS Lambda | Webhook trigger only — tiny | $0 (text payload) |
| Browser ↔ S3 (presigned URL) | Audio + PDF uploads | $0 in (uploads); $0.09/GB out from S3 (downloads) — mitigated by serving downloads via CF cache where possible |
| Cloudflare R2 ↔ anyone | All R2 reads | $0 (CF guarantee) |

**Rule of thumb:** any blob > 1MB goes through a Cloudflare R2 or AWS S3 *presigned URL* — never proxy it through DO.

---

## 6. Hosting vs. inference — the separation

This is a **hosting** plan. AI inference is a separate concern owned by the dispatcher in `artifacts/syrabit-backend/config.py` (`PROVIDER_PRIORITY` + `POOL_WEIGHTS`). Some clouds wear both hats; some wear only one:

| Cloud | Hosting role | Inference role |
|---|---|---|
| Cloudflare | ✅ frontend + edge + R2 + KV + D1 | ✅ Workers AI (bge-m3 embed, IndicTrans2 translate, gpt-oss-20b last-resort chat) |
| Digital Ocean | ✅ backend API + workers + cron | ❌ none |
| AWS | ✅ S3 + SES + Lambda + SQS + CloudWatch | ✅ Bedrock (Cohere embed + rerank only — **never** Anthropic/Nova/Titan/Mistral, those are covered by Azure + the dispatcher's other pools) |
| Azure | ✅ Container Apps cron + Logic Apps + AppInsights | ✅ Azure OpenAI (GPT-4.1-mini for english chat + content fallback) |
| GCP / Vertex | ❌ not used for hosting | ✅ Gemini 2.5 Flash API only (six feature pools — see dispatcher) |

**Why call this out:** the previous plan blurred the two and treated Vertex like a hosting cloud. It isn't — it's only an API endpoint we hit. Same for the inference-side use of AWS Bedrock and Azure OpenAI: they're API services on otherwise-hosting-focused clouds, called by the DO backend.

---

## 7. Credit burn schedule (12-month plan)

| Cloud | Credit | Burn target | When dry |
|---|---:|---|---|
| Cloudflare | $5,000 | $200/mo (mostly Workers/R2 above free tier as we grow) | month 25+ |
| Azure | $2,500 | $150/mo (GPT-4.1-mini + Container Apps + AppInsights) | month 16+ |
| AWS Activate | $1,000 | $50/mo (Bedrock Cohere mostly; S3/SES/Lambda all free tier) | month 20+ |
| Mongo Atlas | $500 | M0 free tier covers MVP; credit applies after upgrade | month 24+ |
| Vertex (inference, not hosting) | $2,000 | $120/mo (Gemini Flash API) | month 16+ |
| DO Hatch (if approved) | up to $25,000 | $25–40/mo cash without credit | depends on grant |
| **Total runway** | **~$11,000 base** | **~$520/mo all-in cloud** | **~20 months at MVP scale** |

Add credits being chased (`credit-applications.md`): OpenRouter $5k + ElevenLabs $4k + AssemblyAI $1.5k + Deepgram $1k = +$11.5k → potential 32-month runway.

---

## 8. What to do next (concrete, ordered)

1. **Re-enable Cohere via direct boto3** to AWS Bedrock (verified working today). 1-day task, high-leverage for Assamese embed quality.
2. **Stand up `s3://syrabit-prod-assets`** in us-west-2 with versioning + 90d → Glacier Deep Archive lifecycle. Wire from backend via boto3 (S3 free tier covers MVP entirely). Add `AWS_S3_BUCKET` secret.
3. **Apply for DO Hatch** — even a rejection gives $200/12mo, and acceptance gives up to $25k. 5-min form.
4. **Wire AWS SES** — verify `noreply@syrabit.ai` domain, replace whatever email path is current (or zero) with `boto3.client('ses').send_email`. Free tier covers ≤62k/mo.
5. **Move daily Mongo→S3 backup** to AWS Lambda triggered by Azure Container Apps cron (Azure has more credit headroom than AWS EventBridge billing).
6. **Wire Application Insights** — install Azure Monitor OpenTelemetry SDK on the DO backend. First dashboards: per-pool latency p50/p95/p99 + per-provider error rate + daily $$ burn.
7. **Set spend alarms** via Logic Apps: AWS > $5/day, Azure > $10/day, Vertex > $8/day → Telegram bot.
8. **Document & retire** `RAILWAY-DEPLOYMENT.md` and the AWS-ECS-Express path in `AWS-DEPLOYMENT.md` once DO is the canonical origin and AWS is scoped to S3/SES/Lambda/Bedrock only. Replace with a pointer to this doc.

---

## 9. What NOT to do (explicit guardrails)

- **Do not** put the backend API on multiple clouds simultaneously. One canonical origin (DO). No Cloud Run, no AWS Fargate, no Azure App Service for the backend — all three would compete with DO and burn credit pools meant for other workloads.
- **Do not** use Azure Blob Storage or GCS — **S3 is the chosen object store**. Mixing storage backends is a footgun for SDK churn and lifecycle policy drift.
- **Do not** use AWS Bedrock for chat (Anthropic, Nova, Mistral, Titan). Azure GPT-4.1-mini and Vertex Gemini cover those roles cheaper. Bedrock is **Cohere-only** in this architecture.
- **Do not** use Cloudflare Workers for long-running backend logic (>10s CPU). Use DO for that; the worker is a proxy, not the app.
- **Do not** spend cash on a managed Postgres while Mongo Atlas + Upstash + Pinecone cover all current needs.
- **Do not** deploy backend code on GCP — Vertex is API-only in this plan; adding compute there would dilute the $2k Vertex credit pool that's already fully booked for Gemini inference.
- **Do not** edit `PROVIDER_CREDITS` / `POOL_WEIGHTS` based on aspirational credit grants — gated checklist in `credit-applications.md` §"When a grant is approved" applies.

---

## 10. Single sentence summary

> **Frontend on Cloudflare, backend on Digital Ocean, blob + email + async on
> AWS, cron + observability on Azure** — and all five AI providers
> (Cloudflare Workers AI, AWS Bedrock Cohere, Azure OpenAI, Vertex Gemini,
> direct vendor APIs) are called from the DO backend via the dispatcher.
