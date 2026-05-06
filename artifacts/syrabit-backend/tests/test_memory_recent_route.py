"""Task #442 — tests for ``GET /api/edu/memory/recent`` (the
"Pick up where you left off" dashboard widget endpoint added in
Task #415).

Covers:
  * Anonymous callers get ``{"items": [], "anon": true}`` (never a 401).
  * Signed-in callers get items shaped via ``_shape`` (Q/A split,
    metadata flattened, ``created_at`` ISO-formatted) sorted by
    ``created_at`` desc.
  * The Mongo projection excludes ``embedding`` so 1024-float vectors
    can never leak to the dashboard payload.
  * A Mongo read failure returns ``{items: [], ok: false}`` instead
    of bubbling up a 500.
  * The ``limit`` parameter is clamped to ``[1, _MAX_LIMIT]``.
"""
from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── Helpers ──────────────────────────────────────────────────────────


class _FakeCursor:
    """Minimal motor-cursor stand-in supporting the call chain
    ``col.find(...).sort(...).limit(...)`` followed by ``async for``."""

    def __init__(self, docs):
        self._docs = list(docs)
        self.sort_calls: list[tuple] = []
        self.limit_calls: list[int] = []
        self.find_projection = None

    def sort(self, key, direction):
        self.sort_calls.append((key, direction))
        return self

    def limit(self, n):
        self.limit_calls.append(n)
        return self

    def __aiter__(self):
        async def _gen():
            for d in self._docs:
                yield d
        return _gen()


class _FakeCollection:
    def __init__(self, docs=None, raise_on_find=False):
        self._docs = docs or []
        self._raise = raise_on_find
        self.find_filter = None
        self.find_projection = None
        self.cursor = None

    def find(self, filt, proj=None):
        if self._raise:
            raise RuntimeError("simulated mongo outage")
        self.find_filter = filt
        self.find_projection = proj
        self.cursor = _FakeCursor(self._docs)
        return self.cursor


class _FakeDB:
    def __init__(self, collection):
        self._collection = collection

    def __getitem__(self, name):
        # Route only ever reads the memory_brain collection; return
        # the same fake regardless of name so the test reads the
        # exact handle it inspects.
        return self._collection


def _build_client(*, user, collection):
    """Mount the route on a bare FastAPI app and override the auth
    dependency. Returns ``(client, set_db)`` so each test can swap
    the ``deps.db`` value the route reads."""
    from routes.memory_recent import router
    from auth_deps import get_current_user_optional

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_optional] = lambda: user
    return TestClient(app)


@pytest.fixture
def signed_in_user():
    return {"id": "user-42", "email": "stu@syrabit.ai"}


@pytest.fixture
def patch_deps_db(monkeypatch):
    """Patch ``deps.db`` (which the route imports lazily inside the
    handler) for the duration of a single test."""
    def _apply(db_value):
        import deps
        monkeypatch.setattr(deps, "db", db_value, raising=False)
    return _apply


# ── Tests ────────────────────────────────────────────────────────────


def test_anonymous_returns_empty_items_and_anon_true(patch_deps_db):
    """No JWT → ``{items: [], anon: true}``. Never a 401."""
    # Anonymous = dependency resolves to None.
    client = _build_client(user=None, collection=_FakeCollection())
    res = client.get("/edu/memory/recent")
    assert res.status_code == 200
    body = res.json()
    assert body == {"items": [], "anon": True, "limit": 5}


def test_anonymous_skips_db_entirely(patch_deps_db):
    """Anonymous path must short-circuit before touching Mongo —
    even if ``deps.db`` is a poison object that explodes on access."""
    class _Poison:
        def __getitem__(self, _name):
            raise AssertionError(
                "anonymous callers must not reach the Mongo layer"
            )
    patch_deps_db(_Poison())
    client = _build_client(user=None, collection=_FakeCollection())
    res = client.get("/edu/memory/recent?limit=3")
    assert res.status_code == 200
    body = res.json()
    assert body["items"] == []
    assert body["anon"] is True
    assert body["limit"] == 3


def test_signed_in_returns_shaped_items_sorted_by_created_at_desc(
    signed_in_user, patch_deps_db,
):
    """Documents come back via ``_shape``: Q/A split into title +
    preview, metadata flattened, ObjectId stringified, ``created_at``
    ISO-formatted. Sort order is whatever Mongo returns — we assert
    the route requests ``sort('created_at', -1)``."""
    t1 = _dt.datetime(2026, 5, 1, 10, 0, 0, tzinfo=_dt.timezone.utc)
    t0 = _dt.datetime(2026, 4, 30, 9, 0, 0, tzinfo=_dt.timezone.utc)
    docs = [
        {
            "_id": "mem-newer",
            "kind": "qa",
            "text": "Q: What is photosynthesis?\nA: Plants convert CO2 + H2O.",
            "metadata": {
                "event": "chat_turn",
                "subject_id": "bio-11",
                "subject_name": "Biology",
                "chapter_name": "Photosynthesis",
                "conversation_id": "conv-abc",
                "quality": "high",
            },
            "created_at": t1,
        },
        {
            "_id": "mem-older",
            "kind": "fact",
            "text": "Mitochondria are the powerhouse of the cell.",
            "metadata": {
                "event": "flashcard_recall",
                "subject_id": "bio-11",
                "subject_name": "Biology",
                "chapter_name": "Cells",
                "quality": "high",
            },
            "created_at": t0,
        },
    ]
    col = _FakeCollection(docs=docs)
    patch_deps_db(_FakeDB(col))

    client = _build_client(user=signed_in_user, collection=col)
    res = client.get("/edu/memory/recent")
    assert res.status_code == 200
    body = res.json()

    assert body["anon"] is False
    assert body["ok"] is True
    assert body["limit"] == 5
    assert len(body["items"]) == 2

    first, second = body["items"]
    # Q/A split applied to the chat-turn doc.
    assert first["id"] == "mem-newer"
    assert first["kind"] == "qa"
    assert first["title"] == "What is photosynthesis?"
    assert first["preview"] == "Plants convert CO2 + H2O."
    assert first["subject_name"] == "Biology"
    assert first["chapter_name"] == "Photosynthesis"
    assert first["conversation_id"] == "conv-abc"
    assert first["event"] == "chat_turn"
    assert first["created_at"] == t1.isoformat()

    # Plain text (no Q:/A:) lands in title with empty preview.
    assert second["id"] == "mem-older"
    assert second["kind"] == "fact"
    assert second["event"] == "flashcard_recall"
    assert second["title"] == "Mitochondria are the powerhouse of the cell."
    assert second["preview"] == ""
    assert second["created_at"] == t0.isoformat()

    # Route filters by user_id and asks Mongo to sort newest-first.
    assert col.find_filter == {"user_id": "user-42"}
    assert col.cursor.sort_calls == [("created_at", -1)]
    assert col.cursor.limit_calls == [5]


def test_embedding_field_is_never_returned(signed_in_user, patch_deps_db):
    """Embeddings are 1024 floats — they must be excluded from the
    payload via the Mongo projection AND never end up on a card
    even if a document somehow carries one through."""
    docs = [{
        "_id": "mem-1",
        "kind": "qa",
        "text": "Q: x\nA: y",
        "metadata": {},
        "created_at": _dt.datetime(2026, 5, 1, tzinfo=_dt.timezone.utc),
        # Defensive: even if a doc smuggled an embedding through, the
        # shaper must drop it.
        "embedding": [0.1] * 1024,
    }]
    col = _FakeCollection(docs=docs)
    patch_deps_db(_FakeDB(col))

    client = _build_client(user=signed_in_user, collection=col)
    res = client.get("/edu/memory/recent")
    assert res.status_code == 200
    body = res.json()

    # Projection passed to Mongo excludes the embedding field.
    assert col.find_projection == {"embedding": 0}
    # And no shaped item has it on the wire either.
    for item in body["items"]:
        assert "embedding" not in item


def test_mongo_failure_returns_empty_items_not_500(
    signed_in_user, patch_deps_db,
):
    """A Mongo outage must not 500 — the widget is best-effort."""
    col = _FakeCollection(raise_on_find=True)
    patch_deps_db(_FakeDB(col))

    client = _build_client(user=signed_in_user, collection=col)
    res = client.get("/edu/memory/recent")
    assert res.status_code == 200
    body = res.json()
    assert body["items"] == []
    assert body["anon"] is False
    assert body["ok"] is False


def test_db_unavailable_returns_empty_items_not_500(
    signed_in_user, patch_deps_db,
):
    """If ``deps.db is None`` (Mongo not configured in this env), the
    route returns the same empty / ok=false envelope as a Mongo
    failure rather than crashing."""
    patch_deps_db(None)
    client = _build_client(user=signed_in_user, collection=_FakeCollection())
    res = client.get("/edu/memory/recent")
    assert res.status_code == 200
    body = res.json()
    assert body == {"items": [], "anon": False, "limit": 5, "ok": False}


def test_limit_param_is_clamped(signed_in_user, patch_deps_db):
    """``limit`` is clamped to ``[1, _MAX_LIMIT(=10)]`` and the
    clamped value drives the Mongo ``.limit(...)`` call."""
    col = _FakeCollection(docs=[])
    patch_deps_db(_FakeDB(col))
    client = _build_client(user=signed_in_user, collection=col)

    res = client.get("/edu/memory/recent?limit=999")
    assert res.status_code == 200
    assert res.json()["limit"] == 10
    assert col.cursor.limit_calls == [10]

    col2 = _FakeCollection(docs=[])
    patch_deps_db(_FakeDB(col2))
    client2 = _build_client(user=signed_in_user, collection=col2)
    # Negative values clamp to the lower bound of 1.
    res2 = client2.get("/edu/memory/recent?limit=-3")
    assert res2.status_code == 200
    assert res2.json()["limit"] == 1
    assert col2.cursor.limit_calls == [1]

