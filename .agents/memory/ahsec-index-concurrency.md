---
name: AHSEC index concurrency
description: Concurrency rule for AHSEC note writes, index replacement, and targeted repair.
---

Per-chapter index serialization must cover the normal chapter note write and the complete non-atomic vector/D1 replacement, not only the vector calls. A repair that waits on the lock must refresh stored notes after acquiring it; otherwise a stale snapshot can overwrite a newer import after the importer finishes.

**Why:** Vectorize and D1 operations are not one transaction, and a repair can be queued with an older chapter snapshot while a normal import publishes newer notes.

**How to apply:** Keep the guard scoped to one chapter so unrelated imports continue, use a bounded acquisition timeout, and record lock contention as an explicit retryable index failure with the existing repair command.