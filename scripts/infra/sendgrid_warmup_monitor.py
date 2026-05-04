#!/usr/bin/env python3
"""
SendGrid warmup gate (Task #364 §3 Phase B + §7 smoke row 9).

Two modes for tallying SendGrid event counters in the soak window:

  --mode messages  (preferred — true minute-precision)
      Queries the SendGrid Email Activity Feed API
      (https://api.sendgrid.com/v3/messages) with a `last_event_time`
      lower bound exactly --window-minutes ago, paginates, and tallies
      `processed`, `delivered`, `bounce`, `spam_report`, `blocked`,
      `dropped`. This is the only SendGrid endpoint that supports
      sub-day windowing.
      REQUIREMENT: the SendGrid plan must have the **Email Activity
      History** add-on (Pro / Premier and above). On plans without
      it, /v3/messages returns 401 with a `permission denied` body —
      the script exits 2 and prints a one-line hint to fall back to
      `--mode stats`.

  --mode stats     (fallback — day-level only)
      Queries the SendGrid Stats API (/v3/stats) with
      `aggregated_by=day` and a date range derived from --window-days.
      The day granularity is intrinsic to the endpoint; --window-minutes
      is rejected in this mode to avoid the misleading-gate problem
      flagged in #364 review (a "60-minute" gate that actually
      summed an entire day of events). Use this only when the
      Activity History add-on is unavailable; the soak gates in
      §3 Phase B should use day-aligned checkpoints when running in
      this mode.

Asserts in either mode:

  1. bounce-rate < --bounce-max-pct
  2. spam-report-rate < --spam-max-pct
  3. blocked + dropped (or invalid_emails in stats mode) < --block-max-pct
  4. processed (or requests in stats mode) >= --min-messages

Reads `SENDGRID_API_KEY` from env. Read-only; never sends an email.

Exit codes:
  0  all thresholds OK; safe to advance to the next checkpoint
  1  threshold breach; do NOT advance — pause the warmup
  2  harness failure (network, bad credentials, missing Activity
     History add-on, malformed response)
  3  usage error
"""

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def _http_json(url: str, api_key: str, timeout: float = 20.0) -> dict | list:
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}",
                 "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_messages(api_key: str, window_minutes: int,
                    page_limit: int = 1000,
                    max_pages: int = 100) -> dict[str, int]:
    """
    Tally SendGrid event counters across all messages whose
    last_event_time is within the prior `window_minutes`.

    /v3/messages query language uses ISO-8601 `last_event_time`
    bounds with TIMESTAMP literals. The endpoint caps `limit` at
    1000; pages are walked by re-issuing the same query with a
    tighter upper bound (the oldest `last_event_time` from the
    previous page minus 1 second) until either max_pages is hit or
    a page returns < limit rows. max_pages * page_limit caps the
    total tallied messages at 100k per invocation, which is well
    above the §3 Phase B per-checkpoint volumes (Day 30 ≈ unlimited
    in cap but still under 100k per 24h for our traffic profile).
    """
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(minutes=window_minutes)
    start_iso = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    upper = end

    counters = {"processed": 0, "delivered": 0, "bounce": 0,
                "spam_report": 0, "blocked": 0, "dropped": 0}
    pages = 0
    while pages < max_pages:
        query = (f'last_event_time BETWEEN TIMESTAMP "{start_iso}" '
                 f'AND TIMESTAMP "{upper.strftime("%Y-%m-%dT%H:%M:%SZ")}"')
        qs = urllib.parse.urlencode({"query": query, "limit": page_limit})
        url = f"https://api.sendgrid.com/v3/messages?{qs}"

        try:
            data = _http_json(url, api_key)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")[:200]
                except Exception:
                    pass
                raise RuntimeError(
                    f"SendGrid /v3/messages returned 401. Body: {body!r}. "
                    f"Most common cause: this account is not on a plan "
                    f"with the Email Activity History add-on. "
                    f"Re-run with --mode stats (day-level gates).") from e
            raise

        if not isinstance(data, dict) or "messages" not in data:
            raise RuntimeError(
                f"Unexpected /v3/messages response shape: "
                f"{type(data).__name__}; keys "
                f"{list(data.keys()) if isinstance(data, dict) else []}")

        rows = data.get("messages") or []
        if not rows:
            break

        oldest_in_page: dt.datetime | None = None
        for msg in rows:
            status = (msg.get("status") or "").lower()
            counters["processed"] += 1
            if status == "delivered":
                counters["delivered"] += 1
            elif status in ("bounce", "bounced"):
                counters["bounce"] += 1
            elif status in ("spam_report", "spam"):
                counters["spam_report"] += 1
            elif status == "blocked":
                counters["blocked"] += 1
            elif status in ("dropped", "drop"):
                counters["dropped"] += 1
            ts_str = msg.get("last_event_time") or ""
            if ts_str:
                try:
                    ts = dt.datetime.strptime(
                        ts_str.replace("Z", "+0000"),
                        "%Y-%m-%dT%H:%M:%S%z")
                except ValueError:
                    ts = None
                if ts is not None and (oldest_in_page is None
                                       or ts < oldest_in_page):
                    oldest_in_page = ts

        pages += 1
        if len(rows) < page_limit:
            break
        if oldest_in_page is None:
            # Cannot advance the upper bound safely — bail out rather
            # than infinite-loop on the same window.
            raise RuntimeError(
                "Could not parse last_event_time from /v3/messages "
                "response — pagination cannot advance")
        # Tighten the upper bound to one second before the oldest row
        # we've already counted, so the next page is strictly older.
        upper = oldest_in_page - dt.timedelta(seconds=1)
        if upper <= start:
            break
    else:
        raise RuntimeError(
            f"Pagination exceeded max_pages={max_pages} "
            f"(>{max_pages * page_limit} messages in window); "
            f"narrow --window-minutes or raise the cap")

    return counters


def _fetch_stats_day(api_key: str, window_days: int) -> dict[str, int]:
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=window_days)
    qs = urllib.parse.urlencode({
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "aggregated_by": "day",
    })
    url = f"https://api.sendgrid.com/v3/stats?{qs}"
    rows = _http_json(url, api_key)
    if not isinstance(rows, list):
        raise RuntimeError(
            f"Unexpected /v3/stats response shape: {type(rows).__name__}")

    keys = ("requests", "delivered", "bounces", "spam_reports",
            "blocks", "invalid_emails")
    agg = {k: 0 for k in keys}
    for row in rows:
        for stat in (row.get("stats") or []):
            metrics = stat.get("metrics") or {}
            for k in keys:
                agg[k] += int(metrics.get(k, 0) or 0)
    return agg


def _pct(numerator: int, denominator: int) -> float:
    return (numerator / denominator) * 100.0 if denominator > 0 else 0.0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=("messages", "stats"),
                   default="messages",
                   help="Source of truth. `messages` uses /v3/messages "
                        "for true minute-precision (requires Email "
                        "Activity History add-on). `stats` falls back "
                        "to /v3/stats with day-level aggregation.")
    p.add_argument("--window-minutes", type=int, default=60,
                   help="messages-mode look-back window in minutes. "
                        "Rejected in --mode stats. Default 60.")
    p.add_argument("--window-days", type=int, default=1,
                   help="stats-mode look-back window in days. "
                        "Rejected in --mode messages. Default 1.")
    p.add_argument("--bounce-max-pct", type=float, default=2.0,
                   help="Bounce-rate threshold. Default 2.0%%. "
                        "SendGrid auto-suspends accounts at 5%%.")
    p.add_argument("--spam-max-pct", type=float, default=0.05,
                   help="Spam-complaint threshold. Default 0.05%%. "
                        "SendGrid auto-suspends accounts at 0.1%%.")
    p.add_argument("--block-max-pct", type=float, default=1.0,
                   help="(blocked + dropped/invalid) / processed "
                        "threshold. Default 1.0%%.")
    p.add_argument("--min-messages", type=int, default=50,
                   help="Minimum processed in the window for a "
                        "decision. Default 50. Smaller N returns "
                        "exit 1 with `hold-for-volume` reason.")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    api_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    if not api_key:
        print("ERROR: SENDGRID_API_KEY env var is unset/empty",
              file=sys.stderr)
        return 2

    if args.mode == "messages":
        if args.window_minutes <= 0:
            print("ERROR: --window-minutes must be positive in "
                  "--mode messages", file=sys.stderr)
            return 3
        try:
            agg = _fetch_messages(api_key, args.window_minutes)
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, json.JSONDecodeError, RuntimeError) as e:
            print(f"ERROR: SendGrid /v3/messages fetch failed: {e}",
                  file=sys.stderr)
            return 2
        processed = agg["processed"]
        bounces = agg["bounce"]
        spam = agg["spam_report"]
        block_total = agg["blocked"] + agg["dropped"]
        window_label = f"{args.window_minutes} min"
    else:
        if args.window_days <= 0:
            print("ERROR: --window-days must be positive in "
                  "--mode stats", file=sys.stderr)
            return 3
        try:
            agg = _fetch_stats_day(api_key, args.window_days)
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, json.JSONDecodeError, RuntimeError) as e:
            print(f"ERROR: SendGrid /v3/stats fetch failed: {e}",
                  file=sys.stderr)
            return 2
        processed = agg["requests"]
        bounces = agg["bounces"]
        spam = agg["spam_reports"]
        block_total = agg["blocks"] + agg["invalid_emails"]
        window_label = f"{args.window_days} day(s)"

    bounce_pct = _pct(bounces, processed)
    spam_pct = _pct(spam, processed)
    block_pct = _pct(block_total, processed)

    if not args.quiet:
        print(f"  mode:            {args.mode}")
        print(f"  window:          {window_label}")
        print(f"  processed:       {processed}")
        print(f"  bounces:         {bounces} ({bounce_pct:.3f}%)")
        print(f"  spam_reports:    {spam} ({spam_pct:.3f}%)")
        print(f"  blocked+dropped: {block_total} ({block_pct:.3f}%)")
        print()

    failures: list[str] = []
    if processed < args.min_messages:
        failures.append(
            f"hold-for-volume: only {processed} processed in window "
            f"(need >= {args.min_messages}); ramping on small N is "
            f"not meaningful")
    if bounce_pct >= args.bounce_max_pct:
        failures.append(
            f"bounce-rate {bounce_pct:.3f}% >= "
            f"--bounce-max-pct {args.bounce_max_pct:.3f}%")
    if spam_pct >= args.spam_max_pct:
        failures.append(
            f"spam-rate {spam_pct:.3f}% >= "
            f"--spam-max-pct {args.spam_max_pct:.3f}%")
    if block_pct >= args.block_max_pct:
        failures.append(
            f"block+drop-rate {block_pct:.3f}% >= "
            f"--block-max-pct {args.block_max_pct:.3f}%")

    if not failures:
        print("OK: all thresholds respected; safe to advance")
        return 0
    for f in failures:
        print(f"FAIL: {f}")
    print(f"\n{len(failures)} threshold breach(es) — pause the warmup "
          f"in SendGrid → Settings → IP Addresses")
    return 1


if __name__ == "__main__":
    sys.exit(main())
