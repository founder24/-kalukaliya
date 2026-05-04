"""bing-submit SQS consumer (Task #332)."""
from __future__ import annotations

from typing import Any

from ._common import run_batch


async def _handle(body: dict[str, Any]) -> None:
    from bing_submit_client import submit_url  # type: ignore
    url = body.get("url")
    if not url:
        raise ValueError("bing-submit message missing 'url'")
    await submit_url(url)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    return run_batch(event, _handle)
