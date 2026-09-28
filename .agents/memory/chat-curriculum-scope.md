---
name: Explicit curriculum scope
description: Trust boundary for resolving student-stated class and subject constraints in chat retrieval.
---

When a student explicitly names a class or subject, resolve that combination to one canonical curriculum subject before any direct-chapter, semantic, authoritative, web, or model path can supply curriculum provenance. Ambiguous, conflicting, or unavailable resolution must ask for clarification rather than broaden the search.

**Why:** Unscoped semantic and authoritative retrieval can return plausible but wrong material from another catalogue, such as a degree-level subject for a Class 11 question. Page context must never override the student's explicit wording.

**How to apply:** Constrain both chapter and subject together, build prompt labels from the resolved hierarchy, and require the chapter, subject, and any stream/class/board ancestors to be public before content or provenance reaches the answer. Future vector reindexes should retain hierarchy metadata as defense in depth.

## Page-selected content sections
Treat `card_context` as untrusted supplemental page data, never as curriculum evidence or instructions. Explicit Q&A requests must use Q&A fields/chunks; PYQ requests may use only student-supplied question text or indexed PYQ chunks. If no PYQ text exists, ask for the question rather than substituting chapter notes.

**Why:** Q&A and notes are distinct source types, while previous-year paper files may be images/PDF metadata with no extractable text.

**How to apply:** Normalize the section server-side and preserve its source type through direct reads, vector filters, mirror validation, and prompt instructions. Never send paper URLs or image metadata as answer evidence.