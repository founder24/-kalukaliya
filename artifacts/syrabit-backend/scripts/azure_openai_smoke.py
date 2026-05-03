"""Deploy-time connectivity smoke check for Azure OpenAI (Task #290).

Exercises the production candidate chain end-to-end:
  1. Reports which candidates are configured (CF BYOK / direct KEY_1 / KEY_2).
  2. Issues a 1-token chat completion through ``call_chat`` so the full
     candidate-chain failover path is exercised.
  3. Issues a 4-token streaming completion through ``stream_chat`` so the
     pre-first-token failover path is exercised too.

Exit codes:
  0 — at least one candidate served a non-empty completion (chain healthy)
  1 — provider not configured at all (no candidates)
  2 — every candidate failed (auth, throttle, deployment-missing, etc.)

Invoke from CI / deploy hook from artifacts/syrabit-backend::

    python -m scripts.azure_openai_smoke

Designed to be cheap (max_tokens=1 + 4) and fast — completes in <5s on a
warm gateway and leaves the regular metrics pipeline untouched.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


async def _run() -> int:
    from providers import azure_openai as az

    health = await az.health_check()
    print("[azure_openai smoke] config:", json.dumps(health, indent=2))

    if not health.get("ok"):
        print("[azure_openai smoke] FAIL — no candidates configured", file=sys.stderr)
        return 1

    # 1. Non-streaming probe.
    try:
        out = await az.call_chat(
            [{"role": "user", "content": "Reply with the single word PONG."}],
            max_tokens=8,
        )
        print(f"[azure_openai smoke] call_chat OK -> {out!r}")
    except Exception as exc:
        print(f"[azure_openai smoke] FAIL call_chat — {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    # 2. Streaming probe — exercises pre-first-token failover.
    try:
        chunks: list[str] = []
        async for tok in az.stream_chat(
            [{"role": "user", "content": "Reply PONG."}],
            max_tokens=8,
        ):
            chunks.append(tok)
        joined = "".join(chunks)
        if not joined.strip():
            print("[azure_openai smoke] FAIL stream_chat — empty stream", file=sys.stderr)
            return 2
        print(f"[azure_openai smoke] stream_chat OK -> {joined!r}")
    except Exception as exc:
        print(f"[azure_openai smoke] FAIL stream_chat — {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print("[azure_openai smoke] PASS — chain healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
