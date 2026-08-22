"""
Admin wrapper for the chat-pipeline quality probe.

Runs the same probe logic as GET /health/chat-pipeline but authenticates
via admin session cookie instead of TRANSLATE_CRON_SECRET, so the admin
health dashboard can poll it without embedding the cron secret in the
browser.  Always returns HTTP 200 — the caller reads ``status`` / ``step``
fields directly.
"""

import asyncio
import time as _time
from typing import Dict, Any, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.v1.admin import require_admin_session, csrf_guard

import logging

logger = logging.getLogger(__name__)


def _is_quota_error(exc: Exception) -> bool:
    """Return True when *exc* is a transient Gemini quota exhaustion (HTTP 429).

    Inlined here (rather than imported from health.py) so this module has no
    dependency on private helpers in a sibling file — prevents ImportError if
    the two files land in different commits during a deploy.
    """
    err = str(exc).upper()
    return "429" in err or "RESOURCE_EXHAUSTED" in err or "QUOTA" in err


router = APIRouter(
    tags=["Admin Pipeline Health"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


@router.get("/health/chat-pipeline-probe")
async def admin_chat_pipeline_probe():
    """Admin view of the full chat-pipeline probe.

    Identical logic to /health/chat-pipeline (Steps 1–4) but protected
    by the admin session cookie so the dashboard can show
    ``streaming_assamese_probe.first_chunk_latency_ms`` without any
    frontend secret.

    Time budget (same as the public probe):
      Step 1 Workers AI: 30 s
      Steps 2 & 4     : 30 s (run in parallel)
      Step 3 RAG      :  4 s
      Total worst case: 64 s

    Returns HTTP 200 in all cases; inspect ``status`` / ``step`` for
    unhealthy detail.
    """
    result: Dict[str, Any] = {}

    # ── Step 1: Workers AI provider ping ─────────────────────────────────────
    try:
        from app.config import settings
        from app.services.ai.workers_ai_client import generate_with_workers_ai

        _ping_sys = "You are a test probe. Respond with exactly the single word PONG and nothing else."
        _ping_usr = "ping"
        provider = settings.CF_AI_MODEL
        t0 = _time.monotonic()
        ping_text = await asyncio.wait_for(
            generate_with_workers_ai(_ping_sys, _ping_usr, max_tokens=256),
            timeout=30.0,
        )

        result["provider"] = provider
        result["latency_ms"] = round((_time.monotonic() - t0) * 1000, 1)
        result["response_preview"] = (ping_text or "")[:40]

    except Exception as exc:
        result["status"] = "unhealthy"
        result["step"] = "ai_pipeline"
        result["error"] = str(exc)[:120]
        return JSONResponse(status_code=200, content=result)

    # ── Steps 2 & 4: Assamese quality probes (parallel) ──────────────────────
    from app.services.ai.workers_ai_client import (
        generate_with_workers_ai,
        workers_ai_client,
    )

    if not settings.EDGE_SHARED_SECRET:
        skip = {"status": "skipped", "reason": "Workers AI internal authentication is not configured"}
        result["assamese_probe"] = skip
        result["streaming_assamese_probe"] = skip
    else:
        async def _ns_probe() -> Dict[str, Any]:
            """Non-streaming Assamese probe via Workers AI."""
            sys_p = "তুমি এটা সহায়কাৰী শিক্ষামূলক সহায়ক। সদায় চমুকৈ অসমীয়া ভাষাত উত্তৰ দিয়া।"
            usr_p = "তুমি কোন?"  # "Who are you?"
            t = _time.monotonic()
            resp = await asyncio.wait_for(
                generate_with_workers_ai(sys_p, usr_p, is_assamese=True, max_tokens=256),
                timeout=30.0,
            )
            ms = round((_time.monotonic() - t) * 1000, 1)
            return {
                "has_assamese_script": any("\u0980" <= c <= "\u09ff" for c in (resp or "")),
                "latency_ms": ms,
                "response_preview": (resp or "")[:80],
            }

        async def _st_probe() -> Dict[str, Any]:
            """Streaming Assamese probe via Workers AI; measures TTFB."""
            sys_p = "তুমি এটা সহায়কাৰী শিক্ষামূলক সহায়ক। সদায় চমুকৈ অসমীয়া ভাষাত উত্তৰ দিয়া।"
            usr_p = "পোহৰ কি?"  # "What is light?"
            t = _time.monotonic()
            first_ms: Optional[float] = None
            chunks: list[str] = []

            async def _collect() -> None:
                nonlocal first_ms
                async for chunk in workers_ai_client.stream_generate_with_retry(
                    sys_p, usr_p, is_assamese=True, max_tokens=256
                ):
                    if first_ms is None:
                        first_ms = round((_time.monotonic() - t) * 1000, 1)
                    chunks.append(chunk)

            await asyncio.wait_for(_collect(), timeout=30.0)
            joined = "".join(chunks)
            total_ms = round((_time.monotonic() - t) * 1000, 1)
            probe: Dict[str, Any] = {
                "has_assamese_script": any("\u0980" <= c <= "\u09ff" for c in joined),
                "first_chunk_latency_ms": first_ms,
                "total_latency_ms": total_ms,
                "chunk_count": len(chunks),
                "response_preview": joined[:80],
            }
            if first_ms is not None and first_ms > 10_000:
                probe["ttfb_warning"] = (
                    f"TTFB {first_ms:.0f} ms exceeds 10 000 ms student threshold"
                )
                logger.warning(
                    "admin_pipeline_probe streaming: TTFB %.0f ms > 10 000 ms", first_ms
                )
            return probe

        ns_r, st_r = await asyncio.gather(
            _ns_probe(), _st_probe(), return_exceptions=True
        )

        def _fmt(r: Any) -> Dict[str, Any]:
            if isinstance(r, Exception):
                if _is_quota_error(r):
                    return {"status": "degraded", "quota_warning": str(r)[:120]}
                return {"status": "error", "error": str(r)[:120]}
            return r

        result["assamese_probe"] = _fmt(ns_r)
        result["streaming_assamese_probe"] = _fmt(st_r)

    # ── Step 3: RAG reachability ──────────────────────────────────────────────
    try:
        from app.services.ai.topic_matcher import topic_matcher

        if not topic_matcher._is_cache_valid():
            await asyncio.wait_for(topic_matcher._load_embeddings(), timeout=4.0)

        count = len(topic_matcher._embeddings or [])
        result["rag_topics_cached"] = count
        result["rag_status"] = "healthy" if count > 0 else "degraded"
    except Exception as exc:
        result["rag_status"] = "unavailable"
        result["rag_error"] = str(exc)[:80]

    # ── Overall status ────────────────────────────────────────────────────────
    any_degraded = any(
        isinstance(result.get(k), dict)
        and result[k].get("status") in ("degraded", "error")
        for k in ("assamese_probe", "streaming_assamese_probe")
    )
    result["status"] = "degraded" if any_degraded else "healthy"
    return JSONResponse(status_code=200, content=result)
