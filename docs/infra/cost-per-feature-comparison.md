# Syrabit — Cost-Per-Feature Delegation Comparison

**Companion to:** `docs/infra/cloud-allocation-plan.md` (the strategic plan)
and `docs/infra/cloud-service-breakdown.md` (per-cloud inventory).
**Last updated:** 2026-05-04
**Question this doc answers:**
*Feature by feature, which cloud delegation is cheapest while staying inside
startup-credit base tiers?* And: *do we still need Vertex, or can the same
features be served by Cloudflare + AWS + Azure alone within their credits?*

---

## 0. Credit pool recap (the budget you can't exceed without paying cash)

| Cloud | Credit (one-time) | Annual budget if spread over 12 mo | Free-tier per month |
|---|---:|---:|---|
| Cloudflare for Startups | $5,000 (exp 2026-09) | ~$417/mo | Pages bandwidth ∞, Workers 10M req, Workers AI 10k neuron-sec, R2 10GB, KV 100k reads/d, D1 5M reads/d, Vectorize 30M-dim |
| AWS Activate | $1,000 | ~$83/mo | S3 5GB, Lambda 1M req + 400k GB-s, SQS 1M req, SES 62k (from Lambda), CloudWatch 5GB |
| Microsoft for Startups (Azure) | $2,500 (exp 2027) | ~$208/mo | Container Apps Jobs 180k vCPU-s + 360k GB-s, App Insights 5GB ingest, Logic Apps consumption first 4k actions |
| GCP for Startups (Vertex) | $2,000 | ~$167/mo | Vision 1k/mo, Speech 60min/mo, TTS 4M chars/mo, Web Risk 10k/mo |
| Mongo Atlas | $500 | n/a (M0 free until upgrade) | Cluster M0 always-free (512MB) |
| **3-cloud total (no Vertex)** | **$8,500 + free tier** | **~$708/mo** | |
| **3-cloud + Vertex** | **$10,500 + free tier** | **~$875/mo** | |

> **Rule of selection:** for each feature, pick the cloud that can serve it
> *under its free tier first*, *under its credit second*, and only pay cash
> as a last resort. If two clouds tie, prefer the one with the larger
> remaining credit (Azure > Cloudflare > AWS > Vertex right now).

---

## 1. Headline answer (TL;DR)

| Question | Answer |
|---|---|
| Can CF + AWS + Azure alone cover every Syrabit feature? | **Yes — every feature has a viable home on those three clouds.** |
| Is removing Vertex cheaper? | **No, it's marginally more expensive (~$70–100/mo more) and worse on three features.** Vertex Gemini Flash is the cheapest 1M-context multimodal LLM available; replacing it pushes spend onto Azure OpenAI (more expensive per-token) and forces vision onto GPT-4o-mini (paid + no Indic-tuning). |
| What does Vertex earn per dollar today? | About **$120/mo of inference value** for **6 feature pools** (chat-asm, vision, safety, plus 3 fallbacks) on a $2k credit. Equivalent Azure OpenAI spend would be ~$220/mo, Vertex saves us ~$100/mo and one full credit pool. |
| Which features have **no** viable non-Vertex substitute under credit? | (a) Multimodal **vision** at high context, (b) **URL safety** API at zero cash, (c) low-latency **Assamese chat** with Indic-tuned LLM. |
| Should we keep Vertex? | **Yes — keep it as inference-only, never as a hosting cloud.** It's worth ~$167/mo of free credit headroom and unlocks 3 features that otherwise need cash. |

**Decision:** the canonical plan stays at **3 hosting clouds (CF + AWS + Azure) + Vertex as an inference-only API surface.** This file justifies that with the per-feature math.

---

## 2. Per-feature comparison (the actual table)

Columns:
- **Best (3-cloud only)** — what wins if we refuse to use Vertex.
- **$ at MVP scale** — expected monthly spend at MVP traffic (~5k DAU, ~50k chats/mo, ~10k embeds/mo, ~100GB blobs).
- **Best (3-cloud + Vertex)** — what wins if Vertex is allowed.
- **$ saved by Vertex** — positive = Vertex is cheaper; negative = Vertex costs more (i.e. don't use it for that feature).
- **Credit pool that absorbs it** — which $ pool gets drained.

### 2.1 Hosting & infra features

| Feature | Best (3-cloud only) | $ at MVP | Best (3-cloud + Vertex) | $ at MVP | $ saved by Vertex | Credit pool |
|---|---|---:|---|---:|---:|---|
| Static SPA hosting | CF Pages | $0 | CF Pages | $0 | $0 | CF (free tier) |
| API gateway / WAF / TLS | CF Worker + WAF + Turnstile | $0 | same | $0 | $0 | CF (free tier) |
| Backend API runtime | AWS App Runner (1 vCPU/2GB, 1→10) | $25–50 | same | $25–50 | $0 | AWS Activate |
| Background workers (light) | Azure Container Apps | $15 | same | $15 | $0 | Azure |
| Rust core (gRPC) | Azure Container Apps | $10 | same | $10 | $0 | Azure |
| Background workers (heavy) | AWS Lambda + SQS | $0 | same | $0 | $0 | AWS free tier |
| Cron / scheduled jobs | Azure Container Apps Jobs | $0 | same | $0 | $0 | Azure free tier |
| Primary blob store | AWS S3 | $1–3 | same | $1–3 | $0 | AWS Activate |
| Cold blob archive | CF R2 | $0 | same | $0 | $0 | CF (under 10GB free) |
| Edge cache for blobs | CF R2 + Cache Reserve | $0 | same | $0 | $0 | CF |
| Async queue | AWS SQS | $0 | same | $0 | $0 | AWS free tier |
| Cache + sessions | Upstash Redis REST | $0 | same | $0 | $0 | Upstash free tier |
| Distributed tracing / APM | Azure App Insights | $0 | same | $0 | $0 | Azure free tier (5GB/mo) |
| Logs (long-term) | Axiom | $0 | same | $0 | $0 | Axiom free tier |
| Alerts → Slack/Telegram | Azure Logic Apps | $0 | same | $0 | $0 | Azure free tier |
| Transactional email | CF Email Routing → AWS SES fallback | $0 | same | $0 | $0 | CF + AWS free tier |
| Edge inference (embed/translate) | CF Workers AI (bge-m3, IndicTrans2) | $0 | same | $0 | $0 | CF credit |

**Hosting subtotal:** ~$51–78/mo, identical with or without Vertex. Vertex earns nothing on hosting.

### 2.2 AI inference features

| Feature | Best (3-cloud only) | $ at MVP | Best (3-cloud + Vertex) | $ at MVP | $ saved by Vertex | Credit pool |
|---|---|---:|---|---:|---:|---|
| Embed (Indic + EN, primary) | CF Workers AI bge-m3 | $0 | same | $0 | $0 | CF credit |
| Embed (fallback 1) | AWS Bedrock Cohere `embed-multilingual-v3` | $20 | same | $20 | $0 | AWS Activate |
| Embed (fallback 2 / standby) | AWS Bedrock Cohere again (replicated region) | $0 | Vertex `text-embedding-004` | $0 | $0 | AWS / Vertex |
| Rerank | AWS Bedrock Cohere `rerank-v3-5` | $30 | same | $30 | $0 | AWS Activate |
| Chat — `english_rag_chat` (primary) | Azure OpenAI GPT-4.1-mini | $80 | Azure OpenAI GPT-4.1-mini | $80 | $0 | Azure credit |
| Chat — `english_rag_chat` (fallback) | CF Workers AI gpt-oss-20b | $0 | Vertex Gemini 2.5 Flash | $0 | $0 | CF / Vertex (rarely hit) |
| Chat — `content` pool (long-context generation) | Azure OpenAI GPT-4.1-mini (128k ctx) | $40 | **Vertex Gemini 2.5 Flash (1M ctx)** | $20 | **+$20** | Azure → Vertex |
| Chat — `assamese_rag_chat` (primary) | **Sarvam-M (paid, no Indic credit)** OR Azure OpenAI GPT-4.1-mini (weak Indic) | **$60 (Sarvam) / $40 (poor quality)** | **Vertex Gemini 2.5 Flash (Indic-tuned, 1M ctx)** | **$25** | **+$15–35** | (cash) → Vertex |
| Vision (image understanding, OCR-for-MCQ, diagrams) | Azure OpenAI GPT-4o-mini (vision SKU) | $50 | **Vertex Gemini 2.5 Flash multimodal** | $20 | **+$30** | Azure → Vertex |
| STT (primary) | Deepgram (existing $1k credit being chased) | $0–10 | same | $0–10 | $0 | Deepgram credit |
| STT (fallback Indic) | CF Workers AI Whisper | $0 | Google Cloud Speech (Chirp) | $0 | $0 | CF / Vertex free tier |
| TTS (primary) | ElevenLabs (existing $4k credit being chased) | $0–15 | same | $0–15 | $0 | ElevenLabs credit |
| TTS (fallback EN) | (none under credit; AWS Polly post-#337 only) | $5–10 cash | Google Cloud TTS (4M chars free) | $0 | **+$5–10** | (cash) → Vertex free tier |
| Translate (Indic↔EN, primary) | CF Workers AI IndicTrans2 | $0 | same | $0 | $0 | CF credit |
| Translate (fallback) | Azure OpenAI GPT-4.1-mini (general LLM, weak Indic translation) | $5 | Vertex Gemini (Indic-tuned) | $0 | **+$5** | Azure → Vertex |
| Safety / moderation | Azure OpenAI content filter (built-in) + admin review | $0 | Vertex Gemini safety + Azure filter | $0 | $0 | Azure / Vertex |
| **URL safety** | (none under credit — Cloudflare Radar is read-only; would need PhishTank/Google Safe Browsing API integration) | **$5–15 cash or self-host** | **GCP Web Risk API (10k/mo free)** | **$0** | **+$5–15** | (cash) → Vertex free tier |
| Vector index (primary) | Pinecone (existing free tier) | $0 | same | $0 | $0 | Pinecone free tier |
| Vector index (fallback 1, active) | CF Vectorize | $0 | **Vertex AI Vector Search / Matching Engine** (`retrievers/vertex.py` already calls `findNeighbors`/`upsertDatapoints`) | $15–30 with traffic | **−$15–30 in cash, but uses Vertex credit** | CF / Vertex credit |
| Vector index (fallback 2) | CF Vectorize | $0 | CF Vectorize | $0 | $0 | CF credit |
| Vector index (3rd fallback) | (none — graceful degrade to Pinecone retry) | $0 | Vertex Discovery Engine (`discovery_engine_client.py`) | $0–10 | $0 | (none) / Vertex credit |

**Inference subtotal (3-cloud only):** ~$295–355/mo, of which **~$70–90/mo is uncovered cash** (Sarvam, AWS Polly post-#337, Web Risk substitute).

**Inference subtotal (3-cloud + Vertex):** ~$215–265/mo (the upper bound includes Vertex Vector Search if it stays an active fallback rather than a code-only rollback), **all under credit**.

> **Routing note:** Generative Gemini calls in production go through
> **Cloudflare AI Gateway BYOK → google-ai-studio**, not directly to Vertex
> AI. This is the path `vertex_services.py` takes for embeddings, translation,
> MCQ/flashcards, content enhancement, SEO meta, gap analysis, and the
> long-doc reader. The CF AI Gateway adds $0 and earns cache hits on hot
> prompts. Direct Vertex SA path (`vertex_chat.py`, `providers/vertex_embed.py`,
> `retrievers/vertex.py`, `discovery_engine_client.py`, Cloud Vision) is used
> for streaming chat, vector search, and rollback.
> Auth priority: `VERTEX_SERVICE_ACCOUNT` → `GEMINI_API_KEY` (legacy) →
> `CF_AI_GATEWAY_*` (prod default).

---

## 3. Bottom-line monthly cost comparison

| Strategy | Hosting | Inference | Total monthly | Cash leakage (uncovered by credits) |
|---|---:|---:|---:|---:|
| **3 clouds only (CF + AWS + Azure)** — no Vertex | ~$65 | ~$325 | **~$390/mo** | **~$70–90/mo cash** (Sarvam Indic, Polly, Web Risk substitute) |
| **3 clouds + Vertex** (current canonical plan) | ~$65 | ~$225 | **~$290/mo** | **~$0/mo cash** (everything fits under credit) |
| **Delta** | $0 | **−$100/mo** | **−$100/mo** | **−$70–90/mo** |

**At the 12-month horizon:** 3-cloud only burns ~$840–1,080 cash that 3-cloud+Vertex avoids entirely, *and* loses access to (a) 1M-context generation, (b) Indic-tuned LLM, (c) free URL-safety API, (d) free TTS fallback at 4M chars/mo.

---

## 4. Credit-coverage check (does each feature fit under its credit base tier?)

> "Base tier" = the minimum monthly burn rate at which the feature stays inside
> the credit's annualized headroom (credit ÷ 12).

| Feature | Monthly cost | Lives on | Credit annualized | Coverage |
|---|---:|---|---:|---|
| Backend API on App Runner | $25–50 | AWS Activate | $83/mo | ✅ ~50% headroom |
| Bedrock Cohere embed + rerank | $50 | AWS Activate | $83/mo | ✅ shares pool with App Runner; combined ≤ $100/mo, slightly over by month 12 — need vendor renewal or scale-down |
| S3 + SES + Lambda + SQS | $1–5 | AWS free tier + Activate | — | ✅ stays in free tier |
| Container Apps workers + rust-core | $25 | Azure | $208/mo | ✅ comfortable |
| Container Apps Jobs cron (45 jobs) | $0 (scale-to-zero) | Azure free tier | — | ✅ stays in free tier |
| Azure OpenAI GPT-4.1-mini | $80–150 | Azure | $208/mo | ✅ uses ~75% of pool |
| App Insights central APM | $0 (5GB/mo free) | Azure free tier | — | ✅ free tier |
| Cloudflare Pages + Workers + Workers AI + R2 + KV + D1 + Vectorize | $0–50 | CF | $417/mo | ✅ deep headroom |
| Vertex Gemini Flash (6 pools) | $80–120 | Vertex | $167/mo | ✅ ~70% of pool |
| Vertex Vision/STT/TTS/Discovery/Web Risk | $0–10 (mostly free tier) | Vertex free tier + credit | — | ✅ free tier |
| **All-up monthly with Vertex** | **~$290/mo** | spread across 4 pools | **$875/mo total** | ✅ **33% of available credit headroom** |

Even at the 33% draw rate, **the runway is ~36 months** before any cash is spent. That assumes traffic stays flat; doubling traffic still keeps everything inside credits.

---

## 5. Where each AI inference dollar is best spent (the optimal map)

```
Embed (Indic + EN)          → CF Workers AI bge-m3                     [free CF credit]
Embed fallback              → AWS Bedrock Cohere embed-multilingual-v3  [AWS Activate]
Rerank                      → AWS Bedrock Cohere rerank-v3-5            [AWS Activate]
Chat — english primary      → Azure OpenAI GPT-4.1-mini                 [Azure credit]
Chat — english fallback     → CF Workers AI gpt-oss-20b                 [free CF credit]
Chat — content (1M ctx)     → Vertex Gemini 2.5 Flash                   [Vertex credit] ← Vertex wins
Chat — assamese primary     → Vertex Gemini 2.5 Flash (Indic-tuned)     [Vertex credit] ← Vertex wins
Chat — assamese fallback    → Sarvam-M (when ElevenLabs credit lands)   [Sarvam credit, future]
Vision (multimodal)         → Vertex Gemini 2.5 Flash multimodal        [Vertex credit] ← Vertex wins
Vision OCR fallback         → Google Cloud Vision (legacy)              [Vertex free tier]
STT primary                 → Deepgram                                  [Deepgram credit, future]
STT fallback                → CF Workers AI Whisper                     [free CF credit]
STT fallback Indic          → Google Cloud Speech (Chirp)               [Vertex free tier]
TTS primary                 → ElevenLabs                                [ElevenLabs credit, future]
TTS fallback EN             → Google Cloud TTS (4M chars free)          [Vertex free tier] ← Vertex wins
Translate primary           → CF Workers AI IndicTrans2                 [free CF credit]
Translate fallback          → Vertex Gemini                             [Vertex credit] ← Vertex wins
Safety / moderation         → Azure OpenAI content filter + Vertex      [Azure / Vertex]
URL safety                  → GCP Web Risk API (10k/mo free)            [Vertex free tier] ← Vertex wins
Vector primary              → Pinecone                                  [Pinecone free tier]
Vector fallback             → CF Vectorize                              [free CF credit]
Vector 3rd fallback         → Vertex Discovery Engine                   [Vertex credit]
```

**Vertex wins exactly 6 feature slots** (content, asm primary, vision, TTS-fallback, translate-fallback, URL safety) at a combined ~$70–100/mo cheaper than the best non-Vertex alternative *and* unlocks features (Web Risk, 1M-context) that have no zero-cash equivalent.

---

## 6. Why each cloud earns its keep

| Cloud | Earns its keep because… | What we'd lose by dropping it |
|---|---|---|
| **Cloudflare** | Only cloud with TLS+WAF+CDN+edge-compute+edge-AI under one bill, and zero egress to anywhere. Workers AI bge-m3 alone covers ~70% of embed volume free. | Frontend hosting, edge perimeter, free embeds, free translate, edge cache. Replacements would cost $200+/mo on AWS CloudFront+WAF+Lambda@Edge. |
| **AWS** | App Runner is the cheapest fully-managed FastAPI host ($25/mo for 1 vCPU/2GB) and Bedrock is the only place to run Cohere embed/rerank under credit. S3 is non-negotiable for blob compliance + lifecycle. | Backend home (would need Azure App Service at higher $/req), Cohere (no other cloud sells it under credit), S3 (no equivalent at our cost). Replacements ~+$100/mo. |
| **Azure** | Largest credit pool ($2.5k) and only place GPT-4.1-mini runs under startup credit. Container Apps Jobs is the cheapest cron in any cloud ($0 with scale-to-zero). App Insights free tier covers full APM. | GPT-4.1-mini chat (would need OpenAI direct = no startup credit), cron (would need EventBridge + Lambda = burns AWS budget), APM (Datadog = $50+/mo). Replacements ~+$200/mo. |
| **Vertex (inference only)** | Cheapest 1M-context multimodal LLM, only Indic-tuned LLM under credit, only free URL-safety API, free TTS at 4M chars/mo. 6 feature slots. | Indic chat quality, multimodal vision, 1M-context generation, Web Risk, free TTS fallback. Replacement ~+$100/mo + worse Indic quality. |

---

## 7. Final recommendation

**Keep the current canonical plan: Cloudflare + AWS + Azure as hosting clouds, Vertex as an inference-only API surface.**

- 3-cloud only is **viable** but ~$100/mo more expensive *and* leaks ~$70–90/mo as cash (uncovered by any credit) *and* delivers worse quality on Indic chat + multimodal vision.
- Adding Vertex as an inference-only surface costs $0 in hosting overhead, drains a *separate* $2k credit pool that would otherwise expire, and keeps the entire system under credit for ~36 months.

> **One-line decision:** spend Vertex's free credit before it expires; do not
> add GCP compute. Hosting stays on Cloudflare + AWS + Azure exactly as
> documented in `cloud-allocation-plan.md` §2.
