---
name: Syrabit frontend test runner
description: Why the full vitest suite can't run in one shot and how to run all 66 frontend tests reliably in Replit.
---

## The rule
Never run `pnpm test` (bare `vitest run`) against all 66 frontend test files in Replit — the process gets OOM-killed silently (exit -1) with no output. Use `pnpm test:all` instead, which runs 6 batches of ~12 files via `scripts/test-all.sh`.

**Why:** Each test file spins up jsdom + React + all mocks. 66 files × ~40 MB = OOM in the Replit container, even with `pool: 'forks'`, `singleFork: true`, or `maxWorkers: 1`. Individual files and small batches always pass fine.

**How to apply:**
- `pnpm --filter syrabit-frontend test:all` — runs all 66 files in 6 batches, exits non-zero if any batch fails.
- `pnpm --filter syrabit-frontend test -- src/path/to/file.test.jsx` — run a single file during development.
- The bare `pnpm test` script (`vitest run`) works fine for individual files or small groups specified on the command line.

## vitest 4.x config note
In vitest 4, `poolOptions.forks.singleFork` was removed — it is now `singleFork: true` at the `test` level. The correct `vitest.config.js` shape is:

```js
test: {
  pool: 'forks',
  singleFork: true,   // NOT poolOptions.forks.singleFork
  ...
}
```

## E2e Playwright — Replit fix
Playwright's bundled Chromium download is SIGTERM'd by the Replit sandbox. Fix: install `chromium` as a nix system package via `installSystemDependencies`. The `run-e2e.sh` auto-detects `which chromium` and sets `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`. The `playwright.config.ts` reads that env var and passes it as `launchOptions.executablePath` with `--no-sandbox` flags. `pnpm test:e2e` works without any manual env var setup.

## Test counts (as of audit)
- Edge (vitest): 11 files, 81 tests — runs fine in one shot
- Backend (pytest): 261 tests — takes ~90s, needs `timeout 120` in bash
- Frontend (vitest): 66 files, ~584 tests — 6 batches via `pnpm test:all`
- E2e (Playwright): 6 spec files, 23 tests — `pnpm test:e2e` via system nix chromium
