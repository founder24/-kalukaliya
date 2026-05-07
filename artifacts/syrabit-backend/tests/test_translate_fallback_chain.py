"""Task #554 — translate dispatch fallback chain.

Task #554 retired the legacy ``providers/azure_openai.py`` module; the
surviving Azure Translator surface lives in ``providers/azure_speech.py``
(``call_translate``). Dispatch chain is now
``workers_ai_indic → azure_translator``.

  1. Pool is multi-leg so dispatch can advance on outage.
  2. When primary IndicTrans2 leg fails, dispatch advances to
     ``azure_translator`` (Azure Translator REST) and returns its result.
  3. Sarvam is never selected.
"""
from unittest.mock import patch, AsyncMock
import pytest


def test_translate_pool_is_multi_leg_no_sarvam():
    from config import PROVIDER_PRIORITY, POOL_WEIGHTS

    pool = PROVIDER_PRIORITY["translate"]
    assert pool[0] == "workers_ai_indic", "IndicTrans2 must remain primary"
    assert "sarvam" not in pool
    assert "vertex" not in pool
    # Task #554 — legacy azure_openai must not appear in translate pool.
    assert "azure_openai" not in pool
    assert len(pool) >= 2

    weights = POOL_WEIGHTS["translate"]
    assert "sarvam" not in weights
    assert weights["workers_ai_indic"] >= max(weights[p] for p in weights if p != "workers_ai_indic")


@pytest.mark.asyncio
async def test_translate_advances_to_azure_when_indictrans_fails():
    """When workers_ai_indic raises, dispatch must advance to the
    surviving Azure Translator leg (``azure_translator``)."""
    from llm import call_translate_with_dispatch

    seq = iter(["workers_ai_indic", "azure_translator"])

    def _fake_select(pool, lang=None, exclude=None):
        return next(seq)

    async def _boom_indic(text, direction="en-indic"):
        raise RuntimeError("simulated IndicTrans2 outage")

    async def _ok_az(text, target_lang=None, source_lang=None):
        return "অসম"

    async def _enabled(_):
        return True

    with patch("config.TRANSLATE_PROVIDER", "auto"), \
         patch("llm.select_provider", side_effect=_fake_select), \
         patch("providers.workers_indic.call_indic_trans", new=AsyncMock(side_effect=_boom_indic)), \
         patch("providers.azure_speech.call_translate", new=AsyncMock(side_effect=_ok_az)), \
         patch("azure_ai_runtime.is_enabled", new=AsyncMock(side_effect=_enabled)):
        out = await call_translate_with_dispatch("Assam", "en-IN", "as-IN", lang="as")
        assert out == "অসম"


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
