"""Supabase JWKS-based local JWT verifier.

Task #47 (prep) — splits the cutover into two PRs:
  * THIS PR (prep, safe to merge any time): land the JWKS local
    verifier + cache + canary + reconciliation script + runbook.
    Nothing in the request hot path calls `verify_supabase_jwt` yet
    — that flip happens in the destructive PR during the
    weeknight 23:00-01:00 IST maintenance window.
  * Next PR (destructive, lands during the window): replace
    `_supa_client.auth.get_user(token)` (HTTP round-trip per
    request) in `routes/auth.py:supabase_session` AND every
    request-time auth check in `auth_deps.py` with
    `verify_supabase_jwt(token)`. Cookie rotates to
    `syrabit_session_v2`. Legacy email/password endpoints get
    deleted.

Why JWKS local verify (vs the current `auth.get_user` round-trip):
  * **Latency** — current path = 1 outbound HTTPS round-trip to
    Supabase per authed request (~30-80ms eu/us). JWKS cached
    locally = ~0.5ms RSA verify, no network.
  * **Availability** — Supabase auth API outage today = every
    authed request 401s. With a JWKS cache + 5-min stale-on-error
    fallback, a transient Supabase outage is invisible to users
    until a key actually rotates.
  * **Cost** — auth.get_user counts against the Supabase free-tier
    request budget; local verify does not.

Cache contract:
  * Fresh window: 1h (`_JWKS_TTL_SECONDS`). Within this window
    we serve from cache without touching Supabase.
  * Stale-on-error window: an extra 5 min after fresh expiry
    (`_JWKS_STALE_GRACE_SECONDS`). If a refresh attempt fails
    inside this window, we keep serving the stale keys and emit
    `Syrabit/Auth::SupabaseJwksStale` rather than 5xx-ing every
    request. Outside both windows, fail loud (401).
  * The cache is process-local on purpose — we want every replica
    to refresh independently so a single-replica DNS hiccup
    cannot poison the whole fleet. JWKS payloads are tiny
    (<2 KB) so duplicating per replica is free.

Key rotation:
  * Supabase rotates JWT signing keys rarely (project lifetime
    measured in years). The cache TTL is the upper bound on how
    long a revoked key keeps validating tokens after rotation.
    1h is the same default used by `google.auth.jwt` and is well
    inside Supabase's documented rotation cadence.

Expected env (no new secret required — these already exist for
the OAuth broker + service-key paths):
  * `SUPABASE_URL` — REST URL, e.g. `https://<ref>.supabase.co`.
    JWKS endpoint is derived as `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`.
  * `SUPABASE_JWT_AUD` — optional audience pin. Defaults to
    `authenticated` (Supabase's default for signed-in users).
  * `SUPABASE_JWKS_TTL_SECONDS` — optional override (default 3600).
  * `SUPABASE_JWKS_STALE_GRACE_SECONDS` — optional override
    (default 300).

Threading: the in-process cache is guarded by a `threading.Lock`
so the FastAPI worker pool's concurrent requests serialize the
network refresh exactly once per TTL cycle (no thundering herd).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


_JWKS_TTL_SECONDS = int(os.environ.get("SUPABASE_JWKS_TTL_SECONDS", "3600") or "3600")
_JWKS_STALE_GRACE_SECONDS = int(
    os.environ.get("SUPABASE_JWKS_STALE_GRACE_SECONDS", "300") or "300"
)
_JWKS_HTTP_TIMEOUT_S = float(os.environ.get("SUPABASE_JWKS_HTTP_TIMEOUT_S", "5") or "5")
_DEFAULT_AUD = (os.environ.get("SUPABASE_JWT_AUD", "authenticated") or "authenticated").strip()


class SupabaseJWKSError(Exception):
    """Raised when JWKS cannot be fetched AND no stale copy is usable."""


class SupabaseTokenInvalid(Exception):
    """Raised when a presented token fails signature / claim validation."""


@dataclass
class _JWKSCacheEntry:
    keys_by_kid: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    fetched_at: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)


_cache_lock = threading.Lock()
_cache: _JWKSCacheEntry = _JWKSCacheEntry()
_last_refresh_error: Optional[str] = None


def _jwks_url() -> str:
    base = (os.environ.get("SUPABASE_URL", "") or "").strip().rstrip("/")
    if not base:
        raise SupabaseJWKSError(
            "SUPABASE_URL is not set; cannot derive JWKS endpoint"
        )
    return f"{base}/auth/v1/.well-known/jwks.json"


def _http_fetch_jwks(url: str) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "syrabit-backend/supabase_jwks",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=_JWKS_HTTP_TIMEOUT_S) as resp:
        body = resp.read()
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict) or "keys" not in payload or not isinstance(payload["keys"], list):
        raise SupabaseJWKSError(f"JWKS payload missing 'keys' array: {payload!r:.200}")
    return payload


def _refresh_cache_locked() -> None:
    """Refresh the JWKS cache. Caller must hold `_cache_lock`."""
    global _cache, _last_refresh_error
    url = _jwks_url()
    try:
        payload = _http_fetch_jwks(url)
    except Exception as exc:
        _last_refresh_error = f"{type(exc).__name__}: {exc}"
        # Honor stale-on-error: keep the existing cache, let the
        # caller decide whether stale is acceptable.
        logger.warning("Supabase JWKS refresh failed: %s", _last_refresh_error)
        raise SupabaseJWKSError(_last_refresh_error) from exc

    keys_by_kid: Dict[str, Dict[str, Any]] = {}
    for jwk in payload["keys"]:
        kid = jwk.get("kid")
        if not kid:
            # Some Supabase projects ship a single key with no kid.
            # Index it under the empty string so a token without a
            # kid header can still resolve to it.
            kid = ""
        keys_by_kid[kid] = jwk

    _cache = _JWKSCacheEntry(
        keys_by_kid=keys_by_kid,
        fetched_at=time.time(),
        raw=payload,
    )
    _last_refresh_error = None
    logger.info("Supabase JWKS cache refreshed: %d key(s)", len(keys_by_kid))


def _get_keys() -> Dict[str, Dict[str, Any]]:
    """Return the current keys-by-kid mapping, refreshing if needed.

    Behavior:
      * Fresh window  (age < TTL)            → no refresh, return cache.
      * Inside grace  (TTL <= age < TTL+grace), refresh attempt:
          - success → return refreshed cache.
          - failure → emit stale signal, return existing cache.
      * Outside grace                        → refresh and raise on failure.
      * Cold (cache empty) → refresh and raise on failure.
    """
    with _cache_lock:
        now = time.time()
        age = now - _cache.fetched_at if _cache.fetched_at else float("inf")

        if _cache.keys_by_kid and age < _JWKS_TTL_SECONDS:
            return _cache.keys_by_kid

        if _cache.keys_by_kid and age < _JWKS_TTL_SECONDS + _JWKS_STALE_GRACE_SECONDS:
            # Inside stale-grace: try refresh, but tolerate failure.
            try:
                _refresh_cache_locked()
            except SupabaseJWKSError:
                logger.warning(
                    "Serving stale Supabase JWKS (age=%.1fs > TTL=%ds, within grace=%ds)",
                    age, _JWKS_TTL_SECONDS, _JWKS_STALE_GRACE_SECONDS,
                )
            return _cache.keys_by_kid

        # Cold cache OR past the grace window — must refresh, must succeed.
        _refresh_cache_locked()
        return _cache.keys_by_kid


def verify_supabase_jwt(token: str, *, audience: Optional[str] = None) -> Dict[str, Any]:
    """Verify a Supabase access token via local JWKS.

    Returns the decoded claims dict on success.
    Raises `SupabaseTokenInvalid` on signature / claim failure.
    Raises `SupabaseJWKSError` if JWKS cannot be loaded at all
    (cold start + Supabase unreachable).

    Dependency: `PyJWT` (already in requirements — `auth_deps.py`
    uses `import jwt` + `jwt.exceptions.PyJWTError`).
    """
    import jwt as _pyjwt
    from jwt.exceptions import PyJWTError, ExpiredSignatureError
    from jwt.algorithms import RSAAlgorithm

    if not token or not isinstance(token, str):
        raise SupabaseTokenInvalid("empty token")

    try:
        unverified_header = _pyjwt.get_unverified_header(token)
    except PyJWTError as exc:
        raise SupabaseTokenInvalid(f"unparseable header: {exc}") from exc

    kid = unverified_header.get("kid", "") or ""
    keys = _get_keys()
    jwk = keys.get(kid)
    if jwk is None and kid == "" and len(keys) == 1:
        # Token without kid + JWKS with single key → unambiguous match.
        jwk = next(iter(keys.values()))
    if jwk is None:
        raise SupabaseTokenInvalid(f"no JWKS key matches kid={kid!r}")

    aud = (audience or _DEFAULT_AUD).strip() or None

    # Hard-pin RS256: do NOT trust an `alg` value coming from the unverified
    # token header (the classic JWT alg-confusion footgun). Supabase signs
    # access tokens with RS256, so anything else is rejected up-front.
    header_alg = unverified_header.get("alg")
    if header_alg and header_alg != "RS256":
        raise SupabaseTokenInvalid(f"unsupported alg in token header: {header_alg!r}")
    jwk_alg = jwk.get("alg")
    if jwk_alg and jwk_alg != "RS256":
        raise SupabaseTokenInvalid(f"unsupported alg in JWKS entry: {jwk_alg!r}")

    try:
        public_key = RSAAlgorithm.from_jwk(json.dumps(jwk))
    except Exception as exc:
        raise SupabaseTokenInvalid(f"jwk→key conversion failed: {exc}") from exc

    try:
        claims = _pyjwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=aud,
            options={
                "verify_aud": bool(aud),
                "verify_signature": True,
                "verify_exp": True,
            },
        )
    except ExpiredSignatureError as exc:
        raise SupabaseTokenInvalid(f"token expired: {exc}") from exc
    except PyJWTError as exc:
        raise SupabaseTokenInvalid(f"verification failed: {exc}") from exc

    sub = claims.get("sub")
    if not sub:
        raise SupabaseTokenInvalid("token missing sub claim")
    return claims


def cache_snapshot() -> Dict[str, Any]:
    """Read-only view of the JWKS cache for the admin health tile."""
    with _cache_lock:
        now = time.time()
        age = now - _cache.fetched_at if _cache.fetched_at else None
        return {
            "key_count": len(_cache.keys_by_kid),
            "fetched_at": _cache.fetched_at or None,
            "age_seconds": age,
            "fresh_window_s": _JWKS_TTL_SECONDS,
            "stale_grace_s": _JWKS_STALE_GRACE_SECONDS,
            "is_fresh": (age is not None and age < _JWKS_TTL_SECONDS),
            "is_stale_grace": (
                age is not None
                and _JWKS_TTL_SECONDS <= age < _JWKS_TTL_SECONDS + _JWKS_STALE_GRACE_SECONDS
            ),
            "last_refresh_error": _last_refresh_error,
        }


def force_refresh() -> Dict[str, Any]:
    """Admin-callable: blow away the cache and refetch immediately.

    Intended for the cutover runbook step "if the canary 401s right
    after rotating Supabase signing keys, force-refresh on every
    replica before debugging further".
    """
    global _cache
    with _cache_lock:
        _cache = _JWKSCacheEntry()
        _refresh_cache_locked()
    return cache_snapshot()


def _reset_for_test() -> None:
    """Test helper — clears cache + last error. NOT for production use."""
    global _cache, _last_refresh_error
    with _cache_lock:
        _cache = _JWKSCacheEntry()
        _last_refresh_error = None
