"""Task #360 — SLO emitter contract tests."""
from __future__ import annotations

import os
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_slo_targets_match_v3_spec():
    """Targets per task #360 §realistic SLO envelope + §15 of
    infra/per-cloud-feature-delegation.md."""
    import slo_emitter as s
    assert s.SLO_TARGETS["chat_ttfb_ms"].p50_ms == 750
    assert s.SLO_TARGETS["chat_ttfb_ms"].p95_ms == 1400
    assert s.SLO_TARGETS["rag_e2e_ms"].p95_ms == 6000  # full response
    assert s.SLO_TARGETS["embed_hotpath_ms"].p95_ms == 60
    assert s.SLO_TARGETS["pinecone_query_ms"].p95_ms == 80
    assert s.SLO_TARGETS["mongo_profile_ms"].p95_ms == 25
    assert s.SLO_TARGETS["moderation_ms"].p95_ms == 250
    assert s.SLO_TARGETS["validation_lag_ms"].p95_ms == 5000


def test_emit_forwards_to_sink():
    import slo_emitter as s
    seen: list = []
    s.set_slo_sink(lambda name, val, labels: seen.append((name, val, labels)))
    try:
        s.emit("chat_ttfb_ms", 250.0, provider="azure_openai")
        assert seen == [("chat_ttfb_ms", 250.0, {"provider": "azure_openai"})]
    finally:
        s.set_slo_sink(None)


def test_emit_swallows_sink_exceptions():
    import slo_emitter as s
    def boom(*a, **k):
        raise RuntimeError("metrics down")
    s.set_slo_sink(boom)
    try:
        s.emit("chat_ttfb_ms", 100.0)  # must not raise
    finally:
        s.set_slo_sink(None)


def test_breaches_slo_p95():
    import slo_emitter as s
    assert s.breaches_slo("chat_ttfb_ms", 1500) is True
    assert s.breaches_slo("chat_ttfb_ms", 800) is False
    assert s.breaches_slo("chat_ttfb_ms", 800, percentile="p50") is True


def test_measure_context_manager_emits_duration():
    import slo_emitter as s
    seen: list = []
    s.set_slo_sink(lambda n, v, l: seen.append((n, v)))
    try:
        with s.measure("rag_e2e_ms", feature="english_rag_chat"):
            pass
        assert len(seen) == 1
        assert seen[0][0] == "rag_e2e_ms"
        assert seen[0][1] >= 0
    finally:
        s.set_slo_sink(None)
