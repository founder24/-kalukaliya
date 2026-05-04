# Syrabit — Per-Cloud Service & Feature Breakdown

**Companion to:** `docs/infra/cloud-allocation-plan.md` (the strategic plan).
**Last updated:** 2026-05-04
**Scope:** exhaustive list of every service used on each of the five clouds —
what it's used for, why it lives on that cloud, what it costs, and what
guardrail keeps it scoped. If a service is **not used**, that's listed too,
with the reason (avoiding silent re-introduction later).

> Five clouds are involved in Syrabit, but only **four are hosting clouds**:
> Cloudflare, Digital Ocean, AWS, Azure. **GCP / Vertex is inference-only**
> (Gemini API + retained legacy AI APIs) and is included here for
> completeness, not as a hosting target.

---

## Quick map

| Cloud | Pillar role | Funded by |
|---|---|---|
| **Cloudflare** | Frontend + edge + global perimeter | $5k Cloudflare for Startups |
| **Digital Ocean** | Backend canonical origin | DO Hatch (apply) → ~$25/mo cash |
| **AWS** | Blob, transactional email, async fan-out, Cohere transport | $1k AWS Activate + free tier |
| **Azure** | Cron, observability, GPT-4.1-mini transport | $2.5k Microsoft for Startups |
| **GCP / Vertex** | AI inference API only — **never hosting** | $2k GCP for Startups |

---

## 1. Cloudflare — frontend + edge + perimeter

### 1.1 Used

| Service | Use in Syrabit | Why CF | Cost shape |
|---|---|---|---|
| **Pages** | Hosts the React + Vite SPA built from `artifacts/syrabit/dist/`. Connected to GitHub `main`; deploys on push. Preview deployments per PR. | Free unlimited bandwidth + global PoPs; build cache hot. | $0 (within free Pages plan) |
| **Workers (edge proxy)** | Single front door for `api.syrabit.ai`. Terminates TLS, applies WAF rules, runs Turnstile challenge for write paths, mTLS upstream to DO origin, KV-backed sliding-window rate limit, D1 hot-row read-cache. | Only place we get TLS + WAF + edge compute under one bill; Bandwidth Alliance to DO = $0 egress. | $0 within free tier (10M req/mo); paid above |
| **Workers AI** | Edge inference: `bge-m3` for embeds (1024-dim, primary embed pool), `IndicTrans2` EN↔Indic translate, `gpt-oss-20b` last-resort chat fallback, `whisper-large-v3-turbo` STT fallback. | Saves an inter-cloud hop on ~30% of requests; included in Enterprise credit. | $0 within $5k credit |
| **R2** | Cold blob bucket for archived generated content, public-readable image assets too large for the SPA build, Cloudflare Cache Reserve for the API edge. | $0 egress to anywhere; no AWS-style egress trap. | $0.015/GB-mo storage; $0 egress |
| **KV** | Session tokens (5-min TTL), per-IP and per-user rate-limit counters, edge feature flags, A/B bucket assignments. | Sub-ms reads at the edge; eventual consistency is fine for these uses. | $0 within free tier |
| **D1** | Hot-row read-cache for chapter/MCQ/content reads with 60s TTL, edge-replicated. | SQLite at the edge; cheap reads; eventual consistency acceptable. | $0 within free tier |
| **Vectorize** | Optional secondary vector index for failover when Pinecone is throttled or down. Not the primary index. | Co-located with Workers AI embeds; no extra hop. | $0 within $5k credit |
| **Cache (HTTP cache + Cache Reserve)** | Tier-1 cache for static + edge-cacheable API responses (chapter content, MCQ lists). | Standard CF feature; Cache Reserve adds R2-backed persistence. | included |
| **Zone (DNS + TLS)** | Authoritative DNS for `syrabit.ai` and subdomains. Universal SSL + ACM-style auto-renewal. Geo-restriction rules for admin paths. | Required for all the above. | $0 |
| **WAF + DDoS + Bot Management** | Block scrapers, SQLi/XSS rules, rate-limit abusive IPs, bot scoring on signup/login. | Best-in-class at the edge; included with the zone. | $0 within plan |
| **Turnstile** | Invisible CAPTCHA on signup, login, password reset, contact form. | Free, no Google reCAPTCHA tax. | $0 |
| **Zero Trust / Access** | mTLS cert issuance for the worker → DO origin handshake; admin-panel SSO. | Closes the origin to the public internet. | $0 within free tier (50 users) |
| **Email Routing + Email Workers** | Catch-all email aliases for `*@syrabit.ai`, tier-1 transactional sender (signup confirm, password reset). Falls back to Resend then SES. | Free, simple, beats configuring SES from scratch for the first 10k/mo. | $0 |
| **Logpush** | Streams CF Worker + zone logs into Axiom for retained search. | Native CF → Axiom integration; no proxy needed. | $0 within plan |
| **Analytics Engine** | Custom edge metrics emitted from Workers (per-pool latency, AI Gateway hit/miss). | Cheap edge time-series, no extra infra. | $0 within free tier |
| **AI Gateway** | Caching + observability layer in front of Cohere/Workers AI/OpenAI/Gemini calls made from the worker (the few calls that originate at the edge). | Saves repeat-call cost on hot prompts; one-line config. | $0 within plan |

### 1.2 Not used (and why)

| Service | Why we skip it |
|---|---|
| **Cloudflare Stream** | Video isn't a current feature; can revisit if Read-Aloud → video lands. |
| **Cloudflare Images** | R2 + native `<img>` resize via Workers covers our needs. |
| **Cloudflare Queues** | AWS SQS is the chosen async transport (closer to Lambda consumers). |
| **Hyperdrive** | Mongo Atlas + Upstash REST already cover DB/cache; no Postgres origin to accelerate. |
| **Pages Functions** | Edge logic lives in the Worker, not in Pages Functions, to keep one deploy unit. |

---

## 2. Digital Ocean — backend canonical origin

### 2.1 Used

| Service | Use in Syrabit | Sizing / config | Cost shape |
|---|---|---|---|
| **App Platform — Service: `syrabit-backend`** | Python/FastAPI from `artifacts/syrabit-backend/Dockerfile`. Canonical API origin. Health check at `/api/health`. Internal port 3000. Env vars from DO secrets. | Start `basic-xs` ($12/mo, 1GB RAM, 1 vCPU). Auto-scale at 80% CPU to 3 instances. Bump to `basic-s` ($25/mo, 2GB) at >1k DAU. | $12–25/mo |
| **App Platform — Worker: `syrabit-workers`** | Same image as the API, entrypoint `python -m workers.queue_runner`. Polls Upstash Redis queue + SQS bridge. | `basic-xxs` ($5/mo, 512MB). Single instance. | $5/mo |
| **App Platform — Service: `rust-core`** | Rust "Neural Mesh Core" from `backend/rust-core/Dockerfile`. Internal-only, called by `syrabit-backend` over App Platform internal network on port 50051 (gRPC) and 3000 (HTTP). | `basic-xxs` ($5/mo). Internal route only — not exposed publicly. | $5/mo |
| **App Platform — Job: `cron-heartbeat`** | Hourly health-check publisher to Upstash, runs `python -m workers.heartbeat`. Heavier crons live on Azure Container Apps. | Pre-built component; ~$0/mo. | included |
| **App Platform — App: `syrabit-staging`** | Separate App Platform app pinned to `staging` branch. Same image; staging Mongo + Upstash creds. Sleeps when idle. | `basic-xxs`, sleep-on-idle. | $0–5/mo |
| **App Platform — App: `syrabit-dev`** | PR preview environments for backend changes. | per-PR ephemeral. | $0 (within Hatch) |
| **Container Registry** | Built images for App Platform components (when not using GitHub source). | included with App Platform. | included |
| **Spaces (S3-compatible) — *optional*** | Only used if AWS S3 is unreachable; staging-only mirror of generated audio. Production reads/writes always hit AWS S3. | 250 GB / $5/mo if enabled. | $0 (currently off) |
| **Monitoring (DO native)** | App Platform metrics (CPU, RAM, req/s, error rate) exported to Application Insights via OTel sidecar. | included. | $0 |
| **Reserved IPs** | Static egress IP for outbound calls to providers that whitelist by IP (Sarvam staging in the past). | $4/mo per IP. Currently 0 reserved. | $0 |
| **VPC** | Default VPC for App Platform internal-network communication between `syrabit-backend` ↔ `rust-core` ↔ `syrabit-workers`. | included. | $0 |

### 2.2 Not used (and why)

| Service | Why we skip it |
|---|---|
| **Droplets / Kubernetes (DOKS)** | App Platform covers our compute needs; managed beats hand-rolled. We previously considered a Droplet for the Rust core but App Platform's gRPC port support solved it. |
| **Managed Databases (Postgres / Mongo / Redis)** | Mongo Atlas + Upstash already chosen; double-managed = double-billed. |
| **Load Balancers** | App Platform fronts services itself; CF Worker is the actual load balancer for clients. |
| **DO Functions** | AWS Lambda + Azure Container Apps Jobs cover serverless / scheduled workloads. |
| **Object Storage (Spaces)** | AWS S3 is the canonical store. Spaces is kept as an opt-in fallback only. |

---

## 3. AWS — blob, email, async, Cohere transport

### 3.1 Used

| Service | Use in Syrabit | Region / config | Cost shape |
|---|---|---|---|
| **S3 — `s3://syrabit-prod-assets`** | User-uploaded PDF lecture notes, audio notes, generated content backups, embed cache (hash → vector blob), daily Mongo backup dumps. Versioning ON, lifecycle to Glacier Deep Archive at 90d for backups. | `us-west-2`. Bucket policy denies non-mTLS, denies public read except on `/public/*` prefix. | ~$1–3/mo at MVP scale (5GB free year 1) |
| **S3 — `s3://syrabit-prod-public`** | Public-readable assets that need stable HTTPS URLs (cover images, sample audio). Served via CF Cache Reserve to dodge S3 egress. | `us-west-2`. CF in front. | <$1/mo |
| **SES** | Transactional email: signup confirm, password reset, study-streak digests, billing receipts, admin notifications. `noreply@syrabit.ai` verified. | `us-west-2`. Out of sandbox. | $0 within 62k/mo Lambda free tier; $0.10 per 1k otherwise |
| **Lambda** | Memory-heavy async jobs: PDF→MCQ extraction, batch audio synthesis, daily Mongo→S3 backup (triggered by Azure Container Apps cron), AI Gateway log replay. | `us-west-2`. ARM64. Each function in its own role. | $0 within 1M req + 400k GB-sec free tier |
| **SQS** | Queue layer between DO backend and Lambda consumers. Standard queues for fan-out (SEO publish, audio synth fan-out), FIFO for ordered jobs (per-user PDF processing). DLQ on each. | `us-west-2`. Visibility timeout 6× expected processing time. `maxReceiveCount` = 5. | $0 within 1M req/mo free tier |
| **SNS** | CloudWatch alarm fan-out: AWS infra alerts → Slack webhook. Bridges CloudWatch → Slack without writing custom code. | `us-west-2`. | $0 within free tier |
| **Bedrock** | Cohere-only: `cohere.embed-multilingual-v3` (Indic/English embeds, 1024-dim, primary embed pool) and `cohere.rerank-v3-5` (rerank pool). Direct boto3 from DO backend (verified working). | `us-west-2`. IAM role with `bedrock:InvokeModel` on those two model ARNs only. | ~$50/mo within $1k Activate credit |
| **CloudWatch Logs** | Lambda + Bedrock invocation logs only. Backend logs go to Axiom + App Insights, not CloudWatch. | `us-west-2`. 5GB/mo free. | $0 expected |
| **CloudWatch Alarms** | SQS queue depth > 100, DLQ depth > 0, Lambda error rate > 1%, Bedrock throttle count > 10/min. Fire to SNS → Slack. | `us-west-2`. | included |
| **Secrets Manager** | Runtime secrets for Lambda functions (DB URIs, API keys for downstream providers). Rotated quarterly. | `us-west-2`. | ~$0.40/mo per secret; ~$2/mo total |
| **IAM** | Per-feature roles: one for Lambda exec, one for `syrabit-backend` (Bedrock + S3 + SES + SQS + Secrets read), one for the GitHub Actions deploy role (OIDC trust, no static keys). | account `926046660612`, IAM user `SYRABIT` for break-glass only. | $0 |
| **Route 53 — *optional, future*** | Only if we need health-checked DNS failover beyond what CF provides. Currently CF zone owns DNS. | not yet enabled. | $0 currently |
| **AWS Systems Manager Parameter Store** | Non-secret config (feature flags, schedule cadences) for Lambda. | `us-west-2`. | $0 within standard tier |
| **EventBridge — *limited*** | Only the Bedrock/Lambda invocation event bus for X-Ray + tracing fan-out. Cron schedules live on Azure Container Apps, not EventBridge. | `us-west-2`. | $0 within free tier |

### 3.2 Not used (and why)

| Service | Why we skip it |
|---|---|
| **EC2 / Lightsail** | DO is cheaper for predictable compute. |
| **ECS / Fargate / EKS** | Same — DO covers it. |
| **RDS / Aurora / DynamoDB** | Mongo Atlas + Upstash are the chosen DB layers. Adding RDS would double-bill the data tier and dilute the $1k Activate credit. |
| **ElastiCache** | Upstash Redis covers cache. |
| **Bedrock — Claude / Llama / Mistral / Titan / Nova** | Explicitly out of scope. Azure OpenAI (GPT-4.1-mini) and Vertex Gemini cover those LLM roles cheaper. Bedrock is **Cohere-only**. |
| **CloudFront** | Cloudflare is the CDN. |
| **API Gateway** | CF Worker is the gateway. |
| **Step Functions** | Lambda + SQS chains cover our workflow needs. |
| **Polly / Transcribe / Textract / Rekognition / Comprehend / Translate / Personalize / Fraud Detector** | Tracked under Task #337 as **future, optional, additional** providers in the failover chain. **Not part of the steady-state hosting plan** until that task lands. |
| **Macie / GuardDuty / Inspector / WAF** | CF WAF is the perimeter; AWS WAF would be redundant. |

---

## 4. Azure — cron, observability, GPT transport

### 4.1 Used

| Service | Use in Syrabit | Region / config | Cost shape |
|---|---|---|---|
| **Container Apps — Jobs (cron)** | Every scheduled workload: daily Mongo→S3 backup trigger (which then invokes AWS Lambda), weekly Pinecone re-index, weekly cost-report email, hourly SEO auto-publish, IndexNow batches, dead-endpoint pruner, Trustpilot refresh, CF log pull, weekly digest. KEDA cron triggers; scale-to-zero between runs. | `centralindia` (closest to Assam users). Managed identity → Key Vault for secrets. | $0 within $2.5k credit (scale-to-zero between runs) |
| **Container Apps — Service: `azure-openai-proxy` (optional)** | Thin proxy in front of Azure OpenAI for caching + retry. Deployed only if request volume justifies it (otherwise the DO backend calls Azure OpenAI directly). | `centralindia`. Currently OFF. | $0 currently |
| **Azure OpenAI Service** | `gpt-4.1-mini` deployment named `syrabit-chat`. Primary for `english_rag_chat` and `content` pools. PTU = none, pay-as-you-go. | `eastus` (cheapest). Content filter at `medium`. | ~$150/mo within $2.5k credit |
| **Logic Apps** | Alerting workflows: AppInsights anomaly → Telegram bot + founder email; spend alarms (AWS > $5/day, Azure > $10/day, Vertex > $8/day) → Telegram; DLQ-depth-rising → Telegram. Low-code, no maintenance. | `centralindia`. | $0 within credit |
| **Application Insights (Azure Monitor)** | Unified APM/trace sink for **all** hosting clouds: DO backend (Python OTel), DO Rust core (Rust OTel), AWS Lambda workers (otel-lambda layer), Azure cron jobs (native). Dashboards: per-pool latency p50/p95/p99, per-provider error rate, daily $$ burn per provider. | `centralindia` workspace. Sampling at 25% for high-volume traces. | $0 within 5GB/mo ingest free + credit |
| **Log Analytics Workspace** | Backing store for App Insights + Container Apps logs + Logic Apps run history. | shares region with App Insights. | $0 within credit |
| **Azure Monitor Alerts** | Backend alerts that don't need Logic-Apps-style fan-out: simple metric > threshold → action group → Slack. | `centralindia`. | $0 within credit |
| **Key Vault** | Secrets for Container Apps cron jobs and Azure OpenAI proxy. Managed identity binding (no static keys). | `centralindia`. RBAC, not access policies. | ~$0.03/secret/mo; <$1/mo |
| **Azure Active Directory (Entra ID)** | Tenant for Azure resource access. Founder + 1 staff identity. App registrations for managed identity. | global. | $0 within free tier |
| **Container Registry (ACR) — *optional*** | Image registry for Container Apps Jobs if we move off the public Docker Hub mirror. Currently OFF. | `centralindia`. | $0 currently |
| **Cost Management + Billing alerts** | Daily $/$ alarm at $10; monthly cap at $200. | global. | $0 |

### 4.2 Not used (and why)

| Service | Why we skip it |
|---|---|
| **App Service / Azure Functions / VMs / VMSS** | DO is the canonical backend home; doubling on Azure for the same workload would compete with DO and dilute the credit pool. |
| **Azure Blob Storage / Files / Queues / Table Storage** | **S3 is the sole object store** per §9 of the plan. Mixing storage backends is a footgun for SDK churn and lifecycle drift. |
| **Cosmos DB / Azure SQL / PostgreSQL Flexible Server** | Mongo Atlas + Upstash are the data tier. |
| **Azure CDN / Front Door** | Cloudflare is the CDN + WAF. |
| **Azure DevOps Pipelines** | GitHub Actions is the CI/CD path. |
| **AKS** | No Kubernetes need. |
| **Service Bus / Event Hubs / Event Grid** | AWS SQS handles async; Azure Monitor handles event routing for our scale. |
| **Azure AI Speech / Translator / Document Intelligence / AI Vision / Content Safety / AI Language / AI Search / Anomaly Detector / Personalizer** | Tracked under Task #338 as **future, optional, additional** providers in the failover chain. Not part of the steady-state hosting plan until that task lands. |
| **Azure Sentinel / Defender** | CF WAF + Axiom + App Insights cover security observability at our scale. |

---

## 5. GCP / Vertex — inference only, no hosting

### 5.1 Used

| Service | Use in Syrabit | Region | Cost shape |
|---|---|---|---|
| **Vertex AI — Gemini 2.5 Flash** | The headliner. Primary for `content`, `vision`, `safety`. Fallback for `english_rag_chat`, `assamese_rag_chat`, `translate`, `vector_search`. Six pools served from one API. Called from DO backend via service-account JSON. | `us-central1`. | ~$120/mo within $2k credit |
| **Vertex AI — Embedding API (`text-embedding-004`) — *standby*** | Standby fallback embed if both Workers AI bge-m3 and Bedrock Cohere embed are throttled. Pool weight = 1 (last-resort). | `us-central1`. | $0 typical (rarely hit) |
| **Cloud Vision API** | Generic OCR + image labelling fallback in the OCR chain. Retained from the earlier GCP build; cheaper than spinning up Textract just for this. | global. | $0–2/mo within credit |
| **Cloud Speech-to-Text (Chirp)** | STT fallback after Workers AI Whisper and Deepgram. Indic-strong. | `asia-south1`. | $0–2/mo within credit |
| **Cloud Text-to-Speech (Neural2/Studio)** | TTS fallback after ElevenLabs and Sarvam for English Read-Aloud. | global. | $0–2/mo within credit |
| **Discovery Engine (Vertex AI Search)** | Optional second-source library retriever for RAG, parallel to Pinecone. Used only when Pinecone returns 5xx or zero hits. | global. | $0 within credit |
| **Web Risk API** | URL safety check for any user-pasted external link before previewing. | global. | $0 within credit (10k/mo free) |
| **Service Accounts + IAM** | Minimal: one SA per AI API the backend calls; no human IAM users. | global. | $0 |
| **Cloud Billing alerts** | $5/day alarm → Telegram. | global. | $0 |

### 5.2 Not used (and why)

| Service | Why we skip it |
|---|---|
| **Cloud Run** | DO is the canonical backend origin. Adding a second-cloud origin is operational debt that dilutes the inference-only credit pool. |
| **GKE / Compute Engine / App Engine** | Same — no GCP compute. |
| **Cloud Build / Artifact Registry / Cloud Deploy** | GitHub Actions builds and deploys directly to DO + AWS + Azure. |
| **Cloud Tasks / Cloud Scheduler / Workflows** | AWS SQS + Lambda cover async; Azure Container Apps cron covers schedules. Both are retired by Task #335. |
| **Cloud Storage (GCS) / Filestore** | **S3 is the sole object store.** |
| **Cloud SQL / Spanner / Firestore / Bigtable / AlloyDB** | Mongo Atlas + Upstash + Pinecone cover all data needs. |
| **Cloud CDN / Cloud Load Balancing / Cloud Armor** | Cloudflare owns the perimeter. |
| **Cloud Trace / Cloud Logging / Cloud Monitoring** | App Insights + Axiom cover all observability. Cloud Trace is explicitly retired in Task #333. |
| **Pub/Sub** | AWS SQS is the chosen async transport. |
| **Vertex AI — Anthropic / Llama-on-Vertex / Mistral-on-Vertex** | Vertex stays Gemini-only for the LLM layer; Azure OpenAI handles the GPT slot. Adding more LLM SKUs on Vertex would dilute the credit pool already booked for Gemini. |
| **Vertex AI Model Garden / Custom Model deploys / Endpoint hosting** | We use API endpoints only, never deploy or fine-tune on Vertex. |
| **Apigee / API Gateway** | CF Worker is the gateway. |
| **Document AI** | Textract (AWS) and Document Intelligence (Azure) are the structured-document paths in Tasks #337/#338 — Document AI is dropped to keep one such service per cloud, not two. |

---

## 6. Cross-cloud feature → service map (the reverse lookup)

When you're holding a *feature* and asking "which cloud serves this?", read this table.

| Syrabit feature | Primary | Fallback 1 | Fallback 2 | Last resort |
|---|---|---|---|---|
| **Static SPA hosting** | CF Pages | — | — | — |
| **API gateway / WAF / TLS** | CF Worker + WAF + Turnstile | — | — | — |
| **Backend API runtime** | DO App Platform `syrabit-backend` | — (no second origin in plan) | — | — |
| **Rust core service** | DO App Platform `rust-core` | — | — | — |
| **Background workers (light)** | DO App Platform worker | — | — | — |
| **Background workers (heavy)** | AWS Lambda (via SQS) | — | — | — |
| **Cron / scheduled jobs** | Azure Container Apps Jobs | — | — | — |
| **Primary blob store** | AWS S3 | CF R2 (cold archive) | — | — |
| **Edge cache for blobs** | CF R2 + Cache Reserve | — | — | — |
| **Transactional email** | CF Email Routing | Resend | AWS SES | log-only |
| **Async queue** | AWS SQS | — | — | — |
| **Primary DB** | Mongo Atlas (M0 → M10 with $500 credit) | — | — | — |
| **Cache + sessions** | Upstash Redis REST | CF KV (edge) | — | — |
| **Vector index** | Pinecone | CF Vectorize | Vertex Discovery Engine | — |
| **Distributed tracing / APM** | Azure App Insights | Axiom (parallel) | — | — |
| **Logs (long-term)** | Axiom | App Insights (subset) | CloudWatch (AWS-native only) | — |
| **Alerts → Slack/Telegram** | Azure Logic Apps | Sentry direct | — | — |
| **Embed (Indic + EN)** | CF Workers AI bge-m3 | AWS Bedrock Cohere `embed-multilingual-v3` | Vertex `text-embedding-004` | — |
| **Rerank** | AWS Bedrock Cohere `rerank-v3-5` | (none — graceful degrade) | — | — |
| **Chat — `english_rag_chat` / `content`** | Azure OpenAI GPT-4.1-mini | Vertex Gemini 2.5 Flash | Groq Llama (free tier) | CF Workers AI gpt-oss-20b |
| **Chat — `assamese_rag_chat`** | Vertex Gemini 2.5 Flash | Sarvam-M (Indic-tuned) | Azure OpenAI GPT-4.1-mini | CF Workers AI gpt-oss-20b |
| **Vision (image understanding)** | Vertex Gemini 2.5 Flash (multimodal) | Google Cloud Vision (legacy) | — | — |
| **STT** | Deepgram | Google Cloud Speech (Chirp) | CF Workers AI Whisper | AWS Transcribe (post-#337) |
| **TTS** | ElevenLabs | Sarvam | Google Cloud TTS | AWS Polly (post-#337) |
| **Translate (Indic↔EN)** | Sarvam | CF Workers AI IndicTrans2 | Vertex Gemini | Azure Translator (post-#338) |
| **Safety / moderation** | Vertex Gemini safety | (admin review) | — | — |
| **URL safety** | GCP Web Risk | — | — | — |
| **CI/CD** | GitHub Actions | — | — | — |
| **Source control** | GitHub | — | — | — |
| **Secrets at rest** | DO App Platform env (backend) + AWS Secrets Manager (Lambda) + Azure Key Vault (cron) + GCP SA JSON (Vertex) | — | — | — |

---

## 7. Cost & credit allocation by cloud

| Cloud | Credit | Typical monthly burn | Coverage period | What burns the credit fastest |
|---|---:|---:|---|---|
| Cloudflare | $5,000 (exp 2026-09) | ~$0–200/mo (mostly Workers AI calls + R2 above free) | ~25 mo | Workers AI growth |
| Digital Ocean | DO Hatch (apply) | ~$25/mo cash | n/a until Hatch lands | App Platform instance scale-up |
| AWS | $1,000 Activate | ~$50/mo | ~20 mo | Bedrock Cohere calls |
| Azure | $2,500 (exp 2027) | ~$150/mo | ~16 mo | Azure OpenAI GPT-4.1-mini tokens |
| GCP / Vertex | $2,000 | ~$120/mo | ~16 mo | Gemini 2.5 Flash 1M-context calls |
| Mongo Atlas | $500 | $0 (M0 free) → $30/mo at M10 | ~20 mo after upgrade | DB scale-up |
| **Combined hosting + inference** | **~$11,000** | **~$520/mo** | **~20 mo** | |

---

## 8. Guardrails (one-line reminders)

1. **Frontend → Cloudflare. Backend → Digital Ocean. Everything else → AWS or Azure. Vertex is inference-only.**
2. **One canonical backend origin (DO).** No Cloud Run, no Fargate, no App Service, no Functions running the API.
3. **S3 is the sole object store.** No Azure Blob, no GCS, no DO Spaces in production.
4. **Bedrock is Cohere-only.** No Anthropic / Llama / Mistral / Titan / Nova on Bedrock.
5. **Vertex is Gemini-only at the LLM layer.** No Anthropic / Llama / Mistral on Vertex either.
6. **Cron lives on Azure Container Apps Jobs.** Not EventBridge, not Cloud Scheduler.
7. **Async lives on AWS SQS + Lambda.** Not Pub/Sub, not Cloud Tasks, not CF Queues.
8. **APM lives on Azure App Insights.** Logs duplicated to Axiom for retention.
9. **Never edit `PROVIDER_CREDITS` / `POOL_WEIGHTS` on aspirational credits.** Only after grant approval email.
10. **Every public URL terminates at Cloudflare.** Origin clouds are never directly addressable.
