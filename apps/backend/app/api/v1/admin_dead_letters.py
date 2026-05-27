"""Admin Dead Letter Endpoints - List and replay failed chat messages."""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional

from app.api.v1.admin import _validate_admin_session, _csrf_check
from app.services.dead_letter import list_dead_letters, replay_dead_letter

router = APIRouter(tags=["Admin Dead Letters"])


@router.get("/dead-letters")
async def get_dead_letters(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
):
    _validate_admin_session(request)
    result = await list_dead_letters(
        page=page, page_size=page_size, status_filter=status
    )
    return result


@router.post("/dead-letters/{dead_letter_id}/replay")
async def replay(request: Request, dead_letter_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        result = await replay_dead_letter(dead_letter_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Replay failed: {str(e)}")
