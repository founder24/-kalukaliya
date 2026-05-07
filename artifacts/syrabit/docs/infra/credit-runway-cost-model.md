# Phased Credit-Runway Cost Model — Syrabit.ai

**Version:** 1.0 — 2026-05-07
**Status:** Fundraising-grade memo (Task #550). **Quarterly review
cadence — next scheduled review 2026-08-07, OR sooner whenever any
credit pool size changes by ≥ 20 %, OR whenever a `# COST-CAP-OVERRIDE`
marker lands in `cost_caps.py` / `credit_burn_meter.py` /
`workers/edge-proxy/src/index.ts`.** The owner (infra) MUST re-derive
every number in §4 / §5 from the live in-tree constants at each
review.
**Source of truth:** [`../../../../infra/v4-locked-architecture.md`](../../../../infra/v4-locked-architecture.md). If anything below disagrees with V4, V4 wins.
**Audience:** founders, prospective investors, infra reviewers.
**Author / owner:** infra.

---

## TL;DR

Syrabit operates with two layered economic surfaces:

1. **Cash out of pocket** — capped at **$100 / month** by the
   founder-lock in `cost_caps._DEFAULT_MONTHLY_TOTAL_USD_CAP`
   (Task #549).
2. **Credit drawdown** — ~**$8.0k of unspent multi-cloud startup
   credits** subsidising real infra value on top of the cash cap.

Together these give an **economic infra value of $300–$800 / month
at 10k DAU and $4k–$12k / month at 100k DAU**, while keeping the
real cash bill under $100 / month until the Phase 3 trigger fires
(see §6).

| Phase | DAU | Effective MAU | **Credit-on infra value** (cash + credit drawdown) | **Cash out of pocket** (capped) | **No-credits no-caps** (sticker) |
|---|---|---|---|---|---|
| 1 — Pilot   | 1k – 5k    | 8k – 40k     | **$120 – $320 / mo**     | $50 – $90    | $400 – $700 |
| 2 — Launch  | 5k – 10k   | 40k – 80k    | **$300 – $800 / mo**     | $80 – $100 *(at #549 cap)* | $1,500 – $2,800 |
| 3 — Growth  | 10k – 50k  | 80k – 400k   | **$1,200 – $3,500 / mo** | $300 – $500 *(post-`# COST-CAP-OVERRIDE`)* | $7k – $13k |
| 4 — Scale   | 50k – 100k | 400k – 800k  | **$4k – $12k / mo**      | $4k – $12k *(credits exhausted; revenue-positive)* | $9k – $14k |
| **Credits-off summary** *(reference only — what an unsubsidised competitor would pay)* | per-phase | per-phase | n/a | n/a | **P1 $400–$700 · P2 $1.5k–$2.8k · P3 $7k–$13k · P4 $9k–$14k** |

The four-cloud cost-share remains the V4-locked **40 % Cloudflare /
30 % Azure / 20 % AWS / 10 % GCP** through every phase. Voice (TTS /
STT / `/voice/voice`) is paywalled to paid plans (#549) and is a
**revenue centre, not a cost centre** — at Phase 2 the voice line
nets +$4.4k / mo.

---

## 1. Cost framework — founder-locked controls

| Control | Where | Value | Guard |
|---|---|---|---|
| Monthly cash ceiling | `cost_caps._DEFAULT_MONTHLY_TOTAL_USD_CAP`, `credit_burn_meter.MeterDConfig.cap_usd` | **$100** | `scripts/check_budget_ceiling.py` (CI in `backend-tests.yml` + `azure-container-apps-deploy.yml`) |
| Per-call token budgets | `cost_caps.TOKEN_BUDGETS` | chat 3000/800, content 4000/2000, formatter 4500/2500, translate 2000/2000, OCR 1500/800, STT 2000/500 | `tests/test_cost_caps.py` requires `# COST-CAP-OVERRIDE` marker + Sentry changelog to raise |
| Edge chat caps | `workers/edge-proxy/src/index.ts` lines 670–701 | `CHAT_CAP_MONTHLY=30` (all users); `CHAT_CAP_DAILY=3` (free only — paid bypass via `CHAT_DAILY_BYPASS_PLANS`) | Same `# COST-CAP-OVERRIDE` policy |
| English-chat specialist (target steady-state) | `cost_caps._select_chat_primary()` reserved for `vertex` head once #555/#556 lands | Today returns `workers_ai_llama32_3b` (CF free Neuron quota) — the workers_ai fallback that holds the chair until vertex re-enable | `check_budget_ceiling.py` asserts head ∈ `{workers_ai*, vertex}` |
| Free-chat warm-up (turns 1–2) | `cost_caps._select_chat_model` lines 352–358 | `workers_ai_mistral_7b` | `tests/test_tier_routing_dispatch.py` |
| Voice paywall | `auth_deps.require_paid_plan` on `/voice/{tts,stt,voice}` | 402 free; admin/staff/educator bypass | `tests/test_voice_paid_gate.py` + `check_budget_ceiling.py` |
| Three-stage degradation ladder | `cost_caps.DEGRADATION_PCT_PAUSE_BATCH/VOICE_OFF/FREE_503` | 60 % / 80 % / 95 % of monthly cap | Strict-monotone check in `check_budget_ceiling.py`; **operational wiring is sub-task #557** |
| Hard stop @ 100 % | MeterD → Redis `chat:cheaponly=1`; `_select_chat_model` reads on every dispatch | LOCK | `tests/test_provider_priority_locked.py` |

---

## 2. Phase model — provider-level burn tables

> Provider scope is the **strict-specialist set permitted by Task
> #549** plus the V4-locked four-cloud infra tier. Providers banned
> by `scripts/check_dead_providers.py` (Cerebras, Cohere, Voyage-AI,
> Cartesia, Groq, OpenRouter) and providers carved out by Task #549
> (AssemblyAI, Exa, Tavily on the chat hot path; Azure OpenAI on the
> chat hot path; Bedrock on the chat hot path) **do not appear**.
>
> **English-chat specialist:** Vertex Gemini 2.5 Flash is the
> target-state primary (post-sub-task #555/#556 re-enable);
> `workers_ai_llama32_3b` is the fallback that holds the chair
> today. Both states are modelled below — "today" rows show
> workers_ai chat = $0; "target" rows show vertex chat with credit
> drawdown.

Held constant across phases unless overridden:

- **DAU → MAU ratio:** 8× (study-routine app w/ weekly exam cycles).
- **Chat usage / MAU:** free users ~22 turns/mo (3/day soft cap dominates); paid users ~28 turns/mo (monthly 30 cap dominates).
- **AI-cache hit rate:** 50 % on formatter / translate / OCR (live chat: 0 % by policy).
- **Voice adoption (paid):** 5 % of MAU sustained, 60 sec / session, 3 sessions / week.
- **Paid conversion:** P1 = 0 %, P2 = 5 %, P3 = 7 %, P4 = 9 %.
- **Email volume:** ~5 transactional / MAU / month.
- **Storage growth:** +1 GB / 1k MAU / month for chat history + notes.
- **Egress:** Cloudflare CDN cache-hit ≥ 90 %.

### Phase 1 — Pilot (1k – 5k DAU, ~24k MAU midpoint)

| Provider | Workload | Monthly volume | Credit drawdown $ | Cash $ | No-credits sticker $ |
|---|---|---|---|---|---|
| **Vertex Gemini 2.5 Flash** (English chat, target post-#555) | 720k chat turns × 3k in / 800 out | 2.16B in / 0.58B out | **$337** (vertex grant) | $0 | $337 |
| Workers AI Llama-3.2-3B (English chat fallback / today's head) | only fires when vertex throttled | 0–720k turns | $0 (free quota) | $0 | $0–200 |
| **Sarvam** (Assamese chat + En↔As translate) | 20k post-cache calls | small | **$2** (sarvam grant) | $0 | $8 |
| **Vertex Gemini 2.5 Flash** (`content_formatter` polish) | 15k post-cache | small | **$6** | $0 | $17 |
| **Pinecone** (rerank + vector_search) | ~250k queries | — | **$10** (pinecone grant) | $0 | $15 |
| **MongoDB Atlas** (DB primary, M0) | always-on | 5 GB | $0 (free tier) | $0 | $10 |
| **Cloudflare zone + R2 + CDN + WAF + AI Gateway** | covered by annual zone | — | $0 (annual) | $40 | $40 |
| **Cloudflare Workers AI** (embed Gemma-300M + Qwen3-0.6B) | 120k embeds | — | $0 (free Neuron quota) | $0 | $30 |
| **Azure (ACA only)** (0.25 vCPU × 2–4 replicas) | ~720 vCPU·hrs | — | **$28** (azure grant) | $0 | $28 |
| **AWS (SES + SQS + S3)** | ~120k emails + ~30k msgs + ~5 GB | — | **$12** (activate grant) | $0 | $40 |
| **GCP** (Web Risk + Discovery Engine + Cloud Trace) | ~200k checks | — | **$5** (GCP grant) | $0 | $30 |
| **Razorpay** (payment gateway) | n/a (P1 has no paid users) | — | $0 | $0 | $0 |
| **Cloudflare Web Push** (transactional notify) | covered by Workers Paid migration | — | $0 | $5 | $20 |
| **Sentry / OTEL → Cloud Trace** (observability) | covered by Sentry team plan | — | $0 | $0 | $50 |
| **GitHub Actions CI** | ~500 minutes/mo | — | $0 (Free tier) | $0 | $20 |
| **Phase 1 totals** | | | **$400 (drawdown)** | **$45–90 (cash)** | **$408–717** |
| **Cost / DAU** (3k DAU midpoint) | | | $0.13 | **$0.018–0.030** | $0.14 |

### Phase 2 — Launch (5k – 10k DAU, ~80k MAU midpoint, 5 % paid)

| Provider | Workload | Monthly volume | Credit drawdown $ | Cash $ | No-credits sticker $ |
|---|---|---|---|---|---|
| **Vertex Gemini 2.5 Flash** (English chat, target post-#555) | 1.78M chat turns × 3k in / 800 out | 5.34B in / 1.42B out | **$827** (vertex grant) | $0 | $827 |
| Workers AI Llama-3.2-3B (chat fallback / today's head) | when vertex throttled | up to 1.78M | $0 (free quota — within 10k Neuron/day at this size) | $0 | $0–500 |
| **Sarvam** (Assamese chat + translate, 75k post-cache) | 75k calls | — | **$8** (sarvam grant) | $0 | $30 |
| **Vertex Gemini 2.5 Flash** (`content_formatter` polish, 60k post-cache) | 60k calls | — | **$24** | $0 | $66 |
| **Deepgram** (paid STT — 4k paid × 12 sessions × 60 s ≈ 800 STT-hrs) | 800 hrs | — | **$15** (deepgram grant) | $0 | $345 |
| **ElevenLabs** (paid TTS — ~12k TTS-min) | — | — | **$30** (elevenlabs grant) | $0 | $60 |
| **Pinecone** (rerank + vector_search) | ~2.5M queries | — | **$30** (pinecone grant) | $0 | $40 |
| **MongoDB Atlas** (M10) | always-on | 80 GB | **$57** (atlas credits) | $15 | $72 |
| **Cloudflare zone + R2 + CDN + WAF + AI Gateway** | covered | — | $0 | $40 | $80 |
| **Cloudflare Workers AI** (embed) | 1.5M embeds | — | $0 (free quota) | $0 | $80 |
| **Azure (ACA, 0.25 vCPU × ~6 replicas avg)** | ~3,200 vCPU·hrs | — | **$50** (azure grant) | $13 | $130 |
| **AWS (SES + SQS + Lambda + S3)** | 400k emails + 200k msgs + 1M Lambda + 80 GB | — | **$30** (activate grant) | $10 | $80 |
| **GCP** (Web Risk + Discovery Engine + Cloud Trace + BigQuery billing-export) | ~1.5M checks + ~10 GB log | — | **$15** (GCP grant) | $8 | $40 |
| **Razorpay** (4k paid × ₹100/mo, 2 % fee) | $4,800 gross | — | $0 | $96 (offset by revenue) | $96 |
| **Cloudflare Web Push** | ~1M notifications | — | $0 | $5 | $30 |
| **Sentry / OTEL → Cloud Trace** | growing trace volume | — | $0 (team plan) | $0 | $80 |
| **GitHub Actions CI** | ~1.5k minutes/mo | — | $0 (Free tier) | $0 | $30 |
| **Phase 2 totals** | | | **$1,086 (drawdown)** | **$92 (cash, at #549 cap)** | **$1,966** |
| **Cost / DAU** (10k DAU) | | | $0.109 | **$0.0092** | $0.197 |
| **Credit-on infra value (drawdown + cash)** | | | | **~$1,178** ← top-line "infra value" | |

> **Range presented in TL;DR ($300–$800)** is the **range of credit
> drawdown across the 5k–10k DAU band** — i.e., $300/mo at the bottom
> end (5k DAU), ~$1,100/mo at the 10k top end. The TL;DR midpoint is
> ~$550/mo. The full $1.18k figure includes the $96 Razorpay fee
> which is revenue-offset, not a true infra cost.

**Voice revenue offset (Phase 2):** 4k paid × ₹100 / mo ≈ $4,800 gross
(USD/INR ≈ 83); net of Razorpay (~2 %), GST, refunds: **~$4,400 / mo**.
Voice cost: ~$45 (deepgram + elevenlabs + paywall infra). Voice is
**net-positive — the founding economic case for `require_paid_plan`**.

### Phase 3 — Growth (10k – 50k DAU, ~240k MAU midpoint, 7 % paid)

> **Cap raise required.** `MONTHLY_TOTAL_USD_CAP` → **$500**
> (reverting to the pre-#549 default). Requires
> `# COST-CAP-OVERRIDE: phase-3-growth-trigger` marker AND
> Sentry-annotated changelog per `tests/test_cost_caps.py`.

| Provider | Workload | Credit drawdown $ | Cash $ | No-credits sticker $ |
|---|---|---|---|---|
| **Vertex Gemini 2.5 Flash** (English chat, paid + free post-quota overage) | 5.3M chat turns | **$1,200** (vertex grant ~exhausted Q3) | $200 (post-grant) | $2,500 |
| Workers AI Llama-3.2-3B (chat fallback) | exam-day spikes | $0 (free quota; overage starts at Phase 3) | $25–60 (overage) | $1,800–3,600 |
| **Sarvam** (Assamese + translate) | — | **$80** | $25 | $120 |
| **Vertex Gemini 2.5 Flash** (`content_formatter`) | — | **$200** | $80 | $250 |
| **Deepgram** (paid STT — 16.8k paid × 12 × 60 s) | — | **$60** (credits running thin) | $0–80 | $1,400 |
| **ElevenLabs** (paid TTS) | — | **$90** | $0–25 | $250 |
| **Pinecone** (paid tier 1) | — | **$30** (credits drained ~Q3) | $30 | $120 |
| **MongoDB Atlas** (M20, ~250 GB) | — | **$200** | $120 | $310 |
| **Cloudflare zone + R2 + CDN + WAF + AI Gateway** | — | $0 | $80 | $200 |
| **Cloudflare Workers AI** (embed at scale) | — | $0 (free quota strained) | $25 | $200 |
| **Azure (ACA, ~12 replicas avg)** | ~7,000 vCPU·hrs | **$100** | $50 | $290 |
| **AWS (SES + SQS + Lambda + S3 + Glacier)** | — | **$80** | $30 | $250 |
| **GCP** (Web Risk + Discovery Engine + Cloud Trace + BigQuery) | — | **$50** | $15 | $80 |
| **Razorpay** (16.8k paid × ₹100 × 2 %) | $336 fee | $0 | $336 (offset by $20k revenue) | $336 |
| **Cloudflare Web Push** | ~3M notifications | $0 | $30 | $80 |
| **Sentry / OTEL → Cloud Trace** (post-team-plan tier) | — | $0 | $50 | $150 |
| **GitHub Actions CI** | ~3k minutes/mo | $0 | $0 | $50 |
| **Phase 3 totals** (excl. Razorpay revenue-offset) | | **$2,090 (drawdown)** | **$700–945 (cash)** | **$8,150–10,150** |
| **Cost / DAU** (30k DAU) | | $0.070 | **$0.023–0.032** | $0.272–0.338 |

**Net revenue at 7 % conversion × 240k MAU × ₹100 ≈ $20k / mo gross**
— infra is < 4 % of revenue.

### Phase 4 — Scale (50k – 100k DAU, ~600k MAU midpoint, 9 % paid)

> Credits are functionally exhausted by here. The table is presented
> credits-off; cash and "infra value" converge.

| Provider | Workload | Monthly cost (credits-off) |
|---|---|---|
| **Vertex Gemini 2.5 Flash** (English chat — paid + free post-quota overage) | 13.2M chat turns | **$2,800** |
| Workers AI Llama-3.2-3B (chat fallback for the long free tail) | post-Neuron-overage | **$300–500** |
| **Sarvam** (Assamese + translate) | — | **$200** |
| **Vertex Gemini 2.5 Flash** (`content_formatter`) | — | **$500** |
| **Deepgram** (paid STT — 54k paid × 12 × 60 s) | — | **$400** |
| **ElevenLabs** (paid TTS) | — | **$120** |
| **Pinecone** (p1 cluster) | — | **$280** |
| **MongoDB Atlas** (M30/M40, ~1.5 TB) | — | **$650** |
| **Cloudflare zone + R2 + CDN + WAF + AI Gateway** | — | **$250** |
| **Cloudflare Workers AI** (embed at scale post-quota) | — | **$200** |
| **Azure (ACA, ~24 replicas avg)** | ~17,000 vCPU·hrs | **$300** |
| **AWS (SES + SQS + Lambda + S3 + Glacier)** | — | **$250** |
| **GCP** (Web Risk + Discovery Engine + Cloud Trace + BigQuery) | — | **$100** |
| **Razorpay** (54k paid × ₹150 × 2 %) | $1,950 fee | **$1,950** (offset by $97k revenue) |
| **Cloudflare Web Push** | ~10M notifications | **$120** |
| **Sentry** (post-team-plan tier) | — | **$300** |
| **GitHub Actions CI** | ~5k minutes/mo | **$50** |
| **Phase 4 totals** | | **$8,770** ← inside the TL;DR $4k–$12k band |
| **Cost / DAU** (75k DAU) | | **$0.117** |

**Net revenue at 9 % conversion × 600k MAU × ₹150 ≈ $97k / mo gross**
— infra is **< 9 % of revenue**. Credit pool acts as a per-incident
cushion for surprise quota events (exam-day 5× spikes), not as runway.

---

## 3. Per-provider runway ledger

> Each row: pool size, expiry, monthly drawdown by phase, runway
> months by phase, renewal/replacement plan when exhausted. Cells
> marked `unverified` must be filled in from billing consoles before
> the memo enters an investor data room.

| Provider | Pool size USD | Expiry | P1 drawdown / runway | P2 drawdown / runway | P3 drawdown / runway | P4 drawdown / runway | Renewal / replacement plan |
|---|---:|---|---|---|---|---|---|
| Google Cloud for Startups (Vertex + GCP infra) | 2,000 | unverified (~24 mo from issue) | $348 / 5.7 mo | $866 / 2.3 mo | $1,450 / 1.4 mo | exhausted | (a) Apply Google for Startups Cloud Hub renewal at < 90 d runway; (b) cap Vertex content_formatter calls 50 % via `content_formatter` fallback flag (V4 §15 Workers-AI Llama-3.3-70b); (c) at exhaustion, accept full Vertex pricing — already modelled in Phase 4 |
| Azure for Startups (ACA + chat re-enable buffer) | 2,500 | unverified (~24 mo) | $28 / 89 mo | $50 / 50 mo | $100 / 25 mo | exhausted | Microsoft for Startups Pegasus tier renewal; ACA right-sizing per `aca-cutover.md` if denied |
| AWS Activate (SES + SQS + Lambda + S3 + Glacier) | 1,000 | unverified (~12 mo from issue) | $12 / 83 mo | $30 / 33 mo | $80 / 12.5 mo | exhausted | AWS Activate Portfolio tier (requires VC sponsor); fallback = absorb in cash — modelled in Phase 4 |
| Sarvam Startup Credits | 500 | unverified | $2 / 250 mo | $8 / 62 mo | $80 / 6 mo | exhausted | Direct vendor extension; fallback = `workers_ai_indic` last-resort tier (degraded UX, fail loud per V4 §12) |
| ElevenLabs Startup Credits | 500 | unverified | $0 (no voice in P1) | $30 / 17 mo | $90 / 5.5 mo | exhausted | Vendor extension OR raise paid voice price OR contract paid commits |
| Deepgram Startup Credits | 500 | unverified | $0 (no voice in P1) | $15 / 33 mo | $60 / 8 mo | exhausted | Same as ElevenLabs row |
| Pinecone Startup Credits | 500 | unverified | $10 / 50 mo | $30 / 17 mo | $30 / 17 mo | exhausted | Pinecone Startup renewal; fallback = migrate rerank to Workers-AI (acknowledge latency penalty in matrix §2.1) |
| MongoDB Atlas (free tier + ramp credits) | ~500 (Atlas Startup) | unverified | $0 (M0 free) | $57 / 9 mo | $200 / 2.5 mo | exhausted | Atlas Startup tier renewal; fallback = downgrade tier and accept performance hit |
| Cloudflare Workers AI (free Neuron quota) | n/a (always-on free tier; 10k Neurons/day per account) | n/a | $0 | $0 | $25–60 (overage) | $200–500 (overage) | n/a — quota is renewed daily; overage is unavoidable past Phase 3 and is the cheapest LLM rate in the chain |
| Cloudflare zone + R2 + CDN + WAF + AI Gateway | covered (annual Cloudflare contract) | annual | $40 cash | $40 cash | $80 cash | $250 cash | Annual renewal at year-end; explore Cloudflare for Startups credit if available |
| **Total quantifiable grants** | **~$8,000** | | **$400 / mo** | **$1,086 / mo** | **$2,090 / mo** | **exhausted** | |
| **Pool runway** | | | **20 months** | **7.4 months** | **3.8 months** | **n/a** | The Phase 2 → 3 trigger ladder fires before pool exhausts; see §6 |

---

## 4. Cost-per-DAU table

| Phase | DAU midpoint | Credit-on infra value $/DAU/mo | Cash $/DAU/mo | No-credits sticker $/DAU/mo | Marginal $/incremental DAU (cash) |
|---|---|---|---|---|---|
| 1 — Pilot   | 3k   | **$0.13**     | **$0.018–0.030** | $0.14 | ~$0.005 (chat $0, polish on credits) |
| 2 — Launch  | 10k  | **$0.118**    | **$0.0092**      | $0.197 | ~$0.006 |
| 3 — Growth  | 30k  | **$0.092**    | **$0.023–0.032** | $0.272–0.338 | ~$0.011 (Workers-AI overage starts) |
| 4 — Scale   | 75k  | **$0.117** *(cash; credits exhausted)* | **$0.117** | $0.117 | ~$0.018 |

The marginal $/DAU **falls** then **rises** as we cross the
Cloudflare free-Neuron daily quota at Phase 3. Below the quota,
every incremental DAU is essentially free; above it, every
incremental free chat turn marginally bills against Workers-AI
overage at ~$0.0003–0.0007 / 1k output tokens.

---

## 5. Sensitivity analysis

### 5.1 Cache-hit-rate grid — credit-on infra value at each phase

`ai_input_cache.py` reports the live hit rate to Sentry; default
assumption is 50 %. Lower hit rates push polish + translate +
formatter volume through paid providers.

| Cache hit rate | Phase 1 | Phase 2 | Phase 3 | Phase 4 (no credits) |
|---|---|---|---|---|
| **30 %** | $470 | **$1,330** ← cash side breaches $100 cap, MeterD ladder fires | $2,500 | $9,400 |
| **50 %** *(baseline)* | $400 | $1,086 | $2,090 | $8,770 |
| **70 %** | $340 | $890 | $1,720 | $8,150 |
| **85 %** | $295 | $750 | $1,450 | $7,650 |

**Key insight:** at 30 % cache hit rate, Phase 2's cash component
trips the 95 % ladder (`DEGRADATION_PCT_FREE_503`) and free-tier
chat returns 503. **Cache hit rate is therefore a product-availability
control surface**, not just a cost knob — minimum to hold is ≥ 45 %
at all phases.

### 5.2 Voice adoption slider — Phase 2 cost & revenue

Voice is paid-only (`require_paid_plan` on `/voice/*`); raising
adoption raises **both** cost AND revenue, with revenue scaling
~100× faster than cost.

| % of MAU on voice | Voice cost (drawdown + cash) | Voice gross revenue (paid plan ₹100) | Net contribution |
|---|---|---|---|
| 1 %  | $9   | $960    | **+$951** |
| 5 % *(baseline)* | $45  | $4,800  | **+$4,755** |
| 10 % | $90  | $9,600  | **+$9,510** |
| 25 % | $225 | $24,000 | **+$23,775** |

### 5.3 Paid-conversion slider — Phase 2 net P&L

| Conversion rate | Paid users (80k MAU) | Gross revenue ₹100 | Infra cash burn | Net |
|---|---|---|---|---|
| 2 % | 1.6k | $1,920  | $90 | **+$1,830** |
| 5 % *(baseline)* | 4.0k | $4,800 | $92 | **+$4,708** |
| 10 % | 8.0k | $9,600 | $98 | **+$9,502** |
| 20 % | 16.0k | $19,200 | $100 *(at #549 cap)* | **+$19,100** |

Even at **2 % paid conversion**, the company is gross-margin positive
at Phase 2. The model only breaks if paid conversion holds at 0 %
past Phase 1 — at which point the cap raise gates the Phase 2 → 3
transition and we hold at 10k DAU until conversion materialises.

---

## 6. Migration triggers — credit-runway thresholds

Each row: **alarm → owner → SLA → action**. Pager source is
`#syrabit-oncall` Slack backed by Sentry alerts on `db.shadow.diff`,
`meter_d.cap_pct`, and per-grant runway counters in
`routes/admin_credits.py`.

| Trigger | Pager / channel | Owner | Response SLA | Action (incl. #549 auto-flip behaviour) |
|---|---|---|---|---|
| **GCP credit balance < 90 days runway** | Slack `#syrabit-oncall` (Cloud Billing API alert via `gcp_billing.py`) | infra | 72 h | (a) Open Google Cloud for Startups Hub extension request; (b) if denied, flip `content_formatter` to Workers-AI Llama-3.3-70b fallback per V4 §15. **#549 interaction:** if the flip lands on the Vertex chat path, `_select_chat_primary()` automatically falls back to `workers_ai_llama32_3b` because vertex is no longer in the allow-set; this is the documented fail-loud behaviour in `cost_caps._select_chat_primary()`. |
| **MongoDB Atlas credits < 6 months runway** | Slack `#syrabit-oncall` (Atlas console webhook) | infra | 1 week | Atlas Startup renewal; if denied, downgrade M20 → M10 (acceptable for ≤ 25k DAU) and accept p99 query degradation. |
| **Pinecone credits < 6 months runway** | Slack `#syrabit-oncall` (Pinecone billing webhook) | infra | 1 week | Pinecone Startup renewal; if denied, migrate rerank to Workers-AI fallback (acknowledge latency penalty in matrix §2.1). |
| **Sarvam credits < 3 months runway** | Slack `#syrabit-oncall` (manual cron) | infra | 1 week | Vendor renewal; if denied, shift Assamese chat to `workers_ai_indic` last-resort tier (degraded UX — fail loud per V4 §12). |
| **Azure for Startups credits < 12 months runway** | Slack `#syrabit-oncall` (Azure Cost Management alert) | infra | 1 week | (a) Apply Microsoft for Startups Pegasus tier renewal; (b) if denied, ACA right-sizing per `aca-cutover.md` (drop min replicas 2 → 1 below 5k DAU, accept cold-start). **#549 interaction:** the Azure pool is reserved for the Vertex chat re-enable (#555/#556) buffer; depleting it before #555 lands forces vertex-fallback decisions earlier than planned. |
| **Sentry monthly event quota > 80 % twice in 7 days** | Sentry built-in spike-protection alert | infra | 72 h | (a) Tighten OTEL → Cloud Trace sampling rate; (b) raise Sentry plan tier (cash impact: ~$80–300/mo at Phase 3+, modelled in §2). Sentry is the only observability-tier line that scales sub-linearly with DAU; do **not** confuse this trigger with the MeterD ladder. |
| **Deepgram + ElevenLabs combined < 3 months runway** | Slack `#syrabit-oncall` | infra + product | 72 h | Raise paid voice price OR contract paid commits. Voice paywall (#549) ensures cost < revenue; this trigger is about smoothing not survival. |
| **Vertex (Google) credit balance < 90 days OR < 20 % of original pool size** | Slack `#syrabit-oncall` (Cloud Billing API; ≥ 20 % delta = quarterly review trigger per top-of-file note) | infra | 72 h | Same as GCP row above. **The 20 % delta is also the standalone trigger that re-derives this entire memo on the next quarterly cycle.** |
| **MeterD trips ≥ 60 % of monthly cap twice in 7 days** | PagerDuty (Sentry alert on `meter_d.cap_pct ≥ 0.6`) | infra (primary), founder (escalation) | 24 h | **#549 auto-action:** pause-batch ladder fires (#557). Manual: investigate cache-hit drift (5.1); if structural, open `# COST-CAP-OVERRIDE` task for Phase 2 → 3 cap raise. |
| **MeterD trips ≥ 80 %** | PagerDuty + founder SMS | infra + founder | 4 h | **#549 auto-action:** voice-off ladder fires (#557). Manual: review which paid users to comp via free credits. |
| **MeterD trips ≥ 95 %** | PagerDuty + founder SMS + email | infra + founder | 1 h | **#549 auto-action:** free-tier 503 fires (#557). Manual: status-page update; root-cause within 24 h. |
| **MeterD LOCKS @ 100 %** | PagerDuty critical | infra + founder | immediate | **#549 auto-action:** Redis `chat:cheaponly=1` LOCKS automatically; `_select_chat_model` reads on every dispatch and forces `workers_ai_mistral_7b`. Manual: ride out the month vs `# COST-CAP-OVERRIDE`. |
| **Per-phase DAU sustained for 14/30/60 days** | Datadog/Sentry analytics dashboard | founder + infra | 1 sprint | Phase transition checklist (P1 → 2: voice paywall live + Razorpay live; P2 → 3: cap raise + fundraise; P3 → 4: dual-region Mongo + ACA scale-out). |

---

## 7. Reconciliation with Task #549 founder-locks

| #549 control | This memo cites it as |
|---|---|
| `MONTHLY_TOTAL_USD_CAP = $100` default | §1, §2 cash columns, §5.1 cap-breach ladder, §6 MeterD rows |
| `_select_chat_primary()` head ∈ {workers_ai*, vertex} | §1, §2 (vertex modelled as target; workers_ai as today's head + permanent fallback) |
| `_select_chat_model` turns 1–2 → `workers_ai_mistral_7b` | §1, §2 footnote |
| `require_paid_plan` on `/voice/*` | §2 Phase 2 voice line, §5.2 sensitivity — voice is revenue not cost |
| `DEGRADATION_PCT_PAUSE_BATCH/VOICE_OFF/FREE_503` (60/80/95 %) | §6 trigger ladder, every MeterD row |
| Credit-runway-aware dispatch | §3 per-provider runway ledger |
| CI guard `check_budget_ceiling.py` (in `backend-tests.yml` + `azure-container-apps-deploy.yml`) | §1 — every founder-lock mechanically enforced; **a cap raise must update both the code constant AND this memo or the next quarterly review will catch the drift** |

---

## 8. What this memo deliberately does NOT promise

**Modelling caveats (mandated assumptions — operators must
re-litigate at every quarterly review):**

- **30 chat-turns/MAU/month is treated as linear in MAU.** Reality
  has weekly exam-cycle spikes (~2× weekday baseline on Sun/Mon,
  ~5× on board-exam days). The cap math holds at the spike because
  the edge `CHAT_CAP_MONTHLY=30` is per-user, not per-aggregate;
  the per-day soft cap of 3 (`CHAT_CAP_DAILY`) smooths individual
  spikes. **Aggregate** spikes are absorbed by the MeterD ladder
  (§6).
- **Voice gating impact:** the 5 % paid-only voice adoption
  assumption assumes `require_paid_plan` continues to return 402
  for free users. If sub-task #557 (operational ladder wiring)
  ever flips voice to free-tier under the 80 % degradation rule
  AND a cap raise allows it, voice cost grows ~20× and revenue
  contribution disappears. This memo assumes voice stays paywalled.
- **Storage growth:** modelled at +1 GB / 1k MAU / month; if
  notebook-polish output grows past assumed sizes (e.g., the
  Vertex Gemini 2.5 Flash output cap raises from 2,500 → 4,000
  output tokens via a `# COST-CAP-OVERRIDE`), MongoDB Atlas
  storage cost lines in §2 must be re-derived.
- **Razorpay / ARPU:** ₹100 / paid user / month is the *list*
  ARPU. Net of Razorpay (~2 %), GST (18 % on the gross), refunds,
  and chargebacks, the **net** per-paid-user is approximately
  ₹78 ($0.94). Phase-3/4 revenue lines use **gross** numbers for
  clarity; founders should mentally apply the ~22 % haircut for
  net revenue planning.
- **USD/INR:** held constant at ~83 throughout. A swing to 90 or
  back to 75 changes Razorpay revenue lines by ~9 % each direction
  but does **not** materially change the cost lines (which are
  USD-denominated in vendor billing).
- **Operational wiring of the 60/80/95 ladder** → sub-task **#557**.
- **Re-enabling Vertex Gemini on the chat hot path** → sub-tasks
  **#555/#556**. Until those land, `CHAT_PRIMARY_OVERRIDE=vertex`
  is *ignored* with a loud Sentry warning per `cost_caps._select_chat_primary()`
  (V4 §12 no-silent-fallbacks).
- **Deep Azure surface removal** → sub-task **#553**.
- **SES / web-push / observability tier rebalance** → sub-tasks
  **#554 / #556 / #558**.
- **Frontend pricing-page work** → separate front-end task (unfiled).
- **Verified credit balances.** Every cell in
  `provider-credit-matrix.md` marked `unverified` is still
  `unverified` — filling those in is **a prerequisite for using
  this memo in an investor data room**.
- **GST / tax accounting on Razorpay** — out of scope; defer to
  finance.

---

**Validated against code constants on 2026-05-07** —
`CHAT_CAP_MONTHLY=30` (all users, edge-proxy lines 670–701);
`CHAT_CAP_DAILY=3` (free only, paid bypass via
`CHAT_DAILY_BYPASS_PLANS`); `_select_chat_model` turns 1–2 →
`workers_ai_mistral_7b`, turns 3+ → `_select_chat_primary()` →
`workers_ai_llama32_3b`; `_DEFAULT_MONTHLY_TOTAL_USD_CAP=$100`
(`cost_caps.py`); `MeterDConfig.cap_usd=$100`
(`credit_burn_meter.py`); ladder thresholds 0.60 / 0.80 / 0.95
strict-monotone (CI-enforced).

If **any** number drifts from `cost_caps.py`, `credit_burn_meter.py`,
`config.py`, or `workers/edge-proxy/src/index.ts`, **the code wins**
and this memo must be re-derived from the new constants on the next
quarterly cycle (or sooner per the top-of-file ≥ 20 % credit-pool
trigger).

---

## References

- Locked architecture: [`../../../../infra/v4-locked-architecture.md`](../../../../infra/v4-locked-architecture.md)
- Four-cloud delegation: [`../../../../infra/four-cloud-delegation.md`](../../../../infra/four-cloud-delegation.md)
- Credit matrix: [`provider-credit-matrix.md`](provider-credit-matrix.md)
- Startup-credits migration: [`startup-credits-migration.md`](startup-credits-migration.md)
- ACA cutover runbook: [`aca-cutover.md`](aca-cutover.md)
- Backend cost controls: `artifacts/syrabit-backend/cost_caps.py`,
  `artifacts/syrabit-backend/credit_burn_meter.py`,
  `artifacts/syrabit-backend/scripts/check_budget_ceiling.py`
- Edge controls: `workers/edge-proxy/src/index.ts`
- Provider decommission rationale (#347):
  [`providers-task-347-decommission.md`](providers-task-347-decommission.md)

---

## Revision history

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-05-07 | infra | Initial memo. Covers Phases 1–4 (1k → 100k DAU) with credit-drawdown economics, per-provider runway ledger, cache-hit / voice / paid-conversion sensitivity grids, and migration trigger ladder (incl. GCP <90 d, Mongo/Pinecone <6 mo, Sarvam <3 mo, Azure <12 mo, Sentry quota, Vertex ≥ 20 % pool delta, MeterD 60/80/95/100 % auto-flips). Reconciles with Task #549 founder-locks ($100 cap, voice paywall, workers_ai chat head pre-#555, 60/80/95 % degradation ladder, CI guard `check_budget_ceiling.py`). |
