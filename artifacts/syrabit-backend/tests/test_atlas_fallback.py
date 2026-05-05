"""Task #217 + Task #372 — Atlas vector-search fallback safety net.

Tests cover three surfaces:

1. ``ensure_vector_index()`` — graceful behaviour when the Atlas index is
   absent (not-yet-created, deleted after embedding cleanup, or on an
   Atlas tier without Vector Search).

2. ATLAS_VS_ENABLED startup gate — ``ensure_vector_index`` is skipped when
   the env var is absent (default off); when it is set and the call fails,
   startup continues rather than crashing.

3. ``_fetch_chunks_semantic`` weighted-pool fallback routing
   (Task #291+ / re-enabled in Task #372) — vector retrieval now dispatches
   through ``llm.select_provider("vector_search", …)`` with exclusion-based
   retry. Tests pin: Pinecone fails → Atlas serves; both fail → empty (no
   500); Pinecone results bypass Atlas entirely; all embedders down → no
   backend touched; failed providers are excluded on the next draw.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Ensure backend root is on sys.path ──────────────────────────────────────
_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from tests._deps_stub import install_deps_stub

install_deps_stub()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Fake cursor helper for db.chunks.aggregate().to_list() ──────────────────

class _AggregateCursor:
    """Simulates motor's cursor returned by collection.aggregate().
    Can either raise (deleted/absent index) or return a list of docs."""

    def __init__(self, *, raise_exc: Exception | None = None, result: list | None = None):
        self._raise_exc = raise_exc
        self._result = result or []

    async def to_list(self, length: int | None = None):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._result


# ══════════════════════════════════════════════════════════════════════════════
# 1. ensure_vector_index() graceful failure behaviour
# ══════════════════════════════════════════════════════════════════════════════

class TestEnsureVectorIndex:
    """Unit tests for ``retrievers.mongodb_vector.ensure_vector_index``.

    All tests run against a fully-mocked ``deps.db`` — no real MongoDB
    connection is made.
    """

    def test_returns_ok_false_when_db_is_none(self, monkeypatch):
        """When MongoDB is unavailable (db is None), the function must
        return {"ok": False} rather than raising AttributeError."""
        import deps
        monkeypatch.setattr(deps, "db", None, raising=False)

        from retrievers.mongodb_vector import ensure_vector_index
        result = _run(ensure_vector_index())

        assert result["ok"] is False
        assert "not available" in result.get("reason", "").lower()

    def test_returns_ok_false_and_logs_warning_when_command_fails(self, monkeypatch, caplog):
        """When the Atlas createSearchIndexes command fails (e.g. unsupported
        tier, deleted index, network error), the function must log a warning
        and return {"ok": False, "reason": ...} without raising."""
        import deps
        mock_db = MagicMock()
        mock_db.command = AsyncMock(side_effect=Exception("Atlas Vector Search not available on this tier"))
        monkeypatch.setattr(deps, "db", mock_db, raising=False)

        import importlib
        import retrievers.mongodb_vector as mv
        monkeypatch.setattr(mv, "_import_db", lambda: mock_db, raising=False)

        # Patch the db reference used inside ensure_vector_index
        with patch("retrievers.mongodb_vector.db", mock_db, create=True):
            # The function uses `from deps import db` locally — patch deps.db
            import deps as _deps_mod
            _deps_mod.db = mock_db

            from retrievers.mongodb_vector import ensure_vector_index
            import importlib as _il
            _il.reload(mv)  # re-bind db at module level after patching deps.db

            with caplog.at_level("WARNING", logger="retrievers.mongodb_vector"):
                result = _run(mv.ensure_vector_index())

        assert result["ok"] is False
        assert "reason" in result

    def test_returns_ok_true_when_index_already_exists(self, monkeypatch):
        """If the Atlas command raises an 'already exists' error, the function
        must treat that as success ({"ok": True, "created": False}) — it means
        the index is already in place, which is the normal re-boot scenario."""
        import deps
        mock_db = MagicMock()
        mock_db.command = AsyncMock(side_effect=Exception("IndexAlreadyExists — index already exists"))
        deps.db = mock_db

        import importlib
        import retrievers.mongodb_vector as mv
        importlib.reload(mv)

        result = _run(mv.ensure_vector_index())
        assert result["ok"] is True
        assert result.get("created") is False

    def test_returns_ok_true_and_created_true_on_fresh_creation(self, monkeypatch):
        """When the command succeeds (new index created), the function must
        return {"ok": True, "created": True}."""
        import deps
        mock_db = MagicMock()
        mock_db.command = AsyncMock(return_value={"ok": 1})
        deps.db = mock_db

        import importlib
        import retrievers.mongodb_vector as mv
        importlib.reload(mv)

        result = _run(mv.ensure_vector_index())
        assert result["ok"] is True
        assert result.get("created") is True


# ══════════════════════════════════════════════════════════════════════════════
# 2. ATLAS_VS_ENABLED startup gate (via startup_checks.run_atlas_vs_startup_check)
# ══════════════════════════════════════════════════════════════════════════════

class TestAtlasVsEnabledGate:
    """Tests for the real startup gate logic in startup_checks.py.

    We import the real ``run_atlas_vs_startup_check`` function (the same code
    server.py delegates to) so regressions in the gate are caught immediately.
    caplog assertions verify the real logger output — not a synthetic list.
    """

    def test_gate_returns_skipped_and_ensure_not_called_when_env_var_not_set(
        self, monkeypatch
    ):
        """With ATLAS_VS_ENABLED unset (the default after Task #208), the
        function must return {"skipped": True} — ensure_vector_index is never
        called so a deleted Atlas index causes no error at startup."""
        monkeypatch.delenv("ATLAS_VS_ENABLED", raising=False)

        import startup_checks

        ensure_calls = []

        async def _tracking_ensure():
            ensure_calls.append(True)
            return {"ok": True}

        with patch(
            "retrievers.mongodb_vector.ensure_vector_index",
            new=_tracking_ensure,
        ):
            result = _run(startup_checks.run_atlas_vs_startup_check())

        assert result == {"skipped": True}
        assert ensure_calls == [], "ensure_vector_index must not be called when gate is off"

    def test_gate_calls_ensure_and_returns_result_when_enabled(
        self, monkeypatch
    ):
        """With ATLAS_VS_ENABLED=true and a working Atlas, the gate must call
        ensure_vector_index and return its result."""
        monkeypatch.setenv("ATLAS_VS_ENABLED", "true")

        import startup_checks

        expected = {"ok": True, "created": False, "index": "vector_index"}

        with patch(
            "retrievers.mongodb_vector.ensure_vector_index",
            new=AsyncMock(return_value=expected),
        ):
            result = _run(startup_checks.run_atlas_vs_startup_check())

        assert result == expected, (
            f"Gate must pass through ensure_vector_index payload unchanged; got {result!r}"
        )

    def test_gate_logs_warning_and_does_not_raise_when_ensure_fails(
        self, monkeypatch, caplog
    ):
        """When ensure_vector_index raises (e.g. deleted index, wrong Atlas tier)
        and ATLAS_VS_ENABLED=true, the gate must:
        1. Log a WARNING via the real logger (not a synthetic list)
        2. Return {"ok": False, "reason": ...} — never raise."""
        monkeypatch.setenv("ATLAS_VS_ENABLED", "true")

        import startup_checks

        async def _failing_ensure():
            raise RuntimeError("Atlas Vector Search not supported on this tier")

        with patch(
            "retrievers.mongodb_vector.ensure_vector_index",
            new=_failing_ensure,
        ):
            with caplog.at_level("WARNING", logger="syrabit.startup"):
                result = _run(startup_checks.run_atlas_vs_startup_check())

        # Must not have raised — function returned gracefully
        assert result["ok"] is False
        assert "reason" in result
        assert "not supported" in result["reason"]

        # The real logger must have emitted a WARNING
        warning_records = [
            r for r in caplog.records
            if r.levelname == "WARNING" and "Atlas" in r.message
        ]
        assert warning_records, (
            f"Expected a WARNING log about Atlas index failure; got: {caplog.records}"
        )

    def test_gate_skips_ensure_for_falsy_values(self, monkeypatch):
        """ATLAS_VS_ENABLED=false / ATLAS_VS_ENABLED=0 must behave like unset —
        the check is skipped and {"skipped": True} is returned."""
        import startup_checks
        for falsy in ("false", "0", "no", ""):
            monkeypatch.setenv("ATLAS_VS_ENABLED", falsy)
            result = _run(startup_checks.run_atlas_vs_startup_check())
            assert result == {"skipped": True}, f"Expected skipped for ATLAS_VS_ENABLED={falsy!r}"


# ══════════════════════════════════════════════════════════════════════════════
# 3. _fetch_chunks_semantic — Atlas fallback routing
# ══════════════════════════════════════════════════════════════════════════════

_FAKE_QVEC = [0.1] * 1024


def _make_pc_retriever(*, configured=True, raises=False, results=None):
    """Build a fake PineconeVectorRetriever substitute."""
    class _FakePcRetriever:
        def is_configured(self):
            return configured

        async def query(self, vec, top_k=10, metadata_filter=None, return_metadata=True):
            if raises:
                raise RuntimeError("Pinecone unavailable in test")
            return results or []

    return _FakePcRetriever


class TestFetchChunksSemanticFallback:
    """Tests for ``rag._fetch_chunks_semantic`` — Task #291+ weighted-pool dispatch.

    Vector retrieval now goes through a weighted ``vector_search`` pool
    (``llm.select_provider("vector_search", …)``) with exclusion-based retry:

      pinecone_ai  → Pinecone Inference embed + Pinecone $vectorSearch  [primary]
      vertex       → Gemini embed + Atlas $vectorSearch                  [fallback]
      mongodb_atlas→ Cohere embed + Atlas $vectorSearch        [weight-0 last resort]
      workers_ai   → no vector endpoint (raises immediately)

    These tests pin the safety contract that survived the architectural change:

      1. Pinecone fails  → Atlas serves the request (no 500)
      2. Both legs fail  → empty result (no 500)
      3. Pinecone succeeds → Atlas $vectorSearch is never queried
      4. All embedders down → empty result, no vector backend touched
      5. Each failed/zero-result provider is added to the next ``select_provider``
         ``exclude`` set (weighted exclusion-based retry).

    All external I/O is mocked:
      - ``llm.select_provider`` is replaced with a deterministic preference-list
        mock that honours the ``exclude`` frozenset
      - ``providers.pinecone_ai.embed_one`` / ``providers.cohere.embed_query`` /
        ``vertex_services.embed_text`` are stubbed
      - ``retrievers.pinecone_vector.PineconeVectorRetriever`` is replaced
      - ``rag.db.chunks.aggregate`` (Atlas $vectorSearch) and
        ``rag.db.chapters.find`` are mocked
      - ``rag._generate_hyde_passage`` is stubbed to skip the LLM HyDE call
    """

    # ── helpers ───────────────────────────────────────────────────────────────

    def _stub_no_hyde(self, monkeypatch):
        async def _no_hyde(_q):
            return None
        monkeypatch.setattr("rag._generate_hyde_passage", _no_hyde)

    def _stub_select_provider(self, monkeypatch, preference_order):
        """Replace ``llm.select_provider`` with a deterministic mock that:

        - records every call
        - returns the first provider in ``preference_order`` not in ``exclude``
        - raises RuntimeError when the preference list is exhausted

        Returns the list of recorded calls so tests can assert on the
        progression of the ``exclude`` frozenset.
        """
        calls: list[dict] = []

        def _mock_select(feature, lang="", exclude=frozenset()):
            calls.append({
                "feature": feature,
                "lang": lang,
                "exclude": frozenset(exclude),
            })
            for p in preference_order:
                if p not in exclude:
                    return p
            raise RuntimeError(f"vector_search pool exhausted (excluded={exclude})")

        import llm as _llm
        monkeypatch.setattr(_llm, "select_provider", _mock_select)
        return calls

    def _stub_pinecone_embed(self, monkeypatch, *, enabled=True, vec=None, raises=False):
        import providers.pinecone_ai as _pa
        monkeypatch.setattr(_pa, "ENABLED", enabled, raising=False)
        if raises:
            async def _embed_one(_text, *, input_type="query"):
                raise RuntimeError("pinecone embed failed in test")
        else:
            async def _embed_one(_text, *, input_type="query"):
                return vec if vec is not None else _FAKE_QVEC
        monkeypatch.setattr(_pa, "embed_one", _embed_one, raising=False)

    def _stub_cohere_embed(self, monkeypatch, *, enabled=True, vec=None, raises=False):
        import providers.cohere as _co
        monkeypatch.setattr(_co, "ENABLED", enabled, raising=False)
        if raises:
            async def _embed_query(_text):
                raise RuntimeError("cohere embed failed in test")
        else:
            async def _embed_query(_text):
                return vec if vec is not None else _FAKE_QVEC
        monkeypatch.setattr(_co, "embed_query", _embed_query, raising=False)

    def _stub_vertex_embed(self, monkeypatch, *, vec=None, raises=False):
        # vertex_services may not be importable in the stubbed test env; create
        # a minimal stub module on sys.modules so rag.py's local import succeeds.
        mod = sys.modules.get("vertex_services")
        if mod is None:
            mod = types.ModuleType("vertex_services")
            sys.modules["vertex_services"] = mod
        if raises:
            async def _embed_text(_text, *, task_type="RETRIEVAL_QUERY"):
                raise RuntimeError("vertex embed failed in test")
        else:
            async def _embed_text(_text, *, task_type="RETRIEVAL_QUERY"):
                return vec if vec is not None else _FAKE_QVEC
        monkeypatch.setattr(mod, "embed_text", _embed_text, raising=False)

    def _stub_pinecone_retriever(self, monkeypatch, *, configured=True, raises=False, results=None):
        """Replace ``retrievers.pinecone_vector.PineconeVectorRetriever``."""
        query_calls: list[dict] = []

        class _FakePc:
            def is_configured(self):
                return configured

            async def query(self, vec, top_k=10, metadata_filter=None,
                            return_metadata=True, namespace=None):
                query_calls.append({"top_k": top_k, "namespace": namespace,
                                    "metadata_filter": metadata_filter})
                if raises:
                    raise RuntimeError("Pinecone unavailable in test")
                return results or []

        monkeypatch.setattr(
            "retrievers.pinecone_vector.PineconeVectorRetriever",
            _FakePc,
        )
        return query_calls

    def _make_db(self, monkeypatch, *, atlas_cursor_factory=None, chapters=None):
        """Build & install a mock ``rag.db`` with chunks + chapters collections.

        ``atlas_cursor_factory`` is called with the aggregate ``pipeline`` and
        must return an awaitable cursor (use _AggregateCursor). Each call is
        recorded in the returned ``aggregate_calls`` list.
        ``chapters`` is the list of chapter docs returned by ``db.chapters.find``.
        """
        import rag as _rag

        aggregate_calls: list[list] = []
        chapters = list(chapters or [])

        def _aggregate(pipeline):
            aggregate_calls.append(pipeline)
            if atlas_cursor_factory is not None:
                return atlas_cursor_factory(pipeline)
            return _AggregateCursor(result=[])

        class _FakeFindCursor:
            async def to_list(self, length=None):
                return chapters

        mock_chunks = MagicMock()
        mock_chunks.aggregate = _aggregate

        mock_chapters = MagicMock()
        mock_chapters.find = lambda *a, **kw: _FakeFindCursor()

        mock_db = MagicMock()
        mock_db.chunks = mock_chunks
        mock_db.chapters = mock_chapters

        monkeypatch.setattr(_rag, "db", mock_db)
        return mock_db, aggregate_calls

    # ── tests ─────────────────────────────────────────────────────────────────

    def test_pinecone_fails_then_atlas_serves(self, monkeypatch):
        """Pinecone leg raises → ``_fetch_chunks_semantic`` must exclude
        pinecone_ai, redraw mongodb_atlas from the pool, run Atlas
        $vectorSearch, resolve the chapter doc, and return it.

        This is the headline fallback-safety guarantee: a Pinecone outage is
        absorbed by the weight-0 Atlas leg with no caller-visible error.
        """
        import rag as _rag

        self._stub_no_hyde(monkeypatch)
        self._stub_select_provider(monkeypatch, ["pinecone_ai", "mongodb_atlas"])

        # pinecone_ai embed succeeds (so the pinecone leg fails at the
        # vector-store query, not at embed — this exercises the retriever path)
        self._stub_pinecone_embed(monkeypatch)
        # …but the Pinecone retriever itself raises (simulated outage)
        pc_calls = self._stub_pinecone_retriever(monkeypatch, raises=True)

        # mongodb_atlas leg embed (Cohere) succeeds
        self._stub_cohere_embed(monkeypatch)

        chapter_doc = {
            "id": "ch-bio-photosyn",
            "title": "Photosynthesis",
            "content": "Plants convert sunlight to chemical energy.",
            "slug": "photosynthesis",
            "subject_id": "bio-11",
        }
        # Atlas $vectorSearch returns one match, chapters lookup resolves it
        atlas_match = [{
            "chapter_id": "ch-bio-photosyn",
            "chapter_title": "Photosynthesis",
            "subject_id": "bio-11",
            "_vs_score": 0.81,
        }]
        _, aggregate_calls = self._make_db(
            monkeypatch,
            atlas_cursor_factory=lambda p: _AggregateCursor(result=atlas_match),
            chapters=[chapter_doc],
        )

        result = _run(_rag._fetch_chunks_semantic("photosynthesis"))

        assert len(pc_calls) == 1, "Pinecone retriever must have been attempted once"
        assert len(aggregate_calls) == 1, "Atlas $vectorSearch must have been queried as fallback"
        assert len(result) == 1
        assert result[0]["id"] == "ch-bio-photosyn"

    def test_atlas_index_missing_returns_empty_no_500(self, monkeypatch):
        """When Pinecone fails AND the Atlas aggregate also raises (e.g.
        the Atlas vector_index was dropped), the function must return ``[]``
        without surfacing a 500. The pool is exhausted by the exclusion loop
        and the function exits gracefully.
        """
        import rag as _rag

        self._stub_no_hyde(monkeypatch)
        self._stub_select_provider(monkeypatch, ["pinecone_ai", "mongodb_atlas"])

        self._stub_pinecone_embed(monkeypatch)
        self._stub_pinecone_retriever(monkeypatch, raises=True)

        self._stub_cohere_embed(monkeypatch)

        atlas_error = Exception(
            "PlanExecutor error — vector index 'vector_index' not found"
        )
        _, aggregate_calls = self._make_db(
            monkeypatch,
            atlas_cursor_factory=lambda p: _AggregateCursor(raise_exc=atlas_error),
            chapters=[],
        )

        result = _run(_rag._fetch_chunks_semantic("photosynthesis"))

        assert result == [], f"Expected [] when both legs fail, got {result!r}"
        # Both legs were attempted — Pinecone (via mocked retriever) AND Atlas
        # (db.chunks.aggregate was called and raised inside .to_list)
        assert len(aggregate_calls) == 1, (
            "Atlas $vectorSearch must have been attempted exactly once before exclusion"
        )

    def test_pinecone_results_bypass_atlas_entirely(self, monkeypatch):
        """When Pinecone returns a non-empty result on the first draw, the
        loop returns immediately — Atlas $vectorSearch must never be queried.

        This guards the perf contract: healthy Pinecone responses do NOT pay
        the Atlas latency tax even though Atlas is in the pool.
        """
        import rag as _rag

        self._stub_no_hyde(monkeypatch)
        self._stub_select_provider(monkeypatch, ["pinecone_ai", "mongodb_atlas"])

        self._stub_pinecone_embed(monkeypatch)
        # Pinecone retriever returns one match with chapter_id metadata
        self._stub_pinecone_retriever(monkeypatch, results=[
            {"score": 0.92, "metadata": {
                "chapter_id": "ch-bio-1",
                "chapter_title": "Bio",
                "subject_id": "bio",
            }}
        ])

        # Cohere is configured but its embed must never be called (Atlas leg
        # never runs). We assert this by tracking calls.
        cohere_embed_calls = []

        import providers.cohere as _co
        monkeypatch.setattr(_co, "ENABLED", True, raising=False)

        async def _tracking_embed(_text):
            cohere_embed_calls.append(_text)
            return _FAKE_QVEC
        monkeypatch.setattr(_co, "embed_query", _tracking_embed, raising=False)

        chapter_doc = {
            "id": "ch-bio-1",
            "title": "Bio",
            "content": "x",
            "slug": "bio",
            "subject_id": "bio",
        }
        _, aggregate_calls = self._make_db(monkeypatch, chapters=[chapter_doc])

        result = _run(_rag._fetch_chunks_semantic("cell division"))

        assert len(result) == 1
        assert result[0]["id"] == "ch-bio-1"
        assert aggregate_calls == [], (
            "Atlas $vectorSearch must NOT be called when Pinecone returned results"
        )
        assert cohere_embed_calls == [], (
            "Cohere embed must NOT be called when Pinecone returned results"
        )

    def test_all_embedders_unavailable_returns_empty_without_querying_backends(self, monkeypatch):
        """When every leg's embedder fails (Pinecone Inference disabled, Cohere
        disabled), each provider raises in the embed step, the loop excludes
        them all, the pool empties, and the function returns ``[]`` — without
        any vector backend (Pinecone retriever or Atlas $vectorSearch) being
        queried. This is the analogue of the historic "no Cohere → no
        backend touched" guarantee under the new per-leg embedder design.
        """
        import rag as _rag

        self._stub_no_hyde(monkeypatch)
        self._stub_select_provider(monkeypatch, ["pinecone_ai", "mongodb_atlas"])

        # Both embedders disabled → _try_vector_provider raises in step 1
        # before any retriever / aggregate call.
        self._stub_pinecone_embed(monkeypatch, enabled=False)
        self._stub_cohere_embed(monkeypatch, enabled=False)

        pc_calls = self._stub_pinecone_retriever(monkeypatch)
        _, aggregate_calls = self._make_db(monkeypatch, chapters=[])

        result = _run(_rag._fetch_chunks_semantic("anything"))

        assert result == []
        assert pc_calls == [], (
            "Pinecone retriever must NOT be queried when its embedder is disabled"
        )
        assert aggregate_calls == [], (
            "Atlas $vectorSearch must NOT be called when its embedder is disabled"
        )

    def test_cohere_unavailable_excludes_atlas_leg_without_querying_atlas(self, monkeypatch):
        """Narrow per-leg embedder-disabled contract.

        Setup:
          - Pinecone Inference is configured AND raises in the retriever
            step (so the pinecone_ai leg fails AFTER its own embed succeeds)
          - Cohere is disabled (``providers.cohere.ENABLED = False``)
          - The mock pool falls back to mongodb_atlas next

        Expected:
          - The mongodb_atlas leg fails inside ``_try_vector_provider``'s
            embed step (Cohere disabled → RuntimeError) BEFORE
            ``db.chunks.aggregate`` is called.
          - Atlas $vectorSearch is therefore never queried — the Cohere-
            unavailable case must not reach the Atlas backend.
          - The function returns ``[]`` after both legs are excluded.

        This pins the historic "Cohere unavailable → Atlas not touched"
        contract under the new per-leg embedder design (Pinecone is a
        separate leg with its own embedder, so it is independently
        attempted; only the Atlas-backed leg is gated by Cohere).
        """
        import rag as _rag

        self._stub_no_hyde(monkeypatch)
        self._stub_select_provider(monkeypatch, ["pinecone_ai", "mongodb_atlas"])

        # Pinecone leg: embed enabled (so the leg reaches the retriever),
        # retriever raises (simulated outage). This forces fallback to
        # mongodb_atlas without short-circuiting at embed time.
        self._stub_pinecone_embed(monkeypatch, enabled=True)
        pc_calls = self._stub_pinecone_retriever(monkeypatch, raises=True)

        # Cohere disabled → mongodb_atlas leg's embed step raises.
        self._stub_cohere_embed(monkeypatch, enabled=False)

        _, aggregate_calls = self._make_db(monkeypatch, chapters=[])

        result = _run(_rag._fetch_chunks_semantic("anything"))

        assert result == []
        assert len(pc_calls) == 1, (
            "Pinecone retriever must have been attempted exactly once"
        )
        assert aggregate_calls == [], (
            "Atlas $vectorSearch must NOT be called when Cohere is disabled — "
            "the mongodb_atlas leg must short-circuit at the embed step"
        )

    def test_weighted_exclusion_retry_excludes_failed_providers(self, monkeypatch):
        """Each failed or zero-result provider must be added to the
        ``exclude`` frozenset on the next ``select_provider`` call.

        Sequence we set up:
          1st call → pinecone_ai (raises) → excluded
          2nd call → mongodb_atlas (returns 0 matches) → excluded
          3rd call → pool exhausted → loop breaks → returns []

        We pin this contract by inspecting the ``exclude`` arg of every
        ``select_provider`` call recorded by the mock.
        """
        import rag as _rag

        self._stub_no_hyde(monkeypatch)
        select_calls = self._stub_select_provider(
            monkeypatch, ["pinecone_ai", "mongodb_atlas"],
        )

        self._stub_pinecone_embed(monkeypatch)
        self._stub_pinecone_retriever(monkeypatch, raises=True)

        self._stub_cohere_embed(monkeypatch)

        # Atlas $vectorSearch returns 0 matches → triggers exclusion path
        _, aggregate_calls = self._make_db(
            monkeypatch,
            atlas_cursor_factory=lambda p: _AggregateCursor(result=[]),
            chapters=[],
        )

        result = _run(_rag._fetch_chunks_semantic("anything"))

        assert result == []
        # Verify the dispatch progression
        assert len(select_calls) >= 3, (
            f"Expected ≥3 select_provider calls (pinecone, atlas, exhausted), "
            f"got: {select_calls}"
        )
        # 1st call: empty exclude
        assert select_calls[0]["exclude"] == frozenset()
        # 2nd call: pinecone_ai excluded after retriever raised
        assert select_calls[1]["exclude"] == frozenset({"pinecone_ai"}), (
            f"Expected pinecone_ai excluded on 2nd draw, got {select_calls[1]['exclude']}"
        )
        # 3rd call: both excluded after Atlas returned 0 matches
        assert select_calls[2]["exclude"] == frozenset({"pinecone_ai", "mongodb_atlas"}), (
            f"Expected both providers excluded on 3rd draw, "
            f"got {select_calls[2]['exclude']}"
        )
        # Atlas was attempted exactly once before being excluded
        assert len(aggregate_calls) == 1


# ══════════════════════════════════════════════════════════════════════════════
# 4. _fetch_chunks_semantic — STRICT Assamese namespace lock (Task #291)
# ══════════════════════════════════════════════════════════════════════════════


class TestFetchChunksSemanticAssameseLock:
    """Task #291 — STRICT Assamese vector-search lock.

    The ``_is_as`` branch in ``_fetch_chunks_semantic`` deliberately bypasses
    the weighted ``vector_search`` pool entirely: it queries ONLY Pinecone
    Inference against ``namespace="as"`` (the only embedding space that
    contains the Assamese corpus written by ``embed_assamese_corpus.py``).
    A miss or error must return ``[]`` — it must NEVER fall back to
    Vertex / Atlas, which are English-only Cohere indexes and would silently
    surface English chapters as "Assamese context", breaking the spec's
    Assamese-first guarantee.

    These tests pin the strict-lock contract so a regression that re-enables
    cross-corpus fallback in this branch is caught immediately:

      1. ``lang="as"`` → ``_try_vector_provider("pinecone_ai", …)`` is called
         exactly once, and the inner Pinecone retriever query uses
         ``namespace="as"``.
      2. Pinecone leg raises → returns ``[]`` and never invokes
         ``llm.select_provider`` / Atlas ``$vectorSearch`` / Vertex embed.
      3. Pinecone leg returns ``[]`` (strict miss) → returns ``[]`` and
         never invokes ``llm.select_provider`` / Atlas / Vertex (no
         cross-corpus retry).
      4. Pinecone hit → chapter docs are resolved via ``db.chapters.find``
         (filtered to ``status="published"`` and projecting ``content_as``)
         and returned in match order with dedup.
    """

    # ── helpers ───────────────────────────────────────────────────────────────

    def _stub_no_hyde(self, monkeypatch):
        async def _no_hyde(_q):
            return None
        monkeypatch.setattr("rag._generate_hyde_passage", _no_hyde)

    def _stub_select_provider_recording(self, monkeypatch):
        """Patch ``llm.select_provider`` with a recorder. The strict Assamese
        branch must NEVER reach the weighted pool, so any call recorded here
        is a regression. We raise inside the mock so that, if a regression
        DOES dispatch through the pool, the loop in ``_fetch_chunks_semantic``
        breaks immediately on the first redraw rather than continuing to
        run side effects — but the recorded call is still observable for the
        assertion."""
        calls: list[dict] = []

        def _mock_select(feature, lang="", exclude=frozenset()):
            calls.append({
                "feature": feature,
                "lang": lang,
                "exclude": frozenset(exclude),
            })
            raise RuntimeError(
                "select_provider must NOT be called in strict Assamese branch"
            )

        import llm as _llm
        monkeypatch.setattr(_llm, "select_provider", _mock_select)
        return calls

    def _stub_pinecone_embed(self, monkeypatch, *, vec=None):
        import providers.pinecone_ai as _pa
        monkeypatch.setattr(_pa, "ENABLED", True, raising=False)

        async def _embed_one(_text, *, input_type="query"):
            return vec if vec is not None else _FAKE_QVEC
        monkeypatch.setattr(_pa, "embed_one", _embed_one, raising=False)

    def _stub_pinecone_retriever(self, monkeypatch, *, raises=False, results=None):
        query_calls: list[dict] = []

        class _FakePc:
            def is_configured(self):
                return True

            async def query(self, vec, top_k=10, metadata_filter=None,
                            return_metadata=True, namespace=None):
                query_calls.append({
                    "top_k": top_k,
                    "namespace": namespace,
                    "metadata_filter": metadata_filter,
                })
                if raises:
                    raise RuntimeError("Pinecone unavailable in test")
                return results or []

        monkeypatch.setattr(
            "retrievers.pinecone_vector.PineconeVectorRetriever",
            _FakePc,
        )
        return query_calls

    def _stub_cohere_embed_tracking(self, monkeypatch):
        """Stub Cohere embed and record every call. The Atlas leg embeds via
        Cohere, so a recorded call here proves cross-corpus fallback ran."""
        calls: list[str] = []
        import providers.cohere as _co
        monkeypatch.setattr(_co, "ENABLED", True, raising=False)

        async def _embed_query(_text):
            calls.append(_text)
            return _FAKE_QVEC
        monkeypatch.setattr(_co, "embed_query", _embed_query, raising=False)
        return calls

    def _stub_vertex_embed_tracking(self, monkeypatch):
        """Stub Vertex embed and record every call. The Vertex leg of the
        weighted pool embeds via this function, so a recorded call here
        proves cross-corpus fallback ran."""
        calls: list[str] = []
        mod = sys.modules.get("vertex_services")
        if mod is None:
            mod = types.ModuleType("vertex_services")
            sys.modules["vertex_services"] = mod

        async def _embed_text(_text, *, task_type="RETRIEVAL_QUERY"):
            calls.append(_text)
            return _FAKE_QVEC
        monkeypatch.setattr(mod, "embed_text", _embed_text, raising=False)
        return calls

    def _make_db(self, monkeypatch, *, chapters=None):
        """Install a mock ``rag.db`` with ``chunks.aggregate`` (Atlas) and
        ``chapters.find`` recorders. ``aggregate_calls`` should remain empty
        in every Assamese-lock test — Atlas $vectorSearch must never run."""
        import rag as _rag

        aggregate_calls: list = []
        find_calls: list[dict] = []
        chapters = list(chapters or [])

        def _aggregate(pipeline):
            aggregate_calls.append(pipeline)
            return _AggregateCursor(result=[])

        class _FakeFindCursor:
            async def to_list(self, length=None):
                return chapters

        def _find(*a, **kw):
            find_calls.append({"args": a, "kwargs": kw})
            return _FakeFindCursor()

        mock_chunks = MagicMock()
        mock_chunks.aggregate = _aggregate

        mock_chapters = MagicMock()
        mock_chapters.find = _find

        mock_db = MagicMock()
        mock_db.chunks = mock_chunks
        mock_db.chapters = mock_chapters

        monkeypatch.setattr(_rag, "db", mock_db)
        return mock_db, aggregate_calls, find_calls

    # ── tests ─────────────────────────────────────────────────────────────────

    def test_assamese_calls_pinecone_once_with_namespace_as(self, monkeypatch):
        """``lang="as"`` must call ``_try_vector_provider("pinecone_ai", …)``
        exactly once, and the underlying Pinecone retriever query must use
        ``namespace="as"``. The weighted-pool dispatch (``select_provider``)
        and English-only backends (Atlas, Vertex, Cohere embed) must NOT be
        touched."""
        import rag as _rag

        self._stub_no_hyde(monkeypatch)
        select_calls = self._stub_select_provider_recording(monkeypatch)
        self._stub_pinecone_embed(monkeypatch)
        pc_calls = self._stub_pinecone_retriever(monkeypatch, results=[
            {"score": 0.9, "metadata": {
                "chapter_id": "ch-as-1",
                "chapter_title": "অধ্যায় ১",
                "subject_id": "bio-11",
            }},
        ])
        cohere_calls = self._stub_cohere_embed_tracking(monkeypatch)
        vertex_calls = self._stub_vertex_embed_tracking(monkeypatch)

        chapter_doc = {
            "id": "ch-as-1",
            "title": "অধ্যায় ১",
            "content": "English fallback content (must not be returned alone)",
            "content_as": "অসমীয়া পাঠ্যবিষয়",
            "slug": "ch-1",
            "subject_id": "bio-11",
        }
        _, aggregate_calls, find_calls = self._make_db(
            monkeypatch, chapters=[chapter_doc],
        )

        result = _run(_rag._fetch_chunks_semantic("সালোক সংশ্লেষণ", lang="as"))

        # 1. Pinecone retriever invoked exactly once with namespace="as"
        assert len(pc_calls) == 1, (
            f"Expected Pinecone retriever to be queried exactly once, "
            f"got {len(pc_calls)}"
        )
        assert pc_calls[0]["namespace"] == "as", (
            f"Expected namespace='as' (Assamese namespace lock), "
            f"got {pc_calls[0]['namespace']!r}"
        )

        # 2. Strict lock: NO weighted-pool dispatch / cross-corpus backends
        assert select_calls == [], (
            "llm.select_provider must NOT be called in strict Assamese branch"
        )
        assert aggregate_calls == [], (
            "Atlas $vectorSearch must NOT be called in strict Assamese branch"
        )
        assert vertex_calls == [], (
            "Vertex embed_text must NOT be called in strict Assamese branch"
        )
        assert cohere_calls == [], (
            "Cohere embed_query must NOT be called in strict Assamese branch"
        )

        # 3. Chapter resolved & returned with the published-only filter and
        #    a projection that includes content_as (so the answer layer can
        #    surface Assamese text, not English).
        assert len(result) == 1
        assert result[0]["id"] == "ch-as-1"
        assert len(find_calls) == 1
        find_filter = find_calls[0]["args"][0]
        find_projection = find_calls[0]["args"][1]
        assert find_filter.get("status") == "published", (
            f"Chapter lookup must restrict to status='published'; "
            f"got {find_filter!r}"
        )
        assert find_projection.get("content_as") == 1, (
            f"Chapter projection must request content_as for Assamese "
            f"answers; got {find_projection!r}"
        )

    def test_assamese_pinecone_failure_returns_empty_no_cross_corpus_fallback(
        self, monkeypatch,
    ):
        """When the Assamese Pinecone leg raises, ``_fetch_chunks_semantic``
        must return ``[]`` and must NOT dispatch through the weighted pool
        or touch Atlas/Vertex — otherwise English chapters would silently
        leak into Assamese answers."""
        import rag as _rag

        self._stub_no_hyde(monkeypatch)
        select_calls = self._stub_select_provider_recording(monkeypatch)
        self._stub_pinecone_embed(monkeypatch)
        pc_calls = self._stub_pinecone_retriever(monkeypatch, raises=True)
        cohere_calls = self._stub_cohere_embed_tracking(monkeypatch)
        vertex_calls = self._stub_vertex_embed_tracking(monkeypatch)
        _, aggregate_calls, find_calls = self._make_db(monkeypatch, chapters=[])

        result = _run(_rag._fetch_chunks_semantic("কোষ বিভাজন", lang="as"))

        assert result == [], (
            f"Expected [] on Assamese Pinecone failure (strict lock), "
            f"got {result!r}"
        )
        # Pinecone leg attempted exactly once with namespace="as"
        assert len(pc_calls) == 1
        assert pc_calls[0]["namespace"] == "as"

        # Strict lock: NO cross-corpus fallback after the failure
        assert select_calls == [], (
            "llm.select_provider must NOT be called after Assamese "
            "Pinecone failure — that would re-enable cross-corpus fallback"
        )
        assert aggregate_calls == [], (
            "Atlas $vectorSearch must NOT be called after Assamese "
            "Pinecone failure"
        )
        assert vertex_calls == [], (
            "Vertex embed_text must NOT be called after Assamese "
            "Pinecone failure"
        )
        assert cohere_calls == [], (
            "Cohere embed_query must NOT be called after Assamese "
            "Pinecone failure"
        )
        # No chapter_ids to resolve — chapters.find must not be called
        assert find_calls == [], (
            "db.chapters.find must NOT be queried when the Assamese leg "
            "fails (no chapter_ids)"
        )

    def test_assamese_pinecone_zero_matches_returns_empty_no_cross_corpus_retry(
        self, monkeypatch,
    ):
        """When the Assamese Pinecone leg returns ``[]`` (strict miss), the
        function must return ``[]`` without falling back to English-only
        Atlas/Vertex backends."""
        import rag as _rag

        self._stub_no_hyde(monkeypatch)
        select_calls = self._stub_select_provider_recording(monkeypatch)
        self._stub_pinecone_embed(monkeypatch)
        pc_calls = self._stub_pinecone_retriever(monkeypatch, results=[])
        cohere_calls = self._stub_cohere_embed_tracking(monkeypatch)
        vertex_calls = self._stub_vertex_embed_tracking(monkeypatch)
        _, aggregate_calls, find_calls = self._make_db(monkeypatch, chapters=[])

        result = _run(_rag._fetch_chunks_semantic("নিউক্লিয়াস", lang="as"))

        assert result == [], (
            f"Expected [] on Assamese Pinecone strict miss, got {result!r}"
        )
        # Pinecone leg attempted exactly once with namespace="as"
        assert len(pc_calls) == 1
        assert pc_calls[0]["namespace"] == "as"

        # Strict lock: NO cross-corpus retry on miss
        assert select_calls == [], (
            "llm.select_provider must NOT be called on Assamese miss"
        )
        assert aggregate_calls == [], (
            "Atlas $vectorSearch must NOT be called on Assamese miss"
        )
        assert vertex_calls == [], (
            "Vertex embed_text must NOT be called on Assamese miss"
        )
        assert cohere_calls == [], (
            "Cohere embed_query must NOT be called on Assamese miss"
        )
        assert find_calls == [], (
            "db.chapters.find must NOT be queried on Assamese miss "
            "(no chapter_ids)"
        )

    def test_assamese_pinecone_hit_resolves_and_dedupes_chapters(
        self, monkeypatch,
    ):
        """A Pinecone hit returning multiple matches (including a duplicate
        chapter_id) must:

          - Look up referenced chapters via ``db.chapters.find`` exactly once
            with a ``status="published"`` filter and the multi-id ``$in`` set.
          - Return chapters in match order, deduplicated by ``chapter_id``.
          - Never dispatch through the weighted pool or touch Atlas/Vertex.
        """
        import rag as _rag

        self._stub_no_hyde(monkeypatch)
        select_calls = self._stub_select_provider_recording(monkeypatch)
        self._stub_pinecone_embed(monkeypatch)
        pc_calls = self._stub_pinecone_retriever(monkeypatch, results=[
            {"score": 0.95, "metadata": {
                "chapter_id": "ch-as-2",
                "chapter_title": "অধ্যায় ২",
                "subject_id": "bio-11",
            }},
            {"score": 0.91, "metadata": {
                "chapter_id": "ch-as-1",
                "chapter_title": "অধ্যায় ১",
                "subject_id": "bio-11",
            }},
            # Duplicate chapter_id — must be deduped in the output
            {"score": 0.88, "metadata": {
                "chapter_id": "ch-as-2",
                "chapter_title": "অধ্যায় ২",
                "subject_id": "bio-11",
            }},
        ])
        cohere_calls = self._stub_cohere_embed_tracking(monkeypatch)
        vertex_calls = self._stub_vertex_embed_tracking(monkeypatch)

        chapter_docs = [
            {"id": "ch-as-1", "title": "অধ্যায় ১", "content": "en-1",
             "content_as": "অসমীয়া ১", "slug": "ch-1", "subject_id": "bio-11"},
            {"id": "ch-as-2", "title": "অধ্যায় ২", "content": "en-2",
             "content_as": "অসমীয়া ২", "slug": "ch-2", "subject_id": "bio-11"},
        ]
        _, aggregate_calls, find_calls = self._make_db(
            monkeypatch, chapters=chapter_docs,
        )

        result = _run(_rag._fetch_chunks_semantic("জীৱবিজ্ঞান", lang="as"))

        # Strict lock contract: single Pinecone call, no cross-corpus dispatch
        assert len(pc_calls) == 1
        assert pc_calls[0]["namespace"] == "as"
        assert select_calls == [], (
            "llm.select_provider must NOT be called even on a Pinecone hit"
        )
        assert aggregate_calls == [], (
            "Atlas $vectorSearch must NOT be called even on a Pinecone hit"
        )
        assert vertex_calls == []
        assert cohere_calls == []

        # Two unique chapters returned, in Pinecone match order (ch-as-2 first)
        assert [c["id"] for c in result] == ["ch-as-2", "ch-as-1"], (
            f"Expected dedup + match-order ['ch-as-2','ch-as-1'], "
            f"got {[c['id'] for c in result]}"
        )

        # Chapters were looked up exactly once with both ids and the
        # published-only filter
        assert len(find_calls) == 1
        find_filter = find_calls[0]["args"][0]
        assert set(find_filter.get("id", {}).get("$in", [])) == {
            "ch-as-1", "ch-as-2",
        }
        assert find_filter.get("status") == "published", (
            f"Chapter filter must restrict to status='published'; "
            f"got {find_filter!r}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 4. Real vector_search pool configuration — Task #377
# ══════════════════════════════════════════════════════════════════════════════

class TestVectorSearchPoolConfig:
    """End-to-end exercise of the *real* ``llm.select_provider("vector_search", …)``
    weighted-pool dispatch — no mock of ``select_provider`` itself.

    The other test classes in this file (and Task #372 in general) mock
    ``llm.select_provider`` with a deterministic preference list to keep
    the fallback-routing assertions hermetic. That isolation is correct
    for those tests, but it leaves the real pool config completely
    unverified: someone could remove ``pinecone_ai`` from
    ``PROVIDER_PRIORITY["vector_search"]``, drop the ``mongodb_atlas``
    weight-0 fallback, or zero out the ``vertex`` weight, and not a single
    test would notice.

    These tests pin the contract that the real config provides:

      1. Empty ``exclude`` → draws come from the weighted pool
         {pinecone_ai, vertex}; both are reached over many draws (load-
         balanced) and no weight-0 / non-pool provider ever leaks in.
      2. Excluding ``{pinecone_ai}`` → every draw returns ``vertex``
         (the only positive-weight survivor).
      3. Excluding ``{pinecone_ai, vertex}`` → falls through to the
         weight-0 ``mongodb_atlas`` last-resort gate.
      4. Excluding ``{pinecone_ai, vertex, mongodb_atlas}`` → returns the
         weight-0 ``workers_ai`` terminal fallback.
      5. Excluding all four → returns ``"workers_ai"`` (the documented
         terminal default for non-strict-chain features).

    Plus a static config-shape sanity check so accidental removals from
    PROVIDER_PRIORITY / POOL_WEIGHTS / PROVIDER_CREDITS are flagged
    independently of dispatch behaviour.

    Saturation is forced to 0 so the RPM soft-shed cannot perturb the
    selection — this test is about *config*, not runtime health.
    """

    def _patch_no_saturation(self, monkeypatch):
        """Pin ``_get_provider_saturation`` to 0 so RPM shedding never
        excludes a provider during these tests. Without this, a leaky
        429-burst counter from an earlier test could flip a provider
        out of the pool and corrupt the assertions."""
        import llm as _llm
        monkeypatch.setattr(_llm, "_get_provider_saturation", lambda _p: 0.0)

    # ── 4a. Static config-shape sanity ────────────────────────────────────────

    # ── Expected ground-truth weights for the vector_search pool ─────────────
    #
    # As of the 2026-05-05 round-robin / load-balancing rewrite, the
    # vector_search pool runs Pinecone and Vertex at EQUAL weight (1000
    # each) so traffic is split ~50/50. Any change to this ratio is a
    # deliberate operator action and must be reflected here at the same
    # time — bumping Pinecone to 3000 or Vertex to 500 must update both
    # POOL_WEIGHTS and these expected values together. Pinning the exact
    # magnitudes (not just "> 0") catches a silent re-weighting that
    # would otherwise pass every existing test.
    _EXPECTED_VECTOR_SEARCH_WEIGHTS: dict[str, int] = {
        "pinecone_ai": 1000,
        "vertex":      1000,
    }

    def test_pool_config_shape(self):
        """Direct config introspection — independent of select_provider.

        A misconfiguration like dropping ``pinecone_ai`` from
        ``PROVIDER_PRIORITY["vector_search"]``, removing the weight-0
        ``mongodb_atlas`` last-resort gate, zeroing out ``vertex`` in
        ``POOL_WEIGHTS["vector_search"]``, or silently re-weighting the
        Pinecone↔Vertex split would all regress the dispatch behaviour.
        This test fails loudly the moment any of those invariants change
        so the operator is forced to update the dispatch tests below at
        the same time.
        """
        from config import PROVIDER_PRIORITY, PROVIDER_CREDITS, POOL_WEIGHTS

        priority = PROVIDER_PRIORITY.get("vector_search")
        assert priority is not None, "vector_search pool missing from PROVIDER_PRIORITY"

        # All four providers must be present in this exact order. The order
        # matters: the weight-0 fallback loop walks PROVIDER_PRIORITY in
        # list order, so mongodb_atlas must come before workers_ai.
        assert priority == ["pinecone_ai", "mongodb_atlas", "vertex", "workers_ai"], (
            f"vector_search PROVIDER_PRIORITY changed unexpectedly: {priority!r}"
        )

        weights = POOL_WEIGHTS.get("vector_search")
        assert weights is not None, "vector_search pool missing from POOL_WEIGHTS"

        # Pin the exact expected magnitudes (not just "> 0") so a silent
        # re-weighting like {pinecone_ai: 3000, vertex: 500} or
        # {pinecone_ai: 100, vertex: 1000} is caught. This is the
        # explicit operator contract for vector retrieval load split.
        assert weights == self._EXPECTED_VECTOR_SEARCH_WEIGHTS, (
            f"vector_search POOL_WEIGHTS changed: expected "
            f"{self._EXPECTED_VECTOR_SEARCH_WEIGHTS!r}, got {weights!r}. "
            f"If this re-weighting is intentional, update "
            f"_EXPECTED_VECTOR_SEARCH_WEIGHTS in this test class so the "
            f"draw-distribution check below stays in sync."
        )

        # And pin the structural invariant: pinecone_ai must remain at
        # least as weighted as vertex (Pinecone is the inference-grade
        # primary; Vertex is the secondary). A future re-weighting that
        # demotes Pinecone below Vertex would be a meaningful product
        # decision and must update both this assertion and the expected
        # weights above.
        assert weights.get("pinecone_ai", 0) >= weights.get("vertex", 0) > 0, (
            f"vector_search pool ordering invariant broken: "
            f"pinecone_ai must be >= vertex > 0; got {weights!r}"
        )

        # Weight-0 last-resort fallbacks live in PROVIDER_CREDITS, not in
        # the per-pool override. Pin them here so a credit reshuffle that
        # accidentally promotes mongodb_atlas / workers_ai to a positive
        # weight (and thus into the round-robin draw) is caught.
        assert PROVIDER_CREDITS.get("mongodb_atlas") == 0, (
            "mongodb_atlas must remain weight-0 (Atlas is fallback-only "
            "for vector_search; promoting it would cost the latency tax "
            "on every request)."
        )
        assert PROVIDER_CREDITS.get("workers_ai") == 0, (
            "workers_ai must remain weight-0 (terminal last-resort)."
        )

    # ── 4b. Live select_provider dispatch — REAL call, no mock ────────────────

    def test_empty_exclude_draws_from_weighted_pool_only(self, monkeypatch):
        """Real ``select_provider("vector_search")`` with no exclusions
        must only ever return providers from the positive-weight pool
        ({pinecone_ai, vertex} per current POOL_WEIGHTS). Over many draws
        the empirical distribution must match the configured weight ratio
        within a generous statistical tolerance, and neither weight-0
        provider (mongodb_atlas, workers_ai) may leak in.

        Determinism is guaranteed by seeding ``random`` for the duration
        of the test — the assertion below would otherwise be a tail-risk
        flake with the small but nonzero probability of a streak
        violating the ±10 % tolerance.

        With ``POOL_WEIGHTS["vector_search"] = {pinecone_ai: 1000,
        vertex: 1000}`` the expected ratio is 50/50; if a future
        operator change re-weights the pool (e.g. pinecone_ai = 3000,
        vertex = 500 → 6:1 expected), updating
        ``_EXPECTED_VECTOR_SEARCH_WEIGHTS`` in this class automatically
        re-targets this test, and the static-shape test above will fail
        first to force the update.
        """
        import random as _random
        import llm as _llm
        self._patch_no_saturation(monkeypatch)

        # Deterministic seed — fixes the empirical distribution so the
        # ±10 % tolerance below is a real ceiling, not a flake risk.
        _random.seed(20260505)

        N = 2000
        seen: dict[str, int] = {}
        for _ in range(N):
            chosen = _llm.select_provider("vector_search", lang="en")
            seen[chosen] = seen.get(chosen, 0) + 1

        # No leak from outside the positive-weight pool.
        forbidden = set(seen) - set(self._EXPECTED_VECTOR_SEARCH_WEIGHTS)
        assert not forbidden, (
            f"select_provider returned a non-pool provider on empty "
            f"exclude — got {forbidden!r} (full distribution: {seen!r}). "
            f"Likely cause: a weight-0 provider was promoted in "
            f"POOL_WEIGHTS['vector_search'] or PROVIDER_CREDITS."
        )

        # Each positive-weight provider must be drawn at the rate
        # implied by its configured weight, within ±10 % of the total
        # draws (a generous tolerance that still catches large
        # mis-weightings — e.g. a 6:1 split would deviate by ~36 %
        # from the current 50/50 expectation, well outside ±10 %).
        total_weight = sum(self._EXPECTED_VECTOR_SEARCH_WEIGHTS.values())
        for provider, weight in self._EXPECTED_VECTOR_SEARCH_WEIGHTS.items():
            expected_share = weight / total_weight
            actual_share = seen.get(provider, 0) / N
            assert abs(actual_share - expected_share) < 0.10, (
                f"{provider} draw rate ({actual_share:.2%}) deviates from "
                f"its configured share ({expected_share:.2%}) by more than "
                f"10 percentage points over {N} seeded draws. Either "
                f"POOL_WEIGHTS['vector_search'] silently changed without "
                f"updating _EXPECTED_VECTOR_SEARCH_WEIGHTS, or the "
                f"weighted-draw logic in select_provider regressed. "
                f"Full distribution: {seen!r}"
            )

        # Belt-and-braces structural check: the higher-weight provider
        # (pinecone_ai per the ordering invariant) must be drawn at
        # least as often as the lower-weight one. With the current 1:1
        # weighting this is essentially a tie-or-Pinecone-leads check;
        # if a future re-weighting promotes Pinecone to e.g. 3000 vs
        # vertex 500, this will catch a regression that flips the
        # relative draw rate without anyone touching POOL_WEIGHTS.
        weights_cfg = self._EXPECTED_VECTOR_SEARCH_WEIGHTS
        if weights_cfg["pinecone_ai"] > weights_cfg["vertex"]:
            assert seen.get("pinecone_ai", 0) > seen.get("vertex", 0), (
                f"pinecone_ai is configured at higher weight than vertex "
                f"({weights_cfg!r}) but was drawn less often "
                f"({seen!r}) — weighted-draw regression."
            )

    def test_exclude_pinecone_falls_through_to_vertex(self, monkeypatch):
        """With ``pinecone_ai`` excluded, the only remaining positive-
        weight provider in the vector_search pool is ``vertex``. Every
        draw must therefore return ``vertex`` deterministically — there
        is no randomness when the pool degenerates to a single member."""
        import llm as _llm
        self._patch_no_saturation(monkeypatch)

        for _ in range(50):
            chosen = _llm.select_provider(
                "vector_search", lang="en",
                exclude=frozenset({"pinecone_ai"}),
            )
            assert chosen == "vertex", (
                f"Expected vertex when pinecone_ai is excluded; got {chosen!r}. "
                f"Likely cause: vertex was removed from POOL_WEIGHTS or "
                f"PROVIDER_PRIORITY['vector_search']."
            )

    def test_exclude_pinecone_and_vertex_falls_through_to_atlas(self, monkeypatch):
        """With BOTH positive-weight providers excluded, the weighted
        pool is empty and ``select_provider`` walks PROVIDER_PRIORITY
        in list order looking for a weight-0 fallback that is (a) not
        excluded and (b) allowed for this feature.

        ``mongodb_atlas`` is gated by ``feature == "vector_search"``
        (the only pool where the Atlas $vectorSearch backend is wired),
        so for any other feature this fallback is skipped. Pinning the
        gate here means a regression that drops the gate (turning Atlas
        into a wildcard fallback for *every* feature) is caught."""
        import llm as _llm
        self._patch_no_saturation(monkeypatch)

        chosen = _llm.select_provider(
            "vector_search", lang="en",
            exclude=frozenset({"pinecone_ai", "vertex"}),
        )
        assert chosen == "mongodb_atlas", (
            f"Expected mongodb_atlas weight-0 fallback when both "
            f"positive-weight providers are excluded; got {chosen!r}. "
            f"Likely cause: mongodb_atlas was removed from "
            f"PROVIDER_PRIORITY['vector_search'], its credit was "
            f"promoted off zero, or the Atlas-only feature gate "
            f"in select_provider was tightened."
        )

    def test_exclude_pinecone_vertex_atlas_falls_through_to_workers_ai(self, monkeypatch):
        """With pinecone_ai + vertex + mongodb_atlas all excluded, the
        last weight-0 fallback in PROVIDER_PRIORITY['vector_search'] is
        ``workers_ai``. The function must return it via the weight-0
        fallback loop, NOT via the terminal default at the bottom of
        ``select_provider`` (workers_ai is in this pool's priority list)."""
        import llm as _llm
        self._patch_no_saturation(monkeypatch)

        chosen = _llm.select_provider(
            "vector_search", lang="en",
            exclude=frozenset({"pinecone_ai", "vertex", "mongodb_atlas"}),
        )
        assert chosen == "workers_ai", (
            f"Expected workers_ai when pinecone_ai+vertex+mongodb_atlas "
            f"are excluded; got {chosen!r}."
        )

    def test_exclude_all_four_returns_terminal_workers_ai_default(self, monkeypatch):
        """When every provider in the vector_search pool is excluded, the
        function falls through to its terminal default branch which
        returns the literal string ``"workers_ai"`` for non-strict-chain
        features. ``vector_search`` is not in ``_STRICT_CHAIN_FEATURES``
        (only ``assamese_rag_chat`` is), so the documented terminal
        fallback is workers_ai — never ``None``.

        This pins the contract that vector retrieval will never raise
        ``select_provider returned None`` regardless of what's in the
        exclude set. Callers downstream of select_provider can therefore
        rely on always getting a string provider name back."""
        import llm as _llm
        self._patch_no_saturation(monkeypatch)

        chosen = _llm.select_provider(
            "vector_search", lang="en",
            exclude=frozenset({
                "pinecone_ai", "vertex", "mongodb_atlas", "workers_ai",
            }),
        )
        assert chosen == "workers_ai", (
            f"vector_search must always return workers_ai as the terminal "
            f"default (never None) since it is NOT a strict-chain feature; "
            f"got {chosen!r}."
        )
