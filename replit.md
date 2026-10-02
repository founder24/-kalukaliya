# Syrabit Replit development notes

## Run the app

The Replit run button starts the React/Vite frontend:

```bash
pnpm --filter @workspace/syrabit run dev
```

The frontend uses relative `/api/v1` requests in development. Staff content
CRUD is served by the Cloudflare API Worker under `/staff/content/...`; the
legacy Python backend and AHSEC importer are retained offline tools and are
not part of the default app workflow.

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