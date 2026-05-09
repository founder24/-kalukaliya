# Frontend knip audit report — Task #7 (2026-05-09)

Companion artifact to [`2026-purge-log.md`](2026-purge-log.md). This file
captures the **post-purge** `knip` output for `artifacts/syrabit/` so a
future agent can diff against it before bumping the allowlist or the
`rules` severity in `artifacts/syrabit/knip.json`.

## How to reproduce

```bash
pnpm --filter @workspace/syrabit run lint:knip
# expands to: pnpm dlx knip --no-progress --no-config-hints
```

CI wiring: `.github/workflows/frontend-tests.yml` runs the same command
on every push / PR to `Replit-agent`. A non-zero exit blocks merge.

## Final output

```
Unused exports (3)
useTurnstile                  function  src/hooks/useTurnstile.jsx:152:17
adminEntitySeoHistory    api            src/utils/api.jsx:224:14
isImageResizerAvailable       function  src/utils/imageCdn.js:64:17
Duplicate exports (2)
renderRoute|default  src/entry-server.jsx
Analytics|default    src/utils/analytics.jsx
```

Exit code: **0** (clean — only `warn`-severity findings remain).

## Severity rationale (`knip.json` → `rules`)

The 5 remaining findings are all **intentional**, so they are downgraded
to `warn` rather than deleted. Deleting them would silently change a
public API surface for future code-paths or break a SSR shape contract.

| Finding | Why kept | Severity |
| --- | --- | --- |
| `useTurnstile` (`src/hooks/useTurnstile.jsx`) | Cloudflare Turnstile bot-defense hook. Currently the `<Turnstile>` widget component embeds its own ref-based logic; the hook is the documented public-API alternative for upcoming forms (e.g. signup flow re-instrumentation discussed in Task #581). Removing it would force a re-implementation when those forms re-add CAPTCHA. | `warn` |
| `adminEntitySeoHistory` (`src/utils/api.jsx`) | Sibling of `adminEntitySeo*` admin-API helpers consumed by `EntitySeoTab.jsx`. The history endpoint is wired backend-side (`routes/admin_seo.py:list_history`) but the UI hasn't surfaced the timeline tab yet. Keeping the typed wrapper avoids drift between the typed client and the live REST shape. | `warn` |
| `isImageResizerAvailable` (`src/utils/imageCdn.js`) | Public probe for Cloudflare Image Resizing plan-availability gating. Its sibling `markImageResizerUnavailable` is called from `<img onError>` handlers documented in `imageCdn.js:60–66`; the read-side helper is the public API even though the current call sites read `_planGated` directly. | `warn` |
| `renderRoute|default` (`src/entry-server.jsx`) | SSR entry must export `renderRoute` named **and** as `default` so that both `prerender-routes.mjs` (which calls `renderRoute`) and any consumer that does `import render from './entry-server.jsx'` (Vite SSR convention) keep working. Knip flags the dual-export as duplicate; this is a Vite-SSR contract requirement, not a real duplicate. | `warn` |
| `Analytics|default` (`src/utils/analytics.jsx`) | Same shape as the SSR entry: the module exports a frozen `Analytics` object both named (for `import { Analytics } from …`) and as default (for `import Analytics from …`). 81 import sites mix the two styles; collapsing to one would force a 81-site refactor with no behavior change. | `warn` |

## What WILL fail CI (severity = `error`)

The `rules` block in `knip.json` keeps these issue types at the default
`error` severity, so any future regression on these is a CI failure
(blocks merge):

- **`files`** — a new orphan `.jsx`/`.js`/`.ts` file that no entry can
  reach. Forces a delete-or-wire-up decision.
- **`dependencies` / `devDependencies` / `optionalPeerDependencies`** —
  a new npm dep that nothing imports. The whole point of Task #7.
- **`unlisted`** — a `package.json` script or production code referring
  to a binary that isn't declared as a (dev)dep.
- **`unresolved`** — an import that knip can't trace to a file or dep.

The five soft findings above can grow without bound and CI will only
warn. If that list ever exceeds ~10 entries, re-evaluate whether to
flip those rules back to `error` and either delete or actively wire up
the listed APIs.

## Allowlist surface (`knip.json` → `entry`)

Why each non-obvious entry exists:

| Entry | Why allowlisted |
| --- | --- |
| `src/entry-server.jsx` | SSR entry — used by `scripts/prerender-*.mjs`, not by the browser bundle. |
| `index.html` | Vite picks this up implicitly, but listing it makes the intent explicit. |
| `scripts/inline-critical-css.mjs` | Spawned by `scripts/build.mjs:202` via `node()` (process spawn, not import) — keeps `beasties` as a live devDep. |
| `scripts/prerender-{chat,library,routes,static-routes}.mjs` | Build-pipeline CLIs spawned by `build.mjs`. |
| `scripts/_prerender-data.mjs`, `scripts/_page-chunk-preload.mjs` | Internal helpers loaded by the prerender CLIs above (the `_` prefix marks them as private, so they're not re-exported and knip can't follow). |
| `scripts/apply-pages-config.mjs` | Cloudflare Pages config applier, run by deploy automation, not the build. |
| `scripts/verify-hydration.mjs` | Spawned by `scripts/verify-all.mjs:472`. |
| `scripts/post-deploy-lighthouse.js` | Run from `.github/workflows/post-deploy-lighthouse.yml` only. |
| `scripts/nightly-smoke.js` | Run from a nightly workflow, not the build. |
| `scripts/cloudflare-{annual-review,full-audit,phase{2,3,4,5,6}-apply}.js` | Operator-run audit / drift-fix CLIs. |
| `scripts/enforce-branch-protection.js` | Run from `.github/workflows/enforce-branch-protection.yml`. |
| `public/_worker.js` | Cloudflare Pages Functions entry — proxied to by Cloudflare, not imported by Vite. |
| `public/sw.js` | Service Worker registered at runtime by the SPA — not part of the Vite graph. |
| `functions/_middleware.js` | Cloudflare Pages middleware — same reason as `public/_worker.js`. |
| `tests/**/*.{js,mjs,ts}`, `src/**/*.test.{js,jsx,ts,tsx}` | Vitest + Playwright suites. |

## Allowlist surface (`knip.json` → `ignoreDependencies`)

| Dep | Why ignored |
| --- | --- |
| `tailwindcss-animate` | Loaded as a Tailwind plugin in `tailwind.config.js` via the string `'tailwindcss-animate'` (not an `import`). Knip can't follow string-literal plugin loading. |

## Allowlist surface (`knip.json` → `ignore`)

| Path | Why ignored |
| --- | --- |
| `vite-plugins/**` | Workspace-internal Vite plugins; knip can't resolve their cross-package import paths. The plugins themselves are exercised on every build. |
