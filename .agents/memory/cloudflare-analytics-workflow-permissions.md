---
name: Cloudflare analytics workflow permissions
description: Permission requirements and diagnosis for the scheduled Cloudflare analytics contract check.
---

Cloudflare GraphQL access is scoped by dataset and field: a token may read account-level Worker invocation and HTTP aggregates yet be denied `viewer.zones.firewallEventsAdaptive` or WAF-signature fields without `com.cloudflare.api.account.zone.analytics.read`. A deployment token that can discover the zone is not sufficient for detailed firewall analytics.

**Why:** Schema introspection and access to some aggregate datasets do not imply access to detailed firewall events. Cloudflare rejected those queries at authorization time, so treat this as credential configuration, not query/schema drift.

**How to apply:** Keep the analytics credential separate from deployment credentials unless the latter explicitly includes zone analytics read. Verify the exact dataset with a read-only GraphQL request; use HTTP aggregate security dimensions for high-level attribution, and request zone analytics read when exact firewall rule IDs are needed.