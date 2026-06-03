---
name: Syrabit chat+auth pipeline bugs
description: Bugs fixed in the chat pipeline and auth/analytics endpoints during the June 2026 audit
---

## conversation_id vs session_id in ChatRequest
The frontend always sends `conversation_id` in the chat payload but the backend `ChatRequest` model had `session_id`. Pydantic drops unknown fields silently (extra="ignore"), so `session_id` was always `None` — multi-turn history broken. Fixed with a `@model_validator(mode="after")` in `ChatRequest` that coalesces `conversation_id` into `session_id` when `session_id` is absent. Both fields are now validated by the same `@field_validator`.

**Why:** Frontend and backend evolved independently and the field name drifted. Keep both fields or fix the frontend to send `session_id` for a cleaner solution.

## LogoutRequest.refresh_token null crash
`LogoutRequest` had `refresh_token: str = Field(min_length=1)`. Frontend `getRefreshToken()` returns `null` on a cold start (in-memory token not yet hydrated). This caused a 422 Unprocessable Entity on logout AND the logout handler's `jwt.decode(body.refresh_token, ...)` would have raised `TypeError` (not caught by `except InvalidTokenError`). Fixed: field is now `Optional[str] = None`, and the revocation block is wrapped in `if body.refresh_token:`.

## Missing analytics endpoints (constant 404 storm)
`apps/backend/app/api/v1/analytics.py` only had `/session-ping` and `/session-end`. Frontend `usePageTracking` fires `POST /api/v1/analytics/page-view` on every SPA route change, visibility resume, and boost interval. Also missing: `/review-prompt-event` and `/ad-impression` (mirrored from analytics.jsx). Added all four stub endpoints. Also added a `/api/analytics` legacy prefix mount in main.py (ad/review mirrors use `${VITE_BACKEND_URL}/api/analytics/...`).

## Missing config/trustpilot endpoints
Frontend `TrustpilotReviewsSection` and `ReviewPrompt` fetch `GET /api/v1/config/trustpilot` and `/api/v1/config/trustpilot/aggregate` on every page load. No config router existed. Created `apps/backend/app/api/v1/config.py` with both endpoints returning `null` gracefully when env vars are unset, registered at `/api/v1/config`.

## LLM knowledge fallback — needs GEMINI_API_KEY
When Vertex Search has no credentials, `check_topic_match` returns `None` → `context_chunks = []` → `build_system_prompt` uses LLM-knowledge-only prompt. This is the intended fallback. BUT the LLM call itself (vertex_client) also needs `GEMINI_API_KEY` or `GOOGLE_APPLICATION_CREDENTIALS`. Without either, chat always errors with "Service temporarily unavailable." The architecture is correct; credentials are required.
