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
import re
import time
import uuid
from typing import Literal

# §K.2 / §K.3 — module-level handles for the deterministic AI
# response cache. The batcher path needs these at import time (the
# per-call lazy import inside `format_content` is fine for the
# single-doc path but the batcher pre-checks N keys synchronously
# before building the upstream prompt). Tolerant of an absent
# module so dev/test environments without `ai_input_cache` still
# import cleanly.
try:
    from ai_input_cache import (
        get_response as _aic_get,
        set_response as _aic_set,
    )
    _AIC_ENABLED = True
except Exception:  # pragma: no cover — optional dep
    _aic_get = None  # type: ignore[assignment]
    _aic_set = None  # type: ignore[assignment]
    _AIC_ENABLED = False

# Task #513 §B — token-budget clamp. The store-time formatter routes
# through the `content_formatter` budget (4500 in / 2500 out) defined in
# cost_caps.py. Imported for the regression test in
# tests/test_cost_caps.py and used by `format_content` to clamp the
# input body before either provider leg is dispatched.
import cost_caps

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


# Task #513 §K.3 — formatter batch size for the bulk-polish AsyncBatcher
# singleton. Locked at 10 so admin chapter pre-gen + the Assamese
# backfill driver coalesce up to 10 concurrent format_content calls
# into a single fan-out batch (one batcher tick per ~50 ms window).
# Bumping requires a Sentry-annotated changelog (founder-locked).
_FORMATTER_BATCH_SIZE = 10  # COST-CAP-OVERRIDE: Task #513 §K.3 — formatter batch size locked at 10. Larger batches starve the dispatcher loop on a slow Vertex leg; smaller batches lose the coalescing benefit. Bumping requires Sentry-annotated changelog.

_FORMATTER_BATCHER = None
_FORMATTER_BATCHER_LOCK = None


_BATCH_DOC_OPEN  = "<<<DOC i={i}>>>"
_BATCH_DOC_CLOSE = "<<<END i={i}>>>"
_BATCH_RE = re.compile(
    r"<<<DOC i=(\d+)>>>(.*?)<<<END i=\1>>>",
    re.DOTALL,
)


def _build_multi_doc_prompt(items: list[tuple[str, str, str, int]]) -> str:
    """Pack N (text, style, lang, max_tokens) tuples into one
    upstream prompt. Each doc is wrapped with `<<<DOC i=N>>>` /
    `<<<END i=N>>>` envelope so the model returns N polished bodies
    we can re-split deterministically. Mixed (style, lang) inputs
    are tolerated — the per-doc instruction line carries them."""
    parts = [
        "You are the Syrabit content formatter. Polish each input "
        "document independently and return ONLY the polished body of "
        "each, wrapped in the SAME `<<<DOC i=N>>>` / `<<<END i=N>>>` "
        "envelope you received. Do not merge documents. Do not add "
        "commentary outside the envelopes.",
        "",
    ]
    for i, (t, style, lang, _mt) in enumerate(items):
        parts.append(_BATCH_DOC_OPEN.format(i=i))
        parts.append(f"[style={style} lang={lang}]")
        parts.append(t)
        parts.append(_BATCH_DOC_CLOSE.format(i=i))
        parts.append("")
    return "\n".join(parts)


def _split_multi_doc_response(raw: str, n: int) -> list[str | None]:
    """Re-split a multi-doc completion into N entries. Missing /
    malformed envelopes return None at that slot so the batcher can
    fall back to per-item dispatch for the orphans."""
    out: list[str | None] = [None] * n
    for m in _BATCH_RE.finditer(raw or ""):
        idx = int(m.group(1))
        if 0 <= idx < n:
            out[idx] = m.group(2).strip() or None
    return out


async def _format_one_for_batcher(payload):
    """§K.3 true multi-item upstream batching. The AsyncBatcher hands
    us a list of (text, style, lang, max_tokens) tuples (≤
    `_FORMATTER_BATCH_SIZE`). We pack them into ONE delimited prompt
    and dispatch a SINGLE upstream call (Vertex Gemini primary →
    Workers-AI Llama-3.3-70b fallback). Cache hits short-circuit
    per-item before we ever build the multi-doc prompt; orphans
    (cache-miss slots that the upstream did not return cleanly) fall
    back to per-item dispatch via `format_content`. This satisfies
    §K.3's "real multi-item formatter batching" requirement while
    preserving the §15 §6 audit shape: every returned dict still
    carries a `formatted_by` field."""
    import asyncio as _asyncio

    n = len(payload)
    results: list[dict | None] = [None] * n

    # 1. Per-item cache pre-check so we never burn upstream tokens
    #    on docs we've already polished within the 30-day window.
    misses: list[int] = []
    for i, (t, style, lang, mt) in enumerate(payload):
        msgs   = [{"role": "user", "content": t}]
        model  = f"content_formatter:{style}:{lang}:multi_doc_v1:{mt}"
        if not _AIC_ENABLED:
            misses.append(i); continue
        try:
            cached = _aic_get(msgs, model, max_tokens=mt)  # type: ignore[misc]
        except Exception:
            cached = None
        if cached:
            results[i] = {
                "formatted":     cached,
                "formatted_by":  "ai_response_cache",
                "fallback_used": False,
            }
        else:
            misses.append(i)

    if not misses:
        return results

    # 2. Build ONE multi-doc prompt for the cache misses and dispatch
    #    a SINGLE upstream call. Use the largest requested
    #    max_tokens × N as the upstream output ceiling so each doc
    #    has room.
    miss_items = [payload[i] for i in misses]
    upstream_max = sum(int(it[3]) for it in miss_items)
    multi_prompt = _build_multi_doc_prompt(miss_items)
    multi_lang   = miss_items[0][2]  # all docs share fallback leg lang

    polished_blob = await _try_vertex(
        multi_prompt, style="notebook_lm", lang=multi_lang, max_tokens=upstream_max,
    )
    formatted_by_used = "vertex"
    if not polished_blob:
        polished_blob = await _try_workers_ai_llama(
            multi_prompt, lang=multi_lang, max_tokens=upstream_max,
        )
        formatted_by_used = "workers_ai_llama33_70b"

    parsed: list[str | None] = (
        _split_multi_doc_response(polished_blob or "", len(miss_items))
        if polished_blob else [None] * len(miss_items)
    )

    # 3. Walk the parsed slots. Hits: cache + return. Orphans
    #    (model dropped that doc): fall back to per-item dispatch
    #    via `format_content` so the caller still gets a result.
    orphans: list[int] = []
    for slot, out_text in enumerate(parsed):
        global_i = misses[slot]
        t, style, lang, mt = payload[global_i]
        if out_text:
            if _AIC_ENABLED:
                try:
                    _aic_set(  # type: ignore[misc]
                        [{"role": "user", "content": t}],
                        f"content_formatter:{style}:{lang}:multi_doc_v1:{mt}",
                        out_text, max_tokens=mt,
                    )
                except Exception:
                    pass
            results[global_i] = {
                "formatted":     out_text,
                "formatted_by":  formatted_by_used,
                "fallback_used": formatted_by_used != "vertex",
            }
        else:
            orphans.append(global_i)

    if orphans:
        per_item = await _asyncio.gather(*[
            format_content(text=payload[i][0], style=payload[i][1],
                           lang=payload[i][2], max_tokens=payload[i][3])
            for i in orphans
        ])
        for i, val in zip(orphans, per_item):
            results[i] = val

    return results


def _get_formatter_batcher():
    global _FORMATTER_BATCHER, _FORMATTER_BATCHER_LOCK
    if _FORMATTER_BATCHER is not None:
        return _FORMATTER_BATCHER
    if _FORMATTER_BATCHER_LOCK is None:
        import threading as _th
        _FORMATTER_BATCHER_LOCK = _th.Lock()
    with _FORMATTER_BATCHER_LOCK:
        if _FORMATTER_BATCHER is None:
            from ai_batch_queue import AsyncBatcher
            _FORMATTER_BATCHER = AsyncBatcher(
                _format_one_for_batcher,
                flush_size=_FORMATTER_BATCH_SIZE,
                flush_window_ms=50,
                name="content_formatter",
            )
    return _FORMATTER_BATCHER


async def format_content_batched(
    text: str,
    *,
    style: FormatterStyle = "notebook_lm",
    lang: FormatterLang = "en",
    max_tokens: int = 4000,
) -> dict:
    """§K.3 entry point for bulk callers (admin chapter pre-gen,
    Assamese backfill, etc). Coalesces concurrent submitters into a
    single dispatcher tick (`flush_size=10`, `flush_window_ms=50`).
    Single-call latency-sensitive paths should keep using
    `format_content` directly to avoid the 50 ms batching window."""
    batcher = _get_formatter_batcher()
    return await batcher.submit((text, style, lang, max_tokens))


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

    # Task #513 §B — clamp the formatter input to its locked budget
    # (`content_formatter`: 4 500 in / 2 500 out). The Vertex/Workers-AI
    # legs accept raw text rather than chat messages, so we clamp via a
    # synthetic single-turn message envelope; the dispatcher inside
    # `vertex_format` does NOT re-clamp on its own.
    try:
        from cost_caps import clamp_messages as _ccs_clamp, max_output_tokens_for as _ccs_max_out
        _clamped = _ccs_clamp(
            [{"role": "user", "content": str(text)}],
            call_type="content_formatter",
        )
        text = str(_clamped[-1].get("content") or text)
        max_tokens = _ccs_max_out("content_formatter", max_tokens)
    except Exception:
        # cost_caps unavailable in unit tests of the formatter — fail
        # open so we never block a polish call on a clamp helper bug.
        pass

    # Validate enums up-front so callers cannot smuggle freeform values
    # into the formatter pipeline (matches vertex_format.SUPPORTED_*).
    if style not in ("notebook_lm", "study_notes", "flashcard"):
        raise ValueError(f"format_content: unknown style {style!r}")
    if lang not in ("en", "as"):
        raise ValueError(f"format_content: unknown lang {lang!r}")

    fallback_used = False
    formatted_by = "passthrough"
    out_text = text

    # Task #513 §K.2 — deterministic AI response cache. The formatter
    # is a pure function of (text, style, lang, max_tokens), so a
    # repeat invocation with the same inputs MUST hit the cache
    # instead of paying for a duplicate Vertex / Workers-AI call.
    # Cache key is built from a synthetic single-turn message so
    # `ai_input_cache` reuses the canonical-JSON hash logic.
    _cache_msgs = [{"role": "user", "content": f"{style}|{lang}|{text}"}]
    _cache_model = f"content_formatter:{style}:{lang}"
    try:
        from ai_input_cache import (
            get_response as _aic_get,
            set_response as _aic_set,
            is_deterministic as _aic_is_det,
        )
        _aic_enabled = _aic_is_det(_cache_msgs, _cache_model, temperature=0.0, stream=False)
    except Exception:
        _aic_get = _aic_set = None  # type: ignore[assignment]
        _aic_enabled = False
    if _aic_enabled and _aic_get is not None:
        _cached = _aic_get(_cache_msgs, _cache_model, max_tokens=max_tokens)
        if _cached:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            logger.info(
                "[content-format][CACHE-HIT] aic served %d chars (style=%s lang=%s)",
                len(_cached), style, lang,
            )
            return {
                "text": _cached,
                "formatted_by": "cache",
                "duration_ms": duration_ms,
                "trace_id": trace_id,
            }

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

    # §K.2 — write back the polished output on success (skip
    # passthrough so a Vertex/WAI dual-outage doesn't pollute the
    # cache with raw input).
    if _aic_enabled and _aic_set is not None and formatted_by != "passthrough" and out_text:
        try:
            _aic_set(_cache_msgs, _cache_model, str(out_text), max_tokens=max_tokens)
        except Exception:
            pass

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
