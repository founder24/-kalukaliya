# Phased Credit-Runway Cost Model — Syrabit.ai

> **Status:** Fundraising-grade memo (Task #550). Numbers are bottom-up
> from the locked controls in `artifacts/syrabit-backend/cost_caps.py`,
> the edge caps in `workers/edge-proxy/src/index.ts`, the
> credit-weighted matrix in
> [`provider-credit-matrix.md`](provider-credit-matrix.md), and the
> four-cloud delegation in [`../../../../infra/four-cloud-delegation.md`](../../../../infra/four-cloud-delegation.md).
> **Source of truth:** [`../../../../infra/v4-locked-architecture.md`](../../../../infra/v4-locked-architecture.md).
> If anything below disagrees with V4, V4 wins.
> **Audience:** founders, prospective investors, infra reviewers.
> **Author / owner:** infra · **Last updated:** 2026-05-07.

---

## TL;DR

Syrabit operates under a **founder-locked $100 / month spend ceiling**
(Task #549) layered on top of ~$8.0k of unspent multi-cloud startup
credits. Combined with the edge chat cap (30 turns/month + 3/day per
anonymous user), the K.2 deterministic AI cache, and tier-routing of
free-user traffic to Cloudflare Workers AI (Llama-3.2-3B,
$0/month within the free Neuron quota), this gives us **a credit
runway of roughly 18–24 months at 10k DAU with no fundraising
required**. The plan below extends the same controls through 100k DAU.

| Phase | DAU range | Effective MAU | Plan ARPU | Steady-state burn (with credits) | Steady-state burn (no credits) | Credit runway @ this phase |
|---|---|---|---|---|---|---|
| 1 — Pilot       | 1k – 5k    | 8k – 40k     | n/a (free-tier loss-leader) | **$60 – $90 / mo**       | $300 – $500 / mo  | 24+ months |
| 2 — Launch      | 5k – 10k   | 40k – 80k    | ₹100 / mo paid (5% conv)    | **$80 – $100 / mo** *(cap)* | $700 – $1,300 / mo | 18 months  |
| 3 — Growth      | 10k – 50k  | 80k – 400k   | blended ₹120 / mo           | **$300 – $500 / mo** | $4k – $7k / mo    | 4–6 months (top-up cycle) |
| 4 — Scale       | 50k – 100k | 400k – 800k  | blended ₹150 / mo           | **$700 – $1,000 / mo** | $9k – $14k / mo   | revenue-positive (no credit dependency) |

The four-cloud cost-share remains the V4-locked **40 % Cloudflare /
30 % Azure / 20 % AWS / 10 % GCP** through every phase. Voice (TTS /
STT / `/voice/voice`) is paywalled to paid plans (#549) and is a
revenue centre, not a cost centre.

---

## 1. Cost framework

### 1.1 Founder-locked controls (in code, CI-enforced)

| Control | Where | Value | Guard |
|---|---|---|---|
| Monthly USD ceiling | `cost_caps._DEFAULT_MONTHLY_TOTAL_USD_CAP`, `credit_burn_meter.MeterDConfig.cap_usd` | **$100** | `scripts/check_budget_ceiling.py` (CI: backend-tests + ACA deploy) |
| Per-call token budgets | `cost_caps.TOKEN_BUDGETS` | chat 3000/800, content 4000/2000, formatter 4500/2500, translate 2000/2000, OCR 1500/800, STT 2000/500 | `tests/test_cost_caps.py` requires `# COST-CAP-OVERRIDE` marker + Sentry changelog to raise |
| Edge chat caps (anon) | `workers/edge-proxy/src/index.ts` `CHAT_CAP_MONTHLY=30`, `CHAT_CAP_DAILY=3` | KV-backed | Same `# COST-CAP-OVERRIDE` policy |
| Free-tier chat primary | `cost_caps._select_chat_primary()` | `workers_ai_llama32_3b` (CF free Neuron quota) | `check_budget_ceiling.py` asserts head ∈ `{workers_ai*, vertex}` |
| Voice paywall | `auth_deps.require_paid_plan` on `/voice/{tts,stt,voice}` | 402 for free users; admin/staff/educator bypass | `tests/test_voice_paid_gate.py` + `check_budget_ceiling.py` |
| Three-stage degradation ladder | `cost_caps.DEGRADATION_PCT_PAUSE_BATCH/VOICE_OFF/FREE_503` | 60 % / 80 % / 95 % of monthly cap | Strict-monotone check in `check_budget_ceiling.py`. **Operational wiring** of pause-batch / voice-off / free-503 actions is sub-task **#557**. |
| Hard stop @ 100 % | MeterD → Redis `chat:cheaponly=1`; `_select_chat_model` reads on every dispatch | LOCK | `tests/test_provider_priority_locked.py` |

### 1.2 Variable-cost drivers, per request

| Surface | Tokens in / out | Provider chosen by | Effective $/1k req with credits | Effective $/1k req no credits |
|---|---|---|---|---|
| Free-user chat (turns 1–2) | 3000 / 800 | `workers_ai_mistral_7b` (`@cf/mistral/mistral-7b-instruct-v0.3`) per `_select_chat_model` lines 352–358 | **$0.00** | $0.30–0.70 (CF Workers-AI overage) |
| Free-user chat (turns 3–15) | 3000 / 800 | `_select_chat_primary()` → `workers_ai_llama32_3b` (`@cf/meta/llama-3.2-3b-instruct`) | **$0.00** | $0.30–0.70 |
| Free-user chat (turns >15) | 3000 / **600** (clamped to `CONSERVATIVE_OUTPUT_TOKENS`) | same llama32_3b primary | **$0.00** | $0.25–0.55 |
| Paid-user chat | 3000 / 800 | same llama32_3b primary today; vertex/azure path lights up after sub-tasks #555/#556 | **$0.00** today; ~**$0.005** post-#555 (vertex headroom) | $0.30–0.70 today; $0.27 (vertex) / $1.50 (azure) post-#555 |
| Cheaponly LOCK (MeterD ≥ 100 %) | 3000 / 600 | `workers_ai_mistral_7b` forced for all English chat | **$0.00** | $0.25–0.55 |
| Long-form notebook polish | 4500 / 2500 | `content_formatter` (vertex Gemini 2.5 Flash → workers-ai Llama-3.3-70b → passthrough) | **~$0.40** (vertex) | $1.10 (vertex) / $0 (workers-ai fallback within quota) |
| Translate (En↔As) | 2000 / 2000 | sarvam (matrix weight 82) | **~$0.10** (sarvam credits) | ~$0.40 |
| OCR (PYQ) | 1500 / 800 | vision pool (vertex/azure/bedrock) | **~$0.20** | ~$0.80 |
| STT (paid voice) | 2000 / 500 | deepgram (matrix weight 82) | **~$0.30** (deepgram credits) | $1.20 |

After **K.2 deterministic-input cache** (`ai_input_cache.py`, wired
into formatter + translate + OCR + `pipeline.stage3_polish`), **~50 %
of formatter / translate / OCR calls are served from cache at $0**.
Live chat is excluded by policy and gets no cache uplift.

---

## 2. Phase model

Assumptions held constant across phases unless overridden:

- **DAU → MAU ratio:** 8× (industry-standard for a study-routine app
  with weekly exam cycles).
- **Chat usage per MAU:** 30 turns / month — **the edge `CHAT_CAP_MONTHLY=30`
  applies to EVERYONE (free + paid)** per `workers/edge-proxy/src/index.ts`
  lines 670–701. Paid plans (`pro`, `student-plus`, `premium`,
  `enterprise`) only bypass the per-day soft cap of 3
  (`CHAT_DAILY_BYPASS_PLANS`); they do **not** get a higher monthly
  ceiling. Free users average ~22 turns/month (limited by the 3/day
  cap), paid users average ~28 turns/month (close to the monthly
  ceiling because the 3/day soft cap is removed).
- **AI-cache hit rate:** 50 % on formatter / translate / OCR (live
  chat: 0 % by policy).
- **Voice adoption (paid):** ~5 % of MAU sustained, 60 sec / session,
  3 sessions / week.
- **Paid conversion:** Phase 1 = 0 %, Phase 2 = 5 %, Phase 3 = 7 %,
  Phase 4 = 9 %. ARPU is post-Razorpay-fee net.
- **Email volume:** ~5 transactional emails / MAU / month (auth,
  receipts, weekly digests).
- **Egress:** Cloudflare CDN cache-hit ratio ≥ 90 % on static; only
  the 10 % cache-miss tail bills against ACA / R2.

### Phase 1 — Pilot (1k – 5k DAU, ~24k MAU midpoint)

**Burn breakdown (with credits, midpoint 3k DAU / 24k MAU):**

| Line | Volume | Unit cost | Subtotal |
|---|---|---|---|
| Chat (free, workers-ai head) | 24k MAU × 30 turns = 720k turns/mo | $0 (free Neuron quota) | **$0** |
| Notebook polish (vertex content_formatter) | ~30k polish calls × 50 % cache miss = 15k | $0.40 / k | **$6** |
| Translate (sarvam) | ~40k calls × 50 % miss = 20k | $0.10 / k | **$2** |
| OCR (PYQ uploads, ~1 % MAU/mo) | ~240 calls | $0.20 / k | **$0.05** |
| Mongo Atlas M0 + Pinecone serverless | always-on | free / serverless | **$0–10** |
| Cloudflare zone + R2 + Workers AI | covered | annual zone plan | **$0** |
| Azure ACA (0.25 vCPU × 2–4 replicas) | ~720 vCPU·hrs/mo | $0.04 / vCPU·hr | **$28** |
| AWS SES + SQS | 120k emails + ~30k queue msgs | SES $0.10/k + SQS $0.40/M | **$12** |
| GCP Web Risk + Discovery Engine + Cloud Trace | ~200k checks | mostly free-tier | **$5** |
| Sentry / OTEL | covered (Sentry team plan) | $0 marginal | **$0** |
| **Total (with credits)** | | | **~$60–90** |

**Without credits**, the same volume costs **~$300–500 / mo** —
dominated by ACA, R2 paid add-ons (currently being migrated, see
`startup-credits-migration.md`), and the formatter / translate
volume if vertex credits ran out. Even at the un-credited rate we
remain under the $100 cap by routing 100 % of chat to the free
Workers-AI tier, which is exactly what `_select_chat_primary()`
does today.

**Credit runway at Phase 1:** with $8.0k unspent and burning
< $20 / mo against the credit pool, **runway is effectively
indefinite** — the limiting factor is per-grant expiry (12–24 months
from issue), not exhaustion.

### Phase 2 — Launch (5k – 10k DAU, ~80k MAU midpoint)

**Burn breakdown (with credits, 10k DAU / 80k MAU, 5 % paid):**

| Line | Volume | Unit cost | Subtotal |
|---|---|---|---|
| Free chat (95 % of MAU, ~22 turns each — 3/day soft-cap dominates) | 76k MAU × ~22 turns ≈ 1.67M turns/mo | $0 (workers-ai free quota) | **$0** |
| Paid chat (5 % of MAU × ~28 turns each — monthly 30-cap dominates) | 4k MAU × ~28 turns ≈ 112k turns/mo | $0 today (still on workers-ai head); ~$0.005 / call once #555 lights up vertex | **$0** today, **~$0.6** post-#555 |
| Notebook polish | ~120k calls × 50 % miss = 60k | $0.40 / k | **~$24** |
| Translate | ~150k calls × 50 % miss = 75k | $0.10 / k | **~$7.5** |
| OCR | ~5k uploads | $0.20 / k | **~$1** |
| Voice (paid only, 4k users × 12 sessions × 60 s) | ~48k STT-min + ~12k TTS-min | deepgram $0.43/hr + elevenlabs credits | **~$15** (deepgram) + **$0** (elevenlabs credits) |
| Mongo Atlas M10 + Pinecone serverless tier-1 | always-on | $57 + ~$15 | **~$72** *(80 % covered by Atlas startup credits → ~$15 net)* |
| ACA (0.25 vCPU × ~6 replicas avg, 30 RPS/pod) | ~3,200 vCPU·hrs/mo | $0.04 | **~$13** |
| AWS SES + SQS + Lambda | 400k emails + 200k queue msgs + 1M Lambda invocations | covered partly by Activate | **~$10** |
| GCP Web Risk + Discovery Engine | ~1.5M checks | partially free | **~$8** |
| **Total (with credits)** | | | **~$80–100** ← at the cap |
| **Total (no credits)** | | | **~$700–1,300** |

**Voice revenue offset:** at 4k paid users × ₹100 / mo ≈ **$4,800
gross / month** (USD/INR ≈ 83). Net of Razorpay (~2 %), GST,
refunds: **~$4,400 / mo**. Voice is **net-positive** — $15 cost vs ~$4,400 revenue —
which is the entire reason `require_paid_plan` was wired in #549.

**Credit runway at Phase 2:** $8k pool, ~$60 / mo charged against
credits → **18 months minimum** before any specific grant expires.
The MeterD ladder (60/80/95 %) keeps us under $100 even on a
3× traffic spike day.

### Phase 3 — Growth (10k – 50k DAU, ~240k MAU midpoint)

At 240k MAU, the founder cap **must rise** because the chat-cache
miss tail alone can exceed $100. The phase is gated by **either**
fundraising / partner deal completion **or** a paid-conversion
ramp-up to ≥ 7 %.

**Burn breakdown (with credits, 30k DAU / 240k MAU, 7 % paid):**

| Line | Approx. burn |
|---|---|
| Free chat (workers-ai overage past free Neuron quota) | **$25–60** (first time we pay non-zero for chat — 240k MAU × ~22 turns ≈ 5.3M turns/mo crosses the 10k Neuron/day account quota) |
| Paid chat (post-#555 vertex primary; ~28 turns × 17k paid users ≈ 470k turns/mo) | **$15–40** |
| Notebook polish | **~$80** |
| Translate | **~$25** |
| OCR | **~$5** |
| Voice (deepgram + elevenlabs) | **~$60** (mostly credits) |
| Mongo Atlas M20 + Pinecone | **~$120** |
| ACA + AWS + GCP infra | **~$80** |
| **Total** | **~$300–500 / mo** |

**Required cap raise:** `MONTHLY_TOTAL_USD_CAP` → $500 *(reverting
to the pre-#549 default)*. This is the **only** phase that requires
a `# COST-CAP-OVERRIDE` marker in code and a new task. Net revenue
at 7 % conversion × 240k MAU × ₹100 ≈ **$20k / mo gross** — gross
margin ~94 %.

**Credit runway at Phase 3:** **4–6 months** at this burn rate —
this is the trigger for either (a) the next round of startup-
credit applications (Activate Founders → Activate Portfolio,
Microsoft for Startups Hub → Pegasus, etc.) or (b) a Series A.

### Phase 4 — Scale (50k – 100k DAU, ~600k MAU midpoint)

At this size we are **revenue-positive on infra** and credits are a
nice-to-have, not a survival mechanism.

**Burn breakdown (no-credit assumption, 75k DAU / 600k MAU, 9 % paid):**

| Line | Approx. burn |
|---|---|
| Free chat (post-quota workers-ai overage; tier-routing turns 1–2 = mistral-7b, turns 3+ = llama-3.2-3b keeps it cheap) | **$150–250** |
| Paid chat (post-#555 vertex primary, azure fallback; 54k paid users × ~28 turns ≈ 1.5M turns/mo) | **$80–150** |
| Notebook polish (vertex Gemini 2.5 Flash) | **~$200** |
| Translate (sarvam paid tier) | **~$80** |
| Voice (deepgram + elevenlabs paid) | **~$150** |
| Mongo Atlas M30/M40 + Pinecone p1 | **~$300** |
| ACA (auto-scaled) + AWS + GCP | **~$200** |
| Sentry / observability (post-team-plan tier) | **~$100** |
| **Total** | **~$700–1,000 / mo** ← *no credits applied* |

**Net revenue at 9 % conversion × 600k MAU × ₹150 ≈ $97k / mo
gross**, infra is **< 1 % of revenue**. Credit pool acts as a
buffer for surprise quota events (e.g. exam-day 5× spikes), not a
runway extender.

---

## 3. Credit pool inventory & runway accounting

Per [`provider-credit-matrix.md`](provider-credit-matrix.md) §1
(headline grants — `unverified` cells must be filled in from billing
consoles before this memo is treated as audit-grade):

| Programme | Grant USD | Burn class | Where it covers in this memo |
|---|---:|---|---|
| Google Cloud for Startups | 2,000 | LLM (vertex content_formatter), GCP infra | Phase 2 formatter + GCP infra; primary survivor for content_format under V4 §15 §6 |
| Azure for Startups | 2,500 | LLM (azure_openai chat fallback), ACA, KV | ACA bill (Phases 1–4), chat fallback once #555/#556 lands |
| AWS Activate | 1,000 | Bedrock, SES, SQS, Lambda, S3 | Vision/safety, queue fan-out, transactional email |
| Sarvam Startup Credits | 500 | Assamese LLM + translate primary | Translate (Phases 1–4), assamese chat primary |
| ElevenLabs | 500 | TTS | Voice (Phases 2–4) |
| AssemblyAI | 1,000 | STT | Voice fallback |
| Pinecone | 500 | rerank + vector_search primary | All phases |
| Exa | 1,000 | search_rag primary | RAG retrieval |
| Tavily | 500 | live_search secondary | RAG retrieval |
| MongoDB Atlas | 0 (free + ramp) | DB primary | All phases |
| Cloudflare Workers AI | 0 (free Neuron quota) | Free-user chat, embed fallback, last-resort | All phases — **the most strategically valuable line in the table** |
| **Total quantifiable grants** | **~$8,000** | | |

**Runway formula** (mirrors `provider-credit-matrix.md` §2.1
`runway_score`):

```
runway_months_at_phase_N = sum(remaining_credit_per_provider) /
                          (monthly_burn_at_phase_N - free_quota_savings)
```

Phase-by-phase:

- Phase 1: $8,000 / ~$10 net credit-burn ≈ **800 months** (bounded by
  per-grant expiry at 12–24 months).
- Phase 2: $8,000 / ~$60 ≈ **130 months** (bounded by 18-month expiry).
- Phase 3: $7,500 / ~$300 ≈ **25 months**, but the cap raise itself
  triggers the next funding event.
- Phase 4: revenue-positive — credits become a **per-incident cushion**
  rather than runway.

---

## 4. Sensitivity analysis

| Lever | Δ assumption | Impact on Phase 2 burn | Impact on runway |
|---|---|---|---|
| AI-cache hit rate | 50 % → 30 % | +$15 / mo (~+18 %) | -2 months |
| AI-cache hit rate | 50 % → 70 % | -$15 / mo | +2 months |
| Voice adoption | 5 % → 10 % paid | -$30 / mo (more revenue, marginal cost stays inside elevenlabs/deepgram credits) | revenue-positive |
| Paid conversion | 5 % → 3 % | revenue -$1.9k, infra burn unchanged | runway unchanged |
| Free-user turn 1–2 routing flip from workers-ai → vertex | (would require code change + override) | +$200–400 / mo | -3 months — **this is exactly what the founder lock prevents** |
| Edge chat cap raised (30 → 60) | requires `# COST-CAP-OVERRIDE` | +$40 / mo | -1 month |
| Workers-AI Neuron quota tightened (vendor change) | hypothetical | +$50 / mo at Phase 2 | -1 month |
| Razorpay outage (1 week, paid chat falls back to free tier) | none — degraded UX, not cost | $0 | n/a |
| Vertex re-enabled for chat (sub-tasks #555/#556) | adds Vertex primary path | +$5–25 / mo (vertex Flash is cheap) | -0.5 months |

The model is **most sensitive to the cache-hit-rate assumption** and
to the **workers-ai free-quota assumption**. Both have monitoring in
place: `ai_input_cache.py` reports hit-rate to Sentry; CF Neuron
usage is reported to the admin credit panel
(`routes/admin_credits.py`).

---

## 5. Migration trigger events

The phases are **not** time-bound; they are **traffic-bound**, and
each transition is gated on a measurable trigger.

| From → To | Trigger | Action |
|---|---|---|
| Phase 1 → Phase 2 | Sustained 5k DAU for 14 days **AND** Razorpay live | Flip Bicep ACA `min_replicas` from 2 → 4; enable voice paywall (already live, #549); start 5 % paid push |
| Phase 2 → Phase 3 | Sustained 10k DAU for 30 days **AND** MeterD trips ≥ 60 % twice in a rolling 7 days | (a) approve `# COST-CAP-OVERRIDE` cap raise to $500, (b) open Vertex re-enable sub-tasks #555/#556, (c) fundraise / new credit cycle |
| Phase 3 → Phase 4 | Sustained 50k DAU for 60 days **AND** monthly net revenue > $10k | Raise cap to $2k, scale ACA max_replicas to 60, dual-region (asia-south1 + asia-southeast1) Mongo Atlas |

**Three-stage degradation ladder** (in code today, operational
wiring is sub-task #557):

| % of monthly cap | Action | Implementation status |
|---|---|---|
| 60 % | Pause non-essential batch jobs (re-embed queue, syllabus refresh, weekly digest) | constant in `cost_caps.py`; wiring = #557 |
| 80 % | Voice routes return 503; admin/staff/educator bypass | constant in `cost_caps.py`; wiring = #557 |
| 95 % | Free-tier chat returns 503 with "service degraded" banner; paid + admin keep working | constant in `cost_caps.py`; wiring = #557 |
| 100 % | MeterD LOCKS `chat:cheaponly=1` in Redis; every dispatch reads it | **live today** |

---

## 6. What this memo does *not* commit to

- **Operational wiring of the 60/80/95 ladder** — see sub-task #557.
- **Re-enabling Vertex Gemini on the chat hot path** — see sub-tasks
  #555/#556. Until those land, `CHAT_PRIMARY_OVERRIDE=vertex` is
  *ignored* with a loud Sentry warning (V4 §12 no-silent-fallbacks).
- **Deep Azure surface removal** — see sub-task #553.
- **SES / web-push / observability tier rebalance** — see sub-tasks
  #554 / #556 / #558.
- **Frontend pricing-page work** — separate front-end task (unfiled).
- **Updated grant-balance numbers** — every cell in
  `provider-credit-matrix.md` marked `unverified` is still
  `unverified`. Filling those in is **a prerequisite for using this
  memo in an investor data room**.
- **GST / tax accounting on Razorpay** — out of scope for an infra
  memo; defer to finance.

---

## 7. Reconciliation with Task #549

This memo is the long-form economic justification for the controls
shipped in Task #549. The mapping is exact:

| #549 control | This memo cites it as |
|---|---|
| `MONTHLY_TOTAL_USD_CAP = $100` | §1.1, §2 (Phase 1, Phase 2) |
| `_select_chat_primary()` → `workers_ai_llama32_3b` | §1.2 (free-user chat $0), §4 (sensitivity) |
| `require_paid_plan` on `/voice/{tts,stt,voice}` | §2 Phase 2 voice line — voice is now revenue, not cost |
| `DEGRADATION_PCT_PAUSE_BATCH/VOICE_OFF/FREE_503` constants | §5 ladder table |
| Credit-runway-aware dispatch | §3 runway formula uses the same `runway_score` shape as the matrix |
| CI guard `check_budget_ceiling.py` | §1.1 — every founder lock is mechanically enforced |

If **any** number in this memo drifts from `cost_caps.py`,
`credit_burn_meter.py`, `config.py`, or
`workers/edge-proxy/src/index.ts`, **the code wins** and this memo
must be re-derived from the new constants.

**Validated against code constants on 2026-05-07** —
`CHAT_CAP_MONTHLY=30` (all users), `CHAT_CAP_DAILY=3` (free only,
paid bypass via `CHAT_DAILY_BYPASS_PLANS`), `_select_chat_model`
turns 1–2 → `workers_ai_mistral_7b`, turns 3+ → `_select_chat_primary()`
= `workers_ai_llama32_3b`, `_DEFAULT_MONTHLY_TOTAL_USD_CAP=$100`,
ladder thresholds 0.60 / 0.80 / 0.95.

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
