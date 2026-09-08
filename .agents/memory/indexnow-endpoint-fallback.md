---
name: IndexNow endpoint fallback
description: Why production IndexNow submission uses a bounded sequence of participating provider endpoints.
---

IndexNow submission from Cloudflare must try a bounded sequence of participating provider endpoints and stop after the first accepted response.

**Why:** A valid key and publicly reachable ownership file were accepted from a build host while the same provider returned HTTP 429 to Cloudflare's shared egress. Another participating endpoint accepted the same Worker request.

**How to apply:** Keep one endpoint as the normal path, fall back only after a network error or non-200/202 response, stop immediately on acceptance, and keep total provider failure best-effort so it cannot fail a release.