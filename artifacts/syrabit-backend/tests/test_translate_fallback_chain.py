"""Task #554 / Task #552 §G-R — translate dispatch fallback chain.

Task #554 retired the legacy ``providers/azure_openai.py`` module. Task
#552 §G-R subsequently retired the surviving Azure Speech + Translator
surfaces, so the translate dispatch chain is now
``workers_ai_indic → workers_ai`` (no Azure leg).

  1. Pool is multi-leg so dispatch can advance on outage.
  2. Azure providers are absent from both the pool and the weights.
  3. Sarvam is never selected.
"""
from unittest.mock import patch, AsyncMock
import pytest


def test_translate_pool_is_multi_leg_no_sarvam_no_azure():
    from config import PROVIDER_PRIORITY, POOL_WEIGHTS

    pool = PROVIDER_PRIORITY["translate"]
    assert pool[0] == "workers_ai_indic", "IndicTrans2 must remain primary"
    assert "sarvam" not in pool
    assert "vertex" not in pool
    # Task #554 — legacy azure_openai must not appear in translate pool.
    assert "azure_openai" not in pool
    # Task #552 §G-R — Azure Translator is fully retired.
    assert "azure_translator" not in pool
    assert len(pool) >= 2

    weights = POOL_WEIGHTS["translate"]
    assert "sarvam" not in weights
    assert "azure_translator" not in weights
    assert weights["workers_ai_indic"] >= max(
        weights[p] for p in weights if p != "workers_ai_indic"
    )


@pytest.mark.asyncio
async def test_translate_indic_failure_falls_through_to_workers_ai():
    """Task #552 §G-R regression: when the IndicTrans2 primary fails,
    `call_translate_with_dispatch` must advance to the generic Workers-AI
    leg (not raise "all providers exhausted") because the post-purge
    chain is exactly two legs: workers_ai_indic → workers_ai."""
    from llm import call_translate_with_dispatch

    seq = iter(["workers_ai_indic", "workers_ai"])

    def _fake_select(pool, lang=None, exclude=None):
        return next(seq)

    async def _indic_fail(text, direction="en-indic"):
        raise RuntimeError("workers_ai_indic: simulated 5xx")

    # workers_ai branch in call_translate_with_dispatch invokes
    # llm.dispatch() with a chat prompt; patch dispatch so we don't have
    # to wire a full LLM mock chain.
    async def _ok_dispatch(*args, **kwargs):
        return {"text": "namaskar"}

    with patch("config.TRANSLATE_PROVIDER", "auto"), \
         patch("llm.select_provider", side_effect=_fake_select), \
         patch("providers.workers_indic.call_indic_trans", new=AsyncMock(side_effect=_indic_fail)), \
         patch("llm.dispatch", new=AsyncMock(side_effect=_ok_dispatch)):
        out = await call_translate_with_dispatch("hello", "en-IN", "as-IN", lang="as")
        assert out == "namaskar", (
            "post-purge translate chain must reach workers_ai when "
            "workers_ai_indic raises"
        )


@pytest.mark.asyncio
async def test_translate_sarvam_branch_raises_loud():
    """If select_provider somehow returns 'sarvam' (e.g. an admin re-adds
    it to the pool by mistake), call_translate_with_dispatch must raise
    a loud RuntimeError instead of silently calling api.sarvam.ai."""
    from llm import call_translate_with_dispatch

    seq = iter(["sarvam", "workers_ai_indic"])

    def _fake_select(pool, lang=None, exclude=None):
        return next(seq)

    async def _ok_indic(text, direction="en-indic"):
        return "fallback-after-sarvam-rejection"

    with patch("config.TRANSLATE_PROVIDER", "auto"), \
         patch("llm.select_provider", side_effect=_fake_select), \
         patch("providers.workers_indic.call_indic_trans", new=AsyncMock(side_effect=_ok_indic)):
        out = await call_translate_with_dispatch("hi", "en-IN", "as-IN", lang="as")
        assert out == "fallback-after-sarvam-rejection", (
            "after sarvam leg raises loud, dispatch should advance to the "
            "next provider (workers_ai_indic) — confirming the loud-fail "
            "RuntimeError is treated as a normal per-provider failure"
        )
