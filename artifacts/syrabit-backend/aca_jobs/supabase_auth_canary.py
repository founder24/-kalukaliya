"""Synthetic canary for the Supabase auth path.

Task #47 step 2 — every 5 minutes, mint a short-lived Supabase
service-key access token, verify it through `supabase_jwks.verify_supabase_jwt`,
and emit a pass/fail metric. If the canary breaks, the cutover
is paused (the maintenance-window runbook treats a red canary as
a hard go/no-go).

The canary is intentionally LOCAL-ONLY in this prep PR:
  * It exercises the JWKS cache + verifier end-to-end against
    the real Supabase JWKS endpoint.
  * It does NOT call any backend route (no HTTP self-call) so
    it works identically in prod, staging, and developer shells.
  * It is wired as an EventBridge `rate(5 minutes)` Lambda by
    the destructive PR alongside the cutover (kept out of this
    PR so we can review JWKS infra without TF churn).

Metric contract:
  * `Syrabit/Auth::SupabaseAuthCanary` (1=pass, 0=fail)
  * `Syrabit/Auth::SupabaseJwksAgeSeconds` (cache age at probe time)
  * `Syrabit/Auth::SupabaseJwksStale` (1 if serving from stale-grace)

Failure semantics:
  * 3 consecutive fails → CloudWatch alarm `supabase-auth-canary-down`
    pages on-call.
  * Any fail emits a Sentry breadcrumb with the exception class so
    we can distinguish JWKS-fetch failures from token-mint failures.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _emit_metric(name: str, value: float, unit: str = "Count") -> None:
    """Best-effort CloudWatch EMF emission via stdout (Lambda log driver picks it up)."""
    payload = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "Syrabit/Auth",
                "Dimensions": [["Source"]],
                "Metrics": [{"Name": name, "Unit": unit}],
            }],
        },
        "Source": "lambda",
        name: value,
    }
    print(json.dumps(payload))


def run_canary() -> Dict[str, Any]:
    """Mint a Supabase token via service-key sign-in, verify it locally.

    Returns a structured result. Raises on hard failure (so EventBridge
    retries and the alarm latches faster).
    """
    from supabase_jwks import verify_supabase_jwt, cache_snapshot, SupabaseTokenInvalid, SupabaseJWKSError

    canary_email = (os.environ.get("SUPABASE_CANARY_EMAIL") or "").strip()
    canary_password = (os.environ.get("SUPABASE_CANARY_PASSWORD") or "").strip()
    if not canary_email or not canary_password:
        raise RuntimeError(
            "SUPABASE_CANARY_EMAIL + SUPABASE_CANARY_PASSWORD must be set "
            "(create a dedicated read-only Supabase user for the canary)"
        )

    # Mint a token using the anon-key client (same path real users walk).
    from supabase import create_client
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    anon = (os.environ.get("SUPABASE_ANON_KEY") or "").strip()
    if not url or not anon:
        raise RuntimeError("SUPABASE_URL + SUPABASE_ANON_KEY required for canary")

    client = create_client(url, anon)
    started = time.time()
    try:
        sb_response = client.auth.sign_in_with_password({
            "email": canary_email,
            "password": canary_password,
        })
        access_token = sb_response.session.access_token
    except Exception as exc:
        _emit_metric("SupabaseAuthCanary", 0.0)
        logger.error("canary token mint failed: %s", exc)
        raise

    try:
        claims = verify_supabase_jwt(access_token)
    except (SupabaseTokenInvalid, SupabaseJWKSError) as exc:
        _emit_metric("SupabaseAuthCanary", 0.0)
        logger.error("canary verify failed: %s", exc)
        raise

    snap = cache_snapshot()
    _emit_metric("SupabaseAuthCanary", 1.0)
    _emit_metric("SupabaseJwksAgeSeconds", float(snap.get("age_seconds") or 0.0), unit="Seconds")
    _emit_metric("SupabaseJwksStale", 1.0 if snap.get("is_stale_grace") else 0.0)

    return {
        "ok": True,
        "elapsed_ms": int((time.time() - started) * 1000),
        "sub": claims.get("sub"),
        "aud": claims.get("aud"),
        "exp": claims.get("exp"),
        "jwks": snap,
    }


def lambda_handler(event, context):  # pragma: no cover — Lambda entrypoint
    try:
        return run_canary()
    except Exception as exc:
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except Exception:
            pass
        raise


if __name__ == "__main__":
    print(json.dumps(run_canary(), indent=2))
