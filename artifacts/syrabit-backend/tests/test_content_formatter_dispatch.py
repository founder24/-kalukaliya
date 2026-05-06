"""Task #494 — content_formatter.format_content dispatcher contract.

Pins:

  1. The return shape ({text, formatted_by, duration_ms, trace_id}).
  2. Vertex success → formatted_by == "vertex" (no fallback call).
  3. Vertex failure + Workers-AI Llama-3.3-70b success → formatted_by ==
     "workers_ai_llama33_70b" with the WAI fallback actually invoked.
  4. Both legs failing → formatted_by == "passthrough" with the original
     text returned unchanged. Dispatcher never raises.
  5. Assamese purity gate: a polished output that drops Assamese script is
     rejected and falls through to passthrough so Vertex / WAI cannot
     silently English-ify lang="as" content.
  6. Style / lang validation rejects unknown enum values.
  7. The `content_format` POOL_WEIGHTS pool lists exactly the two legs of
     the §15 §6 chain in the right order with Vertex dominant.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── Shape + happy-path ────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_format_content_returns_dict_shape_on_vertex_success(monkeypatch):
    import content_formatter as cf

    async def _fake_vertex(text, *, style, lang, max_tokens):
        return "POLISHED-EN"

    async def _fake_wai(text, *, lang, max_tokens):
        raise AssertionError("WAI fallback must NOT be called when Vertex succeeds")

    monkeypatch.setattr(cf, "_try_vertex", _fake_vertex, raising=True)
    monkeypatch.setattr(cf, "_try_workers_ai_llama", _fake_wai, raising=True)

    out = await cf.format_content(
        "raw english notes about photosynthesis",
        style="notebook_lm", lang="en",
    )
    assert set(out.keys()) == {"text", "formatted_by", "duration_ms", "trace_id"}
    assert out["text"] == "POLISHED-EN"
    assert out["formatted_by"] == "vertex"
    assert isinstance(out["duration_ms"], int)
    assert out["trace_id"]


# ── Vertex outage → WAI Llama-3.3-70b fallback ────────────────────────────────
@pytest.mark.anyio
async def test_format_content_falls_back_to_workers_ai_llama33_70b(monkeypatch):
    import content_formatter as cf
    called = {"vertex": 0, "wai": 0}

    async def _fake_vertex(text, *, style, lang, max_tokens):
        called["vertex"] += 1
        return None

    async def _fake_wai(text, *, lang, max_tokens):
        called["wai"] += 1
        return "POLISHED-BY-LLAMA33"

    monkeypatch.setattr(cf, "_try_vertex", _fake_vertex, raising=True)
    monkeypatch.setattr(cf, "_try_workers_ai_llama", _fake_wai, raising=True)

    out = await cf.format_content("some english notes", style="notebook_lm", lang="en")
    assert called == {"vertex": 1, "wai": 1}
    assert out["text"] == "POLISHED-BY-LLAMA33"
    assert out["formatted_by"] == "workers_ai_llama33_70b"


# ── Both legs failing → passthrough, never raises ─────────────────────────────
@pytest.mark.anyio
async def test_format_content_passthrough_on_dual_outage(monkeypatch):
    import content_formatter as cf

    async def _fake_vertex(text, *, style, lang, max_tokens):
        return None

    async def _fake_wai(text, *, lang, max_tokens):
        return None

    monkeypatch.setattr(cf, "_try_vertex", _fake_vertex, raising=True)
    monkeypatch.setattr(cf, "_try_workers_ai_llama", _fake_wai, raising=True)

    raw = "the original raw notes that must survive a dual outage"
    out = await cf.format_content(raw, style="notebook_lm", lang="en")
    assert out["text"] == raw
    assert out["formatted_by"] == "passthrough"


# ── Assamese purity gate ──────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_format_content_rejects_english_leak_on_assamese_polish(monkeypatch):
    """Vertex returns valid-looking but English text for an Assamese polish.
    The purity gate must reject the polish and passthrough the original
    Assamese input rather than ship English under formatted_by="vertex"."""
    import content_formatter as cf

    raw_as = "অসমীয়া ভাষাত লিখা মূল টোকা — ফটোসিন্থেছিচৰ বিষয়ে।"

    async def _fake_vertex(text, *, style, lang, max_tokens):
        # Intentionally English — simulates Vertex translating away from Assamese.
        return "Photosynthesis is the process by which green plants convert light energy."

    async def _fake_wai(text, *, lang, max_tokens):
        # Same English-leakage failure mode on the fallback leg.
        return "Photosynthesis is the process by which green plants convert light energy."

    monkeypatch.setattr(cf, "_try_vertex", _fake_vertex, raising=True)
    monkeypatch.setattr(cf, "_try_workers_ai_llama", _fake_wai, raising=True)

    out = await cf.format_content(raw_as, style="notebook_lm", lang="as")
    assert out["formatted_by"] == "passthrough"
    assert out["text"] == raw_as


# ── Enum validation ──────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_format_content_rejects_unknown_style():
    import content_formatter as cf
    with pytest.raises(ValueError):
        await cf.format_content("anything", style="freeform_chat", lang="en")  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_format_content_rejects_unknown_lang():
    import content_formatter as cf
    with pytest.raises(ValueError):
        await cf.format_content("anything", style="notebook_lm", lang="hi")  # type: ignore[arg-type]


# ── Empty input shortcut ─────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_format_content_empty_input_passthrough():
    import content_formatter as cf
    out = await cf.format_content("", style="notebook_lm", lang="en")
    assert out["formatted_by"] == "passthrough"
    assert out["text"] == ""


# ── POOL_WEIGHTS pool order + dominance ──────────────────────────────────────
def test_content_format_pool_lists_vertex_then_workers_ai_llama33_70b():
    from config import PROVIDER_PRIORITY, POOL_WEIGHTS

    chain = PROVIDER_PRIORITY["content_format"]
    assert chain == ["vertex", "workers_ai_llama33_70b"], (
        "Task #494 / V4 §15 §6 — content_format must list Vertex first, "
        "Llama-3.3-70b second, no other entries."
    )
    weights = POOL_WEIGHTS["content_format"]
    assert weights["vertex"] >= 1000
    assert weights["workers_ai_llama33_70b"] >= 1
    assert weights["vertex"] > weights["workers_ai_llama33_70b"], (
        "Vertex must dominate the weighted draw so Llama-3.3-70b is only "
        "reached when the dispatcher explicitly advances to the fallback."
    )


# ── Audit ring exposes recent breakdown ──────────────────────────────────────
@pytest.mark.anyio
async def test_recent_breakdown_counts_vertex_invocations(monkeypatch):
    import content_formatter as cf
    # Reset the in-process ring so the count is deterministic.
    cf._RECENT_INVOCATIONS.clear()

    async def _fake_vertex(text, *, style, lang, max_tokens):
        return "polished"

    async def _fake_wai(text, *, lang, max_tokens):
        return None

    monkeypatch.setattr(cf, "_try_vertex", _fake_vertex, raising=True)
    monkeypatch.setattr(cf, "_try_workers_ai_llama", _fake_wai, raising=True)

    await cf.format_content("first run", style="notebook_lm", lang="en")
    await cf.format_content("second run", style="notebook_lm", lang="en")
    bd = cf.get_recent_breakdown()
    assert bd["by_formatter"]["vertex"] == 2
    assert bd["by_formatter"]["workers_ai_llama33_70b"] == 0
    assert bd["window"] == 2
