#!/usr/bin/env python3
"""
Per-session fallback anti-thundering-herd check — Task #362 §3.4.

Counts the per-session fallback swap rate over a rolling 5-minute
window against the active-session count. If > 5% of active sessions
have tripped a per-session swap in the last 5 minutes, this is
broad upstream degradation, not a per-session problem — the global
`chat:fallback` is the right tool above this threshold.

What the script does:
  1. Reads two Upstash REST API endpoints (env vars
     UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN required):
       a. SCAN session:fallback:* with TTL filter (only count keys
          created in the last 5 min — ttl > swap_ttl - 300s).
       b. SCAN session:ttfb:* — proxy for active sessions in the
          last 24h.
  2. Computes ratio = swaps_last_5min / active_sessions.
  3. Prints the ratio. Exits 1 if ratio > 0.05.
  4. With --auto-disable, also writes session:fallback:disabled=1
     (TTL 30 min) when over-threshold, and posts a message to the
     on-call webhook (env var ONCALL_WEBHOOK_URL).

Usage:
  session_fallback_thundering_herd_check.py
  session_fallback_thundering_herd_check.py --auto-disable

Exit codes:
  0 — under threshold; no action needed
  1 — over threshold; alert (and auto-disable if --auto-disable)
  2 — Redis / network failure; check logs
  3 — usage / config error
"""

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable

THRESHOLD = 0.05
SWAP_TTL_SECONDS = 7200          # default 2h per spec §3.2
WINDOW_SECONDS = 300             # 5-minute window per spec §3.4
DISABLE_KEY_TTL = 1800           # 30 min cooldown on the auto-disable


def _env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        print(f"ERROR: {name} is unset", file=sys.stderr)
        sys.exit(3)
    return v


def _upstash_request(base_url: str, token: str,
                     path_segments: list[str]) -> dict:
    encoded = "/".join(urllib.parse.quote(s, safe="") for s in path_segments)
    url = f"{base_url.rstrip('/')}/{encoded}"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            import json as _json
            return _json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"ERROR: Upstash request failed for {path_segments[0]}: {e}",
              file=sys.stderr)
        sys.exit(2)


def _scan_keys(base_url: str, token: str, pattern: str) -> Iterable[str]:
    cursor = "0"
    while True:
        resp = _upstash_request(
            base_url, token,
            ["scan", cursor, "match", pattern, "count", "500"])
        result = resp.get("result")
        if not isinstance(result, list) or len(result) != 2:
            print(f"ERROR: unexpected SCAN reply: {resp}", file=sys.stderr)
            sys.exit(2)
        cursor, keys = result[0], result[1]
        for k in keys:
            yield k
        if cursor in ("0", 0):
            break


def _ttl_seconds(base_url: str, token: str, key: str) -> int:
    resp = _upstash_request(base_url, token, ["ttl", key])
    return int(resp.get("result", -2))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--auto-disable", action="store_true",
                   help="When over threshold, set "
                        "session:fallback:disabled=1 (TTL 30min) and "
                        "post to ONCALL_WEBHOOK_URL.")
    p.add_argument("--threshold", type=float, default=THRESHOLD,
                   help=f"Trip ratio. Default {THRESHOLD}.")
    p.add_argument("--swap-ttl", type=int, default=SWAP_TTL_SECONDS,
                   help="Default per-session swap TTL.")
    p.add_argument("--window", type=int, default=WINDOW_SECONDS,
                   help="Recency window in seconds.")
    args = p.parse_args()

    base_url = _env("UPSTASH_REDIS_REST_URL")
    token = _env("UPSTASH_REDIS_REST_TOKEN")

    swap_keys = list(_scan_keys(base_url, token, "session:fallback:*"))
    swap_keys = [k for k in swap_keys
                 if not k.endswith(":disabled")
                 and not k.endswith(":pin")]

    recent_swaps = 0
    min_remaining_ttl = args.swap_ttl - args.window
    for k in swap_keys:
        ttl = _ttl_seconds(base_url, token, k)
        if ttl > min_remaining_ttl:
            recent_swaps += 1

    active_session_keys = list(_scan_keys(base_url, token, "session:ttfb:*"))
    active_sessions = len(active_session_keys)

    if active_sessions == 0:
        print("active_sessions=0 — no traffic; nothing to check")
        return 0

    ratio = recent_swaps / active_sessions
    print(f"recent_swaps={recent_swaps} active_sessions={active_sessions} "
          f"ratio={ratio:.4f} threshold={args.threshold:.4f}")

    if ratio <= args.threshold:
        print("UNDER THRESHOLD — no action")
        return 0

    print(f"OVER THRESHOLD — broad upstream degradation likely",
          file=sys.stderr)

    if args.auto_disable:
        _upstash_request(
            base_url, token,
            ["set", "session:fallback:disabled", "1", "ex",
             str(DISABLE_KEY_TTL)])
        print(f"set session:fallback:disabled=1 TTL={DISABLE_KEY_TTL}s")
        webhook = os.environ.get("ONCALL_WEBHOOK_URL", "").strip()
        if webhook:
            try:
                payload = (f'{{"text":"[#362 §3.4] per-session fallback '
                           f'thundering-herd guard tripped: '
                           f'ratio={ratio:.4f} '
                           f'(recent_swaps={recent_swaps}, '
                           f'active_sessions={active_sessions}). '
                           f'session:fallback:disabled set for 30min. '
                           f'Investigate upstream Mistral health and '
                           f'consider flipping global chat:fallback."}}')
                req = urllib.request.Request(
                    webhook, data=payload.encode("utf-8"),
                    headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=5).read()
                print("on-call webhook posted")
            except Exception as e:
                print(f"WARN: webhook post failed: {e}", file=sys.stderr)
        else:
            print("WARN: ONCALL_WEBHOOK_URL unset; alert NOT posted",
                  file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
