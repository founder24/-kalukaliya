# Cloudflare Deployment Wiring Guide
## Pydantic-Settings Configuration Integration

> ⚠️ **HISTORICAL — Railway-era wiring.** Task #336 migrated the backend
> origin off Railway onto Digital Ocean App Platform. Wherever this
> document references Railway env vars or `railway variables set …`,
> the equivalent today is `bash scripts/digitalocean.sh var-set syrabit-backend KEY=VAL`
> (canonical runbook: [`docs/DIGITALOCEAN-DEPLOYMENT.md`](docs/DIGITALOCEAN-DEPLOYMENT.md)).
> The Pydantic-Settings shape itself is unchanged.

> **Canonical hosting plan:**
> [`docs/infra/cloud-allocation-plan.md`](./docs/infra/cloud-allocation-plan.md)
> — Cloudflare owns **frontend + edge + R2 + KV + D1 + Vectorize + Workers
> AI** in the three-cloud plan. Backend origin is **AWS App Runner**;
> workers + cron live on **Azure Container Apps**. Per-service inventory:
> [`docs/infra/cloud-service-breakdown.md`](./docs/infra/cloud-service-breakdown.md)
> §1.

---


**Task**: Wire all Cloudflare Pages, Edge Worker, and Proxy Edge Worker deployments to support the new centralized pydantic-settings configuration system.

---

## 1. Current Architecture State

### 1.1 Components
- **Backend**: `/workspace/artifacts/syrabit-backend/` (FastAPI on Cloud Run/Railway)
- **Edge Worker**: `/workspace/workers/edge-proxy/` (Wrangler, syrabit-edge)
- **Frontend Pages**: `/workspace/artifacts/syrabit/` (Vite, Cloudflare Pages)
- **Config System**: `/workspace/artifacts/syrabit-backend/config.py` (pydantic-settings)

### 1.2 Existing Workflows
- Edge Worker CI/CD: `.github/workflows/edge-proxy-deploy.yml`
- Pages Config Script: `artifacts/syrabit/scripts/apply-pages-config.mjs`
- Backend Deploy: Railway/Cloud Run with Secret Manager

---

## 2. Required Environment Variables by Component

### 2.1 Edge Worker Secrets (`workers/edge-proxy/wrangler.toml`)

Set via `wrangler secret put` from `/workspace/workers/edge-proxy/`:

```bash
# Authentication & Sync
wrangler secret put D1_SYNC_SECRET                    # D1 sync auth with backend
wrangler secret put BACKEND_ORIGIN_SECRET             # Cloud Run origin auth (X-Origin-Auth)
wrangler secret put EDGE_AI_FALLBACK_SECRET           # Workers AI fallback protection

# Synthetic Probe (Task #708, #898)
wrangler secret put SYNTHETIC_PROBE_CF_ACCESS_CLIENT_ID
wrangler secret put SYNTHETIC_PROBE_CF_ACCESS_CLIENT_SECRET
wrangler secret put SYNTHETIC_PROBE_ADMIN_JWT
wrangler secret put SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL

# Preview Environment (separate secrets)
wrangler secret put D1_SYNC_SECRET --env preview
wrangler secret put BACKEND_ORIGIN_SECRET --env preview
wrangler secret put EDGE_AI_FALLBACK_SECRET --env preview
```

### 2.2 Backend Runtime Variables (Cloud Run/Railway)

These must match the pydantic-settings schema in `config.py`:

```bash
# Shared Secrets (must match Edge Worker values)
D1_SYNC_SECRET=<value-from-wrangler>
ORIGIN_SHARED_SECRET=<backend-value-matches-BACKEND_ORIGIN_SECRET>
WORKERS_AI_FALLBACK_SECRET=<backend-value-matches-EDGE_AI_FALLBACK_SECRET>

# Edge Worker URLs for D1 sync fan-out (Task #879)
EDGE_WORKER_URL=https://syrabit-edge.<account>.workers.dev
EDGE_WORKER_PREVIEW_URL=https://syrabit-edge-preview.<account>.workers.dev
D1_SYNC_SECRET_PREVIEW=<preview-env-secret-value>

# Cloudflare Integration
CLOUDFLARE_ACCOUNT_ID=<account-id>
CLOUDFLARE_ANALYTICS_TOKEN=<analytics-token>
CF_ZONE_ID=<zone-id>
VECTORIZE_INDEX_NAME=syllabus-index-v2
```

### 2.3 Cloudflare Pages Build Variables

Public build-time variables set via `apply-pages-config.mjs`:

```bash
# Required Production Env Vars (from apply-pages-config.mjs)
NODE_ENV=production
NODE_VERSION=22
VITE_BACKEND_URL=https://api.syrabit.ai
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
PUPPETEER_SKIP_DOWNLOAD=1
VITE_GA4_ID=G-XXXXXXXXXX  # Must match /^G-[A-Z0-9]{6,12}$/

# Optional but Recommended
VITE_WORKER_API_URL=https://api.syrabit.ai
VITE_TURNSTILE_SITE_KEY=<turnstile-site-key>
VITE_FIREBASE_API_KEY=<firebase-key>
VITE_FIREBASE_AUTH_DOMAIN=<auth-domain>
VITE_FIREBASE_PROJECT_ID=<project-id>
```

**DO NOT SET ON PAGES** (security risk - public build logs):
```bash
CF_ANALYTICS_API_TOKEN    # Backend only
CF_ZONE_ID                # Backend only
D1_SYNC_SECRET            # Backend + Worker only
SUPABASE_*                # Backend only
ADMIN_*                   # Backend only
*_API_KEY                 # Backend only
JWT_SECRET                # Backend only
SESSION_SECRET            # Backend only
MONGO_URL                 # Backend only
```

---

## 3. Configuration Validation Rules

### 3.1 Pydantic Settings Schema (Backend)

From `config.py`, the following validation rules apply:

```python
class EnvSettings(BaseSettings):
    # JWT Secrets - validated minimum length
    JWT_SECRET: str = Field(..., min_length=64, description="Primary JWT signing key")
    SESSION_SECRET: str = Field(..., min_length=64, description="Session encryption key")
    
    # Database connections - required for production
    MONGO_URL: str = Field(..., description="MongoDB connection string")
    DATABASE_URL: Optional[str] = Field(None, description="PostgreSQL URL")
    
    # Shared secrets must be 32+ chars
    ORIGIN_SHARED_SECRET: str = Field(..., min_length=32)
    WORKERS_AI_FALLBACK_SECRET: str = Field(..., min_length=32)
    D1_SYNC_SECRET: str = Field(..., min_length=32)
    
    # Email configuration
    RESEND_API_KEY: str = Field(..., description="Resend email API key")
    
    # LLM Provider keys (at least one required)
    OPENAI_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    # ... etc
    
    class Config:
        env_file = ".env"
        case_sensitive = True
```

### 3.2 Startup Validation

The backend validates critical variables on import:

```python
# config.py startup validation
if not env.JWT_SECRET or len(env.JWT_SECRET) < 64:
    raise ValueError("JWT_SECRET must be at least 64 characters")
    
if not env.MONGO_URL and os.getenv("PYTEST_CURRENT_TEST") is None:
    raise ValueError("MONGO_URL is required for production")
```

---

## 4. Deployment Workflows

### 4.1 Edge Worker Deployment Pipeline

**File**: `.github/workflows/edge-proxy-deploy.yml`

```yaml
Pipeline Flow:
push → deploy-preview → smoke-preview → deploy-prod (manual approval) → smoke-prod → [auto-rollback if fails]

Required GitHub Secrets:
- CLOUDFLARE_API_TOKEN (Workers Scripts:Edit, KV:Edit, D1:Edit, AI:Edit)
- CLOUDFLARE_ACCOUNT_ID
- D1_SYNC_SECRET (preview value)
- AI_FALLBACK_SECRET (preview value)
- D1_SYNC_SECRET_PROD (prod value)
- AI_FALLBACK_SECRET_PROD (prod value)

Required GitHub Variables:
- CF_WORKERS_SUBDOMAIN (e.g., "syrabit-edge")

Required GitHub Environment:
- production (with required reviewers)
```

**Smoke Test Coverage**:
- ✅ KV bindings enumeration (`/api/edge/kv-usage`)
- ✅ AI fallback endpoint (`/api/ai/fallback/chat`)
- ✅ D1 content tables check
- ✅ Bot cache routing
- ✅ Origin auth header injection

### 4.2 Pages Deployment Configuration

**Script**: `artifacts/syrabit/scripts/apply-pages-config.mjs`

```bash
# Dry run (see planned changes)
node artifacts/syrabit/scripts/apply-pages-config.mjs

# Apply configuration
export CLOUDFLARE_ACCOUNT_ID=<account-id>
export CF_PAGES_API_TOKEN=<token-with-Pages:Edit-scope>
export VITE_GA4_ID=G-XXXXXXXXXX
export VITE_BACKEND_URL=https://api.syrabit.ai
node artifacts/syrabit/scripts/apply-pages-config.mjs --apply

# Apply + trigger deployment
node artifacts/syrabit/scripts/apply-pages-config.mjs --deploy
```

**Build Configuration Applied**:
```javascript
build_command: "corepack enable && corepack prepare pnpm@10.26.1 --activate && pnpm install --filter @workspace/syrabit... --frozen-lockfile && pnpm --filter @workspace/syrabit run build"
destination_dir: "artifacts/syrabit/dist"
root_dir: ""
build_caching: true
```

### 4.3 Backend Deployment (Railway/Cloud Run)

**Railway Environment Variables**:
```bash
# Core configuration
NODE_ENV=production
PYTHON_VERSION=3.11

# Database
MONGO_URL=mongodb+srv://...
DATABASE_URL=postgresql://...

# Secrets (validated by pydantic-settings)
JWT_SECRET=<64-char-random-string>
SESSION_SECRET=<64-char-random-string>
ORIGIN_SHARED_SECRET=<32-char-random-string>
D1_SYNC_SECRET=<same-as-edge-worker>
WORKERS_AI_FALLBACK_SECRET=<same-as-edge-worker>

# Edge Worker integration
EDGE_WORKER_URL=https://syrabit-edge.<account>.workers.dev
EDGE_WORKER_PREVIEW_URL=https://syrabit-edge-preview.<account>.workers.dev
D1_SYNC_SECRET_PREVIEW=<preview-secret-value>

# Cloudflare
CLOUDFLARE_ACCOUNT_ID=<account-id>
CLOUDFLARE_ANALYTICS_TOKEN=<token>
CF_ZONE_ID=<zone-id>
VECTORIZE_INDEX_NAME=syllabus-index-v2

# LLM Providers (at least one required)
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...
SARVAM_API_KEY=...
```

**Cloud Run Secret Manager Bindings**:
```bash
# Create secrets
gcloud secrets create jwt-secret --data-file=jwt-secret.txt
gcloud secrets create session-secret --data-file=session-secret.txt
gcloud secrets create origin-shared-secret --data-file=origin-secret.txt

# Deploy with secret bindings
gcloud run deploy syrabit-api \
  --set-secrets=/etc/secrets/jwt_secret=jwt-secret:latest \
  --set-secrets=/etc/secrets/session_secret=session-secret:latest \
  --set-env-vars=MONGO_URL=$MONGO_URL \
  --set-env-vars=ORIGIN_SHARED_SECRET=$(gcloud secrets versions access latest --secret=origin-shared-secret)
```

---

## 5. Configuration Synchronization Strategy

### 5.1 Shared Secrets Matrix

| Secret | Edge Worker | Backend | Pages | Notes |
|--------|-------------|---------|-------|-------|
| `D1_SYNC_SECRET` | ✅ (secret) | ✅ (env) | ❌ | Must match exactly |
| `BACKEND_ORIGIN_SECRET` / `ORIGIN_SHARED_SECRET` | ✅ (as `BACKEND_ORIGIN_SECRET`) | ✅ (as `ORIGIN_SHARED_SECRET`) | ❌ | Name differs by component |
| `EDGE_AI_FALLBACK_SECRET` / `WORKERS_AI_FALLBACK_SECRET` | ✅ (as `EDGE_AI_FALLBACK_SECRET`) | ✅ (as `WORKERS_AI_FALLBACK_SECRET`) | ❌ | Name differs by component |
| `CLOUDFLARE_ACCOUNT_ID` | ❌ | ✅ | ❌ | Backend only |
| `VITE_BACKEND_URL` | ❌ | ❌ | ✅ (build var) | Public frontend config |
| `JWT_SECRET` | ❌ | ✅ | ❌ | Backend only |

### 5.2 Preview vs Production Separation

```bash
# Preview Environment (independent)
D1_SYNC_SECRET_PREVIEW          # Different from prod
EDGE_WORKER_PREVIEW_URL         # Points to preview worker
syrabit-content-preview         # Separate D1 database
KV preview namespaces           # Separate KV storage

# Production Environment
D1_SYNC_SECRET_PROD             # Different from preview
EDGE_WORKER_URL                 # Points to prod worker
syrabit-content                 # Prod D1 database
KV prod namespaces              # Prod KV storage
```

### 5.3 D1 Sync Fan-Out (Task #879)

The backend automatically syncs to both prod and preview D1 databases:

```python
# Backend d1_sync.py logic
async def sync_to_d1():
    # Sync to prod
    await sync_to_edge_worker(
        url=os.environ["EDGE_WORKER_URL"],
        secret=os.environ["D1_SYNC_SECRET"]
    )
    
    # Sync to preview (if configured)
    if os.environ.get("EDGE_WORKER_PREVIEW_URL"):
        await sync_to_edge_worker(
            url=os.environ["EDGE_WORKER_PREVIEW_URL"],
            secret=os.environ["D1_SYNC_SECRET_PREVIEW"]
        )
```

---

## 6. Verification & Testing

### 6.1 Pre-Deployment Checks

```bash
# 1. Validate backend config loads correctly
cd artifacts/syrabit-backend
python -c "from config import env; print('Config loaded:', env.JWT_SECRET[:8] + '...')"

# 2. Check D1 drift between prod and preview
cd workers/edge-proxy
pnpm run d1:check-drift

# 3. Verify Pages config dry-run
export CLOUDFLARE_ACCOUNT_ID=<id>
export CF_PAGES_API_TOKEN=<token>
node artifacts/syrabit/scripts/apply-pages-config.mjs

# 4. Test edge worker locally
cd workers/edge-proxy
wrangler dev --local
```

### 6.2 Post-Deployment Smoke Tests

```bash
# Edge Worker smoke test (CI automated)
pnpm --filter syrabit-edge run smoke

# Manual health checks
curl -sI https://api.syrabit.ai/api/health | grep "200 OK"
curl -sI https://api.syrabit.ai/api/content/boards | grep "200 OK"
curl -s https://api.syrabit.ai/api/edge/kv-usage -H "X-D1-Sync-Secret: <secret>" | jq

# Pages deployment check
curl -sI https://syrabit-zip-convert.pages.dev | grep "200 OK"

# D1 content verification
pnpm --filter syrabit-edge run smoke:preview
```

### 6.3 Configuration Drift Detection

```bash
# Check for mismatched secrets
echo "Checking secret alignment..."
echo "Edge Worker D1_SYNC_SECRET hash: $(echo -n $D1_SYNC_SECRET | sha256sum)"
echo "Backend D1_SYNC_SECRET hash: $(echo -n $BACKEND_D1_SYNC_SECRET | sha256sum)"

# Verify pydantic validation passes
cd artifacts/syrabit-backend
python -m pytest tests/test_config_validation.py -v
```

---

## 7. Troubleshooting Guide

### 7.1 Common Issues

**Issue**: `ConfigError: Missing required environment variable`
```bash
# Solution: Check which variable is missing
cd artifacts/syrabit-backend
python -c "import os; from config import EnvSettings; EnvSettings()"

# Verify all required vars are set
printenv | grep -E "(JWT_SECRET|MONGO_URL|ORIGIN_SHARED_SECRET)"
```

**Issue**: Edge Worker returns 401 on `/api/d1/sync`
```bash
# Cause: D1_SYNC_SECRET mismatch
# Fix: Ensure both edge worker and backend use same value
wrangler secret put D1_SYNC_SECRET  # Set on worker
# Then update backend environment with same value
```

**Issue**: Pages build fails with "VITE_GA4_ID invalid"
```bash
# Cause: GA4 ID doesn't match regex /^G-[A-Z0-9]{6,12}$/
# Fix: Use valid Measurement ID or skip validation
export VITE_GA4_ID=G-XXXXXXXXXX  # Real GA4 ID
# Or run without strict mode
node scripts/apply-pages-config.mjs --apply  # Allows missing GA4
```

**Issue**: D1 drift detected between prod and preview
```bash
# Solution: Apply pending migrations
cd workers/edge-proxy
pnpm dlx wrangler d1 migrations apply syrabit-content --remote
pnpm dlx wrangler d1 migrations apply syrabit-content-preview --remote

# Re-run drift check
pnpm run d1:check-drift
```

### 7.2 Emergency Rollback Procedures

**Rollback Edge Worker**:
```bash
cd workers/edge-proxy
wrangler rollback --message "Emergency rollback: <reason>"
wrangler rollback --env preview --message "Emergency rollback: <reason>"
```

**Rollback Pages Deployment**:
```bash
# Via Cloudflare Dashboard
https://dash.cloudflare.com/<account-id>/pages/view/syrabit-analytics/deployments

# Or via API
curl -X POST "https://api.cloudflare.com/client/v4/accounts/<account-id>/pages/projects/syrabit-analytics/deployments" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  --data '{"deployment_trigger":{"branch":"Replit-agent"},"production_branch":"Replit-agent"}'
```

**Rollback Backend**:
```bash
# Railway: Click "Deployments" → Select previous version → "Promote"
# Cloud Run: gcloud run services update-traffic syrabit-api --to-revisions=<revision-name>=100
```

---

## 8. Security Best Practices

### 8.1 Secret Management

✅ **DO**:
- Use `wrangler secret put` for Worker secrets (encrypted at rest)
- Use Cloud Secret Manager / Railway Secrets for backend
- Rotate secrets quarterly via automated workflows
- Use separate secrets for preview and production
- Validate secret format on startup (pydantic validators)

❌ **DON'T**:
- Commit `.env` files to git
- Set secrets as Pages build variables (public logs)
- Share preview secrets with production
- Use predictable default values for sensitive fields
- Log secret values (even partially)

### 8.2 Access Control

```bash
# Minimum required scopes for CLOUDFLARE_API_TOKEN
Workers Scripts: Edit
Workers KV Storage: Edit
D1: Edit
Workers AI: Edit
Account: Read (for analytics)

# Pages API token (separate, narrower scope)
Pages: Edit
Account: Read
```

### 8.3 Audit Trail

All configuration changes are logged:
- Edge Worker: `wrangler deploy` outputs version ID
- Pages: `apply-pages-config.mjs` prints applied changes
- Backend: Railway/Cloud Run deployment history
- GitHub Actions: Full workflow run logs with SHA-pinned actions

---

## 9. Migration Checklist

### 9.1 Pre-Migration

- [ ] Backup current environment variables from all components
- [ ] Document current secret values (securely, encrypted)
- [ ] Test pydantic-settings config loading in isolation
- [ ] Verify all required secrets are available

### 9.2 Migration Steps

1. **Backend First** (least risky):
   ```bash
   cd artifacts/syrabit-backend
   # Update config.py imports to use EnvSettings
   # Deploy to staging, validate config loads
   # Promote to production
   ```

2. **Edge Worker Second** (requires coordination):
   ```bash
   cd workers/edge-proxy
   # Update wrangler.toml if needed
   # Set new secrets via wrangler secret put
   # Deploy preview, run smoke tests
   # Deploy production with manual approval
   ```

3. **Pages Last** (public-facing):
   ```bash
   # Run apply-pages-config.mjs to update build vars
   # Trigger deployment
   # Verify frontend loads correctly
   ```

### 9.3 Post-Migration Validation

- [ ] All health endpoints return 200 OK
- [ ] D1 sync completes without errors
- [ ] AI fallback routes authenticate correctly
- [ ] Pages build succeeds with new config
- [ ] No configuration-related errors in logs
- [ ] Synthetic probes passing (check watchdog)
- [ ] Cache hit rates stable (no regression)

---

## 10. Reference Commands Quick Sheet

```bash
# === Edge Worker ===
cd workers/edge-proxy
wrangler secret put D1_SYNC_SECRET
wrangler secret put BACKEND_ORIGIN_SECRET
wrangler secret put EDGE_AI_FALLBACK_SECRET
pnpm run deploy:preview:dry    # Dry run preview
pnpm run deploy:preview         # Deploy preview
pnpm run smoke:preview          # Smoke test preview
pnpm run d1:check-drift         # Check D1 sync
pnpm run deploy                 # Deploy production

# === Pages ===
cd artifacts/syrabit
export CLOUDFLARE_ACCOUNT_ID=<id>
export CF_PAGES_API_TOKEN=<token>
export VITE_GA4_ID=G-XXXXXXXXXX
node scripts/apply-pages-config.mjs --dry-run
node scripts/apply-pages-config.mjs --apply
node scripts/apply-pages-config.mjs --deploy

# === Backend ===
cd artifacts/syrabit-backend
python -c "from config import env; print(env.dict())"  # Validate config
pytest tests/test_config_validation.py -v              # Test validation

# === Verification ===
curl -sI https://api.syrabit.ai/api/health
curl -s https://api.syrabit.ai/api/edge/kv-usage -H "X-D1-Sync-Secret: <secret>"
pnpm --filter syrabit-edge run smoke
```

---

## 11. Support & Documentation Links

- **Pydantic Settings Docs**: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
- **Wrangler Secrets**: https://developers.cloudflare.com/workers/wrangler/secrets/
- **Cloudflare Pages Env Vars**: https://developers.cloudflare.com/pages/configuration/build-configuration/#environment-variables
- **GitHub Actions Environments**: https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment
- **Cloud Run Secret Manager**: https://cloud.google.com/run/docs/configuring/services/secrets

---

**Last Updated**: 2026-05-02  
**Maintainer**: DevOps Team  
**Review Cycle**: Quarterly

---

## 12. Bedrock-Proxy Worker (Task #256)

A new Cloudflare Worker (`workers/bedrock-proxy/`) signs AWS SigV4 requests for
Amazon Polly (TTS), Amazon Transcribe (STT), and Amazon Translate — AWS services
not covered by the CF AI Gateway aws-bedrock BYOK slug.

### 12.1 Deployment Steps

```bash
cd workers/bedrock-proxy

# Install dependencies
npm install

# Set required secrets
wrangler secret put AWS_ACCESS_KEY_ID
wrangler secret put AWS_SECRET_ACCESS_KEY
wrangler secret put BEDROCK_PROXY_AUTH_TOKEN  # Must match backend env var
wrangler secret put AWS_S3_BUCKET             # e.g. syrabit-transcribe-tmp

# Optional — defaults to us-east-1
wrangler secret put AWS_REGION

# Deploy
npm run deploy
```

### 12.2 Required IAM Permissions

The IAM user/role must have:
```json
{
  "Effect": "Allow",
  "Action": [
    "polly:SynthesizeSpeech",
    "transcribe:StartTranscriptionJob",
    "transcribe:GetTranscriptionJob",
    "translate:TranslateText",
    "s3:PutObject",
    "s3:GetObject"
  ],
  "Resource": "*"
}
```

### 12.3 Backend Configuration

After deploying the Worker, set these environment variables in Railway/Replit Secrets:

```bash
BEDROCK_PROXY_URL=https://bedrock-proxy.<your-subdomain>.workers.dev
BEDROCK_PROXY_AUTH_TOKEN=<same-value-as-worker-secret>  # Shared auth secret
BEDROCK_POLLY_VOICE=Raveena   # Optional, default is Raveena (Indian English Neural)
```

### 12.4 Routes

| Route | AWS Service | Notes |
|-------|------------|-------|
| `POST /polly/synthesize` | Amazon Polly | Returns `audio/mpeg` bytes |
| `POST /transcribe` | Amazon Transcribe | Requires `AWS_S3_BUCKET`; polls up to 25s |
| `POST /translate` | Amazon Translate | Returns `{translated_text}` JSON |
| `GET /health` | — | Worker liveness check; no auth required |

### 12.5 Security

- The Worker validates `Authorization: Bearer <BEDROCK_PROXY_AUTH_TOKEN>` on all POST routes.
- The backend (`providers/bedrock.py`) sends this header via `_proxy_headers()` when `BEDROCK_PROXY_AUTH_TOKEN` is set in the Railway environment.
- Set `BEDROCK_PROXY_AUTH_TOKEN` to a random 32+ character string in both the Worker secrets and Railway secrets.
- Without the token, the proxy still works but accepts any caller — only use for private/VPC-internal Workers.

### 12.6 Monitoring

The Worker health endpoint is registered in `workers/edge-proxy/monitored-urls.json`
(`intentionally_external`). The synthetic probe checks `GET /health` on every deploy.
