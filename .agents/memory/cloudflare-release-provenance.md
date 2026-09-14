---
name: Cloudflare release provenance
description: Release-build outputs that can dirty the checkout and invalidate Pages provenance checks
---

Cloudflare release builds must write generated curriculum JSON/XML into `dist/`, keep the committed Trustpilot fallback cache read-only during releases, and ignore generated Playwright results. The final clean-source guard must still fail on any unexpected mutation.

**Why:** A successful frontend build previously reached the provenance guard but was rejected because browser status, generated static data, and a build-time review cache could mutate tracked source files. The failure skipped Pages publication even though the artifact itself was valid.

**How to apply:** Keep source-generation steps pointed at release output directories, treat committed fallback data as read-only in CI, and add only genuinely generated artifacts to ignore rules. Do not replace the guard with a blanket restore or allowlist that hides unrelated mutations.