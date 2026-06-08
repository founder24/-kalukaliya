---
name: Motor 3+ to_list() requirement
description: Motor 3+ requires explicit length= arg; bare .to_list() raises TypeError at runtime
---

Motor 3.x (the async MongoDB driver used by Beanie) changed the `to_list()` API to require an explicit `length` argument.  A bare `.to_list()` call raises `TypeError: to_list() missing 1 required positional argument: 'length'` at request time — it does not fail at startup.

**Why:** Motor 2.x defaulted to returning all documents; Motor 3.x made the argument mandatory to force callers to be explicit about memory bounds.

**How to apply:**
- Queries with `.limit(n)` before `.to_list()` → use `.to_list(length=n)`
- Queries loading full collections (no limit) → use `.to_list(length=None)`
- The pattern applies across all Beanie find()/find_all()/aggregate() chains throughout the backend
