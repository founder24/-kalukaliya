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
- "Start application": `cd apps/frontend && pnpm dev` — port 5000, webview
- "Backend API": `cd apps/backend && python3 -m uvicorn app.main:app --host localhost --port 8000 --reload` — no port probe (localhost only), console

## Key config
- `apps/backend/.env` sets `APP_ENV=development` and `TRUST_EDGE_AUTH=False` — required for dev startup without errors
- Vite config already has `host: '0.0.0.0'`, `allowedHosts: true`, port 5000 — no changes needed
- Vite proxies `/api/*` to `localhost:8000` (BACKEND_TARGET default)

## Dependencies
- Frontend: `pnpm install` in `apps/frontend`
- Backend: `pip install -r apps/backend/requirements.txt`

## Dev behavior without DB
- App loads and UI renders correctly with empty state (no MongoDB/Redis)
- "No subjects found" is expected without MongoDB data populated

**Why:** Backend is designed to start gracefully without DB — all Optional fields, warnings only in dev mode.
