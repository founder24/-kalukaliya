# Cloudflare Cost Map — Syrabit.ai

**Owner**: infra · **Last reviewed**: 2026-05-03 · **Source of truth** for the
Cloudflare for Startups $5,000 credit pool drawdown.

The Cloudflare for Startups program gives Syrabit.ai:

- **$5,000 in metered-product credits** shared across every metered Cloudflare
  product (Workers AI, R2, Vectorize, KV, D1, Workers requests, Logpush,
  Analytics Engine, Durable Objects, Image Resizing, …). Once spent, every
  one of those products starts charging the card on file.
- **100% off many fixed-cost subscriptions** (Workers Standard Paid,
  Workers Unbound, Bot Management add-on, Advanced Rate Limiting, Cloudflare
  Access seats up to a cap, Zero Trust user seats up to a cap, Stream subscription
  base, Logpush base, Smart Tiered Cache, Argo subscription base — anything
  CF flags as "Startup-included subscription"). These do **not** draw the
  credit pool.
- **3 free Enterprise plan domain upgrades** (Bot Management, custom WAF,
  prioritized DDoS, 100% SLA, Argo Smart Routing, Enterprise support). These
  do **not** draw the credit pool.

The goal of this document is to make every Cloudflare product Syrabit uses
**explicit** about which of those three buckets it falls into, so the $5k
metered pool stays as close to 100% reserved for **Workers AI inference** as
possible. That gives us maximum runway to migrate LLM workloads onto Workers
AI later, and lets us justify a top-up to the startup rep with a clean
"all credits went to Workers AI, here's the growth story" narrative.

> Anything **not** listed here is presumed not in use. If you turn on a new
> Cloudflare product, add it to the table in the same PR.

---

## Bucket legend

| Symbol | Meaning | Credit impact |
|---|---|---|
| 🟢 **FREE** | On the permanent free tier; no startup credits required. | $0 |
| 🟦 **STARTUP-100%** | Startup-program 100%-off subscription; not on the metered pool. | $0 (drawn from subscription discount, not credits) |
| 🟪 **ENT-INCLUDED** | Bundled with the free Enterprise zone plan slot. | $0 |
| 🔴 **CREDIT-BURN** | Drawn from the $5,000 metered credit pool. | counts against $5k |
| ⚪ **PAID-CARD** | No discount — billed to the card on file. | direct dollars |

---

## Inventory

### Edge runtime

| Product | Usage | Bucket | Policy decision |
|---|---|---|---|
| Workers requests | `syrabit-edge` worker on `api.syrabit.ai/*`, `syrabit.ai/*`, `www.syrabit.ai/*`. Smart Placement on, Logpush on, 10% observability sampling. | 🟦 STARTUP-100% (Workers Standard Paid included) | Keep on Standard Paid; **do NOT enable Workers Unbound** unless a single request actually needs >10ms CPU. Unbound bills per CPU-ms and would draw credits. |
| Durable Objects (`RateLimiter`) | Per-key sliding-window rate limit for API and AI routes. | 🔴 CREDIT-BURN (DO requests + storage) | Keep — usage is tiny (<<$5/mo at current traffic). Cap policy: alert if monthly DO line item >$10. |
| Smart Placement | `[placement] mode = "smart"` in `wrangler.toml`. | 🟦 STARTUP-100% | Keep. |
| Workers Logpush | `logpush = true` to the Workers Trace Events dataset. | 🟦 STARTUP-100% (Logpush base subscription included) | Keep. **Verify** in dashboard: sample rate ≤ 100% of error events, ≤ 10% of success events to keep destination egress free. Document any dataset added. |
| Workers Observability | `head_sampling_rate = 0.1`. | 🟪 ENT-INCLUDED on the Enterprise zone | Keep at 10%. Increasing this to 100% would draw credits on the per-request observability cost; do not raise without a documented incident reason. |
| Analytics Engine (`syrabit-edge-metrics`) | Per-request metric writes. | 🔴 CREDIT-BURN (per data point written + read query) | Keep, but only write **one row per request**. Audit any new `writeDataPoint` callers. Cap: alert if AE line item >$5/mo. |

### Storage

| Product | Usage | Bucket | Policy decision |
|---|---|---|---|
| R2 (`syrabit-assets`) | Student PDF uploads via `POST /admin/assets/upload`. Served on `assets.syrabit.ai`. | 🔴 CREDIT-BURN (storage GB + Class A/B ops; egress is free) | Lifecycle rule `assets-cold-to-ia-30d` transitions objects untouched for 30d to Infrequent Access — see [`docs/cloudflare-r2-lifecycle.md`](./cloudflare-r2-lifecycle.md). Batch multipart uploads to keep Class A ops minimal. Cap: alert if R2 line item >$10/mo. |
| R2 (`syrabit-media`) | Generated images / OG cards from the FastAPI backend (`r2_storage.py`). | 🔴 CREDIT-BURN | Same 30d-to-IA lifecycle policy as `syrabit-assets` (rule `media-cold-to-ia-30d`, see [`docs/cloudflare-r2-lifecycle.md`](./cloudflare-r2-lifecycle.md)). Most writes are immutable + small; review steady-state monthly. |
| D1 (`syrabit-content`) | Read replica of the Postgres content tables; ~6 hourly sync via cron. | 🔴 CREDIT-BURN (rows read + storage GB) | Free tier is 5GB storage + 25M rows-read/day. Current volume is well under that. Cap: alert if D1 line item >$5/mo. **Do not** add per-user write paths to D1 — it is a read-only edge cache. |
| KV (`RATE_LIMIT`, `BOT_HTML_CACHE`, `CONTENT_CACHE`) | Rate limit counters, prerendered bot HTML, content cache. | 🔴 CREDIT-BURN (read/write/list ops) | Free tier is 100k reads/day, 1k writes/day per namespace. Bot prerender hits push us above this on traffic spikes. Mitigations already in place: aggressive TTLs, KV monitor with admin alerts (`kv-monitor.ts`). Cap: alert if KV line item >$10/mo. |
| KV `CONTENT_CACHE` `preview_id` mismatch | `preview_id == id` (production) — known issue flagged in `wrangler.toml`. | n/a (correctness bug, not a billing one) | Out of scope here; tracked in `wrangler.toml` comment. |

### AI / inference

| Product | Usage | Bucket | Policy decision |
|---|---|---|---|
| **Workers AI** (the `[ai]` binding) | Auto-fallback fan-out from FastAPI when Vertex/Gemini/Sarvam fails: chat (`llama-3.3-70b-instruct-fp8-fast`), embed (`bge-large-en-v1.5`), STT (`whisper-large-v3-turbo`), TTS (`melotts`). | 🔴 **CREDIT-BURN** (this is the line item we are protecting) | **Reserve the $5k pool for this.** Tag every call via AI Gateway (`WORKERS_AI_GATEWAY_ID` env on the worker) so the invoice clearly attributes the spend. Migrate primary LLM workloads onto Workers AI only after a separate task evaluates per-token cost vs Vertex / Gemini. |
| Vectorize (`syllabus-index-v2`, 1024-dim) | Primary semantic-search index, queried from edge worker (`SYLLABUS_INDEX` binding) and from the FastAPI backend (`vectorize_client.py`). | 🔴 CREDIT-BURN (stored vectors + queried vectors / month) | **Keep.** Cap: monitor monthly stored-vector count and query volume; alert if Vectorize line item >$15/mo. |
| Vectorize (`syllabus-index`, 768-dim, legacy) | Rollback fallback only; queried from the worker as `SYLLABUS_INDEX_LEGACY` when v2 returns no matches. | 🔴 CREDIT-BURN (idle storage) | **Retire** once v2 has been the sole live index for 60 consecutive days with zero rollback events. Tracked as a follow-up task; not deleted yet because re-embedding on rollback is expensive. Until then, do not write new vectors to it. |
| AI Gateway | Currently NOT routing Workers AI calls; backend talks to providers directly. Gateway ID env added (`WORKERS_AI_GATEWAY_ID`) for the edge worker. | 🟦 STARTUP-100% (gateway requests are free; cached responses don't re-bill upstream) | **Provision a gateway** in the dashboard (e.g. `syrabit-ai-gw`), enable response caching with TTL ≥ 1h for embeddings + classification + repeat student questions, then set `WORKERS_AI_GATEWAY_ID=syrabit-ai-gw` as a worker env var so Workers AI calls route through it. This both **caches** repeat hits (no credit re-spend) and **tags** the invoice line item. |

### DNS / WAF / security

| Product | Usage | Bucket | Policy decision |
|---|---|---|---|
| `syrabit.ai` zone — DNS, free SSL, basic WAF, basic DDoS | All Syrabit traffic routes through this zone. | 🟪 **ENT-INCLUDED** once the free Enterprise upgrade is applied to this zone | **Action:** apply one of the 3 free Enterprise domain slots to apex `syrabit.ai`. Confirm Bot Management, advanced WAF, prioritized DDoS, 100% SLA, Argo Smart Routing, and Enterprise support are live. |
| Bot Management add-on | Currently using the free heuristic `cf.verifiedBot` flag. | 🟪 ENT-INCLUDED after the upgrade | After Enterprise upgrade: enable Super Bot Fight Mode + Bot Analytics; do **not** enable JS Detections on `/api/*` routes (would break the FastAPI clients). |
| Cloudflare Access | `cf_access.py` admin route protection (Task #637). | 🟦 STARTUP-100% (Access seats up to startup cap) | Keep. Audit seat count quarterly to stay under cap. |
| Cloudflare Tunnel | Not in use. | n/a | If we adopt it for the Cloud Run / Railway origins, it remains 🟦 under the Access subscription. |
| WAF custom rulesets | A handful of country-of-origin and path rules. | 🟪 ENT-INCLUDED after the upgrade | Keep current rules; document any new rule in the same PR that adds it. |
| Rate Limiting (advanced) | Not used today (DO-based limiter does the job). | 🟦 STARTUP-100% (locked-but-free per startup rep) | **Action:** request unlock from the startup team email; once unlocked, evaluate whether the DO limiter can be retired in favour of the dashboard rules (would remove a paid DO line item). |
| SSL for SaaS | Not used today. | 🟦 STARTUP-100% (locked-but-free per startup rep) | **Action:** request unlock from the startup team email so it is available the day we offer custom student domains. |
| Advanced Certificate Manager | Not used today. | 🟦 STARTUP-100% (locked-but-free per startup rep) | **Action:** request unlock; useful when we need wildcard certs for `*.assets.syrabit.ai` style fan-out. |

### Pages / frontend

| Product | Usage | Bucket | Policy decision |
|---|---|---|---|
| Cloudflare Pages (`syrabit-zip-convert.pages.dev`) | The SPA build is hosted here and proxied by the edge worker (`PAGES_ORIGIN`). | 🟢 FREE (Pages requests are free; build minutes inside the free monthly cap) | Keep. Do **not** front Pages directly with a custom domain — the worker is the single ingress. |
| Pages Functions | Not in use (worker handles everything). | n/a | Avoid — splitting logic across Pages Functions and the edge worker creates two billable surfaces with two cache domains. |

### Observability outside the edge worker

| Product | Usage | Bucket | Policy decision |
|---|---|---|---|
| GraphQL Analytics API | Read-only queries from `cf_enterprise.py` and the admin dashboard. | 🟢 FREE | Keep. |
| Logpush destinations (R2 / S3 / external) | Logpush dataset writes ship to R2 `syrabit-media` under the `logpush/` prefix. | 🔴 CREDIT-BURN (R2 storage of log data) | Lifecycle rule `media-logpush-delete-14d` deletes objects under `logpush/` after 14d — see [`docs/cloudflare-r2-lifecycle.md`](./cloudflare-r2-lifecycle.md). Cap: alert if Logpush-driven R2 storage >5GB. |

---

## Sampling & retention rationale

| Knob | Current value | Why |
|---|---|---|
| Workers Observability `head_sampling_rate` | `0.1` (10%) | Enterprise-included quota covers 100% but the surface area is large; 10% is enough to populate latency / flame-graph views without ever brushing the per-request observability cost band that exists outside the included quota. Raise temporarily during an incident, then drop back. |
| Logpush dataset filter | All Workers Trace Events | Logpush base subscription is included; the credit risk is the **destination** (R2). Mitigation is the 14-day lifecycle rule above. |
| Analytics Engine writes per request | 1 | More than 1 multiplies the per-data-point cost. New `writeDataPoint` callers must be reviewed in PR. |
| KV `BOT_HTML_CACHE` TTL | Per-route (see `monitored-urls.json`) | Long enough to keep bot prerender hit-rate ≥ 90% (watchdog at `bot-cache-alert.ts`); raising further would mean older content served to crawlers. |
| D1 sync cron | `0 */6 * * *` (every 6h) | Each sync is a burst of D1 writes. 6h is the lowest cadence that keeps SEO content fresh enough; raising to hourly multiplies D1 write volume by 6x. |

---

## Workers AI invoice tagging

The edge worker now passes a `gateway` option on every `env.AI.run(...)` call
when `WORKERS_AI_GATEWAY_ID` is set, with one of these tags in
`metadata.tag`:

| Tag | Source path |
|---|---|
| `workers-ai-fallback:chat` | `/api/ai/fallback/chat` (FastAPI primary chat provider failed) |
| `workers-ai-fallback:embed` | `/api/ai/fallback/embed` (FastAPI primary embed provider failed) |
| `workers-ai-fallback:stt` | `/api/ai/fallback/stt` (Sarvam STT failed) |
| `workers-ai-fallback:tts` | `/api/ai/fallback/tts` (Sarvam TTS failed) |
| `workers-ai-edge-vector-search` | `/api/edge/vector-search` (worker-side semantic search) |

Backend-initiated Workers AI calls (none today) should send the same gateway
ID + a tag of the form `workers-ai-backend:<route>` so the invoice slice is
contiguous.

---

## Out of scope (deliberate)

- Migrating any LLM workload from Vertex / Gemini / Sarvam onto Workers AI
  as the **primary** provider — separate future task. This document only
  *preserves* the credit pool for that migration.
- Negotiating partner-tier upgrades ($25k tier via NVIDIA Inception /
  Microsoft for Startups / VC nomination) — separate task.
- Moving the API off Railway / Cloud Run onto Workers — out of scope.
- Buying new domains through Cloudflare Registrar (not credit-eligible
  anyway).

---

## Related docs

- `docs/cloudflare-r2-lifecycle.md` — concrete R2 lifecycle rules (30d
  Standard → IA on `syrabit-assets` + `syrabit-media`; 14d delete on the
  Logpush prefix), with `wrangler` JSON and dashboard steps. Backed by
  version-controlled config in `infra/r2-lifecycle/` (apply with
  `./infra/r2-lifecycle/apply.sh`).
- `docs/cloudflare-startup-credits-emails.md` — drafts of the unlock-features
  email and the month-9 top-up email.
- `docs/cloudflare-monthly-cost-review.md` — checklist run on the first
  business day of each month.
- `workers/edge-proxy/docs/cloudflare.md` — feature-by-feature edge inventory
  (the "what is on" view, complementary to the "what does it cost" view here).
