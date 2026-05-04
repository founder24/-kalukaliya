# Production Cutover — Cloudflare → Digital Ocean Origin

Runbook for **Task #334**: flipping production user-visible request
traffic from the legacy **Railway** + **GCP Cloud Run** origins to the
new **Digital Ocean App Platform** origins (`syrabit-backend` and
`rust-core`), with the existing Cloudflare edge fronting both.

This is the request-path slice of the four-cloud hosting plan in
[`ADR-0001`](ADR-0001-four-way-hosting-rebalance.md). Workers/queues
already moved to AWS and cron jobs already moved to Azure in earlier
tasks. **Decommissioning Railway and GCP is the next task (#335)** —
both legacy origins must remain reachable through the soak window so
rollback stays a one-flag operation.

> **Guardrail.** §9 of the hosting plan: there is exactly **one
> canonical backend origin (DO)**. Do not introduce a second backend
> origin on AWS Fargate / Azure App Service / GCP Cloud Run. The
> AWS/Azure/GCP roles in the plan are workers, cron, and AI APIs.

## Hostnames in scope

| Public hostname               | Surface                              | Pre-cutover origin         | Post-cutover origin                    |
| ----------------------------- | ------------------------------------ | -------------------------- | -------------------------------------- |
| `api.syrabit.ai`              | Python FastAPI (REST + webhooks)     | Railway                    | DO `syrabit-backend` (App Platform)    |
| `dispatch.syrabit.ai`         | Edge proxy → backend dispatch path   | CF Worker → GCP Cloud Run  | CF Worker → DO `syrabit-backend`       |
| `grpc.syrabit.ai` (port 50051)| Rust core gRPC                       | Railway                    | DO `rust-core` (App Platform, HTTP/2)  |

Out of scope: `syrabit.ai` (frontend on Cloudflare Pages),
`api.syrabit.ai/api/ai/bedrock/*` (already AWS Lambda),
`api.syrabit.ai/webhooks/ses-sns/*` (already CF Worker → backend).

## Prerequisites — check before cutover starts

1. **DO apps healthy for ≥ 7 consecutive nightly smoke runs.**
   ```sh
   doctl apps list-deployments "$DO_APP_ID_SYRABIT_BACKEND" --format Phase,Cause,Updated | head
   doctl apps list-deployments "$DO_APP_ID_RUST_CORE"        --format Phase,Cause,Updated | head
   scripts/do.sh verify syrabit-backend     # GET /api/health → 200 ok
   scripts/do.sh verify rust-core           # GET /health     → 200 ok
   scripts/do.sh grpc-check                 # health.Check    → SERVING
   ```
2. **Nightly smoke against DO has been green for the same window:**
   ```sh
   SYRABIT_API_BASE=https://syrabit-backend-app.ondigitalocean.app \
     node scripts/nightly-smoke.js
   ```
3. **Edge-proxy staging is on DO** (`[env.staging.vars]
   ORIGIN_TARGET = "do"`) and has been there for ≥ 24 h with no
   regression in `dispatch-staging.syrabit.ai` error rate.
4. **Upstash, Cloudflare WAF, Turnstile, and Zero Trust policies are
   unchanged** — this cutover does not touch them. Confirm the WAF
   ruleset version recorded in `observability.md` matches the live
   zone.
5. **Baseline latency captured** for `api.syrabit.ai` (p50/p95/p99)
   and `dispatch.syrabit.ai` over the prior 24 h. Save the snapshot
   to the cutover ticket so the post-cutover delta is comparable.
6. **Two operators on call** (one to drive, one to observe Sentry +
   Axiom + Cloudflare Analytics).

## Timeline (target window: 60 minutes, low-traffic)

| T-min  | Step                                                       | Owner   | Verification                             |
| ------ | ---------------------------------------------------------- | ------- | ---------------------------------------- |
| T-1440 | Lower DNS TTL on `api.syrabit.ai` and `grpc.syrabit.ai` to **300 s** | infra   | `dig api.syrabit.ai +noall +answer`      |
| T-60   | Confirm pre-cutover checklist above; freeze deploys to backend & rust-core | infra   | Slack #infra-deploys "freeze on"         |
| T-15   | Re-run smoke against DO endpoints; capture baseline metrics | observer| Smoke passes; baselines recorded         |
| T-0    | **Canary — 10 %** via edge-proxy flag (`dispatch.syrabit.ai`)| infra   | See "Canary" below                       |
| T+10   | Promote to **50 %** if error rate < baseline + 0.1 pp      | infra   | CF Analytics + Sentry stable             |
| T+20   | Promote to **100 %** of `dispatch.syrabit.ai`              | infra   | `x-syrabit-origin: do` on every sample   |
| T+25   | Cut `api.syrabit.ai` DNS at Cloudflare → DO origin         | infra   | `dig +short api.syrabit.ai` resolves DO  |
| T+30   | Cut `grpc.syrabit.ai` (port 50051) → DO `rust-core`        | infra   | `grpcurl` health.Check → SERVING         |
| T+30 → T+60 | **Soak** — observe error rate, latency, gRPC success | observer| Within agreed delta (see "Success criteria") |
| T+60   | Lock down origin: WAF + Access rules drop direct hits to Railway / Cloud Run | infra   | `curl` to legacy origins → 403 / blocked |
| T+1440 | Restore DNS TTL to **3600 s** once 24 h clean              | infra   | `dig` shows TTL ≈ 3600                   |

Railway + Cloud Run **stay running** through this window so rollback
is a one-flag operation. Their decommission is Task #335.

## Canary — edge-proxy flag

The edge proxy (`workers/edge-proxy`) reads `ORIGIN_TARGET` per
request. Two ways to run the canary:

- **Cloudflare Worker version split (preferred).** Deploy a second
  worker version with `ORIGIN_TARGET=do`, leave the existing version
  on `cloudrun`, and use a Cloudflare **Gradual Deployment** to send
  10 % → 50 % → 100 % of traffic to the new version. No DNS change
  needed; rollback is "set new version to 0 %".

  ```sh
  cd workers/edge-proxy
  wrangler deploy --env production --var ORIGIN_TARGET:do
  wrangler versions deploy --env production --percentage 10
  # observe → 50 → 100
  wrangler versions deploy --env production --percentage 100
  ```

- **Cloudflare load-balancer weighted pool (alt).** If the
  zone-level LB pool is in use for `dispatch.syrabit.ai`, set the DO
  pool weight to 10 → 50 → 100 in the dashboard. Same monitoring
  applies.

Each promotion gate requires **all** of:

- HTTP error rate ≤ baseline + **0.1 pp**
- p95 latency ≤ baseline + **75 ms**
- p99 latency ≤ baseline + **200 ms**
- gRPC success rate ≥ **99.5 %** over the prior 5-minute window
- Zero new Sentry issue groups tagged `release:do-cutover`

## Validation queries

Run from a laptop (not the origin) so you exercise the public path:

```sh
# 1. The edge proxy is now talking to DO:
curl -sI https://dispatch.syrabit.ai/health \
  | grep -i '^x-syrabit-origin'                          # → x-syrabit-origin: do

# 2. The API hostname resolves to the DO origin (or CF orange cloud
#    fronting it) — in either case the origin trailer shows DO:
curl -sI https://api.syrabit.ai/api/health
curl -s  https://api.syrabit.ai/api/health | jq .       # → {"status":"ok", "origin":"do", ...}
dig +short api.syrabit.ai                                # → CF/DO IPs only, no Railway

# 3. Cloudflare Analytics — last 15 min, by origin tag:
#      Worker var ORIGIN_TARGET surfaces as `x-syrabit-origin` in CF
#      Logs; filter for `host = api.syrabit.ai AND origin != do` and
#      expect 0 rows after T+30.

# 4. Sentry — release:do-cutover, last 30 min, severity ≥ warning.
#    Expect 0 new issue groups.
```

### gRPC verification (`grpcurl`)

The Rust core's gRPC port (50051) is fronted publicly by
`grpc.syrabit.ai`. App Platform terminates HTTP/2 at the LB so no
Droplet is needed.

```sh
# Generic health check against the standard gRPC health service:
grpcurl grpc.syrabit.ai:443 grpc.health.v1.Health/Check
# → { "status": "SERVING" }

# Named service variant:
grpcurl -d '{"service":"syrabit.core.v1.RustCore"}' \
  grpc.syrabit.ai:443 grpc.health.v1.Health/Check

# A representative real RPC (replace with the smoke method you keep
# wired into rust-core for cutover gating):
grpcurl -d '{"ping":"cutover"}' \
  grpc.syrabit.ai:443 syrabit.core.v1.RustCore/Smoke
# → { "pong": "cutover", "origin": "do" }

# Loop for 5 minutes during soak:
for i in $(seq 1 60); do
  grpcurl -max-time 3 grpc.syrabit.ai:443 grpc.health.v1.Health/Check \
    >/dev/null 2>&1 && echo "ok $i" || echo "FAIL $i"
  sleep 5
done
```

In-VPC DO clients should keep dialling the private hostname
(`rust-core-core.private:50051`) so they don't egress through the LB.

## Success criteria (1 h soak)

- p50 latency: within **±15 %** of pre-cutover baseline.
- p95 latency: within **+75 ms** of baseline.
- p99 latency: within **+200 ms** of baseline.
- HTTP 5xx rate: within **+0.1 pp** of baseline.
- gRPC success rate: **≥ 99.5 %**.
- Cloudflare Analytics shows **0 hits** to the Railway / Cloud Run
  origin from the WAF after T+60 lockdown.
- Nightly smoke against `https://api.syrabit.ai` (now DO) is green
  for the next two consecutive runs.

If any criterion misses for two consecutive 5-minute windows during
soak, **execute the rollback** below. Do not "wait it out".

## Lock down legacy origins

Once 100 % traffic is on DO and the soak is green:

1. **Cloudflare WAF — ingress allowlist.** Update the production
   ruleset so `api.syrabit.ai` only allows requests originating from
   Cloudflare colos or, where the origin is hit directly, from the
   DO App Platform LB IP range. Block:
   ```
   ip.src in $railway_origin_ips
   ip.src in $gcp_cloudrun_origin_ips
   http.request.uri.path matches "^/(internal|admin)" and not (cf.client_bot or ip.src in $cloudflare_colo_ips)
   ```
2. **Cloudflare Access — origin auth.** Bind the `api.syrabit.ai`
   and `dispatch.syrabit.ai` Access applications to the DO origin
   only. Remove the Railway / Cloud Run origin from the Access
   policy's "service auth" allowlist.
3. **Origin shared secret.** Confirm `DISPATCH_SHARED_SECRET` is
   present on the DO `syrabit-backend` app and rotate it after the
   soak so any leaked Railway/Cloud Run copy is invalidated.
4. **Cloudflare Tunnel cleanup.** If a `cloudflared` tunnel still
   advertises the Railway or Cloud Run origin, mark it `disabled`
   in the dashboard (Task #335 deletes the tunnel itself).

Verify lockdown:

```sh
# Direct hits to legacy origins should be blocked or unreachable.
curl -fsSI https://syrabit-backend-production.up.railway.app/api/health \
  && echo "WARN: Railway still reachable directly" \
  || echo "ok: Railway blocked"
curl -fsSI "$DISPATCH_CLOUD_RUN_URL/health" \
  && echo "WARN: Cloud Run still reachable directly" \
  || echo "ok: Cloud Run blocked"
```

WAF, Turnstile, mTLS, and Zero Trust policies remain otherwise
unchanged — only the origin allowlist moves.

## Rollback (target: < 5 minutes end-to-end)

Rollback is a flag flip. **Both legacy origins are still running**
through the soak window.

```sh
# 1. Edge proxy back to Cloud Run:
cd workers/edge-proxy
wrangler deploy --env production --var ORIGIN_TARGET:cloudrun
# (or :railway for the FastAPI backend specifically)

# 2. DNS for api.syrabit.ai back to the Railway origin record:
#    Cloudflare dashboard → DNS → api → revert to the
#    "syrabit-backend-production.up.railway.app" CNAME / proxied
#    A-record snapshot saved before cutover.
#    TTL is already 300 s, so propagation is < 5 min.

# 3. DNS for grpc.syrabit.ai back to the Railway gRPC record.

# 4. Sanity:
curl -sI https://dispatch.syrabit.ai/health | grep '^x-syrabit-origin'
#   → x-syrabit-origin: cloudrun (or railway)
dig +short api.syrabit.ai
grpcurl grpc.syrabit.ai:443 grpc.health.v1.Health/Check
```

If the WAF lockdown in the previous section was already applied,
**re-allow the legacy origin first** (CF dashboard → WAF rules →
disable the "block legacy origins" rule) before flipping DNS — the
new origin record will hit the WAF too.

### Rollback rehearsal (staging — required before production cutover)

```sh
# Pre-state: staging on DO (default).
curl -sI https://dispatch-staging.syrabit.ai/health | grep '^x-syrabit-origin'   # → do

# Flip to cloudrun:
wrangler deploy --env staging --var ORIGIN_TARGET:cloudrun
sleep 30
curl -sI https://dispatch-staging.syrabit.ai/health | grep '^x-syrabit-origin'   # → cloudrun

# Flip back to do (steady state):
wrangler deploy --env staging --var ORIGIN_TARGET:do
curl -sI https://dispatch-staging.syrabit.ai/health | grep '^x-syrabit-origin'   # → do
```

Record both timestamps in the cutover ticket; total elapsed time
must be **under 5 minutes** for the rehearsal to count.

## Post-cutover

- Restore DNS TTL on `api.syrabit.ai` and `grpc.syrabit.ai` to
  **3600 s** after 24 h clean.
- Update `docs/infra/inventory/railway.json` and
  `docs/infra/inventory/cloud-run.json` to set `traffic_share: 0`
  (the file is the source of truth for the Task #335 decommission).
- Hand off to **Task #335** (decommission Railway + GCP hosting,
  cron, and CI) once two consecutive nightly smokes against DO have
  been green.

## Quick reference — relevant files

- Edge proxy source: [`workers/edge-proxy/src/index.ts`](../../workers/edge-proxy/src/index.ts)
- Edge proxy config: [`workers/edge-proxy/wrangler.toml`](../../workers/edge-proxy/wrangler.toml)
- DO backend spec: [`infra/do/app-syrabit-backend.yaml`](../../infra/do/app-syrabit-backend.yaml)
- DO Rust core spec: [`infra/do/app-rust-core.yaml`](../../infra/do/app-rust-core.yaml)
- DO day-two ops: [`api-on-do.md`](api-on-do.md)
- Hosting plan: [`ADR-0001-four-way-hosting-rebalance.md`](ADR-0001-four-way-hosting-rebalance.md)
- Helper script: [`scripts/do.sh`](../../scripts/do.sh) (`verify`, `grpc-check`, `logs`)
