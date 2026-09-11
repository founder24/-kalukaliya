---
name: Access bypass rehearsals
description: Distinguishes safe Access policy checks from end-to-end staff warning verification.
---

The reserved TEST-NET `/32` rehearsal validates policy creation, ordinary
traffic isolation, and cleanup, but it cannot produce a matching request or
activate the staff warning. A full warning rehearsal requires the verified
operator egress address as a temporary `/32`, an independent cleanup watchdog,
and a second network check.

**Why:** TEST-NET addresses are intentionally non-routable. Treating that
rehearsal as end-to-end proof leaves the assertion-absent route and browser
banner unverified.

**How to apply:** Run TEST-NET first. For full verification, use leased
disposable staff and Access credentials, capture inactive/active/inactive API
states and browser banner states, then confirm that policies, credentials, and
fixtures are all removed.