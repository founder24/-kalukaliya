"""
Syrabit.ai — Authentication Routes

Cloudflare Turnstile + MongoDB Authentication API endpoints:
- Signup/Login with email/password
- Anonymous user authentication via device tokens
- JWT token management
- User migration (anon → registered)
"""
import os, logging, uuid, json
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Response, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

# Import auth functions from python_auth module
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.python_auth import (
    get_user_by_email,
    get_user_by_id,
    create_user,
    verify_turnstile_token,
    create_access_token,
    create_refresh_token,
    decode_token,
)

from services.python_auth.anon_chat_history import (
    save_conversation,
    get_conversation,
    list_conversations,
    migrate_anon_to_registered,
    get_stats,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: str
    turnstile_token: str
    consent_dpdp: bool = False


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    turnstile_token: str


class AnonLoginRequest(BaseModel):
    device_id: str
    turnstile_token: str


class MigrateAnonRequest(BaseModel):
    device_id: str
    email: str
    password: str
    name: Optional[str] = None
    consent_dpdp: bool = False
    turnstile_token: str


@router.post("/signup")
async def signup(request: SignupRequest, req: Request):
    """
    Register a new user account
    
    Requires Cloudflare Turnstile verification
    Creates user in MongoDB and returns JWT tokens
    """
    # Verify Turnstile token
    remote_ip = req.client.host if req.client else ""
    if not await verify_turnstile_token(request.turnstile_token, remote_ip):
        raise HTTPException(status_code=400, detail="Bot verification failed")
    
    try:
        # Create user in MongoDB
        user = await create_user(
            email=request.email,
            password=request.password,
            name=request.name,
            consent_dpdp=request.consent_dpdp
        )
        
        # Generate JWT tokens
        access_token = create_access_token(
            user["id"],
            role="student",
            plan=user.get("plan", "free")
        )
        refresh_token = create_refresh_token(user["id"])
        
        logger.info(f"User signed up: {request.email}")
        
        return JSONResponse(content={
            "success": True,
            "user": {
                "id": user["id"],
                "email": user["email"],
                "name": user["name"],
                "plan": user.get("plan", "free")
            },
            "tokens": {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer"
            }
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Signup error: {e}")
        raise HTTPException(status_code=500, detail="Registration failed")


@router.post("/login")
async def login(request: LoginRequest, req: Request):
    """
    Login with email and password
    
    Verifies Turnstile token and credentials
    Returns JWT tokens for authenticated session
    """
    # Verify Turnstile token
    remote_ip = req.client.host if req.client else ""
    if not await verify_turnstile_token(request.turnstile_token, remote_ip):
        raise HTTPException(status_code=400, detail="Bot verification failed")
    
    try:
        # Get user from MongoDB
        user = await get_user_by_email(request.email)
        
        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        # Verify password
        from passlib.context import CryptContext
        pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        
        if not pwd_ctx.verify(request.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        # Generate JWT tokens
        access_token = create_access_token(
            user["id"],
            role="student",
            plan=user.get("plan", "free")
        )
        refresh_token = create_refresh_token(user["id"])
        
        logger.info(f"User logged in: {request.email}")
        
        return JSONResponse(content={
            "success": True,
            "user": {
                "id": user["id"],
                "email": user["email"],
                "name": user["name"],
                "plan": user.get("plan", "free")
            },
            "tokens": {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer"
            }
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(status_code=500, detail="Login failed")


@router.post("/anon-login")
async def anon_login(request: AnonLoginRequest, req: Request, response: Response):
    """
    Anonymous user login via device token
    
    Creates/validates anonymous session
    Sets device token cookie for persistent identity
    """
    # Verify Turnstile token
    remote_ip = req.client.host if req.client else ""
    if not await verify_turnstile_token(request.turnstile_token, remote_ip):
        raise HTTPException(status_code=400, detail="Bot verification failed")
    
    try:
        # Use device_id as anonymous identifier
        anon_id = request.device_id or str(uuid.uuid4())
        
        # Generate a temporary access token for anon user
        access_token = create_access_token(
            anon_id,
            role="anonymous",
            plan="free"
        )
        
        # Set device token cookie
        response.set_cookie(
            key="device_id",
            value=anon_id,
            max_age=604800,  # 7 days
            httponly=True,
            secure=os.getenv("SECURE_COOKIES", "true").lower() == "true",
            samesite="lax",
            domain=os.getenv("COOKIE_DOMAIN", None)
        )
        
        # Get existing conversations for this device
        convos = await list_conversations(anon_id, limit=10)
        stats = await get_stats(anon_id)
        
        logger.info(f"Anonymous user login: {anon_id[:8]}...")
        
        return JSONResponse(content={
            "success": True,
            "anonymous": True,
            "anon_id": anon_id,
            "tokens": {
                "access_token": access_token,
                "token_type": "bearer"
            },
            "existing_conversations": len(convos),
            "stats": stats
        })
        
    except Exception as e:
        logger.error(f"Anon login error: {e}")
        raise HTTPException(status_code=500, detail="Anonymous login failed")


@router.post("/migrate-anon-to-registered")
async def migrate_anon(request: MigrateAnonRequest, req: Request):
    """
    Migrate anonymous user conversations to registered account
    
    Called when an anonymous user decides to sign up.
    Transfers all their chat history to the new account.
    """
    # Verify Turnstile token
    remote_ip = req.client.host if req.client else ""
    if not await verify_turnstile_token(request.turnstile_token, remote_ip):
        raise HTTPException(status_code=400, detail="Bot verification failed")
    
    try:
        # Check if email already exists
        existing_user = await get_user_by_email(request.email)
        if existing_user:
            raise HTTPException(status_code=400, detail="Email already registered")
        
        # Create registered user account
        user = await create_user(
            email=request.email,
            password=request.password,
            name=request.name or "User",
            consent_dpdp=request.consent_dpdp,
            device_id=request.device_id  # Link to anon device
        )
        
        # Migrate anonymous conversations to registered user
        migrated_count = await migrate_anon_to_registered(
            anon_id=request.device_id,
            user_id=user["id"],
            email=request.email
        )
        
        # Generate JWT tokens for new account
        access_token = create_access_token(
            user["id"],
            role="student",
            plan=user.get("plan", "free")
        )
        refresh_token = create_refresh_token(user["id"])
        
        logger.info(f"Migrated {migrated_count} conversations from anon {request.device_id[:8]}... to {request.email}")
        
        return JSONResponse(content={
            "success": True,
            "migrated": True,
            "conversations_migrated": migrated_count,
            "user": {
                "id": user["id"],
                "email": user["email"],
                "name": user["name"],
                "plan": user.get("plan", "free")
            },
            "tokens": {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer"
            }
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Migration error: {e}")
        raise HTTPException(status_code=500, detail="Migration failed")


@router.get("/me")
async def get_current_user(request: Request):
    """
    Get current authenticated user info
    
    Validates JWT token from Authorization header
    Returns user profile from MongoDB
    """
    auth_header = request.headers.get("Authorization", "")
    
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    
    # Check if anonymous user
    if payload.get("role") == "anonymous":
        return JSONResponse(content={
            "anonymous": True,
            "anon_id": payload.get("sub"),
            "role": "anonymous",
            "plan": payload.get("plan", "free")
        })
    
    # Get registered user from MongoDB
    user = await get_user_by_id(payload["sub"])
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return JSONResponse(content={
        "anonymous": False,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "plan": user.get("plan", "free"),
            "credits_used": user.get("credits_used", 0),
            "credits_limit": user.get("credits_limit", 30),
            "onboarding_done": user.get("onboarding_done", False)
        }
    })


@router.post("/refresh")
async def refresh_token(request: Request):
    """
    Refresh access token using refresh token
    
    Validates refresh token and issues new access token
    """
    auth_header = request.headers.get("Authorization", "")
    
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    
    # Verify token hasn't expired
    if payload.get("exp", 0) < datetime.now(timezone.utc).timestamp():
        raise HTTPException(status_code=401, detail="Refresh token expired")
    
    user_id = payload["sub"]
    
    # Get user to check status
    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Generate new access token
    new_access_token = create_access_token(
        user_id,
        role="student",
        plan=user.get("plan", "free")
    )
    
    return JSONResponse(content={
        "success": True,
        "access_token": new_access_token,
        "token_type": "bearer"
    })


@router.post("/logout")
async def logout(response: Response):
    """
    Logout user
    
    Clears authentication cookies
    Tokens are invalidated client-side
    """
    response.delete_cookie("device_id")
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    
    return JSONResponse(content={"success": True, "message": "Logged out successfully"})


__all__ = ["router"]
