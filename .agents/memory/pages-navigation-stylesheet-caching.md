---
name: Pages navigation and stylesheet caching
description: Constraints for reliable responsive styles across Cloudflare Pages deployments.
---

Content-hashed assets require network-fresh navigation documents. A cache-first
service-worker navigation response can return old HTML that points at asset
hashes removed by a later deploy. The main Tailwind stylesheet must also remain
an active stylesheet, not a `media="print"` deferred load.

The CDN must also revalidate navigation/HTML routes. A network-first service
worker cannot correct a stale document when its network response is itself
served from an edge `stale-while-revalidate` cache.

Pages `ASSETS.fetch()` can return the SPA fallback document with HTTP 200 for a
missing extensionless route. A crawler worker must not treat every HTML 200 as
a prerender hit. Canonical URLs cannot identify output aliases (for example, an
alias may intentionally canonicalize to its primary route), so each generated
snapshot needs an explicit marker naming the output route.

**Why:** A stale document combined with the deferred stylesheet transform left
responsive utilities unapplied in browser sessions. This created visible
mobile layout footprints and layout shifts despite the compiled CSS itself
being correct. The SPA fallback behavior can also turn a crawler-only missing
route into a soft 200 instead of preserving the backend's true 404.

**How to apply:** Keep navigations network-first with an offline cache fallback
and bump the cache version when correcting a stale-cache incident. Use
`max-age=0, must-revalidate` for HTML/navigation responses while retaining
long-lived caching for content-hashed assets. Do not re-enable the main
stylesheet's print-media/onload deferral unless it is verified across real
browser contexts and deploy transitions. For crawler asset lookups, compare
the snapshot's explicit output-route marker with the requested path before
bypassing backend bot rendering. Keep declared SPA routes eligible for backend
rendering when no snapshot exists; only synthesize a 404 for undeclared paths.

Pages publish completion can precede propagation of the new navigation document and its hashed assets to the public edge. A live smoke check may temporarily see a previous document whose hashes were removed by the deploy; a later fresh request can reference the new asset set and every file can return 200.

**Why:** On 2026-09-30, the immediate Pages smoke failed on stale-asset 404s after deploy, while a later production request returned a new asset set successfully. The early failure prevented unrelated chat-performance checks from running.

**How to apply:** After publishing Pages, re-fetch the production document and verify each referenced content-hashed asset until a bounded deadline. Fail if the latest document still references missing assets at the deadline, and retain the early failures in diagnostics.