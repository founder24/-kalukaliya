---
name: Workers rate-limit test clocks
description: Durable Object isolation and clock rules for deterministic rate-limit runtime tests.
---

Give each Workers runtime test invocation a unique Durable Object name prefix, even when the test pool promises per-test storage isolation.

**Why:** Persisted local Durable Object state can survive repeated suite invocations. Also, historical fake timestamps schedule alarms in the past relative to the Durable Object runtime, so storage may be cleared between requests and admission assertions become inconsistent.

**How to apply:** Capture the real clock before enabling fake timers, derive a test window safely in the future, and use that window for admission, near-boundary, alarm, and reset checks. Route explicit alarm helpers through the same invocation-scoped namespace as the worker.