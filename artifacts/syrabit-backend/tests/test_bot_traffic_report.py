"""Tests for the weekly bot-traffic report (Task #314)."""
import asyncio
import sys
import types
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()
from routes import bot_traffic_report  # noqa: E402


# ── _compose_bot_traffic_report ────────────────────────────────────────────

def test_compose_with_empty_inputs_returns_zeroed_rows():
    stats = bot_traffic_report._compose_bot_traffic_report({}, {})
    assert stats["bot_total_current"] == 0
    assert stats["bot_total_prior"] == 0
    assert stats["bot_total_delta"] == 0
    # Always-show categories are present even with no data
    names = {r["category"] for r in stats["categories"]}
    assert "Search Engine Crawler" in names
    assert "AI Crawler" in names
    assert stats["highlights"] == []
    assert "iso_week" in stats


def test_compose_computes_deltas_and_pct():
    current = {
        "by_category": {"Search Engine Crawler": 280, "AI Crawler": 90},
        "bot_total": 612, "bot_5xx": 4, "source": "cloudflare",
    }
    prior = {
        "by_category": {"Search Engine Crawler": 219, "AI Crawler": 100},
        "bot_total": 500, "bot_5xx": 2, "source": "cloudflare",
    }
    stats = bot_traffic_report._compose_bot_traffic_report(current, prior)
    row_by_name = {r["category"]: r for r in stats["categories"]}
    assert row_by_name["Search Engine Crawler"]["current"] == 280
    assert row_by_name["Search Engine Crawler"]["prior"] == 219
    assert row_by_name["Search Engine Crawler"]["delta"] == 61
    assert row_by_name["Search Engine Crawler"]["delta_pct"] == 27.9
    assert row_by_name["AI Crawler"]["delta_pct"] == -10.0
    assert stats["bot_total_current"] == 612
    assert stats["bot_total_delta"] == 112
    assert stats["bot_total_delta_pct"] == 22.4
    assert stats["bot_5xx_delta"] == 2


def test_compose_highlights_biggest_movers_first():
    current = {"by_category": {"Search Engine Crawler": 280, "AI Crawler": 30,
                               "Monitoring & Analytics": 100}, "bot_total": 410,
               "bot_5xx": 0, "source": "cloudflare"}
    prior = {"by_category": {"Search Engine Crawler": 219, "AI Crawler": 200,
                             "Monitoring & Analytics": 95}, "bot_total": 514,
             "bot_5xx": 0, "source": "cloudflare"}
    stats = bot_traffic_report._compose_bot_traffic_report(current, prior)
    # AI Crawler dropped by 170 — biggest mover — must be first highlight.
    assert stats["highlights"][0]["category"] == "AI Crawler"
    assert stats["highlights"][0]["delta"] == -170


def test_compose_handles_prior_zero_as_new_category():
    current = {"by_category": {"AI Crawler": 50}, "bot_total": 50, "bot_5xx": 0,
               "source": "cloudflare"}
    prior = {"by_category": {}, "bot_total": 0, "bot_5xx": 0, "source": "cloudflare"}
    stats = bot_traffic_report._compose_bot_traffic_report(current, prior)
    ai = next(r for r in stats["categories"] if r["category"] == "AI Crawler")
    assert ai["current"] == 50
    assert ai["prior"] == 0
    # delta_pct is None when prior is zero — UI renders "new"
    assert ai["delta_pct"] is None


# ── _format_bot_traffic_report_html ────────────────────────────────────────

def test_format_html_includes_key_fields_and_delta_line():
    current = {"by_category": {"Search Engine Crawler": 280}, "bot_total": 280,
               "bot_5xx": 3, "source": "cloudflare"}
    prior = {"by_category": {"Search Engine Crawler": 219}, "bot_total": 219,
             "bot_5xx": 1, "source": "cloudflare"}
    stats = bot_traffic_report._compose_bot_traffic_report(current, prior)
    html = bot_traffic_report._format_bot_traffic_report_html(stats)
    assert "Weekly bot traffic report" in html
    assert "syrabit.ai" in html
    # Biggest-movers highlight surfaces at the top
    assert "Biggest movers this week" in html
    # Week-over-week delta for Search Engine Crawler is present and correct
    assert "219 → 280" in html
    assert "+27.9%" in html
    # 5xx surfaced
    assert "5xx to bots" in html
    # Dashboard CTA
    assert "Open SEO Manager dashboard" in html


def test_format_html_renders_when_no_movement():
    stats = bot_traffic_report._compose_bot_traffic_report({}, {})
    html = bot_traffic_report._format_bot_traffic_report_html(stats)
    assert "No category movements this week." in html


# ── Scheduler gate ─────────────────────────────────────────────────────────

def test_scheduler_fires_monday_0400_utc():
    on_time = datetime(2026, 4, 20, 4, 0, tzinfo=timezone.utc)  # Monday
    assert bot_traffic_report._should_send_bot_report_now(on_time, "")
    near = datetime(2026, 4, 20, 3, 50, tzinfo=timezone.utc)
    assert bot_traffic_report._should_send_bot_report_now(near, "")
    far = datetime(2026, 4, 20, 4, 30, tzinfo=timezone.utc)
    assert not bot_traffic_report._should_send_bot_report_now(far, "")


def test_scheduler_skips_other_weekdays():
    tue = datetime(2026, 4, 21, 4, 0, tzinfo=timezone.utc)
    assert not bot_traffic_report._should_send_bot_report_now(tue, "")


def test_scheduler_dedups_same_iso_week():
    on_time = datetime(2026, 4, 20, 4, 0, tzinfo=timezone.utc)
    cur = bot_traffic_report._iso_week_tag(on_time)
    assert not bot_traffic_report._should_send_bot_report_now(on_time, cur)
    assert bot_traffic_report._should_send_bot_report_now(on_time, "2025-W52")


# ── _claim_weekly_bot_report_slot ──────────────────────────────────────────

def test_claim_succeeds_when_marker_stale():
    job_locks = MagicMock()
    job_locks.find_one_and_update = AsyncMock(return_value={
        "_id": bot_traffic_report._BOT_REPORT_LOCK_ID,
        bot_traffic_report._BOT_REPORT_API_CONFIG_KEY: "2026-W15",
    })
    job_locks.insert_one = AsyncMock()
    fake_db = MagicMock(job_locks=job_locks)
    assert asyncio.run(bot_traffic_report._claim_weekly_bot_report_slot(fake_db, "2026-W16"))
    job_locks.insert_one.assert_not_awaited()


def test_claim_fails_when_marker_current_and_insert_dup():
    from pymongo.errors import DuplicateKeyError
    job_locks = MagicMock()
    job_locks.find_one_and_update = AsyncMock(return_value=None)
    job_locks.insert_one = AsyncMock(side_effect=DuplicateKeyError("dup"))
    fake_db = MagicMock(job_locks=job_locks)
    assert not asyncio.run(bot_traffic_report._claim_weekly_bot_report_slot(fake_db, "2026-W16"))


def test_claim_bootstraps_when_doc_missing():
    job_locks = MagicMock()
    job_locks.find_one_and_update = AsyncMock(return_value=None)
    job_locks.insert_one = AsyncMock(return_value=None)
    fake_db = MagicMock(job_locks=job_locks)
    assert asyncio.run(bot_traffic_report._claim_weekly_bot_report_slot(fake_db, "2026-W16"))
    inserted = job_locks.insert_one.await_args.args[0]
    assert inserted["_id"] == bot_traffic_report._BOT_REPORT_LOCK_ID


# ── _try_send_weekly_bot_report_once ───────────────────────────────────────

def test_try_send_skips_when_already_sent_this_week():
    on_time = datetime(2026, 4, 20, 4, 0, tzinfo=timezone.utc)
    cur_week = bot_traffic_report._iso_week_tag(on_time)
    job_locks = MagicMock()
    job_locks.find_one = AsyncMock(return_value={
        bot_traffic_report._BOT_REPORT_API_CONFIG_KEY: cur_week,
    })
    job_locks.find_one_and_update = AsyncMock()
    job_locks.insert_one = AsyncMock()
    fake_db = MagicMock(job_locks=job_locks)
    with patch.object(bot_traffic_report, "_send_bot_traffic_report_email", AsyncMock()) as snd:
        out = asyncio.run(bot_traffic_report._try_send_weekly_bot_report_once(fake_db, on_time))
    assert out["claimed"] is False
    assert out["sent"] is False
    snd.assert_not_awaited()


def test_try_send_fires_fallback_alert_when_cf_fails():
    """When CF API returns nothing, we must dispatch a fallback alert AND
    roll the marker back so the next poll inside the window retries."""
    on_time = datetime(2026, 4, 20, 4, 0, tzinfo=timezone.utc)
    job_locks = MagicMock()
    job_locks.find_one = AsyncMock(return_value={
        bot_traffic_report._BOT_REPORT_API_CONFIG_KEY: "2025-W52",
    })
    job_locks.find_one_and_update = AsyncMock(return_value={
        "_id": bot_traffic_report._BOT_REPORT_LOCK_ID,
        bot_traffic_report._BOT_REPORT_API_CONFIG_KEY: "2025-W52",
    })
    job_locks.insert_one = AsyncMock()
    job_locks.update_one = AsyncMock()
    fake_db = MagicMock(job_locks=job_locks)

    with patch.object(bot_traffic_report, "_gather_bot_traffic_report_inputs",
                      AsyncMock(return_value={"_error": "cf_api_returned_none"})), \
         patch.object(bot_traffic_report, "_send_bot_traffic_report_email",
                      AsyncMock()) as snd, \
         patch.object(bot_traffic_report, "_dispatch_bot_report_failure_alert",
                      AsyncMock()) as alert:
        out = asyncio.run(bot_traffic_report._try_send_weekly_bot_report_once(fake_db, on_time))

    assert out["claimed"] is True
    assert out["sent"] is False
    assert out["reason"] == "cf_api_returned_none"
    alert.assert_awaited_once()
    snd.assert_not_awaited()
    # Marker rolled back
    job_locks.update_one.assert_awaited()


def test_try_send_happy_path_sends_email():
    on_time = datetime(2026, 4, 20, 4, 0, tzinfo=timezone.utc)
    job_locks = MagicMock()
    job_locks.find_one = AsyncMock(return_value={
        bot_traffic_report._BOT_REPORT_API_CONFIG_KEY: "2025-W52",
    })
    job_locks.find_one_and_update = AsyncMock(return_value={
        "_id": bot_traffic_report._BOT_REPORT_LOCK_ID,
        bot_traffic_report._BOT_REPORT_API_CONFIG_KEY: "2025-W52",
    })
    job_locks.update_one = AsyncMock()
    fake_db = MagicMock(job_locks=job_locks)
    fake_stats = bot_traffic_report._compose_bot_traffic_report(
        {"by_category": {"Search Engine Crawler": 10}, "bot_total": 10, "bot_5xx": 0,
         "source": "cloudflare"},
        {"by_category": {"Search Engine Crawler": 5}, "bot_total": 5, "bot_5xx": 0,
         "source": "cloudflare"},
    )

    with patch.object(bot_traffic_report, "_gather_bot_traffic_report_inputs",
                      AsyncMock(return_value=fake_stats)), \
         patch.object(bot_traffic_report, "_send_bot_traffic_report_email",
                      AsyncMock(return_value={"sent": True, "to": "admin@syrabit.ai"})) as snd, \
         patch.object(bot_traffic_report, "_dispatch_bot_report_failure_alert",
                      AsyncMock()) as alert:
        out = asyncio.run(bot_traffic_report._try_send_weekly_bot_report_once(fake_db, on_time))

    assert out["claimed"] is True
    assert out["sent"] is True
    snd.assert_awaited_once()
    alert.assert_not_awaited()


# ── _send_bot_traffic_report_email ─────────────────────────────────────────

def test_send_skipped_when_no_admin_email():
    metrics_stub = types.SimpleNamespace(
        _notification_channels={"email": ""},
        _load_alert_settings=AsyncMock(),
    )
    fake_stats = bot_traffic_report._compose_bot_traffic_report(
        {"by_category": {}, "bot_total": 1, "bot_5xx": 0, "source": "cloudflare"},
        {"by_category": {}, "bot_total": 0, "bot_5xx": 0, "source": "cloudflare"},
    )
    with patch.dict(sys.modules, {"metrics": metrics_stub}), \
         patch.dict("os.environ", {"ALERT_EMAIL": "", "SENDGRID_API_KEY": ""}, clear=False):
        result = asyncio.run(bot_traffic_report._send_bot_traffic_report_email(fake_stats))
    assert result["sent"] is False
    assert result["reason"] == "no_admin_email"


def test_send_uses_sendgrid_when_email_and_key_present():
    metrics_stub = types.SimpleNamespace(
        _notification_channels={"email": "admin@syrabit.ai"},
        _load_alert_settings=AsyncMock(),
    )
    sent_calls = []

    def _fake_send_admin_email(**kwargs):
        sent_calls.append(kwargs)
        return True

    import email_templates as _et
    fake_stats = bot_traffic_report._compose_bot_traffic_report(
        {"by_category": {"Search Engine Crawler": 280}, "bot_total": 280, "bot_5xx": 0,
         "source": "cloudflare"},
        {"by_category": {"Search Engine Crawler": 219}, "bot_total": 219, "bot_5xx": 0,
         "source": "cloudflare"},
    )
    with patch.dict(sys.modules, {"metrics": metrics_stub}), \
         patch.object(_et, "send_admin_email", _fake_send_admin_email), \
         patch.dict("os.environ", {"SENDGRID_API_KEY": "test-key"}, clear=False):
        result = asyncio.run(bot_traffic_report._send_bot_traffic_report_email(fake_stats))
    assert result["sent"] is True
    assert sent_calls
    call = sent_calls[0]
    assert call["to"] == ["admin@syrabit.ai"]
    assert "bot traffic weekly report" in call["subject"].lower()
    assert "219 → 280" in call["html"]


def test_send_refuses_when_stats_has_error():
    result = asyncio.run(bot_traffic_report._send_bot_traffic_report_email(
        {"_error": "cf_api_returned_none"}
    ))
    assert result["sent"] is False
    assert result["reason"] == "cf_api_returned_none"


# ── _gather_bot_traffic_report_inputs ──────────────────────────────────────

def test_gather_returns_error_when_cf_not_configured():
    cf_stub = types.ModuleType("cloudflare_client")
    cf_stub.is_configured = lambda: False
    async def _fake_get(*_a, **_k):
        return None
    cf_stub.get_verified_bot_traffic_cf = _fake_get
    with patch.dict(sys.modules, {"cloudflare_client": cf_stub}):
        stats = asyncio.run(bot_traffic_report._gather_bot_traffic_report_inputs())
    assert stats["_error"] == "cloudflare_not_configured"


def test_gather_returns_error_when_cf_returns_none():
    cf_stub = types.ModuleType("cloudflare_client")
    cf_stub.is_configured = lambda: True
    async def _fake_get(*_a, **_k):
        return None
    cf_stub.get_verified_bot_traffic_cf = _fake_get
    with patch.dict(sys.modules, {"cloudflare_client": cf_stub}):
        stats = asyncio.run(bot_traffic_report._gather_bot_traffic_report_inputs())
    assert stats["_error"] == "cf_api_returned_none"


def test_gather_returns_error_when_prior_window_fails():
    """Current window succeeds but prior-window CF call returns None →
    we cannot compute week-over-week deltas, so we must emit a distinct
    error so _try_send_weekly_bot_report_once fires the fallback alert
    instead of silently sending a report with misleading deltas."""
    cf_stub = types.ModuleType("cloudflare_client")
    cf_stub.is_configured = lambda: True
    payloads = iter([
        {"by_category": {"Search Engine Crawler": 280}, "bot_total": 280,
         "bot_5xx": 0, "source": "cloudflare"},
        None,  # prior window failed
    ])
    async def _fake_get(*_a, **_k):
        return next(payloads)
    cf_stub.get_verified_bot_traffic_cf = _fake_get
    with patch.dict(sys.modules, {"cloudflare_client": cf_stub}):
        stats = asyncio.run(bot_traffic_report._gather_bot_traffic_report_inputs())
    assert stats["_error"] == "cf_api_prior_window_failed"


def test_gather_returns_error_when_current_window_fails():
    cf_stub = types.ModuleType("cloudflare_client")
    cf_stub.is_configured = lambda: True
    payloads = iter([
        None,  # current window failed
        {"by_category": {"Search Engine Crawler": 219}, "bot_total": 219,
         "bot_5xx": 0, "source": "cloudflare"},
    ])
    async def _fake_get(*_a, **_k):
        return next(payloads)
    cf_stub.get_verified_bot_traffic_cf = _fake_get
    with patch.dict(sys.modules, {"cloudflare_client": cf_stub}):
        stats = asyncio.run(bot_traffic_report._gather_bot_traffic_report_inputs())
    assert stats["_error"] == "cf_api_current_window_failed"


def test_try_send_fires_fallback_when_prior_window_fails():
    """Integration: prior-window failure must dispatch the fallback alert
    and NOT send a misleading report email."""
    on_time = datetime(2026, 4, 20, 4, 0, tzinfo=timezone.utc)
    job_locks = MagicMock()
    job_locks.find_one = AsyncMock(return_value={
        bot_traffic_report._BOT_REPORT_API_CONFIG_KEY: "2025-W52",
    })
    job_locks.find_one_and_update = AsyncMock(return_value={
        "_id": bot_traffic_report._BOT_REPORT_LOCK_ID,
        bot_traffic_report._BOT_REPORT_API_CONFIG_KEY: "2025-W52",
    })
    job_locks.update_one = AsyncMock()
    fake_db = MagicMock(job_locks=job_locks)

    with patch.object(bot_traffic_report, "_gather_bot_traffic_report_inputs",
                      AsyncMock(return_value={"_error": "cf_api_prior_window_failed"})), \
         patch.object(bot_traffic_report, "_send_bot_traffic_report_email",
                      AsyncMock()) as snd, \
         patch.object(bot_traffic_report, "_dispatch_bot_report_failure_alert",
                      AsyncMock()) as alert:
        out = asyncio.run(bot_traffic_report._try_send_weekly_bot_report_once(fake_db, on_time))

    assert out["claimed"] is True
    assert out["sent"] is False
    assert out["reason"] == "cf_api_prior_window_failed"
    alert.assert_awaited_once()
    snd.assert_not_awaited()
    job_locks.update_one.assert_awaited()


def test_gather_composes_when_cf_returns_data():
    cf_stub = types.ModuleType("cloudflare_client")
    cf_stub.is_configured = lambda: True
    payloads = iter([
        {"by_category": {"Search Engine Crawler": 280}, "bot_total": 280, "bot_5xx": 2,
         "source": "cloudflare"},
        {"by_category": {"Search Engine Crawler": 219}, "bot_total": 219, "bot_5xx": 1,
         "source": "cloudflare"},
    ])
    async def _fake_get(*_a, **_k):
        return next(payloads)
    cf_stub.get_verified_bot_traffic_cf = _fake_get
    with patch.dict(sys.modules, {"cloudflare_client": cf_stub}):
        stats = asyncio.run(bot_traffic_report._gather_bot_traffic_report_inputs())
    assert "_error" not in stats
    assert stats["bot_total_current"] == 280
    assert stats["bot_total_prior"] == 219
    assert stats["bot_total_delta"] == 61
