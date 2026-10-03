---
name: Refresh-token bridge guard baselines
description: How to handle fail-closed route hash mismatches in the refresh-token bridge release guard.
---

A mismatch in a protected auth-route hash means the source no longer matches the reviewed baseline; it is not a reason to bypass or weaken the release guard. When an intentional change affects protected D1 behavior, review the exact route diff, prove the behavior with a route-level integration test, then update the full-route hash and bridge-stripped D1 hash together. Keep the refresh route and KV bridge checks intact.

**Why:** Stale baselines can stop every production release, while changing hashes without validating the route can silently weaken refresh-token replay or session-revocation protections.

**How to apply:** For a future protected-route change, establish the security behavior first, run the relevant integration tests, then rebaseline only the reviewed route source. Do not treat passing hash tests alone as evidence that the route is safe.