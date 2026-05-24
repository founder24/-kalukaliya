import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import MagicMock, patch


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    """Create async test client with rate limiting disabled (no Redis in tests)."""
    from app.main import app

    async def _noop_rate_limit(*args, **kwargs):
        pass

    with patch("app.api.v1.auth._check_rate_limit", _noop_rate_limit):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest.fixture
def mock_user():
    """Create a mock authenticated user"""
    from app.models.user import User

    user = MagicMock(spec=User)
    user.id = "test-user-id-123"
    user.email = "test@example.com"
    user.name = "Test User"
    user.subscription_tier = "free"
    user.subscription_status = "active"
    user.monthly_message_count = 5
    user.preferred_language = "en"
    user.is_pro.return_value = False
    return user


@pytest.fixture
def auth_headers():
    """Generate valid JWT headers for testing"""
    from app.api.v1.auth import create_access_token

    token = create_access_token("test-user-id-123")
    return {"Authorization": f"Bearer {token}"}
