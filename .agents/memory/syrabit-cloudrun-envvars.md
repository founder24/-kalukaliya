---
name: Syrabit Cloud Run env var strategy
description: Which env vars must be set manually in Cloud Run, and why cloudbuild uses --update-env-vars
---

## Required env vars — must ALL be set in Cloud Run simultaneously

| Var | Source | Why critical |
|---|---|---|
| `VERTEX_PROJECT_ID` | `cloudbuild.yaml --update-env-vars` | Vertex AI credentials resolve; circuit breaker stays CLOSED |
| `VERTEX_LOCATION` | `cloudbuild.yaml --update-env-vars` | Defaults to us-central1 but should be explicit |
| `JWT_SECRET` | Set once via GCP API or gcloud; **NOT** in cloudbuild.yaml (sensitive) | If missing, app uses placeholder → `startup_errors` → `/health` returns 503 → `backend_reachable: false` in edge |
| `MONGODB_URI` | Secret Manager | |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | Secret Manager | |
| `SENTRY_DSN` | Secret Manager | |
| `ADMIN_JWT_SECRET` | Secret Manager | |
| `EDGE_SHARED_SECRET` | Secret Manager | |
| `APP_ENV` | `cloudbuild.yaml --update-env-vars` | |
| `JWT_ALGORITHM` | `cloudbuild.yaml --update-env-vars` | |

## Why --update-env-vars (not --set-env-vars)

`gcloud run deploy --set-env-vars` **replaces all plain env vars** on every deploy. This wiped `VERTEX_PROJECT_ID` and `JWT_SECRET` that were set via GCP API. Changed to `--update-env-vars` in cloudbuild.yaml so only the listed vars are touched; all others (JWT_SECRET, Secret Manager refs) are preserved.

**Why:** JWT_SECRET is sensitive — not safe to embed in git-tracked cloudbuild.yaml. Set it once via:
```
gcloud run services update syrabit-backend --region=asia-south1 \
  --update-env-vars=JWT_SECRET=<value>
```

## `/health` returns 503 chain

`JWT_SECRET` missing → default placeholder → `startup_errors` non-empty → `/health` returns 503 → `fetchBackendHealth` in Worker returns false → `backend_reachable: false` in edge response → jq `false // empty` = empty → test WARN "missing backend_reachable field".

## Cloudflare Worker name

The deployed Worker is `syrabitworker-prod` (not `syrabit-edge`). `BACKEND_URL` is already a binding via `wrangler.toml [env.production.vars]` — cannot override with a secret (error 10053: binding name already in use).

## Remaining open items (require user credentials)

- Redis: needs `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN` in Cloud Run → `/health/deep` unhealthy on redis
- Webhook: needs `RAZORPAY_WEBHOOK_SECRET` in Cloud Run → currently returns 501 gracefully
- Assamese AI: needs `SARVAM_API_KEY` for Sarvam model
