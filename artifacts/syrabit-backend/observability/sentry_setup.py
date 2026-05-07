"""Sentry Developer free-tier init (Task #558).

Sentry is the canonical *errors-only* sink. Tracing is owned by
``../tracing.py`` (GCP Cloud Trace, single exporter). The Developer
free plan ships 5k errors / 0 transactions per month, which fits the
$100/mo perpetual budget (Task #549) without standing up a self-hosted
GlitchTip VM.

Why we picked Sentry-free over GlitchTip self-hosted (the rejected
option, captured in
``docs/architecture/adr/0003-canonical-strict-specialist-delegation.md``):

  * **Zero infra spend.** A Hetzner CX11 + Cloudflare Tunnel + S3
    nightly Postgres dump adds ~$5/mo cash plus a DR runbook to
    maintain. Sentry-free is $0/mo cash and the vendor handles SLA.
  * **Smaller ops surface.** No second container to patch, no
    backup-restore drill, no TLS-tunnel rotation.
  * **Fits the $100/mo cap (Task #549) directly.** The 5k errors/mo
    rate-limit becomes a CI-monitored signal (Sentry inbound-data-filter
    alert at 4k/mo, set in the dashboard) rather than an infra cost.

The decision is reversible: re-pointing the SDK at a self-hosted
GlitchTip DSN is a single env-var swap (``SENTRY_DSN``) — the SDK is
wire-compatible with both back-ends.

Public API:
  * ``init_sentry()`` — idempotent. Reads ``SENTRY_DSN`` /
    ``SENTRY_ENVIRONMENT`` / ``SENTRY_RELEASE``. No-op when DSN is
    empty (dev / smoke).
  * ``get_sentry_health()`` — snapshot for the admin Observability
    card and the ``/api/health/otel`` companion route.
  * ``before_send_filter(event, hint)`` — exported so tests can
    pin the noise-filter contract.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = ["init_sentry", "get_sentry_health", "before_send_filter"]


_INITIALIZED = False
_INIT_DETAILS: dict[str, Any] = {
    "enabled": False,
    "dsn_loaded": False,
    "environment": None,
    "release": None,
    "reason": None,
}


# ─── before_send noise filter ────────────────────────────────────────
# Drops the five categories the task spec lists:
#   1. third-party script errors,
#   2. AbortError from cancelled fetches,
#   3. expected 4xx HTTP responses,
#   4. ResizeObserver loop notifications,
#   5. errors whose stack frames are exclusively in vendored library code.
# Each filter is best-effort — when the event shape is unfamiliar we
# err on the side of letting the event through (better noisy than
# blind).
_THIRD_PARTY_HOSTS = (
    "googletagmanager.com",
    "google-analytics.com",
    "doubleclick.net",
    "googlesyndication.com",
    "facebook.net",
    "connect.facebook.net",
    "hotjar.com",
    "fullstory.com",
    "intercom.io",
)
_ABORT_TYPES = ("AbortError", "asyncio.CancelledError", "CancelledError")
_RESIZE_OBSERVER_NEEDLES = (
    "ResizeObserver loop limit exceeded",
    "ResizeObserver loop completed with undelivered notifications",
)
_VENDOR_PATH_NEEDLES = (
    "/site-packages/",
    "/node_modules/",
    "/dist/vendor/",
)


def _exception_type(event: dict) -> str:
    try:
        values = (event.get("exception") or {}).get("values") or []
        if values:
            return str(values[0].get("type") or "")
    except Exception:
        return ""
    return ""


def _exception_frames(event: dict) -> list[dict]:
    try:
        values = (event.get("exception") or {}).get("values") or []
        if not values:
            return []
        st = values[0].get("stacktrace") or {}
        return list(st.get("frames") or [])
    except Exception:
        return []


def _all_frames_vendored(event: dict) -> bool:
    frames = _exception_frames(event)
    if not frames:
        return False
    for f in frames:
        path = (f.get("abs_path") or f.get("filename") or "")
        if not any(needle in path for needle in _VENDOR_PATH_NEEDLES):
            return False
    return True


def _from_third_party_script(event: dict) -> bool:
    for f in _exception_frames(event):
        path = (f.get("abs_path") or f.get("filename") or "").lower()
        if any(host in path for host in _THIRD_PARTY_HOSTS):
            return True
    return False


def _is_expected_4xx(event: dict) -> bool:
    """Expected client errors (401/403/404/422) carry a status_code
    tag we set in the FastAPI exception handler. Drop them so the
    error budget tracks server-side regressions only."""
    tags = event.get("tags") or {}
    raw = tags.get("status_code") if isinstance(tags, dict) else None
    if raw is None:
        # tags can also arrive as a list of [k, v] pairs in the event payload.
        if isinstance(tags, list):
            for pair in tags:
                if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0] == "status_code":
                    raw = pair[1]
                    break
    if raw is None:
        return False
    try:
        code = int(raw)
    except (TypeError, ValueError):
        return False
    return 400 <= code < 500


def before_send_filter(event: dict, hint: Optional[dict] = None) -> Optional[dict]:
    """Filter the five noise categories. Return ``None`` to drop."""
    try:
        # 4. ResizeObserver loop notifications (browser-only).
        msg = str(event.get("message") or "")
        if any(needle in msg for needle in _RESIZE_OBSERVER_NEEDLES):
            return None
        # 2. AbortError / CancelledError from cancelled fetches.
        if _exception_type(event) in _ABORT_TYPES:
            return None
        # 3. Expected 4xx tagged on the FastAPI exception handler.
        if _is_expected_4xx(event):
            return None
        # 1. Third-party script errors.
        if _from_third_party_script(event):
            return None
        # 5. Stack lives entirely inside vendored library code.
        if _all_frames_vendored(event):
            return None
    except Exception:
        # Never let the filter itself drop a real error.
        return event
    return event


def init_sentry() -> bool:
    """Idempotent. Returns True on successful wire-up; False otherwise.

    Reads:
      SENTRY_DSN          — the project DSN. Empty → init skipped.
      SENTRY_ENVIRONMENT  — defaults DEPLOYMENT_ENV → "production".
      SENTRY_RELEASE      — defaults OTEL_SERVICE_VERSION → "2.0.0".

    Tracing is **disabled by contract**: the call passes
    ``traces_sample_rate=0`` and does not pass ``enable_tracing``. The
    umbrella CI guard
    (``scripts/ci/check_canonical_delegation.py`` Task #558 row) bans
    any other shape.
    """
    global _INITIALIZED, _INIT_DETAILS
    if _INITIALIZED:
        return _INIT_DETAILS.get("enabled", False)
    _INITIALIZED = True

    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        _INIT_DETAILS = {
            "enabled": False,
            "dsn_loaded": False,
            "environment": None,
            "release": None,
            "reason": "SENTRY_DSN not set",
        }
        logger.info("[sentry] disabled (SENTRY_DSN not set)")
        return False

    try:
        import sentry_sdk  # type: ignore
    except Exception as exc:
        _INIT_DETAILS = {
            "enabled": False,
            "dsn_loaded": True,
            "environment": None,
            "release": None,
            "reason": f"sentry-sdk missing: {exc}",
        }
        logger.warning("[sentry] sentry-sdk not installed (%s) — disabled", exc)
        return False

    environment = (
        os.environ.get("SENTRY_ENVIRONMENT")
        or os.environ.get("DEPLOYMENT_ENV")
        or "production"
    )
    release = (
        os.environ.get("SENTRY_RELEASE")
        or os.environ.get("OTEL_SERVICE_VERSION")
        or "2.0.0"
    )

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=release,
            # Errors-only sink. Tracing lives in tracing.py (GCP Cloud Trace).
            traces_sample_rate=0,
            # Profiling is part of the paid Performance product — leave off.
            profiles_sample_rate=0,
            # PII off by default; FastAPI route tags are still attached.
            send_default_pii=False,
            before_send=before_send_filter,
        )
    except Exception as exc:
        _INIT_DETAILS = {
            "enabled": False,
            "dsn_loaded": True,
            "environment": environment,
            "release": release,
            "reason": f"sentry_sdk.init raised: {exc}",
        }
        logger.warning("[sentry] init failed: %s", exc)
        return False

    _INIT_DETAILS = {
        "enabled": True,
        "dsn_loaded": True,
        "environment": environment,
        "release": release,
        "reason": None,
        # Confirms the locked tracing contract for /api/health/otel.
        "traces_sample_rate": 0,
    }
    logger.info(
        "[sentry] initialized environment=%s release=%s (errors-only, traces off)",
        environment, release,
    )
    return True


def get_sentry_health() -> dict[str, Any]:
    """Snapshot for the admin Observability card."""
    return dict(_INIT_DETAILS)
