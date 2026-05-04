"""Cloudflare vs. Google/Bing webmaster-tools cross-check (Task #316).

The per-UA report in `cf_bot_report.py` classifies Cloudflare verified-bot
requests by user-agent. To prove those numbers are trustworthy, the
weekly Googlebot / Bingbot totals need to be cross-checked against
Google Search Console (GSC) and Bing Webmaster Tools (BWT) crawl-stats
for the same ISO week. If the two diverge by more than
`DIVERGENCE_THRESHOLD`, the bucket patterns or filters need adjusting.

### Why a JSON sidecar instead of direct GSC/BWT API calls?

Google Search Console and Bing Webmaster Tools both require
property-scoped OAuth consent tokens that the Syrabit deploy doesn't
currently hold (no `GSC_SERVICE_ACCOUNT` / `BWT_API_KEY` secret). Rather
than block the cross-check on a credentials rollout, the cross-check
reads a small operator-maintained JSON file keyed by ISO week. The
operator drops in the GSC and BWT totals once per week from the two
consoles, the weekly loop picks them up automatically, and the
comparison table is rendered alongside the main report. When/if
Syrabit wires in the GSC/BWT APIs, only `load_external_totals` needs
to change — the rest of this module is pure.

### Systematic-gap note

Cloudflare counts every *HTTP request* from a verified search bot —
including static assets (CSS/JS/images) a crawler fetches while
rendering a page. Search Console and Bing Webmaster Tools report
*crawl operations* against the property, which is closer to "URL
discovery + HTML fetches" and typically *smaller* than the Cloudflare
number. A 10–30% gap is therefore expected and is documented in the
rendered cross-check section so future readers don't misread the delta
as a classification bug.

### File format

`external-crawler-totals.json` lives beside the dated markdown drops
(default `.local/reports/external-crawler-totals.json`; overridable via
`CF_BOT_REPORT_DIR`). Shape:

```json
{
  "source": "Operator copy/paste from GSC + BWT",
  "weeks": {
    "2026-W16": {
      "googlebot": {"requests": 4200, "source": "GSC Crawl stats"},
      "bingbot":   {"requests":  900, "source": "BWT Crawl information"}
    }
  }
}
```
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


DIVERGENCE_THRESHOLD = 0.15  # 15% — per task definition of "notable" divergence.

# Schema-validation constants. Kept module-level so tests can introspect
# them and the renderer can include them verbatim in the operator hint.
_ALLOWED_BOT_KEYS = ("googlebot", "bingbot")
_ISO_WEEK_RE = re.compile(r"^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$")
_REQUIRED_ENTRY_FIELDS = ("requests", "source")

# Cloudflare per-UA bucket → external-source key. Cloudflare's verified-bot
# classifier lumps `Googlebot-News`, `Googlebot-Image`, etc. under their
# own UAs — but GSC's crawl-stats report only the aggregate Googlebot
# total by default, so the cross-check rolls the Googlebot variants up.
_GOOGLE_VARIANTS = (
    "Googlebot", "Googlebot-Image", "Googlebot-News", "Googlebot-Video",
    "AdsBot-Google",
)
_BING_VARIANTS = ("Bingbot",)

_EXTERNAL_TOTALS_FILENAME = "external-crawler-totals.json"


def _default_external_totals_path() -> Path:
    """Resolve the JSON sidecar path using the same override-first,
    walk-up-for-`.local`, else-cwd policy the main report uses."""
    override = os.getenv("CF_BOT_REPORT_DIR", "").strip()
    if override:
        return Path(override) / _EXTERNAL_TOTALS_FILENAME
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".local").is_dir():
            return parent / ".local" / "reports" / _EXTERNAL_TOTALS_FILENAME
    return Path.cwd() / ".local" / "reports" / _EXTERNAL_TOTALS_FILENAME


def _validate_external_totals(raw: object, iso_week: str) -> list[str]:
    """Inspect the raw JSON payload and return a list of human-readable
    issues that the operator should know about (typos, wrong-cased week
    keys, non-integer `requests`, unknown bot keys, etc.).

    Returning an empty list means the file is well-formed for this week.
    The list is *advisory* — `load_external_totals` will still return
    whatever well-formed entries it could extract — but the cross-check
    renderer surfaces the issues so a typo doesn't silently degrade the
    weekly comparison into a "_not supplied_" stub.
    """
    issues: list[str] = []
    if not isinstance(raw, dict):
        return ["top-level JSON must be an object with a `weeks` key"]
    weeks = raw.get("weeks")
    if weeks is None:
        return ["top-level `weeks` key is missing"]
    if not isinstance(weeks, dict):
        return ["top-level `weeks` must be an object keyed by ISO week"]

    # Catch case-mismatched / mis-shaped week keys. Only flag the keys
    # near the requested week — a year of historical entries is not
    # interesting noise.
    target_upper = iso_week.upper()
    for wk in weeks:
        if not isinstance(wk, str):
            issues.append(f"week key {wk!r} is not a string")
            continue
        if not _ISO_WEEK_RE.match(wk):
            # Wrong-case `2026-w16` is the most common typo — call it out.
            if wk.upper() == target_upper and wk != iso_week:
                issues.append(
                    f"week key `{wk}` should be uppercased to `{iso_week}` "
                    "(ISO 8601 uses capital `W`)"
                )
            else:
                issues.append(
                    f"week key `{wk}` is not ISO-8601 format "
                    "(expected like `2026-W16`)"
                )

    entry = weeks.get(iso_week)
    if entry is None:
        # Already reported the case-mismatch above, if applicable.
        return issues
    if not isinstance(entry, dict):
        return issues + [f"week `{iso_week}` value must be an object"]

    for k, v in entry.items():
        if k not in _ALLOWED_BOT_KEYS:
            allowed = ", ".join(f"`{x}`" for x in _ALLOWED_BOT_KEYS)
            issues.append(
                f"week `{iso_week}` has unknown bot key `{k}` "
                f"(expected one of: {allowed})"
            )
            continue
        if not isinstance(v, dict):
            issues.append(
                f"week `{iso_week}`.`{k}` must be an object with "
                "`requests` and `source` fields"
            )
            continue
        for fld in _REQUIRED_ENTRY_FIELDS:
            if fld not in v:
                issues.append(
                    f"week `{iso_week}`.`{k}` is missing required field "
                    f"`{fld}`"
                )
        req_raw = v.get("requests")
        if req_raw is not None:
            try:
                req = int(req_raw)
            except (TypeError, ValueError):
                issues.append(
                    f"week `{iso_week}`.`{k}`.requests={req_raw!r} "
                    "is not an integer"
                )
            else:
                if req < 0:
                    issues.append(
                        f"week `{iso_week}`.`{k}`.requests={req} is negative"
                    )
    return issues


def load_external_totals(iso_week: str, *,
                         path: Optional[str | Path] = None) -> dict:
    """Return `{googlebot: {requests, source}, bingbot: {...}}` for the
    given ISO week, or an empty dict if the file is missing / malformed
    / has no entry for that week.

    Missing files are *not* an error — the cross-check section simply
    renders a stub explaining how to populate it.

    See :func:`load_external_totals_with_issues` for a variant that also
    returns operator-facing schema-validation issues. This thin wrapper
    is preserved for backward compatibility (one external test relies on
    the flat-dict return type).
    """
    return load_external_totals_with_issues(iso_week, path=path)["totals"]


def load_external_totals_with_issues(
    iso_week: str, *, path: Optional[str | Path] = None,
) -> dict:
    """Like :func:`load_external_totals` but also returns schema-validation
    issues so the cross-check renderer can surface operator typos
    (`googelbot` instead of `googlebot`, `2026-w16` instead of
    `2026-W16`, missing `source`, non-integer `requests`, etc.).

    Returns ``{"totals": {...}, "issues": [str, ...], "path": str|None}``
    where ``issues`` is empty when the file is absent (file absence is
    not an operator error — it's the documented bootstrap state).
    """
    p = Path(path) if path else _default_external_totals_path()
    if not p.exists():
        return {"totals": {}, "issues": [], "path": str(p)}
    try:
        raw = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"[CF crosscheck] {p} unreadable: {exc}")
        return {
            "totals": {},
            "issues": [f"file unreadable: {exc}"],
            "path": str(p),
        }

    issues = _validate_external_totals(raw, iso_week)
    if issues:
        # Operator-facing issues should be visible in the API logs the
        # weekly cron tails, not just in the rendered markdown.
        for msg in issues:
            logger.warning(f"[CF crosscheck] {p}: {msg}")

    weeks = (raw or {}).get("weeks") or {}
    entry = weeks.get(iso_week) or {}
    # Normalise the shape so downstream code doesn't need defensive lookups.
    out: dict = {}
    if isinstance(entry, dict):
        for key in _ALLOWED_BOT_KEYS:
            val = entry.get(key)
            if not isinstance(val, dict):
                continue
            try:
                req = int(val.get("requests") or 0)
            except (TypeError, ValueError):
                req = 0
            if req <= 0:
                continue
            out[key] = {
                "requests": req,
                "source": str(val.get("source") or ""),
            }
    return {"totals": out, "issues": issues, "path": str(p)}


def _cf_total_for(per_bot: dict, variants: tuple[str, ...]) -> int:
    """Sum Cloudflare per-UA requests for every variant name matching
    `variants`. Unknown variants are silently ignored."""
    return sum(int((per_bot.get(name) or {}).get("requests") or 0)
               for name in variants)


def compute_comparison(cf_data: dict, externals: dict) -> list[dict]:
    """Build the comparison rows used by the markdown renderer.

    Returns one dict per cross-checked crawler (Googlebot, Bingbot). A
    row is emitted even when `externals` has no entry for that crawler
    — the row just carries `external_requests=None` and is rendered as
    "not supplied" so the reader sees the gap instead of an empty table.
    """
    per_bot = (cf_data or {}).get("per_bot") or {}
    rows: list[dict] = []
    for label, variants, ext_key in (
        ("Googlebot", _GOOGLE_VARIANTS, "googlebot"),
        ("Bingbot",   _BING_VARIANTS,   "bingbot"),
    ):
        cf_total = _cf_total_for(per_bot, variants)
        ext_entry = externals.get(ext_key) or {}
        ext_total = ext_entry.get("requests")
        row: dict = {
            "crawler": label,
            "cf_requests": cf_total,
            "external_requests": ext_total if ext_total else None,
            "external_source": ext_entry.get("source") or None,
            "delta_pct": None,
            "divergent": False,
            "cf_variants": list(variants),
        }
        if ext_total and cf_total:
            # Signed delta: positive => Cloudflare reports MORE than the
            # external source (the expected direction once assets are
            # included). Divergence check is on absolute magnitude.
            delta = (cf_total - ext_total) / ext_total
            row["delta_pct"] = round(delta * 100.0, 1)
            row["divergent"] = abs(delta) > DIVERGENCE_THRESHOLD
        rows.append(row)
    return rows


def format_crosscheck_markdown(rows: list[dict],
                               *, iso_week: str,
                               any_externals: bool,
                               schema_issues: Optional[list[str]] = None,
                               ) -> str:
    """Render the cross-check section. Always emits the systematic-gap
    paragraph so readers who see a 20% positive delta don't reach for
    the bug tracker.

    When ``schema_issues`` is non-empty, a prominent warning callout is
    emitted *before* the table so an operator typo (e.g. `googelbot` or
    `2026-w16`) surfaces in the report instead of silently degrading
    the comparison to "_not supplied_".
    """
    lines: list[str] = []
    lines.append("## Cross-check vs. Google / Bing webmaster tools")
    lines.append("")
    if schema_issues:
        lines.append(
            f"> ⚠️ **Operator config issue in `external-crawler-totals.json`** "
            f"— the comparison below may be incomplete:"
        )
        for msg in schema_issues:
            lines.append(f"> - {msg}")
        lines.append(
            "> "
        )
        lines.append(
            "> Fix the file and the next weekly run will pick up the "
            "corrected totals automatically."
        )
        lines.append("")
    if not any_externals:
        lines.append(
            f"_No external totals supplied for ISO week `{iso_week}`._ "
            "Drop the week's Googlebot total from Google Search Console "
            "(Settings → Crawl stats) and the week's Bingbot total from "
            "Bing Webmaster Tools (Reports → Crawl information) into "
            "`.local/reports/external-crawler-totals.json` (see "
            "`cf_bot_crosscheck.py` docstring for the shape) and the "
            "next weekly run will fill in the comparison."
        )
        lines.append("")
    lines.append("| Crawler | Cloudflare req | External req | Δ vs external | Status | External source |")
    lines.append("|---|---:|---:|---:|---|---|")
    for r in rows:
        ext_cell = f"{r['external_requests']:,}" if r["external_requests"] else "_not supplied_"
        if r["delta_pct"] is None:
            delta_cell = "—"
        else:
            sign = "+" if r["delta_pct"] >= 0 else ""
            delta_cell = f"{sign}{r['delta_pct']:.1f}%"
        if r["external_requests"] is None:
            status_cell = "⚠️ external missing"
        elif r["divergent"]:
            status_cell = f"❌ diverges > {int(DIVERGENCE_THRESHOLD * 100)}%"
        else:
            status_cell = "✅ within tolerance"
        source_cell = r["external_source"] or "—"
        lines.append(
            f"| {r['crawler']} | {r['cf_requests']:,} | {ext_cell} | "
            f"{delta_cell} | {status_cell} | {source_cell} |"
        )
    lines.append("")
    lines.append("### Expected systematic gap")
    lines.append("")
    lines.append(
        "Cloudflare counts **every HTTP request** a verified crawler makes "
        "— HTML pages *plus* CSS / JS / images / fonts fetched while the "
        "crawler renders those pages. Google Search Console and Bing "
        "Webmaster Tools report **crawl operations** against the "
        "property, which is closer to \"URL discovery + HTML fetches\" "
        "and usually excludes same-origin asset fetches."
    )
    lines.append("")
    lines.append(
        "A **10–30% positive delta** (Cloudflare > GSC/BWT) is therefore "
        "the *expected* steady state and is not a classification bug. "
        f"Rows only flag as `❌ diverges` when the absolute gap exceeds "
        f"**{int(DIVERGENCE_THRESHOLD * 100)}%** — at which point the "
        "`_UA_PATTERNS` list in `cf_bot_report.py` (or the "
        "`verifiedBotCategory` filter) likely needs tightening. A "
        "**negative** delta (Cloudflare < external) is always suspect: "
        "it means the UA filter is missing real crawler traffic."
    )
    lines.append("")
    if rows and any(r["cf_requests"] > 0 for r in rows) and not any_externals:
        # Plain-language callout so the reader doesn't miss the stub row.
        lines.append(
            "> **Action:** supply GSC + BWT totals to unlock the "
            "divergence check for this week."
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_crosscheck_section(cf_data: dict, iso_week: str,
                              *, path: Optional[str | Path] = None,
                              externals: Optional[dict] = None,
                              ) -> dict:
    """Convenience wrapper: load externals (if not provided), compute
    the rows, render the markdown. Returns
    ``{"markdown": str, "rows": [...], "externals": {...},
       "schema_issues": [...]}`` so the main report can embed it and
    persist the raw numbers (plus any operator-config issues) alongside
    the Cloudflare data in Mongo for later auditing.

    When the caller passes ``externals`` directly (test path), schema
    validation is skipped because the caller has already constructed a
    well-formed dict in memory — there's no on-disk file to typo.
    """
    if externals is not None:
        ext = externals
        issues: list[str] = []
    else:
        loaded = load_external_totals_with_issues(iso_week, path=path)
        ext = loaded["totals"]
        issues = loaded["issues"]
    rows = compute_comparison(cf_data, ext)
    md = format_crosscheck_markdown(
        rows, iso_week=iso_week,
        any_externals=bool(ext),
        schema_issues=issues or None,
    )
    return {
        "markdown": md,
        "rows": rows,
        "externals": ext,
        "schema_issues": issues,
    }


# ─── Task #332 — SQS consumer entrypoint ─────────────────────────────────────
#
# `services/backend/sqs_consumers/cf_bot_crosscheck.py` invokes this on each
# `cf-bot-crosscheck` SQS message. The message carries a single suspect
# (ip, ua) tuple from the bot-discovery middleware. We resolve the
# verifier inside `routes.bot_discovery` (where the per-IP rDNS+ASN logic
# already lives) and route the result through the existing
# `cf_bot_reports` collection update path so this consumer has parity
# with the in-process implementation.
async def crosscheck(*, ip: str, ua: str = "") -> dict:
    """Cross-check a single (ip, ua) against Cloudflare's verified-bot
    list and authoritative reverse-DNS for the major search engines.

    Returns: {"ip", "ua", "verified": bool, "vendor": str|None,
              "method": "cf"|"rdns"|"asn"|"none"}.
    """
    from routes import bot_discovery as _bd  # type: ignore

    # `bot_discovery._verify_bot` is the same per-request verifier the
    # middleware uses; surfacing it via this consumer keeps the SQS
    # path 1:1 with the legacy in-process call site.
    # Task #332 — RAISE on operational failure so SQS Lambda surfaces
    # the error to AWS (retry + DLQ). Verifier-unavailable is a
    # deploy-time misconfiguration we want loud, not silent.
    verifier = getattr(_bd, "_verify_bot", None) or getattr(_bd, "verify_bot", None)
    if verifier is None:
        raise RuntimeError(
            "cf-bot-crosscheck verifier unavailable — routes.bot_discovery "
            "must export `_verify_bot` (or `verify_bot`)."
        )
    try:
        result = await verifier(ip=ip, ua=ua)  # type: ignore[misc]
    except TypeError:
        # Older signature accepted a single positional dict.
        result = await verifier({"ip": ip, "ua": ua})  # type: ignore[misc]
    if isinstance(result, dict):
        result.setdefault("ip", ip)
        result.setdefault("ua", ua)
        return result
    return {"ip": ip, "ua": ua, "verified": bool(result), "vendor": None, "method": "rdns"}
