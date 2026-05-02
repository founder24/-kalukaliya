"""providers.workers_indic — CF Workers AI IndicTrans2 for Assamese last-resort (Task #267).

IndicTrans2 models hosted on Cloudflare Workers AI:
  en-indic:  @cf/ai4bharat/indictrans2-en-indic-1b  — English → Indic (Assamese)
  indic-en:  @cf/ai4bharat/indictrans2-indic-en-1b  — Indic (Assamese) → English

These are purpose-built translation models, not general chat LLMs.  They are
the mandatory last-resort for all Assamese pools (assamese_rag_chat,
assamese_content, translate) when both Sarvam and Gemini are unavailable.

Usage in _dispatch_llm_for_feature (llm.py):
  When ``provider == "workers_ai_indic"``, the dispatcher calls
  ``call_indic_trans(src_text, direction="en-indic")``.

API format (Cloudflare Workers AI):
  POST /accounts/{account_id}/ai/run/@cf/ai4bharat/indictrans2-en-indic-1b
  Body: {"text": "...", "source_lang": "eng_Latn", "target_lang": "asm_Beng"}

Language codes follow the FLORES-200 convention used by IndicTrans2:
  English:  eng_Latn
  Assamese: asm_Beng
"""
from __future__ import annotations

import logging
import os
from typing import Literal

import httpx

logger = logging.getLogger("providers.workers_indic")

# ── Model identifiers ───────────────────────────────────────────────────────
_MODEL_EN_INDIC = "@cf/ai4bharat/indictrans2-en-indic-1b"
_MODEL_INDIC_EN = "@cf/ai4bharat/indictrans2-indic-en-1b"

# FLORES-200 language codes used by IndicTrans2
_LANG_ENG  = "eng_Latn"
_LANG_ASM  = "asm_Beng"

# ── CF account credentials ──────────────────────────────────────────────────
_ACCOUNT_ID = os.environ.get("CF_AI_GATEWAY_ACCOUNT_ID", "").strip()
_API_TOKEN  = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
ENABLED: bool = bool(_ACCOUNT_ID and _API_TOKEN)

if ENABLED:
    logger.info(
        "workers_indic: IndicTrans2 ready "
        "(en-indic=%s, indic-en=%s)",
        _MODEL_EN_INDIC, _MODEL_INDIC_EN,
    )
else:
    logger.info(
        "workers_indic: disabled — "
        "CF_AI_GATEWAY_ACCOUNT_ID or CLOUDFLARE_API_TOKEN not set"
    )

_TIMEOUT_S = 30.0

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(_TIMEOUT_S),
            http2=True,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )
    return _client


async def close() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


async def call_indic_trans(
    text: str,
    *,
    direction: Literal["en-indic", "indic-en"] = "en-indic",
    target_lang: str = _LANG_ASM,
    source_lang: str | None = None,
) -> str:
    """Translate *text* using CF Workers AI IndicTrans2.

    Args:
        text:        Source text to translate.
        direction:   ``"en-indic"`` for English → Assamese (default),
                     ``"indic-en"`` for Assamese → English.
        target_lang: FLORES-200 target language code (default: ``"asm_Beng"``).
        source_lang: FLORES-200 source language code (auto-derived from direction).

    Returns:
        Translated text string.

    Raises:
        RuntimeError: If CF credentials are missing or the API call fails.
    """
    if not ENABLED:
        raise RuntimeError(
            "workers_indic: CF_AI_GATEWAY_ACCOUNT_ID or CLOUDFLARE_API_TOKEN not set"
        )

    if direction == "en-indic":
        model  = _MODEL_EN_INDIC
        src    = source_lang or _LANG_ENG
        tgt    = target_lang or _LANG_ASM
    else:
        model  = _MODEL_INDIC_EN
        src    = source_lang or _LANG_ASM
        tgt    = target_lang or _LANG_ENG

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{_ACCOUNT_ID}"
        f"/ai/run/{model}"
    )
    payload = {
        "text":        text,
        "source_lang": src,
        "target_lang": tgt,
    }
    headers = {
        "Authorization": f"Bearer {_API_TOKEN}",
        "Content-Type":  "application/json",
    }

    client = _get_client()
    try:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise RuntimeError(
            f"workers_indic: HTTP {status} from CF API — {exc.response.text[:200]}"
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError(f"workers_indic: connection error — {exc}")

    data = resp.json()
    translated: str = (
        (data.get("result") or {}).get("translated_text")
        or data.get("translated_text")
        or ""
    )
    if not translated:
        raise RuntimeError(
            f"workers_indic: empty translation returned — raw={str(data)[:200]}"
        )

    logger.info(
        "workers_indic: %s translated %d chars → %d chars",
        direction, len(text), len(translated),
    )
    return translated.strip()


async def health_check() -> dict:
    """Return IndicTrans2 provider readiness status."""
    if not ENABLED:
        return {
            "ok": False,
            "reason": "CF_AI_GATEWAY_ACCOUNT_ID or CLOUDFLARE_API_TOKEN not set",
        }
    return {
        "ok": True,
        "models": {
            "en_indic": _MODEL_EN_INDIC,
            "indic_en": _MODEL_INDIC_EN,
        },
    }
