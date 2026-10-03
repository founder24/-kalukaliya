---
name: Chat advertising scope
description: Route-specific boundary for ad removal in Syrabit.
---

For chat-only ad requests, remove ad placements from `/chat` and leave every other page's ad placements unchanged.

**Why:** The user's requested scope is limited to chat; changing shared ad behavior would affect unrelated pages.

**How to apply:** Use route-specific suppression for `/chat`. Do not change shared ad defaults or placements on other routes.