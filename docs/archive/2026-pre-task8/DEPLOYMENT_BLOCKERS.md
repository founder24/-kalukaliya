# 🔴 DEPLOYMENT BLOCKERS - Neural Mesh Integration

## Summary
The Syrabit.ai Neural Mesh implementation is **code-complete** but **deployment-unready**. Critical alignment issues prevent seamless deployment.

---

## 🚨 Critical Issues

### 1. Edge Worker Duplication
- **`/workspace/edge/worker.js`**: Neural Mesh spec (gRPC client, speculative prefetch)
  - ❌ No `wrangler.toml` → Cannot deploy
  - ❌ Hardcoded URLs (`https://rust-core.syrabit.ai`) → Not configurable
  - ❌ References `env.D1_DATABASE` but no D1 binding configured
  
- **`/workspace/workers/edge-proxy/src/index.ts`**: Production worker
  - ✅ Has `wrangler.toml` with full config
  - ❌ Routes to Python FastAPI backend, NOT Rust Core
  - ❌ No gRPC integration

**Fix Required:** Merge features or choose one worker as primary.

---

### 2. Backend URL Misconfiguration

**Current State:**
```toml
# workers/edge-proxy/wrangler.toml
BACKEND_URL = "https://workspacemockup-sandbox-production-df37.up.railway.app"
# ↑ This is Python FastAPI, NOT Rust Core
```

**Required Change:**
```toml
BACKEND_URL = "https://<rust-core-railway-url>.up.railway.app"
# Or use environment-specific overrides
```

---

### 3. Database Schema Incompatibility

| Component | Database | ID Type | Vector Support |
|-----------|----------|---------|----------------|
| Rust Core | PostgreSQL | UUID | ✅ pgvector |
| Edge Worker | D1 | TEXT | ❌ None |

**Impact:** 
- D1 sync from Rust Core will fail (UUID ↔ TEXT mismatch)
- GraphRAG vector search cannot run on D1

**Fix Options:**
1. Add UUID→TEXT conversion in sync layer
2. Migrate D1 schema to support UUID (D1 now supports UUID in beta)
3. Keep separate schemas and document divergence

---

### 4. gRPC Protocol Mismatch

**Rust Core exposes:**
- HTTP/1.1 on port 3000 (Axum)
- gRPC/TCP on port 50051 (Tonic)

**Edge Worker expects:**
- gRPC-Web over HTTPS (browser-compatible)

**Problem:** Railway's TCP service (`protocol: "tcp"`) doesn't support gRPC-Web directly.

**Fix Required:**
```rust
// In Rust Core: Enable gRPC-Web proxy or use tonic-web
use tonic_web::enable;
let grpc_service = enable(YourGrpcService);
```

Or update Edge Worker to use HTTP fallback for all requests.

---

### 5. Missing Environment Variables

**Rust Core needs:**
```bash
DATABASE_URL=postgresql://...
JWT_SECRET=...
GRPC_PORT=50051
HTTP_PORT=3000
```

**Edge Worker needs:**
```bash
RUST_CORE_HTTP_URL=https://<railway-url>.up.railway.app
RUST_CORE_GRPC_URL=https://<railway-url>.up.railway.app  # gRPC-Web
PYTHON_BACKEND_URL=https://<fallback-url>  # For non-AI routes
D1_SYNC_SECRET=<secret>
BACKEND_ORIGIN_SECRET=<secret>
```

**Current Gap:** No `.env.example` or secret documentation for Neural Mesh vars.

---

### 6. Deployment Script Gaps

`deploy-neural-mesh.sh` assumes:
- Docker CLI available locally
- Railway CLI authenticated
- Frontend directory exists at `frontend/`

**Missing:**
- Secret injection (`railway variables set`)
- D1 migration application (`wrangler d1 migrations apply`)
- Health check validation before traffic switch

---

## ✅ What Works

| Component | Status | Notes |
|-----------|--------|-------|
| Rust Core code | ✅ Complete | Compiles, has tests |
| Railway config (Rust) | ✅ Valid | Proper health checks |
| Edge Worker (prod) | ✅ Deployed | Live on syrabit.ai |
| D1 sync (Python→D1) | ✅ Working | 6-hourly cron |
| Staff Panel frontend | ✅ Complete | Mobile-responsive |
| JARVIS HUD | ✅ Complete | Three.js visualization |

---

## 🔧 Required Actions

### Phase 1: Configuration Alignment (2-3 hours)
1. **Update `workers/edge-proxy/wrangler.toml`:**
   - Add `RUST_CORE_URL` variable
   - Update routing logic to send `/api/rag/*`, `/api/chat/*` to Rust Core
   - Keep other routes on Python backend

2. **Create `/workspace/edge/wrangler.toml`:**
   - OR delete this directory if merging into `workers/edge-proxy`

3. **Add environment variable docs:**
   - Create `NEURAL_MESH_ENV.md` with all required vars
   - Add `.env.example` files

### Phase 2: Protocol Bridge (4-6 hours)
1. **Add gRPC-Web to Rust Core:**
   ```toml
   # Cargo.toml
   tonic-web = "0.11"
   ```
   
2. **Update Edge Worker gRPC calls:**
   - Use `@improbable-eng/grpc-web` for browser compatibility
   - Or convert to HTTP/JSON for simplicity

### Phase 3: Database Sync (3-4 hours)
1. **Update D1 migrations:**
   - Add UUID support (or document TEXT conversion)
   - Add vector metadata tables (for pre-computed embeddings)

2. **Update `d1_sync.py`:**
   - Handle UUID→TEXT conversion
   - Add error handling for schema mismatches

### Phase 4: Deployment Validation (2-3 hours)
1. **Test deployment sequence:**
   ```bash
   # 1. Deploy Rust Core
   cd backend/rust-core && railway up
   
   # 2. Apply migrations
   sqlx migrate run --database-url $DATABASE_URL
   
   # 3. Deploy Edge Worker
   cd workers/edge-proxy && wrangler deploy
   
   # 4. Validate health
   curl https://rust-core.<railway-url>/health
   curl https://api.syrabit.ai/api/health
   ```

2. **Add smoke tests:**
   - End-to-end RAG query
   - Staff login flow
   - WebSocket metrics stream

---

## 📋 Pre-Deployment Checklist

- [ ] Choose single Edge Worker (merge or delete duplicate)
- [ ] Update `BACKEND_URL` to point to Rust Core
- [ ] Add gRPC-Web support to Rust Core OR convert Edge to HTTP
- [ ] Align database schemas (UUID vs TEXT)
- [ ] Document all environment variables
- [ ] Test D1 sync with new schema
- [ ] Run deployment script in staging
- [ ] Validate health checks pass
- [ ] Monitor error rates after cutover

---

## 🎯 Recommendation

**Do NOT deploy yet.** Complete Phase 1-3 fixes first. The code is production-ready, but the infrastructure wiring has gaps that will cause runtime failures.

**Estimated time to deployment-ready:** 10-15 hours of focused work.
