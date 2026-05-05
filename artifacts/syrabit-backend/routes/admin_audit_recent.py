"""Admin audit-log feed served via ``d1_mirror.read_audit_log_recent``
(D1-first, Mongo fallback)."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth_deps import get_admin_user
from d1_mirror import read_audit_log_recent

router = APIRouter()

_db = None  # populated by server.init_admin_audit_recent()


def init_admin_audit_recent(db) -> None:
    global _db
    _db = db


@router.get("/admin/audit/recent")
async def admin_audit_recent(
    limit: int = Query(50, ge=1, le=500),
    admin: dict = Depends(get_admin_user),
):
    if _db is None:
        raise HTTPException(status_code=503, detail="audit feed not initialised")
    rows = await read_audit_log_recent(int(limit), _db)
    return {"count": len(rows), "rows": rows, "source": "d1_or_mongo"}
