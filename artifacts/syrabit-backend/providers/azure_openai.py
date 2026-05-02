"""providers.azure_openai — Azure OpenAI LLM + feature services via Cloudflare AI Gateway (BYOK).

Routes:
  Chat (LLM):
    call_chat()     — GPT-4o-mini via CF AI Gateway azure-openai BYOK slug

  Feature services (Task #256):
    call_tts()      — Azure Neural TTS via Azure Speech Services REST API
    call_stt()      — Azure Whisper via Azure OpenAI endpoint (CF BYOK)
    call_embed()    — text-embedding-3-large via Azure OpenAI endpoint (CF BYOK)
    call_translate()— Azure Translator REST API (AZURE_TRANSLATOR_KEY)

CF AI Gateway slug: ``azure-openai``

BYOK setup (CF dashboard → AI Gateway → Providers → Azure OpenAI):
  - Store your Azure OpenAI API key + endpoint
  - Enable BYOK — CF forwards the key and routes to your Azure deployment

Additional env vars for feature services (Task #256):
  AZURE_SPEECH_KEY      — Azure Cognitive Services Speech key
  AZURE_SPEECH_REGION   — Azure region (e.g. "eastus")
  AZURE_TTS_VOICE       — Azure Neural TTS voice name (default: en-IN-NeerjaExpressiveNeural)
  AZURE_TRANSLATOR_KEY  — Azure Translator API key
  AZURE_TRANSLATOR_ENDPOINT — Azure Translator endpoint (default: https://api.cognitive.microsofttranslator.com)

Model: gpt-4o-mini (cost-efficient; swap via AZURE_OPENAI_MODEL env var)
"""
from __future__ import annotations

import logging
import os as _os
import re
from typing import Optional

import httpx

from config import (
    CF_GATEWAY_ENABLED,
    CF_CACHE_TTL,
    CF_AI_GATEWAY_TOKEN,
    BYOK_PLACEHOLDER,
    cf_gateway_url,
    is_cf_gateway_up,
)

logger = logging.getLogger("providers.azure_openai")

_MODEL = _os.environ.get("AZURE_OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"  # Task #267: gpt-4.1-mini — highest TPS on Azure
_API_VERSION = "2024-12-01-preview"
_TIMEOUT_S = 30.0

ENABLED: bool = CF_GATEWAY_ENABLED and bool(cf_gateway_url("azure_openai"))

if ENABLED:
    logger.info("Azure OpenAI LLM ready — model=%s gateway=CF-BYOK", _MODEL)
else:
    logger.info("Azure OpenAI LLM disabled (CF_GATEWAY_ENABLED not set or azure-openai slug missing)")


def _base_url() -> str:
    """Return the Azure OpenAI base URL — CF AI Gateway when enabled."""
    if is_cf_gateway_up():
        gw = cf_gateway_url("azure_openai")
        if gw:
            return gw
    return ""


def _headers() -> dict:
    """Build CF AI Gateway BYOK headers for Azure OpenAI."""
    h: dict = {
        "Content-Type": "application/json",
        "api-key": BYOK_PLACEHOLDER,
        "cf-aig-byok-key": "true",
        "Authorization": "",
    }
    if CF_CACHE_TTL:
        h["cf-aig-cache-ttl"] = str(CF_CACHE_TTL)
    if CF_AI_GATEWAY_TOKEN:
        h["cf-aig-authorization"] = f"Bearer {CF_AI_GATEWAY_TOKEN}"
    return h


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


async def call_chat(
    messages: list,
    *,
    model: Optional[str] = None,
    max_tokens: int = 2048,
) -> str:
    """Call Azure OpenAI chat/completions via CF AI Gateway BYOK.

    Raises RuntimeError if the gateway is unavailable or not configured.
    """
    base = _base_url()
    if not base:
        raise RuntimeError("azure_openai: CF AI Gateway is down or azure-openai slug not configured")

    # Azure OpenAI requires the deployment name in the URL path.
    # CF AI Gateway forwards: {gateway}/openai/deployments/{deployment}/chat/completions
    deployment = model or _MODEL
    url = f"{base}/openai/deployments/{deployment}/chat/completions?api-version={_API_VERSION}"
    body = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }

    client = _get_client()
    try:
        resp = await client.post(url, headers=_headers(), json=body)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise RuntimeError(
            f"azure_openai: HTTP {status} from CF gateway — {exc.response.text[:200]}"
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError(f"azure_openai: connection error via CF gateway — {exc}")

    data = resp.json()
    content = data["choices"][0]["message"].get("content", "") or ""
    return re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()


async def health_check() -> dict:
    """Return Azure OpenAI provider readiness status."""
    if not ENABLED:
        return {"ok": False, "reason": "CF_GATEWAY_ENABLED not set or azure-openai slug missing"}
    base = _base_url()
    if not base:
        return {"ok": False, "reason": "CF AI Gateway currently down"}
    return {"ok": True, "model": _MODEL, "gateway": base}


# ── Task #256: Feature services ───────────────────────────────────────────────

async def call_tts(
    text: str,
    *,
    voice: Optional[str] = None,
) -> bytes:
    """TTS via Azure Neural TTS REST API (Azure Speech Services).

    Requires AZURE_SPEECH_KEY and AZURE_SPEECH_REGION env vars.
    Voice selection priority:
      1. ``voice`` argument
      2. ``AZURE_TTS_VOICE`` env var
      3. "en-IN-NeerjaExpressiveNeural" (Indian English neural default)

    Returns MP3 audio bytes (audio-16khz-128kbitrate-mono-mp3).
    Raises RuntimeError if not configured or the call fails.
    """
    key = _os.environ.get("AZURE_SPEECH_KEY", "").strip()
    region = _os.environ.get("AZURE_SPEECH_REGION", "").strip()
    if not key or not region:
        raise RuntimeError(
            "azure_openai tts: AZURE_SPEECH_KEY and AZURE_SPEECH_REGION must both be set"
        )

    voice_name = voice or _os.environ.get(
        "AZURE_TTS_VOICE", "en-IN-NeerjaExpressiveNeural"
    )
    # Detect xml:lang from voice name prefix (e.g. "en-IN-..." → "en-IN")
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
            f"azure_openai tts: HTTP {exc.response.status_code} — {exc.response.text[:200]}"
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError(f"azure_openai tts: connection error — {exc}")
    return resp.content


async def call_stt(
    audio_bytes: bytes,
    *,
    language: str = "en-US",
    model: Optional[str] = None,
) -> str:
    """STT via Azure Whisper endpoint through CF AI Gateway BYOK.

    Uses the Azure OpenAI /audio/transcriptions endpoint (Whisper model).
    Requires the CF AI Gateway azure-openai slug to be configured.

    Raises RuntimeError if CF gateway is unavailable or the call fails.
    """
    base = _base_url()
    if not base:
        raise RuntimeError("azure_openai stt: CF AI Gateway not available")

    url = f"{base}/audio/transcriptions?api-version={_API_VERSION}"
    # Multipart: strip Content-Type to let httpx set the boundary
    hdrs = {k: v for k, v in _headers().items() if k.lower() != "content-type"}
    files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
    data = {"model": model or "whisper", "language": language.split("-")[0] if "-" in language else language}

    client = _get_client()
    try:
        resp = await client.post(url, headers=hdrs, files=files, data=data)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"azure_openai stt: HTTP {exc.response.status_code} — {exc.response.text[:200]}"
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError(f"azure_openai stt: connection error — {exc}")
    return resp.json().get("text", "")


async def call_embed(
    text: str,
    *,
    model: Optional[str] = None,
) -> list:
    """Embed text via Azure OpenAI text-embedding-3-large through CF AI Gateway BYOK.

    Uses the Azure OpenAI /embeddings endpoint.
    Requires the CF AI Gateway azure-openai slug to be configured.

    Returns a list of floats.
    Raises RuntimeError if CF gateway is unavailable or the embedding is empty.
    """
    base = _base_url()
    if not base:
        raise RuntimeError("azure_openai embed: CF AI Gateway not available")

    url = f"{base}/embeddings?api-version={_API_VERSION}"
    body = {
        "model": model or "text-embedding-3-large",
        "input": text,
    }
    client = _get_client()
    try:
        resp = await client.post(url, headers=_headers(), json=body)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"azure_openai embed: HTTP {exc.response.status_code} — {exc.response.text[:200]}"
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError(f"azure_openai embed: connection error — {exc}")

    data = resp.json()
    vec = (data.get("data") or [{}])[0].get("embedding", [])
    if not vec:
        raise RuntimeError("azure_openai embed: empty embedding returned")
    return vec


async def call_translate(
    text: str,
    target_lang: str,
    source_lang: str = "en",
) -> str:
    """Translate text via Azure Translator REST API.

    Requires AZURE_TRANSLATOR_KEY env var.
    AZURE_TRANSLATOR_ENDPOINT defaults to https://api.cognitive.microsofttranslator.com.

    ``target_lang`` / ``source_lang`` are BCP-47 codes (e.g. "as", "hi-IN", "en").
    The function passes them as-is to the Translator API.

    Raises RuntimeError if AZURE_TRANSLATOR_KEY is not configured or the call fails.
    """
    key = _os.environ.get("AZURE_TRANSLATOR_KEY", "").strip()
    if not key:
        raise RuntimeError("azure_openai translate: AZURE_TRANSLATOR_KEY not configured")

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
            f"azure_openai translate: HTTP {exc.response.status_code} — {exc.response.text[:200]}"
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError(f"azure_openai translate: connection error — {exc}")

    data = resp.json()
    try:
        return data[0]["translations"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(
            f"azure_openai translate: unexpected response format — {str(data)[:200]}"
        )
