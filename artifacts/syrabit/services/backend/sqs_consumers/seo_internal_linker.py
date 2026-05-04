"""seo-internal-linker SQS consumer (Task #332).

Two message shapes are accepted:

    {"page_id": "<pid>", "auto_apply": bool}
        Per-page propose pass — calls
        `seo_internal_linker.propose_internal_links_for_page(db, page, ...)`
        which is the same code path `routes.seo_internal_linker_admin`
        already exposes.

    {"nightly": true, "top_n": <int?>}
        One-shot nightly maintenance pass — calls
        `seo_internal_linker.nightly_maintenance_pass(db, top_n=...)`.
        This is what the legacy `_internal_linker_loop(db)` invoked
        once per UTC date inside its `while True:`.
"""
from __future__ import annotations

from typing import Any

from ._common import run_batch


async def _handle(body: dict[str, Any]) -> None:
    from deps import db  # type: ignore
    import seo_internal_linker as _sil  # type: ignore

    if body.get("nightly"):
        top_n = body.get("top_n")
        await _sil.nightly_maintenance_pass(db, top_n=top_n if isinstance(top_n, int) else None)
        return

    page_id = body.get("page_id")
    if not page_id:
        raise ValueError("seo-internal-linker message needs page_id or nightly=true")
    page = await db.pages.find_one({"id": page_id}) or await db.pages.find_one({"_id": page_id})
    if not page:
        raise LookupError(f"seo-internal-linker: page not found for page_id={page_id!r}")
    await _sil.propose_internal_links_for_page(
        db, page, auto_apply=bool(body.get("auto_apply", False))
    )


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    return run_batch(event, _handle)
