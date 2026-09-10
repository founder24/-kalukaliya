---
name: Production browser verification traps
description: Non-obvious Vite and Playwright behaviors that can manufacture staff-portal failures.
---

Vite preload-error handlers may trigger a bounded reload, but they must not cancel the original error event.

**Why:** Cancelling the event tells Vite the failed preload was handled. Its lazy loader then resolves without a module, and React crashes while reading the missing default export instead of preserving the real rejected import.

**How to apply:** Reload once for stale deployed chunks while allowing the original rejection to propagate. Test both the reload limit and that the event remains uncancelled.

Browser init scripts used to inject disposable authentication must seed credentials only once per tab.

**Why:** Init scripts execute on every document navigation. Re-injecting tokens on the post-logout protected-route visit makes a successful logout look broken and invalidates the production proof.

**How to apply:** Store a session-scoped seed marker that logout does not remove, never log credential values, and verify storage is empty before testing protected access after sign-out.