---
name: React lazy SSR with renderToString
description: React.lazy() _status flag is not set until first render call; pre-importing modules CANNOT fix SSR Suspense abort with renderToString
---

## The Rule
`React.lazy()` is incompatible with `renderToString` + `<Suspense fallback={null}>` when:
- The lazy component is not pre-initialized (first render in the same renderToString call)
- Even if you `await import(...)` before `renderToString`, React.lazy()'s internal `_status` stays `-1` until the first render attempt calls `lazyInitializer()`, which schedules the `.then()` on the microtask queue — but `renderToString` is synchronous, so the status never updates to `1 (resolved)` in time

**Symptom:** `<!--$!-->` and "does not support Suspense" markers in prerendered HTML; `verify-all.mjs` reports SSR abort violations.

**Why pre-awaiting only partially works:** If two routes render the same lazy component concurrently (pMap concurrency 8), the second render may see `_status = 1` because the first render's microtask flushed between them. This creates race-condition "partial fixes" that look like success for one route but not another.

**Correct fixes:**
1. **Static imports** (preferred for sub-components already in the same route chunk): Replace `lazy(() => import('./Comp'))` with `import Comp from './Comp'`. No bundle cost if the parent is already route-split.
2. **renderToPipeableStream** (correct for top-level page lazy): The streaming SSR API natively handles Suspense boundaries.
3. **Never** rely on `await Promise.all([import(...)])` alone to fix `renderToString` + lazy Suspense — it doesn't update React.lazy() state.

**How to apply:** Check prerendered routes in `entry-server.jsx`. Any page that uses internal `React.lazy()` wrappers for sub-sections will fail SSR. If those sub-sections are already bundled with the parent page chunk (because only the parent imports them), convert to static imports.
