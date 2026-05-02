"""Syrabit.ai — Multi-LLM pipeline speed benchmark (admin-only).

Compares:
  Method A (Current):  regex intent → RAG → single LLM (Smart Key Pool)
  Method B (Multi-LLM): Cerebras 8b → RAG → Groq 70b → Gemini Flash
  Method B-Opt:         Stage 1 ∥ RAG → Groq 70b → Gemini Flash
"""
import asyncio
import json
import logging
import time
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from auth_deps import get_admin_user
from config import (
    _GEMINI_KEY,
)
from llm import _call_llm_raw, call_llm_api_chat
from prompts import classify_intent, INTENT_TO_DB_CATEGORY
from rag import resolve_rag_context

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_QUERIES = [
    {"query": "Explain the structure of DNA", "subject_name": "Biology", "intent_hint": "notes"},
    {"query": "Important questions for Economics chapter 1", "subject_name": "Economics", "intent_hint": "important_questions"},
    {"query": "Previous year questions of Physics 2024", "subject_name": "Physics", "intent_hint": "pyq"},
    {"query": "Hello, how are you?", "subject_name": None, "intent_hint": "casual"},
    {"query": "Define osmosis and diffusion", "subject_name": "Biology", "intent_hint": "notes"},
    {"query": "Waht is the meening of inflaton in ecnomics", "subject_name": "Economics", "intent_hint": "notes"},
    {"query": "Syllabus of Chemistry HS 2nd year", "subject_name": "Chemistry", "intent_hint": "syllabus"},
    {"query": "Solve 5 mark questions from Accountancy chapter 2", "subject_name": "Accountancy", "intent_hint": "pyq"},
]

STAGE1_SYSTEM_PROMPT = (
    "You are a query classifier for an Indian education platform. "
    "Given a student query, output ONLY valid JSON with these fields:\n"
    '{"topic":"<main topic>","chapter":"<chapter if mentioned else null>",'
    '"subject":"<subject if mentioned else null>",'
    '"intent":"<notes|important_questions|pyq|casual|syllabus|chapter_meta>",'
    '"search_keywords":["<kw1>","<kw2>","<kw3>"]}\n'
    "No explanation, no markdown, just the JSON object."
)

STAGE2_SYSTEM_PROMPT = (
    "You are a factual academic writer. Given RAG context chunks and a student query, "
    "write a concise, accurate draft answer using ONLY the provided context. "
    "Do not add information beyond what the context contains. "
    "If context is insufficient, say so briefly. No formatting instructions needed."
)

STAGE3_SYSTEM_PROMPT = (
    "You are Syra, a friendly AI study mentor for Indian students. "
    "Take the draft answer below and polish it into a clear, student-friendly response. "
    "Use markdown formatting where helpful (headings, bullets, bold for key terms). "
    "Add a brief example or analogy if it helps understanding. "
    "Keep the same factual content — do not add new facts."
)

_GEMINI_PROVIDER_LIST = [{"provider": "gemini", "key": _GEMINI_KEY, "default_model": "gemini-2.5-flash"}] if _GEMINI_KEY else []


class BenchmarkQuery(BaseModel):
    query: str
    subject_name: Optional[str] = None
    subject_id: Optional[str] = None
    intent_hint: Optional[str] = None


class BenchmarkRequest(BaseModel):
    queries: Optional[List[BenchmarkQuery]] = Field(
        default=None,
        description="Test queries. If empty, uses built-in defaults.",
    )
    max_tokens: int = Field(default=1024, ge=128, le=4096)


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


async def _timed_llm_call(messages: list, model: str, max_tokens: int, provider_list: list):
    t0 = time.perf_counter()
    result = await _call_llm_raw(messages, model, max_tokens, provider_list=provider_list)
    elapsed = _ms(t0)
    return result, elapsed


async def _safe_rag_retrieve(query, subject_id, subject_name, intent, db_category):
    try:
        return await resolve_rag_context(
            query,
            subject_id=subject_id,
            subject_name=subject_name,
            intent=intent or "notes",
            db_category=db_category,
        )
    except Exception as e:
        logger.warning(f"[benchmark] RAG retrieval failed: {e}")
        return {
            "chunks": [], "chapters": [], "subjects": [],
            "source": "none", "quality": "none",
            "error": f"{type(e).__name__}: {str(e)[:100]}",
        }


async def _run_method_a(query: str, subject_id: Optional[str], subject_name: Optional[str], max_tokens: int) -> dict:
    timings: dict = {}
    error = None

    try:
        t0 = time.perf_counter()
        intent, db_category = classify_intent(query)
        timings["intent_classification_ms"] = _ms(t0)
    except Exception as e:
        timings["intent_classification_ms"] = _ms(t0)
        intent, db_category = "notes", None
        error = f"Intent classification failed: {type(e).__name__}: {str(e)[:100]}"
        logger.warning(f"[benchmark] {error}")

    t0 = time.perf_counter()
    rag_ctx = await _safe_rag_retrieve(query, subject_id, subject_name, intent, db_category)
    timings["rag_retrieval_ms"] = _ms(t0)

    chunks = rag_ctx.get("chunks", [])
    rag_text = "\n".join(c.get("content", "")[:500] for c in chunks[:5]) if chunks else "(no RAG context)"

    system_prompt = (
        f"You are Syra, a helpful AI study mentor. "
        f"Intent: {intent}. Use the following context to answer:\n\n{rag_text}"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]

    t0 = time.perf_counter()
    try:
        answer = await call_llm_api_chat(messages, max_tokens=max_tokens)
    except Exception as e:
        answer = f"[ERROR: {type(e).__name__}: {str(e)[:100]}]"
        error = error or f"LLM generation failed: {type(e).__name__}: {str(e)[:100]}"
    llm_ms = _ms(t0)
    timings["llm_generation_ms"] = llm_ms
    timings["ttft_ms"] = round(
        timings["intent_classification_ms"] + timings["rag_retrieval_ms"] + llm_ms,
        1,
    )
    timings["total_ms"] = timings["ttft_ms"]

    result = {
        "method": "A (Current: regex + SmartKeyPool)",
        "intent_detected": intent,
        "rag_chunks_found": len(chunks),
        "rag_quality": rag_ctx.get("quality", "none"),
        "provider": "smart_key_pool",
        "model": "pool_selected",
        "stages": {
            "intent_classification": {
                "method": "regex",
                "time_ms": timings["intent_classification_ms"],
            },
            "rag_retrieval": {
                "time_ms": timings["rag_retrieval_ms"],
                "chunks": len(chunks),
            },
            "llm_generation": {
                "time_ms": llm_ms,
                "ttft_ms": llm_ms,
                "ttft_note": "non-streaming: TTFT equals full response time",
                "provider": "smart_key_pool",
            },
        },
        "timings": timings,
        "answer_length_chars": len(answer),
        "answer_preview": answer[:300],
    }
    if error:
        result["error"] = error
    return result


async def _run_method_b(
    query: str,
    subject_id: Optional[str],
    subject_name: Optional[str],
    max_tokens: int,
    parallel_stage1_rag: bool = False,
) -> dict:
    timings: dict = {}
    stages: dict = {}
    wall_t0 = time.perf_counter()
    error = None

    if not _GEMINI_KEY:
        return {
            "method": f"B {'Optimized' if parallel_stage1_rag else 'Sequential'} (Multi-LLM)",
            "error": "Missing API keys: GEMINI_API_KEY",
            "timings": {},
        }

    stage1_messages = [
        {"role": "system", "content": STAGE1_SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    async def _stage1():
        try:
            result, elapsed = await _timed_llm_call(
                stage1_messages, "@cf/meta/llama-3.3-70b-instruct-fp8-fast", 256, _GEMINI_PROVIDER_LIST,
            )
            try:
                cleaned = result.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
                parsed = json.loads(cleaned)
            except (json.JSONDecodeError, Exception):
                parsed = {"topic": query, "intent": "notes", "search_keywords": [query[:50]]}
            return parsed, elapsed, None
        except Exception as e:
            err_msg = f"Stage 1 failed: {type(e).__name__}: {str(e)[:100]}"
            logger.warning(f"[benchmark] {err_msg}")
            return {"topic": query, "intent": "notes", "search_keywords": [query[:50]]}, 0.0, err_msg

    async def _rag_retrieve(intent=None, db_category=None):
        t0 = time.perf_counter()
        rag_ctx = await _safe_rag_retrieve(query, subject_id, subject_name, intent, db_category)
        return rag_ctx, _ms(t0)

    if parallel_stage1_rag:
        (stage1_result, stage1_ms, s1_err), (rag_ctx, rag_ms) = await asyncio.gather(
            _stage1(),
            _rag_retrieve(intent="notes", db_category=None),
        )
        timings["stage1_topic_resolution_ms"] = stage1_ms
        timings["rag_retrieval_ms"] = rag_ms
        timings["stage1_rag_parallel_wall_ms"] = round(max(stage1_ms, rag_ms), 1)
        if s1_err:
            error = s1_err
    else:
        stage1_result, stage1_ms, s1_err = await _stage1()
        timings["stage1_topic_resolution_ms"] = stage1_ms
        if s1_err:
            error = s1_err

        s1_intent = stage1_result.get("intent", "notes")
        s1_db_cat = INTENT_TO_DB_CATEGORY.get(s1_intent)
        rag_ctx, rag_ms = await _rag_retrieve(intent=s1_intent, db_category=s1_db_cat)
        timings["rag_retrieval_ms"] = rag_ms

    stages["stage1_topic_resolution"] = {
        "provider": "cerebras",
        "model": "llama3.1-8b",
        "time_ms": stage1_ms,
        "ttft_ms": stage1_ms,
        "ttft_note": "non-streaming: TTFT equals full response time",
        "output": stage1_result,
    }
    if s1_err:
        stages["stage1_topic_resolution"]["error"] = s1_err

    stages["rag_retrieval"] = {
        "time_ms": rag_ms,
        "chunks": len(rag_ctx.get("chunks", [])),
        "quality": rag_ctx.get("quality", "none"),
    }

    chunks = rag_ctx.get("chunks", [])
    rag_text = "\n".join(c.get("content", "")[:500] for c in chunks[:5]) if chunks else "(no RAG context)"
    search_kw = ", ".join(stage1_result.get("search_keywords", []))

    stage2_user = (
        f"Student query: {query}\n\n"
        f"Topic: {stage1_result.get('topic', 'unknown')}\n"
        f"Keywords: {search_kw}\n\n"
        f"RAG Context:\n{rag_text}"
    )
    stage2_messages = [
        {"role": "system", "content": STAGE2_SYSTEM_PROMPT},
        {"role": "user", "content": stage2_user},
    ]

    try:
        draft, stage2_ms = await _timed_llm_call(
            stage2_messages, "@cf/openai/gpt-oss-120b", max_tokens, _GEMINI_PROVIDER_LIST,
        )
    except Exception as e:
        draft = f"[Stage 2 error: {type(e).__name__}]"
        stage2_ms = 0.0
        error = error or f"Stage 2 failed: {type(e).__name__}: {str(e)[:100]}"
        logger.warning(f"[benchmark] Stage 2 failed: {e}")

    timings["stage2_synthesis_ms"] = stage2_ms
    stages["stage2_synthesis"] = {
        "provider": "workers-ai",
        "model": "@cf/openai/gpt-oss-120b",
        "time_ms": stage2_ms,
        "ttft_ms": stage2_ms,
        "ttft_note": "non-streaming: TTFT equals full response time",
        "draft_length_chars": len(draft),
    }

    stage3_user = (
        f"Original student query: {query}\n\n"
        f"Draft answer to polish:\n{draft}"
    )
    stage3_messages = [
        {"role": "system", "content": STAGE3_SYSTEM_PROMPT},
        {"role": "user", "content": stage3_user},
    ]

    try:
        answer, stage3_ms = await _timed_llm_call(
            stage3_messages, "gemini-2.5-flash", max_tokens, _GEMINI_PROVIDER_LIST,
        )
    except Exception as e:
        answer = draft
        stage3_ms = 0.0
        error = error or f"Stage 3 failed: {type(e).__name__}: {str(e)[:100]}"
        logger.warning(f"[benchmark] Stage 3 failed: {e}")

    timings["stage3_polish_ms"] = stage3_ms
    stages["stage3_polish"] = {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "time_ms": stage3_ms,
        "ttft_ms": stage3_ms,
        "ttft_note": "non-streaming: TTFT equals full response time",
        "final_length_chars": len(answer),
    }

    total = _ms(wall_t0)
    timings["total_wall_ms"] = total

    if parallel_stage1_rag:
        timings["ttft_ms"] = round(
            timings["stage1_rag_parallel_wall_ms"] + stage2_ms + stage3_ms, 1,
        )
    else:
        timings["ttft_ms"] = round(
            stage1_ms + rag_ms + stage2_ms + stage3_ms, 1,
        )

    method_label = "B-Opt (Multi-LLM, Stage1∥RAG)" if parallel_stage1_rag else "B (Multi-LLM, Sequential)"

    result = {
        "method": method_label,
        "intent_detected": stage1_result.get("intent", "unknown"),
        "rag_chunks_found": len(chunks),
        "rag_quality": rag_ctx.get("quality", "none"),
        "stages": stages,
        "timings": timings,
        "answer_length_chars": len(answer),
        "answer_preview": answer[:300],
    }
    if error:
        result["error"] = error
    return result


@router.post("/admin/ai/benchmark")
async def admin_ai_benchmark(
    req: BenchmarkRequest,
    admin: dict = Depends(get_admin_user),
):
    queries = req.queries or [BenchmarkQuery(**q) for q in DEFAULT_QUERIES]
    results = []

    providers_status = {
        "gemini": {"available": bool(_GEMINI_KEY), "model": "gemini-2.5-flash"},
        "smart_key_pool": {"available": True, "model": "pool_selected"},
    }

    multi_llm_possible = bool(_GEMINI_KEY)

    for q in queries:
        query_result = {"query": q.query, "subject_name": q.subject_name, "methods": {}}

        try:
            method_a = await _run_method_a(q.query, q.subject_id, q.subject_name, req.max_tokens)
        except Exception as e:
            method_a = {
                "method": "A (Current: regex + SmartKeyPool)",
                "error": f"{type(e).__name__}: {str(e)[:200]}",
                "timings": {},
            }
            logger.error(f"[benchmark] Method A failed for '{q.query[:40]}': {e}")
        query_result["methods"]["A"] = method_a

        if multi_llm_possible:
            try:
                method_b = await _run_method_b(
                    q.query, q.subject_id, q.subject_name, req.max_tokens, parallel_stage1_rag=False,
                )
            except Exception as e:
                method_b = {
                    "method": "B (Multi-LLM, Sequential)",
                    "error": f"{type(e).__name__}: {str(e)[:200]}",
                    "timings": {},
                }
                logger.error(f"[benchmark] Method B failed for '{q.query[:40]}': {e}")
            query_result["methods"]["B"] = method_b

            try:
                method_b_opt = await _run_method_b(
                    q.query, q.subject_id, q.subject_name, req.max_tokens, parallel_stage1_rag=True,
                )
            except Exception as e:
                method_b_opt = {
                    "method": "B-Opt (Multi-LLM, Stage1∥RAG)",
                    "error": f"{type(e).__name__}: {str(e)[:200]}",
                    "timings": {},
                }
                logger.error(f"[benchmark] Method B-Opt failed for '{q.query[:40]}': {e}")
            query_result["methods"]["B_optimized"] = method_b_opt

            a_total = method_a.get("timings", {}).get("total_ms", 0)
            b_total = method_b.get("timings", {}).get("total_wall_ms", 0)
            b_opt_total = method_b_opt.get("timings", {}).get("total_wall_ms", 0)
            a_ttft = method_a.get("timings", {}).get("ttft_ms", 0)
            b_ttft = method_b.get("timings", {}).get("ttft_ms", 0)
            b_opt_ttft = method_b_opt.get("timings", {}).get("ttft_ms", 0)

            query_result["comparison"] = {
                "method_a_total_ms": a_total,
                "method_b_total_ms": b_total,
                "method_b_opt_total_ms": b_opt_total,
                "method_a_ttft_ms": a_ttft,
                "method_b_ttft_ms": b_ttft,
                "method_b_opt_ttft_ms": b_opt_ttft,
                "b_vs_a_diff_ms": round(b_total - a_total, 1),
                "b_opt_vs_a_diff_ms": round(b_opt_total - a_total, 1),
                "b_opt_savings_vs_b_ms": round(b_total - b_opt_total, 1),
                "a_answer_chars": method_a.get("answer_length_chars", 0),
                "b_answer_chars": method_b.get("answer_length_chars", 0),
                "b_opt_answer_chars": method_b_opt.get("answer_length_chars", 0),
            }
        else:
            query_result["methods"]["B"] = {
                "error": "Multi-LLM pipeline requires CEREBRAS_API_KEY, GROQ_API_KEY, and GEMINI_API_KEY",
            }

        results.append(query_result)

    summary = _build_summary(results, multi_llm_possible)

    return {
        "benchmark": "single-LLM vs multi-LLM pipeline",
        "ttft_note": "Non-streaming benchmark: TTFT (time to first token) equals total response time since all tokens arrive at once.",
        "providers": providers_status,
        "multi_llm_available": multi_llm_possible,
        "query_count": len(queries),
        "summary": summary,
        "results": results,
    }


def _build_summary(results: list, multi_llm: bool) -> dict:
    a_times = [
        r["methods"]["A"]["timings"].get("total_ms", 0)
        for r in results
        if "A" in r["methods"] and r["methods"]["A"].get("timings")
    ]
    a_ttfts = [
        r["methods"]["A"]["timings"].get("ttft_ms", 0)
        for r in results
        if "A" in r["methods"] and r["methods"]["A"].get("timings")
    ]
    summary = {
        "method_a_avg_ms": round(sum(a_times) / max(len(a_times), 1), 1),
        "method_a_min_ms": round(min(a_times) if a_times else 0, 1),
        "method_a_max_ms": round(max(a_times) if a_times else 0, 1),
        "method_a_avg_ttft_ms": round(sum(a_ttfts) / max(len(a_ttfts), 1), 1),
    }

    if multi_llm:
        b_times = [
            r["methods"]["B"]["timings"].get("total_wall_ms", 0)
            for r in results
            if "B" in r["methods"] and r["methods"]["B"].get("timings")
        ]
        b_ttfts = [
            r["methods"]["B"]["timings"].get("ttft_ms", 0)
            for r in results
            if "B" in r["methods"] and r["methods"]["B"].get("timings")
        ]
        b_opt_times = [
            r["methods"]["B_optimized"]["timings"].get("total_wall_ms", 0)
            for r in results
            if "B_optimized" in r["methods"] and r["methods"]["B_optimized"].get("timings")
        ]
        b_opt_ttfts = [
            r["methods"]["B_optimized"]["timings"].get("ttft_ms", 0)
            for r in results
            if "B_optimized" in r["methods"] and r["methods"]["B_optimized"].get("timings")
        ]
        if b_times:
            summary["method_b_avg_ms"] = round(sum(b_times) / len(b_times), 1)
            summary["method_b_min_ms"] = round(min(b_times), 1)
            summary["method_b_max_ms"] = round(max(b_times), 1)
        if b_ttfts:
            summary["method_b_avg_ttft_ms"] = round(sum(b_ttfts) / len(b_ttfts), 1)
        if b_opt_times:
            summary["method_b_opt_avg_ms"] = round(sum(b_opt_times) / len(b_opt_times), 1)
            summary["method_b_opt_min_ms"] = round(min(b_opt_times), 1)
            summary["method_b_opt_max_ms"] = round(max(b_opt_times), 1)
        if b_opt_ttfts:
            summary["method_b_opt_avg_ttft_ms"] = round(sum(b_opt_ttfts) / len(b_opt_ttfts), 1)
        if a_times and b_times:
            a_avg = summary["method_a_avg_ms"]
            b_avg = summary["method_b_avg_ms"]
            b_opt_avg = summary.get("method_b_opt_avg_ms", b_avg)
            summary["b_vs_a_overhead_ms"] = round(b_avg - a_avg, 1)
            summary["b_vs_a_overhead_pct"] = round(((b_avg - a_avg) / max(a_avg, 1)) * 100, 1)
            summary["b_opt_vs_a_overhead_ms"] = round(b_opt_avg - a_avg, 1)
            summary["b_opt_vs_a_overhead_pct"] = round(((b_opt_avg - a_avg) / max(a_avg, 1)) * 100, 1)
            summary["parallel_savings_ms"] = round(b_avg - b_opt_avg, 1)

    return summary
