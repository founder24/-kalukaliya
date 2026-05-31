# Syrabit AI - Educational Assistant for Assamese Students

**Version**: 3.0 | **Classification**: Production Ready | **Target Scale**: 100k DAU

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 22+
- Docker & Docker Compose
- Cloudflare Account
- GCP Project
- MongoDB Atlas Cluster

### Local Development Setup

```bash
# 1. Clone repository
git clone https://github.com/founder24/-kalukaliya.git
cd syrabit-monorepo

# 2. Copy environment template
cp .env.shared .env
# Edit .env with your credentials

# 3. Start local services (MongoDB, Redis)
docker-compose up -d

# 4. Initialize database indexes
python infra/scripts/migrate-users.py

# 5. Run backend
cd apps/backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 6. Run edge worker (separate terminal)
cd apps/edge
npm install
npx wrangler dev
```

Visit `http://localhost:8000/docs` for API documentation.

## Architecture

Syrabit uses a **9-Pillar Hybrid Architecture**:

| Pillar | Provider | Service | Purpose |
|--------|----------|---------|---------|
| P1 | Cloudflare | Workers, Turnstile, R2 | Edge shield, bot protection, static assets |
| P2 | Google Cloud | Cloud Run | FastAPI backend, orchestration |
| P3 | Vertex AI | Search (Discovery Engine) | Hybrid RAG (BM25 + Vector + Rerank) |
| P4 | MongoDB | Atlas M10 | User profiles, chat history, subscriptions |
| P5 | Upstash | Redis Global | Rate limiting, real-time counters |
| P6 | Vertex AI | Gemini 2.5 Flash | English chat + RAG grounding |
| P7 | Sarvam AI | Sarvam-m | Assamese chat (with Vertex fallback) |
| P8 | Razorpay | Payment Gateway | INR transactions, subscriptions |
| P9 | Resend | Email API | Transactional emails |

### Key Features

- **Hybrid RAG**: +35% quality gain over vector-only search
- **Sub-2s TTFB**: Streaming responses with min-instances=1
- **Multi-language**: Automatic Assamese/English detection
- **Rate Limiting**: Token bucket algorithm via Upstash
- **Bot Protection**: Cloudflare Turnstile integration
- **Payment Ready**: Razorpay subscription management
- **SEO Score 100/100**: Triple-stack structured data, Knowledge Graph linking, RSS/JSON feeds
- **Always-on backend**: min-instances=1, no cold starts
- **Vertex Search RAG**: Hybrid retrieval with Discovery Engine

## Request Flow & Deployment Topology

```
Browser (syrabit.ai)
  |
  |-- Static pages --> Cloudflare Pages
  |
  +-- API calls --> Cloudflare Worker
                    |-- JWT verification
                    |-- Rate limiting (KV)
                    |-- Bot detection
                    +-- Proxy --> Cloud Run (IAM-protected)
                         |-- Vertex AI Search (RAG retrieval)
                         |-- Vertex AI Gemini (English)
                         +-- Sarvam AI (Assamese)
```

**Production URLs:**
- Frontend: `https://syrabit.ai` (Cloudflare Pages)
- API/Edge: `https://api.syrabit.ai` (Cloudflare Worker)
- Backend: Cloud Run (IAM-protected, not publicly accessible)

## Content Hierarchy

```
Board (SEBA, AHSEC, CBSE)
  +-- Class (Class 9, Class 10, Class 11, Class 12, Degree)
       +-- Stream (Science, Commerce, Arts) [optional, for Class 11+]
            +-- Subject (Physics, Chemistry, Mathematics, etc.)
                 +-- Chapter (e.g., "Chemical Reactions and Equations")
                      +-- Topic (e.g., "Balancing Chemical Equations")
```

Content types per chapter: Notes, MCQs, Definitions, Important Questions, PYQs, Summary

API: `GET /api/v1/content/boards` -> `classes` -> `streams` -> `subjects` -> `chapters/{subjectId}`

MongoDB model: `KnowledgeObject` with `metadata.board`, `metadata.class_level`, `metadata.subject`, `metadata.chapter`, `metadata.topic`

## SEO / GEO / AEO Infrastructure

- **Structured Data**: JSON-LD + Microdata + RDFa triple-stack on all pages
- **Knowledge Graph**: Entity linking to Wikidata/DBpedia for AHSEC, SEBA, CBSE, subjects
- **Sitemaps**: Dynamic sitemap index at `/sitemap.xml` with sub-sitemaps (static, subjects, chapters, topics)
- **Feeds**: RSS 2.0 (`/feed.xml`) + JSON Feed v1.1 (`/feed.json`) for AI crawlers
- **AI Discovery**: `ai.txt`, `llms.txt`, `robots.txt` with GPTBot/PerplexityBot/ClaudeBot directives
- **Performance**: PageSpeed Insights mobile 93/100, Accessibility 94, Best Practices 92, SEO 100/100

## Project Structure

```
syrabit-monorepo/
├── apps/
│   ├── edge/              # Cloudflare Worker (TypeScript)
│   │   ├── src/
│   │   │   ├── index.ts   # Main entry point
│   │   │   ├── middleware/# Auth, bot, CORS
│   │   │   └── routes/    # Proxy, assets
│   │   └── wrangler.toml
│   └── backend/           # FastAPI (Python)
│       ├── app/
│       │   ├── api/v1/    # REST endpoints
│       │   ├── core/      # Security, rate limiting
│       │   ├── db/        # MongoDB, Redis connections
│       │   ├── models/    # Pydantic schemas
│       │   └── services/  # AI, search, payments
│       └── Dockerfile
├── infra/
│   ├── gcp/               # Cloud Run service definition, Vertex Search schema
│   └── scripts/           # Deployment & migration scripts
├── .github/workflows/     # CI/CD pipelines
├── docs/                  # Architecture documentation
└── docker-compose.yml     # Local development
```

## Configuration

All environment variables are documented in `.env.shared`. Key categories:

- **Cloud providers**: Cloudflare, GCP, MongoDB Atlas, Upstash
- **AI services**: Vertex AI, Sarvam AI
- **Payments**: Razorpay
- **Auth**: JWT secrets, admin credentials
- **Observability**: Sentry, PostHog

See `.env.shared` for the complete list with descriptions. Never commit actual secrets to the repository.

## Deployment

### Backend (Google Cloud Run)

```bash
# Automated via GitHub Actions (deploy-all.yml)
# Push to main triggers: build, push to registry, deploy, health check
```

- Always-on (min-instances=1, no cold starts)
- IAM-protected (only authorized service accounts can invoke)
- Deployed via GitHub Actions or `gcloud run deploy`

### Frontend (Cloudflare Pages)

- Deployed via Cloudflare Pages
- Production URL: `https://syrabit.ai`

### Edge (Cloudflare Workers)

```bash
# Automated via GitHub Actions
# Push to main triggers deploy
```

- Deployed via `wrangler deploy --env production`

### Manual Deployment

```bash
# Backend
gcloud run deploy syrabit-backend --source=apps/backend --region=REGION --min-instances=1

# Edge
cd apps/edge && wrangler deploy --env production
```

## Testing

```bash
# Backend tests
cd apps/backend
pytest tests/

# Load testing
locust --host=http://localhost:8000

# RAG quality testing
python infra/scripts/test-rag-quality.py
```

## Monitoring

- **Errors**: Sentry
- **Analytics**: PostHog
- **Logs**: Cloud Run logs via Cloud Logging
- **Uptime**: GitHub Actions monitor (every 15 min, auto-creates incident issues)

### Key Alerts

- Error rate > 1%
- P95 latency > 2s
- Rate limit violations > 100/min
- Payment webhook failures

## Security & Compliance

- **Data Residency**: All data in India regions (GCP asia-south1, MongoDB Mumbai)
- **GDPR/DPDP**: Right to delete, data portability
- **Rate Limiting**: DDoS protection
- **Bot Mitigation**: Turnstile verification
- **Secrets Management**: GCP Secret Manager

## Performance Targets

| Metric | Target | Current |
|--------|--------|---------|
| Chat TTFB | <2s | ~2s (streaming) |
| PSI Performance (mobile) | >90 | 93 |
| PSI Accessibility | >90 | 94 |
| PSI Best Practices | >90 | 92 |
| PSI SEO | >95 | 100 |
| Uptime | 99.9% | Monitored every 15min |
| RAG Recall@5 | >85% | Vertex Search hybrid |

## Troubleshooting

See internal documentation for common issues and resolution steps.

## License

Proprietary - All rights reserved

## Contributing

This is a private repository. Contact the founding team for access.

---

**Built for Assamese students**

*For detailed architecture, see [docs/architecture.md](docs/architecture.md)*
