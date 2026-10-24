---
name: Cloudflare release provenance
description: Release-build outputs that can dirty the checkout and invalidate Pages provenance checks
---

Cloudflare release builds must write generated curriculum JSON/XML into `dist/`, keep the committed Trustpilot fallback cache read-only during releases, and ignore generated Playwright results. The final clean-source guard must still fail on any unexpected mutation.

**Why:** A successful frontend build previously reached the provenance guard but was rejected because browser status, generated static data, and a build-time review cache could mutate tracked source files. The failure skipped Pages publication even though the artifact itself was valid.

**How to apply:** Keep source-generation steps pointed at release output directories, treat committed fallback data as read-only in CI, and add only genuinely generated artifacts to ignore rules. Do not replace the guard with a blanket restore or allowlist that hides unrelated mutations.

Public Pages build variables are not automatically present in the separate GitHub Actions frontend build; release workflows must explicitly load the Pages production configuration before Vite runs.

**Why:** Manual AdSense placements were configured in Pages but silently compiled disabled when the release workflow built the frontend with only its explicitly declared environment variables.

**How to apply:** Keep Pages as the source of truth for public frontend configuration, load the required values into the release build environment, and fail closed on missing or malformed placement identifiers.

When a release first publishes new topic records through D1, the frontend prerender must be tested against the resulting sitemap before the production sequence starts; otherwise API/D1 can deploy successfully before Pages discovers route or rendered-content gaps.

**Why:** The migration added topic URLs that were absent from earlier checks, and the Pages release then exposed missing Unicode-route coverage and Assamese rendered-page mismatches after the database and Edge Worker had already deployed.

**How to apply:** For content migrations that change sitemap inventory, include realistic post-migration sitemap fixtures in pre-merge frontend checks, covering percent-encoded localized slugs, page titles, substantial content, and canonical parent links.