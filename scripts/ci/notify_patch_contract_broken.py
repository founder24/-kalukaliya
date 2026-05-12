#!/usr/bin/env python3
"""Task #95 — Forward the patch-contract broken flag to Slack / SES email.

Sidecar poller that runs alongside (or independently of)
``watch_patch_route_contract.py``.  Every ``PATCH_CONTRACT_NOTIFY_POLL_S``
seconds (default 300 = 5 min) it reads the flag file written by the watcher
and fires a Slack incoming-webhook POST and/or an SES email when the flag
is present.  Repeat alerts are debounced so the same broken state only
pages once per ``PATCH_CONTRACT_NOTIFY_DEBOUNCE_S`` (default 3600 = 1 h).

When a previously-broken flag disappears (the watcher cleared it after a
passing run) exactly one "resolved" notification is sent.

Channels
--------
* **Slack** — ``SLACK_WEBHOOK_URL`` (incoming webhook).  When unset the
  Slack leg is skipped silently, but a WARNING is printed so a missing
  webhook doesn't hide the alert.
* **SES email** — ``PATCH_CONTRACT_ALERT_EMAIL`` (recipient).  Requires
  ``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` / ``SES_REGION`` (or
  ``AWS_REGION``) plus ``boto3`` installed.  When any credential or the
  package is missing the email leg is skipped with a WARNING; the Slack
  leg (if configured) still fires.

Anti-spam guard
---------------
State is persisted in a small JSON file (``PATCH_CONTRACT_NOTIFY_STATE``,
default ``.local/patch_contract_notify_state.json``) that records the last
known state (``"broken"`` or ``"clear"``) and the ISO-8601 timestamp of
the last successful outbound notification.  On every poll:

* **Flag present, state==broken, elapsed < debounce** → skip (already paged).
* **Flag present, state==broken, elapsed >= debounce** → re-page (repeat alert).
* **Flag present, state==clear** → page immediately (new incident).
* **Flag absent, state==broken** → send "resolved" and flip to clear.
* **Flag absent, state==clear** → quiet (healthy, no change).

Usage
-----
Run directly::

    python scripts/ci/notify_patch_contract_broken.py

Or as a one-shot check (exits after one poll)::

    PATCH_CONTRACT_NOTIFY_ONCE=1 python scripts/ci/notify_patch_contract_broken.py

Environment variables
---------------------
``PATCH_CONTRACT_BROKEN_FLAG``
    Path of the flag file (default ``.local/patch_contract_broken.flag``).
    Must match the value used by ``watch_patch_route_contract.py``.
``SLACK_WEBHOOK_URL``
    Slack incoming-webhook URL.  When unset Slack notifications are skipped.
``PATCH_CONTRACT_ALERT_EMAIL``
    Recipient email for SES alerts.  When unset email notifications are skipped.
``EMAIL_FROM``
    Sender address (default ``noreply@syrabit.ai``).
``SES_REGION``
    AWS region for SES (default ``us-east-1``).
``PATCH_CONTRACT_NOTIFY_POLL_S``
    Seconds between polls (default ``300``).
``PATCH_CONTRACT_NOTIFY_DEBOUNCE_S``
    Minimum seconds between repeat "broken" alerts (default ``3600``).
``PATCH_CONTRACT_NOTIFY_STATE``
    Path of the JSON state file (default ``.local/patch_contract_notify_state.json``).
``PATCH_CONTRACT_NOTIFY_ONCE``
    When set to any non-empty value, run exactly one poll then exit.
    Useful for cron / Lambda invocations.

No third-party packages required for the Slack leg (pure stdlib).
``boto3`` is required for the SES leg.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ─── Configuration ────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_BROKEN_FLAG_PATH: Path = Path(
    os.environ.get(
        "PATCH_CONTRACT_BROKEN_FLAG",
        str(_REPO_ROOT / ".local" / "patch_contract_broken.flag"),
    )
)

_STATE_FILE: Path = Path(
    os.environ.get(
        "PATCH_CONTRACT_NOTIFY_STATE",
        str(_REPO_ROOT / ".local" / "patch_contract_notify_state.json"),
    )
)

_POLL_S: float = float(os.environ.get("PATCH_CONTRACT_NOTIFY_POLL_S", "300"))
_DEBOUNCE_S: float = float(os.environ.get("PATCH_CONTRACT_NOTIFY_DEBOUNCE_S", "3600"))
_ONCE: bool = bool(os.environ.get("PATCH_CONTRACT_NOTIFY_ONCE", ""))

_SLACK_WEBHOOK_URL: str = (os.environ.get("SLACK_WEBHOOK_URL") or "").strip()
_ALERT_EMAIL: str = (os.environ.get("PATCH_CONTRACT_ALERT_EMAIL") or "").strip()
_EMAIL_FROM: str = (
    os.environ.get("EMAIL_FROM") or "noreply@syrabit.ai"
).strip()
_SES_REGION: str = (
    os.environ.get("SES_REGION")
    or os.environ.get("AWS_REGION")
    or "us-east-1"
).strip()

_LOG_PREFIX = "[notify_patch_contract_broken]"


# ─── State helpers ────────────────────────────────────────────────────────────


def _load_state() -> dict:
    """Return persisted state dict, or empty defaults."""
    try:
        if _STATE_FILE.is_file():
            return json.loads(_STATE_FILE.read_text())
    except Exception as exc:
        print(
            f"{_LOG_PREFIX} WARNING: could not load state file "
            f"{_STATE_FILE}: {exc}",
            file=sys.stderr,
            flush=True,
        )
    return {"last_state": "clear", "last_notified_at": None}


def _save_state(state: dict) -> None:
    """Persist state dict, creating parent dirs as needed."""
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as exc:
        print(
            f"{_LOG_PREFIX} WARNING: could not save state file "
            f"{_STATE_FILE}: {exc}",
            file=sys.stderr,
            flush=True,
        )


def _elapsed_since(iso_ts: str | None) -> float:
    """Seconds elapsed since ``iso_ts`` (ISO-8601). Returns inf when unset."""
    if not iso_ts:
        return float("inf")
    try:
        ts = datetime.fromisoformat(iso_ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:
        return float("inf")


# ─── Notification channels ────────────────────────────────────────────────────


def _slack_post(title: str, body: str, color: str) -> bool:
    """POST a Slack Block Kit message.  Returns True on HTTP 200."""
    if not _SLACK_WEBHOOK_URL:
        print(
            f"{_LOG_PREFIX} WARNING: SLACK_WEBHOOK_URL not set — "
            "Slack notification skipped.",
            file=sys.stderr,
            flush=True,
        )
        return False
    payload = {
        "attachments": [
            {
                "color": color,
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*{title}*\n{body}",
                        },
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": (
                                    f"Flag file: `{_BROKEN_FLAG_PATH}` — "
                                    "automated alert from Syrabit patch_contract_guard"
                                ),
                            }
                        ],
                    },
                ],
            }
        ]
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        _SLACK_WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = resp.status == 200
            if not ok:
                body_text = resp.read(256).decode(errors="replace")
                print(
                    f"{_LOG_PREFIX} WARNING: Slack returned HTTP {resp.status}: "
                    f"{body_text}",
                    file=sys.stderr,
                    flush=True,
                )
            return ok
    except urllib.error.URLError as exc:
        print(
            f"{_LOG_PREFIX} WARNING: Slack POST failed: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return False


def _ses_send(subject: str, html: str) -> bool:
    """Send a single SES email.  Returns True on success, False on any error."""
    if not _ALERT_EMAIL:
        return False
    try:
        import boto3  # type: ignore
    except ImportError:
        print(
            f"{_LOG_PREFIX} WARNING: boto3 not installed — SES notification skipped.",
            file=sys.stderr,
            flush=True,
        )
        return False
    try:
        # boto3 resolves credentials via its full chain: explicit env vars,
        # shared credentials file, IAM role, instance profile, ECS task
        # role, etc.  A missing-credential failure raises NoCredentialsError
        # which is caught below as Exception and logged as a WARNING.
        client = boto3.client("ses", region_name=_SES_REGION)
        client.send_email(
            Source=_EMAIL_FROM,
            Destination={"ToAddresses": [_ALERT_EMAIL]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Html": {"Data": html, "Charset": "UTF-8"},
                    "Text": {
                        "Data": (
                            subject
                            + "\n\n"
                            + html.replace("<br>", "\n")
                                 .replace("</p>", "\n")
                                 .replace("<p>", "")
                                 .replace("<b>", "")
                                 .replace("</b>", "")
                        ),
                        "Charset": "UTF-8",
                    },
                },
            },
        )
        return True
    except Exception as exc:
        print(
            f"{_LOG_PREFIX} WARNING: SES send failed: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return False


# ─── Alert payloads ───────────────────────────────────────────────────────────


def _read_flag_content() -> str:
    """Read the flag file body, returning a fallback string on error."""
    try:
        return _BROKEN_FLAG_PATH.read_text().strip()
    except Exception:
        return "(flag file unreadable)"


def _send_broken_alert(flag_content: str, elapsed_s: float) -> bool:
    """Dispatch the "PATCH contract broken" alert to all configured channels.

    Returns True iff at least one channel (Slack or SES) successfully
    delivered the notification.  The caller must NOT advance debounce state
    when this returns False — a delivery failure means the next poll should
    retry rather than treat the alert as already sent.
    """
    elapsed_str = (
        f"{int(elapsed_s // 60)} min" if elapsed_s < 7200 else f"{elapsed_s / 3600:.1f} h"
    )
    title = "PATCH-contract guard: persistent violation detected"
    slack_body = (
        f"The PATCH-route contract check has been failing. "
        f"The flag has been set for approximately *{elapsed_str}*.\n"
        f"> {flag_content}\n"
        f"Fix the missing `@patch_route_contract` decorator(s) in the route file "
        f"and save to clear the alert."
    )
    html_body = (
        f"<p><b>The PATCH-route contract check has been failing for "
        f"approximately {elapsed_str}.</b></p>"
        f"<pre style='background:#1e1b4b;color:#e2e8f0;padding:12px;"
        f"border-radius:6px;font-size:13px;'>{flag_content}</pre>"
        f"<p>Fix the missing <code>@patch_route_contract</code> decorator(s) "
        f"in the route file and save to clear the alert.</p>"
        f"<p style='font-size:12px;color:#6b7280;'>Flag file: "
        f"<code>{_BROKEN_FLAG_PATH}</code></p>"
        f"<p style='font-size:12px;color:#6b7280;'>Automated alert from "
        f"Syrabit patch_contract_guard (Task #95).</p>"
    )
    slack_ok = _slack_post(title, slack_body, "#dc2626")
    ses_ok = _ses_send(f"[Syrabit] {title}", html_body)
    delivered = slack_ok or ses_ok
    if delivered:
        print(
            f"{_LOG_PREFIX} broken alert sent "
            f"(slack={'ok' if slack_ok else 'skipped'}, "
            f"ses={'ok' if ses_ok else 'skipped'}).",
            flush=True,
        )
    else:
        print(
            f"{_LOG_PREFIX} WARNING: broken alert — no channel delivered "
            "(both Slack and SES skipped or failed); will retry next poll.",
            file=sys.stderr,
            flush=True,
        )
    return delivered


def _send_resolved_alert() -> bool:
    """Dispatch the "PATCH contract resolved" alert to all configured channels.

    Returns True iff at least one channel (Slack or SES) successfully
    delivered the notification.  The caller must NOT flip state to ``clear``
    when this returns False — the next poll will retry the resolved notify
    rather than silently dropping it.
    """
    title = "PATCH-contract guard: violation resolved"
    slack_body = (
        "The PATCH-route contract check is passing again. "
        "The flag file has been cleared by the watcher. No further action required."
    )
    html_body = (
        "<p><b>The PATCH-route contract check is passing again.</b></p>"
        "<p>The flag file has been cleared by the watcher. No further action required.</p>"
        "<p style='font-size:12px;color:#6b7280;'>Automated alert from "
        "Syrabit patch_contract_guard (Task #95).</p>"
    )
    slack_ok = _slack_post(title, slack_body, "#16a34a")
    ses_ok = _ses_send(f"[Syrabit] {title}", html_body)
    delivered = slack_ok or ses_ok
    if delivered:
        print(
            f"{_LOG_PREFIX} resolved alert sent "
            f"(slack={'ok' if slack_ok else 'skipped'}, "
            f"ses={'ok' if ses_ok else 'skipped'}).",
            flush=True,
        )
    else:
        print(
            f"{_LOG_PREFIX} WARNING: resolved alert — no channel delivered "
            "(both Slack and SES skipped or failed); will retry next poll.",
            file=sys.stderr,
            flush=True,
        )
    return delivered


# ─── Core poll logic ──────────────────────────────────────────────────────────


def _poll_once(state: dict) -> dict:
    """Execute one poll cycle and return the (possibly updated) state dict.

    State keys:
      ``last_state``      — ``"broken"`` or ``"clear"``
      ``last_notified_at``— ISO-8601 UTC timestamp of the last outbound alert
                           (or ``None`` when we have never paged)
    """
    flag_exists = _BROKEN_FLAG_PATH.is_file()
    last_state: str = state.get("last_state", "clear")
    last_notified_at: str | None = state.get("last_notified_at")
    now_iso = datetime.now(timezone.utc).isoformat()

    if flag_exists:
        elapsed_since_notif = _elapsed_since(last_notified_at)

        if last_state == "clear":
            flag_content = _read_flag_content()
            try:
                flag_age_s = time.time() - _BROKEN_FLAG_PATH.stat().st_mtime
            except OSError:
                flag_age_s = 0.0
            print(
                f"{_LOG_PREFIX} flag detected (age ~{flag_age_s:.0f}s) — sending broken alert.",
                flush=True,
            )
            if _send_broken_alert(flag_content, flag_age_s):
                state = {"last_state": "broken", "last_notified_at": now_iso}
            # else: delivery failed — keep state as "clear" so the next poll
            # retries the alert rather than debouncing a notification that was
            # never delivered.

        elif elapsed_since_notif >= _DEBOUNCE_S:
            flag_content = _read_flag_content()
            try:
                flag_age_s = time.time() - _BROKEN_FLAG_PATH.stat().st_mtime
            except OSError:
                flag_age_s = 0.0
            print(
                f"{_LOG_PREFIX} flag still present after "
                f"{elapsed_since_notif:.0f}s — re-alerting.",
                flush=True,
            )
            if _send_broken_alert(flag_content, flag_age_s):
                state = {"last_state": "broken", "last_notified_at": now_iso}
            # else: delivery failed — keep last_notified_at unchanged so the
            # re-alert fires again on the next poll cycle.

        else:
            remaining = _DEBOUNCE_S - elapsed_since_notif
            print(
                f"{_LOG_PREFIX} flag present — debounced "
                f"(next alert in {remaining:.0f}s).",
                flush=True,
            )

    else:
        if last_state == "broken":
            print(
                f"{_LOG_PREFIX} flag cleared — sending resolved alert.",
                flush=True,
            )
            if _send_resolved_alert():
                state = {"last_state": "clear", "last_notified_at": now_iso}
            # else: delivery failed — keep state as "broken" so the next poll
            # retries the resolved notification rather than silently dropping it.
        else:
            print(f"{_LOG_PREFIX} no flag — healthy.", flush=True)

    return state


# ─── Entry point ─────────────────────────────────────────────────────────────


def main() -> None:
    if not _SLACK_WEBHOOK_URL and not _ALERT_EMAIL:
        print(
            f"{_LOG_PREFIX} WARNING: neither SLACK_WEBHOOK_URL nor "
            "PATCH_CONTRACT_ALERT_EMAIL is set — notifications will not be "
            "delivered. Set at least one to receive alerts.",
            file=sys.stderr,
            flush=True,
        )

    print(
        f"{_LOG_PREFIX} starting "
        f"(poll {_POLL_S}s, debounce {_DEBOUNCE_S}s, "
        f"flag={_BROKEN_FLAG_PATH}, "
        f"slack={'configured' if _SLACK_WEBHOOK_URL else 'not set'}, "
        f"ses={'configured' if _ALERT_EMAIL else 'not set'}) …",
        flush=True,
    )

    state = _load_state()

    if _ONCE:
        state = _poll_once(state)
        _save_state(state)
        return

    while True:
        try:
            state = _poll_once(state)
            _save_state(state)
        except Exception as exc:
            print(
                f"{_LOG_PREFIX} WARNING: unexpected error during poll: {exc}",
                file=sys.stderr,
                flush=True,
            )
        time.sleep(_POLL_S)


if __name__ == "__main__":
    main()
