---
name: Cloudflare-only production boundary
description: Durable architecture rule after removing the local and production Cloud Run dependency.
---

Application API and health traffic must use the Cloudflare edge Worker and the D1-backed API Worker service binding. Missing service bindings fail explicitly; Cloud Run, external backend URLs, Google OIDC, and activation toggles are not valid runtime fallbacks.

MongoDB is not used by the current app; Cloudflare storage is authoritative. Treat Mongo-backed Python quota code as legacy, not as the live quota path.

**Why:** Keeping a dormant fallback allowed local and production behavior to diverge and made releases dependent on GCP billing, Secret Manager, and Cloud Run credentials even after the Worker-native cutover.

**Why:** The user confirmed MongoDB is not in use and Cloudflare storage is used.

**How to apply:** Build and test local UI against the Cloudflare-native API path, keep production releases limited to API Worker, edge Worker, and Pages, and treat any future GCP service deletion as a separate destructive decommissioning task. Legacy MongoDB ingestion scripts, quota code, and workflows are migration-era tooling and must not be treated as current runtime; new ingestion must write D1 and Vectorize directly.