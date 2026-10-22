---
name: Sitemap prerender authority
description: How published chapter sitemap routes interact with the curriculum bundle and detail API during prerender.
---

Treat the chapter sitemap as the public, indexable route contract. Do not discard a listed path merely because the static curriculum bundle cannot map its subject ID or lacks a matching row. Fetch the route-specific detail API; use static curriculum data only as recovery. If a listed route has neither a usable API response nor a fallback, fail release coverage rather than silently emitting no snapshot.

**Why:** The slim hierarchy and full static bundle can disagree on subject IDs. In a verified build, that mismatch hid valid sitemap routes whose detail endpoints still returned chapter data. Separately, old bundle-order candidates outside the sitemap returned 404 and should not be retried when a current sitemap is available.

**How to apply:** Filter ranked candidates to the current sitemap whenever it is available; use broad bundle-order candidates only for development builds without a sitemap. Compare generated chapter files against the complete set of unique sitemap chapter paths.

The sitemap's chapter eligibility must match the public chapter API: include a chapter when `notes_generated` is true **or** user-facing English/Assamese notes or content are non-empty. Keep the generated URL canonical; do not add alternate crawler paths as duplicate sitemap entries.

**Why:** A crawler-reported Office Management chapter resolved through the public API because notes existed, but its legacy flag was false, so the strict sitemap query omitted its canonical URL.

**How to apply:** Keep sitemap queries aligned with the public API fallback across `notes_en`, `content_en`, `notes_as`, and `content_as`; verify the resulting canonical URL is covered by prerender validation.

For direct Pages requests, validate curriculum-looking subject, chapter, and topic paths against their public resolver before returning the SPA shell. Preserve resolver 404s as real HTTP 404s; use 503 when validation is unavailable, and redirect old chapter slugs only when the resolver confirms a canonical replacement. Do not add redirect aliases to sitemaps.

**Why:** The SPA catch-all can make an unresolved curriculum URL look like a successful page to crawlers even when the client later displays not-found UI.

**How to apply:** Keep the Pages Worker route check aligned with the public content API and the canonical sitemap contract; cover GET and HEAD requests, verified redirects, and resolver failures in Worker tests.

Assamese topic sitemap entries require a translated title, a dedicated translated topic slug, and a definition of at least 40 words; do not fall back to the English topic slug for a localized sitemap entry.

**Why:** A partial translation can otherwise look like a published localized page while reusing English route identity or exposing thin content.

**How to apply:** Enforce the same completeness check in every sitemap producer and show editors the exact encoded `/as/` route before they publish the translation.
