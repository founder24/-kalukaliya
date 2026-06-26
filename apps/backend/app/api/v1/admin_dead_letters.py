"""Admin Dead Letter Endpoints - List and replay failed chat messages."""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from app.api.v1.admin import require_admin_session, csrf_guard
from app.services.dead_letter import list_dead_letters, replay_dead_letter

router = APIRouter(
    tags=["Admin Dead Letters"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


@router.get("/dead-letters")
async def get_dead_letters(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
):
    result = await list_dead_letters(
        page=page, page_size=page_size, status_filter=status
    )
    return result


@router.post("/dead-letters/{dead_letter_id}/replay")
async def replay(dead_letter_id: str):
    try:
        result = await replay_dead_letter(dead_letter_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Replay failed: {str(e)}")
