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

Cloudflare Access service-token headers belong only on the protected site origin, never on the public API origin.

**Why:** Adding Access headers to cross-origin API requests triggers a browser preflight that the public API correctly rejects. Redirects can also carry overridden request headers farther than expected.

**How to apply:** Route site and API origins separately, strip and fail on any Access header observed on API requests, and make API login calls without Access headers.

Post-logout HTTP exceptions must be exact: only a 401 from the known auth-probe endpoints is expected.

**Why:** Exempting an endpoint regardless of status lets 404/429/5xx failures pass as if they proved logout.

**How to apply:** Match the exact auth path and status, require at least one expected denial, and keep every other failed response fatal.