# ADR-0001: Migrate user-data store from Replit PostgreSQL to MongoDB Atlas

- **Status:** Proposed (Phase 1 of V4 §13 plan)
- **Date:** 2026-05-06
- **Owner:** founder@syrabit.ai
- **Supersedes:** v3 implicit "PG is the user-data SoT" assumption.
- **Tracked under:** `infra/v4-locked-architecture.md` §13

---

## Context

V4 §11 declares **MongoDB Atlas** in `ap-south-1` as the source of truth
for user data. Reality on `main` (audit, 2026-05-06):

- `artifacts/syrabit-backend/deps.py:135` — `pg_pool: Optional[Any] = None   # filled in lifespan startup`, with the comment "Replit PostgreSQL (asyncpg pool) — primary relational store".
- `_PG_INIT_SQL` in `deps.py` defines `users`, `conversations`, `app_settings`, `password_resets`, `chat_feedback`, `activity_log`, `notifications`.
- `routes/edu_study.py:20` — "Authenticated users → PostgreSQL (asyncpg pool from deps.pg_pool)" with three more tables defined locally: `edu_notes`, `edu_flashcards`, `edu_study_settings`.
- 30+ asyncpg call-sites across `routes/admin_auth_users.py`, `routes/ai_chat.py`, `routes/edu_study.py`, `analytics_helpers.py`, `metrics.py`.
- `server.py:196` — `DATABASE_URL` listed as **always-needed** infrastructure secret (note: the surrounding Railway audit block was tombstoned in 2026-05-06 server.py edit, but the env-var requirement itself is still wired through `_init_pg_pool`).
- Mongo is already used in parallel for `conversations`, `user_profile`, `chunks`, `chat_memory_brain`, `chat_feedback` mirroring (some collections), and most analytics tables — so the dual-write Phase 2 below is partially in place already.

Until V4 §13's full migration completes, V4 is *aspirational* on the
user-data SoT axis: a "Mongo-only" claim in the spec that is not true
on the live pod.

## Decision

Adopt the 5-phase migration in V4 §13. This ADR locks **Phase 1** —
the source-of-truth contract per table, and the read/write semantics
each phase must preserve. Code changes happen in Phases 2–5 against
this contract; any drift requires an ADR amendment.

## Collection-mapping table

The contract is "PG table name on the left, Mongo collection name on
the right; field-level renames only where the PG name is ambiguous;
PG-side `id TEXT` becomes Mongo `_id` unless noted." Every PG table
that any production route reads or writes is listed below.

| PG table | Mongo collection | Migration class | Notes / index targets |
|---|---|---|---|
| `users` | `users` | **Stop-the-world risk** (auth path) | Already-present Mongo `users` collection contains a *subset* (Supabase-mirrored only). Phase 2 dual-write must converge schemas. Indexes: `{email: 1}` unique; `{google_id: 1}` sparse unique. |
| `conversations` | `conversations` *(already exists)* | Soft join | Mongo `conversations` already mirrors writes for most paths; PG and Mongo schemas already overlap. Phase 3 read-shadow will surface any drift in `messages`/`tokens` fields. Indexes: `{user_id: 1, updated_at: -1}`, `{anon_id: 1}`. |
| `app_settings` | `app_settings` | Trivial | Single-row table (`id=1`). Direct copy. |
| `password_resets` | `password_resets` | TTL collection | Mongo TTL index on `expires` replaces the manual cron sweep used in PG. Index: `{expires: 1}` with `expireAfterSeconds: 0`. |
| `chat_feedback` | `chat_feedback` *(already exists)* | Soft join | Already dual-written by `routes/ai_chat.py`. Phase 3 will diff aggregations. Indexes: `{created_at: -1}`, `{user_id: 1}`. |
| `activity_log` | `activity_log` *(already exists)* | Soft join | Mongo collection already used by `db_ops.supa_insert_activity_log`. Phase 3 diff is on per-day count parity. Indexes: `{created_at: -1}`, `{admin_email: 1, created_at: -1}`. |
| `notifications` | `notifications` *(already exists)* | Soft join | Mongo target already populated by admin notify flow. Indexes: `{audience: 1, sent_at: -1}`. |
| `edu_notes` | `edu_notes` (NEW) | Greenfield | PG schema uses `actor_kind, actor` composite key. Mongo translation: `{actor_kind, actor, _id}` document, `tags: [string]`, `structured: {...}` JSON, `citations: [{...}]`. Indexes: `{actor_kind: 1, actor: 1, created_at: -1}`, `{actor_kind: 1, actor: 1, generated: 1}`. |
| `edu_flashcards` | `edu_flashcards` (NEW) | Greenfield | Same actor pattern. Indexes: `{actor_kind: 1, actor: 1, due_at: 1}` for the SR scheduler. Field-level rename: PG `interval_days` → Mongo `interval_days` (no change; explicit so future drift is caught). |
| `edu_study_settings` | `edu_study_settings` (NEW) | Greenfield | Composite key `(actor_kind, actor)` becomes Mongo `_id = "{actor_kind}:{actor}"`. Indexes: none (point lookups by `_id`). |

## Read/write semantics each phase must preserve

Every phase below MUST preserve these invariants for every table above:

1. **Authenticated-user read** never returns data older than 1 s after
   the user's own write (read-your-own-writes). PG today guarantees
   this via the connection pool; Mongo Atlas guarantees it with
   `readPreference=primary` (default for driver writes).
2. **Anonymous-user read** (rows keyed on `anon_id` / `actor_kind=anon`)
   is allowed to be eventually consistent up to 5 s — these flows
   already tolerate Mongo replica lag in their other paths.
3. **Counter columns** (`credits_used`, `repetitions`, `streak_count`)
   MUST NOT be lost across the cutover. Phase 2's dual-write uses
   `$inc` on Mongo for every PG-side increment; Phase 3's read-shadow
   diffs on these counters and BLOCKS Phase 4 if drift > 0 over a
   24 h window.
4. **Schema drift markers** (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
   in `_PG_INIT_SQL`) are translated to Mongo "field appears with
   default on read" semantics, not to a Mongo schema migration. A new
   Python helper `mongo_default_field(coll, field, default)` is added
   in Phase 2.

## Hard blocker: Supabase / Google OAuth

`routes/auth.py` (and `auth_deps.py`) use `deps.supa` to validate
Google ID tokens via Supabase Auth, which itself is backed by the
helium PG. **B5 (Supabase removal) cannot start until Phase 4 of this
plan is complete.** Killing Supabase before Mongo is read-of-record
locks out every Google-OAuth user.

Phase-1 deliverable for the OAuth replacement: a separate ADR-0002
(authored when Phase 4 is approaching) describing a thin native
Mongo-backed `users` collection lookup + a direct Google ID-token
signature verifier (no Supabase round-trip).

## Phases — repeated here for binding force, derived from V4 §13

| # | Phase | Done-when | Rollback |
|---|---|---|---|
| 1 | **ADR (this doc)** | This file is checked in and the V4 §13 row links to it. | n/a (doc-only). |
| 2 | **Dual-write** | Every PG write in `db_ops.py` + `routes/edu_study.py` + `routes/admin_auth_users.py` + `routes/ai_chat.py` is mirrored into the corresponding Mongo collection inside the same request. PG remains read-of-record. New `metric: db.dualwrite.{success,fail}` shipped to Sentry. | Disable mirror via `MONGO_USER_WRITES=0` env flag — single env flip, zero deploy. |
| 3 | **Read-shadow** | Every authenticated read also runs the Mongo equivalent in parallel and diffs the result. Diff > 0.1 % on any 24 h window blocks Phase 4. Sentry counter `db.shadow.{match,diff}`. | Disable shadow read via `MONGO_USER_READ_SHADOW=0`. |
| 4 | **Cutover** | Read-of-record flips to Mongo via `USER_DATA_PRIMARY=mongo` env flag. PG continues as backup-write only. | Flip env back to `USER_DATA_PRIMARY=pg`. (This is the last reversible point.) |
| 5 | **Rip-out** | After 14 days clean on Mongo primary: delete asyncpg from `deps.py`, drop `DATABASE_URL` from the always-needed env list, remove `db_ops.py` PG branches, drop the helium PG instance, remove this asyncpg dependency from `pyproject.toml` / `requirements.txt`. | Restore from PG nightly backup (only viable for ≤24 h post-rip-out). |

## Acceptance script (binary, V4 §13 final gate)

```bash
cd artifacts/syrabit-backend && python -c "
import deps, importlib
assert 'asyncpg' not in [m.__name__ for m in deps.__dict__.values() if hasattr(m, '__name__')], 'asyncpg still imported in deps.py'
src = open('server.py').read()
assert 'DATABASE_URL' not in src, 'DATABASE_URL still referenced in server.py'
print('V4 §13 acceptance: PASS')
"
```

## Out of scope (explicit non-goals)

- Migrating non-user data (chunks, chunk metadata, syllabus map,
  audit logs, SEO meta) — these already live in their V4-correct
  stores (Mongo Atlas, D1, Pinecone). This ADR is bounded to the
  10 PG tables in the table above.
- Replacing Supabase Auth in this plan — see ADR-0002 (TBD) before
  Phase 4 cutover. Phase 4 with Supabase still wired is acceptable
  (Supabase-Auth → Mongo `users` lookup is a valid intermediate
  state); only Phase 5 rip-out is blocked by Supabase removal.
- Cross-region Mongo Atlas replication topology — deferred to its
  own ADR (`ap-south-1` primary stays as-is for Phase 1–5).

## Decision log

- **2026-05-06**: ADR proposed (Phase 1 of V4 §13). Awaiting approval
  before opening Phase 2 dual-write PRs.
- **2026-05-06**: **Phase 2 (users collection only) merged.** Helper
  module `artifacts/syrabit-backend/db_dualwrite.py` added with
  `MONGO_USER_WRITES` rollback flag (default ON), per-process counters
  `users.{success,fail,skipped_disabled,skipped_no_db}`, and a
  best-effort `mirror_user_write(op_label, fn)` that never raises (PG
  remains SoT during Phase 2). Wired into `db_ops.supa_insert_user`,
  `supa_update_user`, `supa_update_user_password`, and the
  `atomic_deduct_credit` PG path (counter `$inc` per ADR §3 invariant
  #3). Added the three previously-missing route mirrors:
  `routes/ai_chat.py:_refund_credit`, `routes/edu_study.py` partial
  refund (race-loss path), and `routes/edu_study.py` full refund
  (`_refund_credits` finally-block). 7-case unit test suite added
  (`tests/test_db_dualwrite.py`). **Carve-out:** the 8 paired
  PG↔Mongo writes in `routes/admin_monetization.py` were intentionally
  NOT migrated to the helper because they have transactional
  compensating-rollback semantics (the Mongo write *must* raise so
  the PG side can be undone); a best-effort helper would silently
  swallow the Mongo error and break the rollback contract. Phase 2
  for the remaining 9 collections (`conversations`, `app_settings`,
  `password_resets`, `chat_feedback`, `activity_log`, `notifications`,
  `edu_notes`, `edu_flashcards`, `edu_study_settings`) NOT STARTED —
  separate sessions per the ADR's per-table contract.

## References

- `infra/v4-locked-architecture.md` §11 (storage roles), §13 (migration plan).
- `artifacts/syrabit-backend/deps.py` — current `_PG_INIT_SQL` source of PG schema.
- `artifacts/syrabit-backend/routes/edu_study.py` — additional `edu_*` table definitions.
- `artifacts/syrabit-backend/db_ops.py` — current dual-store helper layer.
