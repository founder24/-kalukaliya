"""Lightweight Slack/Discord webhook notifier.

Used to surface high-severity Web Security Scanner findings, Discovery
Engine indexing-health regressions, and Cloud Scheduler job failures
without standing up a full alert manager.

Configuration:
    SLACK_WEBHOOK_URL          generic Slack-compatible webhook URL
                               (Slack incoming-webhook OR Discord with
                               `?wait=true` query string).
    SLACK_WEBHOOK_DEFAULT_CHANNEL  optional `#channel-name` override.

Returns the same {status, elapsed_ms, error?} shape the other clients
use so the admin status endpoint can include it cleanly.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT_S = 6.0


def is_configured() -> bool:
    return bool((os.environ.get("SLACK_WEBHOOK_URL") or "").strip())


async def post_message(
    text: str,
    *,
    blocks: Optional[List[Dict[str, Any]]] = None,
    channel: Optional[str] = None,
    timeout_s: float = _HTTP_TIMEOUT_S,
) -> Dict[str, Any]:
    """Post a message to the configured Slack webhook."""
    url = (os.environ.get("SLACK_WEBHOOK_URL") or "").strip()
    if not url:
        return {"status": "disabled", "error": "SLACK_WEBHOOK_URL not set"}

    payload: Dict[str, Any] = {"text": text[:3000]}
    if blocks:
        payload["blocks"] = blocks
    ch = channel or (os.environ.get("SLACK_WEBHOOK_DEFAULT_CHANNEL") or "").strip()
    if ch:
        payload["channel"] = ch

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(url, json=payload)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if r.status_code >= 400:
            return {"status": "error", "elapsed_ms": elapsed_ms,
                    "error": f"HTTP {r.status_code}: {r.text[:300]}"}
        return {"status": "ok", "elapsed_ms": elapsed_ms,
                "response": r.text[:200]}
    except httpx.TimeoutException:
        return {"status": "error",
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": "timeout"}
    except Exception as exc:
        return {"status": "error",
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


SEVERITY_EMOJI = {
    "CRITICAL": ":rotating_light:",
    "HIGH":     ":red_circle:",
    "MEDIUM":   ":large_orange_circle:",
    "LOW":      ":large_yellow_circle:",
    "MINIMAL":  ":white_circle:",
}


async def post_wss_findings(
    findings: List[Dict[str, Any]],
    *,
    min_severity: str = "HIGH",
    scan_run_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Format Web Security Scanner findings as a Slack message and post.

    `findings` should be the list returned by web_security_scanner_client.list_findings.
    Skips silently when no findings meet `min_severity`.
    """
    order = ["MINIMAL", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
    try:
        floor = order.index(min_severity.upper())
    except ValueError:
        floor = order.index("HIGH")

    matching = []
    for f in findings or []:
        sev = (f.get("severity") or "").upper()
        if sev in order and order.index(sev) >= floor:
            matching.append(f)

    if not matching:
        return {"status": "ok", "skipped": True,
                "reason": f"no findings ≥ {min_severity}",
                "checked_count": len(findings or [])}

    matching.sort(key=lambda f: order.index((f.get("severity") or "MINIMAL").upper()),
                  reverse=True)
    head = (f"*Web Security Scanner — {len(matching)} new finding"
            f"{'s' if len(matching) != 1 else ''}*")
    if scan_run_name:
        head += f"\n_scan run: `{scan_run_name.split('/')[-1]}`_"

    lines = [head, ""]
    for f in matching[:15]:
        sev = (f.get("severity") or "MINIMAL").upper()
        emoji = SEVERITY_EMOJI.get(sev, ":grey_question:")
        ftype = f.get("finding_type") or "UNKNOWN"
        url = f.get("fuzzed_url") or f.get("final_url") or "(no url)"
        lines.append(f"{emoji} *{sev}* `{ftype}` — {url}")
        if f.get("description"):
            desc = f["description"][:160].replace("\n", " ")
            lines.append(f"    _{desc}_")
    if len(matching) > 15:
        lines.append(f"\n…and {len(matching) - 15} more.")
    return await post_message("\n".join(lines))
