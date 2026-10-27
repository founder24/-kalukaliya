---
name: Complete Q&A indexing
description: RAG, FAQ schema, and topic pages must not turn unanswered questions into answer-bearing content.
---

Only include a question in answer-oriented RAG or FAQ output when it has a non-empty approved answer or solution. Topic pages already require a complete linked question-and-solution pair; apply the same completeness boundary to the other answer-bearing surfaces.

**Why:** Published curriculum records can contain useful question prompts whose answer fields are blank. Indexing those prompts alone makes retrieval imply that the system has a supported answer and can produce empty or misleading FAQ markup.

**How to apply:** Keep question-only PYQ lists available as question lists, but exclude incomplete entries from answer-oriented RAG and FAQ schema. Reindex the affected scope after the deployed Worker passes the safe Vectorize deletion-batch preflight.
