from typing import Optional
from fastapi import Request

try:
    from posthog import Posthog
except ImportError:
    Posthog = None


def get_posthog(request: Request) -> Optional["Posthog"]:
    """
    Retrieve the PostHog client from the application state.

    Returns None if PostHog is not configured or the request has no app state.
    """
    try:
        return request.app.state.posthog
    except AttributeError:
        return None
