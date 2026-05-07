"""ai_input_cache — Task #513 §K.2 deterministic-input AI response cache.

A small cache that short-circuits the LLM dispatch for *deterministic*
inputs — i.e. the input is fully captured by a stable hash of the
(model, messages, max_tokens, temperature=0) tuple. Streaming responses,
random-seeded responses, RAG contexts that include a wall-clock
timestamp, and anything that varies per-user are NEVER cached.

Two storage tiers:

  1. In-process LRU (`functools.lru_cache`-style) — sub-millisecond hit;
     gone on pod restart. Used for hot duplicates within a single
     request burst (admin batch jobs frequently re-emit the same prompt
     dozens of times in <1 s).
  2. Redis (`deps.redis_client`) — TTL-bound (default 24 h) shared
     cache across pods. Round-trip is a single GETEX; on miss the
     dispatcher proceeds normally and writes the result back via
     `set_response`.

The cache key is `aic:v1:<model>:<sha256(canonical_json(messages))>`.
Canonical JSON sorts keys + uses `(",", ":")` separators so the same
logical message produces the same key regardless of dict construction
order.

Public API:
    get_response(messages, model, *, max_tokens=None) -> str | None
    set_response(messages, model, text, *, max_tokens=None, ttl=86400) -> None
    is_deterministic(messages, model, *, temperature=None, stream=False) -> bool

Importantly: this module is OPT-IN. The dispatcher must explicitly
guard the call site with `is_deterministic(...)`; otherwise we risk
serving a cached response across users. Sites where opt-in is safe
include the admin chapter pre-gen pipeline and the
content_formatter polish path (both are pure functions of the input
text + style + lang).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Any, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

_REDIS_KEY_PREFIX = "ai_response_cache:v1"
_DEFAULT_TTL_SEC = 30 * 24 * 60 * 60
_INPROC_MAX = 2_048
_INPROC_LOCK = threading.Lock()
_INPROC: "OrderedDict[str, str]" = OrderedDict()


def _canonical_messages(messages: Iterable[Mapping], *, normalize_text: bool = False) -> str:
    """Canonicalize the messages list to a stable JSON string.

    When `normalize_text=True` (Task #571), every textual content payload
    is run through `prompt_normalizer.normalize` before serialization so
    cosmetically different prompts (case, punctuation, "what is X" vs
    "define X") collapse to a single cache key.
    """
    payload = []
    norm_fn = None
    if normalize_text:
        try:
            from prompt_normalizer import normalize as _norm
            norm_fn = _norm
        except Exception:
            norm_fn = None
    for m in messages:
        role = str(m.get("role", "user"))
        content = m.get("content", "")
        if isinstance(content, list):
            content = json.dumps(content, sort_keys=True, separators=(",", ":"))
        s = str(content)
        if norm_fn is not None and role in ("user", "system"):
            s = norm_fn(s)
        payload.append({"role": role, "content": s})
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _key(
    messages: Iterable[Mapping],
    model: str,
    *,
    max_tokens: Optional[int],
    normalize_text: bool = False,
    template_version: str = "",
) -> str:
    canon = _canonical_messages(messages, normalize_text=normalize_text)
    # `template_version` is folded into the key so a prompt-template bump
    # invalidates the cache without touching the messages payload — the
    # `template_version_bump` miss-reason below recognises this case.
    digest = hashlib.sha256(
        f"{model}|{template_version}|{max_tokens or ''}|{canon}".encode("utf-8")
    ).hexdigest()
    return f"{_REDIS_KEY_PREFIX}:{model}:{digest}"


# ── Task #571 — per-content-type counters + miss-reason tagging ───────
# Tracked entirely in-process. The nightly Lambda
# `lambda_batch.cache_effectiveness` scrapes `/api/health/cache` (which
# calls `snapshot()` below) and emits the rolled-up numbers to the
# `Syrabit/Cache` CloudWatch namespace where the hit-ratio + cardinality
# alarms live.
_KNOWN_CONTENT_TYPES = (
    "mcq", "flashcard", "definition", "pyq", "formatter",
    "translate", "ocr", "stage3_polish", "unknown",
)
_MISS_REASONS = (
    "normalization_mismatch",
    "template_version_bump",
    "ttl_expiry",
    "uncached_content_type",
    "cold",
)


# Task #571 round-6 — per-tier hit/miss accounting. The §K.2 cache has
# THREE storage tiers (in-process LRU → Cloudflare KV → Redis) and an
# operator needs to see WHICH tier is leaking. Without a per-tier
# breakdown a 0.4 hit-ratio could mean "KV is broken so we always fall
# through to Redis" or "Redis is broken and KV is doing all the work" —
# both look identical at the rolled-up level.
_TIERS = ("inproc", "cf_kv", "redis")


def _empty_ct_counters() -> dict:
    return {
        "hits": 0, "misses": 0, "sets": 0,
        # Lifetime miss-reason counters (kept for backwards compat with
        # existing tests + dashboards). The 24h-windowed view used by the
        # new "Top miss reasons (24h)" tile lives in `miss_reasons_24h`
        # below — a ring of (epoch_seconds, reason) tuples that the
        # snapshot rolls into a ranked aggregate.
        "miss_reasons": {r: 0 for r in _MISS_REASONS},
        "miss_reasons_24h": [],  # ring of (epoch_seconds, reason)
        "unique_keys_24h": [],  # ring of (epoch_seconds, key) for cardinality
        # Per-tier breakdown of `hits` above. `tier_hits["inproc"] +
        # tier_hits["cf_kv"] + tier_hits["redis"] == hits` is an invariant
        # asserted in tests/test_ai_input_cache_metrics.py.
        "tier_hits": {t: 0 for t in _TIERS},
        # Per-tier set-fanout counters. `inproc_sets` always equals `sets`
        # (every set lands in-proc); `cf_kv_sets` / `redis_sets` only
        # increment when the respective tier is actually configured (gated
        # on _CF_KV_ENABLED / `_redis_client() is not None`). A 0 here vs
        # nonzero `sets` is a config-leak signal.
        "tier_sets": {t: 0 for t in _TIERS},
    }


_COUNTERS_LOCK = threading.Lock()
_COUNTERS: dict[str, dict] = {ct: _empty_ct_counters() for ct in _KNOWN_CONTENT_TYPES}
# Recently-set keys (key -> epoch_seconds set) used to attribute
# `ttl_expiry` vs `cold`. Bounded ring; older entries fall out.
_RECENT_SETS: "OrderedDict[str, float]" = OrderedDict()
_RECENT_SETS_MAX = 8_192
# Last-seen template version per content_type. A miss whose
# template_version differs from the last set's is `template_version_bump`.
_LAST_TEMPLATE_VERSION: dict[str, str] = {}


def _bump_unique_key(ct: str, key: str) -> None:
    now = time.time()
    cutoff = now - 86_400.0
    bucket = _COUNTERS[ct]["unique_keys_24h"]
    bucket.append((now, key))
    # Drop entries older than 24 h. Cheap because the list is append-only
    # and the prefix is monotonically aged.
    while bucket and bucket[0][0] < cutoff:
        bucket.pop(0)


# ── Round-8: fleet-wide 24h rolling hit/miss aggregation ────────────────
# Per-process counters are not fleet-representative AND a HitRatio alarm
# computed off lifetime totals can mask a fresh regression. Fix: every
# get_response hit/miss writes a single INCR into Redis under an hourly
# bucket key with a 25h TTL. The snapshot endpoint reads back the last
# 24 buckets and reports `hits_24h` / `misses_24h` / `hit_ratio_24h`
# per content-type AND in totals. Best-effort — if Redis is down the
# 24h fields fall back to 0/0/0.0 (the lifetime fields are unaffected).
_HR24_PREFIX = "aic:hr24"
_HR24_TTL_SEC = 25 * 3600


def _hr24_bucket() -> int:
    """Current UTC hour bucket (epoch hour)."""
    return int(time.time()) // 3600


def _record_24h_event(ct: str, kind: str) -> None:
    """Best-effort Redis INCR for a single hit/miss event. `kind` is
    one of "hits" | "misses". Failures are swallowed — the cache hot
    path must never raise from telemetry."""
    rc = _redis_client()
    if rc is None:
        return
    try:
        bucket = _hr24_bucket()
        key = f"{_HR24_PREFIX}:{ct}:{bucket}:{kind}"
        rc.incr(key)
        # Pipeline-style would be better but the redis-py / upstash
        # clients have inconsistent pipeline support; a separate EXPIRE
        # is safe (idempotent).
        try:
            rc.expire(key, _HR24_TTL_SEC)
        except Exception:
            pass
    except Exception as e:
        logger.debug("[ai_input_cache] hr24 record failed: %s", e)


def _read_24h_totals() -> dict:
    """Return `{ct: {hits, misses}}` summed over the last 24 buckets.
    Returns `{}` on Redis outage — caller falls back to lifetime
    counters with explicit `_24h: 0` fields so the alarm cannot
    silently invert direction."""
    rc = _redis_client()
    if rc is None:
        return {}
    out: dict[str, dict[str, int]] = {}
    try:
        now_b = _hr24_bucket()
        buckets = [now_b - i for i in range(24)]
        for ct in _KNOWN_CONTENT_TYPES:
            h_total = 0
            m_total = 0
            for b in buckets:
                try:
                    rh = rc.get(f"{_HR24_PREFIX}:{ct}:{b}:hits")
                    rm = rc.get(f"{_HR24_PREFIX}:{ct}:{b}:misses")
                except Exception:
                    rh = rm = None
                if rh:
                    try:
                        h_total += int(rh.decode() if isinstance(rh, (bytes, bytearray)) else rh)
                    except Exception:
                        pass
                if rm:
                    try:
                        m_total += int(rm.decode() if isinstance(rm, (bytes, bytearray)) else rm)
                    except Exception:
                        pass
            out[ct] = {"hits": h_total, "misses": m_total}
    except Exception as e:
        logger.debug("[ai_input_cache] hr24 read failed: %s", e)
        return {}
    return out


def _bump_miss_reason_24h(ct: str, reason: str) -> None:
    """Append a (now, reason) tuple to the per-CT 24h ring + age out
    entries older than 24 h. Round-7 fix — the lifetime `miss_reasons`
    counter cannot age out a stale dominator (e.g. a one-off
    `template_version_bump` flood after a deploy keeps the panel
    showing that reason as #1 for weeks). The 24h window is what the
    `Top miss reasons (24h)` tile and the
    `cache-effectiveness` Lambda actually consume."""
    now = time.time()
    cutoff = now - 86_400.0
    bucket = _COUNTERS[ct]["miss_reasons_24h"]
    bucket.append((now, reason))
    while bucket and bucket[0][0] < cutoff:
        bucket.pop(0)


def _classify_miss(
    *,
    ct: str,
    key: str,
    normalized_key: str,
    template_version: str,
) -> str:
    if ct == "unknown":
        return "uncached_content_type"
    if key in _RECENT_SETS:
        # We wrote this key recently and Redis returned None → TTL expired.
        return "ttl_expiry"
    last_tv = _LAST_TEMPLATE_VERSION.get(ct, "")
    if template_version and last_tv and template_version != last_tv:
        return "template_version_bump"
    if normalized_key and normalized_key != key and normalized_key in _RECENT_SETS:
        return "normalization_mismatch"
    return "cold"


def snapshot() -> dict:
    """Return a JSON-safe snapshot of the per-content-type counters.

    Consumed by `/api/health/cache` (admin-only) and by the nightly
    `lambda_batch.cache_effectiveness` shipper.
    """
    # Read fleet-wide 24h totals from Redis OUTSIDE the lock — Redis
    # round-trips can take milliseconds and we don't want to block
    # writers. Round-8 — addresses the architect's "per-process, not
    # fleet-wide" finding by reading shared hourly buckets.
    hr24 = _read_24h_totals()
    with _COUNTERS_LOCK:
        out: dict = {"content_types": {}, "totals": {"hits": 0, "misses": 0, "sets": 0}}
        for ct, c in _COUNTERS.items():
            total = c["hits"] + c["misses"]
            hr = round(c["hits"] / total, 4) if total else 0.0
            tier_hits = dict(c.get("tier_hits") or {t: 0 for t in _TIERS})
            tier_sets = dict(c.get("tier_sets") or {t: 0 for t in _TIERS})
            # Round-7 — roll the 24h ring into a per-reason count.
            # Age-out happens on each append, but a snapshot taken hours
            # after the last miss could still hold expired entries; do
            # one cheap defensive sweep here.
            now_local = time.time()
            cutoff_local = now_local - 86_400.0
            ring = c.get("miss_reasons_24h") or []
            mr24: dict[str, int] = {r: 0 for r in _MISS_REASONS}
            for ts, reason in ring:
                if ts >= cutoff_local and reason in mr24:
                    mr24[reason] += 1
            # Round-8 — fleet-wide 24h rolling counters from Redis.
            # `hits_24h + misses_24h` is the rolling 24h volume and
            # `hit_ratio_24h` is the alarm-grade signal (NOT the
            # lifetime ratio).
            ct_hr24 = hr24.get(ct) or {"hits": 0, "misses": 0}
            h24 = int(ct_hr24.get("hits", 0))
            m24 = int(ct_hr24.get("misses", 0))
            t24 = h24 + m24
            hr24_ratio = round(h24 / t24, 4) if t24 else 0.0
            out["content_types"][ct] = {
                "hits": c["hits"],
                "misses": c["misses"],
                "sets": c["sets"],
                "hit_ratio": hr,
                "hits_24h": h24,
                "misses_24h": m24,
                "hit_ratio_24h": hr24_ratio,
                "unique_keys_24h": len({k for _, k in c["unique_keys_24h"]}),
                "miss_reasons": dict(c["miss_reasons"]),
                "miss_reasons_24h": mr24,
                "tier_hits": tier_hits,
                "tier_sets": tier_sets,
            }
            out["totals"]["hits"] += c["hits"]
            out["totals"]["misses"] += c["misses"]
            out["totals"]["sets"] += c["sets"]
        gt = out["totals"]["hits"] + out["totals"]["misses"]
        out["totals"]["hit_ratio"] = round(out["totals"]["hits"] / gt, 4) if gt else 0.0
        # Round-8 — totals.hit_ratio_24h is what the alarm uses.
        h24_total = sum(int(e.get("hits_24h", 0)) for e in out["content_types"].values())
        m24_total = sum(int(e.get("misses_24h", 0)) for e in out["content_types"].values())
        t24_total = h24_total + m24_total
        out["totals"]["hits_24h"] = h24_total
        out["totals"]["misses_24h"] = m24_total
        out["totals"]["hit_ratio_24h"] = round(h24_total / t24_total, 4) if t24_total else 0.0
        out["totals"]["hr24_source"] = "redis_hourly_buckets" if hr24 else "redis_unavailable"
        out["totals"]["unique_keys_24h"] = sum(
            entry["unique_keys_24h"] for entry in out["content_types"].values()
        )
        # Per-tier rollup so the panel + CloudWatch alarms can show
        # "where is the cache hitting" without summing across CTs.
        out["totals"]["tier_hits"] = {
            t: sum(entry["tier_hits"].get(t, 0)
                   for entry in out["content_types"].values())
            for t in _TIERS
        }
        out["totals"]["tier_sets"] = {
            t: sum(entry["tier_sets"].get(t, 0)
                   for entry in out["content_types"].values())
            for t in _TIERS
        }
        out["tier_config"] = {
            "inproc_enabled": True,
            "cf_kv_enabled": bool(_CF_KV_ENABLED),
            "redis_enabled": _redis_client() is not None,
        }
        # Round-7 — global 24h miss-reason rollup, ranked desc. Drives
        # the new "Top miss reasons (24h)" tile in the admin panel and
        # the per-reason CW alarm thresholds in
        # lambda_batch.cache_effectiveness. Lifetime counters cannot
        # detect "regression in the last day" because a one-off flood
        # would dominate them forever.
        agg: dict[str, int] = {r: 0 for r in _MISS_REASONS}
        for entry in out["content_types"].values():
            for r, n in (entry.get("miss_reasons_24h") or {}).items():
                agg[r] = agg.get(r, 0) + int(n)
        out["totals"]["miss_reasons_24h"] = agg
        out["totals"]["top_miss_reasons_24h"] = [
            {"reason": r, "count": n}
            for r, n in sorted(agg.items(), key=lambda x: x[1], reverse=True)
            if n > 0
        ]
    return out


def reset_for_tests() -> None:
    """Test-only — wipe in-process counters and recent-sets ring."""
    with _COUNTERS_LOCK:
        for ct in _KNOWN_CONTENT_TYPES:
            _COUNTERS[ct] = _empty_ct_counters()
        _RECENT_SETS.clear()
        _LAST_TEMPLATE_VERSION.clear()
        _INPROC.clear()


def is_deterministic(
    messages: Iterable[Mapping],
    model: str,
    *,
    temperature: Optional[float] = None,
    stream: bool = False,
) -> bool:
    """Return True iff this call is safe to cache.

    Conservative — bails out on streaming, on any non-zero temperature,
    on missing model id, and on empty message lists (which usually
    indicate the caller is still constructing the request).
    """
    if not model or not messages:
        return False
    if stream:
        return False
    if temperature is not None and float(temperature) > 0.0:
        return False
    return True


def _inproc_get(key: str) -> Optional[str]:
    with _INPROC_LOCK:
        if key in _INPROC:
            _INPROC.move_to_end(key)
            return _INPROC[key]
    return None


def _inproc_set(key: str, value: str) -> None:
    with _INPROC_LOCK:
        _INPROC[key] = value
        _INPROC.move_to_end(key)
        while len(_INPROC) > _INPROC_MAX:
            _INPROC.popitem(last=False)


def _redis_client():  # pragma: no cover — wires to deps at call time
    try:
        import deps
        return getattr(deps, "redis_client", None)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────
# §K.2 Cloudflare KV tier — `ai_response_cache` namespace, 30-day TTL.
#
# This is the canonical storage tier per V4 §K.2: the SAME KV namespace
# the edge worker binds as `AI_RESPONSE_CACHE` so cache-hits served from
# the worker (e.g. /api/edu/study/explain repeats from a logged-out
# crawler) and cache-hits served from the backend share a single key
# space. Backend writes go through the Cloudflare KV REST API (no SDK
# dependency) — the worker reads/writes directly via its KV binding.
#
# Required env: CF_ACCOUNT_ID, CLOUDFLARE_API_TOKEN (or CF_API_TOKEN),
# AI_RESPONSE_CACHE_KV_ID (the namespace id from wrangler.toml). Absent
# any of these, the KV tier is silently disabled and we degrade to
# Redis-only — never raise from the cache hot path.
# ─────────────────────────────────────────────────────────────────────────
_CF_ACCOUNT_ID    = os.environ.get("CF_ACCOUNT_ID") or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
_CF_API_TOKEN     = os.environ.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CF_API_TOKEN")
_CF_KV_NAMESPACE  = os.environ.get("AI_RESPONSE_CACHE_KV_ID")
_CF_KV_ENABLED    = bool(_CF_ACCOUNT_ID and _CF_API_TOKEN and _CF_KV_NAMESPACE)


def _cf_kv_url(key: str) -> str:
    return (
        f"https://api.cloudflare.com/client/v4/accounts/{_CF_ACCOUNT_ID}"
        f"/storage/kv/namespaces/{_CF_KV_NAMESPACE}/values/{key}"
    )


def _cf_kv_get(key: str) -> Optional[str]:
    if not _CF_KV_ENABLED:
        return None
    try:
        import urllib.request as _ur
        req = _ur.Request(
            _cf_kv_url(key),
            method="GET",
            headers={"Authorization": f"Bearer {_CF_API_TOKEN}"},
        )
        with _ur.urlopen(req, timeout=2.0) as resp:
            if resp.status != 200:
                return None
            body = resp.read()
            return body.decode("utf-8") if body else None
    except Exception as e:
        logger.debug("[ai_input_cache] cf-kv get miss/err: %s", e)
        return None


def _cf_kv_set(key: str, value: str, ttl: int) -> None:
    if not _CF_KV_ENABLED or not value:
        return
    try:
        import urllib.request as _ur
        # CF KV PUT honours `expiration_ttl` as a query-string param.
        url = f"{_cf_kv_url(key)}?expiration_ttl={int(ttl)}"
        req = _ur.Request(
            url, method="PUT", data=value.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {_CF_API_TOKEN}",
                "Content-Type":  "text/plain; charset=utf-8",
            },
        )
        with _ur.urlopen(req, timeout=2.0) as resp:
            _ = resp.read()
    except Exception as e:
        logger.debug("[ai_input_cache] cf-kv put err: %s", e)


def get_response(
    messages: Iterable[Mapping],
    model: str,
    *,
    max_tokens: Optional[int] = None,
    content_type: Optional[str] = None,
    template_version: str = "",
    normalize_text: bool = False,
) -> Optional[str]:
    """Return the cached completion for this (model, messages,
    max_tokens) tuple, or None on miss. Never raises.

    Task #571 additions: callers may pass `content_type`
    (`mcq` / `flashcard` / `definition` / `formatter` / ...),
    `template_version` (folded into the key for safe rotation), and
    `normalize_text=True` to apply prompt-normalization. The miss path
    bumps a per-content-type miss-reason counter exposed via
    `snapshot()` and surfaced through `/api/health/cache`.
    """
    msgs = list(messages)
    ct = content_type if content_type in _KNOWN_CONTENT_TYPES else "unknown"
    key = _key(msgs, model, max_tokens=max_tokens,
               normalize_text=normalize_text, template_version=template_version)
    with _COUNTERS_LOCK:
        _bump_unique_key(ct, key)
    val = _inproc_get(key)
    if val is not None:
        with _COUNTERS_LOCK:
            _COUNTERS[ct]["hits"] += 1
            _COUNTERS[ct]["tier_hits"]["inproc"] += 1
        _record_24h_event(ct, "hits")
        return val
    # Tier 2: Cloudflare KV (canonical per V4 §K.2 — same namespace
    # the edge worker reads from). Tried before Redis so a worker-
    # written entry is honoured even if the backend Redis is cold.
    cf_val = _cf_kv_get(key)
    if cf_val:
        _inproc_set(key, cf_val)
        with _COUNTERS_LOCK:
            _COUNTERS[ct]["hits"] += 1
            _COUNTERS[ct]["tier_hits"]["cf_kv"] += 1
        _record_24h_event(ct, "hits")
        return cf_val
    rc = _redis_client()
    if rc is not None:
        try:
            raw = rc.get(key)
            if raw:
                text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
                _inproc_set(key, text)
                with _COUNTERS_LOCK:
                    _COUNTERS[ct]["hits"] += 1
                    _COUNTERS[ct]["tier_hits"]["redis"] += 1
                _record_24h_event(ct, "hits")
                return text
        except Exception as e:
            logger.debug("[ai_input_cache] redis get failed: %s", e)
    # Genuine miss — attribute it to a reason and bump the counter.
    # For normalization-mismatch detection we also compute what the
    # key WOULD have been with normalize_text flipped, so an unnormalized
    # caller can be told a normalized peer is already cached.
    normalized_key = ""
    try:
        if not normalize_text:
            normalized_key = _key(msgs, model, max_tokens=max_tokens,
                                  normalize_text=True,
                                  template_version=template_version)
    except Exception:
        pass
    reason = _classify_miss(
        ct=ct, key=key, normalized_key=normalized_key,
        template_version=template_version,
    )
    with _COUNTERS_LOCK:
        _COUNTERS[ct]["misses"] += 1
        _COUNTERS[ct]["miss_reasons"][reason] += 1
        _bump_miss_reason_24h(ct, reason)
    _record_24h_event(ct, "misses")
    return None


def set_response(
    messages: Iterable[Mapping],
    model: str,
    text: str,
    *,
    max_tokens: Optional[int] = None,
    ttl: Optional[int] = None,
    content_type: Optional[str] = None,
    template_version: str = "",
    normalize_text: bool = False,
) -> None:
    """Store the completion for this (model, messages) tuple.

    Task #571 additions: `content_type`, `template_version`, and
    `normalize_text` mirror `get_response` so writes land on the same
    canonical key the next read will compute. The recently-set ring is
    bumped so a subsequent miss for the same key gets attributed to
    `ttl_expiry` rather than `cold`.

    Task #575 additions: `ttl` is now optional — when omitted we pick
    the season-aware default via `cache_calendar.ai_cache_ttl_for`,
    which stretches the TTL to 90 days for the deterministic
    exam-relevant content types (mcq / flashcard / definition / pyq)
    during AHSEC + SEBA exam / results windows. Formatter / translate
    / OCR keep the 30-day default. Callers that pass `ttl` explicitly
    bypass the calendar entirely.
    """
    if not text:
        return
    msgs = list(messages)
    ct = content_type if content_type in _KNOWN_CONTENT_TYPES else "unknown"
    if ttl is None:
        try:
            from cache_calendar import ai_cache_ttl_for as _aic_ttl
            ttl = _aic_ttl(ct)
        except Exception:
            ttl = _DEFAULT_TTL_SEC
    key = _key(msgs, model, max_tokens=max_tokens,
               normalize_text=normalize_text, template_version=template_version)
    _inproc_set(key, text)
    with _COUNTERS_LOCK:
        _COUNTERS[ct]["sets"] += 1
        _COUNTERS[ct]["tier_sets"]["inproc"] += 1
        _RECENT_SETS[key] = time.time()
        _RECENT_SETS.move_to_end(key)
        while len(_RECENT_SETS) > _RECENT_SETS_MAX:
            _RECENT_SETS.popitem(last=False)
        if template_version:
            _LAST_TEMPLATE_VERSION[ct] = template_version
    # §K.2 fan-out write: CF KV (canonical) + Redis (fast pod-local).
    # Both are best-effort and never raise from the cache hot path.
    _cf_kv_set(key, text, int(ttl))
    if _CF_KV_ENABLED:
        with _COUNTERS_LOCK:
            _COUNTERS[ct]["tier_sets"]["cf_kv"] += 1
    rc = _redis_client()
    if rc is None:
        return
    try:
        rc.set(key, text, ex=int(ttl))
        with _COUNTERS_LOCK:
            _COUNTERS[ct]["tier_sets"]["redis"] += 1
    except Exception as e:
        logger.debug("[ai_input_cache] redis set failed: %s", e)


__all__ = [
    "get_response", "set_response", "is_deterministic",
    "snapshot", "reset_for_tests",
]
