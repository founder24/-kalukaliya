"""Task #9 — regression guards for the instant-response layer in pipeline.py.

Ensures ``get_instant_response`` and ``get_instant_assamese_response`` fire for
every casual-phrase pattern that bypasses the LLM entirely, and return None for
academic queries that MUST NOT be swallowed by the instant path.

A future refactor that accidentally removes a regex entry, empties the
_INSTANT_ASSAMESE_RESPONSES dict, or strips the Unicode-aware Assamese regex
pass will fail at least one test here before the change ships.

Run::

    python -m pytest tests/test_pipeline_instant.py -v
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline


# ── English instant responses ─────────────────────────────────────────────────

class TestGetInstantResponse:
    """Covers get_instant_response(): exact-dict pass + regex second pass."""

    # --- exact-dict matches (Pass 1, 0 ms) ---

    def test_hi_exact(self):
        assert pipeline.get_instant_response("hi") is not None

    def test_hello_exact(self):
        assert pipeline.get_instant_response("hello") is not None

    def test_thanks_exact(self):
        assert pipeline.get_instant_response("thanks") is not None

    def test_thank_you_exact(self):
        assert pipeline.get_instant_response("thank you") is not None

    def test_bye_exact(self):
        assert pipeline.get_instant_response("bye") is not None

    def test_ok_exact(self):
        assert pipeline.get_instant_response("ok") is not None

    def test_namaste_exact(self):
        assert pipeline.get_instant_response("namaste") is not None

    def test_case_invariant_hi(self):
        """Normalisation (.lower().strip()) means 'Hi', 'HI', 'hi' hit the same entry."""
        assert pipeline.get_instant_response("Hi") is not None
        assert pipeline.get_instant_response("HELLO") is not None

    def test_strips_trailing_punctuation(self):
        """Trailing '!', '.', ' ' are stripped before the dict lookup."""
        assert pipeline.get_instant_response("hi!") is not None
        assert pipeline.get_instant_response("hello.") is not None
        assert pipeline.get_instant_response("bye ") is not None

    # --- regex second pass (Pass 2, ~0.5 ms) ---

    def test_how_are_you(self):
        assert pipeline.get_instant_response("how are you") is not None

    def test_how_are_you_doing(self):
        assert pipeline.get_instant_response("how are you doing?") is not None

    def test_how_are_ya(self):
        assert pipeline.get_instant_response("How are ya?") is not None

    def test_what_can_you_do(self):
        assert pipeline.get_instant_response("what can you do") is not None

    def test_whats_your_name(self):
        assert pipeline.get_instant_response("what's your name") is not None

    def test_what_is_your_name(self):
        assert pipeline.get_instant_response("what is your name") is not None

    def test_who_are_you(self):
        assert pipeline.get_instant_response("who are you") is not None

    def test_are_you_there(self):
        assert pipeline.get_instant_response("are you there") is not None

    def test_can_you_help_me(self):
        assert pipeline.get_instant_response("can you help me") is not None

    def test_could_you_help_us(self):
        assert pipeline.get_instant_response("could you help us") is not None

    def test_whats_up(self):
        assert pipeline.get_instant_response("what's up") is not None

    def test_good_morning(self):
        assert pipeline.get_instant_response("good morning") is not None

    def test_good_evening(self):
        assert pipeline.get_instant_response("good evening") is not None

    def test_good_night(self):
        assert pipeline.get_instant_response("good night") is not None

    def test_tell_me_about_yourself(self):
        assert pipeline.get_instant_response("tell me about yourself") is not None

    def test_tell_us_who_you_are(self):
        assert pipeline.get_instant_response("tell us who you are") is not None

    def test_all_responses_non_empty_strings(self):
        """Every instant response must be a non-empty string — an empty string
        would cause the SSE layer to emit a blank stream event."""
        for query in ("hi", "hello", "thanks", "how are you", "good morning"):
            resp = pipeline.get_instant_response(query)
            assert isinstance(resp, str) and resp.strip(), (
                f"get_instant_response({query!r}) returned empty/non-string: {resp!r}"
            )

    # --- academic queries MUST return None ---

    def test_photosynthesis_returns_none(self):
        assert pipeline.get_instant_response("explain photosynthesis") is None

    def test_newtons_law_returns_none(self):
        assert pipeline.get_instant_response("what is Newton's second law") is None

    def test_osmosis_returns_none(self):
        assert pipeline.get_instant_response("define osmosis") is None

    def test_area_calculation_returns_none(self):
        assert pipeline.get_instant_response("calculate the area of a circle") is None

    def test_french_revolution_returns_none(self):
        assert pipeline.get_instant_response("write a note on the French Revolution") is None


# ── Assamese instant responses ─────────────────────────────────────────────────

class TestGetInstantAssameseResponse:
    """Covers get_instant_assamese_response(): exact-dict + regex second pass."""

    # --- exact Assamese-script dict matches ---

    def test_namoskar_exact(self):
        assert pipeline.get_instant_assamese_response("নমস্কাৰ") is not None

    def test_hello_assamese_exact(self):
        assert pipeline.get_instant_assamese_response("হেল্লো") is not None

    def test_dhanyabad_exact(self):
        assert pipeline.get_instant_assamese_response("ধন্যবাদ") is not None

    def test_apunak_dhanyabad_exact(self):
        assert pipeline.get_instant_assamese_response("আপোনাক ধন্যবাদ") is not None

    def test_bidai_exact(self):
        assert pipeline.get_instant_assamese_response("বিদায়") is not None

    def test_thik_ase_exact(self):
        assert pipeline.get_instant_assamese_response("ঠিক আছে") is not None

    def test_hoy_exact(self):
        assert pipeline.get_instant_assamese_response("হয়") is not None

    def test_bhal_exact(self):
        assert pipeline.get_instant_assamese_response("ভাল") is not None

    def test_sahay_kora_exact(self):
        assert pipeline.get_instant_assamese_response("সহায় কৰা") is not None

    # --- mixed-script regex: English pattern → Assamese response ---

    def test_how_are_you_returns_assamese_script(self):
        resp = pipeline.get_instant_assamese_response("how are you")
        assert resp is not None
        assert re.search(r"[\u0980-\u09FF]", resp), (
            f"Response must contain Assamese-script characters, got: {resp!r}"
        )

    def test_good_morning_returns_assamese_script(self):
        resp = pipeline.get_instant_assamese_response("good morning")
        assert resp is not None
        assert re.search(r"[\u0980-\u09FF]", resp)

    def test_can_you_help_me_returns_assamese_script(self):
        resp = pipeline.get_instant_assamese_response("can you help me")
        assert resp is not None
        assert re.search(r"[\u0980-\u09FF]", resp)

    def test_who_are_you_returns_assamese_script(self):
        resp = pipeline.get_instant_assamese_response("who are you")
        assert resp is not None
        assert re.search(r"[\u0980-\u09FF]", resp)

    def test_what_can_you_do_returns_assamese_script(self):
        resp = pipeline.get_instant_assamese_response("what can you do")
        assert resp is not None
        assert re.search(r"[\u0980-\u09FF]", resp)

    def test_tell_me_about_yourself_returns_assamese_script(self):
        resp = pipeline.get_instant_assamese_response("tell me about yourself")
        assert resp is not None
        assert re.search(r"[\u0980-\u09FF]", resp)

    def test_assamese_response_is_non_empty_string(self):
        for query in ("নমস্কাৰ", "ধন্যবাদ", "how are you"):
            resp = pipeline.get_instant_assamese_response(query)
            assert isinstance(resp, str) and resp.strip(), (
                f"get_instant_assamese_response({query!r}) returned empty/non-string: {resp!r}"
            )

    # --- academic queries MUST return None on the Assamese endpoint too ---

    def test_assamese_endpoint_does_not_capture_photosynthesis(self):
        assert pipeline.get_instant_assamese_response("explain photosynthesis in detail") is None

    def test_assamese_endpoint_does_not_capture_newtons_law(self):
        assert pipeline.get_instant_assamese_response("what is Newton's second law") is None
