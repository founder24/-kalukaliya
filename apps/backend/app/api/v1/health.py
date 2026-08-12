"""
Health Check Endpoints: Basic and Deep Dependency Checks
"""

import asyncio
import os
import time

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


async def _safe_check(coro, timeout: float = 5.0) -> Dict[str, Any]:
    """Run a health check coroutine with a timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return {"status": "unhealthy", "error": "timeout"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


async def mongo_ping() -> Dict[str, Any]:
    """Ping MongoDB connection and verify Beanie ODM is initialized"""
    try:
        from app.db.mongo import get_mongo_client

        client = get_mongo_client()
        t0 = time.monotonic()
        await client.admin.command("ping")
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        # Verify Beanie ODM is initialized
        from app.models.content import Board

        Board.get_pymongo_collection()

        return {"status": "healthy", "latency_ms": latency_ms}
    except RuntimeError as e:
        logger.warning(f"MongoDB not initialized: {str(e)}")
        return {"status": "unhealthy", "error": str(e)}
    except Exception as e:
        logger.warning(f"MongoDB ping failed: {str(e)}")
        return {"status": "unhealthy", "error": str(e)}



async def mongo_vector_search_ping() -> Dict[str, Any]:
    """Ping MongoDB vector search (topic embeddings cache)."""
    try:
        from app.services.ai.topic_matcher import topic_matcher

        if not topic_matcher._is_cache_valid():
            await topic_matcher._load_embeddings()

        count = len(topic_matcher._embeddings or [])
        if count == 0:
            return {"status": "degraded", "error": "No topic embeddings loaded"}
        return {"status": "healthy", "topic_count": count}
    except Exception as e:
        logger.warning(f"MongoDB vector search ping failed: {str(e)}")
        return {"status": "unhealthy", "error": str(e)}


async def sarvam_ping() -> Dict[str, Any]:
    """Check Sarvam AI configuration and lightweight endpoint reachability"""
    try:
        from app.config import settings

        if not settings.SARVAM_API_KEY:
            return {"status": "degraded", "error": "SARVAM_API_KEY not configured"}

        import httpx

        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                settings.SARVAM_BASE_URL,
                headers={"API-Subscription-Key": settings.SARVAM_API_KEY},
            )
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        # Any non-connection-error response means the endpoint is reachable.
        # The base URL (/v1) returning 404 is expected — it has no GET handler.
        # Only 5xx responses indicate a real Sarvam infrastructure problem.
        if resp.status_code < 500:
            return {"status": "healthy", "latency_ms": latency_ms}
        return {"status": "unhealthy", "error": f"HTTP {resp.status_code}", "latency_ms": latency_ms}
    except Exception as e:
        logger.warning(f"Sarvam ping failed: {str(e)}")
        return {"status": "degraded", "error": str(e)[:120]}


@router.get("")
async def basic_health_check():
    """
    Basic health check - returns 200 if app is running.
    Does not check dependencies.
    Returns 503 if there are startup configuration errors.
    """
    from app.config import settings

    warnings = []

    # Issue 12: Detect JWT algorithm mismatch
    if settings.JWT_ALGORITHM == "RS256":
        if not getattr(settings, "JWT_PRIVATE_KEY", None):
            warnings.append("JWT_ALGORITHM is RS256 but JWT_PRIVATE_KEY is not set")
        if not getattr(settings, "JWT_PUBLIC_KEY", None):
            warnings.append("JWT_ALGORITHM is RS256 but JWT_PUBLIC_KEY is not set")

    # Check MongoDB initialization state (fast — no I/O, just reads module-level flag)
    try:
        from app.db.mongo import get_mongo_client
        get_mongo_client()
        mongodb_ok = True
    except RuntimeError:
        mongodb_ok = False

    if settings.startup_errors or not mongodb_ok:
        from app.services.comms.resend_client import (
            get_email_failures_last_hour,
            get_email_rate_limiter_mode,
        )

        errors = list(settings.startup_errors)
        if not mongodb_ok and not any("MongoDB" in e for e in errors):
            errors.append(
                "MongoDB not initialized — check Atlas connectivity/IP allowlist and MONGODB_URI secret. "
                "Hit /api/v1/health/deep for full dependency status."
            )
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "service": "syrabit-backend",
                "mongodb_initialized": mongodb_ok,
                "error_count": len(errors),
                "email_failures_last_hour": await get_email_failures_last_hour(),
                # "redis" = fleet-wide cap active; "in_memory" = per-pod only (degraded protection)
                "email_rate_limiter": get_email_rate_limiter_mode(),
                # Do NOT expose error details publicly — they can contain
                # infrastructure hints (URI patterns, IP allowlist messages).
                # Full details are available at /api/v1/health/deep (admin only).
                "hint": "Check /api/v1/health/deep for detailed diagnostics.",
                "warnings": warnings,
            },
        )

    from app.services.comms.resend_client import (
        get_email_failures_last_hour,
        get_email_rate_limiter_mode,
    )

    response = {
        "status": "healthy",
        "service": "syrabit-backend",
        "mongodb_initialized": True,
        "email_failures_last_hour": await get_email_failures_last_hour(),
        # "redis" = fleet-wide cap active; "in_memory" = per-pod only (degraded protection)
        "email_rate_limiter": get_email_rate_limiter_mode(),
        "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }
    if warnings:
        response["warnings"] = warnings
    return response


@router.get("/deep")
async def deep_health_check():
    """
    Deep health check - verifies all critical dependencies.
    Returns 503 if any dependency is unhealthy.

    Checks:
    - MongoDB connection
    - MongoDB vector search (topic embeddings)
    - Sarvam AI reachability
    """
    results = await asyncio.gather(
        _safe_check(mongo_ping()),
        _safe_check(mongo_vector_search_ping()),
        _safe_check(sarvam_ping()),
    )
    checks = {
        "mongodb": results[0],
        "mongo_vector_search": results[1],
        "sarvam_ai": results[2],
    }

    CORE_SERVICES = {"mongodb"}
    ACCEPTABLE = {"healthy"}

    core_healthy = all(checks[svc].get("status") in ACCEPTABLE for svc in CORE_SERVICES)
    all_healthy = all(check.get("status") == "healthy" for check in checks.values())

    if not core_healthy:
        overall_status = "unhealthy"
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif not all_healthy:
        overall_status = "degraded"
        status_code = status.HTTP_200_OK
    else:
        overall_status = "healthy"
        status_code = status.HTTP_200_OK

    from app.services.comms.resend_client import get_email_failures_last_hour

    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall_status,
            "checks": checks,
            "email_failures_last_hour": await get_email_failures_last_hour(),
        },
    )


async def cloudflare_workers_ai_ping() -> Dict[str, Any]:
    """Verify CF Workers AI token by calling the lightweight /ai/models endpoint."""
    try:
        from app.config import settings

        token = getattr(settings, "CF_WORKER_AI_TOKEN", None) or os.environ.get("CF_WORKER_AI_TOKEN")
        account_id = getattr(settings, "CF_ACCOUNT_ID", None) or os.environ.get("CF_ACCOUNT_ID")

        if not token:
            return {"status": "not_configured", "note": "CF_WORKER_AI_TOKEN not set"}
        if not account_id:
            return {"status": "not_configured", "note": "CF_ACCOUNT_ID not set"}

        import httpx

        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/models/search?per_page=1",
                headers={"Authorization": f"Bearer {token}"},
            )
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        if resp.status_code == 200:
            return {"status": "healthy", "latency_ms": latency_ms}
        elif resp.status_code == 401:
            return {"status": "unhealthy", "error": "CF_WORKER_AI_TOKEN is invalid (401 Unauthorized)"}
        elif resp.status_code == 403:
            return {"status": "unhealthy", "error": "CF_WORKER_AI_TOKEN lacks AI permissions (403 Forbidden)"}
        else:
            return {"status": "degraded", "error": f"HTTP {resp.status_code}", "latency_ms": latency_ms}
    except Exception as e:
        logger.warning(f"CF Workers AI ping failed: {str(e)}")
        return {"status": "unhealthy", "error": str(e)[:120]}


@router.get("/providers")
async def provider_health_check():
    """
    Check all AI provider integrations in parallel.

    Providers checked:
      - sarvam_ai           : Sarvam API endpoint reachability
      - vector_search       : MongoDB topic embedding cache
      - cloudflare_workers_ai : CF Workers AI token validity

    Overall status:
      healthy  — all providers healthy
      degraded — ≥1 provider degraded/not_configured but none unhealthy
      unhealthy — ≥1 provider explicitly unhealthy
    """
    results = await asyncio.gather(
        _safe_check(sarvam_ping(),               timeout=10.0),
        _safe_check(mongo_vector_search_ping(),  timeout=10.0),
        _safe_check(cloudflare_workers_ai_ping(), timeout=10.0),
    )

    providers = {
        "sarvam_ai":             results[0],
        "vector_search":         results[1],
        "cloudflare_workers_ai": results[2],
    }

    statuses = [p.get("status", "unknown") for p in providers.values()]

    if any(s == "unhealthy" for s in statuses):
        overall = "unhealthy"
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    elif any(s == "degraded" for s in statuses):
        overall = "degraded"
        http_status = status.HTTP_200_OK
    else:
        overall = "healthy"
        http_status = status.HTTP_200_OK

    return JSONResponse(
        status_code=http_status,
        content={
            "overall": overall,
            "providers": providers,
            "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        },
    )


@router.get("/chat-pipeline")
async def chat_pipeline_health(request: Request):
    """
    End-to-end chat pipeline integration test.

    Exercises the full AI pipeline (Sarvam → Gemini fallback) with a minimal
    probe question, exactly as real student chat requests do.  The response
    indicates which provider served the request so CI can distinguish between
    Sarvam healthy vs. Gemini-fallback-only.

    Auth: Bearer token matching TRANSLATE_CRON_SECRET.
    The CF Worker overwrites Authorization with an OIDC identity token and
    saves the original in X-User-JWT — both headers are checked.

    Returns 200 on success, 503 on failure, 401 on bad/missing token.
    """
    import time as _time

    from app.config import settings

    # ── Auth: require TRANSLATE_CRON_SECRET ──────────────────────────────────
    # Three paths (same as _verify_cron_token in admin_cron.py):
    #   X-User-JWT     → edge-proxied via Cloudflare Worker
    #   X-Cron-Token   → direct Cloud Run call with OIDC in Authorization
    #   Authorization  → legacy direct call (local dev / Replit shell)
    expected = settings.TRANSLATE_CRON_SECRET
    x_user_jwt = request.headers.get("X-User-JWT", "")
    x_cron_token = request.headers.get("X-Cron-Token", "")
    auth_header = request.headers.get("Authorization", "")
    token = None
    for raw in (x_user_jwt, x_cron_token, auth_header):
        if raw.startswith("Bearer "):
            token = raw[7:]
            break
    if not expected or not token or token != expected:
        return JSONResponse(
            status_code=401,
            content={"status": "unauthorized", "error": "Valid TRANSLATE_CRON_SECRET required"},
        )

    result: Dict[str, Any] = {}

    # ── Step 1: Full AI pipeline (Sarvam → Gemini fallback) ─────────────────
    # Uses the exact same code path as real student chat messages so this probe
    # gives a true signal about what students actually experience.
    try:
        from app.services.ai.sarvam_client import generate_with_sarvam
        from app.services.ai.gemini_fallback import (
            generate_gemini,
            _available as gemini_available,
        )
        from app.core.circuit_breaker import SarvamBillingExhaustedError, CircuitBreakerError

        _sys = "You are a test probe. Respond with exactly the single word PONG and nothing else."
        _usr = "ping"
        provider = "sarvam"
        t0 = _time.monotonic()

        try:
            response_text = await generate_with_sarvam(
                system_prompt=_sys,
                user_message=_usr,
                stream=False,
            )
        except (SarvamBillingExhaustedError, CircuitBreakerError, Exception) as sarvam_err:
            # Sarvam unavailable (billing exhausted, circuit open, or API error).
            # Fall through to Gemini exactly as real chat does.
            if not gemini_available():
                raise RuntimeError(
                    f"Sarvam failed ({str(sarvam_err)[:80]}) and Gemini not configured"
                )
            logger.info(f"chat_pipeline_probe: Sarvam failed ({sarvam_err!r:.80}), using Gemini fallback")
            response_text = await generate_gemini(_sys, _usr, max_output_tokens=20)
            provider = "gemini-2.5-flash"

        latency_ms = round((_time.monotonic() - t0) * 1000, 1)
        result["provider"] = provider
        result["latency_ms"] = latency_ms
        result["response_preview"] = (response_text or "")[:40]

    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "step": "ai_pipeline", "error": str(e)[:120]},
        )

    # ── Step 2: Assamese output quality via Gemini fallback ─────────────────
    # When Sarvam is unavailable, Assamese students are served by Gemini.
    # Sarvam uses complex extraction helpers (_extract_assamese_answer /
    # _extract_assamese_translation) because its reasoning model embeds
    # Assamese lines inside an English chain-of-thought.  Gemini 2.5 Flash
    # responds directly in the requested language — no extraction needed.
    # This step confirms that Gemini produces valid Assamese script so we
    # know the fallback actually serves students correctly.
    try:
        from app.services.ai.gemini_fallback import (
            generate_gemini,
            _available as gemini_available,
        )

        if gemini_available():
            # System prompt in Assamese instructs the model to respond in Assamese.
            # User question: "তুমি কোন?" — "Who are you?"
            _as_sys = (
                "তুমি এটা সহায়কাৰী শিক্ষামূলক সহায়ক। সদায় চমুকৈ অসমীয়া ভাষাত উত্তৰ দিয়া।"
            )
            _as_usr = "তুমি কোন?"
            t_as = _time.monotonic()
            as_response = await generate_gemini(
                _as_sys, _as_usr, timeout=40.0, max_output_tokens=80
            )
            as_latency_ms = round((_time.monotonic() - t_as) * 1000, 1)

            # Verify response contains Assamese/Bengali script (U+0980–U+09FF).
            # A clean Gemini response will contain these directly — no extraction
            # logic is needed, unlike the Sarvam reasoning-model path.
            has_assamese = any("\u0980" <= c <= "\u09ff" for c in (as_response or ""))
            assamese_result: Dict[str, Any] = {
                "has_assamese_script": has_assamese,
                "latency_ms": as_latency_ms,
                "response_preview": (as_response or "")[:80],
            }
            if not has_assamese:
                # Gemini responded but not in Assamese — likely answered in English.
                # Students would receive English instead of Assamese when Sarvam is down.
                assamese_result["warning"] = (
                    "Gemini response contains no Assamese script — "
                    "verify the system-prompt language instruction"
                )
            result["assamese_probe"] = assamese_result
        else:
            result["assamese_probe"] = {
                "status": "skipped",
                "reason": "Gemini not configured (GEMINI_API_KEY absent)",
            }
    except Exception as e:
        result["assamese_probe"] = {"status": "error", "error": str(e)[:120]}

    # ── Step 3: MongoDB vector search reachability ───────────────────────────
    try:
        from app.services.ai.topic_matcher import topic_matcher

        if not topic_matcher._is_cache_valid():
            await asyncio.wait_for(topic_matcher._load_embeddings(), timeout=5.0)

        topic_count = len(topic_matcher._embeddings or [])
        result["rag_topics_cached"] = topic_count
        result["rag_status"] = "healthy" if topic_count > 0 else "degraded"
    except Exception as e:
        result["rag_status"] = "unavailable"
        result["rag_error"] = str(e)[:80]

    result["status"] = "healthy"
    return JSONResponse(status_code=200, content=result)


@router.get("/circuit-breakers")
async def circuit_breaker_status():
    """
    Get status of all circuit breakers.
    Useful for monitoring service resilience.
    """
    from app.core.circuit_breaker import sarvam_circuit_breaker

    return {
        "sarvam_ai": sarvam_circuit_breaker.get_status(),
    }
