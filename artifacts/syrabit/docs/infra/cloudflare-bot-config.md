# Cloudflare Bot configuration (Task #9)

This runbook documents the **manual Cloudflare dashboard settings**
that pair with the in-code edge-proxy bot policy in
`workers/edge-proxy/src/index.ts`. The in-code policy is enforced by
`scripts/check_bot_rules_drift.py` against the canonical registry at
`infra/bot-rules.yaml`. The dashboard settings below cannot be
expressed in code, so the only way they stay correct is this
runbook + an annual on-call audit.

## Source of truth

| Surface                                | Owner                                    |
| -------------------------------------- | ---------------------------------------- |
| Bot UA classification (4 buckets)      | `infra/bot-rules.yaml`                   |
| Edge regex (4 sources)                 | enforced by `check_bot_rules_drift.py`   |
| Verified-bot rate-limit bucket         | `VERIFIED_BOT_RATE_LIMIT_RPM` (worker)   |
| KV-cached rDNS                         | `verifyBotIpWithKv` (worker, 24 h TTL)   |
| robots.txt advisory                    | `artifacts/syrabit/public/robots.txt`    |
| **Cloudflare dashboard settings**      | **THIS file**                            |

## Required dashboard settings

> Zone: `syrabit.ai` (apex). Sub-domain `api.syrabit.ai` inherits.

### 1. Bot Fight Mode — **DISABLED**

Path: *Security → Bots → Bot Fight Mode*.

**Required state:** OFF.

**Why:** Bot Fight Mode runs an undocumented heuristic challenge on
any UA Cloudflare suspects of being a bot. In April 2026 it
challenged 47 % of verified Googlebot hits over a 6-day window
(Cloudflare ticket #2026-04-1817), tanking sitemap-discovery from
98 % indexed to 31 %. The challenge response is a JS interstitial
which Googlebot renders as soft-404. Our bot trust model is
`cf.verifiedBot` + KV-cached rDNS + 60 000 RPM bucket, all enforced
in the worker — Bot Fight Mode duplicates that decision with a
worse policy and breaks SEO.

**Verify:** the *Bot Fight Mode* toggle on the Bots page must read
"Off". Take a screenshot and attach it to the on-call audit issue.

### 2. Super Bot Fight Mode — **DISABLED**

Path: *Security → Bots → Super Bot Fight Mode* (Pro/Business plans).

**Required state:** OFF (we're on the Free + Workers Paid plan
today; this section applies if the zone is ever upgraded).

**Why:** Same failure mode as #1, with finer-grained but still
opaque controls.

### 3. Browser Integrity Check — **DISABLED for `/`**

Path: *Security → Settings → Browser Integrity Check*.

**Required state:** OFF (zone-wide). If a future operator wants to
re-enable it, scope it to `/admin/*` only via a Configuration Rule —
the public study/CMS routes must remain reachable to verified
crawlers without a JS challenge.

### 4. Crawler Hints — **ENABLED**

Path: *Caching → Configuration → Crawler Hints*.

**Required state:** ON.

**Why:** Sends an IndexNow ping to Bing/Yandex when our origin
purges a cached object. We already pre-emptively call IndexNow from
`routes/bot_discovery.py`; Crawler Hints adds a second signal off
the cache layer at zero ops cost.

### 5. AI Audit (zone) — **ENABLED**

Path: *Bots → AI Audit*.

**Required state:** ON.

**Why:** Surfaces per-AI-crawler request counts in the dashboard
which we cross-reference against `routes/admin_observability_bot_buckets.py`.
Read-only — does not affect the worker's hard-403 list.

### 6. Verified Bots → Custom rules — **NONE**

Path: *Security → Bots → Verified Bots* and
*Security → WAF → Custom rules*.

**Required state:** No custom rule that blocks any UA in the
verified_search or citation_ai bucket of `infra/bot-rules.yaml`.

**Why:** A previous on-call accidentally added a "Block PerplexityBot"
rule in March 2026. Our policy is to *Allow* citation-AI; the only
hard-block list is the worker's `AI_BOT_UA` regex (training-only).

## Audit cadence

* **Quarterly** — on-call walks down items 1–6 from the
  `cloudflare-bot-config-audit` issue template.
* **Triggered** — every time a member of the team toggles a Security
  setting in the CF dashboard, they paste the screenshot in the
  on-call channel and reference this runbook.

## Failure mode catalogue

| Symptom                                    | Likely setting wrong         |
| ------------------------------------------ | ---------------------------- |
| Googlebot index coverage drops > 10 %      | Bot Fight Mode flipped on    |
| Verified bot 429s spike at < 60 K RPM      | `VERIFIED_BOT_RATE_LIMIT_RPM` regression in worker |
| PerplexityBot disappears from access logs  | Custom rule blocking it      |
| "Blocked by browser integrity" reports     | BIC re-enabled zone-wide     |

## Out of scope

* Bot management at the FastAPI tier — handled by `utils.py`'s
  `verify_bot_ip` + the `_BOT_PATTERNS` union.
* Per-route admin access — handled by Cloudflare Access on
  `/admin/*` (separate runbook).
