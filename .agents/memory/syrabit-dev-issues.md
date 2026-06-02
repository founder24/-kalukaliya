---
name: Syrabit dev environment issues
description: Root causes and fixes for library/auth/chat not working in Replit dev
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
