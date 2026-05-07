"""providers.vertex_chat — Vertex AI Gemini 2.5 Flash chat dispatch (Task #554).

Re-introduces a hot-path Gemini chat surface that was removed in Task #490
when Vertex was scoped to `content_format` only. Task #554 makes Vertex
the head of `PROVIDER_PRIORITY['english_rag_chat']` (with Workers-AI
Llama-3.2-3B as the SOLE allowed fallback) so the perpetual $100/month
budget still uses GCP startup credits while they last; the credit-runway
selector in ``cost_caps._select_chat_primary`` flips the chain to
workers-first when the projected credit runway falls below 90 days.

Auth: GOOGLE_APPLICATION_CREDENTIALS_JSON service account JSON
(same blob ``vertex_format`` consumes). Region from VERTEX_LOCATION
(default ``us-central1``); model from VERTEX_GEMINI_MODEL
(default ``gemini-2.5-flash``).

Public API:

    await call_chat(messages, *, model=None, max_tokens=2048) -> str

Failure mode: raises ``RuntimeError`` on misconfig / HTTP error /
empty / safety-blocked response. Callers (``llm._dispatch_llm_for_feature``,
``call_with_provider_fallback``) own the fall-through to Workers-AI
(V4 §12 — no silent fallbacks).
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
    timeout_s: float = 30.0,
) -> str:
    """Non-streaming Vertex AI Gemini 2.5 Flash chat completion.

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
        or os.environ.get("VERTEX_GEMINI_MODEL", "gemini-2.5-flash")
        or "gemini-2.5-flash"
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
        async with httpx.AsyncClient(timeout=timeout_s) as c:
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
