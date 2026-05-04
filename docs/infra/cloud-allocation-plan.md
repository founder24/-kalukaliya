# Syrabit — Five-Cloud Hosting & Infra Allocation Plan

**Last updated:** 2026-05-04
**Status:** Active plan — supersedes ad-hoc allocations in `AWS-DEPLOYMENT.md` /
`DEPLOYMENT.md` / `RAILWAY-DEPLOYMENT.md` for the steady-state architecture.
**Owner:** founder@syrabit.ai
**Related:** Tasks #327 (4-way rebalance), #328 (AWS landing zone),
#329 (Azure landing zone), #330 (DO App Platform), #331 (DO port),
#340 (Cohere routing), `credit-applications.md` (credit pool source of truth).

---

## 1. Guiding principles

1. **One cloud, one job.** Each cloud owns workloads that play to its
   structural advantage (latency, free tier, credit pool, vendor lock-in
   of a specific managed service). No cloud holds two competing copies
   of the same workload.
2. **Credit-funded first, free-tier second, paid last.** Spend startup
   credits before they expire; fall back to always-free quotas; only
   pay cash for things no credit covers.
3. **Egress is the silent killer.** Keep request paths inside one cloud
   per hop wherever possible. Cloudflare → DO costs $0 (Bandwidth
   Alliance). DO → AWS costs $0.09/GB. Architect the data plane around
   that ratio.
4. **Single front door (Cloudflare).** Every public URL terminates at
   Cloudflare first — TLS, WAF, Turnstile, mTLS to origin, rate limit,
   Pages, and the edge worker proxy. Origin clouds are never directly
   exposed to the internet.
5. **No vendor-managed primary database.** Mongo Atlas (already
   approved $500 + free M0) is the only stateful primary. Each cloud
   uses its native key/value or queue layer for *transient* state
   only — Mongo remains the source of truth.

---

## 2. Per-cloud responsibility matrix

| Cloud | Owns | Why this cloud | Funded by |
|---|---|---|---|
| **Cloudflare** | Frontend (Pages), edge proxy worker, WAF, DDoS, Turnstile, mTLS, KV (rate-limit + session), R2 (cold blob storage), D1 (read-cache), Vectorize (optional secondary index), **Workers AI** (bge-m3 embed, gpt-oss-20b last-resort chat, IndicTrans2) | Free 5GB R2 egress to anywhere, $0 to Bandwidth Alliance peers (DO), global PoPs, only cloud with TLS+WAF+CDN+compute+inference under one bill. Already-approved $5k Enterprise credit. | $5,000 Cloudflare for Startups (approved, exp 2026-09) |
| **Digital Ocean** | **Backend API** (FastAPI on App Platform), background workers, Rust core service, scheduled cron container, dev/staging environments | Cheapest predictable compute ($12/mo for 1GB app vs $30+ on AWS Fargate), no egress to Cloudflare (Bandwidth Alliance), zero AWS-config tax. Already ported in Task #331. | DO Hatch credits (apply if not already), then cash ~$25–40/mo |
| **AWS** | **Bedrock Cohere** (embed-multilingual-v3 + rerank-v3.5), **S3** (audio notes, PDF uploads, generated content), **SES** (transactional email), **Lambda + SQS** (heavy async jobs that exceed DO worker memory or need fan-out) | Bedrock is the only place to get Cohere multilingual at this price point on direct AWS auth — verified working today. S3+SES+Lambda are best-in-class at the free tier. Activate $1k credit covers Bedrock spend. | $1,000 AWS Activate (approved); free tier covers S3/SES/Lambda/SQS for year 1 |
| **Azure** | **Azure OpenAI** (GPT-4.1-mini for `english_rag_chat` + `content` pools), **Container Apps cron + Logic Apps** (Task #329 landing zone — observability + scheduled cleanups), **App Insights** (alerting) | Azure OpenAI has the highest rate limits and lowest latency for GPT-4.1-mini in Asia-South region. Microsoft for Startups credit pool is large and dedicated to Azure-only spend, so not using Azure forfeits the credit. | $2,500 Azure for Startups (approved) |
| **GCP / Vertex** | **Gemini 2.5 Flash** (primary `content`, `vision`, `safety`, fallback `english_rag_chat`/`assamese_rag_chat`), **Cloud Run** (optional secondary backend origin per Task #606), **Cloud Build** (image registry for Cloud Run) | Gemini 2.5 Flash is the only 1M-context model in budget; Vertex is the cheapest path to it. GCP for Startups credit is generous and Vertex-bound. | $2,000 GCP Startups (approved) |

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
                                                  │  • Workers AI for    │
                                                  │    embed/translate   │
                                                  │  • Last-resort chat  │
                                                  │    via gpt-oss-20b   │
                                                  └──────────┬───────────┘
                                                             │ Bandwidth
                                                             │ Alliance
                                                             │ ($0 egress)
                                                             ▼
                                              ┌──────────────────────────┐
                                              │ Digital Ocean App Platform│
                                              │  • FastAPI backend (8GB)  │
                                              │  • Background workers     │
                                              │  • Rust core service      │
                                              │  • Cron container         │
                                              └──────┬─────────────┬──────┘
                                                     │             │
                          ┌──────────────────────────┤             ├─────────────────────┐
                          │                          │             │                     │
                          ▼                          ▼             ▼                     ▼
                ┌──────────────────┐       ┌─────────────┐  ┌───────────────┐  ┌──────────────────┐
                │ Mongo Atlas      │       │ Upstash     │  │ Pinecone      │  │ AI providers     │
                │ (M0 free + $500) │       │ Redis REST  │  │ (curated      │  │ (per-pool routing│
                │ — primary store  │       │ — sessions, │  │  vector index)│  │  via dispatcher) │
                └──────────────────┘       │   rate-lim, │  └───────────────┘  └────────┬─────────┘
                                           │   queues    │                              │
                                           └─────────────┘                              │
                                                                                        │
   ┌──────────────────────┬──────────────────────┬──────────────────────┬───────────────┘
   │                      │                      │                      │
   ▼                      ▼                      ▼                      ▼
┌────────────┐      ┌──────────────┐      ┌─────────────┐      ┌────────────────┐
│ Vertex     │      │ Azure OpenAI │      │ AWS Bedrock │      │ Cloudflare     │
│ Gemini 2.5 │      │ GPT-4.1-mini │      │ Cohere      │      │ Workers AI     │
│ Flash      │      │              │      │ embed +     │      │ (bge-m3,       │
│ — content, │      │ — english    │      │ rerank      │      │  IndicTrans2,  │
│  vision,   │      │   chat,      │      │             │      │  gpt-oss-20b)  │
│  safety    │      │   content    │      │             │      │ — last resort  │
└────────────┘      │   fallback   │      └─────────────┘      └────────────────┘
                    └──────────────┘
                                                    + AWS S3 (blob storage)
                                                    + AWS SES (transactional email)
                                                    + AWS Lambda+SQS (heavy async fan-out)
```

---

## 4. Workload allocation — service-by-service

### 4.1 Frontend (Cloudflare Pages)
- **Why CF Pages, not Vercel/Netlify:** unlimited bandwidth on the free tier, same vendor as the edge worker (single zone, single dashboard), Bandwidth Alliance to DO origin, and the $5k credit covers Pages Functions if we ever need them.
- **Build:** `pnpm install --frozen-lockfile && cd artifacts/syrabit && pnpm run build` → output `artifacts/syrabit/dist/`. Already documented in `DEPLOYMENT.md`.
- **Cost:** $0 (Pro plan inside the $5k credit if we hit limits).

### 4.2 Edge proxy (Cloudflare Worker)
- **Job:** mTLS to origin, WAF, Turnstile challenge, KV-backed rate limit, D1 read-cache for hot GETs, request-fingerprint logging.
- **Inference at the edge:** when a request can be served by a 3-cent model (translate via IndicTrans2, embed via bge-m3, last-resort chat via gpt-oss-20b), the worker handles it inline without ever hitting DO. Saves a full inter-cloud hop on ~30% of requests.
- **Cost:** included in $5k credit; ~$0 marginal.

### 4.3 Backend API (Digital Ocean App Platform)
- **Why DO, not Cloud Run / Railway / ECS:** Cloud Run cold-starts add 1–2s p95 on first hit per region; Railway pricing scales steeper than DO past the trial; AWS Fargate is 2.5× the cost of equivalent DO. DO App Platform is the only offering with predictable $-per-month pricing under $25 for our actual load shape (mostly idle, bursty).
- **Sizing:** start at `basic-xs` ($12/mo, 1GB RAM, 1 vCPU). Scale to `basic-s` ($25/mo, 2GB) at >1k DAU. App Platform auto-scaling kicks in at 80% CPU.
- **Connects to:** Mongo Atlas (driver), Upstash (REST), Pinecone (REST), and all AI providers via `select_provider` dispatch.
- **Egress:** to Cloudflare = $0 (Bandwidth Alliance). To AI providers = paid but tiny (text only).
- **Cost:** $12–25/mo cash. Apply for [DO Hatch](https://www.digitalocean.com/hatch) — gives $25k credit if accepted; even rejection gives 12 months $200 credit.

### 4.4 AI inference routing
The dispatcher (`config.PROVIDER_PRIORITY` + `POOL_WEIGHTS`) is the **only** place routing decisions live. Per-pool target allocation:

| Feature pool | Primary | Fallback | Last resort | Where |
|---|---|---|---|---|
| `english_rag_chat` | Azure OpenAI GPT-4.1-mini (10000) | Vertex Gemini 2.5 Flash (100) | Workers AI gpt-oss-20b (0) | Azure → GCP → CF |
| `assamese_rag_chat` | Sarvam-M (10000) | Vertex Gemini (100) | — | Sarvam → GCP |
| `content` | Vertex Gemini (10000) | Azure GPT-4.1-mini (100) | Sarvam (50), Workers AI (0) | GCP → Azure → CF |
| `assamese_content` | Workers AI IndicTrans2 (5000) | Vertex (100) | — | CF → GCP |
| `embed` (en + indic) | **Workers AI bge-m3 (10000)** *(post-rollback)* | — | — | CF |
| `rerank` | Pinecone (10000) | Workers AI (0) | — | Pinecone → CF |
| `tts` | ElevenLabs (500) | Deepgram (500) | — | direct |
| `stt` | Deepgram | AssemblyAI | Vertex | direct |
| `translate` | Workers AI IndicTrans2 (10000) | Vertex (100) | — | CF → GCP |
| `vision` | Vertex Gemini | Azure OpenAI | Workers AI | GCP → Azure → CF |
| `search_rag` | Exa | Workers AI | — | Exa → CF |
| `live_search` | Exa | Tavily | Workers AI | Exa → Tavily → CF |

> **Recommended optional change:** re-enable Cohere via direct boto3 to AWS Bedrock (verified working today) and add it back to `embed_indic` as the primary for Indic queries. Workers AI bge-m3 stays primary for English (`embed_en`). This reclaims the multilingual-quality advantage without the Cloudflare BYOK billing problem. Decide separately — current rollback is the safe default.

### 4.5 AWS workloads (beyond Bedrock)

| Service | Use | Why AWS, not elsewhere | Cost shape |
|---|---|---|---|
| **S3** (us-west-2 bucket `syrabit-prod-assets`) | Audio notes, PDF uploads, generated content backups, Cohere embed cache (hash → vector) | Cheapest object store with versioning + lifecycle rules; 5GB free tier for year 1; lifecycle to Glacier Deep Archive after 90d for backups | ~$0.02/GB-mo after free tier; expected ≤ $1/mo at MVP scale |
| **SES** (us-west-2) | Transactional email — signup confirmation, password reset, study-streak digests | 62k emails/mo free *from EC2/Lambda*, $0.10 per 1k otherwise; cheapest reliable transactional email | ~$0–2/mo |
| **Lambda + SQS** | Heavy async jobs that exceed DO worker memory: PDF→MCQ extraction (high RAM), batch audio synthesis, daily Mongo→S3 backup | 1M Lambda req + 400k GB-sec free *forever*; SQS 1M req/mo free forever; pay-per-ms beats keeping a DO worker idle | ~$0/mo at MVP; scales linearly with batch volume |
| **CloudWatch Logs** | Bedrock + Lambda logs only (everything else logs to DO/Cloudflare) | Native integration; 10 metrics + 5GB logs free | $0 |
| ~~EC2 / RDS / ECS / Fargate~~ | **Not used** | DO is cheaper for predictable compute; we don't need them. | — |

### 4.6 Azure workloads (beyond Azure OpenAI)

| Service | Use | Why Azure | Cost shape |
|---|---|---|---|
| **Azure OpenAI** | GPT-4.1-mini for english chat + content fallback | Highest RPM in Asia-South region, lowest p50 latency for that model | covered by $2.5k credit |
| **Container Apps (cron)** | Daily Mongo→S3 backup trigger, Pinecone re-indexing job, weekly aggregate job (Task #329) | Azure cron is cheaper than AWS EventBridge+Lambda for KEDA-style scheduled containers; uses up the credit pool | $0 within credit |
| **Logic Apps (alerting)** | If `error_rate > 1%` → Telegram + email alert (Task #329) | Cheap, low-code alerting that doesn't need its own service to run | $0 within credit |
| **Application Insights** | Distributed tracing for backend ↔ AI providers | Best-in-class for Python OpenTelemetry; integrates with Logic Apps for alerts | $0 within credit |
| ~~App Service / Functions / VMs~~ | **Not used** for backend API (DO does that) | Avoid double-booking the same workload | — |

### 4.7 GCP / Vertex workloads (beyond Gemini)

| Service | Use | Why GCP | Cost shape |
|---|---|---|---|
| **Vertex AI Gemini 2.5 Flash** | Primary `content`, `vision`, `safety`; fallback `english_rag_chat` | Only 1M-context model in budget; native multimodal; cheapest at our token mix | covered by $2k credit |
| **Cloud Run (optional secondary origin)** | Per Task #606 — second backend origin for warm failover | Cold-start mitigated by min-instances=1; same image as DO | ~$5/mo at min-instances=1 |
| **Cloud Build** | Image registry + CI build for Cloud Run image | Native; integrates with Cloud Run; included in credit | $0 within credit |
| ~~Compute Engine / GKE / Cloud SQL~~ | **Not used** | DO + Mongo Atlas cover compute and DB | — |

---

## 5. Egress topology (where the money quietly leaks)

| Hop | Volume estimate | Cost |
|---|---|---|
| Browser ↔ Cloudflare | Unlimited | $0 |
| Cloudflare ↔ DO backend | All API requests | **$0** (Bandwidth Alliance) |
| DO ↔ Mongo Atlas (same region) | All DB ops | $0 (peering) |
| DO ↔ Upstash REST | Session, rate-limit | tiny, $0 effectively |
| DO ↔ AI providers | Text only — small | <$1/mo |
| DO ↔ AWS S3 | Audio + PDF uploads | $0.09/GB out from DO; **mitigation: write S3 directly from CF Worker via R2-style presigned URL** so the upload never traverses DO |
| AWS Bedrock ↔ DO | Embed/rerank responses | included in Bedrock cost (no separate egress) |
| Cloudflare R2 ↔ anyone | All R2 reads | $0 (CF guarantee) |

**Rule of thumb:** any large blob (>1MB) goes through a Cloudflare R2 or S3 *presigned URL* — never proxy it through DO.

---

## 6. Credit burn schedule (12-month plan)

| Cloud | Credit | Burn target | When dry |
|---|---:|---|---|
| Cloudflare | $5,000 | $200/mo (mostly Workers/R2 above free tier as we grow) | month 25+ |
| Azure | $2,500 | $150/mo (GPT-4.1-mini + Container Apps + AppInsights) | month 16+ |
| Vertex | $2,000 | $120/mo (Gemini Flash + Cloud Run) | month 16+ |
| AWS Activate | $1,000 | $50/mo (Bedrock Cohere mostly) | month 20+ |
| Mongo Atlas | $500 | M0 free tier covers MVP; credit applies after upgrade | month 24+ |
| Total runway | **$11,000** | ~$520/mo all-in cloud | **20 months at MVP scale** |

Add credits being chased (`credit-applications.md`): OpenRouter $5k + ElevenLabs $4k + AssemblyAI $1.5k + Deepgram $1k = +$11.5k → potential 32-month runway.

---

## 7. What to do next (concrete, ordered)

1. **Re-enable Cohere via direct boto3** (decide first — see §4.4 note). 1-day task, high-leverage for Assamese embed quality.
2. **Stand up `s3://syrabit-prod-assets`** in us-west-2 with versioning + 90d → Glacier Deep Archive lifecycle. Wire from backend via boto3 (S3 free tier covers MVP entirely). Add `AWS_S3_BUCKET` secret.
3. **Apply for DO Hatch** — even a rejection gives $200/12mo, and acceptance gives $25k. 5-min form.
4. **Wire AWS SES** — verify `noreply@syrabit.ai` domain, replace whatever email path is current (or zero) with `boto3.client('ses').send_email`. Free tier covers ≤62k/mo.
5. **Move daily Mongo→S3 backup to AWS Lambda** triggered by EventBridge cron (or Azure Container Apps cron — pick one; Azure has more credit headroom).
6. **Document & retire** `RAILWAY-DEPLOYMENT.md` and the AWS-ECS-Express path in `AWS-DEPLOYMENT.md` once DO is the canonical origin and AWS is scoped to S3/SES/Lambda/Bedrock only. Replace with a pointer to this doc.
7. **Set CloudWatch + AppInsights alarms** for: any 5xx burst > 10/min, AWS spend > $5/day, Azure spend > $10/day, Vertex spend > $8/day. Routes to Logic Apps → Telegram.

---

## 8. What NOT to do (explicit guardrails)

- **Do not** put the backend API on multiple clouds simultaneously. One canonical origin (DO) + one warm-failover origin (Cloud Run if Task #606 ships). Anything else is operational debt.
- **Do not** use Azure Blob Storage or GCS — S3 is the chosen object store. Mixing is a footgun for SDK churn and lifecycle policy drift.
- **Do not** use AWS Bedrock for chat (Anthropic, Nova, Mistral, Titan). Azure GPT-4.1-mini and Vertex Gemini cover those roles cheaper. Bedrock is **Cohere-only** in this architecture.
- **Do not** use Cloudflare Workers for long-running backend logic (>10s CPU). Use DO for that; the worker is a proxy, not the app.
- **Do not** spend cash on a managed Postgres while Mongo Atlas + Upstash + Pinecone cover all current needs.
- **Do not** edit `PROVIDER_CREDITS` / `POOL_WEIGHTS` based on aspirational credit grants — gated checklist in `credit-applications.md` §"When a grant is approved" applies.

---

## 9. Single sentence summary

> **Cloudflare** is the front door and edge brain, **Digital Ocean** is the
> backend's home, **AWS** is the blob store + transactional email + Cohere
> transport, **Azure** is GPT-4.1-mini + scheduled cron + observability, and
> **Vertex** is Gemini's home and the warm-failover backend origin — and the
> dispatcher routes every AI call to the cheapest credit-funded path.
