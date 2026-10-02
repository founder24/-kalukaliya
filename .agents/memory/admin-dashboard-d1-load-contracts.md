---
name: Admin dashboard D1 load contracts
description: Keep automatic dashboard requests distinct from optional manual operator checks.
---

Dashboard request tests should distinguish initial-load data fetches from operator actions triggered by explicit buttons. Initial panels should use verified D1-backed sources or be removed; a manual probe or sync should not be mistaken for an automatic request.

**Why:** During the D1-source audit, existing tests expected SEO probe data to be preloaded even though the probe is intentionally user-triggered. The manual probe and subsequent sync remain separate actions.

**How to apply:** Assert initial D1-backed calls and the absence of unsupported startup calls. In tests for optional actions, click the corresponding UI control before asserting the request. Show an honest idle state before a manual probe rather than an indefinite loading indicator.