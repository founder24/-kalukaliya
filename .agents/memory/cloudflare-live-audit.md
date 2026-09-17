---
name: Cloudflare live audit migration drift
description: Production Cloudflare Workers can be healthy while D1 migrations lag behind the deployed Worker code.
---

The Cloudflare API health endpoint is not sufficient release evidence: it can return healthy from a simple D1 connectivity check while chat fails because the live schema is missing columns required by the current Worker. Compare production `d1_migrations` and critical table columns with the release before declaring the stack healthy, and require an authenticated chat smoke test after migration.

**Why:** A live chat request reached the API Worker and failed at quota storage because `chat_request_claims` lacked referral-reservation columns added by a later repository migration; the release workflow had failed before applying pending migrations.

**How to apply:** Treat migration drift as a deployment blocker, apply migrations before serving code that depends on them, and make health/release gates exercise quota, auth, refresh, and one bounded chat stream rather than only `SELECT 1`.

In production, a migration can also be physically present but absent from `d1_migrations`. For example, the bilingual subject columns existed before the ledger row, so Wrangler stopped on a duplicate-column error before reaching later migrations. Verify the expected columns first, then repair only the missing ledger marker and rerun Wrangler.

**Why:** Applying later migrations by ad-hoc SQL would bypass Wrangler's ordered migration history and make future releases ambiguous; the ledger repair preserved the repository migration sequence after confirming the schema was already present.

**How to apply:** If Wrangler stops on a duplicate-column migration, do not delete or recreate production data. Compare `pragma_table_info` with the migration, record only the already-proven migration in `d1_migrations`, and let Wrangler apply the remaining files.