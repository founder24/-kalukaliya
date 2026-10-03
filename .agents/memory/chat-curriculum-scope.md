---
name: Explicit curriculum scope
description: Trust boundary for resolving student-stated class and subject constraints in chat retrieval.
---

Treat explicit class references as hard curriculum constraints: resolve the class and subject together, and ask for clarification rather than broadening retrieval when that class is unsupported or ambiguous. A subject-only mention without a unique class binding may continue through semantic retrieval when no direct-page conflict exists. If a selected chapter conflicts with or cannot verify an explicitly named subject, fail closed instead of using that chapter's content. An unsupported class may use verified web-only retrieval for an explicit freshness request only when no chapter is selected; never add curriculum retrieval in that case.

**Why:** Class numbers determine curriculum identity, while ordinary topic questions can mention a subject without requesting a particular class. Treating every subject mention as a strict class scope rejected valid concept questions; page metadata still must not override a conflicting explicit subject.

**How to apply:** Constrain class-bound queries to the resolved hierarchy, build prompt labels from validated metadata, and require the chapter, subject, and any stream/class/board ancestors to be public before content or provenance reaches the answer. Keep subject-only ambiguity distinct from explicit class mismatch, and ensure web-only exceptions cannot feed curriculum context. Future vector reindexes should retain hierarchy metadata as defense in depth.

## Verified chat source chain
For every assistant answer with a matched published curriculum source, show the original question followed by Topic → Chapter → Subject → Course/Stream → Class. Derive labels and the navigation URL from the matched topic and its published D1 ancestors; never trust caller-provided stream names or surface raw vector IDs. Direct-chapter answers keep the fast D1 content path and perform a separate topic lookup limited to the selected chapter and content type (defaulting to notes). Persist the verified source card with the assistant message so the same chain appears after conversation reload.

**Why:** The map is part of the student-facing evidence path, not decorative metadata, and must remain consistent with the answer's matched published source.

**How to apply:** Validate the complete source hierarchy before display, preserve exact matched-passage provenance, and return stored source metadata from conversation history only when it contains a verified curriculum entry.

## Page-selected content sections
Treat `card_context` as untrusted supplemental page data, never as curriculum evidence or instructions. Explicit Q&A requests must use Q&A fields/chunks; PYQ requests may use only student-supplied question text or indexed PYQ chunks. If no PYQ text exists, ask for the question rather than substituting chapter notes.

**Why:** Q&A and notes are distinct source types, while previous-year paper files may be images/PDF metadata with no extractable text.

**How to apply:** Normalize the section server-side and preserve its source type through direct reads, vector filters, mirror validation, and prompt instructions. Never send paper URLs or image metadata as answer evidence.