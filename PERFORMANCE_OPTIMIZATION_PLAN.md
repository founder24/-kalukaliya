# Performance Optimization Plan — Syrabit.ai

## Executive Summary

This document outlines targeted performance optimizations across the full stack based on architectural analysis.

---

## 1. Frontend Optimizations (Cloudflare Pages)

### Current State
- ✅ Vendor chunk splitting implemented (Task #639)
- ✅ GA4 deferral implemented
- ✅ Modulepreload optimization
- ✅ Code splitting by feature (router, query, radix, markdown, icons)

### Recommended Optimizations

#### 1.1 Critical CSS Extraction
**Impact**: High (10-15 kB gzipped savings)
**Effort**: Medium

```bash
# Add critical CSS extraction to build pipeline
pnpm add --save-dev purgecss postcss
```

Update `vite.config.js`:
```javascript
import { PurgeCSS } from 'purgecss';

// Add post-processing step after build
const purgeCSS = new PurgeCSS();
const criticalCSS = await purgeCSS.purge({
  content: ['dist/index.html'],
  css: ['dist/assets/*.css']
});
```

#### 1.2 Image Optimization
**Impact**: Medium-High
**Effort**: Low

Add to `vite.config.js`:
```javascript
import viteImagemin from 'vite-plugin-imagemin';

plugins: [
  // ... existing plugins
  viteImagemin({
    gifsicle: { optimizationLevel: 7 },
    optipng: { optimizationLevel: 7 },
    mozjpeg: { quality: 75 },
    pngquant: { quality: [0.65, 0.8] },
    svgo: { plugins: [{ removeViewBox: false }] }
  })
]
```

#### 1.3 Service Worker Enhancement
**Impact**: High (repeat visits)
**Effort**: Medium

Current PWA manifest exists; enhance caching strategy:
```javascript
// In service worker or workbox config
runtimeCaching: [
  {
    urlPattern: /^https:\/\/api\.syrabit\.ai\/.*/i,
    handler: 'NetworkFirst',
    options: {
      cacheName: 'api-cache',
      expiration: { maxEntries: 100, maxAgeSeconds: 300 },
      networkTimeoutSeconds: 3
    }
  },
  {
    urlPattern: /\.(?:png|jpg|jpeg|svg|gif|webp)$/i,
    handler: 'CacheFirst',
    options: {
      cacheName: 'images-cache',
      expiration: { maxEntries: 50, maxAgeSeconds: 604800 }
    }
  }
]
```

---

## 2. Backend Optimizations (FastAPI + Gunicorn)

### Current State
- Workers: 3 (configurable via `GUNICORN_WORKERS`)
- Threads: 4 per worker
- Timeout: 300s
- Preload: enabled

### Recommended Optimizations

#### 2.1 Worker Configuration Tuning
**Impact**: High
**Effort**: Low

Update `gunicorn.conf.py`:
```python
import multiprocessing

# Dynamic worker count based on CPU cores
workers = int(os.environ.get("GUNICORN_WORKERS", str(multiprocessing.cpu_count() * 2 + 1)))
worker_class = "uvicorn.workers.UvicornWorker"
threads = int(os.environ.get("GUNICORN_THREADS", "2"))  # Reduce threads, increase workers

# Optimize for async workload
worker_tmp_dir = "/dev/shm"
timeout = 120  # Reduce from 300s for faster failure detection
graceful_timeout = 30  # Faster restarts
keepalive = 5  # Reduce connection overhead

# Connection limits
worker_connections = 1000
max_requests = 10000  # Increase from 5000
max_requests_jitter = 1000
```

#### 2.2 Database Connection Pooling
**Impact**: High
**Effort**: Medium

In `deps.py` or database initialization:
```python
from databases import Database

database = Database(
    DATABASE_URL,
    min_size=5,
    max_size=20,
    timeout=30.0,
    pool_recycle=3600
)
```

#### 2.3 Async HTTP Client Reuse
**Impact**: Medium-High
**Effort**: Low-Medium

Already partially implemented in `llm.py` with `_OAI_HTTP_TRANSPORT`. Extend to all HTTP clients:

```python
# Global shared client
_http_client: Optional[httpx.AsyncClient] = None

def get_http_client():
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(
                http2=True,
                limits=httpx.Limits(
                    max_connections=500,
                    max_keepalive_connections=200,
                    keepalive_expiry=120.0
                )
            ),
            timeout=httpx.Timeout(30.0, connect=10.0)
        )
    return _http_client
```

#### 2.4 Response Compression
**Impact**: Medium
**Effort**: Low

Add to `server.py`:
```python
from fastapi.responses import ORJSONResponse
from fastapi.middleware.gzip import GZipMiddleware

app.add_middleware(GZipMiddleware, minimum_size=1000)

# Use ORJSONResponse for JSON endpoints (faster serialization)
@app.get("/api/...")
async def endpoint():
    return ORJSONResponse(data)
```

---

## 3. Caching Strategy Enhancements

### Current State
- ✅ L1 in-memory TTLCache with instrumentation
- ✅ Redis L2 cache
- ✅ AI response cache
- ✅ RAG cache
- ✅ Content cache

### Recommended Optimizations

#### 3.1 Multi-Level Cache Hierarchy
**Impact**: High
**Effort**: Medium

Implement explicit 3-tier caching:
```python
class TieredCache:
    def __init__(self, l1_maxsize=1024, l1_ttl=60, l2_ttl=300):
        self.l1 = TTLCache(maxsize=l1_maxsize, ttl=l1_ttl)
        self.l2 = redis_client
        self.l2_ttl = l2_ttl
    
    async def get(self, key: str):
        # L1 check
        if key in self.l1:
            return self.l1[key]
        
        # L2 check
        cached = await self.l2.get(f"cache:{key}")
        if cached:
            data = json.loads(cached)
            self.l1[key] = data  # Promote to L1
            return data
        
        return None
    
    async def set(self, key: str, value: Any):
        self.l1[key] = value
        await self.l2.setex(f"cache:{key}", self.l2_ttl, json.dumps(value))
```

#### 3.2 Cache Warming Strategy
**Impact**: Medium-High
**Effort**: Medium

Already has prewarm engine (`aca_jobs/prewarm_seo_routes.py`). Enhance with:
- Predictive warming based on user behavior patterns
- Time-based warming before peak hours
- Subject/chapter popularity tracking

---

## 4. RAG Pipeline Optimizations

### Current State
- Hybrid search (vector + keyword)
- Graph traversal (5-hop)
- Multiple embedding providers

### Recommended Optimizations

#### 4.1 Embedding Cache Hit Rate
**Impact**: High
**Effort**: Low

In `vectorize_client.py` or embedding layer:
```python
# Add semantic similarity check before embedding
from sklearn.metrics.pairwise import cosine_similarity

_cached_embeddings = {}

def get_cached_embedding(query: str, threshold=0.95):
    query_hash = hashlib.sha256(query.encode()).hexdigest()
    
    # Check exact match first
    if query_hash in _cached_embeddings:
        return _cached_embeddings[query_hash]
    
    # Check similar queries (optional, adds compute)
    for cached_query, embedding in _cached_embeddings.items():
        similarity = cosine_similarity([query_emb], [embedding])[0][0]
        if similarity > threshold:
            return embedding
    
    return None
```

#### 4.2 Parallel Retrieval
**Impact**: High
**Effort**: Medium

```python
async def parallel_retrieve(query: str):
    vector_results, keyword_results, graph_results = await asyncio.gather(
        vector_search(query),
        keyword_search(query),
        graph_traversal(query),
        return_exceptions=True
    )
    return merge_results(vector_results, keyword_results, graph_results)
```

#### 4.3 Query Intent Classification Cache
**Impact**: Medium
**Effort**: Low

Cache intent classification results:
```python
_intent_cache = TTLCache(maxsize=2048, ttl=3600)

async def classify_intent_cached(query: str):
    if query in _intent_cache:
        return _intent_cache[query]
    
    intent = await classify_intent(query)
    _intent_cache[query] = intent
    return intent
```

---

## 5. Edge Proxy Optimizations (Cloudflare Workers)

### Current State
- Bot detection and caching
- Rate limiting
- Analytics

### Recommended Optimizations

#### 5.1 Edge Caching Rules
**Impact**: High
**Effort**: Low

Update `workers/edge-proxy/src/index.ts`:
```typescript
// Aggressive caching for static content
if (request.method === 'GET' && isStaticPath(url.pathname)) {
  const cached = await caches.default.match(request);
  if (cached) return cached;
  
  const response = await fetch(request);
  const cloned = response.clone();
  
  // Cache with longer TTL for static assets
  if (response.ok) {
    const headers = new Headers(cloned.headers);
    headers.set('Cache-Control', 'public, max-age=31536000, immutable');
    return new Response(cloned.body, { headers });
  }
}
```

#### 5.2 Request Coalescing
**Impact**: Medium
**Effort**: Medium

Implement request deduplication at edge:
```typescript
const pendingRequests = new Map<string, Promise<Response>>();

async function coalescedFetch(key: string, request: Request): Promise<Response> {
  if (pendingRequests.has(key)) {
    return pendingRequests.get(key)!;
  }
  
  const promise = fetch(request).finally(() => {
    pendingRequests.delete(key);
  });
  
  pendingRequests.set(key, promise);
  return promise;
}
```

---

## 6. Rust Core Optimizations

### Current State
- gRPC server with tonic-web
- Axum HTTP server
- GraphRAG implementation

### Recommended Optimizations

#### 6.1 Database Query Optimization
**Impact**: High
**Effort**: Medium

In `src/services/graph_rag.rs`:
```rust
// Use prepared statements and connection pooling
let pool = PgPoolOptions::new()
    .max_connections(20)
    .min_connections(5)
    .acquire_timeout(Duration::from_secs(30))
    .connect(&database_url)
    .await?;

// Batch queries where possible
let results = sqlx::query_as::<_, Document>(
    "SELECT * FROM documents WHERE id = ANY($1)"
)
.bind(&doc_ids)
.fetch_all(&pool)
.await?;
```

#### 6.2 Async Stream Optimization
**Impact**: Medium
**Effort**: Low

```rust
use futures::stream::{self, StreamExt};

// Process items in parallel batches
let results: Vec<_> = stream::iter(items)
    .chunks(10)
    .map(|batch| process_batch(batch))
    .buffer_unordered(5)
    .collect()
    .await;
```

---

## 7. Monitoring & Profiling

### Recommended Tools

#### 7.1 Backend Profiling
```bash
# Install py-spy for production profiling
pip install py-spy

# Sample running process
py-spy record -o profile.svg --pid $(pgrep -f gunicorn)

# Use aiohttp-trace for async profiling
```

#### 7.2 Frontend Performance Monitoring
Already has Firebase Performance Monitoring. Add:
- Web Vitals reporting to analytics
- Custom metrics for chat latency
- Error rate tracking

#### 7.3 Database Query Analysis
```sql
-- Enable pg_stat_statements
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Find slow queries
SELECT query, calls, total_exec_time, mean_exec_time
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 20;
```

---

## 8. Quick Wins (Priority Order)

1. **Gunicorn worker tuning** (15-30% throughput improvement)
2. **HTTP client connection pooling** (10-20% latency reduction)
3. **Response compression** (40-60% bandwidth savings)
4. **Database connection pooling** (50-80% connection overhead reduction)
5. **Edge caching rules** (60-80% origin request reduction)
6. **Query intent cache** (30-50% classification latency reduction)
7. **Parallel RAG retrieval** (40-60% query latency reduction)

---

## 9. Implementation Roadmap

### Phase 1 (Week 1-2): Infrastructure
- [ ] Gunicorn configuration tuning
- [ ] HTTP client pooling
- [ ] Response compression
- [ ] Database connection pooling

### Phase 2 (Week 3-4): Caching
- [ ] Tiered cache implementation
- [ ] Cache warming enhancements
- [ ] Embedding cache optimization

### Phase 3 (Week 5-6): RAG Pipeline
- [ ] Parallel retrieval
- [ ] Intent classification cache
- [ ] Query optimization

### Phase 4 (Week 7-8): Edge & Frontend
- [ ] Edge caching rules
- [ ] Request coalescing
- [ ] Critical CSS extraction
- [ ] Image optimization

---

## 10. Success Metrics

Track these KPIs:
- **P95 Latency**: Target <500ms for chat responses
- **Cache Hit Rate**: Target >80% for L1+L2 combined
- **Throughput**: Target 100+ req/s per backend instance
- **PageSpeed Score**: Target 95+ mobile
- **Error Rate**: Target <0.1%

---

## Next Steps

1. **Baseline Measurement**: Run current performance benchmarks
2. **Prioritize**: Select top 3 optimizations based on impact/effort
3. **Implement**: Start with quick wins
4. **Measure**: Compare before/after metrics
5. **Iterate**: Continue with next priority items

Would you like me to implement any specific optimization from this plan?
