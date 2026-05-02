"""
Syrabit.ai — Single-LLM Pipeline with optional Stage 1 topic classification.

Stage 1 (Topic Resolver):   Fast/small model extracts structured topic metadata (non-blocking, best-effort).
Main LLM call:              Single streaming LLM call using training knowledge directly.

Stage 2 (RAG Synthesizer) and Stage 3 (Response Polisher) have been removed.
All queries go to a single LLM call for sub-1-second TTFT.
"""
import json
import time
import logging
import asyncio
from typing import Optional, AsyncGenerator

logger = logging.getLogger(__name__)

_PIPELINE_METRICS: list = []
_PIPELINE_METRICS_MAX = 5000

def _record_pipeline_stage(stage: str, model: str, provider: str, duration_ms: float, success: bool, error_type: str = ""):
    _PIPELINE_METRICS.append({
        "ts": time.time(),
        "stage": stage,
        "model": model,
        "provider": provider,
        "duration_ms": round(duration_ms, 1),
        "success": success,
        "error_type": error_type,
    })
    if len(_PIPELINE_METRICS) > _PIPELINE_METRICS_MAX:
        del _PIPELINE_METRICS[:500]

def get_pipeline_stats(window_seconds: int = 3600) -> dict:
    cutoff = time.time() - window_seconds
    recent = [m for m in _PIPELINE_METRICS if m["ts"] >= cutoff]
    by_stage: dict = {}
    for m in recent:
        s = m["stage"]
        if s not in by_stage:
            by_stage[s] = {"calls": 0, "successes": 0, "failures": 0, "total_ms": 0.0, "models": set()}
        by_stage[s]["calls"] += 1
        by_stage[s]["total_ms"] += m["duration_ms"]
        by_stage[s]["models"].add(m["model"])
        if m["success"]:
            by_stage[s]["successes"] += 1
        else:
            by_stage[s]["failures"] += 1
    result = {}
    for s, d in by_stage.items():
        result[s] = {
            "calls": d["calls"],
            "success_rate": round(d["successes"] / max(d["calls"], 1) * 100, 1),
            "failures": d["failures"],
            "avg_latency_ms": round(d["total_ms"] / max(d["calls"], 1), 1),
            "models": list(d["models"]),
        }
    return {"stages": result, "window_seconds": window_seconds}


_HARD_BYPASS_INTENTS = {"syllabus", "chapter_meta"}

_STAGE1_PROMPT = """Topic classifier for Assam board education (AHSEC/SEBA/DEGREE). Return ONLY JSON:
{"subject":"","chapter":"","topic":"","intent":"notes|important_questions|pyq|syllabus|chapter_meta|casual|general","search_keywords":["3-5 terms"],"confidence":"high|low"}

Rules: casual=greetings/small talk. general=non-academic. search_keywords should be syllabus-aligned terms that help enrich context. JSON only, no explanation."""

_STAGE2_PROMPT_TEMPLATE = """You are a factual synthesizer. Your job is to read the retrieved content chunks below and produce a strictly-grounded factual answer to the student's question.

RULES:
1. ONLY use information from the provided chunks. Do NOT add facts from your own knowledge.
2. Discard chunks that are irrelevant to the question.
3. CROSS-DOMAIN CHECK: Compare each chunk's academic subject against the student's question domain.
   - If the student asks about a biology/science concept (e.g., "ecosystem", "photosynthesis") but a chunk discusses business, finance, or commerce topics, DISCARD that chunk entirely.
   - If the student asks about a commerce/business concept but a chunk discusses science topics, DISCARD that chunk.
   - Only use chunks whose academic domain matches the question's domain.
4. Synthesize a complete but unpolished factual answer from the remaining relevant chunks.
5. Include all relevant details, definitions, formulas, and examples found in the on-domain chunks.
6. Do NOT format for presentation — no fancy headings or bullet points needed. Just accurate facts.
7. If after discarding off-domain chunks there is not enough information to answer, say "NO_RELEVANT_CONTENT" — do not fabricate an answer from unrelated chunks.
8. Preserve technical terms, formulas, and specific data exactly as they appear in the chunks.

STUDENT'S QUESTION: {query}

{topic_context}

RETRIEVED CONTENT:
{rag_content}

Produce a factual synthesis based ONLY on the above on-domain content. If all chunks are from unrelated subjects, respond with "NO_RELEVANT_CONTENT"."""

_STAGE3_PROMPT_TEMPLATE = """You are Syra, a friendly AI study mentor for students of {board_desc} in Assam, India.

Take the factual draft below and format it into a well-structured, student-friendly response.

RULES:
1. PRESERVE all facts from the draft — do NOT add new information or change any facts.
2. Use clear headings, bullet points, and numbered lists where appropriate.
3. Use Markdown for mathematical expressions and formulas.
4. Add brief, helpful transitions and explanations to aid understanding.
5. Keep the tone warm, encouraging, and appropriate for exam preparation.
6. Match answer depth to the question type:
   - Simple definition: 3-5 clear sentences
   - Conceptual explanation: 150-300 words with key points
   - Detailed/long answer: structured with headings and subpoints
7. Include at least one example or analogy for conceptual topics if the draft has relevant material.
8. End with a natural follow-up suggestion when appropriate.

STUDENT PROFILE:
{student_profile}

STUDENT'S QUESTION: {query}

FACTUAL DRAFT:
{factual_draft}

Format this into a clear, student-friendly response. Preserve all factual content."""


def _pick_stage1_providers() -> list:
    from llm import _LLM_PROVIDERS
    providers = []
    for p in _LLM_PROVIDERS:
        pid = (p["provider"], id(p["key"]))
        if pid not in {(pp["provider"], id(pp["key"])) for pp in providers}:
            providers.append(p)
    return providers


def _pick_stage2_providers() -> list:
    from llm import _LLM_PROVIDERS
    providers = []
    for p in _LLM_PROVIDERS:
        pid = (p["provider"], id(p["key"]))
        if pid not in {(pp["provider"], id(pp["key"])) for pp in providers}:
            providers.append(p)
    return providers


_OBVIOUS_CASUAL_PATTERNS = {"hi", "hello", "hey", "thanks", "thank you", "bye", "ok", "okay", "good", "nice", "hii", "hiii", "namaste", "dhanyabad"}

_INSTANT_CASUAL_RESPONSES = {
    "hi": "Hi there! 👋 I'm Syra, your study assistant. How can I help you today?",
    "hii": "Hi there! 👋 I'm Syra, your study assistant. How can I help you today?",
    "hiii": "Hi there! 👋 I'm Syra. What would you like to study today?",
    "hello": "Hello! 👋 I'm Syra. Ask me anything about your syllabus — or just chat!",
    "hey": "Hey! 👋 Ready to study? Ask me anything about your subjects!",
    "namaste": "Namaste! 🙏 I'm Syra, your study companion. How can I help you today?",
    "thanks": "You're welcome! 😊 Let me know if you need anything else.",
    "thank you": "You're welcome! 😊 Happy to help — feel free to ask more anytime.",
    "dhanyabad": "Dhanyabad! 🙏 I'm always here to help you study.",
    "bye": "Bye! 👋 Good luck with your studies. Come back anytime!",
    "ok": "Great! Let me know if you have any questions. 📚",
    "okay": "Sure! I'm here whenever you need help. 📚",
    "good": "Glad to hear that! Anything else you'd like to learn about?",
    "nice": "Thank you! Is there anything you'd like to study?",
}

def get_instant_response(query: str) -> str | None:
    normalized = query.strip().lower().rstrip("!. ")
    return _INSTANT_CASUAL_RESPONSES.get(normalized)


# Pre-translated Assamese instant responses — returned directly without any
# LLM or translation call, saving 3-7 s on common greetings/acknowledgements.
_INSTANT_ASSAMESE_RESPONSES: dict[str, str] = {
    "নমস্কাৰ": "নমস্কাৰ! 🙏 মই ছয়ৰা, আপোনাৰ অধ্যয়ন সহায়কাৰী। আজি আপোনাক কেনেকৈ সহায় কৰিব পাৰো?",
    "হেল্লো": "হেল্লো! 👋 মই ছয়ৰা। পাঠ্যক্ৰমৰ যিকোনো বিষয়ে সুধিব পাৰে!",
    "হেল্ল'": "হেল্লো! 👋 মই ছয়ৰা। পাঠ্যক্ৰমৰ যিকোনো বিষয়ে সুধিব পাৰে!",
    "ধন্যবাদ": "আপোনাক স্বাগতম! 😊 আৰু কিবা জানিব বিচাৰে নেকি?",
    "আপোনাক ধন্যবাদ": "আপোনাক স্বাগতম! 😊 যিকোনো সময়তে সহায়ৰ বাবে আহিব পাৰে।",
    "বিদায়": "বিদায়! 👋 পঢ়া-শুনাত শুভকামনা। যিকোনো সময়তে আহিব পাৰে!",
    "ঠিক আছে": "ঠিক আছে! 📚 যদি কোনো প্ৰশ্ন থাকে, সুধিব।",
    "হয়": "বহুত ভাল! 📚 আৰু কিবা জানিব বিচাৰে নেকি?",
    "ভাল": "বহুত ভাল! আন কিবা সহায় লাগিব নেকি? 📚",
    "সহায় কৰা": "অৱশ্যেই! আপোনাৰ বিষয় বা অধ্যায়ৰ বিষয়ে সুধিব পাৰে।",
    "নমস্কাৰ!": "নমস্কাৰ! 🙏 মই ছয়ৰা, আপোনাৰ অধ্যয়ন সহায়কাৰী। আজি আপোনাক কেনেকৈ সহায় কৰিব পাৰো?",
}


def get_instant_assamese_response(query: str) -> str | None:
    """Return a pre-translated Assamese response for common greetings/phrases.
    Skips the entire LLM + translation pipeline — TTFB ~0 ms."""
    normalized = query.strip().rstrip("!। ")
    return _INSTANT_ASSAMESE_RESPONSES.get(normalized)

_STAGE1_SKIP_INTENTS = {"casual", "general", "syllabus", "chapter_meta"}

def should_use_pipeline(intent: str, query: str) -> bool:
    if intent in _HARD_BYPASS_INTENTS:
        return False
    if intent in _STAGE1_SKIP_INTENTS:
        return False
    stripped = query.strip()
    if len(stripped) < 8:
        return False
    if stripped.lower().rstrip("!. ") in _OBVIOUS_CASUAL_PATTERNS:
        return False
    return True


def apply_stage1_to_intent(topic_metadata: dict, regex_intent: str, regex_db_category: Optional[str]) -> tuple:
    from prompts import INTENT_TO_DB_CATEGORY
    s1_intent = (topic_metadata.get("intent") or "").strip().lower()
    valid_intents = {"notes", "important_questions", "pyq", "syllabus", "chapter_meta", "casual", "general"}
    if s1_intent in valid_intents:
        new_intent = s1_intent
    else:
        new_intent = regex_intent
    new_db_cat = INTENT_TO_DB_CATEGORY.get(new_intent, regex_db_category)
    return new_intent, new_db_cat


def build_enhanced_query(original_query: str, topic_metadata: dict) -> str:
    keywords = topic_metadata.get("search_keywords", [])
    if not isinstance(keywords, list):
        return original_query
    safe_keywords = [str(k) for k in keywords if isinstance(k, str) and k.strip()]
    if not safe_keywords:
        return original_query
    kw_str = " ".join(k for k in safe_keywords if k.lower() not in original_query.lower())
    if kw_str:
        return f"{original_query} {kw_str}"
    return original_query


import hashlib
_stage1_cache: dict[str, tuple[float, dict]] = {}
_STAGE1_CACHE_TTL = 600
_STAGE1_CACHE_MAX = 768

def _stage1_cache_key(query: str) -> str:
    return hashlib.md5(query.strip().lower().encode()).hexdigest()

async def stage1_resolve_topic(query: str, context: dict = None) -> Optional[dict]:
    ck = _stage1_cache_key(query)
    cached = _stage1_cache.get(ck)
    if cached:
        ts, result = cached
        if time.time() - ts < _STAGE1_CACHE_TTL:
            logger.info(f"[PIPELINE][S1] Cache HIT for '{query[:40]}' (age={time.time()-ts:.0f}s)")
            return result
        else:
            del _stage1_cache[ck]

    from llm import _call_llm_raw
    t0 = time.perf_counter()
    providers = _pick_stage1_providers()

    if not providers:
        logger.warning("[PIPELINE][S1] No providers available for topic resolution")
        return None

    messages = [
        {"role": "system", "content": _STAGE1_PROMPT},
        {"role": "user", "content": query},
    ]

    provider_name = providers[0]["provider"] if providers else "unknown"
    model_name = providers[0]["default_model"] if providers else "unknown"

    try:
        raw = await asyncio.wait_for(
            _call_llm_raw(messages, model=model_name, max_tokens=150, provider_list=providers),
            timeout=0.8,
        )
        dur = (time.perf_counter() - t0) * 1000

        raw = raw.strip()
        if raw.startswith("```json"):
            raw = raw[7:]
        if raw.startswith("```"):
            raw = raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

        result = json.loads(raw)
        _record_pipeline_stage("topic_resolver", model_name, provider_name, dur, True)
        logger.info(
            f"[PIPELINE][S1] Topic resolved in {dur:.0f}ms: "
            f"subject={result.get('subject','?')}, chapter={result.get('chapter','?')}, "
            f"intent={result.get('intent','?')}, keywords={result.get('search_keywords',[])} "
            f"needs_web={result.get('needs_web_search', True)} "
            f"| provider={provider_name}/{model_name}"
        )
        if len(_stage1_cache) >= _STAGE1_CACHE_MAX:
            oldest_key = min(_stage1_cache, key=lambda k: _stage1_cache[k][0])
            del _stage1_cache[oldest_key]
        _stage1_cache[ck] = (time.time(), result)
        return result
    except asyncio.TimeoutError:
        dur = (time.perf_counter() - t0) * 1000
        _record_pipeline_stage("topic_resolver", model_name, provider_name, dur, False, "timeout")
        logger.warning(f"[PIPELINE][S1] Topic resolution timed out after {dur:.0f}ms")
        return None
    except json.JSONDecodeError as e:
        dur = (time.perf_counter() - t0) * 1000
        _record_pipeline_stage("topic_resolver", model_name, provider_name, dur, False, "json_error")
        logger.warning(f"[PIPELINE][S1] Failed to parse JSON from topic resolver: {e}")
        return None
    except Exception as e:
        dur = (time.perf_counter() - t0) * 1000
        _record_pipeline_stage("topic_resolver", model_name, provider_name, dur, False, type(e).__name__)
        logger.warning(f"[PIPELINE][S1] Topic resolution failed: {type(e).__name__}: {str(e)[:150]}")
        return None


def _build_rag_content_text(rag_ctx: dict, max_chars: int = 8000) -> str:
    parts = []

    doc_text = rag_ctx.get("document_text", "")
    if doc_text:
        parts.append(f"[DOCUMENT CONTENT]\n{doc_text[:max_chars]}")
        return "\n\n".join(parts)[:max_chars]

    chunks = rag_ctx.get("chunks", [])
    for i, chunk in enumerate(chunks[:10]):
        title = chunk.get("title", "") or chunk.get("chapter_title", "") or ""
        content = chunk.get("content", "") or chunk.get("text", "") or ""
        ctype = chunk.get("type", "") or chunk.get("content_type", "") or ""
        if content:
            header = f"[CHUNK {i+1}"
            if title:
                header += f": {title}"
            if ctype:
                header += f" | type={ctype}"
            header += "]"
            parts.append(f"{header}\n{content}")

    vector_hits = rag_ctx.get("vector_hits", [])
    for i, hit in enumerate(vector_hits[:5]):
        content = hit.get("content", "") or hit.get("text", "") or ""
        title = hit.get("title", "") or ""
        if content and content not in "\n".join(parts):
            header = f"[VECTOR HIT {i+1}"
            if title:
                header += f": {title}"
            header += "]"
            parts.append(f"{header}\n{content}")

    chapters = rag_ctx.get("chapters", [])
    for ch in chapters[:5]:
        ch_title = ch.get("title", "")
        ch_content = ch.get("content", "") or ch.get("description", "") or ""
        if ch_content and ch_content not in "\n".join(parts):
            parts.append(f"[CHAPTER: {ch_title}]\n{ch_content}")

    result = "\n\n".join(parts)
    return result[:max_chars]


async def stage2_synthesize(query: str, rag_ctx: dict, topic_metadata: Optional[dict] = None) -> Optional[str]:
    from llm import _call_llm_raw
    t0 = time.perf_counter()
    providers = _pick_stage2_providers()

    if not providers:
        logger.warning("[PIPELINE][S2] No providers available for synthesis")
        return None

    rag_content = _build_rag_content_text(rag_ctx)
    if not rag_content.strip():
        logger.info("[PIPELINE][S2] No RAG content to synthesize — skipping Stage 2")
        return None

    topic_context = ""
    if topic_metadata:
        parts = []
        if topic_metadata.get("subject"):
            parts.append(f"Subject: {topic_metadata['subject']}")
        if topic_metadata.get("chapter"):
            parts.append(f"Chapter: {topic_metadata['chapter']}")
        if topic_metadata.get("topic"):
            parts.append(f"Topic: {topic_metadata['topic']}")
        if parts:
            topic_context = "TOPIC CONTEXT:\n" + "\n".join(parts)

    prompt = _STAGE2_PROMPT_TEMPLATE.format(
        query=query,
        topic_context=topic_context,
        rag_content=rag_content,
    )

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"Synthesize the answer for: {query}"},
    ]

    provider_name = providers[0]["provider"] if providers else "unknown"
    model_name = providers[0]["default_model"] if providers else "unknown"

    try:
        result = await asyncio.wait_for(
            _call_llm_raw(messages, model=model_name, max_tokens=2048, provider_list=providers),
            timeout=8.0,
        )
        dur = (time.perf_counter() - t0) * 1000
        _record_pipeline_stage("rag_synthesizer", model_name, provider_name, dur, True)
        logger.info(
            f"[PIPELINE][S2] Synthesis done in {dur:.0f}ms: "
            f"{len(result)} chars | provider={provider_name}/{model_name}"
        )
        return result
    except asyncio.TimeoutError:
        dur = (time.perf_counter() - t0) * 1000
        _record_pipeline_stage("rag_synthesizer", model_name, provider_name, dur, False, "timeout")
        logger.warning(f"[PIPELINE][S2] Synthesis timed out after {dur:.0f}ms")
        return None
    except Exception as e:
        dur = (time.perf_counter() - t0) * 1000
        _record_pipeline_stage("rag_synthesizer", model_name, provider_name, dur, False, type(e).__name__)
        logger.warning(f"[PIPELINE][S2] Synthesis failed: {type(e).__name__}: {str(e)[:150]}")
        return None


def _build_stage3_prompt(
    query: str,
    factual_draft: str,
    context: dict = None,
    user_info: dict = None,
) -> str:
    ctx = context or {}
    ui = user_info or {}

    board = (ctx.get("board_name", "") or "").strip().upper()
    from prompts import _format_board_label
    board_desc = _format_board_label(board) if board else "Assam education boards"

    name = (ui.get("name", "") or "").split()[0] if ui.get("name") else "Student"
    cls = ctx.get("class_name", "") or ui.get("class_name", "")
    subject = ctx.get("subject_name", "") or ""
    chapter = ctx.get("chapter_name", "") or ""
    plan = ui.get("plan", "free")

    profile_lines = [f"  Name: {name}"]
    if board:
        profile_lines.append(f"  Board: {board_desc}")
    if cls:
        profile_lines.append(f"  Class: {cls}")
    if subject:
        profile_lines.append(f"  Subject: {subject}")
    if chapter:
        profile_lines.append(f"  Chapter: {chapter}")
    profile_lines.append(f"  Plan: {plan}")
    student_profile = "\n".join(profile_lines)

    return _STAGE3_PROMPT_TEMPLATE.format(
        board_desc=board_desc,
        student_profile=student_profile,
        query=query,
        factual_draft=factual_draft,
    )


async def stage3_polish(
    query: str,
    factual_draft: str,
    context: dict = None,
    user_info: dict = None,
    max_tokens: int = 4096,
) -> Optional[str]:
    from llm import _call_llm_raw, _LLM_PROVIDERS_CHAT
    from config import _GEMINI_KEY
    t0 = time.perf_counter()

    providers = []
    if _GEMINI_KEY:
        providers.append({"provider": "gemini", "key": _GEMINI_KEY, "default_model": "gemini-2.5-flash"})
    for p in _LLM_PROVIDERS_CHAT:
        pid = (p["provider"], id(p["key"]))
        if pid not in {(pp["provider"], id(pp["key"])) for pp in providers}:
            providers.append(p)

    if not providers:
        logger.warning("[PIPELINE][S3] No providers available for polishing")
        return None

    prompt = _build_stage3_prompt(query, factual_draft, context, user_info)
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": query},
    ]

    provider_name = providers[0]["provider"] if providers else "unknown"
    model_name = providers[0]["default_model"] if providers else "unknown"

    try:
        result = await asyncio.wait_for(
            _call_llm_raw(messages, model=model_name, max_tokens=max_tokens, provider_list=providers),
            timeout=15.0,
        )
        dur = (time.perf_counter() - t0) * 1000
        _record_pipeline_stage("response_polisher", model_name, provider_name, dur, True)
        logger.info(
            f"[PIPELINE][S3] Polish done in {dur:.0f}ms: "
            f"{len(result)} chars | provider={provider_name}/{model_name}"
        )
        return result
    except asyncio.TimeoutError:
        dur = (time.perf_counter() - t0) * 1000
        _record_pipeline_stage("response_polisher", model_name, provider_name, dur, False, "timeout")
        logger.warning(f"[PIPELINE][S3] Polish timed out after {dur:.0f}ms")
        return None
    except Exception as e:
        dur = (time.perf_counter() - t0) * 1000
        _record_pipeline_stage("response_polisher", model_name, provider_name, dur, False, type(e).__name__)
        logger.warning(f"[PIPELINE][S3] Polish failed: {type(e).__name__}: {str(e)[:150]}")
        return None


async def stage3_polish_stream(
    query: str,
    factual_draft: str,
    context: dict = None,
    user_info: dict = None,
    max_tokens: int = 4096,
    intent: str = "",
) -> AsyncGenerator[str, None]:
    from llm import call_llm_api_stream
    from config import _GEMINI_KEY
    t0 = time.perf_counter()

    prompt = _build_stage3_prompt(query, factual_draft, context, user_info)
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": query},
    ]

    stream_model = "gemini-2.5-flash" if _GEMINI_KEY else "openai/gpt-oss-20b"

    first_token = False
    try:
        async for chunk in call_llm_api_stream(messages, model=stream_model, max_tokens=max_tokens, intent=intent):
            if not first_token:
                dur = (time.perf_counter() - t0) * 1000
                logger.info(f"[PIPELINE][S3] Polish TTFT: {dur:.0f}ms (model={stream_model})")
                first_token = True
            yield chunk
        dur = (time.perf_counter() - t0) * 1000
        _record_pipeline_stage("response_polisher_stream", stream_model, "gemini" if _GEMINI_KEY else "slm_pool", dur, True)
        logger.info(f"[PIPELINE][S3] Polish stream done in {dur:.0f}ms (model={stream_model})")
    except Exception as e:
        dur = (time.perf_counter() - t0) * 1000
        _record_pipeline_stage("response_polisher_stream", stream_model, "gemini" if _GEMINI_KEY else "slm_pool", dur, False, type(e).__name__)
        logger.warning(f"[PIPELINE][S3] Polish stream failed: {type(e).__name__}: {str(e)[:150]}")
        raise


async def run_pipeline(
    query: str,
    rag_ctx: dict,
    context: dict = None,
    user_info: dict = None,
    max_tokens: int = 4096,
    regex_intent: str = "notes",
    topic_metadata: Optional[dict] = None,
) -> Optional[str]:
    pipeline_t0 = time.perf_counter()

    if not should_use_pipeline(regex_intent, query):
        logger.info(f"[PIPELINE] Bypassed (intent={regex_intent}, query_len={len(query)})")
        return None

    logger.info("[PIPELINE] Stage 2+3 disabled — using single-LLM with Stage 1 metadata only")
    return None


async def run_pipeline_stream(
    query: str,
    rag_ctx: dict,
    context: dict = None,
    user_info: dict = None,
    max_tokens: int = 4096,
    regex_intent: str = "notes",
    intent: str = "",
    topic_metadata: Optional[dict] = None,
) -> Optional[AsyncGenerator[str, None]]:
    pipeline_t0 = time.perf_counter()

    if not should_use_pipeline(regex_intent, query):
        logger.info(f"[PIPELINE] Bypassed for streaming (intent={regex_intent}, query_len={len(query)})")
        return None

    logger.info("[PIPELINE] Stage 2+3 disabled — using single-LLM with Stage 1 metadata only (stream)")
    return None
