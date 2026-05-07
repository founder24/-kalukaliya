"""Task #480 — Lock the ``DELETE /api/user/memories`` "forget everything"
contract introduced in Task #443.

Companion to the existing ``test_memory_browse_route.py`` suite (which
covers the per-id privacy scoping). This module focuses tightly on the
bulk-delete privacy control:

  * Response shape is exactly ``{"ok": True, "deleted": <int>}`` so the
    UI's toast (``Forgot N memories``) keeps working.
  * The Mongo ``delete_many`` is hard-scoped on ``user_id`` — wiping
    user A's memories must leave user B's ``memory_brain`` rows fully
    intact and still readable.
  * Calling forget-all when the caller has no memories returns
    ``deleted == 0`` (and 200, not 404) so the empty-state path can
    surface the "Nothing to forget" toast cleanly.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

import pytest
from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── Filter-aware fake Mongo collection ───────────────────────────────


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if "." in key:
            head, tail = key.split(".", 1)
            sub = doc.get(head) or {}
            if not isinstance(sub, dict) or sub.get(tail) != expected:
                return False
        elif doc.get(key) != expected:
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
    def __init__(self, docs):
        self.docs: list[dict[str, Any]] = list(docs)
        self.delete_many_calls: list[dict[str, Any]] = []

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

    async def delete_many(self, query):
        # Capture every call so the test can assert the filter is
        # always hard-scoped on user_id (no ``{}`` "wipe everything").
        self.delete_many_calls.append(dict(query))
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

A_IDS = [ObjectId() for _ in range(3)]
B_IDS = [ObjectId() for _ in range(2)]


def _seed_docs():
    t0 = _dt.datetime(2026, 5, 1, 10, 0, 0, tzinfo=_dt.timezone.utc)
    docs = []
    for i, oid in enumerate(A_IDS):
        docs.append({
            "_id": oid, "user_id": "user-A", "kind": "qa",
            "text": f"A memory #{i}", "metadata": {"subject_id": "bio-11"},
            "created_at": t0 + _dt.timedelta(minutes=i),
        })
    for i, oid in enumerate(B_IDS):
        docs.append({
            "_id": oid, "user_id": "user-B", "kind": "fact",
            "text": f"B memory #{i}", "metadata": {"subject_id": "phy-11"},
            "created_at": t0 + _dt.timedelta(hours=1, minutes=i),
        })
    return docs


@pytest.fixture
def collection(monkeypatch):
    col = _FakeCollection(_seed_docs())
    import deps
    monkeypatch.setattr(deps, "db", _FakeDB(col), raising=False)
    return col


def _client(user):
    from routes.memory_browse import router
    from auth_deps import get_current_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


# ── Tests ────────────────────────────────────────────────────────────


def test_forget_all_returns_ok_true_and_exact_deleted_count(collection):
    """Response contract: ``{ok: True, deleted: <int>}`` — the UI toast
    in MyMemoriesPage.jsx pulls ``deleted`` straight off the wire."""
    res = _client(USER_A).delete("/user/memories")
    assert res.status_code == 200
    body = res.json()
    assert body == {"ok": True, "deleted": len(A_IDS)}
    # Belt-and-braces: ``deleted`` is a plain int (not a bool/str), so
    # the frontend's ``Number(...)`` coercion stays a no-op.
    assert isinstance(body["deleted"], int) and not isinstance(body["deleted"], bool)


def test_forget_all_only_removes_callers_rows(collection):
    """User A's wipe must leave every one of user B's memory_brain rows
    intact and still GET-able."""
    res = _client(USER_A).delete("/user/memories")
    assert res.status_code == 200
    assert res.json()["deleted"] == len(A_IDS)

    # Underlying collection: only B's docs survive.
    remaining_ids = {d["_id"] for d in collection.docs}
    assert remaining_ids == set(B_IDS)

    # And B can still list every one of their memories.
    res_b = _client(USER_B).get("/user/memories")
    assert res_b.status_code == 200
    body = res_b.json()
    assert body["total"] == len(B_IDS)
    assert {item["id"] for item in body["items"]} == {str(x) for x in B_IDS}


def test_forget_all_filter_is_always_user_scoped(collection):
    """Defense-in-depth: the Mongo filter handed to ``delete_many``
    must always carry ``user_id`` so a future refactor cannot
    accidentally call ``delete_many({})`` and wipe the whole
    collection."""
    _client(USER_A).delete("/user/memories")
    assert collection.delete_many_calls, "delete_many was never invoked"
    for call in collection.delete_many_calls:
        assert call.get("user_id") == "user-A", (
            f"delete_many filter must be user-scoped, got: {call!r}"
        )


def test_forget_all_with_no_memories_returns_zero_not_404(collection):
    """A user with nothing saved still gets a clean 200 + ``deleted: 0``
    — that's how the UI surfaces its "Nothing to forget" toast."""
    # Wipe A first, then call forget-all again — the second call
    # should be a no-op happy path, not a 404.
    client_a = _client(USER_A)
    client_a.delete("/user/memories")
    res = client_a.delete("/user/memories")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "deleted": 0}
