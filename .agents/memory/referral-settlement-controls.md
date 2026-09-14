---
name: Referral settlement controls
description: Durable rules for weekly referral funding, settlement, and payout evidence
---

Weekly referral rewards must be calculated from mature, verified D1 claims under a frozen tier snapshot and immutable accrual cutoff. The weekly envelope is capped at ₹37,000 by default, but a lower explicitly published funded cap may be used when authoritative evidence supports it. Settlement remains held until quality/fraud review and beneficiary verification are complete; payout records require unique retry keys and payment evidence, while receipts remain private in R2.

**Why:** Referral attribution, qualification, and payment are separate state transitions. Combining them or letting client-visible activity authorize money would permit duplicate, post-pause, low-confidence, or unaudited liabilities.

**How to apply:** Preserve one statement per influencer/week, one claim-to-statement allocation, one payout per statement, immutable pause cutoffs, separate beneficiary review, and audit evidence for every financial transition.

Ad-funded ROI uses a separate aggregate provider-report ledger. Only finalized, fresh AdSense reports can fund rewards; client impression beacons, projections, and disabled networks are diagnostic or zero-value inputs. Missing/stale revenue, invalid-traffic warnings, identity anomalies, enforcement, reserve, exposure, or negative margin must recommend a fail-closed pause.

**Why:** Advertising telemetry describes opportunities and user-visible diagnostics, not settled cash. Treating it as payout evidence would allow projected or manipulated activity to create financial liabilities.

**How to apply:** Reconcile by provider period, currency, gross revenue, adjustments, fees, net revenue, monetized impressions, finalization/freshness metadata, and immutable source evidence before calculating weekly unit economics.