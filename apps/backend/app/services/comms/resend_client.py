import hashlib
import html
import httpx
import logging
import time as _time
from collections import defaultdict
from typing import Optional
from urllib.parse import quote as url_quote

from app.config import settings

logger = logging.getLogger(__name__)

_http_client: Optional[httpx.AsyncClient] = None

RESEND_API_URL = "https://api.resend.com/emails"

# Simple in-memory rate limiter: max 10 emails per minute per recipient
_EMAIL_RATE_LIMIT = 10
_EMAIL_RATE_WINDOW = 60  # seconds
_email_send_times: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(recipient: str) -> bool:
    """Check if sending to this recipient would exceed rate limit."""
    now = _time.time()
    # Clean old entries for this recipient
    _email_send_times[recipient] = [
        t for t in _email_send_times[recipient] if now - t < _EMAIL_RATE_WINDOW
    ]
    # Prune global dict if it grows too large to prevent unbounded memory usage
    if len(_email_send_times) > 10000:
        _email_send_times.clear()
    if len(_email_send_times[recipient]) >= _EMAIL_RATE_LIMIT:
        return False
    _email_send_times[recipient].append(now)
    return True

RESEND_API_URL = "https://api.resend.com/emails"

UNSUBSCRIBE_FOOTER = (
    '<hr style="margin: 20px 0; border: none; border-top: 1px solid #eee;">'
    '<p style="font-size: 12px; color: #666;">If you no longer wish to receive '
    'emails from us, <a href="https://syrabit.ai/profile?unsubscribe=true">'
    'unsubscribe here</a>.</p>'
)


def _get_client() -> httpx.AsyncClient:
    """Get or create the singleton async HTTP client for Resend."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


async def close_resend_client():
    """Close the Resend HTTP client. Call during application shutdown."""
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


async def _send_email(to: str, subject: str, html_body: str) -> bool:
    """Send an email via Resend API using async httpx."""
    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set - email sending disabled")
        return False

    if not _check_rate_limit(to):
        logger.warning(f"Rate limit exceeded for {to} - email not sent")
        return False

    client = _get_client()
    try:
        # Generate idempotency key to prevent duplicate sends
        idempotency_input = f"{to}:{subject}:{int(_time.time() // 60)}"
        idempotency_key = hashlib.sha256(idempotency_input.encode()).hexdigest()[:32]

        response = await client.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json={
                "from": f"{settings.RESEND_FROM_NAME} <{settings.RESEND_FROM_ADDRESS}>",
                "to": to,
                "subject": subject,
                "html": html_body,
                "headers": {
                    "List-Unsubscribe": "<https://syrabit.ai/profile?unsubscribe=true>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                },
            },
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to}: {e}")
        return False


async def send_welcome_email(email: str, name: str = None) -> bool:
    """Send welcome email to new user."""
    safe_name = html.escape(name) if name else "there"
    email_html = f"""
    <h1>Welcome to Syrabit!</h1>
    <p>Hi {safe_name},</p>
    <p>Thank you for joining Syrabit - your AI-powered educational assistant for Assamese students.</p>
    <p>Get started by asking your first question!</p>
    <p>Best regards,<br>The Syrabit Team</p>
    {UNSUBSCRIBE_FOOTER}
    """
    result = await _send_email(email, "Welcome to Syrabit! \U0001f393", email_html)
    if result:
        logger.info(f"Welcome email sent to {email}")
    return result


async def send_receipt_email(email: str, amount: int, event_id: str) -> bool:
    """Send payment receipt email."""
    amount_inr = amount / 100  # Convert paise to rupees
    safe_event_id = html.escape(event_id)
    email_html = f"""
    <h1>Payment Successful!</h1>
    <p>Thank you for subscribing to Syrabit Pro.</p>
    <table style="width: 100%; max-width: 400px; border-collapse: collapse;">
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Amount Paid:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">\u20b9{amount_inr:.2f}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Transaction ID:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{safe_event_id}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Status:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">\u2705 Active</td>
        </tr>
    </table>
    <p>You now have unlimited access to Syrabit Pro features!</p>
    <p>Best regards,<br>The Syrabit Team</p>
    {UNSUBSCRIBE_FOOTER}
    """
    result = await _send_email(email, "Payment Receipt - Syrabit Pro", email_html)
    if result:
        logger.info(f"Receipt email sent to {email}")
    return result


async def send_password_reset_email(email: str, reset_token: str) -> bool:
    """Send password reset email."""
    safe_token = url_quote(reset_token, safe='')
    reset_link = f"https://syrabit.ai/reset-password?token={safe_token}"
    email_html = f"""
    <h1>Password Reset Request</h1>
    <p>You requested to reset your password.</p>
    <p><a href="{reset_link}" style="display: inline-block; padding: 10px 20px; background-color: #007bff; color: white; text-decoration: none; border-radius: 5px;">Reset Password</a></p>
    <p>This link will expire in 1 hour.</p>
    <p>If you didn't request this, please ignore this email.</p>
    <p>Best regards,<br>The Syrabit Team</p>
    {UNSUBSCRIBE_FOOTER}
    """
    result = await _send_email(email, "Password Reset Request - Syrabit", email_html)
    if result:
        logger.info(f"Password reset email sent to {email}")
    return result
