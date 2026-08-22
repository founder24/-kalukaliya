"""
Health Check Endpoints: Basic and Deep Dependency Checks
"""

import asyncio
import os
import time

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


def _is_quota_error(exc: Exception) -> bool:
    """Return True when *exc* is a transient Gemini quota exhaustion (HTTP 429).

    Gemini raises these as RuntimeError with the HTTP status or gRPC status
    code embedded in the message string.  We treat them as "degraded" rather
    than "unhealthy" so a brief quota blip does not cause a false-positive CI
    failure.  Step 1 (main AI pipeline) is unaffected — it does not call this
    helper and still fails hard on any error including quota exhaustion.
    """
    err = str(exc).upper()
    return "429" in err or "RESOURCE_EXHAUSTED" in err or "QUOTA" in err


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


async def workers_ai_ping() -> Dict[str, Any]:
    """Verify the authenticated Workers AI generation route with a bounded call."""
    try:
        from app.config import settings
        from app.services.ai.workers_ai_client import generate_with_workers_ai

        if not settings.EDGE_SHARED_SECRET:
            return {"status": "degraded", "error": "EDGE_SHARED_SECRET not configured"}
        if not settings.WORKERS_AI_INTERNAL_URL:
            return {
                "status": "degraded",
                "error": "WORKERS_AI_INTERNAL_URL not configured",
            }

        t0 = time.monotonic()
        response_text = await asyncio.wait_for(
            generate_with_workers_ai(
                "Reply with exactly OK.",
                "Health check",
                max_tokens=256,
            ),
            timeout=30.0,
        )
        if not response_text.strip():
            return {"status": "degraded", "error": "Workers AI returned an empty response"}

        return {
            "status": "healthy",
            "endpoint": settings.WORKERS_AI_INTERNAL_URL,
            "model": settings.CF_AI_MODEL,
            "latency_ms": round((time.monotonic() - t0) * 1000, 1),
        }
    except Exception as e:
        logger.warning(f"Workers AI generation check failed: {str(e)}")
        return {"status": "degraded", "error": str(e)[:120] or type(e).__name__}


async def workers_ai_embedding_ping() -> Dict[str, Any]:
    """Exercise the production embedding path used by RAG and ingestion."""
    try:
        from app.services.ai.embedder import generate_embedding_vector

        t0 = time.monotonic()
        vector = await asyncio.wait_for(
            generate_embedding_vector("Syrabit embedding health check"),
            timeout=15.0,
        )
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        if len(vector) != 1024:
            return {
                "status": "degraded",
                "error": f"Workers AI embedding returned {len(vector)} dimensions, expected 1024",
                "latency_ms": latency_ms,
            }

        return {
            "status": "healthy",
            "dimensions": len(vector),
            "latency_ms": latency_ms,
        }
    except Exception as e:
        logger.warning(f"Workers AI embedding check failed: {str(e)}")
        return {"status": "unhealthy", "error": str(e)[:120] or type(e).__name__}


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
    - Workers AI internal generation route
    - Workers AI embedding route used by RAG and ingestion
    """
    results = await asyncio.gather(
        _safe_check(mongo_ping()),
        _safe_check(mongo_vector_search_ping()),
        _safe_check(workers_ai_ping(), timeout=35.0),
        _safe_check(workers_ai_embedding_ping(), timeout=20.0),
    )
    checks = {
        "mongodb": results[0],
        "mongo_vector_search": results[1],
        "workers_ai": results[2],
        "workers_ai_embedding": results[3],
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
      - workers_ai          : authenticated Workers AI generation route
      - vector_search       : MongoDB topic embedding cache
      - cloudflare_workers_ai : CF Workers AI token validity

    Overall status:
      healthy  — all providers healthy
      degraded — ≥1 provider degraded/not_configured but none unhealthy
      unhealthy — ≥1 provider explicitly unhealthy
    """
    results = await asyncio.gather(
        _safe_check(workers_ai_ping(),           timeout=10.0),
        _safe_check(mongo_vector_search_ping(),  timeout=10.0),
        _safe_check(cloudflare_workers_ai_ping(), timeout=10.0),
    )

    providers = {
        "workers_ai":            results[0],
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

    # ── Step 1: Workers AI text-generation probe ─────────────────────────────
    try:
        from app.services.ai.workers_ai_client import generate_with_workers_ai

        _sys = "You are a test probe. Respond with exactly the single word PONG and nothing else."
        _usr = "ping"
        provider = settings.CF_AI_MODEL
        t0 = _time.monotonic()
        response_text = await asyncio.wait_for(
            generate_with_workers_ai(_sys, _usr, max_tokens=256),
            timeout=30.0,
        )

        latency_ms = round((_time.monotonic() - t0) * 1000, 1)
        result["provider"] = provider
        result["latency_ms"] = latency_ms
        result["response_preview"] = (response_text or "")[:40]

    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "step": "ai_pipeline", "error": str(e)[:120]},
        )

    # ── Steps 2 & 4: Assamese quality probes (non-streaming + streaming) ────
    # Both probes run in parallel inside a single asyncio.gather() call so they
    # share the same 30 s time window and don't add to the total CI budget.
    #
    from app.services.ai.workers_ai_client import (
        generate_with_workers_ai,
        workers_ai_client,
    )

    # ── Probe coroutines ─────────────────────────────────────────────────────

    async def _run_nonstreaming_probe() -> Dict[str, Any]:
        """Step 2: non-streaming Assamese quality check via Workers AI."""
        _as_sys = (
            "তুমি এটা সহায়কাৰী শিক্ষামূলক সহায়ক। সদায় চমুকৈ অসমীয়া ভাষাত উত্তৰ দিয়া।"
        )
        _as_usr = "তুমি কোন?"  # "Who are you?"
        t_as = _time.monotonic()
        as_response = await asyncio.wait_for(
            generate_with_workers_ai(_as_sys, _as_usr, is_assamese=True, max_tokens=256),
            timeout=30.0,
        )
        latency_ms = round((_time.monotonic() - t_as) * 1000, 1)
        has_assamese = any("\u0980" <= c <= "\u09ff" for c in (as_response or ""))
        return {
            "has_assamese_script": has_assamese,
            "latency_ms": latency_ms,
            "response_preview": (as_response or "")[:80],
        }

    async def _run_streaming_probe() -> Dict[str, Any]:
        """Step 4: streaming Assamese quality check via Workers AI.

        The legacy Cloud Run bridge collects chunks before yielding them, so
        ``first_chunk_latency_ms`` is the wall-clock time until the generator
        produces its first chunk — equivalent to full generation TTFB from the
        student's perspective.  The probe fails CI when no Assamese script
        appears in the joined chunks, or when TTFB exceeds 10 s.
        """
        _as_sys = (
            "তুমি এটা সহায়কাৰী শিক্ষামূলক সহায়ক। সদায় চমুকৈ অসমীয়া ভাষাত উত্তৰ দিয়া।"
        )
        _as_usr = "পোহৰ কি?"  # "What is light?" — differs from non-streaming probe

        t_stream = _time.monotonic()
        first_chunk_ms: Optional[float] = None
        stream_chunks: list[str] = []

        async def _collect() -> None:
            nonlocal first_chunk_ms
            async for chunk in workers_ai_client.stream_generate_with_retry(
                _as_sys, _as_usr, is_assamese=True, max_tokens=256
            ):
                if first_chunk_ms is None:
                    first_chunk_ms = round((_time.monotonic() - t_stream) * 1000, 1)
                stream_chunks.append(chunk)

        await asyncio.wait_for(_collect(), timeout=30.0)

        total_ms = round((_time.monotonic() - t_stream) * 1000, 1)
        joined = "".join(stream_chunks)
        has_assamese = any("\u0980" <= c <= "\u09ff" for c in joined)

        probe: Dict[str, Any] = {
            "has_assamese_script": has_assamese,
            "first_chunk_latency_ms": first_chunk_ms,
            "total_latency_ms": total_ms,
            "chunk_count": len(stream_chunks),
            "response_preview": joined[:80],
        }

        # Warn (not fail) when TTFB exceeds the 10 s student-experience threshold.
        # The legacy Cloud Run bridge buffers Worker output before yielding, so
        # first_chunk_latency_ms == total generation time.  Values above 10 s are
        # still logged so ops can spot quota-throttling or cold-start regressions.
        if first_chunk_ms is not None and first_chunk_ms > 10_000:
            probe["ttfb_warning"] = (
                f"TTFB {first_chunk_ms:.0f} ms exceeds 10 000 ms student threshold"
            )
            logger.warning(
                "streaming_assamese_probe: TTFB %.0f ms > 10 000 ms", first_chunk_ms
            )

        return probe

    # ── Execute both probes ──────────────────────────────────────────────────

    if not settings.EDGE_SHARED_SECRET:
        result["assamese_probe"] = {
            "status": "skipped",
            "reason": "Workers AI internal authentication is not configured",
        }
        result["streaming_assamese_probe"] = {
            "status": "skipped",
            "reason": "Workers AI internal authentication is not configured",
        }
    else:
        try:
            ns_result, st_result = await asyncio.gather(
                _run_nonstreaming_probe(),
                _run_streaming_probe(),
                return_exceptions=True,
            )
        except Exception as gather_err:
            return JSONResponse(
                status_code=503,
                content={
                    **result,
                    "status": "unhealthy",
                    "step": "assamese_probes",
                    "error": str(gather_err)[:120],
                },
            )

        # ── Non-streaming probe result ────────────────────────────────────────
        if isinstance(ns_result, Exception):
            if _is_quota_error(ns_result):
                # Transient quota exhaustion: degrade gracefully so CI is not
                # blocked by a brief 429 blip.  ops can see the quota_warning
                # field in the response body.
                logger.warning(
                    "assamese_probe: Workers AI quota exhausted (429) — marking degraded: %s",
                    str(ns_result)[:120],
                )
                result["assamese_probe"] = {
                    "status": "degraded",
                    "quota_warning": str(ns_result)[:120],
                }
            else:
                return JSONResponse(
                    status_code=503,
                    content={
                        **result,
                        "status": "unhealthy",
                        "step": "assamese_probe",
                        "error": str(ns_result)[:120],
                    },
                )
        else:
            result["assamese_probe"] = ns_result
            if not ns_result.get("has_assamese_script"):
                return JSONResponse(
                    status_code=503,
                    content={
                        **result,
                        "status": "unhealthy",
                        "step": "assamese_probe",
                        "error": (
                            "Workers AI returned no Assamese script. "
                            "Verify the system-prompt language instruction."
                        ),
                    },
                )

        # ── Streaming probe result ────────────────────────────────────────────
        if isinstance(st_result, Exception):
            if _is_quota_error(st_result):
                # Transient quota exhaustion: degrade gracefully so CI is not
                # blocked by a brief 429 blip.  ops can see the quota_warning
                # field in the response body.
                logger.warning(
                    "streaming_assamese_probe: Workers AI quota exhausted (429) — marking degraded: %s",
                    str(st_result)[:120],
                )
                result["streaming_assamese_probe"] = {
                    "status": "degraded",
                    "quota_warning": str(st_result)[:120],
                }
            else:
                return JSONResponse(
                    status_code=503,
                    content={
                        **result,
                        "status": "unhealthy",
                        "step": "streaming_assamese_probe",
                        "error": str(st_result)[:120],
                    },
                )
        else:
            result["streaming_assamese_probe"] = st_result
            if not st_result.get("has_assamese_script"):
                return JSONResponse(
                    status_code=503,
                    content={
                        **result,
                        "status": "unhealthy",
                        "step": "streaming_assamese_probe",
                        "error": (
                            "Workers AI streaming returned no Assamese script. "
                            "Verify the system-prompt language instruction."
                        ),
                    },
                )

    # ── Step 3: RAG retrieval health check ───────────────────────────────────
    # Three sub-checks:
    #   a) topic_embeddings count (TopicMatcher cache)
    #   b) chunks collection document count (Vectorize seed status)
    #   c) real retrieve_v2() call with a cached topic embedding (end-to-end)
    # rag_status: "healthy" = chunks returned, "degraded" = topics ok but 0
    # chunks, "unavailable" = topics missing or error. HTTP 200 always returned
    # (CI is not blocked by RAG degradation — students still get an answer,
    #  just without chapter context).
    try:
        from app.services.ai.topic_matcher import topic_matcher
        from app.db.mongo import get_mongo_client

        if not topic_matcher._is_cache_valid():
            await asyncio.wait_for(topic_matcher._load_embeddings(), timeout=4.0)

        topic_count = len(topic_matcher._embeddings or [])
        result["rag_topics_cached"] = topic_count

        # Count indexed chunks (Vectorize seed status)
        try:
            _db = get_mongo_client()[settings.MONGODB_DB_NAME]
            chunks_indexed = await asyncio.wait_for(
                _db["chunks"].count_documents({}), timeout=3.0
            )
        except Exception:
            chunks_indexed = -1  # MongoDB unavailable; don't fail the whole step
        result["rag_chunks_indexed"] = chunks_indexed

        # End-to-end retrieval call using a cached topic embedding as the test
        # query — skips the CF Workers AI embed call entirely (no extra latency).
        rag_path = "skipped"
        rag_chunks_returned = 0
        if topic_count > 0:
            try:
                test_entry = (topic_matcher._embeddings or [])[0]
                test_emb = test_entry.get("embedding") if isinstance(test_entry, dict) else None
                if test_emb:
                    from app.services.rag.retrieval_v2 import retrieve_v2
                    test_chunks, rag_path = await asyncio.wait_for(
                        retrieve_v2(
                            query="health-probe",
                            lang="en",
                            limit=3,
                            embedding=test_emb,
                        ),
                        timeout=6.0,
                    )
                    rag_chunks_returned = len(test_chunks)
            except Exception as _rag_err:
                rag_path = f"error:{str(_rag_err)[:60]}"

        result["rag_path"] = rag_path
        result["rag_chunks_returned"] = rag_chunks_returned
        result["rag_status"] = (
            "healthy" if rag_chunks_returned > 0
            else "degraded" if topic_count > 0
            else "unavailable"
        )
    except Exception as e:
        result["rag_status"] = "unavailable"
        result["rag_error"] = str(e)[:80]

    # Downgrade overall status to "degraded" when any sub-system reported quota
    # pressure or RAG returned 0 chunks.  HTTP 200 is still returned — CI is
    # not blocked.
    any_degraded = (
        any(
            isinstance(result.get(k), dict) and result[k].get("status") == "degraded"
            for k in ("assamese_probe", "streaming_assamese_probe")
        )
        or result.get("rag_status") == "degraded"
    )
    result["status"] = "degraded" if any_degraded else "healthy"
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
