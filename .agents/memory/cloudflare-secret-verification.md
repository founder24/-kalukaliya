---
name: Cloudflare Worker secret verification
description: Reliable post-provision verification of Worker secret names in deployment workflows.
---

After provisioning Worker secrets, verify required names through the Cloudflare Worker settings API and require `secret_text` bindings. Use bounded retries because both Wrangler and the settings API can briefly return an empty binding list immediately after a successful write.

**Why:** A release uploaded required edge secrets successfully, while the following Wrangler CI listing returned no names and falsely blocked deployment. Cloudflare's Worker settings API and a local Wrangler check both confirmed the bindings existed.

**How to apply:** Query the target script's settings with the deployment token and account ID, compare only binding names and types, never values, and retry briefly before failing closed when required `secret_text` names remain absent.