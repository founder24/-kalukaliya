# Syrabit.ai

AI-powered study, content, and educational browsing platform for Assam Board students.

Syrabit.ai helps students prepare for SEBA, AHSEC, Degree, and related board exams with curriculum-aware content, multilingual AI assistance, staff/admin publishing tools, SEO-ready learning pages, payments, analytics, and an edge-protected production architecture.

- **Status:** Production-oriented private monorepo
- **Primary users:** Students, staff content editors, administrators
- **Production site:** `https://syrabit.ai`
- **API edge:** `https://api.syrabit.ai`

## What This Repo Contains

This is a full-stack monorepo with three main runtime surfaces:

| App | Path | Stack | Purpose |
| --- | --- | --- | --- |
| Frontend | `apps/frontend` | React 18, Vite, Tailwind, React Router | Student app, public pages, library, chat, profile, admin, staff UI |
| Backend | `apps/backend` | FastAPI, MongoDB, Redis, AI providers | Auth, chat/RAG, content APIs, payments, admin/staff services |
| Edge | `apps/edge` | Cloudflare Workers, TypeScript | API shield, rate limiting, caching, ISR/content routes, origin proxy |

Local support services are defined in `docker-compose.yml`:

- MongoDB 7
- Redis 7
- Redis REST bridge compatible with Upstash-style calls
- Optional backend container

## Highlights

- Curriculum-aware content hierarchy for boards, classes, streams, subjects, chapters, topics, and question papers.
- AI chat and grounded answer flows with multilingual support for English and Assamese.
- Staff CMS for chapter notes, Q&A, PYQs, RAG indexing, and content publishing.
- Admin dashboard for health, users, billing, SEO, analytics, logs, AI routing, and infrastructure controls.
- Public library, chapter, learn, PYQ, pricing, terms, privacy, status, and marketing pages.
- SEO/GEO/AEO infrastructure: sitemaps, feeds, structured data, crawler-friendly prerendering, and IndexNow hooks.
- Cloudflare Worker edge layer for API proxying, KV-backed limits/cache, R2 assets, and bot/crawler routing.
- Production integrations for Cloudflare, Google Cloud, MongoDB Atlas, Sarvam AI, Gemini, Razorpay, Resend, Sentry, and PostHog.

## Quick Start

### Prerequisites

- Node.js 22+
- pnpm 10+
- Python 3.12+
- Docker and Docker Compose
- Optional for deploys: Cloudflare Wrangler, Google Cloud CLI

Enable pnpm with Corepack if it is not already available:

```bash
corepack enable
corepack prepare pnpm@10.26.1 --activate
```

### 1. Install Dependencies

```bash
pnpm install

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r apps/backend/requirements.txt
```

### 2. Create Local Environment Files

For Docker Compose, create a root `.env`:

```bash
APP_ENV=development
DEBUG=True

JWT_SECRET=local-dev-jwt-secret-change-me-32chars
ADMIN_JWT_SECRET=local-dev-admin-secret-change-me-32chars
RESET_TOKEN_SECRET=local-dev-reset-secret-change-me-32chars
EDGE_SHARED_SECRET=local-dev-edge-secret-change-me-32chars

MONGODB_URI=mongodb://admin:localdevpassword@localhost:27017/syrabit?authSource=admin
MONGODB_DB_NAME=syrabit

UPSTASH_REDIS_REST_URL=http://localhost:8079
UPSTASH_REDIS_REST_TOKEN=local_dev_token

ALLOWED_ORIGINS=http://localhost:5000,http://127.0.0.1:5000
```

For the frontend, create `apps/frontend/.env.local`:

```bash
VITE_BACKEND_URL=http://localhost:8000
VITE_WORKER_API_URL=http://localhost:8787
```

Provider keys such as `SARVAM_API_KEY`, `GEMINI_API_KEY`, `RAZORPAY_KEY_ID`, `RESEND_API_KEY`, `SENTRY_DSN`, and Cloudflare/GCP credentials are only needed when you exercise those integrations.

### 3. Start Local Services

```bash
docker compose up -d mongo redis redis-rest
```

### 4. Run the Backend

```bash
cd apps/backend
cp ../../.env .env
uvicorn app.main:app --reload --port 8000
```

Useful backend URLs:

- Health: `http://localhost:8000/health`
- Deep health: `http://localhost:8000/health/deep`
- API docs in development: `http://localhost:8000/docs`

### 5. Run the Frontend

In a second terminal:

```bash
pnpm --filter @workspace/syrabit run dev
```

Open `http://localhost:5000`.

### 6. Run the Edge Worker

In a third terminal:

```bash
pnpm --filter syrabit-edge run dev
```

The worker defaults to `http://localhost:8787` and proxies backend traffic to `http://localhost:8000`.

## Common Commands

| Task | Command |
| --- | --- |
| Install JS deps | `pnpm install` |
| Frontend dev server | `pnpm --filter @workspace/syrabit run dev` |
| Frontend client build | `pnpm --filter @workspace/syrabit run build:client` |
| Frontend full build | `pnpm --filter @workspace/syrabit run build` |
| Frontend tests | `pnpm --filter @workspace/syrabit run test` |
| Edge dev worker | `pnpm --filter syrabit-edge run dev` |
| Edge typecheck | `pnpm --filter syrabit-edge run build` |
| Edge tests | `pnpm --filter syrabit-edge run test` |
| Backend tests | `cd apps/backend && pytest` |
| Backend dev server | `cd apps/backend && uvicorn app.main:app --reload --port 8000` |
| Local Mongo/Redis | `docker compose up -d mongo redis redis-rest` |
| Full Docker backend stack | `docker compose up --build backend` |

## Architecture

```text
Browser
  |
  |-- Public app, library, SEO pages
  |      -> Cloudflare Pages / Vite build
  |
  |-- API requests
         -> Cloudflare Worker
              |-- CORS
              |-- JWT / edge auth checks
              |-- KV rate limiting
              |-- ISR and content cache routes
              |-- API proxy
                   -> FastAPI backend
                        |-- MongoDB / Beanie models
                        |-- Redis / Upstash-compatible cache and limits
                        |-- AI routing and RAG services
                        |-- Payment webhooks and subscriptions
                        |-- Staff/admin content workflows
```

### Production Pillars

| Area | Main provider/service | Role |
| --- | --- | --- |
| Edge and static delivery | Cloudflare Workers, Pages, KV, R2, Turnstile | Global delivery, API protection, caching, assets |
| Backend compute | Google Cloud Run | FastAPI runtime and orchestration |
| Data | MongoDB Atlas | Users, content, chat, CMS, subscriptions, analytics snapshots |
| Cache and limits | Redis / Upstash-compatible REST | Burst limits, counters, ephemeral state |
| AI | Sarvam AI, Gemini, Cloudflare Workers AI | Assamese/English responses, fallback, embeddings/media helpers |
| Payments | Razorpay | INR plans, orders, subscriptions, webhooks |
| Email | Resend | Transactional email |
| Observability | Sentry, PostHog, Cloud Logging, GitHub Actions | Errors, analytics, logs, scheduled monitors |

## Content Model

```text
Board
  -> Class
      -> Stream (optional)
          -> Subject
              -> Chapter
                  -> Topic
                      -> Notes
                      -> Q&A
                      -> MCQs
                      -> Definitions
                      -> Important Questions
                      -> PYQs
```

Important backend models live in `apps/backend/app/models`, including:

- `content.py`
- `cms.py`
- `knowledge.py`
- `rag.py`
- `chat.py`
- `user.py`
- `quota.py`

Key API areas are registered from `apps/backend/app/main.py`, including chat, auth, users, education/browser routes, content, SEO, IndexNow, payments, admin, staff, and health.

## Frontend Routes

The routed React app lives in `apps/frontend/src/App.jsx`.

Major user-facing areas:

- `/library` and `/browser` - public subject/library entry points
- `/:board/:classSlug/:subjectSlug` - SEO subject pages
- `/:board/:classSlug/:subjectSlug/:chapterSlug` - SEO chapter pages
- `/learn/:slug` - CMS learning pages
- `/pyq/:slug` - question paper replica pages
- `/chat` - AI chat
- `/browse` and `/browser-tabs` - educational browser
- `/profile`, `/history`, `/notebook`, `/flashcards`, `/guardian` - logged-in app tools
- `/admin` - admin console
- `/staff` - staff content portal

Shared layouts live in `apps/frontend/src/components/layout`.

## Backend Overview

Backend entrypoint:

- `apps/backend/app/main.py`

Core areas:

- `api/v1` - REST routers for public, authenticated, admin, staff, SEO, content, payment, and health flows
- `services` - AI, chat, SEO, publishing, memory, content generation, dead-letter handling
- `models` - Pydantic/Beanie data models
- `db` - MongoDB and Redis clients
- `core` - auth, security, rate limiting, telemetry, secrets, circuit breakers
- `scripts` - ingestion, publishing, admin, migration, translation, and repair tasks
- `tests` - backend unit/integration coverage

## Edge Worker Overview

Edge entrypoint:

- `apps/edge/src/index.ts`

Important areas:

- `middleware/cors.ts`
- `middleware/jwt.ts`
- `middleware/rate-limit.ts`
- `routes/api-proxy.ts`
- `routes/assets.ts`
- `routes/content-kv.ts`
- `routes/isr.ts`
- `routes/robots.ts`

Production secrets are documented in `apps/edge/wrangler.toml`. Set them with Wrangler:

```bash
cd apps/edge
npx wrangler secret put BACKEND_URL --env production
npx wrangler secret put JWT_SECRET --env production
npx wrangler secret put EDGE_SHARED_SECRET --env production
```

## Deployment

### Frontend

The frontend builds from `apps/frontend` and outputs to `apps/frontend/dist`.

```bash
pnpm --filter @workspace/syrabit run build
```

Production delivery is designed for Cloudflare Pages with `VITE_BACKEND_URL=https://api.syrabit.ai`.

### Backend

The backend Docker image is defined in `apps/backend/Dockerfile`.

```bash
gcloud run deploy syrabit-backend \
  --source=apps/backend \
  --region=asia-south1 \
  --min-instances=1
```

Cloud Run should receive secrets through environment variables or Secret Manager. Production docs are disabled automatically when `APP_ENV=production`.

### Edge

```bash
pnpm --filter syrabit-edge run build
pnpm --filter syrabit-edge run deploy
```

Production worker configuration lives in `apps/edge/wrangler.toml`.

### CI/CD

GitHub Actions workflows live in `.github/workflows`, including:

- Frontend, backend, and edge CI
- Full deploy workflows
- Smoke tests
- Uptime monitoring
- Security, dependency, container, drift, and performance checks
- Content translation and accuracy jobs

## Testing Strategy

Run the smallest test surface that covers your change first, then widen if needed.

```bash
# Backend
cd apps/backend
pytest

# Frontend
pnpm --filter @workspace/syrabit run test

# Edge
pnpm --filter syrabit-edge run test
```

Recommended checks before shipping:

- Backend API or model change: backend tests plus a focused smoke test
- Frontend route/layout change: frontend tests plus a local browser pass
- Edge middleware/proxy change: edge tests plus local Wrangler verification
- Payment/auth/security change: include negative-path tests and webhook/auth checks
- SEO/content change: verify generated HTML, sitemap/feed impact, and canonical URLs

## Security Notes

- Never commit real secrets, service-account JSON, webhook secrets, or production tokens.
- Keep `JWT_SECRET`, `ADMIN_JWT_SECRET`, and `EDGE_SHARED_SECRET` distinct in production.
- Keep Cloud Run private when using the Worker as the public API edge.
- Treat payment, auth, admin, and staff routes as high-risk surfaces.
- Use Secret Manager, Wrangler secrets, and CI secrets rather than checked-in env files.

## Observability

Operational signals come from:

- `/health`
- `/health/deep`
- `/health/chat-pipeline`
- Admin health panels
- Sentry
- PostHog
- Cloud Run logs
- Cloudflare analytics
- GitHub scheduled monitors

Common alert themes:

- Elevated 5xx or exception rate
- P95/P99 latency regressions
- AI provider degradation or fallback exhaustion
- Payment webhook failures
- RAG/index freshness issues
- Crawler, sitemap, or SEO drift

## Troubleshooting

| Symptom | First checks |
| --- | --- |
| Frontend API calls fail locally | Confirm backend is on `:8000`, `VITE_BACKEND_URL` is set, and CORS allows `localhost:5000`. |
| Backend starts but AI calls fail | Missing provider key or provider quota/permission issue. Check `/health/deep` and logs. |
| Mongo errors locally | Confirm `docker compose ps`, `MONGODB_URI`, and `authSource=admin`. |
| Redis/rate-limit errors locally | Confirm `redis-rest` is running on `:8079` with `local_dev_token`. |
| Edge proxy returns upstream errors | Confirm `BACKEND_URL` points to the backend and the backend health endpoint is green. |
| Production docs missing | Expected when `APP_ENV=production`; use local development for `/docs`. |

More operational details:

- `docs/architecture.md`
- `docs/RUNBOOK.md`
- `docs/KEY_ROTATION.md`
- `docs/architecture-audit.md`

## Project Map

```text
.
|-- apps
|   |-- backend
|   |   |-- app
|   |   |   |-- api
|   |   |   |-- core
|   |   |   |-- db
|   |   |   |-- models
|   |   |   `-- services
|   |   |-- scripts
|   |   |-- tests
|   |   |-- Dockerfile
|   |   `-- requirements.txt
|   |-- edge
|   |   |-- src
|   |   |-- tests
|   |   |-- package.json
|   |   `-- wrangler.toml
|   `-- frontend
|       |-- src
|       |   |-- components
|       |   |-- context
|       |   |-- hooks
|       |   |-- pages
|       |   |-- utils
|       |   `-- App.jsx
|       |-- package.json
|       `-- vite.config.js
|-- docs
|-- infra
|-- scripts
|-- docker-compose.yml
|-- package.json
|-- pnpm-workspace.yaml
|-- pyproject.toml
`-- run_tests.sh
```

## Contributing

This is a private repository. Keep changes focused, run the relevant checks, and document operational or configuration changes in the same PR.

Before opening a PR:

1. Run targeted tests for the changed area.
2. Check that no secrets or generated local env files were added.
3. Update docs when commands, environment variables, routes, or deployment behavior change.
4. Include screenshots for visible frontend changes.

## License

Proprietary. All rights reserved.

## Credits

Developed by [Ayan Bhaumik](https://ayanbhaumik.in/).

Built for Assam Board students across SEBA, AHSEC, Degree, and beyond.
