---
name: Syrabit credential map
description: Where every secret/env var lives across GCP, Cloudflare, GitHub for the full Syrabit stack
---

## GCP Secret Manager → Cloud Run (all mounted as env vars)
All set via: `gcloud run services update syrabit-backend --region=asia-south1 --update-secrets=NAME=SECRET_NAME:latest`

### Confirmed set as of June 2026
- `JWT_SECRET` (secret: `jwt-secret`)
- `MONGODB_URI` (secret: `MONGODB_URI`)
- `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN`
- `GOOGLE_APPLICATION_CREDENTIALS_JSON` (Vertex AI SA key)
- `SARVAM_API_KEY`, `GEMINI_API_KEY`
- `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`
- `POSTHOG_API_KEY`, `RESEND_API_KEY`
- `ADMIN_JWT_SECRET`, `RESET_TOKEN_SECRET`, `TRANSLATE_CRON_SECRET`
- `INDEXNOW_API_KEY`, `INDEXNOW_INTERNAL_SECRET`
- `VERTEX_PROJECT_ID`
- `EDGE_SHARED_SECRET` (secret: `edge-shared-secret`)
- `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` (secret: `jwt-private-key` / `jwt-public-key`)
- `APP_ENV=production`, `JWT_ALGORITHM=HS256` (plain env vars, not secrets)

### Missing / not yet set (degrade features)
- `SENTRY_DSN` — no error tracking in production
- `VERTEX_SEARCH_DATASTORE_ID` — RAG search returns empty (AI chat falls back to Gemini only)
- `CF_PAGES_DEPLOY_HOOK` — auto-rebuild Cloudflare Pages after content changes (secret name: `cf-pages-deploy-hook`; hook URL must be created in CF Pages dashboard first — see `infra/runbooks/cf-pages-deploy-hook-setup.md`)
- `ADMIN_EMAIL` / `ADMIN_PASSWORD` — auto-create admin account on startup

## Cloudflare Worker Secrets (syrabitworker-prod)
Set via: Dashboard → Workers & Pages → syrabitworker-prod → Settings → Variables → Secrets
OR: `npx wrangler secret put NAME --env production`

- `JWT_SECRET` — must exactly match GCP value
- `EDGE_SHARED_SECRET` — must exactly match GCP value
- `GOOGLE_SA_KEY` — full JSON of `cloudflare-edge-invoker` SA (✅ set June 2026; CF Worker secret is separate from Replit GOOGLE_SA_KEY secret)
- `JWT_PUBLIC_KEY` — optional, only if RS256 enabled

## Cloudflare Pages — Frontend Build Env Vars
Set at: Pages → syrabit → Settings → Environment Variables → Production

- `VITE_BACKEND_URL=https://api.syrabit.ai`
- `VITE_GA` — Google Analytics measurement ID
- `VITE_ADS_ADSENSE_*` — multiple AdSense slot IDs
- `VITE_ADS_ADPUSHUP_*`, `VITE_ADS_ADSTERRA_*`, `VITE_ADS_PROPELLERADS_*`

## GCP Service Accounts
- `syrabit-backend-sa@blissful-acumen-495019-t6.iam.gserviceaccount.com` — Cloud Run runtime
- `cloudflare-edge-invoker@blissful-acumen-495019-t6.iam.gserviceaccount.com` — Edge OIDC auth (✅ has roles/run.invoker)

## GitHub Secrets (for future CI/CD — no workflows yet)
- `GCP_SA_KEY` — JSON of a SA with `roles/run.developer` for auto-deploy
- `CLOUDFLARE_API_TOKEN` — Wrangler deploy (now also stored in Replit Secrets)
- `CLOUDFLARE_ACCOUNT_ID` — stored in Replit env vars as "syrabit" (alias), real ID: `d66e40eac539fff1db270fddf384a5ec`

## CF Pages deploy process (June 2026)
- Git-push builds ALWAYS fail (pyproject.toml in repo root triggers `pip install .` which fails on multi-package flat layout)
- All production deploys are **direct uploads** via wrangler CLI
- Command: `CLOUDFLARE_ACCOUNT_ID=d66e40eac539fff1db270fddf384a5ec apps/edge/node_modules/.bin/wrangler pages deploy apps/frontend/dist --project-name syrabitfrontend --branch main --commit-dirty=true`
- Requires Node.js ≥ 22 (use nodejs-22 module); Node 20 rejects wrangler 4.x
- The local `apps/frontend/dist` already has `VITE_BACKEND_URL=https://api.syrabit.ai` baked in via `.env.production`

## Key relationships
- `JWT_SECRET` must be IDENTICAL in GCP Secret Manager AND Cloudflare Worker
- `EDGE_SHARED_SECRET` must be IDENTICAL in GCP AND Cloudflare Worker
- `GOOGLE_SA_KEY` in Cloudflare Worker = key for `cloudflare-edge-invoker` SA (not the backend SA)
- `GOOGLE_SA_KEY` in Replit Secrets = key for `syrabit-backend-sa` SA (has roles/aiplatform.user; used by ingestion scripts and gemini_fallback.py Vertex AI path)
- Cloud Run project: `blissful-acumen-495019-t6`, region: `asia-south1`, service: `syrabit-backend`
