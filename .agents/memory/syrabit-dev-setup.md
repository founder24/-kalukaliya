---
name: Syrabit dev setup
description: How this monorepo is configured to run in the Replit environment
---

# Syrabit Dev Environment Setup

## Architecture
- Monorepo with pnpm workspaces: `apps/frontend` (Vite+React), `apps/backend` (FastAPI)
- Frontend on port 5000 (webview), Backend on localhost:8000 (console)
- Edge/Cloudflare Worker not run locally — only frontend+backend in dev

## Workflows
- "Start application": `pnpm --filter @workspace/syrabit run dev` — port 5000, webview (run from monorepo root)
- "Start backend": `cd apps/backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` — port 8000, console

## Key config
- `apps/backend/.env` sets `APP_ENV=development` and `TRUST_EDGE_AUTH=False` — required for dev startup without errors
- Vite config already has `host: '0.0.0.0'`, `allowedHosts: true`, port 5000 — no changes needed
- Vite proxies `/api/*` to `localhost:8000` (BACKEND_TARGET default)
- Backend health returns "degraded" in dev (Redis + Vertex AI not configured) — this is expected and non-fatal

## Dependencies
- Frontend: `pnpm install` in `apps/frontend`
- Backend: `pip install -r apps/backend/requirements.txt`

## Dev behavior without full secrets
- App loads and chapter pages render correctly with MongoDB connected
- Backend "degraded" health is normal in dev — Redis disabled, Vertex AI not configured
- 401/403/404 console errors for auth-gated resources are expected for unauthenticated users

## Production GCP resources
- Cloud Run service: `syrabit-backend` (region `asia-south1`, project `blissful-acumen-495019-t6`)
- URL: `https://syrabit-backend-851687450401.asia-south1.run.app`
- Update secrets: `gcloud run services update syrabit-backend --region=asia-south1 --update-secrets=...`

**Why:** Backend is designed to start gracefully without Redis/AI — all Optional fields, warnings only in dev mode.
