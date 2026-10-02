---
name: Cloudflare analytics workflow permissions
description: Permission requirements and diagnosis for the scheduled Cloudflare analytics contract check.
---

Cloudflare GraphQL access is scoped by dataset and field: a token may read account-level Worker invocation and HTTP aggregates yet be denied `viewer.zones.firewallEventsAdaptive` or WAF-signature fields without `com.cloudflare.api.account.zone.analytics.read`. Even after basic zone firewall rows are readable, WAF signature fields can remain denied. A deployment token that can discover the zone is not sufficient for detailed firewall analytics.

**Why:** Schema introspection and access to some aggregate datasets do not imply access to detailed firewall events or signature details. The event description `949110: Inbound Anomaly Score Exceeded` identifies the managed blocking threshold, not which underlying signature contributed the score.

**How to apply:** Keep the analytics credential separate from deployment credentials unless the latter explicitly includes zone analytics read. Verify the exact dataset and fields with read-only GraphQL requests; report 949110 as the threshold rule and do not infer a specific request-content trigger without the contributing signature details.