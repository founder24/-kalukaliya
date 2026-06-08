---
name: CF Worker CORS gaps
description: Three early-exit paths in apps/edge/src/index.ts returned before applyCorsHeaders was called, causing browser TypeError("Failed to fetch")
---

## The Rule

All early-exit Response paths in the CF Worker MUST call `applyCorsHeaders(response.headers, request.headers.get('Origin') || '')` before returning.

**Why:** `applyCorsHeaders` is called at the END of the fetch handler (line ~327) after `proxyRequest` returns. Any `return` that fires before that point skips CORS header injection. Browsers enforce CORS strictly — a missing `Access-Control-Allow-Origin` header causes `fetch()` to throw `TypeError("Failed to fetch")` even if the response body is valid JSON. curl never sends `Origin` so it never notices.

**Three affected paths (fixed in bf2999c):**
1. Production-safety 503 (backend URL = localhost in prod) — was missing ACAO
2. JWT 401 (invalid/expired token) — was missing ACAO
3. Rate-limit 429 (anonymous 30 req/hr exceeded) — was missing ACAO

**How to apply:** Any time a new early-return is added to `apps/edge/src/index.ts` before the `proxyRequest` call, add:
```typescript
applyCorsHeaders(response.headers, request.headers.get('Origin') || '');
```
immediately before the `return`.

## Related

- Cold-start retry added to `apps/edge/src/routes/api-proxy.ts`: non-streaming 502/503 from Cloud Run (HTML body = infra error, not FastAPI) → wait 3s → retry once.
- Frontend `ChatPage.jsx`: CF Worker 503 uses `{"error":"...", "status":...}` format (no `detail` field). Added handler before the generic `throw` to show toast + auto-retry after 5s.
- Test script gap: `test-live.sh` curl calls never sent `Origin` header. Added to all 6 chat tests + new `/chat/stream` streaming test with Origin.

## pages.dev bare-domain CORS gap (fixed)

`https://syrabitfrontend.pages.dev` was blocked by both the edge worker and the GCP backend. Two-layer fix:
1. `cors.ts` `ALLOWED_ORIGINS` list: add `https://syrabitfrontend.pages.dev`. The existing `PAGES_PREVIEW_REGEX` only matched **subdomains** (`*.syrabitfrontend.pages.dev`), not the bare production Pages domain.
2. `api-proxy.ts` `proxyRequest()`: `headers.delete('Origin')` before forwarding to backend. Edge is the CORS authority; backend CSRF guard skips when Origin is absent, avoiding the duplicate origin check.

**Why:** The GCP backend has its own CSRF middleware that also checks origin. Rather than keeping both lists in sync, strip Origin at the edge proxy — the edge validates it and sets correct ACAO on the response. Backend never needs the client origin.

**How to apply:** When adding a new allowed origin, update `ALLOWED_ORIGINS` in `cors.ts` (and optionally `config.py` for direct-backend callers). Do NOT rely on the regex alone for canonical domain names.
