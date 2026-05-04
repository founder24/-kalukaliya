# Syrabit.ai Backend — Railway Deployment Guide

> ⚠️ **RETIRED PATH — DO NOT FOLLOW.** Railway is being decommissioned in
> Task #335. The canonical backend origin is now **AWS App Runner** in
> us-west-2 per
> [`../../docs/infra/cloud-allocation-plan.md`](../../docs/infra/cloud-allocation-plan.md)
> §4.2. Use [`./DEPLOY.md`](./DEPLOY.md) for the active App Runner runbook.
> This file is kept only for historical reference and rollback during the
> cutover soak window.

---


## Prerequisites
- Railway account (https://railway.app)
- GitHub repo connected to Railway (recommended) OR Railway CLI
- All environment variables from `.env.example` ready

## Step 1: Create Railway Project (Dashboard — Recommended)

1. Go to https://railway.app/new
2. Click **"Deploy from GitHub repo"**
3. Select your Syrabit repository
4. **CRITICAL**: After the service is created, go to **Settings > Source**:
   - Set **Root Directory** to: `artifacts/syrabit-backend`
   - This ensures Railway finds the Dockerfile and Python code (not the root package.json)
5. Railway will auto-detect the Dockerfile and start building

### Alternative: Railway CLI

```bash
railway login
railway init
# Select "Empty Project", name: syrabit-backend

cd artifacts/syrabit-backend
railway link
railway up
```

> **If the CLI deploy fails with `pnpm: not found`**: Railway is reading the
> root `package.json` instead of the backend's `Dockerfile`. You MUST set
> the Root Directory to `artifacts/syrabit-backend` in the Railway dashboard
> under **Settings > Source > Root Directory**.

## Step 3: Set Environment Variables

### Option A: Railway Dashboard (Recommended)
1. Go to https://railway.app/dashboard
2. Click your `syrabit-backend` service
3. Go to **Variables** tab
4. Click **RAW Editor** and paste all variables from `.env.example` with real values

### Option B: Railway CLI
```bash
# Core databases
railway variables set MONGO_URL="mongodb+srv://..."
railway variables set DB_NAME="test_database"
railway variables set DATABASE_URL="postgresql://..."
railway variables set UPSTASH_REDIS_REST_URL="https://..."
railway variables set UPSTASH_REDIS_REST_TOKEN="AXxx..."
railway variables set SUPABASE_URL="https://xxx.supabase.co"
railway variables set SUPABASE_SERVICE_KEY="eyJ..."
railway variables set SUPABASE_ANON_KEY="eyJ..."

# Security
railway variables set JWT_SECRET="$(python3 -c 'import secrets;print(secrets.token_hex(48))')"
railway variables set ADMIN_JWT_SECRET="$(python3 -c 'import secrets;print(secrets.token_hex(48))')"
railway variables set ADMIN_EMAILS="admin@syrabit.ai"
railway variables set ADMIN_PASSWORDS="your-secure-password"
railway variables set ADMIN_NAMES="Admin"
railway variables set GOOGLE_OAUTH_CLIENT_ID="xxx.apps.googleusercontent.com"
railway variables set GOOGLE_OAUTH_CLIENT_SECRET="GOCSPx-xxx"

# CORS & Cookies
railway variables set CORS_ORIGINS="https://syrabit.ai,https://www.syrabit.ai,https://api.syrabit.ai"
railway variables set SECURE_COOKIES="true"
railway variables set COOKIE_DOMAIN=".syrabit.ai"
railway variables set FRONTEND_URL="https://syrabit.ai"

# AI Providers
# NOTE: do NOT set GEMINI_API_KEY on Railway. After the BYOK migration
# (Task #666), Gemini auth is provided per-request by the Cloudflare AI
# Gateway binding using the user-supplied key. Setting GEMINI_API_KEY
# here will be ignored by the chat path and only re-introduces a shared
# secret on the backend.
railway variables set GROQ_API_KEY="gsk_xxx"
railway variables set GROQ_API_KEY_2="gsk_xxx"
railway variables set CEREBRAS_API_KEY="csk-xxx"
railway variables set SARVAM_API_KEY="xxx"
railway variables set SARVAM_API_KEY_2="xxx"
railway variables set OPENROUTER_API_KEY="sk-or-xxx"

# Vertex AI Gemini Flash chat (Task #607) — recommended for low TTFT.
# Auth on Railway is via the inline JSON blob below (raw service-account
# key, single-line). Once VERTEX_PROJECT_ID is set the chat stream uses
# Vertex by default; the legacy SLM pool above is the automatic fallback.
railway variables set VERTEX_PROJECT_ID="your-gcp-project"
railway variables set VERTEX_LOCATION="us-central1"
railway variables set VERTEX_GEMINI_MODEL="gemini-2.5-flash"
railway variables set VERTEX_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'
railway variables set CHAT_DEFAULT_MODEL="vertex/gemini-flash"

# Payments & Email
railway variables set RAZORPAY_KEY_ID="rzp_live_xxx"
railway variables set RAZORPAY_KEY_SECRET="xxx"
railway variables set RAZORPAY_WEBHOOK_SECRET="xxx"
railway variables set RESEND_API_KEY="re_xxx"
railway variables set EMAIL_FROM="noreply@syrabit.ai"

# Edge Worker
railway variables set D1_SYNC_SECRET="your-sync-secret"
railway variables set EDGE_WORKER_URL="https://api.syrabit.ai"

# Server Tuning
railway variables set PORT="8000"
railway variables set GUNICORN_WORKERS="2"
railway variables set LOG_LEVEL="warning"
railway variables set LLM_MAX_CONCURRENT="40"
```

## Step 4: Generate a Public Domain

```bash
# Generate a Railway domain
railway domain
# Output: syrabit-backend-production.up.railway.app
```

Or in the dashboard: **Settings > Networking > Generate Domain**

### Custom Domain (Optional)
1. In Railway dashboard: **Settings > Networking > Custom Domain**
2. Add: `backend.syrabit.ai`
3. Add the CNAME record to your DNS:
   - Type: CNAME
   - Name: backend
   - Value: (Railway provides this)

## Step 5: Update Cloudflare Edge Worker

After Railway is deployed, update the Worker's `BACKEND_URL` to point to Railway:

```bash
cd workers/edge-proxy

# Update wrangler.toml BACKEND_URL
# Set to your Railway URL, e.g.:
# BACKEND_URL = "https://workspacesyrabit-production-0ddc.up.railway.app"

# Redeploy the worker
npx wrangler deploy
```

## Step 6: Verify Deployment

```bash
# Health check
curl https://syrabit-backend-production.up.railway.app/api/health

# Expected response:
# {"status":"ok","mongo":"connected","redis":"connected","pg":"connected"}

# Test through edge worker
curl https://api.syrabit.ai/api/health

# AI healthcheck (Task #678) — Railway points its healthcheck here.
# Returns 200 only when the cached Vertex/Gemini probe is healthy and
# fresh (within 2x VERTEX_PROBE_INTERVAL_S). Returns 503 on a broken
# rollout so Railway auto-rolls back instead of serving 502s.
curl -i https://syrabit-backend-production.up.railway.app/healthz/ai
```

### Configure Railway Healthcheck Path

In **Settings > Deploy > Healthcheck**:

- **Healthcheck Path**: `/healthz/ai`
- **Healthcheck Timeout**: `10` seconds
- **Healthcheck Start Period**: keep at `300` seconds (boot + first
  Vertex probe). Until the startup probe completes the endpoint
  intentionally returns 503 with `{"status":"unknown"}` — that is what
  the start-period grace window covers.

Pointing Railway at `/healthz/ai` (instead of the generic `/api/health`)
means a deploy where Gemini auth is broken — wrong
`VERTEX_SERVICE_ACCOUNT_JSON`, revoked AI Gateway BYOK key, etc. — will
fail the healthcheck and Railway will auto-rollback to the last good
revision instead of cutting traffic over to a service that 502s every
chat request.

## Resource Recommendations

### Starter Plan (~$5/month at low traffic)
- 8 GB RAM, shared vCPU
- 512 MB disk
- Railway Hobby plan ($5/month includes $5 usage credit)

### Production Plan (~$20-50/month)
- 8 GB RAM, 2 vCPU
- `GUNICORN_WORKERS=2`
- `LLM_MAX_CONCURRENT=40`

### Cost Optimization
- The Edge Worker (Cloudflare) caches 80-90% of content reads
- Railway only handles: AI chat, auth, admin, webhooks, D1 sync
- At low traffic (<1000 DAU): expect ~$5-15/month
- At moderate traffic (1000-10000 DAU): expect ~$20-50/month

## Architecture After Deployment

```
User → syrabit.ai (CF Pages)
         │
         ▼
    api.syrabit.ai (CF Worker)
         │
         ├─ Content reads → D1 (edge, sub-ms)
         ├─ Cache hits → CF Cache API
         └─ AI chat / auth / admin → Railway backend
                                        │
                                        ├─ MongoDB Atlas
                                        ├─ Supabase (PostgreSQL)
                                        └─ Upstash Redis
```

## Troubleshooting

### Build Fails
- Check Railway build logs for pip install errors
- Ensure `requirements.txt` is in the same directory as `Dockerfile`

### Health Check Fails
- Railway start period is 300s (5 min) — startup takes ~30-60s
- Check logs: `railway logs`
- Verify all required env vars are set

### Cookies Not Working
- Ensure `COOKIE_DOMAIN=.syrabit.ai`
- Ensure `SECURE_COOKIES=true`
- Ensure Railway domain is HTTPS (it is by default)

### CORS Errors
- Add Railway domain to `CORS_ORIGINS`
- Ensure `https://syrabit.ai` and `https://api.syrabit.ai` are included

## Driving deploys from Replit / CI

You don't need to open the Railway dashboard or run `railway login` to
trigger a deploy. The `scripts/railway.sh` dispatcher in this repo wraps
the Railway GraphQL API and CLI in non-interactive subcommands that
authenticate using the `RAILWAY_API_TOKEN` Replit Secret and target this
service by default.

```bash
# from the repo root
pnpm run railway:status        # active deployment + /api/health probe
pnpm run railway:logs          # last 200 deploy logs
pnpm run railway:logs -- -b    # last 200 build logs
pnpm run railway:redeploy      # re-run the latest image, no rebuild
pnpm run railway:deploy        # railway up: upload artifacts/syrabit-backend/, build, deploy
pnpm run railway:vars          # list variable names on the service
pnpm run railway:var-set FOO=bar
pnpm run railway:var-unset FOO
```

`redeploy` and `deploy` exit `0` only after Railway reports the deployment
as `SUCCESS`. `redeploy` polls the GraphQL API; `deploy` uses
`railway up --ci` which streams build logs and waits for healthcheck.

To target a different project / service / environment, override:

```bash
RAILWAY_PROJECT_ID=… RAILWAY_SERVICE_ID=… RAILWAY_ENVIRONMENT=staging \
  pnpm run railway:status
```

The same scripts run from CI via `.github/workflows/railway-deploy.yml`
(`workflow_dispatch` only, gated to `Replit-agent`/`main`).

See [`docs/RAILWAY-DEPLOYMENT.md`](../../docs/RAILWAY-DEPLOYMENT.md#driving-deploys-from-replit--ci)
for the full reference.
