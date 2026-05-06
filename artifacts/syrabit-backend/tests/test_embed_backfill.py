"""Task #411 — Tests for the legacy → workers_ai_custom backfill job.

Covers:

* Selection rule: only chunks whose ``embedding_source`` is missing or
  not the ``workers_ai_custom`` tag are picked up.
* Per-chunk write: each successfully embedded chunk is stamped with
  ``embedding_source=workers_ai_custom`` + the new model/dim so the
  next pass naturally skips it (the resumability invariant).
* Resumability: state is persisted to the ``embed_backfill_state``
  collection with ``last_processed_id`` after each batch.
* Per-call budget: ``max_chunks`` caps work for one pass so the admin
  trigger can keep latency bounded.
* Worker batch cap: ``batch_size`` is hard-capped at 32 (the worker's
  WORKERS_EMBED_MAX_BATCH limit).
* Pinecone failure rolls back: a failed Pinecone upsert must NOT flip
  the Mongo ``embedding_source`` marker, so the chunk stays selected
  on the next pass and we never lose track of it.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _matches(doc: dict, q: dict) -> bool:
    for k, cond in q.items():
        if k == "_id" and isinstance(cond, dict) and "$gt" in cond:
            if not (doc.get("_id") is not None and doc["_id"] > cond["$gt"]):
                return False
            continue
        if k == "embedding_source" and isinstance(cond, dict) and "$ne" in cond:
            if doc.get("embedding_source") == cond["$ne"]:
                return False
            continue
        if doc.get(k) != cond:
            return False
    return True


def _make_db(chunks: list[dict]):
    """Build a tiny Motor-shaped mock that supports our embed_backfill calls."""
    chunks_state = list(chunks)
    state_state: dict = {}
    bulk_writes: list[list] = []

    def _find(query, projection=None):
        results = sorted(
            [c for c in chunks_state if _matches(c, query)],
            key=lambda c: c["_id"],
        )

        cursor = MagicMock()
        cursor._results = results
        cursor.sort = MagicMock(return_value=cursor)

        def _limit(n):
            cursor._results = cursor._results[: n]
            return cursor

        cursor.limit = _limit

        async def _to_list(length=None):
            return list(cursor._results)

        cursor.to_list = _to_list
        return cursor

    async def _bulk_write(ops, ordered=False):
        bulk_writes.append(list(ops))
        # Apply $set ops to the in-memory chunk state so subsequent passes
        # see the freshly stamped embedding_source.
        for op in ops:
            flt = getattr(op, "_filter", {}) or {}
            doc = getattr(op, "_doc", {}) or {}
            sets = doc.get("$set", {}) if isinstance(doc, dict) else {}
            for c in chunks_state:
                if all(c.get(k) == v for k, v in flt.items()):
                    c.update(sets)
        return MagicMock(modified_count=len(ops))

    async def _count_documents(query):
        return len([c for c in chunks_state if _matches(c, query)])

    def _aggregate(pipeline):
        # Minimal $match + $group aggregation good enough for the
        # embed_backfill ``_remaining_by_source`` pipeline.
        match_stage = next((s["$match"] for s in pipeline if "$match" in s), {})
        group_stage = next((s["$group"] for s in pipeline if "$group" in s), None)
        rows = [c for c in chunks_state if _matches(c, match_stage)]
        results: list[dict] = []
        if group_stage and group_stage.get("_id") == "$embedding_source":
            buckets: dict = {}
            for row in rows:
                key = row.get("embedding_source")
                buckets[key] = buckets.get(key, 0) + 1
            results = [{"_id": k, "n": n} for k, n in buckets.items()]

        cursor = MagicMock()

        class _AsyncIter:
            def __init__(self, items):
                self._items = list(items)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._items:
                    raise StopAsyncIteration
                return self._items.pop(0)

        cursor.__aiter__ = lambda self=cursor: _AsyncIter(results).__aiter__()
        return cursor

    chunks_coll = MagicMock(name="chunks")
    chunks_coll.find = _find
    chunks_coll.bulk_write = _bulk_write
    chunks_coll.count_documents = _count_documents
    chunks_coll.aggregate = _aggregate

    state_coll = MagicMock(name="embed_backfill_state")

    async def _state_find_one(q):
        if state_state.get("_id") == q.get("_id"):
            return dict(state_state)
        return None

    async def _state_update_one(q, update, upsert=False):
        patch = update.get("$set", {}) or {}
        if not state_state:
            state_state["_id"] = q.get("_id")
            state_state.update(update.get("$setOnInsert", {}))
        state_state.update(patch)
        return MagicMock(modified_count=1)

    state_coll.find_one = _state_find_one
    state_coll.update_one = _state_update_one

    db = MagicMock()
    db.chunks = chunks_coll

    def _getitem(self, name):
        if name == "chunks":
            return chunks_coll
        if name == "embed_backfill_state":
            return state_coll
        return MagicMock()

    db.__getitem__ = _getitem

    return db, chunks_state, state_state, bulk_writes


@pytest.fixture(autouse=True)
def _stub_workers_embed(monkeypatch):
    """Replace the worker call with a deterministic in-memory embedder."""
    from aca_jobs import embed_backfill

    calls: list[list[str]] = []

    async def _fake_embed(texts):
        calls.append(list(texts))
        return [[0.5] * 1024 for _ in texts]

    monkeypatch.setattr(embed_backfill, "_embed_texts", _fake_embed)

    # Default to a healthy Pinecone retriever so the happy-path tests
    # exercise the real "Pinecone-confirms-then-stamp-Mongo" sequence.
    # Individual tests override this to simulate failure / unconfigured.
    class _FakeRetriever:
        def is_configured(self):
            return True

        async def upsert(self, vectors):
            return {"upserted": len(vectors)}

    import retrievers.pinecone_vector as _pv
    monkeypatch.setattr(
        _pv, "PineconeVectorRetriever", lambda: _FakeRetriever(),
    )
    return calls


@pytest.mark.asyncio
async def test_pinecone_metadata_includes_new_source_tag(monkeypatch):
    """Task #411 acceptance: every re-upserted Pinecone vector must
    carry ``embedding_source=workers_ai_custom`` in its metadata so a
    post-backfill audit on the Pinecone side can verify the cutover."""
    from aca_jobs import embed_backfill

    db, _, _, _ = _make_db([
        {"_id": "m1", "content": "alpha", "embedding_source": "cohere",
         "chapter_id": "ch-1", "subject_id": "sub-1"},
        {"_id": "m2", "content": "beta",  "embedding_source": "voyage",
         "chapter_id": "ch-2", "subject_id": "sub-2"},
    ])

    captured: list[list[dict]] = []

    class _CapturingRetriever:
        def is_configured(self):
            return True

        async def upsert(self, vectors):
            captured.append(list(vectors))
            return {"upserted": len(vectors)}

    import retrievers.pinecone_vector as _pv
    monkeypatch.setattr(_pv, "PineconeVectorRetriever", lambda: _CapturingRetriever())

    summary = await embed_backfill.run_backfill(db, max_chunks=10, batch_size=32)

    assert summary["succeeded"] == 2
    assert captured, "Pinecone upsert should have been called"
    flat = [v for batch in captured for v in batch]
    assert {v["id"] for v in flat} == {"m1", "m2"}
    for v in flat:
        meta = v.get("metadata") or {}
        assert meta.get("embedding_source") == "workers_ai_custom", (
            f"vector {v['id']} missing embedding_source tag in Pinecone metadata: {meta}"
        )
        assert meta.get("embedding_model", "").startswith("workers_ai_custom@")


@pytest.mark.asyncio
async def test_only_legacy_chunks_are_picked_up():
    from aca_jobs import embed_backfill

    db, chunks_state, _, bulk_writes = _make_db([
        {"_id": "a", "content": "old A", "embedding_source": "cohere"},
        {"_id": "b", "content": "new B", "embedding_source": "workers_ai_custom"},
        {"_id": "c", "content": "untagged C"},  # also legacy (missing tag)
    ])

    summary = await embed_backfill.run_backfill(db, max_chunks=10, batch_size=32)

    # Only chunk-a and chunk-c should have been processed.
    assert summary["processed"] == 2
    assert summary["succeeded"] == 2
    # The new tag must have been stamped.
    a = next(c for c in chunks_state if c["_id"] == "a")
    c = next(c for c in chunks_state if c["_id"] == "c")
    assert a["embedding_source"] == "workers_ai_custom"
    assert c["embedding_source"] == "workers_ai_custom"
    # And chunk-b's pre-existing tag must remain untouched.
    b = next(c for c in chunks_state if c["_id"] == "b")
    assert b["embedding_source"] == "workers_ai_custom"


@pytest.mark.asyncio
async def test_resume_state_persists_last_processed_id():
    from aca_jobs import embed_backfill

    db, chunks_state, state_state, _ = _make_db([
        {"_id": f"chunk-{i:02d}", "content": f"text {i}",
         "embedding_source": "cohere"}
        for i in range(5)
    ])

    summary = await embed_backfill.run_backfill(db, max_chunks=3, batch_size=2)

    assert summary["processed"] == 3
    assert state_state.get("last_processed_id") == "chunk-02"
    assert state_state.get("running") is False  # final write flips it off

    # Re-running with another budget continues from chunk-03.
    summary2 = await embed_backfill.run_backfill(db, max_chunks=10, batch_size=2)
    assert summary2["processed"] == 2
    # And after a full pass the running flag is cleared.
    assert state_state.get("running") is False
    # Every chunk now carries the new tag.
    for c in chunks_state:
        assert c["embedding_source"] == "workers_ai_custom"


@pytest.mark.asyncio
async def test_batch_size_capped_at_worker_max(_stub_workers_embed):
    from aca_jobs import embed_backfill

    db, _, _, _ = _make_db([
        {"_id": f"chunk-{i:03d}", "content": f"x{i}",
         "embedding_source": "cohere"}
        for i in range(40)
    ])
    # Caller asks for a 100-text batch; we must hard-cap at 32.
    await embed_backfill.run_backfill(db, max_chunks=40, batch_size=100)

    # First call should never exceed 32 texts.
    assert _stub_workers_embed, "embed should have been called"
    assert all(len(c) <= 32 for c in _stub_workers_embed)


@pytest.mark.asyncio
async def test_pinecone_failure_keeps_chunks_pending(monkeypatch):
    from aca_jobs import embed_backfill

    db, chunks_state, _, bulk_writes = _make_db([
        {"_id": "x1", "content": "one", "embedding_source": "cohere"},
        {"_id": "x2", "content": "two", "embedding_source": "cohere"},
    ])

    class _BrokenRetriever:
        def is_configured(self):
            return True

        async def upsert(self, vectors):
            raise RuntimeError("pinecone down")

    import retrievers.pinecone_vector as _pv
    monkeypatch.setattr(_pv, "PineconeVectorRetriever", lambda: _BrokenRetriever())

    summary = await embed_backfill.run_backfill(db, max_chunks=10, batch_size=32)

    # Pinecone blew up → no Mongo writes, both chunks counted as failed.
    assert bulk_writes == []
    assert summary["failed"] == 2
    assert summary["succeeded"] == 0
    # Critically: tags must NOT have been flipped, so the next pass picks
    # them up again.
    for c in chunks_state:
        assert c.get("embedding_source") == "cohere"


@pytest.mark.asyncio
async def test_pinecone_partial_errors_do_not_stamp_mongo(monkeypatch):
    """PineconeVectorRetriever.upsert catches HTTP errors and returns
    ``{"upserted": <n>, "errors": [...]}`` instead of raising. The
    backfill MUST treat a non-empty ``errors`` list (or a short
    ``upserted`` count) as a failure and refuse to flip the Mongo
    marker — otherwise chunks would be silently lost from Pinecone
    while marked migrated in Mongo."""
    from aca_jobs import embed_backfill

    db, chunks_state, _, bulk_writes = _make_db([
        {"_id": "y1", "content": "one", "embedding_source": "cohere"},
        {"_id": "y2", "content": "two", "embedding_source": "cohere"},
    ])

    class _PartialRetriever:
        def is_configured(self):
            return True

        async def upsert(self, vectors):
            # Looks "successful" at the exception layer but reports an error
            # in the response — the real retriever's failure mode.
            return {"upserted": 0, "errors": ["HTTP 500: pinecone hiccup"]}

    import retrievers.pinecone_vector as _pv
    monkeypatch.setattr(_pv, "PineconeVectorRetriever", lambda: _PartialRetriever())

    summary = await embed_backfill.run_backfill(db, max_chunks=10, batch_size=32)

    assert bulk_writes == [], "no Mongo marker writes when Pinecone reports errors"
    assert summary["failed"] == 2
    assert summary["succeeded"] == 0
    for c in chunks_state:
        assert c.get("embedding_source") == "cohere"


@pytest.mark.asyncio
async def test_pinecone_short_upsert_count_treated_as_failure(monkeypatch):
    """If Pinecone reports fewer upserted vectors than we sent (no
    ``errors`` key), conservatively treat the batch as failed."""
    from aca_jobs import embed_backfill

    db, chunks_state, _, bulk_writes = _make_db([
        {"_id": "z1", "content": "one", "embedding_source": "cohere"},
        {"_id": "z2", "content": "two", "embedding_source": "cohere"},
    ])

    class _ShortRetriever:
        def is_configured(self):
            return True

        async def upsert(self, vectors):
            return {"upserted": 1}  # short by one, no errors key

    import retrievers.pinecone_vector as _pv
    monkeypatch.setattr(_pv, "PineconeVectorRetriever", lambda: _ShortRetriever())

    summary = await embed_backfill.run_backfill(db, max_chunks=10, batch_size=32)

    assert bulk_writes == []
    assert summary["failed"] == 2
    assert summary["succeeded"] == 0
    for c in chunks_state:
        assert c.get("embedding_source") == "cohere"


@pytest.mark.asyncio
async def test_aborts_when_pinecone_unconfigured(monkeypatch):
    """Fail-closed: if the Pinecone retriever isn't configured the run
    must abort immediately without stamping any chunk."""
    from aca_jobs import embed_backfill

    db, chunks_state, _, bulk_writes = _make_db([
        {"_id": "u1", "content": "one", "embedding_source": "cohere"},
    ])

    class _UnconfiguredRetriever:
        def is_configured(self):
            return False

        async def upsert(self, vectors):  # pragma: no cover — must not be hit
            raise AssertionError("upsert should not be called when unconfigured")

    import retrievers.pinecone_vector as _pv
    monkeypatch.setattr(_pv, "PineconeVectorRetriever", lambda: _UnconfiguredRetriever())

    summary = await embed_backfill.run_backfill(db, max_chunks=10, batch_size=32)

    assert summary.get("error") == "pinecone_unavailable"
    assert summary["processed"] == 0
    assert bulk_writes == []
    for c in chunks_state:
        assert c.get("embedding_source") == "cohere"


@pytest.mark.asyncio
async def test_get_progress_reports_remaining_and_total():
    from aca_jobs import embed_backfill

    db, _, _, _ = _make_db([
        {"_id": "a", "content": "a", "embedding_source": "cohere"},
        {"_id": "b", "content": "b", "embedding_source": "workers_ai_custom"},
        {"_id": "c", "content": "c"},
    ])
    progress = await embed_backfill.get_progress(db)
    assert progress["target_source"] == "workers_ai_custom"
    assert progress["total_chunks"] == 3
    # a and c are legacy → 2 remaining; b is already on the new stack → 1 done.
    assert progress["remaining"] == 2
    assert progress["re_embedded"] == 1
    assert progress["batch_size"] == embed_backfill.BATCH_SIZE
    assert progress["max_rpm"] == embed_backfill.MAX_RPM


@pytest.mark.asyncio
async def test_get_progress_breaks_remaining_down_by_source():
    """Task #433 — admins need a per-old-provider breakdown of the
    chunks still on the legacy embed stack so they can tell whether
    the backlog is mostly Cohere (safe to defer) or something more
    drift-prone. Missing/null tags are bucketed under '(missing)' so
    the breakdown sums to the overall ``remaining`` count."""
    from aca_jobs import embed_backfill

    db, _, _, _ = _make_db([
        {"_id": "c1", "content": "x", "embedding_source": "cohere"},
        {"_id": "c2", "content": "x", "embedding_source": "cohere"},
        {"_id": "v1", "content": "x", "embedding_source": "voyage"},
        {"_id": "n1", "content": "x"},  # missing tag → '(missing)'
        {"_id": "ok", "content": "x", "embedding_source": "workers_ai_custom"},
    ])

    progress = await embed_backfill.get_progress(db)

    assert progress["remaining"] == 4
    by_source = progress["remaining_by_source"]
    assert by_source == {"cohere": 2, "voyage": 1, "(missing)": 1}
    # Sanity: the breakdown sums to the overall remaining count, so the
    # admin pill never shows a "missing chunks" gap.
    assert sum(by_source.values()) == progress["remaining"]
    # Already-migrated chunks are excluded from the breakdown.
    assert "workers_ai_custom" not in by_source


# ── Task #466 — throughput + ETA ─────────────────────────────────────────────


def test_compute_throughput_with_recent_samples():
    """Recent batch deltas in the trailing window must produce a sensible
    chunks/min rate so the admin pill can predict completion."""
    import datetime as _dt
    from aca_jobs import embed_backfill

    now = _dt.datetime(2026, 5, 6, 12, 0, 0)
    samples = [
        {"at": now - _dt.timedelta(minutes=10), "delta": 100},
        {"at": now - _dt.timedelta(minutes=5),  "delta": 200},
        {"at": now - _dt.timedelta(minutes=1),  "delta": 100},
    ]
    out = embed_backfill._compute_throughput(samples, now=now)
    # 400 chunks across the 10-minute earliest→now span = 40 chunks/min.
    assert out["samples"] == 3
    assert out["elapsed_s"] == 600.0
    assert out["chunks_per_min"] == 40.0


def test_compute_throughput_drops_stale_samples():
    """Anything older than the window must be ignored, so a once-busy
    job that's been idle for hours doesn't keep claiming progress."""
    import datetime as _dt
    from aca_jobs import embed_backfill

    now = _dt.datetime(2026, 5, 6, 12, 0, 0)
    samples = [
        {"at": now - _dt.timedelta(hours=5), "delta": 9999},  # outside window
        {"at": now - _dt.timedelta(minutes=2), "delta": 60},
    ]
    out = embed_backfill._compute_throughput(samples, now=now, window_s=3600)
    assert out["samples"] == 1
    # 60 chunks across ~120s → 30 chunks/min.
    assert out["chunks_per_min"] == 30.0


def test_compute_throughput_empty_samples():
    from aca_jobs import embed_backfill
    out = embed_backfill._compute_throughput([])
    assert out["chunks_per_min"] is None
    assert out["samples"] == 0


def test_eta_seconds_from_rate():
    from aca_jobs import embed_backfill
    # 600 chunks at 60/min ⇒ 600s.
    assert embed_backfill._eta_seconds(600, 60.0) == 600
    # No remaining work ⇒ 0 (admin pill renders 'done').
    assert embed_backfill._eta_seconds(0, 60.0) == 0
    # No rate ⇒ unknown (None).
    assert embed_backfill._eta_seconds(100, None) is None
    assert embed_backfill._eta_seconds(100, 0) is None


@pytest.mark.asyncio
async def test_run_backfill_records_throughput_samples():
    """A successful pass must persist per-batch deltas into
    ``throughput_samples`` so ``get_progress`` can derive a rate
    without recomputing it from chunk timestamps."""
    from aca_jobs import embed_backfill

    db, _, state_state, _ = _make_db([
        {"_id": f"chunk-{i:02d}", "content": f"x{i}",
         "embedding_source": "cohere"}
        for i in range(6)
    ])
    await embed_backfill.run_backfill(db, max_chunks=6, batch_size=2)

    samples = state_state.get("throughput_samples") or []
    # 6 chunks in batches of 2 → 3 batches → 3 samples, each delta=2.
    assert len(samples) == 3
    assert all(s["delta"] == 2 for s in samples)
    assert all(isinstance(s["at"], __import__("datetime").datetime)
               for s in samples)


@pytest.mark.asyncio
async def test_get_progress_exposes_throughput_and_eta():
    """The admin endpoint payload must include a throughput block + ETA
    so the frontend can render '40 chunks/min · ETA 2h' next to the
    percent number."""
    import datetime as _dt
    from aca_jobs import embed_backfill

    db, _, state_state, _ = _make_db([
        {"_id": f"r{i}", "content": "x", "embedding_source": "cohere"}
        for i in range(120)
    ])
    # Pre-seed state with a synthetic 10-min, 400-chunk burst.
    now = _dt.datetime.utcnow()
    state_state.update({
        "_id": "global",
        "throughput_samples": [
            {"at": now - _dt.timedelta(minutes=10), "delta": 100},
            {"at": now - _dt.timedelta(minutes=5),  "delta": 200},
            {"at": now - _dt.timedelta(minutes=1),  "delta": 100},
        ],
    })

    progress = await embed_backfill.get_progress(db)
    tput = progress["throughput"]
    assert tput["chunks_per_min"] is not None
    assert tput["chunks_per_min"] > 0
    assert tput["samples"] == 3
    # ETA = remaining (120) / rate (~40/min) * 60 → ~180s.
    assert progress["eta_seconds"] is not None
    assert 150 <= progress["eta_seconds"] <= 250


@pytest.mark.asyncio
async def test_run_backfill_drops_malformed_prior_samples(monkeypatch):
    """Historical state docs may contain garbage entries (wrong types,
    missing fields) from older code revs. The run loader must skip
    them silently instead of crashing the whole pass."""
    import datetime as _dt
    from aca_jobs import embed_backfill

    db, _, state_state, _ = _make_db([
        {"_id": "g1", "content": "x", "embedding_source": "cohere"},
        {"_id": "g2", "content": "y", "embedding_source": "cohere"},
    ])
    now = _dt.datetime.utcnow()
    state_state.update({
        "_id": "global",
        "throughput_samples": [
            "not a dict",                                # malformed
            {"at": "garbage", "delta": 5},               # bad timestamp
            {"at": now - _dt.timedelta(minutes=2), "delta": "nope"},  # bad delta
            {"at": now - _dt.timedelta(minutes=1), "delta": -3},      # negative
            {"at": now - _dt.timedelta(minutes=1), "delta": 7},       # good
        ],
    })

    summary = await embed_backfill.run_backfill(db, max_chunks=2, batch_size=2)
    assert summary["succeeded"] == 2
    samples = state_state.get("throughput_samples") or []
    # Only the one good prior sample + the new batch sample survive.
    assert len(samples) == 2
    assert all(isinstance(s["at"], _dt.datetime) for s in samples)
    assert all(s["delta"] > 0 for s in samples)


@pytest.mark.asyncio
async def test_get_progress_eta_none_without_samples():
    """No samples ⇒ no rate ⇒ no ETA. Frontend renders 'throughput
    pending…' instead of a misleading number."""
    from aca_jobs import embed_backfill

    db, _, _, _ = _make_db([
        {"_id": "p1", "content": "x", "embedding_source": "cohere"},
    ])
    progress = await embed_backfill.get_progress(db)
    assert progress["throughput"]["chunks_per_min"] is None
    assert progress["eta_seconds"] is None
