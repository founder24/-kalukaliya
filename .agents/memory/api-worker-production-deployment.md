---
name: API Worker production deployment
description: Production constraints for deploying and smoke-testing the API Worker.
---

Use the configured `syrabit-api-prod.axomxplain.workers.dev` hostname for
authenticated internal-generation smoke tests; a Cloudflare account ID is not
the `workers.dev` account slug. Direct Wrangler deployment requires Node 22 or
newer with the current Wrangler release.

**Why:** Building a hostname from the Cloudflare account ID produces an
unresolvable address, and the Node 20 workspace runtime is rejected before
Wrangler can deploy.

**How to apply:** Keep CI smoke tests pointed at the configured Worker hostname
and retain Node 22+ when running the API Worker's production deployment flow.