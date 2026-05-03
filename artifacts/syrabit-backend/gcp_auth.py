"""Shared Google Cloud service-account auth helper.

Loads the SA from GOOGLE_APPLICATION_CREDENTIALS_JSON (preferred) or
GOOGLE_APPLICATION_CREDENTIALS (path), mints OAuth access tokens via
google-auth, and caches the token in-process for ~50 minutes.

Used by cloud_scheduler_client, cloud_tasks_client,
web_security_scanner_client, discovery_engine_client. When the SA is
absent the helpers return None cleanly so callers can return a structured
"disabled" payload instead of raising.

Auth: GOOGLE_APPLICATION_CREDENTIALS_JSON (raw JSON content) or
GOOGLE_APPLICATION_CREDENTIALS (filesystem path to JSON).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_TOKEN_TTL_S = 50 * 60  # google tokens last 60min; refresh slightly early

_lock = threading.Lock()
_refresh_locks: Dict[str, threading.Lock] = {}  # scope -> dedicated refresh lock
_token_cache: Dict[str, Tuple[str, float]] = {}  # scope -> (token, expires_at)
_sa_info_cache: Optional[Dict[str, Any]] = None
_sa_signature: Optional[str] = None  # tracks env to detect runtime changes


def _current_env_signature() -> str:
    """Cheap fingerprint of the env vars we read; lets us detect runtime SA injection."""
    raw = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON") or "")
    path = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "")
    # Use length+hash of raw to avoid keeping the full secret in memory just for compare.
    return f"{len(raw)}:{hash(raw)}|{path}"


def _load_sa_info() -> Optional[Dict[str, Any]]:
    """Load the SA JSON dict from env.

    Cached, but the cache is invalidated whenever the underlying env vars
    change so SA injection at runtime activates the helpers without a
    process restart.
    """
    global _sa_info_cache, _sa_signature
    sig = _current_env_signature()
    if _sa_info_cache is not None and sig == _sa_signature:
        return _sa_info_cache

    with _lock:
        # Recheck inside the lock.
        sig = _current_env_signature()
        if _sa_info_cache is not None and sig == _sa_signature:
            return _sa_info_cache

        loaded: Optional[Dict[str, Any]] = None
        raw = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON") or "").strip()
        if raw:
            try:
                loaded = json.loads(raw)
            except Exception as exc:
                logger.error("gcp_auth: failed to parse GOOGLE_APPLICATION_CREDENTIALS_JSON: %r", exc)
        if loaded is None:
            path = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
            if path and os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                except Exception as exc:
                    logger.error("gcp_auth: failed to read SA file %s: %r", path, exc)

        # Invalidate token cache when SA changes (or disappears).
        if loaded != _sa_info_cache:
            _token_cache.clear()
        _sa_info_cache = loaded
        _sa_signature = sig
        return _sa_info_cache


def is_configured() -> bool:
    return _load_sa_info() is not None


def project_id() -> Optional[str]:
    info = _load_sa_info()
    if info:
        return info.get("project_id")
    return None


def _scope_lock(scope: str) -> threading.Lock:
    with _lock:
        lk = _refresh_locks.get(scope)
        if lk is None:
            lk = threading.Lock()
            _refresh_locks[scope] = lk
        return lk


def get_access_token(scope: str = DEFAULT_SCOPE) -> Optional[str]:
    """Return a cached OAuth access token for `scope`, or None if SA missing.

    Uses a per-scope refresh lock to prevent thundering-herd minting when
    the token expires under concurrent traffic.
    """
    info = _load_sa_info()
    if not info:
        return None

    now = time.time()
    with _lock:
        cached = _token_cache.get(scope)
        if cached and cached[1] > now:
            return cached[0]

    refresh_lock = _scope_lock(scope)
    with refresh_lock:
        # Re-check under the per-scope lock — another waiter may have just refreshed.
        with _lock:
            cached = _token_cache.get(scope)
            if cached and cached[1] > time.time():
                return cached[0]

        try:
            from google.oauth2 import service_account
            import google.auth.transport.requests as gauth_requests

            creds = service_account.Credentials.from_service_account_info(
                info, scopes=[scope]
            )
            creds.refresh(gauth_requests.Request())
            token = creds.token
            if not token:
                logger.warning("gcp_auth: refresh returned empty token for scope=%s", scope)
                return None
            # Use creds.expiry when available (UTC naive datetime) minus 5min safety,
            # else fall back to a fixed TTL.
            expires_at: float
            try:
                if getattr(creds, "expiry", None):
                    from datetime import timezone as _tz, datetime as _dt
                    exp = creds.expiry
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=_tz.utc)
                    expires_at = exp.timestamp() - 300.0
                else:
                    expires_at = time.time() + _TOKEN_TTL_S
            except Exception:
                expires_at = time.time() + _TOKEN_TTL_S
            with _lock:
                _token_cache[scope] = (token, expires_at)
            return token
        except Exception as exc:
            logger.error("gcp_auth: token mint failed for scope=%s: %r", scope, exc)
            return None


def auth_header(scope: str = DEFAULT_SCOPE) -> Optional[Dict[str, str]]:
    """Return an Authorization header dict, or None if SA missing/invalid."""
    tok = get_access_token(scope)
    if not tok:
        return None
    return {"Authorization": f"Bearer {tok}"}


def disabled_payload(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Standard payload returned by SA-gated clients when no SA is configured."""
    out: Dict[str, Any] = {
        "status": "disabled",
        "error": (
            "GOOGLE_APPLICATION_CREDENTIALS_JSON (or GOOGLE_APPLICATION_CREDENTIALS) "
            "not configured. Provide a service-account JSON to enable this endpoint."
        ),
    }
    if extra:
        out.update(extra)
    return out
