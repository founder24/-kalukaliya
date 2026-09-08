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

Anonymous chat allowance periods are UTC calendar days (`YYYY-MM-DD`), not
calendar months. Credit responses may retain the legacy `monthly_limit` field
for compatibility, but anonymous responses also identify `quota_period:
daily` and expose `daily_limit`.

**Why:** The student UI promises that anonymous messages reset daily. A monthly
enforcement period would silently block students for the rest of the month
while the interface promised a midnight reset.

**How to apply:** Use the same UTC daily period key for anonymous reservation,
usage reads, rollback, and legacy KV-floor migration. Registered-user monthly
account counters remain a separate contract.