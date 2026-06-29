# Syrabit — Full-Stack End-to-End Architecture

> **Stack:** React + Vite (Cloudflare Pages) · FastAPI (GCP Cloud Run) · MongoDB Atlas · Cloudflare Edge Worker · Cloudflare Vectorize · GCS · GitHub CI/CD (Cloud Build)

---

## Infrastructure Layer Map

```
Browser / Bot
     │
     ▼
┌─────────────────────────────────────────────┐
│           Cloudflare Edge Worker            │  ← syrabitworker-prod (api.syrabit.ai)
│  JWT verify · Rate limit · CORS · Bot detect│
│  OCR · TTS · ISR cache · HMAC signing       │
└────────────┬────────────────────────────────┘
             │  /api/* proxied + signed
             ▼
┌─────────────────────────────────────────────┐
│          GCP Cloud Run (Backend)            │  ← FastAPI + Gunicorn/Uvicorn
│  Auth · Chat · Content · Admin · RAG        │
│  Secrets via GCP Secret Manager             │
└────────┬─────────────┬───────────────┬──────┘
         │             │               │
         ▼             ▼               ▼
   MongoDB Atlas   GCS Bucket    Cloudflare
   (App data)      (Delivery)    Vectorize (RAG)
```

```
GitHub (founder24/-kalukaliya)
     │  push to main
     ▼
Cloud Build (cloudbuild.yaml)
     │  1. pip-compile deps check
     │  2. Docker build + push to Artifact Registry
     │  3. gcloud run deploy --update-secrets
     │  4. Smoke test /health
     ▼
Cloud Run (live)          Cloudflare Pages (syrabit.ai)
                               ▲
                          CF Pages deploy hook
                          triggered by Publish Job
```

---

## Role of Each Infrastructure Component

### Cloudflare Pages — `syrabit.ai`
- Hosts the compiled React/Vite static build.
- Serves all user-facing routes: `/`, `/library`, `/chat`, `/history`, `/profile`, `/pricing`, `/signup`, `/login`.
- Hosts prerendered SEO HTML pages for bots (injected at build time from GCS JSON).
- Receives rebuild triggers from the backend Publish Job via `CF_PAGES_DEPLOY_HOOK`.
- Has its own `_worker.js` (Pages Worker) for bot-UA detection and static prerender fallback.

### Cloudflare Edge Worker — `api.syrabit.ai`
- **Single entry point** for all `/api/*` traffic between browser and Cloud Run.
- Verifies JWTs (HS256 + RS256 fallback) using Web Crypto API before any request reaches Python.
- Enforces burst rate limits via Cloudflare KV: 30 req/hr anonymous, 500 req/hr authenticated.
- Handles CORS centrally — removes `Origin` before proxying to prevent backend CORS conflicts.
- Adds HMAC `X-Edge-Signature` + timestamp to every proxied request so Cloud Run can verify requests came through the edge.
- Injects Google OIDC Identity Tokens (`Authorization: Bearer`) for Cloud Run IAM authentication.
- Handles **OCR** (`@cf/unum/uform-gen2-qwen-500m`) and **TTS** (`@cf/myshell/melotts`) natively at the edge — binary payloads never hit the Python backend.
- Serves bot-detected requests via ISR: fetches backend-rendered HTML, caches in KV for 1 hour.
- Blocks scanner-bait paths (`.env`, `.git`, `wp-admin`) with a 404 at edge.
- Retries non-streaming backend requests (502/503) once after 3 s to absorb Cloud Run cold starts.
- Serves dynamic `robots.txt` — allows GPTBot/ClaudeBot, blocks CCBot and scrapers.

### GCP Cloud Run — `api.syrabit.ai` (backend origin)
- Runs the FastAPI application under Gunicorn (4 workers, `timeout=120` for AI requests).
- Private service — only reachable through the Edge Worker (Cloud Run IAM + HMAC guard).
- On startup: initialises MongoDB connection, runs migrations, warms topic embeddings (197 topics), fetches secrets from GCP Secret Manager.
- Handles all business logic: auth, chat, content CRUD, admin, RAG, publish jobs, payments, analytics.
- Scales to zero on idle; Edge Worker retry absorbs the cold-start gap.

### MongoDB Atlas
- Primary application database.
- Collections: `users`, `conversations`, `messages`, `chapters`, `subjects`, `boards`, `classes`, `streams`, `rag_chunks`, `publish_jobs`, `generation_jobs`, `auth_rate_limit`, `ai_usage_logs`, `memory_brain`, `quota_usage`, `feedback`, `changelog`.
- Atlas Vector Search on `rag_chunks.embedding` (1024-dim, cosine) for fast-path retrieval.
- TTL index on `auth_rate_limit.expires_at` (90 s window) for brute-force protection.

### Cloudflare Vectorize
- Vector store for RAG retrieval (`syrabit-rag` index, 1024-dim, cosine).
- Metadata stored per vector: `subjectId`, `chapterId`, `topicId`, `medium` (`english`/`assamese`), `sourceType` (`notes`/`qa`/`pyq`).
- Metadata indexes must exist before filtered retrieval can work.
- Populated by the backend Reindex pipeline using CF Workers AI embeddings.

### GCS Bucket — `syrabit-knowledge-base`
- Source of truth for published chapter JSON delivery artifacts.
- Cloudflare Pages build pulls from here to generate static HTML pages.
- Also stores sitemap XML files pushed during Publish Jobs.

---

## All Secrets Reference

### GCP Secret Manager (loaded at backend startup)

| Secret ID | Env Setting | Purpose |
|---|---|---|
| `SARVAM_API_KEY` | `SARVAM_API_KEY` | Sarvam AI Indic LLM (chat + Assamese) |
| `JWT_SECRET` | `JWT_SECRET` | Sign/verify user JWT tokens (HS256) |
| `ADMIN_JWT_SECRET` | `ADMIN_JWT_SECRET` | Isolated signing key for admin JWTs |
| `RESET_TOKEN_SECRET` | `RESET_TOKEN_SECRET` | Password reset token signing |
| `RAZORPAY_KEY_ID` | `RAZORPAY_KEY_ID` | Razorpay public key |
| `RAZORPAY_KEY_SECRET` | `RAZORPAY_KEY_SECRET` | Razorpay private key |
| `RAZORPAY_WEBHOOK_SECRET` | `RAZORPAY_WEBHOOK_SECRET` | Verify Razorpay webhook payloads |
| `RESEND_API_KEY` | `RESEND_API_KEY` | Resend transactional email |
| `POSTHOG_API_KEY` | `POSTHOG_API_KEY` | PostHog product analytics |
| `INDEXNOW_API_KEY` | `INDEXNOW_API_KEY` | IndexNow instant SEO submission |
| `EDGE_SHARED_SECRET` | `EDGE_SHARED_SECRET` | Backend verifies HMAC from Edge Worker |

### Cloud Run Environment Variables (set via `--update-secrets` in cloudbuild.yaml)

| Variable | Purpose |
|---|---|
| `MONGODB_URI` | MongoDB Atlas connection string |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | Inline GCP SA key JSON (grants Secret Manager access) |
| `SENTRY_DSN` | Sentry error tracking |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Bootstrap admin account |
| `TRANSLATE_CRON_SECRET` | Authorize internal translation cron jobs |
| `CF_PAGES_DEPLOY_HOOK` | Trigger Cloudflare Pages rebuild on publish |
| `CF_WORKER_AI_TOKEN` | Workers AI REST API token (embeddings, OCR, TTS) |
| `CF_API_TOKEN` | General Cloudflare API token (KV, Vectorize, Pages) |
| `CLOUDFLARE_KV_NAMESPACE_ID` | KV namespace for edge content cache |
| `GCS_CONTENT_BUCKET` | GCS bucket name for published chapter JSON |

### Non-Secret Config (`.replit` `[userenv.shared]`)

| Key | Value |
|---|---|
| `APP_ENV` | `development` / `production` |
| `MONGODB_DB_NAME` | `syrabit_prod` |
| `JWT_ALGORITHM` | `HS256` |
| `JWT_EXPIRY_MINUTES` | `60` |
| `REFRESH_TOKEN_EXPIRY_DAYS` | `7` |
| `RATE_LIMIT_FREE_TIER` | `30` |
| `VERTEX_GEMINI_MODEL` | `gemini-2.5-flash` |
| `SARVAM_MODEL` | `sarvam-30b` |
| `VERTEX_PROJECT_ID` | `blissful-acumen-495019-t6` |
| `CLOUDFLARE_ACCOUNT_ID` | `d66e40eac539fff1db270fddf384a5ec` |
| `ALLOWED_ORIGINS` | `https://syrabit.ai,...,https://*.replit.app` |

### Cloudflare Edge Worker Secrets (set via Wrangler / CF API)

| Secret | Used For |
|---|---|
| `JWT_SECRET` | Verify user JWTs at edge before proxying |
| `EDGE_SHARED_SECRET` | Sign `X-Edge-Signature` HMAC on proxied requests |
| `BACKEND_URL` | Cloud Run service URL (private origin) |
| `CF_WORKER_AI_TOKEN` | Workers AI for OCR + TTS at edge |
| `GOOGLE_SA_KEY` | Generate OIDC token for Cloud Run IAM auth |

### GitHub Repository Secrets (used by Cloud Build / deploy.yml)

| Secret | Used For |
|---|---|
| `GCP_SA_KEY` | Authenticate Cloud Build to GCP |
| `CLOUDFLARE_API_TOKEN` | Deploy Cloudflare Worker via Wrangler |
| `CLOUDFLARE_ACCOUNT_ID` | Wrangler deploy target |
| All SM secret IDs above | Passed to `gcloud run deploy --update-secrets` |

---

## 1. User Auth Flow

**Entry:** `/login` or `/signup` page on Cloudflare Pages.

```
Browser → POST /api/v1/auth/login
              │
         [Edge Worker]
         - Public path: JWT verify SKIPPED
         - CORS headers applied
         - Proxied to Cloud Run
              │
         [Cloud Run: auth.py]
         - bcrypt verify password
         - Check auth_rate_limit (MongoDB TTL collection, 90 s window)
         - Issue JWT (HS256, JWT_SECRET, 60 min expiry)
         - Issue refresh token (7 days)
         - Return { access_token, refresh_token, user }
              │
         Browser stores tokens in memory / httpOnly cookie
```

**Session check on every protected route:**
```
Browser → GET /api/v1/users/me
         [Edge Worker] verifyJWT → injects X-User-ID header
         [Cloud Run] reads X-User-ID, returns profile
         If 401 → redirect to /login
```

**Admin auth (separate):**
- Admin JWT signed with `ADMIN_JWT_SECRET` (different key from user JWT).
- Admin session verified via cookie on every mount.
- Bearer token fallback allowed only for machine/cron routes (`admin_cron.py`).
- Admin and user auth state never share browser storage.

**Logout:**
```
POST /api/v1/auth/logout
[Cloud Run] - decode token (HS256 → RS256 fallback for live tokens)
            - blacklist token in MongoDB
            - clear refresh token
```

---

## 2. Board → Class → Stream → Subject Navigation

**Data model:** `boards` → `classes` → `streams` → `subjects` → `chapters` (all in MongoDB).

```
GET /api/v1/content/boards
GET /api/v1/content/classes?board_id=
GET /api/v1/content/streams?class_id=
GET /api/v1/content/subjects?class_id=&stream_id=
GET /api/v1/content/chapters/{subject_id}   ← syllabus spine
```

**URL pattern:** `/:board/:class/:stream/:subject/:chapter`  
Deep-linkable, restorable from URL state via React Router.

**Frontend pipeline:**
```
LibraryPage → SubjectPage → ChapterPage
     │              │             │
  Board/Class    Syllabus     Content Card
  selectors      rendered     (3-section tabs)
```

English ↔ Assamese toggle persists in URL `?lang=en|as` across the entire navigation flow.

---

## 3. Library Page Architecture

**Three top-level sections per subject:**

| Section | Content | MongoDB Field |
|---|---|---|
| Notes | Study notes (markdown) | `content_en` / `content_as` |
| Questions & Answers | Q&A pairs | `qa_en` / `qa_as` |
| Question Paper | PYQs | `pyq_text_en` / `pyq_text_as` (RAG) + PDF link |

**Page structure:**
```
Browse Page
├── Section tabs: Notes · Q&A · Question Paper
└── Full Syllabus (all chapters/topics linked)
    └── Each topic → /board/class/subject/chapter?section=notes|qa|pyq
```

**Crawlability:** Bot requests → Edge Worker detects UA → ISR fetch from backend → full semantic HTML cached in KV for 1 hour. Schema.org `Article`, `BreadcrumbList`, `FAQPage`, `Course` injected at build.

---

## 4. Content Card Division

Every chapter card splits into exactly three sections. Only sections with non-empty content render.

```
ContentCard
├── [Notes tab]    ← content_en / content_as
├── [Q&A tab]      ← qa_en / qa_as
└── [Question Paper tab] ← PDF viewer + pyq_text
     │
     └── [Ask AI] button → passes { subject_id, chapter_id, section, lang, source_name }
```

Active section is highlighted. "Ask AI" passes the exact active section context into the chat pipeline.

---

## 5. Full Syllabus Linking

```
Subject Page
└── SyllabusTree component
    └── For each chapter:
        ├── Link → /board/class/subject/chapter           (Notes)
        ├── Link → /board/class/subject/chapter?section=qa (Q&A, only if qa exists)
        └── Link → /board/class/subject/chapter?section=pyq
```

Same syllabus tree reused in:
- Library browse sidebar.
- Chat context panel.
- Admin content editor navigation.
- SEO page `ItemList` schema.

---

## 6. Ask AI Button and Source Card

**Trigger path:**
```
User clicks [Ask AI] on a content card section
     │
Frontend builds SourceCard:
{
  subject_id, subject_name,
  chapter_id, chapter_title,
  section: "notes" | "qa" | "pyq",
  lang: "en" | "as",
  source_name: "Chapter 3 · Notes"
}
     │
Source chip appears above InputBar
     │
User types message → submit
     │
Payload to /api/v1/chat/stream includes source_card
     │
Chat pipeline uses source_card for metadata-filtered retrieval
```

Dismissing the source chip clears the `section` param. InputBar falls back to broad subject-scoped retrieval.

---

## 7. Chat Pipeline (Full End-to-End)

### 7a. Frontend

```
InputBar.jsx
├── Speculative warm-query: while typing → POST /api/v1/chat/warm-query
│   └── Pre-fetches topic embedding + candidate chunks (reduces TTFB)
└── On submit → POST /api/v1/chat/stream (SSE)
    payload: { message, conversation_id, subject_id, response_lang, source_card }
```

### 7b. Edge Worker

```
POST /api/v1/chat/stream
├── verifyJWT: HS256/RS256 → extract sub → inject X-User-ID
├── checkRateLimit: KV sliding window (anonymous: 30/hr, auth: 500/hr)
│   └── Inject X-Rate-Limited-By: edge
├── Apply CORS headers
├── Add X-Edge-Signature (HMAC + timestamp)
├── Add Authorization: Bearer <Google OIDC token>
└── Detect /stream → pass response body directly (no buffering)
```

### 7c. Backend: Chat Service

```
POST /api/v1/chat/stream  [chat.py]
├── Resolve user identity + tier (free/pro)
├── Load or create conversation document
│
├── [Phase 1] Topic Matching (chat_service.py)
│   ├── Embed user query → CF Workers AI bge-m3 (1024-dim)
│   ├── Compare against 197 pre-warmed topic embeddings (in-memory)
│   └── If score ≥ 0.80 → FAST PATH
│       If score 0.65–0.79 → VECTOR PATH
│       If score < 0.65 → WEB SEARCH FALLBACK
│
├── [Phase 2a] FAST PATH (~30 ms)
│   └── Fetch chapter content directly from MongoDB by topic_id
│
├── [Phase 2b] VECTOR PATH
│   ├── Query Cloudflare Vectorize (syrabit-rag, cosine)
│   │   Filters: { subjectId, chapterId, medium, sourceType }
│   │   Returns: top-k chunks with scores
│   └── Parallel: Atlas $vectorSearch on rag_chunks as fallback
│
├── [Phase 2c] WEB SEARCH FALLBACK
│   └── DuckDuckGo search → top 3 results → extract text
│
├── [Phase 3] Emit SourceCard SSE event (before LLM starts)
│   └── Frontend renders source attribution immediately
│
├── [Phase 4] Build System Prompt
│   ├── Curriculum context: 50% weight
│   ├── Web sources: 20% weight
│   ├── LLM knowledge: 30% weight
│   ├── Force response language: en | as
│   └── Inject persona: Syra AI tutor
│
├── [Phase 5] LLM Routing (ai/router.py)
│   ├── Primary: Sarvam AI (sarvam-30b, enable_thinking=True for EN)
│   │   Assamese: enable_thinking=False, extract from reasoning_content
│   └── Fallback: Google Vertex AI (gemini-2.5-flash)
│
└── Stream chunks back via SSE → Edge Worker pass-through → Browser
    Save message + context to MongoDB (conversations / messages collections)
    Increment ai_usage_logs (per-request token spend tracking)
```

---

## 8. History Page

```
GET /api/v1/conversations?page=1&limit=20
     │
[Cloud Run] paginated query on conversations collection
            { user_id, created_at DESC, message_count, last_message_preview }
     │
History page lists conversations
     │
User clicks → GET /api/v1/conversations/{id}/messages
            → Renders full thread with source cards preserved
```

Admin view: `GET /api/v1/admin/conversations` — paginated, searchable, with user metadata.

---

## 9. Profile Page

```
GET  /api/v1/users/me            → load profile
PATCH /api/v1/users/me           → update name, avatar, preferences
PATCH /api/v1/users/me/language  → set preferred language (en | as)
```

Profile preferences (language, board, class) are injected into:
- Chat system prompt personalization.
- Library default filter state.
- Analytics events (PostHog) for segmentation.

---

## 10. Anonymous User Tracking and Limits

```
Anonymous user (no JWT)
     │
[Edge Worker]
├── Rate limit check: KV key = IP hash
│   Window: 1 hour, limit: 30 req
│   → 429 if exceeded (before backend is ever reached)
│
[Cloud Run: auth_rate_limit collection]
├── Monthly soft-limit: 30 chat messages / month (MongoDB counter)
├── On limit reached → SSE error event with { code: "LIMIT_REACHED" }
└── Frontend shows upgrade prompt
```

Anonymous and authenticated usage tracked separately. IP-based bucket never mixed with user_id bucket.

---

## 11. Admin Panel

**Auth:** Admin JWT (ADMIN_JWT_SECRET), cookie-first. Bearer allowed for cron routes only.

**Shell navigation:** URL-driven via `useSearchParams`. Section state restored on reload.

**Sections with loading guards:**

| Section | Endpoint | Loading State |
|---|---|---|
| Dashboard | `/admin/dashboard/stats` | Skeleton cards |
| Conversations | `/admin/conversations` | Error banner + retry |
| Logs | `/admin/logs` | Loading row → empty state |
| Content Editor | `/admin/content/chapters` | Spinner → guard |
| RAG Editor | `/admin/rag/chapters/{id}` | Spinner → guard |
| Publish Jobs | `/admin/content/chapters/{id}/publish` | Job tracker panel |
| Analytics | `/admin/analytics` | Charts skeleton |
| Users | `/admin/users` | Paginated table |
| Settings | `/admin/settings` | Form skeleton |

**Admin actions tracked in `ai_usage_logs` collection** (per-request token spend).

---

## 12. Content Editor vs RAG Editor

```
┌─────────────────────────────┐    ┌─────────────────────────────┐
│      Content Editor         │    │        RAG Editor            │
│  (Library / User view)      │    │   (Chat / Retrieval view)    │
├─────────────────────────────┤    ├─────────────────────────────┤
│ content_en / content_as     │    │ rag_text_en / rag_text_as   │
│ qa_en / qa_as               │    │ rag_qa_en / rag_qa_as       │
│ PDF link (PYQ viewer)       │    │ pyq_text_en / pyq_text_as   │
│ meta_description, title     │    │ chunk metadata              │
├─────────────────────────────┤    ├─────────────────────────────┤
│ SAVE → MongoDB patch        │    │ SAVE → MongoDB patch        │
│ PUBLISH → GCS + CF Pages    │    │ REINDEX → Vectorize         │
│          + IndexNow         │    │          + rag_chunks coll  │
└─────────────────────────────┘    └─────────────────────────────┘
```

**Same metadata model used across both:**
`{ board_id, class_id, stream_id, subject_id, chapter_id, topic_id, medium, source_type }`

---

## 13. Content Editor → Library Publish Pipeline

```
Admin clicks [Publish]
     │
POST /api/v1/admin/content/chapters/{id}/publish
     │
[Cloud Run: content_publisher.py]
     │
PublishJob created in MongoDB (7-step tracker, GET /jobs/{id} for status)
asyncio.create_task → background pipeline:
     │
     ├── Step 1: Validate chapter completeness
     ├── Step 2: GCS write → gs://syrabit-knowledge-base/chapters/{id}.json
     ├── Step 3: Cloudflare prerender trigger → POST edge /api/prerender
     │           Edge Worker fetches + caches HTML in KV (1 hr)
     ├── Step 4: Generate topic embeddings → MongoDB (semantic matching warm cache)
     ├── Step 5: Trigger CF Pages rebuild → POST CF_PAGES_DEPLOY_HOOK
     │           Pages CI pulls from GCS → builds static HTML → deploys
     ├── Step 6: Regenerate sitemap → push to GCS + CF Pages
     └── Step 7: IndexNow → notify Bing/Yandex of updated URLs
     │
Job status: PENDING → RUNNING → COMPLETED | FAILED
Admin sees live job tracker. Retry endpoint available.
```

---

## 14. RAG Editor → Chat Reindex Pipeline

```
Admin edits RAG text + clicks [Reindex]
     │
POST /api/v1/admin/content/chapters/{id}/reindex
     │
[Cloud Run: ingestion_v2.py]
     │
GenerationJob created in MongoDB
asyncio.create_task → background pipeline:
     │
     ├── Step 1: clean_text → normalize Unicode, strip boilerplate
     ├── Step 2: chunk_content → semantic blocks by source_type
     │           (notes → paragraph chunks ~512 tokens)
     │           (qa → question+answer pairs)
     │           (pyq → question groups)
     ├── Step 3: embed_batch_chunked
     │           → POST CF Workers AI /embeddings (bge-m3, 1024-dim)
     │           → Returns float32 vectors
     ├── Step 4: Delete stale vectors
     │           → MongoDB: delete from rag_chunks where chapter_id = X
     │           → Cloudflare Vectorize: delete by stable IDs {doc_id}_c{idx}
     ├── Step 5: Dual write
     │           → MongoDB rag_chunks: { text, metadata, embedding }
     │           → Cloudflare Vectorize: upsert vectors + camelCase metadata
     │               { subjectId, chapterId, topicId, medium, sourceType }
     └── Step 6: Verify metadata indexes exist on Vectorize
                 (required for filtered retrieval)
```

---

## 15. Embeddings and Source Card Model

### Embedding model
- Provider: Cloudflare Workers AI
- Model: `@cf/baai/bge-m3`
- Dimensions: 1024
- Metric: cosine similarity

### Vectorize metadata schema (camelCase — CF requirement)

```json
{
  "subjectId": "s13",
  "chapterId": "uuid-or-legacy-string",
  "topicId": "t42",
  "medium": "english | assamese",
  "sourceType": "notes | qa | pyq",
  "chunkType": "paragraph | qa_pair | question_group"
}
```

> **FlexId rule:** All DB reference fields use FlexId (accepts legacy string IDs like `s13` AND UUID strings AND ObjectIds). Never raw ObjectId-only queries.

### Source card model (attached to every AI request)

```json
{
  "subject_id": "s13",
  "subject_name": "Physics",
  "chapter_id": "ch-uuid",
  "chapter_title": "Units and Measurements",
  "section": "notes | qa | pyq",
  "lang": "en | as",
  "source_name": "Chapter 1 · Notes"
}
```

### Retrieval query with filters

```python
# Vectorize filtered query
results = await vectorize.query(
    vector=query_embedding,
    top_k=8,
    filter={
        "subjectId": { "$eq": subject_id },
        "medium": { "$eq": "english" },
        "sourceType": { "$eq": "notes" }
    },
    return_metadata=True
)
```

> SnippetSpec only — ExtractiveContentSpec causes 400 on Standard tier.

---

## 16. Core Separation Rules

```
SAVE     → persists data to MongoDB only
PUBLISH  → GCS write + CF Pages rebuild + IndexNow (library delivery)
REINDEX  → Vectorize upsert + rag_chunks update (chat retrieval)

Content Editor → user/library facing   (content_en, qa_en, PDF)
RAG Editor     → chat/retrieval facing (rag_text_en, pyq_text_en)

Notes      → per-lesson (chapter-scoped)
Q&A        → per-lesson (chapter-scoped)
Question Paper → distinct path (subject or board scoped)
Syllabus   → common spine across library, chat, SEO, admin
```

---

## CI/CD Pipeline (GitHub → Cloud Build → Cloud Run)

```
git push main
     │
GitHub Actions: .github/workflows/deploy.yml
     │
├── Lint + test (frontend vitest, backend pytest)
├── scripts/compile-deps.sh → pip-compile check
└── Submit to Cloud Build (cloudbuild.yaml)
         │
         ├── Step 1: pip-compile validation
         ├── Step 2: docker build apps/backend/Dockerfile
         ├── Step 3: docker push → Artifact Registry
         ├── Step 4: gcloud run deploy
         │           --image artifact-registry/syrabit-backend
         │           --update-secrets JWT_SECRET=JWT_SECRET:latest,...
         │           (ALL Secret Manager refs must be in every deploy)
         ├── Step 5: Optional secrets probe (Upstash etc) with || true
         └── Step 6: Smoke test GET /health → assert mongodb_initialized: true

Frontend deploy:
     │
CF Pages build triggered by:
a) CF_PAGES_DEPLOY_HOOK (on Publish Job completion)
b) Direct git push to main (Cloudflare Pages GitHub integration)
     │
Build: pnpm build:client (Vite, pulls GCS JSON for prerender)
Deploy: CF Pages CDN → syrabit.ai
```

---

## Feature Priority Checklist

| # | Feature | Backend | Edge | Frontend |
|---|---|---|---|---|
| 1 | User auth + JWT | `auth.py` | JWT middleware | `AuthPage`, `useAuth` |
| 2 | Admin auth (isolated) | `auth.py` + `ADMIN_JWT_SECRET` | Cookie pass-through | `AdminLogin`, `AdminShell` |
| 3 | Board/class/stream/subject nav | `edu.py`, `content.py` | Cached ISR | `LibraryPage`, `SubjectPage` |
| 4 | 3-section content card | `chapters` model | — | `ContentCard`, tabs |
| 5 | Full syllabus linking | `chapters` + syllabus field | — | `SyllabusTree` |
| 6 | Ask AI + source card | `chat.py` source_card param | — | `InputBar`, source chip |
| 7 | Chat pipeline + RAG | `chat_service.py`, `retrieval.py` | JWT+rate+stream pass | `ChatPage`, `InputBar` |
| 8 | Warm query | `chat.py` warm endpoint | Rate limited | `InputBar` speculative |
| 9 | OCR / TTS | — | Workers AI native | `ImageAttach`, `TtsPlayer` |
| 10 | Anonymous limits | `auth_rate_limit` collection | KV burst limit | Limit banner |
| 11 | History + pagination | `conversations.py` | — | `HistoryPage` |
| 12 | Profile + personalization | `users.py` | — | `ProfilePage` |
| 13 | Content editor → Publish | `admin_content.py`, `content_publisher.py` | — | `AdminContentEditor` |
| 14 | RAG editor → Reindex | `admin_rag.py`, `ingestion_v2.py` | — | `AdminRAG` |
| 15 | Publish Job tracker | `PublishJob` model | — | Job status panel |
| 16 | Admin dashboard | `admin_dashboard.py` | — | Skeleton cards |
| 17 | Admin conversations | `admin_conversations.py` | — | Error banner + retry |
| 18 | SEO prerender (bot) | `seo.py`, bot middleware | ISR KV cache | Pages `_worker.js` |
| 19 | IndexNow | `indexnow.py` | — | — |
| 20 | Payments (Razorpay) | `payments.py`, `razorpay` webhook | — | `PricingPage` |

---

## Issue Resolutions (Applied Fixes)

The following critical issues from the audit have been resolved in code:

### 1. Source Type Canonical Enum — FIXED
**File:** `apps/backend/app/services/rag/source_types.py` (new)

Single source of truth for all source type strings across chunker, ingestion, retrieval, and editor.

| Frontend section | Internal `source_type` (MongoDB/Python) | Vectorize `sourceType` (camelCase) |
|---|---|---|
| `notes` | `notes` | `notes` |
| `qa` | `important_questions` | `importantQuestions` |
| `pyq` | `pyq` | `pyq` |
| `definition` | `definition` | `definition` |
| `mcqs` | `mcqs` | `mcqs` |

Use `normalize_source_type(raw)` to convert any input to canonical form.
Use `snake_to_vectorize_filter(filters)` before passing filters to `VectorizeClient.query()`.

### 2. Ingestion Default `source_type` — FIXED
**File:** `apps/backend/app/services/rag/ingestion_v2.py`

Changed default from `"book_pdf"` (not a valid chunker type → silently fell back to semantic strategy with wrong label) to `DEFAULT_SOURCE_TYPE` (`"notes"`). `normalize_source_type()` is now called at ingestion entry so unknown values are caught before chunking.

### 3. Filter Key Format Boundary — DOCUMENTED
**File:** `apps/backend/app/services/rag/retrieval.py`

Explicit contract now in module docstring:
- Atlas `$vectorSearch` on `rag_chunks` → **snake_case** (`source_type`, `subject_id`)
- Cloudflare Vectorize writes → **camelCase** (`sourceType`, `subjectId`)
- Any future Vectorize query path **must** call `snake_to_vectorize_filter()` first

### 4. Secret / Env-Var Alias Table — FIXED
**File:** `apps/backend/app/config.py`

Canonical alias table added at module top. Every secret now has one Python name, one GCP Secret Manager ID, and a documented list of accepted aliases. Runtime alias mapping via `empty_strings_to_none` validator covers:
- `MONGODB_URL` → `MONGODB_URI`
- `CLOUDFLARE_API_TOKEN` → `CF_API_TOKEN` + `CF_WORKER_AI_TOKEN`
- `GOOGLE_SA_KEY` → `GOOGLE_APPLICATION_CREDENTIALS_JSON` (via `google_credentials` property)
- GCP build identity (`GCP_SA_KEY`) explicitly separated from runtime identity (`GOOGLE_SA_KEY`)

### 5. Auth Route Classification — DOCUMENTED
**File:** `apps/edge/src/middleware/jwt.ts`

Four groups now explicitly documented with invariants:
- **Group A PUBLIC:** no JWT anywhere (content, auth endpoints, analytics)
- **Group B OPTIONAL:** JWT verified if present, anonymous allowed (chat, conversations)
- **Group C PROTECTED:** JWT required (users, subscription, feedback)
- **Group D ADMIN:** intentionally in PUBLIC_PATHS — cookie-protected on backend; edge cannot inspect httpOnly cookies. `JWT_SECRET` ≠ `ADMIN_JWT_SECRET` invariant documented.

### 6. Bot Prerender Ownership — FIXED
**File:** `apps/edge/src/routes/isr.ts`

Clear ownership boundary documented:
- **Edge Worker** = primary/authoritative for all routes proxied through `api.syrabit.ai`
- **Pages Worker** (`_worker.js`) = secondary, handles only direct CDN hits that bypass the edge
- Rule: a route cannot be cached by both layers simultaneously

### 7. Retrieval Path Precedence — DOCUMENTED
**File:** `apps/backend/app/services/rag/retrieval.py`

Deterministic 3-tier contract now in module docstring. Same prompt always hits same path given same topic embedding state — no randomness. Fast path (≥0.80) → Vector path → Web search (<0.50).

### Remaining architectural concerns (not code-fixable, require ops action)
- **Vectorize metadata indexes:** must be created via `wrangler vectorize create-metadata-index` before filtered retrieval works. Ingestion now calls `normalize_source_type()` so stored values are always valid.
- **Pages deploy race condition:** pick one canonical trigger — either GitHub integration OR publish-job hook, not both simultaneously.
- **Cloud Run OIDC audience:** verify `BACKEND_URL` in the edge worker matches the Cloud Run service URL exactly (including trailing slash handling) to prevent 401s.
