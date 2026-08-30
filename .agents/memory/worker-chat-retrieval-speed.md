---
name: Worker chat retrieval speed
description: Direct chapter retrieval and safe semantic fallback rules for the Workers AI chat path.
---

## Rule

When a chat request includes a known chapter, fetch that chapter's D1 content
concurrently with conversation history. If the content is nonblank, use it
directly and skip both query embedding and Vectorize. If it is absent, blank,
or unavailable, fall back to semantic retrieval.

**Why:** The chapter ID is already an authoritative content selection from the
learning page. Embedding and Vectorize add avoidable latency on this common
path. A stale chapter ID must not disable grounding or silently degrade to an
ungrounded answer.

**How to apply:** Preserve subject scope during the fallback, but remove the
failed chapter ID from Vectorize metadata filters so another relevant chapter
can be selected. Keep source-card-before-token SSE ordering, quota rollback,
and numeric-only timing diagnostics intact when changing the chat pipeline.