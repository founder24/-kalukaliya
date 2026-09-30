---
name: Drizzle migration history
description: A generator limitation with the project's hand-maintained D1 migration history.
---

The API migration history is maintained as numbered D1 SQL files without Drizzle snapshots. Running Drizzle Kit generation against the current schema can treat it as a new database, emit the entire schema as a `0000` migration, and replace the migration journal. That output is not an incremental migration and must not be deployed.

**Why:** A schema change generated a duplicate-sequence full-schema migration rather than a delta, while the existing SQL history remained intact.

**How to apply:** Keep the Drizzle schema and D1 SQL history in sync by adding a numbered incremental SQL migration, preserving the journal, and running tests that apply the migrations.

## Production migration identity drift

Applied D1 migration filenames are durable identities. Parallel release histories can leave applied filenames in `d1_migrations` after current source reuses their numeric prefixes. Do not delete or rename ledger rows to satisfy a release check. Do not blindly restore old SQL into Wrangler's active migration directory: an exact identity repair can still make a clean replay fail if another tracked migration repeats the same schema change.

**Why:** A production preflight found applied filenames missing from the active directory; a historical migration and a later migration both add the monthly claim-period column.

**How to apply:** Before resolving an identity mismatch, compare the full remote ledger, historical SQL, current migration contents, production schema, and both clean-database and production-pending replays. Keep production ledger reads read-only except Wrangler's normal migration bookkeeping.