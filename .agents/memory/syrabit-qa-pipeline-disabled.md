---
name: AHSEC Q&A pipeline disabled
description: Q&A generation and published_topics are disabled in ahsec_ingest.py; both fields are managed manually; notes-only ingestion is the current state.
---

# AHSEC Q&A pipeline status and review policy

## Current state and policy
The current AHSEC ingestion path is notes-only; it does not automatically write Q&A pairs or `published_topics`. For new chapters, the user wants question drafts generated from source content and held for staff approval. Generated questions must not be auto-published or marked reviewed. Topics should likewise remain drafts until approved.

**Why:** The earlier manual-authoring policy has been superseded for new chapters by the user's choice to generate drafts for staff review, while retaining a human publication gate.

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
- Keep existing ingestion behavior notes-only until a reviewed draft-generation path is implemented.
- When adding that path, generate source-grounded question drafts and store them as pending for staff review; never publish them automatically.
- Keep the topic publication rule separate: only reviewed, substantial topics may be promoted to public pages.
