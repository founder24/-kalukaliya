"""
API Changelog Endpoint
Returns version history of the API.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Changelog"])

CHANGELOG = [
    {
        "version": "3.0.0",
        "date": "2025-01-01",
        "changes": ["Initial stable API release"],
    },
]


@router.get("/changelog")
async def get_changelog():
    """Return API version changelog."""
    return CHANGELOG
