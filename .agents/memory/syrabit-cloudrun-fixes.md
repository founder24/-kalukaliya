---
name: Syrabit Cloud Run deploy fixes
description: Critical fixes applied to resolve Sentry crash clusters in Cloud Run backend — motor, JWT, Atlas index conflicts, bcrypt
---

## motor must be pinned explicitly in requirements.in

beanie pulls motor as a transitive dep but does NOT pin a compatible version.
pymongo 4.17+ broke older motor versions (`_QUERY_OPTIONS` ImportError in motor/core.py).
**Fix:** Add `motor>=3.7.0` explicitly to requirements.in AND requirements.txt.
Bump `beanie>=2.2.0` to fix `'dict' object has no attribute 'model_config'` (beanie 2.1.0 bug with pydantic 2).

## JWT RuntimeError must not raise per-request in production

`_get_signing_key` / `_get_verification_key` in auth.py raised RuntimeError when:
- JWT_ALGORITHM=RS256 (set in Cloud Run) but no RSA keys provided
- Falls back to HS256 but JWT_SECRET is the default placeholder

**Why:** This filled Sentry with 290+ errors and made auth completely broken.
**Fix:** Log CRITICAL instead of raising — app stays up, operator sees the log.
Also: set `JWT_ALGORITHM=HS256` in cloudbuild.yaml `--set-env-vars` to avoid the RS256 fallback path entirely.

## Atlas index conflicts: never define indexes in both beanie Settings.indexes AND mongo.py create_indexes()

The `email_1` index was defined in User.Settings.indexes (beanie creates it as plain non-unique)
AND in mongo.py create_indexes() as `unique=True, sparse=True`. Atlas already had the unique+sparse version.
This caused IndexKeySpecsConflict (code 86) on every startup, crashing init_beanie().

**Fix:** Remove ALL index definitions from `User.Settings.indexes` (leave it empty).
Manage all indexes exclusively in `create_indexes()` in mongo.py.
Match existing Atlas index options exactly (e.g. `sparse=True` for email, non-sparse for razorpay).

## Beanie TTL conflict: plain model index vs. TTL index is a two-deploy time-bomb

**Pattern:** `ChatFeedback.Settings.indexes` had `[("timestamp", 1)]` (plain, no TTL).
`create_indexes()` also called `_ensure_ttl_index(db.chat_feedback, [("timestamp", ASCENDING)], 2592000)`.

**Sequence:**
1. Deploy 1: `init_beanie()` creates plain `timestamp_1` → `_ensure_ttl_index` sees code 85 (plain conflicts with requested TTL) → drops plain, creates TTL ✓
2. Deploy 2+: `init_beanie()` tries to create plain `timestamp_1` → **code 85** (TTL already exists) → exception escapes `init_beanie()` → `_client = None` → MongoDB not initialized → every collection query throws RuntimeError → library-bundle returns `{boards:[]}` silently.

**Why it was invisible:** The old `/health` endpoint returned 200 regardless (no `_client` check). The exception handler in library-bundle caught the RuntimeError and returned `{"boards":[]}` without logging. Basic health looked green, deep health revealed the truth.

**Fix:** Remove any plain `[("timestamp", 1)]` from `ChatFeedback.Settings.indexes`. The rule: if `create_indexes()` manages a TTL index on a field, that field must NOT appear in the Beanie model's `Settings.indexes` at all — not even as a plain index. Dual ownership always causes a second-deploy conflict.

**How to catch:** `/api/v1/health` now returns `503 + mongodb_initialized:false` when `_client` is None. `/api/v1/health/deep` shows the exact error. The smoke test section 5 checks this.

## AsyncMongoClient.close() is async in pymongo 4.x

Use `await _client.close()` not `_client.close()` — the latter creates an unawaited coroutine.

## bcrypt 4.x raises ValueError for passwords > 72 bytes

bcrypt 4.x raises ValueError instead of silently truncating.
**Fix:** In `_bcrypt_safe()`, SHA-256 hash any password > 72 bytes before passing to bcrypt.
SHA-256 output is 32 bytes, always within bcrypt's limit.
`verify_password` tries the SHA-256 path first, then the raw-truncated path for legacy passwords.

## Cloud Run deployment: allow-unauthenticated + secrets pattern

- Use `--allow-unauthenticated` (security via HMAC X-Edge-Signature, not Cloud Run IAM)
- Sensitive secrets set ONCE via `gcloud run services update --update-secrets` (preserved across deploys)
- Non-sensitive env vars set via `--set-env-vars=APP_ENV=production,JWT_ALGORITHM=HS256`
- DO NOT put `--update-secrets` in cloudbuild.yaml — secret names differ per environment and build will fail if secrets don't exist yet

## Edge proxy: normalize non-JSON Cloud Run errors to JSON 503

When Cloud Run is unreachable or not yet deployed, it returns HTML 404 (Google Frontend).
Edge proxy (api-proxy.ts) must check Content-Type and convert HTML 4xx/5xx to JSON 503.
Use the already-imported `getCorsHeaders()` — not a dynamic import.
