"""Repository pattern implementations for data access layer.

This module provides a clean abstraction over database operations,
enabling:
- Testability through interface-based design
- Swappable storage backends (PostgreSQL, MongoDB, Supabase)
- Centralized query logic and validation
- Reduced coupling between routes and database drivers
"""
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any, Protocol
from datetime import datetime, timezone


class IUserRepository(Protocol):
    """Interface for user data operations."""
    
    async def get_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by unique identifier."""
        ...
    
    async def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get user by email address."""
        ...
    
    async def update(self, user_id: str, updates: Dict[str, Any]) -> bool:
        """Update user fields. Returns True if successful."""
        ...
    
    async def delete(self, user_id: str) -> bool:
        """Delete user. Returns True if successful."""
        ...


class IConversationRepository(Protocol):
    """Interface for conversation data operations."""
    
    async def get_by_user(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get conversations for a user."""
        ...
    
    async def get_by_id(self, conv_id: str) -> Optional[Dict[str, Any]]:
        """Get conversation by ID."""
        ...
    
    async def create(self, user_id: str, content: Dict[str, Any]) -> str:
        """Create a new conversation. Returns conversation ID."""
        ...
    
    async def update(self, conv_id: str, updates: Dict[str, Any]) -> bool:
        """Update conversation. Returns True if successful."""
        ...
    
    async def delete(self, conv_id: str) -> bool:
        """Delete conversation. Returns True if successful."""
        ...


class IUserSettingsRepository(Protocol):
    """Interface for user settings operations."""
    
    async def get(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user settings."""
        ...
    
    async def upsert(self, user_id: str, settings: Dict[str, Any]) -> bool:
        """Insert or update user settings."""
        ...
