"""
Admin Translation Endpoints - Bulk and single content translation to Assamese.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, BackgroundTasks

from app.api.v1.admin import _validate_admin_session, _csrf_check
from app.services.content.translator import ContentTranslator
from app.models.knowledge import KnowledgeObject

router = APIRouter(tags=["Admin Translation"])
translator = ContentTranslator()


@router.post("/content/translate/bulk")
async def bulk_translate(request: Request, background_tasks: BackgroundTasks):
    """Trigger bulk translation. Body: {board?, subject?, limit?, skip_existing?}"""
    _validate_admin_session(request)
    await _csrf_check(request)

    body = await request.json() if await request.body() else {}

    # Check if already running
    status = getattr(request.app.state, "translation_status", None)
    if status and status.get("running"):
        raise HTTPException(
            status_code=409, detail="Translation job already running"
        )

    # Initialize status
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
    _validate_admin_session(request)
    status = getattr(request.app.state, "translation_status", None)
    if not status:
        return {"running": False, "message": "No translation job has been run"}
    return status


@router.post("/content/translate/{slug}")
async def translate_single(request: Request, slug: str):
    """Translate a single knowledge object. Runs synchronously."""
    _validate_admin_session(request)
    await _csrf_check(request)

    ko = await KnowledgeObject.find_one({"slug": slug})
    if not ko:
        raise HTTPException(status_code=404, detail="Knowledge object not found")

    # Check if already translated
    existing = await KnowledgeObject.find_one({"slug": f"{slug}-as"})
    if existing:
        raise HTTPException(status_code=409, detail="Assamese version already exists")

    translated = await translator.translate_knowledge_object(ko)
    await translated.insert()
    return {"status": "ok", "slug": translated.slug}
