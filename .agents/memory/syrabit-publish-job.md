---
name: Syrabit admin publish job pattern
description: How the async publish pipeline works — job creation, step tracking, retry, and frontend polling
---

## Rule
POST `/admin/content/chapters/{id}/publish` creates a `PublishJob` document (status=pending, 7 steps) then fires `asyncio.create_task(publish_chapter_with_job(...))` — returns `{job_id, status:"queued"}` immediately. Never blocks the HTTP response.

**Steps in order:** `gcs | cloudflare | status_update | pages_rebuild | indexnow | wikidata | embeddings`

GET `/admin/content/publish-jobs/{job_id}` — poll for step status.
POST `/admin/content/publish-jobs/{job_id}/retry` — resets steps and re-fires task; only allowed when status=failed.

## Why
Chapter publish involves GCS writes, Cloudflare prerender, Pages rebuild, IndexNow, Wikidata enrichment, and topic embedding — can take 30s+. Blocking the HTTP response causes timeout. Job pattern lets admin panel poll and show per-step progress.

## How to apply
- `PublishJob` Document in `models/rag.py`; registered in `db/mongo.py` Beanie init
- `publish_chapter_with_job(chapter_id, job_id)` method in `content_publisher.py` — saves step status at each `_run_step()` call
- Frontend: `PublishJobsPanel` (default export of `content-editor/PublishJobTracker.jsx`) polls at 2.5s intervals; shown as fixed bottom-right overlay when `publishJobIds.length > 0`
- `AdminContentEditor.jsx` maintains separate `publishJobIds` state from `trackedJobIds` (RAG jobs); both float independently in bottom-right corner
