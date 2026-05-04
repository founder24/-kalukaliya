#!/usr/bin/env python3
"""
Manual RAG-cache flush — Task #361 §1.3.

Operator runbook tool for incident response. Performs a `SCAN` + `DEL`
pass against a cache prefix on the dedicated CACHE_REDIS instance.

This is rarely needed because §1.3's prefix-bump pattern silently
invalidates all prior entries by changing `curriculum:version`. Use
this script only when:

  - You need to reclaim Redis memory faster than the 24 h TTL.
  - You suspect a populator bug wrote bad entries under a current-
    version prefix and want to clear them deterministically.
  - You are tearing down the cache (§6.5 sunset).

Exit codes:
  0  — flush completed
  2  — flush partially completed (some keys errored)
  3  — could not connect to Redis

Usage:
  UPSTASH_REDIS_CACHE_URL=https://...  \\
  UPSTASH_REDIS_CACHE_TOKEN=...        \\
  python3 scripts/perf/flush_rag_cache.py --prefix=rag:syllabus:2026.05:

  # Dry-run first — always recommended:
  python3 scripts/perf/flush_rag_cache.py --prefix=rag:syllabus:2026.05: --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.request
import json


def _upstash_call(base_url: str, token: str, command: list[str]) -> dict:
    """Single Upstash REST API call. command is ['SCAN', '0', 'MATCH', ...]."""
    req = urllib.request.Request(
        base_url,
        data=json.dumps(command).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", required=True,
                    help="Cache key prefix to flush, e.g. rag:syllabus:2026.05:")
    ap.add_argument("--dry-run", action="store_true",
                    help="Scan + count, but do not DEL.")
    ap.add_argument("--batch-size", type=int, default=500)
    args = ap.parse_args()

    if not args.prefix or "*" in args.prefix:
        print("ERROR: --prefix must be a literal string (no wildcards) "
              "and not empty", file=sys.stderr)
        return 3

    base = os.environ.get("UPSTASH_REDIS_CACHE_URL") or os.environ.get(
        "UPSTASH_REDIS_REST_URL"
    )
    token = os.environ.get("UPSTASH_REDIS_CACHE_TOKEN") or os.environ.get(
        "UPSTASH_REDIS_REST_TOKEN"
    )
    if not base or not token:
        print("ERROR: set UPSTASH_REDIS_CACHE_URL + UPSTASH_REDIS_CACHE_TOKEN "
              "(or fall back to UPSTASH_REDIS_REST_URL + ..._TOKEN)",
              file=sys.stderr)
        return 3

    pattern = f"{args.prefix}*"
    cursor = "0"
    scanned = 0
    deleted = 0
    errored = 0
    start = time.monotonic()

    print(f"Mode:    {'DRY-RUN' if args.dry_run else 'LIVE-FLUSH'}")
    print(f"Pattern: {pattern}")
    print()

    while True:
        try:
            r = _upstash_call(
                base, token,
                ["SCAN", cursor, "MATCH", pattern, "COUNT", str(args.batch_size)],
            )
        except Exception as e:
            print(f"ERROR: SCAN failed at cursor={cursor}: {e}", file=sys.stderr)
            return 2 if scanned > 0 else 3

        result = r.get("result")
        if not isinstance(result, list) or len(result) != 2:
            print(f"ERROR: unexpected SCAN response: {r}", file=sys.stderr)
            return 3
        cursor, keys = result[0], result[1]
        scanned += len(keys)

        if keys and not args.dry_run:
            try:
                d = _upstash_call(base, token, ["DEL", *keys])
                deleted += int(d.get("result") or 0)
            except Exception as e:
                print(f"WARN: DEL batch failed ({len(keys)} keys): {e}",
                      file=sys.stderr)
                errored += len(keys)

        if cursor == "0":
            break

    elapsed = time.monotonic() - start
    print(f"Scanned: {scanned}")
    if args.dry_run:
        print(f"Would delete: {scanned}")
    else:
        print(f"Deleted: {deleted}")
        if errored:
            print(f"Errored: {errored}")
    print(f"Elapsed: {elapsed:.2f} s")

    return 2 if errored else 0


if __name__ == "__main__":
    sys.exit(main())
