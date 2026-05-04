# 🚀 Syrabit.ai Neural Mesh Deployment Readiness Plan

> ⚠️ **HISTORICAL — Railway-era plan.** Task #336 migrated the backend
> off Railway onto Digital Ocean App Platform. The current
> infrastructure-alignment checklist now lives at
> [`docs/DIGITALOCEAN-DEPLOYMENT.md`](docs/DIGITALOCEAN-DEPLOYMENT.md)
> and the cutover/rollback runbook at
> [`docs/ops/digitalocean-cutover.md`](docs/ops/digitalocean-cutover.md).
> Treat anything below this banner as historical context only.

**Status**: Code Complete ✅ | Infrastructure Alignment: **NOT READY** ❌  
**Estimated Effort**: 10-15 hours  
**Priority**: Critical Blockers Must Be Fixed Before Deployment

---

## Executive Summary

All Neural Mesh components are fully implemented:
- ✅ Rust Core backend (1,487 lines)
- ✅ Edge Worker with gRPC client (405 lines)
- ✅ JARVIS HUD visualization (303 lines)
- ✅ Staff Management Panel (1,356 lines)
- ✅ Deployment scripts & documentation

**However**, the infrastructure wiring has **6 critical blockers** preventing seamless deployment. This plan provides a phased approach to make the system deployment-ready.

---

## 🔴 Phase 1: Critical Blockers (Must Fix Before Deployment)

### Blocker 1: Edge Worker Duplication & Routing Conflict

**Problem**: Two separate edge workers exist with conflicting purposes:
- `/workspace/edge/worker.js` - New Neural Mesh spec (gRPC to Rust Core)
- `/workspace/workers/edge-proxy/src/index.ts` - Production worker (routes to Python FastAPI)

**Impact**: Deploying both would cause routing conflicts on `api.syrabit.ai/*`

**Solution Options**:

#### Option A: Merge Workers (Recommended)
- Integrate Neural Mesh gRPC logic into existing `workers/edge-proxy/src/index.ts`
- Add conditional routing: gRPC paths → Rust Core, REST paths → Python/Rust
- Preserve all existing features (D1 sync, KV monitoring, synthetic probes)

#### Option B: Gradual Migration
- Keep production worker unchanged
- Deploy Neural Mesh worker to subdomain: `neural-mesh-api.syrabit.ai/*`
- Migrate routes incrementally via DNS updates

**Files to Modify**:
```
/workspace/workers/edge-proxy/src/index.ts  (add gRPC routing logic)
/workspace/edge/wrangler.toml               (create new config)
```

**Effort**: 3-4 hours  
**Risk**: Medium (requires thorough testing of existing routes)

---

### Blocker 2: Backend URL Configuration Mismatch

**Problem**: 
- `wrangler.toml` points to Python FastAPI: `https://workspacemockup-sandbox-production-df37.up.railway.app`
- Neural Mesh expects Rust Core at: `https://rust-core.syrabit.ai:50051` (gRPC) + HTTP endpoint

**Current State**:
```toml
# wrangler.toml line 45
BACKEND_URL = "https://workspacemockup-sandbox-production-df37.up.railway.app"
```

```javascript
// edge/worker.js line 54
RUST_CORE_GRPC_URL: 'https://rust-core.syrabit.ai:50051'
```

**Solution**:
1. Deploy Rust Core to Railway first
2. Capture the new Railway URL (e.g., `syrabit-rust-core-production.up.railway.app`)
3. Update `wrangler.toml` with dual endpoints:
   ```toml
   BACKEND_URL = "https://syrabit-rust-core-production.up.railway.app"  # HTTP
   RUST_CORE_GRPC_URL = "grpcs://syrabit-rust-core-production.up.railway.app:443"  # gRPC over TLS
   ```

**Files to Modify**:
```
/workspace/backend/rust-core/railway.toml    (verify deployment config)
/workspace/workers/edge-proxy/wrangler.toml  (update BACKEND_URL post-deployment)
/workspace/edge/worker.js                    (use env binding, not hardcoded)
```

**Effort**: 1 hour  
**Risk**: Low (configuration only)

---

### Blocker 3: gRPC Protocol Incompatibility

**Problem**:
- Rust Core exposes raw gRPC over TCP port 50051
- Cloudflare Workers require **gRPC-Web over HTTPS** (port 443)
- Missing `tonic-web` bridge in Rust Core

**Evidence**:
```bash
$ grep -r "tonic-web\|grpc-web" /workspace/backend/rust-core/
No gRPC-Web support found
```

**Current Rust Core** (`src/main.rs` lines 150-157):
```rust
tonic::transport::Server::builder()
    .add_service(NeuralMeshGrpcService::into_service(grpc_service))
    .serve(grpc_addr)  // Raw TCP gRPC
```

**Solution**: Add `tonic-web` support for gRPC-Web compatibility

**Step 1**: Update `Cargo.toml`
```toml
[dependencies]
tonic-web = "0.11"  # Add this
tower-http = { version = "0.5", features = ["cors", "trace", "websocket"] }
```

**Step 2**: Modify `src/main.rs` to serve gRPC-Web on HTTP port
```rust
use tonic_web::enable;

// Create gRPC service
let grpc_service = NeuralMeshGrpcService::new(grpc_db, grpc_metrics_tx);

// Enable gRPC-Web compatibility
let grpc_web_service = enable(grpc_service.into_service());

// Add to Axum router under /grpc/* path
let app = Router::new()
    .route("/health", get(handlers::health::health_check))
    .nest_service("/grpc", grpc_web_service)  // gRPC-Web endpoint
    .layer(cors);
```

**Why This Works**:
- Cloudflare Workers can call gRPC-Web over standard HTTPS (port 443)
- No separate TCP port needed (Railway supports HTTPS routing)
- Single endpoint serves both REST and gRPC-Web

**Files to Modify**:
```
/workspace/backend/rust-core/Cargo.toml      (add tonic-web dependency)
/workspace/backend/rust-core/src/main.rs     (add gRPC-Web service)
/workspace/edge/worker.js                    (update fetch calls to use gRPC-Web format)
```

**Effort**: 2-3 hours  
**Risk**: Medium (requires testing gRPC-Web compatibility)

---

### Blocker 4: Database Schema Mismatch (PostgreSQL vs D1)

**Problem**: Two incompatible schemas prevent data synchronization:

| Aspect | Rust Core (PostgreSQL) | Edge Worker (D1) |
|--------|----------------------|------------------|
| ID Type | UUID (binary 128-bit) | TEXT (string) |
| Vector Support | pgvector extension | None |
| Timestamp | TIMESTAMPTZ | TEXT (ISO string) |
| Foreign Keys | CASCADE constraints | Manual enforcement |

**Evidence**:
```sql
-- PostgreSQL (backend/rust-core/migrations/001_initial_schema.sql)
id UUID PRIMARY KEY DEFAULT uuid_generate_v4()

-- D1 (workers/edge-proxy/migrations/0001_create_content_tables.sql)
id TEXT PRIMARY KEY
```

**Impact**: Direct sync will fail due to type mismatches

**Solution**: Add conversion layer in sync process

**Option A: Modify Rust Core Sync Handler** (Recommended)
Add `/api/edge/d1-sync` endpoint that transforms UUIDs to strings:

```rust
// src/handlers/staff.rs or new src/handlers/d1_sync.rs
pub async fn sync_to_d1(
    db: PgPool,
    d1_webhook_url: String,
) -> Result<Json<SyncResponse>> {
    // Fetch from PostgreSQL
    let boards = sqlx::query_as!(Board, "SELECT * FROM boards")
        .fetch_all(&db)
        .await?;
    
    // Transform UUID → String for D1 compatibility
    let d1_payload: Vec<D1Board> = boards.iter().map(|b| D1Board {
        id: b.id.to_string(),  // UUID → TEXT
        name: b.name.clone(),
        // ... other fields
    }).collect();
    
    // POST to D1 webhook
    reqwest::Client::new()
        .post(&d1_webhook_url)
        .json(&d1_payload)
        .send()
        .await?;
    
    Ok(Json(SyncResponse { synced: true }))
}
```

**Option B: Use TEXT IDs in PostgreSQL** (Not Recommended)
- Change PostgreSQL schema to use TEXT IDs
- Loses UUID benefits (type safety, auto-generation)
- Requires migration of existing data

**Files to Create/Modify**:
```
/workspace/backend/rust-core/src/handlers/d1_sync.rs   (new file)
/workspace/backend/rust-core/src/models/d1_types.rs    (new file - D1-compatible types)
/workspace/backend/rust-core/src/main.rs               (add /api/edge/d1-sync route)
```

**Effort**: 2-3 hours  
**Risk**: Medium (data transformation logic must be tested)

---

### Blocker 5: Missing Environment Variables & Secrets

**Problem**: No `.env.example` file documents required secrets for Neural Mesh

**Required Secrets**:
```bash
# Rust Core (Railway)
DATABASE_URL=postgres://user:pass@host:5432/syrabit
JWT_SECRET=<32-byte-random-secret>
ORIGIN_SHARED_SECRET=<shared-with-edge-worker>

# Edge Worker (Cloudflare)
BACKEND_URL=https://rust-core-production.up.railway.app
BACKEND_ORIGIN_SECRET=<same-as-above>
D1_SYNC_SECRET=<shared-secret-for-sync-webhook>
EDGE_AI_FALLBACK_SECRET=<secret-for-workers-ai-routes>
```

**Solution**: Create comprehensive `.env.example` files

**Files to Create**:
```
/workspace/backend/rust-core/.env.example
/workspace/workers/edge-proxy/.env.example
/workspace/.env.shared-secrets.example
```

**Effort**: 30 minutes  
**Risk**: Low (documentation only)

---

### Blocker 6: Deployment Script Gaps

**Problem**: Current `deploy-neural-mesh.sh` script lacks:
- Secret injection workflow
- D1 migration application
- Health check validation
- Rollback procedure

**Current Script Issues**:
```bash
# deploy-neural-mesh.sh line 25-45
docker build -t syrabit-rust-core:latest .  # ✓ Good
railway up --detach                          # ✗ Missing secret prompts
wrangler deploy worker.js --env production   # ✗ No pre-flight checks
```

**Solution**: Enhance deployment script with proper orchestration

**Enhanced Script Structure**:
```bash
#!/bin/bash
set -e

# Phase 1: Pre-flight checks
check_dependencies() {
  command -v railway || exit_error "Railway CLI required"
  command -v wrangler || exit_error "Wrangler CLI required"
  command -v cargo || exit_error "Rust toolchain required"
}

validate_secrets() {
  # Prompt for required secrets if not set
  [ -z "$DATABASE_URL" ] && read -p "Enter DATABASE_URL: " DATABASE_URL
  [ -z "$JWT_SECRET" ] && JWT_SECRET=$(openssl rand -hex 32)
}

# Phase 2: Deploy Rust Core
deploy_rust_core() {
  cd backend/rust-core
  
  # Apply migrations first
  sqlx migrate run --database-url "$DATABASE_URL"
  
  # Build & deploy with secrets
  railway up --detach \
    --env DATABASE_URL="$DATABASE_URL" \
    --env JWT_SECRET="$JWT_SECRET"
  
  # Wait for health check
  wait_for_health "https://$RAILWAY_DOMAIN/health"
  
  cd ../..
}

# Phase 3: Update Edge Worker config
update_edge_config() {
  # Inject new backend URL into wrangler.toml
  sed -i "s|BACKEND_URL.*|BACKEND_URL = \"$RAILWAY_DOMAIN\"|" \
    workers/edge-proxy/wrangler.toml
}

# Phase 4: Deploy Edge Worker
deploy_edge_worker() {
  cd workers/edge-proxy
  
  # Apply D1 migrations
  wrangler d1 migrations apply syrabit-content --remote
  
  # Deploy with updated config
  wrangler deploy --env production
  
  cd ../..
}

# Phase 5: Validate deployment
validate_deployment() {
  curl -f https://api.syrabit.ai/health || exit_error "Edge health check failed"
  curl -f https://rust-core-production.up.railway.app/health || exit_error "Core health check failed"
  
  echo "✅ Deployment successful!"
}

# Main execution
check_dependencies
validate_secrets
deploy_rust_core
update_edge_config
deploy_edge_worker
validate_deployment
```

**Files to Modify**:
```
/workspace/deploy-neural-mesh.sh  (complete rewrite)
```

**Effort**: 2 hours  
**Risk**: Medium (script must be tested in staging first)

---

## 🟡 Phase 2: Integration & Testing (Post-Blocker Resolution)

### Task 2.1: Unified Worker Integration

**Goal**: Merge Neural Mesh logic into production edge worker

**Steps**:
1. Copy gRPC client logic from `/workspace/edge/worker.js` to `/workspace/workers/edge-proxy/src/grpc-client.ts`
2. Add routing rules in `src/index.ts`:
   ```typescript
   if (pathname.startsWith('/api/rag/') || pathname.startsWith('/api/agents/')) {
     return handleGrpcRequest(env, request);  // Route to Rust Core
   }
   if (pathname.startsWith('/api/content/')) {
     return handleD1Cache(env, request);  // Serve from D1
   }
   ```
3. Preserve existing features (KV monitoring, synthetic probes, D1 sync)

**Files to Modify**:
```
/workspace/workers/edge-proxy/src/index.ts
/workspace/workers/edge-proxy/src/grpc-client.ts (new)
/workspace/edge/worker.js (archive or delete after merge)
```

**Effort**: 2-3 hours

---

### Task 2.2: End-to-End Testing Matrix

**Test Scenarios**:

| Test ID | Scenario | Expected Result |
|---------|----------|-----------------|
| E2E-01 | Edge → Rust Core gRPC call | 200 OK, <100ms latency |
| E2E-02 | D1 cache hit for /api/content/boards | Served from edge, no backend call |
| E2E-03 | D1 sync from Rust Core → Edge | Rows appear in D1 within 5s |
| E2E-04 | Staff login OTP flow | JWT returned, role:staff |
| E2E-05 | JARVIS WebSocket metrics stream | Real-time updates visible |
| E2E-06 | Permission guard bypass attempt | 403 Forbidden |
| E2E-07 | Fallback to Workers AI | Activated on primary timeout |

**Testing Tools**:
- Postman collection for API tests
- k6 load testing script
- Browser DevTools for WebSocket verification

**Effort**: 3-4 hours

---

### Task 2.3: Monitoring & Observability Setup

**Requirements**:
1. Add tracing spans to Rust Core handlers
2. Configure Logflare/Logpush for Edge Worker logs
3. Set up Grafana dashboard for metrics
4. Create PagerDuty alerts for critical failures

**Files to Create**:
```
/workspace/backend/rust-core/src/tracing.rs
/workspace/observability/grafana-dashboard.json
/workspace/observability/alert-rules.yml
```

**Effort**: 2 hours

---

## 🟢 Phase 3: Deployment Execution

### Step 3.1: Staging Deployment (Dry Run)

**Environment**: Railway Preview + Cloudflare Preview

**Commands**:
```bash
# Deploy Rust Core to Railway preview
cd backend/rust-core
railway up --preview --env DATABASE_URL="$STAGING_DB" --env JWT_SECRET="$STAGING_JWT"

# Deploy Edge Worker to Cloudflare preview
cd workers/edge-proxy
wrangler deploy --env preview

# Run smoke tests
./scripts/smoke-test-staging.sh
```

**Success Criteria**:
- All health checks pass
- No errors in logs after 100 test requests
- D1 sync completes successfully

**Effort**: 1 hour

---

### Step 3.2: Production Deployment

**Pre-Deployment Checklist**:
- [ ] All Phase 1 blockers resolved
- [ ] Staging tests pass 100%
- [ ] Secrets rotated for production
- [ ] Rollback procedure documented
- [ ] On-call team briefed

**Deployment Order**:
1. **Database migrations** (PostgreSQL + D1)
2. **Rust Core** (Railway production)
3. **Edge Worker** (Cloudflare production)
4. **Frontend routes** (add `/staff/*` and `/jarvis`)

**Rollback Plan**:
```bash
# If Rust Core fails
railway rollback --service rust-core

# If Edge Worker fails
wrangler rollback --name syrabits-edge

# Revert DNS to Python backend
# Update BACKEND_URL in wrangler.toml to old Python URL
```

**Effort**: 1-2 hours

---

### Step 3.3: Post-Deployment Validation

**Immediate Checks** (first 15 minutes):
```bash
# Health endpoints
curl https://api.syrabit.ai/health
curl https://rust-core-production.up.railway.app/health

# Key functionality
curl https://api.syrabit.ai/api/rag/query -d '{"query":"test"}'
curl https://api.syrabit.ai/api/staff/login -d '{"phone":"+91..."}}'

# WebSocket connection
wscat -c wss://api.syrabit.ai/ws/metrics
```

**Monitoring Dashboard Checks**:
- Error rate < 0.1%
- P95 latency < 500ms
- D1 sync lag < 10s
- Active WebSocket connections > 0

**Effort**: 30 minutes

---

## 📋 File Inventory & Changes Summary

### Files to Create (New)
| File | Purpose | Priority |
|------|---------|----------|
| `backend/rust-core/.env.example` | Document required secrets | P0 |
| `backend/rust-core/src/handlers/d1_sync.rs` | D1 synchronization | P0 |
| `backend/rust-core/src/models/d1_types.rs` | D1-compatible types | P0 |
| `workers/edge-proxy/.env.example` | Edge worker secrets | P0 |
| `workers/edge-proxy/src/grpc-client.ts` | gRPC client for Rust | P1 |
| `.env.shared-secrets.example` | Cross-service secrets | P0 |
| `observability/grafana-dashboard.json` | Metrics dashboard | P2 |
| `scripts/smoke-test-staging.sh` | Pre-deployment tests | P1 |

### Files to Modify
| File | Changes | Priority |
|------|---------|----------|
| `backend/rust-core/Cargo.toml` | Add `tonic-web` dependency | P0 |
| `backend/rust-core/src/main.rs` | Add gRPC-Web service, D1 sync route | P0 |
| `workers/edge-proxy/wrangler.toml` | Update BACKEND_URL, add gRPC bindings | P0 |
| `workers/edge-proxy/src/index.ts` | Merge Neural Mesh routing logic | P1 |
| `edge/worker.js` | Use env bindings, not hardcoded URLs | P1 |
| `deploy-neural-mesh.sh` | Complete rewrite with proper orchestration | P0 |
| `NEURAL_MESH_IMPLEMENTATION.md` | Add deployment troubleshooting section | P2 |

### Files to Archive/Delete
| File | Action | Reason |
|------|--------|--------|
| `edge/worker.js` | Archive after merge | Superseded by integrated worker |
| `edge/wrangler.toml` | Delete | Using unified worker config |

---

## ⏱️ Timeline Estimate

| Phase | Tasks | Estimated Hours |
|-------|-------|-----------------|
| **Phase 1: Critical Blockers** | 6 blockers | 8-10 hours |
| **Phase 2: Integration & Testing** | 3 tasks | 7-9 hours |
| **Phase 3: Deployment** | 3 steps | 2-3 hours |
| **Buffer** | Unforeseen issues | 3-4 hours |
| **Total** | | **20-26 hours** |

**Realistic Timeline**: 3-4 working days (assuming 6-8 hours/day focused work)

---

## 🎯 Success Criteria

Deployment is considered successful when:

1. ✅ **Zero Downtime**: No service interruption during deployment
2. ✅ **Health Checks Pass**: All endpoints return 200 OK within 5 minutes
3. ✅ **Data Consistency**: D1 sync completes without errors
4. ✅ **Performance**: P95 latency < 500ms for all routes
5. ✅ **Security**: All permission guards enforced, no unauthorized access
6. ✅ **Observability**: Logs flowing, metrics visible, alerts configured
7. ✅ **Rollback Tested**: Rollback procedure verified in staging

---

## 🚨 Risk Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| gRPC-Web incompatibility | Medium | High | Test in staging extensively |
| D1 sync data loss | Low | Critical | Backup D1 before migration |
| Railway deployment failure | Medium | High | Keep Python backend running in parallel |
| Secret leakage | Low | Critical | Use Railway/Cloudflare secret management |
| DNS propagation delay | High | Medium | Deploy during low-traffic window |

---

## 📞 Next Steps

1. **Immediate** (Today):
   - Review and approve this plan
   - Set up staging environment on Railway
   - Create `.env.example` files

2. **Day 1-2**:
   - Implement Phase 1 blockers (gRPC-Web, D1 sync, secrets)
   - Test in staging environment

3. **Day 3**:
   - Complete Phase 2 integration
   - Run full E2E test matrix

4. **Day 4**:
   - Execute Phase 3 production deployment
   - Monitor for 24 hours post-deployment

---

**Document Version**: 1.0  
**Last Updated**: 2026-04-29  
**Author**: Deployment Planning Team  
**Approval Required**: Yes (before proceeding with Phase 1)
