---
name: Syrabit dev setup
description: How this monorepo is configured to run in the Replit environment
---

# Syrabit Dev Environment Setup

## Architecture
- Monorepo with pnpm workspaces: `apps/frontend` (Vite+React), `apps/edge` (Cloudflare Edge Worker), and `apps/api` (Cloudflare API Worker + D1).
- `apps/backend` is legacy Python/MongoDB code retained for offline or migration tooling; it is not part of the app runtime.

## Workflows
- "Start application": `pnpm --filter @workspace/syrabit run dev` — Vite on port 5000, webview.
- The frontend calls relative `/api/v1` paths in local development; Vite proxies them to `https://api.syrabit.ai` (the Cloudflare Edge Worker), which forwards via `API_WORKER` service binding to the D1-backed API Worker.
- Replit deployment serves the static frontend; it does not run FastAPI.
- The AHSEC D1 importer is a separate one-off data workflow, not an API server.

## Key config
- Vite config already has `host: '0.0.0.0'`, `allowedHosts: true`, port 5000 — no changes needed
- Missing `API_WORKER` binding fails closed; do not add a public backend URL fallback.

## Dependencies
- Frontend: `pnpm install` in `apps/frontend`
- Backend: `pip install -r apps/backend/requirements.txt`
- Backend test collection may require the pypdfium2 PDF dependency even when
  focused credential-free generation tests do not exercise PDF import paths.
- Replit blocks `playwright install --with-deps` because it cannot use apt/sudo; use browser-only Playwright installation locally and keep OS dependency provisioning in Ubuntu CI.
- Backend test dependencies are not included in the shared `.pythonlibs` environment, and package installation may fail when it tries to reinstall pinned app dependencies into read-only Nix paths.
- The pinned `requirements-dev.txt` can conflict with the runtime lock; resolve tests against the development input file instead.

**Why:** Resolving the generated dev lock together with the runtime lock fails on incompatible `python-dotenv` pins, while the development input file resolves successfully.

**How to apply:** From `apps/backend`, run focused tests with `PYTHONPATH=. uv run --no-project --with-requirements requirements.txt --with-requirements requirements-dev.in pytest ...`; do not add test tools to production requirements.

## Dev behavior without full secrets
- Local app traffic uses the Cloudflare API origin; it does not require MongoDB or start the legacy Python API.
- 401/403/404 console errors for auth-gated resources are expected for unauthenticated users.

## Focused API Vitest runs

Use `pnpm --filter syrabit-api exec vitest run <test-path>` to run specific API test files. Passing a path after `--` to the package's `test` script was observed to run the full suite instead of filtering. Filtered package scripts can also receive the literal `--` separator in their argument list, so command-line parsers should ignore it.

**Why:** Concurrent full-suite runs can collide in timing-sensitive atomic quota tests, creating failures that pass when run alone; the forwarded separator also made an otherwise documented CLI invocation fail.

**How to apply:** Use direct Vitest filters for focused checks, avoid running duplicate full API suites concurrently, and allow a standalone `--` token in package-script parsers.

## Production GCP resources
- Cloud Run service: `syrabit-backend` (region `asia-south1`, project `blissful-acumen-495019-t6`)
- URL: `https://syrabit-backend-851687450401.asia-south1.run.app`
- Update secrets: `gcloud run services update syrabit-backend --region=asia-south1 --update-secrets=...`

**Why:** Backend is designed to start gracefully without Redis/AI — all Optional fields, warnings only in dev mode.
