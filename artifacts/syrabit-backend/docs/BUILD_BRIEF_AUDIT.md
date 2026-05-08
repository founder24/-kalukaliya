# Syrabit.ai — Build-from-Scratch Brief: Audit Report

**Audit date:** 2026-04-22
**Source brief:** Task #668 (Syrabit build-from-scratch brief)
**Mode:** Audit only — no code rewrites. Genuine gaps filed as new tasks.

## Executive summary

The brief describes the platform Syrabit.ai needs to be. The platform **already exists in production** and matches the brief on every major axis — three deployables (frontend, backend, edge proxy), full route surface, RAG chat, payments, admin dashboard, SEO engine, observability. Of the 12 build steps, 10 are fully implemented. **Two narrow gaps** are worth fixing and have been filed as follow-up tasks.

This document is the canonical mapping between brief and reality. Use it as the architecture reference instead of treating the brief as build instructions.

## Step-by-step audit

| # | Step | Status | Evidence |
|---|------|--------|----------|
| 1 | Scaffold three deployables | ✅ Done | `artifacts/syrabit/` (Vite+React 18+Tailwind+Radix+TanStack+RR7), `artifacts/syrabit-backend/` (FastAPI), `workers/edge-proxy/` (Wrangler TS) |
| 2 | Data layer (PG + Mongo + D1 + KV) | ✅ Done | `deps.py` `_init_pg_pool`, `server.py` index ensures (chapters, subjects, …), `d1_sync.py`, `wrangler.toml` `RATE_LIMIT` + `BOT_HTML_CACHE` + `CONTENT_DB` bindings |
| 3 | Auth (signup/login/Google/JWT/Turnstile/reset) | ⚠️ Gap | `routes/auth.py` covers all endpoints, `auth_deps.py` separate `ADMIN_JWT_SECRET`, Google OAuth wired, SES reset email working (Task #556 retired the previous transactional vendor; SES is the sole path). **Gap:** Turnstile token NOT verified on `/auth/signup` and `/auth/login` (it is on chat). Filed as task. |
| 4 | Library / content APIs + frontend + prerender | ✅ Done | `routes/content.py`, `LibraryPage`, `BrowserPage`, `SubjectPage`, `ChapterPage`, `scripts/prerender-all.mjs` |
| 5 | AI/RAG chat pipeline | ✅ Done | `routes/ai_chat.py`, `qa_engine.py`, `vertex_services.py` (just restored — Task #663), CF AI Gateway routing in `config.py`, intent classify in `prompts.py`, MongoDB-first context, web fallback gated, citations, credit accounting in `auth_deps.py` |
| 6 | Study tools (notebook, flashcards, edu_study) | ✅ Done | `NotebookPage`, `FlashcardsPage`, `routes/edu_study.py` (`/quiz/generate`, `/notes`, `/flashcards/review`, `/stt`), PYQ replicas |
| 7 | SEO content engine | ✅ Done | `seo_engine.py` (definition/notes/MCQs prompt variants), `seo_writes.py` upserts to `seo_pages`, `seo_fanout.py` parallel signals, `bot_discovery.py` IndexNow with `indexnow_push_log`, `google_indexing_client.py`, `bing_submit_client.py` |
| 8 | Payments (Razorpay) | ✅ Done | `routes/admin_monetization.py` `/payments/create-order` + `/webhooks/razorpay` with HMAC verify + plan upgrade + credit reset + idempotency, `PaymentSuccessPage`, `PaymentCancelPage` |
| 9 | Admin dashboard | ✅ Done | `AdminPage.jsx` + `AdminGuard.jsx`; `admin_settings.py`, `admin_content.py`, `admin_advanced.py`, `admin_monetization.py`, `admin_notifications.py`, `admin_auth_users.py`; analytics modules under `components/admin/analytics/`; SEO pipeline under `components/admin/seo-manager/` |
| 10 | Edge proxy | ⚠️ Minor gap | `workers/edge-proxy/src/index.ts` covers API↔Pages routing with `BACKEND_ORIGIN_SECRET`, IP+bot rate limits via KV, verified Googlebot/Bingbot CIDR checks, D1 mirror via `D1_SYNC_SECRET`, `BOT_HTML_CACHE`, dynamic sitemap from D1. **Gap:** root `/sitemap.xml` is currently proxied to Pages instead of being aliased to `/api/seo/sitemap-index.xml`. Filed as task. |
| 11 | Observability + comms | ✅ Done | `ga4_client.py` server-side proxy, AWS SES transactional emails (Task #556 — sole path; previous vendor retired), `StatusPage.jsx` + `/sarvam/status`, `_JSONFormatter` structured logging in `server.py` |
| 12 | Deploy | ✅ Done | Root `package.json` `deploy:pages` (wrangler pages deploy `artifacts/syrabit/dist`), backend on Railway, Worker via `wrangler deploy` |

## Genuine gaps (filed as tasks)

1. **Turnstile on `/auth/signup` and `/auth/login`** — brief explicitly requires Turnstile on auth surfaces; chat is the only path that currently checks the token. Low effort, real security gap.
2. **Root `/sitemap.xml` alias to `/api/seo/sitemap-index.xml`** — current edge code proxies the root path to Pages, which means crawlers hitting `https://syrabit.ai/sitemap.xml` may get a 404 or stale static file instead of the live D1-driven index.

## Operational notes (not gaps, but worth knowing)

- `wrangler.toml` ships with a default `BACKEND_URL`. Always override per environment via `wrangler secret`/vars; a bare `wrangler deploy` will overwrite the production binding.
- `KV_QUOTA` env in the Worker drives the `KV_WARNING_PCT` alerts; if unset, defaults to free-tier limits. Set explicitly in production.
- `supa_insert_activity_log` is not called on every admin write (e.g. some chapter deletes). Consider auditing `routes/admin_content.py` if a complete admin audit trail becomes a requirement.
- SES credential absence (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`) makes the user-facing transactional path raise `EmailSendFailed` (loud per V4 §12, Task #556) and the admin/digest path return `False` — fine in dev, monitor in prod.

## Conclusion

Treat Task #668's brief as the canonical spec. The codebase implements it. The two filed follow-ups are the only real divergences worth closing.
