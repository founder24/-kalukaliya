---
name: Worker interruption rehearsals
description: How to safely verify Worker lease recovery when remote development cannot establish trusted TLS.
---

# Worker interruption rehearsals

## Rule

When remote Worker development fails before execution with a native workerd
CA-trust error, use an isolated disposable deployment for interruption
rehearsals instead of production resources.

**Why:** Remote development can fail in this environment before the Worker
starts because workerd does not trust the remote peer certificate. A temporary
deployment still exercises the Cloudflare runtime, cron trigger, Workers AI
binding, and remote D1 without modifying production content.

**How to apply:** Keep the rehearsal isolated from production data and remove
the temporary resources after retaining the evidence.