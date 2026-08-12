"""
Unit tests for the per-recipient email rate limiter in resend_client.py.

Verifies:
- The 10th send within the window succeeds; the 11th is blocked (returns False).
- Different recipients are counted independently (one's limit doesn't affect the other).
- Send-times older than the rate window are not counted against the limit.
- Redis path: counter increments are respected and the limit is enforced cross-pod.
- Redis path: when Redis returns count > limit the send is blocked.
- Fallback path: in-memory limiter activates when Redis is unavailable (exception).
- Fallback path: in-memory limiter activates when Redis credentials are absent.
"""

import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_rate_limit_store():
    """Clear _email_send_times before and after each test to prevent bleed."""
    import app.services.comms.resend_client as rc

    rc._email_send_times.clear()
    yield
    rc._email_send_times.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_http_success():
    """Return a mock httpx client whose .post() succeeds with 200."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()  # no-op = success
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    return mock_client


def _patch_settings_with_key(**extra):
    """Patch settings so RESEND_API_KEY is non-empty (required for _send_email to proceed).

    Pass extra kwargs to override additional settings fields (e.g. Redis creds).
    """
    return patch(
        "app.services.comms.resend_client.settings",
        RESEND_API_KEY="fake-key",
        RESEND_FROM_NAME="Test",
        RESEND_FROM_ADDRESS="noreply@example.com",
        UPSTASH_REDIS_REST_URL=None,
        UPSTASH_REDIS_REST_TOKEN=None,
        **extra,
    )


def _make_redis_pipeline_response(count: int):
    """Build a mock httpx response that mimics the Upstash pipeline reply for INCR."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value=[
            {"result": count},   # INCR reply
            {"result": 1},       # EXPIRE reply (1 = TTL was set)
        ]
    )
    return mock_response


# ---------------------------------------------------------------------------
# Test 1: 10th call succeeds, 11th is blocked (in-memory fallback path)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tenth_send_succeeds_eleventh_is_blocked():
    """
    With the rate limit at 10 per minute, calls 1–10 must succeed and the
    11th must be blocked, returning False from _send_email without reaching Resend.
    """
    import app.services.comms.resend_client as rc

    limit = rc._EMAIL_RATE_LIMIT  # 10
    recipient = "flood@example.com"

    with (
        _patch_settings_with_key(),
        patch(
            "app.services.comms.resend_client._get_client",
            return_value=_make_http_success(),
        ),
        # Prevent MongoDB writes in _record_email_failure from complicating the test
        patch(
            "app.services.comms.resend_client._record_email_failure",
            new_callable=AsyncMock,
        ),
    ):
        results = []
        for _ in range(limit + 1):  # 11 attempts
            ok = await rc._send_email(recipient, "Subject", "<p>body</p>")
            results.append(ok)

    # Calls 1–10 must succeed
    assert all(results[:limit]), (
        f"Expected all of the first {limit} sends to succeed; got {results[:limit]}"
    )
    # Call 11 must be blocked
    assert results[limit] is False, (
        f"Expected the {limit + 1}th send to be blocked (False); got {results[limit]}"
    )


# ---------------------------------------------------------------------------
# Test 2: Different recipients are counted independently
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rate_limit_is_per_recipient():
    """
    Exhausting the rate limit for recipient A must not affect recipient B.
    After A is blocked, B's first send must still succeed.
    """
    import app.services.comms.resend_client as rc

    limit = rc._EMAIL_RATE_LIMIT  # 10
    recipient_a = "alice@example.com"
    recipient_b = "bob@example.com"

    with (
        _patch_settings_with_key(),
        patch(
            "app.services.comms.resend_client._get_client",
            return_value=_make_http_success(),
        ),
        patch(
            "app.services.comms.resend_client._record_email_failure",
            new_callable=AsyncMock,
        ),
    ):
        # Exhaust the limit for recipient A
        for _ in range(limit):
            await rc._send_email(recipient_a, "Subject", "<p>body</p>")

        # A's next send must be blocked
        a_blocked = await rc._send_email(recipient_a, "Subject", "<p>body</p>")
        assert a_blocked is False, "Recipient A should be rate-limited after hitting the cap"

        # B's first send must still succeed
        b_ok = await rc._send_email(recipient_b, "Subject", "<p>body</p>")

    assert b_ok is True, (
        "Recipient B must not be affected by recipient A's rate limit"
    )


# ---------------------------------------------------------------------------
# Test 3: Send-times outside the window are not counted
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_old_send_times_outside_window_are_excluded():
    """
    If a recipient has (limit - 1) send-times that are older than the rate window,
    those must not count against the limit, so the next send must succeed.
    """
    import app.services.comms.resend_client as rc

    limit = rc._EMAIL_RATE_LIMIT       # 10
    window = rc._EMAIL_RATE_WINDOW     # 60 s
    recipient = "stale@example.com"

    now = time.time()
    # Pre-populate with (limit - 1) timestamps just outside the window
    stale_times = [now - window - 10 - i for i in range(limit - 1)]
    rc._email_send_times[recipient].extend(stale_times)

    # _check_rate_limit should prune these and allow the send
    allowed = await rc._check_rate_limit(recipient)

    assert allowed is True, (
        f"Expected _check_rate_limit to return True after pruning {limit - 1} stale "
        f"timestamps; got False (stale times were incorrectly counted)"
    )
    # After a successful check, only 1 recent entry should remain
    assert len(rc._email_send_times[recipient]) == 1, (
        f"Expected 1 entry in the rate-limit store after the call, "
        f"got {len(rc._email_send_times[recipient])}"
    )


# ---------------------------------------------------------------------------
# Test 4: Redis path — sends within the limit are allowed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_redis_path_allows_sends_within_limit():
    """
    When Redis returns a count ≤ EMAIL_RATE_LIMIT the helper must return True
    and the in-memory fallback must NOT be touched.
    """
    import app.services.comms.resend_client as rc

    recipient = "redis-ok@example.com"
    count_within_limit = rc._EMAIL_RATE_LIMIT  # exactly at the limit → still allowed

    mock_resp = _make_redis_pipeline_response(count_within_limit)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_ctx

        with patch(
            "app.services.comms.resend_client.settings",
            UPSTASH_REDIS_REST_URL="https://redis.example.upstash.io",
            UPSTASH_REDIS_REST_TOKEN="fake-token",
        ):
            result = await rc._check_rate_limit_redis(recipient, window_bucket=999)

    assert result is True, (
        f"Expected True when Redis count ({count_within_limit}) ≤ limit "
        f"({rc._EMAIL_RATE_LIMIT}); got {result}"
    )
    # In-memory store must be untouched — Redis handled it
    assert recipient not in rc._email_send_times, (
        "In-memory store should not be written when Redis is available"
    )


# ---------------------------------------------------------------------------
# Test 5: Redis path — sends exceeding the limit are blocked
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_redis_path_blocks_sends_over_limit():
    """
    When Redis returns a count > EMAIL_RATE_LIMIT the helper must return False.
    """
    import app.services.comms.resend_client as rc

    recipient = "redis-flood@example.com"
    count_over_limit = rc._EMAIL_RATE_LIMIT + 1  # one past the cap

    mock_resp = _make_redis_pipeline_response(count_over_limit)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_ctx

        with patch(
            "app.services.comms.resend_client.settings",
            UPSTASH_REDIS_REST_URL="https://redis.example.upstash.io",
            UPSTASH_REDIS_REST_TOKEN="fake-token",
        ):
            result = await rc._check_rate_limit_redis(recipient, window_bucket=999)

    assert result is False, (
        f"Expected False when Redis count ({count_over_limit}) > limit "
        f"({rc._EMAIL_RATE_LIMIT}); got {result}"
    )


# ---------------------------------------------------------------------------
# Test 6: Fallback — Redis exception causes fall-through to in-memory
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_redis_unavailable_falls_back_to_in_memory():
    """
    When the Redis call raises an exception, _check_rate_limit must fall back
    to the in-memory limiter and still enforce the cap correctly.
    """
    import app.services.comms.resend_client as rc

    limit = rc._EMAIL_RATE_LIMIT
    recipient = "fallback@example.com"

    # Pre-fill in-memory store to exactly the limit so the next call is blocked
    now = time.time()
    rc._email_send_times[recipient] = [now - 1] * limit  # limit recent entries

    with patch("httpx.AsyncClient") as mock_client_cls:
        # Make the async context manager raise on .post()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.post = AsyncMock(side_effect=Exception("Redis connection refused"))
        mock_client_cls.return_value = mock_ctx

        with patch(
            "app.services.comms.resend_client.settings",
            UPSTASH_REDIS_REST_URL="https://redis.example.upstash.io",
            UPSTASH_REDIS_REST_TOKEN="fake-token",
        ):
            result = await rc._check_rate_limit(recipient)

    assert result is False, (
        "Expected in-memory fallback to block the send when Redis is down "
        f"and the in-memory store is at the limit; got {result}"
    )


# ---------------------------------------------------------------------------
# Test 7: Fallback — no Redis credentials means in-memory is used directly
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_redis_credentials_uses_in_memory():
    """
    When UPSTASH_REDIS_REST_URL / TOKEN are absent, _check_rate_limit_redis must
    return None immediately (no HTTP call) and the caller falls back to in-memory.
    """
    import app.services.comms.resend_client as rc

    recipient = "nocreds@example.com"

    with patch(
        "app.services.comms.resend_client.settings",
        UPSTASH_REDIS_REST_URL=None,
        UPSTASH_REDIS_REST_TOKEN=None,
    ):
        # httpx should NOT be called at all
        with patch("httpx.AsyncClient") as mock_client_cls:
            result = await rc._check_rate_limit_redis(recipient, window_bucket=1)
            mock_client_cls.assert_not_called()

    assert result is None, (
        f"Expected None (signal: use in-memory fallback) when Redis creds are absent; got {result}"
    )


# ---------------------------------------------------------------------------
# Test 8: High-volume recipients are NOT evicted when the dict is pruned
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_high_volume_recipients_not_evicted_during_prune():
    """
    When _email_send_times exceeds 10 000 keys, the cleanup must evict only
    stale/inactive recipients.  A recipient with recent sends within the rate
    window must not be evicted, so its counter survives the prune and continues
    to be enforced correctly.
    """
    import app.services.comms.resend_client as rc

    window = rc._EMAIL_RATE_WINDOW  # 60 s
    limit = rc._EMAIL_RATE_LIMIT    # 10

    now = __import__("time").time()

    # Populate 10 001 stale entries (all sends outside the window)
    for i in range(10_001):
        addr = f"stale{i}@example.com"
        rc._email_send_times[addr] = [now - window - 100]

    # Add one high-volume (active) recipient sitting exactly at the rate limit
    hot = "hot@example.com"
    rc._email_send_times[hot] = [now - 1] * limit

    # Trigger the pruning logic by calling _check_rate_limit for an unrelated address.
    # Redis is disabled so we go straight to in-memory logic.
    with patch(
        "app.services.comms.resend_client.settings",
        UPSTASH_REDIS_REST_URL=None,
        UPSTASH_REDIS_REST_TOKEN=None,
    ):
        result = await rc._check_rate_limit("trigger@example.com")

    # The trigger address itself should have been allowed (its count starts at 0)
    assert result is True, f"Expected True for a fresh address after prune; got {result}"

    # The hot recipient must still be in the store — not evicted
    assert hot in rc._email_send_times, (
        "High-volume recipient was incorrectly evicted during the dict cleanup sweep"
    )

    # And its rate limit must still be enforced (all limit slots are consumed)
    with patch(
        "app.services.comms.resend_client.settings",
        UPSTASH_REDIS_REST_URL=None,
        UPSTASH_REDIS_REST_TOKEN=None,
    ):
        hot_result = await rc._check_rate_limit(hot)

    assert hot_result is False, (
        "High-volume recipient's rate limit should still be enforced after prune; "
        f"expected False but got {hot_result}"
    )
