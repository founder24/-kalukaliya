"""Unit tests for gcp_counters spend estimation math.

Validates per-service pricing formulas and total spend aggregation.
All expected values are hand-computed from published GCP pricing:
  STT Chirp_2:       $0.016 / minute
  TTS Neural2:       $16 / 1M chars   = $0.000016 / char
  Translation v3:    $20 / 1M chars   = $0.000020 / char
  Vision OCR:        $1.50 / 1K imgs  = $0.0015   / image
  Vertex Embed-004:  $0.00013 / 1K ch = $0.00000013 / char
"""
from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _fresh_module():
    """Return a freshly imported gcp_counters module (no shared state)."""
    if "providers.gcp_counters" in sys.modules:
        del sys.modules["providers.gcp_counters"]
    import importlib
    mod = importlib.import_module("providers.gcp_counters")
    return mod


def _reset(mod):
    """Zero all counters without triggering month-reset side effects."""
    for svc in mod._counters.values():
        for k in list(svc.keys()):
            svc[k] = 0.0 if isinstance(svc[k], float) else 0


def test_stt_spend_one_minute():
    mod = _fresh_module()
    _reset(mod)
    mod.inc_stt(1.0)
    s = mod.snapshot()
    assert s["services"]["stt"]["calls"] == 1
    assert abs(s["services"]["stt"]["audio_minutes"] - 1.0) < 1e-9
    expected = round(1.0 * 0.016, 4)
    assert abs(s["services"]["stt"]["estimated_spend_usd"] - expected) < 1e-6, (
        f"STT spend: got {s['services']['stt']['estimated_spend_usd']}, expected {expected}"
    )


def test_stt_spend_fractional():
    mod = _fresh_module()
    _reset(mod)
    mod.inc_stt(0.25)
    mod.inc_stt(0.75)
    s = mod.snapshot()
    assert s["services"]["stt"]["calls"] == 2
    expected = round(1.0 * 0.016, 4)
    assert abs(s["services"]["stt"]["estimated_spend_usd"] - expected) < 1e-6


def test_tts_spend():
    mod = _fresh_module()
    _reset(mod)
    mod.inc_tts(1_000_000)
    s = mod.snapshot()
    assert s["services"]["tts"]["calls"] == 1
    assert s["services"]["tts"]["chars"] == 1_000_000
    expected = round(1_000_000 / 1_000_000 * 16.0, 4)
    assert abs(s["services"]["tts"]["estimated_spend_usd"] - expected) < 1e-6, (
        f"TTS spend: got {s['services']['tts']['estimated_spend_usd']}, expected {expected}"
    )


def test_tts_spend_small():
    mod = _fresh_module()
    _reset(mod)
    mod.inc_tts(500)
    s = mod.snapshot()
    expected = round(500 / 1_000_000 * 16.0, 4)
    assert abs(s["services"]["tts"]["estimated_spend_usd"] - expected) < 1e-6


def test_translate_spend():
    mod = _fresh_module()
    _reset(mod)
    mod.inc_translate(1_000_000)
    s = mod.snapshot()
    expected = round(1_000_000 / 1_000_000 * 20.0, 4)
    assert abs(s["services"]["translate"]["estimated_spend_usd"] - expected) < 1e-6, (
        f"Translate spend: got {s['services']['translate']['estimated_spend_usd']}, expected {expected}"
    )


def test_vision_spend_1000_images():
    mod = _fresh_module()
    _reset(mod)
    for _ in range(1000):
        mod.inc_vision()
    s = mod.snapshot()
    assert s["services"]["vision"]["calls"] == 1000
    assert s["services"]["vision"]["images"] == 1000
    expected = round(1000 / 1000 * 1.50, 4)
    assert abs(s["services"]["vision"]["estimated_spend_usd"] - expected) < 1e-6, (
        f"Vision spend: got {s['services']['vision']['estimated_spend_usd']}, expected {expected}"
    )


def test_vision_spend_single():
    mod = _fresh_module()
    _reset(mod)
    mod.inc_vision()
    s = mod.snapshot()
    expected = round(1 / 1000 * 1.50, 4)
    assert abs(s["services"]["vision"]["estimated_spend_usd"] - expected) < 1e-6


def test_embed_spend_not_1000x_inflated():
    """Regression: embed rate MUST be $0.00013/1K chars, NOT $0.13/1K chars."""
    mod = _fresh_module()
    _reset(mod)
    mod.inc_embed(1_000_000)
    s = mod.snapshot()
    spend = s["services"]["embed"]["estimated_spend_usd"]
    expected = round(1_000_000 / 1_000 * 0.00013, 4)
    inflated = round(1_000_000 / 1_000 * 0.13, 4)
    assert abs(spend - expected) < 1e-4, (
        f"Embed spend: got ${spend}, expected ${expected} (NOT ${inflated} which is 1000x wrong)"
    )


def test_embed_spend_1k_chars():
    mod = _fresh_module()
    _reset(mod)
    mod.inc_embed(1000)
    s = mod.snapshot()
    expected = round(1000 / 1000 * 0.00013, 4)
    assert abs(s["services"]["embed"]["estimated_spend_usd"] - expected) < 1e-7, (
        f"Embed 1K chars: got {s['services']['embed']['estimated_spend_usd']}, expected {expected}"
    )


def test_total_spend_aggregates_all_services():
    mod = _fresh_module()
    _reset(mod)
    mod.inc_stt(1.0)
    mod.inc_tts(1_000_000)
    mod.inc_translate(1_000_000)
    mod.inc_vision()
    mod.inc_embed(1_000_000)
    s = mod.snapshot()
    stt_exp   = round(1.0 * 0.016, 4)
    tts_exp   = round(1_000_000 / 1_000_000 * 16.0, 4)
    trans_exp = round(1_000_000 / 1_000_000 * 20.0, 4)
    vis_exp   = round(1 / 1000 * 1.50, 4)
    emb_exp   = round(1_000_000 / 1_000 * 0.00013, 4)
    expected_total = round(stt_exp + tts_exp + trans_exp + vis_exp + emb_exp, 4)
    assert abs(s["total_estimated_spend_usd"] - expected_total) < 1e-3, (
        f"Total spend: got {s['total_estimated_spend_usd']}, expected {expected_total}"
    )


def test_multi_call_accumulation():
    mod = _fresh_module()
    _reset(mod)
    mod.inc_tts(100)
    mod.inc_tts(200)
    mod.inc_tts(300)
    s = mod.snapshot()
    assert s["services"]["tts"]["calls"] == 3
    assert s["services"]["tts"]["chars"] == 600
    expected = round(600 / 1_000_000 * 16.0, 4)
    assert abs(s["services"]["tts"]["estimated_spend_usd"] - expected) < 1e-6


def test_snapshot_period_and_metadata():
    mod = _fresh_module()
    _reset(mod)
    s = mod.snapshot()
    assert "period" in s
    assert s["counters_reset_on_restart"] is True
    assert isinstance(s["process_uptime_hours"], float)
    assert "services" in s


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS: {fn.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL: {fn.__name__}: {exc}")
            failed += 1
    print(f"\n{'='*50}")
    print(f"Results: {passed}/{passed+failed} passed")
    if failed:
        import sys; sys.exit(1)
