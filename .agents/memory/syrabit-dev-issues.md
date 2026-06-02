---
name: Syrabit dev environment issues
description: Root causes and fixes for library/auth/chat not working in Replit dev and production
---

# Syrabit Dev Environment Issues

**Why:** These were all silent failures introduced by production-only config that doesn't suit the Replit dev environment. Record to avoid re-diagnosing.

## Rule 1: CORS middleware must bypass in development
The unified middleware in `main.py` only skipped CORS for `APP_ENV == "test"`, not `"development"`. The Replit preview domain (`*.sisko.replit.dev`) is not in ALLOWED_ORIGINS. Fix: check `APP_ENV not in ("test", "development")`.

## Rule 2: Auth rate limiter is fail-CLOSED without Redis
`_check_rate_limit` in `auth.py` raises HTTP 503 when Redis unavailable. Fixed by returning early when `APP_ENV == "development"`. This must stay — production should remain fail-closed.

## Rule 3: library-bundle must return flat arrays
Frontend LibraryPage.jsx expects `{subjects, classes, streams, boards}` as flat arrays with parent IDs (`stream_id`, `class_id`, `board_id`). The backend was returning only nested `{boards: [{classes: [{streams: [{subjects:[]}]}]}]}`. Fixed in `public_content.py` to also emit flat lists alongside the nested structure.

## Rule 4: CookieConsent must NOT use `<Link>`
`AppShell` renders CookieConsent OUTSIDE `BrowserRouter`. Never use react-router `<Link>` there — use plain `<a>`.

## Rule 5: react/jsx-dev-runtime must be in resolve.dedupe
Vite config had `['react','react-dom','react/jsx-runtime']` but not `react/jsx-dev-runtime`, risking multiple React copies in dev. Added the fourth entry.

---

# Production Deployment Requirements (June 2026 audit)

## Rule 6: EDGE_SHARED_SECRET must be in BOTH Cloud Run AND Cloudflare Worker
The edge worker signs every proxied request with HMAC (`X-Edge-Signature`). The backend
verifies it when `TRUST_EDGE_AUTH=True`. Without it, all per-user auth (chat, conversations,
user profile) fails with 401/403 — but library/content pages still work (public paths).

**Required in Cloud Run env vars:** `EDGE_SHARED_SECRET=<same-value>`, `TRUST_EDGE_AUTH=True`
**Required in Cloudflare Worker secrets:**
  `npx wrangler secret put EDGE_SHARED_SECRET --env production`
**How to apply:** Any time auth appears to work at the edge (JWT accepted) but backend returns
401 for user-specific endpoints, check this pairing first.

## Rule 7: VITE_BACKEND_URL must be set in Cloudflare Pages build env vars
Frontend computes `API_BASE = ${VITE_BACKEND_URL}/api/v1`. If unset, it falls back to relative
`/api/v1` which hits the Pages CDN origin (404 for API calls). Must be `https://edge.syrabit.ai`.
Set in Cloudflare Pages dashboard → Settings → Environment Variables → Build variables.
Also set `VITE_WORKER_API_URL=https://edge.syrabit.ai` for content-specific calls.

## Rule 8: CORSMiddleware placement in FastAPI create_app()
Must be added LAST in `create_app()` (after all routers, after `@app.middleware("http")`) so it
becomes the outermost layer and handles OPTIONS preflight before other middleware runs.
The `allow_origin_regex` param covers Cloudflare Pages preview domains:
`r"^https://[a-z0-9-]+\.syrabitfrontend\.pages\.dev$"`

## Rule 11: resolve-subject endpoint — slug resolution order
`GET /content/resolve-subject/{board}/{classSlug}/{subjectSlug}` resolves in 4 sequential
DB queries: Board by slug → Classes by board_id (slugify name to match) → Streams by class_id
→ Subjects by stream_id list. Slug priority: `subj.slug` (stored) falls back to `_slugify(subj.name)`.
Board uses stored `slug` field; Class and Stream have no stored slug (computed on the fly).
Response includes breadcrumb fields `board_name/class_name/stream_name` so the page avoids a
second hierarchy fetch. Chapters are NOT included — fetched separately via `/content/chapters/{id}`.
Must be in edge worker `PUBLIC_PATHS` (`/api/v1/content/resolve-subject`) — subject pages are public.
**Why:** SubjectLandingPage calls `useResolveSubject` on direct-URL load before any auth resolves.

## Rule 9: Subject display fields are Optional on the Pydantic model
`slug`, `description`, `tags`, `icon`, `gradient`, `thumbnail_url`, `has_document`, `seo_stats`
were added as `Optional[...]` to the `Subject` model. Old MongoDB documents without these fields
return `None`/`False` defaults — the migration is safe. `public_content.py` library-bundle
endpoint computes `notes_count`/`notes_pct` from chapters and passes all display fields through.

## Rule 10: SSE streaming works through the edge worker without modification
`api-proxy.ts` detects `/stream` in the path and passes `response.body` directly (unbuffered).
CORS headers are added by `index.ts` after `proxyRequest()` returns — `applyCorsHeaders()`
mutates the Response headers on the stream-wrapping Response object. Both work together. ✅
The 30s HMAC timestamp tolerance is applied once at the start of the request, not per-chunk.
