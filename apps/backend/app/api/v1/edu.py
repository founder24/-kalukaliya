"""
Educational content endpoints - Coming Soon.
These stubs return 501 so the frontend gets a clear signal these features
are not yet available rather than confusing 404s.
"""
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["Education"], prefix="/edu")


@router.get("/quiz/{subject}")
async def quiz_not_implemented(subject: str):
    raise HTTPException(status_code=501, detail="This feature is coming soon")


@router.get("/notes/{subject}")
async def notes_not_implemented(subject: str):
    raise HTTPException(status_code=501, detail="This feature is coming soon")


@router.get("/flashcards/{subject}")
async def flashcards_not_implemented(subject: str):
    raise HTTPException(status_code=501, detail="This feature is coming soon")


@router.get("/settings")
async def settings_not_implemented():
    raise HTTPException(status_code=501, detail="This feature is coming soon")


@router.post("/sync")
async def sync_not_implemented():
    raise HTTPException(status_code=501, detail="This feature is coming soon")


@router.get("/voice/{session_id}")
async def voice_not_implemented(session_id: str):
    raise HTTPException(status_code=501, detail="This feature is coming soon")
