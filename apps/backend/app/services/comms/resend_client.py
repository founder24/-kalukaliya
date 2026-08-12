import hashlib
import html
import httpx
import logging
import time as _time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote as url_quote

from app.config import settings

logger = logging.getLogger(__name__)

_http_client: Optional[httpx.AsyncClient] = None

RESEND_API_URL = "https://api.resend.com/emails"

# Per-recipient email rate limiter: max 10 emails per minute per recipient.
# Primary store: Upstash Redis REST API — shared across all Cloud Run pods so the
# cap is enforced fleet-wide (fixes HF-073).
# Fallback: in-memory dict used when Redis credentials are absent or unreachable.
_EMAIL_RATE_LIMIT = 10
_EMAIL_RATE_WINDOW = 60  # seconds
_email_send_times: dict[str, list[float]] = defaultdict(list)


def get_email_rate_limiter_mode() -> str:
    """Return 'redis' if Upstash credentials are configured, else 'in_memory'.

    Used by the health check to surface whether the fleet-wide cap is active.
    Credentials being present does not guarantee Redis is reachable — the code
    always falls back to in-memory on transient errors.
    """
    if settings.UPSTASH_REDIS_REST_URL and settings.UPSTASH_REDIS_REST_TOKEN:
        return "redis"
    return "in_memory"

# ---------------------------------------------------------------------------
# Email failure tracking
# Primary store: MongoDB `email_failure_events` collection (survives restarts,
# aggregates across pods). TTL index on `ts` auto-expires documents after 1 hour.
# Fallback: in-memory list used when MongoDB is unavailable.
# ---------------------------------------------------------------------------
_EMAIL_FAILURE_WINDOW = 3600  # seconds (1 hour)
_EMAIL_ALERT_THRESHOLD = 5     # emit ERROR alert after this many failures/hour
_EMAIL_ALERT_COOLDOWN = 3600   # seconds — suppress repeat alerts within this window
_email_failure_timestamps: list[float] = []  # in-memory fallback
_last_alert_time: float = 0.0  # in-memory fallback for the most-recent alert timestamp

# Singleton document id used in the email_alert_state collection
_ALERT_STATE_DOC_ID = "email_alert"


async def _claim_alert_cooldown_in_mongo(now: float, cutoff: float) -> Optional[bool]:
    """Atomically claim the alert cooldown slot in MongoDB.

    Uses a single conditional upsert so only one pod across the fleet wins the
    claim within a given cooldown window, eliminating the read-then-write race.

    The filter matches only when the stored timestamp is absent or older than
    *cutoff*.  If the filter doesn't match (another pod recently won the claim),
    the upsert would try to insert a document with the same ``_id``, which
    MongoDB rejects with ``DuplicateKeyError`` — we treat that as a lost claim.

    Returns:
        True  — this pod won the claim and should fire the alert.
        False — another pod holds an active cooldown; stay silent.
        None  — MongoDB is unavailable; caller must fall back to in-memory state.
    """
    try:
        from pymongo.errors import DuplicateKeyError
        from app.db.mongo import get_mongo_client
        from app.config import settings as _settings

        client = get_mongo_client()
        db = client[_settings.MONGODB_DB_NAME]
        try:
            result = await db.email_alert_state.update_one(
                {
                    "_id": _ALERT_STATE_DOC_ID,
                    "$or": [
                        {"last_alert_ts": {"$exists": False}},
                        {"last_alert_ts": {"$lt": cutoff}},
                    ],
                },
                {"$set": {"last_alert_ts": now}},
                upsert=True,
            )
            # Won if an existing doc was updated OR a new doc was upserted
            return result.matched_count > 0 or result.upserted_id is not None
        except DuplicateKeyError:
            # Another pod raced to upsert the document — we lost the claim
            return False
    except Exception:
        # Real MongoDB outage — signal caller to use in-memory fallback
        return None


async def _record_email_failure() -> None:
    """Record a send failure in MongoDB (with in-memory fallback) and alert if threshold exceeded."""
    global _last_alert_time
    now = _time.time()

    # Always update the in-memory list so the fallback path stays accurate
    _email_failure_timestamps.append(now)
    cutoff = now - _EMAIL_FAILURE_WINDOW
    while _email_failure_timestamps and _email_failure_timestamps[0] < cutoff:
        _email_failure_timestamps.pop(0)

    # Attempt to persist to MongoDB so failures survive restarts and aggregate
    # across multiple Cloud Run instances.
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings as _settings

        client = get_mongo_client()
        db = client[_settings.MONGODB_DB_NAME]
        await db.email_failure_events.insert_one(
            {"ts": datetime.now(timezone.utc)}
        )
    except Exception as exc:
        # MongoDB unavailable — the in-memory list already recorded this event.
        logger.debug(f"email_failure_events MongoDB write skipped: {exc}")

    # Derive alert count from MongoDB when possible; fall back to in-memory
    count = await get_email_failures_last_hour()
    if count >= _EMAIL_ALERT_THRESHOLD:
        now_alert = _time.time()
        alert_cutoff = now_alert - _EMAIL_ALERT_COOLDOWN

        # Attempt an atomic MongoDB claim so only one pod fires per cooldown window
        claimed = await _claim_alert_cooldown_in_mongo(now_alert, alert_cutoff)

        if claimed is None:
            # MongoDB unavailable — fall back to per-pod in-memory cooldown (fail-open:
            # we prefer to alert rather than silently miss a notification).
            if (now_alert - _last_alert_time) < _EMAIL_ALERT_COOLDOWN:
                return  # in-memory cooldown still active for this pod
            _last_alert_time = now_alert
        elif not claimed:
            return  # another pod holds an active cooldown, stay silent
        else:
            _last_alert_time = now_alert  # keep in-memory in sync with won claim

        logger.error(
            f"EMAIL_DELIVERY_FAILURE_ALERT: {count} email send failures in the last hour"
        )


async def get_email_failures_last_hour() -> int:
    """Return the number of email send failures in the last hour.

    Queries MongoDB for a cross-pod, restart-safe count.
    Falls back to the in-memory list when MongoDB is unavailable.
    """
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings as _settings

        client = get_mongo_client()
        db = client[_settings.MONGODB_DB_NAME]
        import datetime as _dt
        window_start = datetime.now(timezone.utc) - _dt.timedelta(seconds=_EMAIL_FAILURE_WINDOW)
        count = await db.email_failure_events.count_documents(
            {"ts": {"$gte": window_start}}
        )
        return count
    except Exception:
        # MongoDB unavailable — return in-memory count
        now = _time.time()
        cutoff = now - _EMAIL_FAILURE_WINDOW
        return sum(1 for t in _email_failure_timestamps if t >= cutoff)


async def _check_rate_limit_redis(recipient: str, window_bucket: int) -> Optional[bool]:
    """Attempt to enforce the rate limit via Upstash Redis REST API.

    Uses a fixed-window counter keyed by recipient + 1-minute bucket.  Two
    commands are pipelined atomically:
      1. INCR  — atomically increments (and creates) the counter.
      2. EXPIRE key seconds NX — sets the TTL only the first time so the window
         does not slide on every request (NX option, Redis ≥ 7.0, Upstash OK).

    Returns:
        True  — the send is within the limit (proceed).
        False — the limit is exceeded (block the send).
        None  — Redis is unavailable; caller must fall back to in-memory state.
    """
    url = settings.UPSTASH_REDIS_REST_URL
    token = settings.UPSTASH_REDIS_REST_TOKEN
    if not url or not token:
        return None  # Redis not configured — use in-memory fallback

    key = f"email_rate:{recipient}:{window_bucket}"
    try:
        async with httpx.AsyncClient(timeout=2.0) as redis_client:
            resp = await redis_client.post(
                f"{url}/pipeline",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=[
                    ["INCR", key],
                    ["EXPIRE", key, _EMAIL_RATE_WINDOW, "NX"],
                ],
            )
            resp.raise_for_status()
            results = resp.json()
            # results[0] is the INCR response: {"result": <new_count>}
            new_count = results[0]["result"]
            return new_count <= _EMAIL_RATE_LIMIT
    except Exception as exc:
        logger.debug(f"Redis rate-limit check failed (will use in-memory fallback): {exc}")
        return None  # Redis unavailable — fall back to in-memory


async def _check_rate_limit(recipient: str) -> bool:
    """Check if sending to this recipient would exceed the rate limit.

    Tries a shared Redis counter first so the limit is enforced across all
    Cloud Run pods.  Falls back to a per-process in-memory list when Redis is
    unavailable, preserving the original behaviour.
    """
    now = _time.time()
    window_bucket = int(now // _EMAIL_RATE_WINDOW)

    # --- Primary: Redis shared counter ---
    redis_result = await _check_rate_limit_redis(recipient, window_bucket)
    if redis_result is not None:
        return redis_result

    # --- Fallback: per-process in-memory list ---
    _email_send_times[recipient] = [
        t for t in _email_send_times[recipient] if now - t < _EMAIL_RATE_WINDOW
    ]
    # Prune global dict if it grows too large to prevent unbounded memory usage.
    # Instead of clearing everything (which resets all windows and risks a send
    # flood), evict only recipients whose last send is outside the rate window —
    # i.e. they are inactive and safe to drop.  If that is not enough to bring
    # the size under budget, evict the recipients with the oldest most-recent
    # send time until we are back within budget.  Active/high-volume recipients
    # are never evicted.
    if len(_email_send_times) > 10000:
        cutoff = now - _EMAIL_RATE_WINDOW
        # Collect recipients whose most-recent send is stale (all sends expired)
        stale = [
            addr
            for addr, times in _email_send_times.items()
            if not times or max(times) < cutoff
        ]
        for addr in stale:
            del _email_send_times[addr]
        # If still over budget, evict least-recently-active recipients
        if len(_email_send_times) > 10000:
            # Sort by most-recent send time ascending (oldest first)
            sorted_by_last = sorted(
                _email_send_times.keys(),
                key=lambda a: max(_email_send_times[a]) if _email_send_times[a] else 0,
            )
            to_remove = sorted_by_last[: len(_email_send_times) - 10000]
            for addr in to_remove:
                del _email_send_times[addr]
    if len(_email_send_times[recipient]) >= _EMAIL_RATE_LIMIT:
        return False
    _email_send_times[recipient].append(now)
    return True


RESEND_API_URL = "https://api.resend.com/emails"

UNSUBSCRIBE_FOOTER = (
    '<hr style="margin: 20px 0; border: none; border-top: 1px solid #eee;">'
    '<p style="font-size: 12px; color: #666;">If you no longer wish to receive '
    'emails from us, <a href="https://syrabit.ai/profile?unsubscribe=true">'
    "unsubscribe here</a>.</p>"
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

    if not await _check_rate_limit(to):
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
        await _record_email_failure()
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
    """Send payment receipt email (used for renewals via webhook)."""
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
    result = await _send_email(email, "Renewal Receipt - Syrabit Pro", email_html)
    if result:
        logger.info(f"Receipt email sent to {email}")
    return result


async def send_first_purchase_receipt_email(
    email: str, amount: int, order_id: str
) -> bool:
    """Send first-purchase confirmation email after a user upgrades to Pro."""
    amount_inr = amount / 100  # Convert paise to rupees
    safe_order_id = html.escape(order_id)
    email_html = f"""
    <h1>Welcome to Syrabit Pro! \U0001f389</h1>
    <p>Your payment was successful and your account has been upgraded.</p>
    <table style="width: 100%; max-width: 400px; border-collapse: collapse;">
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Amount Paid:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">\u20b9{amount_inr:.2f}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Order ID:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{safe_order_id}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Plan:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">Syrabit Pro</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Status:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">\u2705 Active</td>
        </tr>
    </table>
    <p>You now have unlimited access to Syrabit Pro features. Enjoy!</p>
    <p>Best regards,<br>The Syrabit Team</p>
    {UNSUBSCRIBE_FOOTER}
    """
    result = await _send_email(email, "Welcome to Syrabit Pro \U0001f389", email_html)
    if result:
        logger.info(f"First-purchase receipt email sent to {email}")
    return result


async def send_credit_topup_receipt_email(
    email: str, credits: int, amount: int, order_id: str
) -> bool:
    """Send credit top-up confirmation email."""
    amount_inr = amount / 100  # Convert paise to rupees
    safe_order_id = html.escape(order_id)
    email_html = f"""
    <h1>Credits Added! \u2b50</h1>
    <p>Your credit top-up was successful.</p>
    <table style="width: 100%; max-width: 400px; border-collapse: collapse;">
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Credits Added:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{credits}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Amount Paid:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">\u20b9{amount_inr:.2f}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Order ID:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{safe_order_id}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Status:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">\u2705 Credited</td>
        </tr>
    </table>
    <p>Your credits are now available. Happy learning!</p>
    <p>Best regards,<br>The Syrabit Team</p>
    {UNSUBSCRIBE_FOOTER}
    """
    result = await _send_email(
        email, f"{credits} Credits Added to Your Syrabit Account", email_html
    )
    if result:
        logger.info(f"Credit top-up receipt email sent to {email}")
    return result


async def send_password_reset_email(email: str, reset_token: str) -> bool:
    """Send password reset email."""
    safe_token = url_quote(reset_token, safe="")
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
