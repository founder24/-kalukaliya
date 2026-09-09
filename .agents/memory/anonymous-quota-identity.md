---
name: Anonymous quota identity
description: Stable identity rule for anonymous chat allowances and history.
---

Anonymous history and quota identity must come from an edge-minted random ID in
an HMAC-signed, HttpOnly cookie. The edge may forward that ID internally, but the
API must accept the forwarded header only after verifying the edge's timestamped
request HMAC. Caller-selected browser IDs are never ownership credentials.

**Why:** A caller can rotate unsigned local-storage IDs to reset quota or claim
another pseudonymous history. Signing a caller-selected ID does not fix the
ownership problem because possession of the ID would still be enough to obtain a
signature.

**How to apply:** Resolve anonymous identity exactly once at the edge and reuse
it for the downstream header, cookie issuance, and identity limiter. Prefer a
valid signed cookie; otherwise mint a new random ID. Direct API calls without a
valid cookie or edge HMAC fall back to trusted connection identity and can never
select a pseudonymous account.

Chat misuse protection is a fixed six-request-per-minute D1 bucket for both
anonymous and authenticated students. It is not a daily or monthly message
allowance. Staff and administrators bypass this product limiter.

**Why:** A long-period allowance blocks legitimate ongoing study. A short fixed
RPM ceiling limits bursts and automation without imposing a recurring message
budget.

**How to apply:** Use the same UTC minute key for reservation, usage reads, and
rollback. Do not seed minute buckets from retired daily KV counters. Keep
lifetime or monthly counters analytics-only, and never disable the composer
persistently when a minute bucket fills. The edge must also enforce both a
signed-identity bucket and a trusted CF-Connecting-IP bucket, with one shared
dimension across English and Assamese so language switching cannot double RPM.