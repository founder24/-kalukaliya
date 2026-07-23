---
name: Syrabit /library LCP optimization
description: Root causes found and fixes applied to boost /library PSI from 82→89; what remains for 95+
---

## Confirmed root causes (both fixed)

### 1. React Query cache miss → late API fetch
- `prerender-library.mjs` injected `window.__LIBRARY_BUNDLE__` but `App.jsx` reads `window.__SSR_QUERIES__`
- Fix: prerender now also writes `window.__SSR_QUERIES__` with `{key:["library-bundle-slim"], data: slim}`
- File: `apps/frontend/scripts/prerender-library.mjs`

### 2. VirtualSubjectGrid renders nothing during SSR
- `VirtualSubjectGrid` uses `@tanstack/react-virtual` which needs real DOM measurements → 0 cards in SSR HTML
- Physics `<p>` (LCP element) only appeared after React hydration long task (~3–4 s) → LCP = render delay
- Fix: split-render in `LibraryPage.jsx` — first VIRTUAL_CHUNK (6) cards always rendered as static `<SubjectCard>` elements above VirtualSubjectGrid
- VirtualSubjectGrid receives only subjects from index 6+ and is mounted below the static grid

**Why:** `useVirtualGrid` gating on `scrollContainerEl !== null` (attempted fix) caused scroll-container ref to flip the grid after mount, replacing the static cards → Chrome saw Physics `<p>` updated → new LCP candidate at ~4.8 s (WORSE than original). Split-render approach keeps static cards immutable.

### 3. localStorage-based ranking causes hydration mismatch
- `rankedSubjects` called `getRecentChapters()` which reads localStorage — empty in SSR, non-empty for returning users
- Different subject order → React reconciled entire card grid at hydration time
- Fix: `hydrated` state (set via `startTransition` in useEffect) — ranking only applies after hydration
- File: `apps/frontend/src/pages/LibraryPage.jsx`

## Results achieved

| Run | Performance | FCP | LCP | TBT | CLS |
|-----|------------|-----|-----|-----|-----|
| PSI baseline (before fixes) | 82 | 2.4 s | 4.4 s | 30 ms | 0 |
| PSI after all fixes | **89** | 2.7 s | **3.0 s** | 70 ms | 0.054 |
| Local LH after fixes | 88–94 | 2.1–2.5 s | 2.3–2.8 s | 150 ms | 0.054 |

## Remaining gap (89 → 95+)

LCP 3.0 s → 2.5 s on PSI is the main bottleneck. Root cause: render delay still 2.3–2.4 s.

Likely causes of residual render delay:
1. **CSS parse long task** (~356 ms at 926 ms mark) — beasties inlines full CSS; reducing CSS footprint would help
2. **JS bundle size** — index-*.js (252 ms) and library page JS (283 ms) long tasks block paint
3. **Font timing** — 0.15 s gap between FCP and LCP may be font load latency; adding explicit `<link rel="preload" as="font">` tags in SSR HTML could close this

Quick wins not yet done:
- Verify Space Grotesk font has a preload tag in the prerendered HTML
- Consider `font-display: optional` to avoid font-swap CLS (reduces CLS from 0.054)
- Code-split heavy library-page-only JS to reduce main-thread long tasks

## CLS regression note
CLS went from 0 → 0.054 after split-render. Likely from VirtualSubjectGrid height estimation changing after mount (rows use `estimateSize: 480` but measured differently). To fix: set explicit `minHeight` on VirtualSubjectGrid container or use a more accurate initial estimate.
