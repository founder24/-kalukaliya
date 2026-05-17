"""
Transactional email helpers for Syrabit.ai.

Single-path provider — Amazon SES (Task #556).

  * AWS SES is the **sole** transactional email path. There is no
    fallback. Failures fail loud per V4 §12 ("no silent fallbacks") by
    raising :class:`EmailSendFailed` — caller chooses whether to log,
    retry at the request layer, or surface to the user.

  * Resilience comes from SES multi-region (``us-east-1`` primary,
    ``ap-south-1`` warm secondary). Failover is a manual env-var flip
    (``SES_REGION=ap-south-1``) plus an ACA revision restart — see
    ``artifacts/syrabit/docs/infra/aws-landing-zone.md`` §8.

  * Bulk / digest / marketing sends use Cloudflare Email Workers as a
    completely **separate** code path — never an SES fallback. See
    :mod:`bulk_email` for that surface.

  * The prior tri-tier email fan-out and the legacy provider SDKs are
    fully retired by Task #556. The legacy provider-flag env knobs no
    longer exist.

The historical async :func:`_send` wrapper is preserved so existing
``await _send(...)`` call-sites in this module's helper functions
(e.g. :func:`send_password_reset`) keep working — it now just runs the
synchronous SES call in a thread and re-raises on failure.
"""
from __future__ import annotations

import os
import logging
import asyncio

logger = logging.getLogger(__name__)

EMAIL_FROM = os.environ.get("EMAIL_FROM", "Syrabit.ai <noreply@syrabit.ai>").strip()

_BRAND = "#7c3aed"
_BG    = "#0d0d1a"
_CARD  = "#1e1b4b"
_MUTED = "#94a3b8"
_TEXT  = "#e2e8f0"
_BORDER = "#4c1d95"


class EmailSendFailed(Exception):
    """Raised when an SES send fails. Carries the underlying error.

    Attributes:
        provider: always ``"ses"`` — single-path provider.
        region: the SES region the failed call targeted.
        recipients: list of intended recipients (for ops triage).
        original: the underlying exception (botocore/ClientError, etc).
    """

    def __init__(self, message: str, *, region: str, recipients: list[str],
                 original: Exception | None = None):
        super().__init__(message)
        self.provider = "ses"
        self.region = region
        self.recipients = list(recipients)
        self.original = original


def _ses_region() -> str:
    """Active SES region. Defaults to ``us-east-1`` (primary).

    Operator override: set ``SES_REGION=ap-south-1`` (warm secondary)
    and restart the ACA revision. Legacy ``AWS_SES_REGION`` is still
    accepted as a synonym so an in-flight Bicep/ACA rollout doesn't
    break the send path mid-deploy.
    """
    return (
        os.environ.get("SES_REGION", "").strip()
        or os.environ.get("AWS_SES_REGION", "").strip()
        or "us-east-1"
    )


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


def _ses_client():
    """Build a boto3 SES client lazily so import-time has zero AWS deps."""
    import boto3  # type: ignore
    return boto3.client("ses", region_name=_ses_region())


def _send_via_ses(to: str, subject: str, html: str) -> None:
    """Send a single transactional email via Amazon SES.

    Raises :class:`EmailSendFailed` on any failure. There is no
    fallback (Task #556 — V4 §12 "no silent fallbacks").
    """
    region = _ses_region()
    if not (os.environ.get("AWS_ACCESS_KEY_ID", "").strip() and
            os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()):
        raise EmailSendFailed(
            "SES credentials missing (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)",
            region=region,
            recipients=[to],
        )
    try:
        client = _ses_client()
        addr, name = _parse_from(EMAIL_FROM)
        source = f'"{name}" <{addr}>' if name else addr
        client.send_email(
            Source=source,
            Destination={"ToAddresses": [to]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body":    {"Html":    {"Data": html,    "Charset": "UTF-8"}},
            },
        )
        logger.info(f"[Email/SES:{region}] Sent '{subject}' → {to}")
    except EmailSendFailed:
        raise
    except Exception as exc:
        logger.warning(f"[Email/SES:{region}] send failed for {to}: {exc}")
        raise EmailSendFailed(
            f"SES send_email failed: {exc}",
            region=region,
            recipients=[to],
            original=exc,
        ) from exc


def send_admin_email(
    *,
    to,
    subject: str,
    html: str,
    attachments=None,
    sender: str = "",
) -> bool:
    """Multi-recipient send for admin / digest / alert emails (SES only).

    Returns ``True`` on a 2xx-equivalent SES accept, ``False`` on any
    failure. Admin alert paths must be fire-and-forget — this helper
    swallows :class:`EmailSendFailed` and logs a warning rather than
    raising, so a single bad send can't take down a polling loop.

    For the user-facing transactional path, callers should invoke
    :func:`_send_via_ses` directly (or the high-level ``send_*`` helpers
    below) and handle :class:`EmailSendFailed`.
    """
    if isinstance(to, str):
        recipients = [to]
    else:
        recipients = [t for t in to if t]
    if not recipients:
        return False
    region = _ses_region()
    if not (os.environ.get("AWS_ACCESS_KEY_ID", "").strip() and
            os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()):
        logger.warning("[Email/SES:admin] no AWS creds — skipping send")
        return False
    frm = (sender or EMAIL_FROM).strip()
    addr, name = _parse_from(frm)
    source = f'"{name}" <{addr}>' if name else addr
    try:
        client = _ses_client()
        if attachments:
            import base64
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            from email.mime.base import MIMEBase
            from email import encoders
            msg = MIMEMultipart()
            msg["Subject"] = subject
            msg["From"]    = source
            msg["To"]      = ", ".join(recipients)
            msg.attach(MIMEText(html, "html", "utf-8"))
            for a in attachments:
                part = MIMEBase("application", "octet-stream")
                try:
                    part.set_payload(base64.b64decode(a["content"]))
                except Exception:
                    part.set_payload(a["content"])
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f'attachment; filename="{a["filename"]}"',
                )
                msg.attach(part)
            client.send_raw_email(
                Source=source,
                Destinations=recipients,
                RawMessage={"Data": msg.as_string()},
            )
        else:
            client.send_email(
                Source=source,
                Destination={"ToAddresses": recipients},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body":    {"Html":    {"Data": html,    "Charset": "UTF-8"}},
                },
            )
        logger.info(f"[Email/SES:admin:{region}] Sent '{subject}' → {', '.join(recipients)}")
        return True
    except Exception as exc:
        logger.warning(f"[Email/SES:admin:{region}] send failed: {exc}")
        return False


async def _send(to: str, subject: str, html: str) -> None:
    """Async wrapper around :func:`_send_via_ses`.

    Raises :class:`EmailSendFailed` on failure. There is no fallback.
    """
    await asyncio.to_thread(_send_via_ses, to, subject, html)


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
    """Password reset email — replaces the raw httpx version in server.py.

    reset_url must include ?token=<token> so the button is a direct one-click
    link.  The token is also shown as a fallback code block for email clients
    that strip links.
    """
    body = _base(f"""
      <h2 style="color:{_BRAND};margin:0 0 8px;">Reset your password</h2>
      <p style="color:{_MUTED};margin:0 0 24px;">
        We received a request to reset your Syrabit.ai password.
        Click the button below — it takes you straight to the reset page.
      </p>
      <p style="margin-bottom:24px;">
        {_button("Reset My Password", reset_url)}
      </p>
      {_card(f'''
        <p style="color:{_MUTED};font-size:11px;margin:0 0 6px;">
          Button not working? Paste this token on
          <a href="https://syrabit.ai/reset-password"
             style="color:#a78bfa;text-decoration:none;">syrabit.ai/reset-password</a>
        </p>
        <code style="font-size:13px;color:#a78bfa;word-break:break-all;
                     letter-spacing:0.5px;">{token}</code>
      ''')}
      <p style="color:#475569;font-size:12px;margin-top:24px;">
        This link expires in 1 hour. If you didn't request this, ignore this
        email — your password won't change.
      </p>
    """)
    await _send(email, "Reset your Syrabit.ai password", body)
