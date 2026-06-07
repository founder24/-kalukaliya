---
name: Syrabit pipeline bugs and fixes
description: Documented bugs found and fixed across the backend API audit
---

## Fixed: SEO generators used wrong model (KnowledgeObject vs Chapter)

**Rule:** All SEO generators in `seo.py` must use the Chapter/Subject/Stream/Class/Board hierarchy, NOT KnowledgeObject.

**Why:** Content was migrated from the old KnowledgeObject model to the new Chapter model. KnowledgeObject has 0 published records. seo.py was not updated. Every sitemap, feed, and llms file returned empty/stub content.

**How to apply:**
- `_build_chapter_url_map()` handles the full join chain: `Chapter.subject_id → Subject.stream_id → Stream.class_id → Class.board_id → Board`
- Subject has `stream_id` (NOT board_id or class_id directly)
- Stream has `class_id`; Class has `board_id`
- sitemap-subjects.xml uses the same join but stops at Subject level

## Fixed: sitemap-index.xml was 404

**Rule:** `/sitemap-index.xml` needs its own `@router.get` — the route was registered only as `/sitemap.xml`.

**Why:** robots.txt and CF Pages regex both reference `sitemap-index.xml` but the backend only had `@router.get("/sitemap.xml")`.

**Fix:** Added `@router.get("/sitemap-index.xml")` decorator alongside `/sitemap.xml`.

## Fixed: /user/stats 404

**Rule:** ProfilePage.jsx calls `GET /user/stats` → must exist in users.py.

**Shape:** `{ conversations: int, saved_subjects: 0, total_tokens: int, credits_used: int }`
- `conversations` = count of Chat documents with `user_id == str(user.id)`
- `credits_used` = from `user.credits_used`

## Fixed: /content/chapters/{id}/topic-pyqs 404

**Rule:** ChapterPage.jsx ImportantQuestions component calls this endpoint.

**Shape:** `{ chapter_id, total, pyqs: [...], mark_wise: { "2": [...], "5": [...] } }`
- Derived from `chapter.faq_jsonld` items (has question/answer/marks/year fields)
- Returns empty `{ total: 0, pyqs: [], mark_wise: {} }` when no PYQ data exists (correct — component renders nothing)

## Fixed: /llms.txt 503

**Rule:** CF Pages `_worker.js` proxies `syrabit.ai/llms.txt → backend/llms.txt` (root prefix, no rewrite). Backend had no `/llms.txt` route (only `/llms-full.txt`).

**Fix:** Added `@router.get("/llms.txt")` to seo.py — served at both `/api/v1/seo/llms.txt` and `/llms.txt` (root prefix mount).

## Fixed: feed.json NameError

**Bug:** After rewriting feed_json() to use Chapter model, left `meta.get("keywords", [])` referencing undefined `meta` variable (leftover from KnowledgeObject loop).

**Fix:** `ch.keywords` is `Optional[str]` (comma-separated), not a list. Parse with `kw_str.split(",")`.

## Fixed: Chat RAG returns 0 sources (fullstack test discovery)

**Root causes (3 layers):**

1. **`MATCH_THRESHOLD = 0.65`** in `topic_matcher.py` (was 0.70)  
   Business Studies topic titles are long and verbose (e.g. "Nature of Services: Difference between Services and Goods") — cosine similarity scores sit 0.65-0.70 even for directly relevant queries. Physics short noun-phrase topics score higher. 0.65 catches subject-relevant queries while still filtering off-topic ones.

2. **`SIMILARITY_THRESHOLD = 0.60`** in `chat_service.py` (was 0.70)  
   Vertex Search Standard tier (SnippetSpec) doesn't return a relevance score in the API response. The backend uses a heuristic fallback of 0.65 for results with content_len > 50 chars. This 0.65 was below the 0.70 threshold, filtering out all results.

3. **Timeout too tight for cold GCP**: embedding timeout 0.5s → 2.0s; retrieval timeout 0.8s → 3.0s.  
   On cold Cloud Run instances, the first Vertex AI embedding call + first MongoDB topic load can take 1-2s. The 0.5s guard silently returned None and skipped RAG.

**How to apply:** Any subject with verbose/long topic titles (Business Studies, Social Sciences) will score 0.65-0.70 in topic_matcher. The 0.65 threshold is calibrated for this. Do not raise it back to 0.70 without checking cosine scores for the new subject's topic titles first.

## Fixed: notes_generated never set True in content_generation.py

**Bug:** `chapter.notes_generated` was never set to `True` in the save path after content was generated.

**Fix:** `chapter.notes_generated = True` added in `content_generation.py` before `save()`.

**Impact:** All chapters generated before the fix appeared missing from sitemaps (filtered by `notes_generated=True`). Backfill required for existing published chapters via direct MongoDB update.

## Fixed: GCP sitemap in-process cache multi-instance race

**Rule:** Sitemap cache is in-process dict (not Redis), TTL=10min. Cloud Run can run multiple instances with independent caches. CF load-balances → CF sitemap may show stale count vs GCP direct.

**Why:** After backfilling `notes_generated=True` in MongoDB, new Cloud Run instances pick up the correct sitemap but old instances serve their cached empty version. Self-resolves within 10 min when all instance caches expire.

**Fix:** No code change needed — this is an operational characteristic. If urgent, deploy a new revision (all instances restart with fresh cache).

## Previously fixed (earlier sessions)
- `/content/chapters/{subject_id}` missing endpoint → added to public_content.py
- CMS library format mismatch → `useContent.jsx` reads `d.items` first
- analytics 404s → POST-only endpoints, GET returns 405 (correct)
- conversation_id/session_id mismatch → fixed
- logout null-token crash → fixed
