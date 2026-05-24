from beanie import Document
from datetime import datetime
from pydantic import Field


class AuditLog(Document):
    """Audit Log Model - Security and Compliance Tracking"""

    user_id: str
    action: str  # login, logout, chat, subscription_change, etc.
    resource_type: str  # user, chat, subscription
    resource_id: str
    ip_address: str
    user_agent: str
    status: str = "success"  # success, failure
    metadata: dict = {}
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "audit_logs"
        indexes = [
            [("user_id", 1), ("timestamp", -1)],
            [("action", 1)],
            [("timestamp", -1)],  # TTL candidate
        ]
