> **v3 SUPERSEDES (2026-05-04):** the canonical infra spec is
> [`infra/per-cloud-feature-delegation.md`](../../infra/per-cloud-feature-delegation.md)
> + [`infra/provider-priority-map.md`](../../infra/provider-priority-map.md)
> + [`infra/credit-burn-runbook.md`](../../infra/credit-burn-runbook.md).
> This older same-named doc is retained for historical context.
> Provider removals (OpenAI, Anthropic, Bedrock, Stripe, Quge5, Resend,
> Grok, Railway, DigitalOcean) are tracked in Task #347. If anything
> below disagrees with v3, the v3 docs win.
>
> ---
>
> **Authority sync (2026-05-04):** `docs/infra/provider-priority-map.md`
> is the canonical PROVIDER_PRIORITY map. This doc is a 4-cloud
> projection of that map — same constraints, organized by cloud
> instead of by feature. If anything here disagrees with
> provider-priority-map.md, that doc wins.

# Per-Cloud Feature Delegation (full configuration)

**Date:** 2026-05-04
**Purpose:** answer the question *"what does each of the 4 strategic
clouds actually serve?"* with full configuration detail (region,
SKU, env vars, IAM, credit pool, $/mo at 10k DAU, fallback role).

```
┌──────────────────────────────────────────────────────────────────┐
│  GCP / Vertex   ~$150/mo   inference + vision + safety + search  │
│  Azure          ~$87/mo    content + cache + cron + APM          │
│  Cloudflare     ~$31/mo    edge + translate + universal fallback │
│  AWS            ~$62/mo    embed/rerank + blob + async + host    │
│  Auxiliary      $0         own credit pools (Sarvam, Pinecone…)  │
│                                                                  │
│  Total $339/mo @ 10k DAU,  $0 cash,  $917/mo headroom           │
└──────────────────────────────────────────────────────────────────┘
```

---

## §1 — GCP / Vertex (`~$150/mo`, $2k Google Cloud for Startups, 90% draw)

### 1.1 Features served (Tier‑1 = primary, Tier‑2/3 = fallback)

| # | Feature | Tier | Component | Region | Model / SKU |
|---:|---|:-:|---|---|---|
| 1 | `english_rag_chat` | T1 | Vertex AI Generative — Gemini | `us-central1` (via CF AI Gateway BYOK) | `gemini-2.5-flash` |
| 2 | `assamese_rag_chat` | T1 | Vertex AI Generative — Gemini, Indic-tuned prompt | `us-central1` | `gemini-2.5-flash` |
| 3 | `assamese_content` | T1 | Vertex AI Generative — Gemini translate+adapt | `us-central1` | `gemini-2.5-flash` |
| 4 | `content` (MCQ, notes, flashcards) | T2 | Vertex AI Generative — Gemini | `us-central1` | `gemini-2.5-flash` |
| 5 | `vision` (image understanding) | T1 | Vertex AI Generative multimodal | `asia-south1` | `gemini-2.5-flash` (multimodal) |
| 6 | `vision` (OCR / DOCUMENT_TEXT_DETECTION) | T1 | Cloud Vision API | `asia-south1` | `DOCUMENT_TEXT_DETECTION` |
| 7 | `safety` (Tier‑1 moderation) | T1 | Gemini built-in safety + RAI categories | `us-central1` | `gemini-2.5-flash` |
| 8 | `search_rag` (grounded answers) | T1 | Vertex Discovery Engine | `global` | `discoveryengine.googleapis.com` over CMS+web corpus |
| 9 | `vector_retrieve` | T2 | Vertex AI Vector Search (Matching Engine) | `us-central1` | deployed Index / IndexEndpoint |
| 10 | `tts` | T2 | Cloud Text-to-Speech | `asia-south1` | Neural2 voices: `en-US-Neural2-F`, `as-IN-Wavenet-A`, `hi-IN-Neural2-A`, `bn-IN-Wavenet-A` |
| 11 | `stt` | T4 | Cloud Speech-to-Text | `asia-south1` | Chirp model |
| 12 | `voice` (TTS leg) | T4 | Cloud TTS Neural2 (combo with Cloud STT) | `asia-south1` | low-latency streaming |
| 13 | `translate` | T2 | Cloud Translation v3 + Gemini polish | `us-central1` | `translate.googleapis.com` |

> **Embed: NOT served by GCP.** `providers/vertex_embed.py` is rollback-only.
> Per provider-priority-map.md, embed is Cohere → Voyage → CF.

### 1.2 Auth / IAM

| Surface | Auth path (priority) |
|---|---|
| Direct Vertex AI (vector, vision, OCR, rollback chat) | `VERTEX_SERVICE_ACCOUNT` JSON (Workload Identity for the DO backend; SSM for AWS Lambda workers) |
| Gemini generative (prod default) | **Cloudflare AI Gateway BYOK → google-ai-studio**, env `CF_AI_GATEWAY_*` + AI Studio API key |
| Discovery Engine | `VERTEX_SERVICE_ACCOUNT` with `roles/discoveryengine.viewer` + `roles/discoveryengine.editor` |
| Cloud Vision / Cloud TTS / Cloud STT | `VERTEX_SERVICE_ACCOUNT` (same SA, scoped roles) |

### 1.3 Cost shape at 10k DAU

| Surface | $/mo | Note |
|---|---:|---|
| Gemini via CF AI Gateway | ~$80–120 | Caching reduces by 30–40% |
| Vertex Vector Search | ~$15–30 | At MVP scale (~50k vectors) |
| Discovery Engine | ~$5–10 | Per-query pricing |
| Cloud TTS Neural2 | ~$5–10 | Tier-2 only (ElevenLabs primary) |
| Cloud Vision OCR | ~$3–5 | 300 PDFs × 60 pages avg |
| Cloud STT Chirp | ~$0–2 | Tier-4 only (Deepgram + AssemblyAI primary) |
| **Subtotal** | **~$150** | **90% of $2k Google Cloud for Startups, mitigated by AI Gateway cache** |

---

## §2 — Azure (`~$87/mo`, $2.5k Microsoft Founders Hub, 38% draw)

### 2.1 Features served

| # | Feature | Tier | Component | Region | Model / SKU |
|---:|---|:-:|---|---|---|
| 1 | `content` (MCQ, notes, flashcards) | T1 | Azure OpenAI deployments `syrabit-quiz`, `syrabit-cards`, `syrabit-notes` | `eastus2` | `gpt-4.1-mini` |
| 2 | `english_rag_chat` | T2 | Azure OpenAI deployment `syrabit-chat` | `eastus2` | `gpt-4.1-mini` |
| 3 | `assamese_rag_chat` | T3 | Azure OpenAI translate-then-answer | `eastus2` | `gpt-4.1-mini` + IndicTrans2 (CF) preprocessor |
| 4 | `translate` | T4 | Azure OpenAI translate prompt | `eastus2` | `gpt-4.1-mini` |
| 5 | `cache` (Tier-1 primary) | T1 | **Azure Cache for Redis** Basic C0 | `centralindia` | 250 MB, full Redis protocol, $16/mo |
| 6 | `cron` (canonical, all 18 KEDA-triggered jobs) | T1 | **Azure Container Apps Jobs** | `centralindia` | scale-to-zero, KEDA cron triggers |
| 7 | `apm` (canonical central sink) | T1 | **Azure Application Insights** | `eastus2` | distributed tracing, 5 GB/mo free tier ingested via dual OTLP exporter |
| 8 | Alert routing | T1 | Azure Logic Apps (consumption tier) | `eastus2` | first 4k actions/mo free; routes alerts → Slack/Telegram |
| 9 | Standby failover backend | T3 | Azure Container Apps secondary region | `eastus2` | cold standby for DO backend (Cloudflare Load Balancer steers) |
| 10 | Secret store | — | Azure Key Vault | `eastus2` | central secret store for App Insights keys + AOAI deployments |

### 2.2 Container Apps Jobs — the 18 cron jobs (canonical home)

| Job | Schedule | Purpose |
|---|---|---|
| `seo-cluster-rebuild` | `0 2 * * *` | Rebuild SEO topic clusters from Mongo |
| `seo-internal-linker` | `0 3 * * *` | Cohere rerank → anchor text gen → CMS |
| `seo-auto-publish` | `0 4 * * *` | Vertex Gemini + Cohere via Bedrock → S3 → CF Pages invalidate |
| `quiz-pool-refresh` | `0 1 * * 0` | Regenerate weekly quiz pools |
| `flashcard-spaced-repetition` | `0 5 * * *` | SM-2 algorithm passes |
| `streak-rollover` | `0 0 * * *` (UTC midnight) | Reset/extend user streaks |
| `leaderboard-snapshot` | `*/15 * * * *` | Mongo → Redis ZSET hot leaderboard |
| `push-token-prune` | `0 6 * * *` | Drop stale FCM/APNS tokens |
| `audit-log-rotation` | `0 7 * * *` | Mongo → Axiom long-term archive |
| `pdf-ingest-replay` | `*/5 * * * *` | SQS DLQ retry for failed embeds |
| `redis-warmup` | `0 8 * * *` | Pre-load hot prompts → Azure Redis |
| `pinecone-prune-orphans` | `0 9 * * 0` | Drop vectors for deleted CMS docs |
| `vertex-discovery-reindex` | `0 10 * * 0` | Refresh Discovery Engine corpus |
| `mongo-analytics-rollup` | `0 11 * * *` | Daily revenue + DAU/MAU aggregates |
| `health-prober` | `*/2 * * * *` | Hits all `/api/readyz` endpoints; promotes/demotes tiers |
| `credit-burn-tracker` | `0 12 * * *` | Per-cloud daily $ burn → Slack #ops |
| `ssl-cert-rotator` | `0 13 1 * *` | Monthly Let's Encrypt rotation for custom domains |
| `assamese-corpus-refresh` | `0 14 * * 0` | Re-embed updated Assamese chapters |

### 2.3 Cost shape at 10k DAU

| Surface | $/mo | Note |
|---|---:|---|
| Azure OpenAI (`gpt-4.1-mini` content + chat fallback) | ~$50 | Bulk of spend; primary content workhorse |
| Azure Cache for Redis Basic C0 | $16 | Cache primary, only paid Azure infra item |
| App Insights | $5 | Within 5 GB free tier; tipped over by traces |
| Container Apps Jobs (18 jobs) | $0 | Within 180k vCPU-s + 360k GB-s free tier |
| Logic Apps (alert routing) | $0 | Within 4k actions/mo free |
| Key Vault | $0 | < 10k operations/mo free |
| Standby Container App backend | $16 | Cold-min provisioned 0.25 vCPU |
| **Subtotal** | **~$87** | **38% of $2.5k Founders Hub credit, healthy headroom** |

---

## §3 — Cloudflare (`~$31/mo`, $5k CF for Startups, 7% draw)

### 3.1 Features served

| # | Feature | Tier | Component | Surface | Model / SKU |
|---:|---|:-:|---|---|---|
| 1 | Frontend hosting | T1 | **Cloudflare Pages** | edge | React app build, custom domain, automatic CDN |
| 2 | Edge proxy in front of DO origin | T1 | **Cloudflare** (Task #334) | edge | TLS termination, WAF, rate limit, bot management |
| 3 | `translate` | T1 | Workers AI | edge | `@cf/google/indictrans2-en-indic-1b` |
| 4 | `safety` (Tier-2 fallback) | T2 | Workers AI | edge | `@cf/meta/llama-guard-3-8b` |
| 5 | `english_rag_chat` (Tier-3 fallback) | T3 | Workers AI | edge | `@cf/openai/gpt-oss-20b` → `@cf/meta/llama-3.3-70b-instruct-fp8-fast` |
| 6 | `assamese_rag_chat` (Tier-3 fallback) | T3 | Workers AI | edge | IndicTrans2 → gpt-oss-20b answer leg |
| 7 | `content` (Tier-3 fallback) | T3 | Workers AI | edge | `@cf/openai/gpt-oss-20b` |
| 8 | `assamese_content` (Tier-3 fallback) | T3 | Workers AI | edge | IndicTrans2 |
| 9 | `embed` (Tier-3 edge fallback) | T3 | Workers AI | edge | `@cf/baai/bge-m3` (1024-dim) |
| 10 | `rerank` (Tier-3 edge fallback) | T3 | Workers AI | edge | `@cf/baai/bge-reranker-base` |
| 11 | `tts` (Tier-3 edge fallback) | T3 | Workers AI | edge | `@cf/myshell-ai/melotts` |
| 12 | `stt` (Tier-3 edge fallback) | T3 | Workers AI | edge | `@cf/openai/whisper-large-v3-turbo` |
| 13 | `voice` (Tier-3 full-edge fallback) | T3 | Workers AI | edge | Whisper STT + MeloTTS combo |
| 14 | `vision` (Tier-2 fallback) | T2 | Workers AI | edge | `@cf/llava-hf/llava-1.5-7b-hf` |
| 15 | `search_rag` (Tier-3 fallback) | T3 | Workers AI | edge | `@cf/openai/gpt-oss-20b` with retrieved context |
| 16 | `live_search` (Tier-3 fallback) | T3 | Workers Browser Rendering + edge fetch | edge | headless Chromium + fetch-from-edge |
| 17 | `vector_retrieve` (Tier-3 fallback) | T3 | **CF Vectorize** | edge | edge-replicated vector index |
| 18 | `cache` (Tier-2 fallback) | T2 | **Workers KV** + Durable Objects | edge | session shards, eventual-consistent |
| 19 | `blob` (cold archive only) | T2 | **R2** | edge | S3-compatible cold tier; never primary |
| 20 | AI Gateway (BYOK proxy) | — | **Cloudflare AI Gateway** | edge | Routes `vertex_services.py` calls → google-ai-studio with caching, observability, rate-limit pooling |
| 21 | Email routing | — | Cloudflare Email Routing | edge | Custom-domain inbox → SES/Mailgun forwarder |
| 22 | DNS | — | Cloudflare DNS | edge | Authoritative DNS for `syrabit.com` |
| 23 | Cache Reserve | — | Cloudflare Cache Reserve | edge | 30-day asset cache for cold reads |

### 3.2 Cost shape at 10k DAU

| Surface | $/mo | Note |
|---|---:|---|
| Pages (frontend) | $0 | Within free tier |
| Workers AI (all 13 fallback inferences) | ~$10 | Most never fire; translate (Tier-1) is the only steady draw |
| Workers (CF AI Gateway requests) | ~$5 | 10M req/mo well within paid plan |
| Workers KV cache | ~$3 | Tier-2 only, infrequent |
| Vectorize | ~$2 | Tier-3 only |
| R2 cold archive | ~$5 | ~50 GB cold blobs |
| Cache Reserve + Egress | ~$3 | Long-tail asset hits |
| WAF + Bot Management + Rate Limit | $0 | Within Pro plan included with credit |
| DNS + Email Routing + Browser Rendering | $0 | Free tier |
| Edge proxy / Load Balancer | $3 | Steers DO ↔ Azure standby |
| **Subtotal** | **~$31** | **7% of $5k CF for Startups — massive headroom** |

---

## §4 — AWS (`~$62/mo`, $1k AWS Activate, 75% draw)

### 4.1 Features served

| # | Feature | Tier | Component | Region | Model / SKU |
|---:|---|:-:|---|---|---|
| 1 | `embed` (canonical primary) | T1 | **AWS Bedrock — Cohere** | `us-west-2` | `cohere.embed-multilingual-v3` (1024-dim) |
| 2 | `rerank` (canonical primary) | T1 | **AWS Bedrock — Cohere** | `us-west-2` | `cohere.rerank-multilingual-v3` |
| 3 | `blob` (sole object store) | T1 | **AWS S3** | `us-west-2` (primary) + `us-east-1` (DR) | `syrabit-prod-pdfs`, `syrabit-prod-audio`, `syrabit-prod-backups` |
| 4 | `async_queue` | T1 | **AWS SQS standard queue** + Lambda consumer | `us-west-2` (`ap-south-1` sibling for sqs_consumer + email_worker) | `syrabit-jobs` queue |
| 5 | PDF parse worker | T1 | AWS Lambda `syrabit-pdf-parse` (ARM64) | `us-west-2` | 1024 MB / 30s timeout |
| 6 | Email worker | T1 | AWS Lambda `email-worker` | `ap-south-1` | SES sender |
| 7 | Bedrock proxy worker | T1 | AWS Lambda `bedrock-proxy` | `us-east-1` | Lives in us-east-1 for Bedrock model availability; cross-region invoked from us-west-2 backend |
| 8 | Email send | T1 | **AWS SES** | `us-west-2` | 62k/mo free tier |
| 9 | `tts` (Tier-5 last-resort fallback) | T5 | **AWS Polly** | `us-west-2` | Neural voices, post-#337 fallback |
| 10 | `voice` (TTS leg, Tier-5) | T5 | AWS Polly Neural | `us-west-2` | last resort if ElevenLabs+Vertex+CF all fail |
| 11 | AWS-native alarms | — | **CloudWatch** | per-region | Lambda metric filters → SNS `ops_alerts` topic; alarm covers sqs_consumer + email_worker (ap-south-1 sibling) and bedrock-proxy (us-east-1 sibling) |
| 12 | Secret store for Lambda | — | AWS SSM Parameter Store | per-region | App Insights connection string + Axiom token + Bedrock IAM |

### 4.2 What AWS Bedrock does NOT serve (binding contract)

> **Bedrock is Cohere-only.** The following are explicitly removed from
> any Bedrock chain:
> - ❌ Anthropic Claude (any version)
> - ❌ Amazon Titan (any version)
> - ❌ Meta Llama (any version)
> - ❌ Mistral (any version)
> - ❌ AI21 Jamba (any version)
> - ❌ Amazon Nova (any version)
>
> Chat workloads use Vertex Gemini → Azure GPT-4.1-mini → CF Workers AI.
> The `bedrock` direct entry has been removed from `english_rag_chat`.

### 4.3 IAM scope (least privilege)

| Resource | Principal | Allowed actions |
|---|---|---|
| `bedrock:InvokeModel` | DO backend `syrabit-backend` IAM user | `cohere.embed-multilingual-v3`, `cohere.rerank-multilingual-v3` ONLY |
| `s3:GetObject` / `s3:PutObject` | DO backend + Lambda workers | `syrabit-prod-*` buckets only |
| `sqs:SendMessage` / `sqs:ReceiveMessage` | DO backend + Lambda consumers | `syrabit-jobs` queue only |
| `ses:SendEmail` | `email-worker` Lambda | `noreply@syrabit.com` sender only |
| `polly:SynthesizeSpeech` | DO backend (Tier-5 fallback) | Neural voices only |
| `cloudwatch:PutMetricData` | Lambda functions | Per-function metric namespace |

### 4.4 Cost shape at 10k DAU

| Surface | $/mo | Note |
|---|---:|---|
| Bedrock Cohere embed | ~$20 | 18M tok/mo |
| Bedrock Cohere rerank | ~$30 | 60k queries/mo × 100 candidates |
| S3 storage + ops | ~$5 | ~100 GB blobs + lifecycle to R2 cold |
| SES | $0 | Within 62k/mo free |
| Lambda (PDF parse + email + bedrock proxy) | ~$2 | All ARM64, well within free tier |
| SQS | $0 | Within 1M req/mo free |
| Polly | $0–5 | Tier-5; rarely fires |
| CloudWatch | $0 | Within 5 GB ingest free tier |
| **Subtotal** | **~$62** | **75% of $1k AWS Activate, mitigated by Lambda free tier + SES free tier** |

---

## §5 — Auxiliary providers (`$0` against 4-cloud math)

These do not consume AWS/Azure/CF/GCP credit; each carries its own
free or startup-credit pool.

| Provider | Features | Credit | Status |
|---|---|---|:-:|
| **Pinecone** | `vector_retrieve` (THE RAG store, `syrabit-rag` 1024-dim) | Starter free → $5k Startup reserved | LANDED |
| **MongoDB Atlas** | `chat_history`, `analytics`, all canonical app state | $500 → $5k extended | LANDED |
| **Voyage AI** | `embed` Tier-2, `rerank` Tier-2 | Free trial | LANDED |
| **Cohere** | (consumed via Bedrock — counts as AWS) | n/a | n/a |
| **Sarvam** | `assamese_rag_chat` Tier-2, `assamese_content` Tier-2, `translate` Tier-3 | Sarvam Startup credit | PENDING |
| **ElevenLabs** | `tts` Tier-1, `voice` TTS leg Tier-1 | $4k startup | PENDING |
| **Deepgram** | `stt` Tier-1, `voice` STT leg Tier-1 | $1k startup | PENDING |
| **AssemblyAI** | `stt` Tier-2, `voice` STT leg Tier-2 | $50 instant | LANDED |
| **Cartesia** | `tts` Tier-3 | Free credit | LANDED |
| **Perplexity** | `search_rag` Tier-2 | Free tier | LANDED |
| **Exa AI** | `live_search` Tier-1 | Free tier | LANDED |
| **Tavily** | `live_search` Tier-2 | Free tier | LANDED |
| **Momento** | `cache` Tier-2 | Free 5 GB / 5M req | LANDED |
| **Axiom** | `apm` parallel log sink (long retention) | Free 0.5 TB/mo | LANDED |
| **Sentry** | Error tracking | Free tier | LANDED |
| **Cash exposure today** | — | **$0** | PENDING credits have free-tier coverage |

---

## §6 — Storage & state delegation (cross-cloud)

| Concern | Owner | Notes |
|---|---|---|
| **RAG vectors** | **Pinecone** (auxiliary) | `syrabit-rag` index, 1024-dim cosine, `aws-us-west-2`. THE RAG store of record. Vertex Vector + CF Vectorize are Tier-2/3 fallback only. |
| **Chat history** | **MongoDB Atlas** (auxiliary) | `conversations` collection. Single source of truth — no multi-store writes. |
| **Application state** (notes, flashcards, streaks, leaderboards, quizzes, CMS, SEO topics, push tokens, audit logs) | **MongoDB Atlas** (auxiliary) | All canonical writes go to Mongo. Redis is cache only. |
| **Cache** (sessions, rate limits, prompt cache, leaderboard ZSET) | **Azure Cache for Redis** (T1) → Momento (T2) → CF KV (T3) → Mongo atomic (T4) | TTL-bounded; Mongo is the durable backstop |
| **Object blobs** (PDFs, audio, backups) | **AWS S3** (T1) → CF R2 (cold archive only) | Sole object store; lifecycle policy moves > 30d objects to R2 |
| **Long-term logs / events** | **Axiom** (auxiliary) + Mongo | Axiom for queryable logs; Mongo for typed analytics events |
| **Distributed traces** | **Azure App Insights** (canonical) + Axiom (parallel sink) | Dual OTLP exporters in DO Python backend, AWS Lambda, DO Rust core (via OTel Collector) |

---

## §7 — Inference dispatch — feature × cloud matrix

Tier numbers indicate the call order. ✅ = primary, ◯ = fallback,
— = not in chain.

| Feature | GCP | Azure | Cloudflare | AWS | Auxiliary |
|---|:-:|:-:|:-:|:-:|---|
| `english_rag_chat` | ✅ T1 | ◯ T2 | ◯ T3 | — | — |
| `assamese_rag_chat` | ✅ T1 | ◯ T3 | ◯ T4 | — | Sarvam T2 |
| `content` | ◯ T2 | ✅ T1 | ◯ T3 | — | — |
| `assamese_content` | ✅ T1 | — | ◯ T3 | — | Sarvam T2 |
| `tts` | ◯ T2 | — | ◯ T4 | ◯ T5 (Polly) | ElevenLabs T1, Cartesia T3 |
| `stt` | ◯ T4 | — | ◯ T3 | — | Deepgram T1, AssemblyAI T2 |
| `voice` | ◯ T4 (TTS) | — | ◯ T5 | ◯ T6 (Polly) | Deepgram T1 (STT), AssemblyAI T2 (STT), ElevenLabs T3 (TTS) |
| `embed` | — | — | ◯ T3 | ✅ T1 (Bedrock Cohere) | Voyage T2 |
| `rerank` | — | — | ◯ T3 | ✅ T1 (Bedrock Cohere) | Voyage T2 |
| `translate` | ◯ T2 | ◯ T4 | ✅ T1 (IndicTrans2) | — | Sarvam T3 |
| `vision` | ✅ T1 | — | ◯ T3 | — | — |
| `safety` | ✅ T1 | — | ◯ T2 | — | — |
| `search_rag` | ✅ T1 (Discovery) | — | ◯ T3 | — | Perplexity T2 |
| `live_search` | — | — | ◯ T3 | — | Exa T1, Tavily T2 |
| `vector_retrieve` | ◯ T2 (Vertex) | — | ◯ T3 (Vectorize) | — | Pinecone T1 |
| `cache` | — | ✅ T1 (Redis) | ◯ T3 (KV) | — | Momento T2, Mongo T4 |
| `chat_history` | — | — | — | — | Mongo (sole) |
| `analytics` | — | — | — | — | Mongo + Axiom |
| `blob` | — | — | ◯ T2 (R2 cold) | ✅ T1 (S3) | — |
| `cron` | — | ✅ T1 (ACA Jobs) | — | — | DO cron (backend-resident only) |
| `async_queue` | — | — | — | ✅ T1 (SQS+Lambda) | — |
| `apm` | — | ✅ T1 (App Insights) | — | ◯ (CloudWatch — AWS-native alarms only) | Axiom (parallel), Sentry (errors) |

---

## §8 — Hosting / runtime delegation

| Workload | Cloud | Where | Notes |
|---|---|---|---|
| **Frontend SPA** | Cloudflare | Pages (edge) | React build, custom domain |
| **Backend Python (FastAPI)** | Digital Ocean | App Platform | Behind Cloudflare proxy (Task #334). Was AWS App Runner; migrated. |
| **Rust core (gRPC + axum HTTP)** | Digital Ocean | App Platform | Wired to OTel Collector internal-only |
| **OTel Collector** | Digital Ocean | App Platform basic-xs | Internal-only, exports to App Insights + Axiom |
| **Lambda workers** (PDF parse, email, bedrock proxy) | AWS | Lambda (3 regions) | ARM64, image-based |
| **Async queue** | AWS | SQS standard | + Lambda consumer |
| **Cron jobs (canonical 18 KEDA jobs)** | Azure | Container Apps Jobs | Scale-to-zero |
| **Standby failover backend** | Azure | Container Apps secondary | Cold standby; CF LB steers |
| **Inference (all)** | GCP / Azure / CF / AWS / Auxiliary | per provider-priority-map.md | dispatched from DO backend |

---

## §9 — Per-cloud env var contracts (what each cloud requires in DO backend)

### 9.1 GCP / Vertex
```
VERTEX_SERVICE_ACCOUNT={"type":"service_account",...}      # JSON
VERTEX_LOCATION=us-central1
VERTEX_INDEX_ENDPOINT=projects/.../indexEndpoints/...
GCP_DISCOVERY_DATA_STORE=projects/.../dataStores/...        # already set
GEMINI_API_KEY=...                                          # legacy / fallback
CF_AI_GATEWAY_ACCOUNT_ID=...                                # prod default for Gemini
CF_AI_GATEWAY_GATEWAY_ID=syrabit-gemini
CF_AI_GATEWAY_GOOGLE_AI_STUDIO_KEY=...
GOOGLE_APPLICATION_CREDENTIALS_JSON=...                     # already set
```

### 9.2 Azure
```
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com   # already set
AZURE_OPENAI_DEPLOYMENT=syrabit-chat                        # already set
AZURE_OPENAI_MODEL=gpt-4.1-mini                             # already set
AZURE_OPENAI_API_KEY=...                                    # MISSING — secret needed
AZURE_REDIS_CONNECTION_STRING=...                           # cache primary
APPLICATIONINSIGHTS_CONNECTION_STRING=...                   # APM canonical
```

### 9.3 Cloudflare
```
CF_API_TOKEN=...
CF_ACCOUNT_ID=...
CF_AI_GATEWAY_ACCOUNT_ID=... (above)
CF_VECTORIZE_INDEX=syrabit-rag-vectorize
CF_KV_NAMESPACE_ID=...
CF_R2_BUCKET=syrabit-cold
```

### 9.4 AWS
```
AWS_REGION=us-west-2                                        # already set
AWS_ACCESS_KEY_ID=...                                       # IAM least-privilege user
AWS_SECRET_ACCESS_KEY=...
S3_BUCKET_PDFS=syrabit-prod-pdfs
S3_BUCKET_AUDIO=syrabit-prod-audio
S3_BUCKET_BACKUPS=syrabit-prod-backups
SQS_QUEUE_URL=https://sqs.us-west-2.amazonaws.com/.../syrabit-jobs
BEDROCK_REGION=us-west-2                                    # cohere only
SES_SENDER=noreply@syrabit.com
```

### 9.5 Auxiliary
```
PINECONE_API_KEY=...
PINECONE_INDEX=syrabit-rag
MONGO_URL=...                                               # already set
VOYAGE_API_KEY=...                                          # already set
SARVAM_API_KEY=...                                          # PENDING credit grant
ELEVENLABS_API_KEY=...                                      # PENDING credit grant
DEEPGRAM_API_KEY=...                                        # PENDING credit grant
ASSEMBLYAI_API_KEY=...
CARTESIA_API_KEY=...                                        # already set
PERPLEXITY_API_KEY=...
EXA_API_KEY=...
TAVILY_API_KEY=...
MOMENTO_AUTH_TOKEN=...
AXIOM_API_TOKEN=...                                         # set via task #333
SENTRY_DSN=...
```

---

## §10 — Reconciliation against companion docs

| Doc | What this doc must reconcile with |
|---|---|
| `provider-priority-map.md` | §7 inference matrix must match the canonical dict; §6 storage table must match its storage section |
| `cloud-allocation-plan.md` | §1–§4 per-cloud feature lists must match the hosting-vs-inference matrix |
| `cloud-service-breakdown.md` | every component named here must appear in the per-cloud service inventory |
| `auxiliary-providers-delegation.md` | §5 auxiliary table must match auxiliary-doc roles |
| `feature-to-provider-mapping-detailed.md` | per-feature chains must map onto §7 |
| `feature-deep-dive.md` §7.3 drift register | live config drift items resolve to entries in §7 |
| `feature-to-provider-audit.md` | per-cloud $ totals (§1.3, §2.3, §3.2, §4.4) must reconcile with audit |
| `credit-applications.md` | every PENDING entry in §5 must have a tracked application |
| `observability.md` (Task #333) | §2 APM + §4 CloudWatch + §5 Axiom delegation must match the observability runbook |

---

## §11 — Re-running this doc

```bash
# 1. Refresh §7 matrix from canonical dict
rg -n "PROVIDER_PRIORITY|POOL_WEIGHTS" docs/infra/provider-priority-map.md

# 2. Verify Bedrock-Cohere-only contract
rg -n "claude|titan|jamba|nova|llama|mistral" docs/infra/provider-priority-map.md
# expect: 0 matches inside any chain (only excluded-providers note)

# 3. Verify Sarvam scope
rg -n "sarvam" docs/infra/provider-priority-map.md
# expect: only in assamese_rag_chat, assamese_content, translate

# 4. Verify cron canonical
rg -n "cron|scheduled" docs/infra/provider-priority-map.md
# expect: azure_container_apps_jobs only

# 5. Verify storage delegation
rg -n "pinecone|mongo|s3" docs/infra/provider-priority-map.md
```

> Re-run any time a feature is added, a tier is reordered, or a
> credit pool moves between PENDING and LANDED.
