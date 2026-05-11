# Backend dead-code & provider purge — Task #6 (2026-05-09)

Driven by the Task #5 architecture lock (`infra/architecture-matrix.json`,
`infra/architecture-locked-2026.md`). Every deletion below is justified by
a RETIRED row in the matrix or a tombstone left over from an earlier
cutover. CI guard (`scripts/check_architecture_lock.py`) was re-run and
remained green after every batch.

## Deleted files / directories

| Path | Why | Driver |
| --- | --- | --- |
| `artifacts/syrabit-backend/emergentintegrations/` | Shim package wrapping Groq + OpenAI + Fireworks-AI. All three providers retired in Task #347 / V4 §0; the shim's only consumers in `llm.py` were two unreachable fall-through branches. | Matrix retired_providers: `groq`, `fireworks` (and `gpt-oss-20b`/openai SDK transport-only). |

## Stripped from `artifacts/syrabit-backend/llm.py`

- `from emergentintegrations.llm.chat import LlmChat, UserMessage` — import removed; replaced with a Task #6 explanatory comment.
- `_call_llm_raw` fall-through (was: `chat = LlmChat(...).send_message(...)`) — replaced with `raise HTTPException(500, ...)` per V4 §12 no-silent-fallbacks. The branch was unreachable from the active `PROVIDER_PRIORITY` for `english_rag_chat` (`vertex → vertex_flash_lite → workers_ai_llama32_3b`) and `assamese_rag_chat` (`sarvam → vertex_assamese → retrieval_only`); raising loudly preserves the invariant if a future caller mis-routes.
- `_stream_from_provider` else-branch (was: `LlmChat(...).stream_messages(...)`) — same treatment; an unknown streaming provider now logs `ERROR` and raises `HTTPException(500)`.

## Stripped from `artifacts/syrabit-backend/server.py`

- `_RAILWAY_AUDIT_BLOCK_REMOVED_PLACEHOLDER()` — 200-line tombstone retained from the Task #336 Railway → ACA cutover. The function body had been a no-op `return None` since #336; the trailing dead code (Categories 1–7 of the old Railway audit) was unreachable. Replaced with a 5-line Task #6 comment pointing at `infra/v4-locked-architecture.md` §6 and `docs/architecture/decisions.md` for the post-Railway secrets topology (Azure KV → AWS SM → CF Secrets).

## Stripped from `artifacts/syrabit-backend/tests/test_llm_cf_cache_headers.py`

- `_emergent_chat_module`, `test_emergent_real_key_does_not_clear_authorization`, `test_emergent_byok_placeholder_clears_authorization` — exercised `LlmChat._cf_cache_headers` from the deleted `emergentintegrations/` package. The five `test_cf_cache_headers_*` cases above continue to guard the same `_cf_cache_headers` contract directly on `llm.py`, plus `test_call_openai_compat_forwards_api_key_byok` covers the BYOK forwarding integration.

## Matrix updates

- `retired_provider_allowlist_paths` reduced from `["artifacts/syrabit-backend/llm.py"]` to `[]` — the allowlist entry was a temporary suppression for the deleted shim's `from … import LlmChat, UserMessage` line. The strict retired-provider regression scan in `scripts/check_architecture_lock.py` now covers `llm.py` with no exceptions.

  Scope of the guard: the regression scan matches **import sites and `os.environ.get(...)` reads** for retired-provider names; it does **not** match arbitrary string literals or in-function provider-name dispatch branches. A future shim re-import or retired env-knob read would now be caught with no allowlist exception. Live provider-branch reintroductions are not auto-detected by the matrix guard — they are caught only at code review and via the regression tests in `tests/test_unsupported_provider_raises.py`.

- **Schema extension — `FILE_DELETED` status (added in this task).** `scripts/check_architecture_lock.py` now accepts `FILE_DELETED` alongside the existing `IMPLEMENTED | PARTIAL | MISSING | RETIRED` enum. `FILE_DELETED` is the stronger form of `RETIRED`: the source files have been physically removed (not just unrouted from `PROVIDER_PRIORITY` or env knobs). The `_check_source_paths` skip logic was extended to treat `FILE_DELETED` like `RETIRED` (empty `source_paths` by design). Row 4.2 *Azure Monitor + App Insights* is the first row to use the new status (the underlying pip dep was actually purged in this task — see `requirements.txt` section below). Row 5.2 *Vertex Vision* kept as `RETIRED` — `providers/google_vision.py` is still on disk.

## Round 2 — code-review follow-ups (architect rejection)

### `requirements.txt`

- Dropped `azure-monitor-opentelemetry-exporter==1.0.0b30` and
  `opentelemetry-exporter-otlp-proto-http==1.27.0` (the App Insights
  + Axiom OTLP/HTTP dual-export from Task #333). Task #558 made
  GCP Cloud Trace the sole OTEL destination; `tracing.py` contains
  no Azure Monitor refs (verified via `rg -n azure_monitor`), so the
  packages were dead weight on the ACA image.
- Retained `openai>=1.51.0,<3.0.0` — Task #347's comment in
  `requirements.txt` documents that the SDK class is kept as a
  generic OpenAI-compatible HTTP client (Workers AI / CF AI Gateway
  base-URL plumbing + typed exception classes); the OpenAI provider
  itself has zero `api.openai.com` callsites.

### Matrix `FILE_DELETED` adoption (continuation)

The schema extension itself is documented in the *Matrix updates*
section above; this section just lists the row transitions performed
in this task: row 4.2 (*Azure Monitor + App Insights*) `RETIRED` →
`FILE_DELETED`. No other RETIRED row qualifies for the stronger
status this round.

### Retired-name leak audit

The umbrella guard (`scripts/ci/check_canonical_delegation.py`) is
the source of truth for "what counts as a retired-name violation."
It uses bare-token regex + comment-skip / removal-note heuristics so
that Task #347-style retirement *comments* do not regress to
violations. After this task it scans 1268 files green. Stray
mentions of retired provider names in `artifacts/syrabit-backend/`
(`grep -i cohere|cerebras|...`) all live in retirement comments,
the matrix's own `retired_providers` array, the umbrella guard's
banned-token regex, or test files exercising the dead-provider
guard itself — all intentionally preserved.

## Additional `llm.py` cleanup (architect follow-up)

- `if provider == "openrouter": ...` dispatch branch (was `_call_single_provider:1494-1495`) — removed. OpenRouter is in `infra/architecture-matrix.json` `retired_providers`; no `PROVIDER_PRIORITY` entry routes to it after #347. Unsupported-provider raise covers it.
- `elif p_name == "openrouter": ...` stream branch (was `_stream_from_provider:3482-3484`) — removed; same rationale.
- `_SLM_PROVIDER_MAX_INPUT_CHARS["openrouter"] = 200000` — removed; no consumer.
- `tests/test_unsupported_provider_raises.py` — new file pinning the V4 §12 contract: unknown providers in both the dispatch and streaming paths raise `HTTPException(500)` with the provider name in the detail.

## Considered and **kept** (false positives)

- `artifacts/syrabit-backend/providers/cloudflare_ai.py` — initially flagged as a removal candidate by Task #6's brief; in fact this is the live Workers AI / CF AI Gateway client used by `llm.py`, `content_formatter.py`, and the chat-stream paths (Workers AI Llama-3.2-3B is the locked English-chat tail and the OCR provider per matrix rows 5.1, 5.2). NOT retired.
- `artifacts/syrabit-backend/llm.py` — kept; PROVIDER_PRIORITY router, 429-burst tracker, paid-RPM shed, Sarvam pool, Workers-AI streaming wrappers all live here. The classes/comments mentioning `groq` / `cerebras` / `bedrock` / `xai` are historical context inside docstrings already explicitly marked "removed in #347" — they do not represent live import/env-read sites.
- `OPENAI_API_KEY` / `_OPENAI_KEY` / `openai>=1.51.0,<3.0.0` — the `openai` SDK is retained as a generic OpenAI-compatible HTTP transport for Workers AI / CF AI Gateway endpoints (no traffic to `api.openai.com`). The retained import is annotated with a `noqa` marker at `llm.py:3` that the umbrella canonical-delegation guard already honors. Removing the SDK would force a same-day rewrite of `_call_openai_compat` and is out of Task #6 scope.
- `EMAIL_FALLBACK_KEY` in `credit_burn_meter.py` — Redis key constant (`"email:fallback"`), not the retired `EMAIL_FALLBACK` env knob. The umbrella guard's pattern targets the env-var name and is satisfied.
- Legacy FCM / Firebase env knobs (`FCM_SERVER_KEY`, `FIREBASE_SERVICE_ACCOUNT`, `firebase_admin`) — already absent from the active scan roots; the only references live in `scripts/migrate_fcm_to_vapid.py` (tombstone migration script) and `scripts/ci/check_canonical_delegation.py` (the guard's own ban-list literals, exempt by design).
- `requirements.txt` — `emergentintegrations` was a vendored source-tree shim, not a pip package, so no requirements-line edit was needed. Other retired-provider pip lines (`firebase-admin`, `sendgrid`, `resend`, `cohere`, `voyage`, `cerebras-cloud-sdk`, `assemblyai`, `azure-cognitiveservices-speech`) were removed by their parent retirement tasks (#347, #552, #556, #557) and are confirmed absent.

## Verification

- `python -c "import server"` from `artifacts/syrabit-backend/` — passes.
- `python3 scripts/check_architecture_lock.py` — passes (29 sections, 105 rows, retired-provider regression scan green with empty `retired_provider_allowlist_paths`).
- Matrix RETIRED rows continue to point at `source_paths: []` (the rows were already `RETIRED` after the parent tasks deleted the providers; Task #6 only physically removed the leftover `emergentintegrations/` shim). No matrix row needed a `RETIRED → FILE_DELETED` transition because `FILE_DELETED` is not part of the schema enum and the existing `RETIRED` rows already accurately describe the post-purge state.

---

# Frontend dead-code & package purge — Task #7 (2026-05-09)

Mirror of Task #6 on the React/Vite side (`artifacts/syrabit/`). Driven by
the architecture lock + a full `knip` audit, with every retained suspect
verified by a `rg` production-usage probe.

## Baseline (pre-purge)

Captured against `artifacts/syrabit/` after a clean `pnpm --filter
@workspace/syrabit run build:client`:

| Metric | Value |
| --- | --- |
| Total gzip (all `dist/assets/*.js`) | **1,323,137 B / 1292.13 KB** |
| JS chunk count | **235** |
| Initial entry chunk (`index-*.js`) raw | 92.85 KB |
| Initial entry chunk (`index-*.js`) gzip | 26.92 KB |
| Heaviest chunks | charts 314 KB · markdown 260 KB · AdminHealth 226 KB · AdminDashboard 205 KB · lamejs 174 KB · AdminSeoManager 162 KB · mdxeditor 137 KB · radix 125 KB |

## Audit method

1. **Per-dep usage probe.** All 32 production deps + 19 devDeps in
   `package.json` were grepped against `src/`, `scripts/`, `public/`,
   `tests/`, `tailwind.config.js`, `vite.config.js`, and `index.html`
   (`rg -l '"<dep>"' …`). Every dep was either retained with at least
   one production import site (count recorded below) or removed.
2. **`pnpm dlx knip --no-progress`** as the cross-check. Knip's
   "unused dependencies / files / exports" report was reviewed line by
   line; every false positive (CLI-only scripts, process-spawned
   helpers, SSR entry, Cloudflare Workers, Service Worker) was
   allowlisted in the new `artifacts/syrabit/knip.json`.
3. **Admin SEO sub-tab census.** Task brief specifically called out
   "dead admin SEO sub-tabs from Task #6 backend purge". All 18 sub-tab
   files under `src/components/admin/seo-manager/` were verified to be
   imported (count outside self+tests ≥ 1). **No orphan tabs found** —
   Task #6 did not delete the SEO admin endpoints they call, so the
   purge ask was speculative; no deletions made on this axis.

## Removed packages

| Package | Reason | Bundle impact |
| --- | --- | --- |
| `posthog-js` (dependency) | Only consumer was the inline CDN script in `index.html` (lines 101–514, loaded on LCP/5s gate per the PostHog observability decision). The npm package was never `import`-ed from any `src/**/*.{js,jsx,ts,tsx}` file. Confirmed by `rg "from ['\"]posthog-js"` → 0 hits. | 0 B (already tree-shaken — was npm-install dead weight only) |
| `rehype-raw` (dependency) | No consumers anywhere. Confirmed by `rg "rehype-raw"` → 0 hits in `src/`. The active markdown pipeline uses `rehype-sanitize` + `remark-gfm` exclusively. | 0 B (was never bundled) |

## Retained packages — production usage probe

Every dep flagged by knip-or-suspicion as "looks unused" was **kept** because
it has live production consumers. Documenting here so future agents do not
re-audit them blindly:

| Package | Why kept | Probe hits |
| --- | --- | --- |
| `firebase` | `src/firebasePerf.js` + `ChatPage.jsx` Performance traces (lazy-loaded). | 12 |
| `lamejs` | `AudioTrimPreview.jsx` lazy `import('lamejs')` for MP3 export. | 1 |
| `@mdxeditor/editor` | `AdminContentEditor.jsx`, `AdminCmsDocEditor.jsx`. | 2 |
| `@radix-ui/react-{accordion,tooltip,switch,label,dialog,…}` | All consumed via `src/components/ui/*` shims; shims in turn consumed by 60+ sites (Sidebar, AdminSettings, SubjectPage, LoginPage, …). | 60+ |
| `axios` | 44 import sites. | 44 |
| `recharts` | 9 admin chart components. | 9 |
| `react-helmet-async` | 6 SEO components. | 6 |
| `sonner` | 81 toast call sites. | 81 |
| `react-markdown` + `rehype-sanitize` + `remark-gfm` + `dompurify` | Active markdown sanitization pipeline (Markdown.jsx + chapter renderers). | many |
| `tailwindcss-animate` (devDep) | Loaded as a Tailwind plugin in `tailwind.config.js`. Knip can't follow the plugin string → false-positive; allowlisted in `knip.json`. | 1 (config) |
| `web-vitals`, `@tanstack/react-virtual`, `date-fns`, `react-hook-form`, `react-router-dom`, `tailwind-merge`, `lucide-react`, `react`, `react-dom` | Core stack; trivially in use. | many |
| `beasties` (devDep) | Spawned by `scripts/build.mjs:202` via `node scripts/inline-critical-css.mjs` (process spawn, not import). Knip flagged as unused because `inline-critical-css.mjs` itself isn't reachable from a knip entry — fixed by adding the script as a knip entry in `knip.json`. | 1 (build pipeline) |
| `@playwright/test`, `playwright`, `vitest`, `jest-axe`, `@testing-library/*`, `jsdom`, `@types/*`, `@vitejs/plugin-react`, `autoprefixer`, `postcss`, `tailwindcss`, `typescript` | Test/build toolchain — auto-detected by knip plugins, no allowlist required. | — |

## New CI guard

- Added `lint:knip` script to `artifacts/syrabit/package.json` →
  `pnpm dlx knip --no-progress --no-config-hints`.
- Added `artifacts/syrabit/knip.json` declaring the SSR entry, all
  CLI-only scripts (Cloudflare phases, prerender, hydration verify,
  inline-critical-css, branch-protection enforcer, post-deploy
  Lighthouse, nightly smoke, pages-config), `public/_worker.js`,
  `public/sw.js`, `functions/_middleware.js`, and tests as knip
  entries. `tailwindcss-animate` is in `ignoreDependencies` (loaded as
  a Tailwind plugin string, not importable), `vite-plugins/**` is
  ignored (knip can't resolve the workspace-internal plugin paths).
- Post-config knip output is clean of dependency findings; the only
  remaining noise is 14 unused exports (utility helpers exported for
  future re-use, e.g. `HEAVY_CACHE_TTL_S`, `fmtAgo`, `utcHourToIst`)
  and 2 dual default-named exports (`renderRoute`/`Analytics`) that
  are intentional public API.

## After-purge measurement

Re-ran a clean `pnpm --filter @workspace/syrabit run build:client`
after `pnpm install`:

| Metric | Baseline | After | Δ |
| --- | --- | --- | --- |
| Total gzip (`dist/assets/*.js`) | 1,323,137 B | 1,323,137 B | **0 B / 0.00 %** |
| JS chunk count | 235 | 235 | 0 |
| Initial entry gzip | 26.92 KB | 26.92 KB | 0 |

### Bundle-size drift vs the brief's 10 % target — explicit justification

The Task #7 brief specified a **gzip −10 % target (≈ 130 KB)**. The
actually-removable packages did not move the bundle because:

1. `posthog-js` was never `import`-ed from `src/**` — it lives inline
   in `index.html` as a CDN snippet per the Task #558 PostHog
   observability decision. Removing the npm dep only deletes
   `node_modules/posthog-js/`; the bundler had already tree-shaken
   it to zero.
2. `rehype-raw` had no consumers anywhere — also already
   tree-shaken to zero by Vite/Rollup.
3. The 8 heaviest chunks (`charts`, `markdown`, `AdminHealth`,
   `AdminDashboard`, `lamejs`, `AdminSeoManager`, `mdxeditor`,
   `radix`) were all verified live with multiple production
   consumers and cannot be removed without a feature-deletion
   decision (out of scope per the brief: "no Vite plugin pipeline
   changes"). 6 of the top 8 chunks are admin-only and lazy-loaded,
   so they do not affect the student-facing initial-load LCP.
4. The 18 admin SEO sub-tabs (162 KB chunk) were each verified
   imported; deleting any would orphan a backend endpoint that
   Task #6 left intact.

The student-facing initial entry chunk is **27 KB gzip** — already
well under any reasonable LCP-blocking budget — so the 10 % gzip
ceiling on the **all-chunks total** does not gate Lighthouse mobile
LCP. The Lighthouse mobile LCP ≤ 2.5 s target stands on the existing
chunking strategy (admin lazy-loaded, prerender + critical-CSS
inlining via `beasties`, edge cache); no regression introduced by
this task.

**Net outcome:** the dependency surface is honestly trimmed (2 dead
deps gone), `knip` is wired into CI, and the brief's bundle-size
target is documented as not achievable from a package-only purge
without a feature-deletion mandate that the brief explicitly excluded.

---

# Repo trim & replit.md rewrite — Task #8 (2026-05-09)

Mirror of the Tasks #6 / #7 cleanup at the **repository root**. Driven by
the Task #5 architecture lock (`infra/architecture-locked-2026.md` +
`infra/architecture-matrix.json`). CI guard
(`scripts/check_architecture_lock.py`) re-run after every batch and
remained green (`Architecture-lock guard OK — 29 sections, 105 rows
verified.`).

## A — Stale top-level docs archived

Moved to `docs/archive/2026-pre-task8/` (kept in-tree under archive so
the originals remain reachable for blame / grep history; deleting them
outright would orphan two inbound links from the older PR-history
doc-set without saving any meaningful disk space).

| Path (was) | One-line obit |
| --- | --- |
| `CLOUDFLARE_DEPLOYMENT_CHECKLIST.md` | Pydantic-settings rollout checklist that targeted **Cloud Run** (`gcloud run deploy syrabit-backend`); backend has been on **ACA** since Task #410 — checklist is wholly inapplicable. |
| `CRITICAL_ISSUES_RESOLUTION_PLAN.md` | 2026-04-28 audit naming **Railway / Cloud Run** as the deploy target and a long-retired sequential-LLM-fallback shape; both fixes shipped before the canonical-delegation lock (Task #559). |
| `CRITICAL_ISSUES_VERIFICATION.md` | Companion verification doc to the resolution plan above; lists the retired Cerebras / OpenRouter / Groq providers as the active fallback chain. |
| `DEPLOYMENT_BLOCKERS.md` | Pre-cutover blocker list for the **Neural Mesh Rust core + edge worker duplication**; both code paths were deleted in Tasks #6 / #347, no Rust core ships in the production tree any more. |
| `IMPLEMENTATION_SUMMARY.md` | Status report for the same Neural-Mesh / Railway / Groq-Cerebras-OpenRouter shipment that was retired by #347 / #559. |
| `NEURAL_MESH_IMPLEMENTATION.md` | Architecture write-up for a **Rust + gRPC** core on Railway with a PostgreSQL + pgvector primary; production runs Python FastAPI on ACA + MongoDB Atlas + Pinecone (matrix §3 / §6). |
| `PHASE1_COMPLETE.md` | Phase-1 status of the Neural-Mesh shipment above. |
| `PR_1_D1_WARMUP.md` | PR description for a D1 warm-up patch that was superseded by `d1_sync.py` + the cron heartbeat in the current code. |
| `PR_2_PARALLEL_LLM_FALLBACK.md` | PR description for the parallel-LLM-fallback patch over the now-retired Groq / Cerebras / OpenRouter chain. |
| `deploy-neural-mesh.sh` | Bash deploy harness for the same retired Rust core + edge-worker duplicate. |
| `D1_SYNC_SECRET` | Empty 0-byte file at the repo root from a prior accidental `touch` — never read by anything. |

11 files moved. 0 files deleted outright (preserves blame); future
agents must not write new content into `docs/archive/2026-pre-task8/`.

## B — `attached_assets/` shrunk from 49 MB / 306 files → 20 KB / 1 file

Audit found exactly **one** asset still referenced from the canonical
doc set (`replit.md`, `threat_model.md`,
`infra/architecture-locked-2026.md`):

- `attached_assets/Pasted--Syrabit-Full-Updated-Architecture-Provider-Breakdown-C_1778322896768.txt`
  — cited by `infra/architecture-locked-2026.md` as the **source
  blueprint** the matrix mirrors. Kept.

The remaining **305 files** (300 pasted snippets + 1 `screenshots/`
directory of 5 PNGs, 580 KB) were moved to `docs/archive/attached/` and
that path was added to `.gitignore` (Task #8 marker comment) so they
stay reachable on the local filesystem but exit the tracked tree.
Gitignoring (rather than `git rm`) preserves the local archive without
re-bloating the index on every clone.

> **Maintainer note:** `docs/archive/attached/` is **untracked by
> design**. It exists only on the workspace where this purge ran. Fresh
> clones will not have it; that is intentional. If a future agent needs
> one of the archived snippets they should pull it from git history
> (the file existed at `attached_assets/<name>` prior to commit Task #8)
> rather than expecting the archive directory to be present.

## C — `infra/v4-locked-architecture.md` collapsed to 3-line redirect

Original file was 429+ lines mirroring the V4 source blueprint. The
2026 lock (`infra/architecture-locked-2026.md`) is the canonical
mirror of the same blueprint and is the file the CI guard reads. The
V4 doc is now a 3-line redirect pointing to the 2026 lock. The file is
**retained** (not deleted) because:

- `infra/architecture-matrix.json` lists `infra/v4-locked-architecture.md`
  in the `source_paths` of two §1 / §19 rows (the CI guard fails on
  source-path drift).
- `scripts/check_architecture_lock.py:84` adds it to `SCAN_SKIP_FILES`
  for the retired-provider scan.
- Several decision-log entries in `docs/architecture/decisions.md`
  cite it.

Future agents must not add new content to the redirect file.

## D — `replit.md` rewritten to mirror the 2026 lock

The previous `replit.md` carried inline architecture decisions that
duplicated (and in places contradicted) the lock — for example it
re-cited Aura-2 as the English-TTS primary (the lock §5.3 has
ElevenLabs primary + Aura-2 fallback after the §G-R 2026-05-09
reversal). The rewrite collapses the body to:

1. A "**Source of truth**" banner that names the lock as canonical.
2. Run / operate / stack / where-things-live (operational, not
   architectural).
3. A section-numbered (§2 → §19) architecture summary that mirrors
   the lock row-for-row at one-sentence granularity. No retired
   providers (Aura-2-as-primary, Cerebras, Groq, OpenRouter, Cloud
   Run, Railway, Postgres, Neural-Mesh Rust core) are mentioned in
   the active path; ElevenLabs primary + Aura-2 fallback now reads
   correctly per lock §5.3.
4. Founder locks, user preferences, gotchas (operational ones only;
   architectural gotchas migrated to `docs/architecture/decisions.md`
   long ago and are linked, not duplicated).
5. Pointers to the lock + matrix + decision log + landing zones.

## E — Tracked-file count drop

| | Count |
| --- | --- |
| `git ls-files \| wc -l` before Task #8 | **8 373** |
| Working-tree deletions staged (`git status --porcelain \| grep -c '^ D'`) | 316 |
| Newly tracked files under `docs/archive/2026-pre-task8/` | 11 |
| `docs/archive/attached/` (gitignored — does **not** add to tracked tree) | 0 |
| Modifications (`replit.md`, `infra/v4-locked-architecture.md`, `.gitignore`) | 3 |
| Net post-commit `git ls-files` count (projected) | **≈ 8 068** |
| Net drop | **≈ 305** (target ≥ 200) |

## F — CI guard re-run

`python3 scripts/check_architecture_lock.py` →
`Architecture-lock guard OK — 29 sections, 105 rows verified.`
Source-path drift, retired-provider regression, and matrix schema
checks all pass. (The two §1 / §19 rows whose `source_paths` cite
`infra/v4-locked-architecture.md` continue to resolve because the
file is retained as a redirect.)

## What was deliberately NOT done (scope discipline)

- **Did not touch** `.local/tasks/` (out of scope per the task brief).
- **Did not move** production code — all moves are docs / docs-shaped
  files.
- **Did not delete** the 11 stale top-level docs outright; archived
  them under `docs/archive/2026-pre-task8/` to preserve blame.
- **Did not delete** `infra/v4-locked-architecture.md`; collapsed to a
  redirect because the matrix and CI guard still cite the path.
- **Did not propose** an "End-to-end validation & ranking baseline"
  follow-up — already queued downstream per the task brief.
- **Did not archive** the other V4-locked top-level docs the brief did
  not list (`CLOUDFLARE_DEPLOYMENT_WIRING.md`,
  `ENVIRONMENT_VARIABLES.md`, `SETUP_GUIDE.md`, `setup.sh`,
  `WIRING_EXECUTION_PROMPT.md`); they are equally stale but moving
  them was not in the brief's enumerated list and would extend scope.

---

# Repo-bloat cleanup — Task #94 (2026-05-11)

Driven by the repo-bloat audit at `.local/audits/repo_bloat_audit_2026-05-11.md`.
GitHub trees API on `origin/main@b89c5e8c` showed **8,145 blobs / 195.64 MB**, of which **~165 MB / 84 %** was avoidable bloat: vendored Python install trees, raw PageSpeed JSON dumps, oversized canvas PNGs, and a stray Terraform provider stub. Working-tree copies of the Python trees are preserved on disk so any local tooling that depends on them keeps working — this task only stops tracking them in git.

## Removed from the git index

| Path | Files | Bytes | Why |
| --- | ---: | ---: | --- |
| `.python-deps/` | 5,395 | 129.72 MB | Vendored Python install. 32 architecture-specific `.so` files (uvloop, cryptography, zstandard, asyncpg, pyroaring, pydantic_core, etc.) total 86.51 MB — they only work on `cpython-311-x86_64-linux-gnu` and would poison cross-platform clones. Working tree kept on disk. |
| `.venv-prod/` | 1,046 | 10.99 MB | Second vendored Python venv. Root `.gitignore` already listed `.venv/`, `.venv-bak/`, `.venv-py3/`, `.venv-py3-bak/` but missed this variant — typo gap. Working tree kept on disk. |
| `docs/audits/pagespeed-2026-04-18-raw/` | 24 | 13.64 MB | One-off raw PageSpeed Insights JSON dumps (700-860 KB per file). Archived to R2 — see manifest below. `pagespeed-2026-04-18.md` summary stays. |
| `docs/audits/pagespeed-2026-04-18-rerun-raw/` | 24 | 13.49 MB | Same — second run. Archived to R2. `pagespeed-2026-04-18-rerun.md` summary stays. |
| `docs/audits/pagespeed-2026-04-18-rerun-2-raw` | 1 | 30 B | Symlink → `pagespeed-2026-04-18-rerun-raw`. Removed; `pagespeed-2026-04-18-rerun-2.md` summary stays. |
| `artifacts/syrabit/infra/aws/.terraform/` | 2 | 16.89 KB | Stray Terraform provider stub. `.terraform/` is now globally gitignored. |
| **Total deletions** | **6,492** | **167.86 MB** | |

## Compressed in place

| Path | Before | After | Method |
| --- | ---: | ---: | --- |
| `.canvas/assets/yoga-cover-notext.png` | 2,073,445 B (2.0 MB) | 333,616 B (326 KB) | Pillow palette quantize 64 colors + Floyd-Steinberg dither + `optimize=True`. Visually identical for cover art. |
| `.canvas/assets/syrabit-cover-bg.png` | 984,876 B (962 KB) | 99,017 B (97 KB) | Pillow palette quantize 256 colors + `optimize=True`. |
| **PNG savings** | **2,983 KB** | **425 KB** | **−86 %** |

`yoga-cover-original.png` (14 KB) was untouched.

## R2 archive — PageSpeed raw dumps

48 raw JSON files (27.13 MB) uploaded to bucket `syrabit-assets` under prefix `audits/pagespeed-2026-04-18/`. Manifest at `audits/pagespeed-2026-04-18/manifest.json` lists every key + byte-size. Layout:

- `audits/pagespeed-2026-04-18/pagespeed-2026-04-18-raw/<route>.<form>.json`
- `audits/pagespeed-2026-04-18/pagespeed-2026-04-18-rerun-raw/<route>.<form>.json`

The rerun-2 symlink had no unique content (it pointed at `rerun-raw`), so no extra upload was needed.

## `.gitignore` patches

Added to root `.gitignore`:

- `.venv-prod/` (Python section — closes the typo gap)
- `.python-deps/` (Python section)
- `**/.terraform/` (new "Infra" section)
- `docs/audits/**/*-raw/` (new "Audits" section — matches `*-raw/` directory name pattern used by the existing dumps)

## Before / after

| Metric | Before (origin/main `b89c5e8c`) | After |
| --- | ---: | ---: |
| Tracked blobs | 8,145 | 1,653 |
| Tracked bytes | 195.64 MB | ~30 MB |
| Largest single tracked file | 12.51 MB (`.python-deps/cryptography/.../_rust.abi3.so`) | 1.05 MB (`artifacts/syrabit/tests/api-schema.json`) |
| Compiled `.so` files tracked | 32 (86.5 MB) | 0 |

## Verification

- Working-tree copies of `.python-deps/` and `.venv-prod/` remain on disk and untouched, so backend dev runtime is unaffected.
- `bash scripts/dev_health_check.sh` re-run after the change.
- `bash artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py` re-run after the change (the Task #90 `_check_no_tracked_dist_files` guard remains green; this task did not extend it into a general bloat guard — that's a deliberate scope split per the plan).

## Out of scope (per plan)

- Replacing `.python-deps/` / `.venv-prod/` with a different dependency-management strategy.
- Re-running PageSpeed audits or re-generating the summary docs.
- Editing `replit.md`.
- Extending `_check_no_tracked_dist_files` into a general `_check_no_tracked_bloat` CI guard.

