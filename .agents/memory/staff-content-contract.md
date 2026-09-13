---
name: Staff content editor contract
description: Canonical API paths and compatibility rules for staff/admin content editing.
---

The shared content editor should use the canonical `/staff/content/...` API
routes for subject and chapter CRUD, status changes, and chapter lists. Keep
`/admin/content/...` for admin-only operations such as publish jobs, RAG jobs,
stats, and uploads.

**Why:** The legacy admin CRUD aliases rely on redirects and can lose the
editor's expected response shape. Direct staff routes avoid the redirect
failure and keep one contract for staff and admin roles.

**How to apply:** When adding or repairing editor actions, use the staff route
family for shared CRUD and preserve list aliases such as `content`,
`content_as`, and `notes_generated` when an older editor consumes the result.