---
name: Chat cancellation state
description: Durable rules for making Stop, quota compensation, and chat persistence race-safe.
---

Treat a chat request claim as the authority for cancellation, failure cleanup, and completion. Every quota decrement, history insert, memory write, and completion update must be conditional on the claim still being `reserved`, within the same transactional D1 batch as its state transition.

**Why:** Independent requests can race at any await boundary. Application-level “check then write” logic can double-release a shared quota counter or persist an answer after Stop.

**How to apply:** Capture the exact quota period before reservation and store that same period on the claim. Preserve an owner-scoped cancellation tombstone when Stop wins admission, re-read after insert conflicts, and never delete a `cancelled` claim during failure cleanup.