---
name: AHSEC Q&A pipeline
description: Exercise extraction from PDFs, Q&A generation via Sarvam, chapter activation, meta-commentary cleanup
---

# AHSEC Q&A Pipeline

## Core exercise extraction fix
**Rule:** "Full Book" fallback in `split_into_chapters()` used to set `exercises_text = ""` always. Exercises are at the END of PDFs, past char 12 000. Fix: search the ENTIRE full_text for exercise boundary, take the LAST occurrence.

**Why:** A 160-page book has full_text ~400 000 chars. Exercises start around char 300 000+. The old `full_text[:12000]` slice never contained them.

**How to apply:** Any time exercise extraction returns 0 chars for a Full Book, check if the PDF text is longer than 12 000 chars before the exercise boundary.

## Chapter status
**Rule:** Chapters created by ingestion must use `status="active"`, not `"draft"`. Both the public API and admin panel filter by active/published.

## _EN_EXERCISE_RE coverage
Expanded to 20+ patterns covering NCERT/AHSEC exercise section headers:
- INTEXT QUESTIONS, TERMINAL QUESTIONS, VERY/SHORT/LONG ANSWER
- MULTIPLE CHOICE QUESTIONS, MCQ, FILL IN THE BLANKS, TRUE OR FALSE
- POINTS TO PONDER, ADDITIONAL EXERCISES, NUMERICALS
- _EN_QUESTION_NUM_RE fallback: detects numbered question runs (≥3 consecutive lines starting with "1.", "Q.1") when no header found

## Q&A data format
`qa_rag_sections_en = [{"section": "topic", "question": "...", "answer": "...", "solution": ""}]`
- `section`: topic heading from which question was drawn (can be "")
- Stored via `qa_to_rag_sections(qa_pairs)` where qa_pairs = `[{"question", "answer"}]`
- RAG-indexed via `reindex_chapter(id, scope="qa")` which calls `_flatten_qa_sections()` → embeds as "Q: ...\nA: ..."

## Notes meta-commentary cleanup
Two-layer defence:
1. `_clean_notes_output()` — strips reasoning preamble before first `##`, converts `**Topic N: Name**` → `## Name`, drops meta-commentary lines and meta `##` headings (Draft, Word Count, CRITICAL FORMATTING RULES, Content Analysis, Plan for Notes, etc.)
2. `extract_topics_from_notes()` — `_META_HEADING_RE` filters meta headings from topic list (Draft, Word Count Check, Mental Sandbox, FORMATTING RULES, etc.); does NOT filter on `title.endswith(":")` — legitimate headings use colons

## System prompt lesson
The notes system prompt must NOT use structured sections (numbered rules, "CRITICAL FORMATTING RULES:" headers) because the model echoes those back as `## CRITICAL FORMATTING RULES:` headings in the output. Use plain paragraph instructions instead.

## source_pdf_url field
`Chapter.source_pdf_url` (added to model) is set by `save_chapter_content(source_pdf_url=pdf_url)`. The `ahsec_gen_qa.py` backfill script reads it to re-find the source PDF without re-running full ingestion.

## _log_progress now records chapter_id + pdf_url
`_log_progress(key, "done", chapter_id=str(chapter.id), pdf_url=pdf_url)` — needed for backfill script to look up chapter→PDF mapping from the progress JSONL.

## ahsec_gen_qa.py backfill script
`python3 -m scripts.ahsec_gen_qa [--medium en|as] [--subject NAME] [--force]`
- Re-downloads PDF, extracts exercises, generates Q&A, saves qa_rag_sections_en
- Uses Class 11 EN catalog as priority over Class 12 (same subject name, different year)
- Run AFTER the EN ingestion pass completes

## 0 Q&A for English literature
English Core chapters (Hornbill, An Inspector Calls) consistently return 0 Q&A pairs. The `_QA_SYSTEM_EN` prompt is calibrated for factual/numerical answers; Sarvam returns `[]` for open-ended comprehension questions. Task #126 tracks the fix.

## notes summarise publication page (task #127)
Full Book body_text = full_text[:12000] often covers cover/foreword/acknowledgements, not actual chapter content. Physics ch1 notes describe NCERT publication history, not Units and Measurements. Task #127 tracks the fix.
