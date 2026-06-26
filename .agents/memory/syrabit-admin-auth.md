---
name: Syrabit admin auth pattern
description: Admin router auth standardization, cron separation, and AiUsageLog model
---

## Auth pattern (Phase 1 complete)

All admin routers now use router-level FastAPI dependencies — never inline `_validate_admin_session`:

```python
router = APIRouter(
    tags=["Admin Xyz"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)
```

Import from: `from app.api.v1.admin import require_admin_session, csrf_guard`

**Why:** Inline `await _validate_admin_session(request)` in each handler was error-prone (forgettable, bypassed on new routes, harder to audit). Router-level deps enforce auth on all routes automatically.

## Cron routes (Bearer token)

`/api/v1/admin/cron/*` lives in `admin_cron.py`, mounted separately in main.py.
Uses Bearer token (`TRANSLATE_CRON_SECRET`), NOT session cookies.
Never put cron routes in session-protected routers — they would 401 from CI jobs.

## Removed duplicate routes (admin_dashboard.py)

- `GET /admin/health` — canonical is `GET /health/deep`
- `GET /admin/cf-overview` — canonical is `GET /admin/analytics/cf-overview`

## AiUsageLog model

- Collection: `ai_usage_logs`
- File: `app/models/ai_usage_log.py`
- Fields: user_id, session_id, provider, model, lang, input_tokens, output_tokens, latency_ms, cost_usd, created_at
- 90-day TTL index on `created_at`
- Queried by `GET /admin/ai/usage` for last-24h token breakdown

## Analytics endpoints now real

- `GET /admin/analytics/daily` — real MongoDB aggregation, ?days=7|30|90
- `GET /admin/analytics/funnel` — registered → chatted → pro funnel
- `GET /admin/analytics/content-heatmap` — chunk counts per subject+medium
- `GET /admin/analytics/cf-overview` — calls CF GraphQL API if CF_ZONE_ID + CF_ANALYTICS_TOKEN set
- `GET /admin/analytics/revenue` — sums transactions.amount where status=captured (paise → INR)

## Config additions

- `CF_ZONE_ID` — Cloudflare zone ID for analytics API
- `CF_ANALYTICS_TOKEN` — CF token with Analytics:Read (separate from CF_API_TOKEN)

## AI providers endpoint

`GET /admin/ai/providers` returns all 3 providers: sarvam_ai, vertex_ai, cf_workers_ai.
Circuit breaker status included for sarvam only (no CB for Vertex or CF yet).
New endpoints: `/admin/ai/circuit-breakers`, `/admin/ai/usage`, `/admin/ai/routing-pools`

## RAG coverage

`GET /admin/rag/coverage` — chunk counts + doc counts per (subject_id, medium).
Use to identify which subjects/chapters have zero RAG coverage.
