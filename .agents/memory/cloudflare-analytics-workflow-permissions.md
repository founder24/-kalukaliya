---
name: Cloudflare analytics workflow permissions
description: Permission requirements and diagnosis for the scheduled Cloudflare analytics contract check.
---

The Cloudflare analytics GraphQL query can be schema-valid while still failing when the token lacks `com.cloudflare.api.account.zone.analytics.read`. A deployment token that can discover the zone is not sufficient for `viewer.zones` analytics data; configure a dedicated analytics token or grant the exact zone analytics read permission.

**Why:** A live token successfully resolved the `syrabit.ai` zone and returned HTTP 200 from GraphQL, but Cloudflare rejected the query at authorization time. Treat this as credential configuration, not query/schema drift.

**How to apply:** Keep the analytics credential separate from deployment credentials unless the latter explicitly includes zone analytics read. Verify the permission with a read-only GraphQL request before diagnosing the workflow query.