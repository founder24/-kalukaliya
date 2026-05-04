"""cf-bot-crosscheck SQS consumer (Task #332)."""
from __future__ import annotations

from typing import Any

from ._common import run_batch


async def _handle(body: dict[str, Any]) -> None:
    import cf_bot_crosscheck as _cbc  # type: ignore
    ip = body.get("ip")
    ua = body.get("ua")
    if not ip:
        raise ValueError("cf-bot-crosscheck message missing 'ip'")
    await _cbc.crosscheck(ip=ip, ua=ua or "")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    return run_batch(event, _handle)
