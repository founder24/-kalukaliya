---
name: Syrabit RAG v2 pipeline
description: CF Vectorize + MongoDB dual-write ingestion/retrieval; metadata field names, medium encoding, fallback chain.
---

## Architecture

5 new Beanie collections: `rag_documents`, `chunks`, `content_nodes`, `page_assets`, `generation_jobs`.

Ingestion: `ingestion_v2.py` — clean → chunk → embed (bge-m3, 1024-dim) → write to `chunks` (MongoDB) + upsert to CF Vectorize.

Retrieval: `retrieval_v2.py` — 4-path cascade:
1. TopicMatcher in-memory fast path (~45ms)
2. CF Vectorize top-K + MongoDB chunk hydration (~120ms)
3. Atlas $vectorSearch on legacy `rag_chunks` (v1 compat)
4. In-memory cosine on topic_embeddings (pre-ingest fallback)

## Critical: CF Vectorize metadata field names

Vectorize metadata uses **camelCase** because that's what CF expects for metadata index queries:
- `subjectId` (not subject_id)
- `chapterId` (not chapter_id)
- `topicId` (not topic_id)
- `medium` (not language)
- `sourceType` (not source_type)
- `chunkType` (not chunk_type)

**Why:** CF Vectorize metadata filter syntax is strict; field names in the index must match exactly. snake_case fields fail silently (no match, returns nothing).

## Critical: medium encoding

Stored as `'english'` / `'assamese'` (full words), NOT `'en'` / `'as'` (ISO codes).

`lang` parameter in chat_service is still `'en'`/`'as'` — `retrieval_v2._lang_to_medium()` maps it before querying Vectorize.

**Why:** Consistency with the `Chapter` and `RagDocument` models which use full-word medium strings throughout.

## Chunk ID strategy

Chunk `_id` in MongoDB = Vectorize `vector_id` = `{document_id}_c{index:04d}`.

This means delete-by-document-id is trivial: query `chunks` for `document_id`, collect `_id` values, pass to `vectorize_client.delete()`.

## Wrangler setup (manual step for operators)

```bash
wrangler vectorize create syrabit-rag --dimensions=1024 --metric=cosine
# Then create 6 metadata indexes:
wrangler vectorize create-metadata-index syrabit-rag --property-name=subjectId --type=string
wrangler vectorize create-metadata-index syrabit-rag --property-name=chapterId --type=string
wrangler vectorize create-metadata-index syrabit-rag --property-name=topicId --type=string
wrangler vectorize create-metadata-index syrabit-rag --property-name=medium --type=string
wrangler vectorize create-metadata-index syrabit-rag --property-name=sourceType --type=string
wrangler vectorize create-metadata-index syrabit-rag --property-name=chunkType --type=string
```

## Config fields added

`CF_VECTORIZE_INDEX_NAME` (default: "syrabit-rag"), `CF_VECTORIZE_API_TOKEN` (optional), `CF_WORKER_AI_TOKEN` (already existed).
Token priority: CF_VECTORIZE_API_TOKEN → CF_WORKER_AI_TOKEN → CF_API_TOKEN.

## Admin endpoints

All under `/api/v1/admin/rag/`:
- `POST /upload/book|syllabus|pyq|chapter-questions` — create RagDocument + kick background ingest
- `POST /ingest-text` — one-shot text ingest without a RagDocument record
- `POST /reindex/{document_id}` — delete + re-ingest
- `GET /jobs/{job_id}` + `GET /jobs` — poll GenerationJob progress
- `GET /documents` — list RagDocuments
- `GET /stats` — chunk counts + Vectorize index info
- `GET /vectorize/info` — raw CF index metadata
- `GET /content-nodes`, `PATCH /content-nodes/{id}`, `POST /content-nodes/{id}/publish`
