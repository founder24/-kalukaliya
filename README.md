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
| P2 | Google Cloud | Cloud Run (asia-south1, min-instances=1) | FastAPI backend, orchestration |
| P3 | Vertex AI | Vertex AI Search (Discovery Engine) - Hybrid RAG | BM25 + Vector + Semantic Rerank |
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
- **SEO Score 99/100**: Triple-stack structured data, Knowledge Graph linking, RSS/JSON feeds
- **Always-on backend**: min-instances=1, no cold starts
- **Vertex Search RAG**: Hybrid retrieval with Discovery Engine

## Request Flow & Deployment Topology

```
Browser (syrabit.ai)
  |
  |-- Static pages --> Cloudflare Pages (syrabit.ai)
  |
  +-- API calls --> api.syrabit.ai
                    |
                    +-- Cloudflare Worker (syrabitworker-prod)
                         |-- JWT verification
                         |-- Rate limiting (KV)
                         |-- Bot detection
                         +-- Proxy --> Cloud Run (IAM auth via service account)
                              |
                              |-- Vertex AI Search (RAG retrieval)
                              |-- Vertex AI Gemini 2.5 Flash (English)
                              +-- Sarvam AI sarvam-m (Assamese)
```

**Production URLs:**
- Frontend: `https://syrabit.ai` (Cloudflare Pages)
- API/Edge: `https://api.syrabit.ai` (Cloudflare Worker)
- Backend: `https://syrabit-backend-851687450401.asia-south1.run.app` (Cloud Run, IAM-protected)

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
- **Performance**: Lighthouse score 99/100, LCP 972ms, TBT 0ms, CLS 0.028

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

All environment variables are documented in `.env.shared`:

```bash
# Example required variables
CF_ACCOUNT_ID=acct_xxx
GCP_PROJECT_ID=your-gcp-project
GCP_REGION=asia-south1
BACKEND_URL=https://syrabit-backend-xxxxx.run.app
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net
UPSTASH_REDIS_REST_URL=https://xxx.upstash.io
VERTEX_PROJECT_ID=your-gcp-project
SARVAM_API_KEY=sk_sarvam_xxx
RAZORPAY_KEY_ID=rzp_live_xxx
JWT_SECRET=super_secret_jwt_key_32_chars_min
```

See `.env.shared` for complete list with descriptions.

## Deployment

### Backend (Google Cloud Run)

```bash
# Automated via GitHub Actions (deploy-all.yml)
# Push to main triggers:
# 1. Build Docker image
# 2. Push to Artifact Registry
# 3. Deploy to Cloud Run (min-instances=1)
# 4. Health check verification
```

- `min-instances=1` is set on Cloud Run (no cold starts)
- Cloud Run requires IAM auth: only `syrabit-edge-invoker` service account can invoke
- Edge worker authenticates via `GOOGLE_SA_KEY` (service account JSON)
- Backend deployed via `gcloud run deploy` or GitHub Actions `deploy-all.yml`

### Frontend (Cloudflare Pages)

- Deployed via Cloudflare Pages (project: `syrabitfrontend`)
- Production URL: `https://syrabit.ai`

### Edge (Cloudflare Workers)

```bash
# Automated via GitHub Actions
# Push to main triggers:
# 1. Install Wrangler
# 2. Deploy Worker to production
```

- Deployed via `wrangler deploy --env production`

### Manual Deployment

```bash
# Backend
gcloud builds submit --tag REGION-docker.pkg.dev/PROJECT_ID/syrabit/backend:latest apps/backend
gcloud run deploy syrabit-backend --image REGION-docker.pkg.dev/PROJECT_ID/syrabit/backend:latest --region REGION

# Edge
cd apps/edge
wrangler deploy --env production
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

- **Errors**: Sentry (`SENTRY_DSN`)
- **Analytics**: PostHog (`POSTHOG_API_KEY`)
- **Logs**: Cloud Run logs via Cloud Logging
- **Metrics**: Upstash dashboard, GCP Cloud Monitoring

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
| Lighthouse Score | >90 | 99 |
| LCP | <2500ms | 972ms |
| CLS | <0.1 | 0.028 |
| Uptime | 99.9% | Monitored every 15min |
| RAG Recall@5 | >85% | Vertex Search hybrid |

## Troubleshooting

### Common Issues

**1. Vertex AI Search connection failed**
```bash
# Verify GOOGLE_APPLICATION_CREDENTIALS_JSON is set correctly
# Check that the service account has Discovery Engine permissions
# Test: gcloud ai-platform operations list --project=PROJECT_ID
```

**2. Rate limiting not working**
```bash
# Check Upstash connection
# Verify UPSTASH_REDIS_REST_TOKEN
# Test: redis-cli -u $UPSTASH_REDIS_REST_URL ping
```

**3. High latency**
```bash
# Check Vertex Search reranker configuration
# Verify embedding dimensions match (1536)
# Monitor Upstash latency (<20ms target)
```

## License

Proprietary - All rights reserved

## Contributing

This is a private repository. Contact the founding team for access.

---

**Built for Assamese students**

*For detailed architecture, see [docs/architecture.md](docs/architecture.md)*
