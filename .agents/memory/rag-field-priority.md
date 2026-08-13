---
name: RAG field priority — notes_en/rag_sections_en
description: Chat retrieval fast-paths must read notes_en and rag_sections_en, not just rag_text_en/content_en
---

## Rule
All retrieval paths (retrieve_context_from_chapter, _fast_path, any future path) must resolve
chapter content using this exact priority order:

  rag_sections_en/as (flattened)  — staff-curated structured [{title, content}] sections
  rag_text_en/as                  — staff plain-text, retrieval-optimised
  notes_en/as                     — AI-generated study notes (AHSEC ingestion writes here)
  content_en/as                   — legacy blob

And cross-lingual fallback: if the requested language (AS) is entirely absent, fall to the EN chain.

**Why:** AHSEC ingestion writes to `notes_en` + `rag_sections_en`. The staff RAG editor writes
to `rag_text_en` + `rag_sections_en`. Neither `rag_text_en` nor `content_en` has data for
most chapters (0 chapters in prod as of Aug 2026). Reading only those fields meant topic matching
succeeded but retrieval silently returned 0 chunks — LLM answered without any chapter context.

**How to apply:** When adding a new retrieval path or refactoring, grep for `content_en` to find
any hard-coded field reads and expand them using the priority chain above.
`bulk_seed_rag_en.py:_get_chapter_text()` is the canonical reference implementation.
