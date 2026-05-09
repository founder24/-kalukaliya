"""Task #2 — 2026 blueprint: integration test for the Assamese chat
chain's deterministic third leg.

When both LLM legs (sarvam + vertex_assamese) raise, dispatch must
walk to ``retrieval_only`` and return:
    1. the loud "no LLM" Assamese-script banner, and
    2. the top RAG snippet — sourced either from an explicit
       ``[CONTEXT]`` system message OR from a live retriever query
       against the latest user message.

Round-3 review reject called out that the previous implementation
required a synthetic ``[CONTEXT]`` marker that no caller actually
produced. This test pins both paths so we don't regress.
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_retrieval_only_emits_banner_and_top_snippet_from_context_marker():
    """Path (a): caller embeds the snippet in a `[CONTEXT]` system
    message — the legacy code path. Banner must prepend the snippet."""
    from llm import _dispatch_llm_for_feature

    snippet = "নিউটনৰ গতিৰ প্ৰথম সূত্ৰ — যিকোনো বস্তু ..."
    messages = [
        {"role": "system", "content": f"[CONTEXT]{snippet}"},
        {"role": "user", "content": "নিউটনৰ গতিৰ প্ৰথম সূত্ৰ কি?"},
    ]
    out = asyncio.run(_dispatch_llm_for_feature(
        messages, "retrieval_only", 1024, feature="assamese_rag_chat",
    ))
    assert "অসমীয়া LLM অনুপলব্ধ" in out, (
        f"Expected loud no-LLM Assamese banner; got {out!r}"
    )
    assert snippet[:40] in out, (
        f"Expected top RAG snippet in payload; got {out!r}"
    )


def test_retrieval_only_uses_live_retriever_when_no_marker():
    """Path (b): caller did NOT embed a `[CONTEXT]` marker. Dispatch
    must invoke the live retriever using the latest user message and
    fold the top hit into the banner."""
    from llm import _dispatch_llm_for_feature

    fake_snippet = "অসমৰ ৰাজধানী দিছপুৰ — গুৱাহাটীৰ এটা উপ-শ্বহৰ।"

    class _FakeRetriever:
        async def query(self, q, top_k=1):
            return [{"text": fake_snippet, "score": 0.91}]

    messages = [
        {"role": "user", "content": "অসমৰ ৰাজধানী কি?"},
    ]
    import sys as _sys, types as _types
    _fake_factory = _types.ModuleType("retrievers.factory")
    _fake_factory.get_active_retriever = lambda: _FakeRetriever()  # type: ignore
    _orig = _sys.modules.get("retrievers.factory")
    _sys.modules["retrievers.factory"] = _fake_factory
    try:
        out = asyncio.run(_dispatch_llm_for_feature(
            messages, "retrieval_only", 1024, feature="assamese_rag_chat",
        ))
    finally:
        if _orig is not None:
            _sys.modules["retrievers.factory"] = _orig
        else:
            _sys.modules.pop("retrievers.factory", None)
    assert "অসমীয়া LLM অনুপলব্ধ" in out, (
        f"Expected loud no-LLM Assamese banner; got {out!r}"
    )
    assert fake_snippet[:40] in out, (
        f"Expected live-retriever top snippet in payload; got {out!r}"
    )


def test_retrieval_only_raises_when_no_context_and_no_retriever_hits():
    """Both context paths exhausted → loud RuntimeError, never a
    silent empty answer (V4 §12)."""
    from llm import _dispatch_llm_for_feature

    class _EmptyRetriever:
        async def query(self, q, top_k=1):
            return []

    import sys as _sys, types as _types
    _fake_factory = _types.ModuleType("retrievers.factory")
    _fake_factory.get_active_retriever = lambda: _EmptyRetriever()  # type: ignore
    _orig = _sys.modules.get("retrievers.factory")
    _sys.modules["retrievers.factory"] = _fake_factory
    messages = [{"role": "user", "content": "এটা প্ৰশ্ন"}]
    try:
        try:
            asyncio.run(_dispatch_llm_for_feature(
                messages, "retrieval_only", 1024, feature="assamese_rag_chat",
            ))
        except RuntimeError as e:
            assert "chain exhausted" in str(e), (
                f"Expected loud chain-exhausted error; got {e!r}"
            )
            return
    finally:
        if _orig is not None:
            _sys.modules["retrievers.factory"] = _orig
        else:
            _sys.modules.pop("retrievers.factory", None)
    raise AssertionError(
        "retrieval_only should raise when no context AND no retriever hits"
    )


def test_regional_cache_partition_is_assamese_scoped_only():
    """Round-3 review reject: English cache entries must NOT be
    geo-partitioned. Only Assamese-language `as_chat` / `explanation`
    / `translate` content_types fold the region into the cache key.
    Regression test pinning the gate.
    """
    import importlib
    import ai_input_cache as aic
    importlib.reload(aic)
    aic.reset_for_tests()
    aic.set_request_region("ne-india")

    eng_msgs = [{"role": "user", "content": "english query alpha"}]
    asm_msgs = [{"role": "user", "content": "অসমীয়া প্ৰশ্ন beta"}]

    # English content_type — write under ne-india, read under global,
    # MUST hit (region was forced to "global" by the gate).
    aic.set_response(eng_msgs, "model-x", "english reply",
                     content_type="formatter")
    aic.set_request_region("global")
    got = aic.get_response(eng_msgs, "model-x", content_type="formatter")
    assert got == "english reply", (
        "English cache entries must remain globally distributed; "
        f"got {got!r} after switching region from ne-india → global"
    )

    # Assamese content_type — write under ne-india, read under global,
    # MUST miss (region IS folded into the key for `as_chat`).
    aic.set_request_region("ne-india")
    aic.set_response(asm_msgs, "model-x", "assamese reply",
                     content_type="as_chat")
    aic.set_request_region("global")
    got = aic.get_response(asm_msgs, "model-x", content_type="as_chat")
    assert got is None, (
        "Assamese `as_chat` cache entries must be region-pinned; "
        f"got cross-region hit {got!r}"
    )

    # And reading back under ne-india MUST hit — confirming the
    # partition is real, not a side-effect of always-miss behaviour.
    aic.set_request_region("ne-india")
    got = aic.get_response(asm_msgs, "model-x", content_type="as_chat")
    assert got == "assamese reply", (
        "Same-region read must hit; got {got!r}".format(got=got)
    )


def test_voice_pool_is_elevenlabs_first():
    """Both `tts` and `voice` PROVIDER_PRIORITY pools must list
    elevenlabs as the canonical primary (Task #2 — 2026 blueprint
    voice canonical specialists)."""
    from config import PROVIDER_PRIORITY

    assert PROVIDER_PRIORITY["tts"][0] == "elevenlabs", (
        f"tts pool primary must be elevenlabs; got {PROVIDER_PRIORITY['tts']!r}"
    )
    assert PROVIDER_PRIORITY["voice"][0] == "elevenlabs", (
        f"voice pool primary must be elevenlabs; got {PROVIDER_PRIORITY['voice']!r}"
    )


def test_colo_bias_for_ne_india_is_mumbai_chennai():
    """Mumbai + Chennai are the AP-South colos closest to Assam — the
    edge-proxy stamps these as the intended colo bias for ne-india
    requests, and the helper must agree."""
    from cf_tiered_cache import colo_bias_for_region

    assert colo_bias_for_region("ne-india") == ("BOM", "MAA")
    assert colo_bias_for_region("global") == ("global",)
    assert colo_bias_for_region("") == ("global",)


def test_tier_cache_tag_and_kv_namespace_for_region():
    """Task #2 — region routing is observable end-to-end:
      * `tier_cache_tag_for("ne-india") == "tier:ne-india"` so CF
        Tiered Cache routes upper-tier fetches consistently to AP-South.
      * `kv_namespace_for_region("ne-india") == "ne-india"` so backend
        KV writes land in a separate namespace that can be routed to
        the AP-South KV replica without touching the global namespace.
    """
    from cf_tiered_cache import tier_cache_tag_for, kv_namespace_for_region

    assert tier_cache_tag_for("ne-india") == "tier:ne-india"
    assert tier_cache_tag_for("global") == "tier:global"
    assert tier_cache_tag_for("") == "tier:global"

    assert kv_namespace_for_region("ne-india") == "ne-india"
    assert kv_namespace_for_region("global") == "global"
    assert kv_namespace_for_region("") == "global"


def test_admin_ops_console_outage_map_includes_provider_health():
    """Round-4 review: outage map must include 1h 5xx/timeout rate per
    provider, not just breaker state."""
    import asyncio
    import unittest.mock as _m
    import routes.admin_ops_console as _ops

    fake_breakers = {"sarvam": {"open": True, "failures": 5, "last_error": "timeout"}}
    fake_stats = {
        "providers": {
            "sarvam":   {"calls": 100, "success_rate": 60.0, "avg_latency_ms": 1200.0},
            "vertex":   {"calls": 200, "success_rate": 99.5, "avg_latency_ms": 350.0},
        }
    }
    with _m.patch("llm._BREAKER_STATE", fake_breakers, create=True), \
         _m.patch("llm.get_llm_provider_stats", return_value=fake_stats):
        out = asyncio.run(_ops.admin_ops_console(_admin={"sub": "test"}))
    rows = {r["provider"]: r for r in out["outage_map"]["rows"]}
    assert "sarvam" in rows and "vertex" in rows
    assert rows["sarvam"]["status"] == "open"
    assert rows["sarvam"]["calls_1h"] == 100
    assert rows["sarvam"]["failure_rate_pct_1h"] == 40.0
    assert rows["vertex"]["status"] == "healthy"
    assert rows["vertex"]["success_rate_pct_1h"] == 99.5


def test_cf_kv_routes_ne_india_to_apsouth_namespace(monkeypatch):
    """Round-4 wiring: ne-india writes/reads must actually pick the
    AP-South KV namespace + tier cache tag — not just stamp counters."""
    import ai_input_cache as aic

    monkeypatch.setattr(aic, "_CF_ACCOUNT_ID", "acct-test")
    monkeypatch.setattr(aic, "_CF_API_TOKEN", "tok-test")
    monkeypatch.setattr(aic, "_CF_KV_NAMESPACE", "GLOBAL_NS")
    monkeypatch.setattr(aic, "_CF_KV_NAMESPACES", {
        "global": "GLOBAL_NS", "ne-india": "NE_INDIA_NS",
    })
    monkeypatch.setattr(aic, "_CF_KV_ENABLED", True)

    assert "NE_INDIA_NS" in aic._cf_kv_url("k", region="ne-india")
    assert "GLOBAL_NS" in aic._cf_kv_url("k", region="global")
    assert "GLOBAL_NS" in aic._cf_kv_url("k", region=None)
    assert aic._cf_cache_tag_for("ne-india") == "tier:ne-india"
    assert aic._cf_cache_tag_for("global") == "tier:global"


def test_admin_ops_console_toggles_include_routing_pools():
    """Round-4 review: toggle viewer must aggregate routing-config
    pools alongside env knobs + cost_caps thresholds."""
    import asyncio
    import routes.admin_ops_console as _ops

    out = asyncio.run(_ops.admin_ops_console(_admin={"sub": "test"}))
    toggles = out["toggles"]
    assert "env_knobs" in toggles
    assert "founder_locked_thresholds" in toggles
    assert "routing_pools" in toggles
    assert isinstance(toggles["routing_pools"], list)
    assert len(toggles["routing_pools"]) > 0
    features = {p["feature"] for p in toggles["routing_pools"]}
    assert {"english_rag_chat", "assamese_rag_chat", "tts"} <= features
