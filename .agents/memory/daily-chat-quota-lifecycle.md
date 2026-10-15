---
name: Daily chat quota lifecycle
description: Design constraints for the shared daily allowance across anonymous and signed-in free chat.
---

The free chat allowance is six messages per UTC day for both anonymous and signed-in free identities. Reserve a slot atomically with the idempotent request claim; increment completed usage only when the assistant turn is persisted. Failed or cancelled claims must stop consuming the daily allowance, while paid tiers and staff/admin remain exempt. Preserve the separate six-per-minute burst guard.

**Why:** The daily product allowance must work under concurrency and transport retries without charging failed generations or weakening abuse protection.

**How to apply:** Keep admission, successful-turn persistence, retry identity, and claim cleanup aligned whenever the chat quota or claim schema changes. Apply the D1 migration before deploying code that queries the daily quota table.
