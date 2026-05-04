"""
Transactional email helpers for Syrabit.ai.

Provider priority:
  1. Cloudflare Email Worker (EMAIL_WORKER_URL) — zero-cost under CF $5k credits,
     routes via CF Email Workers send_email binding.  Activated automatically when
     EMAIL_WORKER_URL is set AND CF Email Routing is live (MX records pointing to CF).
  2. Resend (RESEND_API_KEY) — reliable third-party fallback while CF routing is
     being configured.

All functions are fire-and-forget — they log warnings on failure and never raise.
"""
import os
import logging
import asyncio
import json

logger = logging.getLogger(__name__)

RESEND_API_KEY     = os.environ.get("RESEND_API_KEY", "").strip()
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


def _send_via_cf_worker(to: str, subject: str, html: str) -> bool:
    """
    Send email via the Cloudflare Email Worker.
    Returns True on success, False on any failure (caller should fallback to
    Resend, then SES via the `email-fallback` SQS queue — see `_send`).
    Requires:
      - EMAIL_WORKER_URL to be set (e.g. https://syrabit-email.axomxplain.workers.dev)
      - CF Email Routing MX records pointing to Cloudflare (not Hostinger)
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


def _send_sync(to: str, subject: str, html: str) -> str:
    """
    Tier-1 + Tier-2 of the email-send chain.

    Returns:
      "cf"      — sent via Cloudflare Email Worker (Tier-1, zero-cost)
      "resend"  — sent via Resend (Tier-2)
      "fallback"— both prior tiers failed; caller must enqueue to the
                  ``email-fallback`` SQS queue so the email-worker
                  Lambda can retry via Amazon SES (Tier-3).
      "skip"    — no provider configured AND fallback not viable.

    Fire-and-forget: never raises.
    """
    if _send_via_cf_worker(to, subject, html):
        return "cf"
    key = os.environ.get("RESEND_API_KEY", "").strip()
    if not key:
        # No Resend key but we still want SES to take a shot.
        logger.info(f"[Email] Resend not configured — handing off to SES fallback for {to}: {subject}")
        return "fallback"
    try:
        import resend as _resend
        _resend.api_key = key
        frm = os.environ.get("EMAIL_FROM", "Syrabit.ai <noreply@syrabit.ai>").strip()
        _resend.Emails.send({"from": frm, "to": [to], "subject": subject, "html": html})
        logger.info(f"[Email/Resend] Sent '{subject}' → {to}")
        return "resend"
    except Exception as e:
        # Tier-2 failed — Tier-3 (SES via SQS) will retry. Don't log
        # warning yet; the SQS consumer logs success/final failure.
        logger.info(f"[Email/Resend] failed for {to}, falling back to SES: {e}")
        return "fallback"


async def _send(to: str, subject: str, html: str):
    """Tier-1 → Tier-2 → Tier-3 send chain (Task #332).

    Tiers 1 and 2 run in-process (CF Email Worker, then Resend). On
    failure of both, the message is enqueued to the ``email-fallback``
    SQS queue so the ``syrabit-email-worker`` Lambda can deliver via
    Amazon SES with full DLQ + alarm coverage. The chain is:

        CF Email Worker → Resend → SES (via email-fallback SQS) → log warn

    See `routes/admin_aws_infra.py:_QUEUE_INVENTORY["email-fallback"]`
    and `infra/aws/lambda-workers.tf` for the consumer wiring.
    """
    outcome = await asyncio.to_thread(_send_sync, to, subject, html)
    if outcome != "fallback":
        return
    # Tier-3 — enqueue for SES delivery via Lambda. We import lazily
    # so unit tests of this module don't have to stub boto3.
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
        # Tier-3 enqueue failed — final tier is the warn log so on-call
        # has a breadcrumb the email never went out.
        logger.warning(f"[Email] All tiers failed for {to} (CF→Resend→SES enqueue: {e})")


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
