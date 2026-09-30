---
name: Chat cancellation state
description: Durable rules for making Stop, quota compensation, and chat persistence race-safe.
---

Treat a chat request claim as the authority for cancellation, failure cleanup, and completion. Every quota decrement, history insert, memory write, and completion update must be conditional on the claim still being `reserved`, within the same transactional D1 batch as its state transition.

During streaming, use request/writer abort propagation for immediate Stop and treat the D1 cancellation tombstone as a fallback. Avoid an awaited D1 query for every token; keep the pre-generation and pre-completion/persistence checks, and make any mid-stream tombstone polling bounded.

**Why:** Independent requests can race at any await boundary, while per-token D1 reads run inside the bounded provider stream attempt and can consume its deadline. The UI aborts the request, and the API worker propagates that signal through the stream.

**How to apply:** Capture the exact quota period before reservation and store that same period on the claim. Preserve an owner-scoped cancellation tombstone when Stop wins admission, re-read after insert conflicts, and never delete a `cancelled` claim during failure cleanup. Keep transport abort immediate and use a short, bounded D1 poll only as fallback.