"""Tests for Task #660 — admin-configurable recipient list for the
weekly review-prompt digest.

Covers:
  * ``_resolve_review_prompt_digest_recipients`` resolution order
    (override → digest list → legacy email → ALERT_EMAIL env)
  * dedup + invalid-entry filtering
  * ``_send_review_prompt_weekly_digest_email`` returns ``recipients``
    and skips with ``no_admin_email`` when nothing resolves
  * ``metrics._load_alert_settings`` parses both list and
    comma-separated string forms of ``review_prompt_digest_emails``
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()
from routes import admin_review_prompts as arp  # noqa: E402
import metrics as _m  # noqa: E402


# ── _resolve_review_prompt_digest_recipients ────────────────────────────────

def _with_channels(channels: dict):
    saved = dict(_m._notification_channels)
    _m._notification_channels.clear()
    _m._notification_channels.update(channels)
    return saved


def _restore_channels(saved: dict):
    _m._notification_channels.clear()
    _m._notification_channels.update(saved)


def test_resolve_uses_dedicated_digest_list_first(monkeypatch):
    monkeypatch.delenv("ALERT_EMAIL", raising=False)
    saved = _with_channels({
        "email": "fallback@example.com",
        "review_prompt_digest_emails": ["ops@example.com", "growth@example.com"],
    })
    try:
        out = arp._resolve_review_prompt_digest_recipients()
    finally:
        _restore_channels(saved)
    assert out == ["ops@example.com", "growth@example.com"]


def test_resolve_falls_back_to_legacy_email_when_digest_list_empty(monkeypatch):
    monkeypatch.delenv("ALERT_EMAIL", raising=False)
    saved = _with_channels({
        "email": "fallback@example.com",
        "review_prompt_digest_emails": [],
    })
    try:
        out = arp._resolve_review_prompt_digest_recipients()
    finally:
        _restore_channels(saved)
    assert out == ["fallback@example.com"]


def test_resolve_falls_back_to_alert_email_env(monkeypatch):
    monkeypatch.setenv("ALERT_EMAIL", "env@example.com")
    saved = _with_channels({"email": "", "review_prompt_digest_emails": []})
    try:
        out = arp._resolve_review_prompt_digest_recipients()
    finally:
        _restore_channels(saved)
    assert out == ["env@example.com"]


def test_resolve_override_wins_over_persisted(monkeypatch):
    monkeypatch.delenv("ALERT_EMAIL", raising=False)
    saved = _with_channels({
        "email": "fallback@example.com",
        "review_prompt_digest_emails": ["persisted@example.com"],
    })
    try:
        out = arp._resolve_review_prompt_digest_recipients(
            ["a@x.com", "b@y.com"],
        )
    finally:
        _restore_channels(saved)
    assert out == ["a@x.com", "b@y.com"]


def test_resolve_override_accepts_comma_separated_string(monkeypatch):
    monkeypatch.delenv("ALERT_EMAIL", raising=False)
    saved = _with_channels({"email": "", "review_prompt_digest_emails": []})
    try:
        out = arp._resolve_review_prompt_digest_recipients(
            "a@x.com, , b@y.com",
        )
    finally:
        _restore_channels(saved)
    assert out == ["a@x.com", "b@y.com"]


def test_resolve_drops_bogus_entries_and_dedupes(monkeypatch):
    monkeypatch.delenv("ALERT_EMAIL", raising=False)
    saved = _with_channels({
        "email": "",
        "review_prompt_digest_emails": [
            "ops@example.com",
            "OPS@example.com",   # case-insensitive dupe
            "not-an-email",       # missing @ — dropped
            "  ",                 # blank — dropped
            "growth@example.com",
        ],
    })
    try:
        out = arp._resolve_review_prompt_digest_recipients()
    finally:
        _restore_channels(saved)
    assert out == ["ops@example.com", "growth@example.com"]


def test_resolve_returns_empty_list_when_nothing_configured(monkeypatch):
    monkeypatch.delenv("ALERT_EMAIL", raising=False)
    saved = _with_channels({"email": "", "review_prompt_digest_emails": []})
    try:
        out = arp._resolve_review_prompt_digest_recipients()
    finally:
        _restore_channels(saved)
    assert out == []


# ── _send_review_prompt_weekly_digest_email ─────────────────────────────────

def test_send_returns_recipients_field_when_no_admin_email(monkeypatch):
    monkeypatch.delenv("ALERT_EMAIL", raising=False)
    saved = _with_channels({"email": "", "review_prompt_digest_emails": []})
    try:
        result = asyncio.run(
            arp._send_review_prompt_weekly_digest_email(
                {"iso_week": "2026-W17", "shown": 1, "clicked": 0,
                 "dismissed": 0, "ctr_pct": None},
            )
        )
    finally:
        _restore_channels(saved)
    assert result["sent"] is False
    assert result["reason"] == "no_admin_email"
    assert result["recipients"] == []


def test_send_uses_digest_list_and_passes_all_to_sendgrid(monkeypatch):
    monkeypatch.delenv("ALERT_EMAIL", raising=False)
    saved = _with_channels({
        "email": "fallback@example.com",
        "review_prompt_digest_emails": ["ops@example.com", "growth@example.com"],
    })

    captured: dict = {}

    def _fake_send_admin_email(**kwargs):
        captured["payload"] = kwargs
        return True

    import email_templates as _et

    try:
        with patch.object(_et, "send_admin_email", _fake_send_admin_email), \
             patch.object(_m, "_load_alert_settings", AsyncMock()):
            result = asyncio.run(
                arp._send_review_prompt_weekly_digest_email(
                    {"iso_week": "2026-W17", "shown": 100, "clicked": 5,
                     "dismissed": 10, "ctr_pct": 5.0},
                )
            )
    finally:
        _restore_channels(saved)

    assert result["sent"] is True
    assert result["recipients"] == ["ops@example.com", "growth@example.com"]
    assert result["to"] == "ops@example.com"
    assert captured["payload"]["to"] == ["ops@example.com", "growth@example.com"]
    assert "Syrabit review-prompt weekly" in captured["payload"]["subject"]


def test_send_override_to_targets_explicit_recipients(monkeypatch):
    """The admin "send me a test now" path posts the draft list as
    ``to`` so admins can validate a recipient before persisting it."""
    monkeypatch.delenv("ALERT_EMAIL", raising=False)
    saved = _with_channels({
        "email": "fallback@example.com",
        "review_prompt_digest_emails": ["persisted@example.com"],
    })

    captured: dict = {}

    def _fake_send_admin_email(**kwargs):
        captured["payload"] = kwargs
        return True

    import email_templates as _et

    try:
        with patch.object(_et, "send_admin_email", _fake_send_admin_email), \
             patch.object(_m, "_load_alert_settings", AsyncMock()):
            result = asyncio.run(
                arp._send_review_prompt_weekly_digest_email(
                    {"iso_week": "2026-W17", "shown": 1, "clicked": 0,
                     "dismissed": 0, "ctr_pct": None},
                    to=["draft@example.com"],
                )
            )
    finally:
        _restore_channels(saved)

    assert result["sent"] is True
    assert captured["payload"]["to"] == ["draft@example.com"]
    assert "persisted@example.com" not in captured["payload"]["to"]


# ── metrics._load_alert_settings parsing ────────────────────────────────────

def test_load_alert_settings_parses_digest_list(monkeypatch):
    """Persisted list form survives a load round-trip with dedup and
    invalid-entry filtering."""
    cfg = {
        "alert_settings": {
            "thresholds": {},
            "expiration": {},
            "notification_channels": {
                "review_prompt_digest_emails": [
                    "ops@example.com",
                    "OPS@example.com",
                    "  ",
                    "not-an-email",
                    "growth@example.com",
                ],
            },
        },
    }
    fake_db = MagicMock()
    fake_db.api_config.find_one = AsyncMock(return_value=cfg)
    saved_channels = dict(_m._notification_channels)
    try:
        with patch.object(_m, "db", fake_db):
            asyncio.run(_m._load_alert_settings())
        assert _m._notification_channels["review_prompt_digest_emails"] == [
            "ops@example.com", "growth@example.com",
        ]
    finally:
        _restore_channels(saved_channels)


def test_load_alert_settings_parses_comma_separated_string(monkeypatch):
    cfg = {
        "alert_settings": {
            "thresholds": {},
            "expiration": {},
            "notification_channels": {
                "review_prompt_digest_emails": "a@x.com, b@y.com , a@x.com",
            },
        },
    }
    fake_db = MagicMock()
    fake_db.api_config.find_one = AsyncMock(return_value=cfg)
    saved_channels = dict(_m._notification_channels)
    try:
        with patch.object(_m, "db", fake_db):
            asyncio.run(_m._load_alert_settings())
        assert _m._notification_channels["review_prompt_digest_emails"] == [
            "a@x.com", "b@y.com",
        ]
    finally:
        _restore_channels(saved_channels)
