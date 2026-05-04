#!/usr/bin/env python3
"""
SendGrid warmup gate (Task #364 §3 Phase B).

Polls the SendGrid v3 stats endpoint
(https://api.sendgrid.com/v3/stats) for the prior `--window-minutes`
window and asserts:

  1. bounce-rate < --bounce-max-pct
  2. spam-report-rate < --spam-max-pct
  3. messages_sent >= --min-messages
  4. (optional) blocked + invalid-email-rate < --block-max-pct

If all four hold the script exits 0 — operator may ramp to the next
SENDGRID_TRAFFIC_PCT step. If any threshold is breached it exits 1
and the operator must hold the current step (or roll back) per §3.1.

Reads `SENDGRID_API_KEY` from env. Read-only against SendGrid; never
sends an email.

Exit codes:
  0  all thresholds OK; safe to ramp
  1  threshold breach; do NOT ramp
  2  harness failure (network, bad credentials, malformed response)
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


def _fetch_stats(api_key: str, start_date: str, end_date: str,
                 timeout: float = 15.0) -> list[dict]:
    qs = urllib.parse.urlencode({
        "start_date": start_date,
        "end_date": end_date,
        "aggregated_by": "day",
    })
    url = f"https://api.sendgrid.com/v3/stats?{qs}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}",
                 "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _aggregate(rows: list[dict]) -> dict[str, int]:
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
    if denominator <= 0:
        return 0.0
    return (numerator / denominator) * 100.0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--window-minutes", type=int, default=60,
                   help="Look-back window in minutes. Default 60.")
    p.add_argument("--bounce-max-pct", type=float, default=2.0,
                   help="Bounce-rate threshold. Default 2.0%%. "
                        "SendGrid auto-suspends accounts at 5%%.")
    p.add_argument("--spam-max-pct", type=float, default=0.05,
                   help="Spam-complaint threshold. Default 0.05%%. "
                        "SendGrid auto-suspends accounts at 0.1%%.")
    p.add_argument("--block-max-pct", type=float, default=1.0,
                   help="(blocks + invalid) / requests threshold. "
                        "Default 1.0%%.")
    p.add_argument("--min-messages", type=int, default=50,
                   help="Minimum requests in the window for a "
                        "decision. Default 50. If fewer messages "
                        "were sent the script returns exit 1 with a "
                        "`hold-for-volume` reason — ramping on a tiny "
                        "denominator is not meaningful.")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    api_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    if not api_key:
        print("ERROR: SENDGRID_API_KEY env var is unset/empty",
              file=sys.stderr)
        return 2

    if args.window_minutes <= 0:
        print("ERROR: --window-minutes must be positive", file=sys.stderr)
        return 3

    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(minutes=args.window_minutes)
    start_date = start.strftime("%Y-%m-%d")
    end_date = end.strftime("%Y-%m-%d")

    if not args.quiet:
        print(f"Fetching SendGrid stats for "
              f"{start.isoformat(timespec='minutes')} → "
              f"{end.isoformat(timespec='minutes')} "
              f"(window={args.window_minutes} min)")

    try:
        rows = _fetch_stats(api_key, start_date, end_date)
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError) as e:
        print(f"ERROR: SendGrid stats fetch failed: {e}",
              file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"ERROR: SendGrid stats response not JSON: {e}",
              file=sys.stderr)
        return 2

    if not isinstance(rows, list):
        print(f"ERROR: SendGrid stats response shape unexpected: "
              f"{type(rows).__name__}", file=sys.stderr)
        return 2

    agg = _aggregate(rows)
    requests = agg["requests"]
    bounce_pct = _pct(agg["bounces"], requests)
    spam_pct = _pct(agg["spam_reports"], requests)
    block_pct = _pct(agg["blocks"] + agg["invalid_emails"], requests)

    print()
    print(f"  requests:        {requests}")
    print(f"  delivered:       {agg['delivered']}")
    print(f"  bounces:         {agg['bounces']} ({bounce_pct:.3f}%)")
    print(f"  spam_reports:    {agg['spam_reports']} ({spam_pct:.3f}%)")
    print(f"  blocks+invalid:  {agg['blocks'] + agg['invalid_emails']} "
          f"({block_pct:.3f}%)")
    print()

    failures: list[str] = []
    if requests < args.min_messages:
        failures.append(
            f"hold-for-volume: only {requests} messages in window "
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
            f"block+invalid-rate {block_pct:.3f}% >= "
            f"--block-max-pct {args.block_max_pct:.3f}%")

    if not failures:
        print("OK: all thresholds respected; safe to ramp")
        return 0
    for f in failures:
        print(f"FAIL: {f}")
    print(f"\n{len(failures)} threshold breach(es) — DO NOT ramp")
    return 1


if __name__ == "__main__":
    sys.exit(main())
