---
name: CF_WORKER_AI_TOKEN vs CF_API_TOKEN
description: Cloud Run uses CF_WORKER_AI_TOKEN env var name; cloudflare_client must resolve both names; both pydantic fields required
---

## The Rule
`cloudflare_client.py` `api_token` property must return `settings.CF_WORKER_AI_TOKEN or settings.CF_API_TOKEN`.
Both fields must exist in pydantic `Settings` (config.py) for env vars to be picked up.

**Why:** Cloud Run was set up with `CF_WORKER_AI_TOKEN` as the env var name. Pydantic silently ignores env vars with no matching field — `CF_API_TOKEN` stayed `None` → "Cloudflare Workers AI not configured" error on every call. The token itself was valid (tested: 200 OK from Replit).

**How to apply:** Any new deployment that sets a CF token must use `CF_WORKER_AI_TOKEN` as the env var name. The `CF_API_TOKEN` field is kept as legacy fallback for any local `.env` configs.

## Fixed files
- `apps/backend/app/config.py` — added `CF_WORKER_AI_TOKEN: Optional[str] = None` field
- `apps/backend/app/services/ai/cloudflare_client.py` — `api_token` property: `return settings.CF_WORKER_AI_TOKEN or settings.CF_API_TOKEN`
