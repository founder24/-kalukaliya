"""Task #492 (V4 §15) — translate dispatch fallback chain.

After removing Sarvam from the translate pool, the chain is
`workers_ai_indic → azure_openai → workers_ai`. This test pins the
post-cleanup behaviour:

  1. The pool is multi-leg (so `select_provider` can advance on outage
     instead of raising "all providers exhausted" on a single failure).
  2. When the primary `workers_ai_indic` leg fails, dispatch advances
     to `azure_openai` (Azure Translator REST) and returns its result
     instead of bubbling a 503 to callers.
  3. Sarvam is **never** selected from the translate pool — even if
     `select_provider` were nudged to return it, the dead `provider ==
     "sarvam"` branch in `call_translate_with_dispatch` raises a loud
     RuntimeError instead of silently calling `api.sarvam.ai`.
"""
from unittest.mock import patch, AsyncMock
import pytest


def test_translate_pool_is_multi_leg_no_sarvam():
    from config import PROVIDER_PRIORITY, POOL_WEIGHTS

    pool = PROVIDER_PRIORITY["translate"]
    assert pool[0] == "workers_ai_indic", "IndicTrans2 must remain primary"
    assert "sarvam" not in pool, "Task #492: Sarvam removed from translate pool"
    assert "vertex" not in pool, "Task #490: Vertex removed from translate pool"
    assert len(pool) >= 2, (
        "translate pool must be multi-leg so dispatch can advance on outage "
        "(single-leg pool turns every IndicTrans2 hiccup into a hard 503)"
    )

    weights = POOL_WEIGHTS["translate"]
    assert "sarvam" not in weights
    assert weights["workers_ai_indic"] >= max(weights[p] for p in weights if p != "workers_ai_indic"), (
        "IndicTrans2 must remain the dominant-weight primary"
    )


@pytest.mark.asyncio
async def test_translate_advances_to_azure_when_indictrans_fails():
    """When workers_ai_indic raises, dispatch must advance to azure_openai
    and return its result — not raise 503 immediately."""
    from llm import call_translate_with_dispatch

    seq = iter(["workers_ai_indic", "azure_openai"])

    def _fake_select(pool, lang=None, exclude=None):
        return next(seq)

    async def _boom_indic(text, direction="en-indic"):
        raise RuntimeError("simulated IndicTrans2 outage")

    async def _ok_az(text, target_lang=None, source_lang=None):
        return "অসম"

    async def _enabled(_):
        return True

    # Disable workers_indic-only mode so dispatch is allowed to advance
    # past the primary leg on outage (the flag is a separate ops switch).
    with patch("config.TRANSLATE_PROVIDER", "auto"), \
         patch("llm.select_provider", side_effect=_fake_select), \
         patch("providers.workers_indic.call_indic_trans", new=AsyncMock(side_effect=_boom_indic)), \
         patch("providers.azure_openai.call_translate", new=AsyncMock(side_effect=_ok_az)), \
         patch("azure_ai_runtime.is_enabled", new=AsyncMock(side_effect=_enabled)):
        out = await call_translate_with_dispatch("Assam", "en-IN", "as-IN", lang="as")
        assert out == "অসম", (
            "translate dispatch must fall through workers_ai_indic outage "
            "to the azure_openai leg instead of raising 503"
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
