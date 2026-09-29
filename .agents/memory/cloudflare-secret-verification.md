---
name: Cloudflare Worker secret verification
description: Reliable post-provision verification of Worker secret names in deployment workflows.
---

After provisioning Worker secrets, verify required names through the Cloudflare Worker settings API and require `secret_text` bindings. Use bounded retries because both Wrangler and the settings API can briefly return an empty binding list immediately after a successful write.

**Why:** A release uploaded required edge secrets successfully, while the following Wrangler CI listing returned no names and falsely blocked deployment. Cloudflare's Worker settings API and a local Wrangler check both confirmed the bindings existed.

**How to apply:** Query the target script's settings with the deployment token and account ID, compare only binding names and types, never values, and retry briefly before failing closed when required `secret_text` names remain absent.

Cloudflare does not reveal a secret's value after provisioning. When two Workers need the same signing/shared secret, generate it once and provision both from the same short-lived process; if the value is lost, rotate both Workers together.

**Why:** A staging API and edge Worker must validate the same JWT/shared secret, but settings verification exposes only binding names. Independently generated values silently break the trust boundary.

**How to apply:** Keep shared values only in process memory while writing each target secret, never print or persist them, then confirm both secret names exist before testing.

For direct `workers.dev` health probes, Python's default urllib user agent returned Cloudflare edge error 1010 before the Worker ran; a browser-like user agent and Playwright reached the staging gate normally.

**Why:** An edge-level bot block can look like a Worker gate/configuration failure even though the script and secret are healthy.

**How to apply:** Distinguish Cloudflare edge errors from the Worker's expected 401/200 gate responses; use a browser-like probe or Playwright for this staging account.