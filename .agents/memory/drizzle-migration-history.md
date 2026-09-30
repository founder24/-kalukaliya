---
name: Drizzle migration history
description: A generator limitation with the project's hand-maintained D1 migration history.
---

The API migration history is maintained as numbered D1 SQL files without Drizzle snapshots. Running Drizzle Kit generation against the current schema can treat it as a new database, emit the entire schema as a `0000` migration, and replace the migration journal. That output is not an incremental migration and must not be deployed.

**Why:** A schema change generated a duplicate-sequence full-schema migration rather than a delta, while the existing SQL history remained intact.

**How to apply:** Keep the Drizzle schema and D1 SQL history in sync by adding a numbered incremental SQL migration, preserving the journal, and running tests that apply the migrations.