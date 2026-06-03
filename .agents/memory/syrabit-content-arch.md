---
name: Syrabit content architecture
description: How content is stored/served, translation model, and bilingual display patterns
---

## Content models
- `Chapter` (legacy, active) — `content_en` / `content_as` fields for markdown. 175 chapters, 32 have `content_en`, 0 have `content_as`. Add `title_as` was added for bilingual library display.
- `KnowledgeObject` — newer model used by `content.py /render/` endpoint. Currently EMPTY in DB. All live content is in `Chapter`.
- `TopicEmbedding` — vector search index for RAG. 271 records (recovered from Pinecone). NOT used for library display.

## Library page data flow
- `GET /api/v1/content/library-bundle` → Board → Class → Stream → Subject → Chapter hierarchy
- Chapter data includes `title`, `title_as`, `has_assamese`, `notes_generated`
- `SubjectCard.jsx` uses `ch.title_as || ch.title` when `isAs` (Assamese mode)

## Translation pipeline (Sarvam AI)
- `ContentTranslator` (translator.py) → targets KnowledgeObject (empty DB, don't use)
- `ChapterTranslator` (chapter_translator.py) → targets Chapter model, translates `content_en → content_as` and `title → title_as`
- Admin trigger: `POST /api/v1/admin/corpus/assamese/backfill`
- Progress: `GET /api/v1/admin/corpus/assamese/progress`
- Coverage stats: `GET /api/v1/health/corpus/assamese`
- Admin UI: Content Hub → "Assamese" tab → AssameseBackfillPanel

**Why:** Content was migrated to Chapter model but KnowledgeObject was never populated. Any new translation work must target Chapter, not KnowledgeObject.
