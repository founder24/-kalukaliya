from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime, timedelta
from jose import jwt, JWTError
import logging

from app.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Authentication"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


def create_access_token(user_id: str, expires_delta: timedelta = None) -> str:
    """Create JWT access token"""
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.JWT_EXPIRY_MINUTES))
    to_encode = {"sub": user_id, "exp": expire, "type": "access"}
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str, expires_delta: timedelta = None) -> str:
    """Create JWT refresh token"""
    expire = datetime.utcnow() + (expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRY_DAYS))
    to_encode = {"sub": user_id, "exp": expire, "type": "refresh"}
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


async def get_current_user(token: str = Depends(lambda: None)) -> User:
    """Get current user from JWT token (placeholder - implement proper dependency)"""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        user = await User.get(user_id)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


@router.post("/signup", response_model=TokenResponse)
async def signup(request: SignupRequest):
    """Register a new user"""
    # Check if user exists
    existing_user = await User.find_one({"email": request.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create user
    hashed_pw = User.hash_password(request.password)
    user = User(
        email=request.email,
        hashed_password=hashed_pw,
        name=request.name,
        auth_provider="local",
    )
    await user.insert()

    # Generate tokens
    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    logger.info(f"New user signed up: {request.email}")
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """Authenticate user and return tokens"""
    user = await User.find_one({"email": request.email})
    
    if not user or not user.hashed_password:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.verify_password(request.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Generate tokens
    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    logger.info(f"User logged in: {request.email}")
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(refresh_token: str):
    """Refresh access token using refresh token"""
    try:
        payload = jwt.decode(refresh_token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        
        user_id = payload.get("sub")
        user = await User.get(user_id)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        # Generate new tokens
        new_access_token = create_access_token(str(user.id))
        new_refresh_token = create_refresh_token(str(user.id))

        return TokenResponse(access_token=new_access_token, refresh_token=new_refresh_token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
