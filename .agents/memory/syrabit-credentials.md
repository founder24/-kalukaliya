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
- `CF_PAGES_DEPLOY_HOOK` — auto-rebuild Cloudflare Pages after content changes
- `ADMIN_EMAIL` / `ADMIN_PASSWORD` — auto-create admin account on startup

## Cloudflare Worker Secrets (syrabitworker-prod)
Set via: Dashboard → Workers & Pages → syrabitworker-prod → Settings → Variables → Secrets
OR: `npx wrangler secret put NAME --env production`

- `JWT_SECRET` — must exactly match GCP value
- `EDGE_SHARED_SECRET` — must exactly match GCP value
- `GOOGLE_SA_KEY` — full JSON of `cloudflare-edge-invoker` SA (✅ set June 2026)
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
- `CLOUDFLARE_API_TOKEN` — Wrangler deploy
- `CLOUDFLARE_ACCOUNT_ID`

## Key relationships
- `JWT_SECRET` must be IDENTICAL in GCP Secret Manager AND Cloudflare Worker
- `EDGE_SHARED_SECRET` must be IDENTICAL in GCP AND Cloudflare Worker
- `GOOGLE_SA_KEY` in Cloudflare = key for `cloudflare-edge-invoker` SA (not the backend SA)
- Cloud Run project: `blissful-acumen-495019-t6`, region: `asia-south1`, service: `syrabit-backend`
