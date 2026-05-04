"""bing-keyword-refresh SQS consumer (Task #332)."""
from __future__ import annotations

from typing import Any

from ._common import run_batch


async def _handle(body: dict[str, Any]) -> None:
    from bing_keyword_client import refresh_keywords  # type: ignore
    await refresh_keywords(site=body.get("site"), keywords=body.get("keywords") or [])


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    return run_batch(event, _handle)
