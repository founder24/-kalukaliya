"""content_formatter — Task #494 dispatcher for store-time content polish.

V4 §15 amendment §6 (founder lock 2026-05-06):

    content generation = Workers-AI
    content formatting = Vertex Gemini 2.5 Flash
    chat               = Azure (English) / Sarvam (Assamese)

`format_content(text, *, style, lang)` is the *single chokepoint* for
running already-generated long-form notes (notes, chapter content, study
plans, summaries) through the NotebookLM-style polish pass. The dispatcher
guarantees the policy contract above:

  1. Vertex Gemini 2.5 Flash is the primary formatter
     (`vertex_format.format_with_vertex`).
  2. On Vertex 5xx / timeout / breaker-open, fall back ONCE to Workers-AI
     Llama-3.3-70b (the only allowed formatter fallback per the §15 amendment
     — no third-party formatter is added). The same NotebookLM-style system
     prompt is used so the output shape is stable across providers.
  3. Vertex MUST NOT silently English-ify Assamese content. When ``lang="as"``
     the formatter output is run through ``lang_sanitizer.measure_leakage``
     and rejected (passthrough of the original Workers-AI input) when
     ``ratio > ASSAMESE_LEAK_THRESHOLD`` or no Assamese script is present.

Return shape (frozen by `tests/test_content_formatter_dispatch.py`):

    {
        "text":         str,                                 # final text
        "formatted_by": "vertex" | "workers_ai_llama33_70b" | "passthrough",
        "duration_ms":  int,
        "trace_id":     str,                                 # uuid4 hex
    }

Failure mode: this dispatcher NEVER raises. If both Vertex and the
Workers-AI fallback fail (or are misconfigured), the original input is
returned with ``formatted_by="passthrough"`` and the failure is logged +
emitted as a Sentry tag. Callers are expected to persist the
``formatted_by`` value alongside the text so an operator audit can
distinguish polished from raw content.

The dispatcher is store-time only. Streaming chat responses MUST NOT call
this helper — the formatter is for the post-stream "save to notes" / batch
content-generation paths only (V4 §15 §6).
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Literal

logger = logging.getLogger(__name__)

FormatterStyle = Literal["notebook_lm", "study_notes", "flashcard"]
FormatterLang = Literal["en", "as"]

_FALLBACK_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"

_NOTEBOOK_LM_SYSTEM_EN = (
    "You are a senior academic editor producing NotebookLM-style study notes. "
    "Polish the user's raw notes into clean Markdown: ## for topic headings, "
    "### for sub-points, **bold** for every key term and definition on first "
    "mention, 4-6 tightened bullets per topic, no redundancy, no filler. "
    "Preserve the original topic structure exactly — do NOT add new topics, "
    "do NOT drop topics, do NOT translate. End with one final '## Key "
    "Takeaways' section containing 5-7 exam-critical bullets. Return ONLY "
    "the polished Markdown — no commentary, no preamble, no disclaimers."
)
_NOTEBOOK_LM_SYSTEM_AS = (
    "You are an Assamese-fluent academic editor producing NotebookLM-style "
    "study notes. The user's raw notes are in Assamese (অসমীয়া). Output MUST "
    "stay in Assamese script throughout — do NOT translate to English. "
    "Polish into clean Markdown: ## for topic headings, ### for sub-points, "
    "**bold** for every key term and definition on first mention, 4-6 "
    "tightened bullets per topic, no redundancy. Preserve the original topic "
    "structure exactly. End with '## মূল কথাবোৰ' (Key Takeaways) containing "
    "5-7 exam-critical bullets. Return ONLY the polished Markdown — no "
    "English commentary, no preamble, no disclaimers."
)


def _emit_sentry_span(
    *,
    duration_ms: int,
    primary: str,
    fallback_used: bool,
    formatted_by: str,
    trace_id: str,
    lang: str,
    style: str,
) -> None:
    """Best-effort Sentry tag emission so the new content_format panel can
    plot vertex-success / wai-fallback / passthrough counts and p50/p95
    duration. Never raises."""
    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.set_tag("service", "content_format")
        sentry_sdk.set_tag("content_format.primary", primary)
        sentry_sdk.set_tag("content_format.fallback_used", str(fallback_used).lower())
        sentry_sdk.set_tag("content_format.formatted_by", formatted_by)
        sentry_sdk.set_tag("content_format.lang", lang)
        sentry_sdk.set_tag("content_format.style", style)
        sentry_sdk.set_tag("content_format.trace_id", trace_id)
        with sentry_sdk.start_span(op="content_format", description=formatted_by) as span:
            span.set_data("duration_ms", duration_ms)
    except Exception:
        pass


_RECENT_INVOCATIONS: list[dict] = []
_RECENT_INVOCATIONS_MAX = 100


def _record_invocation(rec: dict) -> None:
    """Keep an in-process ring of the last N invocations so
    ``routes/admin_health.py`` can surface a content_formatter row."""
    _RECENT_INVOCATIONS.append(rec)
    if len(_RECENT_INVOCATIONS) > _RECENT_INVOCATIONS_MAX:
        del _RECENT_INVOCATIONS[: len(_RECENT_INVOCATIONS) - _RECENT_INVOCATIONS_MAX]


def get_recent_invocations() -> list[dict]:
    """Defensive copy of the last-N invocation log. Surfaced via
    /admin/system-health → llm_providers.content_formatter."""
    return list(_RECENT_INVOCATIONS)


def get_recent_breakdown() -> dict:
    """Counts of the last-N invocations by formatted_by — used by the
    /admin/system-health content_formatter panel."""
    counts = {"vertex": 0, "workers_ai_llama33_70b": 0, "passthrough": 0}
    for rec in _RECENT_INVOCATIONS:
        fb = rec.get("formatted_by")
        if fb in counts:
            counts[fb] += 1
    return {
        "window": len(_RECENT_INVOCATIONS),
        "max_window": _RECENT_INVOCATIONS_MAX,
        "by_formatter": counts,
    }


def _select_system_prompt(lang: str) -> str:
    return _NOTEBOOK_LM_SYSTEM_AS if lang == "as" else _NOTEBOOK_LM_SYSTEM_EN


def _assamese_purity_ok(text: str) -> tuple[bool, dict]:
    """True iff the formatter output preserved Assamese script within the
    configured leak threshold. Used to gate ``lang="as"`` polish results so
    Vertex / Workers-AI cannot silently English-ify Assamese content."""
    try:
        from lang_sanitizer import measure_leakage, get_threshold

        diag = measure_leakage(text)
        thr = get_threshold()
        if not diag.get("has_assamese"):
            return False, {"reason": "no_assamese_script", "threshold": thr, **diag}
        ok = float(diag.get("ratio", 0.0)) <= float(thr)
        return ok, {"reason": "ok" if ok else "ratio_above_threshold", "threshold": thr, **diag}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[content-format] purity probe failed: %s — accepting output", exc)
        return True, {"reason": "purity_probe_failed", "error": str(exc)}


async def _try_vertex(text: str, *, style: str, lang: str, max_tokens: int) -> str | None:
    """Primary leg — Vertex Gemini 2.5 Flash. Returns polished text or None
    on failure (caller advances to the WAI fallback)."""
    try:
        from vertex_format import format_with_vertex
    except Exception as exc:
        logger.warning("[content-format] vertex_format import failed: %s", exc)
        return None
    try:
        return await format_with_vertex(
            text, style=style, lang=lang, max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning(
            "[content-format] vertex leg failed (%s) — advancing to "
            "workers_ai_llama33_70b fallback",
            type(exc).__name__,
        )
        return None


async def _try_workers_ai_llama(text: str, *, lang: str, max_tokens: int) -> str | None:
    """Fallback leg — Workers-AI Llama-3.3-70b. Returns polished text or
    None on failure (caller passes through the original input)."""
    system_prompt = _select_system_prompt(lang)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]
    try:
        from providers.cloudflare_ai import chat as _cf_chat
    except Exception as exc:
        logger.warning("[content-format] cloudflare_ai import failed: %s", exc)
        return None
    try:
        out = await _cf_chat(
            messages,
            model_key=_FALLBACK_MODEL,
            max_tokens=max_tokens,
            temperature=0.1,
        )
        return (out or "").strip() or None
    except Exception as exc:
        logger.warning(
            "[content-format] workers_ai_llama33_70b leg failed (%s) — "
            "passthrough of original input",
            type(exc).__name__,
        )
        return None


async def format_content(
    text: str,
    *,
    style: FormatterStyle = "notebook_lm",
    lang: FormatterLang = "en",
    max_tokens: int = 4000,
) -> dict:
    """Polish ``text`` via Vertex Gemini 2.5 Flash → Workers-AI Llama-3.3-70b
    fallback. See module docstring for the full contract.

    Never raises. Returns a dict with the polished text, the audit field
    ``formatted_by``, the wallclock duration, and a uuid4 trace id.
    """
    trace_id = uuid.uuid4().hex[:12]
    t0 = time.perf_counter()

    if not text or not str(text).strip():
        return {
            "text": text or "",
            "formatted_by": "passthrough",
            "duration_ms": 0,
            "trace_id": trace_id,
        }

    # Validate enums up-front so callers cannot smuggle freeform values
    # into the formatter pipeline (matches vertex_format.SUPPORTED_*).
    if style not in ("notebook_lm", "study_notes", "flashcard"):
        raise ValueError(f"format_content: unknown style {style!r}")
    if lang not in ("en", "as"):
        raise ValueError(f"format_content: unknown lang {lang!r}")

    fallback_used = False
    formatted_by = "passthrough"
    out_text = text

    polished = await _try_vertex(text, style=style, lang=lang, max_tokens=max_tokens)
    if polished:
        formatted_by = "vertex"
        out_text = polished
    else:
        fallback_used = True
        polished = await _try_workers_ai_llama(text, lang=lang, max_tokens=max_tokens)
        if polished:
            formatted_by = "workers_ai_llama33_70b"
            out_text = polished

    # Assamese purity gate — applies to BOTH Vertex and WAI legs. If the
    # polished output dropped Assamese script the polish is rejected and we
    # passthrough the original Workers-AI / IndicTrans2 input. Never silently
    # ship English content under the Assamese audit label.
    purity_diag: dict = {}
    if formatted_by != "passthrough" and lang == "as":
        ok, purity_diag = _assamese_purity_ok(out_text)
        if not ok:
            logger.warning(
                "[content-format] assamese purity rejected %s output "
                "(reason=%s ratio=%.3f threshold=%.3f) — passthrough",
                formatted_by,
                purity_diag.get("reason"),
                float(purity_diag.get("ratio", 0.0)),
                float(purity_diag.get("threshold", 0.0)),
            )
            formatted_by = "passthrough"
            out_text = text

    duration_ms = int((time.perf_counter() - t0) * 1000)
    rec = {
        "trace_id": trace_id,
        "ts": time.time(),
        "lang": lang,
        "style": style,
        "formatted_by": formatted_by,
        "fallback_used": fallback_used,
        "duration_ms": duration_ms,
        "purity": purity_diag or None,
    }
    _record_invocation(rec)
    _emit_sentry_span(
        duration_ms=duration_ms,
        primary="vertex",
        fallback_used=fallback_used,
        formatted_by=formatted_by,
        trace_id=trace_id,
        lang=lang,
        style=style,
    )
    return {
        "text": out_text,
        "formatted_by": formatted_by,
        "duration_ms": duration_ms,
        "trace_id": trace_id,
    }
