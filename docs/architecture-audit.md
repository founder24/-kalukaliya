# Syrabit — Fullstack End-to-End Architecture Audit
> Generated: 2026-06-30 | Covers: frontend, edge worker, backend, RAG pipeline, deploy, secrets, canonical mapping

---

## 1. System Map

```
Browser
  │
  ├─ syrabit.ai  ──────────────────────► Cloudflare Pages (React + Vite SPA)
  │                                        └─ Bot traffic → botRenderPlugin (SSR-lite HTML)
  │                                        └─ /pyq/* → pyqPagePlugin (backend proxy for SEO)
  │
  └─ api.syrabit.ai ──────────────────► Cloudflare Edge Worker
                                           │  JWT verify, rate-limit (KV), CORS, HMAC sign
                                           │  Edge AI: TTS + OCR (Workers AI, never hits backend)
                                           │  ISR cache: /api/v1/content/library-bundle (KV)
                                           │
                                           └─► Cloud Run — FastAPI (asia-south1)
                                                 │  Auth, Chat, Content, Admin, RAG, Publish
                                                 │  Secret Manager → overrides env at startup
                                                 │
                                                 ├─► MongoDB Atlas (primary data store)
                                                 ├─► Cloudflare Vectorize (embeddings + RAG)
                                                 └─► GCS (published JSON, sitemaps, artifacts)
```

---

## 2. Layer Roles & Boundaries

| Layer | Owns | Must NOT Do |
|---|---|---|
| **CF Pages** | Static SPA delivery, bot-rendered HTML, PYQ proxy | Business logic, data decisions |
| **Edge Worker** | JWT verify, rate-limit, CORS, HMAC sign, TTS/OCR, KV cache | Content state, auth decisions, parallel truth |
| **Cloud Run** | All business logic, auth, chat, content CRUD, RAG orchestration | Direct public exposure (all traffic via Edge) |
| **MongoDB Atlas** | Users, content, conversations, jobs, quotas, chunks, analytics | Embeddings (Vectorize owns these) |
| **CF Vectorize** | Embeddings + filtered vector retrieval | Source of truth for content text |
| **GCS** | Published JSON artifacts, sitemap assets, delivery payloads | Live read path for users |

---

## 3. Live Domain Routing

```
syrabit.ai → CF Pages → React SPA
  /library, /chat, /history, /profile, /signup, /login, /admin
  /:board/:class/:subject/:chapter  (SEO chapter pages)
  /pyq/*  → pyqPagePlugin → backend proxy (SSO SEO HTML)

api.syrabit.ai → CF Edge Worker → Cloud Run
  /api/v1/chat/stream   (SSE — streaming AI)
  /api/v1/chat/tts      (edge-handled, Workers AI)
  /api/v1/chat/image    (edge-handled, Workers AI)
  /api/v1/content/*     (library-bundle cached at edge)
  /api/v1/admin/*       (admin APIs, cookie auth)
  /health, /health/full
```

---

## 4. Frontend Architecture

### 4.1 Routes (App.jsx)

| Route | Component | Key URL Params |
|---|---|---|
| `/library`, `/browser` | `LibraryPage` | `?q`, `?filter` |
| `/chat` | `ChatPage` | `?id`, `?subject`, `?chapter`, `?section`, `?document_id` |
| `/:board/:class/:subject` | `SubjectPage` | — |
| `/:board/:class/:subject/:chapter` | `ChapterPage` | `?tab` (notes/qa/pyq) |
| `/:board/:class/:stream/:subject/:chapter` | `ChapterPage` | `?tab` (with stream) |
| `/as/:board/...` | `ChapterPage` | Assamese language variants |
| `/.../topic/:topicSlug` | `ChapterPage` | deep-links to topic answer card |
| `/profile` | `ProfilePage` | `?upgrade` |
| `/history` | `HistoryPage` | — |
| `/admin` | `AdminPage` | `?s` (section), `?t` (tab), `?st` (subtab) |

**Route guards:** `AuthGuard`, `AdminGuard`, `StaffGuard` — all check `user` object from `AuthContext`.

### 4.2 Auth State (AuthContext + useTokenManager)

```
Access Token  → in-memory (_inMemoryToken) + sessionStorage (tab restore)
Refresh Token → localStorage (cross-session persistence)

Login flow:
  POST /auth/login → { access_token, refresh_token }
  → setInMemoryToken(access) + localStorage.set(refresh)

Silent refresh (useAuthRefresh):
  On 401 → POST /auth/refresh with refresh_token → new access_token

Logout:
  POST /auth/logout (sends token for blacklisting)
  → clears in-memory + storage
```

**Admin auth:** Separate `ADMIN_JWT_SECRET` → cookie-first (`HttpOnly`), Bearer fallback only for cron routes.

### 4.3 API Client (utils/api.jsx)

```javascript
Base URL: VITE_BACKEND_URL || 'https://api.syrabit.ai/api/v1'
         (dev: Vite proxy → localhost:8000)

Headers:
  Authorization: Bearer <access_token>  (when authenticated)
  x-anon-id: <uuid from localStorage>   (always, for rate-limit identity)
  withCredentials: true                  (for HttpOnly cookies)

Retry interceptor:
  GET requests → retry up to 2× on 408/429/5xx
  Admin 401 → redirect to /admin/login
```

### 4.4 Library Loading Strategy

```
Phase 1: GET /content/library-bundle?slim=1  (skeleton cards — LCP)
Phase 2: GET /content/library-bundle?boot=1 (content types, tabs)
Phase 3: GET /content/library-bundle         (full detail — lazy)
```

---

## 5. Cloudflare Edge Worker

### 5.1 Responsibilities

```
Entry: apps/edge/src/index.ts
Route:  api.syrabit.ai/*

Per request:
  1. CORS enforcement (allowlist: syrabit.ai, preview.pages.dev, localhost:5000)
  2. JWT verification (HS256 or RS256, JWT_SECRET)
  3. Rate-limit check (RATE_LIMIT_KV — per lang, per anon/user)
  4. HMAC request signing → X-Edge-Signature, X-Edge-Timestamp
  5. OIDC token injection (GOOGLE_SA_KEY) for Cloud Run identity
  6. Proxy to BACKEND_URL (Cloud Run)
  7. Stream-aware forwarding (SSE passthrough for /chat/stream)

Edge-handled (never hits Cloud Run):
  /api/v1/chat/tts   → CF Workers AI (Whisper/TTS)
  /api/v1/chat/image → CF Workers AI (OCR)
  /health            → edge liveness
  /sitemap*.xml, /robots.txt → KV or static

Cached at edge:
  /api/v1/content/library-bundle → ISR_CACHE_KV (content delivery)
```

### 5.2 Bindings & Secrets (wrangler.toml)

| Name | Type | Purpose |
|---|---|---|
| `BACKEND_URL` | Secret | Cloud Run URL |
| `JWT_SECRET` / `JWT_PUBLIC_KEY` | Secret | Token verification |
| `EDGE_SHARED_SECRET` | Secret | HMAC signing key |
| `GOOGLE_SA_KEY` | Secret | OIDC for Cloud Run auth |
| `RATE_LIMIT_KV` | KV Namespace | Hourly rate-limit windows |
| `ISR_CACHE_KV` | KV Namespace | Library bundle cache + health probes |
| `CONTENT_KV` | KV Namespace | Pre-rendered chapter content |
| `R2_BUCKET` | R2 | Static assets (`syrabit-assets`) |
| `AI` | Workers AI | TTS + OCR at edge |

### 5.3 Edge → Backend Trust Contract

```
Edge signs:
  message = "{timestamp}:{user_id}:{path}"
  sig     = HMAC-SHA256(EDGE_SHARED_SECRET, message)
  headers → X-Edge-Signature, X-Edge-Timestamp, X-User-ID, X-User-JWT

Backend validates (TRUST_EDGE_AUTH=true):
  1. abs(now - X-Edge-Timestamp) ≤ 30s  (replay protection)
  2. Re-compute HMAC; compare_digest(expected, received)
  3. If match → trust X-User-ID, skip JWT re-verification
  4. Fallback → verify X-User-JWT directly
```

**Rate-limit bypass for degraded path:** If Edge KV is unavailable, the Worker fails open (allows request through) to prevent service disruption. Backend enforces its own MongoDB-backed auth rate limit as a second layer.

---

## 6. Backend (Cloud Run / FastAPI)

### 6.1 Startup Sequence (main.py lifespan)

```
1. init_mongo()
   └─ Connect to Atlas (Beanie ODM)
   └─ Create/verify indexes (auto-heals conflicting non-TTL indexes)
   └─ Run pending DB migrations (migrations/runner.py)

2. load_secrets_into_settings()
   └─ GCP Secret Manager → overrides env vars live into settings object
   └─ Sources: SARVAM_API_KEY, JWT_SECRET, ADMIN_JWT_SECRET,
               RAZORPAY_*, RESEND_API_KEY, POSTHOG_API_KEY,
               EDGE_SHARED_SECRET, INDEXNOW_API_KEY

3. topic_matcher.warmup()
   └─ Load all TopicEmbedding docs from Atlas into memory
   └─ 197 topics @ startup → zero cold-start on first chat

4. greeting_rag.warmup()
   └─ Pre-embed 106 common greeting phrases (9 intent categories)
   └─ Serves greetings from memory (<5ms, no LLM call)

5. admin_bootstrap()
   └─ Create/promote admin user from ADMIN_EMAIL + ADMIN_PASSWORD
```

### 6.2 Router Map (/api/v1/*)

| Router | Path Prefix | Key Endpoints |
|---|---|---|
| `auth` | `/auth` | login, signup, logout, refresh, reset-password |
| `users` | `/users` | me, profile, account, memories |
| `chat` | `/chat` | stream (SSE), history, conversations |
| `content` | `/content` | library-bundle, chapters, subjects, question-papers |
| `conversations` | `/conversations` | list, detail, delete |
| `edu` | `/edu` | curriculum, board/class/stream lookup |
| `payments` | `/payments` | Razorpay order, verify, webhook |
| `admin_content` | `/admin/content` | CRUD chapters, subjects, topics |
| `admin_rag` | `/admin/rag` | upload, reindex (single/subject/bulk chapters), jobs, vectorize/info |
| `admin_dashboard` | `/admin` | stats, health, analytics |
| `admin_ai` | `/admin/ai` | LLM config, prompt testing |
| `admin_vertex` | `/admin/vertex` | Vertex AI search |
| `admin_security` | `/admin/security` | token blacklist, audit log |
| `admin_settings` | `/admin/settings` | feature flags, config |
| `seo` | `/seo` | sitemap, indexnow, structured data |

### 6.3 Chat Streaming Pipeline

```
POST /api/v1/chat/stream  (SSE)

1. Auth & rate-limit
   └─ Edge HMAC trust bypass OR JWT verify
   └─ MongoDB auth_rate_limit (90s TTL, no Redis dependency)
   └─ Quota check: user.credits_used / monthly_message_count

2. Input sanitization
   └─ sanitize_user_input() — strips prompt injection markers

3. source_type normalization (ChatRequest model_validator)
   └─ normalize_source_type(req.source_type)
   └─ "qa" → "important_questions", "question_paper" → "pyq"

4. Topic matching (in-memory cosine, 197 topics)
   └─ Returns: chapter_id, subject_id, match_score, confidence_tier

5. Confidence-gated retrieval
   ├─ HIGH ≥0.65: MongoDB fast-path (chapter content direct, ~30ms)
   │               Web search SKIPPED (reduces noise)
   ├─ LOW 0.50–0.65: Vectorize RAG v2 + parallel web search fallback
   └─ NONE <0.50: Web search + LLM general knowledge

6. _card_filters built from:
   └─ subject_id, chapter_id (from topic match OR request card)
   └─ source_type (from normalized ChatRequest.source_type)
   └─ Passed to BOTH high-confidence and low-confidence retrieval paths

7. SSE emission sequence:
   a. event: source_card  — chapter/subject metadata (before LLM starts)
   b. data: {"content": "...", "done": false}  — streamed LLM tokens
   c. data: {"done": true, "source_type": "...", ...}  — metadata
   d. event: syrabit_done — route trace, match_score, rag_path, lang

8. Fire-and-forget:
   └─ Save conversation to MongoDB
   └─ Update user monthly_message_count + AiUsageLog (token spend)
```

---

## 7. RAG Pipeline

### 7.1 Canonical Source Types

```python
FRONTEND_SECTION_TO_SOURCE_TYPE = {
    "notes":          "notes",
    "qa":             "important_questions",   # Q&A tab
    "pyq":            "pyq",                   # Question Paper tab
    # Legacy aliases (normalize at the boundary):
    "question_paper": "pyq",
    "definition":     "definition",
    "mcqs":           "mcqs",
    "book_pdf":       "notes",                 # import compat
}
```

**Rule:** Normalize once at the request boundary (`ChatRequest.model_validator`). Never pass frontend aliases into retrieval or DB queries.

### 7.2 Language Mapping

```
Frontend lang param:   "en" → medium: "english"
                       "as" → medium: "assamese"

Vectorize filter key:  "medium"  (not "lang")
MongoDB chunk field:   "medium"

Sarvam model selection:
  English:   sarvam-30b (fast) / sarvam-105b (quality)
             enable_thinking=True → content field
  Assamese:  same models
             enable_thinking=False → extract from reasoning_content
```

### 7.3 Ingestion Pipeline (ingestion_v2.py)

```
Text input
  │
  ├─ clean_text()     — Unicode NFC, Bijoy→Unicode, boilerplate strip
  ├─ detect_language()
  ├─ chunk_content()  — source-type-aware strategy:
  │     notes       → semantic chunking
  │     qa/pyq      → qa_pair chunking
  │
  ├─ embed_batch_chunked()  — CF Workers AI @cf/baai/bge-m3 (1024-dim)
  │     batches of 50 chunks
  │
  ├─ _upsert_to_mongo()     — "chunks" collection (dual-write)
  │     _id = {document_id}_c{idx:04d}  (stable, used as Vectorize ID)
  │
  └─ vectorize_client.upsert()  — embeddings + camelCase metadata
        subjectId, chapterId, topicId, medium, sourceType, chunkType
        batch size: 100 vectors

Updates:
  RagDocument.status → "completed"
  GenerationJob.progress → 100
  Chapter.rag_indexed_at → timestamp (for admin sync badges)
```

### 7.4 Retrieval Pipeline (retrieval_v2.py)

```
Query embedding (bge-m3, 1024-dim)
  │
  Stage 1: Fast path (TopicMatcher, in-memory cosine)
  │         match_score ≥ 0.65 → fetch chapter from MongoDB directly (~30ms)
  │         Skip Vectorize entirely
  │
  Stage 2: Vectorize path (if fast-path misses)
  │         Build filter:
  │           snake_to_vectorize_filter({
  │             "subject_id"  → "subjectId",
  │             "chapter_id"  → "chapterId",
  │             "source_type" → "sourceType",
  │             "medium"      → "medium",
  │           })
  │         vectorize_client.query(embedding, top_k=10, filter=...)
  │         _hydrate_chunks(vector_ids) → MongoDB chunks collection
  │
  Stage 3: Legacy fallback
            Atlas $vectorSearch on old "rag_chunks" collection
            (backward compat only — remove once all content reindexed on v2)
```

### 7.5 Required Vectorize Metadata Indexes

All 6 must be created via `wrangler vectorize create-metadata-index`:

| Field | Type | Used For |
|---|---|---|
| `subjectId` | string | Subject-scoped retrieval |
| `chapterId` | string | Chapter-scoped retrieval |
| `topicId` | string | Topic deep-links |
| `medium` | string | Language filtering (english/assamese) |
| `sourceType` | string | Section filtering (notes/important_questions/pyq) |
| `chunkType` | string | Chunk strategy filtering |

**Health check:** `GET /admin/rag/vectorize/info` now returns `health.status: ok|degraded|unconfigured` and lists exactly which indexes are missing.

---

## 8. Content Publish Pipeline

```
Admin edits chapter in CMS → Save (MongoDB only, no delivery)
                                        ↓
Admin clicks Publish → POST /admin/content/chapters/:id/publish
                                        ↓
    PublishJob created (MongoDB publish_jobs collection)
    asyncio.create_task → 7-step pipeline:

    Step 1: gcs         — Write full chapter JSON to GCS (source of truth)
    Step 2: cloudflare  — Trigger CF Worker /api/prerender (cache warm)
    Step 3: status_update — Mark chapter published in MongoDB
    Step 4: pages_rebuild — POST to CF Pages deploy hook (static rebuild)
    Step 5: indexnow    — Submit chapter + topic URLs to search engines
    Step 6: wikidata    — batch_lookup_wikidata → store sameAs URIs
    Step 7: embeddings  — Generate TopicEmbedding records (fast-path warmup)

Admin polls GET /admin/content/publish-jobs/:job_id for progress
```

**Separation rule:**
```
Save     → data persistence (MongoDB)
Publish  → content delivery (GCS + Pages + IndexNow)
Reindex  → retrieval update (Vectorize + chunks collection)
```

---

## 9. Reindex Pipeline

```
POST /admin/rag/reindex/chapter/:id        — single chapter
POST /admin/rag/reindex/subject/:subject_id — all chapters for a subject
POST /admin/rag/reindex/chapters           — bulk: arbitrary list of chapter IDs ← NEW

Per chapter:
  1. Load chapter from MongoDB (rag_text_* preferred over content_*)
  2. Run ingest_chapter_v2() — full chunk→embed→write pipeline
  3. Stamp Chapter.rag_indexed_at
  4. Update GenerationJob progress

Concurrency: asyncio.Semaphore(parallelism, max=10) — avoids CF API rate limits
Poll: GET /admin/rag/jobs/:job_id
```

---

## 10. Deploy & Secrets Infrastructure

### 10.1 Secret Map by Layer

| Secret | GCP SM Name | Cloud Run Env Var | Edge Worker | GitHub Actions |
|---|---|---|---|---|
| MongoDB URI | `mongodb-uri` | `MONGODB_URI` | — | — |
| User JWT | `jwt-secret` | `JWT_SECRET` | `JWT_SECRET` | — |
| Admin JWT | `admin-jwt-secret` | `ADMIN_JWT_SECRET` | — | — |
| Reset token | `reset-token-secret` | `RESET_TOKEN_SECRET` | — | — |
| Sarvam API | `sarvam-api-key` | `SARVAM_API_KEY` | — | — |
| Edge HMAC | `edge-shared-secret` | `EDGE_SHARED_SECRET` | `EDGE_SHARED_SECRET` | — |
| Razorpay ID | `razorpay-key-id` | `RAZORPAY_KEY_ID` | — | — |
| Razorpay Secret | `razorpay-key-secret` | `RAZORPAY_KEY_SECRET` | — | — |
| Razorpay Webhook | `razorpay-webhook-secret` | `RAZORPAY_WEBHOOK_SECRET` | — | — |
| Resend | `resend-api-key` | `RESEND_API_KEY` | — | — |
| PostHog | `posthog-api-key` | `POSTHOG_API_KEY` | — | — |
| IndexNow | `indexnow-api-key` | `INDEXNOW_API_KEY` | — | — |
| GCP SA | `GOOGLE_APPLICATION_CREDENTIALS_JSON` | same | `GOOGLE_SA_KEY` | `GCP_SA_KEY` |
| CF Token | — | — | — | `CLOUDFLARE_API_TOKEN` |
| CF Account | — | — | — | `CLOUDFLARE_ACCOUNT_ID` |
| CF Worker AI | `cf-worker-ai-token` | `CF_WORKER_AI_TOKEN` | — | — |
| CF API Token | `cf-api-token` | `CF_API_TOKEN` | — | — |
| CF Pages Hook | `cf-pages-deploy-hook` | `CF_PAGES_DEPLOY_HOOK` | — | — |
| Backend URL | — | — | `BACKEND_URL` | — |

**Critical rule:** Cloud Run `gcloud run deploy` drops all `--update-secrets` refs on every deploy unless explicitly re-passed. Always use the explicit `--update-secrets` flag in every deploy step (see `cloudbuild.yaml` Step 4 + Step 5 optional probe).

### 10.2 Cloud Build Pipeline (cloudbuild.yaml)

```
Step 0: SSH setup (optional — private repo access)
Step 1: Pre-flight — pip install --dry-run (verify PyPI availability)
Step 2: docker build → Artifact Registry
Step 3: docker push
Step 4: gcloud run deploy (core --update-secrets)
Step 5: Optional secrets probe + attach (Sentry, CF creds, etc.)
```

### 10.3 GitHub Actions Pipelines

```
.github/workflows/deploy.yml:

Backend job:
  1. Authenticate (GCP_SA_KEY)
  2. docker build + push
  3. gcloud run deploy (core secrets + optional probe || true)

Edge job:
  1. scripts/sync_cf_sa_key.py → CF Worker secret
  2. wrangler secret put (JWT_SECRET, EDGE_SHARED_SECRET, BACKEND_URL)
  3. wrangler deploy

Frontend job:
  1. pnpm build
  2. wrangler pages deploy
  3. scripts/indexnow-submit.mjs (if INDEXNOW_SECRET present)
```

---

## 11. Canonical Identity Rules

### 11.1 ID Chain (must be consistent across ALL layers)

```
board_id → class_id → stream_id → subject_id → chapter_id → topic_id

Same ID used in:
  ✓ MongoDB document _id
  ✓ Vectorize chunk metadata (chapterId, subjectId, topicId)
  ✓ URL path segments (/:board/:class/:subject/:chapter)
  ✓ Chat request card context (chapter_id, subject_id)
  ✓ Retrieval filters (_card_filters)
  ✓ Admin content editor

DB uses: legacy string IDs (e.g. 's13', UUID) — NOT ObjectIds
All reference fields use FlexId (accepts both str and ObjectId)
```

### 11.2 Source Type Normalization Boundary

```
User/URL → frontend section key → ChatRequest → normalize_source_type() → retrieval
                                                                          ↓
                                                 "qa"    → "important_questions"
                                                 "pyq"   → "pyq"
                                                 "notes" → "notes"

Normalize ONCE at the request boundary. Never pass frontend aliases to:
  - Vectorize filter (sourceType field)
  - MongoDB chunk queries (source_type field)
  - Reindex requests (source_type param)
```

### 11.3 Language / Medium Mapping Boundary

```
Frontend lang:  "en" / "as"
ChatRequest:    lang field → normalized at service layer
Retrieval:      medium = "english" / "assamese"
Vectorize meta: "medium": "english" / "medium": "assamese"
Sarvam models:  sarvam-30b (fast) | sarvam-105b (quality)
```

### 11.4 snake_case → camelCase Conversion Boundary

```
Internal (Python/MongoDB) → Vectorize filter
  subject_id  → subjectId
  chapter_id  → chapterId
  topic_id    → topicId
  source_type → sourceType
  chunk_type  → chunkType
  medium      → medium (unchanged)

Helper: snake_to_vectorize_filter() in source_types.py
Rule: Run this conversion before EVERY Vectorize query. Never send snake_case to CF.
```

---

## 12. Audit Findings

### 12.1 Confirmed Working ✅

| Area | Status | Notes |
|---|---|---|
| source_type frontend → backend | ✅ | `ChatPage.sendMsg` sends `source_type: sourceSection` |
| source_type normalization | ✅ | `ChatRequest.model_validator` calls `normalize_source_type()` |
| Both retrieval paths get same `_card_filters` | ✅ | High + Low confidence paths both use `filters=_card_filters` |
| SubjectCard `notesChs` catch-all | ✅ | Negative match with `QA_TYPES` + `PYQ_TYPES` sets; `important_questions` excluded from Notes |
| Vectorize snake→camelCase | ✅ | `snake_to_vectorize_filter()` runs before every Vectorize call |
| Auth rate limit (no Redis dependency) | ✅ | MongoDB `auth_rate_limit` collection with 90s TTL index |
| Topic embedding warm start | ✅ | 197 topics loaded into memory at startup |
| Edge HMAC replay protection | ✅ | `abs(now - timestamp) > 30s` rejection |
| pyq_pdf_url in admin + backend | ✅ | Added to ChapterUpdate model + AdminContentEditor + ChapterEditForm |
| ChapterPage Ask AI `section` param | ✅ | Fixed: uses `contentMode` (active tab), not `data?.content_type` |
| Vectorize metadata index health-check | ✅ | `GET /admin/rag/vectorize/info` returns `health.status` + missing list |
| Bulk chapter reindex endpoint | ✅ | `POST /admin/rag/reindex/chapters` with `chapter_ids[]`, semaphore, job tracking |

### 12.2 Deployment-Side Items (not code — require operator action) ⚠️

| Item | Risk | Action |
|---|---|---|
| Vectorize metadata indexes existence | HIGH — filtered retrieval silently degrades | Run `GET /admin/rag/vectorize/info` and create any missing indexes with `wrangler vectorize create-metadata-index` |
| `JWT_SECRET` in GCP SM (currently `not_found`) | MEDIUM — falling back to env var | Create `JWT_SECRET` secret in GCP SM; add to `--update-secrets` in cloudbuild.yaml |
| `EDGE_SHARED_SECRET` not in SM | MEDIUM — HMAC trust contract falls back to JWT | Create in GCP SM; add to deploy step |
| Legacy `rag_chunks` collection still active | LOW — retrieval falls through to it; stale data risk | After all chapters reindexed on v2, disable Stage 3 fallback in `retrieval_v2.py` |
| Upstash Redis absent | LOW — gracefully handled, MongoDB fallback active | Keep in optional probe only; do not add to mandatory `--update-secrets` |
| CF_ACCOUNT_ID not set in dev | LOW — Workers AI warmup skipped (GreetingRAG) | Set `CF_ACCOUNT_ID` in dev env or Replit secrets |

### 12.3 Known Architectural Constraints (by design)

| Constraint | Reason |
|---|---|
| Cloud Run `--allow-unauthenticated` | Edge Worker is the only public gateway; HMAC enforces trust |
| `gcloud run deploy` drops secrets every deploy | GCP behavior; `--update-secrets` must be explicit on every deploy step |
| Motor 3.7 `aggregate` is a coroutine | Must double-await: `cursor = await coll.aggregate(); rows = await cursor.to_list()` |
| Gunicorn `timeout=120` | Long AI requests (Sarvam, Vertex) can exceed 30s default |
| RS256→HS256 migration fallback | `_decode_token_with_fallback()` tries HS256 first then RS256 for live tokens |

---

## 13. Operational Runbook

### Check Vectorize index health
```bash
curl -s -H "Cookie: admin_session=..." \
  https://api.syrabit.ai/api/v1/admin/rag/vectorize/info \
  | jq '.health'
```

### Create a missing metadata index
```bash
wrangler vectorize create-metadata-index syrabit-vectors \
  --property-name sourceType --type string
# Repeat for: subjectId, chapterId, topicId, medium, chunkType
```

### Bulk reindex chapters by ID (fix sourceType without editing content)
```bash
curl -X POST https://api.syrabit.ai/api/v1/admin/rag/reindex/chapters \
  -H "Cookie: admin_session=..." \
  -H "Content-Type: application/json" \
  -d '{
    "chapter_ids": ["ch_001", "ch_002", "ch_003"],
    "source_type": "important_questions",
    "parallelism": 3,
    "dry_run": false
  }'
# Poll: GET /admin/rag/jobs/{job_id}
```

### Reindex all chapters for a subject
```bash
curl -X POST https://api.syrabit.ai/api/v1/admin/rag/reindex/subject/{subject_id} \
  -H "Cookie: admin_session=..." \
  -H "Content-Type: application/json" \
  -d '{"source_type": "notes", "parallelism": 3, "dry_run": false}'
```

### Check MongoDB Atlas IP allowlist (Cloud Run dynamic IPs)
Atlas must allow `0.0.0.0/0` or the full GCP IP range. Without it, `init_mongo()` fails silently and all API responses return empty.

### Force-redeploy with secrets (prevents Cloud Run dropping SM refs)
```bash
gcloud run deploy syrabit-backend \
  --image asia-south1-docker.pkg.dev/... \
  --update-secrets MONGODB_URI=mongodb-uri:latest,JWT_SECRET=jwt-secret:latest,...
```

### Verify edge HMAC trust is active
```bash
curl https://api.syrabit.ai/health/full | jq '.edge_trust'
```

### Compile and verify Python dependencies
```bash
./scripts/compile-deps.sh --verify
```

---

## 14. Content Model Reference

### Chapter fields used in retrieval/delivery

| Field | Purpose | Ingested As |
|---|---|---|
| `content_en` / `content_as` | Student-facing text | Fallback if `rag_text_*` empty |
| `rag_text_en` / `rag_text_as` | Retrieval-optimized text | Preferred for ingestion |
| `qa_text_en` / `qa_text_as` | Q&A section student-facing | Q&A tab content |
| `qa_rag_text_en` / `qa_rag_text_as` | Q&A retrieval text | Preferred for Q&A ingestion |
| `content_type` | Section bucket (`notes`/`qa`/`question_paper`/...) | `sourceType` in Vectorize |
| `pyq_pdf_url` | Public PDF URL for Question Papers | Displayed in ChapterPage PYQ tab |
| `rag_indexed_at` | Last reindex timestamp | Admin sync badge |
| `slug` | URL segment | Chapter page routing |
| `has_qa` | Signals Q&A content exists | SubjectPage syllabus Q&A link |

### Admin content editor section routing

```
content_type === 'qa'             → qa_text_en/as fields (not content_en/as)
content_type === 'question_paper' → pyq_pdf_url input, content editor hidden
content_type === 'notes' (default)→ content_en/as editor
```
