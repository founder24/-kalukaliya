"""Syrabit.ai — Enhanced Anonymous Chat History with MongoDB

This module provides full chat history functionality for anonymous users:
1. Store conversations in MongoDB with TTL expiration
2. Retrieve conversation history by device token
3. Merge anonymous conversations when user registers
4. Automatic cleanup of expired conversations
"""
import os, asyncio, logging, json
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta
from motor.motor_asyncio import AsyncIOMotorClient

from config import MONGO_URL, DB_NAME

logger = logging.getLogger(__name__)

# MongoDB client
mongo_client: Optional[AsyncIOMotorClient] = None
db = None

try:
    if MONGO_URL:
        mongo_client = AsyncIOMotorClient(MONGO_URL)
        db = mongo_client[DB_NAME]
        logger.info("MongoDB anon chat history client initialized")
except Exception as e:
    logger.warning(f"MongoDB anon chat history client init failed: {e}")


async def ensure_indexes():
    """Create necessary indexes for anonymous conversations"""
    if not db:
        return
    
    try:
        # Index for finding conversations by anon_id
        await db.anon_conversations.create_index([("anon_id", 1)])
        
        # Index for expiration (TTL)
        await db.anon_conversations.create_index(
            [("expires_at", 1)],
            expireAfterSeconds=0
        )
        
        # Index for quick lookup by compound key
        await db.anon_conversations.create_index([("anon_id", 1), ("conv_id", 1)])
        
        # Index for user index collection
        await db.anon_user_index.create_index([("anon_id", 1)], unique=True)
        
        logger.info("Created indexes for anonymous conversations")
    except Exception as e:
        logger.error(f"Error creating indexes: {e}")


async def save_conversation(
    anon_id: str,
    conv_id: str,
    title: str,
    messages: List[Dict],
    subject_name: Optional[str] = None,
    board_id: Optional[str] = None,
    class_id: Optional[str] = None,
    ttl_days: int = 7
) -> bool:
    """
    Save an anonymous user's conversation to MongoDB
    
    Args:
        anon_id: Unique identifier for the anonymous user (from device token)
        conv_id: Unique identifier for the conversation
        title: Conversation title
        messages: List of message objects
        subject_name: Optional subject name
        board_id: Optional board ID
        class_id: Optional class ID
        ttl_days: Days before conversation expires (default 7)
    
    Returns:
        True if saved successfully, False otherwise
    """
    if not db:
        logger.warning("Database not available for saving anon conversation")
        return False
    
    now = datetime.now(timezone.utc)
    
    try:
        # Prepare conversation document
        conv_doc = {
            "_id": f"{anon_id}:{conv_id}",
            "anon_id": anon_id,
            "conv_id": conv_id,
            "title": title or "Untitled Conversation",
            "messages": messages,
            "subject_name": subject_name,
            "board_id": board_id,
            "class_id": class_id,
            "message_count": len(messages),
            "preview": messages[-1].get("content", "")[:100] if messages else "",
            "created_at": now,
            "updated_at": now,
            "expires_at": now + timedelta(days=ttl_days)
        }
        
        # Upsert conversation
        result = await db.anon_conversations.update_one(
            {"_id": conv_doc["_id"]},
            {"$set": conv_doc},
            upsert=True
        )
        
        # Update user's conversation index
        await db.anon_user_index.update_one(
            {"anon_id": anon_id},
            {
                "$addToSet": {"conv_ids": conv_id},
                "$set": {
                    "updated_at": now,
                    "last_conv_id": conv_id,
                    "conv_count": len(set((await db.anon_user_index.find_one({"anon_id": anon_id}) or {}).get("conv_ids", [])) + [conv_id])
                }
            },
            upsert=True
        )
        
        logger.debug(f"Saved anon conversation {conv_id} for user {anon_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error saving anon conversation: {e}")
        return False


async def get_conversation(anon_id: str, conv_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve a specific conversation for an anonymous user
    
    Args:
        anon_id: Unique identifier for the anonymous user
        conv_id: Conversation ID to retrieve
    
    Returns:
        Conversation document or None if not found
    """
    if not db:
        return None
    
    try:
        doc = await db.anon_conversations.find_one({
            "_id": f"{anon_id}:{conv_id}"
        })
        
        if not doc:
            return None
        
        # Convert ObjectId to string and format response
        return {
            "id": doc["conv_id"],
            "title": doc.get("title", "Untitled"),
            "messages": doc.get("messages", []),
            "subject_name": doc.get("subject_name"),
            "board_id": doc.get("board_id"),
            "class_id": doc.get("class_id"),
            "created_at": doc.get("created_at", "").isoformat() if isinstance(doc.get("created_at"), datetime) else doc.get("created_at", ""),
            "updated_at": doc.get("updated_at", "").isoformat() if isinstance(doc.get("updated_at"), datetime) else doc.get("updated_at", ""),
            "message_count": doc.get("message_count", 0)
        }
        
    except Exception as e:
        logger.error(f"Error getting anon conversation: {e}")
        return None


async def list_conversations(
    anon_id: str,
    limit: int = 20,
    skip: int = 0
) -> List[Dict[str, Any]]:
    """
    List all conversations for an anonymous user
    
    Args:
        anon_id: Unique identifier for the anonymous user
        limit: Maximum number of conversations to return
        skip: Number of conversations to skip (for pagination)
    
    Returns:
        List of conversation summaries
    """
    if not db:
        return []
    
    try:
        # Get user's conversation index
        user_index = await db.anon_user_index.find_one({"anon_id": anon_id})
        
        if not user_index or "conv_ids" not in user_index:
            return []
        
        conv_ids = user_index["conv_ids"]
        
        # Apply pagination
        paginated_ids = list(reversed(conv_ids))[skip:skip+limit]
        
        conversations = []
        for conv_id in paginated_ids:
            conv = await get_conversation(anon_id, conv_id)
            if conv:
                conversations.append({
                    "id": conv["id"],
                    "title": conv["title"],
                    "preview": conv.get("preview", ""),
                    "subject_name": conv.get("subject_name"),
                    "created_at": conv["created_at"],
                    "updated_at": conv["updated_at"],
                    "message_count": conv["message_count"]
                })
        
        return conversations
        
    except Exception as e:
        logger.error(f"Error listing anon conversations: {e}")
        return []


async def delete_conversation(anon_id: str, conv_id: str) -> bool:
    """
    Delete a specific conversation
    
    Args:
        anon_id: Anonymous user ID
        conv_id: Conversation ID to delete
    
    Returns:
        True if deleted, False otherwise
    """
    if not db:
        return False
    
    try:
        result = await db.anon_conversations.delete_one({
            "_id": f"{anon_id}:{conv_id}"
        })
        
        # Remove from user index
        await db.anon_user_index.update_one(
            {"anon_id": anon_id},
            {"$pull": {"conv_ids": conv_id}}
        )
        
        return result.deleted_count > 0
        
    except Exception as e:
        logger.error(f"Error deleting anon conversation: {e}")
        return False


async def migrate_anon_to_registered(
    anon_id: str,
    user_id: str,
    email: str
) -> int:
    """
    Migrate anonymous conversations to a registered user account
    
    Called when an anonymous user decides to register/sign up.
    Transfers all their conversations from anon storage to user storage.
    
    Args:
        anon_id: Anonymous user ID
        user_id: New registered user ID
        email: User's email
    
    Returns:
        Number of conversations migrated
    """
    if not db:
        return 0
    
    try:
        # Get all anon conversations
        conversations = await list_conversations(anon_id, limit=100)
        migrated_count = 0
        
        for conv_summary in conversations:
            conv_id = conv_summary["id"]
            full_conv = await get_conversation(anon_id, conv_id)
            
            if full_conv:
                # Create registered user conversation
                user_conv = {
                    "_id": conv_id,
                    "user_id": user_id,
                    "email": email,
                    "title": full_conv["title"],
                    "messages": full_conv["messages"],
                    "subject_name": full_conv.get("subject_name"),
                    "board_id": full_conv.get("board_id"),
                    "class_id": full_conv.get("class_id"),
                    "created_at": full_conv["created_at"],
                    "updated_at": full_conv["updated_at"],
                    "is_migrated_from_anon": True,
                    "migrated_at": datetime.now(timezone.utc)
                }
                
                await db.conversations.update_one(
                    {"_id": conv_id},
                    {"$set": user_conv},
                    upsert=True
                )
                
                migrated_count += 1
        
        # Delete anon conversations after migration
        await db.anon_user_index.delete_one({"anon_id": anon_id})
        for conv_summary in conversations:
            await db.anon_conversations.delete_one({
                "_id": f"{anon_id}:{conv_summary['id']}"
            })
        
        logger.info(f"Migrated {migrated_count} conversations from anon {anon_id} to user {user_id}")
        return migrated_count
        
    except Exception as e:
        logger.error(f"Error migrating anon conversations: {e}")
        return 0


async def cleanup_expired_conversations() -> int:
    """
    Remove expired anonymous conversations
    
    Should be called periodically (e.g., daily cron job).
    MongoDB TTL index also handles this automatically.
    
    Returns:
        Number of conversations cleaned up
    """
    if not db:
        return 0
    
    try:
        result = await db.anon_conversations.delete_many({
            "expires_at": {"$lt": datetime.now(timezone.utc)}
        })
        
        logger.info(f"Cleaned up {result.deleted_count} expired anon conversations")
        return result.deleted_count
        
    except Exception as e:
        logger.error(f"Error cleaning up expired conversations: {e}")
        return 0


async def get_stats(anon_id: str) -> Dict[str, Any]:
    """
    Get statistics for an anonymous user
    
    Args:
        anon_id: Anonymous user ID
    
    Returns:
        Dictionary with stats
    """
    if not db:
        return {"conversation_count": 0, "total_messages": 0}
    
    try:
        user_index = await db.anon_user_index.find_one({"anon_id": anon_id})
        
        if not user_index:
            return {"conversation_count": 0, "total_messages": 0}
        
        conv_ids = user_index.get("conv_ids", [])
        
        # Count total messages
        total_messages = 0
        for conv_id in conv_ids:
            conv = await db.anon_conversations.find_one(
                {"_id": f"{anon_id}:{conv_id}"},
                {"message_count": 1}
            )
            if conv:
                total_messages += conv.get("message_count", 0)
        
        return {
            "conversation_count": len(conv_ids),
            "total_messages": total_messages,
            "oldest_conv": user_index.get("created_at"),
            "newest_conv": user_index.get("updated_at")
        }
        
    except Exception as e:
        logger.error(f"Error getting anon stats: {e}")
        return {"conversation_count": 0, "total_messages": 0}


__all__ = [
    "ensure_indexes",
    "save_conversation",
    "get_conversation",
    "list_conversations",
    "delete_conversation",
    "migrate_anon_to_registered",
    "cleanup_expired_conversations",
    "get_stats",
]
