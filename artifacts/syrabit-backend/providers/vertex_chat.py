"""providers.vertex_chat — Vertex AI Gemini chat dispatch (Task #554, latency-opt 2026-05).

Primary English chat provider (hardcoded). Uses a module-level shared
httpx.AsyncClient (HTTP/2, connection pooling) to eliminate per-call TCP
handshake overhead.  Default model: ``gemini-2.0-flash`` (lower TTFT than
2.5-flash; same quality for exam-QA workloads).  Override via
``VERTEX_GEMINI_MODEL`` env var.

Auth: GOOGLE_APPLICATION_CREDENTIALS_JSON service account JSON.
Region from VERTEX_LOCATION (default ``us-central1``).

Public API:

    await call_chat(messages, *, model=None, max_tokens=2048) -> str

Failure mode: raises ``RuntimeError`` on misconfig / HTTP error /
empty / safety-blocked response. Callers own the fall-through to
Workers-AI (V4 §12 — no silent fallbacks).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_CACHE: dict = {}

# ── Shared pooled HTTP client (one per process, created lazily) ───────────────
# Eliminates per-call TCP + TLS handshake to aiplatform.googleapis.com.
# HTTP/2 multiplexing keeps concurrent requests on the same connection.
# No asyncio.Lock here — asyncio is single-threaded; if two coroutines both
# see _shared_client=None they each create one and the last write wins, which
# is harmless (both are valid clients with identical config).
_shared_client: Optional[httpx.AsyncClient] = None


async def _get_shared_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=120,
            ),
            http2=True,
        )
        logger.info("[vertex_chat] shared HTTP/2 client created")
    return _shared_client


def is_configured() -> bool:
    return bool((os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "") or "").strip())


async def _ensure_creds():
    """Lazy-load + refresh the SA credentials. Cached process-wide."""
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as _GAuthReq

    if "creds" not in _CACHE:
        sa_raw = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "") or "").strip()
        if not sa_raw:
            raise RuntimeError("vertex_chat: GOOGLE_APPLICATION_CREDENTIALS_JSON not set")
        sa_info = json.loads(sa_raw)
        creds = service_account.Credentials.from_service_account_info(
            sa_info, scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        _CACHE["creds"] = creds
        _CACHE["project"] = (
            os.environ.get("GCP_PROJECT_ID")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("VERTEX_PROJECT_ID")
            or sa_info.get("project_id")
        )
    creds = _CACHE["creds"]
    if not creds.valid:
        await asyncio.to_thread(creds.refresh, _GAuthReq())
    project = _CACHE["project"]
    if not project:
        raise RuntimeError("vertex_chat: project_id missing from SA JSON / env")
    return creds, project


def _split_system(messages: list) -> tuple[str, list]:
    """Split OpenAI-shaped messages into (system_text, gemini_contents).

    Vertex generateContent uses a separate ``systemInstruction`` field;
    every other message becomes a ``contents`` entry whose ``role`` is
    ``user`` or ``model`` (Vertex's name for ``assistant``).
    """
    system_parts: list[str] = []
    contents: list = []
    for m in messages or []:
        role = (m.get("role") or "").strip().lower()
        raw = m.get("content") or ""
        if isinstance(raw, list):
            text = " ".join(p.get("text", "") for p in raw if isinstance(p, dict))
        else:
            text = str(raw)
        if not text:
            continue
        if role == "system":
            system_parts.append(text)
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text}]})
        else:
            contents.append({"role": "user", "parts": [{"text": text}]})
    return "\n\n".join(system_parts).strip(), contents


async def call_chat(
    messages: list,
    *,
    model: Optional[str] = None,
    max_tokens: int = 2048,
    timeout_s: float = 20.0,
) -> str:
    """Non-streaming Vertex AI Gemini chat completion.

    Uses a shared HTTP/2 pooled client (no per-call TCP handshake).
    Default model: gemini-2.0-flash (lower TTFT than 2.5-flash).
    Returns the assistant text. Raises ``RuntimeError`` on misconfig /
    HTTP error / safety-blocked / empty Vertex response so the
    dispatcher's exclusion-redraw loop can advance to Workers-AI.
    """
    if not is_configured():
        raise RuntimeError("vertex_chat: GOOGLE_APPLICATION_CREDENTIALS_JSON not set")
    creds, project = await _ensure_creds()
    location = (os.environ.get("VERTEX_LOCATION", "us-central1") or "us-central1").strip()
    deployment = (
        model
        or os.environ.get("VERTEX_GEMINI_MODEL", "gemini-2.0-flash")
        or "gemini-2.0-flash"
    ).strip()
    url = (
        f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
        f"/locations/{location}/publishers/google/models/{deployment}:generateContent"
    )
    system_text, contents = _split_system(messages)
    if not contents:
        raise RuntimeError("vertex_chat: no user/assistant messages to send")
    payload: dict = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max(1, int(max_tokens)),
            "temperature": 0.2,
        },
    }
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}

    try:
        c = await _get_shared_client()
        r = await c.post(
            url,
            headers={
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"vertex_chat: HTTP {r.status_code} — {r.text[:300]}"
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"vertex_chat: transport error — {exc}")

    data = r.json()
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
    out = "".join(p.get("text", "") for p in parts).strip()
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.DOTALL).strip()
    if not out:
        raise RuntimeError("vertex_chat: Vertex returned empty / safety-blocked response")
    return out
