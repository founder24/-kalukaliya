---
name: Playwright route fixture matching
description: URL glob behavior to preserve when adding stateful browser fixtures.
---

Playwright route patterns using a single `*` only match one URL segment. Register a separate `/**` pattern for nested REST mutations such as `/subjects/:id` and `/chapters/:id`.

**Why:** A list route like `/subjects` can appear correctly mocked while a nested PATCH or DELETE silently falls through to an older catch-all, making the browser test exercise stale state.

**How to apply:** For each stateful fixture, cover both the collection URL and nested resource URL explicitly, then assert the mutation request and the refreshed visible state.