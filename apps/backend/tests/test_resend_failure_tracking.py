"""
Unit tests for email failure tracking in resend_client.py.

Verifies:
- get_email_failures_last_hour() returns the correct count after N failures
- EMAIL_DELIVERY_FAILURE_ALERT is logged once the threshold (5) is crossed
- Old timestamps (>1 hour) are excluded from the count
- Both the in-memory fallback path (MongoDB unavailable) and the MongoDB path work
"""

import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mongo_unavailable():
    """Patch get_mongo_client at the source to raise, forcing the in-memory fallback."""
    return patch(
        "app.db.mongo.get_mongo_client",
        side_effect=Exception("MongoDB unavailable"),
    )


def _make_mongo_with_count(count: int):
    """
    Patch get_mongo_client so count_documents returns *count* and
    insert_one succeeds silently.
    """
    mock_coll = MagicMock()
    mock_coll.count_documents = AsyncMock(return_value=count)
    mock_coll.insert_one = AsyncMock()
    mock_db = MagicMock()
    mock_db.email_failure_events = mock_coll
    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)
    return patch(
        "app.db.mongo.get_mongo_client",
        return_value=mock_client,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_in_memory_store():
    """
    Clear the module-level in-memory failure list, the per-recipient rate-limit
    dict, and the alert cooldown timestamp before and after each test so tests
    don't bleed into each other.
    """
    import app.services.comms.resend_client as rc
    rc._email_failure_timestamps.clear()
    rc._email_send_times.clear()
    rc._last_alert_time = 0.0
    yield
    rc._email_failure_timestamps.clear()
    rc._email_send_times.clear()
    rc._last_alert_time = 0.0


# ---------------------------------------------------------------------------
# Tests: in-memory fallback path (MongoDB unavailable)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_failure_count_correct_after_n_failures_inmemory():
    """
    After N consecutive _send_email failures with MongoDB unavailable,
    get_email_failures_last_hour() returns N via the in-memory fallback.
    """
    import app.services.comms.resend_client as rc
    from app.config import settings

    N = 3
    original_key = settings.RESEND_API_KEY
    settings.RESEND_API_KEY = "fake-key"

    try:
        with (
            _make_mongo_unavailable(),
            patch("app.services.comms.resend_client._get_client") as mock_get_client,
        ):
            mock_httpx = MagicMock()
            mock_httpx.post = AsyncMock(side_effect=Exception("401 Unauthorized"))
            mock_get_client.return_value = mock_httpx

            for _ in range(N):
                await rc._send_email("test@example.com", "Subject", "<p>body</p>")
    finally:
        settings.RESEND_API_KEY = original_key

    # Now check count with MongoDB still unavailable so we get in-memory result
    with _make_mongo_unavailable():
        count = await rc.get_email_failures_last_hour()

    assert count == N, f"Expected {N} failures, got {count}"


@pytest.mark.anyio
async def test_alert_logged_when_threshold_crossed_inmemory(caplog):
    """
    After _EMAIL_ALERT_THRESHOLD failures with MongoDB unavailable, an ERROR
    log containing 'EMAIL_DELIVERY_FAILURE_ALERT' must be emitted.
    Below the threshold no such log must appear.
    """
    import logging
    import app.services.comms.resend_client as rc
    from app.config import settings

    threshold = rc._EMAIL_ALERT_THRESHOLD  # 5
    original_key = settings.RESEND_API_KEY
    settings.RESEND_API_KEY = "fake-key"

    try:
        with (
            _make_mongo_unavailable(),
            patch("app.services.comms.resend_client._get_client") as mock_get_client,
            caplog.at_level(logging.ERROR, logger="app.services.comms.resend_client"),
        ):
            mock_httpx = MagicMock()
            mock_httpx.post = AsyncMock(side_effect=Exception("401 Unauthorized"))
            mock_get_client.return_value = mock_httpx

            # Send threshold - 1 failures; alert must NOT fire yet
            for _ in range(threshold - 1):
                await rc._send_email("test@example.com", "Subject", "<p>body</p>")

            alert_logs_before = [
                r for r in caplog.records
                if "EMAIL_DELIVERY_FAILURE_ALERT" in r.message and r.levelno == logging.ERROR
            ]
            assert not alert_logs_before, (
                f"Alert fired too early after {threshold - 1} failures"
            )

            # One more failure crosses the threshold — alert must fire now
            await rc._send_email("test@example.com", "Subject", "<p>body</p>")
    finally:
        settings.RESEND_API_KEY = original_key

    alert_logs_after = [
        r for r in caplog.records
        if "EMAIL_DELIVERY_FAILURE_ALERT" in r.message and r.levelno == logging.ERROR
    ]
    assert alert_logs_after, (
        f"EMAIL_DELIVERY_FAILURE_ALERT not logged after {threshold} failures"
    )


@pytest.mark.anyio
async def test_old_timestamps_excluded_from_count_inmemory():
    """
    Failures older than 1 hour must not be counted.
    Manually inject old timestamps into the in-memory store and verify they
    are excluded from get_email_failures_last_hour().
    """
    import app.services.comms.resend_client as rc

    now = time.time()
    old_ts = now - rc._EMAIL_FAILURE_WINDOW - 10   # 10 s past the 1-hour cutoff
    recent_ts = now - 30                             # 30 s ago — within the window

    rc._email_failure_timestamps.extend([old_ts, old_ts, recent_ts])

    with _make_mongo_unavailable():
        count = await rc.get_email_failures_last_hour()

    assert count == 1, (
        f"Expected 1 recent failure, got {count} "
        "(old timestamps should be excluded)"
    )


# ---------------------------------------------------------------------------
# Tests: MongoDB path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_failure_count_correct_via_mongodb():
    """
    When MongoDB is available, get_email_failures_last_hour() returns the count
    from count_documents (not the in-memory list).
    """
    import app.services.comms.resend_client as rc

    expected = 7

    with _make_mongo_with_count(expected):
        count = await rc.get_email_failures_last_hour()

    assert count == expected, f"Expected {expected} from MongoDB, got {count}"


@pytest.mark.anyio
async def test_alert_logged_when_threshold_crossed_mongodb(caplog):
    """
    When MongoDB returns a count >= _EMAIL_ALERT_THRESHOLD, _record_email_failure
    must emit the EMAIL_DELIVERY_FAILURE_ALERT error log.
    """
    import logging
    import app.services.comms.resend_client as rc

    threshold = rc._EMAIL_ALERT_THRESHOLD

    # count_documents returns threshold (exactly at the boundary)
    with (
        _make_mongo_with_count(threshold),
        caplog.at_level(logging.ERROR, logger="app.services.comms.resend_client"),
    ):
        await rc._record_email_failure()

    alert_logs = [
        r for r in caplog.records
        if "EMAIL_DELIVERY_FAILURE_ALERT" in r.message and r.levelno == logging.ERROR
    ]
    assert alert_logs, (
        f"EMAIL_DELIVERY_FAILURE_ALERT not logged when MongoDB count = {threshold}"
    )


@pytest.mark.anyio
async def test_no_alert_below_threshold_mongodb(caplog):
    """
    When MongoDB count is below _EMAIL_ALERT_THRESHOLD, no alert must be logged.
    """
    import logging
    import app.services.comms.resend_client as rc

    threshold = rc._EMAIL_ALERT_THRESHOLD

    with (
        _make_mongo_with_count(threshold - 1),
        caplog.at_level(logging.ERROR, logger="app.services.comms.resend_client"),
    ):
        await rc._record_email_failure()

    alert_logs = [
        r for r in caplog.records
        if "EMAIL_DELIVERY_FAILURE_ALERT" in r.message and r.levelno == logging.ERROR
    ]
    assert not alert_logs, (
        f"Alert must not fire when count ({threshold - 1}) < threshold ({threshold})"
    )


# ---------------------------------------------------------------------------
# Tests: MongoDB unavailable → in-memory fallback for get_email_failures_last_hour
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_failures_falls_back_to_inmemory_when_mongo_down():
    """
    When MongoDB raises, get_email_failures_last_hour must fall back to the
    in-memory list and return the correct count.
    """
    import app.services.comms.resend_client as rc

    now = time.time()
    rc._email_failure_timestamps.extend([now - 10, now - 20, now - 30])

    with _make_mongo_unavailable():
        count = await rc.get_email_failures_last_hour()

    assert count == 3, f"Expected 3 from in-memory fallback, got {count}"


# ---------------------------------------------------------------------------
# Tests: alert cooldown — fires once, then suppressed until window resets
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_alert_fires_exactly_once_for_many_failures_above_threshold(caplog):
    """
    When N > _EMAIL_ALERT_THRESHOLD failures occur in quick succession,
    EMAIL_DELIVERY_FAILURE_ALERT must be logged exactly once (not N-threshold+1
    times), because the cooldown suppresses repeat alerts within the same window.
    """
    import logging
    import app.services.comms.resend_client as rc
    from app.config import settings

    threshold = rc._EMAIL_ALERT_THRESHOLD  # 5
    N = threshold + 5  # well above threshold
    original_key = settings.RESEND_API_KEY
    settings.RESEND_API_KEY = "fake-key"

    try:
        with (
            _make_mongo_unavailable(),
            patch("app.services.comms.resend_client._get_client") as mock_get_client,
            caplog.at_level(logging.ERROR, logger="app.services.comms.resend_client"),
        ):
            mock_httpx = MagicMock()
            mock_httpx.post = AsyncMock(side_effect=Exception("503 Service Unavailable"))
            mock_get_client.return_value = mock_httpx

            for _ in range(N):
                await rc._send_email("test@example.com", "Subject", "<p>body</p>")
    finally:
        settings.RESEND_API_KEY = original_key

    alert_logs = [
        r for r in caplog.records
        if "EMAIL_DELIVERY_FAILURE_ALERT" in r.message and r.levelno == logging.ERROR
    ]
    assert len(alert_logs) == 1, (
        f"Expected exactly 1 alert log for {N} failures, got {len(alert_logs)}"
    )


@pytest.mark.anyio
async def test_alert_fires_again_after_cooldown_expires(caplog):
    """
    After the cooldown window passes, a new alert must be emitted the next time
    the threshold is crossed.
    """
    import logging
    import app.services.comms.resend_client as rc

    threshold = rc._EMAIL_ALERT_THRESHOLD

    # Simulate that an alert was already emitted just over one cooldown ago
    rc._last_alert_time = time.time() - rc._EMAIL_ALERT_COOLDOWN - 1

    with (
        _make_mongo_with_count(threshold),
        caplog.at_level(logging.ERROR, logger="app.services.comms.resend_client"),
    ):
        await rc._record_email_failure()

    alert_logs = [
        r for r in caplog.records
        if "EMAIL_DELIVERY_FAILURE_ALERT" in r.message and r.levelno == logging.ERROR
    ]
    assert len(alert_logs) == 1, (
        "Expected exactly 1 alert after cooldown expired, "
        f"got {len(alert_logs)}"
    )


@pytest.mark.anyio
async def test_alert_suppressed_within_cooldown_window(caplog):
    """
    When an alert was already emitted recently (within the cooldown window),
    subsequent failures above the threshold must NOT emit another alert.
    """
    import logging
    import app.services.comms.resend_client as rc

    threshold = rc._EMAIL_ALERT_THRESHOLD

    # Simulate that an alert fired just 60 seconds ago (well within the 1-hour cooldown)
    rc._last_alert_time = time.time() - 60

    with (
        _make_mongo_with_count(threshold + 3),
        caplog.at_level(logging.ERROR, logger="app.services.comms.resend_client"),
    ):
        await rc._record_email_failure()

    alert_logs = [
        r for r in caplog.records
        if "EMAIL_DELIVERY_FAILURE_ALERT" in r.message and r.levelno == logging.ERROR
    ]
    assert len(alert_logs) == 0, (
        f"Alert must be suppressed within the cooldown window, got {len(alert_logs)}"
    )
