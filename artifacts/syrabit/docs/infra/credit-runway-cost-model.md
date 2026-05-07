# Phased Credit-Runway Cost Model — Syrabit.ai

**Version:** 1.0 — 2026-05-07
**Status:** Fundraising-grade memo (Task #550). Quarterly review cadence —
next scheduled review **2026-08-07**; the owner (infra) MUST re-derive
every number in §4 / §5 from live `cost_caps.py`, `credit_burn_meter.py`,
`workers/edge-proxy/src/index.ts`, and `provider-credit-matrix.md` at
each cadence cycle, or sooner if a `# COST-CAP-OVERRIDE` lands.
**Source of truth:** [`../../../../infra/v4-locked-architecture.md`](../../../../infra/v4-locked-architecture.md). If anything below disagrees with V4, V4 wins.
**Audience:** founders, prospective investors, infra reviewers.
**Author / owner:** infra.

---

## Revision history

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-05-07 | infra | Initial memo. Reconciles with Task #549 ($100/mo cap, voice paywall, workers_ai chat head, 60/80/95 % degradation ladder, CI guard `check_budget_ceiling.py`). Validated against in-tree code constants on the same date. |

---

## TL;DR

Syrabit operates under a **founder-locked $100 / month spend ceiling**
(Task #549) layered on top of ~$8.0k of unspent multi-cloud startup
credits. Combined with the edge chat cap (`CHAT_CAP_MONTHLY=30` for
all users, `CHAT_CAP_DAILY=3` for free users), the K.2 deterministic
AI-cache, and tier-routing of free-user chat to Cloudflare Workers
AI, this gives **18–24 months of credit runway at 10k DAU with no
fundraising required**. The plan below extends the same controls
through 100k DAU.

| Phase | DAU range | Effective MAU | Credit-on monthly burn | Credits-off monthly burn | Cost / DAU (credit-on) |
|---|---|---|---|---|---|
| 1 — Pilot       | 1k – 5k    | 8k – 40k     | **$60 – $90**     | $300 – $500    | **$0.020 – $0.030** |
| 2 — Launch      | 5k – 10k   | 40k – 80k    | **$80 – $100** *(cap)* | $700 – $1,300  | **$0.010 – $0.012** |
| 3 — Growth      | 10k – 50k  | 80k – 400k   | **$300 – $500**   | $4k – $7k      | **$0.010 – $0.012** |
| 4 — Scale       | 50k – 100k | 400k – 800k  | **$700 – $1,000** | $9k – $14k     | **$0.009 – $0.013** |

The four-cloud cost-share remains the V4-locked **40 % Cloudflare /
30 % Azure / 20 % AWS / 10 % GCP** through every phase. Voice (TTS /
STT / `/voice/voice`) is paywalled to paid plans (#549) and is a
revenue centre, not a cost centre.

---

## 1. Cost framework — founder-locked controls

| Control | Where | Value | Guard |
|---|---|---|---|
| Monthly USD ceiling | `cost_caps._DEFAULT_MONTHLY_TOTAL_USD_CAP`, `credit_burn_meter.MeterDConfig.cap_usd` | **$100** | `scripts/check_budget_ceiling.py` (CI: backend-tests + ACA deploy) |
| Per-call token budgets | `cost_caps.TOKEN_BUDGETS` | chat 3000/800, content 4000/2000, formatter 4500/2500, translate 2000/2000, OCR 1500/800, STT 2000/500 | `tests/test_cost_caps.py` requires `# COST-CAP-OVERRIDE` marker + Sentry changelog to raise |
| Edge chat caps | `workers/edge-proxy/src/index.ts` lines 670–701 | `CHAT_CAP_MONTHLY=30` (all users); `CHAT_CAP_DAILY=3` (free only — paid bypass via `CHAT_DAILY_BYPASS_PLANS`) | Same `# COST-CAP-OVERRIDE` policy |
| Free-tier chat primary | `cost_caps._select_chat_primary()` | `workers_ai_llama32_3b` (CF free Neuron quota) | `check_budget_ceiling.py` asserts head ∈ `{workers_ai*, vertex}` |
| Free-tier chat warm-up (turns 1–2) | `cost_caps._select_chat_model` lines 352–358 | `workers_ai_mistral_7b` | `tests/test_tier_routing_dispatch.py` |
| Voice paywall | `auth_deps.require_paid_plan` on `/voice/{tts,stt,voice}` | 402 free; admin/staff/educator bypass | `tests/test_voice_paid_gate.py` + `check_budget_ceiling.py` |
| Three-stage degradation ladder | `cost_caps.DEGRADATION_PCT_PAUSE_BATCH/VOICE_OFF/FREE_503` | 60 % / 80 % / 95 % of monthly cap | Strict-monotone check in `check_budget_ceiling.py`. **Operational wiring** is sub-task **#557**. |
| Hard stop @ 100 % | MeterD → Redis `chat:cheaponly=1`; `_select_chat_model` reads on every dispatch | LOCK | `tests/test_provider_priority_locked.py` |

---

## 2. Phase model — provider-level burn tables

> All four phase tables below use the **strict-specialist provider set
> permitted by Task #549**: Cloudflare Workers AI (chat free-tier),
> Sarvam (Assamese / translate primary), Vertex Gemini 2.5 Flash
> (`content_format` only — chat re-enable is sub-task #555/#556),
> Deepgram (STT, paid voice), ElevenLabs (TTS, paid voice), Pinecone
> (rerank + vector_search), MongoDB Atlas (DB), and the four hyperscaler
> infra lines (Cloudflare, Azure, AWS, GCP). **Providers explicitly
> excluded by Task #491 / #549** — Cerebras, Cohere, Voyage-AI,
> AssemblyAI, Exa, Tavily, Azure OpenAI on the chat hot path, Bedrock
> on the chat hot path — do **not** appear here. The CI guards
> `scripts/check_dead_providers.py` and `scripts/check_budget_ceiling.py`
> mechanically enforce this.

Held constant across phases unless overridden:

- **DAU → MAU ratio:** 8× (study-routine app w/ weekly exam cycles).
- **Chat usage / MAU:** free users ~22 turns/mo (3/day soft cap dominates); paid users ~28 turns/mo (monthly 30 cap dominates).
- **AI-cache hit rate:** 50 % on formatter / translate / OCR (live chat: 0 % by policy).
- **Voice adoption (paid):** 5 % of MAU sustained, 60 sec / session, 3 sessions / week.
- **Paid conversion:** P1 = 0 %, P2 = 5 %, P3 = 7 %, P4 = 9 %.
- **Email volume:** ~5 transactional / MAU / month.
- **Egress:** Cloudflare CDN cache-hit ≥ 90 %.

### Phase 1 — Pilot (1k – 5k DAU, ~24k MAU midpoint)

| Provider | Workload | Monthly volume | Credit-on cost | Credits-off cost |
|---|---|---|---|---|
| **Cloudflare Workers AI** | All English chat (turns 1–2 = mistral-7b, turns 3+ = llama-3.2-3b) + embed | 720k chat turns + 120k embeds | **$0** (free Neuron quota) | $200–350 |
| **Sarvam** | Assamese chat + En↔As translate (50 % cache miss) | 20k post-cache calls | **$2** | $8 |
| **Vertex Gemini 2.5 Flash** | `content_formatter` (notebook polish) | 15k post-cache calls | **$6** | $17 |
| **Pinecone** | rerank + vector search | ~250k queries | **$0** (startup credits) | $15 |
| **MongoDB Atlas** | DB primary | M0 always-on | **$0** (free tier) | $10 |
| **Cloudflare zone + R2 + CDN** | edge, static, cache | covered | **$0** (annual zone) | $40 |
| **Azure (ACA only)** | 0.25 vCPU × 2–4 replicas | ~720 vCPU·hrs | **$28** | $28 |
| **AWS (SES + SQS)** | ~120k emails + ~30k queue msgs | (no Lambda yet) | **$12** (Activate) | $40 |
| **GCP (Web Risk + Discovery Engine + Cloud Trace)** | ~200k checks | mostly free tier | **$5** | $30 |
| **Sentry / OTEL** | covered (team plan) | n/a | **$0** | $0 |
| **Phase 1 total** | | | **$53–90** | **$308–510** |
| **Cost / DAU** (3k DAU midpoint) | | | **$0.018–0.030** | $0.10–0.17 |

### Phase 2 — Launch (5k – 10k DAU, ~80k MAU midpoint, 5 % paid)

| Provider | Workload | Monthly volume | Credit-on cost | Credits-off cost |
|---|---|---|---|---|
| **Cloudflare Workers AI** | Free chat (76k MAU × ~22 turns ≈ 1.67M) + paid chat (4k × ~28 ≈ 112k) + embed | 1.78M chat turns | **$0** (free quota; just under daily Neuron ceiling at this size) | $480–950 |
| **Sarvam** | Assamese chat + translate (75k post-cache) | 75k calls | **$7.5** | $30 |
| **Vertex Gemini 2.5 Flash** | `content_formatter` polish | 60k post-cache | **$24** | $66 |
| **Deepgram** | Paid STT — 4k paid × 12 sessions × 60 s ≈ 48k STT-min | ~800 STT-hrs | **$15** (mostly credits) | $345 |
| **ElevenLabs** | Paid TTS — ~12k TTS-min | ~12k char-quota | **$0** (credits cover) | $60 |
| **Pinecone** | rerank + vector search | ~2.5M queries | **$0** (credits) | $40 |
| **MongoDB Atlas** | M10 cluster | always-on | **$15** (post-credit) | $72 |
| **Cloudflare zone + R2 + CDN** | edge, static | covered | **$0** | $80 |
| **Azure (ACA only)** | 0.25 vCPU × ~6 replicas avg | ~3,200 vCPU·hrs | **$13** | $130 |
| **AWS (SES + SQS + Lambda)** | 400k emails + 200k msgs + 1M Lambda invocations | covered partly | **$10** | $80 |
| **GCP (Web Risk + Discovery Engine)** | ~1.5M checks | partially free | **$8** | $40 |
| **Phase 2 total** | | | **$92** ← inside the $100 cap | **$1,343** |
| **Cost / DAU** (10k DAU) | | | **$0.0092** | $0.134 |

**Voice revenue offset (Phase 2):** 4k paid × ₹100 / mo ≈ $4,800 gross
(USD/INR ≈ 83); net of Razorpay (~2 %), GST, refunds: **~$4,400 / mo**.
Voice cost: ~$15. Voice is **net-positive — exactly the reason
`require_paid_plan` was wired in #549**.

### Phase 3 — Growth (10k – 50k DAU, ~240k MAU midpoint, 7 % paid)

> **Cap raise required at this phase.** `MONTHLY_TOTAL_USD_CAP` →
> **$500** (reverting to the pre-#549 default). This is the only phase
> that needs a `# COST-CAP-OVERRIDE: phase-3-growth-trigger` marker
> in code AND a Sentry-annotated changelog entry per
> `tests/test_cost_caps.py`.

| Provider | Workload | Credit-on cost | Credits-off cost |
|---|---|---|---|
| **Cloudflare Workers AI** | Free chat overage past 10k Neuron/day quota | **$25–60** ← first non-zero chat line | $1,800–3,600 |
| **Sarvam** | Translate + Assamese chat | **$25** | $120 |
| **Vertex Gemini 2.5 Flash** | content_formatter | **$80** | $250 |
| **Deepgram** | Paid STT (16.8k paid × 12 × 60 s) | **$60** (credits running thin) | $1,400 |
| **ElevenLabs** | Paid TTS | **$0–25** (credits depleting) | $250 |
| **Pinecone** | rerank + vector search (paid tier 1) | **$30** (credits drained ~Q3) | $120 |
| **MongoDB Atlas** | M20 | **$120** | $310 |
| **Azure (ACA, ~12 replicas avg)** | ~7,000 vCPU·hrs | **$50** | $290 |
| **AWS** | SES + SQS + Lambda + S3 | **$30** | $250 |
| **GCP** | Web Risk + Discovery Engine | **$15** | $80 |
| **Cloudflare zone + R2 + CDN** | covered | **$0** | $200 |
| **Phase 3 total** | | **$335–470** | **$5,070–7,070** |
| **Cost / DAU** (30k DAU) | | **$0.011–0.016** | $0.169–0.236 |

**Net revenue at 7 % conversion × 240k MAU × ₹100 ≈ $20k / mo gross**
— infra is < 3 % of revenue. Gross margin ~94 %.

### Phase 4 — Scale (50k – 100k DAU, ~600k MAU midpoint, 9 % paid)

> Credits are no longer the binding constraint at this size — the
> table is presented **credits-off** because by Phase 4 the unspent
> grant pool is mostly exhausted and revenue covers the bill ~100×
> over.

| Provider | Workload | Monthly cost (credits-off) |
|---|---|---|
| **Cloudflare Workers AI** | Free chat (post-quota overage; tier-routing keeps it cheap) | **$150–250** |
| **Sarvam** | Translate + Assamese chat (paid tier) | **$80** |
| **Vertex Gemini 2.5 Flash** | content_formatter at scale | **$200** |
| **Deepgram** | Paid STT (54k paid × 12 sessions × 60 s) | **$120** |
| **ElevenLabs** | Paid TTS | **$30** |
| **Pinecone** | rerank + vector search (p1 cluster) | **$140** |
| **MongoDB Atlas** | M30/M40 | **$300** |
| **Azure (ACA, ~24 replicas avg)** | ~17,000 vCPU·hrs | **$120** |
| **AWS** | SES + SQS + Lambda + S3 + Glacier | **$80** |
| **GCP** | Web Risk + Discovery Engine + Cloud Trace | **$50** |
| **Cloudflare zone + R2 + CDN** | edge, static | **$50** |
| **Sentry / OTEL** | post-team-plan tier | **$100** |
| **Phase 4 total** | | **$1,420** |
| **Cost / DAU** (75k DAU) | | **$0.019** |

**Net revenue at 9 % conversion × 600k MAU × ₹150 ≈ $97k / mo gross**
— infra is **< 2 % of revenue**. Credit pool acts as a **per-incident
cushion** for surprise quota events (exam-day 5× spikes), not as
runway.

---

## 3. Credit pool inventory & runway accounting

Per [`provider-credit-matrix.md`](provider-credit-matrix.md) §1.
`unverified` cells must be filled in from billing consoles before
this memo is treated as audit-grade for an investor data room.

| Programme | Grant USD | Burn class | Phase coverage |
|---|---:|---|---|
| Google Cloud for Startups | 2,000 | Vertex content_formatter, GCP infra | All phases — content polish + Web Risk + Discovery Engine |
| Azure for Startups | 2,500 | ACA, KV (no chat OpenAI in current chain — kept for #555/#556 re-enable) | All phases |
| AWS Activate | 1,000 | SES, SQS, Lambda, S3, Glacier | All phases |
| Sarvam Startup Credits | 500 | Assamese LLM + translate primary | All phases |
| ElevenLabs | 500 | Paid TTS | Phases 2–4 |
| Deepgram | 500 | Paid STT | Phases 2–4 |
| Pinecone | 500 | rerank + vector_search primary | All phases |
| MongoDB Atlas | 0 (free + ramp) | DB primary | All phases |
| Cloudflare Workers AI | 0 (free Neuron quota) | Free-user chat, embed fallback | **All phases — the most strategically valuable line** |
| **Total quantifiable grants** | **~$7,500** | | |

**Runway formula** (mirrors `provider-credit-matrix.md` §2.1
`runway_score`):

```
runway_months_at_phase_N = sum(remaining_credit_per_provider) /
                          (monthly_burn_at_phase_N - free_quota_savings)
```

| Phase | Monthly credit burn | Runway | Bounding factor |
|---|---|---|---|
| 1 | ~$10 | ~750 months | 12–24-month per-grant expiry |
| 2 | ~$60 | ~125 months | 18-month per-grant expiry → effectively 18 mo |
| 3 | ~$300 | ~25 months | Cap raise itself triggers next funding event |
| 4 | revenue-positive | n/a | Credits become per-incident cushion |

---

## 4. Cost-per-DAU table

| Phase | DAU midpoint | Credits-on $/DAU/mo | Credits-off $/DAU/mo | Marginal $/incremental DAU |
|---|---|---|---|---|
| 1 — Pilot   | 3k   | **$0.018–0.030** | $0.10–0.17 | ~$0.005 (chat $0, polish on credits) |
| 2 — Launch  | 10k  | **$0.0092**      | $0.134     | ~$0.006 |
| 3 — Growth  | 30k  | **$0.011–0.016** | $0.169–0.236 | ~$0.011 (Workers-AI overage starts) |
| 4 — Scale   | 75k  | **$0.019** *(credits-off; credits exhausted by here)* | $0.019 | ~$0.018 |

The **marginal $/DAU** falls then rises as we cross the Cloudflare
free-Neuron daily quota (~10k Neurons/day per account) at Phase 3.
Below that quota, every incremental DAU is essentially free; above
it, every incremental free chat turn marginally bills against
Workers-AI overage at ~$0.0003–0.0007 / 1k output tokens.

---

## 5. Sensitivity analysis

### 5.1 Cache-hit-rate grid — credit-on monthly burn at each phase

`ai_input_cache.py` reports the live hit rate to Sentry; default
assumption is 50 %. Lower hit rates push polish + translate volume
through paid providers.

| Cache hit rate | Phase 1 burn | Phase 2 burn | Phase 3 burn | Phase 4 burn (no credits) |
|---|---|---|---|---|
| **30 %** | $73–110 | **$110** ← breaches $100 cap, MeterD ladder fires | $400–550 | $1,540 |
| **50 %** *(baseline)* | $60–90 | $80–100 | $300–500 | $1,420 |
| **70 %** | $50–75 | $70–85 | $250–410 | $1,330 |
| **85 %** | $42–62 | $62–75 | $215–355 | $1,265 |

**Key insight:** at 30 % cache hit rate, Phase 2 trips the 95 % ladder
(`DEGRADATION_PCT_FREE_503`) and free-tier chat starts returning 503.
This means the cache hit rate is itself a **product-availability
control surface**, not just a cost knob — the threshold to maintain
is **≥ 45 % hit rate at all phases** to keep free chat green.

### 5.2 Voice adoption slider — Phase 2 cost & revenue

| % of MAU on voice | Voice cost | Voice gross revenue (paid plan ₹100) | Net contribution |
|---|---|---|---|
| 2 %  | $6   | $1,920 | **+$1,914** |
| 5 % *(baseline)* | $15  | $4,800 | **+$4,785** |
| 10 % | $30  | $9,600 | **+$9,570** |
| 20 % | $60  | $19,200 | **+$19,140** |

Voice is the **single largest revenue lever** in the model. Doubling
adoption to 10 % adds ~$5k / mo net at Phase 2 — more than the
entire infra bill across all phases combined.

### 5.3 Paid-conversion slider — Phase 2 net P&L

| Conversion rate | Paid users (80k MAU) | Gross revenue ₹100 | Infra burn | Net |
|---|---|---|---|---|
| 2 % | 1.6k | $1,920 | $90 | **+$1,830** |
| 5 % *(baseline)* | 4.0k | $4,800 | $92 | **+$4,708** |
| 7 % | 5.6k | $6,720 | $95 | **+$6,625** |
| 10 % | 8.0k | $9,600 | $98 | **+$9,502** |

Even at **2 % paid conversion**, the company is gross-margin positive
on a unit-economics basis at Phase 2. The model breaks **only** if
paid conversion stays at 0 % past Phase 1 — at which point the cap
raise gates the Phase 2 → 3 transition and we hold at 10k DAU until
conversion materialises.

---

## 6. Migration triggers — credit-runway thresholds

Each row is **alarm → owner → SLA → action**. Pager source is the
existing Slack alerter (`#syrabit-oncall`) backed by Sentry alerts
on `db.shadow.diff`, `meter_d.cap_pct`, and the per-grant runway
counters in `routes/admin_credits.py`.

| Trigger | Pager / channel | Owner | Response SLA | Action |
|---|---|---|---|---|
| **GCP credit balance < 90 days runway** | Slack `#syrabit-oncall` (Cloud Billing API alert via `gcp_billing.py`) | infra | 72 h | Open Google Cloud for Startups extension request OR cap Vertex content_formatter calls 50 % via `content_formatter` fallback flag |
| **MongoDB Atlas credit balance < 6 months** | Slack `#syrabit-oncall` (Atlas console webhook) | infra | 1 week | Submit Atlas startup-credit renewal OR plan downgrade M10 → M0 (acceptable for ≤ 5k DAU) |
| **Pinecone credit balance < 6 months** | Slack `#syrabit-oncall` (Pinecone billing webhook) | infra | 1 week | Submit Pinecone startup-credit renewal OR migrate rerank to Workers-AI fallback (latency penalty acknowledged in matrix §2.1) |
| **Sarvam credit balance < 3 months** | Slack `#syrabit-oncall` (manual cron) | infra | 1 week | Submit renewal OR shift Assamese chat to `workers_ai_indic` last-resort tier (degraded UX — fail loud, V4 §12) |
| **Deepgram + ElevenLabs combined < 3 months runway** | Slack `#syrabit-oncall` | infra + product | 72 h | Raise paid voice price OR contract paid commits with vendor |
| **MeterD trips ≥ 60 % twice in 7 days** | PagerDuty (Sentry alert on `meter_d.cap_pct ≥ 0.6`) | infra (primary), founder (escalation) | 24 h | Investigate cache-hit drift (5.1); if structural, open `# COST-CAP-OVERRIDE` task for Phase 2 → 3 cap raise |
| **MeterD trips ≥ 80 %** | PagerDuty + founder SMS | infra + founder | 4 h | Voice-off ladder fires automatically (#557); manual review of which paid users to comp |
| **MeterD trips ≥ 95 %** | PagerDuty + founder SMS + email | infra + founder | 1 h | Free-tier 503 fires automatically (#557); status page update; root-cause within 24 h |
| **MeterD LOCKS @ 100 %** | PagerDuty critical | infra + founder | immediate | `chat:cheaponly=1` automatic; manual decision: ride out month vs `# COST-CAP-OVERRIDE` |
| **Per-phase DAU sustained for 14/30/60 days** | Datadog/Sentry analytics dashboard | founder + infra | 1 sprint | Phase transition checklist (Phase 1 → 2: voice paywall live + Razorpay live; Phase 2 → 3: cap raise + fundraise; Phase 3 → 4: dual-region Mongo + ACA scale-out) |

---

## 7. Reconciliation with Task #549 founder-locks

| #549 control | This memo cites it as |
|---|---|
| `MONTHLY_TOTAL_USD_CAP = $100` default | §1, §2 (Phase 1 + Phase 2 totals), §5.1 (cap-breach ladder) |
| `_select_chat_primary()` → `workers_ai_llama32_3b` | §1 (free-user chat $0), §2 (every phase chat row), §5.1 (sensitivity) |
| `_select_chat_model` turns 1–2 → `workers_ai_mistral_7b` | §1, §2 footnote |
| `require_paid_plan` on `/voice/*` | §2 Phase 2 — voice as revenue, not cost |
| `DEGRADATION_PCT_PAUSE_BATCH/VOICE_OFF/FREE_503` | §6 trigger ladder |
| Credit-runway-aware dispatch | §3 runway formula = `runway_score` shape |
| CI guard `check_budget_ceiling.py` | §1 — every founder-lock mechanically enforced; **a cap raise must update both the code constant AND this memo or the next quarterly review will catch the drift** |

---

## 8. What this memo does NOT promise

- **Operational wiring of the 60/80/95 ladder** → sub-task **#557**.
- **Re-enabling Vertex Gemini on the chat hot path** → sub-tasks
  **#555/#556**. Until those land, `CHAT_PRIMARY_OVERRIDE=vertex` is
  *ignored* with a loud Sentry warning (V4 §12 no-silent-fallbacks).
- **Deep Azure surface removal** (kill OpenAI dispatch branch + KV
  rotation) → sub-task **#553**.
- **SES / web-push / observability tier rebalance** → sub-tasks
  **#554 / #556 / #558**.
- **Frontend pricing-page work** → separate front-end task (unfiled).
- **Verified credit balances.** Every cell in
  `provider-credit-matrix.md` marked `unverified` is still
  `unverified` — filling those in is **a prerequisite for using this
  memo in an investor data room**.
- **GST / tax accounting on Razorpay** — out of scope; defer to
  finance.

---

**Validated against code constants on 2026-05-07** —
`CHAT_CAP_MONTHLY=30` (all users, edge-proxy lines 670–701);
`CHAT_CAP_DAILY=3` (free only, paid bypass via
`CHAT_DAILY_BYPASS_PLANS`); `_select_chat_model` turns 1–2 →
`workers_ai_mistral_7b`, turns 3+ → `_select_chat_primary()` =
`workers_ai_llama32_3b`; `_DEFAULT_MONTHLY_TOTAL_USD_CAP=$100`
(cost_caps.py); `MeterDConfig.cap_usd=$100` (credit_burn_meter.py);
ladder thresholds 0.60 / 0.80 / 0.95 strict-monotone (CI-enforced).

If **any** number above drifts from `cost_caps.py`,
`credit_burn_meter.py`, `config.py`, or
`workers/edge-proxy/src/index.ts`, **the code wins** and this memo
must be re-derived from the new constants on the next quarterly
cycle (or sooner if a `# COST-CAP-OVERRIDE` lands).

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
