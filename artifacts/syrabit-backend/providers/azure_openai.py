"""providers.azure_openai — Azure OpenAI LLM via Cloudflare AI Gateway (BYOK).

Routes chat completions to Azure OpenAI via the CF AI Gateway, which handles
API key injection when BYOK is configured in the Cloudflare dashboard.

CF AI Gateway slug: ``azure-openai``
Upstream endpoint:  OpenAI-compatible /chat/completions

BYOK setup (CF dashboard → AI Gateway → Providers → Azure OpenAI):
  - Store your Azure OpenAI API key + endpoint
  - Enable BYOK — CF forwards the key and routes to your Azure deployment

Model: gpt-4o-mini (cost-efficient; swap via AZURE_OPENAI_MODEL env var)
"""
from __future__ import annotations

import logging
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

import os as _os
_MODEL = _os.environ.get("AZURE_OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
_API_VERSION = "2024-02-01"
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

    url = f"{base}/chat/completions?api-version={_API_VERSION}"
    body = {
        "model": model or _MODEL,
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
