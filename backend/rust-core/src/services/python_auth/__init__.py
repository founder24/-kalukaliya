"""Syrabit.ai — Cloudflare Turnstile + MongoDB Authentication

This module implements authentication using:
1. Cloudflare Turnstile for bot protection
2. MongoDB for user storage (not Supabase)
3. JWT tokens for session management
4. Support for both registered and anonymous users
"""
import os, asyncio, uuid, logging, hashlib
from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException, Request, Response
from motor.motor_asyncio import AsyncIOMotorClient
import jwt
from passlib.context import CryptContext

from config import (
    MONGO_URL, DB_NAME, JWT_SECRET, JWT_ALGORITHM,
    JWT_ACCESS_EXPIRE_MINUTES, JWT_REFRESH_EXPIRE_MINUTES,
    CLOUDFLARE_TURNSTILE_SECRET_KEY, TURNSTILE_ON,
    COOKIE_DOMAIN, COOKIE_SAMESITE, SECURE_COOKIES,
)

logger = logging.getLogger(__name__)

# MongoDB client
mongo_client: Optional[AsyncIOMotorClient] = None
db = None

try:
    if MONGO_URL:
        mongo_client = AsyncIOMotorClient(MONGO_URL)
        db = mongo_client[DB_NAME]
        logger.info("MongoDB auth client initialized")
except Exception as e:
    logger.warning(f"MongoDB auth client init failed: {e}")

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Get user from MongoDB by email"""
    if not db:
        return None
    try:
        user = await db.users.find_one({"email": email.lower()})
        if user:
            user["id"] = str(user["_id"])
        return user
    except Exception as e:
        logger.error(f"Error getting user by email: {e}")
        return None


async def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    """Get user from MongoDB by ID"""
    if not db:
        return None
    try:
        from bson import ObjectId
        user = await db.users.find_one({"_id": ObjectId(user_id)})
        if user:
            user["id"] = str(user["_id"])
        return user
    except Exception as e:
        logger.error(f"Error getting user by id: {e}")
        return None


async def create_user(
    email: str,
    password: str,
    name: str,
    consent_dpdp: bool = False,
    **kwargs
) -> Dict[str, Any]:
    """Create a new user in MongoDB"""
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    
    # Check if user exists
    existing = await get_user_by_email(email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    
    user_doc = {
        "_id": user_id,
        "email": email.lower(),
        "name": name,
        "password_hash": pwd_ctx.hash(password),
        "plan": "free",
        "credits_used": 0,
        "credits_limit": 30,
        "document_access": "zero",
        "onboarding_done": False,
        "is_admin": False,
        "status": "active",
        "bio": "",
        "phone": "",
        "saved_subjects": [],
        "has_free_credits_issued": True,
        "consent_dpdp": consent_dpdp,
        "consent_dpdp_version": "1.0" if consent_dpdp else None,
        "consent_dpdp_at": now.isoformat() if consent_dpdp else None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        **kwargs
    }
    
    result = await db.users.insert_one(user_doc)
    user_doc["id"] = str(result.inserted_id)
    return user_doc


async def verify_turnstile_token(token: str, remote_ip: str = "") -> bool:
    """Verify Cloudflare Turnstile token"""
    if not TURNSTILE_ON:
        return True
    
    if not CLOUDFLARE_TURNSTILE_SECRET_KEY:
        logger.warning("Turnstile secret key not configured")
        return False
    
    if not token:
        return False
    
    import httpx
    url = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    payload = {
        "secret": CLOUDFLARE_TURNSTILE_SECRET_KEY,
        "response": token
    }
    if remote_ip:
        payload["remoteip"] = remote_ip
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, data=payload)
            body = resp.json()
            return bool(body.get("success", False))
    except Exception as e:
        logger.error(f"Turnstile verification failed: {e}")
        return False


def create_access_token(user_id: str, role: str = "student", plan: str = "free") -> str:
    """Create JWT access token"""
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_ACCESS_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "role": role,
        "plan": plan,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access"
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    """Create JWT refresh token"""
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_REFRESH_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "refresh"
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and verify JWT token"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("Token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {e}")
        return None


async def save_anonymous_conversation(
    anon_id: str,
    conv_id: str,
    conv_data: dict,
    ttl_days: int = 7
) -> bool:
    """Save anonymous user conversation to MongoDB"""
    if not db:
        return False
    
    try:
        doc = {
            "_id": f"{anon_id}:{conv_id}",
            "anon_id": anon_id,
            "conv_id": conv_id,
            "data": conv_data,
            "created_at": datetime.now(timezone.utc),
            "expires_at": datetime.now(timezone.utc) + timedelta(days=ttl_days)
        }
        
        await db.anon_conversations.update_one(
            {"_id": doc["_id"]},
            {"$set": doc},
            upsert=True
        )
        
        # Update index
        await db.anon_conversations.update_one(
            {"anon_id": anon_id},
            {
                "$addToSet": {"conv_ids": conv_id},
                "$set": {"updated_at": datetime.now(timezone.utc)}
            },
            upsert=True
        )
        
        return True
    except Exception as e:
        logger.error(f"Error saving anon conversation: {e}")
        return False


async def get_anonymous_conversation(anon_id: str, conv_id: str) -> Optional[dict]:
    """Get anonymous user conversation from MongoDB"""
    if not db:
        return None
    
    try:
        doc = await db.anon_conversations.find_one({
            "_id": f"{anon_id}:{conv_id}"
        })
        
        if doc:
            return doc["data"]
        return None
    except Exception as e:
        logger.error(f"Error getting anon conversation: {e}")
        return None


async def list_anonymous_conversations(anon_id: str, limit: int = 20) -> list:
    """List all conversations for an anonymous user"""
    if not db:
        return []
    
    try:
        index_doc = await db.anon_conversations.find_one(
            {"anon_id": anon_id}
        )
        
        if not index_doc or "conv_ids" not in index_doc:
            return []
        
        conv_ids = index_doc["conv_ids"][-limit:]
        conversations = []
        
        for conv_id in reversed(conv_ids):
            conv = await get_anonymous_conversation(anon_id, conv_id)
            if conv:
                conversations.append({
                    "id": conv.get("id", conv_id),
                    "title": conv.get("title", "Untitled"),
                    "preview": conv.get("preview", ""),
                    "subject_name": conv.get("subject_name", ""),
                    "created_at": conv.get("created_at", ""),
                    "updated_at": conv.get("updated_at", ""),
                    "message_count": len(conv.get("messages", []))
                })
        
        return conversations
    except Exception as e:
        logger.error(f"Error listing anon conversations: {e}")
        return []


async def cleanup_expired_anon_conversations():
    """Remove expired anonymous conversations"""
    if not db:
        return 0
    
    try:
        result = await db.anon_conversations.delete_many({
            "expires_at": {"$lt": datetime.now(timezone.utc)}
        })
        logger.info(f"Cleaned up {result.deleted_count} expired anon conversations")
        return result.deleted_count
    except Exception as e:
        logger.error(f"Error cleaning up anon conversations: {e}")
        return 0


__all__ = [
    "get_user_by_email",
    "get_user_by_id",
    "create_user",
    "verify_turnstile_token",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "save_anonymous_conversation",
    "get_anonymous_conversation",
    "list_anonymous_conversations",
    "cleanup_expired_anon_conversations",
]
