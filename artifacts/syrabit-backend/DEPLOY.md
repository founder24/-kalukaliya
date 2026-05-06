# Deploying Syrabit Backend on AWS App Runner

> ✅ **CANONICAL PATH — this is the active backend deployment runbook.**
> AWS App Runner in `us-west-2` is the single canonical FastAPI origin
> per [`../../docs/infra/cloud-allocation-plan.md`](../../docs/infra/cloud-allocation-plan.md)
> §4.2. Cloudflare Worker fronts it via mTLS at `api.syrabit.ai`.
> Per-service inventory at
> [`../../docs/infra/cloud-service-breakdown.md`](../../docs/infra/cloud-service-breakdown.md)
> §2. Workers, Rust core, and cron live on Azure Container Apps (§3).

---


## Architecture

```
Users ──→ Cloudflare Pages (syrabit.ai) ──→ Static frontend (React/Vite)
               │
               │ API calls
               ▼
         AWS App Runner (api.syrabit.ai)
          ┌────┴────────────────────────┐
          │  Gunicorn + Uvicorn Workers │
          │  FastAPI application        │
          └────┬───────┬───────┬────────┘
               │       │       │
               ▼       ▼       ▼
        MongoDB    Supabase   Upstash
        Atlas      Postgres   Redis
```

- **Frontend**: Cloudflare Pages (`syrabit.ai`)
- **Backend**: AWS App Runner (`api.syrabit.ai`)
- **Database**: MongoDB Atlas (primary), Supabase PostgreSQL (supplementary)
- **Cache**: Upstash Redis

---

## Prerequisites

- AWS account with billing enabled
- GitHub repository containing the backend code
- MongoDB Atlas cluster provisioned
- Supabase project provisioned
- Upstash Redis instance provisioned
- Cloudflare account managing `syrabit.ai` DNS
- API keys for payment providers (Razorpay/Stripe) and LLM providers

---

## Step 1: Install the AWS Connector for GitHub

1. Open the [AWS App Runner console](https://console.aws.amazon.com/apprunner).
2. Click **Create service**.
3. Under **Source**, select **Source code repository**.
4. Click **Add new** next to the GitHub connection.
5. Follow the prompts to install the **AWS Connector for GitHub** app on your GitHub account/org.
6. Grant access to the repository containing the Syrabit backend.
7. Once connected, the repository will appear in the dropdown.

---

## Step 2: Create the App Runner Service

App Runner supports two deployment modes:

| Mode | How it works | Best for |
|------|-------------|----------|
| **Source-based** | App Runner reads `apprunner.yaml`, installs deps, runs the app | Simple setup, no Docker knowledge needed |
| **Image-based** | You push a Docker image to ECR; App Runner pulls and runs it | Full control over the build environment |

**Recommended: Source-based deployment** (uses the `apprunner.yaml` in the repo).

### Source-Based Setup

1. In the App Runner console, click **Create service**.
2. **Source**: Select **Source code repository**.
3. **Connect to GitHub**: Choose your GitHub connection and select the repository.
4. **Branch**: `main` (or your production branch).
5. **Deployment trigger**: **Automatic** (deploys on every push to the branch).
6. **Build settings**: Select **Use a configuration file** — App Runner will read `apprunner.yaml` from the repository root. If the backend is in a subdirectory, set the **Source directory** to `artifacts/syrabit-backend`.
7. **Service settings**:
   - **Service name**: `syrabit-backend`
   - **Virtual CPU & Memory**: Start with **1 vCPU / 2 GB** (scale up if needed).
   - **Port**: `8000`
8. **Health check** (must be configured in the console — not supported in `apprunner.yaml`):
   - **Protocol**: HTTP
   - **Path**: `/api/health`
   - **Interval**: 10 seconds
   - **Timeout**: 5 seconds
   - **Healthy threshold**: 1
   - **Unhealthy threshold**: 3
9. **Auto scaling**:
   - **Min instances**: 1 (to avoid cold starts)
   - **Max instances**: 4 (adjust based on traffic)
   - **Max concurrency**: 80
10. Click **Create & deploy**.

### Image-Based Setup (Alternative)

If you prefer Docker-based deployment:

1. Build and push the Docker image to Amazon ECR:
   ```bash
   aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.ap-south-1.amazonaws.com
   docker build -t syrabit-backend .
   docker tag syrabit-backend:latest <account-id>.dkr.ecr.ap-south-1.amazonaws.com/syrabit-backend:latest
   docker push <account-id>.dkr.ecr.ap-south-1.amazonaws.com/syrabit-backend:latest
   ```
2. In the App Runner console, select **Container registry** → **Amazon ECR**.
3. Select the image and configure the same health check and scaling settings above.

---

## Step 3: Configure Environment Variables

In the App Runner console, go to your service → **Configuration** → **Environment variables**.

Set every variable listed below. Values marked **required** must be set for the service to start. All secrets should be entered directly in the App Runner console (they are encrypted at rest).

### Required

| Variable | Description |
|----------|-------------|
| `MONGO_URL` | MongoDB Atlas connection string |
| `DB_NAME` | MongoDB database name (e.g. `syrabit_prod`) |
| `JWT_SECRET` | Long random secret for user JWTs |
| `ADMIN_JWT_SECRET` | Separate random secret for admin JWTs |
| `LLM_PROVIDER` | LLM provider: `sarvam`, `openai`, `groq`, `fireworks` (Gemini is BYOK via the Cloudflare AI Gateway binding — see note below — and is not selectable as a backend provider) |
| `LLM_MODEL` | Model identifier (e.g. `sarvam-m`) |
| `SARVAM_API_KEY` | API key for chosen LLM provider |
| `VERTEX_PROJECT_ID` | GCP project for the Vertex Gemini 2.5 Flash content-formatter (Task #490 scope-down: Vertex is **formatter-only** — no chat / embed / Vector Search). Leaving blank disables the formatter polish pass. |
| `VERTEX_LOCATION` | Vertex region (default `us-central1`) |
| `VERTEX_GEMINI_MODEL` | Vertex Gemini model id (default `gemini-2.5-flash`) |
| `VERTEX_SERVICE_ACCOUNT_JSON` | Inline service-account JSON (raw, single-line) — alternative to `GOOGLE_APPLICATION_CREDENTIALS` |
| `CHAT_DEFAULT_MODEL` | System-wide chat model when admin has no override (e.g. `openai/gpt-oss-20b`). Task #490: the legacy `vertex/gemini-flash` value is no longer valid — Vertex was removed from the chat hot-path; if set it is transparently rewritten to `openai/gpt-oss-20b` at dispatch. |
| `UPSTASH_REDIS_REST_URL` | Upstash Redis REST endpoint |
| `UPSTASH_REDIS_REST_TOKEN` | Upstash Redis REST token |
| `RESEND_API_KEY` | Resend API key for transactional email |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase service role key |
| `SUPABASE_ANON_KEY` | Supabase anonymous key |
| `DATABASE_URL` | Supabase/Postgres connection string |
| `RAZORPAY_KEY_ID` | Razorpay key ID |
| `RAZORPAY_KEY_SECRET` | Razorpay key secret |
| `ADMIN_EMAILS` | Comma-separated admin email addresses |
| `ADMIN_PASSWORDS` | Comma-separated admin passwords (same order) |
| `ADMIN_NAMES` | Comma-separated admin display names (same order) |
| `FRONTEND_URL` | `https://syrabit.ai` |
| `CORS_ORIGINS` | `https://syrabit.ai,https://www.syrabit.ai` |
| `COOKIE_DOMAIN` | `.syrabit.ai` |
| `SECURE_COOKIES` | `true` |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8000` | Server port (App Runner routes traffic to this port) |
| `GUNICORN_WORKERS` | auto-detect | Number of Gunicorn workers (auto-detects from CPU count; leave empty for 1 vCPU services, set to `2` for 2+ vCPU) |
| `GUNICORN_THREADS` | `2` | Threads per worker |
| `LOG_LEVEL` | `warning` | Gunicorn log level |
| `LLM_MAX_CONCURRENT` | `20` | Max concurrent LLM requests |
| `LLM_BATCH_WINDOW_MS` | `15` | LLM batch window in milliseconds |
| `EMAIL_FROM` | `noreply@syrabit.ai` | Sender address for emails |
| `OPENAI_API_KEY` | — | OpenAI key (if using OpenAI provider) |
| `GROQ_API_KEY` | — | Groq key (if using Groq provider) |
| `XAI_API_KEY` | — | xAI key (if using xAI provider) |
| `AWS_ACCESS_KEY_ID` | — | AWS key (if using Bedrock provider) |
| `AWS_SECRET_ACCESS_KEY` | — | AWS secret (if using Bedrock provider) |
| `AWS_REGION` | `us-east-1` | AWS region for Bedrock |
| `REDIS_URL` | — | Plain Redis URL (fallback if Upstash not set) |
| `APPRUNNER_SERVICE_URL` | — | App Runner default domain (additionally added to CORS allow list) |

> **Gemini / BYOK (Task #666):** `GEMINI_API_KEY` is intentionally absent
> from both tables above. After the BYOK migration, Gemini auth is
> provided by the Cloudflare AI Gateway BYOK binding using the
> user-supplied key — not by a backend-side env var. Do not set
> `GEMINI_API_KEY` on Railway, Cloud Run, or App Runner. The chat path
> will ignore it and you only re-introduce a shared secret on the origin.

> **CORS and App Runner domains**: The backend automatically allows any `*.awsapprunner.com` origin via regex matching, so the API works immediately after first deploy before a custom domain is configured. No manual setup is needed. Optionally, set `APPRUNNER_SERVICE_URL` to your specific App Runner URL for an additional explicit CORS entry.

---

## Step 4: Custom Domain Setup (`api.syrabit.ai`)

1. In the App Runner console, go to your service → **Custom domains**.
2. Click **Link domain** and enter `api.syrabit.ai`.
3. App Runner provides DNS validation records (CNAME). Add these in Cloudflare:
   - **Type**: CNAME
   - **Name**: the validation subdomain App Runner provides (e.g. `_abcdef.api`)
   - **Target**: the validation target App Runner provides
   - **Proxy status**: **DNS only** (grey cloud) — validation requires direct DNS resolution.
4. Wait for App Runner to show the domain as **Active** (can take 10–30 minutes).
5. Add the production CNAME in Cloudflare:
   - **Type**: CNAME
   - **Name**: `api`
   - **Target**: your App Runner service URL (e.g. `abc123.ap-south-1.awsapprunner.com`)
   - **Proxy status**: **DNS only** (grey cloud) — App Runner provides its own TLS certificate, so Cloudflare proxying is not needed and may cause certificate conflicts.

> **Note**: Unlike the previous VPS setup, do **not** enable Cloudflare proxying (orange cloud) for the `api` record. App Runner handles TLS termination with its own managed certificate.

---

## Step 5: Verify Deployment

### Health Check

```bash
curl https://api.syrabit.ai/api/health
```

Expected response:
```json
{"status": "ok", ...}
```

### CORS Verification

From the frontend domain, verify the API responds with correct CORS headers:

```bash
curl -I -X OPTIONS https://api.syrabit.ai/api/health \
  -H "Origin: https://syrabit.ai" \
  -H "Access-Control-Request-Method: GET"
```

Confirm the `Access-Control-Allow-Origin` header includes `https://syrabit.ai`.

### App Runner Console

Check the **Logs** tab in the App Runner console for any startup errors. Logs are streamed to CloudWatch Logs automatically.

---

## Step 6: Update Webhook URLs

After deploying to `api.syrabit.ai`, update webhook endpoints in each payment provider's dashboard:

| Provider | Webhook URL |
|----------|-------------|
| Razorpay | `https://api.syrabit.ai/api/webhooks/razorpay` |
| Stripe   | `https://api.syrabit.ai/api/webhooks/stripe` |

No code changes are needed — only the dashboard URLs must be updated.

---

## Step 6.5: Cloudflare Pages Deploy Hook (prerender refresh)

The frontend's prerendered subject and chapter HTML (Task #385) is built
at deploy time. To keep that HTML in sync with admin content edits
(Task #387), the backend triggers a Cloudflare Pages deploy hook.

1. In Cloudflare Pages → your project → **Settings** → **Builds &
   deployments** → **Deploy hooks**, click **Add deploy hook** with:
   - Hook name: `syrabit-content-refresh`
   - Branch to build: `main` (or your production branch)
2. Copy the generated URL and set it on the App Runner service as the
   secret `CF_PAGES_DEPLOY_HOOK_URL`.
3. (Optional) Tune the cadence with these env vars (defaults shown):
   - `CF_PAGES_DEPLOY_COALESCE=60` — seconds to batch admin edits before firing
   - `CF_PAGES_DEPLOY_MIN_INTERVAL=300` — minimum seconds between consecutive fires
   - `CF_PAGES_DEPLOY_NIGHTLY_INTERVAL=86400` — leader-only safety-net cadence

Once configured:
- Subject/chapter create / update / delete / bulk-status → debounced rebuild
- `POST /api/admin/prerender/refresh` (admin auth, optional `?immediate=true`) → manual trigger
- `GET /api/admin/prerender/status` (admin auth) → inspect last fire / pending state
- A leader-elected nightly fire ensures stale content gets refreshed even
  if admin edits go through paths that bypass the schedule helper.

When `CF_PAGES_DEPLOY_HOOK_URL` is unset the backend silently no-ops, so
non-prod environments are unaffected.

---

## Step 7: Update Frontend Environment

In the Cloudflare Pages dashboard for the frontend:

1. Go to **Settings** → **Environment variables**.
2. Set `VITE_BACKEND_URL` to `https://api.syrabit.ai` for the **Production** environment.
3. Trigger a redeploy of the frontend for the change to take effect.

---

## Production Checklist

### Environment & Secrets
- [ ] All required environment variables are set in the App Runner console (no `CHANGE_ME` values)
- [ ] `JWT_SECRET` and `ADMIN_JWT_SECRET` are unique, long random strings
- [ ] `COOKIE_DOMAIN` is set to `.syrabit.ai`
- [ ] `CORS_ORIGINS` includes `https://syrabit.ai,https://www.syrabit.ai`
- [ ] `FRONTEND_URL` is `https://syrabit.ai`
- [ ] `SECURE_COOKIES` is `true`

### Domain & DNS
- [ ] Custom domain `api.syrabit.ai` is linked and shows **Active** in App Runner
- [ ] Cloudflare CNAME for `api` points to the App Runner service URL (DNS only, grey cloud)
- [ ] `curl https://api.syrabit.ai/api/health` returns `{"status":"ok"}`

### External Services
- [ ] Razorpay webhook URL updated to `https://api.syrabit.ai/api/webhooks/razorpay`
- [ ] Stripe webhook URL updated to `https://api.syrabit.ai/api/webhooks/stripe`
- [ ] MongoDB Atlas allows connections from App Runner (IP allowlist or `0.0.0.0/0` for managed services)
- [ ] Supabase connection pooler enabled if using connection limits

### App Runner Configuration
- [ ] Health check configured: path `/api/health`, interval 10s, timeout 5s
- [ ] Auto-scaling: min 1 instance (avoids cold starts), max 4 instances
- [ ] Max concurrency set appropriately (default 80)
- [ ] Deployment trigger set to **Automatic** on the production branch

### Frontend
- [ ] `VITE_BACKEND_URL` in Cloudflare Pages set to `https://api.syrabit.ai`
- [ ] Frontend redeployed after updating the environment variable

---

## Notes on Other Deployment Files

### `docker-compose.yml`
The `docker-compose.yml` file is for **local development only**. It is not used by App Runner. Keep it in the repo for developers who want to run the backend locally with Docker.

### `Dockerfile`
The `Dockerfile` is used by App Runner if you choose **image-based deployment** (pushing to ECR). For **source-based deployment**, App Runner uses `apprunner.yaml` instead and ignores the Dockerfile.

### `.dockerignore`
Relevant only for Docker image builds (local development or ECR-based deployment). App Runner source-based deployment does not use it.
