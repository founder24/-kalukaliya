"""Unit tests for the V4 §13 / ADR-0001 Phase 2 dual-write helper."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure backend root is importable when pytest is invoked from the repo root.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


@pytest.fixture
def reset_env(monkeypatch):
    monkeypatch.delenv("MONGO_USER_WRITES", raising=False)
    yield monkeypatch


@pytest.fixture
def fresh_module(reset_env):
    import db_dualwrite as m
    m.reset_dualwrite_counters_for_test()
    yield m
    m.reset_dualwrite_counters_for_test()


@pytest.fixture
def fake_db(monkeypatch, fresh_module):
    import deps
    fake = MagicMock()
    fake.users = MagicMock()
    fake.users.update_one = AsyncMock(return_value=None)
    monkeypatch.setattr(deps, "db", fake)
    return fake


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_flag_default_is_enabled(fresh_module):
    assert fresh_module.mongo_user_writes_enabled() is True


def test_flag_falsy_values_disable(reset_env, fresh_module):
    for v in ("0", "false", "FALSE", "no", "off"):
        reset_env.setenv("MONGO_USER_WRITES", v)
        assert fresh_module.mongo_user_writes_enabled() is False


def test_flag_truthy_values_enable(reset_env, fresh_module):
    for v in ("1", "true", "yes", "on", ""):
        reset_env.setenv("MONGO_USER_WRITES", v)
        assert fresh_module.mongo_user_writes_enabled() is True


def test_mirror_success_increments_counter(fresh_module, fake_db):
    async def go():
        await fresh_module.mirror_user_write(
            "insert",
            lambda: fake_db.users.update_one({"id": "u1"}, {"$set": {"x": 1}}, upsert=True),
        )
    _run(go())
    fake_db.users.update_one.assert_awaited_once()
    counters = fresh_module.get_dualwrite_counters()
    assert counters["users.success"] == 1
    assert counters["users.fail"] == 0


def test_mirror_swallows_exception_and_increments_fail(fresh_module, fake_db):
    fake_db.users.update_one.side_effect = RuntimeError("mongo down")

    async def go():
        # Must NOT raise — PG remains SoT.
        await fresh_module.mirror_user_write(
            "update",
            lambda: fake_db.users.update_one({"id": "u1"}, {"$set": {"x": 2}}),
        )
    _run(go())
    counters = fresh_module.get_dualwrite_counters()
    assert counters["users.fail"] == 1
    assert counters["users.success"] == 0


def test_mirror_disabled_skips_call(reset_env, fresh_module, fake_db):
    reset_env.setenv("MONGO_USER_WRITES", "0")

    async def go():
        await fresh_module.mirror_user_write(
            "insert",
            lambda: fake_db.users.update_one({"id": "u1"}, {"$set": {}}),
        )
    _run(go())
    fake_db.users.update_one.assert_not_awaited()
    counters = fresh_module.get_dualwrite_counters()
    assert counters["users.skipped_disabled"] == 1
    assert counters["users.success"] == 0


def test_mirror_skips_when_db_not_ready(monkeypatch, fresh_module):
    import deps
    monkeypatch.setattr(deps, "db", None)
    called = {"n": 0}

    async def fn():
        called["n"] += 1

    async def go():
        await fresh_module.mirror_user_write("insert", fn)
    _run(go())
    assert called["n"] == 0
    counters = fresh_module.get_dualwrite_counters()
    assert counters["users.skipped_no_db"] == 1


# ── Pipeline-update floor (architect-flagged: refund mirrors must NOT go negative) ──

def test_clamped_decrement_pipeline_shape(fresh_module):
    """Clamp must produce: $set field = $max[0, $subtract[$ifNull[$field,0], N]]."""
    p = fresh_module.clamped_decrement_pipeline(
        {"credits_used_today": 1, "credits_used": 2}
    )
    assert isinstance(p, list) and len(p) == 1
    set_stage = p[0]["$set"]
    assert set(set_stage) == {"credits_used_today", "credits_used"}
    today = set_stage["credits_used_today"]
    assert today["$max"][0] == 0
    sub = today["$max"][1]["$subtract"]
    assert sub[0] == {"$ifNull": ["$credits_used_today", 0]}
    assert sub[1] == 1
    # Second field carries the per-field N
    assert set_stage["credits_used"]["$max"][1]["$subtract"][1] == 2


def test_clamped_decrement_pipeline_handles_str_int(fresh_module):
    """Caller may pass a numeric string by accident — coerce to int."""
    p = fresh_module.clamped_decrement_pipeline({"credits_used": "3"})
    assert p[0]["$set"]["credits_used"]["$max"][1]["$subtract"][1] == 3


# ── conversations collection (B4 Phase 2 second-collection rollout) ──

@pytest.fixture
def fake_conversations_db(monkeypatch, fresh_module):
    import deps
    fake = MagicMock()
    fake.conversations = MagicMock()
    fake.conversations.replace_one = AsyncMock(return_value=None)
    fake.conversations.update_one = AsyncMock(return_value=None)
    fake.conversations.delete_one = AsyncMock(return_value=None)
    monkeypatch.setattr(deps, "db", fake)
    return fake


def test_conversation_flag_env_name(fresh_module):
    """Conversations must use MONGO_CONVERSATION_WRITES (singular form)."""
    assert fresh_module._flag_env_for("conversations") == "MONGO_CONVERSATION_WRITES"


def test_conversation_flag_default_enabled(fresh_module):
    assert fresh_module.mongo_collection_writes_enabled("conversations") is True


def test_conversation_flag_disable(reset_env, fresh_module):
    reset_env.setenv("MONGO_CONVERSATION_WRITES", "0")
    assert fresh_module.mongo_collection_writes_enabled("conversations") is False
    # users flag must remain independent
    assert fresh_module.mongo_collection_writes_enabled("users") is True


def test_conversation_mirror_success_increments_namespaced_counter(
    fresh_module, fake_conversations_db
):
    async def go():
        await fresh_module.mirror_conversation_write(
            "upsert",
            lambda: fake_conversations_db.conversations.replace_one(
                {"id": "c1"}, {"id": "c1", "title": "x"}, upsert=True
            ),
        )
    _run(go())
    fake_conversations_db.conversations.replace_one.assert_awaited_once()
    counters = fresh_module.get_dualwrite_counters()
    assert counters["conversations.success"] == 1
    assert counters["conversations.fail"] == 0
    # Users counters must NOT be touched by a conversation mirror.
    assert "users.success" not in counters or counters.get("users.success", 0) == 0


def test_conversation_mirror_swallows_exception(fresh_module, fake_conversations_db):
    fake_conversations_db.conversations.update_one.side_effect = RuntimeError("boom")

    async def go():
        await fresh_module.mirror_conversation_write(
            "update",
            lambda: fake_conversations_db.conversations.update_one(
                {"id": "c1", "user_id": "u1"}, {"$set": {"title": "y"}}
            ),
        )
    _run(go())  # must NOT raise
    counters = fresh_module.get_dualwrite_counters()
    assert counters["conversations.fail"] == 1
    assert counters["conversations.success"] == 0


def test_conversation_disable_does_not_affect_user_counters(
    reset_env, fresh_module, fake_conversations_db
):
    """Per-collection flags are independent — disabling conversations
    does not turn off users mirroring."""
    reset_env.setenv("MONGO_CONVERSATION_WRITES", "0")

    async def go():
        await fresh_module.mirror_conversation_write(
            "delete",
            lambda: fake_conversations_db.conversations.delete_one({"id": "c1"}),
        )
    _run(go())
    fake_conversations_db.conversations.delete_one.assert_not_awaited()
    counters = fresh_module.get_dualwrite_counters()
    assert counters["conversations.skipped_disabled"] == 1
    assert fresh_module.mongo_user_writes_enabled() is True


def test_back_compat_user_shims_still_work(fresh_module):
    """B4 callers using mirror_user_write / mongo_user_writes_enabled
    must keep working through the generic helper."""
    assert fresh_module.mongo_user_writes_enabled() is True
    # The shim wires through to mirror_collection_write('users', ...).
    assert fresh_module._flag_env_for("users") == "MONGO_USER_WRITES"


# ── edu_notes collection (third Phase 2 rollout) ──

@pytest.fixture
def fake_edu_notes_db(monkeypatch, fresh_module):
    import deps
    fake = MagicMock()
    fake.edu_notes = MagicMock()
    fake.edu_notes.insert_one = AsyncMock(return_value=None)
    fake.edu_notes.replace_one = AsyncMock(return_value=None)
    fake.edu_notes.delete_one = AsyncMock(return_value=None)
    fake.edu_notes.update_many = AsyncMock(return_value=None)
    monkeypatch.setattr(deps, "db", fake)
    return fake


def test_edu_notes_flag_env_name(fresh_module):
    """edu_notes must use MONGO_EDU_NOTE_WRITES (singular form)."""
    assert fresh_module._flag_env_for("edu_notes") == "MONGO_EDU_NOTE_WRITES"


def test_edu_notes_flag_default_enabled(fresh_module):
    assert fresh_module.mongo_collection_writes_enabled("edu_notes") is True


def test_edu_notes_flag_disable_independent(reset_env, fresh_module):
    reset_env.setenv("MONGO_EDU_NOTE_WRITES", "0")
    assert fresh_module.mongo_collection_writes_enabled("edu_notes") is False
    # Other collections must NOT be affected.
    assert fresh_module.mongo_collection_writes_enabled("users") is True
    assert fresh_module.mongo_collection_writes_enabled("conversations") is True


def test_edu_notes_mirror_success_increments_namespaced_counter(
    fresh_module, fake_edu_notes_db
):
    async def go():
        await fresh_module.mirror_edu_notes_write(
            "insert",
            lambda: fake_edu_notes_db.edu_notes.insert_one(
                {"id": "n1", "actor_kind": "user", "actor": "u1", "text": "hi"}
            ),
        )
    _run(go())
    fake_edu_notes_db.edu_notes.insert_one.assert_awaited_once()
    counters = fresh_module.get_dualwrite_counters()
    assert counters["edu_notes.success"] == 1
    assert counters["edu_notes.fail"] == 0
    # Other namespaces untouched.
    assert counters.get("users.success", 0) == 0
    assert counters.get("conversations.success", 0) == 0


def test_edu_notes_mirror_swallows_exception(fresh_module, fake_edu_notes_db):
    fake_edu_notes_db.edu_notes.update_many.side_effect = RuntimeError("boom")

    async def go():
        await fresh_module.mirror_edu_notes_write(
            "claim_bulk",
            lambda: fake_edu_notes_db.edu_notes.update_many(
                {"actor_kind": "anon", "actor": "a1"},
                {"$set": {"actor_kind": "user", "actor": "u1"}},
            ),
        )
    _run(go())  # must NOT raise
    counters = fresh_module.get_dualwrite_counters()
    assert counters["edu_notes.fail"] == 1
    assert counters["edu_notes.success"] == 0
