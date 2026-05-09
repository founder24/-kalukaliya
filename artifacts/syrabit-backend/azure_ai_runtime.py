"""Runtime feature-flag helpers for the ten Azure-native AI services.

Phase 5b — Task #338. Real chains (LLM dispatcher, translator,
content-safety filter, RAG retriever switch, recommender) call
``is_enabled(feature_key)`` to decide whether to attempt the
Azure path or skip straight to the next provider in the chain.

Persistence
-----------
Toggles live in the Mongo collection ``azure_ai_settings``,
keyed by the ``settingKey`` documented in
``routes/admin_azure_ai.FEATURES``. Documents look like::

    {"key": "azure.openai.enabled", "value": false,
     "updatedAt": "2026-05-04T10:30:00+00:00"}

For string-valued toggles (``rag.retriever``,
``recs.next_quiz_provider``) ``value`` is the literal string and
``is_enabled`` compares it against the feature's ``settingValueOn``.

Cache
-----
Reads are cached for ``_TTL_SECONDS`` to keep the hot translation
path off Mongo on every request. The admin toggle endpoint calls
``invalidate()`` after a successful write so a flip propagates
within the next request, not on the next TTL boundary.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_TTL_SECONDS = 30.0
_lock = asyncio.Lock()
_cache: dict[str, tuple[float, Any]] = {}


def _feature(key: str) -> dict[str, Any] | None:
    # Imported lazily so this module can be used from request paths
    # that are imported before routes/ — avoids a circular import.
    from routes.admin_azure_ai import _feature as _lookup  # type: ignore

    return _lookup(key)


async def _read_raw(setting_key: str) -> Any:
    now = time.monotonic()
    cached = _cache.get(setting_key)
    if cached and (now - cached[0]) < _TTL_SECONDS:
        return cached[1]

    async with _lock:
        cached = _cache.get(setting_key)
        if cached and (time.monotonic() - cached[0]) < _TTL_SECONDS:
            return cached[1]

        value: Any = None
        try:
            from deps import db  # type: ignore

            doc = await db.azure_ai_settings.find_one({"key": setting_key})
            if doc is not None:
                value = doc.get("value")
        except Exception as exc:
            logger.debug("azure_ai_runtime: read %s skipped: %s", setting_key, exc)
            value = None

        _cache[setting_key] = (time.monotonic(), value)
        return value


async def is_enabled(feature_key: str) -> bool:
    """Return True when the Azure-native path for ``feature_key`` is on.

    Defaults to ``True`` when no row exists yet — features ship
    enabled and only flip off via the admin panel.
    """
    feature = _feature(feature_key)
    if feature is None:
        return True

    raw = await _read_raw(feature["settingKey"])
    if raw is None:
        return True
    if "settingValueOn" in feature:
        return str(raw).lower() == str(feature["settingValueOn"]).lower()
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in ("0", "false", "off", "no", "")


def is_enabled_sync(feature_key: str, default: bool = True) -> bool:
    """Synchronous probe for non-async paths.

    Reads the cache only — never touches Mongo from a sync context.
    Returns ``default`` when the cache is cold; the next async hit
    will populate it. Used by hot-path translator dispatch which
    runs inside ``asyncio.run`` from worker threads in some
    cron jobs.
    """
    feature = _feature(feature_key)
    if feature is None:
        return default
    cached = _cache.get(feature["settingKey"])
    if cached is None:
        return default
    raw = cached[1]
    if raw is None:
        return default
    if "settingValueOn" in feature:
        return str(raw).lower() == str(feature["settingValueOn"]).lower()
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in ("0", "false", "off", "no", "")


def invalidate(setting_key: str | None = None) -> None:
    if setting_key is None:
        _cache.clear()
    else:
        _cache.pop(setting_key, None)


def _seed_for_tests(setting_key: str, value: Any) -> None:
    _cache[setting_key] = (time.monotonic(), value)
