"""Task #750 — Surface the Trustpilot AggregateRating JSON-LD verifier
result on the admin dashboard.

The build-time inject step (Task #748) and the daily scheduled verifier
(`.github/workflows/trustpilot-jsonld-prod.yml`) already protect SERP
star coverage — but failures only land in GitHub Actions email, which
the non-engineering ops/marketing team doesn't watch. This module
exposes a tiny store/serve pair so the verifier can ship its per-URL
result table to the same admin dashboard the team already checks for
delivery + alert health.

Endpoints
---------
* ``POST /api/admin/trustpilot-jsonld/report`` — webhook the scheduled
  workflow (and any future on-demand runs) calls with the JSON the
  verifier produced via ``--json-out=<path>``. Authenticated by the
  shared ``TRUSTPILOT_REFRESH_SECRET`` header (same secret already used
  by the aggregate refresh webhook in ``routes/config.py``) so we don't
  need to provision/leak a second secret. The latest report replaces
  the previous one — we only care about the most recent run.

* ``GET /api/admin/trustpilot-jsonld/report`` — admin-protected read
  used by the AdminHealth tile. Returns ``{configured: false, ...}``
  when no report has been ingested yet so the UI can show a clear
  "no data" state instead of an error.

Storage
-------
A single Mongo doc keyed by ``_id="trustpilot_jsonld_verifier_report"``
in ``db.api_config`` (the same collection ``routes/admin_monetization.py``
uses for similar telemetry rows). One doc, replaced atomically — the
admin tile reads it for the current pass/fail snapshot.

Task #754 — alongside the "latest" doc we also append every run to the
``trustpilot_jsonld_runs`` collection (TTL-indexed on ``ts`` for 30 days)
so the dashboard tile can render a sparkline of pass-rate over the
trailing month and ops can spot a slow-moving regression (e.g. a single
URL failing 3 days in a row) before it bites SERP coverage. The ``ts``
field is a real ``datetime`` (not the ISO string we keep on the latest
doc) because Mongo's TTL monitor only honours BSON dates.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException

from auth_deps import get_admin_user
from deps import db

logger = logging.getLogger(__name__)
router = APIRouter()

_DOC_ID = "trustpilot_jsonld_verifier_report"

# Task #761 — per-URL consecutive-failure threshold for the "URL failing
# N days in a row" escalation alert. Three matches the intuition in the
# task description (today's PASS→FAIL dedup suppresses repeat pages, so
# a single URL that silently keeps failing can otherwise go un-paged
# after its first regression email).
_STREAK_THRESHOLD = 3

# Task #754 — append-only history collection with a 30-day TTL so the
# admin tile can render a trailing-month sparkline without unbounded
# growth.
_RUNS_COLLECTION = "trustpilot_jsonld_runs"
_RUNS_TTL_SECONDS = 30 * 24 * 3600

# Reuse the aggregate-refresh secret (Task #749) — the workflow already
# has it in repo secrets and the backend already requires it for the
# sibling Trustpilot webhook, so we avoid a second knob to forget.
_SECRET_ENV = "TRUSTPILOT_REFRESH_SECRET"


def _expected_secret() -> str:
    return (os.environ.get(_SECRET_ENV) or "").strip()


async def ensure_trustpilot_jsonld_runs_index() -> None:
    """Idempotently ensure the 30-day TTL index on the per-run history
    collection. Called from server.py lifespan; safe to call repeatedly
    (Mongo no-ops when an equivalent index already exists)."""
    if db is None:
        return
    try:
        await db[_RUNS_COLLECTION].create_index(
            "ts", expireAfterSeconds=_RUNS_TTL_SECONDS,
        )
        # Sparkline query sorts by ts asc within the trailing 30 days —
        # this index makes that scan touch only the live window.
        await db[_RUNS_COLLECTION].create_index([("ts", -1)])
    except Exception as exc:
        logger.warning(
            "[trustpilot-jsonld] runs TTL index create failed: %s", exc,
        )


async def _append_trustpilot_jsonld_run(doc: dict[str, Any]) -> None:
    """Best-effort append of one run to the TTL'd history collection.
    Never raises into the ingest path — losing a sparkline point must
    not fail the verifier webhook."""
    if db is None:
        return
    try:
        ts = datetime.now(timezone.utc)
        # Keep the row narrow on purpose: the sparkline only needs the
        # aggregate counters + average ratingValue. Per-URL detail lives
        # on the "latest" doc, which is sufficient for the table render
        # and avoids ballooning storage 30x.
        rating_values = [
            r["ratingValue"] for r in doc.get("results", [])
            if isinstance(r.get("ratingValue"), (int, float))
        ]
        avg_rating = (
            sum(rating_values) / len(rating_values)
            if rating_values else None
        )
        run_doc = {
            "ts": ts,
            "generatedAt": doc.get("generatedAt"),
            "target": doc.get("target"),
            "origin": doc.get("origin"),
            "totalUrls": doc.get("totalUrls", 0),
            "passed": doc.get("passed", 0),
            "failed": doc.get("failed", 0),
            "ok": bool(doc.get("ok")),
            "avgRatingValue": avg_rating,
            "runUrl": doc.get("runUrl"),
        }
        await db[_RUNS_COLLECTION].insert_one(run_doc)
    except Exception as exc:
        logger.warning(
            "[trustpilot-jsonld] history append failed: %s", exc,
        )


def _coerce_results(raw: Any) -> list[dict[str, Any]]:
    """Defensively normalise the per-URL list — the verifier can emit
    nulls for ratingValue/reviewCount on failure, and we never want a
    single bad row to break the dashboard render."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "").strip()
        if not url:
            continue
        item: dict[str, Any] = {
            "url": url,
            "pass": bool(r.get("pass")),
            "status": r.get("status"),
        }
        if r.get("ratingValue") is not None:
            try:
                item["ratingValue"] = float(r["ratingValue"])
            except (TypeError, ValueError):
                item["ratingValue"] = None
        if r.get("reviewCount") is not None:
            try:
                item["reviewCount"] = int(r["reviewCount"])
            except (TypeError, ValueError):
                item["reviewCount"] = None
        if r.get("reason"):
            item["reason"] = str(r["reason"])[:300]
        out.append(item)
    return out


@router.post("/admin/trustpilot-jsonld/report")
async def ingest_trustpilot_jsonld_report(
    body: dict[str, Any] = Body(...),
    x_trustpilot_refresh_secret: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Persist the latest verifier run so the admin dashboard can render
    pass/fail per URL. Auth: shared ``TRUSTPILOT_REFRESH_SECRET`` header
    (same secret as the aggregate refresh webhook). Returns 503 when the
    secret isn't configured (fail-closed) and 401 on mismatch."""
    expected = _expected_secret()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="trustpilot_refresh_secret_not_configured",
        )
    provided = (x_trustpilot_refresh_secret or "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid_refresh_secret")

    results = _coerce_results(body.get("results"))

    def _safe_int(value: Any, default: int) -> int:
        """Coerce summary counters defensively. A malformed webhook
        payload should not 500 — better to fall back to the value we
        can derive from ``results`` so the dashboard still updates."""
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    derived_failed = sum(1 for r in results if not r["pass"])
    total = _safe_int(body.get("totalUrls"), len(results))
    failed = _safe_int(body.get("failed"), derived_failed)
    passed = _safe_int(body.get("passed"), max(total - failed, 0))
    ok_explicit = body.get("ok")
    ok = bool(ok_explicit) if ok_explicit is not None else (failed == 0)

    generated_at = body.get("generatedAt")
    if not isinstance(generated_at, str) or not generated_at:
        generated_at = datetime.now(timezone.utc).isoformat()

    # Task #753 — load prior report BEFORE replacing so we can diff
    # per-URL pass/fail state and fan out a regression / recovery email
    # without ops having to actively poll the dashboard. The verifier
    # webhook only fires once per scheduled GitHub Actions run, so the
    # window between the read and write here is negligible — best-effort
    # is fine, and the alert path itself never raises into the ingest.
    prior_doc: dict[str, Any] = {}
    try:
        prior_doc = await db.api_config.find_one({"_id": _DOC_ID}) or {}
    except Exception as exc:
        logger.debug("trustpilot jsonld prior load failed: %s", exc)
        prior_doc = {}

    new_failed_urls = sorted({r["url"] for r in results if not r["pass"]})

    # Task #761 — compute per-URL consecutive-failure streaks BEFORE the
    # write so the next ingest has a canonical ledger to diff against,
    # and so the streak alert (fired below after the write) sees exactly
    # the state we just persisted. The helper is pure; callers fire on
    # the returned `newly_streaking_rows` to page ops.
    url_streaks, alerted_streaks, newly_streaking_rows = (
        _compute_url_failure_streaks(prior_doc, results)
    )

    doc = {
        "_id": _DOC_ID,
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "ingestedAt": datetime.now(timezone.utc).isoformat(),
        "target": str(body.get("target") or "remote"),
        "origin": body.get("origin") or None,
        "totalUrls": total,
        "passed": passed,
        "failed": failed,
        "ok": ok,
        "results": results,
        # Preserve the GH Actions context so the dashboard can deep-link
        # to the failing run when ops wants the full log.
        "runUrl": (body.get("runUrl") or "") or None,
        # Per-URL dedup ledger for Task #753: which URLs we've already
        # paged ops about in the *current* failing streak. Cleared the
        # moment a URL flips back to pass, so the next regression on
        # the same URL re-pages.
        "alertedFailedUrls": new_failed_urls,
        # Task #761 — per-URL consecutive-failure streak counters +
        # escalation-alert dedup ledger. Both clear automatically when
        # a URL flips back to pass (see _compute_url_failure_streaks).
        "urlFailureStreaks": url_streaks,
        "alertedStreaks": alerted_streaks,
    }
    await db.api_config.replace_one({"_id": _DOC_ID}, doc, upsert=True)
    logger.info(
        "trustpilot jsonld report ingested: %s/%s pass (target=%s)",
        passed, total, doc["target"],
    )

    # Task #754 — append this run to the 30-day TTL'd history so the
    # admin tile can render a trailing-month sparkline. Best-effort:
    # the "latest" doc above is what the existing tile depends on, and
    # losing one history point must not fail the verifier webhook.
    await _append_trustpilot_jsonld_run(doc)

    # Best-effort fan-out — never fail ingest if the alert path errors.
    try:
        await _maybe_dispatch_jsonld_alerts(prior_doc, doc)
    except Exception as exc:
        logger.warning("trustpilot jsonld alert dispatch errored: %s", exc)

    # Task #761 — escalation alert when a URL has failed 3+ consecutive
    # runs. Fires AFTER the regular regression alerter because today's
    # PASS→FAIL dedup may have already suppressed paging on this URL.
    try:
        if newly_streaking_rows:
            await _send_jsonld_streak_alert(newly_streaking_rows, doc)
    except Exception as exc:
        logger.warning(
            "trustpilot jsonld streak alert errored: %s", exc,
        )

    return {"ok": True, "stored": True, "passed": passed, "failed": failed}


# ─── Task #761 — per-URL consecutive-failure streak detector ───────────────
#
# Motivation: Task #753's regression alert dedups per-URL once it flips
# PASS→FAIL — so a URL that silently keeps failing day after day only
# pages ops once, on day 1. That's correct for high-churn regressions
# but wrong for slow-moving ones (the whole reason Task #754 added the
# 30-day history). This detector escalates: when a URL has failed
# ``_STREAK_THRESHOLD`` consecutive runs AND we haven't yet paged on
# this particular streak, fire one streak alert. The streak ledger
# lives on the same canonical doc as the existing per-URL dedup ledger
# (``alertedStreaks``), so no extra collection is needed.


def _compute_url_failure_streaks(
    prior_doc: dict[str, Any],
    new_results: list[dict[str, Any]],
) -> tuple[dict[str, int], list[str], list[dict[str, Any]]]:
    """Return (new_streaks, new_alerted_streaks, newly_streaking_rows).

    * ``new_streaks`` — {url: consecutive_fail_count}. URLs that passed
      are dropped (streak resets to 0), which keeps the doc narrow and
      makes the "streak has been broken" semantics implicit.
    * ``new_alerted_streaks`` — sorted URL list we've already paged on
      in their current failing streak. A URL stays in this ledger while
      it keeps failing and falls out the moment it passes, so the next
      PASS→FAIL→FAIL→FAIL streak on the same URL re-pages.
    * ``newly_streaking_rows`` — full result rows (with an added
      ``streak`` field) for URLs whose streak just crossed the threshold
      on this run. Callers fire exactly one alert per row.

    Pure/sync — safe to test in isolation without mocking the DB layer.
    """
    prior_streaks_raw = (prior_doc or {}).get("urlFailureStreaks") or {}
    # Defensive coercion — a corrupted doc (e.g. migrated from before
    # this field existed, or hand-edited) must not crash ingest.
    prior_streaks: dict[str, int] = {}
    if isinstance(prior_streaks_raw, dict):
        for k, v in prior_streaks_raw.items():
            try:
                prior_streaks[str(k)] = int(v)
            except (TypeError, ValueError):
                continue
    prior_alerted: set[str] = {
        str(u) for u in ((prior_doc or {}).get("alertedStreaks") or [])
    }

    new_streaks: dict[str, int] = {}
    new_alerted: set[str] = set()
    newly_streaking: list[dict[str, Any]] = []

    for row in new_results:
        url = row.get("url")
        if not url:
            continue
        if row.get("pass"):
            # Pass resets — drop from both ledgers implicitly (we simply
            # don't carry them forward).
            continue
        # Take the prior streak counter once — a malformed payload that
        # lists the same URL twice must not double-increment, and must
        # not fire the alert twice for the same threshold crossing.
        if url in new_streaks:
            streak = new_streaks[url]
        else:
            streak = prior_streaks.get(url, 0) + 1
            new_streaks[url] = streak
        if url in prior_alerted or url in new_alerted:
            # Already paged on this ongoing streak (either in a prior
            # ingest, or earlier in this very payload) — keep the dedup
            # flag so the next run knows not to re-page.
            new_alerted.add(url)
        elif streak >= _STREAK_THRESHOLD:
            new_alerted.add(url)
            newly_streaking.append({**row, "streak": streak})

    return new_streaks, sorted(new_alerted), newly_streaking


async def _send_jsonld_streak_alert(
    streaking_rows: list[dict[str, Any]], new_doc: dict[str, Any],
) -> None:
    """Page ops when one or more URLs have failed ``_STREAK_THRESHOLD``
    consecutive runs. Uses the same notification channel as the Task
    #753 regression / recovery alerts so everything lands in one
    admin inbox."""
    title = (
        f"Trustpilot JSON-LD: {len(streaking_rows)} URL(s) failing "
        f"{_STREAK_THRESHOLD}+ runs in a row"
    )
    lines: list[str] = []
    for row in streaking_rows:
        streak = int(row.get("streak", _STREAK_THRESHOLD))
        lines.append(f"{_format_failing_row(row)} (streak: {streak})")
    msg = (
        f"The following URL(s) have failed the Trustpilot AggregateRating "
        f"JSON-LD verifier on {_STREAK_THRESHOLD} or more consecutive "
        "scheduled runs. The initial PASS→FAIL regression alert was "
        "already emitted on day 1; this escalation prompt fires because "
        "the regression has not been remediated.\n\n"
        + "\n".join(lines)
    )
    run_url = new_doc.get("runUrl")
    if run_url:
        msg += f"\n\nLatest GitHub Actions run: {run_url}"
    await _emit_jsonld_alert(
        title=title,
        message=msg,
        kind="streak",
        run_url=run_url,
        urls=[r["url"] for r in streaking_rows],
    )


# ─── Task #753 — regression / recovery email + in-app notifications ────────
#
# Mirrors the helper shape used by ``routes/admin_trustpilot_alerts.py``
# (Task #728) so the inbox looks consistent across all Trustpilot alert
# channels. We deliberately reuse:
#
# * ``email_templates._send`` for the SES send (same path the
#   existing aggregate-feed alerter uses);
# * ``db_ops.supa_insert_notification`` for the in-app notification —
#   which is also what powers the existing Slack/webhook fan-out for
#   admin alerts (the notification record is the single source of
#   truth that downstream channels subscribe to).
#
# Dedup contract (per the task spec):
# * regression email: sent the first time a URL flips PASS→FAIL, then
#   suppressed for that URL until it flips back to pass. The set of
#   "currently alerted" URLs is persisted on the report doc itself
#   (``alertedFailedUrls``) so we don't need a second collection.
# * recovery email: sent exactly once when the previously-non-empty
#   alerted set returns to empty (i.e. all URLs pass again).


async def _maybe_dispatch_jsonld_alerts(
    prior_doc: dict[str, Any], new_doc: dict[str, Any],
) -> None:
    """Diff prior vs new per-URL state and emit regression / recovery
    notifications. Best-effort: callers swallow exceptions."""
    prior_alerted = set(
        (prior_doc or {}).get("alertedFailedUrls") or []
    )
    new_failed = {r["url"] for r in new_doc.get("results", []) if not r["pass"]}
    newly_failed = new_failed - prior_alerted

    if newly_failed:
        failing_rows = [
            r for r in new_doc["results"] if r["url"] in newly_failed
        ]
        await _send_jsonld_regression_alert(failing_rows, new_doc)

    if prior_alerted and not new_failed:
        await _send_jsonld_recovery_alert(new_doc)


def _format_failing_row(row: dict[str, Any]) -> str:
    bits: list[str] = [row.get("url") or "?"]
    rv = row.get("ratingValue")
    rc = row.get("reviewCount")
    if rv is not None:
        bits.append(f"ratingValue={rv}")
    if rc is not None:
        bits.append(f"reviewCount={rc}")
    if row.get("reason"):
        bits.append(str(row["reason"]))
    return "  - " + " — ".join(bits)


async def _send_jsonld_regression_alert(
    failing_rows: list[dict[str, Any]], new_doc: dict[str, Any],
) -> None:
    title = (
        "Trustpilot JSON-LD regression: AggregateRating missing on "
        f"{len(failing_rows)} production URL(s)"
    )
    lines = [_format_failing_row(r) for r in failing_rows]
    msg = (
        "The Trustpilot AggregateRating JSON-LD verifier reports the "
        "following URL(s) flipped PASS→FAIL on the live production "
        "origin. SERP star rich-snippets will disappear from these "
        "pages on Google's next crawl.\n\n"
        + "\n".join(lines)
    )
    run_url = new_doc.get("runUrl")
    if run_url:
        msg += f"\n\nGitHub Actions run: {run_url}"
    await _emit_jsonld_alert(
        title=title,
        message=msg,
        kind="regression",
        run_url=run_url,
        urls=[r["url"] for r in failing_rows],
    )


async def _send_jsonld_recovery_alert(new_doc: dict[str, Any]) -> None:
    title = (
        "Trustpilot JSON-LD recovered: AggregateRating present on all URLs"
    )
    msg = (
        "All previously-failing Trustpilot AggregateRating JSON-LD URLs "
        "are passing again on the production origin. SERP stars should "
        "reappear on Google's next crawl. No further action required."
    )
    run_url = new_doc.get("runUrl")
    if run_url:
        msg += f"\n\nGitHub Actions run: {run_url}"
    await _emit_jsonld_alert(
        title=title,
        message=msg,
        kind="recovery",
        run_url=run_url,
        urls=[],
    )


async def _emit_jsonld_alert(
    title: str, message: str, kind: str,
    run_url: Optional[str], urls: list[str],
) -> None:
    """Insert the in-app notification (which the existing Slack/webhook
    fan-out subscribes to), email every admin, and — when configured —
    fire a dedicated Slack incoming-webhook so the ops channel sees the
    page the same instant the email goes out (Task #757). All three
    legs are best-effort and isolated so a failure in one channel does
    not suppress the others."""
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        from db_ops import supa_insert_notification
        await supa_insert_notification({
            "id": str(uuid.uuid4()),
            "title": title,
            "message": message,
            "type": "error" if kind == "regression" else "info",
            "channel": "in_app",
            "audience": "admins",
            "status": "sent",
            "created_at": now_iso,
            "sent_at": now_iso,
            "meta": {
                "kind": "trustpilot_jsonld_alert",
                "state": kind,
                "urls": urls,
                "runUrl": run_url,
            },
        })
    except Exception as exc:
        logger.debug(
            "[trustpilot-jsonld-alerts] notification persist failed: %s", exc,
        )

    asyncio.create_task(_email_admins_about_jsonld(title, message, kind))
    asyncio.create_task(
        _post_jsonld_slack_alert(title, message, kind, run_url, urls)
    )


# ─── Task #757 — dedicated Slack fan-out for trustpilot_jsonld_alert ──────
#
# The in-app notification persisted above is what a generic Slack/webhook
# fan-out *should* subscribe to, but in production we observed that the
# generic fan-out doesn't reliably forward this notification kind to the
# ops channel — so until that's fixed end-to-end, wire a dedicated
# incoming-webhook here. Gated on ``SLACK_TRUSTPILOT_WEBHOOK_URL`` so it
# is a true no-op when the env var is unset (no network call, no logs at
# anything noisier than DEBUG).

# Task #972 — the env-var name and the read-and-strip helper now live
# in ``routes.slack_alerter_config`` alongside the three cron silence-
# alerter env-var names that Task #969 collapsed there. Keeping the
# in-module ``_SLACK_WEBHOOK_ENV`` / ``_slack_webhook_url`` aliases
# preserves the symbols this module's tests
# (``tests/test_admin_trustpilot_jsonld_status.py``) and other call
# sites already reference.
from routes.slack_alerter_config import (
    SLACK_TRUSTPILOT_WEBHOOK_ENV as _SLACK_WEBHOOK_ENV,
    slack_config_for,
    slack_webhook_url_for,
)


def _slack_webhook_url() -> str:
    return slack_webhook_url_for(_SLACK_WEBHOOK_ENV)


def _slack_payload_for_jsonld_alert(
    title: str, message: str, kind: str,
    run_url: Optional[str], urls: list[str],
) -> dict[str, Any]:
    """Build the Slack incoming-webhook JSON body. Block Kit is used so
    the run-link button shows up natively, with a ``text`` fallback for
    push notifications and clients that don't render blocks."""
    emoji = {
        "regression": ":rotating_light:",
        "streak": ":warning:",
        "recovery": ":white_check_mark:",
    }.get(kind, ":bell:")
    header_text = f"{emoji} {title}"
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header_text[:150]},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                # Slack section text caps at 3000 chars — truncate
                # defensively so a giant URL list can never blow up
                # the webhook with a 400.
                "text": (message or "")[:2900],
            },
        },
    ]
    if run_url:
        blocks.append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "Open GH Actions run"},
                "url": run_url,
            }],
        })
    return {"text": header_text, "blocks": blocks}


async def _post_jsonld_slack_alert(
    title: str, message: str, kind: str,
    run_url: Optional[str], urls: list[str],
) -> None:
    """Best-effort POST to the dedicated Slack incoming webhook. No-op
    when ``SLACK_TRUSTPILOT_WEBHOOK_URL`` is unset; never raises."""
    webhook_url = _slack_webhook_url()
    if not webhook_url:
        return
    payload = _slack_payload_for_jsonld_alert(
        title, message, kind, run_url, urls,
    )
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code >= 400:
                logger.warning(
                    "[trustpilot-jsonld-alerts] slack webhook %s: %s",
                    resp.status_code, resp.text[:200],
                )
    except Exception as exc:
        logger.debug(
            "[trustpilot-jsonld-alerts] slack webhook post failed: %s", exc,
        )


async def _email_admins_about_jsonld(
    title: str, message: str, kind: str,
) -> None:
    """Email every admin (best-effort). Mirrors the helper shape used
    by ``routes/admin_trustpilot_alerts.py`` (Task #728) so the inbox
    looks consistent across all Trustpilot alert channels."""
    try:
        from email_templates import _send  # internal helper, intentional
    except Exception as exc:
        logger.debug(
            "[trustpilot-jsonld-alerts] email helper unavailable: %s", exc,
        )
        return
    admins: list[str] = []
    try:
        if db is not None:
            cursor = db.users.find(
                {"is_admin": True}, {"_id": 0, "email": 1}
            )
            async for u in cursor:
                e = (u.get("email") or "").strip()
                if e:
                    admins.append(e)
    except Exception as exc:
        logger.debug(
            "[trustpilot-jsonld-alerts] admin lookup failed: %s", exc,
        )
    color = "#16a34a" if kind == "recovery" else "#dc2626"
    safe_msg = (message or "").replace("<", "&lt;").replace(">", "&gt;")
    html = (
        f"<h2 style='color:{color};margin:0 0 8px;'>{title}</h2>"
        f"<p style='font-size:14px;line-height:1.6;color:#374151;"
        f"white-space:pre-line;'>{safe_msg}</p>"
        f"<p style='font-size:12px;color:#6b7280;'>This is an automated "
        f"alert from the Syrabit Trustpilot JSON-LD verifier "
        f"(Task #753).</p>"
    )
    for email in admins:
        try:
            await _send(email, title, html)
        except Exception as exc:
            logger.debug(
                "[trustpilot-jsonld-alerts] email send failed for %s: %s",
                email, exc,
            )


@router.get("/admin/trustpilot-jsonld/report")
async def get_trustpilot_jsonld_report(
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Return the most recent verifier report for the AdminHealth tile.
    Always 200; the UI branches on ``configured`` / ``ok``.

    Task #968 — also surface ``slackConfigured`` (boolean) +
    ``slackWebhookEnv`` (env var name) so the dashboard tile can
    render the same "Slack ✓ / ✗" badge that Task #964 added to the
    three cron pills (cf-waf-drift, edge-proxy-deploy,
    unified-logs-cf-pull). The webhook URL itself must NEVER appear
    in the response — only the *configuration health* of the
    dedicated per-event Slack fan-out from
    :func:`_post_jsonld_slack_alert`.
    """
    base = slack_config_for(_SLACK_WEBHOOK_ENV)
    doc = await db.api_config.find_one({"_id": _DOC_ID})
    if not doc:
        return {"configured": False, "report": None, **base}
    doc.pop("_id", None)
    return {"configured": True, "report": doc, **base}


@router.get("/admin/trustpilot-jsonld/history")
async def get_trustpilot_jsonld_history(
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Task #754 — return the last 30 days of verifier runs (one row per
    run, oldest first) so the AdminHealth tile can render a sparkline of
    pass-rate / ratingValue trend. The collection is TTL'd to 30 days so
    we can return everything we have without an extra cutoff filter, but
    we still bound the response to 200 rows in case the verifier is ever
    re-run more than ~6 times per day during an incident."""
    if db is None:
        return {"points": [], "ttlDays": 30}
    points: list[dict[str, Any]] = []
    try:
        # Sort DESC + limit so a high-frequency rerun day (e.g. ops
        # re-firing the workflow during an incident) keeps the most
        # *recent* 200 rows rather than the oldest. Reverse in-memory
        # below so the chart still renders oldest-first left to right.
        cursor = db[_RUNS_COLLECTION].find(
            {},
            {
                "_id": 0, "ts": 1, "totalUrls": 1, "passed": 1,
                "failed": 1, "ok": 1, "avgRatingValue": 1,
            },
        ).sort("ts", -1).limit(200)
        async for row in cursor:
            ts = row.get("ts")
            ts_iso: Optional[str] = None
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ts_iso = ts.isoformat()
            total = int(row.get("totalUrls") or 0)
            passed = int(row.get("passed") or 0)
            pass_rate = (passed / total) if total > 0 else None
            points.append({
                "ts": ts_iso,
                "totalUrls": total,
                "passed": passed,
                "failed": int(row.get("failed") or 0),
                "ok": bool(row.get("ok")),
                "passRate": pass_rate,
                "avgRatingValue": row.get("avgRatingValue"),
            })
    except Exception as exc:
        logger.warning(
            "[trustpilot-jsonld] history fetch failed: %s", exc,
        )
    # We queried newest-first to bound the window correctly under
    # high-frequency reruns; reverse so the chart renders left→right
    # in chronological order.
    points.reverse()
    return {"points": points, "ttlDays": 30}


# ─── Task #758 — recent regression/recovery/streak alert history ─────────


# Every alert this module emits prefixes its title with the same fixed
# string so we can retrieve the history by title-prefix match without
# relying on `meta` (which the PG and Supabase insert paths in
# ``supa_insert_notification`` silently drop).
_ALERT_TITLE_PREFIX = "Trustpilot JSON-LD"


_URL_LINE_RE = re.compile(r"^\s*-\s+(\S+?)(?:\s+—|\s+\(|\s*$)", re.MULTILINE)


def _extract_jsonld_alert_urls(message: str) -> list[str]:
    """Pull the per-URL bullets out of an alert message body. Every
    regression/streak message built by ``_send_jsonld_*_alert`` formats
    failing URLs with ``_format_failing_row`` → ``"  - <url> — ..."``
    (streak appends ``" (streak: N)"``). Since ``meta`` is dropped on
    the PG/Supabase insert paths, the message body is the only
    reliable place to recover which URLs each alert fired for — which
    is what ops need to see in the history strip to spot a flappy
    URL. Duplicates are removed while preserving order."""
    if not message:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in _URL_LINE_RE.finditer(message):
        u = match.group(1).strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _classify_jsonld_alert_title(title: str) -> str:
    """Map a persisted notification title back to the alert kind the
    dispatcher emitted, so the UI can colour-code without parsing
    free-form strings. Unknown variants default to ``regression`` (the
    most common / loudest path), which is a safe visual fallback."""
    t = (title or "").lower()
    if "recover" in t:
        return "recovery"
    if "runs in a row" in t:
        return "streak"
    return "regression"


@router.get("/admin/trustpilot-jsonld/alerts")
async def get_trustpilot_jsonld_alerts(
    limit: int = 10,
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Task #758 — return the last N Trustpilot JSON-LD alert events
    (regression / recovery / 3-day streak) so the AdminHealth tile can
    render an alert-history strip. Each event's ``state`` is classified
    from the title so the UI can render a colour-coded chip without
    having to parse the free-form message body."""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 10
    n = max(1, min(50, n))

    from db_ops import supa_get_notifications_by_title_prefix
    try:
        rows = await supa_get_notifications_by_title_prefix(
            _ALERT_TITLE_PREFIX, n,
        )
    except Exception as exc:
        logger.warning("[trustpilot-jsonld] alerts fetch failed: %s", exc)
        rows = []

    events: list[dict[str, Any]] = []
    for row in rows or []:
        created = row.get("created_at")
        if isinstance(created, datetime):
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            created = created.isoformat()
        title = str(row.get("title") or "")
        raw_message = str(row.get("message") or "")
        events.append({
            "id": str(row.get("id") or ""),
            "title": title,
            # Truncate the message — the full body can be dozens of
            # lines and the history strip only needs a summary line.
            "message": raw_message[:500],
            # Parsed-out URL list so the UI can render exactly which
            # URL(s) each alert fired for (flappy-URL detection).
            # Parsed from the full message before truncation so long
            # lists aren't silently dropped.
            "urls": _extract_jsonld_alert_urls(raw_message),
            "type": row.get("type") or "info",
            "state": _classify_jsonld_alert_title(title),
            "created_at": created,
        })
    return {"events": events, "limit": n}
