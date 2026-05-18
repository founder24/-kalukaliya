# SYRABIT v3.0 - StackBlitz Setup Guide

## ✅ Backend Verification Complete

**FastAPI server successfully tested on port 8080:**
- Health endpoint: `http://localhost:8080/health` → `{"status":"healthy","version":"3.0.0"}`
- Swagger UI: `http://localhost:8080/docs`
- OpenAPI spec: `http://localhost:8080/openapi.json`

## Architecture Confirmation

| Component | Provider | Status |
|-----------|----------|--------|
| **Frontend/Edge** | Cloudflare Workers | ✅ Configured |
| **Core Backend** | Azure Container Apps | ✅ FastAPI Ready |
| **RAG Engine** | Azure Cognitive Search | ✅ Hybrid Search Configured |
| **LLM (English)** | Vertex AI (Gemini 1.5 Pro) | ✅ Inference Only |
| **LLM (Assamese)** | Sarvam AI (OpenHathi 7B) | ✅ Inference Only |
| **Database** | MongoDB Atlas | ✅ Schema Ready |
| **Rate Limiting** | Upstash Redis | ✅ Token Bucket Ready |
| **Payments** | Razorpay | ✅ Webhook Handler Ready |
| **Email** | Resend | ✅ Transactional Ready |

## Next Steps for StackBlitz

### Option 1: Use Docker Compose (Recommended)
```bash
cd /workspace
docker-compose up -d
```

This will start:
- MongoDB (localhost:27017)
- Redis (localhost:6379)
- Backend API (localhost:8080)

### Option 2: Manual Backend Start
```bash
cd /workspace/apps/backend
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

### Option 3: Test Without Database
The backend now gracefully handles missing DB connections in dev mode.

## Environment Variables

All 42 environment variables are configured in `/workspace/apps/backend/.env` with test values.

**For Production:** Update `.env` with real credentials from:
- Azure Portal (Search, Container Apps)
- Google Cloud Console (Vertex AI)
- Sarvam AI Dashboard
- MongoDB Atlas
- Upstash Dashboard
- Razorpay Dashboard
- Resend Dashboard
- Cloudflare Dashboard

## File Structure Verified

```
/workspace
├── apps/
│   ├── backend/          # FastAPI (Azure)
│   │   ├── app/
│   │   │   ├── api/      # Routes (chat, auth, subscription)
│   │   │   ├── services/ # AI, Search, Payment clients
│   │   │   ├── models/   # MongoDB schemas
│   │   │   └── db/       # DB connections
│   │   └── requirements.txt
│   └── edge/             # Cloudflare Worker
│       └── src/
├── infra/                # Terraform/Bicep
└── docs/                 # API specs
```

## Testing Commands

```bash
# Test backend health
curl http://localhost:8080/health

# View API documentation
curl http://localhost:8080/docs

# Test chat endpoint (requires auth)
curl -X POST http://localhost:8080/api/v1/chat/ \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello", "language": "as"}'
```

## Known Limitations in StackBlitz

1. **MongoDB/Redis**: Not available natively - use Docker or mock
2. **Cloudflare Workers**: Cannot run locally in StackBlitz - deploy to CF
3. **Azure Services**: External APIs only - use test credentials

## Production Deployment

Follow the 6-phase deployment checklist in `ARCHITECTURE_VERIFICATION.md`
