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
| `edu_study_settings` | `edu_study_settings` (NEW) | Greenfield | Composite key `(actor_kind, actor)` is preserved as a natural-key filter `{actor_kind, actor}` on every `update_one` / `delete_one` call (NOT collapsed into a synthetic `_id` string — keeps the two fields independently queryable for read-shadow diffs and for any future Mongo-side analytics by `actor_kind`). Indexes: compound unique on `(actor_kind, actor)` to enforce the PG PK contract; Mongo's auto `_id` ObjectId is left as the document key. Reconciled 2026-05-06 with the shipped Phase 2 implementation. |

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
- **2026-05-06**: **Phase 2 (activity_log collection) merged.** Soft-join
  Mongo target — the ``deps.db.activity_log`` collection is *already*
  populated by the existing 3rd-tier fallback inside
  ``db_ops.supa_insert_activity_log`` whenever both the PG and the
  Supabase legacy tier raise. Phase 2 adds the missing piece: a mirror
  on the **PG-success** branch (and on the ``supa_clear_activity_log``
  PG-success branch) so Mongo now sees *every* admin audit write, not
  only PG-failure ones. This is the prerequisite for the Phase-3
  read-shadow that compares per-day row-counts between the two
  stores. Added ``mirror_activity_log_write()`` shim — no
  ``_FLAG_NAME_OVERRIDES`` entry needed because the default
  ``_flag_env_for("activity_log") = "MONGO_ACTIVITY_LOG_WRITES"``
  already produces the right name (no trailing 's' to strip).
  Crucially, only **2 sites in db_ops.py** had to be instrumented
  (``supa_insert_activity_log`` + ``supa_clear_activity_log``) — the
  routing layer (``routes/admin_settings.py``,
  ``routes/admin_logs.py``, ``routes/admin_auth_users.py`` — 8 total
  call sites) all funnel through the centralised db_ops helpers, so
  zero route-level edits were required. Mirror placement: AFTER the
  ``async with pg_pool.acquire()`` block exits (PG conn released
  first) but BEFORE ``return True`` / ``return len(rows)`` so the
  mirror call is still in scope of the PG-success branch's exception
  handler. The existing 3rd-tier Mongo fallback at the bottom of
  both functions is left untouched — it lives below the helper in
  the call graph and is independently rollback-flagged off (the
  fallback never consults ``MONGO_ACTIVITY_LOG_WRITES`` so flipping
  the mirror off doesn't break the failure-mode safety net). Test
  suite grew 31 → 38 (5 new activity_log cases — env-flag default
  name, default enabled, per-collection isolation across all 6
  collections, insert success counter, delete_many success counter,
  swallows-exception). Soft-join consideration acknowledged: the
  mirror-on-PG-success and the fallback-on-PG-failure paths
  *converge* on the same Mongo doc shape, so post-Phase-2 Mongo
  contains the union of both — exactly what Phase-3 read-shadow
  needs to compare against PG.
- **2026-05-06**: **Phase 2 (edu_study_settings collection) merged.**
  Greenfield Mongo target per §50; fifth collection in the per-collection
  rollout. Distinguishing characteristic: composite primary key
  ``(actor_kind, actor)`` — there is no surrogate ``id`` column, so the
  Mongo doc uses the same composite as its natural key, with every
  write expressed as ``update_one(filter, $set, upsert=True)`` (or
  ``delete_one`` for the claim cleanup). Added
  ``mirror_edu_study_settings_write()`` shim and
  ``_FLAG_NAME_OVERRIDES["edu_study_settings"] = "EDU_STUDY_SETTING"``
  (rollback flag ``MONGO_EDU_STUDY_SETTING_WRITES``, singular form per
  the existing edu_notes / edu_flashcards convention). Wired all 8 PG
  write sites in ``routes/edu_study.py`` — collapsed into 5 mirror
  calls because several PG branches share a final state we can express
  as one upsert: (1-3) the streak block in ``review_flashcard`` has 3
  mutually-exclusive PG writes (first-review INSERT / streak +1 UPDATE
  / streak-reset UPDATE) plus a no-op same-day branch — collapsed into
  ONE post-block ``update_one({actor_kind,actor}, $set, $setOnInsert,
  upsert=True)`` mirror gated on ``_settings_mirror_needed`` (skipped
  when same-day re-review). ``$setOnInsert`` carries the PG defaults
  for ``strict_mode`` / ``guardian_pin_hash`` so the first review
  materialises a complete doc. (4) ``set_study_settings`` strict-mode
  upsert mirrored as ``$set: {updated_at, strict_mode?}`` (the
  ``strict_mode`` $set is omitted when ``req.strict_mode is None`` to
  faithfully replicate PG's ``COALESCE($3, existing)`` no-op
  semantics) plus ``$setOnInsert`` for ``DEFAULT FALSE`` only when the
  request didn't supply a value. (5) ``guardian_pin_set`` PIN upsert
  mirrored as ``$set: {guardian_pin_hash, updated_at}`` plus
  ``$setOnInsert`` defaults — the hash is salted with
  ``f"{actor_kind}:{actor}"`` so it's actor-bound and safe to mirror
  as-is. (6-8) ``claim_anon_data`` has 3 PG writes inside the txn (one
  of {INSERT user-side, UPDATE user-side, no-op} + DELETE anon-side
  + the no-op same-day branch). Captured ``_settings_user_payload``
  (kind=insert | update + the final field values) and
  ``_settings_anon_had_doc`` flag inside the txn; AFTER the txn
  commits, the mirrors fire as: (a) user-side
  ``update_one(filter, $set, upsert=True)`` — INSERT branch overwrites
  fully (we know PG had no user row), UPDATE branch only $sets the 3
  merged fields (leaves Mongo-side ``guardian_pin_hash`` untouched),
  with ``$setOnInsert`` defaults for safety; (b) anon-side
  ``delete_one(filter)``. Same "no phantom Mongo write on PG
  rollback" guarantee as edu_notes / edu_flashcards. Test suite grew
  26 → 31 (5 new edu_study_settings cases — env-flag name singular,
  default enabled, per-collection isolation across all 5 collections,
  upsert success counter, delete_one success, swallows-exception).
  ``routes/edu_study.py`` import line updated to a 3-name multiline
  ``from db_dualwrite import (...)``.
- **2026-05-06**: **Phase 2 (edu_flashcards collection) merged.** Greenfield
  Mongo target per §50; FK child of edu_notes (one note → many cards via
  SM-2 spaced-repetition expansion). Added `mirror_edu_flashcards_write()`
  shim and `_FLAG_NAME_OVERRIDES["edu_flashcards"] = "EDU_FLASHCARD"`
  (rollback flag `MONGO_EDU_FLASHCARD_WRITES`). Wired all 5 PG write
  sites in `routes/edu_study.py`: (1-3) the three INSERT branches in
  `build_flashcards` (Q&A pairs, mnemonics, manual-highlight split) are
  collected into a single `_mongo_docs` list and bulk-mirrored as one
  `insert_many(..., ordered=False)` AFTER the `async with deps.pg_pool.acquire()`
  block exits — amortising the ≤2.4 k-card fan-out (≤12 cards/note × 200
  notes) into one Mongo round-trip and releasing the PG conn first;
  (4) `review_flashcard` SM-2 UPDATE → `replace_one({id, actor_kind, actor},
  _flashcard_row_for_mongo(updated), upsert=True)` (greenfield-safe; the
  PG `async with` was split so the mirror runs between the UPDATE and
  the streak-update block, freeing the PG conn during the Mongo
  round-trip); (5) `claim_anon_data` bulk reassign → `update_many(
  {actor_kind:"anon", actor:_anon}, {$set:{actor_kind:"user", actor:_uid,
  claimed_at:_claimed_at}})` placed in the same post-transaction block
  as the edu_notes claim mirror, gated on `cards_count > 0` so the
  counter only increments when PG actually moved rows. Two new helpers:
  `_flashcard_doc_for_mongo()` constructs the INSERT-side doc from the
  parameters we passed PG (replicates `DEFAULT 2.5 / 0 / NOW()` SM-2
  state client-side rather than round-tripping `RETURNING *` because
  `execute` is materially faster for the bulk-insert hot path; clock
  skew vs PG `NOW()` is sub-ms and irrelevant for day-granularity
  scheduling); `_flashcard_row_for_mongo()` converts the post-review
  `RETURNING *` Record into a Mongo doc (no JSONB columns to normalise).
  Test suite grew 21 → 26 (5 new edu_flashcards cases — env-flag name,
  default enabled, per-collection isolation across users + conversations
  + edu_notes + edu_flashcards, bulk insert_many success counter,
  swallows-exception). `routes/edu_study.py` import line updated to
  `from db_dualwrite import mirror_edu_flashcards_write, mirror_edu_notes_write`.
- **2026-05-06**: **Phase 2 (edu_notes collection) merged.** Greenfield
  Mongo target per §50; added `mirror_edu_notes_write()` shim and
  `_FLAG_NAME_OVERRIDES["edu_notes"] = "EDU_NOTE"` so the rollback flag
  is `MONGO_EDU_NOTE_WRITES`. Wired all 5 PG write sites in
  `routes/edu_study.py`: (1) `create_note` → `insert_one`; (2)
  `patch_note` → `replace_one(upsert=True)` (greenfield-safe — pre-Phase-2
  PG rows have no Mongo twin yet); (3) `delete_note` → `delete_one`;
  (4) AI-autogen note INSERT → `insert_one`; (5) `claim_anon_data` bulk
  reassignment → `update_many({actor_kind:"anon", actor:anon}, {$set:{
  actor_kind:"user", actor:user_id, claimed_at:now}})`. The claim mirror
  fires AFTER the PG `async with conn.transaction()` block exits, so a
  PG rollback cannot leave a phantom Mongo write; gated on
  `notes_count > 0` to keep counters honest. New `_note_row_for_mongo()`
  helper normalizes `structured`/`citations` JSONB (asyncpg may surface
  either str or dict depending on codec) so future Phase-3 read-shadow
  diffs aren't tripped by string-vs-dict noise. Test suite grew 16 → 21
  (added 5 edu_notes cases: env-flag name, default enabled, per-collection
  flag isolation across users+conversations+edu_notes, success counter,
  swallows-exception). `routes/edu_study.py` ships with
  `from db_dualwrite import mirror_edu_notes_write` at the top.
- **2026-05-06**: **Phase 2 (conversations collection) merged.** Generalised
  the dual-write helper to take a per-collection name (counters now keyed
  `<collection>.{success,fail,skipped_disabled,skipped_no_db}`; per-collection
  env flag `MONGO_<NAME>_WRITES`, default ON). Added
  `mirror_conversation_write()` shim and wired it into the three PG-success
  paths in `db_ops.py`: `supa_upsert_conversation` (mirrors `replace_one(...,
  upsert=True)` with empty-id guard), `supa_update_conversation` (mirrors
  `update_one({"id":..., "user_id":...}, {"$set": updates})` using the
  caller's *original* dict so `messages` stays as a Python list — the
  PG-coerced JSON-stringified `u` would corrupt the Mongo doc), and
  `supa_delete_conversation` (mirrors `delete_one({"id":..., "user_id":...}`).
  All three sites already had a Mongo *fallback* write on PG-failure (lines
  897/938/954 pre-edit); the new mirrors fire on the PG-success path inside
  the same request, so no double-write occurs. B4 callers
  (`mirror_user_write`, `mongo_user_writes_enabled`) keep working via thin
  shims over the new generic helper. Test suite grew 9 → 16 (added
  conversations env-flag, namespaced counter, error-swallow, per-collection
  flag isolation, and back-compat shim cases).
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
