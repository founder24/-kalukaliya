"""seo-indexnow SQS consumer (Task #332).

Replaces the Cloud Tasks "seo-indexnow" worker route. Each message
carries one of:

    {"page_id": "<pid>"}            — fetch page from Mongo, push it
    {"url": "https://..."}          — push the URL directly
    {"urls": ["https://...", ...]}  — push a batch of URLs

and forwards to the existing IndexNow primitives in
``routes.bot_discovery`` (`notify_indexnow_for_page` /
`push_indexnow`) — the same code paths the in-process producer used
before the SQS cutover.
"""
from __future__ import annotations

from typing import Any

from ._common import run_batch


async def _handle(body: dict[str, Any]) -> None:
    from routes.bot_discovery import notify_indexnow_for_page, push_indexnow  # type: ignore

    urls = body.get("urls")
    if isinstance(urls, list) and urls:
        await push_indexnow([str(u) for u in urls if u])
        return

    url = body.get("url")
    if isinstance(url, str) and url:
        await push_indexnow([url])
        return

    page_id = body.get("page_id")
    if not page_id:
        raise ValueError("seo-indexnow message must carry one of {urls, url, page_id}")

    from deps import db  # type: ignore
    page = await db.pages.find_one({"id": page_id}) or await db.pages.find_one({"_id": page_id})
    if not page:
        raise LookupError(f"seo-indexnow: page not found for page_id={page_id!r}")
    await notify_indexnow_for_page(page)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    return run_batch(event, _handle)
