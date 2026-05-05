"""Task #383 — Cloudflare Turnstile siteverify (backend half).

The frontend already collects a Turnstile token on the public auth
forms (see ``artifacts/syrabit/src``) and forwards it to the origin
via the ``cf-turnstile-token`` header / ``turnstile_token`` body
field. The edge worker forwards the same value as
``x-turnstile-token`` (see ``workers/edge-proxy/src/index.ts``). The
piece that was missing — and that this module supplies — is the
backend ``siteverify`` call that proves the token is real before we
let an unauthenticated request mutate state.

Endpoints:

  * ``verify_turnstile_token(token, remote_ip)`` — async siteverify
    against ``challenges.cloudflare.com``. Returns a ``VerifyResult``
    with ``ok`` + structured failure info. NEVER raises.
  * ``require_turnstile`` — FastAPI dependency you can attach to
    public POST endpoints. Reads the token from header or body, runs
    siteverify, and raises HTTP 403 with a stable error code on
    failure so the frontend can render an inline retry.

Failure modes (deliberately explicit so on-call can tell them apart):

  * ``TURNSTILE_ON`` is false → dependency is a no-op (so dev / CI
    parity stays intact, and we can ship the dependency in the route
    chain *before* we flip the flag in production).
  * Secret unset but flag on → 503 ``turnstile_misconfigured``.
  * Token missing → 403 ``turnstile_required``.
  * Token rejected by Cloudflare → 403 ``turnstile_failed`` + the
    Cloudflare error codes are echoed in the detail so on-call can
    distinguish "expired" from "invalid" from "duplicate".
  * Network blip reaching Cloudflare → 502 ``turnstile_unreachable``
    so the client knows to retry the request, not the form.

We deliberately do NOT cache verify results: each token is one-shot
per the Turnstile contract — replaying the same token twice is itself
a failure mode CF reports back.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from fastapi import HTTPException, Request

from config import TURNSTILE_ON, TURNSTILE_SECRET_KEY

logger = logging.getLogger(__name__)


SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_VERIFY_TIMEOUT_S = 5.0


@dataclass
class VerifyResult:
    ok: bool
    action: Optional[str] = None
    cdata: Optional[str] = None
    hostname: Optional[str] = None
    error_codes: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


# ── Counters for the admin /admin/cf-health panel ────────────────────────────
_LOCK = threading.Lock()
_STATE = {
    "verify_passes": 0,
    "verify_fails": 0,
    "verify_missing_token": 0,
    "verify_misconfigured": 0,
    "verify_unreachable": 0,
}


def _bump(field_: str) -> None:
    with _LOCK:
        _STATE[field_] = _STATE.get(field_, 0) + 1


def snapshot() -> dict:
    """Aggregate readout used by ``/admin/cf-health``."""
    with _LOCK:
        st = dict(_STATE)
    total = st["verify_passes"] + st["verify_fails"]
    fail_ratio = (st["verify_fails"] / total) if total else 0.0
    return {
        "enabled": bool(TURNSTILE_ON),
        "secret_configured": bool(TURNSTILE_SECRET_KEY),
        "fail_ratio": round(fail_ratio, 4),
        **st,
    }


def reset_for_tests() -> None:
    with _LOCK:
        for k in _STATE:
            _STATE[k] = 0


def frontend_config() -> dict:
    """Tiny JSON the SPA reads on bootstrap so it can render the
    Turnstile widget without baking the site key into the build.

    Mirrors :func:`cf_web_analytics.frontend_config` so the frontend
    can ``GET /api/turnstile/config`` and decide whether to mount the
    widget at all (``enabled=False`` → render nothing, do not load the
    challenges.cloudflare.com script). The ``site_key`` is always safe
    to expose (it is the public half of the Turnstile keypair) but we
    still gate it behind the flag so a half-configured rollout cannot
    leak the namespace early.

    Returns ``{enabled, site_key}`` — empty/None values when the flag
    is off or the site key is unset, matching the behaviour the
    ``require_turnstile`` dependency uses on the server side.
    """
    # Re-read at call time so a test that flips the env via
    # ``importlib.reload(config)`` sees the new value without having to
    # also re-import this module.
    from config import TURNSTILE_ON as _ON, TURNSTILE_SITE_KEY as _KEY
    enabled = bool(_ON and _KEY)
    return {
        "enabled": enabled,
        "site_key": _KEY if enabled else None,
    }


# ── Pure verifier ────────────────────────────────────────────────────────────
async def verify_turnstile_token(
    token: str, remote_ip: str = "",
    *, http_client_factory=httpx.AsyncClient,
) -> VerifyResult:
    """Call Cloudflare ``siteverify``. Never raises — caller decides
    how to surface failures.

    When ``TURNSTILE_ON`` is false this returns a successful result
    immediately so the dependency layer can stay wired in production
    even before the operator flips the flag.
    """
    if not TURNSTILE_ON:
        return VerifyResult(ok=True, action="bypass-flag-off")
    if not TURNSTILE_SECRET_KEY:
        _bump("verify_misconfigured")
        return VerifyResult(ok=False, error_codes=["secret-not-configured"])
    if not token:
        _bump("verify_missing_token")
        return VerifyResult(ok=False, error_codes=["missing-input-response"])

    payload = {"secret": TURNSTILE_SECRET_KEY, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    try:
        async with http_client_factory(timeout=_VERIFY_TIMEOUT_S) as client:
            resp = await client.post(SITEVERIFY_URL, data=payload)
        body = resp.json() if resp.headers.get("content-type", "").startswith(
            "application/json") else {}
    except Exception as exc:
        _bump("verify_unreachable")
        logger.warning("[turnstile] siteverify unreachable: %s", exc)
        return VerifyResult(ok=False, error_codes=["network-error"])

    success = bool(body.get("success"))
    if success:
        _bump("verify_passes")
    else:
        _bump("verify_fails")
    return VerifyResult(
        ok=success,
        action=body.get("action"),
        cdata=body.get("cdata"),
        hostname=body.get("hostname"),
        error_codes=list(body.get("error-codes") or []),
        raw=body,
    )


# ── FastAPI dependency ───────────────────────────────────────────────────────
async def _read_token_from_request(request: Request) -> str:
    # Header is the canonical source — the edge worker sets
    # ``x-turnstile-token`` on every forwarded request that included
    # one. Frontend may also send ``cf-turnstile-token`` (the name CF
    # uses in its docs). Body is a last-resort fallback for clients
    # that can't set custom headers (e.g. plain HTML form posts).
    for name in ("x-turnstile-token", "cf-turnstile-token"):
        v = request.headers.get(name)
        if v:
            return v.strip()
    try:
        body = await request.json()
        if isinstance(body, dict):
            v = body.get("turnstile_token") or body.get("cf_turnstile_response")
            if isinstance(v, str) and v.strip():
                return v.strip()
    except Exception:
        pass
    return ""


async def require_turnstile(request: Request) -> VerifyResult:
    """FastAPI dependency. Attach to any public POST that should be
    Turnstile-gated:

        @router.post("/auth/login", dependencies=[Depends(require_turnstile)])
    """
    if not TURNSTILE_ON:
        return VerifyResult(ok=True, action="bypass-flag-off")
    if not TURNSTILE_SECRET_KEY:
        _bump("verify_misconfigured")
        raise HTTPException(
            status_code=503,
            detail={"code": "turnstile_misconfigured",
                    "message": "TURNSTILE_SECRET_KEY is not set"},
        )
    token = await _read_token_from_request(request)
    if not token:
        _bump("verify_missing_token")
        raise HTTPException(
            status_code=403,
            detail={"code": "turnstile_required",
                    "message": "Turnstile token required"},
        )
    remote_ip = (
        request.headers.get("cf-connecting-ip")
        or (request.client.host if request.client else "")
        or ""
    )
    result = await verify_turnstile_token(token, remote_ip)
    if not result.ok:
        if "network-error" in result.error_codes:
            raise HTTPException(
                status_code=502,
                detail={"code": "turnstile_unreachable",
                        "message": "Could not reach Cloudflare to verify token",
                        "error_codes": result.error_codes},
            )
        raise HTTPException(
            status_code=403,
            detail={"code": "turnstile_failed",
                    "message": "Turnstile verification failed",
                    "error_codes": result.error_codes},
        )
    return result


__all__ = [
    "VerifyResult", "verify_turnstile_token", "require_turnstile",
    "snapshot", "reset_for_tests", "frontend_config",
]
