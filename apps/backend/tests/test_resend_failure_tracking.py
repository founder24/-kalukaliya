"""
Unit tests for email failure tracking in resend_client.py.

Verifies:
- get_email_failures_last_hour() returns the correct count after N failures
- EMAIL_DELIVERY_FAILURE_ALERT is logged once the threshold (5) is crossed
- Old timestamps (>1 hour) are excluded from the count
- Both the in-memory fallback path (MongoDB unavailable) and the MongoDB path work
- The alert cooldown is shared across pods via MongoDB (email_alert_state collection)
"""

import time
import pytest
from typing import Optional
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


def _make_alert_update_result(claimed: bool):
    """Return a mock UpdateResult that represents winning or losing the atomic claim."""
    r = MagicMock()
    if claimed:
        r.matched_count = 1   # existing doc matched and updated → won
        r.upserted_id = None
    else:
        r.matched_count = 0   # filter didn't match and no upsert → lost
        r.upserted_id = None
    return r


def _make_mongo_with_count(count: int, alert_claimed: bool = True):
    """
    Patch get_mongo_client so:
    - email_failure_events.count_documents returns *count* and insert_one succeeds
    - email_alert_state.update_one returns a "won" result when alert_claimed=True,
      or a "lost" result (matched_count=0, upserted_id=None) when alert_claimed=False,
      simulating the atomic conditional-upsert claim.
    """
    # email_failure_events collection
    mock_failure_coll = MagicMock()
    mock_failure_coll.count_documents = AsyncMock(return_value=count)
    mock_failure_coll.insert_one = AsyncMock()

    # email_alert_state collection — atomic upsert claim
    mock_alert_coll = MagicMock()
    mock_alert_coll.update_one = AsyncMock(
        return_value=_make_alert_update_result(alert_claimed)
    )

    mock_db = MagicMock()
    mock_db.email_failure_events = mock_failure_coll
    mock_db.email_alert_state = mock_alert_coll

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
    When the MongoDB atomic claim succeeds (cooldown expired), a new alert must
    be emitted the next time the threshold is crossed.
    """
    import logging
    import app.services.comms.resend_client as rc

    threshold = rc._EMAIL_ALERT_THRESHOLD

    # alert_claimed=True → the conditional upsert matched (old ts replaced) → fires
    with (
        _make_mongo_with_count(threshold, alert_claimed=True),
        caplog.at_level(logging.ERROR, logger="app.services.comms.resend_client"),
    ):
        await rc._record_email_failure()

    alert_logs = [
        r for r in caplog.records
        if "EMAIL_DELIVERY_FAILURE_ALERT" in r.message and r.levelno == logging.ERROR
    ]
    assert len(alert_logs) == 1, (
        "Expected exactly 1 alert when MongoDB claim is won, "
        f"got {len(alert_logs)}"
    )


@pytest.mark.anyio
async def test_alert_suppressed_within_cooldown_window(caplog):
    """
    When the MongoDB atomic claim fails (another pod holds an active cooldown),
    the current pod must NOT emit another alert.
    """
    import logging
    import app.services.comms.resend_client as rc

    threshold = rc._EMAIL_ALERT_THRESHOLD

    # alert_claimed=False → the conditional upsert filter didn't match (fresh ts in DB)
    # → another pod already fired within this window → suppress
    with (
        _make_mongo_with_count(threshold + 3, alert_claimed=False),
        caplog.at_level(logging.ERROR, logger="app.services.comms.resend_client"),
    ):
        await rc._record_email_failure()

    alert_logs = [
        r for r in caplog.records
        if "EMAIL_DELIVERY_FAILURE_ALERT" in r.message and r.levelno == logging.ERROR
    ]
    assert len(alert_logs) == 0, (
        f"Alert must be suppressed when MongoDB claim is lost, got {len(alert_logs)}"
    )


# ---------------------------------------------------------------------------
# Tests: shared (cross-pod) cooldown via MongoDB email_alert_state atomic claim
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_alert_suppressed_when_mongo_shows_recent_alert(caplog):
    """
    When the MongoDB conditional upsert returns 'lost' (another pod recently
    updated the cooldown timestamp), the current pod must NOT fire even if its
    own in-memory _last_alert_time is 0.
    """
    import logging
    import app.services.comms.resend_client as rc

    threshold = rc._EMAIL_ALERT_THRESHOLD
    # In-memory says no prior alert (default 0.0 from fixture)
    assert rc._last_alert_time == 0.0

    # alert_claimed=False → the atomic upsert filter didn't match (DB has a fresh ts)
    with (
        _make_mongo_with_count(threshold + 2, alert_claimed=False),
        caplog.at_level(logging.ERROR, logger="app.services.comms.resend_client"),
    ):
        await rc._record_email_failure()

    alert_logs = [
        r for r in caplog.records
        if "EMAIL_DELIVERY_FAILURE_ALERT" in r.message and r.levelno == logging.ERROR
    ]
    assert len(alert_logs) == 0, (
        "Alert must be suppressed when MongoDB claim is lost (another pod owns it); "
        f"got {len(alert_logs)} alert(s)"
    )


@pytest.mark.anyio
async def test_alert_fires_when_mongo_shows_expired_alert(caplog):
    """
    When the MongoDB conditional upsert wins (stored timestamp is past the cutoff
    or absent), the current pod should fire a new alert.
    """
    import logging
    import app.services.comms.resend_client as rc

    threshold = rc._EMAIL_ALERT_THRESHOLD

    # alert_claimed=True → the atomic upsert matched (old ts replaced) → fires
    with (
        _make_mongo_with_count(threshold, alert_claimed=True),
        caplog.at_level(logging.ERROR, logger="app.services.comms.resend_client"),
    ):
        await rc._record_email_failure()

    alert_logs = [
        r for r in caplog.records
        if "EMAIL_DELIVERY_FAILURE_ALERT" in r.message and r.levelno == logging.ERROR
    ]
    assert len(alert_logs) == 1, (
        f"Expected 1 alert when MongoDB claim is won (expired ts), got {len(alert_logs)}"
    )


@pytest.mark.anyio
async def test_alert_fires_when_mongo_alert_state_write_fails(caplog):
    """
    Fail-open: when the MongoDB atomic upsert raises a generic exception (real
    outage, not a lost-claim DuplicateKeyError), the alert must still be emitted
    using the in-memory cooldown so a DB outage doesn't silently suppress alerts.
    """
    import logging
    import app.services.comms.resend_client as rc

    threshold = rc._EMAIL_ALERT_THRESHOLD

    # email_failure_events works fine; email_alert_state.update_one raises
    mock_failure_coll = MagicMock()
    mock_failure_coll.count_documents = AsyncMock(return_value=threshold)
    mock_failure_coll.insert_one = AsyncMock()

    mock_alert_coll = MagicMock()
    mock_alert_coll.update_one = AsyncMock(side_effect=Exception("network timeout"))

    mock_db = MagicMock()
    mock_db.email_failure_events = mock_failure_coll
    mock_db.email_alert_state = mock_alert_coll

    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)

    # _last_alert_time is 0.0 (fixture reset) → in-memory cooldown is expired → fires
    with (
        patch("app.db.mongo.get_mongo_client", return_value=mock_client),
        caplog.at_level(logging.ERROR, logger="app.services.comms.resend_client"),
    ):
        await rc._record_email_failure()

    alert_logs = [
        r for r in caplog.records
        if "EMAIL_DELIVERY_FAILURE_ALERT" in r.message and r.levelno == logging.ERROR
    ]
    assert len(alert_logs) == 1, (
        f"Alert must fire when email_alert_state update_one raises (fail-open), got {len(alert_logs)}"
    )


@pytest.mark.anyio
async def test_exactly_one_pod_fires_alert_when_two_claim_concurrently(caplog):
    """
    Simulate two pods calling _record_email_failure when the threshold is met.
    Pod A wins the atomic MongoDB claim; Pod B's filter doesn't match (lost).
    Exactly one EMAIL_DELIVERY_FAILURE_ALERT must be emitted across both calls.
    """
    import logging
    import app.services.comms.resend_client as rc

    threshold = rc._EMAIL_ALERT_THRESHOLD

    # Pod A wins: upsert creates/updates the doc → upserted_id is truthy
    win_result = MagicMock()
    win_result.matched_count = 0
    win_result.upserted_id = "oid-pod-a"  # truthy → won

    # Pod B loses: filter didn't match (DB now has a fresh ts from Pod A)
    lose_result = MagicMock()
    lose_result.matched_count = 0
    lose_result.upserted_id = None  # → lost

    alert_call_index = 0

    async def alternating_update_one(*args, **kwargs):
        nonlocal alert_call_index
        alert_call_index += 1
        return win_result if alert_call_index == 1 else lose_result

    mock_failure_coll = MagicMock()
    mock_failure_coll.count_documents = AsyncMock(return_value=threshold)
    mock_failure_coll.insert_one = AsyncMock()

    mock_alert_coll = MagicMock()
    mock_alert_coll.update_one = alternating_update_one

    mock_db = MagicMock()
    mock_db.email_failure_events = mock_failure_coll
    mock_db.email_alert_state = mock_alert_coll

    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)

    with (
        patch("app.db.mongo.get_mongo_client", return_value=mock_client),
        caplog.at_level(logging.ERROR, logger="app.services.comms.resend_client"),
    ):
        await rc._record_email_failure()  # Pod A — wins the claim
        await rc._record_email_failure()  # Pod B — loses the claim

    alert_logs = [
        r for r in caplog.records
        if "EMAIL_DELIVERY_FAILURE_ALERT" in r.message and r.levelno == logging.ERROR
    ]
    assert len(alert_logs) == 1, (
        f"Expected exactly 1 alert when two pods race for the claim; got {len(alert_logs)}"
    )


# ---------------------------------------------------------------------------
# Tests: cooldown resets correctly after 1-hour window (long outage visibility)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_alert_refires_after_cooldown_expires_inmemory(caplog):
    """
    After the 1-hour cooldown window elapses, a new wave of failures above the
    threshold must re-fire the alert so on-call staff are reminded the outage
    persists.

    Approach: set _last_alert_time to more than _EMAIL_ALERT_COOLDOWN seconds ago
    (simulating one hour passing), pre-fill the in-memory failure list up to
    threshold - 1, then call _record_email_failure() once more to cross the
    threshold with MongoDB unavailable.  The in-memory cooldown path must detect
    the expired window and emit a second EMAIL_DELIVERY_FAILURE_ALERT.
    """
    import logging
    import app.services.comms.resend_client as rc

    threshold = rc._EMAIL_ALERT_THRESHOLD
    now = time.time()

    # Simulate: the previous alert fired more than one cooldown window ago
    rc._last_alert_time = now - rc._EMAIL_ALERT_COOLDOWN - 10

    # Pre-fill in-memory store with (threshold - 1) recent failures so the next
    # _record_email_failure call crosses the threshold
    for _ in range(threshold - 1):
        rc._email_failure_timestamps.append(now - 30)

    with (
        _make_mongo_unavailable(),
        caplog.at_level(logging.ERROR, logger="app.services.comms.resend_client"),
    ):
        # This call appends the threshold-th failure and should re-fire the alert
        await rc._record_email_failure()

    alert_logs = [
        r for r in caplog.records
        if "EMAIL_DELIVERY_FAILURE_ALERT" in r.message and r.levelno == logging.ERROR
    ]
    assert len(alert_logs) == 1, (
        "Expected alert to re-fire once the 1-hour cooldown expires; "
        f"got {len(alert_logs)} alert(s)"
    )


@pytest.mark.anyio
async def test_alert_does_not_refire_when_count_below_threshold_after_ttl_reset(caplog):
    """
    After the 1-hour MongoDB TTL window, old failure documents expire and
    count_documents drops below the threshold.  Even though the alert cooldown
    has also expired, the alert must NOT re-fire — it should only re-trigger
    when there are genuinely new failures that push the count back above the
    threshold.

    Approach: expire the cooldown via _last_alert_time, then mock MongoDB to
    return a count of threshold - 1 (old docs gone, not enough new ones yet).
    No EMAIL_DELIVERY_FAILURE_ALERT should be emitted.
    """
    import logging
    import app.services.comms.resend_client as rc

    threshold = rc._EMAIL_ALERT_THRESHOLD
    now = time.time()

    # Cooldown has expired — more than 1 hour since the last alert
    rc._last_alert_time = now - rc._EMAIL_ALERT_COOLDOWN - 10

    # MongoDB shows count just below threshold: old docs expired, no new failures yet
    below_threshold_count = threshold - 1

    with (
        _make_mongo_with_count(below_threshold_count),
        caplog.at_level(logging.ERROR, logger="app.services.comms.resend_client"),
    ):
        await rc._record_email_failure()

    alert_logs = [
        r for r in caplog.records
        if "EMAIL_DELIVERY_FAILURE_ALERT" in r.message and r.levelno == logging.ERROR
    ]
    assert len(alert_logs) == 0, (
        "Alert must NOT re-fire when MongoDB count is below threshold after "
        f"TTL reset; got {len(alert_logs)} alert(s)"
    )
