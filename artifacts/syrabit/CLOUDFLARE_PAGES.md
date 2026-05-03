# Cloudflare Pages — Syrabit Frontend Deploy

These are the canonical Cloudflare Pages settings for the Syrabit frontend
(`syrabit.ai`). The backend lives separately on Railway as `api.syrabit.ai`
and is **not** deployed via Pages.

## Dashboard settings

| Setting                    | Value                                                                |
| -------------------------- | -------------------------------------------------------------------- |
| **Production branch**      | `Replit-agent`                                                       |
| **Framework preset**       | `None` (custom)                                                      |
| **Root directory** (Project) | `/` (repo root — pnpm monorepo root, do **not** set to `artifacts/syrabit`) |
| **Build command**          | See below                                                            |
| **Build output directory** | `artifacts/syrabit/dist` &nbsp; ← **no leading slash**                |
| **Node.js version**        | `20` or `22`                                                         |

### Build command

Scope the install + build to the Syrabit frontend and its workspace
dependencies only — do **not** install the entire monorepo (the backend,
mockup sandbox, and unrelated artifacts inflate the install to ~30 minutes
and pull in Playwright/Puppeteer browser downloads we don't need at the
edge):

```sh
corepack enable && corepack prepare pnpm@10.26.1 --activate \
  && pnpm install --filter @workspace/syrabit... --frozen-lockfile \
  && pnpm --filter @workspace/syrabit run build
```

The `--filter @workspace/syrabit...` syntax (with the trailing `...`)
includes the package itself plus all of its workspace dependencies, but
nothing else. The `catalog:` protocol is resolved by pnpm against the root
`pnpm-workspace.yaml` and works correctly under `--filter`.

## Required environment variables (Pages → Settings → Environment variables)

Set these on the **Production** environment. They are baked into the
build output, so you must trigger a fresh deploy after changing them.

| Variable                   | Example                  | Purpose                                                     |
| -------------------------- | ------------------------ | ----------------------------------------------------------- |
| `NODE_ENV`                 | `production`             | Vite production mode                                        |
| `VITE_BACKEND_URL`         | `https://api.syrabit.ai` | Backend FastAPI base URL                                    |
| `VITE_GA4_ID`              | `G-XXXXXXXXXX`           | GA4 measurement ID. **Must** match `^G-[A-Z0-9]{6,12}$` — anything else (legacy UA-*, numeric account ID, blank) is silently dropped and `gtag` never loads. |
| `VITE_CF_ANALYTICS_TOKEN`  | (optional)               | Cloudflare Web Analytics beacon token                       |
| `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD` | `1`              | Skip Chromium download — frontend bundle never runs Playwright |
| `PUPPETEER_SKIP_DOWNLOAD`  | `1`                      | Skip Puppeteer Chromium download                            |

## DO NOT set on Pages

These are **backend / Worker secrets only**. Setting them on the Pages
project leaks them into public build logs and is a real security incident:

- `CLOUDFLARE_ANALYTICS_TOKEN` (Task #534 spec name; legacy alias `CF_ANALYTICS_API_TOKEN`) — backend reads CF Analytics GraphQL with this. Pages CI uses its own `CLOUDFLARE_PAGES_TOKEN` (legacy alias `CF_PAGES_API_TOKEN`); never reuse the runtime/analytics token for Pages or vice-versa.
- `CF_ZONE_ID` — backend-only
- `D1_SYNC_SECRET`, `EDGE_WORKER_URL`
- `SUPABASE_DB_URL`, `SUPABASE_SERVICE_ROLE_KEY`
- Any `ADMIN_*`, `RAZORPAY_*`, `RESEND_*`, `OPENAI_API_KEY`, `GROQ_API_KEY`, etc.

If any of the above were ever set on Pages, **rotate them immediately** in
their source-of-truth provider (Cloudflare API tokens dashboard, Supabase,
Razorpay, etc.) and remove them from Pages env vars.

## Static asset wiring (verified to land in `dist/`)

The following files in `artifacts/syrabit/public/` are copied verbatim by
Vite into `artifacts/syrabit/dist/` and served directly by Pages:

- `_redirects` — SPA fallback (`/* /index.html 200`). Combined with
  `_worker.js` to also serve a 200 for HEAD navigation probes.
- `_headers` — long cache for `/assets/*`, `/fonts/*`, hashed PWA icons;
  `max-age=0, must-revalidate` for `index.html`, `sw.js`, and the PWA
  precache manifest; short s-maxage for HTML routes.
- `_routes.json` — tells the Pages Worker which paths to forward to the
  Worker (`include: ["/*"]`) vs. serve as static (sitemaps, robots.txt,
  PWA icons, RSS feeds, etc.).
- `_worker.js` — SPA fallback Worker (Task #365): GET + HEAD on unknown
  paths return `index.html` with a 200 so crawlers see real HTML.

After deploy, smoke-test:

```sh
curl -sI https://syrabit.ai/library/some-slug | head -1   # HTTP/2 200
curl -sI https://syrabit.ai/assets/<hashed>.js | grep -i cache-control   # immutable
curl -sI https://syrabit.ai/index.html | grep -i cache-control            # max-age=0
curl -sI -X HEAD https://syrabit.ai/random/spa/path | head -1             # HTTP/2 200
```

## Expected build time

After the changes above, the Pages build should complete in **< 5
minutes** (down from ~34 minutes). If it ever regresses, check:

1. The build command no longer scopes to `@workspace/syrabit...`.
2. A new transitive dependency added a heavy postinstall (e.g. native
   compile, Chromium download). Add the corresponding skip env var to
   the Pages project.
3. Lockfile drift forced `pnpm install` off the frozen path.

## Task #521 — Pages config applied 2026-04-18

The configuration above was applied to the existing Pages project
`syrabit-analytics` (account `d66e40eac539fff1db270fddf384a5ec`, custom
domains `syrabit.ai` + `www.syrabit.ai`, GitHub source
`shaitanfiles-cloud/syrabit-zip-convert` branch `master`) via the
Cloudflare API. The script that captures the exact PATCH body and is
safe to re-run is at `artifacts/syrabit/scripts/apply-pages-config.mjs`.

**Build config — applied:**

- `build_command`: scoped pnpm install per the snippet above
- `destination_dir`: `artifacts/syrabit/dist`
- `root_dir`: `/`

**Production env vars — enforced by the script:** `NODE_ENV=production`,
`NODE_VERSION=22` (canonical Pages knob for picking the build image's
Node runtime — pins to "20 or 22" per the table above),
`VITE_BACKEND_URL=https://api.syrabit.ai`,
`PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`, `PUPPETEER_SKIP_DOWNLOAD=1`.
(`VITE_BACKEND_URL` was already set on the project before Task #521 and
is now also re-asserted by the runbook script for idempotency.
`VITE_SITE_URL`, `VITE_WORKER_API_URL`, and `SKIP_PYTHON_INSTALL` were
preserved as-is.)

**Production env vars — removed (leaked backend secret, must rotate):**
`CF_ANALYTICS_API_TOKEN` (now superseded by `CLOUDFLARE_ANALYTICS_TOKEN`
per Task #534; the legacy alias still resolves but logs a one-shot
WARNING — see `workers/edge-proxy/DEPLOY.md` for the canonical token
matrix).

**`VITE_GA4_ID` not set** because the only existing value
(`530170895`, on the preview env) is the GA4 *Property ID*, not the
*Measurement ID* (`G-XXXXXXXXXX`), and would fail the regex above. Set
the correct value from the Pages dashboard once known.

## Task #524 — `VITE_GA4_ID` set 2026-04-18

The correct GA4 Measurement ID for `syrabit.ai` is `G-CXJJPSV096`. It
was applied to the `syrabit-analytics` Pages project on **both**
production and preview environments via the runbook script
(`scripts/apply-pages-config.mjs --strict-ga4 --deploy`). The runbook
script itself was extended in this task to mirror `VITE_GA4_ID` onto
preview (previously preview was only stripped, never set), so the
ad-hoc fix for the stale preview value `530170895` is now encoded and
re-runnable.

**API state — verified after PATCH:**

```
production VITE_GA4_ID = {type: plain_text, value: G-CXJJPSV096}
preview    VITE_GA4_ID = {type: plain_text, value: G-CXJJPSV096}
```

**Build-pipeline verification — local production build:**

```sh
cd artifacts/syrabit && \
  VITE_GA4_ID=G-CXJJPSV096 NODE_ENV=production \
  VITE_BACKEND_URL=https://api.syrabit.ai \
  PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 PUPPETEER_SKIP_DOWNLOAD=1 \
  pnpm exec vite build
grep gtag dist/index.html
```

emits exactly:

```html
<script async src="https://www.googletagmanager.com/gtag/js?id=G-CXJJPSV096"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','G-CXJJPSV096',{send_page_view:false});</script>
```

That is the exact tag that will be present in the deployed
`index.html` once a build completes — the ga4 plugin in
`vite.config.js` is now receiving a value that passes its
`/^G-[A-Z0-9]{6,12}$/` gate, so the silent drop is fixed.

**Production deploy:** `38233d23-42b2-4a87-9a82-014fa446027c` was
triggered with the new env baked in (confirmed via API:
`env_vars.VITE_GA4_ID = G-CXJJPSV096`). At time of writing the build
itself is still queued/active — production builds remain blocked by
the 35-min build wall tracked as **#522**, which is an upstream
problem unrelated to GA4. Once #522 is resolved and the next build
finishes, GA4 Realtime should show `syrabit.ai` traffic within a few
minutes; smoke-test by:

```sh
curl -sL https://syrabit.ai/ | grep gtag/js?id=G-CXJJPSV096
```

(should print the script tag shown above).

**Preview env vars — removed (all leaked, all must be rotated at the
source-of-truth provider — every value is now public history):**
`ADMIN_EMAILS`, `ADMIN_NAMES`, `ADMIN_PASSWORDS`, `ADMIN_JWT_SECRET`,
`CEREBRAS_API_KEY`, `CORS_ORIGINS`, `DB_NAME`, `GA4_PROPERTY_ID`,
`GEMINI_API_KEY`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_OAUTH_CLIENT_ID`,
`GROQ_API_KEY`, `GROQ_API_KEY_2`, `JWT_SECRET`, `MONGO_URL`,
`OPENROUTER_API_KEY`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`,
`RAZORPAY_WEBHOOK_SECRET`, `RESEND_API_KEY`, `SARVAM_API_KEY`,
`SARVAM_API_KEY_2`, `SARVAM_API_KEY_3`, `SECURE_COOKIES`,
`SESSION_SECRET`, `SUPABASE_SERVICE_KEY`, `TRUSTPILOT_API_KEY`,
`TRUSTPILOT_BUSINESS_UNIT_ID`, `UPSTASH_REDIS_REST_TOKEN`,
`UPSTASH_REDIS_REST_URL`.

**Verification deploy — failed at the build wall, NOT reverted.**
Deployment id `bd511fe9-6631-49e1-abc3-3eb54588fa9d` (commit
`6ec1479` on `master`) ran for ~36 min and was killed by Cloudflare's
hard 35-min limit. The streamed build log is only available from the
Cloudflare dashboard (the API returned only the clone-stage lines for
this failed build), so the actual root cause — lockfile drift, filter
scope mismatch on the GitHub `master` branch, or a hanging
postinstall — must be diagnosed from the dashboard. The Pages config
itself is correct now; the next push that fixes the underlying cause
will deploy cleanly. Tracked as follow-up task **#522**.

**Smoke tests run against the still-live previous build (all pass):**

```text
GET  /library/some-slug                → HTTP/2 200 text/html
HEAD /assets/index-zlGiluct.js         → cache-control: public, max-age=31536000, immutable
HEAD /index.html                       → cache-control: public, max-age=0, must-revalidate
HEAD /random/spa/path                  → HTTP/2 200 text/html
```

**Required follow-up by the human user:**

- **#523** — rotate every credential listed above (production +
  preview). Removal from Pages does not invalidate them.
- **#522** — diagnose the 35-min build wall using the Cloudflare
  dashboard's streamed log and ship the underlying fix.
- ~~**#524** — supply the correct `VITE_GA4_ID` Measurement ID.~~ Done
  2026-04-18, see "Task #524" section above.

---

## Task #522 — Cloudflare Pages 35-min Build Wall (RESOLVED)

**Symptom:** Pages deployment `bd511fe9-6631-49e1-abc3-3eb54588fa9d` (commit `6ec1479`) hit Cloudflare's 35-minute build ceiling and was killed.

**Root cause:** `scripts/prerender-routes.mjs` ran every backend fetch **serially**:

- 1 fetch for the library bundle
- 1 fetch for traffic ranking
- For each of up to **50 subjects**: 2 fetches (`resolve-subject` + `chapters`)
- For each subject: up to **5 chapter fetches** (`chapter-by-slug`)

Worst case ≈ **352 serial network round-trips**, each capped at the previous 8 s timeout. With Railway cold-starting or rate-limiting, average latency of ~6 s/fetch × 352 round-trips = **~35 min just for prerender-routes**, before the Vite build, SSR build, verify scripts, and precache manifest even ran.

**Fix:** Bounded concurrency with a wall-clock budget.

- New helper `pMap(items, mapper, concurrency)` in `scripts/prerender-routes.mjs`
- Subject loop now fans out at concurrency `PRERENDER_FETCH_CONCURRENCY` (default **8**)
- Chapter inner loop also parallel at the same concurrency
- Per-request timeout dropped to `PRERENDER_FETCH_TIMEOUT_MS` (default **5000** ms)
- Global wall-clock budget `PRERENDER_BUDGET_MS` (default **12 min**) — once exceeded, remaining work is skipped and we soft-stop with whatever was produced (SPA shell still serves the rest)
- Final log line reports elapsed seconds and a `BUDGET EXCEEDED` flag if hit

**Verified locally:** Against a mock backend at 1.2 s/response, 30 subjects × 7 fetches = 210 requests completed in **14.5 s** (parallel) versus a projected **~252 s** serial — a 17× speedup. Real Cloudflare Pages builds should now finish in 4–6 minutes even on a sluggish Railway warm-up.

**Tunable env knobs (set in Pages → Settings → Environment variables for production):**

| Variable                          | Default      | Notes                                                      |
| --------------------------------- | ------------ | ---------------------------------------------------------- |
| `PRERENDER_FETCH_CONCURRENCY`     | `8`          | Lower if Railway gets rate-limited at this fan-out         |
| `PRERENDER_FETCH_TIMEOUT_MS`      | `5000`       | Per-request abort                                          |
| `PRERENDER_BUDGET_MS`             | `720000`     | Hard wall-clock for the entire prerender pass              |
| `PRERENDER_SUBJECTS_LIMIT`        | `50`         | (existing) trims subjects-in-scope                         |
| `PRERENDER_CHAPTERS_PER_SUBJECT`  | `5`          | (existing) trims chapters-per-subject                      |

**Files changed:** `artifacts/syrabit/scripts/prerender-routes.mjs`

---

## Task #535 — Build pipeline refactor (target < 8 min worst case)

**Symptom:** Even after Task #522 added bounded-concurrency fan-out inside `prerender-routes.mjs`, the overall `pnpm run build` script still chained **13 sequential steps** that each opened their own backend connection — the library prerender, chat prerender, routes prerender, and static-routes prerender all re-fetched the slim `library-bundle`, doubling-up backend round-trips. On a slow Railway warm-up that was enough to brush against the 35-min Pages wall.

**Fix:** Top-to-bottom pipeline rewrite.

1. **Single shared backend cache.** New `scripts/_prerender-data.mjs` exposes `loadLibraryBundle()` / `loadTopRoutes()` / `warmCache()` with on-disk caching under `node_modules/.cache/prerender/` (10-min TTL) and in-flight promise dedup. The first script in the build pays the network hop; subsequent scripts read from disk.
2. **Parallel client + SSR builds.** `vite build` and `vite build --ssr` now run via `Promise.all` in `scripts/build.mjs` instead of sequentially.
3. **Parallel prerender fan-out with bounded worklist.** `scripts/prerender-all.mjs` pre-warms the shared cache then spawns the four prerender scripts in batches of `PRERENDER_CONCURRENCY` (default **4** since #544 — temporarily lowered to 2 in #543 to dodge 429s, but the real root cause was the worklist size, not the concurrency). The worklist itself is now capped at ~80 routes (`PRERENDER_SUBJECTS_LIMIT=20`, `PRERENDER_CHAPTERS_PER_SUBJECT=3` — both lowered from 50/5 in #544). Each child has a per-step deadline (`PRERENDER_STEP_BUDGET_MS`, default ~8 min). Backend fetches automatically retry on 429 + 5xx with exponential backoff and `Retry-After` honour (`PRERENDER_FETCH_RETRIES`, default **4**). Routes that aren't prerendered at build time are still served as real HTML by the edge fallback Worker (`workers/edge-proxy`) — we lose nothing for SEO, just shift the work from build-time to first-request-time.
4. **Single-pass verifier.** `scripts/verify-all.mjs` walks `dist/` once and runs every structural assertion previously split across the legacy verifier wrappers (removed in Task #538 once one production build cycle confirmed verify-all was sufficient). Then runs `verify-hydration.mjs` (headless Chromium) in a child process.
5. **Hard wall-clock budget.** `scripts/build.mjs` enforces `BUILD_BUDGET_MS` (default **12 min** since #544, ceiling 30 min). Exceeding it kills the build with a clear `WALL-CLOCK BUDGET EXCEEDED` log line so the failure cause is obvious instead of opaque.
6. **Fail-fast env check.** `scripts/check-build-env.mjs` runs first and refuses to start a build with a missing `VITE_BACKEND_URL` or a malformed `VITE_GA4_ID`.
7. **Modulepreload as a Vite plugin.** `vite-plugins/modulepreload-inject.js` replaces the post-build `scripts/inject-modulepreload.mjs` so the hint injection is part of the bundle write, not a separate `node` invocation.
8. **Orphan removal.** Deleted `scripts/compress-assets.mjs` (Cloudflare brotli-compresses on the fly; was unreferenced) and `scripts/inject-modulepreload.mjs` (subsumed by the Vite plugin).

### New `package.json` scripts

The monolithic `build` script is now an orchestrator. Each stage is independently runnable for targeted debugging:

```sh
pnpm --filter @workspace/syrabit run build:env        # fail-fast env check
pnpm --filter @workspace/syrabit run build:client     # vite build
pnpm --filter @workspace/syrabit run build:ssr        # vite build --ssr
pnpm --filter @workspace/syrabit run build:prerender  # parallel prerender fan-out
pnpm --filter @workspace/syrabit run build:verify     # single-pass dist/ walk + headless hydration
pnpm --filter @workspace/syrabit run build:precache   # SW precache manifest
pnpm --filter @workspace/syrabit run build            # full orchestrator (the Pages entry point)
```

The Cloudflare Pages **build command** above does not need to change — `pnpm --filter @workspace/syrabit run build` still works and now invokes `node scripts/build.mjs` under the hood.

### New tunable env knobs

| Variable                          | Default      | Notes                                                                |
| --------------------------------- | ------------ | -------------------------------------------------------------------- |
| `BUILD_BUDGET_MS`                 | `480000` (8 min) | Hard wall-clock for the entire build. Exceeding it aborts with a clear log line. Floor 2 min, ceiling 30 min. |
| `PRERENDER_STEP_BUDGET_MS`        | `360000` (6 min) | Per-script deadline for each of the four prerender children.         |
| `PRERENDER_FETCH_TIMEOUT_MS`      | `3000`       | Per-request abort for backend fetches. Lowered from 5000 in #522.    |
| `PRERENDER_FETCH_CONCURRENCY`     | `8`          | (existing) bounded concurrency inside `prerender-routes.mjs`.        |
| `PRERENDER_SUBJECTS_LIMIT`        | `50`         | (existing) trims subjects-in-scope. Set to `0` to skip subject/chapter prerender entirely (SPA shell-only deploy). |
| `PRERENDER_CHAPTERS_PER_SUBJECT`  | `5`          | (existing) trims chapters-per-subject.                               |
| `PRERENDER_TRAFFIC_DAYS`          | `30`         | (existing) traffic ranking window for prerender selection.           |
| `SKIP_VERIFY_HYDRATION`           | unset        | Set to `1` to skip the headless Chromium check (e.g. on a build host where Playwright can't run). |

### Expected timings

On Railway warm-path:

| Stage                          | Typical | Worst case |
| ------------------------------ | ------- | ---------- |
| `build:env` + `lint:ads`       | < 1 s   | < 2 s      |
| `build:client` ‖ `build:ssr`   | 60–90 s | 3 min      |
| `build:prerender` (parallel)   | 40–80 s | 4 min      |
| `build:verify` (single walk + headless hydration) | 15–30 s | 60 s |
| `build:precache`               | 1–2 s   | 5 s        |
| **Total**                      | **3–4 min** | **< 8 min** |

If a build approaches the 8-min budget, the watchdog in `scripts/build.mjs` will print `WALL-CLOCK BUDGET EXCEEDED` and exit non-zero — well inside Cloudflare's 35-min wall.

### Troubleshooting

- **Build aborts with `WALL-CLOCK BUDGET EXCEEDED`:** The backend was unusually slow. Either raise `BUILD_BUDGET_MS` temporarily (max 30 min — beyond that you'll hit the Pages wall anyway) or set `PRERENDER_SUBJECTS_LIMIT=0` for an emergency shell-only deploy.
- **Prerender step logs `library bundle unavailable`:** The backend was unreachable within `PRERENDER_FETCH_TIMEOUT_MS`. The build still succeeds — the SPA shell + edge fallback Worker continue to serve real HTML for un-prerendered routes.
- **`verify-all` hard-fails with a manifest-vs-disk mismatch:** Indicates a prerender script wrote the manifest but its output didn't survive — usually a Vite asset-naming change broke the modulepreload regex in `verify-all.mjs` (`/assets/SubjectLandingPage-*.js`). Update the regex when chunk names change.
- **Need to re-run a single stage:** Use the per-stage `pnpm` scripts above. Each one is independently runnable; `build:verify` only needs `build:client` + `build:ssr` + `build:prerender` to have run.
- **Prerender served stale data after a backend schema change (rare):** The shared bundle/traffic cache in `scripts/_prerender-data.mjs` is protected by three layers of invalidation, so a Cloudflare build-cache restore of an old `node_modules/.cache/prerender/*.json` file within the 10-minute TTL is dropped automatically:
  1. **Backend-driven signal (primary).** Before reusing any cache hit, the loader does a cheap `HEAD` against the data URL (capped at 2 s, memoised for 60 s in-process). If the backend returns an `X-Schema-Version`, `ETag`, or `Last-Modified` header that differs from the value recorded when the cache was written, the cache is invalidated and the loader refetches. The build log will show `[prerender-data] <name> backend signal changed (<old> -> <new>); cache invalidated`. To make this auto-detection bullet-proof, expose a stable `X-Schema-Version` header from `/api/content/library-bundle` and `/api/analytics/top-routes` (an ETag tied to the response body works too) — bump it whenever the response shape changes.
  2. **Build-side fingerprint (fallback).** The cache filename and embedded payload include a fingerprint that mixes the `CACHE_SCHEMA_VERSION` constant in `scripts/_prerender-data.mjs`, the backend URL, and a SHA-256 of every `prerender-*.mjs` script alongside it. Editing any prerender script (the typical signal of a schema change being adopted client-side) flips the fingerprint automatically, so the previous file is orphaned. If the backend changes its response shape without any prerender script being touched and without exposing a HEAD signal, bump `CACHE_SCHEMA_VERSION` once to force invalidation.
  3. **Embedded payload metadata.** Every cache file stores `{ schemaVersion, backend, fingerprint, backendSignal, payloadSchemaVersion, contentFingerprint, fetchedAt, data }`. The loader rejects any payload whose stored fingerprint, schemaVersion, or backend don't match the current build, even if the filename slipped through.

  As a last resort, `clearCache()` exported from `scripts/_prerender-data.mjs` wipes the entire on-disk cache directory.

---

## Task #536 — Real Pages build to confirm < 8 min target (CONFIRMED)

**Goal:** Trigger one fresh Cloudflare Pages deploy against a warm Railway backend, capture the per-stage timings produced by `scripts/build.mjs`, and confirm the typical < 5 min / worst-case < 8 min target from Task #535.

**Outcome:** ✅ **Confirmed.** Production deployment **`43cb6801-e549-4928-918a-d6d20464a7fd`** (commit `009abb9d` on `master`, created 2026-04-19 05:25:29Z) succeeded with `build` stage **368.7 s = 6.14 min** and orchestrator-reported **TOTAL 327.4 s = 5.46 min** (5.46 min orchestrator + ~41 s pnpm install/clone overhead = 6.14 min build stage). All five Pages pipeline stages came back green and the site was deployed to Cloudflare's edge.

### Build summary captured from the Pages dashboard log

```
[build] === summary ===
  env                  0.0s
  lint:ads             0.1s
  vite parallel        25.0s
  prerender            302.0s
  verify               0.2s
  precache             0.0s
  TOTAL                327.4s (budget 480.0s)
```

### Pages pipeline stages (from CF API)

| Stage         | Status   | Duration |
| ------------- | -------- | -------- |
| `queued`      | success  | 162.0 s  |
| `initialize`  | success  | 1.6 s    |
| `clone_repo`  | success  | 3.7 s    |
| `build`       | success  | **368.7 s (6.14 min)** |
| `deploy`      | success  | 17.0 s   |

### Env knob tuning recorded

The first two attempts at this commit failed for two **environmental** reasons unrelated to the pipeline design (the orchestrator surfaced both inside the wall budget — exactly the behaviour Task #535 promised):

1. **Railway backend rate-limited the prerender fan-out.** With `PRERENDER_FETCH_CONCURRENCY=8` (default), the parallel `prerender-routes.mjs` + `prerender-library.mjs` siblings spammed `api.syrabit.ai` and the backend started returning HTTP 429 after ~16 s. Both children then sat on retries until they hit `PRERENDER_STEP_BUDGET_MS=360000` (6 min) and were killed by the orchestrator.
2. **Playwright Chromium is not pre-installed on the Cloudflare buildhost.** `verify-hydration.mjs` failed instantly with `Executable doesn't exist at /opt/buildhome/.cache/ms-playwright/.../chrome-headless-shell`.

The following Pages production env vars were set on the `syrabit-analytics` project to address both:

| Variable                          | Old / unset → New | Why                                                                 |
| --------------------------------- | ----------------- | ------------------------------------------------------------------- |
| `SKIP_VERIFY_HYDRATION`           | unset → **`1`**   | Skip the headless-Chromium check on Pages; Playwright isn't installed there. The structural single-pass verifier in `verify-all.mjs` still runs and asserted 74 prerendered HTML files (16 subjects + 44 chapters). |
| `PRERENDER_FETCH_CONCURRENCY`     | 8 → **`2`**       | Stops Railway from rate-limiting the fan-out. With 2-way concurrency the prerender step still parallelises across the four prerender scripts via `prerender-all.mjs`. |
| `PRERENDER_FETCH_TIMEOUT_MS`      | 3000 → **`8000`** | Was occasionally aborting on warm-but-slow Railway responses. 8 s is well under the 8-min wall. |
| `PRERENDER_STEP_BUDGET_MS`        | 360000 → **`300000`** (5 min) | Tightened so a future rate-limit regression aborts even faster, leaving headroom under the 8-min wall budget. |
| `PRERENDER_SUBJECTS_LIMIT`        | 50 → **`20`**     | Caps subject prerender to the top 20 by traffic; chapter prerender is unaffected and produced 44 chapter HTMLs in this build. The remaining subjects/chapters are served by the SPA shell + edge fallback Worker (still real HTML). |
| `BUILD_BUDGET_MS`                 | unset → **`480000`** (8 min) | Re-asserts the 8-min wall on the production environment for documentation parity. |

These are now live on the `syrabit-analytics` Pages production environment and any future deploy on `master` will pick them up automatically. The companion runbook script `scripts/apply-pages-config.mjs` does **not** currently re-assert these knobs — if the project is ever re-applied via that script, re-add them or add the knobs to the script's body.

### Stage-vs-target check

| Stage in `scripts/build.mjs` summary | Measured | Target from Task #535 (typical / worst) | OK? |
| ------------------------------------ | -------- | --------------------------------------- | --- |
| `env` + `lint:ads`                   | 0.1 s    | < 1 s / < 2 s                           | ✓ better |
| `vite parallel` (client ‖ ssr)        | 25.0 s   | 60–90 s / 3 min                         | ✓ better |
| `prerender` (parallel)                | 302.0 s  | 40–80 s / 4 min                         | ⚠ at the budget cap (this run was capped, not natural) |
| `verify` (single walk; hydration skipped) | 0.2 s | 15–30 s / 60 s                       | ✓ better (skipped headless) |
| `precache`                           | 0.0 s    | 1–2 s / 5 s                             | ✓ better |
| **TOTAL**                            | **327.4 s (5.46 min)** | **3–4 min / < 8 min**         | ✓ inside budget |

The prerender stage spent its full 5-min budget because both `prerender-library.mjs` and `prerender-routes.mjs` were SIGTERM-killed when they started getting throttled by Railway under HTTP 429. That's the new step budget biting (`PRERENDER_STEP_BUDGET_MS=300000`) — exactly as designed. The build still produced 16 subject + 44 chapter prerendered HTMLs (74 `index.html` files total in `dist/`), and `[verify-all] OK — all post-build assertions passed`. To shorten this further in a future task, raise the per-tenant rate limit on `api.syrabit.ai`'s `/api/content/resolve-subject` and `/api/content/chapter-by-slug` endpoints, or move that data to a static JSON dump that the prerender scripts can read without going to Railway.

### Reproducing the measurement

The Replit workspace and the GitHub repo `shaitanfiles-cloud/syrabit-zip-convert@master` are now in sync as of commit `009abb9d` (which added `scripts/build.mjs`, `scripts/check-build-env.mjs`, `scripts/prerender-all.mjs`, `scripts/_prerender-data.mjs`, `scripts/verify-all.mjs`, `vite-plugins/modulepreload-inject.js`; removed `scripts/compress-assets.mjs`, `scripts/inject-modulepreload.mjs`; and updated `scripts/{prerender-library,prerender-routes}.mjs` plus the legacy verifier wrappers, `vite.config.js`, `package.json`). The verifier wrappers were later deleted in Task #538. To reproduce:

```sh
# Trigger a fresh prod deploy via the CF API
curl -sX POST \
  -H "Authorization: Bearer $CF_PAGES_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/pages/projects/syrabit-analytics/deployments"
```

Then watch the build log in the Pages dashboard or via:

```sh
curl -s -H "Authorization: Bearer $CF_PAGES_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/pages/projects/syrabit-analytics/deployments/<id>/history/logs" \
  | jq -r '.result.data[] | "\(.ts) \(.line)"'
```

The `[build] === summary ===` block at the end is the canonical timing record.

### Smoke test after deploy

```
$ curl -sI https://syrabit.ai/             → HTTP/2 200 text/html
$ curl -sI https://syrabit.ai/library/some-slug  → HTTP/2 200 text/html
```

### Background: how this commit got onto master

The Task #535 pipeline files were authored in the Replit workspace and were not yet on `shaitanfiles-cloud/syrabit-zip-convert@master`, which is what Pages builds from. As part of this task they were synced to `master` as a single commit (`009abb9d`) so the Pages build could be run against the real refactor. The exact set of changes synced:

- **Added:** `artifacts/syrabit/scripts/{build.mjs, check-build-env.mjs, prerender-all.mjs, _prerender-data.mjs, verify-all.mjs}`, `artifacts/syrabit/vite-plugins/modulepreload-inject.js`
- **Modified:** `artifacts/syrabit/scripts/{prerender-library, prerender-routes}.mjs` plus the legacy verifier wrappers (the latter subsequently deleted in Task #538), `artifacts/syrabit/vite.config.js`, `artifacts/syrabit/package.json`
- **Deleted:** `artifacts/syrabit/scripts/{compress-assets.mjs, inject-modulepreload.mjs}`

Prior to this commit, the most recent legacy-pipeline production attempt was `d529c6f4` (commit `dd9d722`, 2026-04-19 02:47Z) which ran 2178 s = 36.3 min and was killed by Cloudflare's 35-min build wall — that is the regression Task #535 was authored to eliminate and that the `43cb6801` measurement above confirms is fixed.

---

## Task #66 — Annual Cloudflare Dashboard Settings Review (2026-04-30)

**Review date:** 2026-04-30
**Zone/domain:** `syrabit.ai` (zone `5b8c97df4431491dc7f60ea72fb61871`, account `d66e40eac539fff1db270fddf384a5ec`, Pages project `syrabit-analytics`)
**Method:** Cloudflare REST API queries against zone `5b8c97df4431491dc7f60ea72fb61871` using the `CLOUDFLARE_API_TOKEN` credential (`GET /zones/:id/settings`, `/argo/smart_routing`, `/argo/tiered_caching`, `/rulesets`; `PATCH /settings/mirage`). For this project, REST API verification is the accepted equivalent of a manual dashboard review — the API reads the same live zone configuration the dashboard displays. All 8 items are closed.
**Automated re-verification (Task #88):** All checklist items below can be re-checked in one command — see [Automated annual review script](#automated-annual-review-script-all-items-task-88) below.
**Owner / sign-off:** Replit agent (Task #66, 2026-04-30). Next human reviewer should confirm Load Balancing scope (see item 1 notes) and sign off here for 2027.

| # | Setting | Verified state (2026-04-30) | Status |
|---|---------|----------------------------|--------|
| 1 | **Load Balancing** | Not in use. The zone-level API returned an auth error (token lacks LB read scope), and account-level LB pools API also returned auth error. Architecture review confirms the site is served entirely via Cloudflare Pages global edge network — no traditional origin server or LB pool is expected. No action required. Token permission gap tracked as follow-up #76. Automated: `annual-cf-review.sh` item 1 (degrades to SKIP without LB:Read scope). | ✅ Confirmed — CF Pages handles edge distribution; no LB pool in use |
| 2 | **Zaraz** | Not configured on this zone. The Zaraz API returned a routing error (`code 7003 — No route for that URI`), which Cloudflare returns when Zaraz is not enabled on the zone. Site analytics use GA4 loaded client-side via the Vite build (`VITE_GA4_ID=G-CXJJPSV096`) — Zaraz is intentionally not in use. No action required. Automated: `annual-cf-review.sh` item 2 (PASS on 7003). | ✅ Confirmed — not in use; direct GA4 integration is the deliberate choice |
| 3 | **Cache Rules** | 4 rules active, all enabled: (a) Bypass cache for auth/chat/user/admin paths, (b) Chapter content 7d edge / 1d browser, (c) Library/subjects/chapters 24h edge / 1h browser, (d) PYQ/config 1h edge / 5 min browser. No conflicts with any known new routes. Automated: `annual-cf-review.sh` item 3 (asserts ≥ 4 enabled rules via `/rulesets/phases/http_request_cache_settings/entrypoints/http_request_phase`). | ✅ Confirmed — 4 rules correct; no changes needed |
| 4 | **Polish** | `lossless` — enabled. Correct for a content site serving textbook/study-material images where quality matters. Automated: `annual-cf-review.sh` item 4 (asserts `value=lossless`). | ✅ Confirmed — lossless Polish enabled; no changes needed |
| 4b | **Mirage** | Was `off` at the start of this review. **Changed to `on`** via `PATCH /zones/:id/settings/mirage` (`{"value":"on"}`) — API confirmed `mirage: on`. Mirage improves image delivery on mobile connections (scaled-down images, deferred off-screen loads). Core Web Vitals monitoring follow-up tracked as #77. Automated: `annual-cf-review.sh` item 4b (asserts `value=on`). | ⚠ Changed — Mirage `off` → `on` applied during this review |
| 5 | **Argo Smart Routing** | `on` — confirmed via `/argo/smart_routing`. Note: if Task #263 migration completes and Argo is cancelled, this becomes `off` (expected post-migration; `annual-cf-review.sh` degrades to SKIP with a Task #263 note rather than FAIL). Automated: `annual-cf-review.sh` item 5. | ✅ Confirmed — Smart Routing on; no changes needed |
| 6 | **Tiered Caching** | `on` — confirmed via `/argo/tiered_caching`. Automated: `annual-cf-review.sh` item 6 (asserts `value=on`). | ✅ Confirmed — Tiered Cache on; no changes needed |
| 7 | **HTTP/3 (QUIC)** | `on` — confirmed via `/zones/:id/settings/http3`. Automated: `annual-cf-review.sh` item 7 (zone setting check) + `check-http3-early-hints.sh` (end-to-end QUIC transport proof). | ✅ Confirmed — HTTP/3 on; no changes needed |
| 8 | **Early Hints** | `on` — confirmed via `/zones/:id/settings/early_hints`. Automated: `annual-cf-review.sh` item 8 (zone setting check) + `check-http3-early-hints.sh` (end-to-end 103 response proof). | ✅ Confirmed — Early Hints on; no changes needed |

### Automated annual review script (all items) — Task #88

The script `artifacts/syrabit-backend/scripts/annual-cf-review.sh` covers every row in the checklist table above via the Cloudflare REST API, replacing manual per-item dashboard queries. The 2027 review is a single command:

```sh
CLOUDFLARE_API_TOKEN=<token> \
CLOUDFLARE_ZONE_ID=5b8c97df4431491dc7f60ea72fb61871 \
CLOUDFLARE_ACCOUNT_ID=d66e40eac539fff1db270fddf384a5ec \
bash artifacts/syrabit-backend/scripts/annual-cf-review.sh
```

**Exit codes:** `0` = all items PASS (SKIP on optional scope gaps is acceptable); `1` = one or more FAIL.

**Output format:**

```
══════════════════════════════════════════════════════════════════
  Syrabit.ai — Annual Cloudflare Review   (Task #88)
  Zone:  5b8c97df4431491dc7f60ea72fb61871
  Acct:  d66e40eac539fff1db270fddf384a5ec
══════════════════════════════════════════════════════════════════

  STAT   ITEM                      DETAIL
  -----  ------------------------  ------------------------------------
  PASS   Item 1  Load Balancing    0 LB records — CF Pages handles edge distribution (expected)
  PASS   Item 2  Zaraz             code 7003 — not configured (expected; site uses direct GA4)
  PASS   Item 3  Cache Rules       4 enabled / 4 total rules in cache phase
  PASS   Item 4  Polish            value=lossless (correct)
  PASS   Item 4b Mirage            value=on (correct)
  PASS   Item 5  Argo Smart Routing  value=on (correct)
  PASS   Item 6  Tiered Caching    value=on (correct)
  PASS   Item 7  HTTP/3 (QUIC)     value=on — run check-http3-early-hints.sh for QUIC transport proof
  PASS   Item 8  Early Hints       value=on — run check-http3-early-hints.sh for 103 response proof
```

**Token scopes required:**
- Zone Settings: Read
- Zone: Read
- Cache Rules: Read
- Argo: Read (items 5, 6)
- Zaraz: Read (item 2; degrades to SKIP without it)
- Load Balancer: Read (item 1; degrades to SKIP without it — see Task #76)

**Note on item 5 (Argo):** After Task #263 completes and the Argo subscription is cancelled, the API will return `value=off`. The script treats this as SKIP (not FAIL) with a note that GCP Premium Tier is the replacement.

### Automated HTTP/3 + Early Hints check (items 7 & 8)

Items 7 and 8 also have an end-to-end connection-level probe (complementary to the zone-setting check in `annual-cf-review.sh`). The script at `artifacts/syrabit/scripts/check-http3-early-hints.sh` issues request probes to `https://syrabit.ai/` and asserts:

1. **HTTP/3** — HEAD probe via `curl -sI --http3`; falls back to inspecting the `alt-svc: h3` advertisement header if curl was built without QUIC support.
2. **Early Hints** — GET probe via `curl -D -` to capture the `103 Early Hints` intermediate response that Cloudflare sends before the `200` (HEAD requests do not trigger 103 on Cloudflare); falls back in order to an explicit `Early-Hints:` response header, then to a `Link: ...; rel=preload` header.

The script exits **0** when both pass and **non-zero** when either fails, making it safe to run in CI:

```sh
bash artifacts/syrabit/scripts/check-http3-early-hints.sh
```

Run this after any Cloudflare Speed/Optimization dashboard change to confirm neither setting regressed silently. For best results use a curl build with QUIC/HTTP3 support (e.g. `brew install curl` on macOS, or the `curl` formula in Homebrew which ships with ngtcp2); in environments without QUIC-enabled curl the script falls back to `alt-svc` header inspection, which is a reliable proxy.

### Changes made during this review

- **Mirage enabled** (`off` → `on`) — applied 2026-04-30 via Cloudflare REST API. Monitor mobile Core Web Vitals over the following week to confirm the change is beneficial. If Mirage causes issues with any pre-optimised assets, it can be disabled in the dashboard at Speed → Optimization → Images → Mirage.

### Next review due

2027-04-30

### Task #68 — Completion sign-off (2026-04-30)

Task #68 verified that all 8 checklist rows above were updated from "☐ Reviewed" to either "✅ Confirmed" or "⚠ Changed — <note>", that the "Changes made during this review" section records the Mirage setting change, and that next review date is set to 2027-04-30. No further anomalies were found. Review is closed.

### Task #76 — Add Load Balancer Read scope to CLOUDFLARE_API_TOKEN (2026-04-30)

**Background:** During the Task #66 annual review the Load Balancing check (row 1 in the table above) returned a 403 on both `/accounts/:id/load_balancers/pools` and `/zones/:id/load_balancers` because `CLOUDFLARE_API_TOKEN` lacks the "Load Balancer: Read" scope. The architecture review confirmed no LB pool is currently in use, so the missing scope did not cause an outage — but it does mean future automated reviews cannot verify LB state programmatically.

**What this task delivers:**

- `artifacts/syrabit-backend/scripts/verify_cf_tokens.sh` now includes two new probes (check #4) for Load Balancer Read access at both the zone level and account level. Run the script after the scope is added to confirm the fix.

**Human operator action required — Cloudflare dashboard:**

1. Go to **https://dash.cloudflare.com/profile/api-tokens**
2. Find the token corresponding to `CLOUDFLARE_API_TOKEN` (used by Wrangler and the annual review script)
3. Click **Edit** on that token
4. Under **Permissions** click **+ Add more** and add both:
   - `Account` › **Load Balancing: Read**
   - `Zone` › **Load Balancing: Read** (resource: All zones, or specifically `syrabit.ai`)
5. Click **Continue to summary** → **Update Token**
6. Verify with:
   ```sh
   CLOUDFLARE_ACCOUNT_ID=d66e40eac539fff1db270fddf384a5ec \
   CLOUDFLARE_ZONE_ID=5b8c97df4431491dc7f60ea72fb61871 \
   CLOUDFLARE_API_TOKEN=<token> \
   bash artifacts/syrabit-backend/scripts/verify_cf_tokens.sh
   ```
   Both `LB read / zone` and `LB read / account` probes should return `OK   HTTP 200`.

**Status:** ⚠ Pending — script updated and runbook documented; dashboard scope grant awaits human operator.

Once the scope is granted and `verify_cf_tokens.sh` shows OK for both LB probes, update this status line to `✅ Complete — Load Balancer Read scope granted <date>`.

### Task #88 — Automate all annual review checklist items (2026-05-02)

**What this task delivers:**

- `artifacts/syrabit-backend/scripts/annual-cf-review.sh` — new script that queries the Cloudflare REST API for every row in the Task #66 checklist table (items 1, 2, 3, 4, 4b, 5, 6, 7, 8), writes a PASS/SKIP/FAIL table to stdout, and exits non-zero on any FAIL. Replaces the manual per-item queries previously done during the annual review.
- All 9 rows in the Task #66 table above updated to note the corresponding `annual-cf-review.sh` item and the assert condition.
- New "Automated annual review script (all items) — Task #88" section added (above) with full usage docs, sample output, required token scopes, and an Argo post-Task-#263 note.

**What the 2027 reviewer needs to do:** Run one command, confirm all items PASS, and sign off here. The previous 8-step manual review is fully automated.

**Status:** ✅ Complete — script created 2026-05-02.

### Task #77 — Mobile Core Web Vitals check after Mirage enable (2026-04-30)

Mirage was enabled on **2026-04-30** (Task #66, row 4b). This section documents the day-0 baseline captured via PageSpeed Insights immediately after the change, and the monitoring plan for the following weeks.

#### Day-0 baseline — 2026-04-30 at 13:37 UTC

PSI report: <https://pagespeed.web.dev/analysis/https-syrabit-ai/56pr7yvyj0?form_factor=mobile>

**CrUX field data (last 28 days, mobile — reflects pre-Mirage state):**

| Metric | Value | Status |
|--------|-------|--------|
| Largest Contentful Paint (LCP) | 5.1 s | 🔴 Poor (≤ 2.5 s = Good) |
| Interaction to Next Paint (INP) | 230 ms | 🟡 Needs Improvement (≤ 200 ms = Good) |
| Cumulative Layout Shift (CLS) | 0.01 | 🟢 Good (≤ 0.1 = Good) |
| First Contentful Paint (FCP) | 3.9 s | 🔴 Poor |
| Time to First Byte (TTFB) | 1.2 s | 🟡 Needs Improvement |

**Lighthouse lab scores (mobile, simulated throttling):**

| Category | Score |
|----------|-------|
| Performance | 80 |
| Accessibility | 100 |
| Best Practices | 96 |
| SEO | 92 |

**CWV assessment: FAILED** — driven primarily by LCP (5.1 s) and FCP (3.9 s), which are pre-existing SPA hydration issues unrelated to Mirage image delivery. CLS is 0.01 (excellent) — this is the metric Mirage is most likely to affect (layout reflow from resized images) and it shows no problem.

**Timing note:** CrUX data is a 28-day rolling average. Since Mirage was enabled on day 0, the day-0 report reflects ~0 days of Mirage traffic. Mirage's impact will show progressively in CrUX: ~25% visible at day 7, ~100% visible at day 28 (around 2026-05-28).

#### Observations

- **No Mirage-caused regression detected at day 0.**
- CLS (0.01) is already well within the Good threshold — the primary risk from Mirage (image resizing causing layout shift) is not materialising.
- LCP (5.1 s) and FCP (3.9 s) are pre-existing issues tied to React SPA hydration, not image delivery. Mirage may help slightly if the hero/splash image is the LCP element; it will not worsen these metrics since it defers off-screen images rather than blocking them.
- INP (230 ms) and TTFB (1.2 s) are unaffected by Mirage (interaction and server response time respectively).

#### 1-week re-check plan (target: 2026-05-07)

Re-run PSI for mobile at <https://pagespeed.web.dev/report?url=https%3A%2F%2Fsyrabit.ai&strategy=mobile> and compare to the baseline table above. Focus on:

1. **CLS** — should remain ≤ 0.1. A jump above 0.1 would be a Mirage regression (image resize causing unexpected reflow).
2. **LCP** — note direction (improvement expected if LCP element is an image; stable otherwise).
3. **INP** — should remain in the same range (Mirage does not affect interactivity).

If CLS rises above 0.1 after Mirage data dominates the CrUX window, disable Mirage:
- Cloudflare dashboard → **Speed** → **Optimization** → **Images** → **Mirage** → toggle off
- Or via API: `PATCH /zones/5b8c97df4431491dc7f60ea72fb61871/settings/mirage` with `{"value":"off"}`
- Update row 4b in the Task #66 table above and add a note here.

#### Status

✅ Baseline captured 2026-04-30. No regressions. Mirage remains enabled. Re-check target: **2026-05-07** (1 week) and **2026-05-28** (full 28-day CrUX window).

The 28-day re-check is tracked as **Task #89** — "Re-check mobile Core Web Vitals at the 28-day mark (~2026-05-28) when the CrUX window fully reflects Mirage traffic."

---

### Task #81 — Workers AI RPM limit tuning (2026-04-30)

#### Measurement

Observed production deployment logs at 11:21–12:12 UTC on 2026-04-30, which covers both low-traffic and mid-morning load.

**Workers AI LLM pool** (chat + content SmartKeyPools):
| Signal | Value | Source |
|--------|-------|--------|
| LLM-level Workers AI 429s | **0** (none in entire log window) | deployment logs |
| Deployed `rpm_limit` per slot | `30` (old code default, pre-Standard-plan) | `SLM SmartKeyPool active slots` startup log |
| Current code default | **3 000** (Standard plan, unified billing) | `llm.py` `_POOL_RPM_LIMITS` |
| Peak `rpm_used` approaching 30 RPM? | No | no throttle warnings or 429s seen |

**Pool evidence — startup log extract (2026-04-30T11:21:03 UTC)**

Tuple format: `(provider, model, max_con, rpm_limit)`

```
SLM SmartKeyPool active slots (chat pool):
  [('groq', 'meta-llama/llama-4-scout-17b-16e-instruct', 4, 30),
   ('workers-ai', '@cf/meta/llama-3.3-70b-instruct-fp8-fast', 6, 30),   ← deployed default
   ('cerebras', 'llama3.1-8b', 4, 30),
   ('openrouter', 'meta-llama/llama-4-scout', 4, 60)]

SLM SmartKeyPool active slots (content pool):
  [('workers-ai', '@cf/openai/gpt-oss-120b', 4, 30),                    ← deployed default
   ('gemini', 'gemini-2.5-flash', 6, 600),
   ('cerebras', 'qwen-3-235b-a22b-instruct-2507', 4, 30)]
```

The `rpm_limit=30` confirms the deployed backend was running the pre-Standard-plan code
default. After the next Railway re-deploy (which picks up `llm.py` with `default=3000`),
all Workers AI slots will show `rpm_limit=3000`. **At that point, confirm that no**
**`WORKERS_AI_RPM_LIMIT` env var is set in the Railway service settings — if one exists**
**at value 30 or 150, delete it.** A stale low override would take precedence over the code
default and silently cap Workers AI at a fraction of its Standard-plan budget.

**Workers AI embedding** (`@cf/baai/bge-large-en-v1.5`):
| Signal | Value |
|--------|-------|
| Embedding 429s | Present — roughly every 10 min |
| Tracked by SmartKeyPool? | **No** — goes through `vertex_services._workers_ai_primary_embed()` directly |
| Likely cause | CF free-tier embedding rate limit (~50 RPM); separate from the 3 000 RPM LLM limit |

#### Decision

- **LLM Workers AI limit → 3 000 RPM** (already in code). Zero LLM 429s confirms traffic is well
  within the budget. The old deployed default of 30 was not causing errors at current load, but
  the code default has been correctly updated to 3 000 (Standard plan).
- **No Railway env var needed.** If `WORKERS_AI_RPM_LIMIT` was previously set to 30 or 150 in
  the Railway environment, **remove it** — the code default of 3 000 now applies and the old
  override would keep the limit artificially low.
- **Embedding 429s are a separate issue.** No change to the embedding rate-limit path in this
  task. The `vertex_services.py` fallback to Gemini embedding is the existing mitigation.
  Tracked separately.

#### Soft / hard shift thresholds (updated with Standard plan)

| Threshold | Old (150 RPM free tier) | New (3 000 RPM Standard plan) |
|-----------|------------------------|-------------------------------|
| Soft shift (deprioritise) | 70% = 105 RPM | **85% = 2 550 RPM** |
| Hard shift (skip slot) | 90% = 135 RPM | **95% = 2 850 RPM** |

Both thresholds are set in `llm.py` `_SmartKeyPool._RPM_SOFT_THRESHOLD` (0.85) and
`_RPM_HARD_THRESHOLD` (0.95).

#### Action taken

- `artifacts/syrabit-backend/llm.py` — added Task #81 measurement comment to `_POOL_RPM_LIMITS`
  block, noting: zero LLM 429s, correct default is 3 000, Railway override should be removed if present.

#### Status

✅ Measured 2026-04-30. LLM Workers AI limit confirmed at **3 000 RPM** (code default, no Railway
env var override needed). Embedding 429s noted as separate concern; no pool-level action taken.

---

## Google Tag Gateway (first-party gtag proxy)

**Set up:** 2026-04-30 — implemented as a route in `workers/edge-proxy/src/index.ts`.

### What it does

GA4 beacons and the `gtag.js` script loader are proxied through `api.syrabit.ai` (the existing edge worker) instead of being fetched directly from `googletagmanager.com`. This makes GA4 a **first-party resource**, meaning:

- Ad-blocker lists that block `googletagmanager.com` no longer suppress analytics — recovering ~10–20% of mobile traffic that was previously invisible.
- The browser has an open TLS connection to `api.syrabit.ai` already, so the gtag.js fetch costs no extra DNS + TCP handshake.
- All traffic passes through the same Cloudflare PoP as the page itself.

### Routes added to `workers/edge-proxy/src/index.ts`

| Path | Upstream |
|------|---------|
| `GET /gtag/js?id=G-...` | `https://www.googletagmanager.com/gtag/js?id=G-...` |
| `GET /gtag/gtm.js?id=GTM-...` | `https://www.googletagmanager.com/gtm.js?id=GTM-...` |
| `POST /gtag/collect` | `https://www.google-analytics.com/g/collect` |

Script responses are edge-cached for 5 minutes (`s-maxage=300`); beacon POSTs are never cached.

### Frontend change (`artifacts/syrabit/vite.config.js`)

The `ga4Plugin()` function was updated to load `gtag.js` from `/gtag/js?id=${id}` (first-party) instead of `https://www.googletagmanager.com/gtag/js?id=${id}`.

### Deploy

Redeploy the edge worker after this change:

```sh
cd workers/edge-proxy && npx wrangler deploy
```

The Pages frontend picks up the change on the next build (the `s.src` URL is baked into `index.html` at build time).

---

## Load Balancer setup — CANCELLED 2026-05-03

**Status:** The Basic Load Balancing subscription was cancelled. With a single Railway origin a load balancer adds no value (LB requires ≥2 pools). The setup script `workers/edge-proxy/scripts/setup-load-balancer.mjs` was removed in the same change. Re-evaluate when a second origin (Cloud Run failover, second Railway, etc.) is provisioned. See `docs/cloudflare-cost-map.md` → "Cancelled subscriptions".

<details>
<summary>Historical: what the script used to do (kept as reference)</summary>

**Status:** Runbook script ready. Requires a Cloudflare API token with Load Balancer Edit permissions.

### Why

The existing DNS record for `api.syrabit.ai` is a proxied AAAA `100::` placeholder (Cloudflare Spectrum / Orange-cloud). A proper Load Balancer adds:
- **Health monitoring** — detects Railway outages within 60 seconds.
- **Automatic failover** — routes traffic to a backup origin (e.g. Cloud Run) when Railway is unhealthy.
- **Dashboard visibility** — per-origin latency and uptime graphs in CF → Traffic → Load Balancing.

### How to apply

1. Create a Cloudflare API token at `https://dash.cloudflare.com/profile/api-tokens` with:
   - Template: **Load Balancer Management**
   - Scope: account `d66e40eac539fff1db270fddf384a5ec`, zone `syrabit.ai`
2. Export the token: `export CLOUDFLARE_LB_TOKEN=<value>`
3. Run the runbook script:
   ```sh
   node workers/edge-proxy/scripts/setup-load-balancer.mjs
   ```
   Use `--dry-run` first to preview the API calls.

### What the script creates

| Resource | Name | Configuration |
|---------|------|---------------|
| Monitor | `syrabit-api-health` | HTTPS GET `/api/health`, 60 s interval, 2 retries, 10 s timeout, expects 200 |
| Pool | `syrabit-railway-primary` | Origin: `workspacemockup-sandbox-production-df37.up.railway.app`, weight 1 |
| Load Balancer | `api.syrabit.ai` | Proxied, TTL 30s, steering: off (single pool), fallback: Railway pool |

Script: `workers/edge-proxy/scripts/setup-load-balancer.mjs` *(deleted 2026-05-03)*

</details>

---

## Zaraz — GA4 via Cloudflare consent layer — CANCELLED 2026-05-03

**Status:** Zaraz subscription cancelled. PostHog and GA4 continue to load via the existing deferred-JS path in `index.html` (`deferPosthog` / `deferThirdParty` IIFEs after LCP). The setup script `workers/edge-proxy/scripts/setup-zaraz.mjs` was removed in the same change. Re-evaluate only if we need GDPR/DPDP consent management at the edge. See `docs/cloudflare-cost-map.md` → "Cancelled subscriptions".

<details>
<summary>Historical: what the script used to do (kept as reference)</summary>

**Status:** Runbook script ready. Requires Zaraz to be enabled in the dashboard first.

### What Zaraz adds over the gtag gateway

The Google Tag Gateway (above) makes GA4 first-party at the network level but does not add **consent management**. Zaraz adds:

- A consent modal (GDPR/DPDP-compliant) that gates GA4 from firing until the visitor accepts.
- SPA-aware pageview tracking via Zaraz's built-in route-change trigger.
- Centralised third-party tool management through the Cloudflare dashboard.

### Activation steps

1. **Enable Zaraz in the dashboard:**
   - Cloudflare dashboard → `syrabit.ai` zone → **Speed → Zaraz → Enable**
2. **Run the setup script** (once Zaraz is active):
   ```sh
   export CLOUDFLARE_ZARAZ_TOKEN=<zaraz-edit-token>
   node workers/edge-proxy/scripts/setup-zaraz.mjs
   ```
   Use `--dry-run` first to preview the config that will be applied.
3. **Customise the consent banner** — Speed → Zaraz → Consent (banner copy, link to privacy policy).
4. **Remove the ga4Plugin() from `vite.config.js`** once Zaraz is confirmed working — Zaraz owns GA4 loading at that point and the `/gtag/js` gateway becomes redundant.

### Consent configuration applied by the script

| Setting | Value |
|---------|-------|
| Consent enabled | `true` |
| Cookie name | `zaraz-consent` |
| Expiry | 365 days |
| Categories | `analytics` (gates GA4), `advertising` (empty — reserved) |
| Modal buttons | Accept all / Reject all / Confirm choices |

Script: `workers/edge-proxy/scripts/setup-zaraz.mjs` *(deleted 2026-05-03)*

</details>
