---
name: AHSEC Q&A pipeline disabled
description: Q&A generation and published_topics are disabled in ahsec_ingest.py; both fields are managed manually; notes-only ingestion is the current state.
---

# AHSEC Q&A pipeline disabled

## The rule
`ahsec_ingest.py` no longer generates Q&A pairs or writes `published_topics` during chapter ingestion. Both fields must be populated manually via the admin panel or a separate script.

**Why:** Founder asked to clear all Q&A and remove the AI pipeline so they can write questions manually per chapter. The ingestion now does notes + RAG sections only.

## What was cleared
All 548 chapters had these fields zeroed in one DB update (Aug 2026):
- `qa_rag_sections_en` → `[]`
- `qa_rag_sections_as` → `[]`
- `published_topics` → `[]`
- `qa_rag_updated_at` → `null`

## What is disabled in ahsec_ingest.py
- The `generate_qa_from_notes()` call in the chapter loop (lines ~1986-1996) — replaced with `qa_pairs = []`
- `qa_sections` is always `[]`; `scope="notes"` is passed to `reindex_chapter()`
- In `save_chapter_content()`: the `qa_rag_sections_en/as` write and the `published_topics` merge are commented out

## How to apply
- Any future ingestion run will not touch Q&A or published_topics
- To re-enable: un-comment the `generate_qa_from_notes()` call and restore the `qa_sections`/`published_topics` writes in `save_chapter_content()`
- The `_QA_FROM_NOTES_SYSTEM_EN/AS` prompts and `generate_qa_from_notes()` function are still in the file and work — just not called

## Official question-paper provenance
Subject-level PYQ records hosted by a college digital library are not, by host or label alone, verified AHSEC/ASSEB-issued sources and are not chapter-level Q&A. Do not infer chapter attribution from a subject-level paper.

**Why:** Chapter Questions are manually managed and need an auditable board/year/class source plus a reviewed chapter mapping; otherwise a paper can be misattributed as chapter content.

**How to apply:** Verify the paper itself identifies the board, year, class, and subject. Keep unverified copies at subject level; only import chapter questions after reviewing their chapter mapping.
