"""Tests for Task #655 — weekly review-prompt summary email in
``routes/admin_review_prompts.py``. Mirrors the patterns in
``test_seo_weekly_digest.py`` and ``test_review_prompt_alerting.py``:
install the deps stub, then exercise the pure helpers
(``_compose_review_prompt_weekly_digest``,
``_format_review_prompt_weekly_digest_html``,
``_should_send_review_prompt_digest_now``,
``_try_send_review_prompt_weekly_digest_once``) without touching real
Mongo / Amazon SES (Task #556 — SES is the sole transactional email path).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()
from routes import admin_review_prompts as arp  # noqa: E402


# ── _compose_review_prompt_weekly_digest ────────────────────────────────────

def test_compose_zero_events_returns_safe_shape():
    stats = arp._compose_review_prompt_weekly_digest(
        {"shown": 0, "clicked": 0, "dismissed": 0},
        [],
        {"shown": 0, "clicked": 0, "dismissed": 0},
    )
    assert stats["shown"] == 0
    assert stats["clicked"] == 0
    assert stats["dismissed"] == 0
    assert stats["ctr_pct"] is None
    assert stats["prev_ctr_pct"] is None
    assert stats["ctr_delta_pct"] is None
    assert stats["ctr_trend"] == "flat"
    assert stats["top_reason"] is None
    assert stats["by_reason"] == []
    assert "iso_week" in stats and stats["iso_week"].startswith(
        str(datetime.now(timezone.utc).year)
    )


def test_compose_computes_ctr_and_wow_delta_up():
    # Curr: 200/20 = 10.0% CTR; prev: 100/5 = 5.0% CTR → +5.0 pp, trend up.
    stats = arp._compose_review_prompt_weekly_digest(
        {"shown": 200, "clicked": 20, "dismissed": 30},
        [
            {"reason": "answer_helpful", "shown": 150, "clicked": 18, "dismissed": 20},
            {"reason": "session_end", "shown": 50, "clicked": 2, "dismissed": 10},
        ],
        {"shown": 100, "clicked": 5, "dismissed": 10},
    )
    assert stats["shown"] == 200
    assert stats["clicked"] == 20
    assert stats["ctr_pct"] == 10.0
    assert stats["dismiss_rate_pct"] == 15.0
    assert stats["prev_ctr_pct"] == 5.0
    assert stats["ctr_delta_pct"] == 5.0
    assert stats["ctr_trend"] == "up"
    # Top reason = highest shown.
    assert stats["top_reason"]["reason"] == "answer_helpful"
    assert stats["top_reason"]["ctr_pct"] == 12.0
    # Per-reason sorted by shown desc.
    assert [r["reason"] for r in stats["by_reason"]] == ["answer_helpful", "session_end"]


def test_compose_trend_down_when_ctr_drops():
    stats = arp._compose_review_prompt_weekly_digest(
        {"shown": 100, "clicked": 2, "dismissed": 20},
        [],
        {"shown": 100, "clicked": 10, "dismissed": 20},
    )
    assert stats["ctr_pct"] == 2.0
    assert stats["prev_ctr_pct"] == 10.0
    assert stats["ctr_delta_pct"] == -8.0
    assert stats["ctr_trend"] == "down"


def test_compose_per_reason_wow_delta_active_reason():
    """Active reason — both windows have data; per-reason delta computed."""
    stats = arp._compose_review_prompt_weekly_digest(
        {"shown": 100, "clicked": 10, "dismissed": 0},
        [{"reason": "answer_helpful", "shown": 100, "clicked": 10, "dismissed": 0}],
        {"shown": 80, "clicked": 16, "dismissed": 0},
        prev_by_reason=[{"reason": "answer_helpful",
                         "shown": 80, "clicked": 16, "dismissed": 0}],
    )
    row = stats["by_reason"][0]
    assert row["reason"] == "answer_helpful"
    assert row["status"] == "active"
    assert row["prev_shown"] == 80
    assert row["prev_clicked"] == 16
    assert row["prev_ctr_pct"] == 20.0
    assert row["ctr_pct"] == 10.0
    assert row["ctr_delta_pct"] == -10.0
    assert row["shown_delta"] == 20


def test_compose_per_reason_marks_new_and_gone_reasons():
    """New reasons (no prev data) tagged 'new'; reasons that disappear
    this week are still surfaced with status 'gone' so ops can spot a
    silenced trigger."""
    stats = arp._compose_review_prompt_weekly_digest(
        {"shown": 50, "clicked": 5, "dismissed": 0},
        [{"reason": "session_end", "shown": 50, "clicked": 5, "dismissed": 0}],
        {"shown": 40, "clicked": 4, "dismissed": 0},
        prev_by_reason=[{"reason": "answer_helpful",
                         "shown": 40, "clicked": 4, "dismissed": 0}],
    )
    by_reason = {r["reason"]: r for r in stats["by_reason"]}
    assert by_reason["session_end"]["status"] == "new"
    assert by_reason["session_end"]["prev_shown"] == 0
    assert by_reason["session_end"]["ctr_delta_pct"] is None
    assert by_reason["answer_helpful"]["status"] == "gone"
    assert by_reason["answer_helpful"]["shown"] == 0
    assert by_reason["answer_helpful"]["prev_shown"] == 40
    assert by_reason["answer_helpful"]["shown_delta"] == -40
    assert by_reason["answer_helpful"]["ctr_delta_pct"] is None


def test_format_html_renders_per_reason_wow_columns():
    """The digest email table must include the new Δ-vs-prev-week
    columns and clearly label new/gone reasons."""
    stats = arp._compose_review_prompt_weekly_digest(
        {"shown": 150, "clicked": 15, "dismissed": 0},
        [
            {"reason": "answer_helpful", "shown": 100, "clicked": 10, "dismissed": 0},
            {"reason": "session_end",    "shown": 50,  "clicked": 5,  "dismissed": 0},
        ],
        {"shown": 80, "clicked": 16, "dismissed": 0},
        prev_by_reason=[
            {"reason": "answer_helpful", "shown": 80, "clicked": 16, "dismissed": 0},
            {"reason": "chapter_engaged", "shown": 30, "clicked": 3, "dismissed": 0},
        ],
    )
    html = arp._format_review_prompt_weekly_digest_html(stats)
    assert "Δ shown vs prev week" in html
    assert "Δ CTR vs prev week" in html
    # session_end is brand new this window.
    assert ">new<" in html
    # chapter_engaged was around last week but disappeared.
    assert ">gone<" in html
    # answer_helpful CTR fell from 20% → 10% → -10.0 pp delta rendered.
    assert "-10.0 pp" in html


def test_compose_trend_flat_when_prev_ctr_unavailable():
    stats = arp._compose_review_prompt_weekly_digest(
        {"shown": 100, "clicked": 5, "dismissed": 10},
        [],
        {"shown": 0, "clicked": 0, "dismissed": 0},
    )
    assert stats["ctr_pct"] == 5.0
    assert stats["prev_ctr_pct"] is None
    assert stats["ctr_delta_pct"] is None
    assert stats["ctr_trend"] == "flat"


def test_compose_window_boundaries_match_window_days():
    now = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
    stats = arp._compose_review_prompt_weekly_digest(
        {"shown": 1, "clicked": 0, "dismissed": 0}, [],
        {"shown": 0, "clicked": 0, "dismissed": 0}, now=now, window_days=7,
    )
    start = datetime.fromisoformat(stats["window_start"])
    end = datetime.fromisoformat(stats["window_end"])
    assert (end - start) == timedelta(days=7)
    assert stats["window_days"] == 7


# ── _format_review_prompt_weekly_digest_html ────────────────────────────────

def test_format_html_includes_key_metrics_and_dashboard_link():
    stats = arp._compose_review_prompt_weekly_digest(
        {"shown": 200, "clicked": 20, "dismissed": 30},
        [{"reason": "answer_helpful", "shown": 150, "clicked": 18, "dismissed": 20}],
        {"shown": 100, "clicked": 5, "dismissed": 10},
    )
    html = arp._format_review_prompt_weekly_digest_html(stats)
    assert "Trustpilot review prompt" in html
    assert "10.0%" in html       # current CTR
    assert "5.0%" in html        # previous CTR
    assert "+5.0 pp" in html     # WoW delta
    assert "answer_helpful" in html
    assert arp._REVIEW_PROMPT_DIGEST_DASHBOARD_URL in html


# ── Task #694 — baseline noise on the per-reason digest rows ─────────────


def _baseline_snapshot(by_reason: dict, *, sigma_mult: float = 2.0,
                       baseline_weeks: int = 4, min_shown: int = 50) -> dict:
    """Helper: build a baseline snapshot in the shape that
    ``_compute_review_prompt_reason_baseline`` returns, so the digest
    composer/formatter can be exercised without touching Mongo."""
    return {
        "window_days": 7,
        "baseline_weeks": baseline_weeks,
        "min_shown": min_shown,
        "sigma_mult": sigma_mult,
        "by_reason": by_reason,
    }


def test_compose_merges_populated_baseline_into_per_reason_rows():
    """Populated-baseline path — every per-reason row picks up
    ``baseline_mean_ctr_pct``, ``baseline_stddev_pp`` and
    ``baseline_z_score`` from the snapshot and the response carries
    ``baseline_meta`` so the formatter can render the legend."""
    baseline = _baseline_snapshot({
        "answer_helpful": {
            "baseline_weeks_used": 4,
            "baseline_mean_ctr_pct": 8.0,
            "baseline_stddev_pp": 1.5,
            "current_ctr_pct": 12.0,
            "current_shown": 150,
            "current_z_score": 2.67,
            "sigma_threshold_pp": 3.0,
        },
    })
    stats = arp._compose_review_prompt_weekly_digest(
        {"shown": 150, "clicked": 18, "dismissed": 0},
        [{"reason": "answer_helpful", "shown": 150, "clicked": 18, "dismissed": 0}],
        {"shown": 100, "clicked": 8, "dismissed": 0},
        baseline=baseline,
    )
    row = next(r for r in stats["by_reason"] if r["reason"] == "answer_helpful")
    assert row["baseline_mean_ctr_pct"] == 8.0
    assert row["baseline_stddev_pp"] == 1.5
    assert row["baseline_z_score"] == 2.67
    assert row["baseline_weeks_used"] == 4
    assert stats["baseline_meta"] == {
        "baseline_weeks": 4,
        "min_shown": 50,
        "sigma_mult": 2.0,
    }


def test_compose_thin_baseline_falls_back_to_none_per_row():
    """Thin-baseline path — a reason with fewer than 2 qualifying
    weekly samples comes back with ``None`` for mean / stddev / z so
    the email can render an explicit "n/a" instead of a misleading
    point estimate."""
    baseline = _baseline_snapshot({
        "answer_helpful": {
            "baseline_weeks_used": 1,           # below the 2-sample gate
            "baseline_mean_ctr_pct": None,
            "baseline_stddev_pp": None,
            "current_ctr_pct": 12.0,
            "current_shown": 150,
            "current_z_score": None,
            "sigma_threshold_pp": None,
        },
    })
    stats = arp._compose_review_prompt_weekly_digest(
        {"shown": 150, "clicked": 18, "dismissed": 0},
        [{"reason": "answer_helpful", "shown": 150, "clicked": 18, "dismissed": 0}],
        {"shown": 0, "clicked": 0, "dismissed": 0},
        baseline=baseline,
    )
    row = stats["by_reason"][0]
    assert row["baseline_mean_ctr_pct"] is None
    assert row["baseline_stddev_pp"] is None
    assert row["baseline_z_score"] is None
    assert row["baseline_weeks_used"] == 1


def test_compose_without_baseline_keeps_legacy_shape():
    """Backwards-compat: callers that don't pass a baseline (e.g. an
    older test) get rows with ``baseline_*`` keys present but None,
    and ``baseline_meta`` is None — the formatter must still render
    without a legend."""
    stats = arp._compose_review_prompt_weekly_digest(
        {"shown": 100, "clicked": 10, "dismissed": 0},
        [{"reason": "x", "shown": 100, "clicked": 10, "dismissed": 0}],
        {"shown": 0, "clicked": 0, "dismissed": 0},
    )
    row = stats["by_reason"][0]
    assert row["baseline_mean_ctr_pct"] is None
    assert row["baseline_stddev_pp"] is None
    assert row["baseline_z_score"] is None
    assert stats["baseline_meta"] is None


def test_format_html_renders_baseline_columns_and_legend_when_populated():
    baseline = _baseline_snapshot({
        "answer_helpful": {
            "baseline_weeks_used": 4,
            "baseline_mean_ctr_pct": 8.0,
            "baseline_stddev_pp": 1.5,
            "current_ctr_pct": 12.0,
            "current_shown": 150,
            "current_z_score": 2.67,
            "sigma_threshold_pp": 3.0,
        },
    }, sigma_mult=2.0, baseline_weeks=4)
    stats = arp._compose_review_prompt_weekly_digest(
        {"shown": 150, "clicked": 18, "dismissed": 0},
        [{"reason": "answer_helpful", "shown": 150, "clicked": 18, "dismissed": 0}],
        {"shown": 0, "clicked": 0, "dismissed": 0},
        baseline=baseline,
    )
    html = arp._format_review_prompt_weekly_digest_html(stats)
    # New columns appear in the per-reason table header.
    assert "Baseline μ" in html
    assert "σ (noise)" in html
    assert "This week z" in html
    # Per-reason values render with the right formatting.
    assert "8.0%" in html               # baseline mean
    assert "±1.5 pp" in html            # baseline stddev
    assert "+2.7σ" in html              # current week z-score
    # Legend explains the noise band, parameterised on the snapshot.
    assert "prior 4 weeks" in html
    assert "±2.0σ" in html
    # Outside-band z-scores get the alert color (drop=red, spike=green).
    # 2.67σ is a positive spike → green color used elsewhere in the file.
    assert "#16a34a" in html


def test_format_html_renders_na_for_thin_baseline_and_omits_legend():
    baseline = _baseline_snapshot({
        "answer_helpful": {
            "baseline_weeks_used": 1,
            "baseline_mean_ctr_pct": None,
            "baseline_stddev_pp": None,
            "current_ctr_pct": 12.0,
            "current_shown": 150,
            "current_z_score": None,
            "sigma_threshold_pp": None,
        },
    })
    stats = arp._compose_review_prompt_weekly_digest(
        {"shown": 150, "clicked": 18, "dismissed": 0},
        [{"reason": "answer_helpful", "shown": 150, "clicked": 18, "dismissed": 0}],
        {"shown": 0, "clicked": 0, "dismissed": 0},
        baseline=baseline,
    )
    html = arp._format_review_prompt_weekly_digest_html(stats)
    # Thin-baseline cells render as explicit "n/a" pills.
    assert html.count("n/a") >= 3
    # Legend renders even when individual rows are thin (the snapshot
    # itself carries the band parameters).
    assert "prior" in html and "weeks" in html


def test_format_html_handles_empty_window_gracefully():
    stats = arp._compose_review_prompt_weekly_digest(
        {"shown": 0, "clicked": 0, "dismissed": 0}, [],
        {"shown": 0, "clicked": 0, "dismissed": 0},
    )
    html = arp._format_review_prompt_weekly_digest_html(stats)
    # No CTR computable → em-dash placeholder, no crash.
    assert "—" in html
    assert "no events recorded" in html


# ── _should_send_review_prompt_digest_now ──────────────────────────────────

def test_schedule_fires_inside_monday_window_and_new_iso_week():
    # Monday 03:30 UTC, 2026-W17 — never sent before.
    target = datetime(2026, 4, 20, 3, 30, tzinfo=timezone.utc)
    assert target.weekday() == 0
    assert arp._should_send_review_prompt_digest_now(target, "") is True
    # Ten minutes either side of the target still fires.
    assert arp._should_send_review_prompt_digest_now(
        target + timedelta(minutes=10), "",
    ) is True
    assert arp._should_send_review_prompt_digest_now(
        target - timedelta(minutes=10), "",
    ) is True


def test_schedule_skips_outside_tolerance_window():
    target = datetime(2026, 4, 20, 3, 30, tzinfo=timezone.utc)
    # 30 minutes past target → outside ±15 min tolerance.
    assert arp._should_send_review_prompt_digest_now(
        target + timedelta(minutes=30), "",
    ) is False
    # Wrong day entirely.
    tuesday = target + timedelta(days=1)
    assert arp._should_send_review_prompt_digest_now(tuesday, "") is False


def test_schedule_dedups_within_same_iso_week():
    target = datetime(2026, 4, 20, 3, 30, tzinfo=timezone.utc)
    cur_week = arp._review_prompt_iso_week_tag(target)
    # Already sent this week → must not fire again.
    assert arp._should_send_review_prompt_digest_now(target, cur_week) is False


# ── _try_send_review_prompt_weekly_digest_once ──────────────────────────────

def _make_lock_db(initial_iso_week: str = ""):
    """Build a fake `db` whose `job_locks` collection behaves like a
    minimal singleton store keyed by `_id`. Supports `find_one`,
    `find_one_and_update` (CAS), `insert_one`, and `update_one`.
    """
    state = {}
    if initial_iso_week:
        state[arp._REVIEW_PROMPT_DIGEST_LOCK_ID] = {
            "_id": arp._REVIEW_PROMPT_DIGEST_LOCK_ID,
            arp._REVIEW_PROMPT_DIGEST_API_CONFIG_KEY: initial_iso_week,
        }

    fake = MagicMock()

    async def find_one(query, projection=None):
        return state.get(query.get("_id"))

    async def find_one_and_update(query, update, upsert=False):
        _id = query.get("_id")
        doc = state.get(_id)
        ne_clause = query.get(arp._REVIEW_PROMPT_DIGEST_API_CONFIG_KEY) or {}
        ne_val = ne_clause.get("$ne") if isinstance(ne_clause, dict) else None
        if doc is None:
            return None
        if doc.get(arp._REVIEW_PROMPT_DIGEST_API_CONFIG_KEY) == ne_val:
            return None
        doc.update(update.get("$set") or {})
        return doc

    async def insert_one(doc):
        from pymongo.errors import DuplicateKeyError
        if doc["_id"] in state:
            raise DuplicateKeyError("dup")
        state[doc["_id"]] = dict(doc)
        return MagicMock(inserted_id=doc["_id"])

    async def update_one(query, update):
        doc = state.get(query.get("_id"))
        if doc is None:
            return MagicMock(matched_count=0)
        if arp._REVIEW_PROMPT_DIGEST_API_CONFIG_KEY in query:
            if doc.get(arp._REVIEW_PROMPT_DIGEST_API_CONFIG_KEY) != \
               query[arp._REVIEW_PROMPT_DIGEST_API_CONFIG_KEY]:
                return MagicMock(matched_count=0)
        doc.update(update.get("$set") or {})
        return MagicMock(matched_count=1)

    fake.job_locks.find_one = find_one
    fake.job_locks.find_one_and_update = find_one_and_update
    fake.job_locks.insert_one = insert_one
    fake.job_locks.update_one = update_one
    return fake, state


def test_try_send_skips_outside_window():
    fake_db, state = _make_lock_db()
    not_monday = datetime(2026, 4, 22, 3, 30, tzinfo=timezone.utc)  # Wed
    with patch.object(arp, "_send_review_prompt_weekly_digest_email",
                      AsyncMock()) as snd:
        result = asyncio.run(
            arp._try_send_review_prompt_weekly_digest_once(fake_db, not_monday)
        )
    assert result["sent"] is False
    assert result["reason"] == "outside_window_or_dedup"
    snd.assert_not_called()
    # Marker untouched.
    assert state == {}


def test_try_send_inside_window_claims_and_sends():
    fake_db, state = _make_lock_db()
    target = datetime(2026, 4, 20, 3, 30, tzinfo=timezone.utc)
    fake_stats = {"iso_week": "2026-W17", "shown": 10, "clicked": 1,
                  "dismissed": 2, "ctr_pct": 10.0}
    with patch.object(arp, "_gather_review_prompt_weekly_digest_inputs",
                      AsyncMock(return_value=fake_stats)), \
         patch.object(arp, "_send_review_prompt_weekly_digest_email",
                      AsyncMock(return_value={"sent": True, "to": "ops@x"})) as snd:
        result = asyncio.run(
            arp._try_send_review_prompt_weekly_digest_once(fake_db, target)
        )
    assert result == {"claimed": True, "sent": True, "reason": None}
    snd.assert_awaited_once_with(fake_stats)
    # Marker advanced to this ISO week so subsequent calls dedup.
    assert state[arp._REVIEW_PROMPT_DIGEST_LOCK_ID][
        arp._REVIEW_PROMPT_DIGEST_API_CONFIG_KEY
    ] == "2026-W17"


def test_try_send_dedups_when_marker_already_set_for_week():
    target = datetime(2026, 4, 20, 3, 30, tzinfo=timezone.utc)
    fake_db, state = _make_lock_db(initial_iso_week="2026-W17")
    with patch.object(arp, "_send_review_prompt_weekly_digest_email",
                      AsyncMock()) as snd:
        result = asyncio.run(
            arp._try_send_review_prompt_weekly_digest_once(fake_db, target)
        )
    assert result["sent"] is False
    assert result["reason"] == "outside_window_or_dedup"
    snd.assert_not_called()


def test_try_send_rolls_back_marker_on_send_failure():
    """Transient SES outage (Task #556 — SES is the sole transactional
    path) must not lock us out for the rest of the Monday window — the
    marker rolls back so the next 5-minute tick retries within the same
    window."""
    target = datetime(2026, 4, 20, 3, 30, tzinfo=timezone.utc)
    fake_db, state = _make_lock_db(initial_iso_week="2026-W16")  # last week
    fake_stats = {"iso_week": "2026-W17", "shown": 10, "clicked": 1,
                  "dismissed": 2, "ctr_pct": 10.0}
    with patch.object(arp, "_gather_review_prompt_weekly_digest_inputs",
                      AsyncMock(return_value=fake_stats)), \
         patch.object(arp, "_send_review_prompt_weekly_digest_email",
                      AsyncMock(return_value={
                          "sent": False, "to": "ops@x",
                          "reason": "send_error:Boom",
                      })):
        result = asyncio.run(
            arp._try_send_review_prompt_weekly_digest_once(fake_db, target)
        )
    assert result == {"claimed": True, "sent": False,
                      "reason": "send_error:Boom"}
    # Marker rolled back to its previous value so a retry can claim again.
    assert state[arp._REVIEW_PROMPT_DIGEST_LOCK_ID][
        arp._REVIEW_PROMPT_DIGEST_API_CONFIG_KEY
    ] == "2026-W16"


# ── _send_review_prompt_weekly_digest_email skip-paths ──────────────────────

def test_send_email_skipped_without_admin_email(monkeypatch):
    monkeypatch.delenv("ALERT_EMAIL", raising=False)
    fake_stats = {"iso_week": "2026-W17", "shown": 1, "clicked": 0,
                  "dismissed": 0, "ctr_pct": None}
    # Force _notification_channels lookup to return no email.
    import metrics as _m
    saved = dict(_m._notification_channels)
    try:
        _m._notification_channels.clear()
        result = asyncio.run(
            arp._send_review_prompt_weekly_digest_email(fake_stats)
        )
    finally:
        _m._notification_channels.clear()
        _m._notification_channels.update(saved)
    assert result["sent"] is False
    assert result["reason"] == "no_admin_email"


def test_send_email_skipped_without_aws_creds(monkeypatch):
    """Task #556 — when AWS credentials are absent, the digest sender
    must short-circuit to ``no_aws_creds`` (no SES call attempted)."""
    monkeypatch.setenv("ALERT_EMAIL", "ops@example.com")
    # Hermetic: explicitly clear AWS creds so the test result doesn't
    # depend on the developer / CI shell environment.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "")
    # Reset the in-memory ``metrics._notification_channels`` dict — earlier
    # tests in the suite (e.g. ``test_admin_assamese_purity``,
    # ``test_hydrate_slack_payload``) populate this with stub values like
    # "x@y.z" via ``_load_alert_settings``. Without resetting, the digest
    # recipient resolver short-circuits on the polluted dict before ever
    # consulting ``ALERT_EMAIL``.
    import metrics
    monkeypatch.setattr(metrics, "_notification_channels",
                        dict(metrics._NOTIFICATION_CHANNELS_DEFAULT),
                        raising=False)
    monkeypatch.setattr(
        metrics, "_load_alert_settings",
        lambda *a, **kw: __import__("asyncio").sleep(0),
    )
    fake_stats = {"iso_week": "2026-W17", "shown": 1, "clicked": 0,
                  "dismissed": 0, "ctr_pct": None}
    result = asyncio.run(
        arp._send_review_prompt_weekly_digest_email(fake_stats)
    )
    assert result["sent"] is False
    assert result["to"] == "ops@example.com"
    assert result["reason"] == "no_aws_creds"  # Task #556 — SES sole path


def test_send_email_returns_no_stats_when_called_with_empty_dict():
    result = asyncio.run(arp._send_review_prompt_weekly_digest_email({}))
    assert result["sent"] is False
    assert result["to"] == ""
    assert result["reason"] == "no_stats"
    assert result.get("recipients") == []


# ── Stats-route ↔ digest aggregation parity ─────────────────────────────────

def _fake_event_db(*, totals_shown: int, totals_clicked: int,
                   totals_dismissed: int, by_reason: list):
    """Stub that emulates the two `review_prompt_events.aggregate` calls
    used by `_aggregate_review_prompt_window`. `by_reason` is a list of
    `{reason, shown, clicked, dismissed}` dicts."""
    fake = MagicMock()

    class _Cursor:
        def __init__(self, docs):
            self._docs = list(docs)
        def __aiter__(self):
            return self
        async def __anext__(self):
            if not self._docs:
                raise StopAsyncIteration
            return self._docs.pop(0)

    def _aggregate(pipeline, *a, **kw):
        # The grouping aggregation is "by event" if its $group._id is a
        # plain string, "by reason+event" if it's a dict.
        group_id = pipeline[1]["$group"]["_id"]
        if isinstance(group_id, str):
            return _Cursor([
                {"_id": "review_prompt_shown", "count": totals_shown},
                {"_id": "review_prompt_clicked", "count": totals_clicked},
                {"_id": "review_prompt_dismissed", "count": totals_dismissed},
            ])
        # by reason+event
        rows = []
        for row in by_reason:
            rows.extend([
                {"_id": {"reason": row["reason"], "event": "review_prompt_shown"},
                 "count": row["shown"]},
                {"_id": {"reason": row["reason"], "event": "review_prompt_clicked"},
                 "count": row["clicked"]},
                {"_id": {"reason": row["reason"], "event": "review_prompt_dismissed"},
                 "count": row["dismissed"]},
            ])
        return _Cursor(rows)

    fake.review_prompt_events.aggregate = MagicMock(side_effect=_aggregate)

    # `find().sort().limit()` chain for the recent-events query.
    class _FindCursor:
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise StopAsyncIteration
        def sort(self, *a, **kw):
            return self
        def limit(self, *a, **kw):
            return self

    fake.review_prompt_events.find = MagicMock(return_value=_FindCursor())
    return fake


def test_stats_route_and_digest_share_aggregation_for_same_window():
    """The admin tile (`/admin/analytics/review-prompt-stats`) and the
    weekly digest must agree on totals + per-reason breakdown for the
    same fixture window — guards against metric drift between the
    dashboard and the Monday email (Task #655 review feedback).
    """
    fixture_by_reason = [
        {"reason": "answer_helpful", "shown": 120, "clicked": 18, "dismissed": 30},
        {"reason": "session_end",   "shown": 80,  "clicked": 2,  "dismissed": 25},
    ]
    totals_shown = sum(r["shown"] for r in fixture_by_reason)
    totals_clicked = sum(r["clicked"] for r in fixture_by_reason)
    totals_dismissed = sum(r["dismissed"] for r in fixture_by_reason)

    fake = _fake_event_db(
        totals_shown=totals_shown,
        totals_clicked=totals_clicked,
        totals_dismissed=totals_dismissed,
        by_reason=fixture_by_reason,
    )

    with patch.object(arp, "db", fake), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)), \
         patch.object(arp, "_ensure_review_prompt_indexes", AsyncMock()):
        # Admin tile rollup — last 7 days for an apples-to-apples compare.
        stats_route = asyncio.run(arp.admin_review_prompt_stats(
            days=7, admin={"id": "admin"},
        ))
        # Weekly digest — same 7-day window. The digest also queries the
        # *previous* 7 days; it'll re-use the same fake totals (the fake
        # doesn't filter by date), but for parity we only inspect the
        # current-window totals + by_reason.
        digest = asyncio.run(arp._gather_review_prompt_weekly_digest_inputs())

    assert stats_route["shown"] == digest["shown"] == totals_shown
    assert stats_route["clicked"] == digest["clicked"] == totals_clicked
    assert stats_route["dismissed"] == digest["dismissed"] == totals_dismissed
    assert stats_route["ctr_pct"] == digest["ctr_pct"]

    # Per-reason raw counts must match across both surfaces, ignoring
    # the extra dismiss_rate_pct column the stats route adds.
    def _norm(rows):
        return sorted(
            ({"reason": r["reason"], "shown": r["shown"],
              "clicked": r["clicked"], "dismissed": r["dismissed"],
              "ctr_pct": r["ctr_pct"]} for r in rows),
            key=lambda r: r["reason"],
        )
    assert _norm(stats_route["by_reason"]) == _norm(digest["by_reason"])


def test_stats_route_per_reason_includes_wow_delta_columns():
    """The admin tile must surface per-reason WoW deltas (Task #659):
    `prev_shown`, `prev_ctr_pct`, `shown_delta`, `ctr_delta_pct`,
    and `status` so ops can pinpoint which trigger reason regressed
    instead of just seeing the overall CTR swing.
    """
    fixture_by_reason = [
        {"reason": "answer_helpful", "shown": 100, "clicked": 10, "dismissed": 0},
    ]
    fake = _fake_event_db(
        totals_shown=100, totals_clicked=10, totals_dismissed=0,
        by_reason=fixture_by_reason,
    )
    with patch.object(arp, "db", fake), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)), \
         patch.object(arp, "_ensure_review_prompt_indexes", AsyncMock()):
        stats_route = asyncio.run(arp.admin_review_prompt_stats(
            days=7, admin={"id": "admin"},
        ))
    rows = stats_route["by_reason"]
    assert len(rows) == 1
    row = rows[0]
    # The fake doesn't filter by date so the prev window mirrors the
    # current — ensures the deltas are wired through end-to-end.
    assert row["prev_shown"] == 100
    assert row["prev_clicked"] == 10
    assert row["prev_ctr_pct"] == 10.0
    assert row["shown_delta"] == 0
    assert row["ctr_delta_pct"] == 0.0
    assert row["status"] == "active"
