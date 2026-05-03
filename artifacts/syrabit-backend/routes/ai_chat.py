"""Syrabit.ai — AI chat & search routes"""
import re, json, asyncio, time as _time_mod, uuid, logging, hashlib

from typing import Optional
from datetime import datetime, timezone
from fastapi import (
    APIRouter, HTTPException, Depends, Request, UploadFile, File,
)
from fastapi.responses import StreamingResponse
import cachetools

from models import (
    ChatMessage,
    SearchResultOut,
)
from config import (
    LLM_MODEL,
    PLAN_LIMITS,
)
from deps import (
    db,
    redis_client,
    sarvam_client,
    supa,
)
import deps
from cache import (
    REDIS_AI_CACHE_TTL,
    REDIS_CASUAL_CACHE_TTL,
    _ai_response_cache,
    _cache_key,  # re-exported for tests/back-compat (test_ai_chat_indic_route)
    _redis_set,
    _syllabus_cache,
    _syllabus_cache_key,
    build_ai_cache_key,
    ai_cache_aget,
    ai_cache_aset,
    ai_cache_record_hit_saved_latency,
    ai_cache_expected_saved_ms,
)
from auth_deps import (
    get_user_credits, rate_limit_chat_optional, rate_limit_ocr_optional,
)
from db_ops import (
    atomic_deduct_credit,
    supa_get_conversation,
    supa_get_user_by_id,
    supa_update_conversation,
    supa_update_user,
    supa_upsert_conversation,
)
from llm import call_llm_api_chat, call_llm_api_stream
from rag import (
    _fetch_internal_chapters,
    _record_chat_latency,
    _sources_from_rag_ctx,
    _sources_from_web_results,
    _trim_history,
    build_rag_system_prompt,
    resolve_rag_context,
    syrabit_library_search,
    web_search_with_fallback,
)
from prompts import classify_intent, _is_out_of_scope_response, extract_semester_number, compute_answer_budget
from tracing import (
    record_chat_attrs,
    record_first_token,
    emit_phase_span,
)
from followup_context import detect_followup, build_followup_context, merge_followup_into_query
from pipeline import should_use_pipeline, stage1_resolve_topic, apply_stage1_to_intent, build_enhanced_query, get_instant_response, get_instant_assamese_response
import wai_chapter_index as _wai_idx

# Chat Enhancement Layer
try:
    from chat_enhancement_layer import chat_enhancement_layer
    from config import CHAT_ENHANCE_ENABLED as _CHAT_ENHANCE_ENABLED
    GEO_ENHANCEMENTS_ENABLED = _CHAT_ENHANCE_ENABLED
except ImportError as e:
    logger.warning(f"[Enhancement] Layer unavailable: {e}")
    GEO_ENHANCEMENTS_ENABLED = False

_CONTENT_INTENTS_SET = {"notes", "important_questions", "pyq"}

import httpx as _httpx_mod

def _tune_response_stream(chunk_text: str, intent: str, _buf: dict) -> str:
    _buf["total"] += chunk_text
    _buf["chars"] += len(chunk_text)

    text = chunk_text
    if _buf["chars"] < 100:
        text = re.sub(r'^(Sure!|Of course!|Absolutely!|Great question!|Hello!)\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r"^(Let me explain|Here's|I'd be happy to)\s*[.!,]?\s*", '', text, flags=re.IGNORECASE)

    text = re.sub(r'\n{3,}', '\n\n', text)

    return text

def _safe_metadata(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}
from qa_engine import log_chat_message as _log_chat_message
from guardrails.prompt_safety import evaluate_prompt_safety, validate_llm_output, llm_classify_safety
import chat_speedup_metrics as _speedup

logger = logging.getLogger(__name__)

def _record_llm_cost(model, prompt_tokens, completion_tokens, provider="gemini", user_id=""):
    from routes.admin_advanced import record_llm_cost
    record_llm_cost(model, prompt_tokens, completion_tokens, provider, user_id)


# ────────────────────────────────────────────────────────────────────────────
# Assamese translation: Qwen fallback + Sarvam polish
# ────────────────────────────────────────────────────────────────────────────
# User-mandated routing (2026-04-26):
#   • English → Assamese translation: Qwen (Cerebras Qwen-3-235B) does the heavy lifting
#     via the `_assamese_translate_gemini_main_sarvam_polish` pipeline,
#     multilingual). Sarvam then polishes the Qwen output for native
#     Assamese fluency (sarvam-m chat, ~30 RPM but excellent for Indic).
#   • For chat *response* generation (not translation), the routing is
#     the inverse: Sarvam main + Qwen fallback — see `llm.py` indic
#     race phase logic.
#
# Tradeoffs documented in `_make_assamese_translate_callable` docstring.
# ────────────────────────────────────────────────────────────────────────────

# Polish only kicks in for substantive output. Below this length the polish
# round-trip cost (~0.8-1.5s) outweighs the marginal quality lift, and N
# small fragments would compound into multi-second post-stream latency.
# Raised from 80→250: one-sentence answers from IndicTrans2 are already
# fluent; the extra 0.8-1.5s round-trip is only justified for longer text.
_POLISH_MIN_LEN = 250

# Hard ceiling on the Sarvam polish call so a slow/dead Sarvam never holds
# up the translation pipeline. On timeout we return the un-polished Qwen
# output (graceful degradation).
_SARVAM_POLISH_TIMEOUT_SEC = 1.8

# Hard ceiling for the Qwen fallback translate call.
# Cerebras Qwen-3-235B is typically <500ms; Workers AI Qwen2.5-72B ~1-2s.
# We budget 5s total for the Qwen tier so a cold Workers AI container
# doesn't hold up the pipeline indefinitely.
_QWEN_TRANSLATE_TIMEOUT_SEC = 5.0

_POLISH_SYSTEM_PROMPT = (
    "You are a native Assamese (অসমীয়া) editor. The user message is an "
    "Assamese text that may sound slightly machine-translated or "
    "English-influenced. Polish it so it reads naturally to a native "
    "Assamese speaker: fix unnatural word choices, smooth grammar, and "
    "use idiomatic Assamese phrasing. Preserve the original meaning "
    "exactly — do NOT add information, do NOT remove information. "
    "Latin script is allowed ONLY for: pure numbers, scientific units "
    "(cm, kg, °C, eV…), math symbols/equations, code, URLs, and "
    "well-known proper nouns/acronyms (NCERT, SEBA, AHSEC, DNA, GDP, "
    "Newton). Return ONLY the polished Assamese text — no explanations, "
    "no preamble, no quote marks."
)


async def _assamese_translate_gemini_main_sarvam_polish(
    text: str,
    *,
    target_lang_code: str = "as-IN",
) -> str:
    """Assamese translation pipeline: Sarvam-primary → Qwen-fallback → Sarvam-polish.

    Step 0 (primary): English text → Assamese via Sarvam translate:v1 (/translate API).
        Sarvam's dedicated translation model is purpose-built for Indic languages
        and produces more natural Assamese than Qwen alone. Typical latency: 300-800ms.

    Step 1 (fallback): If Sarvam translate is unavailable or fails, English text →
        Assamese via Qwen (Cerebras Qwen-3-235B or Workers AI Qwen2.5-72B).

    Step 2 (polish, optional): Qwen Assamese output → polished Assamese
        via Sarvam-m chat. Skipped for short fragments (< _POLISH_MIN_LEN
        chars), AND skipped entirely when Step 0 succeeded (Sarvam translate
        already produces fluent Assamese).

    Failure modes:
        • Sarvam translate fails → falls through to Qwen (Step 1).
        • Qwen fails or returns empty → returns "" (caller falls back to
          its own strip / original-text path).
        • Sarvam polish fails / times out → returns the un-polished Qwen
          output (graceful degradation — translation still landed).

    Args:
        text: Source English text to translate.
        target_lang_code: Sarvam-style language code (e.g., "as-IN"). The
            Qwen translator only uses the bare language ("as"); the full
            code is kept in the signature for symmetry with the legacy
            Sarvam /translate API and forward-compatibility if other Indic
            targets are ever added.

    Returns:
        Polished Assamese string, or un-polished Qwen output, or "".
    """
    src = (text or "").strip()
    if not src:
        return ""

    # Derive bare language ("as-IN" → "as") for the Qwen translator.
    _bare_lang = (target_lang_code or "as-IN").split("-", 1)[0].lower() or "as"

    # ── Redis translation cache (avoids repeated Qwen+Sarvam round-trips) ─
    _cache_key = "tr:" + hashlib.md5(f"{_bare_lang}:{src[:1000]}".encode()).hexdigest()
    _TRANSLATE_CACHE_TTL = 1800  # 30 minutes

    def _tr_cache_store(result: str) -> str:
        """Store result in Redis and return it unchanged."""
        if result:
            try:
                if redis_client:
                    redis_client.setex(_cache_key, _TRANSLATE_CACHE_TTL, result)
            except Exception:
                pass
        return result

    try:
        if redis_client:
            _cached = redis_client.get(_cache_key)
            if _cached:
                logger.debug("[INDIC-TRANSLATE] Redis cache hit for %r", src[:40])
                return _cached if isinstance(_cached, str) else _cached.decode("utf-8", errors="replace")
    except Exception:
        pass  # cache miss — proceed normally

    # ── Step 0: Sarvam translate:v1 (primary — dedicated translation model) ─
    # Sarvam's /translate endpoint is purpose-built for Indic languages and
    # outperforms Gemini on Assamese fluency. Qwen is the Step 1 fallback.
    _sarvam_tc = getattr(deps, "sarvam_translate_client", None) or getattr(deps, "sarvam_client", None)
    if _sarvam_tc:
        try:
            _sv_resp = await asyncio.wait_for(
                _sarvam_tc.post("/translate", json={
                    "input": src[:1950],
                    "source_language_code": "en-IN",
                    "target_language_code": target_lang_code,
                    "speaker_gender": "Female",
                    "mode": "formal",
                    "model": "sarvam-translate:v1",
                    "enable_preprocessing": False,
                }),
                timeout=2.0,
            )
            if _sv_resp.status_code == 200:
                _sv_result = (_sv_resp.json().get("translated_text") or "").strip()
                if _sv_result:
                    logger.debug("[INDIC-TRANSLATE] Sarvam translate:v1 primary OK for %r", src[:40])
                    return _tr_cache_store(_sv_result)
            else:
                logger.warning("[INDIC-TRANSLATE] Sarvam translate HTTP %d — falling back to Qwen", _sv_resp.status_code)
        except asyncio.TimeoutError:
            logger.info("[INDIC-TRANSLATE] Sarvam translate timed out after 3.5s — falling back to Qwen")
        except Exception as _sv_err:
            logger.warning("[INDIC-TRANSLATE] Sarvam translate failed (%s) — falling back to Qwen", _sv_err)

    # ── Step 1: IndicTrans2 fallback (Workers AI) → Gemini fallback ───────────
    # Workers AI IndicTrans2 (primary): purpose-built Indic neural MT model.
    #   Fast (~300-600ms), zero LLM quota consumed, best Assamese script output.
    # Gemini (secondary): handles edge-cases where IndicTrans2 returns empty.
    #
    # Translation prompt: explicit Assamese-only instruction so Gemini
    # doesn't produce code-switched output. Numbers, units, proper nouns
    # (AHSEC, SEBA, DNA, ATP) are allowed in Latin per the Assamese style guide.
    _TRANSLATE_SYSTEM = (
        "You are an expert English-to-Assamese (অসমীয়া) translator. "
        "Translate the input text to fluent, standard Assamese script. "
        "Rules: (1) Output ONLY the Assamese translation — no English words "
        "except pure numbers, scientific units (cm, kg, °C, eV), math symbols, "
        "and well-known proper nouns/acronyms (AHSEC, SEBA, NCERT, DNA, ATP, GDP, Newton). "
        "(2) No preamble, no explanation, no quote marks around the output."
    )
    _TRANSLATE_USER = f"Translate to Assamese:\n\n{src[:2000]}"

    translate_out = ""
    _translate_timed_out = False

    # Tier A: Workers AI IndicTrans2 — dedicated Indic neural MT
    try:
        from providers.workers_indic import call_indic_trans as _indic_trans
        _indic_result = await asyncio.wait_for(
            _indic_trans(src[:2000], direction="en-indic"),
            timeout=_QWEN_TRANSLATE_TIMEOUT_SEC,
        )
        translate_out = (_indic_result or "").strip()
        if translate_out:
            logger.info("[INDIC-TRANSLATE] Workers AI IndicTrans2 OK for %r", src[:40])
        else:
            logger.info("[INDIC-TRANSLATE] Workers AI IndicTrans2 returned empty for %r", src[:40])
    except asyncio.TimeoutError:
        logger.warning("[INDIC-TRANSLATE] Workers AI IndicTrans2 timed out after %ss", _QWEN_TRANSLATE_TIMEOUT_SEC)
        _translate_timed_out = True
    except Exception as _it_err:
        logger.warning("[INDIC-TRANSLATE] Workers AI IndicTrans2 failed (%s: %s) — trying Gemini",
                       type(_it_err).__name__, str(_it_err)[:120])

    # Tier B: Gemini (only if Tier A failed/empty AND didn't timeout)
    if not translate_out and not _translate_timed_out:
        try:
            from llm import _call_gemini as _gemini_call, _GEMINI_KEY as _gkey
            if _gkey:
                _gemini_result = await asyncio.wait_for(
                    _gemini_call(
                        [
                            {"role": "system", "content": _TRANSLATE_SYSTEM},
                            {"role": "user", "content": _TRANSLATE_USER},
                        ],
                        _gkey, "gemini-2.5-flash", 1200,
                    ),
                    timeout=_QWEN_TRANSLATE_TIMEOUT_SEC,
                )
                translate_out = (_gemini_result or "").strip()
                if translate_out:
                    logger.info("[INDIC-TRANSLATE] Gemini fallback OK for %r", src[:40])
                else:
                    logger.info("[INDIC-TRANSLATE] Gemini returned empty for %r", src[:40])
            else:
                logger.info("[INDIC-TRANSLATE] Gemini key not available — skipping")
        except asyncio.TimeoutError:
            logger.warning("[INDIC-TRANSLATE] Gemini timed out — pipeline exhausted for %r", src[:40])
            _translate_timed_out = True
        except Exception as _ge:
            logger.warning("[INDIC-TRANSLATE] Gemini failed: %s: %s",
                           type(_ge).__name__, str(_ge)[:120])

    if not translate_out:
        logger.info("[INDIC-TRANSLATE] translation tier exhausted (timeout=%s) for %r", _translate_timed_out, src[:60])
        return ""

    # ── Step 2: Sarvam polish (optional, best-effort) ───────────────────
    if len(translate_out) < _POLISH_MIN_LEN:
        return _tr_cache_store(translate_out)

    # Read the live `sarvam_llm_client` attribute off the deps module so
    # tests that monkey-patch `deps.sarvam_llm_client = None` see the
    # current value rather than the import-time snapshot.
    _sarvam_chat = getattr(deps, "sarvam_llm_client", None)
    if _sarvam_chat is None:
        # No Sarvam client configured — return un-polished translation output.
        return _tr_cache_store(translate_out)

    try:
        polish_resp = await asyncio.wait_for(
            _sarvam_chat.post(
                "/v1/chat/completions",
                json={
                    "model": "sarvam-m",
                    "messages": [
                        {"role": "system", "content": _POLISH_SYSTEM_PROMPT},
                        {"role": "user", "content": translate_out[:4000]},
                    ],
                    "max_tokens": min(1200, len(translate_out) * 3 + 200),
                    "temperature": 0.05,
                    "top_p": 0.9,
                    "stream": False,
                    "thinking": {"enabled": False},
                    "response_language": target_lang_code,
                },
            ),
            timeout=_SARVAM_POLISH_TIMEOUT_SEC,
        )
        if polish_resp.status_code == 200:
            _body = polish_resp.json()
            _polished = (
                _body.get("choices", [{}])[0].get("message", {}).get("content", "")
                or ""
            ).strip()
            # Strip any sarvam-m <think> blocks (defense; we asked it to skip them).
            _polished = re.sub(r"<think>[\s\S]*?</think>", "", _polished).strip()
            # Also strip wrapping quote marks that LLMs sometimes add.
            if _polished.startswith(('"', "'", "“", "‘")) and _polished.endswith(('"', "'", "”", "’")):
                _polished = _polished[1:-1].strip()
            if _polished:
                return _tr_cache_store(_polished)
            logger.info(
                f"[INDIC-TRANSLATE] Sarvam polish returned empty body for "
                f"{translate_out[:60]!r} — using un-polished translation output"
            )
        else:
            logger.warning(
                f"[INDIC-TRANSLATE] Sarvam polish HTTP {polish_resp.status_code} "
                f"for {translate_out[:60]!r} — using un-polished translation output"
            )
    except asyncio.TimeoutError:
        logger.info(
            f"[INDIC-TRANSLATE] Sarvam polish timed out after "
            f"{_SARVAM_POLISH_TIMEOUT_SEC}s — using un-polished translation output"
        )
    except Exception as _pe:  # pragma: no cover — network defensive
        logger.warning(
            f"[INDIC-TRANSLATE] Sarvam polish exception "
            f"({type(_pe).__name__}: {str(_pe)[:120]}) — using un-polished translation output"
        )

    return _tr_cache_store(translate_out)


router = APIRouter()

_subject_ctx_cache: cachetools.TTLCache = cachetools.TTLCache(maxsize=256, ttl=3600)

async def _resolve_subject_context(subject_id: str) -> dict:
    if not subject_id:
        return {}
    cached = _subject_ctx_cache.get(subject_id)
    if cached is not None:
        return cached
    try:
        subj = await db.subjects.find_one({"id": subject_id}, {"_id": 0})
        if not subj:
            subj = await db.subjects.find_one({"slug": subject_id}, {"_id": 0})
        if not subj:
            _subject_ctx_cache[subject_id] = {}
            return {}
        ctx = {
            "board_id":      subj.get("board_id") or subj.get("boardId") or "",
            "board_name":    subj.get("boardName") or "",
            "board_slug":    subj.get("board_slug") or "",
            "class_id":      subj.get("class_id") or "",
            "class_name":    subj.get("className") or "",
            "class_slug":    subj.get("class_slug") or "",
            "stream_id":     subj.get("stream_id") or "",
            "stream_name":   subj.get("streamName") or "",
            "stream_slug":   subj.get("stream_slug") or "",
            "subject_name":  subj.get("name") or "",
            "subject_slug":  subj.get("slug") or "",
        }
        if ctx["board_slug"] and ctx["class_slug"]:
            _subject_ctx_cache[subject_id] = ctx
            return ctx
        _sid = subj.get("stream_id")
        if _sid:
            stream = await db.streams.find_one({"id": _sid}, {"_id": 0, "id": 1, "name": 1, "slug": 1, "class_id": 1})
            if stream:
                ctx["stream_id"] = stream.get("id", "")
                ctx["stream_name"] = ctx["stream_name"] or stream.get("name", "")
                ctx["stream_slug"] = ctx["stream_slug"] or stream.get("slug", "")
                _cid = stream.get("class_id")
                if _cid:
                    cls = await db.classes.find_one({"id": _cid}, {"_id": 0, "id": 1, "name": 1, "slug": 1, "board_id": 1})
                    if cls:
                        ctx["class_id"] = cls.get("id", "")
                        ctx["class_name"] = ctx["class_name"] or cls.get("name", "")
                        ctx["class_slug"] = ctx["class_slug"] or cls.get("slug", "")
                        _bid = cls.get("board_id")
                        if _bid:
                            board = await db.boards.find_one({"id": _bid}, {"_id": 0, "id": 1, "name": 1, "slug": 1})
                            if board:
                                ctx["board_id"] = board.get("id", "")
                                ctx["board_name"] = ctx["board_name"] or board.get("name", "")
                                ctx["board_slug"] = ctx["board_slug"] or board.get("slug", "")
        _subject_ctx_cache[subject_id] = ctx
        return ctx
    except Exception as e:
        logger.warning(f"_resolve_subject_context({subject_id}) failed: {e}")
        return {}

async def _resolve_semester_class_id(query: str, ctx_board_id: str) -> str | None:
    sem_num = extract_semester_number(query)
    if not sem_num:
        return None
    sem_slugs = [f"semester-{sem_num}", f"{sem_num}th-sem", f"{sem_num}nd-sem", f"{sem_num}st-sem", f"{sem_num}rd-sem"]
    try:
        cls = await db.classes.find_one(
            {"board_id": ctx_board_id, "slug": {"$in": sem_slugs}},
            {"_id": 0, "id": 1},
        )
        if cls:
            logger.info(f"Semester {sem_num} resolved to class_id={cls['id']} for board {ctx_board_id}")
            return cls["id"]
    except Exception as e:
        logger.warning(f"_resolve_semester_class_id failed: {e}")
    return None

_OCR_ALLOWED_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif"}
_OCR_MAX_BYTES = 8 * 1024 * 1024  # 8MB
_OCR_READ_CHUNK = 64 * 1024  # 64KB streamed reads — keeps peak RSS bounded


def _sniff_image_mime(buf: bytes) -> Optional[str]:
    """Magic-byte sniff for the formats Vertex Gemini Vision accepts.

    Returns the canonical `image/<fmt>` string, or None if the buffer does
    not start with a recognised image header. Client-supplied `Content-Type`
    is untrusted, so this guards Vertex against being handed (and billed
    for) non-image payloads such as PDFs or scripts.
    """
    if not buf or len(buf) < 12:
        return None
    if buf.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if buf.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if buf[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if buf[:4] == b"RIFF" and buf[8:12] == b"WEBP":
        return "image/webp"
    # HEIC/HEIF — ISO-BMFF box: bytes 4..8 == "ftyp", then a brand string.
    if buf[4:8] == b"ftyp" and buf[8:12] in (b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1"):
        return "image/heic"
    return None


@router.post("/ai/ocr-image")
async def ocr_chat_image(
    request: Request,
    file: UploadFile = File(...),
    user: Optional[dict] = Depends(rate_limit_ocr_optional),
):
    """Public OCR endpoint for the chat composer.

    Accepts a single image (JPEG/PNG/WebP/GIF/HEIC, ≤8MB), runs Vertex AI
    Gemini Vision OCR, and returns the extracted text. The endpoint uses
    :func:`rate_limit_ocr_optional` (NOT the chat dep) so OCR uploads do
    not consume the daily chat-message budget — Task #819. The follow-up
    chat send still costs 1 credit on its own; OCR is free.

    Bounded streaming reads cap memory pressure and magic-byte sniffing
    refuses non-image payloads before any
    Vertex call is made (a Vertex Vision call is far more expensive than
    a chat call). No documents/PDFs — the chat composer is image-only by
    product decision.
    """
    is_anon = user is None

    # 1. Mime check on the client-supplied Content-Type (cheap fast-path).
    # 415 Unsupported Media Type is the semantically correct status for an
    # unaccepted Content-Type header — RFC 9110 §15.5.16.
    ct = (file.content_type or "").lower()
    if ct not in _OCR_ALLOWED_MIME:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {ct or 'unknown'}. Please upload an image (JPEG, PNG, WebP, GIF or HEIC).",
        )

    # 3. Early reject by Content-Length when present — saves a streaming read.
    cl_hdr = request.headers.get("content-length")
    if cl_hdr:
        try:
            if int(cl_hdr) > _OCR_MAX_BYTES + 4096:  # +4KB multipart envelope overhead
                raise HTTPException(status_code=413, detail="Image too large — max 8MB.")
        except ValueError:
            pass

    # 4. Bounded streaming read — never buffer more than _OCR_MAX_BYTES + 1 byte.
    img_buf = bytearray()
    while True:
        chunk = await file.read(_OCR_READ_CHUNK)
        if not chunk:
            break
        img_buf.extend(chunk)
        if len(img_buf) > _OCR_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Image too large — max 8MB.")
    img_bytes = bytes(img_buf)
    if not img_bytes:
        raise HTTPException(status_code=400, detail="Empty image upload.")

    # 5. Magic-byte sniff — drop payloads whose actual bytes aren't an image,
    #    before paying for a Vertex Vision request.
    sniffed = _sniff_image_mime(img_bytes)
    if sniffed is None:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is not a recognised image (only JPEG, PNG, WebP, GIF and HEIC are supported).",
        )

    # 6. Vertex call — use the magic-byte-sniffed mime (defends against
    #    a JPEG body sent with a "image/png" Content-Type and vice versa).
    #
    # NOTE (Task #819): we deliberately do NOT deduct a chat credit
    # here. OCR is free; the user pays 1 credit only on the follow-up
    # /ai/chat send that contains the extracted question. Per-minute
    # throttle + per-IP coarse cap inside rate_limit_ocr_optional are
    # enough abuse protection — OCR's 10/min/device cap is an order of
    # magnitude tighter than chat's 15/min, so a single client can
    # never burst more than ~600 Vertex Vision calls/hour.
    vertex_mime = sniffed
    try:
        import vertex_services  # local import — avoids cold-start cost on chat path
    except Exception as _imp_err:
        logger.exception("vertex_services import failed")
        raise HTTPException(status_code=503, detail="OCR service unavailable.") from _imp_err

    try:
        result = await vertex_services.ocr_image(img_bytes, mime_type=vertex_mime)
    except Exception as _vx_err:
        logger.exception("vertex ocr_image raised")
        raise HTTPException(status_code=503, detail="OCR service unavailable.") from _vx_err

    if not isinstance(result, dict) or "error" in result:
        err_msg = (result or {}).get("error") if isinstance(result, dict) else "OCR failed"
        raise HTTPException(status_code=503, detail=err_msg or "OCR failed")

    raw_text = (result.get("raw_text") or "").strip()
    if not raw_text:
        # Fallback: if Vertex returned only a structured questions list, flatten it.
        qs = result.get("questions") or []
        if isinstance(qs, list) and qs:
            parts = []
            for q in qs:
                if not isinstance(q, dict):
                    continue
                num = q.get("number")
                txt = q.get("text") or ""
                parts.append(f"{num}. {txt}" if num else txt)
            raw_text = "\n".join(p for p in parts if p).strip()

    if not raw_text:
        raise HTTPException(status_code=422, detail="No text could be extracted from the image.")

    return {
        "text": raw_text,
        "content_type": result.get("content_type") or "extracted",
        "word_count": result.get("word_count") or len(raw_text.split()),
    }


@router.post("/ai/chat")
async def chat(msg: ChatMessage, request: Request, user: Optional[dict] = Depends(rate_limit_chat_optional)):
    _chat_t0 = _time_mod.time()
    is_anon = user is None

    plan = user.get("plan", "free") if user else "free"
    _plan_max_tokens = PLAN_LIMITS[plan]["max_tokens"]
    conv_id = msg.conversation_id
    user_id = user["id"] if user else None

    _detected_intent, _detected_db_category = classify_intent(msg.message)
    # Smart per-request budget: free plan ceiling is 10 000 (so a complex
    # "explain step by step" or "solve every PYQ" request can complete) but
    # the median question stays at the medium ~1024-token tier — see
    # ``prompts.compute_answer_budget`` for the intent + keyword heuristics.
    max_tokens = compute_answer_budget(msg.message, _detected_intent, _plan_max_tokens)
    logger.info(
        f"[NON-STREAM] answer-budget: intent={_detected_intent} "
        f"plan={plan} plan_max={_plan_max_tokens} chosen={max_tokens}"
    )

    _ns_resp_lang = (msg.response_lang or "").lower().strip()
    _instant = get_instant_response(msg.message) if (_detected_intent == "casual" and _ns_resp_lang in ("", "en")) else None
    if _instant:
        credits_info = None
        if not is_anon:
            credits_info = await get_user_credits(user)
            if credits_info["remaining"] <= 0:
                raise HTTPException(status_code=402, detail=f"Daily credit limit reached ({credits_info['limit']} credits/day). Resets at midnight UTC. Upgrade your plan for more.")
        logger.info(f"[NON-STREAM] INSTANT casual fast-path: '{msg.message[:30]}' → {len(_instant)} chars (0 LLM calls)")
        return {
            "answer": _instant,
            "conversation_id": conv_id,
            "credits_remaining": credits_info["remaining"] if credits_info else None,
            "credits_used": 0 if not is_anon else None,
            "rag_source": "none",
            "rag_chunks_used": 0,
            "sources": [],
        }

    if not is_anon:
        credits_info = await get_user_credits(user)
        if credits_info["remaining"] <= 0:
            raise HTTPException(status_code=402, detail=f"Daily credit limit reached ({credits_info['limit']} credits/day). Resets at midnight UTC. Upgrade your plan for more.")

    _is_card_context = bool(msg.card_context and msg.card_context.strip())

    async def _ns_fetch_doc():
        if _is_card_context:
            logger.info(f"Chat [NON-STREAM]: card_context ({len(msg.card_context)} chars) used as grounding")
            return msg.card_context
        if not msg.document_id:
            return None
        subj = await db.subjects.find_one({"id": msg.document_id}, {"_id": 0, "document_text": 1})
        return (subj or {}).get("document_text")

    async def _ns_fetch_stage1_early():
        if not should_use_pipeline(_detected_intent, msg.message):
            return None
        result = await stage1_resolve_topic(msg.message)
        return result if result else {}

    async def _ns_fetch_followup():
        if not (conv_id and user_id):
            return None
        try:
            _conv_for_followup = await supa_get_conversation(conv_id, user_id)
            if _conv_for_followup:
                _conv_meta = _safe_metadata(_conv_for_followup.get("metadata"))
                return detect_followup(msg.message, _conv_meta)
        except Exception as _fu_err:
            logger.warning(f"Follow-up detection failed: {_fu_err}")
        return None

    async def _ns_prefetch_history():
        if not conv_id:
            return None
        if user_id:
            return await supa_get_conversation(conv_id, user_id)
        return None

    _t_phase0 = _time_mod.time()
    _phase0_results = await asyncio.gather(
        _resolve_subject_context(msg.subject_id),
        _resolve_semester_class_id(msg.message, msg.board_id) if msg.board_id else asyncio.sleep(0),
        _ns_fetch_doc(),
        _ns_fetch_stage1_early(),
        _ns_fetch_followup(),
        _ns_prefetch_history(),
        _fetch_internal_chapters(msg.message, subject_id=msg.subject_id, subject_name=msg.subject_name) if (msg.subject_id or msg.subject_name) else asyncio.sleep(0),
        return_exceptions=True,
    )
    _subj_ctx_result = _phase0_results[0] if not isinstance(_phase0_results[0], BaseException) else {}
    _sem_class_result = _phase0_results[1] if not isinstance(_phase0_results[1], BaseException) else None
    document_text = _phase0_results[2] if not isinstance(_phase0_results[2], BaseException) else None
    _topic_metadata = _phase0_results[3] if not isinstance(_phase0_results[3], BaseException) else None
    _followup_info = _phase0_results[4] if not isinstance(_phase0_results[4], BaseException) else None
    _prefetched_conv = _phase0_results[5] if not isinstance(_phase0_results[5], BaseException) else None
    _prefetched_chapters = _phase0_results[6] if not isinstance(_phase0_results[6], BaseException) else []
    for _i, _r in enumerate(_phase0_results):
        if isinstance(_r, BaseException):
            logger.warning(f"[NON-STREAM] Phase 0 task {_i} failed (degrading gracefully): {_r}")

    subj_ctx = _subj_ctx_result
    ctx_board_id   = subj_ctx.get("board_id")   or msg.board_id
    ctx_class_id   = subj_ctx.get("class_id")   or msg.class_id
    ctx_stream_id  = subj_ctx.get("stream_id")  or getattr(msg, 'stream_id', None)
    ctx_board_name = subj_ctx.get("board_name") or msg.board_name or (user.get("board_name", "") if user else "")
    ctx_class_name = subj_ctx.get("class_name") or msg.class_name or (user.get("class_name", "") if user else "")
    ctx_stream_name= subj_ctx.get("stream_name") or getattr(msg, 'stream_name', None) or (user.get("stream_name", "") if user else "")
    if subj_ctx:
        logger.info(f"Chat [NON-STREAM]: Subject context resolved → {ctx_board_name} / {ctx_class_name} / {ctx_stream_name}")

    if not _sem_class_result and ctx_board_id and ctx_board_id != msg.board_id:
        _sem_class_result = await _resolve_semester_class_id(msg.message, ctx_board_id)

    _syl_class_id = _sem_class_result or ctx_class_id
    if _sem_class_result:
        logger.info(f"Chat [NON-STREAM]: Semester override class_id={_sem_class_result} (from query)")

    _original_message = msg.message

    if _followup_info:
        _detected_intent = _followup_info["prev_intent"]
        _detected_db_category = {"notes": "notes", "important_questions": "important_questions", "pyq": "question_paper"}.get(_detected_intent)
        msg.message = merge_followup_into_query(
            msg.message, _followup_info,
            subject_name=msg.subject_name or "",
            chapter_name=msg.chapter_name or "",
        )
        logger.info(f"[NON-STREAM] Follow-up detected: intent={_detected_intent}, rewritten query='{msg.message[:60]}'")

    if not _followup_info and _topic_metadata and _topic_metadata.get("intent"):
        _detected_intent, _detected_db_category = apply_stage1_to_intent(
            _topic_metadata, _detected_intent, _detected_db_category
        )
        logger.info(f"[PIPELINE][S1] Intent resolved: {_detected_intent} (Stage 1 primary)")

    _q_lower_ns = msg.message.lower()
    if _detected_intent == "syllabus" and _topic_metadata and _topic_metadata.get("search_keywords") and "syllabus" not in _q_lower_ns and "curriculum" not in _q_lower_ns and "subject list" not in _q_lower_ns:
        _detected_intent = "notes"
        _detected_db_category = "notes"
        logger.info(f"[NON-STREAM] Intent upgrade: syllabus → notes (Stage 1 has search_keywords, query is content-seeking)")

    _is_casual_sync = _detected_intent in ("casual", "general")

    _rag_query = msg.message
    if _topic_metadata and _topic_metadata.get("search_keywords"):
        _rag_query = build_enhanced_query(msg.message, _topic_metadata)
        if _rag_query != msg.message:
            logger.info(f"[PIPELINE][S1] Enhanced search query: '{_rag_query[:80]}'")

    async def _ns_fetch_syllabus():
        if not (ctx_board_id and _syl_class_id):
            return None
        _sck = _syllabus_cache_key(ctx_board_id, _syl_class_id, ctx_stream_id, msg.subject_id)
        if _sck in _syllabus_cache:
            return _syllabus_cache[_sck]
        try:
            queries = []
            if ctx_stream_id and msg.subject_id:
                queries.append(db.syllabi.find_one({"board_id": ctx_board_id, "class_id": _syl_class_id, "stream_id": ctx_stream_id, "subject_id": msg.subject_id}, {"_id": 0}))
            if ctx_stream_id:
                queries.append(db.syllabi.find_one({"board_id": ctx_board_id, "class_id": _syl_class_id, "stream_id": ctx_stream_id}, {"_id": 0}))
            queries.append(db.syllabi.find_one({"board_id": ctx_board_id, "class_id": _syl_class_id, "stream_id": {"$exists": False}}, {"_id": 0}))
            queries.append(db.syllabi.find_one({"board_id": ctx_board_id, "class_id": _syl_class_id}, {"_id": 0}))
            results = await asyncio.gather(*queries, return_exceptions=True)
            s = None
            for r in results:
                if r and not isinstance(r, Exception):
                    s = r
                    break
            if s:
                _syllabus_cache[_sck] = s
            return s
        except Exception:
            return None

    async def _ns_fetch_history():
        if _prefetched_conv:
            raw = [
                {"role": m.get("role", ""), "content": m.get("content") or ""}
                for m in _prefetched_conv.get("messages", [])
                if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
            ]
            return _trim_history(raw)
        if conv_id and user_id:
            conv = await supa_get_conversation(conv_id, user_id)
            if conv:
                raw = [
                    {"role": m.get("role", ""), "content": m.get("content") or ""}
                    for m in conv.get("messages", [])
                    if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
                ]
                return _trim_history(raw)
        return []

    _t_phase0_done = _time_mod.time()
    logger.info(f"[NON-STREAM][TIMING] Phase 0 (parallel context): {_t_phase0_done - _t_phase0:.3f}s")

    rag_ctx = {"chunks": [], "chapters": [], "chunk_chapters": [], "subjects": [],
               "vector_hits": [], "source": "none", "quality": "none",
               "_general_knowledge_fallback": True}
    _s1_subject_str = (_topic_metadata.get("subject", "") if _topic_metadata else "").strip()
    if _s1_subject_str:
        rag_ctx["_stage1_subject"] = _s1_subject_str

    _use_prefetched = _prefetched_chapters if (_prefetched_chapters and _rag_query == _original_message) else None
    _rag_content_budget = 8000 if _detected_intent in ("notes", "important_questions", "pyq") else 4000
    rag_ctx = await resolve_rag_context(
        _rag_query, subject_id=msg.subject_id, subject_name=msg.subject_name,
        document_text=document_text, intent=_detected_intent,
        prefetched_chapters=_use_prefetched,
        max_content_chars=_rag_content_budget,
    )
    if _s1_subject_str:
        rag_ctx["_stage1_subject"] = _s1_subject_str

    _has_internal = rag_ctx.get("_has_internal_content") or rag_ctx.get("source") in ("document", "internal")

    async def _ns_fetch_web():
        if _is_casual_sync or document_text or _has_internal:
            return []
        try:
            return await web_search_with_fallback(
                _rag_query,
                board_name=ctx_board_name or "",
                class_name=ctx_class_name or "",
                subject_name=msg.subject_name or "",
                chapter_name=msg.chapter_name or "",
                enrich_top_n=2,
            )
        except Exception as _ws_err:
            logger.warning(f"[NON-STREAM] Web search failed (non-fatal): {_ws_err}")
            return []

    _ns_phase2 = await asyncio.gather(
        _ns_fetch_history(),
        _ns_fetch_syllabus(),
        _ns_fetch_web(),
        return_exceptions=True,
    )
    history_messages = _ns_phase2[0] if not isinstance(_ns_phase2[0], BaseException) else []
    syllabus = _ns_phase2[1] if not isinstance(_ns_phase2[1], BaseException) else None
    web_results = _ns_phase2[2] if not isinstance(_ns_phase2[2], BaseException) else []
    for _pi, _pr in enumerate(_ns_phase2):
        if isinstance(_pr, BaseException):
            logger.warning(f"[NON-STREAM] Phase 2 task {_pi} failed: {_pr}")

    # ── Build system prompt with web search context ───────────────────────────
    system_prompt = build_rag_system_prompt(
        {
            "board_name":  ctx_board_name,
            "class_name":  ctx_class_name,
            "stream_name": ctx_stream_name,
            "subject_name": msg.subject_name,
            "subject_id":  msg.subject_id,
            "chapter_name": msg.chapter_name,
        },
        rag_ctx,
        user_info={
            "name":        (user.get("name", "") if user else ""),
            "board_name":  ctx_board_name or (user.get("board_name", "") if user else ""),
            "class_name":  ctx_class_name or (user.get("class_name", "") if user else ""),
            "stream_name": ctx_stream_name or (user.get("stream_name", "") if user else ""),
            "plan":        (user.get("plan", "free") if user else "free"),
        },
        query=msg.message,
        syllabus=syllabus,
        web_results=web_results or None,
        resolved_intent=_detected_intent,
        response_lang=_ns_resp_lang,
    )
    if not conv_id and user_id:
        conv_id = str(uuid.uuid4())
        title = msg.message[:50] + ("..." if len(msg.message) > 50 else "")
        conv_doc = {
            "id": conv_id,
            "user_id": user_id,
            "title": title,
            "subject_id": msg.subject_id,
            "subject_name": msg.subject_name,
            "messages": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await supa_upsert_conversation(conv_doc)
    elif not user_id:
        conv_id = None

    _MAX_PROMPT_CHARS_NS = 100_000
    _total_chars_ns = len(system_prompt) + sum(len(m.get("content", "")) for m in history_messages) + len(msg.message)
    if _total_chars_ns > _MAX_PROMPT_CHARS_NS:
        _overhead_ns = len(system_prompt) - (_total_chars_ns - _MAX_PROMPT_CHARS_NS)
        if _overhead_ns > 2000:
            system_prompt = system_prompt[:_overhead_ns]
            logger.warning(f"[CHAT] System prompt truncated from {_total_chars_ns} to ~{_MAX_PROMPT_CHARS_NS} chars to fit provider limits")

    messages = [{"role": "system", "content": system_prompt}] + history_messages + [{"role": "user", "content": msg.message}]

    # ── Cache check (Non-streaming) — managed AI cache (Memorystore) ────────
    is_casual = _detected_intent in ("casual", "general")
    _ns_model = msg.model or "meta-llama/llama-4-scout-17b-16e-instruct"
    cache_key = build_ai_cache_key(
        model=_ns_model,
        prompt=msg.message,
        retrieval=rag_ctx,
        scope="" if is_casual else (conv_id or ""),
    )
    _cache_ttl = REDIS_CASUAL_CACHE_TTL if is_casual else REDIS_AI_CACHE_TTL
    answer = None
    _ns_cache_hit = False
    _ns_cache_t0 = _time_mod.time()

    answer = await ai_cache_aget(cache_key)
    if answer:
        _ns_cache_hit = True
        ai_cache_record_hit_saved_latency(ai_cache_expected_saved_ms())
        logger.info(f"AI cache HIT (managed): {cache_key}")
    elif cache_key in _ai_response_cache:
        answer = _ai_response_cache[cache_key]
        _ns_cache_hit = True
        ai_cache_record_hit_saved_latency(ai_cache_expected_saved_ms())
        logger.info(f"AI cache HIT (L1): {cache_key}")

    if answer is None:
        _t_llm_start = _time_mod.time()
        try:
            answer = await call_llm_api_chat(messages, model=_ns_model, max_tokens=max_tokens, lang=_ns_resp_lang or "en")
            _llm_elapsed_ms = (_time_mod.time() - _t_llm_start) * 1000
            await ai_cache_aset(cache_key, answer, _cache_ttl, saved_ms=_llm_elapsed_ms)
            _ai_response_cache[cache_key] = answer
            _t_llm_done = _time_mod.time()
            logger.info(f"[NON-STREAM][TIMING] LLM call: {_t_llm_done - _t_llm_start:.3f}s | Cache MISS → stored (ttl={_cache_ttl}s): {cache_key}")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"AI chat error: {e}")
            raise HTTPException(status_code=503, detail="AI service temporarily unavailable")

    lib_sources = _sources_from_rag_ctx(rag_ctx)
    if web_results:
        lib_sources.extend(_sources_from_web_results(web_results))

    now = datetime.now(timezone.utc).isoformat()
    _rag_subject_ids = list({s["id"] for s in rag_ctx.get("subjects", []) if s.get("id")})
    _rag_subject_names = list({s.get("name","") for s in rag_ctx.get("subjects", []) if s.get("name")})
    _src_sid = _rag_subject_ids[0] if _rag_subject_ids else msg.subject_id
    if _src_sid and _src_sid == msg.subject_id and subj_ctx:
        _src_ctx = subj_ctx
    else:
        _src_ctx = await _resolve_subject_context(_src_sid) if _src_sid else {}
    _src_board = _src_ctx.get("board_name") or ctx_board_name or ""
    _src_class = _src_ctx.get("class_name") or ctx_class_name or ""
    _src_stream = _src_ctx.get("stream_name") or ctx_stream_name or ""
    _src_board_slug_nr = _src_ctx.get("board_slug") or ""
    _src_class_slug_nr = _src_ctx.get("class_slug") or ""
    _src_subject_slug_nr = _src_ctx.get("subject_slug") or ""
    new_messages = [
        {"role": "user", "content": msg.message, "timestamp": now},
        {"role": "assistant", "content": answer, "timestamp": now,
         "rag_source": rag_ctx.get("source", "none"),
         "rag_chunks": len(rag_ctx.get("chunks", [])),
         "sources": lib_sources,
         "rag_subject_id": _rag_subject_ids[0] if _rag_subject_ids else None,
         "rag_subject_name": _rag_subject_names[0] if _rag_subject_names else msg.subject_name,
         "rag_board_name": _src_board,
         "rag_class_name": _src_class,
         "rag_stream_name": _src_stream,
         "rag_board_slug": _src_board_slug_nr,
         "rag_class_slug": _src_class_slug_nr,
         "rag_subject_slug": _src_subject_slug_nr},
    ]
    new_used = 0
    if user_id:
        # Optimistic new_used: the precheck at ~L247 already confirmed the
        # user has remaining credits, so we account for 1 immediately and
        # fire the real deduction in the background (see _ns_tail_writes
        # below). Races across concurrent requests can at most let the user
        # overspend by 1 credit — acceptable for chat.
        new_used = credits_info["used"] + 1

        # Reuse the conversation prefetched in Phase 0 (line ~294) instead
        # of re-fetching from Supabase here. That one round-trip is worth
        # ~100–300ms on the happy path.
        conv = _prefetched_conv
        _update_payload = None
        if conv:
            existing_msgs = conv.get("messages", [])
            if isinstance(existing_msgs, str):
                try: existing_msgs = json.loads(existing_msgs)
                except: existing_msgs = []
            updated_msgs = existing_msgs + new_messages
            _update_payload = {
                "messages": json.dumps(updated_msgs) if supa else updated_msgs,
                "updated_at": now,
                "preview": answer[:100],
                "tokens": len(answer.split()),
            }
            if _detected_intent in ("notes", "important_questions", "pyq"):
                _existing_meta = _safe_metadata(conv.get("metadata"))
                _prev_followup = _existing_meta.get("followup_context") or {}
                _completed = list(_prev_followup.get("completed", []))
                _current = _prev_followup.get("current_item", "")
                if _current and _current not in _completed:
                    _completed.append(_current)
                _remaining = list(_prev_followup.get("remaining", []))
                if _current in _remaining:
                    _remaining.remove(_current)
                if not _remaining:
                    if _detected_intent == "pyq":
                        _remaining = [m for m in ["1m", "2m", "3m", "5m", "10m"] if m not in _completed]
                    else:
                        _rag_chapters = rag_ctx.get("chapters", [])
                        _remaining = [
                            ch.get("title", "") for ch in _rag_chapters
                            if ch.get("title", "") and ch.get("title", "") not in _completed
                        ]
                _new_current = msg.chapter_name or _current
                if _new_current in _remaining:
                    _remaining.remove(_new_current)
                _new_followup = build_followup_context(
                    intent=_detected_intent,
                    current_item=_new_current,
                    completed=_completed,
                    remaining=_remaining,
                )
                _existing_meta["followup_context"] = _new_followup
                _update_payload["metadata"] = _existing_meta

        # Fire all three tail writes (conversation save, credit deduction,
        # admin chat log) in parallel in the background. The user's response
        # is sent immediately; these finish afterwards. Errors are logged
        # but don't affect the user-visible answer.
        async def _ns_tail_writes():
            tasks = []
            if _update_payload is not None:
                tasks.append(supa_update_conversation(conv_id, user_id, _update_payload))
            tasks.append(atomic_deduct_credit(user_id, credits_info["used"], credits_info["limit"]))
            tasks.append(_log_chat_message(
                user_id=user_id,
                question=msg.message,
                raw_ai_answer=answer,
                subject_id=msg.subject_id,
                subject_name=msg.subject_name,
                board_name=ctx_board_name,
                class_name=ctx_class_name,
                conversation_id=conv_id,
            ))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for _i, _res in enumerate(results):
                if isinstance(_res, BaseException):
                    logger.warning(f"[NON-STREAM] background tail-write task {_i} failed: {_res}")
                elif _i == (1 if _update_payload is not None else 0) and _res is False:
                    # atomic_deduct_credit returned False (race: credits already drained
                    # by a concurrent request). User already has their answer; just log.
                    logger.warning(f"[NON-STREAM] atomic_deduct_credit race detected for user={user_id} — overspend by 1 credit")
        asyncio.create_task(_ns_tail_writes())

    _ns_total_ms = (_time_mod.time() - _chat_t0) * 1000
    try:
        from middleware import request_id_var
        _ns_rid = request_id_var.get("")
    except Exception:
        _ns_rid = ""
    logger.info(
        f"[NON-STREAM][TIMING][SUMMARY] "
        f"rid={_ns_rid} | "
        f"total={_ns_total_ms:.0f}ms | "
        f"intent={_detected_intent} | "
        f"rag_quality={rag_ctx.get('quality', 'none')} | "
        f"words={len(answer.split()) if answer else 0}"
    )
    try:
        _record_chat_latency(_ns_total_ms)
    except Exception:
        pass

    try:
        _prompt_chars = sum(len(m.get("content", "")) for m in messages)
        _compl_chars  = len(answer) if answer else 0
        _actual_provider = "cache" if _ns_cache_hit else getattr(answer, "provider", "unknown")
        _record_llm_cost(
            model=msg.model or LLM_MODEL,
            prompt_tokens=max(1, _prompt_chars // 4),
            completion_tokens=max(1, _compl_chars // 4),
            provider=_actual_provider,
            user_id=str(user_id) if user_id else "anonymous",
        )
    except Exception:
        pass

    # Task #636 — surface provider/fallback attribution in chat metadata so
    # the frontend (and any API consumer) can tell when a response came
    # from the Workers AI fallback tier instead of the configured primary.
    # `answer` is serialized as a string by FastAPI, so the LlmResult's
    # provider/fallback_reason attributes are otherwise dropped.
    _meta_provider = getattr(answer, "provider", None) or "unknown"
    _meta_reason = getattr(answer, "fallback_reason", "") or ""
    return {
        "answer": answer,
        "meta": {
            "provider": _meta_provider,
            "fallback_reason": _meta_reason,
            "fallback": bool(_meta_reason) or _meta_provider == "workers-ai",
        },
        "conversation_id": conv_id,
        "credits_remaining": max(0, credits_info["remaining"] - 1) if not is_anon else None,
        "credits_used": new_used if not is_anon else None,
        "rag_source": rag_ctx.get("source", "none"),
        "rag_chunks_used": len(rag_ctx.get("chunks", [])),
        "sources": lib_sources,
        "rag_subject_id": _rag_subject_ids[0] if _rag_subject_ids else None,
        "rag_subject_name": _rag_subject_names[0] if _rag_subject_names else msg.subject_name,
        "rag_board_name": _src_board,
        "rag_class_name": _src_class,
        "rag_stream_name": _src_stream,
        "rag_board_slug": _src_board_slug_nr,
        "rag_class_slug": _src_class_slug_nr,
        "rag_subject_slug": _src_subject_slug_nr,
    }

async def _refund_credit(uid: str, credits_used: int) -> None:
    """Refund 1 daily credit (decrement credits_used_today and credits_used) when streaming fails/empty answer."""
    from datetime import datetime, timezone
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        if deps.pg_pool:
            async with deps.pg_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE users SET credits_used_today = GREATEST(0, credits_used_today - 1), credits_used = GREATEST(0, credits_used - 1) WHERE id = $1",
                    uid,
                )
            return
        if redis_client:
            redis_key = f"daily_credits:{uid}:{today_str}"
            refunded_count = redis_client.decr(redis_key)
            if refunded_count is not None and refunded_count >= 0:
                user_data = await supa_get_user_by_id(uid)
                lifetime_used = max(0, (user_data.get("credits_used", 0) if user_data else 0) - 1)
                await supa_update_user(uid, {"credits_used_today": int(refunded_count), "credits_used": lifetime_used})
            return
        if credits_used > 0:
            user_data = await supa_get_user_by_id(uid)
            lifetime_used = max(0, (user_data.get("credits_used", 0) if user_data else 0) - 1)
            await supa_update_user(uid, {"credits_used_today": credits_used - 1, "credits_used": lifetime_used})
    except Exception as e:
        logger.warning(f"_refund_credit failed: {e}")

async def _persist_chat_turn(
    conv_id: str, user_id: str,
    user_msg: str, answer: str,
    rag_source: str, rag_chunks: int,
    credits_used_before: int,
    deduct_credit: bool = False,
    sources: list | None = None,
    rag_subject_id: str | None = None,
    rag_subject_name: str | None = None,
    rag_board_name: str | None = None,
    rag_class_name: str | None = None,
    rag_stream_name: str | None = None,
    rag_board_slug: str | None = None,
    rag_class_slug: str | None = None,
    rag_subject_slug: str | None = None,
    rag_chapter_name: str | None = None,
    rag_chapter_slug: str | None = None,
    rag_topic_name: str | None = None,
    rag_chunk_snippet: str | None = None,
    followup_context: dict | None = None,
    # SEO/GEO/AEO Enhancement metadata
    enhancements_applied: list | None = None,
    geo_score_boost: float = 0.0,
    cognitive_anchor: str | None = None,
):
    """Background: save conversation messages with GEO enhancements. Optionally deduct 1 credit. Non-blocking."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        assistant_msg = {
            "role": "assistant", "content": answer, "timestamp": now,
            "rag_source": rag_source, "rag_chunks": rag_chunks,
        }
        if sources:
            assistant_msg["sources"] = sources
        if rag_subject_id:
            assistant_msg["rag_subject_id"] = rag_subject_id
        if rag_subject_name:
            assistant_msg["rag_subject_name"] = rag_subject_name
        if rag_board_name:
            assistant_msg["rag_board_name"] = rag_board_name
        if rag_class_name:
            assistant_msg["rag_class_name"] = rag_class_name
        if rag_stream_name:
            assistant_msg["rag_stream_name"] = rag_stream_name
        if rag_board_slug:
            assistant_msg["rag_board_slug"] = rag_board_slug
        if rag_class_slug:
            assistant_msg["rag_class_slug"] = rag_class_slug
        if rag_subject_slug:
            assistant_msg["rag_subject_slug"] = rag_subject_slug
        if rag_chapter_name:
            assistant_msg["rag_chapter_name"] = rag_chapter_name
        if rag_chapter_slug:
            assistant_msg["rag_chapter_slug"] = rag_chapter_slug
        if rag_topic_name:
            assistant_msg["rag_topic_name"] = rag_topic_name
        if rag_chunk_snippet:
            assistant_msg["rag_chunk_snippet"] = rag_chunk_snippet
        
        # Add GEO enhancement metadata
        if enhancements_applied:
            assistant_msg["enhancements_applied"] = enhancements_applied
        if geo_score_boost > 0:
            assistant_msg["geo_score_boost"] = geo_score_boost
        if cognitive_anchor:
            assistant_msg["cognitive_anchor"] = cognitive_anchor
        
        new_msgs = [
            {"role": "user", "content": user_msg, "timestamp": now},
            assistant_msg,
        ]
        conv = await supa_get_conversation(conv_id, user_id)
        if conv:
            existing = conv.get("messages", [])
            if isinstance(existing, str):
                try: existing = json.loads(existing)
                except: existing = []
            updated = existing + new_msgs
            _persist_payload = {
                "messages": json.dumps(updated) if supa else updated,
                "updated_at": now,
                "preview": answer[:100],
                "tokens": len(answer.split()),
            }
            if followup_context:
                _existing_meta = _safe_metadata(conv.get("metadata"))
                _existing_meta["followup_context"] = followup_context
                _persist_payload["metadata"] = _existing_meta
            await supa_update_conversation(conv_id, user_id, _persist_payload)
        if deduct_credit:
            await atomic_deduct_credit(user_id, credits_used_before, 999999)
    except Exception as e:
        logger.warning(f"_persist_chat_turn failed: {e}")

@router.post("/ai/chat/stream")
async def chat_stream(msg: ChatMessage, request: Request, user: Optional[dict] = Depends(rate_limit_chat_optional)):
    _stream_t0 = _time_mod.time()
    _speedup.record_chat_started()
    is_anon = user is None
    user_id = user["id"] if user else None
    anon_id = None
    if is_anon:
        _raw_anon = request.headers.get("x-anon-id", "")
        if _raw_anon and re.match(r"^anon_[a-f0-9]{32}$", _raw_anon):
            anon_id = _raw_anon

    safe_prompt, fallback_msg, guardrail_tag = evaluate_prompt_safety(msg.message)
    if safe_prompt is not None:
        _llm_safety_tag = await llm_classify_safety(safe_prompt)
        if _llm_safety_tag:
            guardrail_tag = _llm_safety_tag
            safe_prompt = None
            fallback_msg = "I can only help with educational questions. Let's keep our conversation on-topic."
    _stream_intent, _stream_db_category = classify_intent(msg.message)

    # Task #610 — annotate the auto-created request span so chat traces are
    # filterable by intent / model / auth-mode / message length in Cloud Trace.
    # No-op when TRACING_ENABLED is unset.
    try:
        record_chat_attrs(**{
            "syrabit.chat.intent": _stream_intent or "",
            "syrabit.chat.model": (msg.model or "default"),
            "syrabit.chat.is_anon": bool(is_anon),
            "syrabit.chat.has_subject": bool(msg.subject_id or msg.subject_name),
            "syrabit.chat.has_card_context": bool(msg.card_context),
            "syrabit.chat.message_chars": len(msg.message or ""),
            "syrabit.chat.response_lang": (msg.response_lang or "en"),
        })
    except Exception:
        pass

    credits_info = None
    if not is_anon:
        credits_info = await get_user_credits(user)
        if credits_info["remaining"] <= 0:
            raise HTTPException(status_code=402, detail=f"Daily credit limit reached ({credits_info['limit']} credits/day). Resets at midnight UTC. Upgrade your plan for more.")
        deducted = await atomic_deduct_credit(user_id, credits_info["used"], credits_info["limit"])
        if not deducted:
            raise HTTPException(status_code=402, detail="Credit limit reached. Upgrade your plan for more.")

    if fallback_msg:
        logger.info(f"[guardrails] Prompt blocked ({guardrail_tag}) for user {user_id or 'anon'}: {msg.message[:80]!r}")
        async def _blocked_stream():
            yield f"data: {json.dumps({'conversation_id': msg.conversation_id or '', 'rag_source': 'none', 'rag_quality': 'none', 'rag_chunks': 0, 'guardrail_blocked': True})}\n\n"
            _CHUNK = 40
            for i in range(0, len(fallback_msg), _CHUNK):
                yield f"data: {json.dumps({'content': fallback_msg[i:i+_CHUNK]})}\n\n"
                await asyncio.sleep(0.01)
            yield f"data: {json.dumps({'event': 'syrabit_done', 'conversation_id': msg.conversation_id or '', 'guardrail_tag': guardrail_tag})}\n\n"
            yield "data: [DONE]\n\n"
        if not is_anon and credits_info:
            asyncio.create_task(_refund_credit(user_id, credits_info["used"] + 1))
        return StreamingResponse(_blocked_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    plan = user.get("plan", "free") if user else "free"
    _plan_max_tokens = PLAN_LIMITS[plan]["max_tokens"]
    # Smart per-request budget — same heuristic as the non-streaming path.
    # The intent has already been classified above as ``_stream_intent`` /
    # ``_detected_intent`` (whichever this branch uses); fall back to a fresh
    # classification if neither is in scope yet so we never break the route.
    _budget_intent = locals().get("_stream_intent") or locals().get("_detected_intent")
    if not _budget_intent:
        try:
            _budget_intent, _ = classify_intent(msg.message)
        except Exception:
            _budget_intent = "general"
    max_tokens = compute_answer_budget(msg.message, _budget_intent, _plan_max_tokens)
    logger.info(
        f"[STREAM] answer-budget: intent={_budget_intent} "
        f"plan={plan} plan_max={_plan_max_tokens} chosen={max_tokens}"
    )

    _t_auth_done = _time_mod.time()
    _auth_elapsed = _t_auth_done - _stream_t0

    _SARVAM_LANG_MAP = {"as": "as-IN"}

    def _make_assamese_translate_callable(_target: str):
        """Build an `async (str) -> str` callable that translates a single
        English fragment to Assamese for the leakage-sanitiser splice path
        (Task #419).

        Routing contract (user-mandated 2026-04-26 — see replit.md
        "LLM Providers"):
          • English → Assamese translation: **Qwen fallback + Sarvam polish**
          • Qwen (Cerebras Qwen-3-235B) does the heavy translation
            (high RPM headroom, native multilingual quality).
          • For substantive output (≥ `_POLISH_MIN_LEN` chars), Sarvam
            polishes the Qwen text via `sarvam-m` chat to lift it to
            native-Assamese fluency.
          • For short fragments (< `_POLISH_MIN_LEN` chars) — the common
            sanitiser case where leaked English runs are typically just a
            few words — polish is **skipped** because the per-fragment
            polish round-trip (~0.8-1.5s) would compound across N
            fragments into multi-second post-stream latency. Qwen's
            translation is already high-quality at that length.
          • All Sarvam polish failures degrade gracefully to the
            un-polished Qwen output. Qwen failure returns "" so the
            sanitiser falls back to its existing `strip` behaviour.
        """
        async def _translate(fragment: str) -> str:
            return await _assamese_translate_gemini_main_sarvam_polish(
                fragment, target_lang_code=_target
            )
        return _translate

    _resp_lang = (msg.response_lang or "").lower().strip()
    _sarvam_target = _SARVAM_LANG_MAP.get(_resp_lang)
    _want_translate = bool(_sarvam_target and _resp_lang != "en")

    # S2: Assamese greeting fast-path — returns pre-translated text directly,
    # skipping the entire LLM + translation pipeline (~3-7 s saved per greeting).
    if _want_translate and _stream_intent == "casual":
        _instant_as = get_instant_assamese_response(msg.message)
        if _instant_as:
            logger.info(f"[STREAM] INSTANT Assamese fast-path: '{msg.message[:30]}' → {len(_instant_as)} chars (0 LLM calls)")
            _speedup.record_instant_fastpath()
            _instant_as_ms = (_time_mod.time() - _stream_t0) * 1000
            _speedup.record_ttfb(_instant_as_ms)
            _speedup.record_total_latency(_instant_as_ms)
            try:
                _speedup.record_lang_ttfb(_resp_lang, _instant_as_ms, _instant_as_ms)
            except Exception:
                pass
            _instant_as_text = _instant_as
            async def _instant_as_stream():
                yield f"data: {json.dumps({'conversation_id': msg.conversation_id or '', 'rag_source': 'none', 'rag_quality': 'none', 'rag_chunks': 0})}\n\n"
                yield f"data: {json.dumps({'content': _instant_as_text})}\n\n"
                yield f"data: {json.dumps({'event': 'syrabit_done', 'conversation_id': msg.conversation_id or ''})}\n\n"
                yield "data: [DONE]\n\n"
            if not is_anon and credits_info:
                asyncio.create_task(_refund_credit(user_id, credits_info["used"] + 1))
            return StreamingResponse(_instant_as_stream(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    _instant_s = get_instant_response(msg.message) if _stream_intent == "casual" else None
    if _instant_s:
        if _want_translate and _instant_s:
            # Qwen-fallback + Sarvam-polish translation (user-mandated routing).
            # Polish is applied here because the instant fast-path is a single
            # substantive translation, not a fragment splice — the polish
            # quality lift is worth the extra ~1-1.5s round-trip.
            _translated_full = await _assamese_translate_gemini_main_sarvam_polish(
                _instant_s, target_lang_code=_sarvam_target,
            )
            if _translated_full:
                _instant_s = _translated_full
            # else: keep the original English instant response — better than
            # blocking the fast-path on a translator outage.
        logger.info(f"[STREAM] INSTANT casual fast-path: '{msg.message[:30]}' → {len(_instant_s)} chars (0 LLM calls)")
        _speedup.record_instant_fastpath()
        _instant_ms = (_time_mod.time() - _stream_t0) * 1000
        _speedup.record_ttfb(_instant_ms)
        _speedup.record_total_latency(_instant_ms)
        try:
            _speedup.record_lang_ttfb(_resp_lang, _instant_ms, _instant_ms)
        except Exception:
            pass
        try:
            record_first_token(_instant_ms, source="instant")
            record_chat_attrs(**{
                "syrabit.chat.path": "instant",
                "syrabit.chat.total_ms": _instant_ms,
                "syrabit.chat.retrieval_ms": 0.0,
            })
            # Task #610 — instant fast-path has no retrieval/LLM, just
            # emit a single post_processing-equivalent span so the
            # request still shows a child for consistency in dashboards.
            _t_now_inst = _time_mod.time()
            emit_phase_span(
                "chat.post_processing",
                _stream_t0,
                _t_now_inst,
                **{"syrabit.chat.path": "instant"},
            )
        except Exception:
            pass
        async def _instant_stream():
            nonlocal _instant_s
            yield f"data: {json.dumps({'conversation_id': msg.conversation_id or '', 'rag_source': 'none', 'rag_quality': 'none', 'rag_chunks': 0})}\n\n"
            yield f"data: {json.dumps({'content': _instant_s})}\n\n"
            yield f"data: {json.dumps({'event': 'syrabit_done', 'conversation_id': msg.conversation_id or ''})}\n\n"
            yield "data: [DONE]\n\n"
        if not is_anon and credits_info:
            asyncio.create_task(_refund_credit(user_id, credits_info["used"] + 1))
        return StreamingResponse(_instant_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    _s_should_pipeline, _s_stage1, _s_apply_s1, _s_enhance_q = should_use_pipeline, stage1_resolve_topic, apply_stage1_to_intent, build_enhanced_query

    _cache_msg_key_early = f"{msg.message}::lang={_resp_lang}" if _want_translate else msg.message
    _early_is_casual = _stream_intent in ("casual", "general")
    # Early-cache lookup runs BEFORE RAG resolution, so the cache key cannot
    # include a retrieval fingerprint. To preserve the requirement that
    # "changing retrieval context produces a fresh answer" (Task #609), we
    # only consult the early cache for intents that are intrinsically
    # retrieval-independent (casual / small-talk). Anything that may use
    # RAG falls through to the post-RAG, fingerprinted cache below.
    _early_model = msg.model or "stream-default"
    _cache_key_early = build_ai_cache_key(
        model=_early_model,
        prompt=_cache_msg_key_early,
        retrieval=None,
        language=_resp_lang or "",
        scope=f"early-casual|{msg.subject_id or ''}|{msg.board_id or ''}",
    )
    _early_cached_answer = None
    if _early_is_casual:
        _early_cached_answer = await ai_cache_aget(_cache_key_early)
        if not _early_cached_answer and _cache_key_early in _ai_response_cache:
            _early_cached_answer = _ai_response_cache[_cache_key_early]
        if not _early_cached_answer:
            _legacy_early = _cache_key(
                _cache_msg_key_early,
                subject_id=msg.subject_id or "",
                board_id=msg.board_id or "",
                conversation_id=getattr(msg, "conversation_id", "") or "",
            )
            if _legacy_early in _ai_response_cache:
                _early_cached_answer = _ai_response_cache[_legacy_early]
        if _early_cached_answer:
            ai_cache_record_hit_saved_latency(ai_cache_expected_saved_ms())
    if _early_cached_answer:
        logger.info(f"[STREAM] EARLY cache HIT — skipping all preprocessing (key={_cache_key_early})")
        _speedup.record_early_cache_hit()
        _conv_id_early = msg.conversation_id
        if not _conv_id_early and (user_id or anon_id):
            _conv_id_early = str(uuid.uuid4())
            _title_early = msg.message[:50] + ("..." if len(msg.message) > 50 else "")
            _now_early = datetime.now(timezone.utc).isoformat()
            _conv_doc_early = {
                "id": _conv_id_early,
                "user_id": user_id or anon_id,
                "title": _title_early,
                "subject_id": msg.subject_id or "",
                "subject_name": msg.subject_name or "",
                "messages": [],
                "created_at": _now_early,
                "updated_at": _now_early,
            }
            if is_anon and anon_id:
                _conv_doc_early["is_anonymous"] = True
                _conv_doc_early["anon_id"] = anon_id
                from cache import redis_save_anon_conversation
                redis_save_anon_conversation(anon_id, _conv_id_early, _conv_doc_early)
            asyncio.create_task(supa_upsert_conversation(_conv_doc_early))

        async def _early_cache_persist():
            try:
                _answer_e = _early_cached_answer
                _now_e = datetime.now(timezone.utc).isoformat()
                _uid_e = user_id or anon_id
                if _conv_id_early and _uid_e:
                    await _persist_chat_turn(
                        _conv_id_early, _uid_e,
                        msg.message, _answer_e,
                        "cache", 0, credits_info["used"] if credits_info else 0,
                    )
            except Exception as _pe:
                logger.warning(f"[STREAM] Early cache persist failed (non-fatal): {_pe}")

        async def _early_cache_stream():
            yield f"data: {json.dumps({'conversation_id': _conv_id_early or '', 'rag_source': 'cache', 'rag_quality': 'none', 'rag_chunks': 0, 'web_search_used': False, 'ctx_board_name': msg.board_name or '', 'ctx_class_name': msg.class_name or ''})}\n\n"
            _ttfb_early_ms = (_time_mod.time() - _stream_t0) * 1000
            logger.info(f"[STREAM][TIMING] TTFT (early cache): {_ttfb_early_ms / 1000:.3f}s")
            try:
                _speedup.record_ttfb(_ttfb_early_ms)
            except Exception:
                pass
            try:
                record_first_token(_ttfb_early_ms, source="early-cache")
                record_chat_attrs(**{
                    "syrabit.chat.path": "early-cache",
                    "syrabit.chat.retrieval_ms": _ttfb_early_ms,
                })
                # Task #610 — early-cache short path: retrieval = the
                # whole window from request start through cache hit.
                emit_phase_span(
                    "chat.retrieval",
                    _stream_t0,
                    _time_mod.time(),
                    **{
                        "syrabit.chat.path": "early-cache",
                        "syrabit.chat.cached": True,
                    },
                )
            except Exception:
                pass
            # Sanitise cached Assamese replies before emission so legacy
            # leaky cache entries (written before this filter existed) do
            # not reach the user. Uses the same async pipeline as the
            # streaming path so `translate` mode replaces leaked English
            # runs with Assamese instead of just deleting them.
            _early_emit = _early_cached_answer
            if _resp_lang == "as":
                from lang_sanitizer import (
                    sanitize_assamese_with_optional_regenerate as _sanitize_asm_early,
                )
                # Always run the sanitiser (even when behaviour="off" it
                # short-circuits to measure-only) so we ALWAYS get the
                # per-reply diagnostic log line and the live leakage
                # ratio for monitoring (Task #419).
                _cleaned_early, _early_diag = await _sanitize_asm_early(
                    _early_emit,
                    translate_callable=_make_assamese_translate_callable("as-IN"),
                    trace={
                        "conversation_id": _conv_id_early or msg.conversation_id or None,
                        "user_id": str(user_id) if user_id else None,
                    },
                )
                _early_action = _early_diag.get("action") or "noop"
                _early_log = (
                    f"[INDIC-SANITIZE][EARLY-CACHE] action={_early_action} "
                    f"ratio={_early_diag.get('ratio', 0):.3f} "
                    f"threshold={_early_diag.get('threshold', 0):.3f} "
                    f"behaviour={_early_diag.get('behaviour')} "
                    f"translated={_early_diag.get('translated', False)} "
                    f"regenerated={_early_diag.get('regenerated', False)} "
                    f"sample_tokens={_early_diag.get('suspicious_tokens', [])[:6]}"
                )
                if _early_action == "noop":
                    logger.info(_early_log)
                else:
                    logger.warning(_early_log)
                if _early_action != "noop":
                    _early_emit = _cleaned_early
                    # Refresh cache so subsequent hits are clean.
                    try:
                        _redis_set("ai_cache", _cache_key_early, _early_emit, REDIS_AI_CACHE_TTL)
                        if not redis_client:
                            _ai_response_cache[_cache_key_early] = _early_emit
                    except Exception:
                        pass
            _CHUNK_SIZE = 300
            for _ci in range(0, len(_early_emit), _CHUNK_SIZE):
                yield f"data: {json.dumps({'content': _early_emit[_ci:_ci + _CHUNK_SIZE]})}\n\n"
                if _ci % (_CHUNK_SIZE * 5) == 0:
                    await asyncio.sleep(0)
            _answer_words = len(_early_emit.split())
            yield f"data: {json.dumps({'event': 'syrabit_done', 'conversation_id': _conv_id_early or '', 'rag_source': 'cache', 'words': _answer_words, 'web_search_used': False})}\n\n"
            yield "data: [DONE]\n\n"
            asyncio.create_task(_early_cache_persist())
            try:
                _final_ms_e = (_time_mod.time() - _stream_t0) * 1000
                _record_chat_latency(_final_ms_e)
                _speedup.record_total_latency(_final_ms_e)
                record_chat_attrs(**{"syrabit.chat.total_ms": _final_ms_e})
            except Exception:
                pass
        if not is_anon and credits_info:
            asyncio.create_task(_refund_credit(user_id, credits_info["used"] + 1))
        return StreamingResponse(_early_cache_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ── Phase 0+1 fully parallel: context, semester, doc, search scope, Stage 1, follow-up all at once ──
    _t_phase0 = _time_mod.time()

    _is_card_context = bool(msg.card_context and msg.card_context.strip())

    async def _fetch_doc():
        if _is_card_context:
            logger.info(f"Chat [STREAM]: card_context ({len(msg.card_context)} chars) used as grounding (library source)")
            return msg.card_context
        if not msg.document_id:
            return None
        subj = await db.subjects.find_one({"id": msg.document_id}, {"_id": 0, "document_text": 1})
        return (subj or {}).get("document_text")

    _is_casual = _stream_intent in ("casual", "general")

    _stage1_task = None
    if _s_should_pipeline(_stream_intent, msg.message):
        _s1_timeout = 0.2
        async def _stage1_wrapper():
            try:
                result = await asyncio.wait_for(_s_stage1(msg.message), timeout=_s1_timeout)
                return result if result else {}
            except asyncio.TimeoutError:
                logger.info(f"[STREAM] Stage 1 timeout ({_s1_timeout}s) — proceeding without")
                return {}
        _stage1_task = asyncio.create_task(_stage1_wrapper())

    # WAI chapter classification — runs in parallel with Stage 1 + Phase 0.
    # Uses Workers AI @cf/baai/bge-small-en-v1.5 to embed the query and find
    # the closest matching chapter in the subject's vector index.
    # Falls back gracefully (returns None) on cold-start or error.
    _wai_chapter_task = None
    _wai_subject_id_for_classify = msg.subject_id
    if _wai_subject_id_for_classify and not _is_casual:
        async def _wai_classify_wrapper():
            try:
                return await _wai_idx.classify(
                    msg.message,
                    _wai_subject_id_for_classify,
                    timeout_s=3.5,
                )
            except Exception as _wai_err:
                logger.debug("[WAI] classify error (non-fatal): %s", _wai_err)
                return None
        _wai_chapter_task = asyncio.create_task(_wai_classify_wrapper())

    async def _fetch_followup_info():
        if is_anon or _is_casual:
            return None
        if not (msg.conversation_id and user_id):
            return None
        try:
            _conv_for_fu_s = await supa_get_conversation(msg.conversation_id, user_id)
            if _conv_for_fu_s:
                _conv_meta_s = _safe_metadata(_conv_for_fu_s.get("metadata"))
                return detect_followup(msg.message, _conv_meta_s)
        except Exception as _fu_err_s:
            logger.warning(f"[STREAM] Follow-up detection failed: {_fu_err_s}")
        return None

    async def _prefetch_history():
        try:
            if not msg.conversation_id:
                return None
            if user_id:
                return await supa_get_conversation(msg.conversation_id, user_id)
            if anon_id:
                from cache import redis_get_anon_conversation
                return redis_get_anon_conversation(anon_id, msg.conversation_id)
        except Exception as _hist_err:
            logger.warning(f"[STREAM] History prefetch failed (non-fatal): {_hist_err}")
        return None

    # ── Speculative parallel web-search (Task #282 T003 + T005) ──────────────
    # We kick this off in Phase 0 in PARALLEL with internal chapter fetch so
    # the wall-clock cost of a network round-trip overlaps the Mongo lookup.
    # After Phase 0 completes we apply an internal-first policy:
    #   • If internal chapters were found → discard web results (saves tokens
    #     and keeps answers grounded in Syrabit's curated content).
    #   • If internal returned nothing AND it's a content-seeking query →
    #     hand the web results to the prompt builder as a fallback so the
    #     model has *some* context instead of falling back to bare LLM
    #     general knowledge.
    # Web search only runs for content-seeking intents where the user
    # actually has a subject context to search within. Casual chats,
    # follow-ups on uploaded documents, and unscoped queries all skip it
    # because the cost wouldn't pay back.
    _stream_skip_web = (
        _is_casual
        or _is_card_context
        or bool(msg.document_id)
        or not (msg.subject_id or msg.subject_name)
    )

    async def _early_web_search():
        if _stream_skip_web:
            return []
        try:
            return await asyncio.wait_for(
                web_search_with_fallback(
                    msg.message,
                    board_name=msg.board_name or "",
                    class_name=msg.class_name or "",
                    subject_name=msg.subject_name or "",
                    chapter_name=msg.chapter_name or "",
                    enrich_top_n=2,
                ),
                # Hard cap so Phase-0 budget (_PRE_LLM_BUDGET) can still
                # cancel us cleanly. The outer asyncio.wait already enforces
                # this, but a per-call timeout protects the speculative
                # fetch from a stuck DDG socket.
                timeout=1.5,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return []
        except Exception as _ws_err:
            logger.warning(f"[STREAM] Speculative web search failed (non-fatal): {_ws_err}")
            return []

    _skip_semester = _is_casual or is_anon

    _PRE_LLM_BUDGET = 0.15

    # F2: Warm-cache helpers — /ai/warm-query endpoint pre-populates Redis
    # (20s TTL) via the frontend 800ms debounce. Phase 0 only does a fast Redis
    # check (T006) so the 150ms budget always completes. Phase 2 / resolve_rag_context
    # handles the full _fetch_internal_chapters call if Redis misses.

    async def _check_warm_redis() -> list:
        """Redis-only check used in Phase 0 (150ms budget).
        Uses run_in_executor so the sync Upstash REST call doesn't block
        the event loop — critical when the Phase-0 asyncio.wait fires."""
        import hashlib as _hl, json as _json
        _sid = msg.subject_id or ""
        _wkey = f"warm_ch:{_hl.md5(f'{msg.message.strip()}|{_sid}'.encode()).hexdigest()}"
        if redis_client:
            try:
                _loop = asyncio.get_event_loop()
                _wdata = await _loop.run_in_executor(None, redis_client.get, _wkey)
                if _wdata:
                    _parsed = _json.loads(_wdata)
                    if _parsed:
                        logger.debug("[F2-WARM] Phase-0 Redis hit — %d chapters cached", len(_parsed))
                        return _parsed
            except Exception:
                pass
        return []  # cache miss → resolve_rag_context will call _fetch_internal_chapters

    async def _fetch_chapters_warm_first():
        """Redis check + _fetch_internal_chapters fallback. Kept for Phase 2 if needed."""
        import hashlib as _hl, json as _json
        _sid = msg.subject_id or ""
        _wkey = f"warm_ch:{_hl.md5(f'{msg.message.strip()}|{_sid}'.encode()).hexdigest()}"
        if redis_client:
            try:
                _loop = asyncio.get_event_loop()
                _wdata = await _loop.run_in_executor(None, redis_client.get, _wkey)
                if _wdata:
                    _parsed = _json.loads(_wdata)
                    if _parsed:
                        logger.debug("[F2-WARM] Redis hit — skipping _fetch_internal_chapters")
                        return _parsed
            except Exception:
                pass
        return await _fetch_internal_chapters(msg.message, subject_id=msg.subject_id, subject_name=msg.subject_name)

    # Speculative web search runs OUTSIDE the Phase-0 budget so the 150ms cap
    # doesn't kill it before HTTP can return (Task #282 T003/T005). It still
    # has its own internal 1.5s wait_for inside _early_web_search, so it
    # cannot block the request indefinitely. We await it later, only if RAG
    # actually misses, so the common (internal-hit) path doesn't pay for it.
    _early_web_task: Optional[asyncio.Task] = None

    if _is_casual and not msg.subject_id and not msg.document_id and not msg.conversation_id and not _is_card_context:
        _tasks_pre = []
        _defaults_pre = []
        _pre_results = []
        _subj_ctx_result, _sem_class_result, document_text, _stream_followup_info, _prefetched_conv, _s_prefetched_chapters = None, None, None, None, None, []
        _done_pre, _pending_pre = set(), set()
        logger.info("[STREAM] Casual fast-path: skipping Phase 0 entirely")
    else:
        _early_web_task = asyncio.create_task(_early_web_search())
        _tasks_pre = [
            asyncio.create_task(_resolve_subject_context(msg.subject_id)),
            asyncio.create_task(_resolve_semester_class_id(msg.message, msg.board_id) if (msg.board_id and not _skip_semester) else asyncio.sleep(0)),
            asyncio.create_task(_fetch_doc()),
            asyncio.create_task(_fetch_followup_info()),
            asyncio.create_task(_prefetch_history()),
            asyncio.create_task(_check_warm_redis() if (msg.subject_id or msg.subject_name) else asyncio.sleep(0)),
        ]
        _defaults_pre = [None, None, None, None, None, []]

        _done_pre, _pending_pre = await asyncio.wait(_tasks_pre, timeout=_PRE_LLM_BUDGET, return_when=asyncio.ALL_COMPLETED)
    if _pending_pre:
        logger.info(f"[STREAM] Pre-LLM budget expired — {len(_pending_pre)} tasks still pending, using partial results")
        for _pt in _pending_pre:
            _pt.cancel()

    _pre_results = []
    for _i, _t in enumerate(_tasks_pre):
        if _t.done() and not _t.cancelled():
            try:
                _pre_results.append(_t.result())
            except Exception:
                _pre_results.append(_defaults_pre[_i])
        else:
            _pre_results.append(_defaults_pre[_i])

    if _pre_results:
        _subj_ctx_result, _sem_class_result, document_text, _stream_followup_info, _prefetched_conv, _s_prefetched_chapters = _pre_results

    _s_topic_meta = None
    if _stage1_task:
        if _stage1_task.done():
            try:
                _s_topic_meta = _stage1_task.result()
            except Exception:
                _s_topic_meta = None
        else:
            logger.info("[STREAM] Stage 1 still pending after gather — proceeding without it (non-blocking)")
            _s_topic_meta = None

    _s_original_message = msg.message

    _is_followup_s = False
    if _stream_followup_info:
        _is_followup_s = True
        _stream_intent = _stream_followup_info["prev_intent"]
        _stream_db_category = {"notes": "notes", "important_questions": "important_questions", "pyq": "question_paper"}.get(_stream_intent)
        msg.message = merge_followup_into_query(
            msg.message, _stream_followup_info,
            subject_name=msg.subject_name or "",
            chapter_name=msg.chapter_name or "",
        )
        logger.info(f"[STREAM] Follow-up detected: intent={_stream_intent}, rewritten query='{msg.message[:60]}'")

    if not _is_followup_s and _s_topic_meta and _s_topic_meta.get("intent"):
        _stream_intent, _stream_db_category = _s_apply_s1(
            _s_topic_meta, _stream_intent, _stream_db_category
        )
        logger.info(f"[PIPELINE][S1][STREAM] Intent resolved: {_stream_intent} (Stage 1 primary)")

    _q_lower_s = msg.message.lower()
    if _stream_intent == "syllabus" and _s_topic_meta and _s_topic_meta.get("search_keywords") and "syllabus" not in _q_lower_s and "curriculum" not in _q_lower_s and "subject list" not in _q_lower_s:
        _stream_intent = "notes"
        _stream_db_category = "notes"
        logger.info(f"[STREAM] Intent upgrade: syllabus → notes (Stage 1 has search_keywords, query is content-seeking)")

    _is_casual = _stream_intent in ("casual", "general")

    subj_ctx = _subj_ctx_result or {}
    ctx_board_id   = subj_ctx.get("board_id")   or msg.board_id
    ctx_class_id   = subj_ctx.get("class_id")   or msg.class_id
    ctx_stream_id  = subj_ctx.get("stream_id")  or getattr(msg, 'stream_id', None)
    ctx_board_name = subj_ctx.get("board_name") or msg.board_name or (user.get("board_name", "") if user else "")
    ctx_class_name = subj_ctx.get("class_name") or msg.class_name or (user.get("class_name", "") if user else "")
    ctx_stream_name= subj_ctx.get("stream_name") or getattr(msg, 'stream_name', None) or (user.get("stream_name", "") if user else "")

    if not _is_casual and not _sem_class_result and ctx_board_id and ctx_board_id != msg.board_id:
        try:
            _sem_class_result = await asyncio.wait_for(
                _resolve_semester_class_id(msg.message, ctx_board_id), timeout=0.1
            )
        except (asyncio.TimeoutError, Exception):
            _sem_class_result = None

    _syl_class_id_s = _sem_class_result or ctx_class_id

    if subj_ctx:
        logger.info(f"Chat [STREAM]: Subject context resolved → {ctx_board_name} / {ctx_class_name} / {ctx_stream_name}")
    if _sem_class_result:
        logger.info(f"Chat [STREAM]: Semester override class_id={_sem_class_result} (from query)")

    async def _fetch_syllabus_async():
        if not (ctx_board_id and _syl_class_id_s):
            return None
        _sck = _syllabus_cache_key(ctx_board_id, _syl_class_id_s, ctx_stream_id, msg.subject_id)
        if _sck in _syllabus_cache:
            return _syllabus_cache[_sck]
        try:
            queries = []
            if ctx_stream_id and msg.subject_id:
                queries.append(db.syllabi.find_one({"board_id": ctx_board_id, "class_id": _syl_class_id_s, "stream_id": ctx_stream_id, "subject_id": msg.subject_id}, {"_id": 0}))
            if ctx_stream_id:
                queries.append(db.syllabi.find_one({"board_id": ctx_board_id, "class_id": _syl_class_id_s, "stream_id": ctx_stream_id}, {"_id": 0}))
            queries.append(db.syllabi.find_one({"board_id": ctx_board_id, "class_id": _syl_class_id_s, "stream_id": {"$exists": False}}, {"_id": 0}))
            queries.append(db.syllabi.find_one({"board_id": ctx_board_id, "class_id": _syl_class_id_s}, {"_id": 0}))
            results = await asyncio.gather(*queries, return_exceptions=True)
            for r in results:
                if r and not isinstance(r, Exception):
                    _syllabus_cache[_sck] = r
                    return r
        except Exception:
            pass
        return None
    _syllabus_task = asyncio.create_task(_fetch_syllabus_async())

    _t_phase1_done = _time_mod.time()
    logger.info(f"[STREAM][TIMING] Phase 0+1 (context+doc+scope): {_t_phase1_done - _t_phase0:.3f}s")

    # ── Phase 2: context assembly (no web search) ──
    _t_phase2 = _time_mod.time()

    _s_rag_query = msg.message
    if _s_topic_meta and _s_topic_meta.get("search_keywords"):
        _s_rag_query = _s_enhance_q(msg.message, _s_topic_meta)
        if _s_rag_query != msg.message:
            logger.info(f"[PIPELINE][S1][STREAM] Enhanced search query: '{_s_rag_query[:80]}'")

    _rag_quality = "none"

    rag_ctx = {"chunks": [], "chapters": [], "chunk_chapters": [], "subjects": [],
               "vector_hits": [], "source": "none", "quality": "none",
               "_general_knowledge_fallback": True}
    _s1_subject_str = (_s_topic_meta.get("subject", "") if _s_topic_meta else "").strip()
    if _s1_subject_str:
        rag_ctx["_stage1_subject"] = _s1_subject_str
    raw_conv = _prefetched_conv

    _s_use_prefetched = _s_prefetched_chapters if (_s_prefetched_chapters and _s_rag_query == _s_original_message) else None
    _s_rag_content_budget = 8000 if _stream_intent in ("notes", "important_questions", "pyq") else 4000
    rag_ctx = await resolve_rag_context(
        _s_rag_query, subject_id=msg.subject_id, subject_name=msg.subject_name,
        document_text=document_text, intent=_stream_intent,
        prefetched_chapters=_s_use_prefetched,
        max_content_chars=_s_rag_content_budget,
    )
    if _s1_subject_str:
        rag_ctx["_stage1_subject"] = _s1_subject_str

    # ── Internal-first web fallback (Task #282 T003 + T005) ──────────────────
    # Phase 0 already kicked off web search in parallel with the internal
    # chapter fetch. Now that we know whether RAG found anything, pick:
    #   • internal hit  → discard the speculative web results entirely.
    #   • internal miss → use them so the LLM has *some* grounding.
    _has_internal_stream = (
        rag_ctx.get("_has_internal_content")
        or rag_ctx.get("source") in ("document", "internal")
    )
    if _has_internal_stream or _stream_skip_web or _early_web_task is None:
        # Internal RAG hit — discard the speculative search and cancel the
        # task so we don't waste an outbound HTTP round-trip.
        web_results = []
        if _early_web_task is not None and not _early_web_task.done():
            _early_web_task.cancel()
        # Only count this as a "discarded" speculative fetch when the
        # search was actually eligible to run. When _stream_skip_web is
        # true, _early_web_search() short-circuits to [] without making
        # any network call, so counting it would distort the
        # used/discarded ratio.
        if _early_web_task is not None and not _stream_skip_web:
            try:
                _speedup.record_speculative_web(used=False, discarded=True)
            except Exception:
                pass
    else:
        # Internal RAG missed → wait briefly for the speculative web fetch
        # we kicked off in Phase 0. It runs OUTSIDE the 150ms pre-LLM budget
        # so it has had ~RAG-resolve time to produce results already; we add
        # a small extra grace window to let it finish if it's almost done.
        try:
            _early_web = await asyncio.wait_for(_early_web_task, timeout=1.2)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _early_web = []
            if not _early_web_task.done():
                _early_web_task.cancel()
        except Exception as _ew_err:
            logger.warning(f"[STREAM] Awaiting speculative web search failed: {_ew_err}")
            _early_web = []
        web_results = _early_web or []
        if web_results:
            logger.info(
                f"[STREAM] Using {len(web_results)} web result(s) as fallback "
                f"(internal RAG returned source={rag_ctx.get('source','none')})"
            )
        try:
            _speedup.record_speculative_web(used=bool(web_results), discarded=False)
        except Exception:
            pass

    if _syllabus_task.done():
        try:
            syllabus = _syllabus_task.result()
        except Exception:
            syllabus = None
    else:
        try:
            syllabus = await asyncio.wait_for(_syllabus_task, timeout=0.05)
        except (asyncio.TimeoutError, Exception):
            syllabus = None

    _t_phase2_done = _time_mod.time()
    logger.info(f"[STREAM][TIMING] Phase 2 (context): {_t_phase2_done - _t_phase2:.3f}s | total pre-LLM: {_t_phase2_done - _stream_t0:.3f}s")

    # ── WAI chapter classification result (T002) ──────────────────────────────
    # Resolved inside event_stream() so StreamingResponse is returned immediately
    # and discovery:searching reaches the client ~0ms after submit.
    # The outer handler no longer awaits the WAI task — event_stream handles it
    # after yielding the initial discovery:searching/class/subject SSE events.
    _wai_match: Optional[dict] = None

    # ── Build prompt ───────────────────────────────────────────────────────────
    system_prompt = build_rag_system_prompt(
        {
            "board_name":  ctx_board_name,
            "class_name":  ctx_class_name,
            "stream_name": ctx_stream_name,
            "subject_name": msg.subject_name,
            "subject_id":  msg.subject_id,
            "chapter_name": msg.chapter_name,
        },
        rag_ctx,
        user_info={
            "name":        (user.get("name", "") if user else ""),
            "board_name":  ctx_board_name or (user.get("board_name", "") if user else ""),
            "class_name":  ctx_class_name or (user.get("class_name", "") if user else ""),
            "stream_name": ctx_stream_name or (user.get("stream_name", "") if user else ""),
            "plan":        (user.get("plan", "free") if user else "free"),
        },
        query=msg.message,
        syllabus=syllabus,
        web_results=web_results or None,
        resolved_intent=_stream_intent,
        response_lang=_resp_lang,
    )

    conv_id = msg.conversation_id
    history_messages = []

    if conv_id and raw_conv:
        raw_history = [
            {"role": m.get("role", ""), "content": m.get("content") or ""}
            for m in raw_conv.get("messages", [])
            if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
        ]
        history_messages = _trim_history(raw_history)
    elif not conv_id and user_id:
        conv_id = str(uuid.uuid4())
        title = msg.message[:50] + ("..." if len(msg.message) > 50 else "")
        conv_doc = {
            "id": conv_id,
            "user_id": user_id,
            "title": title,
            "subject_id": msg.subject_id,
            "subject_name": msg.subject_name,
            "messages": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        asyncio.create_task(supa_upsert_conversation(conv_doc))
    elif not conv_id and anon_id:
        conv_id = str(uuid.uuid4())
        title = msg.message[:50] + ("..." if len(msg.message) > 50 else "")
        _now_ts = datetime.now(timezone.utc).isoformat()
        _anon_conv_doc = {
            "id": conv_id,
            "anon_id": anon_id,
            "user_id": anon_id,
            "title": title,
            "subject_id": msg.subject_id or "",
            "subject_name": msg.subject_name or "",
            "messages": [],
            "is_anonymous": True,
            "created_at": _now_ts,
            "updated_at": _now_ts,
        }
        from cache import redis_save_anon_conversation
        redis_save_anon_conversation(anon_id, conv_id, _anon_conv_doc)
        asyncio.create_task(supa_upsert_conversation(_anon_conv_doc))
    elif not conv_id:
        conv_id = None

    _MAX_PROMPT_CHARS = 12_000
    _sys_len = len(system_prompt)
    _hist_len = sum(len(m.get("content", "")) for m in history_messages)
    _user_len = len(msg.message)
    _total_chars = _sys_len + _hist_len + _user_len
    logger.info(f"[STREAM] Payload chars: system={_sys_len}, history={_hist_len} ({len(history_messages)} msgs), user={_user_len}, total={_total_chars}")
    if _total_chars > _MAX_PROMPT_CHARS:
        _target_sys = max(2000, _MAX_PROMPT_CHARS - _hist_len - _user_len)
        if _sys_len > _target_sys:
            system_prompt = system_prompt[:_target_sys]
            logger.warning(f"[STREAM] System prompt truncated: {_sys_len} → {_target_sys} chars (total was {_total_chars})")
        _total_chars = len(system_prompt) + _hist_len + _user_len
        if _total_chars > _MAX_PROMPT_CHARS and history_messages:
            history_messages = history_messages[-4:]
            _hist_len = sum(len(m.get("content", "")) for m in history_messages)
            logger.warning(f"[STREAM] History trimmed to last 4 messages ({_hist_len} chars)")

    _LANG_NAME_MAP = {"as": "Assamese"}
    _target_lang_name = _LANG_NAME_MAP.get(_resp_lang)
    if _want_translate and _target_lang_name:
        from prompts import assamese_enforcement_block as _asm_block
        _indic_system_prompt = (
            "/think অসমীয়াত চমুকৈ চিন্তা কৰক — তাৰ পিছত সম্পূৰ্ণ উত্তৰ অসমীয়াত দিয়ক।\n"
            "তুমি Syra, এগৰাকী AI শিক্ষক। উত্তৰ দিয়াৰ আগতে অসমীয়াত চিন্তা কৰা। "
            "কেৱল অসমীয়াত উত্তৰ দিয়া। "
            "কাৰিকৰী শব্দ/সূত্ৰ ইংৰাজীত ৰাখিব পাৰা। চমুকৈ লিখা: ৩০-৬০ শব্দ, সৰ্বাধিক ২০০।\n"
            + _asm_block()
        )
        if system_prompt:
            _ctx_markers = ["**GROUNDING CONTEXT", "**CURRICULUM", "**SUBJECT CHAPTERS", "REFERENCE MATERIAL:", "CONTEXT:", "RELEVANT CONTENT:"]
            _ctx_section = ""
            for _marker in _ctx_markers:
                _idx = system_prompt.find(_marker)
                if _idx != -1:
                    _ctx_section = system_prompt[_idx:]
                    break
            if _ctx_section:
                _indic_system_prompt += f"\n{_ctx_section}"
            else:
                _content_lines = [l for l in system_prompt.split('\n') if l.strip() and not l.strip().startswith(('You are', 'IMPORTANT:', 'RULES:', 'Format'))]
                if _content_lines:
                    _indic_system_prompt += "\nতথ্য:\n" + "\n".join(_content_lines[-15:])

        system_prompt = _indic_system_prompt
        logger.info(f"[STREAM] Indic-first prompt built for {_target_lang_name} ({len(system_prompt)} chars)")

    messages_payload = [{"role": "system", "content": system_prompt}] + history_messages + [{"role": "user", "content": msg.message}]

    user_msg_saved   = msg.message
    rag_source_saved = rag_ctx.get("source",  "none")
    if _is_card_context and rag_source_saved == "document":
        rag_source_saved = "library"
        rag_ctx["source"] = "library"
    rag_quality_saved = rag_ctx.get("quality", "none")
    rag_chunks_count = len(rag_ctx.get("chunks",   []))
    rag_subjects_count = len(rag_ctx.get("subjects", []))
    web_search_used  = bool(web_results)
    content_card_meta = rag_ctx.get("content_card_meta") or None
    # Resolve the primary subject this answer came from (for frontend badge link)
    _rag_subjs = rag_ctx.get("subjects", [])
    _rag_has_real_subject = bool(_rag_subjs and _rag_subjs[0].get("id"))
    rag_subject_id   = (_rag_subjs[0].get("id")   if _rag_has_real_subject else None) or msg.subject_id   or None
    rag_subject_name = (_rag_subjs[0].get("name") if _rag_has_real_subject else None) or msg.subject_name or None
    rag_subject_icon = (_rag_subjs[0].get("icon") if _rag_has_real_subject else None) or None
    rag_subject_gradient = (_rag_subjs[0].get("gradient") if _rag_has_real_subject else None) or None
    _rag_chaps       = rag_ctx.get("chunk_chapters") or rag_ctx.get("chapters", [])
    rag_chapter_name = (_rag_chaps[0].get("title", "") if _rag_chaps else None) or msg.chapter_name or None
    rag_chapter_slug = (_rag_chaps[0].get("slug", "") if _rag_chaps else None) or None
    full_response = []

    _rag_raw_chunks = rag_ctx.get("chunks", [])
    rag_chunk_snippet = ""
    _chunk_topic_name = ""
    if _rag_raw_chunks:
        _first = _rag_raw_chunks[0]
        _chunk_topic_name = (_first.get("topic_name") or "").strip()
        _snippet = (_first.get("content") or "").strip()
        _snippet = re.sub(r'#{1,6}\s+', '', _snippet)
        _snippet = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', _snippet)
        _snippet = re.sub(r'_{1,3}([^_]+)_{1,3}', r'\1', _snippet)
        _snippet = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', _snippet)
        _snippet = re.sub(r'`([^`]+)`', r'\1', _snippet)
        _snippet = re.sub(r'^\s*[-*+]\s+', '', _snippet, flags=re.MULTILINE)
        _snippet = re.sub(r'^\s*\d+\.\s+', '', _snippet, flags=re.MULTILINE)
        _snippet = re.sub(r'\s+', ' ', _snippet).strip()
        if len(_snippet) > 250:
            _snippet = _snippet[:250]
        rag_chunk_snippet = _snippet

    _router_subject = None
    _router_chapter = None
    _router_board   = None
    _router_subject_id = None
    if _router_chapter and not rag_chapter_name:
        rag_chapter_name = _router_chapter
    if _router_subject and not rag_subject_name:
        rag_subject_name = _router_subject
    if _router_subject_id and not rag_subject_id:
        rag_subject_id = _router_subject_id

    _syl_topic_name = None
    _syl_level      = "chapter"
    if _syl_topic_name and _syl_level == "topic":
        rag_topic_name = _syl_topic_name
    elif _chunk_topic_name:
        rag_topic_name = _chunk_topic_name
    elif rag_chapter_name and msg.message and msg.message.strip():
        rag_topic_name = msg.message.strip()
    else:
        rag_topic_name = None

    async def _resolve_chapter_slug_bg():
        nonlocal rag_chapter_slug
        if rag_chapter_name and not rag_chapter_slug:
            try:
                _slug_sid = rag_subject_id or msg.subject_id or None
                _slug_filter: dict = {"title": {"$regex": f"^{re.escape(rag_chapter_name)}$", "$options": "i"}}
                if _slug_sid:
                    _slug_filter["subject_id"] = _slug_sid
                _slug_ch = await db.chapters.find_one(_slug_filter, {"_id": 0, "slug": 1})
                if _slug_ch and _slug_ch.get("slug"):
                    rag_chapter_slug = _slug_ch["slug"]
            except Exception:
                pass

    async def _resolve_source_ctx_bg():
        _src_sid_s = rag_subject_id or msg.subject_id
        if _src_sid_s and _src_sid_s == msg.subject_id and subj_ctx:
            return subj_ctx
        return await _resolve_subject_context(_src_sid_s) if _src_sid_s else {}

    _slug_task = asyncio.create_task(_resolve_chapter_slug_bg())
    _src_ctx_task = asyncio.create_task(_resolve_source_ctx_bg())

    rag_sources = _sources_from_rag_ctx(rag_ctx)
    if web_results:
        rag_sources.extend(_sources_from_web_results(web_results))

    _cache_is_casual = _stream_intent in ("casual", "general")
    _cache_msg_key = f"{msg.message}::lang={_resp_lang}" if _want_translate else msg.message
    _stream_model_for_key = msg.model or "stream-default"
    _cache_key_val = build_ai_cache_key(
        model=_stream_model_for_key,
        prompt=_cache_msg_key,
        retrieval=rag_ctx,
        language=_resp_lang or "",
        scope=f"{ctx_board_id or ''}|" + ("" if _cache_is_casual else (conv_id or "")),
    )
    _cache_ttl_val = REDIS_CASUAL_CACHE_TTL if _cache_is_casual else REDIS_AI_CACHE_TTL
    _cached_answer = _ai_response_cache.get(_cache_key_val)
    if _cached_answer:
        ai_cache_record_hit_saved_latency(ai_cache_expected_saved_ms())
        logger.info(f"AI cache HIT (pre-SSE, L1): {_cache_key_val}")
    else:
        _cached_answer = await ai_cache_aget(_cache_key_val)
        if _cached_answer:
            ai_cache_record_hit_saved_latency(ai_cache_expected_saved_ms())
            logger.info(f"AI cache HIT (pre-SSE, managed): {_cache_key_val}")
    if not _cached_answer:
        _legacy_key = _cache_key(
            _cache_msg_key,
            subject_id=msg.subject_id or "",
            board_id=msg.board_id or "",
            conversation_id=conv_id or "",
        )
        _legacy_hit = _ai_response_cache.get(_legacy_key)
        if _legacy_hit:
            _cached_answer = _legacy_hit
            ai_cache_record_hit_saved_latency(ai_cache_expected_saved_ms())
            logger.info(f"AI cache HIT (pre-SSE, L1 legacy): {_legacy_key}")
    if _cached_answer:
        try:
            _speedup.record_pre_sse_cache_hit()
        except Exception:
            pass

    async def event_stream():
        nonlocal full_response, rag_chapter_name, rag_chapter_slug, _wai_match
        _credit_saved = False  # set True when answer is committed; controls refund in finally
        try:
            # ── Discovery SSE events ───────────────────────────────────────────
            # Emitted before _meta_event so the ThinkingIndicator can advance
            # each step as the backend reports what it found (step 0→3).
            # These arrive in the same TCP frame as _meta_event today but the
            # frontend handles them independently so future streaming
            # architecture improvements (emitting mid-resolve) will work
            # without any frontend changes.
            yield f"data: {json.dumps({'event': 'discovery:searching'})}\n\n"
            _disc_class = ctx_class_name or (ctx_stream_name if ctx_stream_name else None)
            if _disc_class:
                yield f"data: {json.dumps({'event': 'discovery:class', 'value': _disc_class})}\n\n"
            _disc_subject = rag_subject_name or (msg.subject_name if msg.subject_name else None)
            if _disc_subject:
                yield f"data: {json.dumps({'event': 'discovery:subject', 'value': _disc_subject})}\n\n"
            # ── WAI chapter classification (T002) — resolved here, not in outer handler ──
            # discovery:searching + class + subject are already in-flight to the client.
            # Await WAI with remaining grace (up to 1.0s from request start) so that
            # discovery:chapter and _meta_event include the matched chapter.
            if _wai_chapter_task is not None:
                if _wai_chapter_task.done():
                    try:
                        _wai_match = _wai_chapter_task.result()
                    except Exception:
                        _wai_match = None
                else:
                    _wai_elapsed_es = _time_mod.time() - _stream_t0
                    _wai_grace_es = max(0.0, 1.0 - _wai_elapsed_es)
                    if _wai_grace_es > 0.02:
                        try:
                            _wai_match = await asyncio.wait_for(_wai_chapter_task, timeout=_wai_grace_es)
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            _wai_match = None
                            logger.info("[WAI] classify not ready inside event_stream — skipping")
                        except Exception:
                            _wai_match = None
                    else:
                        _wai_chapter_task.cancel()
                if _wai_match:
                    logger.info(
                        "[WAI] chapter match: '%s' (sim=%.3f, confident=%s)",
                        _wai_match.get("chapter_title"), _wai_match.get("similarity"),
                        _wai_match.get("confident"),
                    )
                    if not rag_chapter_name and _wai_match.get("chapter_title"):
                        rag_chapter_name = _wai_match["chapter_title"]
                    if not rag_chapter_slug and _wai_match.get("slug"):
                        rag_chapter_slug = _wai_match["slug"]
            _disc_chapter = rag_chapter_name or (_wai_match.get('chapter_title') if _wai_match else None)
            if _disc_chapter:
                yield f"data: {json.dumps({'event': 'discovery:chapter', 'value': _disc_chapter})}\n\n"
            # ── Meta event ────────────────────────────────────────────────────
            _meta_event = {'conversation_id': conv_id, 'rag_source': rag_source_saved, 'rag_quality': rag_quality_saved, 'rag_chunks': rag_chunks_count, 'rag_subjects': rag_subjects_count, 'rag_subject_id': rag_subject_id, 'rag_subject_name': rag_subject_name, 'rag_subject_icon': rag_subject_icon or '', 'rag_subject_gradient': rag_subject_gradient or '', 'rag_chapter_name': rag_chapter_name, 'rag_chapter_slug': rag_chapter_slug or '', 'rag_topic_name': rag_topic_name or '', 'rag_chunk_snippet': rag_chunk_snippet, 'router_subject': _router_subject, 'router_chapter': _router_chapter, 'router_board': _router_board, 'web_search_used': web_search_used, 'ctx_board_name': ctx_board_name or '', 'ctx_class_name': ctx_class_name or '', 'ctx_stream_name': ctx_stream_name or ''}
            if content_card_meta:
                _meta_event['content_card_name'] = content_card_meta.get('card_name', '')
                _meta_event['content_card_lesson'] = content_card_meta.get('lesson_name', '')
                _meta_event['content_card_subject'] = content_card_meta.get('subject_name', '')
            # WAI chapter match — lets the frontend ThinkingIndicator show the
            # REAL matched chapter during the thinking animation (Step 4),
            # instead of cycling through all chapters randomly.
            if _wai_match:
                _meta_event['wai_chapter_match'] = {
                    'chapter_title':  _wai_match.get('chapter_title', ''),
                    'chapter_number': _wai_match.get('chapter_number', 0),
                    'slug':           _wai_match.get('slug', ''),
                    'similarity':     _wai_match.get('similarity', 0.0),
                    'confident':      _wai_match.get('confident', False),
                }
            yield f"data: {json.dumps(_meta_event)}\n\n"

            cached_answer = _cached_answer
            _stream_provider = "unknown"
            if cached_answer:
                _ttfb_cache_ms = (_time_mod.time() - _stream_t0) * 1000
                logger.info(f"[STREAM][TIMING] TTFT (cache hit): {_ttfb_cache_ms / 1000:.3f}s")
                try:
                    _speedup.record_ttfb(_ttfb_cache_ms)
                except Exception:
                    pass
                try:
                    record_first_token(_ttfb_cache_ms, source="cache")
                    # Cache-hit short-circuit: retrieval == cache-lookup
                    # window. Set retrieval_ms here so the attribute is
                    # present on all 4 SSE paths (instant / early-cache
                    # / cache / main).
                    record_chat_attrs(**{
                        "syrabit.chat.path": "cache",
                        "syrabit.chat.retrieval_ms": _ttfb_cache_ms,
                    })
                except Exception:
                    pass
                # Defensive sanitisation of cached Assamese answers — older
                # cache entries may have been written before the leakage
                # filter existed. Re-run the strip if the response was meant
                # for response_lang="as" and contains stray English.
                if _want_translate and _resp_lang == "as":
                    from lang_sanitizer import (
                        sanitize_assamese_with_optional_regenerate as _sanitize_asm_cache,
                    )
                    # Always run sanitiser (off mode short-circuits to
                    # measure-only) so the diagnostic line is always
                    # emitted (Task #419).
                    _cleaned_cache, _cache_diag = await _sanitize_asm_cache(
                        cached_answer,
                        translate_callable=_make_assamese_translate_callable("as-IN"),
                        trace={
                            "conversation_id": conv_id or msg.conversation_id or None,
                            "user_id": str(user_id) if user_id else None,
                        },
                    )
                    _cache_action = _cache_diag.get("action") or "noop"
                    _cache_log = (
                        f"[INDIC-SANITIZE][CACHE] action={_cache_action} "
                        f"ratio={_cache_diag.get('ratio', 0):.3f} "
                        f"threshold={_cache_diag.get('threshold', 0):.3f} "
                        f"behaviour={_cache_diag.get('behaviour')} "
                        f"translated={_cache_diag.get('translated', False)} "
                        f"regenerated={_cache_diag.get('regenerated', False)} "
                        f"sample_tokens={_cache_diag.get('suspicious_tokens', [])[:6]}"
                    )
                    if _cache_action == "noop":
                        logger.info(_cache_log)
                    else:
                        logger.warning(_cache_log)
                    if _cache_action != "noop":
                        cached_answer = _cleaned_cache
                        # Refresh cache entry so subsequent hits are clean.
                        _redis_set("ai_cache", _cache_key_val, cached_answer, _cache_ttl_val)
                        if not redis_client:
                            _ai_response_cache[_cache_key_val] = cached_answer
                _CHUNK_SIZE = 300
                for _ci in range(0, len(cached_answer), _CHUNK_SIZE):
                    yield f"data: {json.dumps({'content': cached_answer[_ci:_ci + _CHUNK_SIZE]})}\n\n"
                    if _ci % (_CHUNK_SIZE * 5) == 0:
                        await asyncio.sleep(0)
                full_response.append(cached_answer)
            else:
                _bp_count = 0
                _first_token_logged = False
                _output_buf = ""
                _output_violation = False
                _tune_buf = {"total": "", "chars": 0}

                # Resolve default chat model (Task #607): admin override in
                # db.api_config.chat_model.default → env CHAT_DEFAULT_MODEL →
                # legacy SLM. Indic (translate) flow keeps None so the existing
                # Sarvam hedged path wins.
                if _want_translate:
                    _stream_model = None
                elif msg.model:
                    _stream_model = msg.model
                else:
                    _stream_model = "openai/gpt-oss-20b"
                    try:
                        _api_cfg = await db.api_config.find_one({}, {"_id": 0, "chat_model": 1})
                        _admin_default = ((_api_cfg or {}).get("chat_model") or {}).get("default")
                        if _admin_default and isinstance(_admin_default, str):
                            _stream_model = _admin_default.strip() or _stream_model
                        else:
                            from config import CHAT_DEFAULT_MODEL as _CHAT_DEFAULT_MODEL
                            if _CHAT_DEFAULT_MODEL:
                                _stream_model = _CHAT_DEFAULT_MODEL
                    except Exception as _e_cfg:
                        logger.debug(f"chat_model config lookup failed: {_e_cfg}")
                # Task #610 — phase span boundaries.
                # Retrieval = everything from request start through the
                # moment we hand the prompt to the LLM (Phase 0+1+2,
                # cache lookup, prompt build). Captured so we can emit
                # an explicit `chat.retrieval` child span at the end of
                # the request alongside `chat.llm_call` and
                # `chat.post_processing`.
                _t_llm_start = _time_mod.time()
                _retrieval_ms = (_t_llm_start - _stream_t0) * 1000.0
                try:
                    record_chat_attrs(**{"syrabit.chat.retrieval_ms": _retrieval_ms})
                except Exception:
                    pass
                _active_stream = call_llm_api_stream(messages_payload, model=_stream_model, max_tokens=max_tokens, intent=_stream_intent, response_lang=_resp_lang)

                # For Assamese (Indic) responses we buffer the entire LLM
                # output server-side instead of streaming it directly so we
                # can sanitize stray English-fragment leakage (e.g. "me uses",
                # "ssible") before any tokens reach the user.
                from lang_sanitizer import (
                    sanitize_assamese_with_optional_regenerate as _sanitize_asm_async,
                    get_behaviour as _asm_behaviour,
                )
                _indic_buffer_mode = bool(_want_translate) and _asm_behaviour() != "off"
                _indic_pending_chunks: list = []
                # S1: rolling chunk translate — 250-char English windows flushed as
                # Assamese immediately, cutting TTFB from 5-18 s → ~3-5 s.
                _rolling_en_buf = ""        # accumulated English content
                _rolling_as_chunks: list = []  # Assamese chunks already yielded
                _ROLL_CHUNK_CHARS = 250     # translate every ~250 English chars

                # When buffering Assamese, the user otherwise sees a blank
                # bubble for the entire LLM generation + sanitize window
                # (multiple seconds), which feels like a hung / truncated
                # reply. Emit a `translating: true` marker so the existing
                # frontend handler swaps the empty bubble for the
                # "Translating to Assamese…" indicator. Cleared
                # automatically when the first content chunk flushes
                # client-side (frontend `flushPending` resets the flag).
                if _indic_buffer_mode:
                    yield f"data: {json.dumps({'translating': True})}\n\n"

                async for chunk in _active_stream:
                    if '"__provider"' in chunk and chunk.startswith("data: "):
                        try:
                            _pdata = json.loads(chunk[6:])
                            if isinstance(_pdata, dict) and "__provider" in _pdata and len(_pdata) == 1:
                                _stream_provider = _pdata["__provider"]
                                continue
                        except Exception:
                            pass
                    if '"content"' in chunk:
                        if not _first_token_logged:
                            _ttfb_llm_ms = (_time_mod.time() - _stream_t0) * 1000
                            logger.info(f"[STREAM][TIMING] TTFT (first LLM token): {_ttfb_llm_ms / 1000:.3f}s")
                            try:
                                _speedup.record_ttfb(_ttfb_llm_ms)
                                _speedup.record_lang_ttfb(_resp_lang, _ttfb_llm_ms)
                            except Exception:
                                pass
                            try:
                                # Task #610 — chat span: first-token milestone
                                record_first_token(_ttfb_llm_ms, source="llm")
                                record_chat_attrs(**{"syrabit.chat.provider": _stream_provider or "unknown"})
                            except Exception:
                                pass
                            _first_token_logged = True
                        _roll_piece = ""  # S1: reset per-chunk accumulator
                        try:
                            data = json.loads(chunk[6:])
                            _piece = data.get("content", "")
                            _tuned = _tune_response_stream(_piece, _stream_intent, _tune_buf)
                            _roll_piece = _tuned  # S1: capture for rolling buffer
                            full_response.append(_tuned)
                            _output_buf += _tuned
                            if _tuned != _piece:
                                chunk = f"data: {json.dumps({'content': _tuned})}\n\n"
                            if len(_output_buf) > 200:
                                _out_safe, _out_tag = validate_llm_output(_output_buf)
                                if not _out_safe:
                                    _output_violation = True
                                    logger.warning(f"[guardrails] LLM output violation mid-stream ({_out_tag})")
                                    break
                                _output_buf = _output_buf[-80:]
                        except:
                            pass
                    if _output_violation:
                        break
                    if _indic_buffer_mode:
                        if '"content"' in chunk and _sarvam_target and _roll_piece:
                            # S1: accumulate English content; translate at threshold boundary
                            _rolling_en_buf += _roll_piece
                            if len(_rolling_en_buf) >= _ROLL_CHUNK_CHARS:
                                try:
                                    _tr_rolled = await _assamese_translate_gemini_main_sarvam_polish(
                                        _rolling_en_buf, target_lang_code=_sarvam_target,
                                    )
                                    if _tr_rolled:
                                        _rolling_as_chunks.append(_tr_rolled)
                                        yield f"data: {json.dumps({'content': _tr_rolled})}\n\n"
                                except Exception as _roll_err:
                                    logger.debug("[S1-ROLL] chunk translate error: %s", _roll_err)
                                _rolling_en_buf = ""
                        else:
                            # Non-content events (metadata, SSE markers) buffered for later
                            _indic_pending_chunks.append(chunk)
                    else:
                        yield chunk
                    _bp_count += 1
                    if _bp_count % 40 == 0:
                        await asyncio.sleep(0)
                # Task #610 — mark end of LLM streaming phase. Anything
                # after this point (Indic sanitize, cache write, persist,
                # log fan-out) is `chat.post_processing`.
                _t_llm_end = _time_mod.time()
                # S1: flush any remaining English in the rolling buffer (tail of response)
                if _indic_buffer_mode and _rolling_en_buf and _sarvam_target:
                    try:
                        _tr_tail = await _assamese_translate_gemini_main_sarvam_polish(
                            _rolling_en_buf, target_lang_code=_sarvam_target,
                        )
                        if _tr_tail:
                            _rolling_as_chunks.append(_tr_tail)
                            yield f"data: {json.dumps({'content': _tr_tail})}\n\n"
                    except Exception as _tail_err:
                        logger.debug("[S1-ROLL] tail flush error: %s", _tail_err)
                    _rolling_en_buf = ""
                if _output_violation:
                    full_response.clear()
                    _fallback = "I need to stop here — my response was heading in a direction that doesn't align with my guidelines. Please try rephrasing your question."
                    full_response.append(_fallback)
                    _indic_pending_chunks = []
                    yield f"data: {json.dumps({'content': _fallback})}\n\n"

                # Always measure+log Assamese leakage at end of stream
                # (Task #419) — even when behaviour="off" we still emit the
                # per-reply diagnostic line so production can monitor live
                # leakage rates. Only mutate the user-visible stream when
                # `_indic_buffer_mode` is on (i.e. behaviour != "off").
                if bool(_want_translate) and not _output_violation and full_response:
                    _raw_indic = "".join(full_response)

                    async def _regenerate_indic():
                        # One-shot retry with a stronger leading directive.
                        # Guard via a flag so this can only run once per turn.
                        if getattr(_regenerate_indic, "_ran", False):
                            return None
                        _regenerate_indic._ran = True  # type: ignore[attr-defined]
                        retry_payload = [dict(m) for m in messages_payload]
                        if retry_payload and retry_payload[0].get("role") == "system":
                            retry_payload[0]["content"] = (
                                "STRICT RETRY — your previous reply contained "
                                "stray English words inside an Assamese answer. "
                                "Reply ONLY in Assamese script (অসমীয়া) this "
                                "time. No mid-sentence English words.\n\n"
                                + retry_payload[0]["content"]
                            )
                        retry_text_parts: list[str] = []
                        try:
                            async for _rchunk in call_llm_api_stream(
                                retry_payload, model=_stream_model,
                                max_tokens=max_tokens, intent=_stream_intent,
                                response_lang=_resp_lang,
                            ):
                                if '"content"' in _rchunk and _rchunk.startswith("data: "):
                                    try:
                                        _rd = json.loads(_rchunk[6:])
                                        retry_text_parts.append(_rd.get("content", ""))
                                    except Exception:
                                        pass
                        except Exception as _re:
                            logger.warning(f"[INDIC-SANITIZE] regenerate stream failed: {_re}")
                            return None
                        return "".join(retry_text_parts) or None

                    _cleaned_indic, _asm_diag = await _sanitize_asm_async(
                        _raw_indic,
                        # S1 rolling mode: content already yielded — retraction not
                        # possible, so skip regeneration (diagnostics still run).
                        regenerate_callable=None if _rolling_as_chunks else _regenerate_indic,
                        translate_callable=_make_assamese_translate_callable(_sarvam_target or "as-IN"),
                        trace={
                            "conversation_id": conv_id or msg.conversation_id or None,
                            "user_id": str(user_id) if user_id else None,
                        },
                    )
                    # Per-Task #419: emit one diagnostic line per Assamese
                    # streamed reply (even no-op) so we can monitor live
                    # leakage rates from production logs.
                    _asm_action = _asm_diag.get("action") or "noop"
                    _asm_log = (
                        f"[INDIC-SANITIZE][STREAM] action={_asm_action} "
                        f"ratio={_asm_diag.get('ratio', 0):.3f} "
                        f"threshold={_asm_diag.get('threshold', 0):.3f} "
                        f"behaviour={_asm_diag.get('behaviour')} "
                        f"translated={_asm_diag.get('translated', False)} "
                        f"regenerated={_asm_diag.get('regenerated', False)} "
                        f"sample_tokens={_asm_diag.get('suspicious_tokens', [])[:6]}"
                    )
                    if _asm_action == "noop" and not _asm_diag.get("regenerated"):
                        logger.info(_asm_log)
                    else:
                        logger.warning(_asm_log)
                    if _rolling_as_chunks:
                        # S1 rolling mode: content already yielded in translated chunks;
                        # update full_response with assembled Assamese for cache storage.
                        full_response.clear()
                        full_response.append("".join(_rolling_as_chunks))
                        # Yield any buffered non-content events (metadata SSE markers)
                        for _pc in _indic_pending_chunks:
                            yield _pc
                    elif _asm_action not in (None, "noop") or _asm_diag.get("regenerated"):
                        full_response.clear()
                        full_response.append(_cleaned_indic)
                        _CHUNK_SIZE_INDIC = 300
                        for _ic in range(0, len(_cleaned_indic), _CHUNK_SIZE_INDIC):
                            yield f"data: {json.dumps({'content': _cleaned_indic[_ic:_ic + _CHUNK_SIZE_INDIC]})}\n\n"
                            if _ic % (_CHUNK_SIZE_INDIC * 5) == 0:
                                await asyncio.sleep(0)
                    else:
                        for _pc in _indic_pending_chunks:
                            yield _pc

                if full_response:
                    answer_str = "".join(full_response)
                    if answer_str:
                        try:
                            _stream_elapsed_ms = (_time_mod.time() - _stream_t0) * 1000
                        except Exception:
                            _stream_elapsed_ms = 0.0
                        await ai_cache_aset(
                            _cache_key_val, answer_str, _cache_ttl_val,
                            saved_ms=_stream_elapsed_ms,
                        )
                        # Also write through the early-cache key for casual /
                        # small-talk intents only — the early path never
                        # consults the RAG fingerprint, so populating it for
                        # retrieval-bound intents could later return stale
                        # answers when the corpus changes (Task #609).
                        if _early_is_casual:
                            try:
                                await ai_cache_aset(
                                    _cache_key_early, answer_str, _cache_ttl_val,
                                    saved_ms=_stream_elapsed_ms,
                                )
                            except Exception:
                                pass
                            _ai_response_cache[_cache_key_early] = answer_str
                        _ai_response_cache[_cache_key_val] = answer_str
                        logger.info(f"Cache MISS → stored (STREAM, ttl={_cache_ttl_val}s): {_cache_key_val}")

            # Yield DONE immediately — DB writes happen in background
            answer = "".join(full_response)
            new_used_optimistic = (credits_info["used"] + 1 if answer else credits_info["used"]) if credits_info else 0

            # ── Resolve deferred source card slugs (ran in parallel with LLM) ────
            try:
                await asyncio.wait_for(_slug_task, timeout=0.5)
            except (asyncio.TimeoutError, Exception):
                pass
            try:
                _src_ctx_s = await asyncio.wait_for(_src_ctx_task, timeout=0.5)
            except (asyncio.TimeoutError, Exception):
                _src_ctx_s = subj_ctx or {}
            _src_board_s = _src_ctx_s.get("board_name") or ctx_board_name or ""
            _src_class_s = _src_ctx_s.get("class_name") or ctx_class_name or ""
            _src_stream_s = _src_ctx_s.get("stream_name") or ctx_stream_name or ""
            _src_board_slug = _src_ctx_s.get("board_slug") or ""
            _src_class_slug = _src_ctx_s.get("class_slug") or ""
            _src_subject_slug = _src_ctx_s.get("subject_slug") or ""

            # Re-build the inline-citation source list now that the
            # board/class/subject slugs have actually resolved — this
            # is what powers the clickable [PAGE: ...] markers in the
            # answer body and the source card under the bubble.  Without
            # this rebuild, chapter sources would have empty URLs and
            # the markdown layer would silently degrade citations to
            # bold text.  Web sources are kept as-is.
            try:
                _rebuilt = _sources_from_rag_ctx(
                    rag_ctx,
                    board_slug=_src_board_slug,
                    class_slug=_src_class_slug,
                    subject_slug=_src_subject_slug,
                )
                if web_results:
                    _rebuilt.extend(_sources_from_web_results(web_results))
                rag_sources = _rebuilt
            except Exception:
                # Keep the partial sources we built earlier rather than
                # erroring out the SSE on a sources-list bug.
                pass

            # ── syrabit_done event with credits metadata + RAG-derived sources + slugs ────
            done_payload = {
                "event": "syrabit_done",
                "conversation_id": conv_id,
                "credits_used": 1 if not is_anon else None,
                "credits_used_total": new_used_optimistic if not is_anon else None,
                "remaining_credits": max(0, credits_info["remaining"] - 1) if credits_info else None,
                "rag_source": rag_source_saved,
                "rag_chunks": rag_chunks_count,
                "words": len(answer.split()) if answer else 0,
                "sources": rag_sources,
                "web_search_used": web_search_used,
                "rag_chapter_slug": rag_chapter_slug or "",
                "ctx_board_name": _src_board_s,
                "ctx_class_name": _src_class_s,
                "ctx_stream_name": _src_stream_s,
                "ctx_board_slug": _src_board_slug,
                "ctx_class_slug": _src_class_slug,
                "ctx_subject_slug": _src_subject_slug,
                # Task #636 — fallback attribution mirrors the non-stream
                # `meta` block. `_stream_provider` is captured from the
                # in-band `__provider` SSE marker (see line ~1859); when
                # it ends up "workers-ai" the response came from the
                # last-resort edge tier. `fallback_reason` is reserved
                # for when streaming gains its own WAI fallback path
                # (currently empty since stream rotation handles its
                # own primary failover internally).
                "meta": {
                    "provider": locals().get("_stream_provider", "unknown") or "unknown",
                    "fallback_reason": "",
                    "fallback": locals().get("_stream_provider", "") == "workers-ai",
                },
            }
            if content_card_meta:
                done_payload["content_card_name"] = content_card_meta.get("card_name", "")
                done_payload["content_card_lesson"] = content_card_meta.get("lesson_name", "")
                done_payload["content_card_subject"] = content_card_meta.get("subject_name", "")
                done_payload["content_card_board"] = content_card_meta.get("board_name", "")
                done_payload["content_card_class"] = content_card_meta.get("class_name", "")
            yield f"data: {json.dumps(done_payload)}\n\n"
            yield "data: [DONE]\n\n"

            _t_stream_end = _time_mod.time()
            _total_stream_time = _t_stream_end - _stream_t0
            _rid = getattr(request.state, "request_id", "") if request else ""
            logger.info(
                f"[STREAM][TIMING][SUMMARY] "
                f"rid={_rid} | "
                f"auth={_auth_elapsed:.3f}s | "
                f"phase0+1={_t_phase1_done - _t_phase0:.3f}s | "
                f"phase2(RAG+web)={_t_phase2_done - _t_phase2:.3f}s | "
                f"pre-LLM={_t_phase2_done - _stream_t0:.3f}s | "
                f"total={_total_stream_time:.3f}s | "
                f"cached={'yes' if cached_answer else 'no'} | "
                f"model={msg.model or 'openai/gpt-oss-20b'} | "
                f"intent={_stream_intent} | "
                f"rag_quality={_rag_quality} | "
                f"web_used={web_search_used} | "
                f"words={len(answer.split()) if answer else 0}"
            )

            try:
                _final_total_ms = (_time_mod.time() - _stream_t0) * 1000
                _record_chat_latency(_final_total_ms)
                _speedup.record_total_latency(_final_total_ms)
                _speedup.record_lang_ttfb(_resp_lang, 0.0, _final_total_ms)
                # Preserve the path label set earlier by the cache-hit
                # branch (`cache`) — only attribute as `main` for true
                # non-cached LLM responses, otherwise the request-span
                # path breakdown (instant/cache/early-cache/main) ends
                # up double-counting cache hits as `main`.
                _final_attrs = {
                    "syrabit.chat.total_ms": _final_total_ms,
                    "syrabit.chat.cached": bool(cached_answer),
                    "syrabit.chat.web_used": bool(web_search_used),
                    "syrabit.chat.rag_source": rag_source_saved or "none",
                }
                if not cached_answer:
                    _final_attrs["syrabit.chat.path"] = "main"
                record_chat_attrs(**_final_attrs)
            except Exception:
                pass

            # Task #610 — emit explicit phase spans so retrieval, LLM
            # and post-processing each show up as a distinct child of
            # the request span in Cloud Trace. Wall-clock timestamps
            # are captured at known phase boundaries above; this block
            # converts them into proper OTel spans (no-op when tracing
            # is disabled).
            try:
                _t_done = _time_mod.time()
                if cached_answer:
                    # Cache-hit short-circuit: no LLM phase, retrieval
                    # spans the entire pre-yield window.
                    emit_phase_span(
                        "chat.retrieval",
                        _stream_t0,
                        _t_done,
                        **{
                            "syrabit.chat.path": "cache",
                            "syrabit.chat.rag_source": rag_source_saved or "none",
                            "syrabit.chat.cached": True,
                        },
                    )
                else:
                    _ts_llm_start = locals().get("_t_llm_start", _stream_t0)
                    _ts_llm_end = locals().get("_t_llm_end", _ts_llm_start)
                    emit_phase_span(
                        "chat.retrieval",
                        _stream_t0,
                        _ts_llm_start,
                        **{
                            "syrabit.chat.rag_source": rag_source_saved or "none",
                            "syrabit.chat.web_used": bool(web_search_used),
                            "syrabit.chat.rag_chunks": int(rag_chunks_count or 0),
                        },
                    )
                    emit_phase_span(
                        "chat.llm_call",
                        _ts_llm_start,
                        _ts_llm_end,
                        **{
                            "syrabit.chat.model": str(msg.model or "openai/gpt-oss-20b"),
                            "syrabit.chat.provider": locals().get("_stream_provider", "unknown") or "unknown",
                            "syrabit.chat.intent": _stream_intent,
                        },
                    )
                    emit_phase_span(
                        "chat.post_processing",
                        _ts_llm_end,
                        _t_done,
                        **{
                            "syrabit.chat.indic_buffered": bool(locals().get("_indic_buffer_mode", False)),
                            "syrabit.chat.output_violation": bool(locals().get("_output_violation", False)),
                        },
                    )
            except Exception:
                pass

            try:
                _pc = sum(len(m.get("content", "")) for m in messages_payload)
                _record_llm_cost(
                    model=msg.model or LLM_MODEL,
                    prompt_tokens=max(1, _pc // 4),
                    completion_tokens=max(1, len(answer) // 4) if answer else 1,
                    provider=_stream_provider if not cached_answer else "cache",
                    user_id=str(user_id) if user_id else "anonymous",
                )
            except Exception:
                pass

            if answer and _is_out_of_scope_response(answer) and rag_source_saved == "none":
                logger.info(f"[GUARD] Out-of-scope response detected — refunding credit for user {user_id or 'anon'}")
                _credit_saved = False

            if answer and (not _is_out_of_scope_response(answer) or rag_source_saved != "none"):
                _credit_saved = True
                if user_id:
                    _stream_followup_ctx = None
                    if _stream_intent in ("notes", "important_questions", "pyq"):
                        _prev_fu_completed = []
                        _prev_fu_remaining = []
                        _prev_fu_current = msg.chapter_name or ""
                        if _stream_followup_info:
                            _prev_fu_completed = list(_stream_followup_info.get("completed", []))
                            _next = _stream_followup_info.get("next_item", "")
                            if _next:
                                if _next not in _prev_fu_completed:
                                    _prev_fu_completed.append(_next)
                                _prev_fu_current = _next
                            _prev_fu_remaining = [r for r in _stream_followup_info.get("remaining", []) if r not in _prev_fu_completed]
                        if not _prev_fu_remaining:
                            if _stream_intent == "pyq":
                                _prev_fu_remaining = [m for m in ["1m", "2m", "3m", "5m", "10m"] if m not in _prev_fu_completed]
                            else:
                                _s_rag_chapters = rag_ctx.get("chapters", [])
                                _prev_fu_remaining = [
                                    ch.get("title", "") for ch in _s_rag_chapters
                                    if ch.get("title", "") and ch.get("title", "") not in _prev_fu_completed
                                ]
                        if _prev_fu_current in _prev_fu_remaining:
                            _prev_fu_remaining.remove(_prev_fu_current)
                        _stream_followup_ctx = build_followup_context(
                            intent=_stream_intent,
                            current_item=_prev_fu_current,
                            completed=_prev_fu_completed,
                            remaining=_prev_fu_remaining,
                        )
                    # Apply SEO/GEO/AEO enhancements to the response
                    _enhanced_result = None
                    if GEO_ENHANCEMENTS_ENABLED:
                        try:
                            _enhanced_result = chat_enhancement_layer.enhance_response(
                                answer=answer,
                                rag_sources=rag_sources or [],
                                intent=_stream_intent,
                                topic=msg.subject_name or msg.message[:100],
                                user_context={"user_id": user_id}
                            )
                            # Use enhanced answer if available
                            if _enhanced_result.get("answer") != answer:
                                answer = _enhanced_result["answer"]
                                logger.info(f"[GEO] Enhanced response with {len(_enhanced_result.get('enhancements_applied', []))} upgrades")
                        except Exception as _enh_err:
                            logger.warning(f"[GEO] Enhancement failed (non-fatal): {_enh_err}")
                            _enhanced_result = None
                    
                    asyncio.create_task(_persist_chat_turn(
                        conv_id, user_id,
                        user_msg_saved, answer,
                        rag_source_saved, rag_chunks_count,
                        credits_info["used"],
                        sources=rag_sources,
                        rag_subject_id=rag_subject_id,
                        rag_subject_name=rag_subject_name,
                        rag_board_name=_src_board_s,
                        rag_class_name=_src_class_s,
                        rag_stream_name=_src_stream_s,
                        rag_board_slug=_src_board_slug,
                        rag_class_slug=_src_class_slug,
                        rag_subject_slug=_src_subject_slug,
                        rag_chapter_name=rag_chapter_name,
                        rag_chapter_slug=rag_chapter_slug,
                        rag_topic_name=rag_topic_name,
                        rag_chunk_snippet=rag_chunk_snippet,
                        followup_context=_stream_followup_ctx,
                        # GEO enhancement metadata
                        enhancements_applied=_enhanced_result.get("enhancements_applied", []) if _enhanced_result else None,
                        geo_score_boost=_enhanced_result.get("geo_score_boost", 0.0) if _enhanced_result else 0.0,
                        cognitive_anchor=_enhanced_result.get("metadata", {}).get("framework_used") if _enhanced_result else None,
                    ))
                    asyncio.create_task(_log_chat_message(
                        user_id=user_id,
                        question=user_msg_saved,
                        raw_ai_answer=answer,
                        subject_id=msg.subject_id,
                        subject_name=msg.subject_name,
                        board_name=ctx_board_name,
                        class_name=ctx_class_name,
                        conversation_id=conv_id,
                    ))
                elif anon_id and conv_id:
                    try:
                        from cache import redis_get_anon_conversation, redis_save_anon_conversation
                        _now = datetime.now(timezone.utc).isoformat()
                        _existing = redis_get_anon_conversation(anon_id, conv_id)
                        if not _existing:
                            _existing = await supa_get_conversation(conv_id, anon_id)
                        _prev_msgs = (_existing.get("messages") or []) if _existing else []
                        _prev_msgs.append({"role": "user", "content": user_msg_saved, "timestamp": _now})
                        _asst_msg = {"role": "assistant", "content": answer, "timestamp": _now,
                                     "rag_source": rag_source_saved, "rag_chunks": rag_chunks_count}
                        if rag_sources:
                            _asst_msg["sources"] = rag_sources
                        if rag_subject_id:
                            _asst_msg["rag_subject_id"] = rag_subject_id
                        if rag_subject_name:
                            _asst_msg["rag_subject_name"] = rag_subject_name
                        if rag_chapter_name:
                            _asst_msg["rag_chapter_name"] = rag_chapter_name
                        if rag_chapter_slug:
                            _asst_msg["rag_chapter_slug"] = rag_chapter_slug
                        if rag_topic_name:
                            _asst_msg["rag_topic_name"] = rag_topic_name
                        if rag_chunk_snippet:
                            _asst_msg["rag_chunk_snippet"] = rag_chunk_snippet
                        if _src_board_s:
                            _asst_msg["rag_board_name"] = _src_board_s
                        if _src_class_s:
                            _asst_msg["rag_class_name"] = _src_class_s
                        if _src_stream_s:
                            _asst_msg["rag_stream_name"] = _src_stream_s
                        if _src_board_slug:
                            _asst_msg["rag_board_slug"] = _src_board_slug
                        if _src_class_slug:
                            _asst_msg["rag_class_slug"] = _src_class_slug
                        if _src_subject_slug:
                            _asst_msg["rag_subject_slug"] = _src_subject_slug
                        _prev_msgs.append(_asst_msg)
                        _anon_doc = _existing or {}
                        _anon_doc.update({
                            "id": conv_id, "anon_id": anon_id,
                            "user_id": anon_id,
                            "messages": _prev_msgs,
                            "preview": answer[:100],
                            "updated_at": _now,
                            "is_anonymous": True,
                        })
                        redis_save_anon_conversation(anon_id, conv_id, _anon_doc)
                        asyncio.create_task(supa_upsert_conversation({
                            "id": conv_id, "user_id": anon_id,
                            "title": _anon_doc.get("title", user_msg_saved[:50]),
                            "preview": answer[:100],
                            "subject_id": msg.subject_id or "",
                            "subject_name": msg.subject_name or "",
                            "messages": _prev_msgs,
                            "tokens": len(answer.split()),
                            "created_at": _anon_doc.get("created_at", _now),
                            "updated_at": _now,
                            "is_anonymous": True,
                            "anon_id": anon_id,
                        }))
                        asyncio.create_task(_log_chat_message(
                            user_id=anon_id,
                            question=user_msg_saved,
                            raw_ai_answer=answer,
                            subject_id=msg.subject_id,
                            subject_name=msg.subject_name,
                            board_name=ctx_board_name,
                            class_name=ctx_class_name,
                            conversation_id=conv_id,
                        ))
                    except Exception as _anon_err:
                        logger.warning(f"anon persist failed: {_anon_err}")
        finally:
            if not _credit_saved and user_id and credits_info:
                asyncio.create_task(_refund_credit(user_id, credits_info["used"] + 1))

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ─────────────────────────────────────────────
# F2: SPECULATIVE WARM-QUERY  — /api/ai/warm-query
# Fired by the frontend on a typing pause (≥15 chars, ≥800ms idle).
# Pre-fetches internal chapters into Redis so the next /ai/chat request
# can skip _fetch_internal_chapters entirely in Phase 0.
# ─────────────────────────────────────────────
@router.post("/ai/warm-query", status_code=202)
async def warm_query_endpoint(request: Request):
    """F2: Pre-fetch RAG chapters for a query being typed; cache for 20 s."""
    import hashlib as _hl, json as _json
    try:
        _body = await request.json()
    except Exception:
        return {"status": "skip"}
    query = (_body.get("query") or "").strip()
    _subject_id = _body.get("subject_id") or None
    _subject_name = _body.get("subject_name") or None
    if len(query) < 15 or not (_subject_id or _subject_name):
        return {"status": "skip"}

    _sid = _subject_id or ""
    _wkey = f"warm_ch:{_hl.md5(f'{query}|{_sid}'.encode()).hexdigest()}"

    # Skip if already warmed for this exact query
    if redis_client:
        try:
            if redis_client.exists(_wkey):
                return {"status": "cached"}
        except Exception:
            pass

    async def _bg_prefetch():
        try:
            chapters = await _fetch_internal_chapters(query, subject_id=_subject_id, subject_name=_subject_name)
            if chapters and redis_client:
                redis_client.setex(_wkey, 20, _json.dumps(chapters))
                logger.debug("[F2-WARM] Pre-fetched %d chapters for warm key", len(chapters))
        except Exception as _e:
            logger.debug("[F2-WARM] Background prefetch error (non-fatal): %s", _e)

    asyncio.create_task(_bg_prefetch())
    return {"status": "warming"}


# ─────────────────────────────────────────────
# PUBLIC SEARCH API  — /api/v1/search
# ─────────────────────────────────────────────
@router.get("/v1/search", response_model=SearchResultOut)
async def public_library_search(q: str = "", board: Optional[str] = None, class_num: Optional[str] = None):
    """Public search endpoint: returns matching syrabit.ai library pages.
    Example: GET /api/v1/search?q=limits+class+11+ahsec
    """
    if not q.strip():
        raise HTTPException(status_code=400, detail="q parameter is required")
    results = await syrabit_library_search(q.strip(), board_slug=board, class_slug=class_num)
    return {"query": q, "results": results, "count": len(results)}


# ─────────────────────────────────────────────
# CONVERSATION ROUTES
