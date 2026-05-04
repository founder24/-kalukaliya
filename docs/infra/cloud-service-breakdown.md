# Syrabit — Per-Cloud Service & Feature Breakdown

**Companion to:** `docs/infra/cloud-allocation-plan.md` (the strategic plan).
**Last updated:** 2026-05-04
**Scope:** exhaustive list of every service used on each of the four clouds in
play — what it's used for, why it lives on that cloud, what it costs, and
what guardrail keeps it scoped. If a service is **not used**, that's listed
too, with the reason (avoiding silent re-introduction later).

> Four clouds are involved in Syrabit, but only **three are hosting clouds**:
> Cloudflare, AWS, Azure. **GCP / Vertex is inference-only** (Gemini API +
> retained legacy AI APIs) and is included here for completeness, not as a
> hosting target.
>
> ⚠️ **Digital Ocean has been removed** from the architecture. Its previous
> workloads have been redistributed: backend API → AWS App Runner; workers +
> Rust core + cron → Azure Container Apps. Do not re-introduce DO without
> revising the strategic plan first.

---

## Quick map

| Cloud | Pillar role | Funded by |
|---|---|---|
| **Cloudflare** | Frontend + edge + global perimeter | $5k Cloudflare for Startups |
| **AWS** | Backend canonical origin (App Runner) + blob + email + async + Cohere transport | $1k AWS Activate + free tier |
| **Azure** | Workers + Rust core + cron + observability + GPT-4.1-mini transport | $2.5k Microsoft for Startups |
| **GCP / Vertex** | AI inference API only — **never hosting** | $2k GCP for Startups |

---

## 1. Cloudflare — frontend + edge + perimeter

### 1.1 Used

| Service | Use in Syrabit | Why CF | Cost shape |
|---|---|---|---|
| **Pages** | Hosts the React + Vite SPA built from `artifacts/syrabit/dist/`. Connected to GitHub `main`; deploys on push. Preview deployments per PR. | Free unlimited bandwidth + global PoPs; build cache hot. | $0 (within free Pages plan) |
| **Workers (edge proxy)** | Single front door for `api.syrabit.ai`. Terminates TLS, applies WAF rules, runs Turnstile challenge for write paths, mTLS upstream to AWS App Runner origin, KV-backed sliding-window rate limit, D1 hot-row read-cache. | Only place we get TLS + WAF + edge compute under one bill. | $0 within free tier (10M req/mo); paid above |
| **Workers AI** | Edge inference: `bge-m3` for embeds (1024-dim, primary embed pool), `IndicTrans2` EN↔Indic translate, `gpt-oss-20b` last-resort chat fallback, `whisper-large-v3-turbo` STT fallback. | Saves an inter-cloud hop on ~30% of requests; included in Enterprise credit. | $0 within $5k credit |
| **R2** | Cold blob bucket for archived generated content, public-readable image assets too large for the SPA build, Cloudflare Cache Reserve for the API edge. | $0 egress to anywhere; no AWS-style egress trap. | $0.015/GB-mo storage; $0 egress |
| **KV** | Session tokens (5-min TTL), per-IP and per-user rate-limit counters, edge feature flags, A/B bucket assignments. | Sub-ms reads at the edge; eventual consistency is fine for these uses. | $0 within free tier |
| **D1** | Hot-row read-cache for chapter/MCQ/content reads with 60s TTL, edge-replicated. | SQLite at the edge; cheap reads; eventual consistency acceptable. | $0 within free tier |
| **Vectorize** | Secondary vector index for failover when Pinecone is throttled or down. | Co-located with Workers AI embeds; no extra hop. | $0 within $5k credit |
| **Cache (HTTP cache + Cache Reserve)** | Tier-1 cache for static + edge-cacheable API responses (chapter content, MCQ lists). | Standard CF feature; Cache Reserve adds R2-backed persistence. | included |
| **Zone (DNS + TLS)** | Authoritative DNS for `syrabit.ai` and subdomains. Universal SSL + ACM-style auto-renewal. Geo-restriction rules for admin paths. | Required for all the above. | $0 |
| **WAF + DDoS + Bot Management** | Block scrapers, SQLi/XSS rules, rate-limit abusive IPs, bot scoring on signup/login. | Best-in-class at the edge; included with the zone. | $0 within plan |
| **Turnstile** | Invisible CAPTCHA on signup, login, password reset, contact form. | Free, no Google reCAPTCHA tax. | $0 |
| **Zero Trust / Access** | mTLS cert issuance for the worker → AWS App Runner origin handshake; admin-panel SSO. | Closes the origin to the public internet. | $0 within free tier (50 users) |
| **Email Routing + Email Workers** | Catch-all email aliases for `*@syrabit.ai`, tier-1 transactional sender (signup confirm, password reset). Falls back to Resend then SES. | Free, simple, beats configuring SES from scratch for the first 10k/mo. | $0 |
| **Logpush** | Streams CF Worker + zone logs into Axiom for retained search. | Native CF → Axiom integration; no proxy needed. | $0 within plan |
| **Analytics Engine** | Custom edge metrics emitted from Workers (per-pool latency, AI Gateway hit/miss). | Cheap edge time-series, no extra infra. | $0 within free tier |
| **AI Gateway** | Caching + observability layer in front of Cohere / Workers AI / OpenAI / Gemini calls made from the worker (the few calls that originate at the edge). | Saves repeat-call cost on hot prompts; one-line config. | $0 within plan |

### 1.2 Not used (and why)

| Service | Why we skip it |
|---|---|
| **Cloudflare Stream** | Video isn't a current feature; can revisit if Read-Aloud → video lands. |
| **Cloudflare Images** | R2 + native `<img>` resize via Workers covers our needs. |
| **Cloudflare Queues** | AWS SQS is the chosen async transport (closer to Lambda consumers). |
| **Hyperdrive** | Mongo Atlas + Azure Cache for Redis already cover DB/cache; no Postgres origin to accelerate. |
| **Pages Functions** | Edge logic lives in the Worker, not in Pages Functions, to keep one deploy unit. |

---

## 2. AWS — backend canonical origin + blob + email + async + Cohere

### 2.1 Used

| Service | Use in Syrabit | Region / config | Cost shape |
|---|---|---|---|
| **App Runner — `syrabit-backend`** | **Canonical FastAPI backend origin.** Built from `artifacts/syrabit-backend/Dockerfile`. 1 vCPU / 2 GB RAM. Auto-scale 1 → 10 at 80% CPU. Health check `/api/health`. Custom domain `api.syrabit.ai` fronted by CF Worker (mTLS only). Env vars from Secrets Manager. | `us-west-2`. | ~$25–50/mo within $1k Activate credit |
| **App Runner — `syrabit-backend-staging`** | Separate App Runner service pinned to `staging` branch. Same image; staging Mongo + Azure Cache for Redis creds. Min 0 instances (scale-to-zero). | `us-west-2`. | $0–10/mo |
| **S3 — `s3://syrabit-prod-assets`** | User-uploaded PDF lecture notes, audio notes, generated content backups, embed cache (hash → vector blob), daily Mongo backup dumps. Versioning ON, lifecycle to Glacier Deep Archive at 90d for backups. Browser uploads via presigned URL — never proxy through App Runner. | `us-west-2`. Bucket policy denies non-mTLS, denies public read except on `/public/*` prefix. | ~$1–3/mo at MVP scale (5GB free year 1) |
| **S3 — `s3://syrabit-prod-public`** | Public-readable assets that need stable HTTPS URLs (cover images, sample audio). Served via CF Cache Reserve to dodge S3 egress. | `us-west-2`. CF in front. | <$1/mo |
| **SES** | Transactional email: signup confirm, password reset, study-streak digests, billing receipts, admin notifications. `noreply@syrabit.ai` verified. | `us-west-2`. Out of sandbox. | $0 within 62k/mo Lambda free tier; $0.10 per 1k otherwise |
| **Lambda** | Memory-heavy async jobs: PDF→MCQ extraction, batch audio synthesis, daily Mongo→S3 backup (triggered by Azure Container Apps Job), AI Gateway log replay. | `us-west-2`. ARM64. Each function in its own role. | $0 within 1M req + 400k GB-sec free tier |
| **SQS** | Queue layer between AWS App Runner backend and Lambda consumers. Standard queues for fan-out (SEO publish, audio synth fan-out), FIFO for ordered jobs (per-user PDF processing). DLQ on each. | `us-west-2`. Visibility timeout 6× expected processing time. `maxReceiveCount` = 5. | $0 within 1M req/mo free tier |
| **SNS** | CloudWatch alarm fan-out: AWS infra alerts → Slack webhook. Bridges CloudWatch → Slack without writing custom code. | `us-west-2`. | $0 within free tier |
| **Bedrock** | Cohere-only: `cohere.embed-multilingual-v3` (Indic/English embeds, 1024-dim, primary embed pool) and `cohere.rerank-v3-5` (rerank pool). Direct boto3 from App Runner backend (verified working). | `us-west-2`. IAM role with `bedrock:InvokeModel` on those two model ARNs only. | ~$50/mo within $1k Activate credit |
| **CloudWatch Logs** | App Runner + Lambda + Bedrock invocation logs. Backend logs also forwarded to Azure App Insights for cross-cloud APM. | `us-west-2`. 5GB/mo free. | $0 expected |
| **CloudWatch Alarms** | App Runner unhealthy targets, SQS queue depth > 100, DLQ depth > 0, Lambda error rate > 1%, Bedrock throttle count > 10/min. Fire to SNS → Slack and to Azure Logic Apps for Telegram fan-out. | `us-west-2`. | included |
| **Secrets Manager** | Runtime secrets for App Runner + Lambda (DB URIs, downstream API keys, Cohere/OpenAI/Vertex creds). Rotated quarterly. | `us-west-2`. | ~$0.40/mo per secret; ~$3/mo total |
| **IAM** | Per-feature roles: one for App Runner exec (Bedrock + S3 + SES + SQS + Secrets read), one per Lambda function, one for the GitHub Actions deploy role (OIDC trust, no static keys). | account `926046660612`, IAM user `SYRABIT` for break-glass only. | $0 |
| **ECR (Elastic Container Registry)** | Built images for App Runner deploys (when not using App Runner's GitHub source path). | `us-west-2`. | $0 within free tier (500MB/mo) |
| **AWS Systems Manager Parameter Store** | Non-secret config (feature flags, schedule cadences) for Lambda + App Runner. | `us-west-2`. | $0 within standard tier |
| **EventBridge — *limited*** | Bedrock/Lambda invocation event bus for X-Ray + tracing fan-out. Cron schedules live on Azure Container Apps Jobs, not EventBridge. | `us-west-2`. | $0 within free tier |
| **Route 53 — *optional, future*** | Only if we need health-checked DNS failover beyond what CF provides. Currently CF zone owns DNS. | not yet enabled. | $0 currently |

### 2.2 Not used (and why)

| Service | Why we skip it |
|---|---|
| **EC2 / Lightsail** | App Runner covers our backend compute needs; managed beats hand-rolled. |
| **ECS / Fargate / EKS** | App Runner is simpler for a single FastAPI service; ECS/Fargate would re-introduce cluster + task-definition overhead for no gain. EKS is overkill. |
| **RDS / Aurora / DynamoDB** | Mongo Atlas + Upstash are the chosen DB layers. Adding RDS would double-bill the data tier and dilute the $1k Activate credit. |
| **ElastiCache** | Upstash Redis covers cache. |
| **Bedrock — Claude / Llama / Mistral / Titan / Nova** | Explicitly out of scope. Azure OpenAI (GPT-4.1-mini) and Vertex Gemini cover those LLM roles cheaper. Bedrock is **Cohere-only**. |
| **CloudFront** | Cloudflare is the CDN. |
| **API Gateway** | CF Worker is the gateway. |
| **Step Functions** | Lambda + SQS chains cover our workflow needs. |
| **Polly / Transcribe / Textract / Rekognition / Comprehend / Translate / Personalize / Fraud Detector** | Tracked under Task #337 as **future, optional, additional** providers in the failover chain. **Not part of the steady-state hosting plan** until that task lands. |
| **Macie / GuardDuty / Inspector / WAF** | CF WAF is the perimeter; AWS WAF would be redundant. |

---

## 3. Azure — workers + Rust core + cron + observability + GPT transport

### 3.1 Used

| Service | Use in Syrabit | Region / config | Cost shape |
|---|---|---|---|
| **Container Apps — `syrabit-workers`** | Background workers, same image as the backend, entrypoint `python -m workers.queue_runner`. Polls Upstash Redis queue + SQS bridge. Min 1, max 3 replicas. Managed identity → Key Vault for secrets. | `centralindia`. | ~$15/mo within credit |
| **Container Apps — `rust-core`** | Rust "Neural Mesh Core" service. Internal-only (mTLS-restricted ingress), called from AWS App Runner over public mTLS endpoint (gRPC port 50051 + HTTP 3000). Lives on Azure because App Runner does not natively support gRPC. | `centralindia`. | ~$10/mo within credit |
| **Container Apps Jobs (cron)** | Every scheduled workload: daily Mongo→S3 backup trigger (which then invokes AWS Lambda), weekly Pinecone re-index, weekly cost-report email, hourly SEO auto-publish, IndexNow batches, dead-endpoint pruner, Trustpilot refresh, CF log pull, weekly digest. KEDA cron triggers; scale-to-zero between runs. | `centralindia`. | $0 within $2.5k credit |
| **Container Apps — `syrabit-backend-failover`** | Standby replica of the FastAPI backend image, scale-to-zero. CF Worker can fail over by env flag if AWS App Runner us-west-2 is degraded. | `centralindia`. | $0 idle, ~$25/mo if active |
| **Container Apps — `azure-openai-proxy` (optional)** | Thin proxy in front of Azure OpenAI for caching + retry. Deployed only if request volume justifies it (otherwise the AWS App Runner backend calls Azure OpenAI directly). | `centralindia`. Currently OFF. | $0 currently |
| **Azure OpenAI Service** | `gpt-4.1-mini` deployment named `syrabit-chat`. Primary for `english_rag_chat` and `content` pools. PTU = none, pay-as-you-go. | `eastus` (cheapest). Content filter at `medium`. | ~$150/mo within $2.5k credit |
| **Logic Apps** | Alerting workflows: AppInsights anomaly → Telegram bot + founder email; spend alarms (AWS > $5/day, Azure > $10/day, Vertex > $8/day) → Telegram; DLQ-depth-rising → Telegram; AWS CloudWatch alarm fan-in. | `centralindia`. | $0 within credit |
| **Application Insights (Azure Monitor)** | **Central APM/trace sink for all three hosting clouds**: AWS App Runner backend (Python OTel), AWS Lambda workers (otel-lambda layer), Azure Container Apps (native), Azure rust-core (Rust OTel). Dashboards: per-pool latency p50/p95/p99, per-provider error rate, daily $$ burn per provider. | `centralindia` workspace. Sampling at 25% for high-volume traces. | $0 within 5GB/mo ingest free + credit |
| **Log Analytics Workspace** | Backing store for App Insights + Container Apps logs + Logic Apps run history. | shares region with App Insights. | $0 within credit |
| **Azure Monitor Alerts** | Backend alerts that don't need Logic-Apps-style fan-out: simple metric > threshold → action group → Slack. | `centralindia`. | $0 within credit |
| **Key Vault** | Secrets for Container Apps (workers, rust-core, cron jobs, optional OpenAI proxy). Managed identity binding (no static keys). | `centralindia`. RBAC, not access policies. | ~$0.03/secret/mo; <$1/mo |
| **Azure Container Registry (ACR)** | Image registry for Container Apps + Container Apps Jobs. GitHub Actions push on every main merge. | `centralindia`. Basic SKU. | ~$5/mo within credit |
| **Azure Active Directory (Entra ID)** | Tenant for Azure resource access. Founder + 1 staff identity. App registrations for managed identity. | global. | $0 within free tier |
| **Cost Management + Billing alerts** | Daily $/$ alarm at $10; monthly cap at $200. | global. | $0 |

### 3.2 Not used (and why)

| Service | Why we skip it |
|---|---|
| **App Service / Azure Functions / VMs / VMSS** | Container Apps cover all our long-running compute needs on Azure. App Service would be redundant; Functions are unnecessary because cron is on Container Apps Jobs. |
| **Azure Blob Storage / Files / Queues / Table Storage** | **S3 is the sole object store** per §9 of the plan. Mixing storage backends is a footgun for SDK churn and lifecycle drift. |
| **Cosmos DB / Azure SQL / PostgreSQL Flexible Server** | Mongo Atlas + Upstash are the data tier. |
| **Azure CDN / Front Door** | Cloudflare is the CDN + WAF. |
| **Azure DevOps Pipelines** | GitHub Actions is the CI/CD path. |
| **AKS** | Container Apps cover our orchestration needs without Kubernetes overhead. |
| **Service Bus / Event Hubs / Event Grid** | AWS SQS handles async; Azure Monitor handles event routing for our scale. |
| **Azure AI Speech / Translator / Document Intelligence / AI Vision / Content Safety / AI Language / AI Search / Anomaly Detector / Personalizer** | Tracked under Task #338 as **future, optional, additional** providers in the failover chain. Not part of the steady-state hosting plan until that task lands. |
| **Azure Sentinel / Defender** | CF WAF + Axiom + App Insights cover security observability at our scale. |

---

## 4. GCP / Vertex — inference only, no hosting

The codebase touches **four distinct Google API surfaces**, all called from the
AWS App Runner backend (no GCP compute). The prod default for *generative*
calls is **Cloudflare AI Gateway BYOK → google-ai-studio (Gemini)**; direct
Vertex AI Platform calls are kept as rollback. Vector + Discovery + Vision
calls go direct (no AI Gateway).

**Auth priority recognized by the dispatcher** (highest first):
1. `VERTEX_SERVICE_ACCOUNT` — Vertex AI Platform service-account JSON (used by `vertex_chat.py`, `providers/vertex_embed.py`, `retrievers/vertex.py`, `discovery_engine_client.py`, Cloud Vision)
2. `GEMINI_API_KEY` — raw AI Studio API key (legacy fallback, kept for rollback only)
3. `CF_AI_GATEWAY_*` — Cloudflare AI Gateway BYOK to `google-ai-studio` (**prod default for `vertex_services.py`** — embeddings, translation, MCQ/flashcards, content enhancement, SEO meta, gap analysis, long-doc reader)

### 4.1 Used

#### Surface A — Vertex AI Platform API (`*-aiplatform.googleapis.com`)

| Service | Caller in repo | Use in Syrabit | Region | Cost shape |
|---|---|---|---|---|
| **Vertex AI — Vector Search (Matching Engine)** | `retrievers/vertex.py` | `findNeighbors`, `upsertDatapoints`, `removeDatapoints`, `readIndexDatapoints` against the deployed Index / IndexEndpoint. Active retriever surface (not just a Pinecone fallback). | `us-central1` | ~$15–30/mo within $2k credit at MVP scale |
| **Vertex AI — Text Embeddings (`text-embedding-004`, 768-dim)** | `providers/vertex_embed.py` | Long-form embed fallback via `…:predict` when Workers AI bge-m3 and Bedrock Cohere are throttled or out of band for context length. | `us-central1` | $0–5/mo (rarely hit) |
| **Vertex AI — Gemini streaming chat (direct SA path)** | `vertex_chat.py` | The **only** generative path that still hits Vertex AI directly with a service account (rather than going through CF AI Gateway). Kept as a rollback when AI Gateway is degraded. | `us-central1` | $0–10/mo (rollback only) |

#### Surface B — Discovery Engine API (`discoveryengine.googleapis.com`)

| Service | Caller in repo | Use in Syrabit | Region | Cost shape |
|---|---|---|---|---|
| **Discovery Engine (Vertex AI Search)** | `discovery_engine_client.py` | `{servingConfig}:search` for retrieval. Second-source library retriever for RAG, parallel/fallback to Pinecone + Vector Search. | global | $0–10/mo within credit |

#### Surface C — Generative Language / Gemini API via Cloudflare AI Gateway BYOK *(prod default)*

| Service | Caller in repo | Use in Syrabit | Region | Cost shape |
|---|---|---|---|---|
| **Gemini 2.5 Flash via CF AI Gateway → google-ai-studio** | `vertex_services.py` (umbrella) | All non-streaming generative work: embeddings (short-form), translation, MCQ + flashcard generation, content enhancement, SEO meta-tag generation, gap analysis, long-doc (1M-context) reader. Routed through the CF AI Gateway for caching, observability, and rate-limit pooling. The direct Vertex SA path and raw `GEMINI_API_KEY` path are kept only as rollback. | edge (CF) → `us-central1` (Gemini) | ~$80–120/mo within $2k credit (CF Gateway adds $0; cache hits reduce spend further) |

#### Surface D — Google Cloud Vision API (separate service, same SA setup)

| Service | Caller in repo | Use in Syrabit | Region | Cost shape |
|---|---|---|---|---|
| **Cloud Vision API** | `vertex_services.py` (OCR path) | OCR backend after the 2026-05-03 "vertex-only Gemini" migration: image text extraction for PDF lecture notes, MCQ-from-photo, diagram labelling. Lives in the same SA / auth surface even though it's a separate API. | global | $0–2/mo within credit (1k/mo free) |

#### Other GCP services still in use

| Service | Caller in repo | Use in Syrabit | Region | Cost shape |
|---|---|---|---|---|
| **Cloud Speech-to-Text (Chirp)** | dispatcher fallback | STT fallback after Workers AI Whisper and Deepgram. Indic-strong. | `asia-south1` | $0–2/mo within credit |
| **Cloud Text-to-Speech (Neural2/Studio)** | dispatcher fallback | TTS fallback after ElevenLabs and Sarvam for English Read-Aloud. 4M chars/mo free. | global | $0 within free tier |
| **Web Risk API** | dispatcher | URL safety check for user-pasted external links before previewing. 10k/mo free. | global | $0 within free tier |
| **Service Accounts + IAM** | infra | One SA per surface the backend calls (Vertex AI Platform, Discovery, Vision). No human IAM users. Same SA also signs `vertex_services.py` rollback path. | global | $0 |
| **Cloud Billing alerts** | infra | $5/day alarm → Telegram. | global | $0 |

### 4.2 Not used (and why)

| Service | Why we skip it |
|---|---|
| **Cloud Run** | AWS App Runner is the canonical backend origin. Adding a second-cloud origin is operational debt that dilutes the inference-only credit pool. |
| **GKE / Compute Engine / App Engine** | Same — no GCP compute. |
| **Cloud Build / Artifact Registry / Cloud Deploy** | GitHub Actions builds and deploys directly to AWS + Azure. |
| **Cloud Tasks / Cloud Scheduler / Workflows** | AWS SQS + Lambda cover async; Azure Container Apps Jobs cover schedules. |
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

## 5. Cross-cloud feature → service map (the reverse lookup)

When you're holding a *feature* and asking "which cloud serves this?", read this table.

| Syrabit feature | Primary | Fallback 1 | Fallback 2 | Last resort |
|---|---|---|---|---|
| **Static SPA hosting** | CF Pages | — | — | — |
| **API gateway / WAF / TLS** | CF Worker + WAF + Turnstile | — | — | — |
| **Backend API runtime** | AWS App Runner `syrabit-backend` (us-west-2) | Azure Container Apps `syrabit-backend-failover` (centralindia, scale-to-zero standby) | — | — |
| **Rust core service** | Azure Container Apps `rust-core` | — | — | — |
| **Background workers (light)** | Azure Container Apps `syrabit-workers` | — | — | — |
| **Background workers (heavy)** | AWS Lambda (via SQS) | — | — | — |
| **Cron / scheduled jobs** | Azure Container Apps Jobs | — | — | — |
| **Primary blob store** | AWS S3 | CF R2 (cold archive) | — | — |
| **Edge cache for blobs** | CF R2 + Cache Reserve | — | — | — |
| **Transactional email** | CF Email Routing | Resend | AWS SES | log-only |
| **Async queue** | AWS SQS | — | — | — |
| **Primary DB** | Mongo Atlas (M0 → M10 with $500 credit) | — | — | — |
| **Cache + sessions** | Azure Cache for Redis Basic C0 (within Azure credit) | Momento Cache (free 5GB/5M req) | CF KV / Durable Objects (edge, within CF credit) | Mongo `find_and_modify` (graceful degrade for atomic ops) |
| **Vector index** | Pinecone | Vertex AI Vector Search (Matching Engine, via `retrievers/vertex.py`) | CF Vectorize | Vertex Discovery Engine |
| **Distributed tracing / APM** | Azure App Insights | Axiom (parallel) | — | — |
| **Logs (long-term)** | Axiom | App Insights (subset) | CloudWatch (AWS-native only) | — |
| **Alerts → Slack/Telegram** | Azure Logic Apps | Sentry direct | — | — |
| **Embed (Indic + EN)** | CF Workers AI bge-m3 | AWS Bedrock Cohere `embed-multilingual-v3` (1024-dim) | Vertex `text-embedding-004` (768-dim, via `providers/vertex_embed.py`) | — |
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
| **Secrets at rest** | AWS Secrets Manager (App Runner + Lambda) + Azure Key Vault (Container Apps + cron) + GCP SA JSON (Vertex) | — | — | — |

---

## 6. Cost & credit allocation by cloud

| Cloud | Credit | Typical monthly burn | Coverage period | What burns the credit fastest |
|---|---:|---:|---|---|
| Cloudflare | $5,000 (exp 2026-09) | ~$0–200/mo (mostly Workers AI calls + R2 above free) | ~25 mo | Workers AI growth |
| AWS | $1,000 Activate | ~$80/mo (App Runner + Bedrock Cohere; S3/SES/Lambda free tier) | ~12 mo | App Runner instance count + Bedrock Cohere calls |
| Azure | $2,500 (exp 2027) | ~$200/mo (Azure OpenAI + Container Apps workers + cron + AppInsights + ACR) | ~12 mo | Azure OpenAI GPT-4.1-mini tokens |
| GCP / Vertex | $2,000 | ~$120/mo | ~16 mo | Gemini 2.5 Flash 1M-context calls |
| Mongo Atlas | $500 | $0 (M0 free) → $30/mo at M10 | ~20 mo after upgrade | DB scale-up |
| **Combined hosting + inference** | **~$11,000** | **~$600/mo** | **~18 mo** | |

---

## 7. Guardrails (one-line reminders)

1. **Frontend → Cloudflare. Backend API → AWS App Runner. Workers + Rust core + cron + APM → Azure. Vertex is inference-only.**
2. **One canonical backend origin (AWS App Runner).** Azure Container Apps `syrabit-backend-failover` is scale-to-zero standby, never active concurrently.
3. **No Digital Ocean, no Railway, no Cloud Run, no App Service, no Fly.io.** All retired or rejected.
4. **S3 is the sole object store.** No Azure Blob, no GCS, no DO Spaces in production.
5. **Bedrock is Cohere-only.** No Anthropic / Llama / Mistral / Titan / Nova on Bedrock.
6. **Vertex is Gemini-only at the LLM layer.** No Anthropic / Llama / Mistral on Vertex either.
7. **Cron lives on Azure Container Apps Jobs.** Not EventBridge, not Cloud Scheduler.
8. **Async lives on AWS SQS + Lambda.** Not Pub/Sub, not Cloud Tasks, not CF Queues.
9. **APM lives on Azure App Insights** (central for all three hosting clouds). Logs duplicated to Axiom for retention.
10. **Never edit `PROVIDER_CREDITS` / `POOL_WEIGHTS` on aspirational credits.** Only after grant approval email.
11. **Every public URL terminates at Cloudflare.** AWS and Azure origins are never directly addressable from the internet (mTLS-only ingress).
