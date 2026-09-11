---
name: Prerender request concurrency
description: Why frontend prerender backend concurrency must be enforced across the entire process.
---

Prerender backend concurrency must use one process-wide request pool. Limiting subject or chapter iteration alone is insufficient when those loops nest and each chapter launches parallel enrichment requests.

**Why:** Nested iteration multiplied a nominally bounded fan-out into large backend bursts, causing request aborts during full release prerenders. Reducing loop concurrency avoided bursts but made strict coverage miss the release deadline.

**How to apply:** Route every backend fetch in the heavy prerender pass through the same pool. Keep rendering concurrency separate, start request timeouts only after a pool slot is acquired, and retain strict subject, chapter, and sitemap coverage checks.