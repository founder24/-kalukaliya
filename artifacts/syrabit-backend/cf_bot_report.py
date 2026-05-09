"""Cloudflare per-UA crawler report generator (Task #315 / Phase B).

Pure data + rendering helpers used by both:
  * the weekly background loop in `routes/bot_discovery.py`
    (`_cf_bot_report_loop`) which stores the report in the
    `cf_bot_reports` Mongo collection so admin dashboards can read it; and
  * the local CLI wrapper at `.local/scripts/cf_bot_report_per_ua.py`
    which writes a dated markdown file to `.local/reports/` for the
    SEO/crawl-budget review workflow.

Splitting the generator out of `cloudflare_client.py` keeps the
analytics-API plumbing in one module and the report shape (which is
specific to the per-bot crawl-budget review) in another.

Design constraints:
  * Returns None on missing credentials or upstream failure — callers
    decide how to surface it (the loop logs and skips; the CLI prints
    a clear error).
  * Pure functions where possible (`compose_wow_diff`,
    `format_report_markdown`, `_classify_ua`) so unit tests don't need
    network access.
  * The week-over-week diff highlights only signals an SEO operator
    cares about: new bots that started crawling, pace deltas above
    `WOW_PACE_DELTA_THRESHOLD`, error-rate spikes above
    `WOW_ERROR_RATE_THRESHOLD`, and disappearance of previously-active
    bots.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from cloudflare_client import _cfg, _graphql_query, is_configured
from internal_user_agents import is_internal_user_agent

logger = logging.getLogger(__name__)


# ── User-agent classification ────────────────────────────────────────────────
# Maps UA substrings → canonical crawler name. Order matters: more specific
# patterns first so e.g. "Googlebot-Image" maps to "Googlebot-Image" not
# "Googlebot". Matching is case-insensitive.
_UA_PATTERNS: list[tuple[str, str]] = [
    # Search-engine crawlers (most-specific Google variants first so
    # "Googlebot-Image" doesn't get caught by the bare "googlebot" rule).
    ("googlebot-image", "Googlebot-Image"),
    ("googlebot-news", "Googlebot-News"),
    ("googlebot-video", "Googlebot-Video"),
    ("google-inspectiontool", "Google-InspectionTool"),
    ("googleother", "GoogleOther"),
    ("adsbot-google", "AdsBot-Google"),
    ("google-extended", "Google-Extended"),  # Gemini training crawler
    ("googlebot", "Googlebot"),
    ("bingbot", "Bingbot"),
    ("yandexbot", "YandexBot"),
    ("duckduckbot", "DuckDuckBot"),
    ("baiduspider", "Baiduspider"),
    ("applebot-extended", "Applebot-Extended"),  # Apple AI training
    ("applebot", "Applebot"),
    ("petalbot", "PetalBot"),
    # NOTE — long-tail crawlers below (SeznamBot, MojeekBot, Yeti) are
    # KEPT INTENTIONALLY even though Syrabit targets Assam/India and
    # they're geo-irrelevant. They are *explicitly Allowed* in
    # `artifacts/syrabit/public/robots.txt` per SEO Plan 10 (see
    # `tests/test_robots_txt_snapshot.py::test_allow_long_tail_search_bots`).
    # Removing them here would mean any traffic from them would silently
    # fall into "Other verified bot" and the operator would lose the
    # audit trail that proves the robots.txt allow-list is working.
    ("seznambot", "SeznamBot"),
    ("mojeekbot", "MojeekBot"),
    ("yeti", "Yeti"),
    # AI / LLM crawlers — Cloudflare files these under the
    # "AI Crawler" verifiedBotCategory, which is why the GraphQL
    # filter below must NOT pin to "Search Engine Crawler" alone.
    ("oai-searchbot", "OAI-SearchBot"),       # ChatGPT search index
    ("chatgpt-user", "ChatGPT-User"),         # ChatGPT live browsing
    ("gptbot", "GPTBot"),                     # OpenAI training
    ("perplexitybot", "PerplexityBot"),
    ("perplexity-user", "Perplexity-User"),
    ("claudebot", "ClaudeBot"),
    ("claude-web", "Claude-Web"),
    ("anthropic-ai", "Anthropic-AI"),
    ("meta-externalagent", "Meta-ExternalAgent"),
    ("bytespider", "Bytespider"),             # ByteDance / TikTok
    ("ccbot", "CCBot"),                       # Common Crawl
    ("amazonbot", "Amazonbot"),
    ("youbot", "YouBot"),
    ("cohere-ai", "Cohere-AI"),
    ("diffbot", "Diffbot"),
]


def _classify_ua(ua: str) -> Optional[str]:
    """Return the canonical crawler name for a raw user-agent string, or
    None if it doesn't match any known search-engine bot.

    Task #820: returns ``None`` for any UA carrying our own
    ``SyrabitInternal`` marker (or any registered legacy internal
    token) BEFORE pattern-matching against the search-bot list. The
    KV-prewarm UA intentionally spoofs Googlebot to seed the edge bot
    cache, and without this short-circuit every prewarm cycle would
    inflate Googlebot's count in the per-UA report.
    """
    if not ua:
        return None
    if is_internal_user_agent(ua):
        return None
    low = ua.lower()
    for needle, name in _UA_PATTERNS:
        if needle in low:
            return name
    return None


# Thresholds for the week-over-week alerting section. Tuned for low noise:
# we want operators to see real shifts, not normal weekly variance.
WOW_PACE_DELTA_THRESHOLD = 0.50   # ±50% req/hr change
WOW_ERROR_RATE_THRESHOLD = 0.05   # 5% absolute jump in (4xx+5xx)/total
WOW_MIN_SAMPLE = 20               # ignore bots with < 20 req either week


# ── Cloudflare data fetch ────────────────────────────────────────────────────

async def _fetch_per_ua_buckets(zone_id: str, since_iso: str, until_iso: str,
                                 limit: int = 1000) -> Optional[list[dict]]:
    """Fetch verified-search-bot request buckets grouped by user-agent +
    edge-cache + status. Returns the raw GraphQL bucket list or None.

    We pull a relatively wide bucket (one row per UA × cache-status × HTTP
    code combination) and aggregate client-side so the report code stays
    a pure function over a known shape.
    """
    query = """
    query PerUaBots($zoneTag: String!, $since: String!, $until: String!, $limit: Int!) {
      viewer {
        zones(filter: { zoneTag: $zoneTag }) {
          httpRequestsAdaptiveGroups(
            filter: {
              datetime_geq: $since
              datetime_leq: $until
              # NOTE: previously we tried `verifiedBotCategory_neq: ""`
              # to restrict to verified bots at the GraphQL layer, but
              # CF's adaptive-groups schema rejects the `_neq` operator
              # with a parser error ("Expected :, found Name 'eq'").
              # Filtering happens client-side instead — `_classify_ua`
              # in this file only returns canonical names for bots we
              # care about (traditional crawlers under CF's "Search
              # Engine Crawler" and AI/LLM crawlers under "AI Crawler",
              # incl. GPTBot, PerplexityBot, ClaudeBot, OAI-SearchBot,
              # Google-Extended, Applebot-Extended, Bytespider,
              # Meta-ExternalAgent, …) and unverified UAs are dropped.
            }
            limit: $limit
            orderBy: [count_DESC]
          ) {
            count
            sum { edgeResponseBytes }
            dimensions {
              userAgent
              cacheStatus
              edgeResponseStatus
            }
          }
        }
      }
    }
    """
    variables = {
        "zoneTag": zone_id,
        "since": since_iso,
        "until": until_iso,
        "limit": limit,
    }
    data = await _graphql_query(query, variables)
    if not data:
        return None
    try:
        zones = data.get("viewer", {}).get("zones", [])
        if not zones:
            return []
        return zones[0].get("httpRequestsAdaptiveGroups", []) or []
    except Exception as exc:
        logger.warning(f"CF per-UA parse failed: {exc}")
        return None


# ── Aggregation ──────────────────────────────────────────────────────────────

def aggregate_per_ua(buckets: list[dict]) -> dict:
    """Roll the raw GraphQL buckets up into a per-bot summary.

    Returns:
      {
        "totals": {"requests": N, "bytes": N, "bots": M},
        "per_bot": {
          "Googlebot": {
            "requests": int, "bytes": int,
            "by_status": {"2xx": N, "3xx": N, "4xx": N, "5xx": N},
            "by_cache": {"HIT": N, "MISS": N, "DYNAMIC": N, ...},
            "hit_pct": float,
            "error_rate": float,  # (4xx+5xx)/total, 0.0 if total==0
          },
          ...
        }
      }
    """
    per_bot: dict[str, dict] = {}
    for b in buckets or []:
        dims = b.get("dimensions") or {}
        ua = dims.get("userAgent", "") or ""
        name = _classify_ua(ua)
        if not name:
            continue
        bot = per_bot.setdefault(name, {
            "requests": 0,
            "bytes": 0,
            "by_status": {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0},
            "by_cache": {},
        })
        cnt = int(b.get("count") or 0)
        bot["requests"] += cnt
        bot["bytes"] += int((b.get("sum") or {}).get("edgeResponseBytes") or 0)
        try:
            status = int(dims.get("edgeResponseStatus") or 0)
        except (TypeError, ValueError):
            status = 0
        if 200 <= status < 300:
            bot["by_status"]["2xx"] += cnt
        elif 300 <= status < 400:
            bot["by_status"]["3xx"] += cnt
        elif 400 <= status < 500:
            bot["by_status"]["4xx"] += cnt
        elif 500 <= status < 600:
            bot["by_status"]["5xx"] += cnt
        cache = dims.get("cacheStatus") or "unknown"
        bot["by_cache"][cache] = bot["by_cache"].get(cache, 0) + cnt

    for name, bot in per_bot.items():
        total = bot["requests"]
        hit = bot["by_cache"].get("hit", 0) + bot["by_cache"].get("HIT", 0)
        bot["hit_pct"] = round(100.0 * hit / total, 1) if total else 0.0
        errs = bot["by_status"]["4xx"] + bot["by_status"]["5xx"]
        bot["error_rate"] = round(errs / total, 4) if total else 0.0

    totals = {
        "requests": sum(b["requests"] for b in per_bot.values()),
        "bytes": sum(b["bytes"] for b in per_bot.values()),
        "bots": len(per_bot),
    }
    return {"totals": totals, "per_bot": per_bot}


# ── Week-over-week diff ──────────────────────────────────────────────────────

def compose_wow_diff(this_week: dict, last_week: Optional[dict]) -> dict:
    """Compute notable week-over-week changes.

    Returns a dict with four lists (`new_bots`, `disappeared_bots`,
    `pace_shifts`, `error_spikes`) plus `had_baseline` so callers can
    distinguish "no baseline" from "baseline showed nothing notable".
    """
    out = {
        "new_bots": [],
        "disappeared_bots": [],
        "pace_shifts": [],
        "error_spikes": [],
        "had_baseline": bool(last_week and (last_week.get("per_bot") or {})),
    }
    cur_per = (this_week or {}).get("per_bot") or {}
    prev_per = (last_week or {}).get("per_bot") or {}

    if not out["had_baseline"]:
        # First-ever run — every active bot is "new" but flagging them all
        # as alerts would be noisy. Just record that the baseline is empty
        # so the renderer can say so; populate `new_bots` with the headline
        # active list for context.
        for name, bot in cur_per.items():
            if bot["requests"] >= WOW_MIN_SAMPLE:
                out["new_bots"].append({"name": name, "requests": bot["requests"]})
        out["new_bots"].sort(key=lambda x: -x["requests"])
        return out

    for name, bot in cur_per.items():
        cur_req = bot["requests"]
        prev = prev_per.get(name)
        if prev is None and cur_req >= WOW_MIN_SAMPLE:
            out["new_bots"].append({"name": name, "requests": cur_req})
            continue
        if prev is None:
            continue
        prev_req = prev["requests"]
        if max(cur_req, prev_req) < WOW_MIN_SAMPLE:
            continue
        # Pace shift: pace = req / hours_in_window. Use raw req ratio since
        # both windows are 7 days (168 h).
        if prev_req == 0:
            ratio = float("inf") if cur_req > 0 else 0.0
        else:
            ratio = (cur_req - prev_req) / prev_req
        if abs(ratio) >= WOW_PACE_DELTA_THRESHOLD:
            out["pace_shifts"].append({
                "name": name,
                "this_week": cur_req,
                "last_week": prev_req,
                "delta_pct": round(ratio * 100.0, 1),
            })
        # Error-rate spike: difference in (4xx+5xx)/total above threshold.
        err_delta = bot["error_rate"] - prev["error_rate"]
        if err_delta >= WOW_ERROR_RATE_THRESHOLD:
            out["error_spikes"].append({
                "name": name,
                "this_week_pct": round(bot["error_rate"] * 100.0, 2),
                "last_week_pct": round(prev["error_rate"] * 100.0, 2),
                "delta_pp": round(err_delta * 100.0, 2),
            })

    for name, prev in prev_per.items():
        if name in cur_per:
            continue
        if prev["requests"] >= WOW_MIN_SAMPLE:
            out["disappeared_bots"].append({
                "name": name,
                "last_week_requests": prev["requests"],
            })

    out["new_bots"].sort(key=lambda x: -x["requests"])
    out["pace_shifts"].sort(key=lambda x: -abs(x["delta_pct"]))
    out["error_spikes"].sort(key=lambda x: -x["delta_pp"])
    out["disappeared_bots"].sort(key=lambda x: -x["last_week_requests"])
    return out


# ── Markdown rendering ───────────────────────────────────────────────────────

def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def format_report_markdown(data: dict, *, since_iso: str, until_iso: str,
                            zone_id: str, generated_at: datetime,
                            wow: Optional[dict] = None,
                            crosscheck_md: Optional[str] = None) -> str:
    """Render the per-UA report as the same markdown shape as the hand-run
    reports in `.local/reports/`."""
    totals = data.get("totals") or {"requests": 0, "bytes": 0, "bots": 0}
    per_bot = data.get("per_bot") or {}
    sorted_bots = sorted(per_bot.items(), key=lambda kv: -kv[1]["requests"])

    lines: list[str] = []
    lines.append("# Search Engine Crawler Traffic — Per User-Agent")
    lines.append(f"_Window: {since_iso} → {until_iso}_")
    lines.append(f"_Zone: `{zone_id}`_")
    lines.append(f"_Generated: {generated_at.isoformat()}_")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- **Total verified search-bot requests:** {totals['requests']:,}")
    lines.append(f"- **Bytes served to search bots:** {_fmt_bytes(totals['bytes'])}")
    lines.append(f"- **Distinct crawlers detected:** {totals['bots']}")
    if sorted_bots:
        top_name, top_bot = sorted_bots[0]
        pct = (100.0 * top_bot["requests"] / totals["requests"]) if totals["requests"] else 0.0
        lines.append(f"- **Top crawler:** **{top_name}** — {top_bot['requests']:,} req ({pct:.1f}%)")
    lines.append("")

    if wow is not None:
        lines.append("## Week-over-week changes")
        if not wow.get("had_baseline"):
            lines.append("_No prior-week baseline found — this is the first stored run._")
            if wow["new_bots"]:
                lines.append("")
                lines.append("**Currently active crawlers:**")
                for b in wow["new_bots"]:
                    lines.append(f"- {b['name']} — {b['requests']:,} req")
        else:
            had_anything = False
            if wow["new_bots"]:
                had_anything = True
                lines.append("**New crawlers this week:**")
                for b in wow["new_bots"]:
                    lines.append(f"- {b['name']} — {b['requests']:,} req (no traffic last week)")
                lines.append("")
            if wow["disappeared_bots"]:
                had_anything = True
                lines.append("**Crawlers that stopped this week:**")
                for b in wow["disappeared_bots"]:
                    lines.append(f"- {b['name']} — {b['last_week_requests']:,} req last week → 0 this week")
                lines.append("")
            if wow["pace_shifts"]:
                had_anything = True
                lines.append(f"**Pace shifts > ±{int(WOW_PACE_DELTA_THRESHOLD * 100)}%:**")
                for s in wow["pace_shifts"]:
                    arrow = "▲" if s["delta_pct"] > 0 else "▼"
                    lines.append(
                        f"- {s['name']}: {s['last_week']:,} → {s['this_week']:,} "
                        f"({arrow} {s['delta_pct']:+.1f}%)"
                    )
                lines.append("")
            if wow["error_spikes"]:
                had_anything = True
                lines.append(f"**4xx/5xx spikes ≥ {int(WOW_ERROR_RATE_THRESHOLD * 100)}pp:**")
                for s in wow["error_spikes"]:
                    lines.append(
                        f"- {s['name']}: {s['last_week_pct']}% → {s['this_week_pct']}% "
                        f"(▲ {s['delta_pp']:+.2f}pp)"
                    )
                lines.append("")
            if not had_anything:
                lines.append("_No notable changes vs prior week (within thresholds)._")
        lines.append("")

    if crosscheck_md:
        # Emits its own `## Cross-check ...` heading and trailing newline.
        lines.append(crosscheck_md.rstrip())
        lines.append("")

    lines.append("## Per-crawler totals")
    lines.append("")
    lines.append("| Crawler | Requests | % of search bots | Bytes served | Cache hit % | Error rate |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for name, bot in sorted_bots:
        pct = (100.0 * bot["requests"] / totals["requests"]) if totals["requests"] else 0.0
        lines.append(
            f"| {name} | {bot['requests']:,} | {pct:.1f}% | {_fmt_bytes(bot['bytes'])} "
            f"| {bot['hit_pct']}% | {bot['error_rate'] * 100:.2f}% |"
        )
    lines.append(f"| **Total** | **{totals['requests']:,}** | **100.0%** | **{_fmt_bytes(totals['bytes'])}** | — | — |")
    lines.append("")

    lines.append("## HTTP status breakdown per crawler")
    lines.append("")
    lines.append("| Crawler | 2xx | 3xx | 4xx | 5xx |")
    lines.append("|---|---:|---:|---:|---:|")
    for name, bot in sorted_bots:
        bs = bot["by_status"]
        lines.append(f"| {name} | {bs['2xx']} | {bs['3xx']} | {bs['4xx']} | {bs['5xx']} |")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ── Top-level entry ──────────────────────────────────────────────────────────

def _iso_week_for(dt: datetime) -> str:
    """Return the ISO year-week tag (e.g. `2026-W16`) for a datetime."""
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


# ── Admin AI Crawl Control summary ───────────────────────────────────────────
# CF's "AI Crawl Control" dashboard categorises verified bots into AI-training
# /answer crawlers vs traditional search-index crawlers. We mirror that split
# locally using the canonical names from `_UA_PATTERNS` so the admin panel can
# show the same headline numbers (top crawlers, AI vs search totals, daily
# series) that the CF dashboard shows — sourced from the same dataset
# (`httpRequestsAdaptiveGroups` filtered to verified bots).
_AI_BOT_NAMES: frozenset[str] = frozenset({
    "OAI-SearchBot", "ChatGPT-User", "GPTBot",
    "PerplexityBot", "Perplexity-User",
    "ClaudeBot", "Claude-Web", "Anthropic-AI",
    "Meta-ExternalAgent",
    "Bytespider", "CCBot", "Amazonbot", "Cohere-AI", "Diffbot",
    # YouBot (You.com) removed — it is a search/answer engine that cites
    # sources and drives referral traffic; treated as a search bot henceforth.
    # Both of these are AI-training opt-out crawlers operated by Google/Apple
    # respectively — CF files them under the "AI Crawler" verifiedBotCategory.
    "Google-Extended", "Applebot-Extended",
})


def is_ai_bot(name: str) -> bool:
    """True when ``name`` (a canonical bot name from ``_classify_ua``) is an
    AI-training/answer crawler rather than a traditional search-index
    crawler. The list mirrors Cloudflare's "AI Crawler" verifiedBotCategory
    so the admin AI Crawl Control card can split totals the same way CF's
    own dashboard does."""
    return name in _AI_BOT_NAMES


# ── Operator (company) grouping for the AI Crawl Control overview ────────────
# Cloudflare's *AI Crawl Control → Overview* tab presents one tile per
# operator company (Google, Meta, Microsoft, Apple, OpenAI, Perplexity,
# Anthropic, Common Crawl, …) with the operator's individual bots rolled
# up underneath. The map below mirrors that grouping so the admin card can
# render the same operator-tile layout. Every canonical name produced by
# ``_classify_ua`` should appear here exactly once; bots not listed are
# rolled into the synthetic "Other" tile so the operator can still see
# their volume.
_OPERATOR_MAP: dict[str, str] = {
    # Google family — search + AI training (Gemini)
    "Googlebot": "Google",
    "Googlebot-Image": "Google",
    "Googlebot-News": "Google",
    "Googlebot-Video": "Google",
    "Google-InspectionTool": "Google",
    "GoogleOther": "Google",
    "AdsBot-Google": "Google",
    "Google-Extended": "Google",
    # Meta — Llama training + link previews
    "Meta-ExternalAgent": "Meta",
    # Microsoft
    "Bingbot": "Microsoft",
    # Apple — search + AI training opt-out
    "Applebot": "Apple",
    "Applebot-Extended": "Apple",
    # OpenAI
    "GPTBot": "OpenAI",
    "ChatGPT-User": "OpenAI",
    "OAI-SearchBot": "OpenAI",
    # Perplexity
    "PerplexityBot": "Perplexity",
    "Perplexity-User": "Perplexity",
    # Anthropic
    "ClaudeBot": "Anthropic",
    "Claude-Web": "Anthropic",
    "Anthropic-AI": "Anthropic",
    # Common Crawl (open dataset, used by many AI training pipelines)
    "CCBot": "Common Crawl",
    # Long-tail search engines (kept individually so the robots.txt
    # allow-list stays auditable — see the note on _UA_PATTERNS).
    "YandexBot": "Yandex",
    "DuckDuckBot": "DuckDuckGo",
    "Baiduspider": "Baidu",
    "PetalBot": "Huawei",
    "SeznamBot": "Seznam",
    "MojeekBot": "Mojeek",
    "Yeti": "Naver",
    # Other AI / research crawlers
    "Bytespider": "ByteDance",
    "Amazonbot": "Amazon",
    "YouBot": "You.com",
    "Cohere-AI": "Cohere",
    "Diffbot": "Diffbot",
}


def operator_for(name: str) -> str:
    """Return the operator-company tile name that ``name`` belongs to,
    or ``"Other"`` for unmapped (long-tail / brand-new) crawlers so
    they still surface with a request count."""
    return _OPERATOR_MAP.get(name, "Other")


def aggregate_per_operator(per_bot: list[dict]) -> list[dict]:
    """Group a flat ``per_bot`` list (the shape produced by
    ``fetch_admin_summary``) into operator-company tiles, mirroring the
    cards on Cloudflare's *AI Crawl Control → Overview* tab.

    Each tile carries the same headline numbers CF surfaces:
      * ``allowed`` — successful (non-4xx/5xx) verified-bot requests.
      * ``unsuccessful`` — 4xx/5xx requests (derived from ``error_rate``,
        which is fraction-of-1 in our shape).
      * ``requests`` — total verified-bot requests for the operator.
      * ``bots`` — the canonical bot names that contributed, sorted by
        request count descending (matches CF's "Googlebot +1" chip).
      * ``category`` — ``"ai"`` if any contributing bot is in the AI
        bucket per ``is_ai_bot()`` (canonical-name-based, *not* the
        per-bot ``category`` field on the input). This makes the
        promotion deterministic from the bot name alone — important
        because `_AI_BOT_NAMES` is the same source of truth used by
        the AI-vs-search totals on the same response, so the tile
        colouring and the `ai_totals` headline can never disagree.
        Used for tile colouring.

    The list is sorted by ``allowed`` descending so the busiest
    operator renders first, matching CF's overview ordering.
    """
    by_op: dict[str, dict] = {}
    for b in per_bot or []:
        name = b.get("name") or ""
        if not name:
            continue
        requests = int(b.get("requests") or 0)
        # error_rate is stored as a fraction (0.0–1.0); guard against
        # GraphQL hiccups returning a percent (>1.0) by clamping.
        err = float(b.get("error_rate") or 0.0)
        if err > 1.0:
            err = err / 100.0
        err = max(0.0, min(1.0, err))
        unsuccessful = int(round(requests * err))
        allowed = max(0, requests - unsuccessful)
        op = operator_for(name)
        tile = by_op.setdefault(op, {
            "operator": op,
            "allowed": 0,
            "unsuccessful": 0,
            "requests": 0,
            "bots": [],
            "category": "search",
        })
        tile["allowed"] += allowed
        tile["unsuccessful"] += unsuccessful
        tile["requests"] += requests
        tile["bots"].append({"name": name, "requests": requests})
        # Promote to "ai" if any contributing bot is AI — matches CF
        # which files the operator under AI Crawl Control as soon as
        # one of its bots is in the AI Crawler category.
        if is_ai_bot(name):
            tile["category"] = "ai"
    # Order each tile's bot chips by request count desc, then sort
    # tiles by total allowed requests desc so the busiest operator
    # renders first (CF's overview ordering).
    out = list(by_op.values())
    for tile in out:
        tile["bots"].sort(key=lambda b: -b["requests"])
    out.sort(key=lambda t: (-t["allowed"], -t["requests"], t["operator"]))
    return out


async def _fetch_per_ua_daily_series(zone_id: str, since_iso: str,
                                     until_iso: str,
                                     limit: int = 1000) -> Optional[list[dict]]:
    """Daily request series grouped by ``(date, userAgent)`` for verified
    bots only. Used by the admin AI Crawl Control card to render the
    Cloudflare-style stacked time-series. Returns the raw GraphQL bucket
    list, or None on credential/upstream failure (caller treats that as
    "no series available" and renders the per-bot summary alone)."""
    query = """
    query PerUaBotsDaily($zoneTag: String!, $since: String!, $until: String!, $limit: Int!) {
      viewer {
        zones(filter: { zoneTag: $zoneTag }) {
          httpRequestsAdaptiveGroups(
            filter: {
              datetime_geq: $since
              datetime_leq: $until
              # See PerUaBots query above — CF rejects
              # `verifiedBotCategory_neq: ""`. We rely on
              # client-side `_classify_ua` to drop unverified UAs.
            }
            limit: $limit
            orderBy: [date_ASC]
          ) {
            count
            dimensions {
              date
              userAgent
            }
          }
        }
      }
    }
    """
    data = await _graphql_query(query, {
        "zoneTag": zone_id,
        "since": since_iso,
        "until": until_iso,
        "limit": limit,
    })
    if not data:
        return None
    try:
        zones = data.get("viewer", {}).get("zones", [])
        if not zones:
            return []
        return zones[0].get("httpRequestsAdaptiveGroups", []) or []
    except Exception as exc:
        logger.warning(f"CF per-UA daily parse failed: {exc}")
        return None


def aggregate_daily_series(buckets: list[dict], top_n: int = 5) -> dict:
    """Roll daily-by-UA buckets into a chart-ready dict. Top-N bots get
    their own keyed series; everything else is collapsed under ``"Other"``
    so the legend stays readable.

    Returns:
      ``{"top_bots": ["Googlebot", "Meta-ExternalAgent", ...],
         "rows": [{"date": "2026-04-19", "Googlebot": 240, "Other": 3}, ...]}``
    """
    per_bot_total: dict[str, int] = {}
    classified: list[tuple[str, str, int]] = []  # (date, name, count)
    for b in buckets or []:
        dims = b.get("dimensions") or {}
        date = dims.get("date") or ""
        ua = dims.get("userAgent") or ""
        cnt = int(b.get("count") or 0)
        name = _classify_ua(ua)
        if not name or not date:
            continue
        per_bot_total[name] = per_bot_total.get(name, 0) + cnt
        classified.append((date, name, cnt))

    top_names = [n for n, _ in sorted(per_bot_total.items(),
                                      key=lambda kv: -kv[1])[:top_n]]
    top_set = set(top_names)
    rows: dict[str, dict] = {}
    for date, name, cnt in classified:
        row = rows.setdefault(date, {"date": date})
        key = name if name in top_set else "Other"
        row[key] = row.get(key, 0) + cnt
    series = sorted(rows.values(), key=lambda r: r["date"])
    return {"top_bots": top_names, "rows": series}


async def collect_recent_bot_hits(window_h: int = 24) -> dict[str, int]:
    """Task #9 — flat ``{ua_lower: hit_count}`` projection over the last
    ``window_h`` hours, used by the admin bot-buckets tile to fold raw
    per-UA traffic onto the canonical four buckets in
    ``infra/bot-rules.yaml``.

    Returns an empty dict (never raises) when CF analytics are not
    configured or the GraphQL query fails — the caller renders an
    empty-state tile rather than 5xx-ing the admin health page.

    Implementation note: re-uses the existing ``_fetch_per_ua_buckets``
    GraphQL query so we don't open a second analytics-API quota line
    item. We aggregate on the *raw* user-agent (lowercased), NOT on
    ``_classify_ua``'s canonical name — the bot-buckets tile needs to
    reach UAs that ``_classify_ua`` rejects (the "unclassified"
    bucket) so an operator can spot a new crawler before adding it to
    the YAML.
    """
    if not is_configured():
        return {}
    try:
        cfg = _cfg()
        zone_id = cfg["zone_id"]
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=window_h)
        since_iso = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        until_iso = end.strftime("%Y-%m-%dT%H:%M:%SZ")
        buckets = await _fetch_per_ua_buckets(zone_id, since_iso, until_iso)
        if not buckets:
            return {}
        out: dict[str, int] = {}
        for b in buckets:
            ua = ((b.get("dimensions") or {}).get("userAgent") or "").strip().lower()
            if not ua:
                continue
            out[ua] = out.get(ua, 0) + int(b.get("count") or 0)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("collect_recent_bot_hits failed: %s", exc)
        return {}


async def fetch_admin_summary(days: int = 7) -> Optional[dict]:
    """Admin-API shaped summary of Cloudflare's verified-bot data — the
    same dataset Cloudflare's *AI Crawl Control* dashboard reads, sourced
    from CF GraphQL ``httpRequestsAdaptiveGroups`` filtered to
    ``verifiedBotCategory_neq: ""`` (which covers both "Search Engine
    Crawler" and "AI Crawler" buckets).

    Splits results into AI vs search-engine crawlers using ``is_ai_bot``
    so the admin card can show the AI-bot headline counts CF emphasises.

    Returns ``None`` when CF analytics credentials are missing or the
    upstream call fails — admin route then renders a clear empty-state
    instead of a 500."""
    if not is_configured():
        return None
    cfg = _cfg()
    zone_id = cfg["zone_id"]
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    since_iso = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    until_iso = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    buckets = await _fetch_per_ua_buckets(zone_id, since_iso, until_iso)
    if buckets is None:
        return None
    agg = aggregate_per_ua(buckets)

    # Daily series is best-effort — if it fails we still return the per-bot
    # summary so the card renders at least the totals + bar list.
    daily_buckets = await _fetch_per_ua_daily_series(zone_id, since_iso, until_iso)
    daily_summary = aggregate_daily_series(daily_buckets or [], top_n=5)

    per_bot_list: list[dict] = []
    ai_requests = ai_bots_count = 0
    search_requests = search_bots_count = 0
    for name, bot in agg.get("per_bot", {}).items():
        cat = "ai" if is_ai_bot(name) else "search"
        per_bot_list.append({
            "name": name,
            "requests": bot["requests"],
            "bytes": bot["bytes"],
            "category": cat,
            "hit_pct": bot.get("hit_pct", 0.0),
            "error_rate": bot.get("error_rate", 0.0),
        })
        if cat == "ai":
            ai_requests += bot["requests"]
            if bot["requests"] > 0:
                ai_bots_count += 1
        else:
            search_requests += bot["requests"]
            if bot["requests"] > 0:
                search_bots_count += 1
    per_bot_list.sort(key=lambda b: -b["requests"])

    per_operator = aggregate_per_operator(per_bot_list)
    # Allowed/unsuccessful are derived from per-bot error_rate so they
    # tally exactly with the per_operator tiles. Mirrors the four-tile
    # Metrics row at the top of CF's overview tab.
    allowed_total = sum(op["allowed"] for op in per_operator)
    unsuccessful_total = sum(op["unsuccessful"] for op in per_operator)

    return {
        "totals": agg.get("totals", {"requests": 0, "bytes": 0, "bots": 0}),
        "ai_totals": {"requests": ai_requests, "bots": ai_bots_count},
        "search_totals": {"requests": search_requests, "bots": search_bots_count},
        "allowed_total": allowed_total,
        "unsuccessful_total": unsuccessful_total,
        "per_bot": per_bot_list,
        "per_operator": per_operator,
        "daily_series": daily_summary,
        "since": since_iso,
        "until": until_iso,
        "zone_id": zone_id,
        "period_days": days,
        "source": "cloudflare",
    }


async def generate_per_ua_report(*, days: int = 7,
                                  prior: Optional[dict] = None,
                                  now: Optional[datetime] = None,
                                  externals: Optional[dict] = None,
                                  externals_path: Optional[str] = None) -> Optional[dict]:
    """End-to-end report generation. Returns
    ``{"data": agg, "wow": diff, "markdown": str, "since": iso, "until": iso}``
    or None when the Cloudflare credentials are missing/the API call fails.

    ``prior`` should be the previous run's ``data`` dict (i.e. the
    ``aggregate_per_ua`` shape) if available; passing None disables the
    week-over-week diff section.
    """
    if not is_configured():
        return None
    cfg = _cfg()
    zone_id = cfg["zone_id"]
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    since_iso = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    until_iso = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    buckets = await _fetch_per_ua_buckets(zone_id, since_iso, until_iso)
    if buckets is None:
        return None
    data = aggregate_per_ua(buckets)
    # Always compute the WoW block — when `prior` is None the diff carries
    # `had_baseline=False` so the markdown explicitly notes "first run".
    # This avoids the silent "missing section" surprise on first-ever runs.
    wow = compose_wow_diff(data, prior)

    # Cross-check Googlebot/Bingbot against the operator-supplied GSC/BWT
    # totals for this ISO week. Section is always rendered (even when no
    # externals are supplied) so readers always see the comparison —
    # with either numbers or the "how to populate" stub.
    from cf_bot_crosscheck import build_crosscheck_section
    iso_week = _iso_week_for(end)
    crosscheck = build_crosscheck_section(
        data, iso_week, path=externals_path, externals=externals)

    md = format_report_markdown(
        data, since_iso=since_iso, until_iso=until_iso,
        zone_id=zone_id, generated_at=end, wow=wow,
        crosscheck_md=crosscheck["markdown"],
    )
    return {
        "data": data,
        "wow": wow,
        "crosscheck": {"rows": crosscheck["rows"],
                       "externals": crosscheck["externals"],
                       "iso_week": iso_week},
        "markdown": md,
        "since": since_iso,
        "until": until_iso,
        "zone_id": zone_id,
        "generated_at": end.isoformat(),
    }
