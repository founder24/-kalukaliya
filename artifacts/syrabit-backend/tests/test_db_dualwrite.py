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


# ── edu_flashcards collection (fourth Phase 2 rollout) ──

@pytest.fixture
def fake_edu_flashcards_db(monkeypatch, fresh_module):
    import deps
    fake = MagicMock()
    fake.edu_flashcards = MagicMock()
    fake.edu_flashcards.insert_many = AsyncMock(return_value=None)
    fake.edu_flashcards.replace_one = AsyncMock(return_value=None)
    fake.edu_flashcards.update_many = AsyncMock(return_value=None)
    monkeypatch.setattr(deps, "db", fake)
    return fake


def test_edu_flashcards_flag_env_name(fresh_module):
    """edu_flashcards must use MONGO_EDU_FLASHCARD_WRITES (singular)."""
    assert fresh_module._flag_env_for("edu_flashcards") == "MONGO_EDU_FLASHCARD_WRITES"


def test_edu_flashcards_flag_default_enabled(fresh_module):
    assert fresh_module.mongo_collection_writes_enabled("edu_flashcards") is True


def test_edu_flashcards_flag_disable_independent(reset_env, fresh_module):
    reset_env.setenv("MONGO_EDU_FLASHCARD_WRITES", "0")
    assert fresh_module.mongo_collection_writes_enabled("edu_flashcards") is False
    # Sibling collections must NOT be affected.
    assert fresh_module.mongo_collection_writes_enabled("users") is True
    assert fresh_module.mongo_collection_writes_enabled("conversations") is True
    assert fresh_module.mongo_collection_writes_enabled("edu_notes") is True


def test_edu_flashcards_bulk_insert_success(
    fresh_module, fake_edu_flashcards_db
):
    """Mirror an insert_many bulk build (the build_flashcards hot path)."""
    docs = [
        {"id": f"c{i}", "actor_kind": "user", "actor": "u1",
         "front": f"q{i}", "back": f"a{i}"}
        for i in range(3)
    ]

    async def go():
        await fresh_module.mirror_edu_flashcards_write(
            "build_bulk",
            lambda: fake_edu_flashcards_db.edu_flashcards.insert_many(
                docs, ordered=False
            ),
        )
    _run(go())
    fake_edu_flashcards_db.edu_flashcards.insert_many.assert_awaited_once()
    counters = fresh_module.get_dualwrite_counters()
    assert counters["edu_flashcards.success"] == 1
    assert counters["edu_flashcards.fail"] == 0
    # Sibling counters untouched.
    assert counters.get("edu_notes.success", 0) == 0


def test_edu_flashcards_mirror_swallows_exception(
    fresh_module, fake_edu_flashcards_db
):
    fake_edu_flashcards_db.edu_flashcards.replace_one.side_effect = (
        RuntimeError("boom")
    )

    async def go():
        await fresh_module.mirror_edu_flashcards_write(
            "review",
            lambda: fake_edu_flashcards_db.edu_flashcards.replace_one(
                {"id": "c1"}, {"id": "c1", "ef": 2.6}, upsert=True,
            ),
        )
    _run(go())  # must NOT raise
    counters = fresh_module.get_dualwrite_counters()
    assert counters["edu_flashcards.fail"] == 1


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


# ── edu_study_settings collection (fifth Phase 2 rollout) ──

@pytest.fixture
def fake_edu_study_settings_db(monkeypatch, fresh_module):
    import deps
    fake = MagicMock()
    fake.edu_study_settings = MagicMock()
    fake.edu_study_settings.update_one = AsyncMock(return_value=None)
    fake.edu_study_settings.delete_one = AsyncMock(return_value=None)
    monkeypatch.setattr(deps, "db", fake)
    return fake


def test_edu_study_settings_flag_env_name(fresh_module):
    """edu_study_settings must use MONGO_EDU_STUDY_SETTING_WRITES (singular)."""
    assert (
        fresh_module._flag_env_for("edu_study_settings")
        == "MONGO_EDU_STUDY_SETTING_WRITES"
    )


def test_edu_study_settings_flag_default_enabled(fresh_module):
    assert (
        fresh_module.mongo_collection_writes_enabled("edu_study_settings") is True
    )


def test_edu_study_settings_flag_disable_independent(reset_env, fresh_module):
    reset_env.setenv("MONGO_EDU_STUDY_SETTING_WRITES", "0")
    assert (
        fresh_module.mongo_collection_writes_enabled("edu_study_settings") is False
    )
    # Sibling collections must NOT be affected.
    assert fresh_module.mongo_collection_writes_enabled("users") is True
    assert fresh_module.mongo_collection_writes_enabled("conversations") is True
    assert fresh_module.mongo_collection_writes_enabled("edu_notes") is True
    assert fresh_module.mongo_collection_writes_enabled("edu_flashcards") is True


def test_edu_study_settings_upsert_success(
    fresh_module, fake_edu_study_settings_db
):
    """Mirror a streak update_one upsert (the review_flashcard hot path)."""
    async def go():
        await fresh_module.mirror_edu_study_settings_write(
            "streak_update",
            lambda: fake_edu_study_settings_db.edu_study_settings.update_one(
                {"actor_kind": "user", "actor": "u1"},
                {"$set": {"streak_count": 3, "streak_last_day": "2026-05-06"}},
                upsert=True,
            ),
        )
    _run(go())
    fake_edu_study_settings_db.edu_study_settings.update_one.assert_awaited_once()
    counters = fresh_module.get_dualwrite_counters()
    assert counters["edu_study_settings.success"] == 1
    assert counters["edu_study_settings.fail"] == 0
    # Sibling counters untouched.
    assert counters.get("edu_flashcards.success", 0) == 0
    assert counters.get("edu_notes.success", 0) == 0


def test_edu_study_settings_delete_one_success(
    fresh_module, fake_edu_study_settings_db
):
    """Mirror the anon-side delete fired post-claim-transaction."""
    async def go():
        await fresh_module.mirror_edu_study_settings_write(
            "claim_anon_delete",
            lambda: fake_edu_study_settings_db.edu_study_settings.delete_one(
                {"actor_kind": "anon", "actor": "a1"},
            ),
        )
    _run(go())
    fake_edu_study_settings_db.edu_study_settings.delete_one.assert_awaited_once()
    counters = fresh_module.get_dualwrite_counters()
    assert counters["edu_study_settings.success"] == 1


def test_edu_study_settings_mirror_swallows_exception(
    fresh_module, fake_edu_study_settings_db
):
    fake_edu_study_settings_db.edu_study_settings.update_one.side_effect = (
        RuntimeError("boom")
    )

    async def go():
        await fresh_module.mirror_edu_study_settings_write(
            "set_strict_mode",
            lambda: fake_edu_study_settings_db.edu_study_settings.update_one(
                {"actor_kind": "user", "actor": "u1"},
                {"$set": {"strict_mode": True}},
                upsert=True,
            ),
        )
    _run(go())  # must NOT raise — PG is SoT
    counters = fresh_module.get_dualwrite_counters()
    assert counters["edu_study_settings.fail"] == 1
    assert counters["edu_study_settings.success"] == 0


# ── activity_log collection (sixth Phase 2 rollout — soft join) ──

@pytest.fixture
def fake_activity_log_db(monkeypatch, fresh_module):
    import deps
    fake = MagicMock()
    fake.activity_log = MagicMock()
    fake.activity_log.insert_one = AsyncMock(return_value=None)
    fake.activity_log.delete_many = AsyncMock(return_value=None)
    monkeypatch.setattr(deps, "db", fake)
    return fake


def test_activity_log_flag_env_name(fresh_module):
    """activity_log has no trailing 's' — default name (no override) is correct."""
    assert (
        fresh_module._flag_env_for("activity_log")
        == "MONGO_ACTIVITY_LOG_WRITES"
    )


def test_activity_log_flag_default_enabled(fresh_module):
    assert (
        fresh_module.mongo_collection_writes_enabled("activity_log") is True
    )


def test_activity_log_flag_disable_independent(reset_env, fresh_module):
    reset_env.setenv("MONGO_ACTIVITY_LOG_WRITES", "0")
    assert fresh_module.mongo_collection_writes_enabled("activity_log") is False
    # Sibling collections must NOT be affected.
    assert fresh_module.mongo_collection_writes_enabled("users") is True
    assert fresh_module.mongo_collection_writes_enabled("conversations") is True
    assert fresh_module.mongo_collection_writes_enabled("edu_notes") is True
    assert fresh_module.mongo_collection_writes_enabled("edu_flashcards") is True
    assert (
        fresh_module.mongo_collection_writes_enabled("edu_study_settings") is True
    )


def test_activity_log_insert_success(fresh_module, fake_activity_log_db):
    """Mirror the supa_insert_activity_log PG-success branch."""
    async def go():
        await fresh_module.mirror_activity_log_write(
            "insert",
            lambda: fake_activity_log_db.activity_log.insert_one(
                {"id": "x", "action": "test", "level": "info"},
            ),
        )
    _run(go())
    fake_activity_log_db.activity_log.insert_one.assert_awaited_once()
    counters = fresh_module.get_dualwrite_counters()
    assert counters["activity_log.success"] == 1
    assert counters["activity_log.fail"] == 0
    # Sibling counters untouched.
    assert counters.get("edu_study_settings.success", 0) == 0


def test_activity_log_clear_delete_many_success(
    fresh_module, fake_activity_log_db
):
    """Mirror the supa_clear_activity_log bulk purge."""
    async def go():
        await fresh_module.mirror_activity_log_write(
            "clear",
            lambda: fake_activity_log_db.activity_log.delete_many({}),
        )
    _run(go())
    fake_activity_log_db.activity_log.delete_many.assert_awaited_once_with({})
    counters = fresh_module.get_dualwrite_counters()
    assert counters["activity_log.success"] == 1


def test_activity_log_mirror_swallows_exception(
    fresh_module, fake_activity_log_db
):
    """Mongo failure must NOT propagate — PG remains SoT, audit trail safe."""
    fake_activity_log_db.activity_log.insert_one.side_effect = (
        RuntimeError("mongo down")
    )

    async def go():
        await fresh_module.mirror_activity_log_write(
            "insert",
            lambda: fake_activity_log_db.activity_log.insert_one(
                {"id": "x", "action": "audit_event"},
            ),
        )
    _run(go())  # must NOT raise — PG is SoT
    counters = fresh_module.get_dualwrite_counters()
    assert counters["activity_log.fail"] == 1
    assert counters["activity_log.success"] == 0


# ── notifications collection (seventh Phase 2 rollout — soft join) ──

@pytest.fixture
def fake_notifications_db(monkeypatch, fresh_module):
    import deps
    fake = MagicMock()
    fake.notifications = MagicMock()
    fake.notifications.insert_one = AsyncMock(return_value=None)
    fake.notifications.delete_one = AsyncMock(return_value=None)
    monkeypatch.setattr(deps, "db", fake)
    return fake


def test_notifications_flag_env_name(fresh_module):
    """notifications has a trailing 's' — default rstrip rule yields the
    singular ``MONGO_NOTIFICATION_WRITES`` (no override entry needed)."""
    assert (
        fresh_module._flag_env_for("notifications")
        == "MONGO_NOTIFICATION_WRITES"
    )


def test_notifications_flag_default_enabled(fresh_module):
    assert (
        fresh_module.mongo_collection_writes_enabled("notifications") is True
    )


def test_notifications_flag_disable_independent(reset_env, fresh_module):
    reset_env.setenv("MONGO_NOTIFICATION_WRITES", "0")
    assert fresh_module.mongo_collection_writes_enabled("notifications") is False
    # Sibling collections must NOT be affected.
    assert fresh_module.mongo_collection_writes_enabled("activity_log") is True
    assert fresh_module.mongo_collection_writes_enabled("users") is True
    assert fresh_module.mongo_collection_writes_enabled("conversations") is True


def test_notifications_insert_success(fresh_module, fake_notifications_db):
    """Mirror the supa_insert_notification PG-success branch."""
    async def go():
        await fresh_module.mirror_notifications_write(
            "insert",
            lambda: fake_notifications_db.notifications.insert_one(
                {"id": "n1", "title": "Hi", "message": "test"},
            ),
        )
    _run(go())
    fake_notifications_db.notifications.insert_one.assert_awaited_once()
    counters = fresh_module.get_dualwrite_counters()
    assert counters["notifications.success"] == 1
    assert counters["notifications.fail"] == 0
    # Sibling counters untouched.
    assert counters.get("activity_log.success", 0) == 0


def test_notifications_delete_one_success(
    fresh_module, fake_notifications_db
):
    """Mirror the supa_delete_notification per-id delete."""
    async def go():
        await fresh_module.mirror_notifications_write(
            "delete",
            lambda: fake_notifications_db.notifications.delete_one(
                {"id": "n1"},
            ),
        )
    _run(go())
    fake_notifications_db.notifications.delete_one.assert_awaited_once_with(
        {"id": "n1"}
    )
    counters = fresh_module.get_dualwrite_counters()
    assert counters["notifications.success"] == 1


def test_notifications_mirror_swallows_exception(
    fresh_module, fake_notifications_db
):
    """Mongo failure must NOT propagate — PG remains SoT."""
    fake_notifications_db.notifications.insert_one.side_effect = (
        RuntimeError("mongo down")
    )

    async def go():
        await fresh_module.mirror_notifications_write(
            "insert",
            lambda: fake_notifications_db.notifications.insert_one(
                {"id": "n1", "title": "broken"},
            ),
        )
    _run(go())  # must NOT raise — PG is SoT
    counters = fresh_module.get_dualwrite_counters()
    assert counters["notifications.fail"] == 1
    assert counters["notifications.success"] == 0
