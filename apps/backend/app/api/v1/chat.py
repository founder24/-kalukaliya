from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
import logging
import time

from app.config import settings
from app.models.user import User
from app.services.ai.router import detect_language_and_route
from app.services.search.azure_search import search_service
from app.db.redis import get_redis

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    context_messages: List[dict] = []


class ChatResponse(BaseModel):
    response: str
    model_used: str
    latency_ms: int
    sources: List[dict] = []


async def check_rate_limit(user_id: str, user_tier: str) -> bool:
    """Check if user has exceeded rate limit"""
    redis = get_redis()
    
    limit = settings.RATE_LIMIT_PRO_TIER if user_tier == "pro" else settings.RATE_LIMIT_FREE_TIER
    key = f"rate:{user_id}:{time.strftime('%Y-%m')}"
    
    current_count = await redis.incr(key)
    if current_count == 1:
        # Set expiry to end of month
        from datetime import datetime
        next_month = datetime.now().replace(day=28) + timedelta(days=4)
        expire_at = next_month.replace(day=1, hour=0, minute=0, second=0)
        ttl = int(expire_at.timestamp() - time.time())
        await redis.expire(key, ttl)
    
    return current_count <= limit


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest, user: User = None):
    """
    Main chat endpoint with RAG and streaming support
    Handles language detection, hybrid search, and LLM routing
    """
    start_time = time.time()
    
    # Determine user tier
    user_tier = user.subscription_tier if user else "free"
    user_id = str(user.id) if user else request.session_id or "anonymous"
    
    # Check rate limit
    if not await check_rate_limit(user_id, user_tier):
        raise HTTPException(
            status_code=429, 
            detail="Rate limit exceeded. Upgrade to Pro for unlimited messages."
        )
    
    try:
        # 1. Detect language and route to appropriate model
        detected_lang, target_model = detect_language_and_route(request.message)
        
        # 2. Generate embedding for RAG
        from app.services.ai.embedder import generate_embedding
        embedding = await generate_embedding(request.message)
        
        # 3. Hybrid search with semantic reranking
        context_chunks = await search_service.search_context(
            query=request.message,
            embedding=embedding,
            user_tier=user_tier,
            limit=settings.MAX_CONTEXT_DOCS
        )
        
        # 4. Build prompt with context
        system_prompt = f"""You are Syrabit, an expert educational assistant for Assamese students.
Use the following context to answer. If the answer is not in the context, say so.
Cite sources using [Source Title].
Language: Respond in {detected_lang}.

Context:
"""
        for chunk in context_chunks:
            system_prompt += f"[{chunk['title']}]: {chunk['content']}\n\n"
        
        # 5. Call LLM
        from app.services.ai.router import generate_response
        response_text = await generate_response(
            system_prompt=system_prompt,
            user_message=request.message,
            model=target_model,
            stream=False
        )
        
        # Calculate latency
        latency_ms = int((time.time() - start_time) * 1000)
        
        # 6. Save chat to MongoDB (async background task)
        from app.models.chat import Chat
        chat_doc = Chat(
            user_id=user_id if user else None,
            session_id=request.session_id,
        )
        chat_doc.add_message(
            role="user",
            content=request.message,
        )
        chat_doc.add_message(
            role="assistant",
            content=response_text,
            model_used=target_model,
            latency_ms=latency_ms,
            rag_sources=[{"doc_id": c["id"], "title": c["title"], "score": c["score"]} for c in context_chunks]
        )
        await chat_doc.save()
        
        # 7. Update usage counter
        if user:
            await user.update({
                "$inc": {"monthly_message_count": 1, "total_lifetime_messages": 1},
                "$set": {"updated_at": time.strftime('%Y-%m-%dT%H:%M:%SZ')}
            })
        
        logger.info(f"Chat completed for user {user_id} in {latency_ms}ms")
        
        return ChatResponse(
            response=response_text,
            model_used=target_model,
            latency_ms=latency_ms,
            sources=[{"doc_id": c["id"], "title": c["title"], "score": c["score"], "url": c["url"]} for c in context_chunks]
        )
        
    except Exception as e:
        logger.error(f"Chat error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to process chat: {str(e)}")
