"""Task #9 — regression guards for the keyword-based topic estimator.

Covers:
* ``estimate_topic_score_from_keywords`` positive / negative cases.
* ``probe_topic_score`` returns a ``float`` (not ``None``) when ``subject_id``
  is absent — the BM25-keyword-estimator path that replaced the old probe-
  pending sentinel for subject-less turns.

Run::

    python -m pytest tests/test_chat_router_keyword.py -v
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chat_router


# ── estimate_topic_score_from_keywords ────────────────────────────────────────

class TestEstimateTopicScoreFromKeywords:

    def test_physics_query_above_floor(self):
        score = chat_router.estimate_topic_score_from_keywords(
            "explain the laws of thermodynamics physics"
        )
        assert score > 0.3, f"Expected >0.3, got {score}"

    def test_photosynthesis_above_floor(self):
        score = chat_router.estimate_topic_score_from_keywords(
            "explain photosynthesis biology"
        )
        assert score >= 0.3, f"Expected >=0.3, got {score}"

    def test_osmosis_above_floor(self):
        score = chat_router.estimate_topic_score_from_keywords(
            "define osmosis cell membrane science"
        )
        assert score >= 0.3, f"Expected >=0.3, got {score}"

    def test_maths_above_floor(self):
        score = chat_router.estimate_topic_score_from_keywords(
            "calculate area maths formula"
        )
        assert score >= 0.3, f"Expected >=0.3, got {score}"

    def test_history_above_floor(self):
        score = chat_router.estimate_topic_score_from_keywords(
            "describe French Revolution history"
        )
        assert score >= 0.3, f"Expected >=0.3, got {score}"

    def test_economics_above_floor(self):
        score = chat_router.estimate_topic_score_from_keywords(
            "principles of economics commerce"
        )
        assert score >= 0.3, f"Expected >=0.3, got {score}"

    # --- casual greetings score near zero ---

    def test_hi_below_threshold(self):
        score = chat_router.estimate_topic_score_from_keywords("hi")
        assert score < chat_router._DEFAULT_TOPIC_THRESHOLD, (
            f"'hi' scored {score:.3f} — expected <{chat_router._DEFAULT_TOPIC_THRESHOLD}"
        )

    def test_hello_below_threshold(self):
        score = chat_router.estimate_topic_score_from_keywords("hello")
        assert score < chat_router._DEFAULT_TOPIC_THRESHOLD

    def test_how_are_you_below_threshold(self):
        score = chat_router.estimate_topic_score_from_keywords("how are you")
        assert score < chat_router._DEFAULT_TOPIC_THRESHOLD

    def test_good_morning_below_threshold(self):
        score = chat_router.estimate_topic_score_from_keywords("good morning")
        assert score < chat_router._DEFAULT_TOPIC_THRESHOLD

    def test_thanks_below_threshold(self):
        score = chat_router.estimate_topic_score_from_keywords("thanks")
        assert score < chat_router._DEFAULT_TOPIC_THRESHOLD

    def test_bye_below_threshold(self):
        score = chat_router.estimate_topic_score_from_keywords("bye")
        assert score < chat_router._DEFAULT_TOPIC_THRESHOLD

    # --- boundary / edge cases ---

    def test_empty_query_returns_zero(self):
        assert chat_router.estimate_topic_score_from_keywords("") == 0.0

    def test_whitespace_only_returns_zero(self):
        assert chat_router.estimate_topic_score_from_keywords("   ") == 0.0

    def test_score_clamped_to_one(self):
        score = chat_router.estimate_topic_score_from_keywords(
            "physics chemistry biology mathematics history geography economics "
            "science formula theory law principle explain define describe"
        )
        assert 0.0 <= score <= 1.0, f"Score out of [0,1]: {score}"

    def test_score_non_negative(self):
        for query in ("hello", "explain physics", ""):
            assert chat_router.estimate_topic_score_from_keywords(query) >= 0.0

    # --- context boost ---

    def test_context_boost_raises_score(self):
        base = chat_router.estimate_topic_score_from_keywords("describe the process")
        boosted = chat_router.estimate_topic_score_from_keywords(
            "describe the process",
            subject_name="chemistry",
            chapter_name="thermodynamics reactions",
        )
        assert boosted >= base, (
            f"Context boost did not help: base={base:.3f}, boosted={boosted:.3f}"
        )

    # --- Assamese-script detection ---

    def test_assamese_physics_token_above_floor(self):
        score = chat_router.estimate_topic_score_from_keywords("পদার্থবিজ্ঞান")
        assert score > 0.3, (
            f"Assamese physics token scored {score:.3f} — _CURRICULUM_KEYWORDS_AS may be missing it"
        )

    def test_assamese_greeting_below_threshold(self):
        score = chat_router.estimate_topic_score_from_keywords("নমস্কাৰ")
        assert score < chat_router._DEFAULT_TOPIC_THRESHOLD, (
            f"Assamese greeting 'নমস্কাৰ' scored {score:.3f} — should be below threshold"
        )


# ── probe_topic_score — keyword-estimator path (no subject_id) ────────────────

class TestProbeTopicScoreNoSubjectId:
    """probe_topic_score must return a float in [0,1] when subject_id is absent."""

    async def test_returns_float_for_academic_query(self):
        score = await chat_router.probe_topic_score(
            "explain photosynthesis", subject_id=None, lang="en",
        )
        assert isinstance(score, float), (
            f"Expected float, got {type(score).__name__}({score!r})"
        )
        assert 0.0 <= score <= 1.0

    async def test_academic_query_above_zero(self):
        score = await chat_router.probe_topic_score(
            "define osmosis cell membrane biology", subject_id=None, lang="en",
        )
        assert isinstance(score, float)
        assert score > 0.0, f"Expected >0.0, got {score}"

    async def test_casual_greeting_returns_zero(self):
        score = await chat_router.probe_topic_score(
            "hi", subject_id=None, lang="en",
        )
        assert isinstance(score, float)
        assert score == 0.0, f"Expected 0.0, got {score}"

    async def test_empty_query_returns_none(self):
        score = await chat_router.probe_topic_score(
            "", subject_id=None, lang="en",
        )
        assert score is None, f"Expected None for empty query, got {score!r}"

    async def test_whitespace_only_returns_none(self):
        score = await chat_router.probe_topic_score(
            "   ", subject_id=None, lang="en",
        )
        assert score is None, f"Expected None for whitespace query, got {score!r}"

    async def test_assamese_subject_token_returns_float(self):
        score = await chat_router.probe_topic_score(
            "পদার্থবিজ্ঞান", subject_id=None, lang="as",
        )
        assert isinstance(score, float), (
            f"Expected float for Assamese academic query, got {type(score).__name__}"
        )
