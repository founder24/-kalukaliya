"""Task #571 — pin per-content-type counters + miss-reason tagging."""
from __future__ import annotations

import ai_input_cache as aic


def setup_function(_):
    aic.reset_for_tests()


def test_cold_miss_then_hit_increments_counters():
    msgs = [{"role": "user", "content": "What is photosynthesis?"}]
    assert aic.get_response(msgs, "test_model", content_type="definition",
                            template_version="v1", normalize_text=True) is None
    snap = aic.snapshot()
    assert snap["content_types"]["definition"]["misses"] == 1
    assert snap["content_types"]["definition"]["miss_reasons"]["cold"] == 1

    aic.set_response(msgs, "test_model", "PHOTOSYNTHESIS_RESULT",
                     content_type="definition", template_version="v1", normalize_text=True)

    # Cosmetically different prompt collapses to the same canonical key
    # under the normalizer — should be a HIT.
    msgs2 = [{"role": "user", "content": "Define photosynthesis."}]
    got = aic.get_response(msgs2, "test_model", content_type="definition",
                           template_version="v1", normalize_text=True)
    assert got == "PHOTOSYNTHESIS_RESULT"
    snap2 = aic.snapshot()
    assert snap2["content_types"]["definition"]["hits"] == 1
    assert snap2["content_types"]["definition"]["unique_keys_24h"] == 1


def test_template_version_bump_attribution():
    msgs = [{"role": "user", "content": "define x"}]
    aic.set_response(msgs, "m", "v1_answer",
                     content_type="mcq", template_version="v1")
    # Bumping the template version forces a new key — the miss must be
    # attributed to `template_version_bump`, not `cold`.
    aic.get_response(msgs, "m", content_type="mcq", template_version="v2")
    reasons = aic.snapshot()["content_types"]["mcq"]["miss_reasons"]
    assert reasons["template_version_bump"] == 1
    assert reasons["cold"] == 0


def test_uncached_content_type_attribution():
    aic.get_response([{"role": "user", "content": "x"}], "m")
    reasons = aic.snapshot()["content_types"]["unknown"]["miss_reasons"]
    assert reasons["uncached_content_type"] == 1


def test_normalization_mismatch_attribution():
    # A normalized variant is in the recently-set ring, but the caller
    # missed because they did NOT pass normalize_text=True. Operators
    # should be told to flip the caller on, not chase a "cold" ghost.
    aic.set_response([{"role": "user", "content": "Define photosynthesis."}],
                     "m", "ANS",
                     content_type="definition", template_version="v1",
                     normalize_text=True)
    aic.get_response([{"role": "user", "content": "What is photosynthesis?"}],
                     "m",
                     content_type="definition", template_version="v1",
                     normalize_text=False)
    reasons = aic.snapshot()["content_types"]["definition"]["miss_reasons"]
    assert reasons["normalization_mismatch"] == 1
    assert reasons["cold"] == 0


def test_snapshot_is_json_safe():
    import json as _j
    aic.get_response([{"role": "user", "content": "x"}], "m",
                     content_type="formatter", template_version="v1")
    blob = _j.dumps(aic.snapshot())
    assert "totals" in blob and "content_types" in blob
