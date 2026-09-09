---
name: Explicit curriculum scope
description: Trust boundary for resolving student-stated class and subject constraints in chat retrieval.
---

When a student explicitly names a class or subject, resolve that combination to one canonical curriculum subject before any direct-chapter, semantic, authoritative, web, or model path can supply curriculum provenance. Ambiguous, conflicting, or unavailable resolution must ask for clarification rather than broaden the search.

**Why:** Unscoped semantic and authoritative retrieval can return plausible but wrong material from another catalogue, such as a degree-level subject for a Class 11 question. Page context must never override the student's explicit wording.

**How to apply:** Constrain both chapter and subject together, build prompt labels from the resolved hierarchy, and require the chapter, subject, and any stream/class/board ancestors to be public before content or provenance reaches the answer. Future vector reindexes should retain hierarchy metadata as defense in depth.