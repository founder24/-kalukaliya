"""Tests for Task #414 — hydrate Slack card builder + per-category mute."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()
import metrics  # noqa: E402


def _failure_spike_snap():
    return {
        "metric": "hydrate_preload_failed_per_hour",
        "value": 50,
        "actual": 137,
        "top_kind": "chunk",
        "auto_reload_attempts": 12,
        "auto_reload_recoveries": 9,
    }


def _recovery_low_snap():
    return {
        "metric": "auto_reload_success_rate_pct",
        "value": 50.0,
        "actual": 16.7,
        "top_kind": "css",
        "auto_reload_attempts": 30,
        "auto_reload_recoveries": 5,
    }


# -------- _build_hydrate_slack_payload --------

def test_failure_spike_payload_has_block_kit_and_button():
    payload = metrics._build_hydrate_slack_payload(
        "hydrate_failure_spike",
        "Stale-build hydration failures spiked",
        "137 hydrate_preload_failed events in the last hour (threshold: 50).",
        _failure_spike_snap(),
    )
    assert payload["alert_type"] == "hydrate_failure_spike"
    assert payload["service"] == "syrabit-api"
    # Slack blocks present + dashboard button is the primary action.
    assert any(b.get("type") == "header" for b in payload["blocks"])
    actions = [b for b in payload["blocks"] if b.get("type") == "actions"]
    assert actions and actions[0]["elements"][0]["url"].startswith(
        "https://syrabit.ai/admin/dashboard"
    )
    # Text fallback (Discord/generic webhooks) carries the key facts.
    assert "137 events/hr" in payload["text"]
    assert "> 50/hr" in payload["text"]
    assert "Dashboard:" in payload["text"]


def test_recovery_low_payload_uses_percent_units_and_recovery_field():
    payload = metrics._build_hydrate_slack_payload(
        "hydrate_recovery_low",
        "Auto-reload recovery rate is low",
        "Auto-reload success rate is 16.7% (5/30) over the last hour.",
        _recovery_low_snap(),
    )
    assert "16.7%" in payload["text"]
    assert "< 50.0%" in payload["text"]
    # Recovery field included when both attempts + recoveries are present.
    fields_section = next(
        b for b in payload["blocks"] if b.get("type") == "section" and "fields" in b
    )
    field_texts = [f["text"] for f in fields_section["fields"]]
    assert any("Recovery" in t for t in field_texts)


def test_payload_handles_missing_optional_fields():
    payload = metrics._build_hydrate_slack_payload(
        "hydrate_failure_spike", "title", "body",
        {"metric": "x", "value": 1, "actual": 2},  # no top_kind / attempts
    )
    fields_section = next(
        b for b in payload["blocks"] if b.get("type") == "section" and "fields" in b
    )
    field_texts = [f["text"] for f in fields_section["fields"]]
    assert any("n/a" in t for t in field_texts)
    # No Recovery row when attempts/recoveries are absent.
    assert not any("Recovery" in t for t in field_texts)


# -------- _dispatch_alert routing --------

def _patch_dispatch_environment(*, webhook_url="https://hooks.slack.test/abc"):
    """Common patch set: stub Mongo, SES (Task #556 — sole path), push, and inject a webhook URL."""
    fake_db = MagicMock()
    fake_db.alerts.insert_one = AsyncMock()
    return [
        patch.object(metrics, "db", fake_db),
        patch.object(
            metrics, "_notification_channels",
            {"email": "", "webhook_url": webhook_url,
             "seo_slack_enabled": True, "hydrate_slack_enabled": True},
        ),
        patch.dict("os.environ", {"ALERT_EMAIL": ""}, clear=False),
    ]


def _run_dispatch(alert_type, snap):
    metrics._alert_last_fired.pop(alert_type, None)
    captured = {}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, json=None):
            captured["url"] = url
            captured["payload"] = json
            return MagicMock(status_code=200)

    with patch.object(metrics, "httpx", MagicMock(AsyncClient=_FakeClient)):
        asyncio.run(metrics._dispatch_alert(
            alert_type, "title", "body", threshold_snapshot=snap,
        ))
    return captured


def test_dispatch_routes_failure_spike_through_hydrate_builder():
    patches = _patch_dispatch_environment()
    for p in patches:
        p.start()
    try:
        captured = _run_dispatch("hydrate_failure_spike", _failure_spike_snap())
    finally:
        for p in patches:
            p.stop()
    assert captured["payload"]["alert_type"] == "hydrate_failure_spike"
    # The hydrate builder always emits Block Kit "blocks"; the generic
    # fallback payload does not.
    assert "blocks" in captured["payload"]
    assert "events/hr" in captured["payload"]["text"]


def test_dispatch_routes_recovery_low_through_hydrate_builder():
    patches = _patch_dispatch_environment()
    for p in patches:
        p.start()
    try:
        captured = _run_dispatch("hydrate_recovery_low", _recovery_low_snap())
    finally:
        for p in patches:
            p.stop()
    assert "blocks" in captured["payload"]
    assert "16.7%" in captured["payload"]["text"]


def test_hydrate_slack_enabled_false_mutes_webhook_only():
    """With hydrate_slack_enabled=False we should NOT POST to the webhook,
    but the persisted-alert write (db.alerts.insert_one) must still run."""
    fake_db = MagicMock()
    fake_db.alerts.insert_one = AsyncMock()
    posted = {"called": False}

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None):
            posted["called"] = True
            return MagicMock(status_code=200)

    metrics._alert_last_fired.pop("hydrate_failure_spike", None)
    with patch.object(metrics, "db", fake_db), \
         patch.object(metrics, "_notification_channels",
                      {"email": "", "webhook_url": "https://hooks.slack.test/x",
                       "seo_slack_enabled": True, "hydrate_slack_enabled": False}), \
         patch.object(metrics, "httpx", MagicMock(AsyncClient=_FakeClient)), \
         patch.dict("os.environ", {"ALERT_EMAIL": ""}, clear=False):
        asyncio.run(metrics._dispatch_alert(
            "hydrate_failure_spike", "title", "body",
            threshold_snapshot=_failure_spike_snap(),
        ))
    assert posted["called"] is False
    fake_db.alerts.insert_one.assert_awaited_once()


def test_seo_mute_does_not_affect_hydrate_routing():
    """hydrate_slack_enabled and seo_slack_enabled are independent."""
    fake_db = MagicMock()
    fake_db.alerts.insert_one = AsyncMock()
    posted = {"payload": None}

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None):
            posted["payload"] = json
            return MagicMock(status_code=200)

    metrics._alert_last_fired.pop("hydrate_failure_spike", None)
    with patch.object(metrics, "db", fake_db), \
         patch.object(metrics, "_notification_channels",
                      {"email": "", "webhook_url": "https://hooks.slack.test/x",
                       "seo_slack_enabled": False, "hydrate_slack_enabled": True}), \
         patch.object(metrics, "httpx", MagicMock(AsyncClient=_FakeClient)), \
         patch.dict("os.environ", {"ALERT_EMAIL": ""}, clear=False):
        asyncio.run(metrics._dispatch_alert(
            "hydrate_failure_spike", "title", "body",
            threshold_snapshot=_failure_spike_snap(),
        ))
    assert posted["payload"] is not None
    assert posted["payload"]["alert_type"] == "hydrate_failure_spike"


def test_hydrate_mute_does_not_suppress_email_or_push():
    """hydrate_slack_enabled=False must mute ONLY the webhook — SES
    email (Task #556 — sole path) and the browser-push fan-out must still run.
    """
    fake_db = MagicMock()
    fake_db.alerts.insert_one = AsyncMock()
    sent_email = {"called": False}
    pushed = {"called": False, "payload": None}

    def _fake_send_admin_email(**kwargs):
        sent_email["called"] = True
        sent_email["payload"] = kwargs
        return True

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None):
            raise AssertionError("webhook must be muted when hydrate_slack_enabled=False")

    async def _fake_push(payload):
        pushed["called"] = True
        pushed["payload"] = payload

    import sys, types
    fake_admin_notif = types.ModuleType("routes.admin_notifications")
    fake_admin_notif._dispatch_push_to_admins = _fake_push

    import email_templates as _et
    metrics._alert_last_fired.pop("hydrate_failure_spike", None)
    with patch.object(metrics, "db", fake_db), \
         patch.object(metrics, "_notification_channels",
                      {"email": "ops@syrabit.ai", "webhook_url": "https://hooks.slack.test/x",
                       "seo_slack_enabled": True, "hydrate_slack_enabled": False}), \
         patch.object(metrics, "httpx", MagicMock(AsyncClient=_FakeClient)), \
         patch.dict(sys.modules, {"routes.admin_notifications": fake_admin_notif}), \
         patch.object(_et, "send_admin_email", _fake_send_admin_email), \
         patch.dict("os.environ", {"ALERT_EMAIL": ""},
                    clear=False):
        asyncio.run(metrics._dispatch_alert(
            "hydrate_failure_spike", "title", "body",
            threshold_snapshot=_failure_spike_snap(),
        ))
        # Push fan-out is fire-and-forget via asyncio.create_task — let
        # the event loop run one tick so the task executes.
        async def _flush():
            await asyncio.sleep(0)
        asyncio.run(_flush())

    assert sent_email["called"] is True, "email must still be sent when only webhook is muted"
    assert pushed["called"] is True, "push must still fan out when only webhook is muted"
    fake_db.alerts.insert_one.assert_awaited_once()


def test_hydrate_mute_does_not_affect_seo_alert_routing():
    """Inverse independence: muting hydrate must not stop SEO alerts from
    posting their own Slack card.
    """
    fake_db = MagicMock()
    fake_db.alerts.insert_one = AsyncMock()
    posted = {"payload": None}

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None):
            posted["payload"] = json
            return MagicMock(status_code=200)

    metrics._alert_last_fired.pop("seo_health_degraded", None)
    with patch.object(metrics, "db", fake_db), \
         patch.object(metrics, "_notification_channels",
                      {"email": "", "webhook_url": "https://hooks.slack.test/x",
                       "seo_slack_enabled": True, "hydrate_slack_enabled": False}), \
         patch.object(metrics, "httpx", MagicMock(AsyncClient=_FakeClient)), \
         patch.dict("os.environ", {"ALERT_EMAIL": ""}, clear=False):
        asyncio.run(metrics._dispatch_alert(
            "seo_health_degraded", "title", "body",
            threshold_snapshot={"actual": "degraded"},
        ))
    assert posted["payload"] is not None
    assert posted["payload"]["alert_type"] == "seo_health_degraded"


# -------- _load_alert_settings persists the new toggle --------

def test_load_alert_settings_picks_up_hydrate_slack_enabled():
    fake_db = MagicMock()
    fake_db.api_config.find_one = AsyncMock(return_value={
        "alert_settings": {
            "notification_channels": {
                "email": "ops@syrabit.ai",
                "webhook_url": "",
                "seo_slack_enabled": True,
                "hydrate_slack_enabled": False,
            },
        },
    })
    with patch.object(metrics, "db", fake_db):
        asyncio.run(metrics._load_alert_settings())
    assert metrics._notification_channels["hydrate_slack_enabled"] is False
    # Default reasserts on a fresh load when the toggle isn't sent.
    fake_db.api_config.find_one = AsyncMock(return_value={
        "alert_settings": {"notification_channels": {"email": "x@y.z"}}
    })
    with patch.object(metrics, "db", fake_db):
        asyncio.run(metrics._load_alert_settings())
    assert metrics._notification_channels["hydrate_slack_enabled"] is True
