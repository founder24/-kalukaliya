"""
Bulk / digest / marketing email surface — Cloudflare Email Workers.

Task #556. This module is **completely independent** of the
SES transactional surface (see ``_send_via_ses``). The two surfaces
share nothing beyond the small :class:`BulkEmailMessage` dataclass
below — there is no provider abstraction, no fallback, no shared
queue. Picking the wrong surface is a code-review concern, not a
runtime concern.

When to use which:

  * Transactional, user-triggered, low-volume, must-deliver-now
    (password reset, plan activation, topup, OTP)  → :mod:`email_templates`
    (Amazon SES, fails loud).
  * Bulk, digest, scheduled, high-volume, may-skip-on-outage
    (weekly digest, marketing newsletter, daily admin summary fan-out)
    → this module (Cloudflare Email Workers, fails soft).

Wire-up:

  * The Cloudflare Email Worker URL is configured via
    ``BULK_EMAIL_WORKER_URL`` (e.g.
    ``https://syrabit-bulk-email.<acct>.workers.dev``).
  * Worker-to-origin auth: ``BULK_EMAIL_WORKER_AUTH_KEY`` (HMAC bearer).
  * The Worker itself talks to Cloudflare's MTA via the
    ``send_email`` binding (configured in ``wrangler.toml`` under
    ``workers/bulk-email/``).

Failures here do NOT raise. The bulk send returns a structured
report dict (``{"sent": N, "failed": M, "skipped": K, "reason": ...}``)
that the caller logs / dashboards. A failed bulk send is a
"missed digest", not a user-visible incident.
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass
class BulkEmailMessage:
    """The single shape exchanged between the transactional + bulk
    surfaces. Kept intentionally minimal so swapping providers on
    either side never forces a touch on the other."""

    to: list[str]
    subject: str
    html: str
    sender: str = ""
    tags: dict[str, str] = field(default_factory=dict)


def _worker_url() -> str:
    return os.environ.get("BULK_EMAIL_WORKER_URL", "").rstrip("/")


def _worker_auth() -> str:
    return os.environ.get("BULK_EMAIL_WORKER_AUTH_KEY", "").strip()


def send_bulk(message: BulkEmailMessage) -> dict:
    """Dispatch a bulk message via the Cloudflare Email Worker.

    Never raises. Returns a structured report:

      ``{"sent": int, "failed": int, "skipped": int, "reason": str}``

    Outcomes:
      * worker URL unset                → ``skipped`` (logged + counted).
      * worker returns 2xx              → ``sent``.
      * worker returns non-2xx / errors → ``failed``.
    """
    worker_url = _worker_url()
    recipients = [r for r in (message.to or []) if r]
    if not recipients:
        return {"sent": 0, "failed": 0, "skipped": 0, "reason": "no_recipients"}
    if not worker_url:
        logger.info(
            "[BulkEmail/CF] BULK_EMAIL_WORKER_URL unset — skipping bulk send"
        )
        return {
            "sent": 0, "failed": 0, "skipped": len(recipients),
            "reason": "no_worker_url",
        }
    try:
        import httpx
        headers = {"Content-Type": "application/json"}
        auth = _worker_auth()
        if auth:
            headers["Authorization"] = f"Bearer {auth}"
        sender = (message.sender
                  or os.environ.get("EMAIL_FROM", "Syrabit.ai <noreply@syrabit.ai>")).strip()
        payload = {
            "to":      recipients,
            "subject": message.subject,
            "html":    message.html,
            "from":    sender,
            "tags":    dict(message.tags or {}),
        }
        r = httpx.post(
            f"{worker_url}/bulk/send",
            json=payload,
            headers=headers,
            timeout=15.0,
        )
        if 200 <= r.status_code < 300:
            logger.info(
                f"[BulkEmail/CF] sent '{message.subject}' to {len(recipients)} recipients"
            )
            return {
                "sent": len(recipients), "failed": 0, "skipped": 0,
                "reason": "ok",
            }
        logger.warning(
            f"[BulkEmail/CF] worker returned {r.status_code}: {r.text[:200]}"
        )
        return {
            "sent": 0, "failed": len(recipients), "skipped": 0,
            "reason": f"worker_http_{r.status_code}",
        }
    except Exception as exc:
        logger.warning(f"[BulkEmail/CF] worker call failed: {exc}")
        return {
            "sent": 0, "failed": len(recipients), "skipped": 0,
            "reason": f"transport_error:{type(exc).__name__}",
        }


def send_bulk_iter(messages: Iterable[BulkEmailMessage]) -> dict:
    """Dispatch a sequence of bulk messages, accumulating the per-message
    reports into a single aggregate. Useful for fanned-out digests where
    each recipient gets a personalized body."""
    agg = {"sent": 0, "failed": 0, "skipped": 0, "reasons": {}}
    for msg in messages:
        rep = send_bulk(msg)
        agg["sent"]    += int(rep.get("sent", 0) or 0)
        agg["failed"]  += int(rep.get("failed", 0) or 0)
        agg["skipped"] += int(rep.get("skipped", 0) or 0)
        reason = rep.get("reason", "")
        if reason:
            agg["reasons"][reason] = agg["reasons"].get(reason, 0) + 1
    return agg
