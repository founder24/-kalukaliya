# Syrabit.ai Backend — AWS ECS Express Mode Deployment Guide

> ⚠️ **RETIRED PATH (ECS Express Mode only) — DO NOT FOLLOW.** AWS ECS /
> Fargate / EKS are **not** used. The canonical backend origin is now
> **AWS App Runner** (much simpler — no cluster, no task definitions). See
> the App Runner runbook at
> [`../artifacts/syrabit-backend/DEPLOY.md`](../artifacts/syrabit-backend/DEPLOY.md)
> and the strategic plan at
> [`docs/infra/cloud-allocation-plan.md`](./infra/cloud-allocation-plan.md)
> §4.2 (full AWS workload list: App Runner + S3 + SES + Lambda + SQS +
> CloudWatch + Bedrock Cohere + Secrets Manager + IAM). This file is kept
> only for historical reference.

---


## Architecture

```
Users
  │
  ├── https://syrabit.ai ──► Cloudflare Pages (frontend PWA)
  │                            • React + Vite build
  │                            • Global CDN, edge caching
  │
  └── API calls ──► AWS ECS Express Mode (backend API)
                     • FastAPI + Gunicorn in Docker container
                     • Auto-scaling, HTTPS via ALB
                     • Connects to: MongoDB Atlas, Supabase, Upstash Redis
```

## Why ECS Express Mode?

- Simplest way to run containers on AWS (replaces App Runner)
- Connects to GitHub for auto-deploy on push
- No need to manage servers, clusters, or networking
- Auto-scales based on traffic
- Uses your existing Dockerfile — no extra config needed

## Prerequisites

- AWS Account with billing enabled
- GitHub repo with the backend code pushed
- All external services already set up (MongoDB Atlas, Supabase, Upstash Redis)

---

## Step 1: Push Code to GitHub

Make sure your GitHub repo has the `artifacts/syrabit-backend/` directory with:
- `Dockerfile` (already exists and ready)
- `requirements.txt`
- `gunicorn.conf.py`
- `server.py`

---

## Step 2: Create ECS Express Service

1. Go to [Amazon ECS Console](https://console.aws.amazon.com/ecs)
2. Click **"Create service"** (Express Mode will be the default for new services)

### Source Configuration

| Setting | Value |
|---------|-------|
| Deployment source | **GitHub** |
| Connect to | Authorize AWS to access your GitHub repo |
| Repository | Select your Syrabit repo |
| Branch | `main` |
| Dockerfile path | `artifacts/syrabit-backend/Dockerfile` |
| Deployment trigger | **Automatic** (deploys on every push) |

### Service Configuration

| Setting | Recommended Value |
|---------|-------------------|
| Service name | `syrabit-api` |
| CPU | **1 vCPU** |
| Memory | **2 GB** |
| Desired tasks | **1** (start with 1, scale later) |
| Port | **8000** |

### Health Check

| Setting | Value |
|---------|-------|
| Path | `/api/health` |
| Interval | **30 seconds** |
| Timeout | **10 seconds** |
| Healthy threshold | **2** |
| Unhealthy threshold | **3** |
| Start period | **120 seconds** (app needs time to connect to MongoDB) |

---

## Step 3: Add Environment Variables

In ECS → Your service → **Task definition** → **Environment variables**, add these:

### Required (app won't start without these)

| Variable | Value | Where to find it |
|----------|-------|-------------------|
| `MONGO_URL` | `mongodb+srv://...` | MongoDB Atlas → Connect → Connection String |
| `DB_NAME` | `test_database` | Your MongoDB database name |
| `JWT_SECRET` | (random 64-char string) | Generate with: `openssl rand -hex 32` |
| `ADMIN_JWT_SECRET` | (different random string) | Generate with: `openssl rand -hex 32` |
| `ADMIN_PASSWORDS` | Your admin password(s) | Comma-separated |
| `ADMIN_EMAILS` | `admin@syrabit.ai` | Comma-separated |
| `ADMIN_NAMES` | `Administrator` | Comma-separated |
| `PORT` | `8000` | Fixed |

### Database & Cache

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | Your Supabase/PostgreSQL connection string |
| `SUPABASE_URL` | `https://czeznmqogtwecidhpysa.supabase.co` |
| `SUPABASE_SERVICE_KEY` | Your Supabase service key |
| `UPSTASH_REDIS_REST_URL` | Your Upstash Redis URL |
| `UPSTASH_REDIS_REST_TOKEN` | Your Upstash Redis token |

### AI Provider Keys

| Variable | Notes |
|----------|-------|
| `GROQ_API_KEY` | Primary LLM provider |
| `GROQ_API_KEY_2` | Second key for rate limit relief |
| `CEREBRAS_API_KEY` | Fast inference (hedged with Groq) |
| `GEMINI_API_KEY` | Google Gemini |
| `GEMINI_API_KEY_2` | Second Gemini key |
| `SARVAM_API_KEY` | Assamese translation + LLM |
| `SARVAM_API_KEY_2` | Second Sarvam key |
| `FIREWORKS_API_KEY` | Fallback LLM provider |
| `OPENROUTER_API_KEY` | Fallback LLM provider |
| `EMERGENT_API_KEY` | Badge/attribution |
| `XAI_API_KEY` | xAI provider (optional) |

### Auth & Payments

| Variable | Notes |
|----------|-------|
| `GOOGLE_CLIENT_ID` | Google OAuth login |
| `GOOGLE_CLIENT_SECRET` | Google OAuth secret |
| `RAZORPAY_KEY_ID` | Razorpay payments |
| `RAZORPAY_KEY_SECRET` | Razorpay secret |
| `RAZORPAY_WEBHOOK_SECRET` | Razorpay webhooks |
| `RESEND_API_KEY` | Email sending |

### App Configuration

| Variable | Value |
|----------|-------|
| `FRONTEND_URL` | `https://syrabit.ai` |
| `CORS_ORIGINS` | `https://syrabit.ai,https://www.syrabit.ai` |
| `SECURE_COOKIES` | `true` |
| `LOG_LEVEL` | `info` |
| `GUNICORN_WORKERS` | `2` |

> **Tip for secrets:** Use AWS Secrets Manager for sensitive values (API keys, passwords). ECS can inject them as environment variables without storing them in plain text.

---

## Step 4: Deploy

1. Click **"Create"** / **"Deploy"**
2. Wait 5-10 minutes for the Docker image to build and the service to start
3. ECS will provide a public URL (via the load balancer): `https://syrabit-api-XXXX.us-east-1.amazonaws.com` or similar
4. Test: visit `https://YOUR-URL/api/health` — should return `200 OK`
5. Test library: visit `https://YOUR-URL/api/content/library-bundle` — should return JSON with boards and subjects

---

## Step 5: Update Cloudflare Pages

Once your ECS service is running and healthy:

1. Go to **Cloudflare Pages** → Your project → **Settings** → **Environment variables**
2. Update these:

| Variable | New Value |
|----------|-----------|
| `VITE_BACKEND_URL` | `https://YOUR-ECS-URL` |
| `VITE_WORKER_API_URL` | `https://YOUR-ECS-URL` |

3. Trigger a redeploy (push a commit or click **"Retry deployment"** in CF Pages)
4. Visit `https://syrabit.ai/library` — should load all subjects

---

## Step 6: Custom Domain (Optional)

To use `api.syrabit.ai` instead of the long AWS URL:

### Option A: Via Cloudflare DNS (recommended)
1. In ECS/ALB → Note the load balancer DNS name
2. Go to **Cloudflare DNS** → Add a CNAME record:
   - **Name:** `api`
   - **Target:** Your ALB DNS name (e.g., `syrabit-api-lb-XXXX.us-east-1.elb.amazonaws.com`)
   - **Proxy:** ON (orange cloud) — Cloudflare handles SSL
3. Update CF Pages env vars to use `https://api.syrabit.ai`

### Option B: Via AWS Certificate Manager
1. Request an SSL certificate for `api.syrabit.ai` in ACM
2. Attach it to the ALB listener
3. Add CNAME in Cloudflare DNS (Proxy OFF — grey cloud, AWS handles SSL)

---

## Step 7: Auto-Scaling (Optional)

Configure auto-scaling after your service is stable:

| Setting | Value |
|---------|-------|
| Min tasks | **1** |
| Max tasks | **4** |
| Scale-out metric | CPU utilization > 70% |
| Scale-in metric | CPU utilization < 30% |
| Cooldown | 60 seconds |

---

## Estimated Monthly Costs

| Resource | Cost |
|----------|------|
| ECS Fargate (1 vCPU, 2GB, always on) | ~$30-35/month |
| ECS Fargate (with scale-to-zero) | ~$10-20/month |
| ALB (Application Load Balancer) | ~$16/month + $0.008/LCU-hour |
| MongoDB Atlas (M0 free tier) | Free |
| Supabase (free tier) | Free |
| Upstash Redis (free tier) | Free |
| Cloudflare Pages | Free |
| **Total estimate** | **$45-55/month** (always-on) or **$25-35/month** (low traffic) |

> **Cost tip:** For a startup, the always-on config (~$45/month) avoids cold starts. The Replit backend is simpler and cheaper if budget is tight — ECS is better when you need more control, reliability, or are scaling up.

---

## Troubleshooting

### Docker build fails
- Check that the Dockerfile path is `artifacts/syrabit-backend/Dockerfile`
- Make sure `requirements.txt` is up to date
- Check ECS build logs in the console

### Health check keeps failing
- The app needs ~60-120 seconds to start (MongoDB connections, index creation, library pre-warm)
- Set the health check **start period** to at least **120 seconds**
- Verify port is `8000`
- Check task logs in ECS for startup errors

### CORS errors on syrabit.ai
- Make sure `CORS_ORIGINS` env var includes `https://syrabit.ai`
- Make sure `FRONTEND_URL` is `https://syrabit.ai`
- The backend auto-adds `*.awsapprunner.com` and `*.amazonaws.com` origins

### MongoDB timeout on library page
- MongoDB Atlas free tier (M0) can be slow on cold connections
- The backend has built-in retry logic (3 attempts with backoff)
- Consider upgrading to M2/M5 for better performance ($9-25/month)

### Container keeps restarting
- Check task logs in ECS console
- Common cause: missing required env vars (MONGO_URL, JWT_SECRET)
- Increase memory if you see OOM (Out of Memory) errors

---

## Migration from Replit

When ready to switch from Replit to ECS:

1. Deploy ECS service and verify `/api/health` returns 200
2. Test key endpoints: `/api/content/library-bundle`, `/api/auth/me`
3. Update CF Pages `VITE_BACKEND_URL` to the ECS URL
4. Monitor for 24 hours
5. Once stable, you can stop the Replit deployment

**Do NOT delete the Replit project** — keep it as a fallback. You can always switch back by changing `VITE_BACKEND_URL` in CF Pages.
