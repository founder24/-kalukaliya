"""
Admin Syra AI Assistant Endpoints
AI chat, actions, preferences, briefing, STT/TTS, CMS suggestions.
"""

from fastapi import APIRouter, HTTPException, Request
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/syra/chat")
async def syra_chat(request: Request):
    """Placeholder AI chat response."""
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    message = body.get("message", "")
    return {
        "response": f"Syra is not yet connected. You said: {message[:100]}",
        "source": "placeholder",
    }


@router.get("/syra/actions")
async def syra_actions(request: Request):
    """Return list of available Syra actions."""
    _validate_admin_session(request)
    return {
        "actions": [
            {
                "id": "summarize_feedback",
                "label": "Summarize Recent Feedback",
                "description": "Get a summary of user feedback",
            },
            {
                "id": "check_health",
                "label": "Check System Health",
                "description": "Run health checks on all services",
            },
            {
                "id": "generate_report",
                "label": "Generate Report",
                "description": "Generate admin analytics report",
            },
        ],
        "source": "placeholder",
    }


@router.post("/syra/execute-action")
async def syra_execute_action(request: Request):
    """Placeholder action execution."""
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    action_id = body.get("action_id", "")
    return {
        "action_id": action_id,
        "result": "Action execution is not yet connected.",
        "source": "placeholder",
    }


@router.get("/syra/prefs")
async def syra_prefs(request: Request):
    """Return admin Syra preferences."""
    _validate_admin_session(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        doc = await db.syra_prefs.find_one({"_id": "admin"})
        if doc:
            doc.pop("_id", None)
            return doc
        return {"voice_enabled": True, "auto_briefing": True, "theme": "default"}
    except Exception:
        return {"voice_enabled": True, "auto_briefing": True, "theme": "default"}


@router.put("/syra/prefs")
async def update_syra_prefs(request: Request):
    """Update Syra preferences."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()

        # Allow-list of permitted fields
        allowed_fields = {
            "voice_enabled",
            "auto_briefing",
            "theme",
            "language",
            "response_style",
            "notifications_enabled",
        }
        body = {k: v for k, v in body.items() if k in allowed_fields}
        if not body:
            raise HTTPException(status_code=400, detail="No valid fields provided")

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.syra_prefs.update_one({"_id": "admin"}, {"$set": body}, upsert=True)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error updating Syra prefs: {e}")
        return {"status": "error", "detail": "Internal server error"}


@router.get("/syra/briefing")
async def syra_briefing(request: Request):
    """Placeholder daily briefing."""
    _validate_admin_session(request)
    return {
        "briefing": "Daily briefing is not yet available.",
        "highlights": [],
        "source": "placeholder",
    }


@router.post("/syra/stt")
async def syra_stt(request: Request):
    """Placeholder speech-to-text."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"text": "", "source": "placeholder"}


@router.post("/syra/tts")
async def syra_tts(request: Request):
    """Placeholder text-to-speech."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"audio_url": None, "source": "placeholder"}


@router.post("/cms/ai-suggest")
async def cms_ai_suggest(request: Request):
    """Placeholder content suggestion."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"suggestions": [], "source": "placeholder"}
