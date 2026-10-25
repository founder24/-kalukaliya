---
name: Syrabit content architecture
description: How content is stored/served, translation model, and bilingual display patterns
---

## Content models
- `Chapter` (legacy source of truth) — `content_en` / `content_as` markdown. 479 total, 242 have `content_en`.
- `KnowledgeObject` — what the `/api/v1/content/render/` endpoint reads. **Populated June 2026**: 242 KOs created via `infra/scripts/migrate_chapters_to_ko.py`. All have `status=published` and pre-rendered HTML for notes/mcqs/summary/definitions/important-questions.
- `TopicEmbedding` — vector search for RAG. 271 records (Degree-level only). NOT used for library display.
- `QuestionPaper` — 2 records in `syrabit_prod` (SEBA Class 10 Science 2024, SEBA Class 10 Math 2024). R2 keys set but **images not yet uploaded to R2** — `syrabit-assets` bucket is missing the actual .jpg files.

## KO slug format
`{board}-{class_level}-{subject}-{chapter}` e.g. `ahsec-hs-1st-year-economics-collection-of-data`

## Library page data flow
- `GET /api/v1/content/library-bundle` → Board → Class → Stream → Subject → Chapter hierarchy
- Chapter data includes `title`, `title_as`, `has_assamese`, `notes_generated`
- `SubjectCard.jsx` uses `ch.title_as || ch.title` when `isAs` (Assamese mode)

## Content gaps (as of June 2026)
- **Physics** (28 ch), **Chemistry** (30 ch), **Mathematics** (29 ch): draft chapters, `content_en` is NULL everywhere — need AI generation
- These subjects show as "DRAFT" status in the library; KOs created only for chapters WITH content_en

## Translation pipeline (Sarvam AI)
- `ChapterTranslator` (chapter_translator.py) → targets Chapter model, translates `content_en → content_as` and `title → title_as`
- Admin trigger: `POST /api/v1/admin/corpus/assamese/backfill`
- Progress: `GET /api/v1/admin/corpus/assamese/progress`
- Admin UI: Content Hub → "Assamese" tab → AssameseBackfillPanel

**Why:** Content was migrated to Chapter model but KnowledgeObject was never populated until June 2026 migration. Render endpoint reads KO, not Chapter — so any new chapter content needs to be written to BOTH Chapter.content_en and a new KnowledgeObject. The migration script at `infra/scripts/migrate_chapters_to_ko.py` is the reference for how to do this.

## Degree profile course selections
Degree course types are represented by `streams.slug` values (`major`, `minor`, `sec`, `vac`, `mdc`, `aec`), not a separate course-type table. The public profile selector groups subjects through `streams → classes → board_id` and includes only published subjects.

**Why:** The D1 schema stores the degree taxonomy in the same hierarchy as school streams; grouping by subject names or omitting the board join can mix catalogues.

**How to apply:** Treat the six slugs as the supported taxonomy, scope every lookup to the requested board, and preserve `{ slug, name, description, icon, subject_count, subjects: [{ id, name }] }` for the profile selector.

## AEC content-card placement
For library content, AEC courses are subject cards under Degree → semester → an existing program stream (such as B.A.). Do not create an AEC stream for these cards. This is distinct from the `aec` stream slug used by the degree profile course-type selector above.

**Why:** The user clarified that AEC is a course within a semester and stream; the profile selector uses a separate course-type taxonomy.

**How to apply:** Place each syllabus AEC card under its correct semester and existing degree stream, while leaving the profile selector taxonomy unchanged.

## Curriculum content rollout sequence
The user will provide source material in chat in this order: first the English and Assamese syllabi together, used to create the subject card and its SEO/GEO/AEO content; next English and Assamese PYQs as paired inputs; then chapter-wise topic PYQs in both languages as paired inputs; finally chapter Notes in English and Assamese as paired inputs. Work chapter-by-chapter and keep language pairs aligned; do not complete all English chapters before beginning Assamese. Treat pasted curriculum as source material to validate and persist to D1 through the approved content/import flow, then verify the stored records. D1 is the durable source of truth for the full syllabus, PYQs, and notes; agent memory stores this workflow only, never the verbatim curriculum. The syllabi must be substantive discoverable pages and primary curriculum grounding for Syrabit chat, not merely subject-card metadata. Keep syllabus, individual PYQ, chapter Notes, and chapter Questions as independently indexable content families. Generate PYQ solutions only after Notes completion, grounded in approved Notes and chapter question data.

**Why:** The user wants to paste bilingual source material in a fixed sequence and have it saved to D1, with the syllabus supporting SEO/GEO/AEO and RAG, and English/Assamese chapter content developed in parallel.

**How to apply:** Preserve the user’s stated order in chat-assisted ingestion and staff upload flows. Validate each pasted language pair, persist it to D1, and confirm the resulting scope before moving to the next family. Keep both syllabus languages aligned and publish substantial source-cited pages from them; use syllabus evidence in RAG. Keep PYQ, topic-PYQ, chapter Notes, and chapter Questions independently publishable and crawlable. Do not generate PYQ solutions until Notes are complete; ground solutions only in approved Notes and chapter Questions. Treat perfect benchmark accuracy as a measured target, not a guarantee for every possible query or search ranking.
