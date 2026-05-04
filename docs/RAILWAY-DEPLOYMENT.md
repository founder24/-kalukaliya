# Syrabit.ai Backend — Railway Deployment Guide

> ⚠️ **RETIRED PATH — DO NOT FOLLOW.** Railway was decommissioned in
> Task #336. The canonical backend origin is now **Digital Ocean App
> Platform** (`syrabit-backend` in region `blr1`); the Rust core moved
> to the matching DO `rust-core` app (or the optional Droplet under
> `backend/rust-core/deploy/digitalocean/`). The active runbooks are:
>
> * Day-to-day operations: [`./DIGITALOCEAN-DEPLOYMENT.md`](./DIGITALOCEAN-DEPLOYMENT.md)
> * Cutover & rollback   : [`./ops/digitalocean-cutover.md`](./ops/digitalocean-cutover.md)
> * Per-app spec         : [`../.do/app.yaml`](../.do/app.yaml), [`../.do/app-rust-core.yaml`](../.do/app-rust-core.yaml)
> * Helper script        : `bash scripts/digitalocean.sh …` (replaces `scripts/railway.sh`)
>
> This file is kept only as a tombstone redirect so external bookmarks
> still resolve. Everything below is historical Railway content; do not
> use it for new deploys.

---


## Architecture

```
Users
  │
  ├── https://syrabit.ai ──► Cloudflare Pages (frontend PWA)
  │                            • React + Vite build
  │                            • Global CDN, edge caching
  │
  └── https://api.syrabit.ai ──► Cloudflare Worker (edge proxy)
                                  • D1 edge cache for content reads
                                  • Rate limiting (KV)
                                  │
                                  └──► Railway (backend API)
                                        • FastAPI + Gunicorn in Docker container
                                        • Auto-scaling, HTTPS included
                                        • Connects to: MongoDB Atlas, Supabase, Upstash Redis
```

## Why Railway?

- Cheapest paid hosting for your backend (~₹200-400/month at low traffic)
- No load balancer fees (unlike AWS which charges ₹1,600/month just for ALB)
- Connect GitHub → auto-deploys on every push
- Uses your existing Dockerfile — zero code changes
- Custom domain with free SSL
- Pay only for what you use (CPU + RAM)

## Prerequisites

- Railway account (https://railway.app — sign up with GitHub)
- GitHub repo with backend code pushed
- All external services set up (MongoDB Atlas, Supabase, Upstash Redis)

---

## Step 1: Create Railway Project

1. Go to https://railway.app/new
2. Click **"Deploy from GitHub repo"**
3. Select your Syrabit repository
4. Railway will detect the Dockerfile automatically

---

## Step 2: Configure Build Settings

Railway auto-detects Docker, but verify these settings:

Go to your service → **Settings** tab:

| Setting | Value |
|---------|-------|
| **Root Directory** | `artifacts/syrabit-backend` |
| **Builder** | Dockerfile (auto-detected) |
| **Start Command** | Leave empty (uses Dockerfile CMD) |
| **Port** | `8000` |

Under **Networking**:
- Click **"Generate Domain"** to get a public URL (e.g., `syrabit-api-production.up.railway.app`)
- Or add a custom domain later

---

## Step 3: Add Environment Variables

Go to your service → **Variables** tab → Click **"Raw Editor"** and paste:

```
MONGO_URL=mongodb+srv://YOUR_CONNECTION_STRING
DB_NAME=test_database
JWT_SECRET=GENERATE_WITH_openssl_rand_hex_32
ADMIN_JWT_SECRET=GENERATE_DIFFERENT_WITH_openssl_rand_hex_32
ADMIN_EMAILS=admin@syrabit.ai
ADMIN_PASSWORDS=YOUR_ADMIN_PASSWORD
ADMIN_NAMES=Administrator
PORT=8000
FRONTEND_URL=https://syrabit.ai
CORS_ORIGINS=https://syrabit.ai,https://www.syrabit.ai,https://api.syrabit.ai
SECURE_COOKIES=true
LOG_LEVEL=info
GUNICORN_WORKERS=2
```

Then add each of these one by one (or in raw editor):

### Database & Cache

```
DATABASE_URL=YOUR_SUPABASE_POSTGRES_URL
SUPABASE_URL=https://czeznmqogtwecidhpysa.supabase.co
SUPABASE_SERVICE_KEY=YOUR_SUPABASE_KEY
UPSTASH_REDIS_REST_URL=YOUR_UPSTASH_URL
UPSTASH_REDIS_REST_TOKEN=YOUR_UPSTASH_TOKEN
```

### AI Provider Keys

```
GROQ_API_KEY=YOUR_KEY
GROQ_API_KEY_2=YOUR_KEY
CEREBRAS_API_KEY=YOUR_KEY
GEMINI_API_KEY=YOUR_KEY
GEMINI_API_KEY_2=YOUR_KEY
SARVAM_API_KEY=YOUR_KEY
SARVAM_API_KEY_2=YOUR_KEY
FIREWORKS_API_KEY=YOUR_KEY
OPENROUTER_API_KEY=YOUR_KEY
EMERGENT_API_KEY=YOUR_KEY
XAI_API_KEY=YOUR_KEY
```

### Auth & Payments

```
GOOGLE_CLIENT_ID=YOUR_GOOGLE_OAUTH_CLIENT_ID
GOOGLE_CLIENT_SECRET=YOUR_GOOGLE_OAUTH_SECRET
RAZORPAY_KEY_ID=YOUR_KEY
RAZORPAY_KEY_SECRET=YOUR_SECRET
RAZORPAY_WEBHOOK_SECRET=YOUR_WEBHOOK_SECRET
RESEND_API_KEY=YOUR_KEY
```

### Edge Worker Sync

```
D1_SYNC_SECRET=YOUR_SYNC_SECRET_MATCHING_WORKER
EDGE_WORKER_URL=https://api.syrabit.ai
COOKIE_DOMAIN=.syrabit.ai
```

---

## Step 4: Deploy

1. After adding variables, Railway will auto-deploy
2. Watch the build logs in the **Deployments** tab
3. First build takes 3-5 minutes
4. Once deployed, your service URL appears in the **Settings** tab

### Verify Deployment

Visit these URLs to confirm everything works:

```
https://YOUR-RAILWAY-URL/api/health
→ Should return 200 OK with service info

https://YOUR-RAILWAY-URL/api/content/library-bundle
→ Should return JSON with boards, subjects, streams
```

---

## Step 5: Update Cloudflare Pages

1. Go to **Cloudflare Pages** → Your project → **Settings** → **Environment variables**
2. Update:

| Variable | Value |
|----------|-------|
| `VITE_BACKEND_URL` | `https://api.syrabit.ai` |
| `VITE_WORKER_API_URL` | `https://api.syrabit.ai` |

3. Trigger a redeploy (push a commit or click **"Retry deployment"**)
4. Visit `https://syrabit.ai` — should load and connect to the API via the edge Worker

> **Note**: The frontend points to `api.syrabit.ai` (the Edge Worker), not directly to Railway. The Worker proxies requests to Railway and serves cached content from D1.

---

## Step 5b: Update Edge Worker BACKEND_URL

After Railway is deployed, update the Worker to point to your Railway URL:

1. Edit `workers/edge-proxy/wrangler.toml`
2. Set `BACKEND_URL` to your Railway URL (e.g., `https://workspacesyrabit-production-0ddc.up.railway.app`)
3. Redeploy:

```bash
cd workers/edge-proxy
wrangler deploy
```

Traffic flow: `syrabit.ai` (Pages) → `api.syrabit.ai` (Worker) → Railway backend

---

## Step 6: Domain Setup

> **Important**: `api.syrabit.ai` is routed to the Cloudflare Worker (edge proxy), **not** directly to Railway. The Worker handles D1 caching, rate limiting, and CORS before forwarding to Railway. Do not point `api.syrabit.ai` DNS directly to Railway — this would bypass all edge features.

The Railway service uses its auto-generated domain (e.g., `workspacesyrabit-production-0ddc.up.railway.app`). The Worker's `BACKEND_URL` points to this Railway URL.

If you want a custom Railway domain (optional, for direct access during debugging):
1. In Railway → Service → **Settings** → **Networking** → **Custom Domain**
2. Use a subdomain like `railway-backend.syrabit.ai` (not `api.syrabit.ai`)
3. Add the CNAME in Cloudflare DNS with **Proxy OFF** (grey cloud)

---

## Step 7: Update Google OAuth Redirect

After your Railway URL is set:

1. Go to Google Cloud Console → APIs & Services → Credentials
2. Edit your OAuth 2.0 Client ID
3. Add to **Authorized redirect URIs**:
   - `https://YOUR-RAILWAY-URL/api/auth/google/callback`
   - `https://api.syrabit.ai/api/auth/google/callback` (if using custom domain)

---

## Step 8: Update Razorpay Webhook

1. Go to Razorpay Dashboard → Settings → Webhooks
2. Update webhook URL to: `https://YOUR-RAILWAY-URL/api/payments/webhook`

---

## OpenAPI schema is suppressed in prod by design (Task #857)

`/openapi.json` and `/docs` are intentionally NOT reachable from the
public internet. Two stacked gates enforce this:

1. **Backend** — `OriginSharedSecretMiddleware`
   (`artifacts/syrabit-backend/middleware.py:79-93`) excludes
   `/openapi.json` + `/docs` from `_ORIGIN_AUTH_OPEN_PATHS`. Hitting the
   Railway hostname directly returns
   `403 {"detail": "Direct origin access denied — must traverse the edge worker."}`
   for those paths even with a real browser UA. Only `/health`,
   `/api/health`, `/api/livez`, `/api/readyz`, and `/api/ready` are
   open without the `X-Origin-Auth` header.
2. **Edge** — the Cloudflare worker
   (`workers/edge-proxy/src/index.ts`) only proxies `/api/*` paths to
   the backend. `/openapi.json` and `/docs` fall through to
   `PAGES_ORIGIN` and serve the SPA HTML 200, so the schema is
   invisible from `https://api.syrabit.ai/openapi.json` too.

Rationale: exposing every route shape is a low-cost reconnaissance
vector and we have no public SDK consumer that needs the live schema.
Internal codegen (Orval) reads from a checked-in spec, not the live
endpoint.

To temporarily expose the schema for one-off internal codegen:

```bash
# 1. Add "/openapi.json" to _ORIGIN_AUTH_OPEN_PATHS in middleware.py
# 2. Redeploy backend:
pnpm run railway:redeploy && pnpm run railway:status
# 3. Look up the current Railway backend hostname. Source of truth:
#      workers/edge-proxy/wrangler.toml  →  BACKEND_URL = "..."
#    or `pnpm run railway:status` (prints the active deployment URL).
#    Export it for the next step:
RAILWAY_BACKEND_URL="$(grep -E '^BACKEND_URL' workers/edge-proxy/wrangler.toml \
  | sed -E 's/.*"([^"]+)".*/\1/')"
# 4. Pull the spec straight from the Railway origin (still gated by
#    Railway-edge IP allowlist + Cloudflare WAF in front of it):
curl -A 'Mozilla/5.0' "${RAILWAY_BACKEND_URL}/openapi.json" \
  -o /tmp/openapi.json
# 5. Revert the middleware change and redeploy.
```

> The audit step in `workers/edge-proxy/DEPLOY.md` that pipes the old
> Railway hostname through `python3 -c "…"` is from a previous service
> URL **and** a previous middleware state. Both the hostname and the
> open-paths allowlist have changed since — that audit step no longer
> applies as written. Use the recipe above instead. (Cleanup of that
> stale runbook step is tracked separately as a follow-up task.)

## Railway Health Check

Railway auto-detects health from your Dockerfile's HEALTHCHECK instruction.
Your Dockerfile already has:

```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=60s \
    CMD curl -f http://localhost:${PORT}/api/health || exit 1
```

No additional configuration needed.

---

## Scaling on Railway

Railway auto-scales based on your usage. To configure:

Go to **Settings** → **Service**:

| Setting | Recommended |
|---------|-------------|
| **Replicas** | 1 (start), increase as needed |
| **CPU limit** | No limit (pay for usage) |
| **Memory limit** | 2 GB (increase if needed) |
| **Restart policy** | Always |

For high traffic, increase replicas:
- 1 replica → ~200-300 concurrent users
- 2 replicas → ~500 concurrent users
- 5 replicas → ~1,500 concurrent users
- 10 replicas → ~3,000 concurrent users

---

## Cost Breakdown

### Railway Pricing (Hobby Plan — $5/month)

| Resource | Rate |
|----------|------|
| vCPU | $0.000463/min (~₹200/month per vCPU) |
| RAM | $0.000231/min (~₹100/month per GB) |
| Included credit | $5/month (₹500) |
| Egress | $0.10/GB after 100GB free |

### Monthly Estimates

| Concurrent Users | CPU | RAM | **Railway Cost** |
|---|---|---|---|
| 50-100 | 0.5 vCPU | 512 MB | **₹150** (within free $5 credit) |
| 100-300 | 1 vCPU | 1 GB | **₹300** |
| 300-500 | 1 vCPU | 2 GB | **₹400** |
| 500-1,000 | 2 vCPU | 4 GB | **₹800** |
| 1,000-3,000 | 4 vCPU | 8 GB | **₹1,600** |
| 5,000+ | 8 vCPU | 16 GB | **₹3,200** |

### Total Cost Comparison

| Hosting | 100 users | 1,000 users | 10,000 users |
|---|---|---|---|
| **Replit** | **₹0** | ₹0 | ₹0 (may hit limits) |
| **Railway** | **₹300** | ₹800 | ₹4,800 |
| **Fly.io** | ₹300 | ₹1,000 | ₹5,000 |
| **AWS EB** | ₹2,400 | ₹6,800 | ₹35,500 |

---

## Troubleshooting

### Build fails
- Check that **Root Directory** is set to `artifacts/syrabit-backend`
- Check build logs in Deployments tab for errors
- Make sure `requirements.txt` is up to date

### App crashes on startup
- Check deploy logs for missing environment variables
- Common cause: `MONGO_URL` not set or malformed
- Make sure `PORT=8000` is set

### CORS errors
- Verify `CORS_ORIGINS` includes `https://syrabit.ai`
- Verify `FRONTEND_URL` is `https://syrabit.ai`
- The backend auto-includes Replit domains — for Railway, add your Railway URL to `CORS_ORIGINS`

### MongoDB timeout
- MongoDB Atlas free tier (M0) can be slow on cold connections
- Backend has built-in 3-attempt retry with backoff for library pre-warm
- Consider upgrading Atlas to M2/M5 ($9-25/month)

### Streaming chat not working
- Railway supports SSE streaming natively — no special config needed
- Check browser console for CORS errors
- Verify the backend URL in CF Pages env vars is correct

---

## Driving deploys from Replit / CI

Day-to-day deploy operations don't require opening the Railway dashboard or
running `railway login` interactively. The `scripts/railway.sh` dispatcher
wraps everything in non-interactive commands that authenticate via the
`RAILWAY_API_TOKEN` secret (already stored in Replit Secrets) and target the
production `syrabit-backend` service by default.

| `pnpm run …`            | What it does                                                                                                                                |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `railway:status`        | Prints active deployment id, status, region, image digest, and a live `/api/health` probe of `api.syrabit.ai`. Read-only.                    |
| `railway:logs`          | Prints the last 200 deploy logs of the current deployment. `… -- -b` for build logs, `… -- -n 500` to widen the window.                      |
| `railway:redeploy`      | Re-runs the **latest already-built image** (no source upload, no rebuild). Polls Railway until the deployment reports `SUCCESS`, exits 0.    |
| `railway:deploy`        | `railway up`-style. Uploads the current `artifacts/syrabit-backend/` tree, builds a fresh image, deploys it, exits 0 only on `SUCCESS`.       |
| `railway:vars`          | Lists variable names on the production service+environment.                                                                                  |
| `railway:var-set …`     | Upserts one or more `KEY=VALUE` pairs, then waits for the resulting deployment to reach `SUCCESS` before exiting 0. Example: `pnpm run railway:var-set LOG_LEVEL=info`. |
| `railway:var-unset …`   | Deletes one or more variables, then waits for the resulting deployment to reach `SUCCESS` before exiting 0. Example: `pnpm run railway:var-unset FEATURE_FLAG_X`.       |

All scripts auto-target the live production project / service / `production`
environment — those defaults are baked into `scripts/railway.sh`. Override
for staging by exporting `RAILWAY_PROJECT_ID`, `RAILWAY_SERVICE_ID`, and/or
`RAILWAY_ENVIRONMENT` (name) before invoking. `RAILWAY_API_TOKEN` must be set
in the shell — in this Replit workspace it's already a Secret, so just open
a terminal and run.

### Example: redeploy + verify

```bash
pnpm run railway:redeploy        # re-runs the existing image
pnpm run railway:status          # confirm status: SUCCESS + healthcheck 200
```

A clean redeploy looks like this (smoke run from this workspace, 2026-04-25):

```text
[railway.sh] redeploying latest built image for service=5acc87f2-… env=production
[railway.sh] found latest deployment: 20d1dfab-…
[railway.sh] redeploy enqueued as deployment 14f2642d-…
[railway.sh] polling deployment 14f2642d-… (timeout 1800s)
[railway.sh] deployment status: BUILDING
[railway.sh] deployment status: DEPLOYING
[railway.sh] deployment status: SUCCESS
[railway.sh] deployment 14f2642d-… succeeded.
```

Followed by `pnpm run railway:status` reporting `active_deployment.status:
"SUCCESS"` and `health.status: 200`.

### CI: `.github/workflows/railway-deploy.yml`

A `workflow_dispatch`-only GitHub Actions workflow runs the same
`scripts/railway.sh` from CI. It uses the `RAILWAY_API_TOKEN` repo secret
and accepts these inputs:

- `mode` — `redeploy` (re-run latest image) or `deploy-from-source`.
- `service` — optional Railway service ID override.
- `environment` — optional environment name (default: `production`).
- `health_url` — public URL probed for HTTP 200 after the deploy
  (default: `https://api.syrabit.ai/api/health`). **Blank this out when
  you override `service`/`environment` for a non-production target**, or
  set it to that target's public hostname; otherwise the probe will
  pass/fail based on production rather than what you actually deployed.

The workflow is gated to `Replit-agent`/`main` and prints status before and
after the deploy. There is no auto-deploy on push from this workflow —
Railway's own GitHub integration handles that separately.

> **Token scope note.** The `redeploy`, `status`, `logs`, `vars`,
> `var-set`, and `var-unset` subcommands talk directly to the Railway
> GraphQL API and work with any token that has access to the project. The
> `deploy` subcommand (source upload) shells out to the Railway CLI's
> `railway up`, which validates the token through a separate code path —
> use a Railway account or team token with full workspace access for that
> one. CI uses such a token via `secrets.RAILWAY_API_TOKEN`.

## Migration from Replit

When ready to switch:

1. Deploy on Railway, verify `/api/health` returns 200
2. Test: `/api/content/library-bundle`, `/api/auth/me`, streaming chat
3. Update CF Pages `VITE_BACKEND_URL` to Railway URL
4. Update Google OAuth redirect URIs
5. Update Razorpay webhook URL
6. Monitor for 24 hours
7. Once stable, you can stop Replit deployment

**Keep the Replit project as a fallback.** You can switch back anytime by changing `VITE_BACKEND_URL` in CF Pages.

---

## Refreshing the Rust Cargo.lock (`backend/rust-core`)

`backend/rust-core/Cargo.lock` is committed to the repo so that Docker, Railway,
and GitHub Actions builds all resolve to the exact same crate versions every
time. The Rust Dockerfile (`backend/rust-core/Dockerfile`) explicitly copies
`Cargo.toml` and `Cargo.lock` (no wildcard) and runs `cargo build --release`,
which honors the lockfile.

**Do not** edit `Cargo.lock` by hand. Refresh it only after an intentional
dependency change in `backend/rust-core/Cargo.toml`:

```bash
cd backend/rust-core

# 1. Update only the crates whose versions you changed in Cargo.toml
cargo update -p <crate-name>

# …or, after a major bump (e.g. axum/tonic/reqwest/sqlx like Task #192),
# regenerate the full lockfile:
cargo generate-lockfile

# 2. Verify the build still resolves and compiles against the new lock
cargo build --locked --release

# 3. Commit Cargo.toml + Cargo.lock together in the same commit
git add Cargo.toml Cargo.lock
```

CI/Railway will fail fast if `Cargo.lock` is missing or out of sync with
`Cargo.toml` (because the Dockerfile uses an explicit `COPY` and the build
runs against the locked graph), which is the desired behavior — it forces
the lockfile bump to land in the same PR as the manifest change.
