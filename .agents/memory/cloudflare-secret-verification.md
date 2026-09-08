---
name: Cloudflare Worker secret verification
description: Reliable post-provision verification of Worker secret names in deployment workflows.
---

After provisioning Worker secrets, verify required names through the Cloudflare Worker settings API and require `secret_text` bindings. Do not treat an immediate empty `wrangler secret list` result in CI as authoritative.

**Why:** A release uploaded required edge secrets successfully, while the following Wrangler CI listing returned no names and falsely blocked deployment. Cloudflare's Worker settings API and a local Wrangler check both confirmed the bindings existed.

**How to apply:** Query the target script's settings with the deployment token and account ID, compare only binding names and types, never values, and fail closed when required `secret_text` names are genuinely absent.