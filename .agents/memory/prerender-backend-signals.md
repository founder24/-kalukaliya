---
name: Prerender backend signals
description: Why prerender cache validation must perform a fresh backend schema probe on each cache read.
---

Backend schema signals used to validate a prerender cache entry may share an in-flight HEAD request, but a completed signal must not be reused across later cache reads. The curriculum API must register HEAD explicitly and return the same signal on GET and HEAD.

**Why:** A TTL-cached signal can hide a backend schema change while the prerender process remains alive. FastAPI GET routes do not automatically satisfy HEAD probes, so relying on the GET declaration alone silently degrades validation to fingerprint and TTL checks.

**How to apply:** Preserve a live HEAD comparison whenever a prerender payload cache is read. Coalesce only concurrent probes. Use a stable deployment/source-derived signal and cover changed-signal invalidation, unchanged-signal reuse, and the real route/header contract.