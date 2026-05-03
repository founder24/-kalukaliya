"""Task #298 — backend tests for the JARVIS-style Syra upgrade.

Locks down the contracts the frontend orb relies on:

* ``GET  /admin/syra/actions``         — registry introspection.
* ``POST /admin/syra/execute-action``  — registry rejects unknown ids
  AND requires ``confirmed=True`` for destructive actions.
* ``GET  /admin/syra/briefing``        — returns a non-empty paragraph
  built from open-alerts / failed-jobs / negative-feedback / signups.
* ``POST /admin/syra/chat``            — accepts the new history +
  screen-context payload and normalises hallucinated action_ids back
  to a plain ``answer`` instead of leaking them to the frontend.
"""
from __future__ import annotations

import json
from unittest.mock import patch, AsyncMock

import pytest


@pytest.fixture
def mock_admin():
    return {
        "id": "admin-1", "email": "ops@syrabit.ai", "is_admin": True,
        "username": "ops", "sub": "admin-1", "name": "Ops Admin",
    }


@pytest.fixture
def client(mock_admin):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from auth_deps import get_admin_user
    from routes.admin_syra import router

    app = FastAPI(); app.include_router(router, prefix="/api")
    app.dependency_overrides = {get_admin_user: lambda: mock_admin}
    return TestClient(app)


# ── Registry endpoint ─────────────────────────────────────────────────────
def test_actions_registry_lists_all_eight(client):
    r = client.get("/api/admin/syra/actions")
    assert r.status_code == 200
    payload = r.json()
    ids = {a["id"] for a in payload["actions"]}
    # The registry is intentionally curated — these are the verbs the
    # frontend confirm card understands. If you add a new one, update
    # this test together with the spec card in syra_actions.py.
    expected = {
        "user.set_status", "user.set_plan", "user.reset_quiz_quota",
        "alert.acknowledge", "alert.acknowledge_all",
        "conversation.flag", "cache.purge_all", "settings.toggle_maintenance",
    }
    assert expected.issubset(ids), f"missing: {expected - ids}"
    # Every entry must declare a destructive flag and a label so the
    # confirm card can render without fallbacks.
    for a in payload["actions"]:
        assert isinstance(a["destructive"], bool)
        assert isinstance(a["label"], str) and a["label"]


# ── Execute-action contract ───────────────────────────────────────────────
def test_execute_unknown_action_returns_400(client):
    r = client.post(
        "/api/admin/syra/execute-action",
        json={"action_id": "user.delete_universe", "params": {}, "confirmed": True},
    )
    assert r.status_code == 400
    assert "Unknown" in r.json()["detail"]


def test_execute_destructive_requires_confirm(client):
    # cache.purge_all is destructive — without confirmed=True the
    # registry must refuse to dispatch even if the executor would
    # otherwise succeed silently.
    r = client.post(
        "/api/admin/syra/execute-action",
        json={"action_id": "cache.purge_all", "params": {}, "confirmed": False},
    )
    assert r.status_code == 400
    assert "confirm" in r.json()["detail"].lower()


def _patch_executor(action_id: str, fake):
    """Patch the executor stored on the registry entry rather than the
    module-level symbol. The dispatcher reads ``action.executor`` from
    the ``_REGISTRY`` dict at call time, so monkey-patching the module
    name (``syra_actions._exec_*``) would not take effect — the
    registry already holds the original function reference."""
    import syra_actions
    return patch.object(syra_actions._REGISTRY[action_id], "executor", fake)


def test_execute_non_destructive_runs_without_confirm(client):
    # alert.acknowledge is non-destructive (operator just confirms an
    # already-fired alert). Patch the registry-bound executor so the
    # call doesn't hit a real DB but the registry still routes through
    # the audit + dispatch path.
    fake = AsyncMock(return_value="Alert ack'd.")
    with _patch_executor("alert.acknowledge", fake):
        r = client.post(
            "/api/admin/syra/execute-action",
            json={"action_id": "alert.acknowledge", "params": {"alert_id": "a-1"}},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["action_id"] == "alert.acknowledge"
    assert "ack" in body["summary"].lower()
    fake.assert_awaited_once()


def test_execute_destructive_succeeds_when_confirmed(client):
    fake = AsyncMock(return_value="Purged.")
    with _patch_executor("cache.purge_all", fake):
        r = client.post(
            "/api/admin/syra/execute-action",
            json={"action_id": "cache.purge_all", "params": {}, "confirmed": True},
        )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    fake.assert_awaited_once()


def test_executor_value_error_surfaces_as_400(client):
    # When an executor raises ValueError (e.g. unknown alert id), the
    # dispatcher must wrap it as SyraActionError → HTTP 400 so the
    # frontend can show the precise reason rather than a generic 500.
    fake = AsyncMock(side_effect=ValueError("Alert xyz not found"))
    with _patch_executor("alert.acknowledge", fake):
        r = client.post(
            "/api/admin/syra/execute-action",
            json={"action_id": "alert.acknowledge", "params": {"alert_id": "xyz"}},
        )
    assert r.status_code == 400
    assert "not found" in r.json()["detail"].lower()


# ── Chat: history + context + hallucination guard ─────────────────────────
def test_chat_accepts_history_and_screen_context(client):
    fake_llm = AsyncMock(return_value=json.dumps({
        "action": "navigate", "target": "users",
        "response": "Heading to users.",
    }))
    with patch("routes.admin_syra.call_llm_api_chat", fake_llm):
        r = client.post(
            "/api/admin/syra/chat",
            json={
                "transcript": "show me that user",
                "history": [
                    {"role": "user", "content": "find priya"},
                    {"role": "assistant", "content": "Found one match."},
                ],
                "context": {
                    "active_section": "conversations",
                    "selected_entity": {"type": "conversation", "id": "c-1", "label": "Math chat"},
                    "filters": {"plan": "pro"},
                    "visible_error": None,
                },
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["action"] == "navigate"
    assert body["target"] == "users"
    # The system prompt + history + context must have all reached the LLM.
    args, _ = fake_llm.call_args
    messages = args[0]
    assert messages[0]["role"] == "system"
    # Memory turns survive the trip.
    roles = [m["role"] for m in messages]
    assert "user" in roles and "assistant" in roles
    # Screen context is embedded in the final user message.
    last = messages[-1]["content"]
    assert "conversations" in last
    assert "Math chat" in last


def test_chat_normalises_hallucinated_action_id(client):
    # Even if the LLM invents an action_id, the route must downgrade
    # the response to ``answer`` so the frontend never tries to call
    # an endpoint that doesn't exist.
    fake_llm = AsyncMock(return_value=json.dumps({
        "action": "run_action",
        "action_id": "nuke.everything_from_orbit",
        "params": {}, "confirm": "Wipe everything?",
        "response": "On it.",
    }))
    with patch("routes.admin_syra.call_llm_api_chat", fake_llm):
        r = client.post(
            "/api/admin/syra/chat",
            json={"transcript": "do the dangerous thing"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "answer"
    assert body["action_id"] is None


# ── Briefing endpoint ─────────────────────────────────────────────────────
def test_briefing_returns_text_paragraph(client):
    # Patch the fact gatherer so we don't depend on Mongo / Supabase.
    fake_facts = AsyncMock(return_value={
        "open_alerts": 3,
        "active_users_today": None,
        "new_signups_today": 12,
        "negative_feedback_24h": 2,
        "failed_jobs_24h": 1,
    })
    with patch("routes.admin_syra._gather_briefing_facts", fake_facts):
        r = client.get("/api/admin/syra/briefing")
    assert r.status_code == 200
    body = r.json()
    text = body["text"]
    assert isinstance(text, str) and len(text) > 20
    # The paragraph should mention the headline numbers.
    assert "3" in text  # open alerts
    assert "12" in text  # signups
    assert "1" in text   # failed jobs
    assert body["facts"]["open_alerts"] == 3


def test_briefing_handles_quiet_panel(client):
    fake_facts = AsyncMock(return_value={
        "open_alerts": 0, "active_users_today": None,
        "new_signups_today": 0, "negative_feedback_24h": 0,
        "failed_jobs_24h": 0,
    })
    with patch("routes.admin_syra._gather_briefing_facts", fake_facts):
        r = client.get("/api/admin/syra/briefing")
    assert r.status_code == 200
    text = r.json()["text"]
    # We never want a bare "Good morning ops." with no follow-up — the
    # quiet-panel branch adds a closing sentence.
    assert "quiet" in text.lower() or "no open alerts" in text.lower()
