import httpx
import logging
from urllib.parse import quote

from app.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)
_RAZORPAY_BASE_URL = "https://api.razorpay.com/v1"
_RAZORPAY_SUBSCRIPTIONS_PATH = "/subscriptions"


class PaymentNotConfiguredError(RuntimeError):
    """Raised when Razorpay credentials are missing."""

    pass


class RazorpayClient:
    """Razorpay Payment Gateway Client"""

    def __init__(self):
        self.key_id = settings.RAZORPAY_KEY_ID
        self.key_secret = settings.RAZORPAY_KEY_SECRET
        self.base_url = _RAZORPAY_BASE_URL
        self._client = httpx.AsyncClient(
            base_url=_RAZORPAY_BASE_URL,
            timeout=30.0,
            follow_redirects=False,
            auth=(self.key_id, self.key_secret)
            if self.key_id and self.key_secret
            else None,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    async def close(self):
        """Close the HTTP client (call on app shutdown)"""
        await self._client.aclose()

    async def create_subscription_order(self, user: User) -> dict:
        """Create Razorpay subscription order"""
        if not self.key_id or not self.key_secret:
            raise PaymentNotConfiguredError(
                "Payment service not configured - Razorpay credentials missing"
            )
        try:
            # The client has an immutable Razorpay HTTPS base URL and this path
            # is a module constant, so request data cannot choose the origin.
            # nosemgrep: python.fastapi.net.tainted-fastapi-http-request-httpx.tainted-fastapi-http-request-httpx
            response = await self._client.post(
                _RAZORPAY_SUBSCRIPTIONS_PATH,
                json={
                    "plan_id": settings.RAZORPAY_PLAN_ID,
                    "total_count": 12,  # 12 months
                    "customer_notify": 1,
                    "customer": {
                        "email": user.email,
                        "name": user.name or "",
                    },
                    "notes": {
                        "user_id": str(user.id),
                    },
                },
            )
            if 300 <= response.status_code < 400:
                raise RuntimeError("Payment gateway returned an unexpected redirect")
            response.raise_for_status()
            data = response.json()

            return {
                "order_id": data["id"],
                "subscription_id": data["id"],
                "amount": data["plan"]["amount"],
                "currency": data["plan"]["currency"],
            }

        except httpx.HTTPStatusError as e:
            logger.error(f"Razorpay API error: {e.response.status_code}")
            raise RuntimeError(f"Payment gateway error: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Razorpay error: {str(e)}")
            raise RuntimeError(f"Failed to create order: {e}")

    async def cancel_subscription(self, subscription_id: str) -> bool:
        """Cancel Razorpay subscription"""
        if not self.key_id or not self.key_secret:
            raise PaymentNotConfiguredError(
                "Payment service not configured - Razorpay credentials missing"
            )
        try:
            safe_subscription_id = quote(subscription_id, safe="")
            response = await self._client.delete(
                f"{_RAZORPAY_SUBSCRIPTIONS_PATH}/{safe_subscription_id}"
            )
            if 300 <= response.status_code < 400:
                raise RuntimeError("Payment gateway returned an unexpected redirect")
            response.raise_for_status()
            return True

        except Exception as e:
            logger.error(f"Failed to cancel subscription: {e}")
            raise RuntimeError(f"Cancellation failed: {e}")


# Singleton instance
razorpay_client = RazorpayClient()


async def create_subscription_order(user: User) -> dict:
    """Convenience function to create subscription order"""
    return await razorpay_client.create_subscription_order(user)


async def cancel_razorpay_subscription(subscription_id: str) -> bool:
    """Convenience function to cancel subscription"""
    return await razorpay_client.cancel_subscription(subscription_id)
