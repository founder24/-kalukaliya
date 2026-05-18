from beanie import Document, Indexed
from pydantic import Field
from typing import List, Optional, Literal
from datetime import datetime
import uuid


class Message(Document):
    """Chat Message Model"""
    
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    model_used: Optional[str] = None
    latency_ms: Optional[int] = None
    thumbs_up: Optional[bool] = None


class RAGSource(Document):
    """RAG Source Citation"""
    
    doc_id: str
    title: str
    score: float
    url: Optional[str] = None


class Chat(Document):
    """Chat Session Model - MongoDB Schema"""
    
    user_id: Optional[str] = None  # References User._id
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: Optional[str] = None
    messages: List[dict] = []  # Embedded messages with RAG sources
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "chats"
        indexes = [
            [("user_id", 1), ("updated_at", -1)],
            [("session_id", 1)],
            [("updated_at", -1)],
        ]

    def add_message(self, role: str, content: str, model_used: str = None, 
                    latency_ms: int = None, rag_sources: List[dict] = None):
        """Add a message to the chat"""
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat(),
            "model_used": model_used,
            "latency_ms": latency_ms,
            "rag_sources": rag_sources or [],
            "feedback": {"thumbs_up": None}
        }
        self.messages.append(message)
        self.updated_at = datetime.utcnow()

    async def generate_title(self, llm_client) -> str:
        """Auto-generate chat title from first message"""
        if not self.title and len(self.messages) > 0:
            first_msg = self.messages[0]["content"][:50]
            self.title = f"Chat about {first_msg}..."
        return self.title
