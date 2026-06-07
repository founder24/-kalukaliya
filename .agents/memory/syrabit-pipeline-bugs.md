---
name: Syrabit chat+auth pipeline bugs and audit fixes
description: Fixed bugs (analytics 404s, conversation/session mismatch, logout crash) + Layer 0–6 audit fixes
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
