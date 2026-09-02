---
name: GCP decommission billing block
description: Artifact Registry cleanup behavior after the Cloud Run service is retired and project billing is disabled.
---

Cloud Run service deletion can succeed while project billing is disabled, but Artifact Registry list and delete calls return `BILLING_DISABLED`.

**Why:** This project reached the safe runtime state with billing already disabled. Re-enabling billing solely for cleanup would be a separate account-level decision, even though the retained image repositories cannot serve production traffic after the service is gone.

**How to apply:** Keep billing disabled and production Cloudflare-native. If billing is temporarily enabled later, delete the retired Artifact Registry repositories immediately, verify they are absent, and disable billing again.