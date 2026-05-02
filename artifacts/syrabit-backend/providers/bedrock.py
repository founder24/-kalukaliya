"""providers.bedrock — AWS Bedrock LLM + feature services via Cloudflare AI Gateway (BYOK).

Routes:
  Chat (LLM):
    call_converse()         — Claude 3.5 Haiku via CF AI Gateway aws-bedrock BYOK slug
    call_converse_vision()  — Claude 3.5 Sonnet v2 multimodal via same slug

  Feature services (Task #256):
    call_tts()      — Amazon Polly TTS via bedrock-proxy Worker (SigV4)
    call_stt()      — Amazon Transcribe STT via bedrock-proxy Worker (SigV4)
    call_embed()    — Amazon Titan Embeddings v2 via CF AI Gateway aws-bedrock BYOK
    call_translate()— Amazon Translate via bedrock-proxy Worker (SigV4)

CF AI Gateway slug: ``aws-bedrock``
Bedrock-proxy Worker URL: ``BEDROCK_PROXY_URL`` env var (required for TTS/STT/Translate)

BYOK setup (CF dashboard → AI Gateway → Providers → AWS Bedrock):
  - Store your AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + AWS_REGION
  - Enable BYOK — CF substitutes them and signs requests with SigV4

Message format: OpenAI chat messages → Bedrock Converse API → string reply
"""
from __future__ import annotations

import base64 as _b64
import logging
import os as _os
from typing import Optional

import httpx

from config import (
    CF_GATEWAY_ENABLED,
    CF_CACHE_TTL,
    CF_AI_GATEWAY_TOKEN,
    BYOK_PLACEHOLDER,
    BEDROCK_PROXY_AUTH_TOKEN,
    _AWS_REGION,
    cf_gateway_url,
    is_cf_gateway_up,
)

logger = logging.getLogger("providers.bedrock")

_MODEL_ID = "amazon.nova-micro-v1:0"   # Task #267: Nova Micro — fastest/cheapest Bedrock LLM
_ANTHROPIC_VERSION = "bedrock-2023-05-31"
_TIMEOUT_S = 30.0

ENABLED: bool = CF_GATEWAY_ENABLED and bool(cf_gateway_url("bedrock"))

if ENABLED:
    logger.info("Bedrock LLM ready — model=%s gateway=CF-BYOK", _MODEL_ID)
else:
    logger.info("Bedrock LLM disabled (CF_GATEWAY_ENABLED not set or bedrock slug missing)")


def _base_url() -> str:
    """Return the Bedrock base URL — CF AI Gateway when enabled."""
    if is_cf_gateway_up():
        gw = cf_gateway_url("bedrock")
        if gw:
            return gw
    return ""


def _headers() -> dict:
    """Build CF AI Gateway Provider Keys headers for Bedrock.

    CF AI Gateway Provider Keys auto-injects AWS credentials — no cf-aig-byok-key needed.
    URL must include bedrock-runtime/{region}/model/{id} for CF to forward correctly.
    """
    h: dict = {
        "Content-Type": "application/json",
    }
    if CF_CACHE_TTL:
        h["cf-aig-cache-ttl"] = str(CF_CACHE_TTL)
    if CF_AI_GATEWAY_TOKEN:
        h["cf-aig-authorization"] = f"Bearer {CF_AI_GATEWAY_TOKEN}"
    return h


def _proxy_url() -> str:
    """Return the bedrock-proxy Worker URL from env (BEDROCK_PROXY_URL)."""
    return _os.environ.get("BEDROCK_PROXY_URL", "").strip().rstrip("/")


def _proxy_headers() -> dict:
    """Build HTTP headers for bedrock-proxy Worker calls.

    Adds ``Authorization: Bearer <token>`` when BEDROCK_PROXY_AUTH_TOKEN is set.
    The Worker validates this token via its own ``BEDROCK_PROXY_AUTH_TOKEN``
    wrangler secret binding, preventing unauthorised cost-incurring calls.
    """
    h: dict = {"Content-Type": "application/json"}
    if BEDROCK_PROXY_AUTH_TOKEN:
        h["Authorization"] = f"Bearer {BEDROCK_PROXY_AUTH_TOKEN}"
    return h


def _to_bedrock_messages(messages: list) -> tuple[Optional[str], list]:
    """Convert OpenAI chat messages to Bedrock Converse API format.

    Returns (system_prompt_or_None, bedrock_messages).
    System role is extracted separately as Bedrock requires it outside the
    messages array in the ``system`` field.
    """
    system_prompt: Optional[str] = None
    bedrock_msgs: list = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "") or ""
        if role == "system":
            system_prompt = content
        elif role in ("user", "assistant"):
            bedrock_msgs.append({
                "role": role,
                "content": [{"type": "text", "text": content}],
            })
    return system_prompt, bedrock_msgs


def _extract_text(response: dict) -> str:
    """Extract plain text from a Bedrock Converse API response."""
    try:
        output = response.get("output", {})
        message = output.get("message", {})
        for block in message.get("content", []):
            if block.get("type") == "text":
                return block.get("text", "").strip()
    except Exception:
        pass
    return ""


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


async def call_converse(
    messages: list,
    *,
    model: Optional[str] = None,
    max_tokens: int = 2048,
) -> str:
    """Call AWS Bedrock Converse API via CF AI Gateway BYOK.

    Raises RuntimeError if the gateway is unavailable or not configured.
    """
    base = _base_url()
    if not base:
        raise RuntimeError("bedrock: CF AI Gateway is down or aws-bedrock slug not configured")

    model_id = model or _MODEL_ID
    url = f"{base}/bedrock-runtime/{_AWS_REGION}/model/{model_id}/converse"
    system_prompt, bedrock_msgs = _to_bedrock_messages(messages)
    if not bedrock_msgs:
        raise ValueError("bedrock: no user/assistant messages to send")

    body: dict = {
        "messages": bedrock_msgs,
        "inferenceConfig": {
            "maxTokens": max_tokens,
            "temperature": 0.1,
        },
    }
    if system_prompt:
        body["system"] = [{"type": "text", "text": system_prompt}]

    client = _get_client()
    try:
        resp = await client.post(url, headers=_headers(), json=body)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise RuntimeError(
            f"bedrock: HTTP {status} from CF gateway — {exc.response.text[:200]}"
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError(f"bedrock: connection error via CF gateway — {exc}")

    return _extract_text(resp.json())


async def health_check() -> dict:
    """Return Bedrock provider readiness status."""
    if not ENABLED:
        return {"ok": False, "reason": "CF_GATEWAY_ENABLED not set or aws-bedrock slug missing"}
    base = _base_url()
    if not base:
        return {"ok": False, "reason": "CF AI Gateway currently down"}
    return {"ok": True, "model": _MODEL_ID, "gateway": base}


async def call_converse_vision(
    b64_image: str,
    mime_type: str = "image/jpeg",
    prompt: str = "Describe this image.",
    *,
    model: Optional[str] = None,
    max_tokens: int = 1024,
) -> str:
    """Analyse an image using Claude multimodal via Bedrock Converse API (CF gateway BYOK).

    Uses Claude 3.5 Sonnet v2 by default (supports vision via the Converse API).
    Raises RuntimeError if the CF gateway is unavailable or bedrock not configured.
    """
    base = _base_url()
    if not base:
        raise RuntimeError("bedrock vision: CF AI Gateway not available for vision")

    # Claude claude-3-5-sonnet supports multimodal via Converse API.
    vision_model_id = model or "anthropic.claude-3-5-sonnet-20241022-v2:0"
    url = f"{base}/bedrock-runtime/{_AWS_REGION}/model/{vision_model_id}/converse"

    # Bedrock Converse image format: image block with format + base64 bytes.
    img_format = mime_type.split("/")[-1].lower()
    if img_format == "jpg":
        img_format = "jpeg"

    body: dict = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "image": {
                            "format": img_format,
                            "source": {"bytes": b64_image},
                        }
                    },
                    {"text": prompt},
                ],
            }
        ],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0.0},
    }

    client = _get_client()
    try:
        resp = await client.post(url, headers=_headers(), json=body)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise RuntimeError(f"bedrock vision: HTTP {status} — {exc.response.text[:200]}")
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError(f"bedrock vision: connection error via CF gateway — {exc}")

    return _extract_text(resp.json())


# ── Task #256: Feature services ───────────────────────────────────────────────

async def call_tts(
    text: str,
    *,
    voice: Optional[str] = None,
    output_format: str = "mp3",
) -> bytes:
    """TTS via Amazon Polly, routed through the bedrock-proxy Worker (SigV4).

    The bedrock-proxy Worker signs requests with AWS SigV4 and forwards to the
    Amazon Polly /v1/speech endpoint. Requires BEDROCK_PROXY_URL env var.

    Voice selection priority:
      1. ``voice`` argument
      2. ``BEDROCK_POLLY_VOICE`` env var
      3. "Raveena" (Indian English Neural default)

    Raises RuntimeError if BEDROCK_PROXY_URL is not configured or the call fails.
    """
    proxy = _proxy_url()
    if not proxy:
        raise RuntimeError("bedrock tts: BEDROCK_PROXY_URL not configured")

    voice_id = voice or _os.environ.get("BEDROCK_POLLY_VOICE", "Raveena")
    client = _get_client()
    try:
        resp = await client.post(
            f"{proxy}/polly/synthesize",
            headers=_proxy_headers(),
            json={"text": text, "voice_id": voice_id, "output_format": output_format},
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"bedrock tts: HTTP {exc.response.status_code} from proxy — {exc.response.text[:200]}"
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError(f"bedrock tts: connection error to proxy — {exc}")
    return resp.content


async def call_stt(
    audio_bytes: bytes,
    *,
    mime_type: str = "audio/wav",
    language: str = "en-US",
) -> str:
    """STT via Amazon Transcribe, routed through the bedrock-proxy Worker (SigV4).

    The bedrock-proxy Worker accepts base64-encoded audio, uploads it to S3
    (configured via BEDROCK_S3_BUCKET Worker binding), starts a Transcribe job,
    polls for completion, and returns the transcript text.

    Requires BEDROCK_PROXY_URL env var. Raises RuntimeError if not configured
    or the call fails.
    """
    proxy = _proxy_url()
    if not proxy:
        raise RuntimeError("bedrock stt: BEDROCK_PROXY_URL not configured")

    audio_b64 = _b64.b64encode(audio_bytes).decode("ascii")
    client = _get_client()
    try:
        resp = await client.post(
            f"{proxy}/transcribe",
            headers=_proxy_headers(),
            json={
                "audio_b64": audio_b64,
                "mime_type": mime_type,
                "language_code": language,
            },
            timeout=httpx.Timeout(60.0),  # Transcribe jobs can take up to ~30s for short clips
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"bedrock stt: HTTP {exc.response.status_code} from proxy — {exc.response.text[:200]}"
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError(f"bedrock stt: connection error to proxy — {exc}")
    return resp.json().get("transcript", "")


async def call_embed(
    text: str,
    *,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> list:
    """Embed text via Amazon Titan Embeddings v2 through CF AI Gateway BYOK.

    Uses the ``amazon.titan-embed-text-v2:0`` model via the CF AI Gateway
    aws-bedrock BYOK slug (same path as Converse). No proxy Worker needed —
    Titan Embeddings is natively accessible via the CF gateway.

    Raises RuntimeError if CF gateway is unavailable or the embedding is empty.
    """
    base = _base_url()
    if not base:
        raise RuntimeError("bedrock embed: CF AI Gateway not available for Titan embeddings")

    titan_url = f"{base}/bedrock-runtime/{_AWS_REGION}/model/amazon.titan-embed-text-v2:0/invoke"
    client = _get_client()
    try:
        resp = await client.post(
            titan_url,
            headers=_headers(),
            json={"inputText": text},
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"bedrock embed: HTTP {exc.response.status_code} — {exc.response.text[:200]}"
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError(f"bedrock embed: connection error via CF gateway — {exc}")

    vec = resp.json().get("embedding", [])
    if not vec:
        raise RuntimeError("bedrock embed: empty embedding returned by Titan v2")
    return vec


async def call_translate(
    text: str,
    target_lang: str,
    source_lang: str = "en",
) -> str:
    """Translate text via Amazon Translate, routed through the bedrock-proxy Worker (SigV4).

    The bedrock-proxy Worker signs the request with AWS SigV4 and forwards to
    the Amazon Translate TranslateText API.

    ``target_lang`` and ``source_lang`` are ISO 639-1 language codes
    (e.g. "as", "hi", "en"). BCP-47 codes are normalised to the base code.

    Raises RuntimeError if BEDROCK_PROXY_URL is not configured or the call fails.
    """
    proxy = _proxy_url()
    if not proxy:
        raise RuntimeError("bedrock translate: BEDROCK_PROXY_URL not configured")

    # Normalise BCP-47 to ISO 639-1 (e.g. "as-IN" → "as")
    tgt = target_lang.split("-")[0] if "-" in target_lang else target_lang
    src = source_lang.split("-")[0] if "-" in source_lang else source_lang

    client = _get_client()
    try:
        resp = await client.post(
            f"{proxy}/translate",
            headers=_proxy_headers(),
            json={
                "text": text,
                "source_language_code": src,
                "target_language_code": tgt,
            },
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"bedrock translate: HTTP {exc.response.status_code} from proxy — {exc.response.text[:200]}"
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError(f"bedrock translate: connection error to proxy — {exc}")
    return resp.json().get("translated_text", "")
