"""Routes module for Syrabit.ai Python authentication services"""

from .auth import router as auth_router
from .ai_chat import router as chat_router

__all__ = ["auth_router", "chat_router"]
