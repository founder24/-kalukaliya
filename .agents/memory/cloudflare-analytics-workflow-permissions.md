---
name: Cloudflare analytics workflow permissions
description: Permission requirements and diagnosis for the scheduled Cloudflare analytics contract check.
---

Cloudflare GraphQL access is scoped by dataset and field: analytics read can allow `viewer.zones.firewallEventsAdaptive` and event metadata (`score_total`, `score_rules`), while `wafRequestSignatureCategories` / `wafRequestSignatureRefs` are separately gated by zone WAF read. Ruleset detail reads separately require `Account Rulesets Read`; do not add write scopes.

**Why:** Schema introspection and access to event rows do not imply access to signature details or ruleset configuration. The event description `949110: Inbound Anomaly Score Exceeded` identifies the managed blocking threshold, not which underlying signature contributed the score; `score_rules` may contain opaque IDs. `firewallEventsAdaptive` also rejects query windows wider than 4w3d.

**How to apply:** Keep audit credentials separate from deployment credentials and restrict them to the target zone plus its account-level ruleset resource. Verify each dataset with read-only calls, filter by the ruleset's UUID rather than the human CRS rule number, and keep queries within 4w3d. Do not bypass the threshold before identifying the contributing signatures.