"""
Transactional email helpers for Syrabit.ai.

Provider priority (Task #347 — Resend removed):
  1. Cloudflare Email Worker (EMAIL_WORKER_URL) — zero-cost under CF $5k credits,
     routes the message via SendGrid v3 HTTP API from the Worker.
  2. SendGrid v3 HTTP API (SENDGRID_API_KEY) direct from the backend, used when
     the Worker is unreachable or returns a 5xx. Same template, same `From`.
  3. Amazon SES via the ``email-fallback`` SQS queue — final-tier retry handled
     by the email-worker Lambda (kept as 5xx-only fallback per Task #347).

All functions are fire-and-forget — they log warnings on failure and never raise.
"""
import os
import logging
import asyncio
import json

logger = logging.getLogger(__name__)

SENDGRID_API_KEY   = os.environ.get("SENDGRID_API_KEY", "").strip()
EMAIL_FROM         = os.environ.get("EMAIL_FROM", "Syrabit.ai <noreply@syrabit.ai>").strip()
EMAIL_WORKER_URL   = os.environ.get("EMAIL_WORKER_URL", "").rstrip("/")
EMAIL_WORKER_KEY   = os.environ.get("EMAIL_WORKER_AUTH_KEY", "").strip()

_BRAND = "#7c3aed"
_BG    = "#0d0d1a"
_CARD  = "#1e1b4b"
_MUTED = "#94a3b8"
_TEXT  = "#e2e8f0"
_BORDER = "#4c1d95"


def _base(body_html: str) -> str:
    return f"""
<div style="font-family:sans-serif;max-width:520px;margin:auto;padding:32px;
            background:{_BG};color:{_TEXT};border-radius:12px;">
  <div style="margin-bottom:24px;">
    <span style="font-size:20px;font-weight:700;color:{_BRAND};">Syrabit</span>
    <span style="font-size:20px;font-weight:700;color:{_TEXT};">.ai</span>
  </div>
  {body_html}
  <p style="color:#475569;font-size:11px;margin-top:32px;border-top:1px solid #1e293b;padding-top:16px;">
    You received this email because of account activity on Syrabit.ai.<br>
    Questions? Reply to this email or write to admin@syrabit.ai
  </p>
</div>
"""


def _card(content: str) -> str:
    return f"""<div style="background:{_CARD};border:1px solid {_BORDER};border-radius:8px;
                           padding:20px;margin-bottom:20px;">{content}</div>"""


def _button(label: str, url: str) -> str:
    return (f'<a href="{url}" style="display:inline-block;background:{_BRAND};color:white;'
            f'text-decoration:none;padding:12px 24px;border-radius:8px;font-weight:600;'
            f'font-size:14px;">{label}</a>')


def _parse_from(frm: str) -> tuple[str, str]:
    """Parse ``Display Name <addr@host>`` into ``(addr, name)``.

    Falls back to ``(frm, "")`` for a bare address.
    """
    frm = frm.strip()
    if "<" in frm and frm.endswith(">"):
        name = frm[: frm.index("<")].strip().strip('"')
        addr = frm[frm.index("<") + 1 : -1].strip()
        return addr, name
    return frm, ""


def _send_via_cf_worker(to: str, subject: str, html: str) -> bool:
    """Send email via the Cloudflare Email Worker (Tier-1).

    Returns True on success, False on any failure (caller falls back to
    SendGrid in-process, then SES via the ``email-fallback`` SQS queue).
    Requires:
      - ``EMAIL_WORKER_URL`` set (e.g. https://syrabit-email.<acct>.workers.dev)
      - The Worker's ``SENDGRID_API_KEY`` secret bound (set via wrangler).
    """
    worker_url = os.environ.get("EMAIL_WORKER_URL", "").rstrip("/")
    auth_key   = os.environ.get("EMAIL_WORKER_AUTH_KEY", "").strip()
    if not worker_url:
        return False
    try:
        import httpx
        headers = {"Content-Type": "application/json"}
        if auth_key:
            headers["Authorization"] = f"Bearer {auth_key}"
        payload = {"to": to, "subject": subject, "html": html}
        r = httpx.post(f"{worker_url}/email/send", json=payload, headers=headers, timeout=8.0)
        if r.status_code in (200, 201):
            logger.info(f"[Email/CF] Sent '{subject}' → {to}")
            return True
        logger.warning(f"[Email/CF] Worker returned {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"[Email/CF] Worker call failed: {e}")
        return False


# Tri-state outcome from the SendGrid attempt so _send_sync can apply the
# Task #347 fallback policy (only retryable failures escalate to SES).
#
#   "ok"        — 2xx, message accepted; nothing more to do.
#   "no_key"    — SENDGRID_API_KEY missing → treat as configuration outage,
#                 escalate so SES can carry the load.
#   "retry_5xx" — SendGrid returned a 5xx OR the HTTP call itself failed
#                 (timeout / DNS / TLS). These are infrastructure failures
#                 SES is qualified to retry.
#   "perm_4xx"  — SendGrid returned a 4xx (auth, rate-limit, invalid
#                 payload, suppressed recipient). Re-sending the same
#                 payload via SES would just produce the same error and
#                 risk reputation damage — log + drop instead.
_SENDGRID_OK         = "ok"
_SENDGRID_NO_KEY     = "no_key"
_SENDGRID_RETRY_5XX  = "retry_5xx"
_SENDGRID_PERM_4XX   = "perm_4xx"


def _send_via_sendgrid(to: str, subject: str, html: str) -> str:
    """Send via SendGrid v3 and report Task #347 fallback policy outcome.

    See ``_SENDGRID_*`` constants above for the contract. The legacy
    bool-returning callers were rewritten in the same task, so this
    function is now the sole API.
    """
    key = os.environ.get("SENDGRID_API_KEY", "").strip()
    if not key:
        return _SENDGRID_NO_KEY
    try:
        import httpx
        frm = os.environ.get("EMAIL_FROM", "Syrabit.ai <noreply@syrabit.ai>").strip()
        addr, name = _parse_from(frm)
        body = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": addr, "name": name} if name else {"email": addr},
            "subject": subject,
            "content": [{"type": "text/html", "value": html}],
        }
        r = httpx.post(
            "https://api.sendgrid.com/v3/mail/send",
            json=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )
        if 200 <= r.status_code < 300:
            logger.info(f"[Email/SendGrid] Sent '{subject}' → {to}")
            return _SENDGRID_OK
        if r.status_code >= 500:
            logger.warning(
                f"[Email/SendGrid] retryable HTTP {r.status_code}: "
                f"{r.text[:200]} — escalating to SES"
            )
            return _SENDGRID_RETRY_5XX
        # 4xx — permanent. Do NOT escalate; SES will produce the same error.
        logger.warning(
            f"[Email/SendGrid] permanent HTTP {r.status_code} — dropping (no SES retry): "
            f"{r.text[:200]}"
        )
        return _SENDGRID_PERM_4XX
    except Exception as e:
        # Transport-level failure (timeout, DNS, TLS, connection reset). SES
        # handles these well — escalate.
        logger.warning(f"[Email/SendGrid] transport failure → escalating to SES: {e}")
        return _SENDGRID_RETRY_5XX


def send_admin_email(
    *,
    to,
    subject: str,
    html: str,
    attachments=None,
    sender: str = "",
) -> bool:
    """Synchronous SendGrid-only sender for admin / digest / alert emails.

    Replaces the Task #347-deleted Resend SDK call sites in
    ``routes/admin_review_prompts.py``, ``routes/bot_traffic_report.py``
    and ``routes/bot_discovery.py``. Mirrors the previous Resend
    semantics (single SDK call, multi-recipient, optional attachments).

    Args:
        to: a single address string OR an iterable of address strings.
        subject: subject line.
        html: rendered HTML body.
        attachments: optional list of ``{"filename": str, "content": str}``
            entries where ``content`` is the **base64-encoded** payload
            (matches the Resend SDK contract the call sites already use).
        sender: optional ``Display Name <addr@host>`` override; defaults
            to ``EMAIL_FROM``.

    Returns ``True`` on a 2xx, ``False`` otherwise. Never raises — admin
    alert paths must be fire-and-forget.
    """
    key = os.environ.get("SENDGRID_API_KEY", "").strip()
    if not key:
        logger.warning("[Email/SendGrid:admin] no SENDGRID_API_KEY — skipping send")
        return False
    if isinstance(to, str):
        recipients = [to]
    else:
        recipients = [t for t in to if t]
    if not recipients:
        return False
    frm = (sender or os.environ.get(
        "EMAIL_FROM", "Syrabit.ai <noreply@syrabit.ai>")).strip()
    addr, name = _parse_from(frm)
    body = {
        "personalizations": [
            {"to": [{"email": r} for r in recipients]},
        ],
        "from": {"email": addr, "name": name} if name else {"email": addr},
        "subject": subject,
        "content": [{"type": "text/html", "value": html}],
    }
    if attachments:
        body["attachments"] = [
            {
                "filename": a["filename"],
                "content":  a["content"],
                "type": a.get("type", "text/csv"),
                "disposition": "attachment",
            }
            for a in attachments
        ]
    try:
        import httpx
        r = httpx.post(
            "https://api.sendgrid.com/v3/mail/send",
            json=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )
        if 200 <= r.status_code < 300:
            logger.info(
                f"[Email/SendGrid:admin] Sent '{subject}' → {', '.join(recipients)}"
            )
            return True
        logger.warning(
            f"[Email/SendGrid:admin] HTTP {r.status_code}: {r.text[:200]}"
        )
        return False
    except Exception as exc:
        logger.warning(f"[Email/SendGrid:admin] send failed: {exc}")
        return False


def _send_sync(to: str, subject: str, html: str) -> str:
    """Tier-1 + Tier-2 of the email-send chain.

    Returns:
      "cf"        — sent via Cloudflare Email Worker (Tier-1, zero-cost).
      "sendgrid"  — sent via SendGrid v3 HTTP API (Tier-2).
      "fallback"  — Tier-2 returned a *retryable* failure (5xx, transport
                    error, or no SENDGRID_API_KEY); caller enqueues the
                    message to the ``email-fallback`` SQS queue so the
                    Lambda can retry via Amazon SES (Tier-3).
                    Per Task #347 policy, **permanent 4xx** failures (bad
                    auth, suppressed recipient, validation error) deliberately
                    do NOT escalate — SES would produce the same error and
                    repeated retries hurt sender reputation. They terminate
                    here as ``"dropped_perm"`` (logged-and-forgotten).
      "dropped_perm" — see above.

    The ``EMAIL_FALLBACK`` env var (``"sendgrid"``, ``"ses"``, ``"both"``)
    forces the policy: ``"ses"`` always escalates (operator override for
    SES burn-in or SendGrid outages); ``"sendgrid"`` never escalates;
    unset / ``"both"`` uses the 5xx-only policy described above.

    Fire-and-forget: never raises.
    """
    if _send_via_cf_worker(to, subject, html):
        return "cf"
    sg_outcome = _send_via_sendgrid(to, subject, html)
    if sg_outcome == _SENDGRID_OK:
        return "sendgrid"
    override = os.environ.get("EMAIL_FALLBACK", "").strip().lower()
    if override == "sendgrid":
        logger.info(
            f"[Email] EMAIL_FALLBACK=sendgrid — not escalating to SES for {to}: {subject}"
        )
        return "dropped_perm"
    if override == "ses":
        logger.info(
            f"[Email] EMAIL_FALLBACK=ses — operator-forced escalation for {to}: {subject}"
        )
        return "fallback"
    # Default policy: only retryable failures (5xx / transport / missing key)
    # escalate to SES. Permanent 4xx terminates as dropped.
    if sg_outcome == _SENDGRID_PERM_4XX:
        logger.info(
            f"[Email] SendGrid 4xx permanent — dropping (no SES retry) for {to}: {subject}"
        )
        return "dropped_perm"
    logger.info(
        f"[Email] Tier-2 retryable failure ({sg_outcome}) — handing off to SES for {to}: {subject}"
    )
    return "fallback"


async def _send(to: str, subject: str, html: str):
    """Tier-1 → Tier-2 → Tier-3 send chain (Task #347).

    Tiers 1 and 2 run in-process (CF Email Worker → SendGrid v3 HTTP API).
    On failure of both, the message is enqueued to the ``email-fallback``
    SQS queue so the ``syrabit-email-worker`` Lambda can deliver via
    Amazon SES with full DLQ + alarm coverage.

        CF Email Worker → SendGrid → SES (via email-fallback SQS) → log warn
    """
    outcome = await asyncio.to_thread(_send_sync, to, subject, html)
    # Only the explicit "fallback" outcome enqueues to SES; "cf", "sendgrid"
    # and the new "dropped_perm" terminal state all return immediately.
    if outcome != "fallback":
        return
    try:
        from sqs_fanout import enqueue as _sqs_enqueue
        await _sqs_enqueue(
            "email-fallback",
            {
                "to": to,
                "subject": subject,
                "html": html,
                "from": os.environ.get("EMAIL_FROM", "Syrabit.ai <noreply@syrabit.ai>").strip(),
                "source": "email_templates._send",
            },
        )
        logger.info(f"[Email/SES-fallback] enqueued '{subject}' → {to}")
    except Exception as e:
        logger.warning(f"[Email] All tiers failed for {to} (CF→SendGrid→SES enqueue: {e})")


async def send_plan_activation(email: str, name: str, plan: str, credits: int, amount_paise: int):
    """Confirmation email after a successful plan upgrade."""
    plan_cap = plan.capitalize()
    amount_inr = amount_paise / 100
    body = _base(f"""
      <h2 style="color:{_BRAND};margin:0 0 8px;">Welcome to {plan_cap}!</h2>
      <p style="color:{_MUTED};margin:0 0 24px;">
        Hi {name or 'there'}, your plan has been upgraded successfully.
      </p>
      {_card(f'''
        <table style="width:100%;border-collapse:collapse;">
          <tr><td style="color:{_MUTED};padding:6px 0;">Plan</td>
              <td style="text-align:right;font-weight:600;">{plan_cap}</td></tr>
          <tr><td style="color:{_MUTED};padding:6px 0;">Credits added</td>
              <td style="text-align:right;font-weight:600;color:{_BRAND};">+{credits:,}</td></tr>
          <tr><td style="color:{_MUTED};padding:6px 0;">Amount charged</td>
              <td style="text-align:right;font-weight:600;">₹{amount_inr:,.2f}</td></tr>
        </table>
      ''')}
      <p style="margin-bottom:24px;">
        {_button("Open Syrabit.ai", "https://syrabit.ai")}
      </p>
      <p style="color:{_MUTED};font-size:13px;">
        Your credits are ready to use. Start chatting with AI on any subject,
        access full notes, and unlock important questions.
      </p>
    """)
    await _send(email, f"You're on {plan_cap}! — Syrabit.ai", body)


async def send_topup_confirmation(email: str, name: str, credits: int, amount_paise: int):
    """Confirmation email after a credit top-up."""
    amount_inr = amount_paise / 100
    body = _base(f"""
      <h2 style="color:{_BRAND};margin:0 0 8px;">Credits Added!</h2>
      <p style="color:{_MUTED};margin:0 0 24px;">
        Hi {name or 'there'}, your credit top-up was processed successfully.
      </p>
      {_card(f'''
        <table style="width:100%;border-collapse:collapse;">
          <tr><td style="color:{_MUTED};padding:6px 0;">Credits added</td>
              <td style="text-align:right;font-weight:600;color:{_BRAND};">+{credits:,}</td></tr>
          <tr><td style="color:{_MUTED};padding:6px 0;">Amount charged</td>
              <td style="text-align:right;font-weight:600;">₹{amount_inr:,.2f}</td></tr>
        </table>
      ''')}
      <p style="margin-bottom:24px;">
        {_button("Start Chatting", "https://syrabit.ai/chat")}
      </p>
      <p style="color:{_MUTED};font-size:13px;">
        Your new credits are immediately available. Keep up the great work!
      </p>
    """)
    await _send(email, "Credits topped up — Syrabit.ai", body)


async def send_password_reset(email: str, token: str, reset_url: str):
    """Password reset email — replaces the raw httpx version in server.py."""
    body = _base(f"""
      <h2 style="color:{_BRAND};margin:0 0 8px;">Reset your password</h2>
      <p style="color:{_MUTED};margin:0 0 24px;">
        We received a request to reset your Syrabit.ai password.
        Use the token below on the reset page.
      </p>
      {_card(f'''
        <p style="color:{_MUTED};font-size:12px;margin:0 0 8px;">Your reset token (valid 1 hour)</p>
        <code style="font-size:14px;color:#a78bfa;word-break:break-all;
                     letter-spacing:0.5px;">{token}</code>
      ''')}
      <p style="margin-bottom:24px;">
        {_button("Go to Reset Page", reset_url)}
      </p>
      <p style="color:#475569;font-size:12px;">
        If you didn't request this, ignore this email — your password won't change.
      </p>
    """)
    await _send(email, "Reset your Syrabit.ai password", body)
