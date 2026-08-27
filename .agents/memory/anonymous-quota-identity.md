---
name: Anonymous quota identity
description: Stable identity rule for anonymous chat allowances and history.
---

Anonymous chat quota and its visible credit allowance must resolve from the same
validated browser-generated anonymous ID whenever it is available. Network and
proxy IP data is only a fallback for browsers that cannot provide that ID.

**Why:** IP addresses can change between visits, and different proxy paths can
produce different IP headers for chat versus a credit lookup. Treating an IP as
canonical makes returning students appear to receive a fresh allowance and
causes the displayed balance to diverge from enforcement.

**How to apply:** Any anonymous endpoint that reads or writes a quota, chat
history, or credit balance must accept and consistently resolve the browser's
validated anonymous ID. Keep the edge burst limit as separate abuse protection.
When an IP fallback is unavoidable, trust only Cloudflare's overwritten
`CF-Connecting-IP`; never use caller-controlled forwarding headers as ownership
or limiter identity.