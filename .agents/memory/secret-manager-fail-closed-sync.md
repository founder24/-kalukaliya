---
name: Secret Manager fail-closed sync
description: Safe secret synchronization when an upstream secret provider is unavailable.
---

When a managed secret source is unavailable, never pipe its output directly
into `wrangler secret put`. Capture it privately, verify it is non-empty, and
otherwise stop the release before changing the Worker secret.

**Why:** A provider failure can produce an empty stdout stream. Wrangler accepts
that stream and creates a secret name with an empty value, making a secret-name
audit pass while authentication or payment verification fails at runtime.

**How to apply:** Treat source availability and Worker secret-name presence as
separate release checks. For cross-platform syncs, use protected temporary
files, avoid printing values, require non-empty payloads, and preserve a known
working secret rather than rotating it from an unverified source.