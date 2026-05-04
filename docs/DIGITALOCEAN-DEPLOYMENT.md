# Syrabit.ai Backend — Digital Ocean Deployment Guide

> **Status (Task #336):** ✅ canonical hosting for the FastAPI
> backend (`syrabit-backend`) and the Rust core
> (`backend/rust-core`). Replaces the legacy Railway runbook at
> [`./RAILWAY-DEPLOYMENT.md`](./RAILWAY-DEPLOYMENT.md) and the
> retired Cloud Run path at
> [`../artifacts/syrabit-backend/CLOUDRUN-DEPLOY.md`](../artifacts/syrabit-backend/CLOUDRUN-DEPLOY.md).

## TL;DR

```sh
export DIGITALOCEAN_ACCESS_TOKEN=<doctl PAT>
export DO_APP_ID_SYRABIT_BACKEND=<uuid>   # `doctl apps list`
export DO_APP_ID_RUST_CORE=<uuid>

bash scripts/digitalocean.sh deploy   syrabit-backend   # build → push DOCR → roll
bash scripts/digitalocean.sh verify   syrabit-backend   # GET /api/health
bash scripts/digitalocean.sh logs     syrabit-backend   # streaming runtime logs
bash scripts/digitalocean.sh deploy   rust-core
bash scripts/digitalocean.sh grpc-check                  # rust-core gRPC SERVING
```

`pnpm run do:deploy`, `pnpm run do:logs`, etc. are 1:1 aliases.

## Architecture

```
Users
  │
  ├── https://syrabit.ai ──► Cloudflare Pages (frontend PWA)
  │
  └── https://api.syrabit.ai ──► Cloudflare Worker (workers/edge-proxy)
                                  • mTLS to origin
                                  • WAF / Turnstile / rate-limit
                                  • D1 read-cache
                                  │
                                  └──► Digital Ocean App Platform (blr1)
                                        ├── syrabit-backend  (FastAPI :8080)
                                        │     /api/health
                                        └── rust-core         (HTTP :3000 + gRPC :50051)
                                              /health     • internal-only port
                                        • dialled VPC-internally as
                                          rust-core-core.private:50051
```

Both apps run in **`blr1` (Bangalore)** — closest DO region to the
primary user base in Assam. App Platform terminates HTTP/2 at the
platform LB so gRPC works without a Droplet.

## Prerequisites

1. `doctl` ≥ 1.110, `docker`, `yq` (optional but strongly recommended
   for `var-set` / `import-env`), `grpcurl` (for the gRPC health check).
2. `DIGITALOCEAN_ACCESS_TOKEN` exported with App Platform + DOCR
   write scopes. The same token is set as the
   `DIGITALOCEAN_ACCESS_TOKEN` repo secret driving
   `.github/workflows/digitalocean-deploy.yml`.
3. The two App Platform apps already created via
   `doctl apps create --spec .do/app.yaml` and
   `doctl apps create --spec .do/app-rust-core.yaml`.
4. App IDs exported locally and as repo variables:
   ```sh
   export DO_APP_ID_SYRABIT_BACKEND=<uuid>
   export DO_APP_ID_RUST_CORE=<uuid>
   ```
5. A populated env file. The original cutover (Task #336) snapshotted
   the legacy Railway env via `railway variables list`; subsequent
   refreshes pull from `doctl apps spec get $DO_APP_ID_SYRABIT_BACKEND`.
   Output files (`do-backend-vars.env`, `do-rust-vars.env`) are
   gitignored.

## First-time secret import

```sh
bash scripts/digitalocean.sh import-env syrabit-backend do-backend-vars.env
bash scripts/digitalocean.sh import-env rust-core       do-rust-vars.env
```

`import-env` patches the committed spec in a temp file (never in the
repo), then calls `doctl apps update --spec --wait`. The
`PLACEHOLDER_SET_VIA_doctl` markers in the YAML are replaced with the
real values; any key absent from the env file keeps its placeholder
and the import warns loudly so the deploy fails fast instead of
booting with empty credentials.

## Deploy

CI handles every merge to `master` via
`.github/workflows/digitalocean-deploy.yml` (matrix: `syrabit-backend`,
`rust-core`). Each matrix job builds the image, pushes to DOCR
(SHA-tagged), rewrites `image.tag` in the spec, and runs
`doctl apps update --wait`.

Manual deploy from a checkout:

```sh
bash scripts/digitalocean.sh deploy syrabit-backend
bash scripts/digitalocean.sh deploy rust-core
```

Re-roll the latest already-built image (no rebuild):

```sh
bash scripts/digitalocean.sh redeploy syrabit-backend
```

## Health checks

```sh
bash scripts/digitalocean.sh verify syrabit-backend     # GET /api/health
bash scripts/digitalocean.sh verify rust-core           # GET /health
bash scripts/digitalocean.sh grpc-check                 # grpcurl health.Check :50051
```

Expected: HTTP 200 with `{"status":"ok"}` on both HTTP endpoints; the
gRPC check returns `status: SERVING`.

## Logs

```sh
bash scripts/digitalocean.sh logs syrabit-backend run      # default
bash scripts/digitalocean.sh logs syrabit-backend build    # build phase
bash scripts/digitalocean.sh logs syrabit-backend deploy   # deploy phase (rolling)
bash scripts/digitalocean.sh logs rust-core
```

`--follow` is on by default. Combine with the Axiom dashboard
(`syrabit-backend-do`, `rust-core-do` datasets) for cross-service
correlation.

## Scaling

Both apps autoscale on CPU (70% backend, 75% rust-core). To bump the
ceiling temporarily:

```sh
doctl apps update "$DO_APP_ID_SYRABIT_BACKEND" \
  --spec <(yq '.services[0].autoscaling.max_instance_count = 8' .do/app.yaml) --wait
```

Then commit the spec change to make it permanent.

## Rollback

App Platform keeps the last 5 successful deployments. List them and
roll forward to a previous one — no rebuild required, ~30 s to live:

```sh
doctl apps list-deployments "$DO_APP_ID_SYRABIT_BACKEND"
doctl apps create-deployment "$DO_APP_ID_SYRABIT_BACKEND" \
  --rollback-deployment <previous-deployment-uuid>
```

The full cutover/rollback runbook (including how to flip
`workers/edge-proxy/wrangler.toml` `BACKEND_URL` back to the legacy
Railway origin during the soak window) lives at
[`./ops/digitalocean-cutover.md`](./ops/digitalocean-cutover.md).

## Rust core: App Platform vs Droplet

| Surface             | App Platform (`.do/app-rust-core.yaml`) | Droplet (`backend/rust-core/deploy/digitalocean/`) |
|---------------------|----------------------------------------|----------------------------------------------------|
| HTTP / REST         | ✅ `:3000` public, fronted by App Platform LB | ✅ `:443`, fronted by Caddy |
| gRPC inside the VPC | ✅ `internal_ports: [50051]` | ✅ `:50051` exposed publicly |
| gRPC outside the VPC | ❌ App Platform doesn't publish internal ports | ✅ raw TCP, Cloudflare Spectrum or DO firewall ACL |
| Cost (basic plan)   | ~$12/mo                                | ~$6/mo + Caddy overhead                            |
| Operational toil    | Managed rolling deploys, no patching   | Manual SSH, manual `docker compose`                |

Default is App Platform; switch to the Droplet only if an external
gRPC client (mobile SDK, third-party webhook) needs to reach `:50051`
directly.

## Common failure modes

| Symptom                             | Likely cause                                                   |
|-------------------------------------|----------------------------------------------------------------|
| Deploy stuck in `PENDING_BUILD`     | DOCR rate limit — re-run `doctl registry login` and retry      |
| Health check fails immediately      | `PORT` env var mismatch — spec must match the Dockerfile       |
| 502 from edge proxy                 | `BACKEND_URL` updated before the DO app went healthy           |
| gRPC `UNAVAILABLE`                   | `internal_ports` not declared, or HTTP/2 not negotiated         |
| Upstash 401                          | `UPSTASH_REDIS_REST_TOKEN` not imported (still `PLACEHOLDER…`) |
| Worker still hits Railway           | Stale `BACKEND_URL` plaintext binding — `wrangler deploy` from this checkout |
