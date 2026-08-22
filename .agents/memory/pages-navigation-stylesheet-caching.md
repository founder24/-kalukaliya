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

**Why:** A stale document combined with the deferred stylesheet transform left
responsive utilities unapplied in browser sessions. This created visible
mobile layout footprints and layout shifts despite the compiled CSS itself
being correct.

**How to apply:** Keep navigations network-first with an offline cache fallback
and bump the cache version when correcting a stale-cache incident. Use
`max-age=0, must-revalidate` for HTML/navigation responses while retaining
long-lived caching for content-hashed assets. Do not re-enable the main
stylesheet's print-media/onload deferral unless it is verified across real
browser contexts and deploy transitions.