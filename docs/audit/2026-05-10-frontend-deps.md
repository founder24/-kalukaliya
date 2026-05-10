# Frontend Dependency Audit — 2026-05-10

> **Scope:** root pnpm workspace + `artifacts/syrabit/`, `artifacts/mockup-sandbox/`,
> `workers/edge-proxy/`, `workers/email-worker/`, `scripts/`. Read-only audit
> per Task #54. **No `package.json` or `pnpm-lock.yaml` was modified.**
>
> **Tooling:** `pnpm@10.26.1`, `pnpm audit --json`, `pnpm outdated --recursive --format json`.
> 1 052 total dependencies in the dependency graph; 6 workspace packages; 122
> direct deps across all `package.json` files.

---

## 1. Headline risks (read these first)

| # | Package | Current | Latest | Risk | Recommendation |
|---|---|---|---|---|---|
| 1 | **`basic-ftp`** (transitive via `puppeteer → @puppeteer/browsers → proxy-agent → pac-proxy-agent → get-uri`) | ≤5.3.0 | ≥5.3.1 | **HIGH CVE-2026-44240** — malicious FTP server can DoS the client via unbounded multiline control-response buffering. CVSS not yet assigned but rated `high` by GH advisory DB. | **patch via puppeteer bump** → `puppeteer 24.41 → 24.43`, then re-run `pnpm audit`. Puppeteer is a **dev-only** dep (used by `tests/` Playwright/Puppeteer suite), so production runtime exposure is **zero**. Recommend bumping in next patch window, not emergency. |
| 2 | **`ip-address`** (transitive via same puppeteer chain) | ≤10.1.0 | ≥10.1.1 | **MODERATE CVE-2026-42338** — XSS in `Address6` HTML-emitting methods. | Same chain as #1; same fix. **Dev-only**, no prod runtime exposure. |
| 3 | **`firebase`** | `^10.14.1` | `12.13.0` | **2 majors behind**. v11 dropped CommonJS, v12 changed `getAuth()` modular API. Used by `artifacts/syrabit/` for analytics + push. | **MAJOR-BUMP-WITH-FOLLOW-UP-TASK** — schedule explicit migration task. Read v11 + v12 changelogs; expect ~half-day of import rewrites. Don't bundle into a generic dep bump. |
| 4 | **`lamejs`** | `^1.2.1` | n/a — package effectively unmaintained (last npm publish 2017; not surfaced by `pnpm outdated` because no newer version exists). | Used by `artifacts/syrabit/` for client-side MP3 encoding (TTS playback / voice features). **Known-unmaintained** with open security issues against `BitStream` int overflow. | **REPLACE** — evaluate `@breezystack/lamejs` (community fork, last publish 2024) or move MP3 encoding server-side. Track as a separate hardening task. |
| 5 | **`react-helmet-async`** | `3.0.0` (pinned exact, no caret) | `2.0.5` is the actually-published 2.x line; the 3.x line was the abandoned `@dr.pogodin/react-helmet` fork. | Pin is to a deprecated package release. Not surfaced by `pnpm outdated` because the registry doesn't show a "newer" version. | **REPLACE** — migrate to `@dr.pogodin/react-helmet` (active maintainer, drop-in compatible) **or** to React 19's native `<title>`/`<meta>` hoisting which the project already supports (workspace pins `react@19.1.0`). Treat as tech-debt follow-up. |
| 6 | **`react-markdown`** | `^8.0.7` | `10.1.0` | **2 majors behind**. v9 dropped Node 16 / changed plugin API; v10 dropped React 17 (we're on 19, fine). | **MAJOR-BUMP-WITH-FOLLOW-UP-TASK** — pair with `remark-gfm 3 → 4` (also flagged below). Renderer surface in chat/notes — needs visual smoke. |
| 7 | **`react-resizable-panels`** | `^2.1.9` | `4.11.0` | **2 majors behind**. Used in shadcn `<Resizable>` shells. | **MAJOR-BUMP-WITH-FOLLOW-UP-TASK** — v3 changed group `direction` semantics; v4 changed the imperative API. Visual regression risk in admin/CMS layouts. |

> **No `critical` advisories.** **No `info`/`low` advisories.** Net advisory
> ladder: `0 critical / 1 high / 1 moderate / 0 low / 0 info` — both
> findings transitive under a dev-only package.

---

## 2. Vulnerability scan (`pnpm audit --json`)

```
{ "info": 0, "low": 0, "moderate": 1, "high": 1, "critical": 0 }
```

| Severity | Package | CVE | GH Advisory | Vuln versions | Patched | Path | Direct/Transitive | Workspace | Fix-available? |
|---|---|---|---|---|---|---|---|---|---|
| HIGH | `basic-ftp` | CVE-2026-44240 | [GHSA-rpmf-866q-6p89](https://github.com/advisories/GHSA-rpmf-866q-6p89) | `<=5.3.0` | `>=5.3.1` | `.>puppeteer>@puppeteer/browsers>proxy-agent>pac-proxy-agent>get-uri>basic-ftp` | TRANSITIVE (dev) | root | YES — bump `puppeteer` |
| MODERATE | `ip-address` | CVE-2026-42338 | [GHSA-v2v4-37r5-5v8g](https://github.com/advisories/GHSA-v2v4-37r5-5v8g) | `<=10.1.0` | `>=10.1.1` | `.>puppeteer>@puppeteer/browsers>proxy-agent>socks-proxy-agent>socks>ip-address` | TRANSITIVE (dev) | root | YES — bump `puppeteer` |

**Both findings collapse to a single fix:** `pnpm -w up puppeteer@latest`
(target `24.43.0`). Recommend doing this as a **minor-bump** PR alongside the
next maintenance round; no emergency cycle warranted because:

- `puppeteer` is in `devDependencies` only (verified — see `package.json` root + workspace).
- It is invoked **exclusively by Playwright/Puppeteer test runs** in CI; never bundled into the SPA, the worker, or the SSR pipeline.
- Even in dev, exploitation requires the local test runner to connect to an attacker-controlled FTP server / IPv6 address (not a realistic threat model for this codebase).

---

## 3. Outdated scan (`pnpm outdated --recursive`)

37 direct deps with newer versions on the registry. Grouped by major-version
jump (` ` = patch/minor, `!` = 1 major, `!!` = ≥2 majors).

### 3a. Two-or-more majors behind (`!!`) — needs a thinking-task each

| Package | Current | Latest | Workspace | Type | Risk | Recommendation |
|---|---|---|---|---|---|---|
| `firebase` | `10.14.1` | `12.13.0` | `artifacts/syrabit` | dep | API breakage v10→v11→v12 (modular SDK rewrites) | **major-bump-with-follow-up-task** (see headline #3) |
| `react-markdown` | `8.0.7` | `10.1.0` | `artifacts/syrabit` | dep | Plugin API + remark/rehype peer changes | **major-bump-with-follow-up-task** (pair with `remark-gfm`) |
| `react-resizable-panels` | `2.1.9` | `4.11.0` | `artifacts/syrabit` | devDep (used in admin shells) | Imperative API rewrite v3 + v4 | **major-bump-with-follow-up-task** |
| `@hookform/resolvers` | `3.10.0` | `5.2.2` | `artifacts/syrabit` | devDep | v4 dropped Yup/Zod sub-paths; v5 changed validator signatures | **major-bump-with-follow-up-task** — paired with `react-hook-form` peer (currently 7.71→7.75 patch) |

### 3b. One major behind (`!`)

| Package | Current | Latest | Workspace | Type | Recommendation |
|---|---|---|---|---|---|
| `@vitejs/plugin-react` | `5.1.4` | `6.0.1` | both SPAs | devDep | **minor-bump-after-vite-major** — gated on the vite 7→8 decision below |
| `chokidar` | `4.0.3` | `5.0.0` | scripts | devDep | minor-bump (only used in dev watchers) |
| `date-fns` | `3.6.0` | `4.1.0` | `artifacts/syrabit` | devDep | minor-bump — v4 ships ESM-only + tree-shake-friendly tz; some imports rewrite |
| `lucide-react` | `0.545.0` | `1.14.0` | `artifacts/syrabit` (catalog pin!) | devDep | `1.0` is the maintainer's stable cut. Renames a few icons. **Catalog-pinned — needs catalog edit, see §5** |
| `react-day-picker` | `9.14.0` | `10.0.0` | `artifacts/syrabit` | devDep | v10 changed render slot props |
| `recharts` | `2.15.4` | `3.8.1` | `artifacts/syrabit` | devDep | v3 dropped UMD + simplified tooltip API; touches every chart |
| `remark-gfm` | `3.0.1` | `4.0.1` | `artifacts/syrabit` | dep | **bundle with the `react-markdown 8→10` task** |
| `tailwindcss` | `3.4.19` | `4.3.0` | both SPAs (catalog pin!) | devDep | **TAILWIND v4 IS A REWRITE** — config moves into CSS, JIT engine rewritten in Rust. Treat as a **dedicated cross-cutting task**; not a routine bump. Catalog-pinned. |
| `typescript` | `5.9.3` | `6.0.3` | many | devDep | TS 6 removed deprecated compiler flags + tightened `useUnknownInCatchVariables`. Audit TS errors first. |
| `vite` | `7.3.2` | `8.0.11` | both SPAs (catalog pin!) | devDep | **gated** — bump after `@vitejs/plugin-react 6` and after the `tailwind v4` decision (they share the same dev pipeline) |
| `zod` | `3.25.76` | `4.4.3` | many (catalog pin!) | devDep | **zod v4 is a rewrite** (faster but changed inference + `safeParse` shape). Cross-cutting; touches every Orval-generated client + every route validator. **Dedicated task.** |

### 3c. Patch / minor only (safe to bump in a single batch)

These 22 packages are all patch or minor bumps with no advertised breaking
changes; recommend a single grouped PR labeled `chore(deps): patch+minor batch`.

| Package | Current → Latest |
|---|---|
| `@cloudflare/workers-types` | `4.20260424.1 → 4.20260509.1` |
| `@mdxeditor/editor` | `3.53.1 → 3.55.0` |
| `@replit/vite-plugin-cartographer` | `0.5.1 → 0.5.5` |
| `@supabase/supabase-js` | `2.105.1 → 2.105.4` |
| `@tailwindcss/vite` | `4.2.1 → 4.3.0` (couples with `tailwindcss` v4 decision) |
| `@tanstack/react-query` | `5.90.21 → 5.100.9` |
| `@tanstack/react-virtual` | `3.13.23 → 3.13.24` |
| `@types/node` | `25.3.5 → 25.6.2` |
| `autoprefixer` | `10.4.27 → 10.5.0` |
| `axios` | `1.15.2 → 1.16.0` |
| `dompurify` | `3.4.1 → 3.4.2` |
| `framer-motion` | `12.35.1 → 12.38.0` |
| `jsdom` | `29.0.2 → 29.1.1` |
| `postcss` | `8.5.12 → 8.5.14` |
| `prettier` | `3.8.1 → 3.8.3` |
| `puppeteer` | `24.41.0 → 24.43.0` ← **clears both CVEs in §2** |
| `react` | `19.1.0 → 19.2.6` (catalog pin — see §5) |
| `react-dom` | `19.1.0 → 19.2.6` (catalog pin — see §5) |
| `react-hook-form` | `7.71.2 → 7.75.0` |
| `react-router-dom` | `7.13.2 → 7.15.0` |
| `vitest` | `4.1.4 → 4.1.5` |
| `wrangler` | `4.85.0 → 4.90.0` |

---

## 4. Vulnerable AND > 1 major behind (cross-reference)

**None.** The two advisories are in transitive packages whose direct parent
(`puppeteer`) is only 2 minor versions behind.

---

## 5. No-touch list (catalog-pinned or known-bad upgrade path)

Anything in this list **must not** be edited by a generic dep-bump batch
PR. Each requires its own decision + task.

### 5a. `pnpm-workspace.yaml` `catalog:` entries — single-source-of-truth pins

Bumping any of these in a workspace `package.json` will be **silently
overridden** by the catalog. To bump, edit `pnpm-workspace.yaml` first.

| Catalog key | Pin | Outdated? | Notes |
|---|---|---|---|
| `react` | `19.1.0` | yes → `19.2.6` (patch) | safe in next batch; catalog edit |
| `react-dom` | `19.1.0` | yes → `19.2.6` (patch) | bump in lock-step with `react` |
| `@types/react` | `^19.2.0` | unknown — not in outdated scan | aligns with `react` major |
| `@types/react-dom` | `^19.2.0` | unknown | aligns with `react-dom` major |
| `@types/node` | `^25.3.3` | yes → `25.6.2` (minor) | safe in next batch |
| `vite` | `^7.3.2` | yes → `8.0.11` (major) | **dedicated task** |
| `@vitejs/plugin-react` | `^5.0.4` | yes → `6.0.1` (major) | gated on `vite` v8 |
| `@tailwindcss/vite` | `^4.1.14` | yes → `4.3.0` (minor) | safe |
| `tailwindcss` | `^4.1.14` | actually `3.4.19` resolved → `4.3.0` available | **WARNING:** the catalog pin says `^4.1.14` but `pnpm outdated` resolves to `3.4.19` from `artifacts/syrabit/package.json` — there's a mismatch worth investigating. The SPA package.json likely overrides the catalog with an older v3 pin to dodge the v4 rewrite. **Resolve before any tailwind bump.** |
| `lucide-react` | `^0.545.0` | yes → `1.14.0` (major) | **catalog edit + icon-rename audit** |
| `framer-motion` | `^12.23.24` | yes → `12.38.0` (minor) | safe |
| `@tanstack/react-query` | `^5.90.21` | yes → `5.100.9` (minor) | safe |
| `zod` | `^3.25.76` | yes → `4.4.3` (major) | **dedicated cross-cutting task** |
| `tsx`, `clsx`, `class-variance-authority`, `tailwind-merge`, `drizzle-orm`, `@replit/*` | various | not surfaced as outdated | leave alone |

### 5b. Pinned-exact / replace-rather-than-bump

| Package | Current | Why no-touch | Path forward |
|---|---|---|---|
| `react-helmet-async` | `3.0.0` (no caret) | Pinned to a deprecated 3.x line of an abandoned package. | **REPLACE** with `@dr.pogodin/react-helmet` or React-19 native head hoisting. Tracked as headline #5. |
| `lamejs` | `^1.2.1` | Last npm publish 2017; no maintained successor under same name. | **REPLACE** with `@breezystack/lamejs` or move MP3 encoding server-side. Tracked as headline #4. |

### 5c. `overrides:` in `pnpm-workspace.yaml`

The workspace pins **`esbuild: 0.27.3`**, **`undici: 7.25.0`**,
**`pdfjs-dist: 4.2.67`**, **`postcss: 8.5.12`**, **`follow-redirects: 1.16.0`**,
**`lodash: 4.18.1`**, **`tar: ^7.5.7`** as overrides. These are **deliberate
floor-pins** (likely set during prior CVE remediation). Don't touch them
without re-running `pnpm audit` to confirm the override is no longer needed.

---

## 6. Per-finding recommendation summary

| Finding | Verdict |
|---|---|
| `basic-ftp` HIGH (transitive) | **patch** via `puppeteer@24.43` in next batch PR |
| `ip-address` MODERATE (transitive) | **patch** via same puppeteer bump |
| `firebase 10→12` | **major-bump-with-follow-up-task** |
| `react-markdown 8→10` + `remark-gfm 3→4` | **major-bump-with-follow-up-task** (paired) |
| `react-resizable-panels 2→4` | **major-bump-with-follow-up-task** |
| `@hookform/resolvers 3→5` + `react-hook-form 7.71→7.75` | **major-bump-with-follow-up-task** (paired) |
| `react-helmet-async@3.0.0` (deprecated package) | **replace** (not bump) |
| `lamejs@1.2.1` (unmaintained 2017) | **replace** (not bump) |
| `tailwindcss 3→4` (catalog mismatch + rewrite) | **dedicated cross-cutting task**; resolve catalog drift first |
| `vite 7→8` + `@vitejs/plugin-react 5→6` | **dedicated task**, paired |
| `zod 3→4` | **dedicated cross-cutting task** (touches every validator + Orval client) |
| `typescript 5→6` | **minor task**, run after zod 4 (zod 4 typings change) |
| Other 22 patch/minor entries (§3c) | **single batch PR** — `chore(deps): patch+minor batch` |

## 7. Methodology notes (for the next auditor)

- `pnpm audit --json` captures both direct and transitive vulnerabilities,
  but only against the *current* lockfile. If the lockfile is regenerated
  with newer versions, re-run.
- `pnpm outdated` only surfaces packages where a newer version is *published*
  on npm. It will silently miss:
  - **Pinned-exact deps** that are deprecated but published (e.g. `react-helmet-async@3.0.0`).
  - **Unmaintained deps** with no successor on the same name (e.g. `lamejs`).
  - **Catalog-driven** deps where the catalog pin matches the workspace pin.
  Cross-check by hand for any package you suspect is stuck.
- `pnpm-workspace.yaml > minimumReleaseAge: 1440` (24 h) means freshly
  published versions are intentionally held back for one day before pnpm
  will resolve them. This explains tiny lags between `latest` and what
  actually installs.

---

*Audit run: 2026-05-10, pnpm 10.26.1, against origin/main HEAD `70cfb40b`.*
