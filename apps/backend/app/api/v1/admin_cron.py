"""
Admin Cron Endpoints — triggered by CI/cron jobs, NOT browser sessions.

Auth: Bearer token (TRANSLATE_CRON_SECRET) — no session cookie required.
These routes must NOT be mixed with session-protected admin routes.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["Admin Cron"])


def _verify_cron_token(request: Request) -> None:
    """Validate Bearer token against TRANSLATE_CRON_SECRET."""
    from app.config import settings

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = auth_header[7:]
    expected = settings.TRANSLATE_CRON_SECRET
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid cron token")


@router.post("/cron/translate")
async def cron_translate(request: Request):
    """
    Cron/CI-triggered bulk translation.
    Auth: Bearer {TRANSLATE_CRON_SECRET}
    Body (optional JSON): { board?, subject?, limit? }
    """
    _verify_cron_token(request)

    from app.services.content.translator import ContentTranslator

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    translator = ContentTranslator()

    request.app.state.translation_status = {
        "running": True,
        "total": 0,
        "completed": 0,
        "failed": 0,
        "current_slug": "",
        "errors": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    result = await translator.bulk_translate(
        request.app.state,
        board=body.get("board"),
        subject=body.get("subject"),
        limit=body.get("limit", 100),
        skip_existing=True,
    )
    return result
