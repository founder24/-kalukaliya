# Syrabit Replit development notes

## Run the app

The Replit run button starts the React/Vite frontend:

```bash
pnpm --filter @workspace/syrabit run dev
```

The browser-to-storage path is:

`React UI → /api/v1 → Vite dev proxy or Cloudflare Edge Worker → API_WORKER service binding → syrabit-api-prod → D1`

In local development, Vite proxies `/api` and `/health` to
`https://api.syrabit.ai`, the Cloudflare Edge Worker. Production Pages uses the
same Edge Worker origin. The Edge Worker forwards API and health traffic only
through its private `API_WORKER` service binding; missing bindings fail closed.
Chat quota reservations and user/account reads and writes are handled by the
D1-backed API Worker.

The Replit deployment is static frontend hosting and has no server run command.
The legacy Python/MongoDB backend is not started by the app workflow or used by
the production request path; its code and the AHSEC importer are retained only
for offline/migration work.

## Verification

```bash
pnpm --filter @workspace/syrabit run typecheck
pnpm --filter @workspace/syrabit run check:undefined
pnpm --filter @workspace/syrabit run test
pnpm --filter syrabit-api run type-check
```

## Live QA credentials

Use credentials stored in Replit Secrets for admin-panel and student-interface
QA. Use admin credentials for admin flows and a student-role account for
student-only flows; ask before creating a student account if none is available.
Never print credential values or write them to project files.

Cloudflare and provider credentials should be configured through Replit
Secrets or the Cloudflare deployment environment; do not commit local
environment files.