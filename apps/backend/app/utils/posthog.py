"""PostHog utility - safe accessor for the PostHog client from app state."""

from fastapi import Request
from typing import Optional


def get_posthog(request: Optional[Request] = None):
    """
    Get PostHog client from app state.
    Returns None if PostHog is not configured or request is unavailable.
    """
    if not request:
        return None
    try:
        return getattr(request.app.state, "posthog", None)
    except Exception:
        return None
