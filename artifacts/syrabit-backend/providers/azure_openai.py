"""providers.azure_openai — Azure OpenAI LLM + feature services with multi-endpoint failover.

Endpoint candidate chain (tried in order on each call):
  1. CF AI Gateway BYOK   — when ``CF_GATEWAY_ENABLED`` and the ``azure-openai``
                            slug is registered. CF injects the dashboard-stored
                            key (``cf-aig-byok-key: true`` + empty ``api-key``).
  2. Direct AZURE_OPENAI_ENDPOINT + ``AZURE_OPENAI_KEY_1``  (primary)
  3. Direct AZURE_OPENAI_ENDPOINT + ``AZURE_OPENAI_KEY_2``  (failover)

A retryable failure on candidate N (connect error, HTTP 401/403/408/425/429/5xx,
empty stream) advances to candidate N+1. The last candidate's exception is
re-raised so callers see a meaningful error.

Routes:
  Chat (LLM):
    call_chat()       — non-streaming chat completion
    stream_chat()     — async iterator of content tokens
  Feature services:
    call_tts()        — Azure Neural TTS (Speech REST API; AZURE_SPEECH_*)
    call_stt()        — Azure Whisper (Azure OpenAI /audio/transcriptions)
    call_embed()      — text-embedding-3-large (Azure OpenAI /embeddings)
    call_translate()  — Azure Translator (AZURE_TRANSLATOR_KEY)

Env vars:
  AZURE_OPENAI_ENDPOINT   — e.g. ``https://my-resource.openai.azure.com``
  AZURE_OPENAI_KEY_1      — primary subscription key (preferred)
  AZURE_OPENAI_KEY_2      — secondary subscription key (failover)
  AZURE_OPENAI_API_KEY    — legacy single-key fallback (used as KEY_1 when set)
  AZURE_OPENAI_MODEL      — deployment name (default: gpt-4o-mini)
  AZURE_OPENAI_API_VERSION— REST api-version (default: 2024-12-01-preview)
"""
from __future__ import annotations

import json
import logging
import os as _os
import re
from typing import AsyncIterator, Optional

import httpx

from config import (
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_DEPLOYMENT,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_KEY_1,
    AZURE_OPENAI_KEY_2,
    BYOK_PLACEHOLDER,
    CF_AI_GATEWAY_TOKEN,
    CF_CACHE_TTL,
    CF_GATEWAY_ENABLED,
    cf_gateway_url,
    is_cf_gateway_up,
)

logger = logging.getLogger("providers.azure_openai")

# Single source of truth lives in config.py (Task #290 — AZURE_OPENAI_DEPLOYMENT
# replaces AZURE_OPENAI_MODEL; legacy name still resolves via the config alias).
_MODEL = AZURE_OPENAI_DEPLOYMENT
_API_VERSION = AZURE_OPENAI_API_VERSION
_TIMEOUT_S = 30.0

# ── Direct-endpoint config (Task #290) ───────────────────────────────────────
_DIRECT_ENDPOINT = AZURE_OPENAI_ENDPOINT
_KEY_1 = AZURE_OPENAI_KEY_1
_KEY_2 = AZURE_OPENAI_KEY_2

# Status codes that justify advancing to the next candidate (transient/auth).
_RETRYABLE_STATUS = frozenset({401, 403, 408, 425, 429, 500, 502, 503, 504})

# Direct-endpoint mode is viable when we have an endpoint AND at least one key.
_DIRECT_ENABLED = bool(_DIRECT_ENDPOINT and (_KEY_1 or _KEY_2))
# CF AI Gateway path is intentionally DISABLED for Azure OpenAI.
#
# CF AI Gateway's azure-openai provider requires URLs of shape
#   /azure-openai/{resource-name}/{deployment}/chat/completions?api-version=...
# where {resource-name} maps to a per-resource subdomain
# (https://{resource-name}.openai.azure.com). Our Azure resource is provisioned
# on the shared regional endpoint (https://eastus.api.cognitive.microsoft.com)
# without a custom subdomain, so CF cannot route requests to it — every call
# returned HTTP 401 "wrong API endpoint" before always failing over to direct.
#
# Re-enabling requires creating an Azure OpenAI resource WITH a custom
# subdomain and pointing AZURE_OPENAI_ENDPOINT at it.
_GATEWAY_AVAILABLE = False

ENABLED: bool = _GATEWAY_AVAILABLE or _DIRECT_ENABLED

if ENABLED:
    _modes = []
    if _GATEWAY_AVAILABLE:
        _modes.append("CF-BYOK")
    if _KEY_1:
        _modes.append("direct(KEY_1)")
    if _KEY_2:
        _modes.append("direct(KEY_2)")
    logger.info(
        "Azure OpenAI ready — model=%s candidates=[%s]",
        _MODEL, ", ".join(_modes),
    )
else:
    logger.info(
        "Azure OpenAI disabled — neither CF AI Gateway azure-openai slug nor "
        "AZURE_OPENAI_ENDPOINT+AZURE_OPENAI_KEY_1/2 are configured."
    )


# ── Candidate chain ───────────────────────────────────────────────────────────

def _gateway_headers() -> dict:
    """CF AI Gateway BYOK headers for Azure OpenAI.

    Currently dead code — _GATEWAY_AVAILABLE is False because our Azure
    resource lives on the shared regional endpoint which CF's azure-openai
    provider does not support. Kept so re-enabling the gateway path (after
    provisioning a custom-subdomain Azure resource) is a one-line revert.
    """
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


def _direct_headers(key: str) -> dict:
    """Direct Azure endpoint headers — uses ``api-key`` subscription header."""
    return {"Content-Type": "application/json", "api-key": key}


def _candidates() -> list[tuple[str, str, dict]]:
    """Return the ordered list of (label, base_url, headers) to try.

    Order:
      1. CF AI Gateway (when up) — single attempt; gateway has its own retries.
      2. Direct endpoint with KEY_1 (primary).
      3. Direct endpoint with KEY_2 (failover).

    An empty list means the provider is fully unavailable and callers should
    raise. Re-evaluated per-call so transient gateway recovery is picked up.
    """
    out: list[tuple[str, str, dict]] = []
    if _GATEWAY_AVAILABLE and is_cf_gateway_up():
        gw = cf_gateway_url("azure_openai")
        if gw:
            out.append(("cf_byok", gw, _gateway_headers()))
    if _DIRECT_ENDPOINT:
        if _KEY_1:
            out.append(("direct_key_1", _DIRECT_ENDPOINT, _direct_headers(_KEY_1)))
        if _KEY_2:
            out.append(("direct_key_2", _DIRECT_ENDPOINT, _direct_headers(_KEY_2)))
    return out


def _multipart_headers(headers: dict) -> dict:
    """Strip Content-Type so httpx sets the multipart boundary itself."""
    return {k: v for k, v in headers.items() if k.lower() != "content-type"}


# ── HTTP client singleton ─────────────────────────────────────────────────────

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


# ── Chat ──────────────────────────────────────────────────────────────────────

# Reasoning-model deployments (OpenAI o-series + GPT-5 family) require a
# different request body shape than classic chat models:
#   - Use ``max_completion_tokens`` instead of ``max_tokens``.
#   - Only ``temperature=1`` (the default) is accepted — sending 0.1 returns
#     HTTP 400 ``unsupported_value``.
#   - They burn a large slice of the budget on hidden reasoning tokens, so
#     callers must pass enough headroom or the visible content will be empty
#     with ``finish_reason="length"``.
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")

# Reasoning effort tier passed to gpt-5 / o-series models. ``minimal`` cuts
# hidden-reasoning token spend (and therefore latency) ~3-5x vs the default
# ``medium`` for short user-facing replies. Override via env when a feature
# genuinely needs deeper reasoning (e.g. AZURE_REASONING_EFFORT=low|medium|high).
# Valid values: minimal | low | medium | high.
_REASONING_EFFORT = (_os.environ.get("AZURE_REASONING_EFFORT") or "minimal").strip().lower()
if _REASONING_EFFORT not in {"minimal", "low", "medium", "high"}:
    _REASONING_EFFORT = "minimal"


def _is_reasoning_model(deployment: str) -> bool:
    name = (deployment or "").lower()
    return any(name.startswith(p) for p in _REASONING_PREFIXES)


def _build_chat_body(
    messages: list, deployment: str, max_tokens: int, *, stream: bool = False
) -> dict:
    """Construct the chat-completions request body for the given deployment.

    Reasoning models use ``max_completion_tokens``, omit ``temperature`` (only
    the default 1 is accepted), and pass ``reasoning_effort`` to bound how
    many hidden reasoning tokens the model burns before answering. Classic
    models use ``max_tokens`` and the standard low-temperature setting.
    """
    body: dict = {"messages": messages}
    if _is_reasoning_model(deployment):
        body["max_completion_tokens"] = max_tokens
        body["reasoning_effort"] = _REASONING_EFFORT
    else:
        body["max_tokens"] = max_tokens
        body["temperature"] = 0.1
    if stream:
        body["stream"] = True
    return body


def _endpoint_misconfig_hint(
    endpoint: str, status: int, body: str, deployment: str = ""
) -> Optional[str]:
    """Return an actionable diagnostic for two distinct Azure failure modes:

    1. CATALOG endpoint: host is the bare catalog URL
       ``api.cognitive.microsoft.com`` (no region prefix). It exposes
       ``/openai/models`` but has NO deployments, so every chat call 404s.
       NOTE: regional shared endpoints like ``eastus.api.cognitive.microsoft.com``
       are valid Azure OpenAI endpoints when paired with a regional resource
       key — they are NOT the catalog URL and must not trip this hint.
    2. DEPLOYMENT NOT FOUND on an otherwise-valid endpoint: the deployment
       name doesn't exist on this resource (typo, deleted, or never created).

    Returned only on definitive matches; ambiguous failures yield None so the
    generic HTTP-status error is surfaced instead.
    """
    host = (endpoint or "").lower()
    body_txt = body or ""

    # Case 1: catalog endpoint — must be a 404 AND the bare catalog hostname
    # (no region prefix). Earlier versions fired this for ANY status code on
    # any host containing "api.cognitive.microsoft.com", which incorrectly
    # masked legitimate 400s (e.g. unsupported_parameter on reasoning models)
    # against the regional shared endpoint.
    if status == 404 and host.split("//", 1)[-1].startswith("api.cognitive.microsoft.com"):
        return (
            "azure_openai: endpoint is the BARE Cognitive Services CATALOG URL "
            "(api.cognitive.microsoft.com with no region prefix), not a "
            "per-resource Azure OpenAI endpoint. Catalog URLs host /openai/models "
            "but have NO deployments. FIX in Azure Portal → Azure OpenAI "
            "resource → Keys and Endpoint: use the per-resource URL like "
            "https://<your-resource>.openai.azure.com/ or the regional shared "
            "endpoint like https://<region>.api.cognitive.microsoft.com/, and "
            "create at least one deployment under Resource Management → Model "
            f"deployments. Current AZURE_OPENAI_ENDPOINT={endpoint!r}"
        )

    # Case 2: DeploymentNotFound on a valid-looking endpoint.
    if status == 404 and "DeploymentNotFound" in body_txt:
        return (
            f"azure_openai: deployment {deployment!r} does not exist on "
            f"endpoint {endpoint!r}. FIX in Azure Portal → your Azure OpenAI "
            "resource → Resource Management → Model deployments → create a "
            "deployment with this exact name, OR update AZURE_OPENAI_DEPLOYMENT "
            "to match an existing deployment name on this resource."
        )

    return None


async def call_chat(
    messages: list,
    *,
    model: Optional[str] = None,
    max_tokens: int = 2048,
) -> str:
    """Non-streaming chat completion with multi-candidate failover.

    Iterates the candidate chain, advancing on retryable HTTP status or
    connect errors. Re-raises the last candidate's error as RuntimeError.
    """
    chain = _candidates()
    if not chain:
        raise RuntimeError("azure_openai: no candidates available (gateway down and no direct keys)")

    deployment = model or _MODEL
    body = _build_chat_body(messages, deployment, max_tokens)
    last_err: Optional[Exception] = None
    client = _get_client()

    for label, base, headers in chain:
        url = f"{base}/openai/deployments/{deployment}/chat/completions?api-version={_API_VERSION}"
        try:
            resp = await client.post(url, headers=headers, json=body)
            # Task #383 — record CF AI Gateway response headers (cf-aig-*)
            # whenever the request actually went through the gateway. Pure
            # observation — never raises, never blocks the chat response.
            if label == "cf_byok":
                try:
                    from ai_gateway_observability import record_aig_response
                    record_aig_response(resp.headers, provider="azure_openai")
                except Exception:
                    pass
            if resp.status_code in _RETRYABLE_STATUS:
                last_err = RuntimeError(
                    f"azure_openai[{label}]: HTTP {resp.status_code} — {resp.text[:200]}"
                )
                logger.warning("%s — advancing to next candidate", last_err)
                continue
            if resp.status_code == 404:
                hint = _endpoint_misconfig_hint(base, 404, resp.text, deployment)
                if hint:
                    raise RuntimeError(hint)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"].get("content", "") or ""
            return re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
        except httpx.HTTPStatusError as exc:
            # Non-retryable status (e.g. 400 BadRequest, 404 DeploymentNotFound)
            # — the next key will fail identically. Fail fast with the most
            # actionable error message we can produce.
            hint = _endpoint_misconfig_hint(base, exc.response.status_code, exc.response.text, deployment)
            if hint:
                raise RuntimeError(hint)
            raise RuntimeError(
                f"azure_openai[{label}]: HTTP {exc.response.status_code} — {exc.response.text[:200]}"
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            last_err = RuntimeError(f"azure_openai[{label}]: connection error — {exc}")
        logger.warning("%s — advancing to next candidate", last_err)

    raise last_err if last_err else RuntimeError("azure_openai: all candidates exhausted")


async def stream_chat(
    messages: list,
    *,
    model: Optional[str] = None,
    max_tokens: int = 2048,
    timeout_s: float = 20.0,
) -> AsyncIterator[str]:
    """Stream content tokens with pre-first-token candidate failover.

    If a candidate fails BEFORE emitting any content token (HTTP error,
    connect error, or empty stream), we advance to the next candidate.
    A mid-stream failure (after first token) is propagated — we never
    silently double-stream tokens to the client.
    """
    chain = _candidates()
    if not chain:
        raise RuntimeError("azure_openai: no candidates available (gateway down and no direct keys)")

    deployment = model or _MODEL
    body = _build_chat_body(messages, deployment, max_tokens, stream=True)
    client = _get_client()
    last_err: Optional[Exception] = None

    for label, base, headers in chain:
        url = f"{base}/openai/deployments/{deployment}/chat/completions?api-version={_API_VERSION}"
        first_token_seen = False
        try:
            async with client.stream(
                "POST", url, headers=headers, json=body,
                timeout=httpx.Timeout(timeout_s),
            ) as resp:
                # Task #383 — capture cf-aig-* response headers for the
                # streaming path too. Headers are available the moment
                # the response object exists, before any tokens flow.
                if label == "cf_byok":
                    try:
                        from ai_gateway_observability import record_aig_response
                        record_aig_response(resp.headers, provider="azure_openai_stream")
                    except Exception:
                        pass
                if resp.status_code in _RETRYABLE_STATUS:
                    body_bytes = await resp.aread()
                    last_err = RuntimeError(
                        f"azure_openai[{label}]: HTTP {resp.status_code} — {body_bytes.decode(errors='replace')[:200]}"
                    )
                    logger.warning("%s — advancing to next candidate", last_err)
                    continue
                if resp.status_code >= 400:
                    body_bytes = await resp.aread()
                    raise RuntimeError(
                        f"azure_openai[{label}]: HTTP {resp.status_code} — {body_bytes.decode(errors='replace')[:200]}"
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                        choices = chunk.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        token = delta.get("content") or ""
                        if token:
                            first_token_seen = True
                            yield token
                    except json.JSONDecodeError:
                        continue
            if first_token_seen:
                return
            # Empty stream → try next candidate.
            last_err = RuntimeError(f"azure_openai[{label}]: empty stream")
            logger.warning("%s — advancing to next candidate", last_err)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            if first_token_seen:
                # Mid-stream failure — must propagate so caller can flag the user.
                raise RuntimeError(f"azure_openai[{label}]: mid-stream {type(exc).__name__}: {exc}")
            last_err = RuntimeError(f"azure_openai[{label}]: connection error — {exc}")
            logger.warning("%s — advancing to next candidate", last_err)

    raise last_err if last_err else RuntimeError("azure_openai: all candidates exhausted")


# ── Health ────────────────────────────────────────────────────────────────────

async def health_check() -> dict:
    """Return Azure OpenAI provider readiness with mode breakdown."""
    if not ENABLED:
        return {
            "ok": False,
            "reason": "no candidates — set CF AI Gateway azure-openai slug "
                      "or AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_KEY_1/2",
        }
    chain = _candidates()
    return {
        "ok": bool(chain),
        "model": _MODEL,
        "candidates": [c[0] for c in chain],
        "gateway_available": _GATEWAY_AVAILABLE,
        "direct_available": _DIRECT_ENABLED,
        "key_1_set": bool(_KEY_1),
        "key_2_set": bool(_KEY_2),
    }


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
    """STT via Azure Whisper endpoint with multi-candidate failover."""
    chain = _candidates()
    if not chain:
        raise RuntimeError("azure_openai stt: no candidates available")

    deployment = model or "whisper"
    files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
    data = {
        "model": deployment,
        "language": language.split("-")[0] if "-" in language else language,
    }
    client = _get_client()
    last_err: Optional[Exception] = None

    for label, base, headers in chain:
        url = f"{base}/openai/deployments/{deployment}/audio/transcriptions?api-version={_API_VERSION}"
        try:
            resp = await client.post(url, headers=_multipart_headers(headers), files=files, data=data)
            if resp.status_code in _RETRYABLE_STATUS:
                last_err = RuntimeError(
                    f"azure_openai stt[{label}]: HTTP {resp.status_code} — {resp.text[:200]}"
                )
                logger.warning("%s — advancing to next candidate", last_err)
                continue
            resp.raise_for_status()
            return resp.json().get("text", "")
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"azure_openai stt[{label}]: HTTP {exc.response.status_code} — {exc.response.text[:200]}"
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            last_err = RuntimeError(f"azure_openai stt[{label}]: connection error — {exc}")
        logger.warning("%s — advancing to next candidate", last_err)

    raise last_err if last_err else RuntimeError("azure_openai stt: all candidates exhausted")


async def call_embed(
    text: str,
    *,
    model: Optional[str] = None,
) -> list:
    """Embed text via Azure OpenAI text-embedding-3-large with failover."""
    chain = _candidates()
    if not chain:
        raise RuntimeError("azure_openai embed: no candidates available")

    deployment = model or "text-embedding-3-large"
    body = {"input": text}
    client = _get_client()
    last_err: Optional[Exception] = None

    for label, base, headers in chain:
        url = f"{base}/openai/deployments/{deployment}/embeddings?api-version={_API_VERSION}"
        try:
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code in _RETRYABLE_STATUS:
                last_err = RuntimeError(
                    f"azure_openai embed[{label}]: HTTP {resp.status_code} — {resp.text[:200]}"
                )
                logger.warning("%s — advancing to next candidate", last_err)
                continue
            resp.raise_for_status()
            data = resp.json()
            vec = (data.get("data") or [{}])[0].get("embedding", [])
            if not vec:
                last_err = RuntimeError(f"azure_openai embed[{label}]: empty embedding returned")
                logger.warning("%s — advancing to next candidate", last_err)
                continue
            return vec
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"azure_openai embed[{label}]: HTTP {exc.response.status_code} — {exc.response.text[:200]}"
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            last_err = RuntimeError(f"azure_openai embed[{label}]: connection error — {exc}")
        logger.warning("%s — advancing to next candidate", last_err)

    raise last_err if last_err else RuntimeError("azure_openai embed: all candidates exhausted")


async def call_translate(
    text: str,
    target_lang: str,
    source_lang: str = "en",
) -> str:
    """Translate text via Azure Translator REST API.

    Requires AZURE_TRANSLATOR_KEY env var.
    AZURE_TRANSLATOR_ENDPOINT defaults to https://api.cognitive.microsofttranslator.com.
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
