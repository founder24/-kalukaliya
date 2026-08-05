"""
Unit tests for the per-recipient email rate limiter in resend_client.py.

Verifies:
- The 10th send within the window succeeds; the 11th is blocked (returns False).
- Different recipients are counted independently (one's limit doesn't affect the other).
- Send-times older than the rate window are not counted against the limit.
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


def _patch_settings_with_key():
    """Patch settings so RESEND_API_KEY is non-empty (required for _send_email to proceed)."""
    return patch(
        "app.services.comms.resend_client.settings",
        RESEND_API_KEY="fake-key",
        RESEND_FROM_NAME="Test",
        RESEND_FROM_ADDRESS="noreply@example.com",
    )


# ---------------------------------------------------------------------------
# Test 1: 10th call succeeds, 11th is blocked
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
    allowed = rc._check_rate_limit(recipient)

    assert allowed is True, (
        f"Expected _check_rate_limit to return True after pruning {limit - 1} stale "
        f"timestamps; got False (stale times were incorrectly counted)"
    )
    # After a successful check, only 1 recent entry should remain
    assert len(rc._email_send_times[recipient]) == 1, (
        f"Expected 1 entry in the rate-limit store after the call, "
        f"got {len(rc._email_send_times[recipient])}"
    )
