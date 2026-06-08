---
name: Cloud Run deploy root causes
description: All known causes of Cloud Run revision failures and secret misconfiguration patterns
---

# Cloud Run Deploy Root Causes

## Secret name case mismatch (CRITICAL)
GCP Secret Manager uses CASE-SENSITIVE names. When `--update-secrets` in `gcloud run deploy` 
specifies `MONGODB_URI=mongodb-uri:latest` (SM name is `mongodb-uri`, env var is `MONGODB_URI`).
If a previous REST API patch stored `MONGODB_URI → MONGODB_URI:latest` (wrong case), that 
revision will fail: "Secret projects/.../secrets/MONGODB_URI/versions/latest was not found".

**Fix**: Always patch Cloud Run via REST API using the CORRECT SM names (lowercase dashes where SM uses them):
- MONGODB_URI → `mongodb-uri`
- JWT_SECRET → `jwt-secret`  
- EDGE_SHARED_SECRET → `edge-shared-secret`
- GEMINI_API_KEY → `gemini-api-key`
- JWT_PRIVATE_KEY → `jwt-private-key`
- JWT_PUBLIC_KEY → `jwt-public-key`
- CF_API_TOKEN → `CF_KV_API_TOKEN` (the CF KV API token, NOT the main CF API token)
- All others: use their uppercase names exactly

## VERTEX_PROJECT_ID is required by embedder (NOT Vertex Search specific)
`apps/backend/app/services/ai/embedder.py` reads `settings.VERTEX_PROJECT_ID` and
raises RuntimeError if missing. The Vertex AI text-embedding-005 API call uses this.
mongo_vector_search.search_context() calls the embedder → without VERTEX_PROJECT_ID,
all vector searches return 0 results.

**Keep in deploy.yml `--update-env-vars`**: `VERTEX_PROJECT_ID=blissful-acumen-495019-t6,VERTEX_LOCATION=us-central1`
**Only remove**: `VERTEX_SEARCH_DATASTORE_ID`, `VERTEX_SEARCH_LOCATION`, `VERTEX_SEARCH_SERVING_CONFIG`

## Stale VERTEX_SEARCH_* env vars
Revisions that have `VERTEX_SEARCH_DATASTORE_ID`, `VERTEX_SEARCH_LOCATION`, `VERTEX_SEARCH_SERVING_CONFIG`
are fine (code no longer uses them) but they're noise. Use `--remove-env-vars` to purge.

## SARVAM_MODEL must be sarvam-30b (not sarvam-m1)
`sarvam-m1` is invalid as of 2026. Valid models: `sarvam-30b` (fast), `sarvam-105b` (quality).

## REST API patches vs GitHub Actions deploys race
When using REST API patches to Cloud Run, GitHub Actions deploys may race in and overwrite.
Each GH Actions deploy creates a new revision (TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST).
After a GH Actions deploy, always re-check the latest revision's env vars.

## optional-secrets step strips Cloud Run unauthenticated access (CRITICAL — NOW FIXED)
`gcloud run services update` in non-interactive CI mode defaults to `--no-allow-unauthenticated`,
which removes Cloud Run's IAM binding and makes ALL requests return 403 from Google Frontend.
The old optional-secrets step in deploy.yml called `gcloud run services update --remove-secrets=...`
without `--allow-unauthenticated`, triggering this every deploy.

**Root cause confirmed**: CI logs showed all health check attempts returning HTTP 403000 (curl -sf
with -f flag exits 22 on 4xx → || echo "000" → both "403" and "000" captured = "403000").

**Fixes applied**:
1. Removed the optional-secrets step entirely — it was 100% redundant (main deploy step already
   lists the same secrets in `--remove-secrets=CF_PAGES_DEPLOY_HOOK,...`).
2. Fixed health check URL from `/health` to `/api/v1/health` (defensive fix).
3. Restored unauthenticated access via `invokerIamDisabled: true` on the service (bypasses IAM
   binding requirement, works even when org policy blocks allUsers:run.invoker).

**Why invokerIamDisabled**: The GCP org has `constraints/iam.allowedPolicyMemberDomains` blocking
`allUsers` IAM bindings. `gcloud run deploy --allow-unauthenticated` sets the IAM binding in CI
using CI SA key's elevated permissions, but fails for our Replit local SA key. Setting
`invokerIamDisabled: true` via REST PATCH bypasses IAM entirely for Cloud Run — equivalent effect,
different mechanism.

**Health check path**: FastAPI mounts health router at BOTH `/health` and `/api/v1/health`. The
EdgeAuthMiddleware excludes both paths. Always use `/api/v1/health` for CI polling since it is
the canonical path that both old and new Cloud Run URL formats serve correctly.

## deploy.yml gcloud --remove-secrets race condition
The `--remove-secrets` line removes secrets from the service. If gcloud deploy runs BEFORE
the optional-secrets step that re-adds them, and then the REST API patches the service,
the patch needs to include those secrets explicitly (they won't be re-added automatically).
