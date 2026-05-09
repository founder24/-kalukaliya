"""PostHog server-side capture (Task #2 follow-up — backend analytics).

Companion to the LCP-gated browser SDK in ``artifacts/syrabit/index.html``
(the ``deferPosthog`` block at line ~430). The browser snippet handles
page-views + UI interaction events; this module adds server-side capture
for events the browser can't reliably observe:

  * Razorpay webhook -> ``purchase_verified`` (the browser's
    ``purchaseComplete`` fires before the webhook, so server-side is
    the source of truth for revenue).
  * Voice paywall hits, OCR completions, RAG fallbacks — all emitted
    from FastAPI without a browser round-trip.
  * Background ACA jobs (translate backfill, embed backfill) where
    no browser is in the loop at all.

Init contract (mirrors ``observability.sentry_setup``):
  * Reads ``POSTHOG_API_KEY`` (project key, ``phc_…``) and
    ``POSTHOG_HOST`` (``https://us.i.posthog.com`` or eu.* equivalent).
  * No-op when either env var is unset — every public function returns
    cleanly so call sites don't need to guard.
  * Idempotent. Safe to call from server.py startup AND from worker
    contexts that re-import the module.

V4 §12 (no silent fallbacks): we surface init outcome via
``get_posthog_health()`` so the admin Ops Console can show
``available=False`` instead of pretending events are landing when they
aren't.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = ["init_posthog", "capture", "identify", "shutdown_posthog", "get_posthog_health"]

_INITIALIZED: bool = False
_CLIENT: Optional[Any] = None
_INIT_DETAILS: dict[str, Any] = {
    "enabled": False,
    "host": None,
    "reason": None,
}


def init_posthog() -> bool:
    """Initialize the PostHog client. Returns True if active, False otherwise."""
    global _INITIALIZED, _CLIENT

    if _INITIALIZED:
        return _INIT_DETAILS["enabled"]

    api_key = (os.environ.get("POSTHOG_API_KEY") or "").strip()
    host = (os.environ.get("POSTHOG_HOST") or "").strip()

    if not api_key:
        _INIT_DETAILS["reason"] = "POSTHOG_API_KEY unset"
        _INITIALIZED = True
        logger.info("[posthog] disabled — POSTHOG_API_KEY not set (browser SDK still active)")
        return False

    if not host:
        host = "https://us.i.posthog.com"
        logger.info("[posthog] POSTHOG_HOST unset — defaulting to https://us.i.posthog.com")

    try:
        from posthog import Posthog  # type: ignore
    except ImportError:
        _INIT_DETAILS["reason"] = "posthog package not installed"
        _INITIALIZED = True
        logger.warning("[posthog] disabled — posthog package not installed")
        return False

    try:
        _CLIENT = Posthog(
            project_api_key=api_key,
            host=host,
            # Educational workload: ~hundreds of events/min peak. Default
            # flush_at=100 / flush_interval=0.5s is fine, but raise the
            # interval slightly so we batch more aggressively under low
            # load (free-tier event budget conservation).
            flush_interval=2.0,
            # Errors-only logging — we don't want PostHog client INFO logs
            # cluttering the JSON log stream.
            debug=False,
        )
        _INIT_DETAILS.update({"enabled": True, "host": host, "reason": None})
        _INITIALIZED = True
        logger.info(f"[posthog] server-side capture ready (host={host})")
        return True
    except Exception as e:
        _INIT_DETAILS["reason"] = f"client init failed: {e!r}"
        _INITIALIZED = True
        logger.warning(f"[posthog] init failed (non-fatal): {e!r}")
        return False


def capture(distinct_id: str, event: str, properties: Optional[dict] = None) -> None:
    """Fire-and-forget event capture. No-op when client is unavailable."""
    if not _CLIENT:
        return
    try:
        payload = {"app": "syrabit.ai", "source": "backend", **(properties or {})}
        _CLIENT.capture(distinct_id=distinct_id, event=event, properties=payload)
    except Exception as e:
        # Never let analytics break a request path.
        logger.debug(f"[posthog] capture('{event}') swallowed: {e!r}")


def identify(distinct_id: str, properties: Optional[dict] = None) -> None:
    """Identify a user (server-side, e.g. after Razorpay verification)."""
    if not _CLIENT:
        return
    try:
        _CLIENT.identify(distinct_id=distinct_id, properties=properties or {})
    except Exception as e:
        logger.debug(f"[posthog] identify swallowed: {e!r}")


def shutdown_posthog() -> None:
    """Flush pending events. Call from app shutdown handler."""
    global _CLIENT
    if not _CLIENT:
        return
    try:
        _CLIENT.shutdown()
    except Exception as e:
        logger.debug(f"[posthog] shutdown swallowed: {e!r}")
    _CLIENT = None


def get_posthog_health() -> dict[str, Any]:
    """Snapshot for the admin Ops Console / `/api/health/observability`."""
    return {
        "available": bool(_INIT_DETAILS["enabled"]),
        "host": _INIT_DETAILS["host"],
        "reason": _INIT_DETAILS["reason"],
        "initialized": _INITIALIZED,
    }
