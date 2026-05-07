"""assamese_dispatch — Task #581 §L6 Assamese chat optimization.

Sarvam Bhasha is the locked Assamese-chat primary, but it's also the
single most expensive provider per token in the chain. Most Assamese
turns are repeats of the same exam-prep questions ("define X",
"explain Y") and can be served from caches without a Sarvam round-trip.

This module implements the §L6 three-step gate that runs BEFORE the
Sarvam dispatch:

  1. ``translation_cache_lookup`` — exact-Assamese-prompt cache hit.
  2. ``cached_explanation_lookup`` — English explanation in
     ``ai_input_cache`` + on-the-fly translate via Workers-AI IndicTrans2.
     ~10x cheaper than a Sarvam reasoning call.
  3. ``needs_reasoning(text)`` classifier — if the prompt does NOT
     require multi-step reasoning, route to a tighter Sarvam call
     (smaller output budget). Heuristic-only; no model call.

If all three short-circuits miss, the caller proceeds with the
existing Sarvam strict-chain dispatch (Sarvam → workers_ai_indic).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Heuristic: prompts that almost always need reasoning carry one of
# these markers. Anything else can be answered with the cached/short
# tier. Tuned against the top-5k Assamese chat prompts in the past
# 30 days (admin analytics).
_REASONING_MARKERS = (
    # Assamese
    "কিয়", "কেনেকৈ", "ব্যাখ্যা", "তুলনা", "প্ৰমাণ", "যুক্তি", "বিশ্লেষণ",
    # Romanized
    "kio", "kenekoi", "byakhya", "tulona", "proman", "yukti",
    # English exam-prep verbs that often appear inside Assamese prompts
    "explain", "compare", "derive", "prove", "why", "how does",
    "analyse", "analyze", "differentiate", "discuss",
)

_DEFINE_MARKERS = (
    "সংজ্ঞা", "সংজ্ঞা দিয়া", "কি", "কী",
    "define", "what is", "what are", "name", "list",
)


def needs_reasoning(text: str) -> bool:
    """True iff the prompt likely requires multi-step Sarvam reasoning.

    Pure heuristic — no model call. False positives are fine (we just
    spend a normal Sarvam call), false negatives degrade UX (we send
    a definition prompt to a tight call and it gets truncated). We
    therefore lean conservative and treat any prompt over 240 chars OR
    containing a reasoning marker as needs_reasoning=True.
    """
    if not text:
        return False
    s = text.strip().lower()
    if len(s) > 240:
        return True
    for marker in _REASONING_MARKERS:
        if marker.lower() in s:
            return True
    return False


def is_simple_definition(text: str) -> bool:
    """True iff the prompt looks like a one-liner definition / list /
    factual question — perfect for the cached_explanation tier.
    """
    if not text:
        return False
    s = text.strip().lower()
    if len(s) > 200:
        return False
    if needs_reasoning(s):
        return False
    return any(m.lower() in s for m in _DEFINE_MARKERS) or len(s) < 60


async def translation_cache_lookup(prompt_as: str) -> Optional[str]:
    """Step 1: exact-Assamese-prompt cache hit. Best-effort."""
    try:
        from ai_input_cache import get_response  # type: ignore
    except Exception:
        return None
    try:
        return get_response(
            content_type="as_chat",
            template_version="as_dispatch_v1",
            prompt=prompt_as,
            model="sarvam-m",
            max_tokens=0,
        )
    except Exception:
        return None


async def cached_explanation_lookup(prompt_as: str) -> Optional[str]:
    """Step 2: English explanation in cache + on-the-fly translate.

    Returns translated Assamese answer or None on miss / translate
    failure. Translation uses Workers-AI IndicTrans2 (the strict
    Assamese fallback head — same chain shape, no provider drift).
    """
    try:
        from ai_input_cache import get_response  # type: ignore
    except Exception:
        return None
    try:
        en_prompt = await _translate_as_to_en(prompt_as)
    except Exception:
        en_prompt = None
    if not en_prompt:
        return None
    try:
        cached_en = get_response(
            content_type="explanation",
            template_version="retrieval_first_v1",
            prompt=en_prompt,
            model="*",
            max_tokens=0,
        )
    except Exception:
        cached_en = None
    if not cached_en:
        return None
    try:
        return await _translate_en_to_as(cached_en)
    except Exception:
        return None


async def _translate_as_to_en(text: str) -> Optional[str]:
    try:
        from providers import workers_ai_indic  # type: ignore
        return await workers_ai_indic.translate(text, src="as", dst="en")  # type: ignore[attr-defined]
    except Exception:
        return None


async def _translate_en_to_as(text: str) -> Optional[str]:
    try:
        from providers import workers_ai_indic  # type: ignore
        return await workers_ai_indic.translate(text, src="en", dst="as")  # type: ignore[attr-defined]
    except Exception:
        return None


__all__ = [
    "needs_reasoning",
    "is_simple_definition",
    "translation_cache_lookup",
    "cached_explanation_lookup",
]
