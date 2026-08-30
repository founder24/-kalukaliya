---
name: Prerender canonical counts
description: Why strict frontend release checks compare unique canonical output files rather than raw source-record writes
---

Production content can contain multiple source records that resolve to the same public subject or chapter URL. A successful prerender may therefore write the same canonical output file more than once.

**Why:** Comparing the number of successful source-record writes with files on disk falsely reports missing prerenders even when every unique canonical page exists.

**How to apply:** Track unique generated output paths in release manifests and compare those counts with the hydrated pages on disk. Keep API origins separate from route prefixes; native content endpoints use the versioned `/api/v1/content` path.