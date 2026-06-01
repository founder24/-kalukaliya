"""
Tests for MongoDB initialization bug fixes:
- _client reset on failure
- Lifespan re-raises in production
- Health check Beanie-awareness
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pymongo.errors import ConnectionFailure


class TestClientResetOnFailure:
    """Verify _client is reset to None when init_beanie or connection fails."""

    @pytest.mark.anyio
    async def test_client_reset_on_non_connection_failure(self):
        """_client is reset to None when init_beanie raises a non-ConnectionFailure exception."""
        import app.db.mongo as mongo_module

        mongo_module._client = None

        with patch.object(mongo_module, "settings") as mock_settings:
            mock_settings.MONGODB_URI = "mongodb://fake:27017"
            mock_settings.MONGODB_MAX_POOL_SIZE = 10
            mock_settings.MONGODB_MIN_POOL_SIZE = 1
            mock_settings.MONGODB_DB_NAME = "testdb"
            mock_settings.APP_ENV = "test"

            with patch(
                "app.db.mongo.init_beanie",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Beanie init failed"),
            ):
                with pytest.raises(RuntimeError, match="Beanie init failed"):
                    await mongo_module.init_mongo()

        assert mongo_module._client is None

    @pytest.mark.anyio
    async def test_client_reset_after_all_connection_retries_exhausted(self):
        """_client is reset to None when ConnectionFailure is raised on all retries."""
        import app.db.mongo as mongo_module

        mongo_module._client = None

        with patch.object(mongo_module, "settings") as mock_settings:
            mock_settings.MONGODB_URI = "mongodb://fake:27017"
            mock_settings.MONGODB_MAX_POOL_SIZE = 10
            mock_settings.MONGODB_MIN_POOL_SIZE = 1
            mock_settings.MONGODB_DB_NAME = "testdb"
            mock_settings.APP_ENV = "test"

            with patch(
                "app.db.mongo.AsyncMongoClient",
                side_effect=ConnectionFailure("connection refused"),
            ):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with pytest.raises(ConnectionFailure):
                        await mongo_module.init_mongo()

        assert mongo_module._client is None

    @pytest.mark.anyio
    async def test_get_mongo_client_raises_after_failed_init(self):
        """get_mongo_client() raises RuntimeError when _client is None after failed init."""
        import app.db.mongo as mongo_module

        mongo_module._client = None

        with pytest.raises(RuntimeError, match="MongoDB not initialized"):
            mongo_module.get_mongo_client()


class TestLifespanProductionBehavior:
    """Verify lifespan re-raises init_mongo errors in production but swallows in dev."""

    @pytest.mark.anyio
    async def test_lifespan_reraises_in_production(self):
        """In production APP_ENV, init_mongo failure causes exception to propagate."""
        from app.main import lifespan

        mock_app = MagicMock()

        with patch("app.main.settings") as mock_settings:
            mock_settings.APP_ENV = "production"
            mock_settings.ADMIN_EMAIL = None
            mock_settings.ADMIN_PASSWORD = None
            mock_settings.JWT_SECRET = "test-secret-at-least-32-characters-long"
            mock_settings.SENTRY_DSN = None
            mock_settings.POSTHOG_API_KEY = None

            with patch(
                "app.main.init_mongo",
                new_callable=AsyncMock,
                side_effect=ConnectionFailure("connection refused"),
            ):
                with pytest.raises(ConnectionFailure):
                    async with lifespan(mock_app):
                        pass

    @pytest.mark.anyio
    async def test_lifespan_reraises_in_staging(self):
        """In staging APP_ENV, init_mongo failure causes exception to propagate."""
        from app.main import lifespan

        mock_app = MagicMock()

        with patch("app.main.settings") as mock_settings:
            mock_settings.APP_ENV = "staging"
            mock_settings.ADMIN_EMAIL = None
            mock_settings.ADMIN_PASSWORD = None
            mock_settings.JWT_SECRET = "test-secret-at-least-32-characters-long"
            mock_settings.SENTRY_DSN = None
            mock_settings.POSTHOG_API_KEY = None

            with patch(
                "app.main.init_mongo",
                new_callable=AsyncMock,
                side_effect=ConnectionFailure("connection refused"),
            ):
                with pytest.raises(ConnectionFailure):
                    async with lifespan(mock_app):
                        pass

    @pytest.mark.anyio
    async def test_lifespan_swallows_in_dev(self):
        """In non-production APP_ENV (development), init_mongo failure is swallowed."""
        from app.main import lifespan

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        with patch("app.main.settings") as mock_settings:
            mock_settings.APP_ENV = "development"
            mock_settings.ADMIN_EMAIL = None
            mock_settings.ADMIN_PASSWORD = None
            mock_settings.JWT_SECRET = "test-secret-at-least-32-characters-long"
            mock_settings.SENTRY_DSN = None
            mock_settings.POSTHOG_API_KEY = None

            with patch(
                "app.main.init_mongo",
                new_callable=AsyncMock,
                side_effect=ConnectionFailure("connection refused"),
            ), patch(
                "app.main.init_redis", new_callable=AsyncMock
            ), patch(
                "app.main.close_mongo", new_callable=AsyncMock
            ), patch(
                "app.main.close_redis", new_callable=AsyncMock
            ), patch(
                "app.services.search.vertex_search.search_service.warm_up",
                new_callable=AsyncMock,
            ), patch(
                "app.services.ai.vertex_client.vertex_client._get_access_token",
                new_callable=AsyncMock,
            ), patch(
                "app.services.ai.vertex_client.vertex_client.close",
                new_callable=AsyncMock,
            ), patch(
                "app.services.ai.sarvam_client.sarvam_client.close",
                new_callable=AsyncMock,
            ), patch(
                "app.services.payment.razorpay_client.razorpay_client.close",
                new_callable=AsyncMock,
            ), patch(
                "app.services.comms.resend_client.close_resend_client",
                new_callable=AsyncMock,
            ):
                # Should not raise - the error is swallowed in dev mode
                async with lifespan(mock_app):
                    pass

    @pytest.mark.anyio
    async def test_lifespan_swallows_in_test(self):
        """In test APP_ENV, init_mongo failure is swallowed."""
        from app.main import lifespan

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        with patch("app.main.settings") as mock_settings:
            mock_settings.APP_ENV = "test"
            mock_settings.ADMIN_EMAIL = None
            mock_settings.ADMIN_PASSWORD = None
            mock_settings.JWT_SECRET = "test-secret-at-least-32-characters-long"
            mock_settings.SENTRY_DSN = None
            mock_settings.POSTHOG_API_KEY = None

            with patch(
                "app.main.init_mongo",
                new_callable=AsyncMock,
                side_effect=ConnectionFailure("connection refused"),
            ), patch(
                "app.main.init_redis", new_callable=AsyncMock
            ), patch(
                "app.main.close_mongo", new_callable=AsyncMock
            ), patch(
                "app.main.close_redis", new_callable=AsyncMock
            ), patch(
                "app.services.search.vertex_search.search_service.warm_up",
                new_callable=AsyncMock,
            ), patch(
                "app.services.ai.vertex_client.vertex_client._get_access_token",
                new_callable=AsyncMock,
            ), patch(
                "app.services.ai.vertex_client.vertex_client.close",
                new_callable=AsyncMock,
            ), patch(
                "app.services.ai.sarvam_client.sarvam_client.close",
                new_callable=AsyncMock,
            ), patch(
                "app.services.payment.razorpay_client.razorpay_client.close",
                new_callable=AsyncMock,
            ), patch(
                "app.services.comms.resend_client.close_resend_client",
                new_callable=AsyncMock,
            ):
                # Should not raise - the error is swallowed in test mode
                async with lifespan(mock_app):
                    pass


class TestHealthCheckBeanieAware:
    """Verify mongo_ping returns unhealthy when Beanie is not initialized."""

    @pytest.mark.anyio
    async def test_mongo_ping_unhealthy_when_client_not_initialized(self):
        """mongo_ping returns unhealthy when get_mongo_client raises RuntimeError."""
        from app.api.v1.health import mongo_ping

        with patch(
            "app.db.mongo.get_mongo_client",
            side_effect=RuntimeError("MongoDB not initialized. Call init_mongo() first."),
        ):
            result = await mongo_ping()
            assert result["status"] == "unhealthy"
            assert "MongoDB not initialized" in result["error"]

    @pytest.mark.anyio
    async def test_mongo_ping_unhealthy_when_beanie_not_initialized(self):
        """mongo_ping returns unhealthy when Board.get_pymongo_collection() raises CollectionWasNotInitialized."""
        from app.api.v1.health import mongo_ping
        from beanie.exceptions import CollectionWasNotInitialized

        mock_client = MagicMock()
        mock_client.admin.command = AsyncMock(return_value={"ok": 1})

        with patch("app.db.mongo.get_mongo_client", return_value=mock_client):
            with patch(
                "app.models.content.Board.get_pymongo_collection",
                side_effect=CollectionWasNotInitialized,
            ):
                result = await mongo_ping()
                assert result["status"] == "unhealthy"
                assert "CollectionWasNotInitialized" in result["error"] or "unhealthy" == result["status"]

    @pytest.mark.anyio
    async def test_mongo_ping_healthy_when_fully_initialized(self):
        """mongo_ping returns healthy when client and Beanie are both working."""
        from app.api.v1.health import mongo_ping

        mock_client = MagicMock()
        mock_client.admin.command = AsyncMock(return_value={"ok": 1})
        mock_collection = MagicMock()

        with patch("app.db.mongo.get_mongo_client", return_value=mock_client):
            with patch(
                "app.models.content.Board.get_pymongo_collection",
                return_value=mock_collection,
            ):
                result = await mongo_ping()
                assert result["status"] == "healthy"
