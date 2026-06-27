# Admin Panel Upgrade — File-by-File Implementation Roadmap

> **Core rule enforced throughout:** Save changes the data. Publish changes delivery. Reindex changes retrieval.  
> Every UI affordance, backend endpoint, and data model change below is designed to make that rule explicit and impossible to violate silently.

---

## Phase 1 — Correctness First
**Week 1 goal:** Separate content vs. RAG editing in the UI, add stale/fresh sync badges, and auto-trigger reindex when RAG text changes.

---

### 1.1 — Separate Content and RAG Editing in `ChapterEditForm`

**File:** `apps/frontend/src/components/admin/content-editor/ChapterEditForm.jsx`

**What changes:**
- The current dual-mode toggle (Reader / RAG Text) is a single tab control with one save button. Separate it into two **distinct tab panels**, each with its **own Save button** and a clear label:
  - **"Student Content"** tab — edits `content_en` / `content_as`. Save button calls `PATCH /admin/content/chapters/:id` with `{ content_en, content_as }`.
  - **"RAG Retrieval Text"** tab — edits `rag_text_en` / `rag_text_as`. Save button calls the new `PATCH /admin/content/chapters/:id/rag` endpoint (see §1.3).
- Add a static callout banner in the RAG tab: `"This text is used only for AI chat retrieval. Changes here do not affect the student-facing page."`
- After a successful RAG save, optimistically set a local `ragStale: true` flag on the chapter, then fire `POST /admin/rag/reindex/chapter/:id` in the background (see §1.3). Show a toast with a job-poll link.
- Remove any code path that saves both content and RAG text in a single request.

**New local state to add:**
```js
const [ragDirty, setRagDirty] = useState(false);
const [ragSyncState, setRagSyncState] = useState('unknown'); // 'current' | 'stale' | 'indexing' | 'unknown'
```

---

### 1.2 — Add Stale/Fresh Sync Badges to `ChapterList` and `ChapterEditForm`

**File:** `apps/frontend/src/components/admin/content-editor/ChapterList.jsx`

**What changes:**
- Add a `RagSyncBadge` inline component next to each chapter's status pill. It reads a `rag_indexed_at` field from the chapter card response and compares it against `rag_updated_at`:
  - `rag_indexed_at >= rag_updated_at` → green chip **"RAG current"**
  - `rag_indexed_at < rag_updated_at` → amber chip **"RAG stale"**
  - `rag_indexed_at` is null → grey chip **"Not indexed"**
- The badge is also shown at the top of `ChapterEditForm.jsx` inside the RAG tab panel.

**New component to extract:**

**File:** `apps/frontend/src/components/admin/content-editor/RagSyncBadge.jsx` *(new)*

```jsx
// Props: { ragUpdatedAt, ragIndexedAt, isIndexing }
// Returns a single <span> chip with colour and tooltip.
```

---

### 1.3 — Backend: Split Chapter Save and RAG Save Endpoints

**File:** `apps/backend/app/api/v1/admin_content.py`

**What changes:**

**Add new endpoint** `PATCH /admin/content/chapters/{chapter_id}/rag`:
```python
@router.patch("/chapters/{chapter_id}/rag")
async def update_chapter_rag_text(
    chapter_id: str,
    body: ChapterRagUpdateRequest,   # rag_text_en, rag_text_as
    admin=Depends(get_current_admin),
):
    chapter = await get_chapter_or_404(chapter_id)
    chapter.rag_text_en = body.rag_text_en
    chapter.rag_text_as = body.rag_text_as
    chapter.rag_updated_at = datetime.utcnow()   # stamp divergence
    await chapter.save()
    # Enqueue background reindex
    job_id = await rag_ingestor.enqueue_chapter_reindex(chapter_id)
    return {"ok": True, "job_id": job_id, "rag_updated_at": chapter.rag_updated_at}
```

**Modify existing** `PATCH /admin/content/chapters/{chapter_id}`:
- Explicitly **exclude** `rag_text_en` and `rag_text_as` from the patchable fields. If they appear in the body, return `422` with message: `"RAG text must be saved via PATCH /chapters/{id}/rag"`.

**File:** `apps/backend/app/models/content.py`

**What changes in `Chapter` model:**
```python
rag_updated_at: Optional[datetime] = None   # set on every RAG save
rag_indexed_at: Optional[datetime] = None   # set by reindex job on success
```

**File:** `apps/backend/app/api/v1/admin_rag.py`

**Modify** `POST /rag/reindex/chapter/{chapter_id}`:
- After successful reindex, write `chapter.rag_indexed_at = datetime.utcnow()` back to MongoDB before returning.

---

### 1.4 — Expose Sync Timestamps in Chapter Cards API

**File:** `apps/backend/app/api/v1/admin_content.py`

**Modify** `GET /admin/content/subject/:id/chapter-cards` response schema:

Add to the per-chapter card object:
```json
{
  "rag_updated_at": "2026-06-20T10:00:00Z",
  "rag_indexed_at": "2026-06-20T09:55:00Z",
  "published_at":   "2026-06-19T14:00:00Z",
  "content_saved_at": "2026-06-20T09:50:00Z"
}
```

These four timestamps let the frontend independently assess: is the student page stale? is RAG stale? Each is set by its respective operation, never inferred.

**File:** `apps/backend/app/models/content.py`

Add `content_saved_at: Optional[datetime] = None` to `Chapter` and stamp it on every `PATCH /chapters/:id` save.

---

## Phase 2 — Publish and Delivery
**Week 2 goal:** Add per-step publish job tracking, expose it in the admin UI, and make it clear that publish ≠ reindex.

---

### 2.1 — Publish Job State Model

**File:** `apps/backend/app/models/rag.py` *(extend existing)*

Add a new document model `PublishJob`:
```python
class PublishJobStep(BaseModel):
    name: str           # "gcs_upload" | "cf_invalidate" | "search_index" | "indexnow" | "topic_embeddings"
    status: str         # "pending" | "running" | "succeeded" | "failed"
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    error: Optional[str]

class PublishJob(Document):
    chapter_id: FlexId
    triggered_by: str       # admin username
    steps: List[PublishJobStep]
    overall_status: str     # "running" | "succeeded" | "partial" | "failed"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime]

    class Settings:
        name = "publish_jobs"
```

---

### 2.2 — Instrument `content_publisher.py` with Step Tracking

**File:** `apps/backend/app/services/content_publisher.py`

**What changes in `publish_chapter()`:**
- At the top of the function, create and save a `PublishJob` document with all steps in `"pending"` state.
- Wrap each pipeline step (GCS upload, CF KV invalidation, Pages build hook, IndexNow, topic embeddings) in a try/except that updates the matching `PublishJobStep.status` to `"running"` → `"succeeded"` or `"failed"` with `error` message.
- After all steps, set `overall_status` to `"succeeded"` if all passed, `"partial"` if some failed, `"failed"` if the GCS step itself failed.
- Return `{"job_id": str(job.id), "status": job.overall_status}` from `publish_chapter()`.

**Add new function `retry_publish_job(job_id: str)`:**
- Loads the existing `PublishJob`, re-runs only steps where `status == "failed"`, updates those steps in place.

---

### 2.3 — Publish Job API Endpoints

**File:** `apps/backend/app/api/v1/admin_content.py`

**Modify** `POST /admin/content/chapters/{chapter_id}/publish`:
- Return `{ "job_id": "...", "status": "running" }` immediately (publish runs as a background task via `asyncio.create_task` or FastAPI `BackgroundTasks`).
- Add `POST /admin/content/publish-jobs/{job_id}/retry` — calls `retry_publish_job(job_id)`.
- Add `GET /admin/content/publish-jobs/{job_id}` — returns full `PublishJob` document.
- Add `GET /admin/content/chapters/{chapter_id}/publish-history` — last 10 `PublishJob` records for that chapter.

---

### 2.4 — Publish Job Status Panel in Frontend

**File:** `apps/frontend/src/components/admin/content-editor/ChapterEditForm.jsx`

**What changes:**
- After clicking **Publish**, replace the current spinner with a `PublishJobTracker` sub-component.
- Poll `GET /admin/content/publish-jobs/:job_id` every 3 seconds until `overall_status` is terminal.
- Show a collapsible step list: each step renders as a coloured icon (pending/running/succeeded/failed) with `name` and `error` message if failed.
- Show a **Retry failed steps** button if `overall_status === "partial"` or `"failed"`.
- Add a note below the Publish button: `"Publish updates the student page and search index. It does not update AI chat retrieval — use 'Save RAG Text' for that."`

**New component:**

**File:** `apps/frontend/src/components/admin/content-editor/PublishJobTracker.jsx` *(new)*
```jsx
// Props: { jobId, adminToken, onComplete }
// Polls /admin/content/publish-jobs/:jobId, renders step list
```

---

### 2.5 — Source-of-Truth Label in Editor Header

**File:** `apps/frontend/src/components/admin/AdminContentEditor.jsx`

**What changes:**
- Add a static `<InfoBanner>` at the top of the chapter editor panel area with three rows:
  - **MongoDB** — live content store (saved here first)
  - **Cloudflare Vectorize** — retrieval store (updated on RAG reindex)
  - **CDN / GCS / Pages** — publish-time static artifacts

---

## Phase 3 — Admin Workflow and Safety
**Week 3 goal:** Optimistic locking, audit logs, and bulk actions.

---

### 3.1 — Optimistic Locking on Chapter Edits

**File:** `apps/backend/app/models/content.py`

Add to `Chapter`:
```python
version: int = 0
```

**File:** `apps/backend/app/api/v1/admin_content.py`

**Modify** `PATCH /admin/content/chapters/{chapter_id}` and `PATCH /admin/content/chapters/{chapter_id}/rag`:
- Require `version` field in request body.
- Use MongoDB atomic `findOneAndUpdate` with `{ version: <supplied_version> }` as the filter. If no document matches, return `409 Conflict` with `{ "error": "version_conflict", "current_version": <db_version> }`.
- Increment `version` on every successful save.

**File:** `apps/frontend/src/components/admin/content-editor/ChapterEditForm.jsx`

**What changes:**
- Track `version` in `contentForm` state (loaded from chapter fetch).
- On `409` response: show a modal — `"Another admin saved this chapter while you were editing. Reload to see their changes, or force-save to overwrite."` with **Reload** and **Force Save** buttons.
- Force-save fetches fresh `version` first, then re-submits.

---

### 3.2 — Content-Edit Audit Log

**File:** `apps/backend/app/models/audit.py` *(extend existing)*

Add a new document `ContentAuditLog`:
```python
class ContentAuditLog(Document):
    chapter_id: FlexId
    admin_username: str
    action: str   # "content_save" | "rag_save" | "publish" | "reindex" | "topic_add" | "topic_edit" | "generate_notes" | "status_change"
    changed_fields: List[str]   # e.g. ["content_en", "rag_text_as"]
    before_snapshot: Optional[dict]   # only for destructive actions
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    ip_address: Optional[str]

    class Settings:
        name = "content_audit_logs"
        indexes = [
            IndexModel([("chapter_id", 1), ("timestamp", -1)]),
            IndexModel([("admin_username", 1)]),
        ]
```

**File:** `apps/backend/app/api/v1/admin_content.py`

Add a shared helper `_log_content_action(chapter_id, admin, action, changed_fields)` and call it in:
- `PATCH /chapters/:id` (action: `"content_save"`)
- `PATCH /chapters/:id/rag` (action: `"rag_save"`)
- `POST /chapters/:id/publish` (action: `"publish"`)
- `POST /chapters/:id/generate-notes` (action: `"generate_notes"`)
- `POST /content/bulk-status` (action: `"status_change"`)

**New endpoint:**
- `GET /admin/content/chapters/{chapter_id}/audit-log` — returns last 50 `ContentAuditLog` entries for a chapter, paginated with `?before=<timestamp>`.

---

### 3.3 — Audit Log Panel in Chapter Editor

**File:** `apps/frontend/src/components/admin/content-editor/ChapterEditForm.jsx`

**What changes:**
- Add a **History** tab (third tab, after Student Content and RAG Text) that lazily loads and renders the audit log via `GET /admin/content/chapters/:id/audit-log`.
- Each entry shows: timestamp, admin name, action label, changed fields.

---

### 3.4 — Bulk Reindex and Bulk Publish Actions

**File:** `apps/backend/app/api/v1/admin_rag.py`

Add `POST /admin/rag/bulk-reindex`:
```python
class BulkReindexRequest(BaseModel):
    chapter_ids: List[str]   # max 50

@router.post("/bulk-reindex")
async def bulk_reindex(body: BulkReindexRequest, admin=Depends(get_current_admin)):
    job_ids = []
    for cid in body.chapter_ids[:50]:
        job_id = await rag_ingestor.enqueue_chapter_reindex(cid)
        job_ids.append({"chapter_id": cid, "job_id": job_id})
    return {"queued": job_ids}
```

**File:** `apps/backend/app/api/v1/admin_content.py`

Add `POST /admin/content/bulk-publish`:
```python
class BulkPublishRequest(BaseModel):
    chapter_ids: List[str]   # max 20

@router.post("/bulk-publish")
async def bulk_publish(body: BulkPublishRequest, bg: BackgroundTasks, admin=Depends(get_current_admin)):
    for cid in body.chapter_ids[:20]:
        bg.add_task(content_publisher_service.publish_chapter, cid)
    return {"queued": len(body.chapter_ids)}
```

**File:** `apps/frontend/src/components/admin/AdminContentEditor.jsx`

**What changes:**
- The existing bulk-action toolbar (used for `bulk-status`) already accepts a `selectedChapterIds` set.
- Add two new bulk action buttons to that toolbar:
  - **"Bulk Reindex RAG"** — calls `POST /admin/rag/bulk-reindex` with selected IDs, then shows a toast per job.
  - **"Bulk Publish"** — calls `POST /admin/content/bulk-publish` with selected IDs, shows a queued count toast.
- Disable both buttons when selection is empty.

---

## Phase 4 — User-Facing Consistency
**Week 4, part A goal:** Library visibility tied to content state, chat/history alignment, pagination.

---

### 4.1 — Draft Guard on Library API

**File:** `apps/backend/app/api/v1/public_content.py`

**Modify** the chapter list / subject chapter endpoints:
- Add a `status != "draft"` filter to every public query that fetches chapters. This already likely exists, but audit every query path and add a `status: "published"` enforcement guard.
- If `published_at` is set but `content_saved_at > published_at`, add a `stale_publish: true` flag to the response (indicates an unpublished edit exists). The public page should ignore this, but it can be surfaced in the admin library view.

---

### 4.2 — Admin Library Shows Pending-Publish State

**File:** `apps/frontend/src/components/admin/AdminContentEditor.jsx`

**What changes:**
- In `ChapterList`, add a `PublishStaleBadge` alongside the existing `RagSyncBadge`. Condition: `content_saved_at > published_at`.
- Shows amber chip: **"Unpublished edits"** with tooltip: `"Content was saved after the last publish. Run Publish to deliver to students."`

---

### 4.3 — Paginate Conversation Review

**File:** `apps/frontend/src/components/admin/AdminConversations.jsx`

**What changes:**
- Replace any `GET /admin/conversations` call that fetches all records with a paginated call: `GET /admin/conversations?page=1&limit=25`.
- Add an infinite-scroll or page-number control below the conversation list.

**File:** `apps/backend/app/api/v1/admin_conversations.py`

**Modify** the conversations list endpoint:
- Accept `?page=1&limit=25` query params (default limit 25, max 100).
- Return `{ "items": [...], "total": N, "page": 1, "pages": M }`.

---

### 4.4 — Paginate Admin Logs Explorer

**File:** `apps/frontend/src/components/admin/AdminLogsExplorer.jsx`

**What changes:**
- Apply the same pagination pattern: `?page=1&limit=50`, add page controls, cap at 100 per page.

**File:** `apps/backend/app/api/v1/admin_analytics.py` *(or whichever endpoint the logs explorer calls)*

- Add `page`/`limit` query params and return paginated wrapper.

---

## Phase 5 — Security and Ops
**Week 4, part B goal:** Admin session hardening, cron route semantics, admin action analytics.

---

### 5.1 — Minimise Bearer Fallback, Harden Admin Session

**File:** `apps/backend/app/api/v1/admin.py`

**What changes:**
- Audit `get_current_admin` dependency: ensure the Bearer token path is only enabled for machine-triggered routes (cron, webhooks). All browser-facing admin routes must use the cookie-based session.
- Add a `require_cookie_session` variant dependency that raises `403` if only a Bearer token is present. Apply it to all `admin_content`, `admin_rag`, `admin_users`, `admin_settings` routers.
- Strengthen logout: ensure `POST /admin/auth/logout` writes the token to the MongoDB `token_blacklist` collection (already exists per memory) even if the primary session store fails.

**File:** `apps/frontend/src/pages/AdminPage.jsx`

**What changes:**
- Shorten `adminVerify()` re-check interval from 12 hours to **2 hours**.
- On `401` response from any admin API call, immediately call `adminLogout()` and redirect to `/admin/login` — add a global Axios response interceptor to catch this rather than handling per-component.

---

### 5.2 — Move Side-Effectful Cron Routes off GET

**File:** `apps/backend/app/api/v1/admin_cron.py`

**What changes:**
- Any route defined as `@router.get(...)` that has side effects (triggers reindex, re-renders, cache busting, sitemap regeneration) must be converted to `@router.post(...)`.
- Protect each with the existing `verify_cron_secret` dependency (Bearer token check). Confirm the secret is distinct from the admin user JWT.
- Update any Cloudflare cron triggers or GCP Cloud Scheduler jobs that call these routes to send `POST` requests.

---

### 5.3 — Admin Action Analytics

**File:** `apps/backend/app/models/ai_usage_log.py` *(extend existing or create sibling)*

Add `AdminActionLog` document:
```python
class AdminActionLog(Document):
    admin_username: str
    action: str   # "publish" | "reindex" | "generate_notes" | "bulk_publish" | "bulk_reindex" | "rag_save" | "content_save"
    chapter_id: Optional[FlexId]
    subject_id: Optional[FlexId]
    payload_size_bytes: Optional[int]
    duration_ms: Optional[int]
    success: bool
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "admin_action_logs"
        indexes = [IndexModel([("timestamp", -1)]), IndexModel([("action", 1)])]
```

**File:** `apps/backend/app/api/v1/admin_content.py` and `apps/backend/app/api/v1/admin_rag.py`

- Add a shared `_track_admin_action(action, admin, **kwargs)` async helper that creates an `AdminActionLog` in the background (fire-and-forget, must not block the response).
- Call it in: `publish_chapter`, `PATCH /rag`, `POST /rag/reindex/chapter/:id`, `POST /generate-notes`, all bulk endpoints.

**File:** `apps/frontend/src/components/admin/AdminAnalytics.jsx`

**What changes:**
- Add a new **"Admin Actions"** tab (or accordion section) that hits a new endpoint `GET /admin/analytics/admin-actions?days=7` and renders a bar chart by action type (publish, reindex, generate-notes, etc.) using the existing chart library already in the file.

**New endpoint:**

**File:** `apps/backend/app/api/v1/admin_analytics.py`

```python
@router.get("/admin-actions")
async def admin_action_stats(days: int = 7, admin=Depends(get_current_admin)):
    since = datetime.utcnow() - timedelta(days=days)
    pipeline = [
        {"$match": {"timestamp": {"$gte": since}}},
        {"$group": {"_id": "$action", "count": {"$sum": 1}, "failures": {"$sum": {"$cond": [{"$eq": ["$success", False]}, 1, 0]}}}},
        {"$sort": {"count": -1}}
    ]
    results = await AdminActionLog.aggregate(pipeline).to_list(length=None)
    return {"days": days, "actions": results}
```

---

## Dependency and Sequencing Map

```
Phase 1 must be done before Phase 2 (rag_updated_at / rag_indexed_at timestamps are read by Phase 2 badges)
Phase 2 must be done before Phase 4.2 (publish history needed for library stale badge)
Phase 3.1 (optimistic locking) is independent — can run in parallel with Phase 2
Phase 3.2 (audit log) depends on Phase 1 endpoints existing (actions to audit)
Phase 3.4 (bulk actions) depends on Phase 1 RAG endpoint and Phase 2 publish endpoint
Phase 5 is independent — can start any time after Phase 1 routes exist
```

---

## New Files Summary

| File | Purpose |
|---|---|
| `apps/frontend/src/components/admin/content-editor/RagSyncBadge.jsx` | Chip showing RAG current / stale / not-indexed |
| `apps/frontend/src/components/admin/content-editor/PublishJobTracker.jsx` | Polls publish job, renders per-step status |
| `apps/backend/app/models/rag.py` (extend) | Add `PublishJob` and `PublishJobStep` models |
| `apps/backend/app/models/audit.py` (extend) | Add `ContentAuditLog` model |
| `apps/backend/app/models/ai_usage_log.py` (extend) | Add `AdminActionLog` model |

## Modified Files Summary

| File | Key Changes |
|---|---|
| `apps/backend/app/models/content.py` | Add `rag_updated_at`, `rag_indexed_at`, `content_saved_at`, `version` to `Chapter` |
| `apps/backend/app/api/v1/admin_content.py` | New RAG endpoint, publish job endpoints, bulk publish, optimistic locking, audit calls, analytics endpoint |
| `apps/backend/app/api/v1/admin_rag.py` | Stamp `rag_indexed_at` after reindex, bulk-reindex endpoint |
| `apps/backend/app/api/v1/admin_conversations.py` | Add pagination |
| `apps/backend/app/api/v1/admin_analytics.py` | Add `GET /admin-actions` aggregation |
| `apps/backend/app/api/v1/admin_cron.py` | Convert side-effectful GETs to POSTs |
| `apps/backend/app/api/v1/admin.py` | `require_cookie_session` dependency, logout hardening |
| `apps/backend/app/services/content_publisher.py` | Per-step `PublishJob` tracking, `retry_publish_job()` |
| `apps/frontend/src/components/admin/content-editor/ChapterEditForm.jsx` | Split save buttons, RAG tab, publish tracker, history tab, version conflict modal |
| `apps/frontend/src/components/admin/content-editor/ChapterList.jsx` | `RagSyncBadge`, `PublishStaleBadge` |
| `apps/frontend/src/components/admin/AdminContentEditor.jsx` | Source-of-truth banner, bulk reindex/publish buttons |
| `apps/frontend/src/components/admin/AdminConversations.jsx` | Pagination |
| `apps/frontend/src/components/admin/AdminLogsExplorer.jsx` | Pagination |
| `apps/frontend/src/components/admin/AdminAnalytics.jsx` | Admin Actions tab |
| `apps/frontend/src/pages/AdminPage.jsx` | 2hr verify interval, global 401 interceptor |
