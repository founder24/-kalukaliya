---
name: Playwright route fixture matching
description: URL glob behavior to preserve when adding stateful browser fixtures.
---

Playwright route patterns using a single `*` only match one URL segment. Register a separate `/**` pattern for nested REST mutations such as `/subjects/:id` and `/chapters/:id`.

**Why:** A list route like `/subjects` can appear correctly mocked while a nested PATCH or DELETE silently falls through to an older catch-all, making the browser test exercise stale state.

**How to apply:** For each stateful fixture, cover both the collection URL and nested resource URL explicitly, then assert the mutation request and the refreshed visible state.

Browser fixtures for data-loading panels should also tolerate duplicate initial reads from React effects or remounts; distinguish the user-triggered refresh from mount-time requests instead of assuming request one is the refresh.

**Why:** A panel can fetch the same state more than once while mounting, and a one-request state queue can serve the post-refresh response too early.

**How to apply:** Keep initial responses stable across duplicate reads, then transition the fixture only after the explicit interaction under test.