---
name: Production seed rollout caveat
description: The production release workflow can be blocked before the API Worker by a GCP billing prerequisite.
---

The seed-recovery rollout depends on the API Worker's D1 migration and Worker deployment, but the combined release workflow may stop earlier in Cloud Run's GCP Secret Manager setup when project billing is disabled.

**Why:** A backend credential-sync failure prevented the workflow from reaching the D1 migration and API Worker jobs even though the Worker and D1 could be safely rolled out independently.

**How to apply:** Treat the Cloud Run billing/credential gate as a separate prerequisite. If a task explicitly authorizes the API seed-recovery rollout, run the exact API migration and Worker release commands from `.github/workflows/deploy.yml`, retain the workflow failure evidence, and do not claim the full-stack workflow passed until the GCP gate is repaired.