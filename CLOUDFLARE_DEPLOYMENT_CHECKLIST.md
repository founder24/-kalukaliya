# Cloudflare Deployment Checklist - Pydantic Settings Upgrade

> **Canonical hosting plan:**
> [`docs/infra/cloud-allocation-plan.md`](./docs/infra/cloud-allocation-plan.md)
> — Cloudflare owns **frontend + edge + R2 + KV + D1 + Workers AI** in the
> four-cloud plan. Per-service inventory:
> [`docs/infra/cloud-service-breakdown.md`](./docs/infra/cloud-service-breakdown.md)
> §1.

---


## ✅ Pre-Commit Checklist

Before committing changes, complete these steps to ensure Cloudflare services are properly wired:

### 1. Generate and Deploy Secrets

```bash
# Run the wiring script
./scripts/wire_cloudflare_secrets.sh
```

This will:
- Generate 4 cryptographically secure secrets (64 chars each)
- Update `workers/edge-proxy/.dev.vars` for local development
- Push secrets to Cloudflare Workers (production & preview)
- Output backend environment variables

### 2. Save Generated Secrets

The script will output these values - **save them immediately**:

```bash
# Add to Google Cloud Run environment variables:
CF_WORKERS_SUBDOMAIN=syrabit-edge
EDGE_WORKER_URL=https://syrabit-edge.workers.dev
ORIGIN_SHARED_SECRET=<generated-value>
WORKERS_AI_FALLBACK_SECRET=<generated-value>
WORKERS_AI_FALLBACK_SECRET_PROD=<generated-value>
D1_SYNC_SECRET=<generated-value>
```

### 3. Deploy Backend with New Environment Variables

```bash
# Redeploy Cloud Run service with new env vars
gcloud run deploy syrabit-backend \
  --update-env-vars \
    CF_WORKERS_SUBDOMAIN=syrabit-edge,\
    EDGE_WORKER_URL=https://syrabit-edge.workers.dev,\
    ORIGIN_SHARED_SECRET=<value>,\
    WORKERS_AI_FALLBACK_SECRET=<value>,\
    WORKERS_AI_FALLBACK_SECRET_PROD=<value>,\
    D1_SYNC_SECRET=<value>
```

### 4. Verify Deployment

```bash
# Test backend health
curl https://your-backend-url/api/health

# Test edge worker origin auth
curl -H "X-Origin-Auth: <ORIGIN_SHARED_SECRET>" \
  https://syrabit-edge.workers.dev/health

# Test AI fallback route
curl -H "X-Edge-AI-Secret: <WORKERS_AI_FALLBACK_SECRET>" \
  https://syrabit-edge.workers.dev/api/ai/fallback/chat
```

### 5. Update Cloudflare Pages (if applicable)

```bash
# Apply Pages configuration
cd workers/edge-proxy
# Update .pages-vars.json with your actual values for:
# - VITE_GA4_ID
# - VITE_TURNSTILE_SITE_KEY
# - VITE_FIREBASE_* variables
```

## 🔐 Secret Inventory

| Secret | Worker Env | Backend Env Var | Purpose |
|--------|-----------|-----------------|---------|
| `D1_SYNC_SECRET` | `D1_SYNC_SECRET` | `D1_SYNC_SECRET` | D1 sync authentication |
| `BACKEND_ORIGIN_SECRET` | `BACKEND_ORIGIN_SECRET` | `ORIGIN_SHARED_SECRET` | Edge → Backend origin auth |
| `EDGE_AI_FALLBACK_SECRET` | `EDGE_AI_FALLBACK_SECRET` | `WORKERS_AI_FALLBACK_SECRET` | AI fallback route protection |
| N/A | N/A | `WORKERS_AI_FALLBACK_SECRET_PROD` | Production-specific AI fallback |

## 📋 Commit Checklist

- [ ] Secrets generated and saved to password manager
- [ ] Secrets deployed to Cloudflare Workers (prod & preview)
- [ ] Backend environment variables updated in Cloud Run
- [ ] Backend redeployed successfully
- [ ] Health checks passing
- [ ] `.dev.vars` file created for local development
- [ ] `.pages-vars.json` updated with production values
- [ ] Documentation updated

## 🚀 Post-Commit Deployment Flow

After committing:

1. **CI/CD will automatically:**
   - Run tests with pydantic-settings validation
   - Deploy Edge Worker if `workers/edge-proxy/` changed
   - Deploy Pages if frontend code changed

2. **Manual steps required:**
   - Redeploy backend if `config.py` or requirements changed
   - Update Cloud Run environment variables if new vars added

## 🔄 Rollback Procedure

If issues occur after deployment:

```bash
# Rollback Edge Worker
wrangler rollback

# Rollback Preview
wrangler rollback --env preview

# Rollback Cloud Run
gcloud run services update-traffic syrabit-backend \
  --to-revisions=<previous-revision>=100
```

## 📞 Support

For issues with:
- **Worker secrets**: Check `wrangler secret list` and `wrangler secret list --env preview`
- **Backend config**: Review Cloud Run environment variables in GCP Console
- **Pages vars**: Verify `.pages-vars.json` and Pages build settings
- **Pydantic validation**: Check backend logs for validation errors on startup

---

**Last Updated**: $(date +%Y-%m-%d)
**Worker Name**: syrabit-edge
**Worker Subdomain**: syrabit-edge.workers.dev
