"""Syrabit.ai — Database operations: supa_*, pg_* helpers."""
import json, asyncio, logging, uuid, concurrent.futures as _cf
from datetime import datetime, timezone
from typing import Any
import deps as _deps_mod
from deps import supa, db, redis_client
from cache import (
    _invalidate_user_cache, _user_cache, _conv_cache, _conv_cache_key,
    _invalidate_conv_cache, _redis_get_conversation,
    _redis_invalidate_conversation, _redis_cache_conversation,
)
from db_dualwrite import mirror_user_write, clamped_decrement_pipeline

logger = logging.getLogger(__name__)

__all__ = [
    "_THREAD_POOL", "_pg_row", "_pg_rows", "_pg_user_cols", "_supa", "_supa_mirror",
    "_ALLOWED_CONV_COLUMNS", "_ALLOWED_SETTINGS_COLUMNS", "_ALLOWED_USER_COLUMNS",
    "atomic_deduct_credit", "atomic_deduct_ip_credit", "atomic_deduct_device_credit",
    "peek_device_credit_used",
    "supa_clear_activity_log", "supa_count_conversations",
    "supa_count_users", "supa_create_password_reset", "supa_delete_conversation",
    "supa_delete_notification", "supa_delete_password_reset", "supa_get_activity_logs",
    "supa_get_all_conversations", "supa_get_conversation", "supa_get_conversations",
    "supa_get_notifications", "supa_get_password_reset", "supa_get_settings",
    "supa_get_user", "supa_get_user_by_id", "supa_get_user_for_reset",
    "supa_get_users_by_ids", "supa_insert_activity_log", "supa_insert_notification",
    "supa_insert_user", "supa_list_users", "supa_update_conversation",
    "supa_update_settings", "supa_update_user", "supa_update_user_password",
    "supa_upsert_conversation",
    "get_admin_notification_prefs", "upsert_admin_notification_prefs",
    "_ADMIN_NOTIF_PREFS_DEFAULTS",
]

_THREAD_POOL = _cf.ThreadPoolExecutor(max_workers=32)

async def _supa(fn):
    """Await a sync supabase-py operation non-blockingly."""
    return await asyncio.get_event_loop().run_in_executor(_THREAD_POOL, fn)

def _pg_row(row) -> dict:
    """Convert asyncpg Record to plain dict, parsing JSON fields."""
    if row is None:
        return None
    d = dict(row)
    for field in ("saved_subjects", "messages"):
        if field in d and isinstance(d[field], str):
            try: d[field] = json.loads(d[field])
            except: d[field] = [] if field == "messages" else []
    if "metadata" in d and isinstance(d["metadata"], str):
        try: d["metadata"] = json.loads(d["metadata"])
        except: d["metadata"] = {}
    return d

def _pg_rows(rows) -> list:
    return [_pg_row(r) for r in rows] if rows else []

def _pg_user_cols():
    return """id, name, email, password_hash, plan, credits_used, credits_limit,
              document_access, onboarding_done, is_admin, status, bio, phone,
              avatar_url, saved_subjects::text, has_free_credits_issued,
              board_id, board_name, class_id, class_name, stream_id, stream_name,
              credits_used_today, credits_reset_date, created_at,
              google_id, auth_provider,
              consent_dpdp, consent_dpdp_version, consent_dpdp_at,
              ads_opt_out, role"""

# ── Supabase mirror helper ────────────────────────────────────────────────────
def _supa_mirror(fn):
    """Fire-and-forget Supabase write (non-blocking, best-effort)."""
    if supa:
        async def _run():
            try:
                await asyncio.get_event_loop().run_in_executor(_THREAD_POOL, fn)
            except Exception as e:
                logger.debug(f"Supabase mirror failed (non-critical): {e}")
        asyncio.ensure_future(_run())


# ─────────────────────────────────────────────
# USER OPERATIONS  (Supabase / Replit PG layer)
# ─────────────────────────────────────────────

async def supa_get_user(email: str):
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"SELECT {_pg_user_cols()} FROM users WHERE email = $1 LIMIT 1",
                    email.lower()
                )
                if row:
                    return _pg_row(row)
                # not found in PG — fall through to Supabase
        except Exception as e:
            logger.warning(f"pg supa_get_user failed: {e}")
    if supa:
        try:
            r = await _supa(lambda: supa.table("users").select("*").eq("email", email.lower()).limit(1).execute())
            if r.data:
                user = r.data[0]
                # Back-fill PG so future logins are fast
                try:
                    await supa_insert_user(user)
                except Exception:
                    pass
                return user
        except Exception: pass
    try:
        if db is not None:
            return await db.users.find_one({"email": email.lower()}, {"_id": 0})
    except Exception:
        pass
    return None

async def supa_get_user_by_id(uid: str):
    # Fast path: in-memory 30-second cache — skips DB on every auth'd request
    if uid in _user_cache:
        return _user_cache[uid]
    result = None
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"SELECT {_pg_user_cols()} FROM users WHERE id = $1 LIMIT 1", uid
                )
                if row:
                    result = _pg_row(row)
        except Exception as e:
            logger.warning(f"pg supa_get_user_by_id failed: {e}")
    if result is None and supa:
        try:
            r = await _supa(lambda: supa.table("users").select("*").eq("id", uid).limit(1).execute())
            if r.data:
                result = r.data[0]
                try:
                    await supa_insert_user(result)
                except Exception:
                    pass
        except Exception:
            pass
    if result is None:
        try:
            if db is not None:
                result = await db.users.find_one({"id": uid}, {"_id": 0})
        except Exception:
            pass
    if result:
        _user_cache[uid] = result
    return result

async def supa_insert_user(user: dict):
    if _deps_mod.pg_pool:
        try:
            saved = json.dumps(user.get("saved_subjects", []))
            async with _deps_mod.pg_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO users (id, name, email, password_hash, plan, credits_used,
                       credits_limit, document_access, onboarding_done, is_admin, status,
                       bio, phone, avatar_url, saved_subjects, has_free_credits_issued,
                       board_id, board_name, class_id, class_name, stream_id, stream_name,
                       referred_by_code, referred_by_user_id, created_at,
                       google_id, auth_provider)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,$16,
                               $17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27)
                       ON CONFLICT (id) DO NOTHING""",
                    user.get("id",""), user.get("name",""), user.get("email",""),
                    user.get("password_hash",""), user.get("plan","free"),
                    user.get("credits_used",0), user.get("credits_limit",30),
                    user.get("document_access","zero"), user.get("onboarding_done",False),
                    user.get("is_admin",False), user.get("status","active"),
                    user.get("bio",""), user.get("phone",""), user.get("avatar_url",""),
                    saved, user.get("has_free_credits_issued",True),
                    user.get("board_id"), user.get("board_name"),
                    user.get("class_id"), user.get("class_name"),
                    user.get("stream_id"), user.get("stream_name"),
                    user.get("referred_by_code"), user.get("referred_by_user_id"),
                    user.get("created_at",""),
                    user.get("google_id"), user.get("auth_provider"),
                )
            _supa_mirror(lambda: supa.table("users").upsert(user).execute())
            # ADR-0001 Phase 2: mirror to Mongo (best-effort; PG is SoT).
            # upsert via $set so an existing Supabase-mirrored doc is
            # converged rather than colliding on the unique id index.
            # Architect-flagged 2026-05-06: empty/missing id would
            # collapse the filter to {"id": ""} and create a malformed
            # mirror doc shared by every id-less caller — skip the
            # mirror in that case rather than corrupt the collection.
            _new_id = (user.get("id") or "").strip()
            if _new_id:
                await mirror_user_write(
                    "insert",
                    lambda: _deps_mod.db.users.update_one(
                        {"id": _new_id},
                        {"$set": user},
                        upsert=True,
                    ),
                )
            else:
                logger.warning(
                    "dualwrite users.insert skipped: empty 'id' on payload "
                    "(email=%r) — mirror would corrupt collection",
                    user.get("email", ""),
                )
            return
        except Exception as e:
            logger.warning(f"pg supa_insert_user failed: {e}")
    if supa:
        try: await _supa(lambda: supa.table("users").insert(user).execute()); return
        except Exception: pass
    try:
        await db.users.insert_one(user)
    except Exception as e:
        logger.warning(f"All stores failed for insert_user: {e}")

_ALLOWED_USER_COLUMNS = frozenset({
    "name", "bio", "phone", "avatar_url", "plan", "status",
    "credits_used", "credits_limit", "document_access",
    "credits_used_today", "credits_reset_date",
    "saved_subjects", "deletion_requested_at", "deletion_hard_at",
    "last_seen", "onboarding_done",
    "board_id", "board_name", "class_id", "class_name",
    "stream_id", "stream_name",
    "referred_by_code", "referred_by_user_id",
    "google_id", "auth_provider",
    "consent_dpdp", "consent_dpdp_version", "consent_dpdp_at",
    "is_admin",
    "ads_opt_out",
    "role",
})

_ALLOWED_CONV_COLUMNS = frozenset({
    "title", "preview", "subject_id", "subject_name",
    "starred", "archived", "messages", "tokens", "updated_at",
    "metadata",
})

_ALLOWED_SETTINGS_COLUMNS = frozenset({
    "registrations_open", "maintenance_mode", "app_name", "tagline",
    "crawl_coverage_red", "crawl_coverage_yellow", "bot_missing_days",
})

def _quote_ident(name: str) -> str:
    """Double-quote a SQL identifier to prevent injection (defense-in-depth on top of allowlists)."""
    return '"' + name.replace('"', '""') + '"'

_TIMESTAMPTZ_USER_COLS: frozenset = frozenset({"consent_dpdp_at"})

def _coerce_user_val(k: str, v):
    """Coerce values for known PostgreSQL column types in the users table."""
    if k in _TIMESTAMPTZ_USER_COLS and isinstance(v, str) and v:
        from datetime import datetime, timezone
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return v
    return v

async def supa_update_user(uid: str, updates: dict):
    _invalidate_user_cache(uid)  # always bust cache before touching DB
    if _deps_mod.pg_pool and updates:
        try:
            unknown = set(updates) - _ALLOWED_USER_COLUMNS
            if unknown:
                raise ValueError(f"supa_update_user: disallowed column(s): {unknown}")
            cols = []
            vals = []
            for i, (k, v) in enumerate(updates.items(), start=1):
                qi = _quote_ident(k)
                if k == "saved_subjects":
                    cols.append(f"{qi} = ${i}::jsonb")
                    vals.append(json.dumps(v))
                else:
                    cols.append(f"{qi} = ${i}")
                    vals.append(_coerce_user_val(k, v))
            vals.append(uid)
            sql = f"UPDATE users SET {', '.join(cols)} WHERE id = ${len(vals)}"
            async with _deps_mod.pg_pool.acquire() as conn:
                await conn.execute(sql, *vals)
            _supa_mirror(lambda: supa.table("users").update(updates).eq("id", uid).execute())
            # ADR-0001 Phase 2: mirror to Mongo (best-effort; PG is SoT).
            await mirror_user_write(
                "update",
                lambda: _deps_mod.db.users.update_one(
                    {"id": uid},
                    {"$set": updates},
                ),
            )
            return
        except Exception as e:
            logger.warning(f"pg supa_update_user failed: {e}")
    if supa:
        try: await _supa(lambda: supa.table("users").update(updates).eq("id", uid).execute()); return
        except Exception: pass
    try:
        await db.users.update_one({"id": uid}, {"$set": updates})
    except Exception as e:
        logger.warning(f"All stores failed for update_user: {e}")

# Lua: seed the daily counter to ``seed`` if it does not exist (with TTL),
# then atomically increment-and-return only if the post-increment value is
# within ``limit``. Returns the new count on success, -1 if the limit was
# already reached. Executes inside Redis as a single atomic operation, so
# concurrent callers cannot both succeed when only one slot remains.
_REDIS_DEDUCT_LUA = """
local key = KEYS[1]
local seed = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
if redis.call('EXISTS', key) == 0 then
  redis.call('SET', key, seed, 'EX', ttl)
end
local cur = tonumber(redis.call('GET', key)) or 0
if cur >= limit then
  return -1
end
return redis.call('INCR', key)
"""


_REDIS_DEDUCT_SCRIPT_CACHE: dict[int, Any] = {}


def _redis_atomic_deduct(client, key: str, seed: int, limit: int, ttl: int) -> int:
    """Run the atomic deduct script. The registered ``Script`` object is cached
    per Redis client so we don't re-register on every call (registration only
    needs to happen once; ``Script.__call__`` uses EVALSHA with EVAL fallback).

    Falls back to a raw ``eval`` for clients without ``register_script``. Two
    raw-eval signatures are tried, in order:

    1. Upstash REST client: ``client.eval(script, keys=[...], args=[...])``.
       The Upstash client exposes ``eval`` but has no ``register_script``,
       so it lands here on first call. Its signature only accepts the two
       keyword lists — passing redis-py-style positional ``numkeys`` raises
       ``TypeError: Commands.eval() takes from 2 to 4 positional arguments
       but 7 were given``, which is exactly the failure mode that caused
       every IP-coarse-cap deduction to fail-closed and return a spurious
       429 to guests on this environment.

    2. redis-py-shaped test doubles: positional
       ``client.eval(script, numkeys, key, *args)``. Kept as the second
       attempt so legacy unit-test stubs that mimic redis-py's positional
       signature still work.
    """
    cached = _REDIS_DEDUCT_SCRIPT_CACHE.get(id(client))
    if cached is None:
        try:
            cached = client.register_script(_REDIS_DEDUCT_LUA)
            _REDIS_DEDUCT_SCRIPT_CACHE[id(client)] = cached
        except AttributeError:
            try:
                return int(
                    client.eval(_REDIS_DEDUCT_LUA, keys=[key], args=[seed, limit, ttl])
                )
            except TypeError:
                return int(client.eval(_REDIS_DEDUCT_LUA, 1, key, seed, limit, ttl))
    return int(cached(keys=[key], args=[seed, limit, ttl]))


def atomic_deduct_ip_credit(ip: str, daily_limit: int, window_seconds: int = 86400) -> bool:
    """Atomically charge 1 credit against a per-IP daily quota in Redis.

    Mirrors :func:`atomic_deduct_credit`'s Redis fallback: a single Lua
    script (``_REDIS_DEDUCT_LUA``) seeds the counter to 0 with a TTL on
    first use, then check-and-increments only when the post-increment
    value would still be within ``daily_limit``. Concurrent callers can
    therefore never push the counter past the limit, which is the same
    double-spend race that Task #765 fixed for user credit ledgers.

    .. note::
       Task #793 demoted this from "the daily free-tier quota" (30/day)
       to a much higher coarse abuse cap (a few hundred per day,
       configurable via ``IP_COARSE_DAILY_CAP``). The per-device 30/day
       budget is now enforced by :func:`atomic_deduct_device_credit`
       so that shared NAT / school WiFi / Jio CGNAT users no longer
       drain each other's quota.

    Returns
    -------
    True  — credit charged, caller may proceed.
    False — quota already exhausted (or Redis unavailable, fail-closed).
    """
    if not ip:
        return False
    if redis_client is None:
        # Fail-closed: without Redis we cannot make the cross-worker
        # atomic guarantee that this primitive promises. Callers that
        # want a permissive in-memory fallback can implement it on top.
        return False
    from datetime import datetime, timezone
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"ip_daily_credits:{ip}:{today_str}"
    try:
        new_count = _redis_atomic_deduct(
            redis_client, key, 0, int(daily_limit), int(window_seconds),
        )
    except Exception as e:
        logger.warning(f"atomic_deduct_ip_credit redis failed for ip={ip}: {e}")
        return False
    return new_count > 0


def atomic_deduct_device_credit(
    token_id: str, daily_limit: int, window_seconds: int = 86400,
) -> bool:
    """Atomically charge 1 credit against a per-device daily quota.

    This is the Task #793 replacement for the per-IP daily counter
    formerly used in :func:`auth_deps.rate_limit_chat_optional`. The
    key is keyed on the **device-token id** (the verified payload
    minted by :mod:`device_token`), not on the public IP, so two
    students on the same school/college NAT or Jio CGNAT egress each
    get their own 30/day budget.

    Implementation is identical to :func:`atomic_deduct_ip_credit` —
    same Lua script, same atomic check-and-increment guarantee, same
    midnight-UTC reset via the date-suffixed key + TTL — only the key
    namespace differs (``device_daily_credits:`` instead of
    ``ip_daily_credits:``). Reusing the script means the existing
    concurrency regression in
    ``tests/test_atomic_deduct_ip_race.py`` already covers the
    correctness of the underlying primitive; the new tests added in
    Task #793 only have to assert the routing.

    Returns
    -------
    True  — credit charged, caller may proceed.
    False — quota already exhausted (or Redis unavailable, fail-closed).
    """
    if not token_id:
        return False
    if redis_client is None:
        # Same fail-closed posture as atomic_deduct_ip_credit: without
        # Redis we cannot honour the cross-worker atomic guarantee that
        # the per-device quota promises, so we deny rather than silently
        # let abusers through.
        return False
    from datetime import datetime, timezone
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"device_daily_credits:{token_id}:{today_str}"
    try:
        new_count = _redis_atomic_deduct(
            redis_client, key, 0, int(daily_limit), int(window_seconds),
        )
    except Exception as e:
        logger.warning(
            f"atomic_deduct_device_credit redis failed for token={token_id[:8]}…: {e}"
        )
        return False
    return new_count > 0


def peek_device_credit_used(token_id: str) -> int:
    """Return today's per-device daily-credit usage *without* incrementing it.

    Read-only companion to :func:`atomic_deduct_device_credit`, used by
    the ``/user/credits`` endpoint (Task #796) to surface the remaining
    free messages-of-the-day on the chat composer for anonymous
    students. A peek must never charge a credit — students are simply
    rendering "X / 30 left" in the UI; a side-effecting "peek" would
    silently burn one of their messages on every page load.

    Mirrors the date-suffixed Redis key built by
    :func:`atomic_deduct_device_credit` exactly (same
    ``device_daily_credits:<token>:<YYYY-MM-DD>`` namespace, midnight
    UTC reset boundary), so the value returned here always matches
    what the dependency would observe on its next charge attempt.

    Returns
    -------
    int
        Number of messages already charged against the device today
        (``0`` when the counter has not yet been seeded). Returns
        ``0`` for missing tokens or when Redis is unreachable — a
        peek that fails should fall back to an optimistic
        "fresh quota" view in the UI rather than show a misleading
        "0 left" badge.
    """
    if not token_id:
        return 0
    if redis_client is None:
        return 0
    from datetime import datetime, timezone
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"device_daily_credits:{token_id}:{today_str}"
    try:
        raw = redis_client.get(key)
    except Exception as e:
        logger.warning(
            f"peek_device_credit_used redis failed for token={token_id[:8]}…: {e}"
        )
        return 0
    if raw is None:
        return 0
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("ascii", errors="ignore")
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


async def atomic_deduct_credit(uid: str, current_used: int, current_limit: int) -> bool:
    """Atomically deduct 1 daily credit only if credits_used_today < daily limit.
    Returns True on success, False if limit already reached (race condition guard).
    Resets credits_used_today to 0 when credits_reset_date is before today (UTC).
    Uses PG UPDATE...WHERE for atomic check+increment; falls back to a single
    atomic Redis Lua script (see ``_REDIS_DEDUCT_LUA``) that seeds the daily
    counter if absent and only increments when still under ``current_limit``;
    last resort falls back to Supabase with an explicit limit guard.
    """
    from datetime import datetime, timezone
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _invalidate_user_cache(uid)
    # ── Primary: PostgreSQL atomic UPDATE (multi-worker safe) ──────────────
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                await conn.execute(
                    """UPDATE users
                          SET credits_used_today = 0,
                              credits_reset_date = $2
                        WHERE id = $1
                          AND (credits_reset_date IS NULL OR credits_reset_date::text < $2)""",
                    uid, today_str,
                )
                result = await conn.execute(
                    """UPDATE users
                          SET credits_used_today = credits_used_today + 1,
                              credits_used = credits_used + 1
                        WHERE id = $1
                          AND credits_used_today < $2""",
                    uid, current_limit,
                )
            if result and result.split()[-1] != '0':
                # ADR-0001 Phase 2 invariant #3: counter columns must NOT
                # be lost across cutover — mirror the $inc to Mongo
                # (best-effort; PG remains SoT). Today_str carried so the
                # Phase 3 read-shadow can diff per-day buckets.
                _today_str = today_str
                await mirror_user_write(
                    "atomic_deduct_credit",
                    lambda: _deps_mod.db.users.update_one(
                        {"id": uid},
                        {
                            "$inc": {
                                "credits_used_today": 1,
                                "credits_used": 1,
                            },
                            "$set": {"credits_reset_date": _today_str},
                        },
                    ),
                )
                return True
            return False
        except Exception as e:
            logger.warning(f"atomic_deduct_credit pg failed, falling back: {e}")
    # ── Fallback: Redis Lua script — atomic seed-if-absent + check-and-incr ──
    # Task #765 (audit finding B1): the prior implementation issued SETNX
    # then INCR then a compensating DECR as three independent commands.
    # Two concurrent callers could both observe the pre-deduction value,
    # both INCR past the limit, and both write the over-spent count back
    # to the user record before either rollback landed. The Lua script
    # below executes atomically inside Redis, so only callers that fit
    # within ``current_limit`` ever see a successful return.
    if redis_client:
        # Task #769: we've left the Postgres happy path — record the
        # fallback so the alerting loop can page on-call when PG is
        # silently broken. Recording before the attempt ensures we
        # capture the event even if the Redis op itself raises and
        # cascades to Supabase below (in which case both events fire,
        # which is correct: both fallback paths were exercised).
        # Lazy import avoids an import cycle with metrics.py.
        try:
            from metrics import record_credit_fallback as _rcf
            _rcf("redis")
        except Exception:
            pass
        try:
            redis_key = f"daily_credits:{uid}:{today_str}"
            new_count = _redis_atomic_deduct(
                redis_client, redis_key, int(current_used), int(current_limit), 86400,
            )
            if new_count < 0:
                return False
            user_data = await supa_get_user_by_id(uid)
            lifetime_used = (user_data.get("credits_used", 0) if user_data else 0) + 1
            await supa_update_user(uid, {"credits_used_today": int(new_count), "credits_reset_date": today_str, "credits_used": lifetime_used})
            return True
        except Exception as e:
            logger.warning(f"atomic_deduct_credit redis failed, falling back: {e}")
    # ── Last resort: Supabase with explicit limit guard ─────────────────────
    # Task #769: same instrumentation as the Redis branch above.
    try:
        from metrics import record_credit_fallback as _rcf
        _rcf("supabase")
    except Exception:
        pass
    if current_used >= current_limit:
        return False
    new_used = current_used + 1
    user_data = await supa_get_user_by_id(uid)
    lifetime_used = (user_data.get("credits_used", 0) if user_data else 0) + 1
    await supa_update_user(uid, {"credits_used_today": new_used, "credits_reset_date": today_str, "credits_used": lifetime_used})
    return True

async def supa_list_users():
    """Return all non-admin users, merging PG + Supabase so no one is lost."""
    pg_users = []
    supa_users = []

    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    f"SELECT {_pg_user_cols()} FROM users WHERE is_admin = FALSE ORDER BY created_at DESC LIMIT 2000"
                )
                pg_users = _pg_rows(rows)
        except Exception as e:
            logger.warning(f"pg supa_list_users failed: {e}")

    if supa:
        try:
            r = await _supa(lambda: supa.table("users").select("*").neq("is_admin", True).order("created_at", desc=True).limit(2000).execute())
            supa_users = r.data or []
        except Exception:
            pass

    if pg_users or supa_users:
        # Merge: PG is authoritative for users it has; fill in Supabase-only users
        seen = {u["id"] for u in pg_users if u.get("id")}
        for u in supa_users:
            if u.get("id") and u["id"] not in seen:
                seen.add(u["id"])
                pg_users.append(u)
        # Sort by created_at descending
        pg_users.sort(key=lambda u: u.get("created_at") or "", reverse=True)
        return pg_users

    # Fallback to MongoDB
    try:
        if db is not None:
            return await db.users.find({"is_admin": {"$ne": True}}, {"_id": 0}).to_list(2000)
    except Exception:
        pass
    return []

async def supa_get_user_for_reset(email: str):
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                row = await conn.fetchrow("SELECT id, email FROM users WHERE email = $1 LIMIT 1", email.lower())
                return _pg_row(row)
        except Exception: pass
    if supa:
        try:
            r = await _supa(lambda: supa.table("users").select("id,email").eq("email", email.lower()).limit(1).execute())
            if r.data: return r.data[0]
        except Exception: pass
    try:
        return await db.users.find_one({"email": email.lower()}, {"_id": 0, "id": 1, "email": 1})
    except Exception:
        return None

async def supa_update_user_password(email: str, password_hash: str):
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                await conn.execute("UPDATE users SET password_hash = $1 WHERE email = $2", password_hash, email.lower())
            _supa_mirror(lambda: supa.table("users").update({"password_hash": password_hash}).eq("email", email.lower()).execute())
            # ADR-0001 Phase 2: mirror to Mongo (best-effort; PG is SoT).
            _email_lower = email.lower()
            _ph = password_hash
            await mirror_user_write(
                "update_password",
                lambda: _deps_mod.db.users.update_one(
                    {"email": _email_lower},
                    {"$set": {"password_hash": _ph}},
                ),
            )
            return
        except Exception as e:
            logger.warning(f"pg supa_update_user_password failed: {e}")
    if supa:
        try:
            await _supa(lambda: supa.table("users").update({"password_hash": password_hash}).eq("email", email.lower()).execute())
            return
        except Exception: pass
    try:
        await db.users.update_one({"email": email.lower()}, {"$set": {"password_hash": password_hash}})
    except Exception: pass

async def supa_count_users():
    """Count users from both PG and Supabase, returning the larger (most complete) count."""
    pg_count = 0
    supa_count = 0
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                pg_count = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_admin = FALSE") or 0
        except Exception: pass
    if supa:
        try:
            r = await _supa(lambda: supa.table("users").select("id", count="exact").neq("is_admin", True).execute())
            supa_count = r.count if r.count is not None else len(r.data or [])
        except Exception: pass
    if pg_count or supa_count:
        return max(pg_count, supa_count)
    try:
        if db is not None:
            return await db.users.count_documents({"is_admin": {"$ne": True}})
    except Exception: pass
    return 0

async def supa_get_users_by_ids(user_ids: list):
    if not user_ids:
        return []
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, name, email, plan, avatar_url, board_name, class_name, stream_name FROM users WHERE id = ANY($1::text[])",
                    user_ids
                )
                return _pg_rows(rows)
        except Exception: pass
    if supa:
        try:
            r = await _supa(lambda: supa.table("users").select(
                "id,name,email,plan,avatar_url,board_name,class_name,stream_name"
            ).in_("id", user_ids).execute())
            return r.data
        except Exception: pass
    try:
        return await db.users.find(
            {"id": {"$in": user_ids}},
            {"_id": 0, "id": 1, "name": 1, "email": 1, "plan": 1, "avatar_url": 1,
             "board_name": 1, "class_name": 1, "stream_name": 1}
        ).to_list(len(user_ids))
    except Exception:
        return []

# ─────────────────────────────────────────────
# CONVERSATION OPERATIONS  (Supabase = metadata, MongoDB = full content)
# ─────────────────────────────────────────────

async def supa_get_conversations(uid: str):
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id, title, preview, subject_id, subject_name, starred, archived,
                              tokens, created_at, updated_at
                       FROM conversations WHERE user_id = $1 ORDER BY updated_at DESC LIMIT 200""",
                    uid
                )
                return _pg_rows(rows)
        except Exception as e:
            logger.warning(f"pg supa_get_conversations failed: {e}")
    if supa:
        try:
            r = await _supa(lambda: supa.table("conversations").select(
                "id,title,preview,subject_id,subject_name,starred,archived,tokens,created_at,updated_at"
            ).eq("user_id", uid).order("updated_at", desc=True).limit(200).execute())
            return r.data
        except Exception: pass
    try:
        return await db.conversations.find({"user_id": uid}, {"_id": 0, "messages": 0}).sort("updated_at", -1).to_list(200)
    except Exception:
        return []

async def supa_get_conversation(conv_id: str, uid: str):
    _ck = _conv_cache_key(conv_id, uid)
    if _ck in _conv_cache:
        return _conv_cache[_ck]
    cached = _redis_get_conversation(conv_id, uid)
    if cached:
        _conv_cache[_ck] = cached
        return cached

    async def _pg_fetch():
        if not _deps_mod.pg_pool:
            return None
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM conversations WHERE id = $1 AND user_id = $2 LIMIT 1",
                    conv_id, uid
                )
                return _pg_row(row)
        except Exception as e:
            logger.warning(f"pg supa_get_conversation failed: {e}")
            return None

    async def _mongo_fetch():
        try:
            return await db.conversations.find_one({"id": conv_id, "user_id": uid}, {"_id": 0})
        except Exception:
            return None

    import asyncio
    pg_result, mongo_result = await asyncio.gather(_pg_fetch(), _mongo_fetch(), return_exceptions=True)
    result = None
    if pg_result and not isinstance(pg_result, Exception):
        result = pg_result
    elif mongo_result and not isinstance(mongo_result, Exception):
        result = mongo_result

    if result is None and supa:
        try:
            r = await _supa(lambda: supa.table("conversations").select("*").eq("id", conv_id).eq("user_id", uid).limit(1).execute())
            if r.data:
                result = r.data[0]
                if isinstance(result.get("messages"), str):
                    try: result["messages"] = json.loads(result["messages"])
                    except: result["messages"] = []
        except Exception: pass
    if result:
        _conv_cache[_conv_cache_key(conv_id, uid)] = result
        _redis_cache_conversation(conv_id, uid, result)
    return result

def _coerce_ts(value, default_now: bool = True):
    """Coerce ISO-8601 string / datetime / None to a datetime suitable for asyncpg.

    asyncpg's timestamptz binding requires a real ``datetime`` instance — passing
    a string raises ``invalid input for query argument: expected a datetime.date
    or datetime.datetime instance, got 'str'``. Callers historically stored
    ``conv['created_at']`` / ``conv['updated_at']`` as ISO strings (because that
    is how they round-trip through JSON / Mongo / Supabase REST), so we coerce
    here right before binding.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            # fromisoformat handles "2026-04-19T14:31:27.390528+00:00" natively on 3.11+
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc) if default_now else None


async def supa_upsert_conversation(conv: dict):
    _invalidate_conv_cache(conv.get("id",""), conv.get("user_id",""))
    _redis_invalidate_conversation(conv.get("id",""), conv.get("user_id",""))
    if _deps_mod.pg_pool:
        try:
            msgs = json.dumps(conv.get("messages", [])) if isinstance(conv.get("messages"), list) else (conv.get("messages") or "[]")
            _is_anon = conv.get("is_anonymous", False)
            _anon_id = conv.get("anon_id") or None
            _created_at = _coerce_ts(conv.get("created_at"))
            _updated_at = _coerce_ts(conv.get("updated_at"))
            async with _deps_mod.pg_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO conversations (id, user_id, title, preview, subject_id, subject_name,
                       starred, archived, messages, tokens, created_at, updated_at, is_anonymous, anon_id)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                       ON CONFLICT (id) DO UPDATE SET
                         title=EXCLUDED.title, preview=EXCLUDED.preview,
                         subject_id=EXCLUDED.subject_id, subject_name=EXCLUDED.subject_name,
                         starred=EXCLUDED.starred, archived=EXCLUDED.archived,
                         messages=EXCLUDED.messages, tokens=EXCLUDED.tokens,
                         updated_at=EXCLUDED.updated_at,
                         is_anonymous=EXCLUDED.is_anonymous, anon_id=EXCLUDED.anon_id""",
                    conv.get("id",""), conv.get("user_id",""),
                    conv.get("title","New Chat"), conv.get("preview",""),
                    conv.get("subject_id"), conv.get("subject_name"),
                    conv.get("starred",False), conv.get("archived",False),
                    msgs, conv.get("tokens",0),
                    _created_at, _updated_at,
                    _is_anon, _anon_id,
                )
            _mirror_fields = {"id","user_id","title","preview","subject_id","subject_name","starred","archived","messages","tokens","created_at","updated_at"}
            _mirror_data = {}
            for k, v in conv.items():
                if k not in _mirror_fields:
                    continue
                if k == "messages" and isinstance(v, list):
                    _mirror_data[k] = json.dumps(v)
                else:
                    _mirror_data[k] = v
            _supa_mirror(lambda d=_mirror_data: supa.table("conversations").upsert(d).execute())
            return
        except Exception as e:
            logger.warning(f"pg supa_upsert_conversation failed: {e}")
    if supa:
        try:
            allowed = {"id","user_id","title","preview","subject_id","subject_name","starred","archived","messages","tokens","created_at","updated_at"}
            c = {k: v for k, v in conv.items() if k in allowed}
            if isinstance(c.get("messages"), list): c["messages"] = json.dumps(c["messages"])
            await _supa(lambda: supa.table("conversations").upsert(c).execute()); return
        except Exception as e:
            logger.warning(f"supa_upsert_conversation failed: {e}")
    try:
        await db.conversations.replace_one({"id": conv["id"]}, conv, upsert=True)
    except Exception as e:
        logger.warning(f"All stores failed for upsert_conversation: {e}")

async def supa_update_conversation(conv_id: str, uid: str, updates: dict):
    _invalidate_conv_cache(conv_id, uid)
    _redis_invalidate_conversation(conv_id, uid)
    if _deps_mod.pg_pool and updates:
        try:
            u = {k: v for k, v in updates.items() if k in _ALLOWED_CONV_COLUMNS}
            unknown = set(updates) - _ALLOWED_CONV_COLUMNS
            if unknown:
                raise ValueError(f"supa_update_conversation: disallowed column(s): {unknown}")
            if isinstance(u.get("messages"), list): u["messages"] = json.dumps(u["messages"])
            if isinstance(u.get("metadata"), dict): u["metadata"] = json.dumps(u["metadata"])
            if u:
                cols = [f"{_quote_ident(k)} = ${i}" for i, k in enumerate(u.keys(), start=1)]
                vals = list(u.values()) + [conv_id, uid]
                sql = f"UPDATE conversations SET {', '.join(cols)} WHERE id = ${len(vals)-1} AND user_id = ${len(vals)}"
                async with _deps_mod.pg_pool.acquire() as conn:
                    await conn.execute(sql, *vals)
            if supa:
                _supa_allowed = {"title","preview","subject_id","subject_name","starred","archived","messages","tokens","updated_at","metadata"}
                _su = {k: v for k, v in updates.items() if k in _supa_allowed}
                if isinstance(_su.get("messages"), list): _su["messages"] = json.dumps(_su["messages"])
                if _su:
                    _supa_mirror(lambda d=_su, cid=conv_id, u=uid: supa.table("conversations").update(d).eq("id", cid).eq("user_id", u).execute())
            return
        except Exception as e:
            logger.warning(f"pg supa_update_conversation failed: {e}")
    if supa:
        try:
            allowed = {"title","preview","subject_id","subject_name","starred","archived","messages","tokens","updated_at","metadata"}
            u = {k: v for k, v in updates.items() if k in allowed}
            if isinstance(u.get("messages"), list): u["messages"] = json.dumps(u["messages"])
            if u:
                await _supa(lambda: supa.table("conversations").update(u).eq("id", conv_id).eq("user_id", uid).execute())
            return
        except Exception as e:
            logger.warning(f"supa_update_conversation failed: {e}")
    try:
        await db.conversations.update_one({"id": conv_id, "user_id": uid}, {"$set": updates})
    except Exception: pass

async def supa_delete_conversation(conv_id: str, uid: str):
    _redis_invalidate_conversation(conv_id, uid)
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                await conn.execute("DELETE FROM conversations WHERE id = $1 AND user_id = $2", conv_id, uid)
            _supa_mirror(lambda: supa.table("conversations").delete().eq("id", conv_id).eq("user_id", uid).execute())
            return
        except Exception: pass
    if supa:
        try: await _supa(lambda: supa.table("conversations").delete().eq("id", conv_id).eq("user_id", uid).execute()); return
        except Exception: pass
    try:
        await db.conversations.delete_one({"id": conv_id, "user_id": uid})
    except Exception: pass

async def supa_count_conversations():
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                return await conn.fetchval("SELECT COUNT(*) FROM conversations")
        except Exception: pass
    if supa:
        try:
            r = await _supa(lambda: supa.table("conversations").select("id", count="exact").execute())
            return r.count if r.count is not None else len(r.data)
        except Exception: pass
    try:
        return await db.conversations.count_documents({})
    except Exception:
        return 0

async def supa_get_all_conversations(limit: int = 200):
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT $1", limit)
                result = _pg_rows(rows)
                return result
        except Exception as e:
            logger.warning(f"pg supa_get_all_conversations failed: {e}")
    if supa:
        try:
            r = await _supa(lambda: supa.table("conversations").select("*").order("updated_at", desc=True).limit(limit).execute())
            for row in r.data:
                if isinstance(row.get("messages"), str):
                    try: row["messages"] = json.loads(row["messages"])
                    except: row["messages"] = []
            return r.data
        except Exception as e:
            logger.warning(f"supa_get_all_conversations failed: {e}")
    try:
        return await db.conversations.find({}, {"_id": 0}).sort("updated_at", -1).to_list(limit)
    except Exception:
        return []

# ─────────────────────────────────────────────
# APP SETTINGS  (Supabase layer)
# ─────────────────────────────────────────────

async def supa_get_settings():
    defaults = {"registrations_open": True, "maintenance_mode": False, "app_name": "Syrabit.ai", "tagline": "AI-Powered Exam Prep", "crawl_coverage_red": 30, "crawl_coverage_yellow": 50, "bot_missing_days": 3}
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM app_settings WHERE id = 1 LIMIT 1")
                if row: return {**defaults, **dict(row)}
        except Exception: pass
    if supa:
        try:
            r = await _supa(lambda: supa.table("app_settings").select("*").eq("id", 1).limit(1).execute())
            if r.data: return {**defaults, **r.data[0]}
        except Exception: pass
    try:
        s = await db.settings.find_one({}, {"_id": 0})
        return {**defaults, **(s or {})}
    except Exception:
        return defaults

async def supa_update_settings(updates: dict):
    if _deps_mod.pg_pool and updates:
        try:
            unknown = set(updates) - _ALLOWED_SETTINGS_COLUMNS
            if unknown:
                raise ValueError(f"supa_update_settings: disallowed column(s): {unknown}")
            cols = [f"{_quote_ident(k)} = ${i}" for i, k in enumerate(updates.keys(), start=1)]
            vals = list(updates.values())
            sql = f"UPDATE app_settings SET {', '.join(cols)} WHERE id = 1"
            async with _deps_mod.pg_pool.acquire() as conn:
                await conn.execute(sql, *vals)
            return
        except Exception: pass
    if supa:
        try: await _supa(lambda: supa.table("app_settings").update(updates).eq("id", 1).execute()); return
        except Exception: pass
    try:
        await db.settings.update_one({}, {"$set": updates}, upsert=True)
    except Exception: pass

# ─────────────────────────────────────────────
# PASSWORD RESETS  (Supabase layer)
# ─────────────────────────────────────────────

async def supa_create_password_reset(token: str, email: str, expires: str):
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO password_resets (token, email, expires) VALUES ($1, $2, $3) ON CONFLICT (token) DO UPDATE SET email=EXCLUDED.email, expires=EXCLUDED.expires",
                    token, email.lower(), expires
                )
            return
        except Exception as e:
            logger.warning(f"pg supa_create_password_reset failed: {e}")
    if supa:
        try:
            await _supa(lambda: supa.table("password_resets").upsert(
                {"token": token, "email": email.lower(), "expires": expires}, on_conflict="email"
            ).execute()); return
        except Exception as e:
            logger.warning(f"supa_create_password_reset failed: {e}")
    try:
        await db.password_resets.replace_one({"email": email.lower()}, {"email": email.lower(), "token": token, "expires": expires}, upsert=True)
    except Exception: pass

async def supa_get_password_reset(token: str):
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM password_resets WHERE token = $1 LIMIT 1", token)
                return _pg_row(row)
        except Exception: pass
    if supa:
        try:
            r = await _supa(lambda: supa.table("password_resets").select("*").eq("token", token).limit(1).execute())
            if r.data: return r.data[0]
        except Exception: pass
    try:
        return await db.password_resets.find_one({"token": token}, {"_id": 0})
    except Exception:
        return None

async def supa_delete_password_reset(token: str):
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                await conn.execute("DELETE FROM password_resets WHERE token = $1", token)
            return
        except Exception: pass
    if supa:
        try: await _supa(lambda: supa.table("password_resets").delete().eq("token", token).execute()); return
        except Exception: pass
    try:
        await db.password_resets.delete_one({"token": token})
    except Exception: pass

# ─────────────────────────────────────────────
# ACTIVITY LOG + NOTIFICATIONS  (Supabase / Admin layer)
# ─────────────────────────────────────────────

async def supa_get_activity_logs(limit: int = 200):
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM activity_log ORDER BY created_at DESC LIMIT $1", limit)
                return _pg_rows(rows)
        except Exception: pass
    if supa:
        try:
            r = await _supa(lambda: supa.table("activity_log").select("*").order("created_at", desc=True).limit(limit).execute())
            return r.data
        except Exception: pass
    try:
        return await db.activity_log.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)
    except Exception:
        return []

async def supa_insert_activity_log(entry: dict) -> bool:
    """Insert an activity log entry across the pg → supa → mongo tiers.

    Returns True if any tier accepted the write, False only when every
    available tier raised. Callers that need observability on the
    write outcome (e.g. the activity-log purge breadcrumb in
    admin_clear_activity_log) can branch on the return; the existing
    fire-and-forget callers ignore it without behavior change.
    """
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO activity_log (id, action, details, level, admin_name, admin_email, created_at)
                       VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (id) DO NOTHING""",
                    entry.get("id", str(uuid.uuid4())), entry.get("action",""),
                    entry.get("details",""), entry.get("level","info"),
                    entry.get("admin_name",""), entry.get("admin_email",""),
                    entry.get("created_at", datetime.now(timezone.utc).isoformat())
                )
            return True
        except Exception as e:
            logger.warning(f"pg supa_insert_activity_log failed: {e}")
    if supa:
        try:
            allowed = {"id", "action", "details", "level", "admin_name", "admin_email", "created_at"}
            await _supa(lambda: supa.table("activity_log").insert({k: v for k, v in entry.items() if k in allowed}).execute())
            return True
        except Exception as e:
            logger.warning(f"supa_insert_activity_log failed: {e}")
    try:
        await db.activity_log.insert_one(entry)
        return True
    except Exception as e:
        logger.warning(f"mongo supa_insert_activity_log failed: {e}")
        return False

async def supa_clear_activity_log() -> int:
    """Purge every row from the activity log.

    Returns the number of rows actually deleted so callers can attribute
    "Cleared N prior entries" in the immediate self-audit entry that
    admin_settings.admin_clear_activity_log() inserts after this call —
    that breadcrumb is the only thing standing between us and a malicious
    admin silently erasing their own trail. Falls through the pg → supa →
    mongo tiers in the same order as supa_get_activity_logs() so the count
    matches whatever the GET endpoint would have returned. Returns 0 if
    every tier fails (caller still inserts the self-audit entry — the
    breadcrumb is more important than the count's accuracy).
    """
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                # Single round-trip: DELETE ... RETURNING id is cheaper
                # than COUNT(*) followed by DELETE and avoids a TOCTOU
                # race where another admin inserts between the two.
                rows = await conn.fetch("DELETE FROM activity_log RETURNING id")
                return len(rows)
        except Exception as e:
            logger.warning(f"pg supa_clear_activity_log failed: {e}")
    if supa:
        try:
            # supabase-py doesn't surface a delete-count, so we count
            # first then delete. The window between the two is tiny and
            # acceptable for an audit-trail caption.
            cnt_resp = await _supa(lambda: supa.table("activity_log").select("id", count="exact").limit(1).execute())
            count = int(getattr(cnt_resp, "count", 0) or 0)
            await _supa(lambda: supa.table("activity_log").delete().neq("id", "").execute())
            return count
        except Exception as e:
            logger.warning(f"supa supa_clear_activity_log failed: {e}")
    try:
        result = await db.activity_log.delete_many({})
        return int(getattr(result, "deleted_count", 0) or 0)
    except Exception as e:
        logger.warning(f"mongo supa_clear_activity_log failed: {e}")
        return 0

async def supa_get_notifications(limit: int = 100):
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM notifications ORDER BY created_at DESC LIMIT $1", limit)
                return _pg_rows(rows)
        except Exception: pass
    if supa:
        try:
            r = await _supa(lambda: supa.table("notifications").select("*").order("created_at", desc=True).limit(limit).execute())
            return r.data
        except Exception: pass
    try:
        return await db.notifications.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)
    except Exception:
        return []

async def supa_get_notifications_by_title_prefix(prefix: str, limit: int = 10):
    """Return the N most-recent notifications whose title starts with
    ``prefix`` (used by Task #758 to power the Trustpilot JSON-LD alert
    history strip on the admin dashboard).

    Filters on ``title`` rather than ``meta.kind`` because the PG and
    Supabase code paths in ``supa_insert_notification`` only persist a
    fixed column set — ``meta`` / ``channel`` are silently dropped — so
    a meta-based filter would miss everything inserted in production.
    Title-prefix matching is stable because every alert emitter in
    this codebase uses a distinct, namespaced title prefix.

    Tries the same storage backends, in the same priority order, as
    ``supa_get_notifications`` so we stay consistent with the writer.
    """
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT * FROM notifications
                       WHERE title LIKE $1
                       ORDER BY created_at DESC LIMIT $2""",
                    f"{prefix}%", int(limit),
                )
                return _pg_rows(rows)
        except Exception as exc:
            logger.warning(
                "pg supa_get_notifications_by_title_prefix failed: %s", exc,
            )
    if supa:
        try:
            r = await _supa(
                lambda: supa.table("notifications")
                .select("*")
                .like("title", f"{prefix}%")
                .order("created_at", desc=True)
                .limit(int(limit))
                .execute()
            )
            return r.data or []
        except Exception as exc:
            logger.warning(
                "supa supa_get_notifications_by_title_prefix failed: %s", exc,
            )
    try:
        # Mongo fallback — regex-anchored to the prefix.
        import re as _re
        safe = _re.escape(prefix)
        cursor = db.notifications.find(
            {"title": {"$regex": f"^{safe}"}},
            {"_id": 0},
        ).sort("created_at", -1).limit(int(limit))
        return await cursor.to_list(int(limit))
    except Exception:
        return []


async def supa_insert_notification(notif: dict):
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO notifications (id, title, message, type, audience, status, sent_at, created_at)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT (id) DO NOTHING""",
                    notif.get("id", str(uuid.uuid4())), notif.get("title",""),
                    notif.get("message",""), notif.get("type","info"),
                    notif.get("audience","all"), notif.get("status","sent"),
                    notif.get("sent_at"), notif.get("created_at", datetime.now(timezone.utc).isoformat())
                )
            return
        except Exception as e:
            logger.warning(f"pg supa_insert_notification failed: {e}")
    if supa:
        try:
            allowed = {"id", "title", "message", "type", "audience", "status", "sent_at", "created_at"}
            await _supa(lambda: supa.table("notifications").insert({k: v for k, v in notif.items() if k in allowed}).execute()); return
        except Exception as e:
            logger.warning(f"supa_insert_notification failed: {e}")
    try:
        await db.notifications.insert_one(notif)
    except Exception: pass

async def supa_delete_notification(notif_id: str):
    if _deps_mod.pg_pool:
        try:
            async with _deps_mod.pg_pool.acquire() as conn:
                await conn.execute("DELETE FROM notifications WHERE id = $1", notif_id)
            return
        except Exception: pass
    if supa:
        try: await _supa(lambda: supa.table("notifications").delete().eq("id", notif_id).execute()); return
        except Exception: pass
    try:
        await db.notifications.delete_one({"id": notif_id})
    except Exception: pass


_ADMIN_NOTIF_PREFS_DEFAULTS = {
    "sound_enabled": True,
    "push_enabled": False,
    "chime_tone": "default",
    "custom_chime_url": None,
    "custom_chime_filename": None,
    "sound_severities": ["high_error_rate", "high_latency", "spoofed_bot_surge", "high_fallback_rate", "endpoint_down", "auto_block_expired"],
    "push_severities": ["high_error_rate", "spoofed_bot_surge", "endpoint_down", "auto_block_expired"],
    # Task #348: when an admin-triggered SEO sitemap deep scan turns up
    # more than 50 failing URLs, auto-email the full list as a CSV
    # attachment to the configured alert channel so on-call admins can
    # start triage from a phone. Default ON; per-admin opt-out via this
    # toggle in notification preferences.
    "email_failing_csv_enabled": True,
    # Task #465: per-admin opt-in for the daily summary email sent after
    # the scheduled SEO auto-publish run completes. Default ON so admins
    # see degraded runs (errors, drops in avg score, 0 pages) without
    # having to babysit the scheduler.
    "email_seo_daily_summary_enabled": True,
    # Task #465: optional UTC quiet-hours window (inclusive start, exclusive
    # end). When the dispatch time falls inside the window, the SEO daily
    # summary email is suppressed for that admin. Either field can be ``None``
    # to disable the gate. Honored by ``seo_engine._quiet_hours_active``.
    "quiet_hours_start_utc": None,
    "quiet_hours_end_utc": None,
}


async def get_admin_notification_prefs(admin_id: str) -> dict:
    try:
        doc = await db.admin_notification_prefs.find_one({"admin_id": admin_id}, {"_id": 0})
        if doc:
            merged = {**_ADMIN_NOTIF_PREFS_DEFAULTS, **doc}
            merged["defaults"] = _ADMIN_NOTIF_PREFS_DEFAULTS
            return merged
    except Exception as exc:
        logger.warning(f"Failed to get admin notification prefs from MongoDB: {exc}")
    result = {**_ADMIN_NOTIF_PREFS_DEFAULTS, "admin_id": admin_id}
    result["defaults"] = _ADMIN_NOTIF_PREFS_DEFAULTS
    return result


async def upsert_admin_notification_prefs(admin_id: str, prefs: dict) -> dict:
    valid_tones = {"default", "soft", "urgent", "bell", "custom"}
    valid_severities = {"high_error_rate", "high_latency", "spoofed_bot_surge", "high_fallback_rate", "endpoint_down", "auto_block_expired"}

    doc = {"admin_id": admin_id, "updated_at": datetime.now(timezone.utc).isoformat()}

    if "sound_enabled" in prefs:
        doc["sound_enabled"] = bool(prefs["sound_enabled"])
    if "push_enabled" in prefs:
        doc["push_enabled"] = bool(prefs["push_enabled"])
    if "chime_tone" in prefs:
        tone = str(prefs["chime_tone"]).strip()
        doc["chime_tone"] = tone if tone in valid_tones else "default"
    if "custom_chime_url" in prefs:
        val = prefs["custom_chime_url"]
        doc["custom_chime_url"] = str(val).strip() if val else None
    if "custom_chime_filename" in prefs:
        val = prefs["custom_chime_filename"]
        doc["custom_chime_filename"] = str(val).strip()[:100] if val else None
    if "sound_severities" in prefs:
        doc["sound_severities"] = [s for s in prefs["sound_severities"] if s in valid_severities]
    if "push_severities" in prefs:
        doc["push_severities"] = [s for s in prefs["push_severities"] if s in valid_severities]
    if "email_failing_csv_enabled" in prefs:
        doc["email_failing_csv_enabled"] = bool(prefs["email_failing_csv_enabled"])
    if "email_seo_daily_summary_enabled" in prefs:
        doc["email_seo_daily_summary_enabled"] = bool(prefs["email_seo_daily_summary_enabled"])
    for _qh_key in ("quiet_hours_start_utc", "quiet_hours_end_utc"):
        if _qh_key in prefs:
            raw = prefs[_qh_key]
            if raw is None or raw == "":
                doc[_qh_key] = None
            else:
                try:
                    h = int(raw)
                except (TypeError, ValueError):
                    h = None
                doc[_qh_key] = h if (h is not None and 0 <= h <= 23) else None

    await db.admin_notification_prefs.update_one(
        {"admin_id": admin_id},
        {"$set": doc},
        upsert=True,
    )
    return await get_admin_notification_prefs(admin_id)
