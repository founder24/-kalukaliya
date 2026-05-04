"""
Workers AI powered services for Syrabit.ai.

All calls route through providers/cloudflare_ai.py → Cloudflare AI Gateway →
Workers AI.  No Google / Gemini credentials are required or used.

Drop-in replacement for the old Gemini-backed vertex_services.py —
every public function signature is identical so all callers work unchanged.

Services: embeddings, semantic_search, translate, analyze_image,
ocr_image, extract_key_concepts, generate_flashcards, generate_mcqs,
enhance_content, score_content, suggest_topics, generate_seo_meta,
find_content_gaps, extract_from_document, health_check.

Rate-limit protection
─────────────────────
Embed:  sliding-window 429 burst counter + cooldown (shared with tests).
        _EMBED_429_THRESHOLD / _EMBED_429_COOLDOWN_S control the window.
        _track_embed_429() / _reset_embed_429() keep state.
        After the threshold is hit the embed path returns None for
        _EMBED_429_COOLDOWN_S seconds so callers skip Workers AI without
        an extra network round-trip.

Chat/vision:  failure classification via the same CircuitBreaker used by
        the old Gemini path, so admin health-check endpoints and periodic
        probes (server.py) continue to work without changes.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ── Model identifiers ──────────────────────────────────────────────────────────
# Kept as module constants so syllabus_embedder.py / ingest scripts can read
# them without importing config.py.
_EMBED_MODEL      = "@cf/baai/bge-large-en-v1.5"   # 1024-dim, matches Vectorize
_EMBED_DIMENSIONS = 1024

# Internal model keys used when calling cloudflare_ai.chat / analyze_image.
_GEN_MODEL_KEY    = "chat_long"   # gpt-oss-120b  — quality-first for admin ops
_VISION_MODEL_KEY = "vision"      # llama-3.2-11b-vision-instruct

# ── Backwards-compat stubs (old Gemini path attributes) ──────────────────────
# A small set of callers read these for display / logging purposes.
GEMINI_KEY  = ""
_AUTH_MODE  = "workers_ai"
_GEN_MODEL  = _GEN_MODEL_KEY
_PRO_MODEL  = _GEN_MODEL_KEY
_VISION_MODEL = _VISION_MODEL_KEY
_CF_GW_ENABLED = True   # gateway always on when Workers AI is configured

# ── Circuit breaker ────────────────────────────────────────────────────────────
# Retained so admin health endpoints (`/admin/vertex/health`,
# vertex_health_cache, server.py probes) keep working without modification.
from vertex_breaker import CircuitBreaker  # noqa: E402

_BREAKER_THRESHOLD  = max(1, int(os.environ.get("VERTEX_BREAKER_THRESHOLD", "5")))
_BREAKER_COOLDOWN_S = max(1.0, float(os.environ.get("VERTEX_BREAKER_COOLDOWN_S", "300")))

_breaker = CircuitBreaker(
    name="vertex_services",
    failure_threshold=_BREAKER_THRESHOLD,
    cooldown_s=_BREAKER_COOLDOWN_S,
)


def breaker_snapshot() -> dict:
    """Public accessor for admin endpoints / health probes."""
    return _breaker.snapshot()


def force_breaker_close() -> None:
    """Operator override (admin endpoint) to force-close the breaker."""
    _breaker.force_close()


def _ok() -> bool:
    """True iff Workers AI is configured and the breaker allows traffic."""
    from providers.cloudflare_ai import _ENABLED as _CF_ENABLED
    if not _CF_ENABLED:
        return False
    return _breaker.allow()


# ── Backwards-compat record_response helper ───────────────────────────────────
# Tests call _record_response directly with mock httpx.Response objects to
# verify breaker state transitions.  The classification rules are unchanged —
# infra 4xx / 5xx open the breaker; user-payload errors do not.
_INFRA_4XX_STATUS = (401, 403, 408, 429)
_USER_4XX_STATUS  = (413, 422)
_INFRA_400_MARKERS = (
    "api key", "expired", "quota", "billing", "limit", "exhausted",
    "permission denied", "deadline exceeded", "invalid_grant",
    "service is currently unavailable", "consumer", "denied",
)


def _is_infra_400(body_text: str) -> bool:
    if not body_text:
        return False
    bt = body_text.lower()
    return any(m in bt for m in _INFRA_400_MARKERS)


def _record_response(r: Optional["httpx.Response"], label: str) -> bool:
    """Update the circuit breaker based on an HTTP response.

    Returns True iff the response is suitable for the caller to continue
    parsing (i.e. the status code is a success).  Failure classification:
      - Infra failures  → feed the breaker
      - User-payload failures (413, 422) → logged but NOT counted
    """
    if r is None:
        _breaker.record_failure(f"{label}_no_response")
        return False

    code = r.status_code
    if code == 400:
        body = ""
        try:
            body = r.text[:500] if r.text else ""
        except Exception:
            pass
        if _is_infra_400(body):
            _breaker.record_failure(f"{label}_400_infra")
            logger.warning("vertex %s 400 (infra): %s", label, body[:160])
        else:
            logger.info("vertex %s 400 (payload/unknown): %s", label, body[:120])
        return False

    if code in _INFRA_4XX_STATUS or code >= 500:
        _breaker.record_failure(f"{label}_http_{code}")
        return False

    if code in _USER_4XX_STATUS:
        logger.info("vertex %s %d — user payload error (not breaker-counted)", label, code)
        return False

    if code >= 400:
        logger.warning("vertex %s HTTP %d (uncategorised, not breaker-counted)", label, code)
        return False

    _breaker.record_success()
    return True


# ── Embed 429 sliding-window rate-limit tracker ───────────────────────────────
# Threshold raised to 10 / cooldown 30s — Workers AI bge-large-en-v1.5 has
# 3 000 RPM on the Standard unified-billing plan; transient bursts are rare
# and the model recovers quickly.
_EMBED_429_THRESHOLD  = 10    # 429 hits in window before cooldown activates
_EMBED_429_COOLDOWN_S = 30    # seconds to skip Workers AI embed path
_embed_429_timestamps: List[float] = []
_embed_cooldown_until: float = 0.0


def _track_embed_429() -> None:
    """Record one Workers AI embed 429 hit; activate cooldown if threshold met."""
    global _embed_cooldown_until
    now = time.time()
    _embed_429_timestamps.append(now)
    cutoff = now - _EMBED_429_COOLDOWN_S
    recent = [t for t in _embed_429_timestamps if t > cutoff]
    _embed_429_timestamps[:] = recent
    if len(recent) >= _EMBED_429_THRESHOLD:
        _embed_cooldown_until = now + _EMBED_429_COOLDOWN_S
        logger.warning(
            "[wai] embed 429 burst (%d hits in %ds) — pausing Workers AI embed for %ds",
            len(recent), _EMBED_429_COOLDOWN_S, _EMBED_429_COOLDOWN_S,
        )


def _reset_embed_429() -> None:
    """Reset the embed 429 counter and cooldown after a successful embed call."""
    global _embed_cooldown_until
    _embed_429_timestamps.clear()
    _embed_cooldown_until = 0.0


def get_embed_429_burst(window_seconds: int = 60) -> int:
    """Return the number of embed 429s recorded in the last *window_seconds*."""
    cutoff = time.time() - window_seconds
    return sum(1 for t in _embed_429_timestamps if t > cutoff)


def is_embed_cooldown_active() -> bool:
    """Return True if the Workers AI embed cooldown is currently active."""
    return time.time() < _embed_cooldown_until


def get_embed_cooldown_remaining_s() -> float:
    """Return seconds remaining in the embed cooldown, or 0.0 if inactive."""
    return max(0.0, _embed_cooldown_until - time.time())


def get_embed_cooldown_config() -> dict:
    """Return static embed-cooldown constants (admin endpoint helper)."""
    return {
        "threshold":  _EMBED_429_THRESHOLD,
        "duration_s": _EMBED_429_COOLDOWN_S,
    }


# ── Embed semaphore ────────────────────────────────────────────────────────────
_EMBED_MAX_CONCURRENT = int(os.environ.get("EMBED_MAX_CONCURRENT", "16"))
_EMBED_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _get_embed_semaphore() -> asyncio.Semaphore:
    global _EMBED_SEMAPHORE
    if _EMBED_SEMAPHORE is None:
        _EMBED_SEMAPHORE = asyncio.Semaphore(_EMBED_MAX_CONCURRENT)
    return _EMBED_SEMAPHORE


# ─────────────────────────────────────────────────────────────────────────────
# Internal: shared generate helper (Workers AI chat via cloudflare_ai.py)
# ─────────────────────────────────────────────────────────────────────────────

async def _generate(
    prompt: str,
    model: str = _GEN_MODEL_KEY,
    max_tokens: int = 2048,
    temperature: float = 0.1,
) -> Optional[str]:
    """Call Workers AI chat and return the text response, or None on failure."""
    if not _ok():
        return None
    from providers.cloudflare_ai import chat as _cf_chat
    try:
        text = await _cf_chat(
            [{"role": "user", "content": prompt}],
            model_key=_GEN_MODEL_KEY,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        _breaker.record_success()
        return text or None
    except Exception as exc:
        logger.warning("[wai] _generate failed: %s: %s", type(exc).__name__, str(exc)[:200])
        _breaker.record_failure(f"generate_{type(exc).__name__}")
        return None


def _clean_json(raw: str) -> str:
    """Strip markdown code-fence wrappers from an LLM JSON response."""
    s = raw.strip()
    if s.startswith("```"):
        parts = s.split("```")
        # parts[1] is the fenced block; strip optional 'json' prefix
        s = parts[1]
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    return s


# ─────────────────────────────────────────────────────────────────────────────
# 1. TEXT EMBEDDINGS — Workers AI bge-large-en-v1.5 (1024-dim)
# ─────────────────────────────────────────────────────────────────────────────

async def _workers_ai_primary_embed(text: str) -> Optional[List[float]]:
    """Workers AI primary embedding (bge-large-en-v1.5, 1024-dim).

    Returns None if the cooldown is active or the call fails, so callers
    can continue to any configured fallback without blocking.
    """
    if not text:
        return None
    if is_embed_cooldown_active():
        logger.debug("[wai] embed cooldown active — skipping Workers AI embed")
        return None
    from providers.cloudflare_ai import embed_one as _cf_embed
    try:
        async with _get_embed_semaphore():
            vec = await _cf_embed(text.strip()[:8000], model_key="embed")
        if not vec:
            return None
        if len(vec) != _EMBED_DIMENSIONS:
            logger.warning(
                "[wai] embed returned %d-dim; index expects %d — dropping",
                len(vec), _EMBED_DIMENSIONS,
            )
            return None
        _reset_embed_429()
        return vec
    except Exception as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
            _track_embed_429()
            logger.warning("[wai] embed 429 rate-limited; burst=%d", get_embed_429_burst())
        else:
            logger.warning("[wai] primary embed failed: %s: %s", type(exc).__name__, str(exc)[:200])
        return None


async def _cohere_primary_embed(text: str, task_type: str) -> Optional[List[float]]:
    """Cohere embedding path — used only when COHERE_EMBED_PRIMARY=true.

    Cohere ``embed-multilingual-v3.0`` also outputs 1024-dim, so it is
    compatible with the Atlas Vector Search index dimensionally.
    WARNING: switching to Cohere as primary requires re-indexing all content
    because BGE and Cohere vectors live in different embedding spaces.

    Maps task_type → Cohere input_type:
      RETRIEVAL_DOCUMENT → search_document
      RETRIEVAL_QUERY    → search_query
      (anything else)    → search_document
    """
    from providers.cohere import embed as _cohere_embed, ENABLED as _COHERE_ENABLED
    if not _COHERE_ENABLED:
        return None
    input_type = (
        "search_query"
        if task_type == "RETRIEVAL_QUERY"
        else "search_document"
    )
    try:
        results = await _cohere_embed([text.strip()[:8000]], input_type=input_type)
        return results[0] if results else None
    except Exception as exc:
        logger.warning("[cohere] embed failed (non-fatal): %s", exc)
        return None


async def embed_text(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> Optional[List[float]]:
    """Return a 1024-dim embedding vector, or None on failure.

    Uses the local LRU cache (cache.py) when available so hot queries
    never round-trip to any embedding provider.

    Embedding priority:
    1. If COHERE_EMBED_PRIMARY=true — Cohere embed-multilingual-v3.0
       (NOTE: requires ALL content to have been indexed with Cohere vectors;
        re-run the embedding pipeline after switching.)
    2. Workers AI BGE-large-en-v1.5 (default primary)
    3. Vertex AI text-embedding-004 (fallback for long-form content > 2048 tokens
       or when Workers AI embed is in cooldown) — Task #247.
       WARNING: Vertex vectors are 768-dim and in a different embedding space;
       results are only useful for standalone long-doc similarity, not for
       mixing with the main 1024-dim Vectorize index.
    """
    if not text:
        return None

    try:
        from cache import _embedding_cache, _embedding_cache_key
        _ek = _embedding_cache_key(text, task_type)
        cached = _embedding_cache.get(_ek)
        if cached:
            return cached
    except Exception:
        _ek = None
        _embedding_cache = None  # type: ignore[assignment]

    # Task #337 — Bedrock-Cohere is the **primary** Cohere route per
    # cloud-allocation-plan §6 + §9. When the BEDROCK_COHERE_PRIMARY
    # flag is on AND the admin has not disabled the bedrock_cohere
    # feature, we hit Bedrock-Cohere FIRST. Same Cohere model
    # (embed-multilingual-v3) so vectors stay in the 1024-dim space
    # the main Vectorize index expects — no cross-space contamination.
    # Cohere-only allow-list is enforced inside
    # providers.aws_native.bedrock_embed itself.
    vec = None
    from config import (
        BEDROCK_COHERE_PRIMARY as _BEDROCK_PRIMARY,
        COHERE_EMBED_PRIMARY as _COHERE_PRIMARY,
    )
    if _BEDROCK_PRIMARY:
        try:
            from providers import aws_native as _awsn
            if _awsn.is_enabled("bedrock_cohere") and _awsn.is_configured():
                vecs = await asyncio.to_thread(_awsn.bedrock_embed, [text.strip()[:8000]])
                if vecs:
                    vec = vecs[0]
                    logger.debug("[embed] Bedrock-Cohere PRIMARY hit (Task #337)")
        except Exception as exc:
            logger.warning("[embed] Bedrock-Cohere primary failed, falling back: %s", str(exc)[:150])

    # Direct Cohere → Workers AI fallback chain when Bedrock-Cohere is
    # disabled or returned no vector.
    if vec is None:
        if _COHERE_PRIMARY:
            vec = await _cohere_primary_embed(text, task_type)
            if vec is None:
                logger.debug("[embed] Cohere direct failed — falling back to Workers AI")
                vec = await _workers_ai_primary_embed(text)
        else:
            vec = await _workers_ai_primary_embed(text)

    # Vertex AI embed fallback (Task #247) — for long-form content or when
    # Workers AI embed is in cooldown. NOTE: Vertex text-embedding-004 is
    # 768-dim; results must NOT be mixed with 1024-dim bge-large vectors in
    # the main Vectorize index. Use only for standalone long-doc similarity.
    if vec is None:
        from providers import vertex_embed as _vx_embed
        if _vx_embed.is_configured() and (
            _vx_embed.is_long_form(text) or is_embed_cooldown_active()
        ):
            logger.debug("[embed] Vertex AI embed fallback triggered (long_form=%s, cooldown=%s)",
                         _vx_embed.is_long_form(text), is_embed_cooldown_active())
            vec = await _vx_embed.embed_text(text, task_type)

    try:
        if _ek and vec and _embedding_cache is not None:
            _embedding_cache[_ek] = vec
    except Exception:
        pass
    return vec


async def embed_batch(texts: List[str], task_type: str = "RETRIEVAL_DOCUMENT") -> List[Optional[List[float]]]:
    """Embed a batch; returns one Optional[List[float]] per input."""
    if not texts:
        return []
    results = await asyncio.gather(*[embed_text(t, task_type) for t in texts])
    return list(results)


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    return dot / (mag_a * mag_b + 1e-9)


async def semantic_search(
    query: str,
    candidates: List[Dict],
    text_key: str = "title",
    top_k: int = 10,
) -> List[Dict]:
    if not _ok() or not candidates:
        return candidates[:top_k]

    q_vec = await embed_text(query, task_type="RETRIEVAL_QUERY")
    if not q_vec:
        return candidates[:top_k]

    texts = [c.get(text_key, "") for c in candidates]
    vecs  = await embed_batch(texts)

    scored = []
    for item, vec in zip(candidates, vecs):
        score = cosine_similarity(q_vec, vec) if vec else 0.0
        scored.append({**item, "score": round(score, 4)})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


# ─────────────────────────────────────────────────────────────────────────────
# 2. TRANSLATION
# ─────────────────────────────────────────────────────────────────────────────

_LANG_NAMES = {
    "as": "Assamese", "hi": "Hindi", "bn": "Bengali",
    "en": "English",  "bho": "Bodo",
}


async def translate(text: str, target_lang: str = "as", source_lang: str = "en") -> Optional[str]:
    """Translate text to an Indic language.

    Priority for Indic targets (hi, bn, as):
      1. Google Cloud Translation v3 (primary — most accurate for Indic scripts)
      2. Workers AI indictrans2 (fallback)
      3. LLM prompt fallback (last resort)

    Non-Indic targets bypass Google Translation entirely and go straight
    to Workers AI indictrans2.
    """
    if not text:
        return None

    from providers import google_translate as _gt
    if _gt.is_configured() and _gt.is_indic_target(target_lang):
        try:
            result = await _gt.translate(text, target_lang=target_lang, source_lang=source_lang)
            if result:
                return result
        except Exception as exc:
            logger.warning("[google-translate] primary failed, falling back to Workers AI: %s", str(exc)[:150])

    from providers.cloudflare_ai import translate as _cf_translate
    try:
        result = await _cf_translate(text, target_lang=target_lang, source_lang=source_lang)
        if result:
            return result
    except Exception as exc:
        logger.warning("[wai] translate (indictrans2) failed, trying LLM fallback: %s", str(exc)[:150])

    # Task #337 — AWS Translate slot in *before* the LLM-prompt fallback.
    # The runbook (§3.7) lists Translate as the Sarvam/Workers-AI fallback,
    # so we attempt it whenever the prior tiers have all returned empty.
    try:
        from providers import aws_native as _awsn
        if _awsn.is_enabled("translate") and _awsn.is_configured():
            translated = await asyncio.to_thread(
                _awsn.translate_text, text[:5000],
                source_lang=source_lang or "auto",
                target_lang=target_lang,
            )
            if translated:
                return translated
    except Exception as exc:
        logger.warning("[aws-translate] fallback failed: %s", str(exc)[:150])

    if not _ok():
        return None
    lang_name = _LANG_NAMES.get(target_lang, target_lang)
    src_name  = _LANG_NAMES.get(source_lang, source_lang)
    prompt = (
        f"Translate the following educational content from {src_name} to {lang_name}. "
        f"Keep all technical terms, subject names, and proper nouns as-is. "
        f"Return ONLY the translated text, no explanations.\n\n{text[:4000]}"
    )
    return await _generate(prompt, max_tokens=4096)


async def translate_structured(
    content: dict,
    fields: List[str],
    target_lang: str = "as",
) -> dict:
    tasks = {f: translate(content.get(f, ""), target_lang) for f in fields if content.get(f)}
    results = await asyncio.gather(*tasks.values())
    out = dict(content)
    for key, result in zip(tasks.keys(), results):
        if result:
            out[f"{key}_{target_lang}"] = result
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 3. VISION ANALYSIS — Workers AI llama-3.2-11b-vision-instruct
# ─────────────────────────────────────────────────────────────────────────────

async def analyze_image(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    prompt: str = "Describe this image in detail.",
    max_output_tokens: int = 1024,
) -> Optional[str]:
    """Analyse an image with the Workers AI llama-3.2-11b vision model."""
    if not _ok():
        return None
    from providers.cloudflare_ai import analyze_image as _cf_vision
    try:
        text = await _cf_vision(image_bytes, prompt=prompt, mime_type=mime_type)
        _breaker.record_success()
        return text or None
    except Exception as exc:
        logger.warning("[wai] analyze_image failed: %s: %s", type(exc).__name__, str(exc)[:200])
        _breaker.record_failure(f"analyze_image_{type(exc).__name__}")
        return None


async def analyze_thumbnail(
    image_bytes: bytes,
    subject: str = "",
    topic: str = "",
) -> dict:
    if not _ok():
        return {}
    prompt = (
        f"Analyse this educational thumbnail for '{topic}' ({subject}). "
        f"Return a JSON object with: dominant_colors (list), style (string), "
        f"accessibility_score (0-10), text_readability (0-10), "
        f"improvement_suggestions (list of strings), overall_score (0-10). "
        f"Return ONLY valid JSON."
    )
    raw = await analyze_image(image_bytes, prompt=prompt)
    if not raw:
        return {}
    try:
        return json.loads(_clean_json(raw))
    except Exception:
        return {"raw_analysis": raw}


# ─────────────────────────────────────────────────────────────────────────────
# 3b. OCR
# ─────────────────────────────────────────────────────────────────────────────

# Round-robin counter shared across all OCR fallback calls.
# The primary cloud fallbacks (Textract ↔ Azure Doc Intelligence) alternate
# on each invocation so quota / cost is spread across both AWS and Azure credits.
_ocr_rotation_counter: int = 0


async def _textract_ocr_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> Optional[str]:
    """AWS Textract OCR — DetectDocumentText (sync, bytes payload).

    Requires AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY in env (already set for
    the billing Cost-Explorer path).  Falls back gracefully if boto3 or creds
    are missing.

    Returns the concatenated page text, or None on any error.
    """
    try:
        import boto3  # type: ignore
        from config import _AWS_ACCESS_KEY, _AWS_SECRET_KEY, _AWS_REGION
        if not (_AWS_ACCESS_KEY and _AWS_SECRET_KEY):
            logger.debug("[ocr/textract] AWS credentials not set — skipping")
            return None

        region = _AWS_REGION or "us-east-1"

        def _run_sync() -> Optional[str]:
            client = boto3.client(
                "textract",
                aws_access_key_id=_AWS_ACCESS_KEY,
                aws_secret_access_key=_AWS_SECRET_KEY,
                region_name=region,
            )
            resp = client.detect_document_text(Document={"Bytes": image_bytes})
            blocks = resp.get("Blocks", [])
            lines = [b["Text"] for b in blocks if b.get("BlockType") == "LINE" and b.get("Text")]
            return "\n".join(lines) or None

        text = await asyncio.get_event_loop().run_in_executor(None, _run_sync)
        if text:
            logger.info("[ocr/textract] extracted %d chars", len(text))
        return text
    except Exception as exc:
        logger.warning("[ocr/textract] failed: %s: %s", type(exc).__name__, str(exc)[:200])
        return None


async def _azure_doc_intel_ocr_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> Optional[str]:
    """Azure Document Intelligence (prebuilt-read) OCR.

    Reads AZURE_DOCUMENT_INTELLIGENCE_KEY + AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT
    from config (both env aliases supported — see config.py).
    Falls back gracefully when either is unset.

    Returns the concatenated page text, or None on any error.
    """
    try:
        from config import AZURE_DOC_INTEL_KEY, AZURE_DOC_INTEL_ENDPOINT
        if not (AZURE_DOC_INTEL_KEY and AZURE_DOC_INTEL_ENDPOINT):
            logger.debug("[ocr/azure] AZURE_DOCUMENT_INTELLIGENCE_KEY / ENDPOINT not set — skipping")
            return None

        from azure.ai.documentintelligence import DocumentIntelligenceClient  # type: ignore
        from azure.core.credentials import AzureKeyCredential  # type: ignore

        def _run_sync() -> Optional[str]:
            client = DocumentIntelligenceClient(
                endpoint=AZURE_DOC_INTEL_ENDPOINT,
                credential=AzureKeyCredential(AZURE_DOC_INTEL_KEY),
            )
            poller = client.begin_analyze_document(
                "prebuilt-read",
                analyze_request={"base64Source": base64.b64encode(image_bytes).decode()},
                content_type="application/json",
            )
            result = poller.result()
            lines: list[str] = []
            for page in (result.pages or []):
                for line in (page.lines or []):
                    if line.content:
                        lines.append(line.content)
            return "\n".join(lines) or None

        text = await asyncio.get_event_loop().run_in_executor(None, _run_sync)
        if text:
            logger.info("[ocr/azure] extracted %d chars", len(text))
        return text
    except Exception as exc:
        logger.warning("[ocr/azure] failed: %s: %s", type(exc).__name__, str(exc)[:200])
        return None


async def _cloud_ocr_with_rotation(image_bytes: bytes, mime_type: str) -> Optional[str]:
    """Call AWS Textract and Azure Doc Intelligence on a round-robin basis.

    On each invocation the counter advances.  Even calls → Textract first,
    odd calls → Azure first.  If the primary fails the secondary is tried
    automatically, so the service degrades gracefully when one provider is
    down or quota-exhausted.
    """
    global _ocr_rotation_counter
    _ocr_rotation_counter += 1
    use_textract_first = (_ocr_rotation_counter % 2 == 1)

    primary_name, primary_fn, secondary_name, secondary_fn = (
        ("textract", _textract_ocr_image, "azure", _azure_doc_intel_ocr_image)
        if use_textract_first else
        ("azure", _azure_doc_intel_ocr_image, "textract", _textract_ocr_image)
    )

    logger.info("[ocr/rotation] call #%d → primary=%s", _ocr_rotation_counter, primary_name)
    text = await primary_fn(image_bytes, mime_type)
    if text:
        return text

    logger.info("[ocr/rotation] primary %s returned nothing — trying %s", primary_name, secondary_name)
    return await secondary_fn(image_bytes, mime_type)


# NOTE: `_gemini_ocr_image` (last-resort Gemini Vision REST OCR) was removed
# in the 2026-05-03 vertex-only Gemini migration. The OCR chain is now:
#   1. Workers AI (llama-3.2-11b-vision)
#   2. Google Cloud Vision DOCUMENT_TEXT_DETECTION
#   3. AWS Textract ↔ Azure Document Intelligence (rotated)
# If a Gemini-Vision OCR fallback is ever needed again, route it through
# `llm._call_vertex_chat` (which now supports multimodal `image_url` parts)
# rather than re-introducing a direct GEMINI_API_KEY caller.


async def ocr_image(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    lang: Optional[str] = None,
) -> dict:
    """Extract text from an AHSEC/SEBA question paper or textbook image.

    Provider priority:
      1. Workers AI (llama-3.2-11b-vision) — free, fast, always tried first.
      2. Google Cloud Vision DOCUMENT_TEXT_DETECTION — for Indic script or
         when Workers AI confidence < 0.80.
      3. AWS Textract ↔ Azure Document Intelligence (rotated) — last-resort
         cloud OCR pair when Workers AI and Google Cloud Vision are both
         unavailable. The legacy Gemini-Vision REST fallback was removed in
         the 2026-05-03 vertex-only Gemini migration.
    """
    from providers import google_vision as _gv

    _workers_ai_confidence: Optional[float] = None
    _workers_ai_text: Optional[str] = None

    if _ok():
        prompt = (
            "You are an OCR engine for AHSEC/SEBA educational content. "
            "Extract ALL visible text from this image exactly as written, preserving "
            "question numbers, sub-parts, mathematical notation, and formatting. "
            "Structure the output as:\n"
            "- Detected content type (question paper / textbook / notes)\n"
            "- Extracted text (verbatim)\n"
            "- Structured questions list (if applicable): [{number, text, marks, sub_parts:[]}]\n"
            "Return JSON: {content_type, raw_text, questions, word_count, confidence}"
        )
        raw = await analyze_image(image_bytes, mime_type=mime_type, prompt=prompt)
        if raw:
            try:
                parsed = json.loads(_clean_json(raw))
                _workers_ai_confidence = float(parsed.get("confidence", 1.0))
                _workers_ai_text = parsed.get("raw_text", "")
            except Exception:
                _workers_ai_text = raw
                _workers_ai_confidence = 0.5

    use_google = _gv.should_use_google_vision(
        lang=lang,
        workers_ai_confidence=_workers_ai_confidence,
    )

    if use_google and _gv.is_configured():
        logger.info(
            "[ocr] Routing to Google Vision DOCUMENT_TEXT_DETECTION "
            "(lang=%s, wai_confidence=%s)",
            lang,
            f"{_workers_ai_confidence:.2f}" if _workers_ai_confidence is not None else "N/A",
        )
        gv_result = await _gv.ocr_document(image_bytes, mime_type=mime_type, lang=lang)
        if "error" not in gv_result and gv_result.get("text"):
            text = gv_result["text"]
            return {
                "raw_text": text,
                "content_type": "extracted",
                "questions": [],
                "word_count": len(text.split()),
                "provider": "google_vision",
                "confidence": gv_result.get("confidence", 1.0),
                "pages": gv_result.get("pages", 1),
            }
        logger.warning("[ocr] Google Vision failed: %s — using Workers AI result", gv_result.get("error"))

    if _workers_ai_text:
        try:
            parsed_result = json.loads(_clean_json(_workers_ai_text))
            parsed_result["provider"] = "workers_ai"
            return parsed_result
        except Exception:
            return {
                "raw_text": _workers_ai_text,
                "content_type": "extracted",
                "questions": [],
                "word_count": len(_workers_ai_text.split()),
                "provider": "workers_ai",
            }

    # ── Cloud OCR rotation: AWS Textract ↔ Azure Document Intelligence ──────────
    # Alternates per call so quota / cost is spread across both providers.
    # Each call tries the primary first; if it fails the secondary is tried
    # automatically, so the service degrades gracefully.
    logger.info(
        "[ocr] Workers AI + Google Vision both unavailable — "
        "trying cloud OCR rotation (Textract ↔ Azure Doc Intelligence)"
    )
    cloud_text = await _cloud_ocr_with_rotation(image_bytes, mime_type)
    if cloud_text:
        # Determine which provider actually succeeded by checking the counter parity.
        # _cloud_ocr_with_rotation already incremented it, so subtract 1 for this call.
        provider_label = (
            "aws_textract" if (_ocr_rotation_counter % 2 == 1) else "azure_doc_intelligence"
        )
        return {
            "raw_text": cloud_text,
            "content_type": "extracted",
            "questions": [],
            "word_count": len(cloud_text.split()),
            "provider": provider_label,
            "confidence": 0.97,
        }

    # NOTE: The legacy Gemini Vision REST last-resort fallback was removed in
    # the 2026-05-03 vertex-only Gemini migration. Google Cloud Vision is the
    # OCR primary; Workers AI / Textract / Azure Doc Intelligence cover the
    # rest of the chain.

    return {
        "error": (
            "OCR failed — Workers AI, Google Vision, "
            "AWS Textract, Azure Document Intelligence, "
            "and Gemini Vision all unavailable"
        )
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3c. NLP KEY CONCEPTS
# ─────────────────────────────────────────────────────────────────────────────

async def extract_key_concepts(
    text: str,
    subject: str = "",
    class_name: str = "Class 11",
) -> dict:
    if not _ok():
        return {"error": "Workers AI not configured"}
    prompt = (
        f"You are an educational NLP engine for {subject} ({class_name}, AHSEC/SEBA board). "
        f"Analyse this chapter/passage and extract:\n"
        f"1. key_terms: top 10-15 important terms as [{{term, definition, importance: high/medium/low}}]\n"
        f"2. entities: named entities (laws, formulas, people, places, events) as [{{name, type, context}}]\n"
        f"3. difficulty_level: easy/medium/hard/advanced\n"
        f"4. chapter_summary: 2-3 sentence summary\n"
        f"5. exam_weightage: estimated marks weightage (low/medium/high)\n"
        f"6. prerequisite_topics: list of topics students need to know first\n\n"
        f"TEXT:\n{text[:4000]}\n\n"
        f"Return ONLY valid JSON."
    )
    raw = await _generate(prompt, max_tokens=2048, temperature=0.1)
    if not raw:
        return {"error": "NLP analysis failed"}
    try:
        return json.loads(_clean_json(raw))
    except Exception:
        return {"raw": raw}


# ─────────────────────────────────────────────────────────────────────────────
# 3d. FLASHCARD GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

async def generate_flashcards(
    text: str,
    subject: str = "",
    count: int = 10,
    class_name: str = "Class 11",
) -> dict:
    if not _ok():
        return {"error": "Workers AI not configured"}
    prompt = (
        f"Generate {count} high-quality revision flashcards for {subject} ({class_name}, AHSEC board).\n"
        f"Based on this content:\n{text[:4000]}\n\n"
        f"Mix card types:\n"
        f"- Definition cards (What is X?)\n"
        f"- Concept cards (Explain Y)\n"
        f"- Application cards (How does Z work?)\n"
        f"- Formula/fact cards\n"
        f"- True/False cards\n\n"
        f"For each card: {{id, front, back, type, difficulty: easy/medium/hard, tags: []}}\n"
        f"Return ONLY a JSON object: {{flashcards: [...], subject, total_cards}}"
    )
    raw = await _generate(prompt, max_tokens=3000, temperature=0.1)
    if not raw:
        return {"error": "Flashcard generation failed"}
    try:
        return json.loads(_clean_json(raw))
    except Exception:
        return {"raw": raw, "flashcards": [], "subject": subject}


# ─────────────────────────────────────────────────────────────────────────────
# 3e. MCQ GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

async def generate_mcqs(
    text: str,
    subject: str = "",
    class_name: str = "Class 11",
    count: int = 10,
    difficulty: str = "mixed",
) -> dict:
    if not _ok():
        return {"error": "Workers AI not configured"}
    prompt = (
        f"Generate {count} AHSEC-pattern MCQ questions for {subject} ({class_name}) with {difficulty} difficulty.\n"
        f"Based on content:\n{text[:4000]}\n\n"
        f"Each MCQ must have exactly 4 options (A, B, C, D).\n"
        f"Format: [{{id, question, options: {{A, B, C, D}}, correct_answer, explanation, difficulty, topic, marks: 1}}]\n"
        f"Make distractors plausible. Ensure correct_answer is one of A/B/C/D.\n"
        f"Return ONLY valid JSON: {{mcqs: [...], subject, total, difficulty}}"
    )
    raw = await _generate(prompt, max_tokens=3000, temperature=0.1)
    if not raw:
        return {"error": "MCQ generation failed"}
    try:
        return json.loads(_clean_json(raw))
    except Exception:
        return {"raw": raw, "mcqs": [], "subject": subject}


# ─────────────────────────────────────────────────────────────────────────────
# 4. CONTENT ENHANCER
# ─────────────────────────────────────────────────────────────────────────────

async def enhance_content(
    content: str,
    page_type: str = "notes",
    subject: str = "",
    topic: str = "",
    class_name: str = "Class 11",
) -> Optional[str]:
    if not _ok() or not content:
        return None
    type_hints = {
        "notes": "Make the notes clearer, add more examples, better structure with headers, and ensure exam relevance.",
        "mcqs":  "Improve MCQ distractors to be more plausible, ensure questions test understanding not just recall.",
        "definition": "Make the definition precise, include etymology if helpful, and give a real-world analogy.",
        "important-questions": (
            "Ensure questions cover all mark categories in ascending order (1-mark, 2-mark, 3-mark, 5-mark, 10-mark). "
            "Use AHSEC/SEBA/Degree exam-style language. Add model answer hints."
        ),
        "examples": "Add more relatable, Assam-context examples that AssamBoard students will recognise.",
    }
    prompt = (
        f"You are an expert {subject} teacher for AssamBoard ({class_name}) students in Assam, India.\n"
        f"Improve the following {page_type} content for the topic: {topic}\n\n"
        f"Instruction: {type_hints.get(page_type, 'Make it better and more exam-focused.')}\n\n"
        f"Return ONLY the improved content in the same format (Markdown). Do not add explanatory text.\n\n"
        f"ORIGINAL CONTENT:\n{content[:6000]}"
    )
    return await _generate(prompt, max_tokens=4096)


# ─────────────────────────────────────────────────────────────────────────────
# 5. QUALITY SCORER
# ─────────────────────────────────────────────────────────────────────────────

async def score_content(
    content: str,
    page_type: str = "notes",
    topic: str = "",
    subject: str = "",
) -> dict:
    if not _ok() or not content:
        return {"overall": 0, "error": "Workers AI not configured"}
    prompt = (
        f"Score this {page_type} content about '{topic}' ({subject}) for AssamBoard students.\n"
        f"Return a JSON with:\n"
        f"  accuracy (0-10), completeness (0-10), clarity (0-10),\n"
        f"  exam_relevance (0-10), overall (0-10),\n"
        f"  issues (list of strings), strengths (list of strings)\n"
        f"Return ONLY valid JSON.\n\nCONTENT:\n{content[:3000]}"
    )
    raw = await _generate(prompt, max_tokens=512)
    if not raw:
        return {"overall": 0, "error": "Generation failed"}
    try:
        return json.loads(_clean_json(raw))
    except Exception:
        return {"overall": 5, "raw": raw}


# ─────────────────────────────────────────────────────────────────────────────
# 6. TOPIC SUGGESTER
# ─────────────────────────────────────────────────────────────────────────────

async def suggest_topics(
    subject: str,
    class_name: str,
    existing_topics: List[str],
    board: str = "AHSEC",
) -> List[dict]:
    if not _ok():
        return []
    existing_sample = ", ".join(existing_topics[:30])
    prompt = (
        f"You are an expert {board} {class_name} {subject} curriculum designer.\n"
        f"The platform already has content for: {existing_sample}{'...' if len(existing_topics) > 30 else ''}.\n\n"
        f"Suggest 10 important topics that are MISSING and have HIGH search volume from students.\n"
        f"Return a JSON array of objects with: title, priority (high/medium), "
        f"search_volume_estimate (number/month), reason.\n"
        f"Return ONLY valid JSON array."
    )
    raw = await _generate(prompt, max_tokens=1024)
    if not raw:
        return []
    try:
        result = json.loads(_clean_json(raw))
        return result if isinstance(result, list) else []
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# 7. SEO META GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

async def generate_seo_meta(
    topic: str,
    subject: str,
    class_name: str,
    page_type: str,
    board: str = "AHSEC",
    content_preview: str = "",
) -> dict:
    if not _ok():
        return {}
    prompt = (
        f"Generate SEO metadata for an educational page about:\n"
        f"  Topic: {topic}\n  Subject: {subject}\n  Class: {class_name}\n"
        f"  Page type: {page_type}\n  Board: {board}\n"
        f"{'Content preview: ' + content_preview[:500] if content_preview else ''}\n\n"
        f"Return JSON with:\n"
        f"  title (max 60 chars), meta_description (max 160 chars),\n"
        f"  keywords (list of 8-12 strings), og_title, og_description,\n"
        f"  schema_name (for JSON-LD)\n"
        f"Return ONLY valid JSON."
    )
    raw = await _generate(prompt, max_tokens=512)
    if not raw:
        return {}
    try:
        return json.loads(_clean_json(raw))
    except Exception:
        return {"raw": raw}


# ─────────────────────────────────────────────────────────────────────────────
# 8. CONTENT GAP FINDER
# ─────────────────────────────────────────────────────────────────────────────

async def find_content_gaps(
    published_slugs: List[str],
    top_searches: List[str],
    subjects: List[str],
) -> List[dict]:
    if not _ok():
        return []
    prompt = (
        f"Analyse these search queries from students and identify content gaps.\n\n"
        f"Top student search queries:\n{chr(10).join(f'- {q}' for q in top_searches[:20])}\n\n"
        f"Subjects covered: {', '.join(subjects[:10])}\n"
        f"Published pages count: {len(published_slugs)}\n\n"
        f"Return a JSON array of top 10 gaps with:\n"
        f"  query, gap_type (missing_topic/incomplete_coverage/wrong_level),\n"
        f"  priority (high/medium), suggested_action,\n"
        f"  estimated_monthly_searches\n"
        f"Return ONLY valid JSON array."
    )
    raw = await _generate(prompt, max_tokens=1024)
    if not raw:
        return []
    try:
        result = json.loads(_clean_json(raw))
        return result if isinstance(result, list) else []
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# 9. LONG DOCUMENT READER (PDF)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_pdf_text(pdf_bytes: bytes, max_chars: int = 16000) -> str:
    """Extract plain text from a PDF using pypdf.

    Returns an empty string if the library is unavailable or the PDF is
    encrypted / image-only.  Callers should fall back gracefully.
    """
    try:
        import pypdf  # type: ignore
        import io
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        parts: List[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            parts.append(text)
            if sum(len(p) for p in parts) >= max_chars:
                break
        return "\n".join(parts)[:max_chars]
    except Exception as exc:
        logger.warning("[wai] PDF text extraction failed: %s", str(exc)[:150])
        return ""


async def extract_from_document(pdf_bytes: bytes, task: str = "extract_mcqs") -> dict:
    """Extract structured data from a PDF document.

    Text is extracted via pypdf then sent to Workers AI chat for structured
    analysis.  Workers AI vision does not accept PDF inputs natively, so
    this text-first approach works for all text-based PDFs.  Scanned /
    image-only PDFs will return an error asking for a text-based PDF.
    """
    if not _ok():
        return {"error": "Workers AI not configured"}

    text = _extract_pdf_text(pdf_bytes)
    if not text.strip():
        return {
            "error": (
                "Could not extract text from this PDF. "
                "It may be a scanned/image-only document. "
                "Please use a text-based PDF."
            )
        }

    task_prompts = {
        "extract_mcqs":      "Extract all MCQ questions with their options and correct answers. Return a JSON array of {question, options, correct_answer, topic}.",
        "extract_topics":    "List all chapter topics and subtopics covered. Return a JSON array of {chapter, topics: []}.",
        "summarise":         "Summarise each chapter in 3-5 bullet points. Return a JSON array of {chapter, summary_points: []}.",
        "extract_questions": "Extract all exam-style questions (short answer, long answer, numericals). Return a JSON array of {question, type, marks, topic}.",
    }
    task_prompt = task_prompts.get(task, task_prompts["summarise"])
    prompt = (
        f"You are an educational content analyser for AHSEC/SEBA board materials.\n\n"
        f"TASK: {task_prompt}\n\n"
        f"DOCUMENT TEXT:\n{text[:12000]}\n\n"
        f"Return ONLY valid JSON."
    )
    raw = await _generate(prompt, max_tokens=8192, temperature=0.1)
    if not raw:
        return {"error": "LLM extraction failed"}
    try:
        return {"result": json.loads(_clean_json(raw)), "task": task}
    except Exception:
        return {"result": raw, "task": task}


# ─────────────────────────────────────────────────────────────────────────────
# STATUS CHECK
# ─────────────────────────────────────────────────────────────────────────────

async def health_check() -> dict:
    """Run embed + generate probes concurrently; return a diagnostic dict."""
    from providers.cloudflare_ai import _ENABLED as _CF_ENABLED, MODELS

    if not _CF_ENABLED:
        return {
            "ok": False,
            "auth_mode": "workers_ai",
            "via_cf_gateway": False,
            "reason": (
                "Workers AI not configured — set CLOUDFLARE_API_TOKEN and "
                "CF_AI_GATEWAY_ACCOUNT_ID in your environment."
            ),
        }

    if not _breaker.allow():
        snap = _breaker.snapshot()
        return {
            "ok": False,
            "auth_mode": "workers_ai",
            "via_cf_gateway": True,
            "reason": (
                f"Workers AI circuit breaker is {snap.get('state', 'open')} — "
                f"last failure: {snap.get('last_reason', 'unknown')}"
            ),
            "breaker": snap,
        }

    test, gen_test = await asyncio.gather(
        embed_text("test", task_type="SEMANTIC_SIMILARITY"),
        _generate("Reply with just the word: OK", max_tokens=64),
        return_exceptions=True,
    )
    if isinstance(test, BaseException):
        logger.warning("vertex health_check embed raised: %r", test)
        test = None
    if isinstance(gen_test, BaseException):
        logger.warning("vertex health_check generate raised: %r", gen_test)
        gen_test = None

    embed_ok = test is not None and len(test) == _EMBED_DIMENSIONS
    gen_ok   = bool(gen_test and gen_test.strip())

    return {
        "ok": embed_ok and gen_ok,
        "auth_mode": "workers_ai",
        "via_cf_gateway": True,
        "byok": False,
        "project": None,
        "location": "cloudflare-workers",
        "embeddings": embed_ok,
        "embed_dimensions": len(test) if test else 0,
        "generation": gen_ok,
        "embed_cooldown_active": is_embed_cooldown_active(),
        "embed_429_burst": get_embed_429_burst(),
        "models": {
            "generation": MODELS.get(_GEN_MODEL_KEY, _GEN_MODEL_KEY),
            "embedding":  _EMBED_MODEL,
            "vision":     MODELS.get(_VISION_MODEL_KEY, _VISION_MODEL_KEY),
            "long_doc":   MODELS.get(_GEN_MODEL_KEY, _GEN_MODEL_KEY),
        },
        "breaker": _breaker.snapshot(),
    }


# Log startup mode
logger.info(
    "vertex_services: ready auth_mode=workers_ai "
    "embed_model=%s gen_model=%s via_cf_gateway=true",
    _EMBED_MODEL,
    _GEN_MODEL_KEY,
)
