---
name: Atomic quota and refresh state
description: Consistency and rollout rules for quota reservations, refresh rotation, and edge burst limits.
---

Authoritative anonymous/authenticated quota counters and refresh-token claims
must use strongly consistent storage. Edge burst counters must use a
transactional Durable Object rather than KV.

**Why:** KV read-then-write operations lose updates under parallel requests.
Refresh migration also has a rolling-deploy hazard: old isolates only understand
KV revocation markers, while new isolates use D1 claims.

**How to apply:** Keep quota reservations/releases as conditional atomic D1
updates and refresh rotation as a unique D1 claim. Fail closed on storage
outages. During anonymous quota migration, atomically treat the legacy
period-scoped KV count as a floor so existing usage never resets. During refresh
migration, dual-read and dual-write the legacy KV marker until every deployment
has used D1 claims for at least one full refresh-token TTL; only then may the KV
bridges be removed.

For failed chat streams, release the D1 quota reservation before emitting the
terminal SSE error, and refresh the client’s displayed quota after structured
SSE failures and transport failures.

**Why:** A client refresh triggered by the terminal error can race a later
reservation release and leave the visible allowance at zero after a refunded
request.

**How to apply:** Await the idempotent release before writing terminal failure
events; refetch quota on failed or cancelled chat turns.