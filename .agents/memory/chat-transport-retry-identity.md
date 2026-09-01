---
name: Chat transport retry identity
description: Durable rule for retrying interrupted chat streams without duplicate quota charges
---

Use separate identities for a logical chat turn and its network attempts. The
logical request key stays stable across a bounded retry, while each server
attempt keeps its own request ID and failure stage for diagnostics.

**Why:** A cross-origin stream can fail before headers arrive or after the
server has reserved quota. Retrying as a brand-new turn can duplicate the user
message and charge quota twice even though the student asked only once.

**How to apply:** Any automatic or manual transport retry must reuse the
original logical key and UI turn. HTTP quota, authentication, and provider
errors remain separate failure classes and must not be relabeled as network
disconnects.