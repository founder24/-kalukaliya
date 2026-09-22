---
name: Cloudflare release convergence
description: Durable release-gate rules for Cloudflare Pages and Worker propagation
---

Post-publish Cloudflare probes should preserve strict HTTP, payload, and Worker-native route assertions while using a bounded retry window for edge propagation. Fixture-dependent authenticated cutover validation should remain explicitly opt-in; an intentional public-only skip must be distinguished from a real failure in the release summary.

**Why:** A freshly deployed Pages/Worker release can briefly expose inconsistent edge state even though the same checks recover moments later. Older static authentication fixtures can also fail independently of the public Cloudflare-native release path.

**How to apply:** Retry the exact public probe instead of weakening it or accepting fallback routes. Keep automatic releases on the public contract unless fresh authenticated fixtures are deliberately supplied, and keep the opt-in authenticated suite strict when enabled.

Hashed JS/CSS must not be emitted in Pages `_headers` Link preload rules. HTML modulepreload links are release-local, but Early Hints can converge separately and replay an older release's asset graph.

**Why:** A live browser audit showed current HTML alongside requests for deleted prior-release bundles; the stale requests matched Cloudflare Early Hints, not the current HTML asset graph.

**How to apply:** Keep the hashed-preload-disabled marker and make the build asset gate reject any `/assets/*` preload Link. Treat the loss of hashed Early Hints as intentional correctness protection, not as a release failure.

Pages deployments invoked from a non-production release branch must pass `--branch=main`; otherwise Wrangler creates a Preview deployment even when the upload succeeds, leaving the custom production domain on the prior artifact.

**Why:** A successful release uploaded the correct Pages Worker but appeared live only as a Preview deployment, so the public domain continued serving the old SPA fallback. An explicit production branch publish corrected the domain.

**How to apply:** After every direct Pages upload, verify the deployment list says `Production` and `main`, then probe the custom domain rather than trusting the upload result alone.