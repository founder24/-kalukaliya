---
name: Syrabit chat+auth pipeline bugs and audit fixes
description: Fixed bugs (analytics 404s, conversation/session mismatch, logout crash) + Layer 0–6 audit fixes + frontend audit fixes
---

## Fixed bugs (prior sessions)
- Analytics 404s: endpoint path mismatch resolved
- conversation_id/session_id mismatch: aligned across chat service and API
- logout null-token crash: guard added before token revocation call

## Audit fixes (2026-06-07 session, layers 0–6)

### Layer 0 (Critical)
- C-1: test-live.sh hardcoded password → fail-fast env var check
- C-2: signup DuplicateKeyError → 400
- C-3: /health strips startup error details (only error_count exposed)

### Layer 1 (High)
- H-1: admin logout returns server_revocation bool
- H-7: expanded _PLACEHOLDER_SECRETS set in auth.py
- H-8: require_admin_session + csrf_guard FastAPI Depends on all 4 admin routers
- H-9: test-live.sh EXIT trap for temp jar files

### Layer 2 (Medium)
- H-3: seo_bulk_generate N+1 → single $in query
- M-1: admin list endpoints (boards/classes/streams/subjects/chapters) get skip+limit params
- M-2: asyncio.Lock on sitemap cache + async _set_cached_sitemap + await all 6 call sites
- M-6: sanitize_user_input max_length 4000→2000 (matches ChatRequest validator)
- M-12: rollback_migration(down_fn) added to runner.py
- M-13: migration claim-first: insert "pending" before up_fn, update to "applied"/"failed" after
- M-8, M-11: already had retry/backoff — no change needed

### Layer 3 (Frontend)
- H-6: DOMPurify.sanitize() wraps dangerouslySetInnerHTML in PersonalizedCmsPage + PYQReplicaPage
- M-9: studyApi.js now uses WORKER_API (edge proxy) instead of API_BASE
- L-1: _slugify gains max_length=200 input cap (public_content.py)
- L-14: AuthGuard logo alt="" → alt="Syrabit.ai logo"

### Layer 4 (Edge Worker _worker.js)
- H-4: addSecurityHeaders() helper (X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy) on sitemapProxy + botRender responses
- M-5: botRender returns 503+Retry-After on failure instead of falling through to SPA shell

### Layer 5 (SEO/Content)
- M-4: robots.txt (public/ + seo.py ROBOTS_TXT constant) — 7 stale sitemaps removed, actual routes listed
- M-15: /llms-full.txt endpoint added to seo.py (proxied by CF worker)
- L-8: sitemap-index.xml and sitemap-static.xml lastmod now computed at request time (was hardcoded 2026-06-07)
- M-19: confirmed correct — Vertex AI already non-core in health.py CORE_SERVICES

### Scripts
- L-17: compile-deps.sh EXIT trap for TMP_REQ/TMP_OUT/TMP cleanup

## Key patterns to know
- Migration runner: now claim-first (pending→applied/failed); down_fn rollback via rollback_migration()
- Beanie pagination: .find(query).skip(skip).limit(limit).to_list() is valid chaining
- seo.py _set_cached_sitemap is async — must await all 6 call sites
- admin_translate.py deliberately excluded from require_admin_session (has its own Bearer token auth)

## Edge JWT_SECRET not provisioned — crashes auth for all logged-in users

**Symptom:** Every request with a Bearer token returns 401 from the edge, even with a valid token issued by the backend. Anonymous requests work fine.

**Root cause:** `wrangler deploy` (in CI) only deploys code — it does NOT push secrets. If `JWT_SECRET` was never set via `wrangler secret put JWT_SECRET --env production`, `env.JWT_SECRET` is `undefined` in the CF worker. Then `secret.trim()` in jwt.ts throws a TypeError caught as `{ valid: false, error: "Cannot read properties of undefined..." }`. That error !== the pass-through string, so the edge returns 401.

**Fix (code):** Added guard in `verifyJWT()` — if neither `jwtSecret` nor `jwtPublicKey` is set, return the pass-through error string so the backend handles auth. See `apps/edge/src/middleware/jwt.ts`.

**Fix (infra — DONE 2026-06-07):** CI deploy-edge job in `.github/workflows/deploy.yml` now authenticates to GCP (using `secrets.GCP_SA_KEY`) then pipes each GCP secret value into `wrangler secret put --env production` via stdin. Runs after every edge deploy — JWT_SECRET and EDGE_SHARED_SECRET stay in sync permanently. The first successful run was commit 371c06ec152f.

## Frontend audit fixes (2026-06-07 session)

### Bug: Missing /content/chapters/{subject_id} endpoint (SubjectLandingPage broken)
- public_content.py had a comment "use GET /content/chapters/{subject_id}" but endpoint never existed → 404
- Added @router.get("/chapters/{subject_id}") in public_content.py
- Returns chapters sorted by chapter_number; uses PydanticObjectId(subject_id) for Beanie query
- Chapter.subject_id is FlexId = Union[PydanticObjectId, str] — passing PydanticObjectId works
- Verified: 14 chapters for physics subject_id 6a19e0d74d8e6ddb2deb7d04

### Bug: CMS library format mismatch (useCmsLibrary silently returned [])
- Backend /content/cms-library returns {"items": [...], "total": N} (pagination object)
- Frontend fetchCmsLibrary used Array.isArray(d) → returned [] for the object, dropping all posts
- Fixed in useContent.jsx: check d?.items array first, fall back to legacy array shape

## Remaining known 404s (silent failures, no fix needed)
- /edu/allowlist → 404: BrowserPage catches with .catch(()=>{}) — browser still works
- /seo/topics/{board}/{class}/{subject} → 404: only in prefetchSubjectData prefetch, not rendered
- Chapter page shows "Failed to load chapter" in screenshot tool — screenshot tool bot-protection
  artifact; chapter-by-slug returns HTTP 200 correctly from real browsers (curl confirmed)
