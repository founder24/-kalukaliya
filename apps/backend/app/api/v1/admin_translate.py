"""
Admin Translation Endpoints - Bulk and single content translation to Assamese.

Note: The cron-triggered endpoint (Bearer token auth) lives in admin_cron.py.
All endpoints here require admin session via router-level dependency.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks

from app.api.v1.admin import require_admin_session, csrf_guard
from app.services.content.translator import ContentTranslator
from app.models.knowledge import KnowledgeObject

router = APIRouter(
    tags=["Admin Translation"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)
translator = ContentTranslator()


@router.post("/content/translate/bulk")
async def bulk_translate(request: Request, background_tasks: BackgroundTasks):
    """Trigger bulk translation. Body: {board?, subject?, limit?, skip_existing?}"""
    body = await request.json() if await request.body() else {}

    status = getattr(request.app.state, "translation_status", None)
    if status and status.get("running"):
        raise HTTPException(status_code=409, detail="Translation job already running")

    request.app.state.translation_status = {
        "running": True,
        "total": 0,
        "completed": 0,
        "failed": 0,
        "current_slug": "",
        "errors": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    background_tasks.add_task(
        translator.bulk_translate,
        request.app.state,
        board=body.get("board"),
        subject=body.get("subject"),
        limit=body.get("limit", 50),
        skip_existing=body.get("skip_existing", True),
    )
    return {"status": "started", "message": "Bulk translation running in background"}


@router.get("/content/translate/status")
async def get_translate_status(request: Request):
    """Get current translation job status."""
    status = getattr(request.app.state, "translation_status", None)
    if not status:
        return {"running": False, "message": "No translation job has been run"}
    return status


@router.post("/content/translate/{slug}")
async def translate_single(slug: str):
    """Translate a single knowledge object. Runs synchronously."""
    ko = await KnowledgeObject.find_one({"slug": slug})
    if not ko:
        raise HTTPException(status_code=404, detail="Knowledge object not found")

    existing = await KnowledgeObject.find_one({"slug": f"{slug}-as"})
    if existing:
        raise HTTPException(status_code=409, detail="Assamese version already exists")

    translated = await translator.translate_knowledge_object(ko)
    await translated.insert()
    return {"status": "ok", "slug": translated.slug}
