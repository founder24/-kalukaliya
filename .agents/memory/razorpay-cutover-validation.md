---
name: Razorpay cutover validation
description: Safety rules for release-time Razorpay payment and webhook verification.
---

Payment cutover checks must confirm the deployed key is a Razorpay sandbox key before creating an order or calculating a verification HMAC, and must use a dedicated fixture user rather than a student account.

**Why:** A valid HMAC made with a live secret would otherwise let a release check grant a paid entitlement even when no payment occurred.

**How to apply:** Keep the preflight non-mutating, reject non-`rzp_test_` keys, and isolate all resulting order, payment, and entitlement records to the release fixture identity.

Webhook event claims and entitlement changes must commit together.

**Why:** Claiming a retryable provider event before its D1 writes complete turns a transient failure into a permanently lost subscription update.

**How to apply:** Use one D1 transaction/batch with an unprocessed event marker; perform effects only for that marker, then mark it processed in the same batch.