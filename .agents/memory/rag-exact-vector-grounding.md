---
name: Exact vector grounding
description: Semantic retrieval must ground prompts in the matched passages, not chapter openings.
---

Vector search results must preserve score order and load exact full chunk text
from the D1 mirror by vector ID. Never replace a matched passage with the start
of its chapter.

**Why:** Chapter-head substitution discards the semantic evidence the retriever
selected, producing plausible answers grounded in unrelated introductory text.

**How to apply:** Check whether each vector ID exists before any fallback.
Reject blank, stale, chapter-mismatched, subject-mismatched, or unpublished D1
rows. Use the vector metadata passage only when no D1 mirror row exists and the
claimed chapter independently passes the full published hierarchy and resolved
subject checks.