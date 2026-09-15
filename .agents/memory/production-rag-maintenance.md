---
name: Production RAG maintenance
description: Operational constraint when production chapter notes are changed outside the staff Worker route
---

Out-of-band production D1 chapter-note writes do not run the staff route’s Vectorize reindex or subject-cache prewarm hooks. A complete maintenance write must replace the chapter’s note vectors and D1 chunk mappings, update `rag_indexed_at`, and invalidate the subject chapter-list KV key.

**Why:** The database write can succeed while search still serves old vectors or the public chapter list serves stale cached availability/content.

**How to apply:** Prefer the authenticated staff chapter PATCH plus reindex route; if direct Wrangler maintenance is unavoidable, mirror the Worker’s deterministic note chunk IDs and metadata, refresh embeddings, verify `rag_indexed_at >= rag_updated_at`, and invalidate the subject cache.