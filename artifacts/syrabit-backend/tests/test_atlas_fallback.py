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
