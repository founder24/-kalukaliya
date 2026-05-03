"""Google OIDC ID-token verification dependency.

Cloud Scheduler and Cloud Tasks can attach an OIDC token to outbound
HTTP requests; the receiving handler must verify the token's signature
and that it was minted by an allow-listed service account.

Usage in a route:

    from fastapi import Depends
    from oidc_auth import require_google_oidc

    @router.post("/internal/jobs/something")
    async def handler(claims: dict = Depends(require_google_oidc())):
        ...

Configuration env vars:
    GCP_OIDC_ALLOWED_EMAILS   comma-separated SA emails permitted to call
                              internal endpoints. If unset, falls back to
                              the SA loaded by gcp_auth (i.e. *our own SA*
                              calling itself, which is the default Cloud
                              Scheduler→FastAPI pattern).
    GCP_OIDC_REQUIRED_AUDIENCE  if set, also pin the token `aud` claim.
                                Otherwise audience check is skipped (Cloud
                                Scheduler defaults `aud` to the target URL,
                                which is fine to ignore for our use).
    GCP_OIDC_DEV_BYPASS=1     dev-only escape hatch — accepts a fixed
                              shared-secret bearer token for local
                              testing without minting OIDC tokens.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional, Set

from fastapi import Depends, Header, HTTPException

import gcp_auth

logger = logging.getLogger(__name__)


def _allowed_sa_emails() -> Set[str]:
    raw = (os.environ.get("GCP_OIDC_ALLOWED_EMAILS") or "").strip()
    if raw:
        return {s.strip().lower() for s in raw.split(",") if s.strip()}
    info = gcp_auth._load_sa_info() if hasattr(gcp_auth, "_load_sa_info") else None
    if info and info.get("client_email"):
        return {info["client_email"].lower()}
    return set()


def require_google_oidc(
    *,
    audience: Optional[str] = None,
    extra_emails: Optional[Set[str]] = None,
) -> Callable:
    """Build a FastAPI dependency that validates an incoming Google OIDC token."""

    async def _dep(authorization: Optional[str] = Header(None)) -> dict:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        token = authorization.split(None, 1)[1].strip()
        if not token:
            raise HTTPException(status_code=401, detail="Empty bearer token")

        # Dev-only bypass: simple shared-secret to allow `curl` testing locally.
        if os.environ.get("GCP_OIDC_DEV_BYPASS") == "1":
            shared = (os.environ.get("GCP_OIDC_DEV_SECRET") or "").strip()
            if shared and token == shared:
                return {"email": "dev-bypass@local", "_dev_bypass": True}

        try:
            from google.oauth2 import id_token as gid
            from google.auth.transport import requests as gauth_requests
        except Exception as exc:  # pragma: no cover
            logger.error("google-auth not installed: %r", exc)
            raise HTTPException(status_code=500, detail="OIDC verification unavailable")

        aud = audience or (
            os.environ.get("GCP_OIDC_REQUIRED_AUDIENCE") or None
        )
        try:
            # When `audience` is None, google-auth still verifies signature/exp
            # but skips the aud check — exactly what we want for Scheduler tokens
            # whose aud is the target URL (which differs across environments).
            claims = gid.verify_oauth2_token(
                token, gauth_requests.Request(), audience=aud
            )
        except ValueError as exc:
            logger.warning("OIDC token verification failed: %r", exc)
            raise HTTPException(status_code=401, detail=f"Invalid OIDC token: {exc}")

        email = (claims.get("email") or "").lower()
        if not email or not claims.get("email_verified"):
            raise HTTPException(status_code=401, detail="Token missing verified email")

        allowed = _allowed_sa_emails()
        if extra_emails:
            allowed = allowed | {e.lower() for e in extra_emails}
        if not allowed:
            raise HTTPException(
                status_code=503,
                detail=("OIDC allow-list empty. Set GCP_OIDC_ALLOWED_EMAILS or "
                        "load a service account via GOOGLE_APPLICATION_CREDENTIALS_JSON."),
            )
        if email not in allowed:
            logger.warning("OIDC token rejected: email %s not in allow-list", email)
            raise HTTPException(status_code=403, detail="Caller not authorized")

        return claims

    return _dep
