# Syrabit — 10k DAU Cost Audit (Four-Cloud Delegation)

**Companion to:** `docs/infra/cloud-allocation-plan.md` (the canonical plan)
and `docs/infra/cost-per-feature-comparison.md` (the MVP-scale baseline).
**Last updated:** 2026-05-04
**Question this doc answers:**
*At 10k DAU traffic, what is the minimum-cost delegation across our four
providers (Cloudflare + AWS + Azure + Vertex) such that **each provider's
monthly burn is fully covered by its own startup-credit annualized
headroom**?*

> Hosting clouds = CF + AWS + Azure (three). Vertex = inference-only API
> surface (fourth provider, not a hosting cloud). The plan is "four
> providers, three hosting clouds" — exactly as
> `cloud-allocation-plan.md` §6 spells out.

---

## 0. Credit headroom budget (the hard ceiling per provider)

| Provider | Credit (one-time) | Annualized headroom | This audit's monthly target |
|---|---:|---:|---:|
| Cloudflare for Startups | $5,000 | $417/mo | ≤ $200/mo (50% draw) |
| AWS Activate | $1,000 | $83/mo | ≤ $80/mo (96% draw — tightest) |
| Microsoft for Startups (Azure) | $2,500 | $208/mo | ≤ $180/mo (87% draw) |
| GCP for Startups (Vertex) | $2,000 | $167/mo | ≤ $150/mo (90% draw) |
| Mongo Atlas | $500 | n/a (M0 free) | $0 |
| **Combined** | **$10,500** | **$917/mo** | **≤ $610/mo total** |

> **Hardest constraint:** AWS Activate. $1k credit divided over 12 months
> gives only $83/mo. App Runner alone, sized naively, would exhaust this
> pool by month 7. The plan below sizes around it.

---

## 1. 10k DAU traffic model (assumptions used everywhere below)

| Quantity | Value | Derivation |
|---|---:|---|
| Daily active users | 10,000 | input |
| Monthly active users | ~30,000 | DAU × 3 stickiness |
| Sessions / user / month | 30 | typical EdTech engagement |
| Total sessions / mo | 300k | |
| Page views / mo | ~3M | 10 PV/session |
| Backend API requests / mo | ~10M | 33 API calls/session |
| Chat completions / mo | 100k | english + asm + content pools combined |
| Embed calls / mo | 20k | course-content + query embeds |
| MCQ + flashcard gens / mo | 50k | per `vertex_services.py` |
| Translation calls / mo | 30k | EN↔Indic, mostly cached |
| Vision (OCR + multimodal) / mo | 5k | photo-MCQ + diagram |
| STT minutes / mo | 15k | mostly cached lecture audio |
| TTS chars / mo | 10M | Read-Aloud + voice tutor |
| URL safety checks / mo | 5k | user-pasted external links |
| Vector search queries / mo | 200k | RAG retriever |
| Blob storage (S3 + R2) | 200 GB | growing ~50 GB/mo |
| Egress from backend | ~500 GB/mo | mostly to CF (free egress to CF Workers) |
| Logs ingest / mo | ~10 GB | mostly to App Insights free tier |

---

## 2. Per-provider per-feature cost minimization

### 2.1 Cloudflare — frontend + edge + perimeter

| Feature | Service | Sizing assumption | Monthly $ |
|---|---|---|---:|
| Static SPA hosting | Pages | unlimited bandwidth | **$0** (free) |
| API gateway / WAF | Workers + WAF | 10M req covered by 10M free tier | **$0** (free) |
| Turnstile bot/captcha | Turnstile | unlimited free | **$0** (free) |
| Edge embed (Indic+EN, primary) | Workers AI bge-m3 | 20k embeds × ~0.5 neuron-s = 10k neuron-s ⇐ 300k/mo free | **$0** (free) |
| Edge translate (primary) | Workers AI IndicTrans2 | 30k calls × ~3 neuron-s = 90k neuron-s ⇐ in free tier with margin | **$0** (free) |
| Edge chat last-resort | Workers AI gpt-oss-20b | rarely hit (only when Azure + Vertex both 5xx) | **$0–10** |
| Edge STT fallback | Workers AI Whisper | only when Deepgram throttled | **$0–5** |
| Cold blob archive | R2 | 200 GB × $0.015 (above 10 GB free) | **$2.85** |
| Cache + sessions (edge) | KV | 10k DAU × ~10 reads/d ≈ 100k reads/d ≈ at free-tier ceiling | **$0–5** |
| Edge metadata DB | D1 | well within 5M reads/d free | **$0** |
| Vector backup index | Vectorize | small backup index ⇐ free tier | **$0** |
| AI Gateway (Gemini BYOK) | AI Gateway | adds $0; earns cache hits | **$0** |
| Transactional email front | Email Routing | free | **$0** |
| **Cloudflare subtotal** | | | **~$10–30/mo** |
| **Headroom check** | $417/mo cap | **2–7% draw** | ✅ comfortable |

### 2.2 AWS — backend canonical origin + storage + email + async + Cohere

> **Sizing rule for App Runner:** at 10k DAU we run **0.25 vCPU / 0.5 GB
> minimum, autoscale to 4 instances**. App Runner does not scale to zero,
> so the floor matters. The 0.25/0.5 SKU is the cheapest SKU.

| Feature | Service | Sizing assumption | Monthly $ |
|---|---|---|---:|
| Backend canonical API | App Runner (0.25 vCPU/0.5 GB, min 1 → max 4) | avg 1.3 instances active; provisioned $0.007/vCPU-hr × 0.25 vCPU × 720 hr × 1.3 = $1.64; active 30% utilization $0.064 × 0.25 × 720 × 0.3 = $3.46 + memory $0.008 × 0.5 × 720 × 1.3 = $3.74 | **~$25** |
| Object storage (primary) | S3 Standard | 200 GB × $0.023 + req ~$1 | **~$5.60** |
| S3 → IA lifecycle | S3 IA | older content at $0.0125/GB | included above |
| Transactional email | SES (Lambda tier-3) | 62k free + 138k overflow × $0.10/1k | **~$13.80** |
| Async queue | SQS | 1M free + 9M × $0.40/M | **~$3.60** |
| Async workers | Lambda | 1M req free + 4M × $0.20/M; 400k GB-s free + ~600k × $0.0000167 | **~$2.80** |
| Cohere embed (fallback) | Bedrock `embed-multilingual-v3` | only when CF Workers AI bge-m3 exhausted; ~5M tokens × $0.10/M | **~$0.50** |
| Cohere rerank | Bedrock `rerank-v3-5` | 10k searches × $2/1k | **~$20** ⚠️ |
| Logs (AWS-native) | CloudWatch | 5 GB free + 5 GB × $0.50/GB | **~$2.50** |
| Secrets | Secrets Manager | 5 secrets × $0.40 | **~$2** |
| **AWS subtotal** | | | **~$75/mo** |
| **Headroom check** | $83/mo cap | **90% draw** | ✅ fits — but tight |

⚠️ **Cohere rerank is the swing item.** If we let rerank fire on every
RAG query (200k/mo) instead of throttling to top-K candidates, cost
balloons to $400/mo and breaks AWS's pool single-handedly. The dispatcher
must keep rerank at ≤ 10k calls/mo (cache the rest). This is the single
most important AWS guardrail at 10k DAU.

### 2.3 Azure — workers + Rust core + cron + Azure OpenAI + central APM

| Feature | Service | Sizing assumption | Monthly $ |
|---|---|---|---:|
| Rust core (gRPC) | Container Apps (0.5 vCPU/1 GB, always-on) | 0.5 × 86400 × 30 × $0.000024/vCPU-s = $31.10; mem 1 × 86400 × 30 × $0.000003/GB-s = $7.78 | **~$39** |
| Background workers (light) | Container Apps (scale-to-zero, 0.25/0.5) | avg 0.3 active fraction; ~$12 | **~$12** |
| Background workers (heavy) | AWS Lambda + SQS (NOT here) | runs on AWS pool | $0 (on AWS) |
| Cron (45 jobs) | Container Apps Jobs | 45 jobs/d × ~30 s = ~40k vCPU-s/mo ⇐ 180k/mo free | **$0** (free) |
| Logic Apps (alerts) | Consumption | 6k actions × $0.000025 | **~$0.15** |
| Azure OpenAI — `english_rag_chat` (primary) | GPT-4.1-mini | 50k completions × ~1k tokens (70% in / 30% out) = 35M in × $0.15/M + 15M out × $0.60/M | **~$14.25** |
| Azure OpenAI — `content` pool fallback | GPT-4.1-mini | only when Vertex Gemini overflow; 10k × 1.5k tokens | **~$5** |
| Central APM | App Insights | 5 GB free + 3 GB × $2.30/GB | **~$6.90** |
| Key Vault | Key Vault | 50k ops × $0.03/10k | **~$0.15** |
| Failover routing | Traffic Manager (Azure) | 1M DNS queries × $0.54/M | **~$0.54** |
| Front Door standby (tier-3 failover) | **DROPPED** at 10k DAU — Traffic Manager + App Runner failover suffices | — | $0 |
| **Azure subtotal** | | | **~$78/mo** |
| **Headroom check** | $208/mo cap | **38% draw** | ✅ deep headroom |

### 2.4 Vertex — inference-only via four API surfaces

| Feature | Service | Sizing assumption | Monthly $ |
|---|---|---|---:|
| Generative umbrella (`vertex_services.py`) via CF AI Gateway BYOK | Gemini 2.5 Flash | 105k calls × ~3k tokens (2k in/1k out) = 210M in × $0.075/M + 105M out × $0.30/M; CF Gateway cache hits assumed 30% | **~$33.07** (after cache) |
| Asm chat | Gemini 2.5 Flash | 50k chats × ~1.5k tokens, mixed in/out | **~$15** |
| Long-context content (1M ctx pool) | Gemini 2.5 Flash | 5k long-doc passes × ~50k tokens | **~$22.50** |
| Multimodal vision | Gemini 2.5 Flash multimodal | 5k images × ~$0.003 / image | **~$15** |
| Vector Search (Matching Engine) | `retrievers/vertex.py` | small Index Endpoint, 200k queries × $0.0001 + node-hour $0.094/hr × 720 (e2-standard-2 minimum) | **~$30** ⚠️ |
| Discovery Engine | `discovery_engine_client.py` | 10k searches × $4/1k | **~$5** |
| Cloud Vision OCR | Cloud Vision API | 1k free + 4k × $1.50/k | **~$6** |
| Cloud Speech-to-Text fallback | Chirp | rarely hit | **$0–2** |
| Cloud TTS Standard | Cloud TTS | 4M chars free + 6M × $4/M | **~$24** |
| Web Risk | Web Risk API | 10k/mo free; 5k usage | **$0** |
| **Vertex subtotal** | | | **~$150/mo** |
| **Headroom check** | $167/mo cap | **90% draw** | ✅ fits — but tight |

⚠️ **Vector Search node-hour is the swing item.** The Index Endpoint runs
on a min-1 e2-standard-2 node 24/7 ($30/mo even at zero queries). If we
keep Pinecone as primary and Vertex Vector Search as a *cold* code-only
fallback (no live endpoint), drops to **$0** and Vertex subtotal drops to
$120/mo (72% draw). The dispatcher decides; both options fit credit.

---

## 3. All-up monthly burn at 10k DAU

| Provider | Monthly | Annualized | Credit | Coverage |
|---|---:|---:|---:|---:|
| Cloudflare | ~$20 | $240 | $5,000 | 4.8% |
| AWS | ~$75 | $900 | $1,000 | 90% ⚠️ |
| Azure | ~$78 | $936 | $2,500 | 37% |
| Vertex | ~$150 | $1,800 | $2,000 | 90% ⚠️ |
| Mongo | $0 (M0) | $0 | $500 | 0% |
| **Total** | **~$323/mo** | **$3,876/yr** | **$11,000** | **35%** |

**Verdict:** ✅ at 10k DAU the four-cloud plan stays inside every credit
pool individually, with combined runway of **~34 months** before any
cash is spent.

---

## 4. The two tight spots — what to watch

### 4.1 AWS Activate — 90% draw

The single largest items inside AWS are **App Runner ($25)** and **Cohere
rerank ($20)**. Together they're 60% of the AWS pool. Mitigations in
priority order if AWS starts overspending:

1. **Cap rerank to ≤ 10k calls/mo** (already in the plan; enforced by
   the retriever cache layer). Going from 10k → 50k calls = +$80/mo and
   instantly breaks the AWS pool.
2. **Keep App Runner at the smallest SKU (0.25 vCPU / 0.5 GB).**
   Upgrading to 1 vCPU / 2 GB at min=1 = +$70/mo.
3. **SES tier-3 path through Lambda** (already required): keeps the
   62k/mo free tier. Sending from EC2/App Runner directly = +$13/mo.
4. **Last resort:** burst overflow chat traffic to Azure OpenAI
   (`english_rag_chat` already has Azure as primary; use it more) and let
   App Runner auto-scale less aggressively.

### 4.2 Vertex — 90% draw

The single largest items inside Vertex are **Cloud TTS ($24)**, **Vector
Search node-hour ($30 if active)**, and **`vertex_services.py` umbrella
($33)**. Mitigations:

1. **Keep Vector Search code-only / cold-endpoint** — saves $30/mo, drops
   Vertex draw to 72%. Pinecone stays primary; Vertex Vector Search
   activates only on Pinecone outage.
2. **Cap TTS at 8M chars/mo** by aggressively caching Read-Aloud audio
   in R2 (already a planned optimization).
3. **Rely on CF AI Gateway cache** — every cached generative call is
   $0 to Vertex. Target ≥ 30% cache hit rate on `vertex_services.py`.
4. **Move overflow chat back to Azure OpenAI.** Plenty of headroom in
   Azure's pool (62% unused).

### 4.3 Cross-pool rebalancing — the safety valve

At 10k DAU, **Cloudflare (95% unused)** and **Azure (62% unused)** are
the two underloaded pools. If AWS or Vertex pool depletes early, the
dispatcher can shift work toward CF and Azure:

| If overloaded… | Shift to… | Mechanism |
|---|---|---|
| AWS rerank > $20/mo | CF Workers AI rerank (when available) | dispatcher demote Bedrock |
| AWS App Runner > $30/mo | Azure Container Apps backend mirror | Traffic Manager weighted routing |
| Vertex Gemini > $80/mo | Azure OpenAI GPT-4.1-mini | dispatcher promote Azure |
| Vertex TTS > $30/mo | ElevenLabs (when credit lands) or self-host Coqui on Azure | dispatcher demote GCP TTS |

---

## 5. The optimum 10k DAU map (one-screen summary)

```
Frontend + edge + perimeter   → Cloudflare              [$20/mo,   5% of CF pool]
Backend canonical API         → AWS App Runner           [$25/mo,  30% of AWS pool]
Object storage primary        → AWS S3                   [$6/mo,    7% of AWS pool]
Transactional email           → AWS SES (via Lambda)     [$14/mo,  17% of AWS pool]
Async queue + heavy workers   → AWS SQS + Lambda         [$6/mo,    7% of AWS pool]
Embed + rerank under credit   → AWS Bedrock Cohere       [$21/mo,  25% of AWS pool]
Logs (AWS-native)             → CloudWatch               [$3/mo,    4% of AWS pool]
Workers (light) + Rust core   → Azure Container Apps     [$51/mo,  25% of Azure pool]
Cron (45 jobs)                → Azure Container Apps Jobs [$0,     0% of Azure pool]
Chat english primary          → Azure OpenAI GPT-4.1     [$19/mo,   9% of Azure pool]
Central APM                   → Azure App Insights       [$7/mo,    3% of Azure pool]
Generative umbrella           → CF AI Gateway → Gemini   [$33/mo,  20% of Vertex pool]
Asm chat primary              → Vertex Gemini direct     [$15/mo,   9% of Vertex pool]
Content (1M ctx)              → Vertex Gemini direct     [$22/mo,  13% of Vertex pool]
Vision multimodal             → Vertex Gemini multimodal [$15/mo,   9% of Vertex pool]
Vector search active fallback → Vertex Matching Engine   [$30/mo*, 18% of Vertex pool, optional]
Discovery + Vision OCR        → Vertex direct            [$11/mo,   7% of Vertex pool]
TTS fallback                  → GCP TTS Standard         [$24/mo,  14% of Vertex pool]
URL safety                    → Vertex Web Risk          [$0,       0% of Vertex pool]
Primary DB                    → Mongo Atlas M0           [$0,       free tier]

*Vector Search node-hour is optional; default plan keeps Pinecone primary.
```

**Total monthly: ~$323. Each provider individually fits its own credit
headroom. Combined runway: ~34 months at constant traffic.**

---

## 6. What changes if traffic grows past 10k DAU

| DAU | Estimated total $/mo | Pool that breaks first | Action |
|---:|---:|---|---|
| 10k | $323 | none | (this audit) |
| 25k | ~$700 | AWS (rerank explodes) | scale rerank cache; consider Cohere bulk pricing |
| 50k | ~$1,400 | AWS + Vertex | App Runner upgrade to 0.5 vCPU; renew Vertex credit or hop AWS Bedrock embed |
| 100k | ~$2,600 | all four | Series-A territory; renegotiate credits or move to paid plans |

> **At 100k DAU we exit credit-funded mode regardless of plan.** This audit
> is engineered for the 10k–25k DAU window.

---

## 7. Final answer to the question

**Yes — at 10k DAU, the four-provider delegation (Cloudflare hosting +
AWS hosting + Azure hosting + Vertex inference-only) keeps every
provider's monthly burn inside its own startup-credit annualized
headroom**, with the two tightest pools (AWS at 90%, Vertex at 90%)
explicitly mitigated by:

- enforcing the rerank cap (AWS),
- keeping App Runner on the smallest SKU (AWS),
- routing email through Lambda for SES tier-3 (AWS),
- keeping Vector Search cold-endpoint (Vertex),
- caching TTS aggressively in R2 (Vertex),
- exploiting CF AI Gateway cache hits (Vertex).

The plan delivers **~34 months of runway** at $323/mo combined burn,
versus $11,000 of total available credit. No cash spend at any provider.
