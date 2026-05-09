"""Task #10 — semantic fingerprint + ai_input_cache wiring + content_formatter
deterministic-template render. Pinned canonical pairs and the legacy-key
dual-read bridge.
"""
from __future__ import annotations

import os
import importlib

import ai_input_cache as aic
import cache_fingerprint as cf


def setup_function(_):
    aic.reset_for_tests()
    cf.reset_runtime_synonyms_for_tests()
    os.environ.pop("CACHE_FINGERPRINT_DUAL_READ", None)


# ── §1 fingerprint() canonical pairs ─────────────────────────────────
def test_fingerprint_collapses_paraphrased_english():
    a = cf.fingerprint("Explain photosynthesis", language="en")
    b = cf.fingerprint("What is photosynthesis?", language="en")
    c = cf.fingerprint("Define photosynthesis.", language="en")
    d = cf.fingerprint("Describe photosynthesis", language="en")
    assert a == b == c == d
    assert len(a) == 32  # 16-byte hex


def test_fingerprint_collapses_bilingual_pair():
    en = cf.fingerprint("Explain photosynthesis", language="en")
    asm = cf.fingerprint("ফটোসিন্থেসিস কি", language="as")
    assert en == asm, f"expected EN/AS to share fingerprint, got {en} vs {asm}"


def test_fingerprint_distinguishes_topic():
    photo = cf.fingerprint("Define photosynthesis", language="en")
    resp = cf.fingerprint("Define cellular respiration", language="en")
    assert photo != resp


def test_fingerprint_distinguishes_verb():
    define = cf.fingerprint("Define photosynthesis", language="en")
    list_ = cf.fingerprint("List the steps of photosynthesis", language="en")
    compare = cf.fingerprint("Compare photosynthesis and respiration", language="en")
    assert len({define, list_, compare}) == 3


def test_fingerprint_folds_syllabus_scope():
    bare = cf.fingerprint("Define photosynthesis", language="en")
    scoped = cf.fingerprint(
        "Define photosynthesis", language="en",
        board="ahsec", class_=11, subject="biology", chapter="ch-13",
    )
    assert bare != scoped


def test_canonical_form_returns_verb_topic():
    verb, topic = cf.canonical_form("Explain photosynthesis", "en")
    assert verb == "DEFINE"
    assert topic == "photosynthesis"


def test_register_synonym_overrides_builtin():
    cf.register_synonym("en", "photsyn", "photosynthesis")
    assert cf.resolve_topic_synonym("photsyn", "en") == "photosynthesis"


# ── §2 ai_input_cache: same KV key for EN+AS pair ────────────────────
def test_ai_input_cache_same_key_for_bilingual_pair():
    fp = cf.fingerprint("Explain photosynthesis", language="en")
    fp_as = cf.fingerprint("ফটোসিন্থেসিস কি", language="as")
    assert fp == fp_as

    msgs_en = [{"role": "user", "content": "Explain photosynthesis"}]
    msgs_as = [{"role": "user", "content": "ফটোসিন্থেসিস কি"}]

    # cold miss on EN
    assert aic.get_response(
        msgs_en, "test_model", content_type="definition",
        template_version="v1", fingerprint=fp,
    ) is None

    aic.set_response(
        msgs_en, "test_model", "PHOTOSYNTHESIS_RESULT",
        content_type="definition", template_version="v1", fingerprint=fp,
    )

    # AS query with the SAME fingerprint must hit the same entry
    got = aic.get_response(
        msgs_as, "test_model", content_type="definition",
        template_version="v1", fingerprint=fp_as,
    )
    assert got == "PHOTOSYNTHESIS_RESULT"

    snap = aic.snapshot()
    ct = snap["content_types"]["definition"]
    assert ct["fingerprint_hits"] == 1
    assert ct["fingerprint_misses"] == 1
    assert ct["fingerprint_hit_ratio"] == 0.5
    assert snap["totals"]["fingerprint_hit_ratio"] == 0.5


def test_legacy_dual_read_serves_pre_fingerprint_writes():
    msgs = [{"role": "user", "content": "Explain photosynthesis"}]
    # Simulate an entry written by an older build that did NOT pass a
    # fingerprint — written under the literal SHA256 key.
    aic.set_response(
        msgs, "test_model", "LEGACY_RESULT",
        content_type="definition", template_version="v1",
    )
    aic.reset_for_tests()  # clear counters but keep the inproc store... actually clears the store too
    # _inproc_set persists in module-level dict; reset_for_tests clears
    # counters AND the store, so re-prime the legacy entry.
    aic.set_response(
        msgs, "test_model", "LEGACY_RESULT",
        content_type="definition", template_version="v1",
    )
    fp = cf.fingerprint("Explain photosynthesis", language="en")
    got = aic.get_response(
        msgs, "test_model", content_type="definition",
        template_version="v1", fingerprint=fp,
    )
    assert got == "LEGACY_RESULT"
    snap = aic.snapshot()
    ct = snap["content_types"]["definition"]
    assert ct["legacy_hits"] == 1
    assert ct["fingerprint_hits"] == 0
    assert ct["legacy_hit_ratio"] == 1.0


def test_dual_read_disabled_returns_miss():
    os.environ["CACHE_FINGERPRINT_DUAL_READ"] = "false"
    msgs = [{"role": "user", "content": "Explain photosynthesis"}]
    aic.set_response(
        msgs, "test_model", "LEGACY_RESULT",
        content_type="definition", template_version="v1",
    )
    fp = cf.fingerprint("Explain photosynthesis", language="en")
    got = aic.get_response(
        msgs, "test_model", content_type="definition",
        template_version="v1", fingerprint=fp,
    )
    assert got is None
    snap = aic.snapshot()
    ct = snap["content_types"]["definition"]
    assert ct["legacy_hits"] == 0
    assert ct["fingerprint_misses"] == 1


# ── §3 deterministic template render ─────────────────────────────────
def test_render_deterministic_template_definition():
    from content_formatter import _render_deterministic_template
    out = _render_deterministic_template("definition", {
        "topic": "Photosynthesis",
        "summary": "The process by which plants convert light into chemical energy.",
        "bullets": "- Occurs in chloroplasts\n- Requires CO2 + H2O + light",
        "chapter": "Class 11 Biology · Ch 13",
    })
    assert "Photosynthesis" in out
    assert "Key points" in out
    assert "Ch 13" in out


def test_render_deterministic_template_mcq():
    from content_formatter import _render_deterministic_template
    out = _render_deterministic_template("mcq", {
        "question": "Which organelle hosts photosynthesis?",
        "choice_a": "Mitochondrion",
        "choice_b": "Chloroplast",
        "choice_c": "Ribosome",
        "choice_d": "Nucleus",
        "answer": "B",
        "explanation": "Chloroplasts contain chlorophyll.",
    })
    assert "Chloroplast" in out
    assert "**Answer:** B" in out


def test_render_deterministic_template_unknown_query_type_returns_none():
    from content_formatter import _render_deterministic_template
    assert _render_deterministic_template("freeform", {"x": 1}) is None


def test_render_deterministic_template_missing_placeholder_raises():
    """V4 §12 — fail loud when an eligible deterministic render cannot
    be completed, instead of silently degrading to Vertex / Workers-AI."""
    import pytest
    from content_formatter import (
        _render_deterministic_template,
        DeterministicTemplateError,
    )
    with pytest.raises(DeterministicTemplateError):
        _render_deterministic_template("definition", {"topic": "X"})  # missing summary/bullets/chapter
