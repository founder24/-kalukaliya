"""Cloudflare Workers AI — primary LLM / translate / vision provider.

Replaces the legacy LLM tail (retired #347), Sarvam (translation), and
Vertex AI (legacy embeddings) as the primary tier. All calls go
through the Cloudflare AI REST API (api.cloudflare.com/client/v4/
accounts/{account_id}/ai/run/{model}) — no edge worker round-trip
needed from the backend.

**Embed note (V4 §2, 2026-05-06):** The 1024-dim primary embed path is
the *custom* Workers-AI worker at `embed.syrabit.ai` (Gemma-300M +
Qwen3-0.6B mean-pooled), NOT `@cf/baai/bge-large-en-v1.5` from this
catalog. The catalog entry below is retained as a *failover-failover*
target — only reachable when `workers_ai_custom`
have been excluded by the dispatcher. See `providers/workers_embed.py`
for the active primary client.

Models used (all available on Workers AI Enterprise):
  chat        → @cf/meta/llama-3.3-70b-instruct-fp8-fast (70B, fp8)
  chat_long   → @cf/openai/gpt-oss-120b (120B, for admin content gen)
  chat_code   → @cf/qwen/qwen2.5-coder-32b-instruct
  embed       → @cf/baai/bge-large-en-v1.5 (1024-dim — V4 failover-failover only)
  embed_multi → @cf/baai/bge-m3 (multilingual — V4 failover-failover only)
  translate   → @cf/ai4bharat/indictrans2-en-indic-1B (EN→Indic, free on CF)
  vision      → @cf/meta/llama-3.2-11b-vision-instruct
  stt         → @cf/openai/whisper-large-v3-turbo
  tts         → @cf/deepgram/aura-2-en / @cf/deepgram/aura-2-es
  rerank      → @cf/baai/bge-reranker-base

Auth: CLOUDFLARE_API_TOKEN (already in env) + CF_AI_GATEWAY_ACCOUNT_ID.
All calls route through CF AI Gateway when CF_AI_GATEWAY_ID is set —
enabling caching, rate-limiting, and cost visibility in the dashboard.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
_ACCOUNT_ID  = (
    os.environ.get("CF_AI_GATEWAY_ACCOUNT_ID", "").strip()
    or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
)
_API_TOKEN   = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
_GW_ID       = os.environ.get("CF_AI_GATEWAY_ID", "").strip()
_GW_TOKEN    = os.environ.get("CF_AI_GATEWAY_TOKEN", "").strip()
_GW_CACHE_TTL = int(os.environ.get("CF_AI_GATEWAY_CACHE_TTL", "86400"))

# Use AI Gateway URL when available — adds caching + logging in CF dashboard
if _ACCOUNT_ID and _GW_ID:
    _BASE_URL = f"https://gateway.ai.cloudflare.com/v1/{_ACCOUNT_ID}/{_GW_ID}/workers-ai"
else:
    _BASE_URL = f"https://api.cloudflare.com/client/v4/accounts/{_ACCOUNT_ID}/ai/run"

_ENABLED = bool(_ACCOUNT_ID and _API_TOKEN)

# ── Model catalog ─────────────────────────────────────────────────────────────
MODELS = {
    "chat":          "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "chat_long":     "@cf/openai/gpt-oss-120b",
    "chat_gpt_oss":  "@cf/openai/gpt-oss-20b",
    "chat_qwen":     "@cf/qwen/qwen2.5-72b-instruct",
    "chat_code":     "@cf/qwen/qwen2.5-coder-32b-instruct",
    "chat_fast":     "@cf/meta/llama-3.1-8b-instruct-fp8",
    "chat_ultrafast": "@cf/meta/llama-3.2-3b-instruct",
    "chat_indic":    "@cf/aisingapore/gemma-sea-lion-v4-27b-it",
    "embed":       "@cf/baai/bge-large-en-v1.5",
    "embed_multi": "@cf/baai/bge-m3",
    "embed_small": "@cf/baai/bge-small-en-v1.5",
    "translate":   "@cf/ai4bharat/indictrans2-en-indic-1B",
    "vision":      "@cf/meta/llama-3.2-11b-vision-instruct",
    "stt":         "@cf/openai/whisper-large-v3-turbo",
    "tts_en":      "@cf/deepgram/aura-2-en",
    "tts_es":      "@cf/deepgram/aura-2-es",
    "rerank":      "@cf/baai/bge-reranker-base",
    "guard":       "@cf/meta/llama-guard-3-8b",
}

# ── HTTP client ───────────────────────────────────────────────────────────────
_http_client: Optional[httpx.AsyncClient] = None
_http_lock = asyncio.Lock()


def _headers() -> Dict[str, str]:
    h = {"Authorization": f"Bearer {_API_TOKEN}"}
    if _GW_TOKEN:
        h["cf-aig-authorization"] = f"Bearer {_GW_TOKEN}"
        h["cf-aig-cache-ttl"] = str(_GW_CACHE_TTL)
    return h


async def _get_client() -> httpx.AsyncClient:
    global _http_client
    async with _http_lock:
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10, read=120, write=30, pool=30),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            )
    return _http_client


def _model_url(model_key: str) -> str:
    model = MODELS.get(model_key, model_key)
    if _ACCOUNT_ID and _GW_ID:
        return f"{_BASE_URL}/{model}"
    return f"https://api.cloudflare.com/client/v4/accounts/{_ACCOUNT_ID}/ai/run/{model}"


# ── Rate-limit retry config ────────────────────────────────────────────────────
# Workers AI returns 429 when a per-model RPM window is hit.  Back off with
# truncated exponential jitter: 1s → 2s → 4s (max 3 attempts).  5xx errors
# from the gateway (transient upstream timeouts) are retried once.
_POST_MAX_RETRIES = int(os.environ.get("CF_AI_POST_MAX_RETRIES", "3"))
_POST_RETRY_BASE_S = float(os.environ.get("CF_AI_POST_RETRY_BASE_S", "1.0"))


async def _post(model_key: str, payload: dict, *, stream: bool = False,
                timeout: float = 90.0) -> Any:
    """POST to a Workers AI model endpoint with automatic 429/5xx retry.

    Retry policy (non-streaming only):
      - 429 (rate-limited): exponential back-off with jitter, up to
        _POST_MAX_RETRIES attempts total.
      - 5xx (transient gateway error): single immediate retry.
      - Any other 4xx or final failure: raise_for_status().
    Streaming responses are NOT retried (caller controls the stream lifecycle).
    """
    import random  # stdlib, cheap import
    url = _model_url(model_key)
    client = await _get_client()
    headers = {**_headers(), "Content-Type": "application/json"}
    if stream:
        headers["Accept"] = "text/event-stream"

    last_resp = None
    for attempt in range(1, _POST_MAX_RETRIES + 1):
        resp = await client.post(url, json=payload, headers=headers, timeout=timeout)
        last_resp = resp

        # Task #403 — feed CF AI Gateway response headers (cf-aig-*) into the
        # observability counters whenever the request actually went through the
        # gateway. Pure observation: never raises, never blocks the chat path.
        # When the direct CF API URL is in use (no _GW_ID), the headers carry
        # no cf-aig-* values and record_aig_response() no-ops via summary.present.
        try:
            from ai_gateway_observability import record_aig_response
            record_aig_response(
                resp.headers,
                provider="workers-ai",
                model=MODELS.get(model_key, model_key),
            )
        except Exception:
            pass

        if stream:
            resp.raise_for_status()
            return resp

        if resp.status_code == 429:
            if attempt == _POST_MAX_RETRIES:
                break
            # Respect Retry-After header if present, else exponential back-off
            retry_after = resp.headers.get("retry-after", "")
            try:
                wait_s = float(retry_after)
            except (ValueError, TypeError):
                wait_s = _POST_RETRY_BASE_S * (2 ** (attempt - 1)) * (0.5 + random.random() * 0.5)
            wait_s = min(wait_s, 30.0)
            logger.warning(
                "[cf-ai] %s 429 rate-limited (attempt %d/%d) — waiting %.1fs",
                MODELS.get(model_key, model_key), attempt, _POST_MAX_RETRIES, wait_s,
            )
            await asyncio.sleep(wait_s)
            continue

        if resp.status_code >= 500 and attempt < _POST_MAX_RETRIES:
            wait_s = _POST_RETRY_BASE_S * attempt
            logger.warning(
                "[cf-ai] %s HTTP %d (attempt %d/%d) — retrying in %.1fs",
                MODELS.get(model_key, model_key), resp.status_code, attempt, _POST_MAX_RETRIES, wait_s,
            )
            await asyncio.sleep(wait_s)
            continue

        resp.raise_for_status()
        data = resp.json()
        if "result" in data:
            return data["result"]
        return data

    # All retries exhausted — surface the final 429 / 5xx
    last_resp.raise_for_status()
    data = last_resp.json()
    if "result" in data:
        return data["result"]
    return data


# ── Chat ──────────────────────────────────────────────────────────────────────
async def chat(
    messages: List[Dict[str, str]],
    *,
    model_key: str = "chat",
    max_tokens: int = 2048,
    temperature: float = 0.7,
    stream: bool = False,
) -> str:
    """Run a chat completion. Returns the full text response."""
    if not _ENABLED:
        raise RuntimeError("Cloudflare AI not configured (missing CLOUDFLARE_API_TOKEN or CF_AI_GATEWAY_ACCOUNT_ID)")
    t0 = time.perf_counter()
    payload: Dict[str, Any] = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if stream:
        payload["stream"] = True
    result = await _post(model_key, payload, stream=stream)
    if stream:
        return result
    # Workers AI native: {"response": "..."}
    # OpenAI-compat (gpt-oss-120b etc.): {"choices":[{"message":{"content":"..."}}]}
    r = result or {}
    text = r.get("response") or ""
    if not text:
        choices = r.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            text = msg.get("content") or msg.get("reasoning_content") or ""
    logger.debug(
        "[cf-ai] chat model=%s tokens≈%d dur=%.0fms",
        MODELS.get(model_key, model_key),
        len(text) // 4,
        (time.perf_counter() - t0) * 1000,
    )
    return text


async def chat_stream(
    messages: List[Dict[str, str]],
    *,
    model_key: str = "chat",
    max_tokens: int = 2048,
) -> AsyncIterator[str]:
    """Stream chat tokens as an async generator of delta strings."""
    if not _ENABLED:
        raise RuntimeError("Cloudflare AI not configured")
    payload: Dict[str, Any] = {
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    url = _model_url(model_key)
    client = await _get_client()
    headers = {**_headers(), "Content-Type": "application/json", "Accept": "text/event-stream"}
    async with client.stream("POST", url, json=payload, headers=headers, timeout=120.0) as resp:
        # Task #403 — feed CF AI Gateway response headers (cf-aig-*) into the
        # observability counters as soon as the streaming response object
        # exists, before any tokens flow. Best-effort, never blocks the stream.
        try:
            from ai_gateway_observability import record_aig_response
            record_aig_response(
                resp.headers,
                provider="workers-ai-stream",
                model=MODELS.get(model_key, model_key),
            )
        except Exception:
            pass
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            raw = line[6:]
            if raw.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(raw)
                # Workers AI native format: {"response": "..."}
                delta = chunk.get("response") or ""
                # OpenAI-compat format (e.g. gpt-oss-120b reasoning models):
                # {"choices":[{"delta":{"content":"..."}}]}
                # Also handle reasoning-only chunks where content is null but
                # reasoning_content holds the token.
                if not delta:
                    choices = chunk.get("choices") or []
                    if choices:
                        d = choices[0].get("delta") or {}
                        delta = d.get("content") or d.get("reasoning_content") or ""
                if delta:
                    yield delta
            except json.JSONDecodeError:
                continue


# ── Embeddings ────────────────────────────────────────────────────────────────
async def embed(
    texts: List[str],
    *,
    model_key: str = "embed",
) -> List[List[float]]:
    """Return embedding vectors for a list of texts."""
    if not _ENABLED:
        raise RuntimeError("Cloudflare AI not configured")
    result = await _post(model_key, {"text": texts})
    data = (result or {}).get("data", [])
    return data


async def embed_one(text: str, *, model_key: str = "embed") -> Optional[List[float]]:
    vecs = await embed([text], model_key=model_key)
    return vecs[0] if vecs else None


# ── Translation (Assamese / Indic) ────────────────────────────────────────────
_INDIC_TRANS2_LANG_MAP = {
    "as": "asm_Beng",  # Assamese
    "bn": "ben_Beng",  # Bengali
    "hi": "hin_Deva",  # Hindi
    "or": "ory_Orya",  # Odia
    "ta": "tam_Taml",  # Tamil
    "te": "tel_Telu",  # Telugu
    "kn": "kan_Knda",  # Kannada
    "ml": "mal_Mlym",  # Malayalam
    "mr": "mar_Deva",  # Marathi
    "gu": "guj_Gujr",  # Gujarati
    "pa": "pan_Guru",  # Punjabi
}
_INDIC_SRC = "eng_Latn"  # source: English


async def translate(
    text: str,
    target_lang: str = "as",
    source_lang: str = "en",
) -> Optional[str]:
    """Translate text using indictrans2-en-indic-1B (Workers AI).

    Falls back to None on error so callers can degrade gracefully.
    target_lang: ISO 639-1 code (e.g. 'as' for Assamese).
    """
    if not _ENABLED:
        return None
    tgt = _INDIC_TRANS2_LANG_MAP.get(target_lang.lower())
    if not tgt:
        logger.warning("[cf-ai] translate: unsupported target_lang=%s", target_lang)
        return None
    try:
        result = await _post("translate", {
            "text": text[:4000],
            "source_lang": _INDIC_SRC,
            "target_lang": tgt,
        }, timeout=60.0)
        translated = (result or {}).get("translated_text", "")
        return translated or None
    except Exception as exc:
        logger.warning("[cf-ai] translate failed: %s: %s", type(exc).__name__, str(exc)[:200])
        return None


async def translate_structured(
    content: dict,
    fields: List[str],
    target_lang: str = "as",
) -> dict:
    """Translate multiple string fields of a dict concurrently."""
    tasks = {
        f: translate(content.get(f, ""), target_lang=target_lang)
        for f in fields if content.get(f)
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    out = dict(content)
    for field, result in zip(tasks.keys(), results):
        if isinstance(result, str) and result:
            out[field] = result
    return out


# ── Vision / Image ────────────────────────────────────────────────────────────
async def analyze_image(
    image_bytes: bytes,
    prompt: str = "Describe this image in detail for an educational context.",
    mime_type: str = "image/jpeg",
) -> str:
    """Analyse an image with the llama-3.2-11b vision model."""
    if not _ENABLED:
        raise RuntimeError("Cloudflare AI not configured")
    b64 = base64.b64encode(image_bytes).decode()
    result = await _post("vision", {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                ],
            }
        ],
        "max_tokens": 1024,
    }, timeout=60.0)
    return (result or {}).get("response", "")


# ── Speech-to-Text ────────────────────────────────────────────────────────────
async def transcribe(audio_bytes: bytes) -> str:
    """Transcribe audio using whisper-large-v3-turbo."""
    if not _ENABLED:
        raise RuntimeError("Cloudflare AI not configured")
    audio_arr = list(audio_bytes)
    result = await _post("stt", {"audio": audio_arr}, timeout=120.0)
    return (result or {}).get("text", "")


# ── Text-to-Speech ────────────────────────────────────────────────────────────
async def speak(text: str, lang: str = "en") -> bytes:
    """Generate TTS audio using Deepgram Aura via Workers AI."""
    if not _ENABLED:
        raise RuntimeError("Cloudflare AI not configured")
    model_key = "tts_en" if lang == "en" else "tts_es"
    result = await _post(model_key, {"text": text[:4096]}, timeout=60.0)
    audio = (result or {}).get("audio")
    if isinstance(audio, list):
        return bytes(audio)
    if isinstance(audio, str):
        return base64.b64decode(audio)
    return b""


# ── Content Safety (Llama Guard) ──────────────────────────────────────────────
async def is_safe(text: str) -> bool:
    """Return True if text passes Llama Guard 3 8B safety check."""
    if not _ENABLED:
        return True
    try:
        result = await _post("guard", {
            "messages": [{"role": "user", "content": text[:4000]}],
            "max_tokens": 16,
        }, timeout=15.0)
        response = (result or {}).get("response", "").strip().lower()
        return response.startswith("safe")
    except Exception:
        return True


# ── Reranking ─────────────────────────────────────────────────────────────────
async def rerank(query: str, documents: List[str]) -> List[float]:
    """Return relevance scores for each document relative to query."""
    if not _ENABLED:
        return [0.0] * len(documents)
    result = await _post("rerank", {
        "query": query,
        "contexts": [{"text": d} for d in documents],
    }, timeout=30.0)
    scores_raw = (result or {}).get("response", [])
    if isinstance(scores_raw, list):
        return [float(r.get("score", 0.0)) for r in scores_raw]
    return [0.0] * len(documents)


# ── Health ────────────────────────────────────────────────────────────────────
async def health_check() -> dict:
    """Quick health probe using a minimal chat request."""
    if not _ENABLED:
        return {"ok": False, "error": "not_configured"}
    t0 = time.perf_counter()
    try:
        text = await chat(
            [{"role": "user", "content": "Say OK"}],
            model_key="chat_fast",
            max_tokens=4,
        )
        return {
            "ok": True,
            "latency_ms": round((time.perf_counter() - t0) * 1000),
            "model": MODELS["chat_fast"],
            "response": text,
        }
    except Exception as exc:
        return {
            "ok": False,
            "latency_ms": round((time.perf_counter() - t0) * 1000),
            "error": str(exc)[:200],
        }


# Alias used by llm.py's _stream_from_provider
stream_chat = chat_stream
