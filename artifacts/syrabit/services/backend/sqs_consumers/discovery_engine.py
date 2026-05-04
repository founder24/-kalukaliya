"""discovery-engine-ingest SQS consumer (Task #332)."""
from __future__ import annotations

from typing import Any

from ._common import run_batch


async def _handle(body: dict[str, Any]) -> None:
    import discovery_engine_ingest as _dei  # type: ignore
    target = body.get("target") or "page"
    payload = body.get("payload") or {}
    await _dei.ingest(target=target, payload=payload)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    return run_batch(event, _handle)
