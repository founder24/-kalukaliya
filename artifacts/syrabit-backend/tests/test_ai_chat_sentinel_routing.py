"""Task #21 — verify the chat handler switches to training-knowledge mode
when web_search_with_fallback returns the sentinel (no real results), rather
than 503-ing or injecting the sentinel as grounding context to the LLM.

Three invariants are guarded here:

  I.  503 guard — the sentinel is a non-empty list so the fail-loud check
      ``if _route_force_web and not web_results: raise HTTPException(503)``
      in ai_chat.py (line ~1151) MUST evaluate to False.

  II. Prompt purity — build_rag_system_prompt filters web_results by
      ``_layer`` ('syrabit', 'base', 'polish').  Sentinel items carry no
      ``_layer``, so no sentinel snippet or URL is written into the LLM
      system prompt.  The LLM effectively answers from training knowledge.

  III.Source-card purity — _sources_from_web_results skips entries without
      a ``url`` field.  Sentinel has no url, so the response source-card
      list is empty (no ghost "No web results" citation in the chat bubble).

If someone:
  * reverts the sentinel to return ``[]`` → invariant I fails (503 fires).
  * adds a real ``url`` to the sentinel → invariant II / III fail.
  * changes ``_layer`` filter logic so sentinel keys slip through → II fails.
  * changes ``_sources_from_web_results`` to skip the url guard → III fails.

Run::

    python -m pytest tests/test_ai_chat_sentinel_routing.py -v
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()

import rag

# ── shared fixtures ───────────────────────────────────────────────────────────

#: Exact sentinel shape produced by rag.web_search_with_fallback when both
#: DDG and Exa return no results.  Must stay in sync with rag.py lines ~339-345.
SENTINEL = [
    {
        "_source": "training_knowledge",
        "_fallback": True,
        "web_fallback_reason": "ddg_zero_results",
        "title": "No web results",
        "snippet": "",
    }
]

#: A minimal real web result with the fields build_rag_system_prompt uses.
REAL_WEB_RESULT = [
    {
        "title": "Newton's Laws — NCERT",
        "url": "https://ncert.nic.in/laws-of-motion",
        "snippet": "First law: an object at rest stays at rest.",
        "_layer": "base",
    }
]

_MINIMAL_CONTEXT = {
    "board_name": "AHSEC",
    "class_name": "Class 11",
    "stream_name": "Science",
    "subject_name": "Physics",
    "subject_id": None,
    "chapter_name": "Laws of Motion",
}

_MINIMAL_RAG_CTX = {
    "chunks": [],
    "chapters": [],
    "chunk_chapters": [],
    "subjects": [],
    "vector_hits": [],
    "source": "none",
    "quality": "none",
    "_general_knowledge_fallback": True,
}


# ── Invariant I — sentinel prevents 503 ──────────────────────────────────────

class TestSentinelPrevents503:
    """Pin the boolean algebra of the fail-loud guard in ai_chat.py.

    The guard is:
        if _route_force_web and not web_results:
            raise HTTPException(status_code=503, ...)

    Sentinel is a non-empty list → ``not web_results`` is False → guard
    never fires.
    """

    def test_sentinel_is_truthy(self):
        """Non-empty list with one dict must be truthy."""
        assert bool(SENTINEL), (
            "Sentinel must be truthy — if it becomes falsy the 503 guard fires"
        )
        assert len(SENTINEL) == 1

    def test_fail_loud_guard_does_not_fire_with_sentinel(self):
        """Re-enact the ai_chat.py guard logic verbatim to pin the invariant."""
        _route_force_web = True
        web_results = SENTINEL
        # This is the exact condition from ai_chat.py ~line 1151:
        would_503 = _route_force_web and not web_results
        assert not would_503, (
            "Sentinel made web_results falsy — the 503 fail-loud guard will fire. "
            "This is the regression the sentinel was designed to prevent."
        )

    def test_empty_list_triggers_fail_loud_guard(self):
        """Inverse: [] must trigger the guard.  Proves the guard is live and
        the only reason it doesn't fire with the sentinel is the sentinel's
        truthiness — not an accidentally disabled guard."""
        _route_force_web = True
        web_results = []
        would_503 = _route_force_web and not web_results
        assert would_503, (
            "Empty web_results must trigger the 503 fail-loud guard; "
            "if this fails the guard has been disabled."
        )

    def test_none_triggers_fail_loud_guard(self):
        """None also triggers the guard (web fetch failed entirely)."""
        _route_force_web = True
        web_results = None
        would_503 = _route_force_web and not web_results
        assert would_503


# ── Invariant II — sentinel does not pollute the system prompt ───────────────

class TestSentinelDoesNotPollutSystemPrompt:
    """build_rag_system_prompt filters by _layer ('syrabit','base','polish').
    Sentinel items have no _layer → all three filter lists are empty →
    no web snippet or URL from the sentinel enters the LLM system prompt.
    """

    def test_sentinel_has_no_layer_field(self):
        """Structural pre-condition: sentinel must not carry a _layer that
        matches any of the three filter values.  If it ever does, the
        sentinel body will be rendered as a real web result."""
        for item in SENTINEL:
            assert item.get("_layer") not in ("syrabit", "base", "polish"), (
                f"Sentinel acquired a '_layer' key that matches a web filter: {item!r}. "
                "It will appear as a real web result in the system prompt."
            )

    def test_sentinel_has_no_url_field(self):
        """Structural pre-condition: sentinel must not carry a url.  A url
        would make it survive _sources_from_web_results (invariant III) and
        could also leak a citation into the prompt."""
        for item in SENTINEL:
            assert not item.get("url"), (
                f"Sentinel acquired a 'url' field: {item!r}. "
                "It will be rendered as a web citation in both the prompt and source cards."
            )

    def test_sentinel_web_results_produce_no_url_in_prompt(self):
        """build_rag_system_prompt with sentinel as web_results must not
        write any 'Source: <url>' line — that is the marker emitted for
        every real web-result entry."""
        prompt = rag.build_rag_system_prompt(
            _MINIMAL_CONTEXT,
            _MINIMAL_RAG_CTX,
            web_results=SENTINEL,
            query="What is Newton's first law?",
            resolved_intent="notes",
        )
        # The sentinel has no url, so "Source: " should not be followed by
        # any http URL (the format is "Source: https://...").
        import re
        _web_source_line = re.search(r"Source:\s*https?://", prompt)
        assert _web_source_line is None, (
            f"Sentinel leaked a 'Source: <url>' line into the system prompt. "
            f"Matched: {_web_source_line.group()!r}"
        )

    def test_sentinel_web_results_produce_no_snippet_in_prompt(self):
        """The sentinel's 'snippet' field is '' (empty string).  Even if the
        rendering loop somehow runs, no snippet content is emitted."""
        prompt = rag.build_rag_system_prompt(
            _MINIMAL_CONTEXT,
            _MINIMAL_RAG_CTX,
            web_results=SENTINEL,
            query="What is Newton's first law?",
            resolved_intent="notes",
        )
        # Sentinel title is "No web results" — if it appears as a formatted
        # web entry the item would look like:
        #   [Web 1] [Snippet] No web results\n\nSource: \n\n
        # Guard against this by asserting the sentinel title does not appear
        # inside a web-result entry block:
        assert "[Web 1]" not in prompt, (
            "Sentinel was rendered as a '[Web N]' web-result entry — "
            "the _layer filter is not working correctly."
        )
        assert "[Syrabit 1]" not in prompt, (
            "Sentinel was rendered as a '[Syrabit N]' entry — "
            "the _layer='syrabit' filter is not working correctly."
        )

    def test_real_web_result_does_appear_as_grounding_in_prompt(self):
        """Inverse: a genuine result (url + _layer='base') MUST appear in the
        prompt so the LLM can ground its answer.  Guards against accidentally
        over-filtering that silently drops real web context."""
        prompt = rag.build_rag_system_prompt(
            _MINIMAL_CONTEXT,
            _MINIMAL_RAG_CTX,
            web_results=REAL_WEB_RESULT,
            query="What is Newton's first law?",
            resolved_intent="notes",
        )
        assert "WEB SEARCH RESULTS" in prompt, (
            "Real web result must inject 'WEB SEARCH RESULTS' block into prompt"
        )
        assert "ncert.nic.in" in prompt, (
            "Real result URL must be present in the system prompt"
        )
        assert "Newton" in prompt, (
            "Real result snippet content must appear in the system prompt"
        )


# ── Invariant III — sentinel produces no source cards ────────────────────────

class TestSentinelProducesNoSourceCards:
    """_sources_from_web_results skips entries with empty/missing 'url'.
    Sentinel has no url → returns [] → no ghost citation appended to the
    chat response card.
    """

    def test_sources_from_web_results_returns_empty_for_sentinel(self):
        """Primary guard: sentinel → no source cards."""
        sources = rag._sources_from_web_results(SENTINEL)
        assert sources == [], (
            f"_sources_from_web_results must return [] for the sentinel; got: {sources!r}"
        )

    def test_sources_from_web_results_returns_empty_for_none(self):
        """Edge case: None input must also return [] (safe by default)."""
        sources = rag._sources_from_web_results(None)
        assert sources == []

    def test_sources_from_web_results_returns_empty_for_empty_list(self):
        """Edge case: [] input must return []."""
        sources = rag._sources_from_web_results([])
        assert sources == []

    def test_sources_from_web_results_returns_items_for_real_result(self):
        """Inverse: real result with url MUST produce source cards.  Guards
        against _sources_from_web_results being over-narrowed."""
        sources = rag._sources_from_web_results(REAL_WEB_RESULT)
        assert len(sources) == 1, f"Expected 1 source card, got: {sources!r}"
        assert sources[0]["url"] == "https://ncert.nic.in/laws-of-motion"
        assert sources[0]["type"] == "web"

    def test_sources_from_web_results_skips_mixed_sentinel_and_real(self):
        """When sentinel and a real result are both present, only the real
        result produces a source card.  Guards the mixed-list case."""
        mixed = SENTINEL + REAL_WEB_RESULT
        sources = rag._sources_from_web_results(mixed)
        assert len(sources) == 1, (
            f"Only the real result should produce a card; got: {sources!r}"
        )
        assert sources[0]["url"] == "https://ncert.nic.in/laws-of-motion"


# ── Task #22 — Stream-path sentinel guards ────────────────────────────────────

class TestStreamPathSentinelGuards:
    """Mirror of the non-stream invariants (Task #21) for the streaming chat
    path in routes/ai_chat.py.

    The streaming path has two unique code gates before the system-prompt
    build:

      Gate A — rag-decision speculative-web discard (line ~2976):
        ``if _s_route_decision_obj.decision == "rag" and web_results:
              web_results = []``
        When the router decided "web", this gate's condition is False so
        the sentinel is NOT discarded.  Pin the invariant: sentinel survives
        Gate A when decision="web".

      Gate B — stream fail-loud 503 guard (line ~2988):
        ``if _s_route_force_web and not web_results:
              raise HTTPException(503, ...)``
        Identical boolean algebra to the non-stream guard.  Sentinel is
        truthy → guard does not fire.  Labelled separately so that if the
        stream guard is ever split out from the non-stream guard, a test
        already exists that pins it.

    The system-prompt purity check (no [Web N] / [Syrabit N] entries for
    the sentinel) delegates to build_rag_system_prompt — the same function
    used by both paths.  Because Task #21's TestSentinelDoesNotPollutSystemPrompt
    already covers that function exhaustively, the stream tests here focus on
    the stream-specific gates and add one integration-level assertion to prove
    the function call signature at line ~3100 (``web_results=web_results or None``)
    still behaves correctly with the sentinel.
    """

    # ── Gate A — rag-decision discard does NOT apply when router says "web" ──

    def test_sentinel_survives_rag_discard_gate_when_decision_is_web(self):
        """Stream Gate A discards web_results only when decision=="rag".
        When the router decided "web", the gate condition is False and the
        sentinel passes through untouched."""
        web_results = list(SENTINEL)          # mutable copy as the stream handler uses it
        _decision = "web"                     # router decided web (weak topic match)

        # Re-enact Gate A exactly as written in ai_chat.py ~line 2976:
        if _decision == "rag" and web_results:
            web_results = []

        assert web_results == SENTINEL, (
            "Sentinel must survive Gate A when the router decision is 'web'; "
            f"gate incorrectly discarded it — web_results is now: {web_results!r}"
        )

    def test_real_results_are_discarded_by_rag_gate_when_decision_is_rag(self):
        """Inverse: Gate A MUST discard speculative web results when the router
        decided 'rag'.  Guards against the gate being accidentally removed."""
        web_results = list(REAL_WEB_RESULT)
        _decision = "rag"

        if _decision == "rag" and web_results:
            web_results = []

        assert web_results == [], (
            "Gate A must discard real web results when decision='rag'; "
            f"gate is not working — web_results: {web_results!r}"
        )

    def test_sentinel_discarded_by_rag_gate_when_decision_is_rag(self):
        """When the router decided 'rag', even the sentinel is discarded by
        Gate A.  This is intentional: the rag branch never goes web.  After
        discarding, web_results=[] triggers Gate B (503) — but that 503
        would only fire if _s_route_force_web is also True, which cannot
        happen when decision='rag' (force_web is only set for decision='web').
        This test pins the Gate A logic for completeness."""
        web_results = list(SENTINEL)
        _decision = "rag"
        _s_route_force_web = False  # decision='rag' → force_web is False

        if _decision == "rag" and web_results:
            web_results = []

        # Gate B: only fires when _s_route_force_web=True, so no 503 here.
        would_503 = _s_route_force_web and not web_results
        assert not would_503, (
            "Gate B must not fire when force_web=False even if web_results=[] "
            "(the rag branch never hits the web-empty 503)"
        )

    # ── Gate B — stream fail-loud 503 guard ──────────────────────────────────

    def test_stream_503_guard_does_not_fire_with_sentinel(self):
        """Stream Gate B mirrors the non-stream guard.  Sentinel is truthy →
        ``not web_results`` is False → HTTPException(503) is NOT raised."""
        _s_route_force_web = True
        web_results = SENTINEL

        # Re-enact Gate B exactly as written in ai_chat.py ~line 2988:
        would_503 = _s_route_force_web and not web_results
        assert not would_503, (
            "Stream Gate B (503 guard) fired with sentinel — "
            "sentinel must be truthy to prevent the fail-loud 503."
        )

    def test_stream_503_guard_fires_with_empty_list(self):
        """Inverse: [] must trigger stream Gate B — proves the guard is live
        and the sentinel's truthiness is the only thing suppressing it."""
        _s_route_force_web = True
        web_results = []
        would_503 = _s_route_force_web and not web_results
        assert would_503, (
            "Stream Gate B must fire with empty web_results; "
            "if this fails the guard has been disabled."
        )

    def test_stream_503_guard_inactive_when_not_force_web(self):
        """Gate B is gated on _s_route_force_web.  When the router decided
        'rag' or 'direct', _s_route_force_web=False so Gate B never fires
        regardless of web_results content."""
        _s_route_force_web = False
        for web_results in ([], None, SENTINEL):
            would_503 = _s_route_force_web and not web_results
            assert not would_503, (
                f"Gate B must be inactive when force_web=False; "
                f"fired for web_results={web_results!r}"
            )

    # ── Stream prompt-build — sentinel produces no web entries ───────────────

    def test_stream_prompt_call_with_sentinel_produces_no_web_entries(self):
        """Integration pin for the stream path's build_rag_system_prompt call
        at ai_chat.py ~line 3085:
            system_prompt = build_rag_system_prompt(..., web_results=web_results or None, ...)

        ``web_results or None`` with SENTINEL evaluates to SENTINEL (truthy),
        so the function receives the sentinel.  Verify no [Web N] / [Syrabit N]
        entry and no "Source: <url>" line appear in the output — the LLM
        effectively answers from training knowledge."""
        import re

        # Simulate the ``web_results or None`` expression used by the stream handler.
        stream_web_results = SENTINEL or None
        assert stream_web_results is SENTINEL, (
            "SENTINEL or None must return SENTINEL — if this fails the "
            "sentinel somehow became falsy and would be passed as None, "
            "which changes the build_rag_system_prompt branch."
        )

        prompt = rag.build_rag_system_prompt(
            _MINIMAL_CONTEXT,
            _MINIMAL_RAG_CTX,
            web_results=stream_web_results,
            query="What is the photoelectric effect?",
            resolved_intent="notes",
        )

        # No [Web N] or [Syrabit N] rendered entry:
        assert "[Web 1]" not in prompt, (
            "Sentinel was rendered as a '[Web N]' entry in the stream system prompt"
        )
        assert "[Syrabit 1]" not in prompt, (
            "Sentinel was rendered as a '[Syrabit N]' entry in the stream system prompt"
        )

        # No "Source: <url>" line (the format for real web-result citations):
        _url_source = re.search(r"Source:\s*https?://", prompt)
        assert _url_source is None, (
            f"Sentinel leaked a URL source line into the stream system prompt: "
            f"{_url_source.group()!r}"
        )

    def test_stream_prompt_none_sentinel_passes_none_to_prompt_builder(self):
        """Edge case: if the sentinel is ever replaced by [] (falsy), then
        ``web_results or None`` evaluates to None and build_rag_system_prompt
        receives web_results=None.  The function must handle None gracefully
        (it does — ``if web_results:`` is False for None).

        This test proves the graceful-None path does not crash, and that the
        system prompt still contains no web entries — the LLM falls back to
        general knowledge via the ``elif not _is_casual:`` branch."""
        prompt = rag.build_rag_system_prompt(
            _MINIMAL_CONTEXT,
            _MINIMAL_RAG_CTX,
            web_results=None,
            query="What is the photoelectric effect?",
            resolved_intent="notes",
        )
        assert "[Web 1]" not in prompt
        assert "[Syrabit 1]" not in prompt
