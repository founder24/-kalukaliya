---
name: Workers AI RAG indexing contract
description: Current Cloudflare limits and embedding response shapes that affect chapter reindexing.
---

Workers AI bge-m3 may return `result.data` as bare numeric arrays rather than `{ values }` objects, and Vectorize delete requests accept at most 100 IDs.

**Why:** A reindex can otherwise discard every embedding or fail before indexing because the older response/batch assumptions are not rejected clearly.

**How to apply:** Normalize both bge-m3 item shapes before upsert, and keep stale-vector deletion batches at or below the provider limit.