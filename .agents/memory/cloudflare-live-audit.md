---
name: Cloudflare live audit migration drift
description: Production Cloudflare Workers can be healthy while D1 migrations lag behind the deployed Worker code.
---

The Cloudflare API health endpoint is not sufficient release evidence: it can return healthy from a simple D1 connectivity check while chat fails because the live schema is missing columns required by the current Worker. Compare production `d1_migrations` and critical table columns with the release before declaring the stack healthy, and require an authenticated chat smoke test after migration.

**Why:** A live chat request reached the API Worker and failed at quota storage because `chat_request_claims` lacked referral-reservation columns added by a later repository migration; the release workflow had failed before applying pending migrations.

**How to apply:** Treat migration drift as a deployment blocker, apply migrations before serving code that depends on them, and make health/release gates exercise quota, auth, refresh, and one bounded chat stream rather than only `SELECT 1`.