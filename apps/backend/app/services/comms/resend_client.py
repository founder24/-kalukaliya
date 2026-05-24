import httpx
import logging

from app.config import settings

logger = logging.getLogger(__name__)

_resend_client: httpx.AsyncClient | None = None


def _get_resend_client() -> httpx.AsyncClient:
    """Lazily create a singleton async HTTP client for Resend API."""
    global _resend_client
    if _resend_client is None:
        _resend_client = httpx.AsyncClient(
            base_url="https://api.resend.com",
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )
    return _resend_client


async def send_welcome_email(email: str, name: str = None) -> bool:
    """Send welcome email to new user"""
    try:
        params = {
            "from": f"{settings.RESEND_FROM_NAME} <{settings.RESEND_FROM_ADDRESS}>",
            "to": email,
            "subject": "Welcome to Syrabit! 🎓",
            "html": f"""
            <h1>Welcome to Syrabit!</h1>
            <p>Hi {name or 'there'},</p>
            <p>Thank you for joining Syrabit - your AI-powered educational assistant for Assamese students.</p>
            <p>Get started by asking your first question!</p>
            <p>Best regards,<br>The Syrabit Team</p>
            """,
        }

        client = _get_resend_client()
        response = await client.post("/emails", json=params)
        response.raise_for_status()
        logger.info(f"Welcome email sent to {email}")
        return True

    except Exception as e:
        logger.error(f"Failed to send welcome email: {e}")
        return False


async def send_receipt_email(email: str, amount: int, event_id: str) -> bool:
    """Send payment receipt email"""
    try:
        amount_inr = amount / 100  # Convert paise to rupees

        params = {
            "from": f"{settings.RESEND_FROM_NAME} <{settings.RESEND_FROM_ADDRESS}>",
            "to": email,
            "subject": "Payment Receipt - Syrabit Pro",
            "html": f"""
            <h1>Payment Successful!</h1>
            <p>Thank you for subscribing to Syrabit Pro.</p>
            <table style="width: 100%; max-width: 400px; border-collapse: collapse;">
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd;"><strong>Amount Paid:</strong></td>
                    <td style="padding: 8px; border: 1px solid #ddd;">₹{amount_inr:.2f}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd;"><strong>Transaction ID:</strong></td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{event_id}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd;"><strong>Status:</strong></td>
                    <td style="padding: 8px; border: 1px solid #ddd;">✅ Active</td>
                </tr>
            </table>
            <p>You now have unlimited access to Syrabit Pro features!</p>
            <p>Best regards,<br>The Syrabit Team</p>
            """,
        }

        client = _get_resend_client()
        response = await client.post("/emails", json=params)
        response.raise_for_status()
        logger.info(f"Receipt email sent to {email}")
        return True

    except Exception as e:
        logger.error(f"Failed to send receipt email: {e}")
        return False


async def send_password_reset_email(email: str, reset_token: str) -> bool:
    """Send password reset email"""
    try:
        reset_link = f"https://syrabit.ai/reset-password?token={reset_token}"

        params = {
            "from": f"{settings.RESEND_FROM_NAME} <{settings.RESEND_FROM_ADDRESS}>",
            "to": email,
            "subject": "Password Reset Request - Syrabit",
            "html": f"""
            <h1>Password Reset Request</h1>
            <p>You requested to reset your password.</p>
            <p><a href="{reset_link}" style="display: inline-block; padding: 10px 20px; background-color: #007bff; color: white; text-decoration: none; border-radius: 5px;">Reset Password</a></p>
            <p>This link will expire in 1 hour.</p>
            <p>If you didn't request this, please ignore this email.</p>
            <p>Best regards,<br>The Syrabit Team</p>
            """,
        }

        client = _get_resend_client()
        response = await client.post("/emails", json=params)
        response.raise_for_status()
        logger.info(f"Password reset email sent to {email}")
        return True

    except Exception as e:
        logger.error(f"Failed to send password reset email: {e}")
        return False
