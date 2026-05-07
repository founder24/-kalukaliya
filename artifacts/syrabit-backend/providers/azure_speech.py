"""providers.azure_speech — Azure Speech (Neural TTS) + Azure Translator (Task #554).

Replaces the TTS / Translator helpers that previously lived inside
``providers/azure_openai.py``. Task #554 deleted the Azure OpenAI module
entirely (chat / Whisper STT / text-embedding-3-large were retired); the
two surviving Azure surfaces are:

  * Azure Neural TTS  — ``call_tts(text, voice=…)`` — uses
    ``AZURE_SPEECH_KEY`` + ``AZURE_SPEECH_REGION``.
  * Azure Translator  — ``call_translate(text, target_lang, source_lang)``
    — uses ``AZURE_TRANSLATOR_KEY`` (+ optional ``AZURE_TRANSLATOR_ENDPOINT``).

Neither helper reads ``AZURE_OPENAI_*`` env. Both raise ``RuntimeError``
on missing credentials / HTTP error so the dispatcher's exclusion-redraw
loop can advance to the next provider (V4 §12 — no silent fallbacks).
"""
from __future__ import annotations

import logging
import os as _os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_S = 30.0
_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(_TIMEOUT_S),
            http2=True,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _client


async def close() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


# ── Azure Neural TTS ──────────────────────────────────────────────────────────

async def call_tts(text: str, *, voice: Optional[str] = None) -> bytes:
    """TTS via Azure Neural TTS REST API.

    Returns MP3 audio bytes (audio-16khz-128kbitrate-mono-mp3).
    """
    key = _os.environ.get("AZURE_SPEECH_KEY", "").strip()
    region = _os.environ.get("AZURE_SPEECH_REGION", "").strip()
    if not key or not region:
        raise RuntimeError(
            "azure_speech tts: AZURE_SPEECH_KEY and AZURE_SPEECH_REGION must both be set"
        )
    voice_name = voice or _os.environ.get(
        "AZURE_TTS_VOICE", "en-IN-NeerjaExpressiveNeural"
    )
    parts = voice_name.split("-")
    xml_lang = f"{parts[0]}-{parts[1]}" if len(parts) >= 2 else "en-US"
    ssml = (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis"'
        f' xml:lang="{xml_lang}">'
        f'<voice name="{voice_name}">{text}</voice>'
        f'</speak>'
    )
    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-16khz-128kbitrate-mono-mp3",
        "User-Agent": "syrabit-backend",
    }
    client = _get_client()
    try:
        resp = await client.post(url, headers=headers, content=ssml.encode("utf-8"))
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"azure_speech tts: HTTP {exc.response.status_code} — {exc.response.text[:200]}"
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError(f"azure_speech tts: connection error — {exc}")
    return resp.content


# ── Azure Translator ──────────────────────────────────────────────────────────

async def call_translate(
    text: str,
    target_lang: str,
    source_lang: str = "en",
) -> str:
    """Translate text via Azure Translator REST API."""
    key = _os.environ.get("AZURE_TRANSLATOR_KEY", "").strip()
    if not key:
        raise RuntimeError("azure_translator: AZURE_TRANSLATOR_KEY not configured")
    endpoint = _os.environ.get(
        "AZURE_TRANSLATOR_ENDPOINT",
        "https://api.cognitive.microsofttranslator.com",
    ).rstrip("/")
    url = f"{endpoint}/translate?api-version=3.0&to={target_lang}&from={source_lang}"
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/json",
    }
    client = _get_client()
    try:
        resp = await client.post(url, headers=headers, json=[{"text": text}])
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"azure_translator: HTTP {exc.response.status_code} — {exc.response.text[:200]}"
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError(f"azure_translator: connection error — {exc}")
    data = resp.json()
    try:
        return data[0]["translations"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(
            f"azure_translator: unexpected response format — {str(data)[:200]}"
        )
