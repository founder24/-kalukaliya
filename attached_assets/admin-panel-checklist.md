# Syrabit Admin Panel — End-to-End Checklist

> **Rule:** Save changes the data. Publish changes delivery. Reindex changes retrieval.

| Area | Checkpoint | Status | Risk | File | Fix Priority |
|------|-----------|--------|------|------|-------------|
| **1. Auth & Access** | Admin login uses a separate auth flow from user login | Exists | Low | `apps/backend/app/api/v1/admin.py`, `apps/frontend/src/components/admin/AdminLoginPage.jsx` | — |
| **1. Auth & Access** | Admin session is cookie-based (HttpOnly) for browser routes | Exists | Low | `apps/backend/app/api/v1/admin.py` | — |
| **1. Auth & Access** | Admin `verify` runs on page load and on session refresh | Exists | Low | `GET /api/v1/admin/verify` → `admin.py` | — |
| **1. Auth & Access** | Logout clears session and revokes token server-side | Exists | Low | `POST /api/v1/admin/logout` → `admin.py` | — |
| **1. Auth & Access** | Bearer fallback limited to machine-triggered (cron) routes only | Exists | Medium | `apps/backend/app/api/v1/admin_cron.py` | — |
| **1. Auth & Access** | CSRF protection on all mutating admin routes | Exists | Medium | `admin.py` (CSRF middleware) | — |
| **2. Admin Shell & Navigation** | Admin dashboard layout loads correctly | Exists | Low | `apps/frontend/src/components/admin/AdminPage.jsx` | — |
| **2. Admin Shell & Navigation** | Sidebar sections map to correct admin modules | Exists | Low | `AdminPage.jsx` (Main / Audience / Operations / System groups) | — |
| **2. Admin Shell & Navigation** | Lazy-loaded panels do not break route state | Needs Verification | Medium | `AdminContentHub.jsx`, `AdminAiHub.jsx`, `AdminRevenueHub.jsx` | P2 |
| **2. Admin Shell & Navigation** | Legacy route redirects still work | Needs Verification | Low | `apps/frontend/src/App.jsx` or router config | P3 |
| **2. Admin Shell & Navigation** | Global 401 handling logs out and redirects cleanly | Exists | Medium | `apps/frontend/src/` (axios interceptor) | — |
| **2. Admin Shell & Navigation** | Shared admin context stays in sync across panels | Needs Verification | Medium | `AdminPage.jsx` (context provider) | P2 |
| **3. Content Editor** | Student content and RAG content separated in UI | Exists | Low | `apps/frontend/src/components/admin/AdminContentHub.jsx` (Reader / RAG tabs) | — |
| **3. Content Editor** | Content editor saves only display content fields | Exists | Low | `apps/backend/app/api/v1/admin_content.py` | — |
| **3. Content Editor** | RAG editor saves only retrieval text fields (`rag_text_*`) | Exists | Medium | `admin_content.py`, `admin_rag.py` | — |
| **3. Content Editor** | Save buttons are separate or clearly scoped | Exists | Low | `AdminContentHub.jsx` | — |
| **3. Content Editor** | Editor explains that content save ≠ publish | Needs Verification | Medium | `AdminContentHub.jsx` (UI copy) | P2 |
| **3. Content Editor** | Editor explains that RAG save ≠ retrieval freshness | Needs Verification | High | `AdminContentHub.jsx` (UI copy) | P1 |
| **4. RAG Pipeline** | RAG save updates `rag_text_*` fields | Exists | Low | `apps/backend/app/api/v1/admin_content.py` | — |
| **4. RAG Pipeline** | RAG save stamps `rag_updated_at` | Needs Verification | High | `admin_content.py` (model field check) | P1 |
| **4. RAG Pipeline** | Reindex runs after RAG save or is strongly prompted | Needs Verification | High | `admin_rag.py`, `AdminContentHub.jsx` | P1 |
| **4. RAG Pipeline** | Reindex stamps `rag_indexed_at` on success | Needs Verification | High | `apps/backend/app/api/v1/admin_rag.py` | P1 |
| **4. RAG Pipeline** | Vectorize metadata indexes exist for required filter fields | Needs Verification | High | `apps/backend/app/services/` (Cloudflare Vectorize client) | P1 |
| **4. RAG Pipeline** | Chat retrieval uses current indexed chunks, not stale content | Needs Verification | High | `apps/backend/app/api/v1/chat.py` + RAG retriever | P1 |
| **5. Publish Pipeline** | Publish updates delivery artifacts, not retrieval text | Exists | Medium | `POST /api/v1/admin/content/chapters/{id}/publish` → `admin_content.py` | — |
| **5. Publish Pipeline** | Publish job has step-by-step tracking | Needs Verification | Medium | `admin_content.py` / `admin_seo.py` | P2 |
| **5. Publish Pipeline** | Publish status is visible in the admin UI | Needs Verification | Medium | `AdminContentHub.jsx`, `AdminSeoManager.jsx` | P2 |
| **5. Publish Pipeline** | Failed publish steps can be retried | Needs Verification | Medium | `admin_content.py` | P2 |
| **5. Publish Pipeline** | Publish and reindex presented as clearly different operations | Needs Verification | High | `AdminContentHub.jsx` (UI copy + button labels) | P1 |
| **5. Publish Pipeline** | Public-facing content only shows published chapters | Exists | Low | `apps/backend/app/api/v1/public_content.py` | — |
| **6. Chapter State & Sync** | Chapter cards show `content_saved_at` | Needs Verification | Medium | `AdminContentHub.jsx` (chapter card component) | P2 |
| **6. Chapter State & Sync** | Chapter cards show `published_at` | Needs Verification | Medium | `AdminContentHub.jsx` | P2 |
| **6. Chapter State & Sync** | Chapter cards show `rag_updated_at` | Needs Verification | High | `AdminContentHub.jsx` | P1 |
| **6. Chapter State & Sync** | Chapter cards show `rag_indexed_at` | Needs Verification | High | `AdminContentHub.jsx` | P1 |
| **6. Chapter State & Sync** | Badges show "RAG current," "RAG stale," or "Not indexed" | Missing | High | `AdminContentHub.jsx` (badge component) | P1 |
| **6. Chapter State & Sync** | Badges show unpublished edits when content is newer than publish | Missing | High | `AdminContentHub.jsx` (badge component) | P1 |
| **7. Safety & Concurrency** | Versioning prevents silent overwrite | Needs Verification | High | `admin_content.py` (ETag / version field check) | P1 |
| **7. Safety & Concurrency** | Conflicting edits return 409 | Needs Verification | High | `admin_content.py` | P1 |
| **7. Safety & Concurrency** | Force overwrite is explicit and intentional | Needs Verification | High | `admin_content.py` | P1 |
| **7. Safety & Concurrency** | Audit logs exist for content, RAG, publish, and bulk actions | Exists | Low | `apps/backend/app/api/v1/admin_analytics.py`, `GET /admin/activity-log` | — |
| **7. Safety & Concurrency** | Admin actions are traceable by user and timestamp | Exists | Low | `admin_analytics.py` (AiUsageLog, activity_log) | — |
| **7. Safety & Concurrency** | Concurrent edits do not silently corrupt chapter state | Needs Verification | High | `admin_content.py` | P1 |
| **8. Bulk Operations** | Bulk reindex exists and respects limits | Exists | Medium | `POST /api/v1/admin/rag/reindex/bulk` → `admin_rag.py` | — |
| **8. Bulk Operations** | Bulk publish exists and respects limits | Needs Verification | Medium | `admin_content.py` | P2 |
| **8. Bulk Operations** | Bulk actions show queued counts or job IDs | Needs Verification | Medium | `AdminContentHub.jsx`, `apps/backend/app/api/v1/admin_rag.py` | P2 |
| **8. Bulk Operations** | Bulk actions have clear success/failure feedback | Needs Verification | Medium | `AdminContentHub.jsx` (toast/feedback) | P2 |
| **8. Bulk Operations** | Empty selections disable bulk action buttons | Needs Verification | Low | `AdminContentHub.jsx` | P3 |
| **9. Library & Public Delivery** | Library page reads from correct source of truth | Exists | Low | `apps/backend/app/api/v1/public_content.py`, `apps/backend/app/api/v1/edu.py` | — |
| **9. Library & Public Delivery** | Public chapter pages ignore drafts | Exists | Low | `public_content.py` (published filter) | — |
| **9. Library & Public Delivery** | Unpublished edits only visible in admin views | Exists | Medium | `admin_content.py` vs `public_content.py` separation | — |
| **9. Library & Public Delivery** | Cached/prerendered content invalidated through publish | Needs Verification | High | `admin_content.py` → `CF_PAGES_DEPLOY_HOOK` trigger | P1 |
| **9. Library & Public Delivery** | Search and CDN reflect publish state consistently | Needs Verification | High | `admin_seo.py`, Cloudflare KV / Vectorize sync | P1 |
| **10. Chat, History & Profile** | Chat uses the latest retrieval index | Needs Verification | High | `apps/backend/app/api/v1/chat.py` + RAG indexer | P1 |
| **10. Chat, History & Profile** | History reflects saved conversation state correctly | Exists | Low | `apps/backend/app/api/v1/conversations.py` | — |
| **10. Chat, History & Profile** | Profile updates propagate into personalization logic | Needs Verification | Medium | `apps/backend/app/api/v1/users.py` | P2 |
| **10. Chat, History & Profile** | Admin can inspect conversations with pagination | Exists | Low | `apps/backend/app/api/v1/admin_conversations.py` | — |
| **10. Chat, History & Profile** | Admin can inspect logs with pagination | Exists | Low | `apps/backend/app/api/v1/admin_analytics.py`, `AdminLogsExplorer.jsx` | — |
| **10. Chat, History & Profile** | Latency-sensitive flows avoid unnecessary work | Needs Verification | Medium | `chat.py` (RAG retriever, embedding calls) | P2 |
| **11. Analytics & Ops** | Analytics show publish, reindex, generate-notes, admin usage | Exists | Low | `apps/backend/app/api/v1/admin_analytics.py`, `AdminPage.jsx` | — |
| **11. Analytics & Ops** | Health panels expose system and AI status | Exists | Low | `apps/backend/app/api/v1/admin_db_health.py`, `admin_dashboard.py` | — |
| **11. Analytics & Ops** | Side-effectful cron routes use POST, not GET | Exists | Medium | `apps/backend/app/api/v1/admin_cron.py` | — |
| **11. Analytics & Ops** | Cron routes use separate secrets from admin auth | Exists | Medium | `admin_cron.py` (Bearer token, separate from admin cookie) | — |
| **11. Analytics & Ops** | Operational panels do not expose unsafe actions to browser | Exists | Medium | Admin routes all behind `require_admin_session` / Bearer guard | — |
| **11. Analytics & Ops** | Metrics help identify slow or failing steps | Needs Verification | Medium | `admin_analytics.py`, `admin_ai.py` (token/latency tracking) | P2 |

---

## Summary

| Status | Count |
|--------|-------|
| ✅ Exists | 26 |
| ⚠️ Needs Verification | 28 |
| ❌ Missing | 2 |

## Priority Queue

| Priority | Items |
|----------|-------|
| **P1 — Fix now** | RAG save stamps `rag_updated_at`; Reindex prompt after RAG save; `rag_indexed_at` stamping; Vectorize metadata indexes; chat uses current index; "Publish ≠ reindex" UI clarity; RAG stale/current badges; unpublished-edit badges; concurrency / 409 / versioning; CF Pages deploy hook on publish; CDN/search publish sync |
| **P2 — Fix soon** | Lazy-load route state; shared admin context sync; "save ≠ publish" UI copy; publish job tracking; publish retry; chapter card timestamps; bulk publish; bulk job feedback; profile propagation; chat latency review; metrics coverage |
| **P3 — Nice to have** | Legacy route redirects; empty-selection button disabling |
