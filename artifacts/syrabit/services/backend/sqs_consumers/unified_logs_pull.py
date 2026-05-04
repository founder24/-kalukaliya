"""unified-logs-cf-pull SQS consumer (Task #332).

Each message is a Cloudflare GraphQL pull tick — the body carries the
[since, until] window. We forward to the existing
``unified_logs_dao`` pull entrypoint.
"""
from __future__ import annotations

from typing import Any

from ._common import run_batch


async def _handle(body: dict[str, Any]) -> None:
    from unified_logs_dao import pull_cf_window  # type: ignore
    since = body.get("since")
    until = body.get("until")
    if not (since and until):
        raise ValueError("unified-logs message missing since/until")
    await pull_cf_window(since=since, until=until)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    return run_batch(event, _handle)
