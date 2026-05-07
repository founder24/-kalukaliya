"""Tests for the weekly SEO health digest (Task #293)."""
import asyncio
import sys
import types
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()
from routes import bot_discovery  # noqa: E402


def _snap(status: str, *, hours_ago: int, valid_sm=9, total_sm=9, ok_url=30, total_url=30, rate=100.0):
    return {
        "status": status,
        "checked_at": (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(),
        "recorded_at": datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        "summary": {
            "total_sitemaps": total_sm, "valid_sitemaps": valid_sm,
            "total_url_checks": total_url, "ok_url_checks": ok_url,
            "url_check_success_rate": rate,
        },
    }


# ── _compose_seo_weekly_digest ──────────────────────────────────────────────

def test_compose_empty_history_returns_zeroed_stats():
    stats = bot_discovery._compose_seo_weekly_digest([])
    assert stats["total_snapshots"] == 0
    assert stats["uptime_pct"] == 0.0
    assert stats["status_counts"] == {"ok": 0, "degraded": 0, "critical": 0, "unknown": 0}
    assert stats["latest_status"] == "unknown"
    assert "iso_week" in stats and stats["iso_week"].startswith(str(datetime.now(timezone.utc).year))


def test_compose_aggregates_status_counts_and_uptime():
    history = (
        [_snap("ok", hours_ago=h) for h in (1, 2, 3, 4, 5, 6, 7, 8)]
        + [_snap("degraded", hours_ago=h, valid_sm=7, ok_url=24, rate=80.0) for h in (9, 10)]
    )
    stats = bot_discovery._compose_seo_weekly_digest(history, recent_alerts=2)
    assert stats["total_snapshots"] == 10
    assert stats["status_counts"]["ok"] == 8
    assert stats["status_counts"]["degraded"] == 2
    # 8 healthy / 10 total → 80.0%
    assert stats["uptime_pct"] == 80.0
    assert stats["recent_alerts"] == 2
    # avg url success: (100*8 + 80*2)/10 = 96.0
    assert stats["avg_url_success_rate"] == 96.0
    assert stats["worst_status_in_window"] == "degraded"


def test_compose_drops_snapshots_outside_7_day_window():
    history = [
        _snap("ok", hours_ago=1),
        _snap("critical", hours_ago=24 * 30),  # 30 days ago — must be excluded
    ]
    stats = bot_discovery._compose_seo_weekly_digest(history)
    assert stats["total_snapshots"] == 1
    assert stats["status_counts"]["critical"] == 0
    assert stats["latest_status"] == "ok"


def test_compose_handles_string_recorded_at():
    h = _snap("ok", hours_ago=2)
    h["recorded_at"] = h["recorded_at"].isoformat()
    stats = bot_discovery._compose_seo_weekly_digest([h])
    assert stats["total_snapshots"] == 1


# ── _format_seo_weekly_digest_html ──────────────────────────────────────────

def test_format_html_includes_key_fields():
    stats = bot_discovery._compose_seo_weekly_digest(
        [_snap("ok", hours_ago=h) for h in range(1, 6)],
        recent_alerts=3,
    )
    html = bot_discovery._format_seo_weekly_digest_html(stats)
    assert "SEO weekly digest" in html
    assert "100.0%" in html             # uptime
    assert "OK: 5" in html              # status breakdown
    assert "Open SEO Manager" in html
    assert "syrabit.ai/admin/seo" in html
    # alerts count surfaces
    assert ">3</b>" in html or ">3</td>" in html or "<b>3</b>" in html


# ── _send_seo_weekly_digest_email ───────────────────────────────────────────

def test_send_skipped_when_no_admin_email():
    metrics_stub = types.SimpleNamespace(
        _notification_channels={"email": ""},
        _load_alert_settings=AsyncMock(),
    )
    fake_stats = bot_discovery._compose_seo_weekly_digest([_snap("ok", hours_ago=1)])
    with patch.dict(sys.modules, {"metrics": metrics_stub}), \
         patch.dict("os.environ", {"ALERT_EMAIL": ""}, clear=False):
        result = asyncio.run(bot_discovery._send_seo_weekly_digest_email(fake_stats))
    assert result["sent"] is False
    assert result["reason"] == "no_admin_email"


def test_send_uses_ses_when_email_and_aws_creds_present():
    """Task #556 — SES is the sole transactional path; happy path must
    invoke ``send_admin_email`` once with the right payload."""
    metrics_stub = types.SimpleNamespace(
        _notification_channels={"email": "admin@syrabit.ai"},
        _load_alert_settings=AsyncMock(),
    )
    sent_calls = []

    def _fake_send_admin_email(**kwargs):
        sent_calls.append(kwargs)
        return True

    import email_templates as _et
    fake_stats = bot_discovery._compose_seo_weekly_digest(
        [_snap("ok", hours_ago=h) for h in (1, 2, 3)]
    )
    with patch.dict(sys.modules, {"metrics": metrics_stub}), \
         patch.object(_et, "send_admin_email", _fake_send_admin_email), \
         patch.dict("os.environ",
                    {"AWS_ACCESS_KEY_ID": "AKIA_TEST",
                     "AWS_SECRET_ACCESS_KEY": "secret_test"},
                    clear=False):
        result = asyncio.run(bot_discovery._send_seo_weekly_digest_email(fake_stats))
    assert result["sent"] is True
    assert result["to"] == "admin@syrabit.ai"
    assert sent_calls, "send_admin_email should have been called once"
    call = sent_calls[0]
    assert call["to"] == ["admin@syrabit.ai"]
    assert "weekly digest" in call["subject"].lower()
    assert "SEO weekly digest" in call["html"]


def test_send_skipped_with_no_aws_creds_reason():
    """Task #556 — when AWS credentials are absent, the digest sender
    must short-circuit to ``no_aws_creds`` BEFORE attempting any SES
    call (V4 §12 — no silent fallbacks)."""
    metrics_stub = types.SimpleNamespace(
        _notification_channels={"email": "admin@syrabit.ai"},
        _load_alert_settings=AsyncMock(),
    )
    invoked = []

    def _should_not_be_called(**kwargs):
        invoked.append(kwargs)
        return True

    import email_templates as _et
    fake_stats = bot_discovery._compose_seo_weekly_digest(
        [_snap("ok", hours_ago=h) for h in (1, 2, 3)]
    )
    with patch.dict(sys.modules, {"metrics": metrics_stub}), \
         patch.object(_et, "send_admin_email", _should_not_be_called), \
         patch.dict("os.environ",
                    {"AWS_ACCESS_KEY_ID": "", "AWS_SECRET_ACCESS_KEY": ""},
                    clear=False):
        result = asyncio.run(bot_discovery._send_seo_weekly_digest_email(fake_stats))
    assert result["sent"] is False
    assert result["reason"] == "no_aws_creds"
    assert result["to"] == "admin@syrabit.ai"
    assert not invoked, "send_admin_email must not be called without AWS creds"


def test_send_returns_ses_non_2xx_reason_on_provider_failure():
    """Task #556 — when ``send_admin_email`` returns False (the SES
    helper recorded a non-2xx), the digest sender must surface
    ``send_error:ses_non_2xx`` (NOT the legacy sendgrid_non_2xx)."""
    metrics_stub = types.SimpleNamespace(
        _notification_channels={"email": "admin@syrabit.ai"},
        _load_alert_settings=AsyncMock(),
    )

    def _fake_send_admin_email(**kwargs):
        return False  # SES recorded a non-2xx

    import email_templates as _et
    fake_stats = bot_discovery._compose_seo_weekly_digest(
        [_snap("ok", hours_ago=h) for h in (1, 2, 3)]
    )
    with patch.dict(sys.modules, {"metrics": metrics_stub}), \
         patch.object(_et, "send_admin_email", _fake_send_admin_email), \
         patch.dict("os.environ",
                    {"AWS_ACCESS_KEY_ID": "AKIA_TEST",
                     "AWS_SECRET_ACCESS_KEY": "secret_test"},
                    clear=False):
        result = asyncio.run(bot_discovery._send_seo_weekly_digest_email(fake_stats))
    assert result["sent"] is False
    assert result["reason"] == "send_error:ses_non_2xx"


# ── _gather_weekly_digest_inputs ────────────────────────────────────────────

def test_gather_inputs_returns_empty_when_mongo_unavailable():
    with patch("deps.is_mongo_available", AsyncMock(return_value=False)):
        result = asyncio.run(bot_discovery._gather_weekly_digest_inputs())
    assert result == {}


# ── _compose: valid-sitemap trend ──────────────────────────────────────────

def test_compose_reports_valid_sitemap_trend_down():
    history = [
        _snap("ok", hours_ago=72, valid_sm=9, total_sm=9),   # first
        _snap("ok", hours_ago=48, valid_sm=8, total_sm=9),
        _snap("degraded", hours_ago=2, valid_sm=6, total_sm=9),  # latest
    ]
    stats = bot_discovery._compose_seo_weekly_digest(history)
    assert stats["valid_sitemaps_first"] == 9
    assert stats["valid_sitemaps_latest"] == 6
    assert stats["valid_sitemaps_delta"] == -3
    assert stats["valid_sitemaps_trend"] == "down"


def test_compose_reports_valid_sitemap_trend_up():
    history = [
        _snap("degraded", hours_ago=72, valid_sm=5, total_sm=9),
        _snap("ok", hours_ago=2, valid_sm=9, total_sm=9),
    ]
    stats = bot_discovery._compose_seo_weekly_digest(history)
    assert stats["valid_sitemaps_delta"] == 4
    assert stats["valid_sitemaps_trend"] == "up"


def test_format_html_includes_trend_block():
    stats = bot_discovery._compose_seo_weekly_digest([
        _snap("ok", hours_ago=72, valid_sm=9, total_sm=9),
        _snap("degraded", hours_ago=2, valid_sm=6, total_sm=9),
    ])
    html = bot_discovery._format_seo_weekly_digest_html(stats)
    assert "Valid sitemaps trend" in html
    assert "9 → 6" in html
    assert "-3" in html


# ── _should_send_weekly_digest_now (scheduler gate) ────────────────────────

def test_scheduler_fires_within_tolerance_on_monday():
    # 2026-04-13 is a Monday. 03:30 UTC == 09:00 IST.
    on_time = datetime(2026, 4, 13, 3, 30, tzinfo=timezone.utc)
    assert bot_discovery._should_send_weekly_digest_now(on_time, "")
    near = datetime(2026, 4, 13, 3, 20, tzinfo=timezone.utc)  # -10 min
    assert bot_discovery._should_send_weekly_digest_now(near, "")
    far = datetime(2026, 4, 13, 4, 0, tzinfo=timezone.utc)    # +30 min
    assert not bot_discovery._should_send_weekly_digest_now(far, "")


def test_scheduler_skips_other_weekdays():
    tue = datetime(2026, 4, 14, 3, 30, tzinfo=timezone.utc)
    assert not bot_discovery._should_send_weekly_digest_now(tue, "")
    sun = datetime(2026, 4, 12, 3, 30, tzinfo=timezone.utc)
    assert not bot_discovery._should_send_weekly_digest_now(sun, "")


def test_scheduler_dedups_same_iso_week():
    on_time = datetime(2026, 4, 13, 3, 30, tzinfo=timezone.utc)
    same_week_marker = bot_discovery._iso_week_tag(on_time)
    assert not bot_discovery._should_send_weekly_digest_now(on_time, same_week_marker)
    assert bot_discovery._should_send_weekly_digest_now(on_time, "2025-W52")


# ── _claim_weekly_digest_slot: atomic dedup across replicas ────────────────

def test_claim_succeeds_when_marker_is_stale():
    """CAS path: doc exists with an older ISO week → exactly one CAS update
    succeeds, returning the previous doc, so we win the claim."""
    job_locks = MagicMock()
    job_locks.find_one_and_update = AsyncMock(return_value={
        "_id": bot_discovery._SEO_WEEKLY_DIGEST_LOCK_ID,
        bot_discovery._SEO_WEEKLY_DIGEST_API_CONFIG_KEY: "2026-W14",
    })
    job_locks.insert_one = AsyncMock()
    fake_db = MagicMock(job_locks=job_locks)
    won = asyncio.run(bot_discovery._claim_weekly_digest_slot(fake_db, "2026-W15"))
    assert won is True
    job_locks.find_one_and_update.assert_awaited_once()
    job_locks.insert_one.assert_not_awaited()  # CAS path won, no need to insert


def test_claim_fails_when_marker_already_current():
    """The losing worker in a race: doc already at cur_iso_week → CAS filter
    matches nothing (returns None), insert raises DuplicateKeyError → False."""
    from pymongo.errors import DuplicateKeyError

    job_locks = MagicMock()
    job_locks.find_one_and_update = AsyncMock(return_value=None)
    job_locks.insert_one = AsyncMock(side_effect=DuplicateKeyError("dup _id"))
    fake_db = MagicMock(job_locks=job_locks)
    won = asyncio.run(bot_discovery._claim_weekly_digest_slot(fake_db, "2026-W15"))
    assert won is False


def test_claim_bootstraps_when_doc_missing():
    """First-ever run: doc doesn't exist → CAS returns None, insert succeeds
    → we own the claim. Verifies the bootstrap path can't multi-send because
    only one insert can win on the unique _id."""
    job_locks = MagicMock()
    job_locks.find_one_and_update = AsyncMock(return_value=None)
    job_locks.insert_one = AsyncMock(return_value=None)
    fake_db = MagicMock(job_locks=job_locks)
    won = asyncio.run(bot_discovery._claim_weekly_digest_slot(fake_db, "2026-W15"))
    assert won is True
    job_locks.insert_one.assert_awaited_once()
    inserted_doc = job_locks.insert_one.await_args.args[0]
    assert inserted_doc["_id"] == bot_discovery._SEO_WEEKLY_DIGEST_LOCK_ID
    assert inserted_doc[bot_discovery._SEO_WEEKLY_DIGEST_API_CONFIG_KEY] == "2026-W15"


def test_try_send_skips_when_already_sent_this_week():
    """End-to-end iteration: pre-gate sees marker == cur_iso_week, so we
    bail out before claim/send. No email, no rollback."""
    on_time = datetime(2026, 4, 13, 3, 30, tzinfo=timezone.utc)
    cur_week = bot_discovery._iso_week_tag(on_time)
    job_locks = MagicMock()
    job_locks.find_one = AsyncMock(return_value={
        bot_discovery._SEO_WEEKLY_DIGEST_API_CONFIG_KEY: cur_week,
    })
    job_locks.find_one_and_update = AsyncMock()
    job_locks.insert_one = AsyncMock()
    fake_db = MagicMock(job_locks=job_locks)
    with patch.object(bot_discovery, "_send_seo_weekly_digest_email", AsyncMock()) as snd:
        out = asyncio.run(bot_discovery._try_send_weekly_digest_once(fake_db, on_time))
    assert out["claimed"] is False
    assert out["sent"] is False
    snd.assert_not_awaited()
    job_locks.find_one_and_update.assert_not_awaited()
    job_locks.insert_one.assert_not_awaited()


def test_lock_writes_do_not_touch_api_config():
    """Regression guard: weekly digest lock writes go to db.job_locks, not
    db.api_config. If this ever flips back, find_one({}) reads of api_config
    elsewhere (alert settings, GA4 token, notification channels) become
    nondeterministic."""
    api_config = MagicMock()
    # Wire all api_config write methods so any accidental call shows up.
    api_config.find_one_and_update = AsyncMock(return_value=None)
    api_config.update_one = AsyncMock()
    api_config.insert_one = AsyncMock()
    api_config.replace_one = AsyncMock()
    job_locks = MagicMock()
    job_locks.find_one_and_update = AsyncMock(return_value={
        "_id": bot_discovery._SEO_WEEKLY_DIGEST_LOCK_ID,
        bot_discovery._SEO_WEEKLY_DIGEST_API_CONFIG_KEY: "2026-W14",
    })
    job_locks.insert_one = AsyncMock()
    fake_db = MagicMock(api_config=api_config, job_locks=job_locks)

    won = asyncio.run(bot_discovery._claim_weekly_digest_slot(fake_db, "2026-W15"))
    assert won is True

    # Critical assertions — none of these may have been touched.
    api_config.find_one_and_update.assert_not_awaited()
    api_config.update_one.assert_not_awaited()
    api_config.insert_one.assert_not_awaited()
    api_config.replace_one.assert_not_awaited()


def test_gather_inputs_pulls_history_and_alert_count():
    history_docs = [_snap("ok", hours_ago=h) for h in (1, 2, 3)]

    class _Cursor:
        def __init__(self, docs):
            self._docs = docs
        def sort(self, *_a, **_k):
            return self
        async def to_list(self, length=None):
            return self._docs

    fake_db = MagicMock()
    fake_db.seo_health_history.find = MagicMock(return_value=_Cursor(history_docs))
    fake_db.alerts.count_documents = AsyncMock(return_value=4)

    with patch("deps.db", fake_db), \
         patch("deps.is_mongo_available", AsyncMock(return_value=True)):
        stats = asyncio.run(bot_discovery._gather_weekly_digest_inputs())
    assert stats["total_snapshots"] == 3
    assert stats["recent_alerts"] == 4
    assert stats["uptime_pct"] == 100.0
