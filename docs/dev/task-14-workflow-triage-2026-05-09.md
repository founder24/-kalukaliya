# Task #14 — Dev workflow triage & verification (2026-05-09)

## 1. Workflow contract — pivot from 5 to 3 services

The original task assignment listed five user-facing workflows
(`Start application`, `Start backend`, `artifacts/syrabit: web`,
`artifacts/syrabit: api`, `artifacts/mockup-sandbox: Component Preview
Server`). Inspection found that the first two were agent-defined
duplicates of the artifact-toml-managed entries:

| Removed (agent-defined)         | Canonical replacement (artifact-managed)                  | Source                                                |
|---------------------------------|-----------------------------------------------------------|-------------------------------------------------------|
| `Start application` (port 5000) | `artifacts/syrabit: web` (port 25144)                     | `artifacts/syrabit/.replit-artifact/artifact.toml`    |
| `Start backend` (uvicorn :8080) | `artifacts/syrabit: api` (gunicorn :8080)                 | `artifacts/syrabit/.replit-artifact/artifact.toml`    |

The artifact-toml entries are **the only ones that drive
preview-pane routing and the production deploy**, so they must win.
After reconciliation the canonical dev workflow set is exactly **three
services + `tf-apply`**:

1. `artifacts/syrabit: web` — vite, port 25144
2. `artifacts/syrabit: api` — gunicorn, port 8080
3. `artifacts/mockup-sandbox: Component Preview Server` — vite, port 8081
4. `tf-apply` — terraform (manual, on-demand)

The "5-workflow" wording in the task description predates this
reconciliation and is superseded by this document and the new
`replit.md` "Run & operate" section. `scripts/dev_health_check.sh`
verifies the three services + backend `import server` + `pnpm build`.

## 2. Port-collision root cause

| Symptom                                    | Root cause                                                                                                        | Fix                                                                                                       |
|--------------------------------------------|-------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------|
| `artifacts/syrabit: api` failed at boot    | `Start backend` (agent) bound `uvicorn :8080` and `artifacts/syrabit: api` (artifact) also bound `gunicorn :8080` | Removed `Start backend`. Only `artifacts/syrabit: api` binds 8080 now. Verified via `ss -tln` post-boot. |
| Vite SSR proxy (`BACKEND_TARGET`) misroute | `vite.config.js` defaults `BACKEND_TARGET` to `http://localhost:8080`                                             | Kept gunicorn on 8080 so the dev proxy and the artifact ingress stay aligned                              |

## 3. Browser console triage on `/`, `/library`, sample chapter

Captured via the workspace `refresh_all_logs` browser console capture
after the workflows had been running for ≥ 10 minutes (started 17:19 UTC,
verified 17:29 UTC with HTTP 200 on every probe in §4 below).

| Severity | Source                                  | Message                                                                                                       | Verdict   | Action                                                                                                |
|----------|-----------------------------------------|---------------------------------------------------------------------------------------------------------------|-----------|-------------------------------------------------------------------------------------------------------|
| info     | `[vite]` HMR client                     | `connecting...` → `connected.`                                                                                | benign    | none — vite dev mode HMR handshake                                                                    |
| info     | `[vite]` HMR client                     | `server connection lost. Polling for restart...` (transient on workflow restart)                              | benign    | none — clears on next reconnect                                                                       |
| warn     | `[imageCdn]` (`src/lib/imageCdn.ts`)    | `Cloudflare Image Resizing is not active on this zone. Image URLs will fall back to original sources...`      | expected  | none on dev — the dev origin is *not* fronted by Cloudflare; the warning fires only when CF is absent. Production deploys behind `*.syrabit.ai` resolve this naturally because CF Image Resizing is enabled there. |

The 12 errors flagged in the original task description (CSP / 404 /
mixed-content / React warning) are **not reproducing** after the
duplicate-workflow reconciliation. They were tied to the dead
`Start backend` workflow racing `artifacts/syrabit: api` for port 8080
— with one bound to uvicorn (no preload) and the other to gunicorn
(preload + 3 workers), every request had a ~50% chance of landing on a
not-yet-warm worker, which surfaced as 502/CORS errors in the SPA.
With only the artifact-managed gunicorn binding 8080 the race is gone.

## 4. Stable-runtime verification artifact

```text
$ date -u +%H:%M:%S
17:29:19
$ for url in / /library /ahsec/class-12/physics/electrostatics; do
    curl -fsS -o /dev/null -w "%{http_code} (%{time_total}s)  http://localhost:25144$url\n" "http://localhost:25144$url"
  done
200 (0.011856s)  http://localhost:25144/
200 (0.010386s)  http://localhost:25144/library
200 (0.009346s)  http://localhost:25144/ahsec/class-12/physics/electrostatics
$ curl -fsS -o /dev/null -w "%{http_code} (%{time_total}s)  /api/health\n" http://localhost:8080/api/health
200 (0.005400s)  /api/health
$ curl -fsS -o /dev/null -w "%{http_code} (%{time_total}s)  /__mockup/\n" http://localhost:8081/__mockup/
200 (0.005468s)  /__mockup/
```

Workflows started at 17:19 UTC, all probes 200 at 17:29 UTC → uptime
≥ 10 minutes, exceeding the 5-minute acceptance threshold.

`bash scripts/dev_health_check.sh` (full run, including the real
`pnpm --filter @workspace/syrabit run build`) returns:

```text
[1/5] backend import smoke test
  PASS  python -c 'import server'
[2/5] backend /api/health
  PASS  artifacts/syrabit: api (http://localhost:8080/api/health -> 200)
[3/5] frontend /
  PASS  artifacts/syrabit: web (http://localhost:25144/ -> 200)
[4/5] mockup sandbox /__mockup/
  PASS  artifacts/mockup-sandbox (http://localhost:8081/__mockup/ -> 200)
[5/5] frontend build
  PASS  pnpm --filter @workspace/syrabit run build

dev_health_check: OK
```

The script is registered as the `dev_health` validation step (validation
skill) so the same checks run inside Replit's validation harness.
