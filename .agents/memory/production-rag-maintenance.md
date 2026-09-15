---
name: Production RAG maintenance
description: Operational constraint when production chapter notes are changed outside the staff Worker route
---

Out-of-band production D1 chapter-note writes do not run the staff route’s Vectorize reindex or subject-cache prewarm hooks. A complete maintenance write must replace the chapter’s note vectors and D1 chunk mappings, update `rag_indexed_at`, and invalidate the subject chapter-list KV key.

**Why:** The database write can succeed while search still serves old vectors or the public chapter list serves stale cached availability/content.

**How to apply:** Prefer the authenticated staff chapter PATCH plus reindex route; if direct Wrangler maintenance is unavoidable, mirror the Worker’s deterministic note chunk IDs and metadata, refresh embeddings, verify `rag_indexed_at >= rag_updated_at`, and invalidate the subject cache.

Cloudflare’s current Vectorize REST API uses the underscore form `delete_by_ids`; the hyphenated form can return 404. Remote D1 chunk-mapping inserts also need small batches because a large multi-row statement can exceed the bind-parameter limit.

**Why:** A chapter note write can complete while an outdated Vectorize cleanup path or oversized mapping statement aborts the index refresh, leaving search stale or incomplete.

**How to apply:** Validate the REST endpoint with a non-destructive request before a repair, keep purge/upsert batches within provider limits, and only mark the chapter indexed after the D1 mappings are read back successfully.