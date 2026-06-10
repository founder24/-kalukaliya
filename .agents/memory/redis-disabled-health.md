---
name: Redis disabled health check
description: Upstash Redis is optional; missing credentials should surface as "disabled" not "unhealthy" in /health/deep
---

## The Rule
When `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` are absent, `redis_ping()` must return `{"status": "disabled"}` — not let `get_redis()` throw `RuntimeError("Redis not initialized...")` which gets caught as `"unhealthy"`.

The core-services check in `deep_health_check()` must treat `"disabled"` as acceptable alongside `"healthy"`:
```python
ACCEPTABLE = {"healthy", "disabled"}
core_healthy = all(checks[svc].get("status") in ACCEPTABLE for svc in CORE_SERVICES)
```

The smoke test (`scripts/fullstack-smoke-test.sh`) must also accept `"disabled"` for Redis.

**Why:** Redis (Upstash) is an optional performance layer for rate limiting and caching. Cloud Run doesn't have the Upstash secrets bound (they're in the optional Step 5 of cloudbuild.yaml and not in Secret Manager). Reporting this as "unhealthy" caused the backend to return HTTP 503 from `/health/deep`, which failed the smoke test and caused the CF edge to report `backend_reachable=false`.

**How to apply:** Any new optional service added to the health check should follow the same pattern — check credentials first, return `"disabled"` if absent, only check connectivity if credentials are present.
