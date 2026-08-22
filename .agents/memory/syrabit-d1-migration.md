---
name: D1 migration outcome
description: Final state of MongoDB→D1 migration; schema notes, cutover details, remaining Cloud Run routes.
updated: 2026-08-17
---

# D1 Migration Outcome

**Status**: Complete and live in production (Aug 2026)

## Key facts
- D1 database: `ff8e76ec-02c5-45f3-92ea-4d67d7d2a510` (syrabit-db)
- Migrations applied: 0001 (drop FK on chapters/chunks), 0002 (allow NULL stream_id on subjects)
- RAZORPAY_WEBHOOK_SECRET must be set on API Worker (wrangler secret put --env production)

## D1-backed routes (all student-facing + payment flows)
- auth, chat, content (boards/classes/streams/subjects/chapters), users/profile
- subscription (plans, status, create-order, cancel)
- payments (create-order, verify, credit-topup, credit-topup/verify, history)
- webhooks/razorpay (HMAC-SHA256 verified, D1 idempotency)

## Still on Cloud Run fallback (Phase 7)
- /api/v1/admin/** — 25+ staff sub-routers (publish, RAG, PYQ, translate…)
- /api/v1/seed/** — bulk-seed cron (internal)
- /api/v1/seo/** — dynamic sitemap generation
- /api/v1/analytics/**, /api/v1/config/**, /api/v1/indexnow/**, /api/v1/changelog/**
- All other paths → 404 (no catch-all Cloud Run proxy)

## Schema divergence (tech debt)
- `subjects.stream_id`: live D1 allows NULL; schema.ts still has NOT NULL — do NOT run drizzle-kit push without updating schema.ts first
- `chapters.subject_id`, `chunks.document_id`: live D1 has no FK

## Service Binding cutover
- Edge Worker (syrabitworker-prod) → Service Binding → API Worker (syrabit-api-prod) → D1
- API_WORKER_LIVE=true activates the binding
- Cloud Run min-instances should be set to 0 after admin routes are ported (Phase 7)

## Payment idempotency pattern
- D1 payments_pending replaces Redis SET NX for order metadata
- D1 payments table INSERT OR IGNORE replaces Redis dedup lock
- Razorpay API fetch is the last-resort fallback when D1 metadata is missing
