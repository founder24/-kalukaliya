---
name: MongoDB index conflicts on existing cluster
description: The production MongoDB Atlas cluster has indexes created without all options (e.g., missing unique/sparse flags). create_indexes() handles this gracefully.
---

# MongoDB Index Conflicts on Existing Atlas Cluster

**Rule:** Index creation failures in `create_indexes()` are non-fatal WARNINGs — don't let them abort the whole MongoDB init.

**Why:**
- The production Atlas cluster has `email_1` without `unique: true` and `razorpay_subscription_id_1` without `sparse: true`.
- Attempting to create these with new options throws `IndexKeySpecsConflict` (error code 86).
- These are existing pre-production indexes that predate the current schema. A migration is needed to drop and recreate them with correct options — but that's a separate planned task.

**How to apply:**
- `init_mongo()` wraps `create_indexes()` and `check_and_apply_migrations()` in their own try/except so failures are logged as warnings but don't abort Beanie initialization.
- The email uniqueness is still enforced in prod/staging (the existing try/except in `create_indexes` raises in prod if it fails).
- To fix properly: write a migration in `app/db/migrations/` that drops the conflicting indexes and recreates them with the correct options.
