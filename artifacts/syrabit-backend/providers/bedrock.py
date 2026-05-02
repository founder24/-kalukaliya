"""providers.bedrock — AWS Bedrock LLM via Cloudflare AI Gateway (BYOK).

Routes chat completions to AWS Bedrock (Anthropic Claude) via the CF AI
Gateway, which handles AWS SigV4 request signing when BYOK is configured
in the Cloudflare dashboard with AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY.

CF AI Gateway slug: ``aws-bedrock``
Upstream endpoint:  ``/model/{model_id}/converse``

BYOK setup (CF dashboard → AI Gateway → Providers → AWS Bedrock):
  - Store your AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + AWS_REGION
  - Enable BYOK — CF substitutes them and signs requests with SigV4

Message format: OpenAI chat messages → Bedrock Converse API → string reply
"""
from __future__ import annotations

import logging
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

logger = logging.getLogger("providers.bedrock")

_MODEL_ID = "anthropic.claude-3-5-haiku-20241022-v1:0"
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
    """Build CF AI Gateway BYOK headers for Bedrock."""
    h: dict = {
        "Content-Type": "application/json",
        "cf-aig-byok-key": "true",
    }
    if CF_CACHE_TTL:
        h["cf-aig-cache-ttl"] = str(CF_CACHE_TTL)
    if CF_AI_GATEWAY_TOKEN:
        h["cf-aig-authorization"] = f"Bearer {CF_AI_GATEWAY_TOKEN}"
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
    url = f"{base}/model/{model_id}/converse"
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
    url = f"{base}/model/{vision_model_id}/converse"

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
