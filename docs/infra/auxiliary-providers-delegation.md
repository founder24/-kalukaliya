# Syrabit — Auxiliary Provider Delegation (Beyond the Four Clouds)

**Companion to:** `docs/infra/cloud-allocation-plan.md`,
`docs/infra/cost-per-feature-comparison.md`, and
`docs/infra/10k-dau-cost-audit.md`.
**Last updated:** 2026-05-04
**Question this doc answers:**
*Beyond the four hosting/inference clouds (CF + AWS + Azure + Vertex),
which third-party providers does Syrabit lean on, what role does each
play in the dispatcher, and at 10k DAU is each one fully covered by its
free tier or credit?*

> Scope: data layer (Mongo / Pinecone / Azure Cache for Redis / Momento),
> observability (Axiom / Sentry), specialized inference (Deepgram /
> ElevenLabs / Sarvam (Assamese chat only) / Cartesia / Voyage / Cohere-direct),
> email (Resend), and CI/CD (GitHub). Each is an API surface the AWS App
> Runner backend calls; none host compute.

---

## 0. Standing rules

1. **Dispatcher is the source of truth.** Provider priority and weights
   live in `artifacts/syrabit-backend/config.py` (`PROVIDER_PRIORITY` +
   `POOL_WEIGHTS`). This doc describes *which providers we use*, not the
   ordering. Ordering changes happen only in code.
2. **Every provider must be covered by a free tier or active credit** at
   10k DAU. Providers requiring cash spend at 10k DAU are flagged
   ⚠️ CASH and require either a credit application or a swap.
3. **Each "primary" provider must have at least one cross-provider
   fallback** so that any single account suspension doesn't take a
   feature down.

---

## 1. Data layer

### 1.1 Mongo Atlas — primary application database

| Item | Value |
|---|---|
| Role | Primary OLTP store: users, attempts, MCQ banks, lecture metadata, payments. |
| Tier at 10k DAU | M0 free (512 MB) → upgrade to **M2 Shared ($9/mo)** at ~5 GB working set |
| Sizing | ~5 GB (10k DAU × ~500 KB metadata/user including attempts history) |
| Credit | $500 startup credit |
| Coverage | M2 × 12 mo = $108 ⇐ $500 credit covers **~55 months** |
| Cash exposure | $0 |
| Fallback | None for primary; nightly logical backup → S3 (point-in-time recovery via Atlas Continuous Backup on M10+) |
| **Status** | ✅ free → low-cost upgrade fully covered |

**Upgrade trigger:** `db.serverStatus().storageEngine.cacheBytes > 400 MB`
(80% of M0 cap). Stay on M0 until then.

### 1.2 Pinecone — primary vector index

| Item | Value |
|---|---|
| Role | Primary RAG retriever. Course content embeddings, lecture transcripts, MCQ similarity search. |
| Tier at 10k DAU | Free **Starter pod** (1 project, 100k vectors, 1 index) |
| Sizing | ~50k content vectors (one per AHSEC/SEBA chapter + worked-example), 1024-dim from Cohere or 768-dim from Vertex `text-embedding-004` |
| Credit | None (free tier perpetual) |
| Coverage | 100k vector ceiling reached at ~30k DAU; upgrade then |
| Cash exposure | $0 at 10k DAU |
| Fallback chain (per dispatcher) | Pinecone → **Vertex Vector Search / Matching Engine** (`retrievers/vertex.py`, code-only/cold by default) → **CF Vectorize** (free credit) → **Vertex Discovery Engine** (`discovery_engine_client.py`) → graceful degrade to Pinecone retry |
| **Status** | ✅ free tier sufficient |

### 1.3 Azure Cache for Redis Basic C0 — cache + sessions + rate limiting (PRIMARY)

| Item | Value |
|---|---|
| Role | **Primary** session store, JWT blacklist, dispatcher rate limiting, prompt cache layer for App Runner. Full Redis protocol (transactional INCR for rate limits, atomic SETEX for sessions, sorted-set leaderboards). Reachable from App Runner via PrivateLink-to-Azure peering or public TLS endpoint with IP allowlist. Reachable from Azure Container Apps (rust-core + workers) via private endpoint. |
| SKU at 10k DAU | **Basic C0** — 250 MB cache, single node, no SLA. Sufficient for sessions + rate-limit hot keys + prompt-cache index. |
| Monthly cost | ~$16.20/mo (Basic C0 list price) |
| Credit | **Microsoft for Startups Azure pool ($2,500)** — already counted in `10k-dau-cost-audit.md` §2.3 (Azure pool was at 37% draw before this; $16/mo brings it to ~45% — still deep headroom) |
| Cash exposure | $0 (covered by existing Azure credit pool) |
| Coverage | $2,500 alone ÷ $16.20/mo ≈ **154 months**; in shared pool with other Azure spend ≈ **≥ 24 months runway** |
| Fallback chain | Azure Cache → **Momento Cache** (Tier-2, free 5GB/5M req) → **CF KV / Durable Objects** (Tier-3 last resort, within CF credit) → **Mongo `find_and_modify`** (Tier-4 graceful degrade for atomic ops only) |
| **Status** | ✅ **PRIMARY on Azure credit, zero cash** |

### 1.4 Momento Cache — serverless cache fallback (startup credit + generous free tier)

| Item | Value |
|---|---|
| Role | Tier-2 fallback below Azure Cache for Redis. Serverless HTTP/gRPC API (no long-lived connections), so it works from CF Workers + App Runner + Lambda without connection-pool gymnastics. Good fit for prompt-cache and ephemeral session blobs; weaker semantics than Redis for atomic rate-limit counters (Azure Redis primary handles those — Momento takes over the read-heavy session/cache reads if Azure Redis is degraded). |
| Tier at 10k DAU | Free **5 GB storage + 50 req/sec sustained + 5M req/mo** (perpetual free tier) |
| Sizing | At 10k DAU prompt-cache + ephemeral session blobs: ~3M req/mo, ~1 GB storage ⇒ **comfortably within free tier** even if promoted to primary |
| Credit | **Momento Startup Program** ($500–1k typical award) — pursued as further safety margin if free tier breaches |
| Cash exposure | $0 (free tier sufficient at 10k DAU) |
| Fallback role | Tier-2 in the cache chain. Especially valuable for CF-edge-originated cache reads (HTTP API, no Redis-protocol middleware needed). |
| **Status** | ✅ **Tier-2 fallback on free tier; Momento credit reserved as further safety margin** |

### 1.5 CF KV / Durable Objects — last-resort eventually-consistent fallback

| Item | Value |
|---|---|
| Role | Final fallback for read-heavy session lookups when both Azure Cache for Redis and Momento are unavailable. Eventually consistent (CF KV) or strongly consistent (Durable Objects). Cannot serve transactional rate-limit semantics — degrade to Mongo `find_and_modify` in that case. |
| Tier at 10k DAU | CF KV: 100k reads/d free; Durable Objects: 1M req free in CF for Startups credit |
| Sizing | Already partially used as edge cache (per CF section in `10k-dau-cost-audit.md`); promotable to fallback role |
| Credit | Cloudflare for Startups pool ($5k, 5% drawn — vast headroom) |
| Cash exposure | $0 |
| Fallback role | Tier-3 (last resort). Strongly preferred to outright outage of session/cache layer. |
| **Status** | ✅ within CF credit |

### 1.6 Cache provider chain — summary

```
Cache + sessions + rate limit dispatcher chain (per artifacts/syrabit-backend/config.py):

  1. Azure Cache for Redis Basic C0  ← PRIMARY, $16/mo within Azure credit, full Redis semantics
  2. Momento Cache                    ← Tier-2, free 5GB/5M req, HTTP API
  3. CF KV / Durable Objects          ← Tier-3 last resort, within CF credit, eventually consistent
  4. Mongo find_and_modify            ← Tier-4 graceful-degrade for atomic ops only

Net cash at 10k DAU: $0 at every tier.
```

> **Why we replaced Upstash:** Upstash REST was the previous primary at
> ~$5–8/mo cash overage past its 10k commands/day free tier. Azure Cache
> for Redis Basic C0 ($16.20/mo) is fully covered by the existing Azure
> startup credit (62% unused before this swap) and provides full Redis
> protocol semantics — strictly better than Upstash's REST-only API for
> atomic rate-limit counters and sorted-set operations. Momento as Tier-2
> covers the HTTP-API niche with a perpetual free tier plus an active
> startup credit program. **Net effect: zero cash at every tier of the
> cache chain.**
>
> **Operational migration note:** the dispatcher code still references
> `UPSTASH_REDIS_REST_*` env vars in `artifacts/syrabit-backend/cache.py`
> and the Mongo+Upstash references appear in deployment docs
> (`docs/AWS-DEPLOYMENT.md`, `docs/DEPLOYMENT.md`,
> `docs/SYRABIT_DEVELOPER_GUIDE.md`). Cutting them over to
> `AZURE_REDIS_*` + `MOMENTO_*` is a code task tracked separately; this
> doc reflects the **target** strategic delegation.

---

## 2. Observability (alongside the Azure App Insights central APM)

### 2.1 Axiom — long-term log retention + analytics

| Item | Value |
|---|---|
| Role | Long-retention searchable logs (90 days). Backstop to App Insights' 5 GB/mo free tier. Dataset receives JSON logs from App Runner, Container Apps, Lambda, Workers via OTLP. |
| Tier at 10k DAU | Free **0.5 GB/day = 15 GB/mo** |
| Sizing | ~10 GB/mo at 10k DAU (matches our log volume model) |
| Credit | None (free tier) |
| Cash exposure | $0 |
| Fallback | App Insights (subset, 5 GB free) + CloudWatch (AWS-native only) |
| **Status** | ✅ free tier sufficient |

### 2.2 Sentry — error tracking

| Item | Value |
|---|---|
| Role | Frontend + backend exception capture, release tracking, performance traces. SDK in CF Workers + App Runner FastAPI + Container Apps + frontend SPA. |
| Tier at 10k DAU | Free **Developer plan**: 5k errors/mo + 10k performance units + 50 replays |
| Sizing | At 10k DAU with healthy app: ~2k errors/mo, ~8k perf units. Within free tier. |
| Credit | None |
| Cash exposure | $0 (until error rate exceeds 5k/mo — alarm at 4k) |
| Fallback | Errors also surface in App Insights and Axiom; Sentry is the dev-experience layer |
| **Status** | ✅ free tier sufficient (alarm at 80% utilization) |

---

## 3. Specialized AI inference

### 3.1 Deepgram — primary STT

| Item | Value |
|---|---|
| Role | Primary speech-to-text. Lecture audio → transcript pipeline; live voice tutor STT path. |
| Model | Nova-2 ($0.0043/min) or Nova-2 Multilingual ($0.0050/min) for Indic |
| Sizing at 10k DAU | 15k min/mo |
| Pay-as-you-go cost | 15k × $0.0043 = ~$64.50/mo (English-heavy mix) |
| Credit status | $200 free credit on signup; **$1k startup credit being pursued** (PENDING) |
| Coverage if $1k lands | $1k ÷ $64.50/mo = **~15 months** ⇒ ✅ |
| Coverage today (only $200 free) | ~3 months ⇒ ⚠️ runs out before EOY |
| Cash exposure if credit doesn't land | ⚠️ **~$65/mo cash** from month 4 |
| Fallback chain | Deepgram → **CF Workers AI Whisper** (free credit) → **Vertex Cloud Speech-to-Text Chirp** (paid post-free, but mostly free at 60min/mo) → graceful degrade (skip transcript, prompt user to retry) |
| **Status** | ⚠️ **DEPENDS on $1k credit landing.** Mitigation: if pursuit fails, demote Deepgram to fallback and promote Workers AI Whisper as primary (slight quality drop, $0/mo). |

### 3.2 ElevenLabs — primary TTS

| Item | Value |
|---|---|
| Role | Primary text-to-speech for Read-Aloud, voice tutor responses. |
| Model | Multilingual v2 (Indic + EN) |
| Sizing at 10k DAU | 10M chars/mo (caching cuts to ~6M effective after R2 audio cache) |
| Pay-as-you-go cost | Pro plan $99/mo (covers ~500k chars in plan, then $0.30/1k overage). 10M chars overage = ~$3,000/mo on Pro pay-go. **Effectively must use Scale plan or credit.** |
| Credit status | **$4k startup credit being pursued** (PENDING) |
| Coverage if $4k lands | At enterprise rate ~$0.05/1k chars on Scale plan: 10M chars = $500/mo ⇒ ~8 months. Or with caching to 6M effective: ~13 months. |
| Coverage today (only free 10k chars/mo) | ~minutes |
| Cash exposure if credit doesn't land | ⚠️ **prohibitive** (~$500–3000/mo depending on plan) |
| Fallback chain | ElevenLabs → **GCP Cloud TTS Standard** (4M chars/mo free, then $4/M = $24/mo for 6M extra) → **Cartesia** (free credit) → **CF Workers AI MeloTTS** (within CF credit) → **AWS Polly** (post-#337 = paid, ~$16/M chars Neural) |
| **Status** | ⚠️ **DEPENDS on $4k credit landing.** Mitigation: if pursuit fails, demote ElevenLabs to "premium voice" feature flag and promote GCP TTS Standard as primary (quality drop on Indic, but $24/mo all-in within Vertex pool). |

### 3.3 Sarvam — Indic LLM (Assamese chat only, future activation)

| Item | Value |
|---|---|
| Role | Indic-tuned LLM **scoped to the `assamese_rag_chat` chain only** (Tier-2 fallback after Vertex Gemini 2.5 Flash). Removed from voice/TTS chain — Indic TTS now goes GCP Cloud TTS Neural2 → CF Workers AI MeloTTS. |
| Sizing if active | 50k asm chats × 1.5k tokens = 75M tokens/mo |
| Pay-as-you-go cost | Sarvam-M ~$5/M tokens = ~$375/mo |
| Credit status | None pursued yet (Vertex Gemini fills the slot) |
| Cash exposure | $0 (not currently in dispatcher rotation) |
| Fallback role today | Reserved as fallback below Vertex Gemini for asm chat |
| **Status** | ✅ inactive; activate only if a Sarvam credit lands and only inside the Assamese chat chain. Otherwise Vertex Gemini handles asm chat at ~$15/mo within Vertex pool. |

### 3.4 ~~Groq — fast Llama inference~~ (REMOVED from chat chain)

**Removed from the chat fallback chain.** Reason: chat chain consolidated
to Vertex Gemini 2.5 Flash → Azure GPT-4.1-mini → CF Workers AI
gpt-oss-20b/Llama-3, all within existing Tier-1 credit pools. Groq's
"fast Llama overflow" tier added latency-vs-coverage complexity with no
credit-pool benefit since CF Workers AI Llama is already on the CF $5k
credit pool. Account/key may stay provisioned for future re-introduction
but is not in the dispatcher.

### 3.5 ~~Cerebras — fast Llama inference (alternative to Groq)~~ (REMOVED from chat chain)

| Item | Value |
|---|---|
| Role | Burst overflow for `english_rag_chat` when both Azure OpenAI and Vertex Gemini are throttled or for latency-critical "instant answer" UX. Llama-3.3-70B at >400 tok/s. |
| Tier at 10k DAU | Free tier: ~14.4k req/day = ~432k/mo |
| Sizing | At 10k DAU, expected overflow ~10k req/mo (rare path) |
| Credit | API key already in secrets (`GROQ_API_KEY`); free tier perpetual |
| Cash exposure | $0 |
| Fallback role | Tier-3 fallback for english chat (after Azure OpenAI, after Vertex Gemini) |
| **Status** | ✅ free tier vastly oversized for our overflow volume |

### 3.5 ~~Cerebras — fast Llama inference~~ (REMOVED from chat chain — duplicate header retained intentionally for back-link safety)

| Item | Value |
|---|---|
| Role | **Removed from chat chain** alongside Groq for the same consolidation reason. Account/key may stay provisioned for future re-introduction but is not in the dispatcher. |
| Tier at 10k DAU | Free tier: 30 req/min, 14.4k req/day |
| Sizing | Same overflow volume; not exhausted |
| Credit | API key already in secrets (`CEREBRAS_API_KEY`) |
| Cash exposure | $0 |
| Fallback role | None (removed from chat dispatcher) |
| **Status** | ✅ free tier sufficient |

### 3.6 Cartesia — alternative TTS

| Item | Value |
|---|---|
| Role | Fallback TTS below ElevenLabs and GCP TTS. Sonic model has good Indic quality. |
| Tier at 10k DAU | Free credits on signup ($5–10) |
| Sizing | Only invoked if both ElevenLabs and GCP TTS exhausted |
| Credit | API key in secrets (`CARTESIA_API_KEY`) |
| Cash exposure | $0 (rarely hit) |
| Fallback role | Tier-3/4 in TTS chain |
| **Status** | ✅ inactive but credentialed; quiet standby |

### 3.7 Voyage — embedding alternative

| Item | Value |
|---|---|
| Role | Embedding alternative below CF Workers AI bge-m3 and AWS Bedrock Cohere. `voyage-3-large` for English-heavy content. |
| Tier at 10k DAU | Free trial 50M tokens (one-time) |
| Sizing | Not in active rotation; reserved for evaluation A/B tests |
| Credit | API key in secrets (`VOYAGE_API_KEY`) |
| Cash exposure | $0 (not active) |
| Fallback role | Standby; primary EN embedding alternative if bge-m3 quality regresses |
| **Status** | ✅ standby (free trial credit untouched) |

### 3.8 Cohere — embeddings + rerank (via AWS Bedrock primarily)

| Item | Value |
|---|---|
| Role | `embed-multilingual-v3` (1024-dim) + `rerank-v3-5`. Always called via **AWS Bedrock** under AWS Activate credit. The direct Cohere API is *not* used. |
| Sizing | See `10k-dau-cost-audit.md` §2.2 — ~$21/mo within AWS pool |
| Credit | AWS Activate ($1k pool, shared with App Runner + S3 + SES) |
| Cash exposure | $0 (within AWS Activate) |
| Fallback role | Embed chain primary fallback below CF Workers AI; rerank has no fallback (graceful degrade) |
| **Status** | ✅ within AWS Activate. **Hard cap: ≤ 10k rerank calls/mo** (per AWS audit §4.1). |

### 3.9 Gemini direct API key — legacy fallback

| Item | Value |
|---|---|
| Role | Legacy `GEMINI_API_KEY` rollback path for `vertex_services.py` if both CF AI Gateway and Vertex SA paths fail. AI Studio billing (separate from Vertex). |
| Sizing | Effectively zero in steady state |
| Credit | None (AI Studio free tier ~1500 req/day at flash rates) |
| Cash exposure | $0 |
| Fallback role | Tier-3 in `vertex_services.py` auth chain (per `cloud-service-breakdown.md` §4) |
| **Status** | ✅ rollback only; no operational role |

---

## 4. Email beyond AWS SES

### 4.1 Resend — marketing email + low-volume transactional fallback

| Item | Value |
|---|---|
| Role | Fallback transactional email below CF Email Routing and AWS SES. Also handles *marketing* email (separate domain) where SES tier-3 reputation can't be risked. |
| Tier at 10k DAU | Free **3k emails/mo, 100/day** |
| Sizing | Marketing volume ~1k/mo; fallback transactional ~500/mo |
| Credit | None (free tier) |
| Cash exposure | $0 |
| Fallback role | Tier-3 in email chain (CF Email Routing → AWS SES → Resend) |
| **Status** | ✅ free tier sufficient |

---

## 5. CI/CD + auth

### 5.1 GitHub — code, Actions, OIDC federation

| Item | Value |
|---|---|
| Role | Source of truth for all repos; GitHub Actions runs CI + deploy workflows; OIDC federation issues short-lived AWS + Azure credentials (no long-lived static keys). |
| Tier | GitHub Free for public repos; we use the platform-installed `github==1.0.0` integration. Actions: 2,000 min/mo free on private repos (Linux). |
| Sizing | ~500 build-min/mo at 10k DAU release cadence (1–2 deploys/day) |
| Credit | None needed |
| Cash exposure | $0 |
| **Status** | ✅ free tier sufficient |

---

## 6. All-up auxiliary monthly cost at 10k DAU

| Provider | Role | Monthly $ at 10k DAU | Cash vs credit |
|---|---|---:|---|
| Mongo Atlas | Primary DB (M0 / M2) | $0–9 | M0 free → M2 from $500 credit |
| Pinecone | Primary vector | $0 | free tier |
| Azure Cache for Redis Basic C0 | Cache + sessions + rate limit (**PRIMARY**) | $16 | Azure credit pool (Azure draw goes 37% → 45%) |
| Momento Cache | Cache fallback (Tier-2) | $0 | free tier (5GB / 5M req/mo) + Momento Startup credit reserved |
| CF KV / Durable Objects | Cache last resort (Tier-3) | $0 | CF credit pool |
| Axiom | Long-term logs | $0 | free tier |
| Sentry | Error tracking | $0 | free tier |
| Deepgram | Primary STT | $0 today / $65 from M4 if no credit | ⚠️ **DEPENDS on $1k credit** |
| ElevenLabs | Primary TTS | $0 today / $500–3000 if no credit | ⚠️ **DEPENDS on $4k credit** |
| Sarvam | Reserved Indic LLM (Assamese chat only) | $0 | inactive — activates only in `assamese_rag_chat` chain if Sarvam credit lands |
| ~~Groq~~ | ~~Fast chat overflow~~ | $0 | **REMOVED from chat chain** (consolidated to Vertex/Azure/CF Workers AI) |
| ~~Cerebras~~ | ~~Fast chat overflow~~ | $0 | **REMOVED from chat chain** (consolidated to Vertex/Azure/CF Workers AI) |
| Cartesia | TTS fallback | $0 | inactive (free credits standby) |
| Voyage | Embedding standby | $0 | inactive (free trial standby) |
| Cohere (via Bedrock) | Embed + rerank | $21 | AWS Activate (already counted) |
| Gemini direct key | Rollback only | $0 | rollback |
| Resend | Email fallback | $0 | free tier |
| GitHub | CI/CD | $0 | free tier |
| **Auxiliary subtotal (excluding Cohere already in AWS pool)** | | **$0/mo guaranteed cash + 2 credit dependencies** (cache primary now Azure Cache for Redis on credit, Momento as free-tier Tier-2) | |

### Combined with the four-cloud audit

| Layer | Monthly $ | Status |
|---|---:|---|
| Cloudflare | ~$20 | ✅ within $417 credit headroom |
| AWS | ~$75 | ✅ within $83 credit headroom (90% draw, mitigated) |
| Azure | ~$78 | ✅ within $208 credit headroom (37% draw) |
| Vertex | ~$150 | ✅ within $167 credit headroom (90% draw, mitigated) |
| Mongo + Pinecone + Axiom + Sentry + Cartesia/Voyage + Resend + GitHub | ~$0 | ✅ free tiers / Mongo $500 credit |
| Azure Cache for Redis (primary) + Momento (Tier-2) + CF KV (Tier-3) | **$0** | ✅ **cash eliminated** — Azure Cache on existing Azure credit, Momento on free tier, CF KV on CF credit |
| Deepgram | $0 today / $65 from M4 | ⚠️ DEPENDS on $1k credit |
| ElevenLabs | $0 today / $500+ if no credit | ⚠️ DEPENDS on $4k credit |
| **All-up at 10k DAU (best case)** | **~$323/mo** | ✅ all under credit, **zero cash** |
| **All-up at 10k DAU (worst case — both Deepgram + ElevenLabs credits fail and not demoted)** | **~$888/mo** | ⚠️ blow past credits in ~12 mo |
| **All-up at 10k DAU (worst case — both credits fail but graceful-degrade fallbacks engaged)** | **~$355/mo** | ✅ stays under combined credit headroom |

---

## 7. The three risks worth tracking

| Risk | Trigger | Mitigation in plan today |
|---|---|---|
| 🔴 **ElevenLabs $4k credit doesn't land** | TTS overflow at >$24/mo on GCP TTS Standard alone | Promote GCP TTS to primary (quality drop on Indic), or self-host Coqui TTS on Azure Container Apps (~$15/mo within Azure pool) |
| 🟠 **Deepgram $1k credit doesn't land by month 4** | Free $200 exhausted | Promote CF Workers AI Whisper to STT primary ($0/mo on CF credit), accept slight quality drop |
| 🟢 **Azure Cache for Redis Basic C0 saturates** (auth attacks, scraper bots fill the 250 MB cache) | Cache eviction rate spikes, hit rate drops below 80% | Bump SKU to **Basic C1** ($33/mo, 1 GB) — still within Azure credit (~50% draw); or promote **Momento** to absorb prompt-cache load (free 5GB), keeping Azure Redis for atomic rate-limit counters only. **No cash at any tier.** |

---

## 8. Final answer to the question

With **Upstash replaced by Azure Cache for Redis Basic C0 as the cache
primary** (and Momento + CF KV as the fallback chain), the non-cloud
auxiliary providers add **$0 of guaranteed cash spend** at 10k DAU.
The cache primary now sits inside the Azure credit pool (which moves
from 37% drawn to ~45% drawn — still deep headroom for ~24 months) and
delivers strictly better data semantics than Upstash's REST-only API.

Two providers (Deepgram, ElevenLabs) still have **outstanding credit
applications**. If both fail, documented fallback chains promote
CF Workers AI Whisper (STT) + GCP TTS Standard (TTS) at modest quality
cost and **zero additional cash**.

> **Net at 10k DAU: ~$339/mo all-in across four clouds + auxiliary
> chain (the Upstash $0 → Azure Cache $16 swap raises the total by $16
> but eliminates the $5–8/mo cash overage that would have appeared as
> traffic ramps). ~32-month combined runway. Zero guaranteed cash line
> items. Two PENDING credit dependencies, both with cash-free
> graceful-degrade fallbacks.**
