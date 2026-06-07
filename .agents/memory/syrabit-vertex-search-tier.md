---
name: Syrabit Vertex Search Standard Tier
description: Vertex AI Search engine is STANDARD tier — Enterprise-only features cause 400 errors; correct spec and content extraction approach
---

## Rule
Never use `ExtractiveContentSpec` or `ExtractiveSegmentSpec` in Discovery Engine `SearchRequest`. Both require `SEARCH_TIER_ENTERPRISE`. The engine `syrabit-search-engine` is `SEARCH_TIER_STANDARD`.

**Why:** Using `max_extractive_answer_count=1` caused a 400 error on every warm-up call, meaning RAG search was broken on every backend cold start and restart.

**How to apply:**
- `SearchRequest.ContentSearchSpec` → use only `SnippetSpec(return_snippet=True, max_snippet_count=1)`
- Content extraction order in result parsing: `struct_data["content"]` first (all our docs store content there), then `derived_struct_data.snippets[0].snippet` as fallback
- Never check `derived_struct_data.extractive_answers` or `extractive_segments` — these are Enterprise only and will always be empty on Standard

## Datastore config (as of 2026-06-07)
- Datastore: `syrabit-edu-datastore` — `CONTENT_REQUIRED`, `GENERIC`, `SOLUTION_TYPE_SEARCH`
- Engine: `syrabit-search-engine` — `SEARCH_TIER_STANDARD`, `SEARCH_ADD_ON_LLM`
- Location: `global`
- Serving config: `default_search`
- Documents: structured (`struct_data`) with fields: `title`, `content`, `source_url`, `board`, `class_level`, `subject`, `chapter`, `difficulty`, `language`, `slug`, `chunk_index`, `tier_access`

## Document upload — CONTENT_REQUIRED
The datastore has `CONTENT_REQUIRED` config, so every `discoveryengine_v1.Document` upserted via `UpdateDocumentRequest` **must** include `content=Document.Content(raw_bytes=..., mime_type="text/plain")` in addition to `struct_data`. Omitting it causes `400 To create document without content, content config of data store must be NO_CONTENT`.

**How to apply:**
```python
doc = discoveryengine_v1.Document(
    id=doc_id,
    struct_data=struct_data,
    content=discoveryengine_v1.Document.Content(
        raw_bytes=chunk.encode("utf-8"),
        mime_type="text/plain",
    ),
)
```
Both chapter chunk docs and topic micro-docs need this. The `struct_data["content"]` field is still needed for RAG retrieval (`search_context` reads it back), so set both.

## Verification
After fix: `"Vertex Search warm-up successful"` appears in backend startup logs (was 400 error before).
Upload verification: `vertex_search: uploaded chunks=N topic_docs=M` in pipeline result.
