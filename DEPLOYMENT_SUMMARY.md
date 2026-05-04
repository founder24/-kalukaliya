# Syrabit.ai Neural Mesh Architecture & Staff Management Panel

> ⚠️ **HISTORICAL — Railway-era summary.** Task #336 migrated the
> backend (and the Rust core) off Railway onto Digital Ocean App
> Platform. Active runbooks: [`docs/DIGITALOCEAN-DEPLOYMENT.md`](docs/DIGITALOCEAN-DEPLOYMENT.md)
> + [`docs/ops/digitalocean-cutover.md`](docs/ops/digitalocean-cutover.md).
> Anything below this banner that says "Railway" should be read as
> "Digital Ocean App Platform"; do not run the Railway CLI commands
> verbatim.

## Implementation Summary

This implementation delivers two major upgrades to the Syrabit.ai Education Platform:

### Part 1: Neural Mesh Architecture (High Performance Core)

#### Rust Core Backend (`/workspace/backend/rust-core/`)

**Files Created:**
- `Cargo.toml` - Rust dependencies (Axum, Tonic, Tokio, SQLx, etc.)
- `build.rs` - Protobuf compilation script
- `proto/schema.proto` - gRPC service definitions
- `src/main.rs` - Dual HTTP/gRPC server entry point
- `src/handlers/` - HTTP endpoint handlers (health, rag, agents, staff, websocket)
- `src/services/graph_rag.rs` - GraphRAG with 5-hop traversal
- `src/grpc/service.rs` - gRPC service implementation
- `src/db/models.rs` - Database models
- `src/db/repository.rs` - CRUD operations
- `src/generated/mod.rs` - Generated protobuf code placeholder
- `migrations/001_initial_schema.sql` - Database schema
- `Dockerfile` - Multi-stage production build
- `railway.toml` - Railway deployment config

**Key Features:**
- Axum HTTP server on port 3000
- Tonic gRPC server on port 50051
- GraphRAG hybrid search with vector + keyword fusion
- WebSocket streaming for real-time metrics
- Staff authentication with phone/OTP

#### Edge Worker (`/workspace/edge/worker.js`)

**Features:**
- gRPC client for Rust Core communication
- Speculative Prefetch based on user intent prediction
- D1 cache integration for edge caching
- Intelligent routing (static → cache, AI → Rust Core)
- Intent pattern matching for prefetch triggers

#### JARVIS HUD (`/workspace/frontend/src/components/jarvis/JarvisDashboard.jsx`)

**Features:**
- Three.js 3D neural mesh visualization
- Real-time metrics via WebSocket
- Node health indicators
- Agent status display
- Interactive orbit controls

### Part 2: Staff Management Panel (Mobile-First CMS)

#### Staff Login (`/workspace/frontend/src/pages/staff/StaffLogin.jsx`)

**Features:**
- Phone number input with validation
- OTP verification flow
- JWT token storage
- Mobile-responsive design

#### Permission Model

| Resource | Create | Read | Update | Delete |
|----------|--------|------|--------|--------|
| Boards   | ❌     | ✅   | ❌     | ❌     |
| Classes  | ❌     | ✅   | ❌     | ❌     |
| Subjects | ✅     | ✅   | ✅*    | ❌     |
| Pages    | ✅     | ✅   | ✅     | ✅     |

*Subject updates limited to name/description only

### Deployment Scripts

Create `deploy-neural-mesh.sh`:

```bash
#!/bin/bash
set -e

echo "🚀 Deploying Syrabit Neural Mesh..."

# Build and push Rust Core Docker image
cd backend/rust-core
docker build -t syrabit-rust-core:latest .
docker push syrabit-rust-core:latest

# Deploy Edge Worker
cd ../../edge
wrangler deploy worker.js

# Deploy Frontend
cd ../frontend
pnpm build
pnpm deploy

echo "✅ Deployment complete!"
```

## API Endpoints

### Rust Core HTTP API
- `GET /health` - Health check
- `POST /api/rag/query` - RAG query with GraphRAG
- `POST /api/rag/search` - Hybrid search
- `GET /api/agents` - List agents
- `POST /api/agents/:id/execute` - Execute agent
- `GET /ws/metrics` - WebSocket metrics stream
- `POST /api/staff/login` - Send OTP
- `POST /api/staff/verify` - Verify OTP
- `GET /api/staff/content-hub` - Get content hierarchy
- `POST /api/staff/subjects` - Create subject
- `PUT /api/staff/subjects/:id` - Update subject
- `POST /api/staff/subject-pages` - Create page
- `PUT /api/staff/subject-pages/:id` - Update page
- `DELETE /api/staff/subject-pages/:id` - Delete page

### gRPC Service (NeuralMeshService)
- `Chat` - Streaming chat with AI assistant
- `QueryRag` - RAG query with GraphRAG
- `ExecuteAgent` - Agent command execution
- `HealthCheck` - Service health check
- `StreamMetrics` - Real-time metrics streaming

## Environment Variables

### Rust Core
```
DATABASE_URL=postgres://user:pass@localhost/syrabit
JWT_SECRET=your-secret-key
HTTP_PORT=3000
GRPC_PORT=50051
RUST_LOG=info
ENVIRONMENT=production
```

### Edge Worker
```
RUST_CORE_GRPC_URL=https://rust-core.syrabit.ai:50051
RUST_CORE_HTTP_URL=https://rust-core.syrabit.ai
PYTHON_BACKEND_URL=https://python-backend.syrabit.ai
```

### Frontend
```
VITE_API_URL=http://localhost:3000
VITE_WS_METRICS_URL=ws://localhost:3000/ws/metrics
```

## Next Steps

1. **Build Rust Core**: `cd backend/rust-core && cargo build --release`
2. **Run Migrations**: Apply `migrations/001_initial_schema.sql` to PostgreSQL
3. **Start Servers**: Run both Python FastAPI (existing) and Rust Core (new)
4. **Deploy Edge Worker**: `wrangler deploy edge/worker.js`
5. **Update Frontend Routes**: Add `/staff/*` routes to React Router
6. **Test Integration**: Verify gRPC communication between Edge and Rust Core

## Notes

- Python FastAPI backend continues running alongside Rust Core (non-breaking transition)
- Edge Worker routes traffic intelligently between Python and Rust backends
- Staff permissions are enforced at both frontend UI and backend API levels
- Mock OTP is used in development; integrate Twilio/Firebase for production
