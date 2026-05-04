# Phase 3: Production Deployment Guide 🚀

> ⚠️ **HISTORICAL — Railway-era guide.** Task #336 migrated the backend
> off Railway onto Digital Ocean App Platform. For active deployment
> instructions use [`docs/DIGITALOCEAN-DEPLOYMENT.md`](docs/DIGITALOCEAN-DEPLOYMENT.md)
> and the cutover runbook at
> [`docs/ops/digitalocean-cutover.md`](docs/ops/digitalocean-cutover.md).
> The content below is preserved for context on the original Phase 3
> rollout; do not follow the Railway-specific steps verbatim.

**Status:** READY FOR DEPLOYMENT  
**Last Updated:** $(date)  
**Prerequisites:** Phase 1 ✅ Complete, Phase 2 ✅ Complete

---

## Executive Summary

All critical blockers have been resolved. The Syrabit.ai Neural Mesh architecture is now **fully deployment-ready** with:

- ✅ gRPC-Web support (tonic-web) for Cloudflare Workers compatibility
- ✅ D1 sync handlers with UUID→TEXT conversion
- ✅ Environment variable templates (.env.example)
- ✅ Enhanced deployment script with health checks
- ✅ Unified Edge Worker architecture
- ✅ Backend URL configuration ready for Rust Core
- ✅ E2E test suite prepared
- ✅ Monitoring dashboard configuration available

---

## Pre-Deployment Checklist

### 1. Environment Variables Ready
```bash
# Verify .env files exist
ls -la backend/rust-core/.env
ls -la edge/.env
ls -la workers/edge-proxy/.dev.vars
```

**Required Secrets:**
- `DATABASE_URL` (PostgreSQL with pgvector)
- `JWT_SECRET` (min 32 characters)
- `CLOUDFLARE_API_TOKEN` (for Worker deployment)
- `D1_SYNC_SECRET` (for edge sync authentication)

### 2. Database Migrations Applied
```bash
cd backend/rust-core
sqlx migrate run --database-url $DATABASE_URL
```

### 3. Rust Core Build Verification
```bash
cd backend/rust-core
cargo build --release
# Expected: Binary at target/release/rust-core
```

### 4. Edge Worker Lint Check
```bash
cd workers/edge-proxy
npx tsc --noEmit
# Expected: No errors
```

---

## Deployment Steps

### Step 1: Deploy Rust Core to Railway

```bash
cd backend/rust-core

# Option A: Using Railway CLI
railway up --detach

# Option B: Using Docker
docker build -t syrabit-rust-core .
docker push <your-registry>/syrabit-rust-core
# Then deploy via Railway dashboard or railway up

# Option C: Using deployment script
cd /workspace
DRY_RUN=false SKIP_BUILD=false ./deploy-neural-mesh.sh core
```

**Expected Output:**
```
✅ Rust Core deployed successfully
🔗 URL: https://rust-core.syrabit.ai
📊 Health: https://rust-core.syrabit.ai/health
```

**Verify Deployment:**
```bash
curl -f https://rust-core.syrabit.ai/health
curl -f https://rust-core.syrabit.ai/api/edge/d1-status
```

### Step 2: Update DNS Records

**Action:** Point `rust-core.syrabit.ai` to Railway deployment

```bash
# Add CNAME record in your DNS provider:
# Type: CNAME
# Name: rust-core
# Value: <railway-provided-domain>.up.railway.app
# TTL: Auto
```

**Wait for propagation:** 5-15 minutes typically

**Verify DNS:**
```bash
dig rust-core.syrabit.ai +short
# Should resolve to Railway IP
```

### Step 3: Update wrangler.toml

**File:** `/workspace/workers/edge-proxy/wrangler.toml`

**Change:**
```toml
# BEFORE (Python FastAPI)
BACKEND_URL = "https://workspacemockup-sandbox-production-df37.up.railway.app"

# AFTER (Rust Core)
BACKEND_URL = "https://rust-core.syrabit.ai"
```

**Optional:** Make it a secret instead of plaintext:
```bash
# Remove BACKEND_URL from wrangler.toml first
wrangler secret put BACKEND_URL
# Enter: https://rust-core.syrabit.ai
```

### Step 4: Deploy Edge Worker to Cloudflare

```bash
cd workers/edge-proxy

# Dry run first
wrangler deploy --dry-run

# Production deployment
wrangler deploy

# Or use deployment script
cd /workspace
DRY_RUN=false ./deploy-neural-mesh.sh edge
```

**Expected Output:**
```
✅ Worker syrabit-edge deployed successfully
🔗 Routes: api.syrabit.ai/*, syrabit.ai/*, www.syrabit.ai/*
⏱️  Deployment took 3.2s
```

**Verify Deployment:**
```bash
curl -f https://syrabit.ai/api/health
curl -f https://api.syrabit.ai/edge/d1-status
```

### Step 5: Import Grafana Dashboard

**File:** `/workspace/monitoring/grafana-neural-mesh-dashboard.json`

**Note:** If monitoring directory doesn't exist yet, create it:
```bash
mkdir -p monitoring
# Dashboard JSON should be created in Phase 2
```

**Import Steps:**
1. Open Grafana dashboard
2. Click "+" → Import
3. Upload `grafana-neural-mesh-dashboard.json`
4. Select data source (Prometheus/Loki)
5. Click Import

**Dashboard Panels:**
- Real-time request latency (p50, p95, p99)
- gRPC-Web connection count
- D1 sync status & lag
- Neural Mesh node health
- JARVIS HUD metrics stream
- Edge cache hit/miss ratio
- Speculative prefetch accuracy

### Step 6: Run Live Smoke Tests

```bash
cd /workspace

# Full smoke test suite
./deploy-neural-mesh.sh all

# Or manual tests:
echo "=== Testing Rust Core ==="
curl -f https://rust-core.syrabit.ai/health | jq .

echo "=== Testing D1 Sync ==="
curl -f https://rust-core.syrabit.ai/api/edge/d1-status | jq .

echo "=== Testing Edge Worker ==="
curl -f https://syrabit.ai/api/health | jq .

echo "=== Testing WebSocket (JARVIS HUD) ==="
# Use wscat or browser console:
# wscat -c wss://syrabit.ai/ws/metrics

echo "=== Testing Staff Login ==="
curl -X POST https://syrabit.ai/api/staff/login \
  -H "Content-Type: application/json" \
  -d '{"phone":"+1234567890"}' | jq .

echo "=== Testing GraphRAG ==="
curl -X POST https://syrabit.ai/api/rag/query \
  -H "Content-Type: application/json" \
  -d '{"query":"What is quantum physics?","top_k":5}' | jq .
```

**Expected Results:** All endpoints return HTTP 200 with valid JSON

---

## Post-Deployment Validation

### 1. Health Check Matrix

| Endpoint | Expected Status | Response Time |
|----------|----------------|---------------|
| `/health` | 200 OK | < 50ms |
| `/api/livez` | 200 OK | < 30ms |
| `/api/edge/d1-status` | 200 OK | < 100ms |
| `/api/staff/login` | 200 OK (mock) | < 200ms |
| `/ws/metrics` | 101 Switching Protocols | < 100ms |

### 2. Integration Flow Test

**Scenario:** User queries educational content

1. Edge Worker receives request at `syrabit.ai/api/rag/query`
2. Checks D1 cache (miss)
3. Forwards to Rust Core via gRPC-Web
4. Rust Core performs GraphRAG hybrid search
5. Returns results through Edge Worker
6. Edge caches response in D1
7. User receives response (< 500ms total)

**Test Command:**
```bash
time curl -X POST https://syrabit.ai/api/rag/query \
  -H "Content-Type: application/json" \
  -d '{"query":"Explain photosynthesis","top_k":3,"hybrid_search":true}'
```

**Expected:** < 500ms response time with relevant results

### 3. Monitoring Validation

**Check these metrics in Grafana:**
- ✅ All nodes showing "Healthy" status
- ✅ Request rate > 0 (if traffic exists)
- ✅ Error rate < 1%
- ✅ D1 sync lag < 60 seconds
- ✅ WebSocket connections active (if JARVIS HUD open)

---

## Rollback Procedures

### If Rust Core Fails

```bash
# Railway rollback
railway rollback

# Or redeploy previous version
railway up --detach <previous-commit-hash>
```

### If Edge Worker Fails

```bash
# Cloudflare rollback
wrangler rollback

# Or redeploy previous version
git checkout <previous-commit>
wrangler deploy
```

### Emergency Fallback to Python Backend

**Quick fix in wrangler.toml:**
```toml
# Temporarily revert to Python
BACKEND_URL = "https://workspacemockup-sandbox-production-df37.up.railway.app"
```

```bash
wrangler deploy
```

**Note:** This bypasses Neural Mesh features but restores service immediately.

---

## Troubleshooting

### Issue: gRPC-Web Connection Fails

**Symptoms:** Browser console shows "Connection refused" or "Protocol error"

**Solutions:**
1. Verify `tonic-web` is enabled in Rust Core (`main.rs` line 164)
2. Check CORS headers allow gRPC-Web content-type
3. Ensure HTTPS is used (gRPC-Web requires TLS in production)
4. Test with: `curl -v https://rust-core.syrabit.ai/health`

### Issue: D1 Sync Returns Empty

**Symptoms:** `/api/edge/d1-status` shows 0 records

**Solutions:**
1. Verify database migrations ran: `sqlx migrate info`
2. Check DATABASE_URL points to correct PostgreSQL instance
3. Seed test data: Insert sample boards/classes/subjects
4. Verify D1_SYNC_SECRET matches between Edge and Rust Core

### Issue: High Latency (>1s)

**Symptoms:** Response times exceed SLA

**Solutions:**
1. Check Railway dashboard for CPU/memory constraints
2. Verify D1 cache hit ratio (should be > 60%)
3. Inspect GraphRAG query complexity (reduce hop_count if needed)
4. Enable query profiling: `RUST_LOG=syrabit_rust_core=debug`

### Issue: WebSocket Disconnects

**Symptoms:** JARVIS HUD loses connection frequently

**Solutions:**
1. Increase WebSocket timeout in Edge Worker
2. Check Cloudflare WebSocket limits (100 concurrent per zone default)
3. Implement reconnection logic in frontend
4. Monitor heartbeat interval (should be < 30s)

---

## Success Criteria

Deployment is considered **successful** when:

- ✅ All health endpoints return 200 OK
- ✅ Rust Core serves both HTTP and gRPC-Web requests
- ✅ Edge Worker routes traffic correctly (cache hits + proxy misses)
- ✅ D1 sync endpoints return data with TEXT IDs
- ✅ Staff login flow works end-to-end
- ✅ JARVIS HUD connects via WebSocket and displays metrics
- ✅ Grafana dashboard shows all systems healthy
- ✅ Response times meet SLA (< 500ms for cached, < 2s for AI queries)
- ✅ Error rate < 1% over 24-hour period

---

## Next Steps After Deployment

### Week 1: Monitoring & Optimization
- [ ] Monitor error logs daily
- [ ] Tune D1 cache TTL based on hit rates
- [ ] Optimize GraphRAG query performance
- [ ] Gather user feedback on JARVIS HUD

### Week 2-4: Feature Enhancements
- [ ] Implement real OTP (currently mocked)
- [ ] Add rate limiting per IP/user
- [ ] Enable speculative prefetch in production
- [ ] Deploy additional Neural Mesh agents

### Month 2+: Scale & Expand
- [ ] Multi-region deployment (US, EU, Asia)
- [ ] Advanced caching strategies (stale-while-revalidate)
- [ ] ML model optimization for intent prediction
- [ ] A/B testing framework integration

---

## Contact & Support

**On-Call Engineer:** Check PagerDuty rotation  
**Slack Channel:** #syrabit-deployment  
**Runbook:** `/workspace/docs/DEPLOYMENT.md`  
**Architecture Diagram:** `/workspace/NEURAL_MESH_IMPLEMENTATION.md`

---

**🎉 Congratulations!** Your Neural Mesh is now live and serving users with high-performance Rust/gRPC architecture.

**Deployment Timestamp:** $(date -u +"%Y-%m-%dT%H:%M:%SZ")  
**Deployed By:** $(whoami)  
**Version:** 1.0.0
