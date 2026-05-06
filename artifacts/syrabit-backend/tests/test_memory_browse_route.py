"""Task #445 — privacy regression tests for the memory browse / delete
endpoints (``GET /api/user/memories`` and
``DELETE /api/user/memories/{memory_id}``).

The contract being protected: every Mongo query in
``routes/memory_browse.py`` is hard-scoped to the caller's ``user_id``
so a logged-in student can only see and delete their own memory_brain
entries. These tests seed two users (A and B) into a fake Mongo
collection and assert:

  * A's GET returns only A's docs (B's docs never leak).
  * A's DELETE on B's memory id returns 404 and leaves B's doc intact.
  * Unauthenticated GET / DELETE / DELETE-all all return 401.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

import pytest
from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── Fake Mongo collection (filter-aware) ─────────────────────────────


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    """Tiny subset of Mongo's match semantics — enough for the
    ``user_id`` / ``_id`` / ``metadata.subject_id`` / ``kind`` filters
    used by ``routes/memory_browse.py``."""
    for key, expected in query.items():
        if "." in key:
            head, tail = key.split(".", 1)
            sub = doc.get(head) or {}
            if not isinstance(sub, dict) or sub.get(tail) != expected:
                return False
        else:
            if doc.get(key) != expected:
                return False
    return True


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction):
        self._docs.sort(
            key=lambda d: d.get(key) or _dt.datetime.min.replace(
                tzinfo=_dt.timezone.utc,
            ),
            reverse=(direction == -1),
        )
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __aiter__(self):
        async def _gen():
            for d in self._docs:
                yield d
        return _gen()


class _FakeCollection:
    """In-memory stand-in for a motor collection."""

    def __init__(self, docs):
        self.docs: list[dict[str, Any]] = list(docs)

    async def count_documents(self, query):
        return sum(1 for d in self.docs if _matches(d, query))

    def find(self, query, projection=None):
        matched = [d for d in self.docs if _matches(d, query)]
        if projection:
            drop = {k for k, v in projection.items() if v == 0}
            matched = [
                {k: v for k, v in d.items() if k not in drop}
                for d in matched
            ]
        return _FakeCursor(matched)

    async def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _matches(d, query):
                self.docs.pop(i)
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()

    async def delete_many(self, query):
        keep = [d for d in self.docs if not _matches(d, query)]
        removed = len(self.docs) - len(keep)
        self.docs = keep
        return type("R", (), {"deleted_count": removed})()


class _FakeDB:
    def __init__(self, collection):
        self._collection = collection

    def __getitem__(self, _name):
        return self._collection


# ── Fixtures ─────────────────────────────────────────────────────────


USER_A = {"id": "user-A", "email": "a@syrabit.ai"}
USER_B = {"id": "user-B", "email": "b@syrabit.ai"}

A_MEM_1 = ObjectId()
A_MEM_2 = ObjectId()
B_MEM_1 = ObjectId()
B_MEM_2 = ObjectId()


def _seed_docs():
    t0 = _dt.datetime(2026, 5, 1, 10, 0, 0, tzinfo=_dt.timezone.utc)
    return [
        {
            "_id": A_MEM_1, "user_id": "user-A", "kind": "qa",
            "text": "A's first memory",
            "metadata": {"subject_id": "bio-11"},
            "created_at": t0,
        },
        {
            "_id": A_MEM_2, "user_id": "user-A", "kind": "fact",
            "text": "A's second memory",
            "metadata": {"subject_id": "phy-11"},
            "created_at": t0 + _dt.timedelta(hours=1),
        },
        {
            "_id": B_MEM_1, "user_id": "user-B", "kind": "qa",
            "text": "B's first memory",
            "metadata": {"subject_id": "bio-11"},
            "created_at": t0 + _dt.timedelta(hours=2),
        },
        {
            "_id": B_MEM_2, "user_id": "user-B", "kind": "note",
            "text": "B's second memory",
            "metadata": {"subject_id": "chem-11"},
            "created_at": t0 + _dt.timedelta(hours=3),
        },
    ]


@pytest.fixture
def collection(monkeypatch):
    col = _FakeCollection(_seed_docs())
    import deps
    monkeypatch.setattr(deps, "db", _FakeDB(col), raising=False)
    return col


def _build_app(user):
    """Mount the route on a bare FastAPI app. If ``user`` is None, do
    NOT override ``get_current_user`` so the real dependency raises 401
    on the missing Authorization header."""
    from routes.memory_browse import router
    from auth_deps import get_current_user

    app = FastAPI()
    app.include_router(router)
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


# ── Tests ────────────────────────────────────────────────────────────


def test_get_returns_only_callers_memories(collection):
    """User A's GET returns A's two docs only — B's never leak."""
    client = _build_app(USER_A)
    res = client.get("/user/memories")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    ids = {item["id"] for item in body["items"]}
    assert ids == {str(A_MEM_1), str(A_MEM_2)}
    # Belt-and-braces: the texts on the wire are A's, never B's.
    texts = {item["text"] for item in body["items"]}
    assert "B's first memory" not in texts
    assert "B's second memory" not in texts


def test_get_for_user_b_returns_only_b_docs(collection):
    """Symmetric assertion: user B sees only B's two docs."""
    client = _build_app(USER_B)
    res = client.get("/user/memories")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    ids = {item["id"] for item in body["items"]}
    assert ids == {str(B_MEM_1), str(B_MEM_2)}


def test_delete_other_users_memory_returns_404_and_leaves_doc_intact(
    collection,
):
    """User A trying to delete one of B's memory ids must:
       1. get a 404 (we deliberately do not distinguish "not found"
          from "not yours" so ids cannot be probed), and
       2. leave the doc in the collection — B can still see it."""
    client_a = _build_app(USER_A)
    res = client_a.delete(f"/user/memories/{B_MEM_1}")
    assert res.status_code == 404
    # B's doc is still in the underlying collection.
    assert any(d["_id"] == B_MEM_1 for d in collection.docs)

    # And B can still GET it back.
    client_b = _build_app(USER_B)
    res_b = client_b.get("/user/memories")
    assert res_b.status_code == 200
    assert any(item["id"] == str(B_MEM_1) for item in res_b.json()["items"])


def test_delete_own_memory_succeeds(collection):
    """Sanity check on the happy path: deleting your own memory works
    and removes exactly that one doc."""
    client = _build_app(USER_A)
    res = client.delete(f"/user/memories/{A_MEM_1}")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "id": str(A_MEM_1)}
    assert not any(d["_id"] == A_MEM_1 for d in collection.docs)
    # A's other doc + both of B's docs survive.
    assert len(collection.docs) == 3


def test_delete_all_only_clears_callers_memories(collection):
    """``DELETE /user/memories`` (forget-all) must hard-scope to the
    caller — wiping A's memories must leave B's untouched."""
    client = _build_app(USER_A)
    res = client.delete("/user/memories")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "deleted": 2}
    remaining_ids = {d["_id"] for d in collection.docs}
    assert remaining_ids == {B_MEM_1, B_MEM_2}


def test_unauthenticated_get_returns_401(collection):
    """No Authorization header → the real ``get_current_user``
    dependency raises 401 before the route body runs."""
    client = _build_app(user=None)
    res = client.get("/user/memories")
    assert res.status_code == 401


def test_unauthenticated_delete_returns_401(collection):
    client = _build_app(user=None)
    res = client.delete(f"/user/memories/{A_MEM_1}")
    assert res.status_code == 401
    # And the doc must survive an unauthenticated delete attempt.
    assert any(d["_id"] == A_MEM_1 for d in collection.docs)


def test_unauthenticated_delete_all_returns_401(collection):
    client = _build_app(user=None)
    res = client.delete("/user/memories")
    assert res.status_code == 401
    # Nothing got wiped.
    assert len(collection.docs) == 4
