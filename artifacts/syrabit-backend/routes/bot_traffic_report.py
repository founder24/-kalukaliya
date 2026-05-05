"""Syrabit.ai — Weekly bot-traffic report (Task #314).

Every Monday morning (09:30 IST = 04:00 UTC) this module pulls a 7-day
Cloudflare verified-bot breakdown for the syrabit.ai zone, computes
week-over-week deltas per bot category (Search Engine Crawler, AI Crawler,
Monitoring & Analytics, …), renders an HTML summary with the biggest movers
highlighted at the top, and emails it to the admin inbox via Resend.

Design mirrors the existing ``_seo_weekly_digest_*`` pattern in
``routes/bot_discovery.py``:

  * Pure compose/format functions so everything is unit-testable without
    hitting Mongo, Cloudflare, or Resend.
  * Per-ISO-week dedup via ``db.job_locks`` (atomic compare-and-set +
    bootstrap insert on a unique ``_id``) so multi-replica deployments
    never double-send.
  * A ±15-minute tolerance window around the target time, with 5-minute
    polling, so a restart inside the window still fires the email.
  * CF API failures trigger a fallback admin alert via
    ``metrics._dispatch_alert`` instead of silently dropping the week.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Config ──────────────────────────────────────────────────────────────────
_BOT_REPORT_DASHBOARD_URL = "https://syrabit.ai/admin/seo"
_BOT_REPORT_LOCK_ID = "bot_traffic_weekly_report_lock"
_BOT_REPORT_API_CONFIG_KEY = "bot_traffic_weekly_report_last_iso_week"

# Fire 30 min after the SEO digest so both emails don't land in the same
# second. Monday 04:00 UTC = 09:30 IST.
_BOT_REPORT_TARGET_WEEKDAY = 0     # Monday
_BOT_REPORT_TARGET_HOUR_UTC = 4
_BOT_REPORT_TARGET_MINUTE_UTC = 0
_BOT_REPORT_TOLERANCE_MINUTES = 15
_BOT_REPORT_LOOP_SLEEP_S = 300     # poll every 5 minutes

# Categories we always surface, even if Cloudflare returned zero requests
# for them, so "Search Engine Crawler: 0 → 0" is still visible when traffic
# actually collapses.
_ALWAYS_SHOW_CATEGORIES = (
    "Search Engine Crawler",
    "AI Crawler",
    "Search Engine Optimization",
    "Monitoring & Analytics",
)


# ── Pure helpers ────────────────────────────────────────────────────────────

def _iso_week_tag(now: datetime) -> str:
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _should_send_bot_report_now(now_utc: datetime, last_iso_week: str) -> bool:
    """True iff ``now_utc`` is within the Monday-morning window AND we
    haven't already sent a report for this ISO week."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    if now_utc.weekday() != _BOT_REPORT_TARGET_WEEKDAY:
        return False
    target = now_utc.replace(
        hour=_BOT_REPORT_TARGET_HOUR_UTC,
        minute=_BOT_REPORT_TARGET_MINUTE_UTC,
        second=0, microsecond=0,
    )
    delta_minutes = abs((now_utc - target).total_seconds()) / 60.0
    if delta_minutes > _BOT_REPORT_TOLERANCE_MINUTES:
        return False
    return _iso_week_tag(now_utc) != (last_iso_week or "")


def _pct_delta(cur: int, prev: int) -> Optional[float]:
    """Return signed % change, or None when the prior window was zero (in
    which case "+∞%" is meaningless — the UI shows 'new' instead)."""
    if prev <= 0:
        return None
    return round((cur - prev) / prev * 100.0, 1)


def _compose_bot_traffic_report(current: Dict[str, Any],
                                prior: Dict[str, Any],
                                *,
                                now: Optional[datetime] = None,
                                dashboard_url: str = _BOT_REPORT_DASHBOARD_URL,
                                zone_name: str = "syrabit.ai") -> Dict[str, Any]:
    """Aggregate per-category CF verified-bot counts into a digest dict.

    Both ``current`` and ``prior`` are the dicts returned by
    :func:`cloudflare_client.get_verified_bot_traffic_cf`::

        {"by_category": {"Search Engine Crawler": 280, ...},
         "bot_total": 612, "bot_5xx": 4, "source": "cloudflare"}

    Callers may pass ``{}`` for either window if the CF call failed —
    this function treats missing categories as zero so the email still
    renders with whatever we have.
    """
    _now = now or datetime.now(timezone.utc)
    cur_cat = dict((current or {}).get("by_category") or {})
    prev_cat = dict((prior or {}).get("by_category") or {})
    cur_total = int((current or {}).get("bot_total", 0) or 0)
    prev_total = int((prior or {}).get("bot_total", 0) or 0)
    cur_5xx = int((current or {}).get("bot_5xx", 0) or 0)
    prev_5xx = int((prior or {}).get("bot_5xx", 0) or 0)

    categories_set = set(cur_cat) | set(prev_cat) | set(_ALWAYS_SHOW_CATEGORIES)
    rows: List[Dict[str, Any]] = []
    for name in categories_set:
        cur_v = int(cur_cat.get(name, 0) or 0)
        prev_v = int(prev_cat.get(name, 0) or 0)
        rows.append({
            "category": name,
            "current": cur_v,
            "prior": prev_v,
            "delta": cur_v - prev_v,
            "delta_pct": _pct_delta(cur_v, prev_v),
        })
    # Sort by absolute delta magnitude desc so the biggest movers surface
    # at the top — drops and spikes both matter.
    rows.sort(key=lambda r: (abs(r["delta"]), r["current"]), reverse=True)

    highlights = [r for r in rows if r["delta"] != 0][:3]

    total_delta = cur_total - prev_total
    bot_5xx_delta = cur_5xx - prev_5xx

    return {
        "zone": zone_name,
        "window_end": _now.isoformat(),
        "window_start": (_now - timedelta(days=7)).isoformat(),
        "prior_window_end": (_now - timedelta(days=7)).isoformat(),
        "prior_window_start": (_now - timedelta(days=14)).isoformat(),
        "iso_week": _iso_week_tag(_now),
        "bot_total_current": cur_total,
        "bot_total_prior": prev_total,
        "bot_total_delta": total_delta,
        "bot_total_delta_pct": _pct_delta(cur_total, prev_total),
        "bot_5xx_current": cur_5xx,
        "bot_5xx_prior": prev_5xx,
        "bot_5xx_delta": bot_5xx_delta,
        "bot_5xx_delta_pct": _pct_delta(cur_5xx, prev_5xx),
        "categories": rows,
        "highlights": highlights,
        "dashboard_url": dashboard_url,
        "data_ok": bool(current) and bool(current.get("source")),
    }


def _fmt_delta(cur: int, prev: int, pct: Optional[float]) -> str:
    if prev == 0 and cur == 0:
        return "0 → 0"
    if prev == 0:
        return f"0 → {cur} (new)"
    sign = "+" if (cur - prev) >= 0 else ""
    pct_str = f"{sign}{pct}%" if pct is not None else "n/a"
    return f"{prev} → {cur} ({pct_str})"


def _delta_color(delta: int, *, invert: bool = False) -> str:
    """Green for positive delta (more crawlers = good), red for negative.
    Pass ``invert=True`` for metrics where 'up' is bad (e.g. 5xx errors)."""
    if delta == 0:
        return "#475569"
    up_is_good = not invert
    going_up = delta > 0
    good = up_is_good == going_up
    return "#16a34a" if good else "#c0392b"


# Bots we always promote to the top of the per-UA table even if their
# request count is low — these are the SEO/AEO crawlers that operators
# care about most. Other bots fill in below sorted by request volume.
_PROMOTED_BOTS = (
    "Googlebot", "Bingbot",
    "GPTBot", "PerplexityBot", "ClaudeBot",
    "OAI-SearchBot", "ChatGPT-User",
    "Google-Extended", "Applebot-Extended",
)


def _fmt_bytes_short(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def _format_per_ua_table_html(per_ua: Optional[Dict[str, Any]]) -> str:
    """Render the per-crawler breakdown sourced from the `cf_bot_reports`
    Mongo collection (Task #315). Returns an empty string when no per-UA
    data is available so the email gracefully degrades to category-only.

    Promoted SEO/AEO crawlers (Googlebot, Bingbot, GPTBot, PerplexityBot,
    ClaudeBot, OAI-SearchBot, ChatGPT-User, Google-Extended,
    Applebot-Extended) are listed first even when traffic is low — they're
    the ones an operator wants to confirm are active. Remaining bots fill
    in sorted by request volume, capped at 12 rows so the email stays
    skim-friendly.
    """
    if not per_ua:
        return ""
    per_bot = per_ua.get("per_bot") or {}
    if not per_bot:
        return ""

    ordered: List[tuple[str, Dict[str, Any]]] = []
    seen: set[str] = set()
    for name in _PROMOTED_BOTS:
        bot = per_bot.get(name)
        if bot is None:
            ordered.append((name, {"requests": 0, "bytes": 0,
                                    "hit_pct": 0.0, "error_rate": 0.0}))
        else:
            ordered.append((name, bot))
        seen.add(name)
    rest = [(n, b) for n, b in per_bot.items() if n not in seen]
    rest.sort(key=lambda kv: -int(kv[1].get("requests", 0) or 0))
    ordered.extend(rest[: max(0, 12 - len(_PROMOTED_BOTS))])

    rows_html = []
    for name, bot in ordered:
        req = int(bot.get("requests", 0) or 0)
        bytes_v = int(bot.get("bytes", 0) or 0)
        hit_pct = float(bot.get("hit_pct", 0.0) or 0.0)
        err_pct = float(bot.get("error_rate", 0.0) or 0.0) * 100.0
        # Visual emphasis on absent promoted bots — they're the alert
        # signal embedded in the table.
        is_absent = req == 0 and name in _PROMOTED_BOTS
        name_style = "color:#94a3b8;font-style:italic" if is_absent else "color:#0f172a"
        req_str = "—" if is_absent else f"{req:,}"
        rows_html.append(
            "<tr>"
            f"<td style='padding:6px 10px;border:1px solid #e2e8f0;{name_style}'>{name}</td>"
            f"<td style='padding:6px 10px;border:1px solid #e2e8f0;text-align:right'>{req_str}</td>"
            f"<td style='padding:6px 10px;border:1px solid #e2e8f0;text-align:right'>{_fmt_bytes_short(bytes_v)}</td>"
            f"<td style='padding:6px 10px;border:1px solid #e2e8f0;text-align:right'>{hit_pct:.1f}%</td>"
            f"<td style='padding:6px 10px;border:1px solid #e2e8f0;text-align:right'>{err_pct:.2f}%</td>"
            "</tr>"
        )

    iso_week = per_ua.get("iso_week", "")
    suffix = f" · ISO {iso_week}" if iso_week else ""
    return (
        f"<h3 style='margin:20px 0 6px;font-size:15px;color:#334155'>"
        f"Per-crawler breakdown{suffix}</h3>"
        "<p style='margin:0 0 6px;font-size:12px;color:#64748b'>"
        "Greyed-italic rows are crawlers we expect to see but had no traffic "
        "in the latest weekly snapshot.</p>"
        "<table style='border-collapse:collapse;width:100%;font-size:13px;margin:0 0 16px'>"
        "<tr style='background:#f3f4f6'>"
        "<th style='text-align:left;padding:6px 10px;border:1px solid #e2e8f0'>Crawler</th>"
        "<th style='text-align:right;padding:6px 10px;border:1px solid #e2e8f0'>Requests</th>"
        "<th style='text-align:right;padding:6px 10px;border:1px solid #e2e8f0'>Bytes</th>"
        "<th style='text-align:right;padding:6px 10px;border:1px solid #e2e8f0'>Cache hit</th>"
        "<th style='text-align:right;padding:6px 10px;border:1px solid #e2e8f0'>Error rate</th>"
        "</tr>"
        + "".join(rows_html)
        + "</table>"
    )


def _format_bot_traffic_report_html(stats: Dict[str, Any]) -> str:
    """Render the digest as a Resend-compatible HTML body. Highlights
    (biggest week-over-week movers + total + bot 5xx) go at the top."""
    zone = stats.get("zone", "syrabit.ai")
    iso_week = stats.get("iso_week", "")
    ws = (stats.get("window_start") or "")[:10]
    we = (stats.get("window_end") or "")[:10]
    pws = (stats.get("prior_window_start") or "")[:10]
    pwe = (stats.get("prior_window_end") or "")[:10]

    # Highlights ribbon — biggest three movers.
    hi_rows = []
    for h in stats.get("highlights") or []:
        color = _delta_color(h["delta"])
        arrow = "▲" if h["delta"] > 0 else "▼"
        hi_rows.append(
            f"<li style='margin:4px 0'><b>{h['category']}</b>: "
            f"{_fmt_delta(h['current'], h['prior'], h['delta_pct'])} "
            f"<span style='color:{color};font-weight:bold'>{arrow}</span></li>"
        )
    highlights_html = (
        "<ul style='margin:6px 0 16px 18px;padding:0;font-size:14px'>"
        + ("".join(hi_rows) or "<li style='color:#64748b'>No category movements this week.</li>")
        + "</ul>"
    )

    # Per-category table.
    cat_rows = []
    for r in stats.get("categories") or []:
        color = _delta_color(r["delta"])
        cat_rows.append(
            "<tr>"
            f"<td style='padding:6px 10px;border:1px solid #e2e8f0'>{r['category']}</td>"
            f"<td style='padding:6px 10px;border:1px solid #e2e8f0;text-align:right'>{r['prior']}</td>"
            f"<td style='padding:6px 10px;border:1px solid #e2e8f0;text-align:right'><b>{r['current']}</b></td>"
            f"<td style='padding:6px 10px;border:1px solid #e2e8f0;text-align:right;color:{color};font-weight:bold'>"
            f"{_fmt_delta(r['current'], r['prior'], r['delta_pct'])}</td>"
            "</tr>"
        )
    cat_table = (
        "<table style='border-collapse:collapse;width:100%;font-size:13px;margin:8px 0 20px'>"
        "<tr style='background:#f3f4f6'>"
        "<th style='text-align:left;padding:6px 10px;border:1px solid #e2e8f0'>Verified bot category</th>"
        "<th style='text-align:right;padding:6px 10px;border:1px solid #e2e8f0'>Prior 7d</th>"
        "<th style='text-align:right;padding:6px 10px;border:1px solid #e2e8f0'>This week</th>"
        "<th style='text-align:right;padding:6px 10px;border:1px solid #e2e8f0'>Week-over-week</th>"
        "</tr>"
        + "".join(cat_rows)
        + "</table>"
    )

    total_color = _delta_color(stats.get("bot_total_delta", 0))
    b5xx_color = _delta_color(stats.get("bot_5xx_delta", 0), invert=True)

    dashboard = stats.get("dashboard_url") or _BOT_REPORT_DASHBOARD_URL

    return (
        "<div style='font-family:sans-serif;max-width:640px;margin:auto;padding:24px;color:#0f172a'>"
        f"<h2 style='color:#7c3aed;margin:0 0 4px'>Syrabit.ai · Weekly bot traffic report</h2>"
        f"<p style='color:#64748b;margin:0 0 16px;font-size:13px'>"
        f"Zone: <b>{zone}</b> · ISO week {iso_week} · This week {ws} → {we} · "
        f"Prior {pws} → {pwe}</p>"

        # Highlights block — always first so skim-readers see the movers.
        "<div style='background:#faf5ff;border-left:4px solid #7c3aed;padding:12px 16px;"
        "border-radius:4px;margin-bottom:18px'>"
        "<div style='font-weight:bold;color:#6b21a8;margin-bottom:4px'>Biggest movers this week</div>"
        f"{highlights_html}"
        "<div style='font-size:13px;color:#334155'>"
        f"Total verified bot requests: "
        f"<b style='color:{total_color}'>"
        f"{_fmt_delta(stats.get('bot_total_current', 0), stats.get('bot_total_prior', 0), stats.get('bot_total_delta_pct'))}"
        "</b> · "
        f"5xx to bots: "
        f"<b style='color:{b5xx_color}'>"
        f"{_fmt_delta(stats.get('bot_5xx_current', 0), stats.get('bot_5xx_prior', 0), stats.get('bot_5xx_delta_pct'))}"
        "</b>"
        "</div>"
        "</div>"

        f"{cat_table}"

        f"{_format_per_ua_table_html(stats.get('per_ua_report'))}"

        f"<p style='margin:18px 0'><a href='{dashboard}' style='display:inline-block;background:#7c3aed;"
        "color:white;text-decoration:none;padding:10px 20px;border-radius:8px;font-weight:600;font-size:14px'>"
        "Open SEO Manager dashboard</a></p>"
        "<p style='color:#94a3b8;font-size:12px;margin-top:24px'>"
        "You're getting this because you're listed as the Syrabit.ai SEO admin contact. "
        "To stop these weekly summaries, clear the email channel in /admin notifications."
        "</p></div>"
    )


# ── Cloudflare + Mongo inputs ──────────────────────────────────────────────

async def _gather_bot_traffic_report_inputs(
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Fetch the current 7-day and prior 7-day verified-bot breakdowns from
    Cloudflare and compose the report. Raises no exceptions — on CF failure
    returns ``{"_error": "..."}`` so the loop can send a fallback alert."""
    from cloudflare_client import get_verified_bot_traffic_cf, is_configured as _cf_is_configured  # type: ignore
    _now = now or datetime.now(timezone.utc)
    if not _cf_is_configured():
        return {"_error": "cloudflare_not_configured"}

    cur_since = _now - timedelta(days=7)
    prev_since = _now - timedelta(days=14)
    try:
        current = await get_verified_bot_traffic_cf(cur_since, _now)
        prior = await get_verified_bot_traffic_cf(prev_since, cur_since)
    except Exception as exc:
        return {"_error": f"cf_api_exception:{type(exc).__name__}:{str(exc)[:120]}"}

    if current is None and prior is None:
        return {"_error": "cf_api_returned_none"}
    if current is None:
        return {"_error": "cf_api_current_window_failed"}
    if prior is None:
        # Without the prior window we can't compute week-over-week deltas —
        # the whole point of the report. Emit a distinct error so the
        # fallback alert tells operators exactly which window failed.
        return {"_error": "cf_api_prior_window_failed"}

    stats = _compose_bot_traffic_report(current, prior, now=_now)

    # Pull the latest per-UA report (Task #315 sister job stores
    # `cf_bot_reports` docs with `{iso_week, data: {per_bot: {...}}}`).
    # Without this the email shows category totals only and the operator
    # has to log into the dashboard to see WHICH crawler moved.
    try:
        from deps import db, is_mongo_available  # type: ignore
        if await is_mongo_available():
            doc = await db.cf_bot_reports.find_one(
                {}, sort=[("generated_at", -1)],
            )
            if doc and doc.get("data"):
                stats["per_ua_report"] = {
                    "iso_week": doc.get("iso_week", ""),
                    "totals": doc["data"].get("totals") or {},
                    "per_bot": doc["data"].get("per_bot") or {},
                    "generated_at": doc.get("generated_at", ""),
                }
    except Exception as exc:
        # Per-UA enrichment is opportunistic — never block the email.
        logger.debug(f"per-UA report enrichment skipped: {exc}")

    return stats


async def _send_bot_traffic_report_email(
    stats: Dict[str, Any], *, to: Optional[str] = None,
) -> Dict[str, Any]:
    """Send the rendered report via Resend. Returns ``{sent, to, reason?}``."""
    if not stats or "_error" in stats:
        return {"sent": False, "to": "", "reason": stats.get("_error") or "no_stats"}
    try:
        from metrics import _notification_channels, _load_alert_settings  # type: ignore
        try:
            await _load_alert_settings()
        except Exception:
            pass
        admin_email = (to or _notification_channels.get("email")
                       or os.environ.get("ALERT_EMAIL", "")).strip()
    except Exception:
        admin_email = (to or os.environ.get("ALERT_EMAIL", "")).strip()

    # Task #400 — provider gating moved into email_templates.send_admin_email
    # (EMAIL_PROVIDER=ses|sendgrid). Pre-checking SENDGRID_API_KEY here would
    # falsely skip sends under SES-default deploys.
    if not admin_email:
        return {"sent": False, "to": "", "reason": "no_admin_email"}

    try:
        from email_templates import EMAIL_FROM  # type: ignore
    except Exception:
        EMAIL_FROM = os.environ.get("EMAIL_FROM", "Syrabit.ai <noreply@syrabit.ai>").strip()

    html = _format_bot_traffic_report_html(stats)
    total_cur = stats.get("bot_total_current", 0)
    total_pct = stats.get("bot_total_delta_pct")
    pct_str = (f"{'+' if (total_pct or 0) >= 0 else ''}{total_pct}%"
               if total_pct is not None else "new")
    subject = (
        f"Syrabit bot traffic weekly report · "
        f"{total_cur} req ({pct_str}) · "
        f"{stats.get('iso_week','')}"
    )
    try:
        from email_templates import send_admin_email  # type: ignore
        ok = send_admin_email(
            to=[admin_email],
            subject=subject,
            html=html,
            sender=EMAIL_FROM,
        )
        if not ok:
            return {"sent": False, "to": admin_email,
                    "reason": "send_error:sendgrid_non_2xx"}
        logger.info(f"[bot-report] sent weekly report → {admin_email} ({stats.get('iso_week','')})")
        return {"sent": True, "to": admin_email, "subject": subject}
    except Exception as exc:
        logger.warning(f"[bot-report] SendGrid send failed: {exc}")
        return {"sent": False, "to": admin_email, "reason": f"send_error:{type(exc).__name__}"}


async def _dispatch_bot_report_failure_alert(reason: str) -> None:
    """Fire a high-visibility admin alert when the weekly CF fetch fails so
    nobody silently loses a week of crawl-health visibility. Uses the same
    alerting channel as the rest of the system (email + Slack)."""
    try:
        from metrics import _dispatch_alert  # type: ignore
        await _dispatch_alert(
            "bot_traffic_report_failed",
            "Weekly bot traffic report could not be generated",
            (
                "The Monday bot-traffic report job could not pull data from "
                "Cloudflare. Fix CF_ANALYTICS_API_TOKEN / CF_ZONE_ID and run "
                "POST /admin/bot-traffic/weekly-report/send to catch up. "
                f"Reason: {reason}"
            ),
            threshold_snapshot={
                "metric": "cloudflare_verified_bot_feed",
                "value": "reachable",
                "actual": reason,
            },
        )
    except Exception as exc:
        logger.warning(f"[bot-report] fallback alert dispatch failed: {exc}")


# ── Scheduler (loop + atomic claim) ─────────────────────────────────────────

async def _claim_weekly_bot_report_slot(db, cur_iso_week: str) -> bool:
    """Atomic compare-and-set on db.job_locks (``_id`` = _BOT_REPORT_LOCK_ID).

    Returns True iff this caller successfully advanced the marker from
    ``!= cur_iso_week`` to ``cur_iso_week``. Concurrent callers race on the
    unique ``_id`` so at most one wins per ISO week — no duplicate emails
    even with multiple Railway replicas."""
    from pymongo.errors import DuplicateKeyError

    try:
        res = await db.job_locks.find_one_and_update(
            {
                "_id": _BOT_REPORT_LOCK_ID,
                _BOT_REPORT_API_CONFIG_KEY: {"$ne": cur_iso_week},
            },
            {"$set": {_BOT_REPORT_API_CONFIG_KEY: cur_iso_week}},
            upsert=False,
        )
        if res is not None:
            return True
    except Exception as exc:
        logger.debug(f"[bot-report] CAS update failed: {exc}")
        return False

    try:
        await db.job_locks.insert_one({
            "_id": _BOT_REPORT_LOCK_ID,
            _BOT_REPORT_API_CONFIG_KEY: cur_iso_week,
        })
        return True
    except DuplicateKeyError:
        return False
    except Exception as exc:
        logger.debug(f"[bot-report] bootstrap insert failed: {exc}")
        return False


async def _try_send_weekly_bot_report_once(db, now_utc: datetime) -> Dict[str, Any]:
    """One iteration of the loop, factored out for testability."""
    cur_iso_week = _iso_week_tag(now_utc)
    try:
        cfg = await db.job_locks.find_one(
            {"_id": _BOT_REPORT_LOCK_ID},
            {"_id": 0, _BOT_REPORT_API_CONFIG_KEY: 1},
        ) or {}
    except Exception:
        cfg = {}
    last_sent = cfg.get(_BOT_REPORT_API_CONFIG_KEY, "")
    if not _should_send_bot_report_now(now_utc, last_sent):
        return {"claimed": False, "sent": False, "reason": "outside_window_or_dedup"}

    if not await _claim_weekly_bot_report_slot(db, cur_iso_week):
        return {"claimed": False, "sent": False, "reason": "lost_race"}

    stats = await _gather_bot_traffic_report_inputs(now_utc)
    if not stats or stats.get("_error"):
        reason = (stats or {}).get("_error", "no_stats")
        await _dispatch_bot_report_failure_alert(reason)
        # Roll the marker back so a subsequent poll inside the window can retry.
        try:
            await db.job_locks.update_one(
                {"_id": _BOT_REPORT_LOCK_ID,
                 _BOT_REPORT_API_CONFIG_KEY: cur_iso_week},
                {"$set": {_BOT_REPORT_API_CONFIG_KEY: last_sent or ""}},
            )
        except Exception:
            pass
        return {"claimed": True, "sent": False, "reason": reason}

    result = await _send_bot_traffic_report_email(stats)
    if not result.get("sent"):
        logger.info(
            f"[bot-report] send failed for {cur_iso_week} "
            f"(reason={result.get('reason','unknown')}); rolling back claim"
        )
        try:
            await db.job_locks.update_one(
                {"_id": _BOT_REPORT_LOCK_ID,
                 _BOT_REPORT_API_CONFIG_KEY: cur_iso_week},
                {"$set": {_BOT_REPORT_API_CONFIG_KEY: last_sent or ""}},
            )
        except Exception:
            pass
    return {"claimed": True, "sent": result.get("sent", False), "reason": result.get("reason")}


async def _bot_traffic_report_loop():
    """Background loop: polls every 5 minutes and fires the weekly report
    inside a ±15 min window around Monday 04:00 UTC (= 09:30 IST)."""
    from deps import db, is_mongo_available  # type: ignore
    await asyncio.sleep(630)  # let the app warm up (30s after SEO digest loop)
    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            if await is_mongo_available():
                await _try_send_weekly_bot_report_once(db, now_utc)
        except Exception as exc:
            logger.debug(f"[bot-report] loop iteration error: {exc}")
        await asyncio.sleep(_BOT_REPORT_LOOP_SLEEP_S)


# ── Admin: manual trigger / preview ─────────────────────────────────────────

def _get_admin_dependency():
    """Resolve the production admin guard lazily so that unit tests that
    import this module with a stubbed ``deps`` / missing auth surface don't
    fail at collection time."""
    try:
        from routes.bot_discovery import get_admin_user  # type: ignore
        return get_admin_user
    except Exception:
        def _deny():
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail="admin auth unavailable")
        return _deny


@router.post("/admin/bot-traffic/weekly-report/send")
async def admin_bot_traffic_weekly_report_send(
    preview_only: bool = Query(
        False,
        description="If true, return the rendered stats/HTML without sending the email.",
    ),
    admin: dict = Depends(_get_admin_dependency()),
):
    """Manually trigger (or preview) the weekly bot-traffic report. Useful
    for QA and for catching up after an outage. Does not advance the
    ISO-week dedup marker so the regular Monday send still happens."""
    stats = await _gather_bot_traffic_report_inputs()
    if stats.get("_error"):
        html = ""
    else:
        html = _format_bot_traffic_report_html(stats) if stats else ""
    if preview_only:
        return {"sent": False, "preview": True, "stats": stats, "html": html}
    if stats.get("_error"):
        await _dispatch_bot_report_failure_alert(stats["_error"])
        return {"sent": False, "reason": stats["_error"], "stats": stats}
    result = await _send_bot_traffic_report_email(stats)
    return {"sent": result.get("sent", False), "to": result.get("to", ""),
            "reason": result.get("reason"), "stats": stats}
