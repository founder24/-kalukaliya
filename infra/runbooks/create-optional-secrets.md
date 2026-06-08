# Create Optional GCP Secrets

Activates the 6 features that are silently disabled in production because their
GCP Secret Manager secrets don't yet exist under the names that `deploy.yml`
expects.

| Secret Manager name         | Env var in Cloud Run          | Feature                  |
|-----------------------------|-------------------------------|--------------------------|
| `upstash-redis-rest-url`    | `UPSTASH_REDIS_REST_URL`      | Response caching (Redis) |
| `upstash-redis-rest-token`  | `UPSTASH_REDIS_REST_TOKEN`    | Response caching (Redis) |
| `posthog-api-key`           | `POSTHOG_API_KEY`             | Analytics                |
| `indexnow-api-key`          | `INDEXNOW_API_KEY`            | SEO pinging (IndexNow)   |
| `indexnow-internal-secret`  | `INDEXNOW_INTERNAL_SECRET`    | SEO pinging (IndexNow)   |
| `VERTEX_SEARCH_DATASTORE_ID`| `VERTEX_SEARCH_DATASTORE_ID`  | Vertex AI Search / RAG   |

## Background

`deploy.yml` has an "Attach optional secrets" step with a `_check()` function
that looks up each secret by name. If found → it attaches it. If not found →
it removes any stale ref. The step succeeds either way, so deploys work even
when secrets are absent. But the features remain disabled until the secrets
exist with real values.

> **Name mismatch note:** An older `deploy.sh` stored some of these as uppercase
> names (`UPSTASH_REDIS_REST_URL`, `POSTHOG_API_KEY`, etc.). The GitHub Actions
> workflow uses lowercase-kebab names. The setup script below handles this
> automatically — if the uppercase variant exists in Secret Manager it copies
> the value across.

## Quick setup (Cloud Shell)

```bash
# Clone the repo (or open Cloud Shell from GitHub)
git clone https://github.com/founder24/-kalukaliya.git
cd -kalukaliya

bash infra/runbooks/create-optional-secrets.sh
```

The script is **idempotent** — safe to re-run. It skips any secret that already
exists and only creates the missing ones.

## Manual steps (if you prefer copy-paste)

Run in Cloud Shell (GCP project `blissful-acumen-495019-t6`).

### Where to find each value

**upstash-redis-rest-url**
> Upstash Console → your database → "REST API" tab → `UPSTASH_REDIS_REST_URL`
> Looks like: `https://xxxxxxxx.upstash.io`

**upstash-redis-rest-token**
> Same page → `UPSTASH_REDIS_REST_TOKEN`

**posthog-api-key**
> PostHog → Project Settings → "Project API Key" (starts with `phc_`)

**indexnow-api-key**
> The key you use as both the IndexNow API key and the filename served at
> `https://syrabit.ai/<key>.txt`. Can be any strong random alphanumeric string.

**indexnow-internal-secret**
> Internal HMAC secret used by the backend to validate IndexNow calls.
> Any strong random string (e.g. `openssl rand -hex 32`).

**VERTEX_SEARCH_DATASTORE_ID**
> GCP Console → Vertex AI Search → Data Stores → `syrabit-edu-datastore`
> → copy the **Datastore ID** shown at the top (format: `syrabit-edu-datastore_XXXXXXXXXX`).

### Commands

```bash
PROJECT=blissful-acumen-495019-t6
SA=syrabit-backend-sa@${PROJECT}.iam.gserviceaccount.com

# Helper: create secret + grant SA access
_make() {
  local name="$1"; local value="$2"
  printf '%s' "$value" | gcloud secrets create "$name" --data-file=- --project="$PROJECT"
  gcloud secrets add-iam-policy-binding "$name" \
    --member="serviceAccount:${SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$PROJECT" --quiet
}

_make upstash-redis-rest-url      "PASTE_UPSTASH_REST_URL_HERE"
_make upstash-redis-rest-token    "PASTE_UPSTASH_REST_TOKEN_HERE"
_make posthog-api-key             "PASTE_POSTHOG_KEY_HERE"
_make indexnow-api-key            "PASTE_INDEXNOW_KEY_HERE"
_make indexnow-internal-secret    "PASTE_INDEXNOW_SECRET_HERE"
_make VERTEX_SEARCH_DATASTORE_ID  "PASTE_DATASTORE_ID_HERE"
```

## Verification

After running the script, trigger a GitHub Actions deploy and check the
**"Attach optional secrets (guarded)"** step log. You should see:

```
Attaching optional secrets: UPSTASH_REDIS_REST_URL=upstash-redis-rest-url:latest,...
```

No `⚠ ... not found` lines means all 6 secrets were found and attached.

## Re-running after a value change

If a secret value changes (e.g. Redis database rotated), add a new version:

```bash
printf 'NEW_VALUE' | gcloud secrets versions add SECRET_NAME \
  --data-file=- \
  --project=blissful-acumen-495019-t6
```

Cloud Run always uses `:latest`, so the new version takes effect on the next
deploy without any workflow changes.
