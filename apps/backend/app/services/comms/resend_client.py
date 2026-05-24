import httpx
import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

_http_client: Optional[httpx.AsyncClient] = None

RESEND_API_URL = "https://api.resend.com/emails"


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


async def _send_email(to: str, subject: str, html: str) -> bool:
    """Send an email via Resend API using async httpx."""
    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set - email sending disabled")
        return False

    client = _get_client()
    try:
        response = await client.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": f"{settings.RESEND_FROM_NAME} <{settings.RESEND_FROM_ADDRESS}>",
                "to": to,
                "subject": subject,
                "html": html,
            },
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to}: {e}")
        return False


async def send_welcome_email(email: str, name: str = None) -> bool:
    """Send welcome email to new user."""
    html = f"""
    <h1>Welcome to Syrabit!</h1>
    <p>Hi {name or "there"},</p>
    <p>Thank you for joining Syrabit - your AI-powered educational assistant for Assamese students.</p>
    <p>Get started by asking your first question!</p>
    <p>Best regards,<br>The Syrabit Team</p>
    """
    result = await _send_email(email, "Welcome to Syrabit! \U0001f393", html)
    if result:
        logger.info(f"Welcome email sent to {email}")
    return result


async def send_receipt_email(email: str, amount: int, event_id: str) -> bool:
    """Send payment receipt email."""
    amount_inr = amount / 100  # Convert paise to rupees
    html = f"""
    <h1>Payment Successful!</h1>
    <p>Thank you for subscribing to Syrabit Pro.</p>
    <table style="width: 100%; max-width: 400px; border-collapse: collapse;">
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Amount Paid:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">\u20b9{amount_inr:.2f}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Transaction ID:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{event_id}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Status:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">\u2705 Active</td>
        </tr>
    </table>
    <p>You now have unlimited access to Syrabit Pro features!</p>
    <p>Best regards,<br>The Syrabit Team</p>
    """
    result = await _send_email(email, "Payment Receipt - Syrabit Pro", html)
    if result:
        logger.info(f"Receipt email sent to {email}")
    return result


async def send_password_reset_email(email: str, reset_token: str) -> bool:
    """Send password reset email."""
    reset_link = f"https://syrabit.ai/reset-password?token={reset_token}"
    html = f"""
    <h1>Password Reset Request</h1>
    <p>You requested to reset your password.</p>
    <p><a href="{reset_link}" style="display: inline-block; padding: 10px 20px; background-color: #007bff; color: white; text-decoration: none; border-radius: 5px;">Reset Password</a></p>
    <p>This link will expire in 1 hour.</p>
    <p>If you didn't request this, please ignore this email.</p>
    <p>Best regards,<br>The Syrabit Team</p>
    """
    result = await _send_email(email, "Password Reset Request - Syrabit", html)
    if result:
        logger.info(f"Password reset email sent to {email}")
    return result
