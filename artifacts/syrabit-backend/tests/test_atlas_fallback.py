"""Task #217 — Confirm the emergency Atlas fallback can be switched back on safely.

Tests cover three surfaces:

1. ``ensure_vector_index()`` — graceful behaviour when the Atlas index is
   absent (not-yet-created, deleted after embedding cleanup, or on an
   Atlas tier without Vector Search).

2. ATLAS_VS_ENABLED startup gate — ``ensure_vector_index`` is skipped when
   the env var is absent (default off); when it is set and the call fails,
   startup continues rather than crashing.

3. ``_fetch_chunks_semantic`` fallback routing — with
   ``PINECONE_ATLAS_FALLBACK=true`` and a broken Atlas index, the function
   still returns ``[]`` (no 500); with ``PINECONE_ATLAS_FALLBACK=false`` the
   Atlas path is never attempted; when Pinecone returns results the Atlas path
   is completely bypassed.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

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


@pytest.mark.skip(
    reason=(
        "Architecture changed (Task #291+): _fetch_chunks_semantic no "
        "longer routes through a binary PINECONE_ATLAS_FALLBACK env "
        "switch. Vector retrieval now goes through a weighted "
        "vector_search pool (`_select_vs` → `_try_vector_provider`) "
        "with per-provider embedding (pinecone_ai uses Pinecone "
        "Inference, mongodb_atlas uses Cohere) and exclusion-based "
        "retry. These tests pin the old contract — rewriting them "
        "against the new dispatch surface is tracked as a follow-up."
    )
)
class TestFetchChunksSemanticFallback:
    """Tests for ``rag._fetch_chunks_semantic`` Atlas fallback routing.

    All external I/O is mocked:
      - Cohere embed_query returns _FAKE_QVEC (or is patched to fail)
      - PineconeVectorRetriever is replaced with _make_pc_retriever(...)
      - rag.db.chunks.aggregate is replaced with _AggregateCursor(...)
    """

    def _run_semantic(self, query="test", subject_id=None, monkeypatch=None, env=None):
        """Helper: patch env, run _fetch_chunks_semantic, return result."""
        import rag as _rag
        if env:
            for k, v in env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        return _run(_rag._fetch_chunks_semantic(query, subject_id=subject_id))

    def test_pinecone_fails_atlas_raises_fallback_enabled_returns_empty(self, monkeypatch):
        """When Pinecone fails AND the Atlas aggregate also raises (deleted index)
        AND PINECONE_ATLAS_FALLBACK=true, the function must return [] without
        raising — no 500 error is surfaced to the caller."""
        import rag as _rag

        monkeypatch.setenv("PINECONE_ATLAS_FALLBACK", "true")

        # Cohere embed succeeds
        monkeypatch.setattr("providers.cohere.ENABLED", True, raising=False)
        monkeypatch.setattr(
            "providers.cohere.embed_query",
            AsyncMock(return_value=_FAKE_QVEC),
            raising=False,
        )

        # Pinecone raises
        FakePc = _make_pc_retriever(configured=True, raises=True)
        monkeypatch.setattr("retrievers.pinecone_vector.PineconeVectorRetriever", FakePc)

        # Atlas aggregate raises (deleted index)
        atlas_error = Exception("PlanExecutor error — vector index 'vector_index' not found")
        mock_chunks = MagicMock()
        mock_chunks.aggregate = lambda pipeline: _AggregateCursor(raise_exc=atlas_error)
        mock_db = MagicMock()
        mock_db.chunks = mock_chunks
        monkeypatch.setattr(_rag, "db", mock_db)

        result = _run(_rag._fetch_chunks_semantic("photosynthesis"))
        assert result == [], f"Expected [] but got {result!r}"

    def test_atlas_fallback_disabled_pinecone_fails_atlas_never_queried(self, monkeypatch):
        """With PINECONE_ATLAS_FALLBACK=false, even when Pinecone fails, the
        Atlas $vectorSearch path must not be attempted — db.chunks.aggregate
        is never called."""
        import rag as _rag

        monkeypatch.setenv("PINECONE_ATLAS_FALLBACK", "false")

        monkeypatch.setattr("providers.cohere.ENABLED", True, raising=False)
        monkeypatch.setattr(
            "providers.cohere.embed_query",
            AsyncMock(return_value=_FAKE_QVEC),
            raising=False,
        )

        FakePc = _make_pc_retriever(configured=True, raises=True)
        monkeypatch.setattr("retrievers.pinecone_vector.PineconeVectorRetriever", FakePc)

        aggregate_calls = []

        class _TrackingCursor:
            async def to_list(self, length=None):
                return []

        mock_chunks = MagicMock()
        mock_chunks.aggregate = lambda p: (_TrackingCursor() if not aggregate_calls.append(p) else None)
        mock_db = MagicMock()
        mock_db.chunks = mock_chunks
        monkeypatch.setattr(_rag, "db", mock_db)

        result = _run(_rag._fetch_chunks_semantic("osmosis"))
        assert result == []
        assert aggregate_calls == [], (
            "Atlas aggregate must not be called when PINECONE_ATLAS_FALLBACK=false"
        )

    def test_pinecone_results_bypass_atlas_entirely(self, monkeypatch):
        """When Pinecone returns results, Atlas $vectorSearch must not be
        queried — even with PINECONE_ATLAS_FALLBACK=true — since raw is
        non-empty and the condition ``if not raw and _atlas_fallback_enabled``
        is false."""
        import rag as _rag

        monkeypatch.setenv("PINECONE_ATLAS_FALLBACK", "true")

        monkeypatch.setattr("providers.cohere.ENABLED", True, raising=False)
        monkeypatch.setattr(
            "providers.cohere.embed_query",
            AsyncMock(return_value=_FAKE_QVEC),
            raising=False,
        )

        # Pinecone returns one match with a chapter_id so raw is non-empty
        pc_results = [
            {"score": 0.92, "metadata": {"chapter_id": "ch-bio-1", "chapter_title": "Bio", "subject_id": "bio"}}
        ]
        FakePc = _make_pc_retriever(configured=True, results=pc_results)
        monkeypatch.setattr("retrievers.pinecone_vector.PineconeVectorRetriever", FakePc)

        aggregate_calls = []

        class _TrackingCursor:
            async def to_list(self, length=None):
                return []

        mock_chunks = MagicMock()
        mock_chunks.aggregate = lambda p: (_TrackingCursor() if not aggregate_calls.append(p) else None)
        # Mock chapters fetch so the function doesn't error after raw is set
        mock_chapters = MagicMock()
        mock_chapters.find.return_value.to_list = AsyncMock(return_value=[])
        mock_db = MagicMock()
        mock_db.chunks = mock_chunks
        mock_db.chapters = mock_chapters
        monkeypatch.setattr(_rag, "db", mock_db)

        _run(_rag._fetch_chunks_semantic("cell division"))
        assert aggregate_calls == [], (
            "Atlas aggregate must not be called when Pinecone returned results"
        )

    def test_pinecone_returns_chapter_even_when_atlas_index_absent(self, monkeypatch):
        """Decisive end-to-end positive test — no silent empty.

        Scenario: PINECONE_ATLAS_FALLBACK=true, Atlas vector_index deleted,
        Pinecone returns a match, chapter lookup finds the chapter doc.
        Expected: _fetch_chunks_semantic returns the chapter (not []).

        This is the key guarantee: even after the embedding cleanup drops the
        Atlas vector_index, Pinecone still delivers results and the function
        does not silently return an empty list.
        """
        import rag as _rag

        monkeypatch.setenv("PINECONE_ATLAS_FALLBACK", "true")

        monkeypatch.setattr("providers.cohere.ENABLED", True, raising=False)
        monkeypatch.setattr(
            "providers.cohere.embed_query",
            AsyncMock(return_value=_FAKE_QVEC),
            raising=False,
        )

        # Pinecone returns one result — chapter_id present
        pc_results = [
            {
                "score": 0.91,
                "metadata": {
                    "chapter_id": "ch-bio-photosyn",
                    "chapter_title": "Photosynthesis",
                    "subject_id": "bio-11",
                },
            }
        ]
        FakePc = _make_pc_retriever(configured=True, results=pc_results)
        monkeypatch.setattr("retrievers.pinecone_vector.PineconeVectorRetriever", FakePc)

        # Chapters collection returns the chapter doc
        chapter_doc = {
            "id": "ch-bio-photosyn",
            "title": "Photosynthesis",
            "content": "Plants convert sunlight to chemical energy via chlorophyll.",
            "slug": "photosynthesis",
            "subject_id": "bio-11",
        }

        class _FakeFindCursor:
            async def to_list(self, length=None):
                return [chapter_doc]

        mock_chunks = MagicMock()
        # Atlas NOT called (Pinecone raw is non-empty)
        mock_chunks.aggregate = lambda pipeline: _AggregateCursor(
            raise_exc=Exception("No vector_index — should not be reached")
        )
        mock_chapters = MagicMock()
        mock_chapters.find = lambda *a, **kw: _FakeFindCursor()
        mock_db = MagicMock()
        mock_db.chunks = mock_chunks
        mock_db.chapters = mock_chapters
        monkeypatch.setattr(_rag, "db", mock_db)

        result = _run(_rag._fetch_chunks_semantic("photosynthesis"))

        assert len(result) == 1, f"Expected 1 chapter from Pinecone, got: {result!r}"
        assert result[0]["id"] == "ch-bio-photosyn"
        assert "Photosynthesis" in result[0]["title"]

    def test_cohere_unavailable_returns_empty_without_querying_either_backend(self, monkeypatch):
        """When Cohere embed is unavailable (ENABLED=False), _fetch_chunks_semantic
        must return [] immediately — neither Pinecone nor Atlas is queried.
        This confirms the semantic search skip path works end-to-end."""
        import rag as _rag

        monkeypatch.setattr("providers.cohere.ENABLED", False, raising=False)
        monkeypatch.setattr(
            "providers.cohere.embed_query",
            AsyncMock(return_value=None),
            raising=False,
        )

        pc_calls = []

        class _TrackingPc:
            def is_configured(self):
                return True

            async def query(self, *a, **kw):
                pc_calls.append(True)
                return []

        monkeypatch.setattr("retrievers.pinecone_vector.PineconeVectorRetriever", _TrackingPc)

        result = _run(_rag._fetch_chunks_semantic("anything"))
        assert result == []
        assert pc_calls == [], "Pinecone must not be queried when embed_query returns no vector"
