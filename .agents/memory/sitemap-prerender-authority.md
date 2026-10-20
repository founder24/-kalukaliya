---
name: Sitemap prerender authority
description: How published chapter sitemap routes interact with the curriculum bundle and detail API during prerender.
---

Treat the chapter sitemap as the public, indexable route contract. Do not discard a listed path merely because the static curriculum bundle cannot map its subject ID or lacks a matching row. Fetch the route-specific detail API; use static curriculum data only as recovery. If a listed route has neither a usable API response nor a fallback, fail release coverage rather than silently emitting no snapshot.

**Why:** The slim hierarchy and full static bundle can disagree on subject IDs. In a verified build, that mismatch hid valid sitemap routes whose detail endpoints still returned chapter data. Separately, old bundle-order candidates outside the sitemap returned 404 and should not be retried when a current sitemap is available.

**How to apply:** Filter ranked candidates to the current sitemap whenever it is available; use broad bundle-order candidates only for development builds without a sitemap. Compare generated chapter files against the complete set of unique sitemap chapter paths.
