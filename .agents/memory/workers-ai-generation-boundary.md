---
name: Workers AI generation boundary
description: Rules for the private Cloud Run-to-API-Worker text generation path.
---

All server-side text generation should use the API Worker's authenticated internal
generation route rather than calling model vendors from Cloud Run.

**Why:** Centralizing model selection and the primary-to-fallback retry keeps
generation credentials and behavior inside Cloudflare. The endpoint must fail
closed if its shared secret is absent: treating an empty secret as valid would
expose unmetered model access.

**How to apply:** Keep the same non-empty shared secret in the API Worker and
the Cloud Run environment. When changing generation size limits, update the
Python client/callers and the Worker validation limit together; notes generation
is intentionally capped at 4096 output tokens.