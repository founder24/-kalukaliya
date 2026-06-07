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

## Previously fixed (earlier sessions)
- `/content/chapters/{subject_id}` missing endpoint → added to public_content.py
- CMS library format mismatch → `useContent.jsx` reads `d.items` first
- analytics 404s → POST-only endpoints, GET returns 405 (correct)
- conversation_id/session_id mismatch → fixed
- logout null-token crash → fixed
