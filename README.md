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
| API | `apps/api` | Cloudflare Workers, Hono, D1 | Production auth, chat/RAG, content, payments, admin/staff services |
| Backend tools | `apps/backend` | FastAPI/Python utilities | Local ingestion, migration, and retained offline reporting tools |
| Edge | `apps/edge` | Cloudflare Workers, TypeScript | API shield, rate limiting, caching, ISR/content routes, API service binding |

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
- Cloudflare Worker edge layer for API service-binding routing, KV-backed limits/cache, R2 assets, and bot/crawler routing.
- Production integrations for Cloudflare, Sarvam AI, Gemini, Razorpay, Resend, Sentry, and PostHog.

## Quick Start

### Prerequisites

- Node.js 22+
- pnpm 10+
- Python 3.12+
- Docker and Docker Compose
- Optional for deploys: Cloudflare Wrangler

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

For Docker Compose, generate a root `.env`. Each developer gets fresh local
credentials; the file is gitignored and must never be committed:

```bash
umask 077
mongo_password="$(openssl rand -hex 24)"
redis_password="$(openssl rand -hex 24)"
redis_rest_token="$(openssl rand -hex 32)"

cat > .env <<EOF
APP_ENV=development
DEBUG=True

JWT_SECRET=$(openssl rand -hex 32)
ADMIN_JWT_SECRET=$(openssl rand -hex 32)
RESET_TOKEN_SECRET=$(openssl rand -hex 32)
EDGE_SHARED_SECRET=$(openssl rand -hex 32)

MONGO_INITDB_ROOT_USERNAME=admin
MONGO_INITDB_ROOT_PASSWORD=${mongo_password}
MONGODB_URI=mongodb://admin:${mongo_password}@localhost:27017/syrabit?authSource=admin
MONGODB_DB_NAME=syrabit

REDIS_PASSWORD=${redis_password}
REDIS_REST_TOKEN=${redis_rest_token}
UPSTASH_REDIS_REST_URL=http://localhost:8079
UPSTASH_REDIS_REST_TOKEN=${redis_rest_token}

ALLOWED_ORIGINS=http://localhost:5000,http://127.0.0.1:5000
EOF

unset mongo_password redis_password redis_rest_token
```

On Replit, store the same variable names in Replit Secrets instead of putting
credential values in a tracked file.

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
         -> Cloudflare edge Worker
              |-- CORS
              |-- JWT / edge auth checks
              |-- KV rate limiting
              |-- ISR and content cache routes
               |-- API_WORKER service binding
                    -> Cloudflare API Worker
                         |-- D1 application data
                         |-- R2, KV, and Vectorize
                         |-- Workers AI and provider integrations
                         |-- Auth, payments, staff/admin workflows
```

### Production Pillars

| Area | Main provider/service | Role |
| --- | --- | --- |
| Edge and static delivery | Cloudflare Workers, Pages, KV, R2, Turnstile | Global delivery, API protection, caching, assets |
| API compute | Cloudflare Workers | Hono runtime and orchestration |
| Data | Cloudflare D1, R2, Vectorize | Users, content, chat, CMS, subscriptions, assets, RAG |
| Cache and limits | Cloudflare KV and D1 | Burst limits, counters, ephemeral state |
| AI | Sarvam AI, Gemini, Cloudflare Workers AI | Assamese/English responses, fallback, embeddings/media helpers |
| Payments | Razorpay | INR plans, orders, subscriptions, webhooks |
| Email | Resend | Transactional email |
| Observability | Sentry, PostHog, Cloudflare analytics, GitHub Actions | Errors, analytics, logs, scheduled monitors |

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

## API Worker Overview

Production API entrypoint:

- `apps/api/src/index.ts`

Core areas:

- `routes` - public, authenticated, admin, staff, SEO, content, payment, and health flows
- `services` - AI, chat, search, publishing, email, and payment integrations
- `drizzle` - D1 schema and migrations
- `middleware` - authentication, CORS, and request controls

`apps/backend` remains available for local/offline ingestion and the retained
MongoDB-dependent accuracy report; it is not a production request runtime.

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

Production secrets are documented in the Worker Wrangler configurations. Set
them directly on the relevant Worker:

```bash
cd apps/edge
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

### API Worker

The production API is the D1-backed Cloudflare Worker in `apps/api`.

```bash
pnpm --filter syrabit-api run deploy
```

The retired Google Cloud backend must not be recreated. See
`docs/gcp-backend-decommission.md`.

### Edge

```bash
pnpm --filter syrabit-edge run build
pnpm --filter syrabit-edge run deploy
```

Production worker configuration lives in `apps/edge/wrangler.toml`.

### CI/CD

GitHub Actions workflows live in `.github/workflows`, including:

- Frontend, API Worker, and edge CI
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
- Do not add a public-backend fallback around the `API_WORKER` service binding.
- Treat payment, auth, admin, and staff routes as high-risk surfaces.
- Use Wrangler, Replit, and CI secrets rather than checked-in env files.

## Observability

Operational signals come from:

- `/health`
- `/health/deep`
- `/health/chat-pipeline`
- Admin health panels
- Sentry
- PostHog
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
| Frontend API calls fail locally | Confirm the local API Worker is running, `VITE_BACKEND_URL` is set, and CORS allows `localhost:5000`. |
| API Worker starts but AI calls fail | Missing provider key or provider quota/permission issue. Check `/health` and logs. |
| Mongo errors locally | Confirm `docker compose ps`, `MONGODB_URI`, and `authSource=admin`. |
| Redis/rate-limit errors locally | Confirm `redis-rest` is running on `:8079` and `REDIS_REST_TOKEN` matches `UPSTASH_REDIS_REST_TOKEN` in the untracked `.env`. |
| Edge proxy returns upstream errors | Confirm the `API_WORKER` service binding targets `syrabit-api-prod` and `/health` is green. |
| Production docs missing | Expected when `APP_ENV=production`; use local development for `/docs`. |

More operational details:

- `docs/architecture.md`
- `docs/RUNBOOK.md`
- `docs/KEY_ROTATION.md`
- `docs/gcp-backend-decommission.md`

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
