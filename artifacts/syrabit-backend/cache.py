"""Syrabit.ai — Redis + in-memory caching helpers."""
import asyncio, hashlib, json, logging, os, threading, time
from typing import Any, Optional
import cachetools
from deps import redis_client

logger = logging.getLogger(__name__)


# ── Task #571 — L1 in-process cache instrumentation ──────────────────────────
# Hit/miss/set counters per named TTLCache. The counters are module-level and
# read by `routes/admin_cache._l1_inproc_stats` for the /api/health/cache panel
# + the nightly `Syrabit/Cache` CloudWatch shipper. Implementation contract:
# every write path (`__setitem__`, `pop`) and every read path (`__getitem__`,
# `__contains__`, `get`) is intercepted on the subclass. The previous bare
# `cachetools.TTLCache` instances had no counters at all — operators could not
# tell whether a Redis fan-out / DB hit was happening because the L1 ring was
# missing or just because the request was genuinely cold.
_L1_COUNTERS_LOCK = threading.Lock()
_L1_COUNTERS: dict[str, dict[str, int]] = {}


def _l1_record(name: str, kind: str) -> None:
    """Increment a (name, kind) counter under the module-level lock.

    `kind` ∈ {"hits", "misses", "sets"}. Best-effort — any failure inside the
    counter path must never raise into the caller's hot path."""
    try:
        with _L1_COUNTERS_LOCK:
            row = _L1_COUNTERS.setdefault(name, {"hits": 0, "misses": 0, "sets": 0})
            if kind in row:
                row[kind] += 1
    except Exception:  # pragma: no cover — defensive
        pass


def l1_counters_snapshot() -> dict[str, dict[str, int]]:
    """Return a deep copy of every (cache-name → {hits, misses, sets}) row.

    Read by `routes/admin_cache._l1_inproc_stats`. Snapshot is a copy so the
    admin endpoint cannot mutate the live counters. Cheap (≤ 11 caches)."""
    with _L1_COUNTERS_LOCK:
        return {name: dict(row) for name, row in _L1_COUNTERS.items()}


def l1_counters_reset_for_tests() -> None:
    """Test-only escape hatch — wipe every counter row.

    Production callers MUST NOT use this; the panel relies on monotonic
    counters and the `cache-cardinality-spike` alarm computes a 7-day moving
    average that breaks if counters reset mid-day."""
    with _L1_COUNTERS_LOCK:
        _L1_COUNTERS.clear()


class _InstrumentedTTLCache(cachetools.TTLCache):
    """`cachetools.TTLCache` subclass that records hits/misses/sets per name.

    Counting policy (Task #571 round-5 — accuracy fix):
    Every successful public read = 1 hit, every failed public read = 1 miss.
    The catch is that `cachetools.Cache.get()` is implemented as roughly
    ``if key in self: return self[key]`` — which would route a single hit
    through BOTH `__contains__` and `__getitem__` and double-count it.
    We therefore override `get()` directly to dispatch through `__getitem__`
    only, and we leave `__contains__` to count standalone `key in cache`
    queries (which are independent read attempts and deserve their own
    accounting). Sets count `__setitem__` only — `pop` of an absent key
    is silently a no-op for the cardinality counter, matching the parent
    class semantics.

    Verified by `tests/test_cache_l1_instrumentation.py`."""

    def __init__(self, *args: Any, name: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Pre-register the row so the snapshot endpoint always shows every L1
        # ring even if it has not yet served a request. Otherwise a freshly
        # restarted pod would be missing rows the panel expects.
        with _L1_COUNTERS_LOCK:
            _L1_COUNTERS.setdefault(name, {"hits": 0, "misses": 0, "sets": 0})
        self._name = name

    def __getitem__(self, key: Any) -> Any:  # type: ignore[override]
        try:
            v = super().__getitem__(key)
        except KeyError:
            _l1_record(self._name, "misses")
            raise
        _l1_record(self._name, "hits")
        return v

    def __contains__(self, key: object) -> bool:  # type: ignore[override]
        # Standalone `key in cache` query. Counted independently of
        # `get()` / `__getitem__` so a containment check followed by a
        # subsequent read counts as two reads (which is what it is — two
        # public lookups on this ring).
        present = super().__contains__(key)
        _l1_record(self._name, "hits" if present else "misses")
        return present

    def get(self, key, default=None):  # type: ignore[override]
        # Override the cachetools default `get` (which does `if key in
        # self: return self[key]`) so a single `get()` does not double-
        # count through both `__contains__` and `__getitem__`. Routing
        # through `__getitem__` only means each `get()` call increments
        # exactly one counter — the hit on success or the miss on KeyError.
        try:
            return self[key]
        except KeyError:
            return default

    def __setitem__(self, key: Any, value: Any) -> None:  # type: ignore[override]
        super().__setitem__(key, value)
        _l1_record(self._name, "sets")

# Lazy import — ai_cache imports from us indirectly via the legacy fallback
# path, so we resolve it on demand inside the AI-cache helpers below to keep
# import order safe.
def _ai_cache_mod():
    try:
        import ai_cache as _m
        return _m
    except Exception:
        return None

REDIS_ANON_CONV_TTL = 604800

__all__ = [
    "CONTENT_CACHE_SECONDS", "REDIS_AI_CACHE_TTL", "REDIS_ANON_CONV_TTL",
    "REDIS_CASUAL_CACHE_TTL",
    "REDIS_CHAT_CACHE_TTL", "REDIS_CONTENT_PREFIX", "REDIS_RATE_WINDOW",
    "REDIS_SEARCH_CACHE_TTL", "REDIS_SESSION_CACHE_TTL",
    "build_ai_cache_key", "ai_cache_aget", "ai_cache_aset",
    "ai_cache_record_hit_saved_latency",
    "_ai_response_cache", "_cache_key", "_content_cache",
    "_embedding_cache", "_embedding_cache_key", "_query_embed_cache",
    "_content_card_cache", "_content_card_cache_key", "_conv_cache", "_conv_cache_key",
    "_get_content_cache", "_invalidate_content_cache", "_invalidate_conv_cache",
    "_invalidate_user_cache", "_rag_cache", "_rag_cache_key",
    "_redis_cache_conversation", "_redis_cache_search", "_redis_cache_session",
    "_redis_del", "_redis_get", "_redis_get_ai_cache", "_redis_get_ai_cache_async", "_redis_get_conversation",
    "_redis_get_search", "_redis_get_session", "_redis_hit_count",
    "_redis_invalidate_conversation", "_redis_invalidate_session", "_redis_miss_count",
    "_redis_set", "_set_content_cache", "_syllabus_cache", "_syllabus_cache_key",
    "_user_cache", "_vector_rag_cache", "_vector_rag_cache_key",
    "redis_save_anon_conversation", "redis_get_anon_conversation",
    "redis_list_anon_conversations", "redis_list_all_anon_conversations",
    "redis_delete_anon_conversation",
    "get_hierarchy_cache", "set_hierarchy_cache",
]

_ai_response_cache = _InstrumentedTTLCache(maxsize=4096, ttl=7200, name="_ai_response_cache")  # Flex/upgraded Redis: 4× larger, 2× longer

# ── User Object Cache ─────────────────────────────────────────────────────────
# Keyed by user_id — eliminates DB round-trip on every auth'd request.
# Flex tier: 5000 entries (up from 2000), 15-min TTL (up from 10-min).
_user_cache: cachetools.TTLCache = _InstrumentedTTLCache(maxsize=5000, ttl=900, name="_user_cache")

def _invalidate_user_cache(uid: str):
    _user_cache.pop(uid, None)
    _redis_del("session", uid)

# ── Conversation Object Cache ──────────────────────────────────────────────────
# Keyed by "conv_id:uid" — avoids PG on every chat turn.
# Flex tier: 10000 entries (up from 4000).
_conv_cache: cachetools.TTLCache = _InstrumentedTTLCache(maxsize=10000, ttl=600, name="_conv_cache")

def _conv_cache_key(conv_id: str, uid: str) -> str:
    return f"{conv_id}:{uid}"

def _invalidate_conv_cache(conv_id: str, uid: str):
    _conv_cache.pop(_conv_cache_key(conv_id, uid), None)

# ── RAG Result Cache ───────────────────────────────────────────────────────────
# Keyed by (query_hash, subject_id) — skips 3 MongoDB queries on repeat.
# Flex tier: 8192 entries (up from 2048), 30-min TTL (up from 15-min).
_rag_cache: cachetools.TTLCache = _InstrumentedTTLCache(maxsize=8192, ttl=1800, name="_rag_cache")

# Vector RAG cache — Workers AI embed API calls saved by keeping results longer.
# Flex tier: 2048 entries (up from 512), 20-min TTL (up from 10-min).
_vector_rag_cache: cachetools.TTLCache = _InstrumentedTTLCache(maxsize=2048, ttl=1200, name="_vector_rag_cache")

# Query embedding cache — avoids re-embedding the same queries.
# Flex tier: 2048 entries (up from 512), 30-min TTL (up from 15-min).
_query_embed_cache: cachetools.TTLCache = _InstrumentedTTLCache(maxsize=2048, ttl=1800, name="_query_embed_cache")

# Content card cache — avoids duplicate seo_pages + chapters queries.
# Flex tier: 2048 entries (up from 512), 15-min TTL (up from 10-min).
_content_card_cache: cachetools.TTLCache = _InstrumentedTTLCache(maxsize=2048, ttl=900, name="_content_card_cache")

def _content_card_cache_key(query: str, subject_id: Optional[str], subject_name: Optional[str], intent: Optional[str] = None, chapter_title: Optional[str] = None) -> str:
    raw = f"{query.strip().lower()}|{subject_id or ''}|{subject_name or ''}|{intent or ''}|{chapter_title or ''}"
    # Using MD5 for cache key (non-cryptographic use case, collision risk acceptable for cache invalidation)
    return hashlib.md5(raw.encode()).hexdigest()

# Syllabus cache — syllabi almost never change; 1h TTL.
# Flex tier: 1024 entries (up from 256).
_syllabus_cache: cachetools.TTLCache = _InstrumentedTTLCache(maxsize=1024, ttl=3600, name="_syllabus_cache")

def _syllabus_cache_key(board_id: str, class_id: str, stream_id: Optional[str], subject_id: Optional[str] = None) -> str:
    return f"{board_id}|{class_id}|{stream_id or ''}|{subject_id or ''}"

def _rag_cache_key(query: str, subject_id: Optional[str], subject_name: Optional[str]) -> str:
    raw = f"{query.strip().lower()}|{subject_id or ''}|{subject_name or ''}"
    # Using MD5 for cache key (non-cryptographic use case)
    return hashlib.md5(raw.encode()).hexdigest()

def _vector_rag_cache_key(query: str, subject_id: Optional[str], top_k: int) -> str:
    raw = f"{query.strip().lower()}|{subject_id or ''}|{top_k}"
    # Using MD5 for cache key (non-cryptographic use case)
    return hashlib.md5(raw.encode()).hexdigest()

# Embedding cache — Flex tier: 4096 entries (up from 1024), 30-min TTL.
_embedding_cache: cachetools.TTLCache = _InstrumentedTTLCache(maxsize=4096, ttl=1800, name="_embedding_cache")

def _embedding_cache_key(text: str, task_type: str) -> str:
    raw = f"{text[:200].strip().lower()}|{task_type}"
    # Using MD5 for cache key (non-cryptographic use case)
    return hashlib.md5(raw.encode()).hexdigest()

# Task #609 — TTLs are env-tunable; defaults preserved for back-compat.
# Pulled from config.py so any change to the env vars is picked up without
# having to keep two copies in sync. Importing here (instead of at module
# top) avoids a circular import from modules that import constants from
# `cache` during config load.
try:
    from config import (
        REDIS_AI_CACHE_TTL as _CFG_AI_TTL,
        REDIS_CASUAL_CACHE_TTL as _CFG_CASUAL_TTL,
        REDIS_CHAT_CACHE_TTL as _CFG_CHAT_TTL,
    )
    REDIS_AI_CACHE_TTL = int(_CFG_AI_TTL)
    REDIS_CASUAL_CACHE_TTL = int(_CFG_CASUAL_TTL)
    REDIS_CHAT_CACHE_TTL = int(_CFG_CHAT_TTL)
except Exception:
    REDIS_AI_CACHE_TTL = int(os.environ.get('REDIS_AI_CACHE_TTL', '3600') or '3600')
    REDIS_CASUAL_CACHE_TTL = int(os.environ.get('REDIS_CASUAL_CACHE_TTL', '300') or '300')
    REDIS_CHAT_CACHE_TTL = int(os.environ.get('REDIS_CHAT_CACHE_TTL', '600') or '600')
REDIS_SEARCH_CACHE_TTL = 300
REDIS_SESSION_CACHE_TTL = 1800
REDIS_RATE_WINDOW = 60

_redis_miss_count = 0
_redis_hit_count = 0

def _cache_key(query: str, subject_id: str = "", board_id: str = "", conversation_id: str = "") -> str:
    normalized = f"{query.lower().strip()}|{subject_id or ''}|{board_id or ''}|{conversation_id or ''}"
    # Using MD5 for cache key (non-cryptographic use case)
    return hashlib.md5(normalized.encode()).hexdigest()

def _redis_get(prefix: str, key: str) -> Optional[str]:
    global _redis_hit_count, _redis_miss_count
    if redis_client:
        try:
            val = redis_client.get(f"{prefix}:{key}")
            if val is not None:
                _redis_hit_count += 1
                return val
            _redis_miss_count += 1
        except Exception as e:
            logger.debug(f"Redis GET {prefix}:{key} failed: {e}")
    return None

async def _redis_get_async(prefix: str, key: str) -> Optional[str]:
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _redis_get, prefix, key)

async def _redis_get_ai_cache_async(key: str) -> Optional[str]:
    """Async AI-cache GET. Routes through the managed Memorystore client when
    initialised. When MEMORYSTORE_REDIS_URL is unset (current default), LLM
    caching is handled by Cloudflare AI Gateway at the edge."""
    mod = _ai_cache_mod()
    if mod is not None and getattr(mod, "_async_pool", None) is not None:
        return await mod.aget(key)
    return await _redis_get_async("ai_cache", key)

def _redis_set(prefix: str, key: str, value: str, ttl: int):
    # AI-cache writes go through the managed module when available so the
    # Memorystore namespace, max-entry guard, and metrics all apply.
    if prefix == "ai_cache":
        mod = _ai_cache_mod()
        if mod is not None and getattr(mod, "_async_pool", None) is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(mod.aset(key, value, int(ttl)))
                return
            except RuntimeError:
                # No running loop — fall through to sync path
                pass
    if redis_client:
        try:
            redis_client.set(f"{prefix}:{key}", value, ex=ttl)
        except Exception as e:
            logger.debug(f"Redis SET {prefix}:{key} failed: {e}")

def _redis_del(prefix: str, key: str):
    if redis_client:
        try:
            redis_client.delete(f"{prefix}:{key}")
        except Exception:
            pass

def _redis_get_ai_cache(key: str) -> Optional[str]:
    return _redis_get("ai_cache", key)


def build_ai_cache_key(*, model: str, prompt: str, retrieval=None,
                       language: str = "", scope: str = "") -> str:
    """Public, delegating shim for the managed AI cache key builder."""
    mod = _ai_cache_mod()
    if mod is not None:
        return mod.build_ai_cache_key(model=model, prompt=prompt,
                                      retrieval=retrieval, language=language,
                                      scope=scope)
    # Lightweight degraded fallback (no model/retrieval scope) — should never
    # happen in practice because ai_cache is importable whenever cache.py is.
    raw = f"{model}|{(prompt or '').strip().lower()}|{language}|{scope}"
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:40]


async def ai_cache_aget(key: str) -> Optional[str]:
    mod = _ai_cache_mod()
    if mod is not None:
        return await mod.aget(key)
    return await _redis_get_ai_cache_async(key)


async def ai_cache_aset(key: str, value: str, ttl: int, *, saved_ms: float = 0.0) -> bool:
    mod = _ai_cache_mod()
    if mod is not None:
        return await mod.aset(key, value, int(ttl), saved_ms=saved_ms)
    _redis_set("ai_cache", key, value, int(ttl))
    return True


def ai_cache_record_hit_saved_latency(ms: float) -> None:
    mod = _ai_cache_mod()
    if mod is not None:
        mod.record_hit_saved_latency(ms)


def ai_cache_expected_saved_ms() -> float:
    """Returns rolling-avg LLM latency a hit would have avoided (ms)."""
    mod = _ai_cache_mod()
    if mod is not None:
        try:
            return float(mod.expected_saved_ms())
        except Exception:
            return 0.0
    return 0.0


def _redis_cache_conversation(conv_id: str, user_id: str, conv_data: dict):
    _redis_set("chat", f"{conv_id}:{user_id}", json.dumps(conv_data, default=str), REDIS_CHAT_CACHE_TTL)

def _redis_get_conversation(conv_id: str, user_id: str) -> Optional[dict]:
    val = _redis_get("chat", f"{conv_id}:{user_id}")
    if val:
        try:
            return json.loads(val) if isinstance(val, str) else val
        except Exception:
            pass
    return None

def _redis_invalidate_conversation(conv_id: str, user_id: str):
    _redis_del("chat", f"{conv_id}:{user_id}")

def _redis_cache_search(query_hash: str, results: list):
    _redis_set("search", query_hash, json.dumps(results, default=str), REDIS_SEARCH_CACHE_TTL)

def _redis_get_search(query_hash: str) -> Optional[list]:
    val = _redis_get("search", query_hash)
    if val:
        try:
            return json.loads(val) if isinstance(val, str) else val
        except Exception:
            pass
    return None

def _redis_cache_session(user_id: str, session_data: dict):
    _redis_set("session", user_id, json.dumps(session_data, default=str), REDIS_SESSION_CACHE_TTL)

def _redis_get_session(user_id: str) -> Optional[dict]:
    val = _redis_get("session", user_id)
    if val:
        try:
            return json.loads(val) if isinstance(val, str) else val
        except Exception:
            pass
    return None

def _redis_invalidate_session(user_id: str):
    _redis_del("session", user_id)
    _invalidate_user_cache(str(user_id))

_hierarchy_cache: cachetools.TTLCache = _InstrumentedTTLCache(maxsize=256, ttl=1800, name="_hierarchy_cache")

def get_hierarchy_cache(stream_id: str):
    return _hierarchy_cache.get(stream_id)

def set_hierarchy_cache(stream_id: str, data: dict):
    _hierarchy_cache[stream_id] = data

_content_cache: cachetools.TTLCache = _InstrumentedTTLCache(maxsize=1024, ttl=1800, name="_content_cache")
CONTENT_CACHE_SECONDS = 1800
REDIS_CONTENT_PREFIX = "content:"

def _get_content_cache(key: str):
    cached = _content_cache.get(key)
    if cached is not None:
        return cached
    if redis_client:
        try:
            val = redis_client.get(f"{REDIS_CONTENT_PREFIX}{key}")
            if val:
                parsed = json.loads(val) if isinstance(val, str) else val
                _content_cache[key] = parsed
                return parsed
        except Exception:
            pass
    return None

def _invalidate_content_cache(prefix: str):
    _CHAPTER_PREFIXES = ("ch-slug:", "ch-topic-content:", "ch-topic-summary:", "chunks:", "topic-pyqs:", "topic-page:", "flashcards:")
    _LIB_BUNDLE_KEYS = ("library-bundle", "library-bundle:seo", "library-bundle:slim")
    if prefix == "chapters":
        keys_to_del = [k for k in list(_content_cache.keys())
                       if k == prefix or k.startswith(f"{prefix}:")
                       or k in _LIB_BUNDLE_KEYS
                       or any(k.startswith(p) for p in _CHAPTER_PREFIXES)]
    else:
        keys_to_del = [k for k in list(_content_cache.keys())
                       if k == prefix or k.startswith(f"{prefix}:") or k in _LIB_BUNDLE_KEYS]
    for k in keys_to_del:
        _content_cache.pop(k, None)
    _content_card_cache.clear()
    _vector_rag_cache.clear()
    if redis_client:
        try:
            for rk in redis_client.scan_iter(f"{REDIS_CONTENT_PREFIX}{prefix}*"):
                redis_client.delete(rk)
            for lbk in _LIB_BUNDLE_KEYS:
                redis_client.delete(f"{REDIS_CONTENT_PREFIX}{lbk}")
            if prefix == "chapters":
                for cp in _CHAPTER_PREFIXES:
                    for rk in redis_client.scan_iter(f"{REDIS_CONTENT_PREFIX}{cp}*"):
                        redis_client.delete(rk)
        except Exception:
            pass
    _fire_cf_edge_purge(prefix)


_cf_purge_scheduled = False
_cf_purge_prefixes: list = []

def _fire_cf_edge_purge(prefix: str):
    global _cf_purge_scheduled
    _cf_purge_prefixes.append(prefix)
    if _cf_purge_scheduled:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _cf_purge_scheduled = True
    try:
        from cloudflare_client import purge_content_prefixes
        loop.create_task(_debounced_cf_purge(purge_content_prefixes))
    except Exception:
        _cf_purge_scheduled = False
        _cf_purge_prefixes.clear()


async def _debounced_cf_purge(fn):
    global _cf_purge_scheduled
    try:
        await asyncio.sleep(0.5)
        prefixes = list(set(_cf_purge_prefixes))
        _cf_purge_prefixes.clear()
        result = await fn(prefixes)
        if result:
            logger.info(f"CF edge purge triggered for prefixes: {prefixes}")
        else:
            logger.warning(f"CF edge purge returned false for prefixes: {prefixes}")
        try:
            from cloudflare_client import purge_worker_cache
            await purge_worker_cache(purge_all=True)
        except Exception as we:
            logger.debug(f"Worker cache purge in debounced flow: {we}")
    except Exception as e:
        logger.warning(f"CF edge purge background error: {e}")
    finally:
        _cf_purge_scheduled = False
        if _cf_purge_prefixes:
            _fire_cf_edge_purge(_cf_purge_prefixes.pop(0))

def _set_content_cache(key: str, value):
    _content_cache[key] = value
    if redis_client:
        try:
            redis_client.set(f"{REDIS_CONTENT_PREFIX}{key}", json.dumps(value, default=str), ex=CONTENT_CACHE_SECONDS)
        except Exception:
            pass


_ANON_CONV_PREFIX = "anon_conv:"
_ANON_INDEX_PREFIX = "anon_idx:"
_ANON_MAX_CONVS = 20

def redis_save_anon_conversation(anon_id: str, conv_id: str, conv_data: dict):
    if not redis_client:
        return
    try:
        key = f"{_ANON_CONV_PREFIX}{anon_id}:{conv_id}"
        redis_client.set(key, json.dumps(conv_data, default=str), ex=REDIS_ANON_CONV_TTL)
        idx_key = f"{_ANON_INDEX_PREFIX}{anon_id}"
        redis_client.zadd(idx_key, {conv_id: time.time()})
        redis_client.expire(idx_key, REDIS_ANON_CONV_TTL)
        count = redis_client.zcard(idx_key)
        if count and count > _ANON_MAX_CONVS:
            old_ids = redis_client.zrange(idx_key, 0, count - _ANON_MAX_CONVS - 1)
            for oid in old_ids:
                oid_str = oid if isinstance(oid, str) else oid.decode()
                redis_client.delete(f"{_ANON_CONV_PREFIX}{anon_id}:{oid_str}")
            redis_client.zremrangebyrank(idx_key, 0, count - _ANON_MAX_CONVS - 1)
    except Exception as e:
        logger.warning(f"redis_save_anon_conversation: {e}")

def redis_get_anon_conversation(anon_id: str, conv_id: str) -> Optional[dict]:
    if not redis_client:
        return None
    try:
        val = redis_client.get(f"{_ANON_CONV_PREFIX}{anon_id}:{conv_id}")
        if val:
            return json.loads(val) if isinstance(val, str) else json.loads(val.decode())
    except Exception as e:
        logger.warning(f"redis_get_anon_conversation: {e}")
    return None

def redis_list_anon_conversations(anon_id: str) -> list:
    if not redis_client:
        return []
    try:
        idx_key = f"{_ANON_INDEX_PREFIX}{anon_id}"
        conv_ids = redis_client.zrevrange(idx_key, 0, _ANON_MAX_CONVS - 1)
        results = []
        for cid in conv_ids:
            cid_str = cid if isinstance(cid, str) else cid.decode()
            val = redis_client.get(f"{_ANON_CONV_PREFIX}{anon_id}:{cid_str}")
            if val:
                data = json.loads(val) if isinstance(val, str) else json.loads(val.decode())
                results.append({
                    "id": data.get("id", cid_str),
                    "title": data.get("title", "Untitled"),
                    "preview": data.get("preview", ""),
                    "subject_name": data.get("subject_name", ""),
                    "created_at": data.get("created_at", ""),
                    "updated_at": data.get("updated_at", ""),
                    "message_count": len(data.get("messages", [])),
                })
        return results
    except Exception as e:
        logger.warning(f"redis_list_anon_conversations: {e}")
    return []

def redis_list_all_anon_conversations() -> list:
    if not redis_client:
        return []
    try:
        idx_keys = []
        cursor = 0
        while True:
            cursor, keys = redis_client.scan(cursor, match=f"{_ANON_INDEX_PREFIX}*", count=200)
            idx_keys.extend(keys)
            if cursor == 0:
                break
        results = []
        for idx_key in idx_keys:
            idx_key_str = idx_key if isinstance(idx_key, str) else idx_key.decode()
            anon_id = idx_key_str[len(_ANON_INDEX_PREFIX):]
            conv_ids = redis_client.zrevrange(idx_key_str, 0, _ANON_MAX_CONVS - 1)
            for cid in conv_ids:
                cid_str = cid if isinstance(cid, str) else cid.decode()
                val = redis_client.get(f"{_ANON_CONV_PREFIX}{anon_id}:{cid_str}")
                if val:
                    data = json.loads(val) if isinstance(val, str) else json.loads(val.decode())
                    results.append({
                        "id": data.get("id", cid_str),
                        "title": data.get("title", "Untitled"),
                        "preview": data.get("preview", ""),
                        "subject_name": data.get("subject_name", ""),
                        "created_at": data.get("created_at", ""),
                        "updated_at": data.get("updated_at", ""),
                        "messages": data.get("messages", []),
                        "is_anonymous": True,
                        "anon_id": anon_id,
                        "user_id": None,
                    })
        return results
    except Exception as e:
        logger.warning(f"redis_list_all_anon_conversations: {e}")
    return []

def redis_delete_anon_conversation(anon_id: str, conv_id: str) -> bool:
    if not redis_client:
        return False
    try:
        redis_client.delete(f"{_ANON_CONV_PREFIX}{anon_id}:{conv_id}")
        redis_client.zrem(f"{_ANON_INDEX_PREFIX}{anon_id}", conv_id)
        return True
    except Exception as e:
        logger.warning(f"redis_delete_anon_conversation: {e}")
    return False

