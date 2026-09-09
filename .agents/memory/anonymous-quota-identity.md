---
name: Anonymous quota identity
description: Stable identity rule for anonymous chat allowances and history.
---

Anonymous chat quota and its visible credit allowance must resolve from the same
validated browser-generated anonymous ID whenever it is available. If browser
storage cannot provide one, use the edge-minted HMAC-signed persistent cookie.
Network IP data is the final fallback only.

**Why:** IP addresses can change between visits, and different proxy paths can
produce different IP headers for chat versus a credit lookup. Treating an IP as
canonical makes returning students appear to receive a fresh allowance and
causes the displayed balance to diverge from enforcement.

**How to apply:** Anonymous quota, history, credit, and edge limiter paths must
resolve identities in this order: validated browser ID, verified signed cookie,
then Cloudflare's overwritten `CF-Connecting-IP`. Credentialed frontend requests
must omit the anonymous header when storage is blocked so the cookie path can
take over. Never use caller-controlled forwarding headers as ownership or
limiter identity.

Chat misuse protection is a fixed six-request-per-minute D1 bucket for both
anonymous and authenticated students. It is not a daily or monthly message
allowance. Staff and administrators bypass this product limiter.

**Why:** A long-period allowance blocks legitimate ongoing study. A short fixed
RPM ceiling limits bursts and automation without imposing a recurring message
budget.

**How to apply:** Use the same UTC minute key for reservation, usage reads, and
rollback. Do not seed minute buckets from retired daily KV counters. Keep
lifetime or monthly counters analytics-only, and never disable the composer
persistently when a minute bucket fills.