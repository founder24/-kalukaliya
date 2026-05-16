"""PostgreSQL and MongoDB implementation of user repository."""
from typing import Optional, Dict, Any, List
import logging

from deps import db, pg_pool, supa
from cache import _invalidate_user_cache
from repositories import IUserRepository

logger = logging.getLogger(__name__)


class SupabaseUserRepository:
    """User repository implementation using Supabase (PostgreSQL)."""
    
    def __init__(self):
        self.supa = supa
    
    async def get_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by unique identifier from Supabase."""
        if not self.supa:
            logger.error("Supabase client not initialized")
            return None
        
        try:
            response = self.supa.table("users").select("*").eq("id", user_id).execute()
            if response.data and len(response.data) > 0:
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"Error fetching user {user_id}: {e}")
            return None
    
    async def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get user by email address from Supabase."""
        if not self.supa:
            logger.error("Supabase client not initialized")
            return None
        
        try:
            response = self.supa.table("users").select("*").eq("email", email).execute()
            if response.data and len(response.data) > 0:
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"Error fetching user by email {email}: {e}")
            return None
    
    async def update(self, user_id: str, updates: Dict[str, Any]) -> bool:
        """Update user fields in Supabase."""
        if not self.supa:
            logger.error("Supabase client not initialized")
            return False
        
        try:
            response = self.supa.table("users").update(updates).eq("id", user_id).execute()
            # Invalidate cache after update
            await _invalidate_user_cache(user_id)
            return True
        except Exception as e:
            logger.error(f"Error updating user {user_id}: {e}")
            return False
    
    async def delete(self, user_id: str) -> bool:
        """Soft delete user in Supabase (mark as deleted)."""
        if not self.supa:
            logger.error("Supabase client not initialized")
            return False
        
        try:
            # Soft delete - mark as deleted rather than removing
            response = self.supa.table("users").update({
                "deleted_at": datetime.now(timezone.utc).isoformat(),
                "account_status": "deleted"
            }).eq("id", user_id).execute()
            
            await _invalidate_user_cache(user_id)
            return True
        except Exception as e:
            logger.error(f"Error deleting user {user_id}: {e}")
            return False


class MongoUserRepository:
    """User repository implementation using MongoDB."""
    
    def __init__(self):
        self.db = db
    
    async def get_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by unique identifier from MongoDB."""
        if not self.db:
            logger.error("MongoDB client not initialized")
            return None
        
        try:
            return await self.db.users.find_one({"id": user_id})
        except Exception as e:
            logger.error(f"Error fetching user {user_id} from MongoDB: {e}")
            return None
    
    async def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get user by email address from MongoDB."""
        if not self.db:
            logger.error("MongoDB client not initialized")
            return None
        
        try:
            return await self.db.users.find_one({"email": email})
        except Exception as e:
            logger.error(f"Error fetching user by email {email} from MongoDB: {e}")
            return None
    
    async def update(self, user_id: str, updates: Dict[str, Any]) -> bool:
        """Update user fields in MongoDB."""
        if not self.db:
            logger.error("MongoDB client not initialized")
            return False
        
        try:
            result = await self.db.users.update_one(
                {"id": user_id},
                {"$set": updates}
            )
            await _invalidate_user_cache(user_id)
            return result.modified_count > 0 or result.matched_count > 0
        except Exception as e:
            logger.error(f"Error updating user {user_id} in MongoDB: {e}")
            return False
    
    async def delete(self, user_id: str) -> bool:
        """Soft delete user in MongoDB."""
        if not self.db:
            logger.error("MongoDB client not initialized")
            return False
        
        try:
            result = await self.db.users.update_one(
                {"id": user_id},
                {"$set": {
                    "deleted_at": datetime.now(timezone.utc),
                    "account_status": "deleted"
                }}
            )
            await _invalidate_user_cache(user_id)
            return True
        except Exception as e:
            logger.error(f"Error deleting user {user_id} from MongoDB: {e}")
            return False
