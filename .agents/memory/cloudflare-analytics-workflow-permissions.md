---
name: Cloudflare analytics workflow permissions
description: Permission requirements and diagnosis for the scheduled Cloudflare analytics contract check.
---

Cloudflare GraphQL access is scoped by dataset and field: zone `Analytics:Read` can allow `viewer.zones.firewallEventsAdaptive`, while WAF-signature fields and Rulesets API configuration remain separately permissioned. A deployment token that can discover the zone is not sufficient for detailed firewall analytics.

**Why:** Schema introspection and access to event rows do not imply access to signature details or ruleset configuration. The event description `949110: Inbound Anomaly Score Exceeded` identifies the managed blocking threshold, not which underlying signature contributed the score; the score-rule metadata may contain opaque IDs.

**How to apply:** Keep analytics credentials separate from deployment credentials. Verify each dataset with read-only calls; when exact WAF attribution is needed, obtain the zone WAF read permission or inspect the Security Events dashboard. Do not bypass the threshold before identifying the contributing signatures.