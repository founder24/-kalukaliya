# API on Digital Ocean App Platform

Runbook for the Python FastAPI backend (`syrabit-backend`) and the
Rust core (`rust-core`) on Digital Ocean App Platform. Introduced by
Task #331, which superseded Task #336.

## Topology

```
                  Cloudflare edge proxy (Worker, free tier)
                   │           ▲
                   │  ORIGIN_TARGET=do (only target after Task #335)
                   ▼
        ┌──────────────────────┐         ┌───────────────────┐
        │  syrabit-backend     │ gRPC   │   rust-core       │
        │  HTTP 8080           │◀─────▶│   HTTP 3000       │
        │  /api/health         │ 50051 │   /health         │
        │  basic-s, autoscale  │ (VPC) │   basic-xs        │
        └──────────┬───────────┘        └─────────┬─────────┘
                   │ Upstash Redis REST           │
                   ▼                              ▼
              cache, rate-limits, 429 burst counter
```

Both apps run in `blr1` (Bangalore) — closest DO region to the
primary user base in IN. App Platform terminates HTTP/2 at the
platform LB so gRPC works without a Droplet.

## Prerequisites

1. `doctl` installed and authenticated against the `syrabit` team
   (see `infra/do/README.md`).
2. The two App Platform apps already created via
   `doctl apps create --spec infra/do/app-*.yaml`.
3. App IDs exported locally and as GitHub repo variables:
   ```sh
   export DO_APP_ID_SYRABIT_BACKEND=<uuid>
   export DO_APP_ID_RUST_CORE=<uuid>
   ```
4. A populated env file. The original cutover (Task #331) pulled
   these from Railway via `railway variables list`. Railway has been
   decommissioned (Task #335); use the saved snapshots in
   `docs/infra/decommission.md` or DO's own
   `doctl apps spec get "$DO_APP_ID_SYRABIT_BACKEND"` as the source of
   truth instead. Output files (`do-backend-vars.env`,
   `do-rust-vars.env`) are gitignored.

## First-time secret import

```sh
scripts/do.sh import-env syrabit-backend do-backend-vars.env
scripts/do.sh import-env rust-core       do-rust-vars.env
```

The script patches the committed spec in a temp file (never on disk
in the repo), then calls `doctl apps update --spec --wait`. The
`PLACEHOLDER_SET_VIA_doctl` markers in the YAML are replaced with the
real values; any key absent from the env file keeps its placeholder
and the deploy fails fast.

## Deploy

CI handles every merge to `master`:

- `.github/workflows/do-deploy-backend.yml`
- `.github/workflows/do-deploy-rust-core.yml`

Each workflow builds the image, pushes to DOCR (SHA-tagged), rewrites
`image.tag` in the spec, and runs `doctl apps update --wait`.

Manual deploy of the committed spec only (no rebuild):

```sh
scripts/do.sh deploy syrabit-backend
scripts/do.sh deploy rust-core
```

## Health checks

```sh
scripts/do.sh verify syrabit-backend     # GET /api/health
scripts/do.sh verify rust-core           # GET /health
scripts/do.sh grpc-check                 # grpcurl health.Check on :50051
```

Expected: HTTP 200 with `{"status":"ok"}` on both HTTP endpoints; the
gRPC check returns `status: SERVING`.

## Logs

```sh
scripts/do.sh logs syrabit-backend          # runtime
scripts/do.sh logs syrabit-backend build    # build phase
scripts/do.sh logs syrabit-backend deploy   # deploy phase (rolling)
scripts/do.sh logs rust-core
```

`--follow` is on by default. Combine with the Axiom dashboard
(`syrabit-backend-do`, `rust-core-do` datasets) for cross-service
correlation.

## Scaling

Both apps autoscale on CPU (70% backend, 75% rust-core). To bump the
ceiling temporarily:

```sh
doctl apps update "$DO_APP_ID_SYRABIT_BACKEND" \
  --spec <(yq '.services[0].autoscaling.max_instance_count = 8' \
              infra/do/app-syrabit-backend.yaml) --wait
```

Then commit the spec change to make it permanent.

## Rollback

App Platform keeps the last 5 successful deployments. List them:

```sh
doctl apps list-deployments "$DO_APP_ID_SYRABIT_BACKEND"
```

Roll back:

```sh
doctl apps create-deployment "$DO_APP_ID_SYRABIT_BACKEND" \
  --rollback-deployment <previous-deployment-uuid>
```

The rollback uses the previous spec + image tag verbatim — no rebuild
required, ~30 s to live.

## Upstash Redis verification

Run a one-shot check from inside the app via `doctl`:

```sh
doctl apps logs "$DO_APP_ID_SYRABIT_BACKEND" --type run \
  | grep -E "Upstash Redis (configured|not configured)"
```

The backend logs `"Upstash Redis configured (REST)"` on boot when
`UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN` resolve. The
429 burst counter increments under load — watch the
`/admin/health/limits` panel.

## External-dependency smoke

After the first deploy, exercise each upstream:

| Upstream         | How to verify                                              |
|------------------|------------------------------------------------------------|
| Supabase         | `curl $URL/api/auth/whoami -H 'Authorization: Bearer …'`   |
| Pinecone         | `/api/admin/health/pinecone` returns `ok: true`            |
| MongoDB Atlas    | `/api/admin/health/mongo` returns counts                   |
| Cloudflare R2    | `/api/admin/health/r2` returns latest object key            |
| Cloudflare AI GW | `/api/admin/health/ai-gateway`                             |
| Vertex AI        | `/api/admin/health/vertex`                                 |
| Stripe           | `/api/admin/health/payments` (Stripe + Razorpay)           |
| Resend           | `/api/admin/health/email`                                  |
| Sentry           | trigger a `?_sentry_smoke=1` request and check Sentry feed |

The nightly smoke suite (`scripts/nightly-smoke.js`) runs the full
matrix against the DO endpoint via the `SYRABIT_API_BASE` env var:

```sh
SYRABIT_API_BASE=https://syrabit-backend-app.ondigitalocean.app \
  node scripts/nightly-smoke.js
```

## Edge proxy origin (post-cutover)

After Task #334 cut traffic to DO and Task #335 decommissioned the
legacy Railway and Cloud Run origins, the Cloudflare edge proxy at
`workers/edge-proxy/` only resolves `ORIGIN_TARGET=do`. The variable
is retained as a future extension point but no other value maps to a
configured origin today; an unsupported value logs a warning and
still falls through to the DO upstream. See [`cutover.md`](cutover.md)
for the original cutover timeline and [`decommission.md`](decommission.md)
for the legacy-origin removal log.

## Common failure modes

| Symptom                              | Likely cause                                |
|--------------------------------------|---------------------------------------------|
| Deploy stuck in `PENDING_BUILD`      | DOCR rate limit — re-run `doctl registry login` |
| Health check fails immediately       | `PORT` env var mismatch — spec must match Dockerfile |
| 502 from edge proxy                  | `ORIGIN_TARGET` set to `do` before app is healthy |
| gRPC `UNAVAILABLE`                    | Internal port not declared in spec, or HTTP/2 not negotiated |
| Upstash 401                           | `UPSTASH_REDIS_REST_TOKEN` not imported (still placeholder) |
