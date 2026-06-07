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
