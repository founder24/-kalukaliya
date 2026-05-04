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

> Scope: data layer (Mongo / Pinecone / Upstash), observability (Axiom /
> Sentry), specialized inference (Deepgram / ElevenLabs / Sarvam / Groq /
> Cerebras / Cartesia / Voyage / Cohere-direct), email (Resend), and
> CI/CD (GitHub). Each is an API surface the AWS App Runner backend
> calls; none host compute.

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

### 1.3 Upstash Redis (REST) — cache + sessions + rate limiting (primary)

| Item | Value |
|---|---|
| Role | Session store, JWT blacklist, dispatcher rate limiting, Cloudflare-AI-Gateway-style prompt cache layer for App Runner. REST API (HTTP) so callable from CF Workers + App Runner + Azure Container Apps. |
| Tier at 10k DAU | Free 10k commands/day = 300k/mo |
| Sizing | ~10 commands/user/day × 10k DAU = 100k commands/day = **3M/mo** ⇒ exceeds free tier |
| Pricing past free | $0.20 per 100k commands → ~$5.40/mo |
| Credit | None directly; eliminated by promoting one of the fallbacks below |
| Cash exposure | ~$5–8/mo if kept primary, **$0 if demoted to fallback role** |
| **Status** | ✅ **cash eliminated** by the fallback chain in §1.4–1.5 |

### 1.4 Azure Cache for Redis Basic C0 — Redis-protocol fallback (within Azure credit)

| Item | Value |
|---|---|
| Role | Drop-in Redis-protocol fallback when Upstash is throttled, suspended, or breaches its free tier. Same data semantics (transactional INCR for rate limits, atomic SETEX for sessions, sorted-set leaderboards). Reachable from App Runner via PrivateLink-to-Azure peering or public TLS endpoint with IP allowlist. |
| SKU at 10k DAU | **Basic C0** — 250 MB cache, single node, no SLA. Sufficient for sessions + rate-limit hot keys. |
| Monthly cost | ~$16.20/mo (Basic C0 list price) |
| Credit | **Microsoft for Startups Azure pool ($2,500)** — already 37% drawn per `10k-dau-cost-audit.md` §2.3, ample headroom |
| Cash exposure | $0 (covered by existing Azure credit pool) |
| Coverage | $2,500 ÷ $16.20/mo ≈ **154 months alone**; in shared pool with other Azure spend ≈ ≥ 24 months runway |
| Fallback role | Tier-2 in the cache chain (after Upstash). Promotable to primary with a one-line dispatcher flip if Upstash incident persists > 24 hr. |
| **Status** | ✅ **on Azure credit, zero cash** |

### 1.5 Momento Cache — serverless cache fallback (startup credit + generous free tier)

| Item | Value |
|---|---|
| Role | Serverless cache fallback below Azure Cache for Redis. HTTP/gRPC API (no long-lived connections), making it usable from CF Workers + App Runner + Lambda without connection-pool gymnastics. Good fit for prompt-cache and ephemeral session blobs; weaker semantics than Redis for atomic rate-limit counters (use Azure Redis for those). |
| Tier at 10k DAU | Free **5 GB storage + 50 req/sec sustained + 5M req/mo** (perpetual) |
| Sizing | At 10k DAU prompt-cache + ephemeral session blobs: ~3M req/mo, ~1 GB storage ⇒ **comfortably within free tier** |
| Credit | **Momento Startup Program** ($500–1k typical award) — small but real; pursued only if free tier breaches |
| Cash exposure | $0 (free tier sufficient at 10k DAU) |
| Fallback role | Tier-3 in the cache chain. Especially valuable for CF-edge-originated cache reads (HTTP API, no Redis-protocol middleware needed). |
| **Status** | ✅ **free tier sufficient; Momento credit reserved as further safety margin** |

### 1.6 CF KV / Durable Objects — last-resort eventually-consistent fallback

| Item | Value |
|---|---|
| Role | Final fallback for read-heavy session lookups when all of Upstash + Azure Redis + Momento are unavailable. Eventually consistent (CF KV) or strongly consistent (Durable Objects). Cannot serve transactional rate-limit semantics — degrade to Mongo `find_and_modify` in that case. |
| Tier at 10k DAU | CF KV: 100k reads/d free; Durable Objects: 1M req free in CF for Startups credit |
| Sizing | Already partially used as edge cache (per CF section in `10k-dau-cost-audit.md`); promotable to fallback role |
| Credit | Cloudflare for Startups pool ($5k, 5% drawn — vast headroom) |
| Cash exposure | $0 |
| Fallback role | Tier-4 (last resort). Strongly preferred to outright outage of session/cache layer. |
| **Status** | ✅ within CF credit |

### 1.7 Cache provider chain — summary

```
Cache + sessions + rate limit dispatcher chain (per artifacts/syrabit-backend/config.py):

  1. Upstash Redis (REST)          ← primary, free→$5–8/mo overage if kept primary
  2. Azure Cache for Redis Basic C0 ← Tier-2, $16/mo within Azure credit, full Redis semantics
  3. Momento Cache                  ← Tier-3, free 5GB/5M req, HTTP API
  4. CF KV / Durable Objects        ← Tier-4 last resort, within CF credit, eventually consistent
  5. Mongo find_and_modify          ← Tier-5 graceful-degrade for atomic ops only

Net cash at 10k DAU after promoting any of 2/3/4 to absorb Upstash overage: $0.
```

> **Operational rule:** Upstash stays primary as long as monthly commands stay
> within ~5M (under $10/mo overage). If it crosses that, the dispatcher
> promotes Azure Cache for Redis Basic C0 to primary (within the Azure credit
> pool) and demotes Upstash to Tier-2. **No cash is required at any tier of
> this chain.**

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
| Fallback chain | ElevenLabs → **GCP Cloud TTS Standard** (4M chars/mo free, then $4/M = $24/mo for 6M extra) → **Cartesia** (free credit) → **AWS Polly** (post-#337 = paid, ~$16/M chars Neural) → **Sarvam TTS** (Indic, future credit) |
| **Status** | ⚠️ **DEPENDS on $4k credit landing.** Mitigation: if pursuit fails, demote ElevenLabs to "premium voice" feature flag and promote GCP TTS Standard as primary (quality drop on Indic, but $24/mo all-in within Vertex pool). |

### 3.3 Sarvam — Indic LLM (future)

| Item | Value |
|---|---|
| Role | Indic-tuned LLM. Currently NOT used (Vertex Gemini 2.5 Flash is asm-chat primary). Reserved for swap-in if Sarvam credit lands. |
| Sizing if active | 50k asm chats × 1.5k tokens = 75M tokens/mo |
| Pay-as-you-go cost | Sarvam-M ~$5/M tokens = ~$375/mo |
| Credit status | None pursued yet (Vertex Gemini fills the slot) |
| Cash exposure | $0 (not currently in dispatcher rotation) |
| Fallback role today | Reserved as fallback below Vertex Gemini for asm chat |
| **Status** | ✅ inactive; activate only if a Sarvam credit lands. Otherwise Vertex Gemini handles asm chat at ~$15/mo within Vertex pool. |

### 3.4 Groq — fast Llama inference (free tier)

| Item | Value |
|---|---|
| Role | Burst overflow for `english_rag_chat` when both Azure OpenAI and Vertex Gemini are throttled or for latency-critical "instant answer" UX. Llama-3.3-70B at >400 tok/s. |
| Tier at 10k DAU | Free tier: ~14.4k req/day = ~432k/mo |
| Sizing | At 10k DAU, expected overflow ~10k req/mo (rare path) |
| Credit | API key already in secrets (`GROQ_API_KEY`); free tier perpetual |
| Cash exposure | $0 |
| Fallback role | Tier-3 fallback for english chat (after Azure OpenAI, after Vertex Gemini) |
| **Status** | ✅ free tier vastly oversized for our overflow volume |

### 3.5 Cerebras — fast Llama inference (free tier alternative)

| Item | Value |
|---|---|
| Role | Same role as Groq (latency-critical chat overflow), kept as a *parallel* alternative so a Groq outage doesn't take down the fast-path tier. Llama-3.3-70B at ~2000 tok/s (faster than Groq). |
| Tier at 10k DAU | Free tier: 30 req/min, 14.4k req/day |
| Sizing | Same overflow volume; not exhausted |
| Credit | API key already in secrets (`CEREBRAS_API_KEY`) |
| Cash exposure | $0 |
| Fallback role | Tier-3 alternative beside Groq (dispatcher round-robins between them) |
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
| Upstash Redis | Cache + sessions (primary) | $0–8 | free tier OR demote to Tier-2 (see below) |
| Azure Cache for Redis Basic C0 | Cache fallback (Tier-2) | $16 | Azure credit pool (already counted in 10k-dau audit if promoted) |
| Momento Cache | Cache fallback (Tier-3) | $0 | free tier (5GB / 5M req/mo) |
| CF KV / Durable Objects | Cache last resort (Tier-4) | $0 | CF credit pool |
| Axiom | Long-term logs | $0 | free tier |
| Sentry | Error tracking | $0 | free tier |
| Deepgram | Primary STT | $0 today / $65 from M4 if no credit | ⚠️ **DEPENDS on $1k credit** |
| ElevenLabs | Primary TTS | $0 today / $500–3000 if no credit | ⚠️ **DEPENDS on $4k credit** |
| Sarvam | Reserved Indic LLM | $0 | inactive |
| Groq | Fast chat overflow | $0 | free tier |
| Cerebras | Fast chat overflow | $0 | free tier |
| Cartesia | TTS fallback | $0 | inactive (free credits standby) |
| Voyage | Embedding standby | $0 | inactive (free trial standby) |
| Cohere (via Bedrock) | Embed + rerank | $21 | AWS Activate (already counted) |
| Gemini direct key | Rollback only | $0 | rollback |
| Resend | Email fallback | $0 | free tier |
| GitHub | CI/CD | $0 | free tier |
| **Auxiliary subtotal (excluding Cohere already in AWS pool)** | | **$0/mo guaranteed cash + 2 credit dependencies** (Upstash overage absorbed by Azure Redis / Momento fallback chain) | |

### Combined with the four-cloud audit

| Layer | Monthly $ | Status |
|---|---:|---|
| Cloudflare | ~$20 | ✅ within $417 credit headroom |
| AWS | ~$75 | ✅ within $83 credit headroom (90% draw, mitigated) |
| Azure | ~$78 | ✅ within $208 credit headroom (37% draw) |
| Vertex | ~$150 | ✅ within $167 credit headroom (90% draw, mitigated) |
| Mongo + Pinecone + Axiom + Sentry + Groq/Cerebras/Cartesia/Voyage + Resend + GitHub | ~$0 | ✅ free tiers / Mongo $500 credit |
| Upstash Redis (with Azure Redis + Momento fallback chain) | **$0** | ✅ **cash eliminated** by demoting to Tier-2 within Azure credit pool, or promoting Momento free tier |
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
| 🟢 **Upstash Redis usage triples** (auth attacks, scraper bots) | Commands/mo exceeds 5M (overage > $10/mo) | Promote **Azure Cache for Redis Basic C0** to primary ($16/mo within Azure credit pool, full Redis semantics) and demote Upstash to Tier-2. Or promote **Momento Cache** (free 5GB / 5M req/mo). See §1.4–1.7 for the full chain. **No cash at any tier.** |

---

## 8. Final answer to the question

With the cache provider chain expanded to four tiers (Upstash → Azure
Cache for Redis → Momento → CF KV/DO), the **non-cloud auxiliary providers
add $0 of guaranteed cash spend** at 10k DAU. The previous Upstash $5–8/mo
exposure is now eliminated by demoting to Tier-2 inside the Azure credit
pool (which is only 37% drawn) the moment Upstash overage exceeds $10/mo.

Two providers (Deepgram, ElevenLabs) still have **outstanding credit
applications**. If both fail, documented fallback chains promote
CF Workers AI Whisper (STT) + GCP TTS Standard (TTS) at modest quality
cost and **zero additional cash**.

> **Net at 10k DAU: ~$323/mo all-in across four clouds + auxiliary
> chain. ~34-month combined runway. Zero guaranteed cash line items.
> Two PENDING credit dependencies, both with cash-free graceful-degrade
> fallbacks.**
