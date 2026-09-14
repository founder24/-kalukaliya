---
name: AHSEC cleanup preview gate
description: Production preamble cleanup requires a fresh preview matching the exact filters and chapter set.
---

# AHSEC cleanup preview gate

**Rule:** A production `--clean-preambles` run must use a recent preview artifact whose canonical scope matches the current filters and sorted chapter IDs. Normal imports remain independent of this gate.

**Why:** Cleanup rewrites curriculum notes and RAG content. Requiring reviewable evidence before approval prevents an operator from confirming a broad or stale cleanup by mistake.

**How to apply:** Generate the preview with `--clean-preambles --dry-run`; production validation must happen before approval logging and D1 writes. Any compatibility or emergency helper must require an explicit emergency decision rather than silently bypassing the preview workflow.