"""AI Outage Alert: notify the admin when both Sarvam and Gemini are down.

De-duplicated: at most one alert email per 10-minute window.
The alert accumulates affected users and error details during that window so a
single email summarises the full blast radius rather than sending one email per
failed request.
"""

import asyncio
import html
import logging
import time as _time
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_DEDUP_WINDOW_SECONDS = 10 * 60  # 10 minutes between alert emails

# ---------------------------------------------------------------------------
# In-memory state (per Cloud Run instance)
# Protected by _state_lock so concurrent coroutines cannot bypass dedup.
# ---------------------------------------------------------------------------
_state_lock = asyncio.Lock()
_last_alert_sent_at: float = 0.0          # epoch seconds of the last email send
_window_start: float = 0.0                # start of the current accumulation window
_affected_user_ids: set[str] = set()
_sarvam_errors: list[str] = []
_gemini_errors: list[str] = []


def _reset_window(now: float) -> None:
    """Reset accumulation state for a fresh window. Must be called under _state_lock."""
    global _window_start, _affected_user_ids, _sarvam_errors, _gemini_errors
    _window_start = now
    _affected_user_ids = set()
    _sarvam_errors = []
    _gemini_errors = []


async def record_ai_outage(
    user_id: str,
    sarvam_error: Optional[str] = None,
    gemini_error: Optional[str] = None,
) -> None:
    """Record a dual-provider outage event and send an alert if the window allows.

    Call this every time both Sarvam and Gemini fail for a single request.
    The function accumulates events; it sends at most one email per 10 minutes.
    Concurrency-safe: all state mutations and the dedup decision are protected by
    an asyncio.Lock so concurrent coroutines cannot both decide to send.

    Args:
        user_id:      The affected user's ID (used to count unique users).
        sarvam_error: String description of the Sarvam failure (truncated to 200 chars).
        gemini_error: String description of the Gemini failure (truncated to 200 chars).
    """
    global _last_alert_sent_at, _window_start, _affected_user_ids, _sarvam_errors, _gemini_errors

    now = _time.time()
    snapshot_count = 0
    snapshot_sarvam: list[str] = []
    snapshot_gemini: list[str] = []
    should_send = False

    async with _state_lock:
        # If we're outside the dedup window, reset the accumulation state.
        if now - _window_start > _DEDUP_WINDOW_SECONDS:
            _reset_window(now)

        # Accumulate this event.
        _affected_user_ids.add(user_id)
        if sarvam_error:
            _sarvam_errors.append(sarvam_error[:200])
        if gemini_error:
            _gemini_errors.append(gemini_error[:200])

        # Decide whether to send an alert.
        # Condition: no alert has been sent in the last _DEDUP_WINDOW_SECONDS.
        if now - _last_alert_sent_at < _DEDUP_WINDOW_SECONDS:
            # Already alerted in this window — just accumulate silently.
            logger.debug(
                "ai_outage_alert: skipping email (already sent in dedup window), "
                f"affected_users={len(_affected_user_ids)}"
            )
            return

        # Snapshot state for the email (sent outside the lock to avoid blocking
        # other coroutines during the HTTP call).
        snapshot_count = len(_affected_user_ids)
        snapshot_sarvam = list(_sarvam_errors)
        snapshot_gemini = list(_gemini_errors)

        # Mark as sent *before* the await so that any other coroutine waiting on
        # the lock sees the updated timestamp and skips.
        _last_alert_sent_at = now
        should_send = True

    # Send the alert outside the lock so we don't block accumulation.
    if not should_send:
        return

    sent = await _send_outage_alert(
        affected_count=snapshot_count,
        sarvam_errors=snapshot_sarvam,
        gemini_errors=snapshot_gemini,
    )
    if sent:
        logger.warning(
            f"ai_outage_alert: alert sent, affected_users={snapshot_count}"
        )
    else:
        # Reset timestamp so we retry on the next event instead of silently
        # swallowing the failure for the full dedup window.
        async with _state_lock:
            _last_alert_sent_at = 0.0


async def _send_outage_alert(
    affected_count: int,
    sarvam_errors: list[str],
    gemini_errors: list[str],
) -> bool:
    """Build and send the outage alert email to ADMIN_EMAIL.

    Returns True if the email was dispatched successfully (or if email is
    disabled — we log the alert but don't treat it as an error).
    """
    try:
        from app.config import settings
        from app.services.comms.resend_client import _send_email

        admin_email = settings.ADMIN_EMAIL
        if not admin_email:
            # No admin email configured — log prominently so the alert is visible
            # in Cloud Logging even without email.
            logger.error(
                "AI_OUTAGE_ALERT: Both AI providers are down! "
                f"affected_users={affected_count} "
                f"sarvam_errors={sarvam_errors[:3]} "
                f"gemini_errors={gemini_errors[:3]}"
            )
            return True  # logged; suppress further retries this window

        # Build deduplicated, truncated error summaries.
        unique_sarvam = list(dict.fromkeys(sarvam_errors))[:5]
        unique_gemini = list(dict.fromkeys(gemini_errors))[:5]

        sarvam_html = (
            "<ul>" + "".join(f"<li>{html.escape(e)}</li>" for e in unique_sarvam) + "</ul>"
            if unique_sarvam
            else "<p><em>No error detail captured.</em></p>"
        )
        gemini_html = (
            "<ul>" + "".join(f"<li>{html.escape(e)}</li>" for e in unique_gemini) + "</ul>"
            if unique_gemini
            else "<p><em>No error detail captured.</em></p>"
        )

        import datetime as _dt
        utc_now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        body = f"""
        <h2 style="color:#c0392b;">&#x26A0; AI Provider Outage Detected</h2>
        <p>
            Both <strong>Sarvam</strong> and <strong>Gemini</strong> are unavailable.
            Users are seeing &ldquo;Syra is resting&rdquo; and not getting responses.
        </p>

        <table style="border-collapse:collapse;width:100%;max-width:500px;">
          <tr>
            <td style="padding:8px;border:1px solid #ddd;"><strong>Detected at</strong></td>
            <td style="padding:8px;border:1px solid #ddd;">{html.escape(utc_now)}</td>
          </tr>
          <tr>
            <td style="padding:8px;border:1px solid #ddd;"><strong>Affected users (this window)</strong></td>
            <td style="padding:8px;border:1px solid #ddd;">{affected_count}</td>
          </tr>
          <tr>
            <td style="padding:8px;border:1px solid #ddd;"><strong>Providers down</strong></td>
            <td style="padding:8px;border:1px solid #ddd;">Sarvam AI, Gemini (Google)</td>
          </tr>
        </table>

        <h3>Sarvam errors</h3>
        {sarvam_html}

        <h3>Gemini errors</h3>
        {gemini_html}

        <p style="color:#666;font-size:12px;">
            This alert fires at most once per 10 minutes. Check the dead_letters
            collection in MongoDB for the full list of affected requests.
        </p>
        """

        return await _send_email(
            to=admin_email,
            subject="[Syrabit] ALERT: Both AI providers are down",
            html_body=body,
        )

    except Exception as exc:
        logger.error(f"ai_outage_alert: failed to send alert email: {exc}")
        return False
