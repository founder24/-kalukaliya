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

## Optional content-pipeline secrets (conditional step in deploy)

These 4 secrets are attached by a separate post-deploy step in both `deploy.yml` and `cloudbuild.yaml` that probes with `gcloud secrets describe` first. If not found, the deploy continues cleanly and logs a `gcloud secrets create` command. Once created, they are picked up automatically on the next deploy.

| SM secret name | Cloud Run env var(s) | Used by |
|---|---|---|
| `CF_ACCOUNT_ID` | `CF_ACCOUNT_ID` + `CLOUDFLARE_ACCOUNT_ID` | `cloudflare_client.py` (Workers AI) + `pipeline.py` (KV) — same value, two consumers |
| `CF_KV_API_TOKEN` | `CLOUDFLARE_KV_API_TOKEN` | `pipeline._push_cloudflare_kv()` — bulk write HTML to KV |
| `CF_KV_NAMESPACE_ID` | `CLOUDFLARE_KV_NAMESPACE_ID` | same |
| `GCS_CONTENT_BUCKET` | `GCS_CONTENT_BUCKET` | `gcs_store.py` — GCS chapter content bucket |

**Why conditional:** `--update-secrets` hard-fails the entire deploy if the referenced SM secret doesn't exist. Using a separate `gcloud run services update` step inside a bash loop keeps the core deploy safe.

**config.py type note:** `CLOUDFLARE_KV_*`, `INDEXNOW_KEY`, `TRANSLATE_CRON_SECRET` are `Optional[str] = None` (not `str = ""`). The `empty_strings_to_none` validator only applies to those Optional fields correctly.

**cloudflare_client.py note:** `account_id`, `api_token`, `base_url` are `@property` (lazy, read from `settings` at call time). Were eager `__init__` attributes that baked in `None` before SM env vars were injected at startup.
