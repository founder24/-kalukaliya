---
name: Prerender canonical counts
description: Why strict frontend release checks compare unique canonical output files rather than raw source-record writes
---

Production content can contain multiple source records that resolve to the same public subject or chapter URL. A successful prerender may therefore write the same canonical output file more than once.

**Why:** Comparing the number of successful source-record writes with files on disk falsely reports missing prerenders even when every unique canonical page exists.

**How to apply:** Track unique generated output paths in release manifests and compare those counts with the hydrated pages on disk. Keep API origins separate from route prefixes; native content endpoints use the versioned `/api/v1/content` path.

Topic pages can use the same chapter hydrator while remaining a separate sitemap and manifest category. A raw count of every `data-hydrate="chapter"` file therefore overstates chapter coverage by the number of topic pages.

**Why:** A release verifier counted 670 topic pages as chapters, making the disk total 864 against 194 actual chapter routes and blocking Pages publication.

**How to apply:** Exclude routes with the exact `/topic/` path segment from chapter counts and validate topic coverage against the topic sitemap independently.