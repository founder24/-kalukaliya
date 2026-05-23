import { lazy } from "react";

/**
 * `React.lazy()` wrapper that exposes a `.preload()` method which both
 * fetches the chunk AND primes React.lazy's internal payload so the
 * lazy component renders **synchronously** on its first render — no
 * Suspense fallback, no DOM swap, no hydration mismatch.
 *
 * Used by Task #395 to give each prerendered page (Library, Chat,
 * SubjectLanding, Chapter) its own JS chunk while still hydrating
 * cleanly. `index.jsx` calls `.preload()` for the route's hydration
 * kind BEFORE invoking `hydrateRoot()`, so when React renders the
 * route's lazy component during hydration it sees the resolved module
 * already cached on the payload.
 *
 * Implementation note: `Component._payload._status === 1` (Resolved)
 * + `_payload._result === moduleObject` is React.lazy's internal
 * "ready" state shape, stable across React 18 and 19. If a future
 * React release changes that shape, the worst-case fallback is the
 * standard React.lazy behaviour (Suspense fallback during hydration),
 * which the surrounding `<Suspense fallback={...}>` already handles.
 */
export function lazyPreload(loader) {
  const Component = lazy(loader);
  let pending = null;

  Component.preload = () => {
    if (!pending) {
      pending = Promise.resolve()
        .then(loader)
        .then((mod) => {
          try {
            const payload = Component._payload;
            if (payload && payload._status !== 1) {
              payload._status = 1;
              payload._result = mod;
            }
          } catch {
            /* ignore — fall back to standard lazy resolution */
          }
          return mod;
        });
    }
    return pending;
  };

  return Component;
}
