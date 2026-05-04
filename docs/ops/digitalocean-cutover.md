# Cutover & Rollback Runbook — Railway → Digital Ocean

> **Task #336.** Operational runbook for swinging production
> user-visible request traffic from the legacy **Railway**
> `syrabit-backend` and `rust-core` services to the new **Digital
> Ocean App Platform** services of the same name, with the existing
> Cloudflare edge proxy fronting both. Companion of the day-to-day
> runbook at [`../DIGITALOCEAN-DEPLOYMENT.md`](../DIGITALOCEAN-DEPLOYMENT.md).

A higher-level cutover narrative for the wider four-cloud rebalance
lives at
[`../../artifacts/syrabit/docs/infra/cutover.md`](../../artifacts/syrabit/docs/infra/cutover.md);
this document is the Task #336-specific operational checklist plus
the rollback path back to Railway during the soak window.

---

## Hostnames in scope

| Public hostname                  | Surface                          | Pre-cutover origin | Post-cutover origin                  |
|----------------------------------|----------------------------------|--------------------|--------------------------------------|
| `api.syrabit.ai`                 | Python FastAPI (REST + webhooks) | Railway            | DO `syrabit-backend` (App Platform)  |
| `dispatch.syrabit.ai`            | Edge-proxy → backend dispatch    | Railway            | DO `syrabit-backend`                 |
| `rust-core.syrabit.ai`           | Rust core HTTP                   | Railway            | DO `rust-core` (App Platform)        |
| `grpc.syrabit.ai` (port 50051)   | Rust core gRPC                   | Railway            | DO `rust-core` (App Platform internal port — Spectrum-fronted) |

Out of scope: `syrabit.ai` (frontend on Cloudflare Pages),
`api.syrabit.ai/api/ai/bedrock/*` (already on AWS Lambda),
`api.syrabit.ai/webhooks/ses-sns/*` (already CF Worker → backend).

---

## Pre-cutover checklist

1. **DO apps healthy for ≥ 7 consecutive nightly smoke runs.**
   ```sh
   doctl apps list-deployments "$DO_APP_ID_SYRABIT_BACKEND" \
     --format Phase,Cause,Updated | head
   doctl apps list-deployments "$DO_APP_ID_RUST_CORE" \
     --format Phase,Cause,Updated | head
   bash scripts/digitalocean.sh verify syrabit-backend
   bash scripts/digitalocean.sh verify rust-core
   bash scripts/digitalocean.sh grpc-check
   ```
2. **Nightly smoke against DO has been green for the same window.**
   ```sh
   SYRABIT_API_BASE=https://syrabit-backend-app.ondigitalocean.app \
     node artifacts/syrabit/scripts/nightly-smoke.js
   ```
3. **Edge-proxy staging is on DO** (`workers/edge-proxy/wrangler.toml`
   `[env.staging.vars] BACKEND_URL = "https://syrabit-backend-staging.ondigitalocean.app"`)
   and has been there for ≥ 24 h with no regression in
   `dispatch-staging.syrabit.ai` error rate.
4. **Upstash, Cloudflare WAF, Turnstile, and Zero Trust policies are
   unchanged** — this cutover does not touch them.
5. **Baseline latency captured** for `api.syrabit.ai` (p50/p95/p99)
   and `dispatch.syrabit.ai` over the prior 24 h. Save the snapshot
   to the cutover ticket so the post-cutover delta is comparable.
6. **Two operators on call** (one to drive, one to observe Sentry +
   Axiom + Cloudflare Analytics).

---

## Timeline (target window: 60 minutes, low-traffic)

| T-min   | Step                                                            | Owner    | Verification                                   |
|---------|-----------------------------------------------------------------|----------|------------------------------------------------|
| T-1440  | Lower DNS TTL on `api.syrabit.ai` and `grpc.syrabit.ai` to 300 s | infra    | `dig api.syrabit.ai +noall +answer`            |
| T-60    | Confirm pre-cutover checklist; freeze deploys to backend & rust-core | infra | Slack #infra-deploys "freeze on"               |
| T-15    | Re-run smoke against DO endpoints; capture baseline metrics      | observer | Smoke passes; baselines recorded               |
| T-0     | **Canary — 10%** via edge-proxy `BACKEND_URL` env=canary      | infra    | `x-syrabit-origin: do` on 10% of samples       |
| T+10    | Promote to **50%** if error rate < baseline + 0.1pp            | infra    | CF Analytics + Sentry stable                   |
| T+20    | Promote to **100%**                                              | infra    | `x-syrabit-origin: do` on every sample         |
| T+25    | Cut `api.syrabit.ai` DNS at Cloudflare → DO origin               | infra    | `dig +short api.syrabit.ai` resolves DO IP     |
| T+30    | Cut `grpc.syrabit.ai` (port 50051) → DO `rust-core`              | infra    | `grpcurl` health.Check → SERVING               |
| T+30 → T+60 | **Soak** — observe error rate, latency, gRPC success         | observer | Within agreed delta (see "Success criteria")   |
| T+60    | Lock down origin: WAF + Access rules drop direct hits to Railway | infra    | `curl` to legacy Railway URL → 403 / blocked   |

### Canary mechanism

The edge-proxy worker reads `BACKEND_URL` from `wrangler.toml`. To
canary, set the staging deployment of the worker (different route,
same code) to the DO origin and the production deployment to the old
Railway origin, then split traffic at the Cloudflare Load Balancer
between the two routes. Once 100% of traffic is on the staging
worker, swap `BACKEND_URL` in the production worker config and
`wrangler deploy`.

```sh
# Promote the staging-on-DO worker to 100%
wrangler deploy --env staging   # DO origin
wrangler deploy --env production  # also DO origin after swap
```

---

## Success criteria

The cutover is **successful** if all of the following hold for the
T+30 → T+60 soak window:

* `api.syrabit.ai` HTTP 5xx rate ≤ Railway baseline + 0.1pp.
* `api.syrabit.ai` p95 latency ≤ Railway baseline + 5 ms (measured
  client-side via the synthetic probes in
  `.github/workflows/synthetic-probe-secrets-daily.yml`).
* `grpc.syrabit.ai` `health.Check` returns `SERVING` for every
  60 s probe (`grpcurl ... grpc.health.v1.Health/Check`).
* No new Sentry issues tagged `release:do-cutover`.
* Axiom dataset `syrabit-backend-do` shows live ingest at the
  expected per-minute rate.

If any criterion fails, follow §Rollback below within 15 minutes.

---

## Rollback

Two paths, in increasing order of blast radius.

### Path A — flip `BACKEND_URL` only (~5 min, no DNS change)

The fastest rollback. Edit `workers/edge-proxy/wrangler.toml`:

```toml
# Rollback to Railway during cutover soak
BACKEND_URL = "https://workspacemockup-sandbox-production-df37.up.railway.app"
```

Then:

```sh
pnpm --filter ./workers/edge-proxy run deploy
curl -fsS https://api.syrabit.ai/api/health \
  | jq '.deploy_origin'   # → "railway" once the new worker is live
```

This works as long as the Railway services are still running (they
remain running until §Decommission below). The Cloudflare DNS record
for `api.syrabit.ai` continues to point at the same Cloudflare proxy
IPs — only the worker's outbound origin changed.

### Path B — DNS revert + `BACKEND_URL` flip (~15 min)

Use this if Path A doesn't recover the error budget within 5 minutes
(which would mean Cloudflare itself is misbehaving, not the origin).

1. In Cloudflare DNS, swap the `api.syrabit.ai` and
   `grpc.syrabit.ai` records back to the legacy Railway CNAMEs
   captured in the cutover ticket.
2. Apply Path A above.
3. Lower TTL on both records back to 60 s for the duration of the
   investigation.
4. Open an incident in Sentry tagged `incident:do-cutover-rollback`
   and link the cutover ticket.

### After any rollback

* Do **not** re-attempt cutover within the same on-call window.
* File a post-mortem within 24 h.
* Re-run the pre-cutover checklist in full before the next attempt.

---

## Decommission (Railway tear-down)

Only after **14 consecutive days** of green DO production:

```sh
# 1. Snapshot Railway env vars to the team 1Password vault first.
railway variables list --service syrabit-backend --format env \
  > backups/railway-syrabit-backend.env
railway variables list --service rust-core --format env \
  > backups/railway-rust-core.env
# Store both files in 1Password under "syrabit/decommission-2026-XX",
# then shred the local copies. NEVER commit these to git.

# 2. Pause services (final go/no-go gate).
#    Railway dashboard → each service → Pause → 24h soak
#    If any user-visible incident in those 24h, unpause and abort.

# 3. Delete the project.
#    Railway dashboard → Settings → Delete project (irreversible)
#    Cancel the Railway billing plan in the same session.

# 4. Remove the deprecation stubs from this repo.
git rm backend/rust-core/railway.toml
git rm scripts/railway.sh
# Leave docs/RAILWAY-DEPLOYMENT.md as a tombstone redirect for
# at least one quarter so external bookmarks still resolve.

# 5. Strip the railway:* aliases from package.json.
#    (The `_railway:deprecated` shim becomes dead code at this point.)

# 6. Update the Cloudflare WAF rule that allows the edge worker to
#    reach Railway IPs — set the rule action to "block" so the legacy
#    URL becomes inert from inside the Cloudflare account too.
```

---

## Communication template

Pre-cutover Slack post (T-60):

> **#infra-deploys** — Cutover for `api.syrabit.ai` + `grpc.syrabit.ai`
> from Railway → Digital Ocean kicks off in 60 min (Task #336).
> Edge proxy `BACKEND_URL` swap + DNS roll. Two operators on call.
> Rollback path: flip `BACKEND_URL` back to the Railway hostname
> (~5 min). Soak window 30 min, success criteria in
> `docs/ops/digitalocean-cutover.md`. **Deploys to syrabit-backend
> and rust-core are FROZEN until I post "freeze off".**

Post-cutover Slack post (T+60):

> **#infra-deploys** — DO cutover complete. 100% of `api.syrabit.ai`
> traffic now served from DO `syrabit-backend` (blr1). p95 latency
> Δ vs Railway baseline: <fill>. 5xx rate Δ: <fill>. Railway
> services remain running for 14-day soak; decommission scheduled
> for <date>. Deploys are unfrozen.
