---
name: D1 migration test parser
description: Constraint for adding migrations in the local D1 test harness
---

Local D1 integration fixtures split each migration file on semicolons before removing SQL comment lines. A semicolon inside a comment therefore creates an incomplete SQL fragment and fails the whole migration setup.

**Why:** A consumer-referral migration initially failed before any test ran because its explanatory comment contained a semicolon.

**How to apply:** Keep semicolons out of migration comments, or update the fixture parser before adding such comments.