---
name: Vectorize deletion batching
description: Vectorize rejects ID-deletion requests that exceed 100 IDs.
---

Limit every Vectorize ID-deletion request to at most 100 IDs; split larger cleanup sets into batches.

**Why:** The native publish reindex path attempted to delete 1,000 IDs in one request, which Vectorize rejected and left the publish job partial.

**How to apply:** Before reindexing a chapter or corpus, batch stale Vectorize IDs in groups of 100 or fewer and verify all cleanup batches complete before writing replacements.