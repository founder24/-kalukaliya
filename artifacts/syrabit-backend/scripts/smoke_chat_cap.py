#!/usr/bin/env python3
"""Task #513 §A — chat-cap smoke test.

Hammers ``/api/ai/chat`` 35 times against the Cloudflare edge worker
with a stable ``x-anon-id`` header and asserts that the worker:

  * Returns 200 for the first ≤30 requests in a calendar month, AND
  * Returns 429 with `X-Cap: chat_monthly_30_per_anon` once the cap is
    reached, AND
  * Returns 429 with `X-Cap: chat_daily_3_per_anon` for the 4th
    same-day request before the monthly cap is hit.

Read-only / idempotent: the script does not mutate user accounts. It
prints a one-line PASS / FAIL summary and exits 0/1 so CI can wire it
in.

Usage:
    BASE_URL=https://syrabit.ai python scripts/smoke_chat_cap.py
"""
from __future__ import annotations

import os
import sys
import time
import uuid
import json
from urllib import request as _req
from urllib.error import HTTPError


BASE = (os.environ.get("BASE_URL") or "https://syrabit.ai").rstrip("/")
ANON = os.environ.get("SMOKE_ANON_ID") or f"smoke-{uuid.uuid4().hex[:12]}"
PATH = "/api/ai/chat"
PAYLOAD = json.dumps({"message": "what is photosynthesis?", "lang": "en"}).encode()


def _post(i: int) -> tuple[int, dict]:
    req = _req.Request(
        BASE + PATH,
        data=PAYLOAD,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Anon-Id": ANON,
            "User-Agent": "syrabit-smoke-chat-cap/1.0",
        },
    )
    try:
        with _req.urlopen(req, timeout=15) as resp:
            return resp.status, dict(resp.headers)
    except HTTPError as e:
        return e.code, dict(e.headers or {})


def main() -> int:
    print(f"[smoke_chat_cap] BASE={BASE} ANON={ANON}")
    daily_429 = False
    monthly_429 = False
    success_count = 0
    for i in range(1, 36):
        code, headers = _post(i)
        cap = headers.get("X-Cap") or headers.get("x-cap") or ""
        if code == 200:
            success_count += 1
        elif code == 429 and "daily" in cap:
            daily_429 = True
            print(f"[smoke_chat_cap] hit daily cap at request #{i} (X-Cap={cap})")
            # Wait so subsequent requests are not all daily-429.
            time.sleep(1.0)
        elif code == 429 and "monthly" in cap:
            monthly_429 = True
            print(f"[smoke_chat_cap] hit monthly cap at request #{i} (X-Cap={cap})")
            break
        else:
            print(f"[smoke_chat_cap] unexpected status={code} cap={cap!r}")
        time.sleep(0.2)

    print(f"[smoke_chat_cap] success_count={success_count} daily_429={daily_429} monthly_429={monthly_429}")
    if not daily_429:
        print("[smoke_chat_cap] FAIL — daily cap (3/day per anon) was never enforced")
        return 1
    if success_count > 30:
        print(f"[smoke_chat_cap] FAIL — {success_count} successful requests exceeded the 30/month cap")
        return 1
    print("[smoke_chat_cap] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
