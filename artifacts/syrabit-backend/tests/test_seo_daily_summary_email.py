"""Tests for the daily SEO auto-publish summary email (Task #465)."""
import asyncio
import sys
import types
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()
import seo_engine  # noqa: E402
from db_ops import _ADMIN_NOTIF_PREFS_DEFAULTS  # noqa: E402


def _outcome(outcome: str, reason: str = "") -> dict:
    return {"outcome": outcome, "reason": reason, "topic_id": "t", "page_type": "notes"}


# ── _top_failure_reasons ────────────────────────────────────────────────────

def test_top_failure_reasons_counts_only_failures():
    outcomes = [
        _outcome("generated"),
        _outcome("skipped", "page exists"),
        _outcome("failed", "LLM timeout"),
        _outcome("failed", "LLM timeout"),
        _outcome("failed", "guardrail rejected"),
        _outcome("failed", "missing hierarchy"),
        _outcome("failed", "missing hierarchy"),
        _outcome("failed", "missing hierarchy"),
    ]
    top = seo_engine._top_failure_reasons(outcomes, k=3)
    assert top[0] == {"reason": "missing hierarchy", "count": 3}
    assert top[1] == {"reason": "LLM timeout", "count": 2}
    assert top[2] == {"reason": "guardrail rejected", "count": 1}


def test_top_failure_reasons_handles_missing_reason():
    outcomes = [_outcome("failed", ""), _outcome("failed", None)]
    top = seo_engine._top_failure_reasons(outcomes, k=3)
    assert top == [{"reason": "unknown", "count": 2}]


def test_top_failure_reasons_empty_returns_empty_list():
    assert seo_engine._top_failure_reasons([]) == []


# ── _compose_seo_daily_summary ──────────────────────────────────────────────

def test_compose_pulls_all_required_fields():
    log_doc = {
        "job_id": "job-sched-abc",
        "completed_at": "2026-04-18T02:30:00+00:00",
        "total_generated": 12,
        "skipped": 4,
        "errors": 2,
        "new_topics": 5,
        "avg_seo_score": 84,
        "avg_geo_score": 78,
        "page_types": ["notes", "mcqs"],
        "outcomes": [
            _outcome("failed", "LLM timeout"),
            _outcome("failed", "LLM timeout"),
            _outcome("failed", "guardrail rejected"),
        ],
    }
    stats = seo_engine._compose_seo_daily_summary(log_doc)
    assert stats["pages_generated"] == 12
    assert stats["skipped"] == 4
    assert stats["errors"] == 2
    assert stats["new_topics"] == 5
    assert stats["avg_seo_score"] == 84
    assert stats["avg_geo_score"] == 78
    assert stats["page_types"] == ["notes", "mcqs"]
    assert stats["top_failure_reasons"][0]["reason"] == "LLM timeout"
    assert stats["top_failure_reasons"][0]["count"] == 2


# ── _format_seo_daily_summary_html ──────────────────────────────────────────

def test_format_html_includes_all_key_metrics_and_link():
    stats = seo_engine._compose_seo_daily_summary({
        "job_id": "job-sched-xyz",
        "completed_at": "2026-04-18T02:30:00+00:00",
        "total_generated": 7,
        "skipped": 3,
        "errors": 1,
        "new_topics": 2,
        "avg_seo_score": 91,
        "avg_geo_score": 88,
        "page_types": ["notes"],
        "outcomes": [_outcome("failed", "LLM timeout")],
    })
    html = seo_engine._format_seo_daily_summary_html(stats)
    assert "SEO daily summary" in html
    assert "job-sched-xyz" in html
    assert ">7</b>" in html or ">7<" in html
    assert ">91</b>" in html
    assert ">88</b>" in html
    assert "LLM timeout" in html
    assert "syrabit.ai/admin/seo" in html
    assert "email_seo_daily_summary_enabled" in html


def test_format_html_clean_state_when_no_failures():
    stats = seo_engine._compose_seo_daily_summary({
        "job_id": "job-sched-clean",
        "completed_at": "2026-04-18T02:30:00+00:00",
        "total_generated": 50,
        "skipped": 0,
        "errors": 0,
        "new_topics": 0,
        "avg_seo_score": 95,
        "avg_geo_score": 92,
        "page_types": ["notes"],
        "outcomes": [_outcome("generated")],
    })
    html = seo_engine._format_seo_daily_summary_html(stats)
    assert "every page generated cleanly" in html


def test_format_html_escapes_failure_reason():
    stats = seo_engine._compose_seo_daily_summary({
        "job_id": "j",
        "outcomes": [_outcome("failed", "bad <script>alert(1)</script>")],
    })
    html = seo_engine._format_seo_daily_summary_html(stats)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ── _quiet_hours_active ─────────────────────────────────────────────────────

def test_quiet_hours_simple_window():
    prefs = {"quiet_hours_start_utc": 8, "quiet_hours_end_utc": 18}
    assert seo_engine._quiet_hours_active(prefs, datetime(2026, 4, 18, 10, 0, tzinfo=timezone.utc))
    assert not seo_engine._quiet_hours_active(prefs, datetime(2026, 4, 18, 7, 0, tzinfo=timezone.utc))
    # End is exclusive
    assert not seo_engine._quiet_hours_active(prefs, datetime(2026, 4, 18, 18, 0, tzinfo=timezone.utc))


def test_quiet_hours_overnight_wraparound():
    prefs = {"quiet_hours_start_utc": 22, "quiet_hours_end_utc": 6}
    assert seo_engine._quiet_hours_active(prefs, datetime(2026, 4, 18, 23, 0, tzinfo=timezone.utc))
    assert seo_engine._quiet_hours_active(prefs, datetime(2026, 4, 18, 2, 0, tzinfo=timezone.utc))
    assert not seo_engine._quiet_hours_active(prefs, datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc))


def test_quiet_hours_disabled_when_bounds_missing_or_equal():
    now = datetime(2026, 4, 18, 2, 0, tzinfo=timezone.utc)
    assert not seo_engine._quiet_hours_active({}, now)
    assert not seo_engine._quiet_hours_active({"quiet_hours_start_utc": 5, "quiet_hours_end_utc": None}, now)
    assert not seo_engine._quiet_hours_active({"quiet_hours_start_utc": 5, "quiet_hours_end_utc": 5}, now)
    # Out-of-range silently disables instead of raising
    assert not seo_engine._quiet_hours_active({"quiet_hours_start_utc": 25, "quiet_hours_end_utc": 6}, now)


# ── _resolve_seo_summary_recipients ─────────────────────────────────────────

class _AdminCursor:
    def __init__(self, docs): self._docs = docs
    async def to_list(self, length=None): return self._docs


def _fake_db(admins):
    db = MagicMock()
    db.users = MagicMock()
    db.users.find = MagicMock(return_value=_AdminCursor(admins))
    return db


def test_recipients_filters_by_opt_in_and_quiet_hours():
    admins = [
        {"id": "a1", "email": "opt-in@x.com"},
        {"id": "a2", "email": "opt-out@x.com"},
        {"id": "a3", "email": "quiet@x.com"},
        {"id": "a4", "email": ""},  # missing email — must be skipped
    ]
    prefs_map = {
        "a1": {**_ADMIN_NOTIF_PREFS_DEFAULTS, "email_seo_daily_summary_enabled": True},
        "a2": {**_ADMIN_NOTIF_PREFS_DEFAULTS, "email_seo_daily_summary_enabled": False},
        "a3": {**_ADMIN_NOTIF_PREFS_DEFAULTS, "email_seo_daily_summary_enabled": True,
               "quiet_hours_start_utc": 0, "quiet_hours_end_utc": 6},
    }

    async def _fake_get_prefs(admin_id):
        return prefs_map.get(admin_id, {**_ADMIN_NOTIF_PREFS_DEFAULTS})

    fake_db = _fake_db(admins)
    now = datetime(2026, 4, 18, 2, 30, tzinfo=timezone.utc)  # inside a3's quiet window
    with patch("db_ops.get_admin_notification_prefs", _fake_get_prefs):
        recipients = asyncio.run(seo_engine._resolve_seo_summary_recipients(fake_db, now))
    emails = [r["email"] for r in recipients]
    assert emails == ["opt-in@x.com"]


def test_recipients_default_opt_in_when_no_prefs_doc():
    admins = [{"id": "a1", "email": "fresh-admin@x.com"}]

    async def _fake_get_prefs(admin_id):
        return {**_ADMIN_NOTIF_PREFS_DEFAULTS, "admin_id": admin_id}

    now = datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc)
    with patch("db_ops.get_admin_notification_prefs", _fake_get_prefs):
        recipients = asyncio.run(seo_engine._resolve_seo_summary_recipients(_fake_db(admins), now))
    assert recipients == [{"admin_id": "a1", "email": "fresh-admin@x.com"}]


# ── _send_seo_daily_summary_email ───────────────────────────────────────────

def test_send_skipped_with_no_recipients():
    res = asyncio.run(seo_engine._send_seo_daily_summary_email({"pages_generated": 1}, []))
    assert res["sent"] == 0
    assert res["reason"] == "no_recipients"


def test_send_skipped_without_aws_creds():
    """Task #556 — SES is the sole transactional path; no AWS creds = no send."""
    fake_stats = seo_engine._compose_seo_daily_summary({"job_id": "j", "total_generated": 1})
    with patch.dict("os.environ",
                    {"AWS_ACCESS_KEY_ID": "", "AWS_SECRET_ACCESS_KEY": ""},
                    clear=False):
        res = asyncio.run(seo_engine._send_seo_daily_summary_email(
            fake_stats, [{"admin_id": "a", "email": "a@b.com"}]
        ))
    assert res["sent"] == 0
    assert res["reason"] == "no_aws_creds"


def test_send_dispatches_per_recipient_via_sendgrid():
    fake_stats = seo_engine._compose_seo_daily_summary({
        "job_id": "j", "total_generated": 5, "errors": 1,
        "outcomes": [_outcome("failed", "x")],
    })
    sent_calls = []

    def _fake_send_admin_email(**kwargs):
        sent_calls.append(kwargs)
        return True

    import email_templates as _et
    recipients = [
        {"admin_id": "a1", "email": "one@x.com"},
        {"admin_id": "a2", "email": "two@x.com"},
    ]
    with patch.object(_et, "send_admin_email", _fake_send_admin_email), \
         patch.dict("os.environ", {}, clear=False):
        res = asyncio.run(seo_engine._send_seo_daily_summary_email(fake_stats, recipients))
    assert res["sent"] == 2
    assert res["failed"] == 0
    assert len(sent_calls) == 2
    assert sent_calls[0]["to"] == "one@x.com"
    assert sent_calls[1]["to"] == "two@x.com"
    assert "SEO daily summary" in sent_calls[0]["subject"]


def test_send_continues_when_one_recipient_fails():
    fake_stats = seo_engine._compose_seo_daily_summary({"job_id": "j", "total_generated": 1})
    calls = []

    def _fake_send_admin_email(**kwargs):
        calls.append(kwargs)
        if kwargs.get("to") == "bad@x.com":
            raise RuntimeError("smtp boom")
        return True

    import email_templates as _et
    recipients = [
        {"admin_id": "a1", "email": "ok@x.com"},
        {"admin_id": "a2", "email": "bad@x.com"},
        {"admin_id": "a3", "email": "ok2@x.com"},
    ]
    with patch.object(_et, "send_admin_email", _fake_send_admin_email), \
         patch.dict("os.environ", {}, clear=False):
        res = asyncio.run(seo_engine._send_seo_daily_summary_email(fake_stats, recipients))
    assert res["sent"] == 2
    assert res["failed"] == 1
    assert len(calls) == 3
    # Task #474 — per-admin failures must be captured so the admin
    # notifications panel can show exactly which recipient failed.
    assert isinstance(res.get("errors"), list) and len(res["errors"]) == 1
    err = res["errors"][0]
    assert err["admin_id"] == "a2"
    assert err["email"] == "bad@x.com"
    assert "smtp boom" in err["error"]
    assert err["error"].startswith("RuntimeError")


# ── _maybe_dispatch_seo_daily_summary ───────────────────────────────────────

def test_dispatch_skips_non_scheduled_runs():
    seo_engine._seo_jobs["job-manual-1"] = {"trigger": "manual"}
    res = asyncio.run(seo_engine._maybe_dispatch_seo_daily_summary("job-manual-1", {"job_id": "x"}))
    assert res["reason"] == "non_scheduled_run"
    assert res["sent"] == 0
    seo_engine._seo_jobs.pop("job-manual-1", None)


def test_dispatch_runs_for_scheduled_jobs():
    seo_engine._seo_jobs["job-sched-9"] = {"trigger": "scheduler"}
    fake_send = AsyncMock(return_value={"sent": 1, "failed": 0, "total": 1,
                                        "subject": "subj"})
    fake_audience = AsyncMock(return_value={
        "recipients": [{"admin_id": "a", "email": "a@b.com"}],
        "suppressed_quiet_hours": 2, "opted_out": 1,
        "no_email": 0, "total_admins": 4,
    })
    fake_record = AsyncMock(return_value=None)
    log_doc = {"job_id": "job-sched-9", "total_generated": 3, "errors": 0,
               "skipped": 0, "new_topics": 1, "avg_seo_score": 80, "avg_geo_score": 75,
               "page_types": ["notes"], "outcomes": []}
    with patch.object(seo_engine, "_send_seo_daily_summary_email", fake_send), \
         patch.object(seo_engine, "_resolve_seo_summary_audience", fake_audience), \
         patch.object(seo_engine, "_record_seo_summary_dispatch", fake_record):
        res = asyncio.run(seo_engine._maybe_dispatch_seo_daily_summary("job-sched-9", log_doc))
    assert res["sent"] == 1
    # Task #474 — dispatcher must mirror audience counters into the result
    # so the persisted log doc and admin UI agree.
    assert res["suppressed_quiet_hours"] == 2
    assert res["opted_out"] == 1
    fake_send.assert_awaited_once()
    sent_stats = fake_send.await_args.args[0]
    assert sent_stats["pages_generated"] == 3
    assert sent_stats["job_id"] == "job-sched-9"
    fake_record.assert_awaited_once()
    persisted = fake_record.await_args.args[0]
    assert persisted["job_id"] == "job-sched-9"
    assert persisted["sent"] == 1
    assert persisted["suppressed_quiet_hours"] == 2
    assert persisted["opted_out"] == 1
    assert persisted["total_admins"] == 4
    assert persisted["pages_generated"] == 3
    seo_engine._seo_jobs.pop("job-sched-9", None)


def test_dispatch_persists_log_even_on_send_error():
    """Even when send blows up we must still write a row so admins can see
    the failure surfaced in the notifications panel (Task #474)."""
    seo_engine._seo_jobs["job-sched-err"] = {"trigger": "scheduler"}
    fake_audience = AsyncMock(return_value={
        "recipients": [{"admin_id": "a", "email": "a@b.com"}],
        "suppressed_quiet_hours": 0, "opted_out": 0,
        "no_email": 0, "total_admins": 1,
    })
    fake_send = AsyncMock(side_effect=RuntimeError("boom"))
    fake_record = AsyncMock(return_value=None)
    log_doc = {"job_id": "job-sched-err", "total_generated": 0}
    with patch.object(seo_engine, "_send_seo_daily_summary_email", fake_send), \
         patch.object(seo_engine, "_resolve_seo_summary_audience", fake_audience), \
         patch.object(seo_engine, "_record_seo_summary_dispatch", fake_record):
        res = asyncio.run(seo_engine._maybe_dispatch_seo_daily_summary("job-sched-err", log_doc))
    assert res["sent"] == 0
    assert "dispatch_error" in (res.get("reason") or "")
    fake_record.assert_awaited_once()
    persisted = fake_record.await_args.args[0]
    assert persisted["sent"] == 0
    assert "dispatch_error" in (persisted.get("reason") or "")
    seo_engine._seo_jobs.pop("job-sched-err", None)


# ── _resolve_seo_summary_audience ───────────────────────────────────────────

def test_audience_breaks_down_suppression_reasons():
    """Audience helper must distinguish quiet-hours suppression from opt-out
    and missing-email so the persisted dispatch log is meaningful."""
    admins = [
        {"id": "a1", "email": "opt-in@x.com"},
        {"id": "a2", "email": "opt-out@x.com"},
        {"id": "a3", "email": "quiet@x.com"},
        {"id": "a4", "email": ""},  # missing email
    ]
    prefs_map = {
        "a1": {**_ADMIN_NOTIF_PREFS_DEFAULTS, "email_seo_daily_summary_enabled": True},
        "a2": {**_ADMIN_NOTIF_PREFS_DEFAULTS, "email_seo_daily_summary_enabled": False},
        "a3": {**_ADMIN_NOTIF_PREFS_DEFAULTS, "email_seo_daily_summary_enabled": True,
               "quiet_hours_start_utc": 0, "quiet_hours_end_utc": 6},
    }

    async def _fake_get_prefs(admin_id):
        return prefs_map.get(admin_id, {**_ADMIN_NOTIF_PREFS_DEFAULTS})

    now = datetime(2026, 4, 18, 2, 30, tzinfo=timezone.utc)  # inside a3's quiet window
    with patch("db_ops.get_admin_notification_prefs", _fake_get_prefs):
        audience = asyncio.run(seo_engine._resolve_seo_summary_audience(_fake_db(admins), now))
    assert [r["email"] for r in audience["recipients"]] == ["opt-in@x.com"]
    assert audience["suppressed_quiet_hours"] == 1
    assert audience["opted_out"] == 1
    assert audience["no_email"] == 1
    assert audience["total_admins"] == 4
