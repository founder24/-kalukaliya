---
name: Password reset cutover proof
description: Release-safe evidence for reset-email delivery and token single-use behavior.
---

Password-reset delivery evidence must be tied to a request issued after the
release being validated. A pre-supplied reset link is not valid release
evidence because it may have been delivered by an earlier deployment.

**Why:** Reset-request responses are intentionally non-enumerating and do not
report email-provider failures. The nonce must be stored with the issued token
and checked during confirmation; merely echoing it into a URL lets an older
link be edited to appear fresh.

**How to apply:** Request the disposable reset email after deployment with a
new nonce, pause at a protected approval boundary, and accept only a link with
the matching nonce. Send that nonce to confirmation so the server can compare
it with the stored token binding. Consume it once, assert replay rejection,
and verify login with the new password. A new release must use a new nonce and
link.