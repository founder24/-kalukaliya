"""
Tests for startup resilience: Settings() should never crash on invalid config.
Instead, errors are collected in startup_errors for health endpoint reporting.
"""

import os
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Remove any env vars that could interfere with test isolation."""
    env_vars_to_clear = [
        "APP_ENV",
        "JWT_SECRET",
        "ADMIN_JWT_SECRET",
        "EDGE_SHARED_SECRET",
        "TRUST_EDGE_AUTH",
        "JWT_ALGORITHM",
        "JWT_PRIVATE_KEY",
        "JWT_PUBLIC_KEY",
        "RESET_TOKEN_SECRET",
    ]
    for var in env_vars_to_clear:
        monkeypatch.delenv(var, raising=False)


class TestConfigResilienceProduction:
    """Settings() in production mode should not raise even with invalid secrets."""

    def test_short_jwt_secret_no_crash(self, monkeypatch):
        """Settings with a too-short JWT_SECRET does not raise ValueError."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("JWT_SECRET", "short")
        monkeypatch.setenv("TRUST_EDGE_AUTH", "False")

        from app.config import Settings

        s = Settings()
        assert isinstance(s, Settings)
        assert len(s.startup_errors) > 0

    def test_missing_admin_jwt_secret_no_crash(self, monkeypatch):
        """Settings with missing ADMIN_JWT_SECRET does not raise ValueError."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("JWT_SECRET", "a-valid-production-secret-that-is-long-enough-32")
        monkeypatch.setenv("TRUST_EDGE_AUTH", "False")
        monkeypatch.delenv("ADMIN_JWT_SECRET", raising=False)

        from app.config import Settings

        s = Settings()
        assert isinstance(s, Settings)
        assert any("ADMIN_JWT_SECRET" in e for e in s.startup_errors)

    def test_missing_edge_shared_secret_no_crash(self, monkeypatch):
        """Settings with TRUST_EDGE_AUTH=True and no EDGE_SHARED_SECRET does not raise."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("JWT_SECRET", "a-valid-production-secret-that-is-long-enough-32")
        monkeypatch.setenv("ADMIN_JWT_SECRET", "admin-secret-value-here")
        monkeypatch.setenv("TRUST_EDGE_AUTH", "True")
        monkeypatch.delenv("EDGE_SHARED_SECRET", raising=False)

        from app.config import Settings

        s = Settings()
        assert isinstance(s, Settings)
        assert any("EDGE_SHARED_SECRET" in e for e in s.startup_errors)

    def test_placeholder_jwt_secret_no_crash(self, monkeypatch):
        """Settings with a known placeholder JWT_SECRET does not raise."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("JWT_SECRET", "dev-only-secret-not-for-production-use-32chars")
        monkeypatch.setenv("TRUST_EDGE_AUTH", "False")

        from app.config import Settings

        s = Settings()
        assert isinstance(s, Settings)
        assert any("placeholder" in e for e in s.startup_errors)

    def test_admin_jwt_equals_private_key_no_crash(self, monkeypatch):
        """Settings where ADMIN_JWT_SECRET == JWT_PRIVATE_KEY does not raise."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("JWT_SECRET", "a-valid-production-secret-that-is-long-enough-32")
        monkeypatch.setenv("ADMIN_JWT_SECRET", "same-key-value")
        monkeypatch.setenv("JWT_PRIVATE_KEY", "same-key-value")
        monkeypatch.setenv("TRUST_EDGE_AUTH", "False")

        from app.config import Settings

        s = Settings()
        assert isinstance(s, Settings)
        assert any("ADMIN_JWT_SECRET must not be the same" in e for e in s.startup_errors)

    def test_rs256_missing_keys_no_crash(self, monkeypatch):
        """Settings with RS256 algorithm but missing keys does not raise."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("JWT_SECRET", "a-valid-production-secret-that-is-long-enough-32")
        monkeypatch.setenv("ADMIN_JWT_SECRET", "admin-secret-value-here")
        monkeypatch.setenv("JWT_ALGORITHM", "RS256")
        monkeypatch.setenv("TRUST_EDGE_AUTH", "False")
        monkeypatch.delenv("JWT_PRIVATE_KEY", raising=False)
        monkeypatch.delenv("JWT_PUBLIC_KEY", raising=False)

        from app.config import Settings

        s = Settings()
        assert isinstance(s, Settings)
        assert any("JWT_PRIVATE_KEY" in e for e in s.startup_errors)
        assert any("JWT_PUBLIC_KEY" in e for e in s.startup_errors)

    def test_multiple_errors_collected(self, monkeypatch):
        """Multiple validation failures are all collected in startup_errors."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("JWT_SECRET", "short")
        monkeypatch.setenv("TRUST_EDGE_AUTH", "True")
        monkeypatch.delenv("EDGE_SHARED_SECRET", raising=False)
        monkeypatch.delenv("ADMIN_JWT_SECRET", raising=False)

        from app.config import Settings

        s = Settings()
        # Should have at least 3 errors: EDGE_SHARED_SECRET, JWT_SECRET length, ADMIN_JWT_SECRET
        assert len(s.startup_errors) >= 3


class TestConfigResilienceDevelopment:
    """Settings() in development mode should have no startup_errors."""

    def test_dev_mode_no_errors(self, monkeypatch):
        """Development mode with missing secrets has no startup_errors."""
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("JWT_SECRET", "short")
        monkeypatch.delenv("ADMIN_JWT_SECRET", raising=False)
        monkeypatch.delenv("EDGE_SHARED_SECRET", raising=False)

        from app.config import Settings

        s = Settings()
        assert s.startup_errors == []

    def test_dev_mode_trust_edge_auth_warning_only(self, monkeypatch):
        """Development mode with TRUST_EDGE_AUTH=True but no secret logs warning only."""
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("TRUST_EDGE_AUTH", "True")
        monkeypatch.delenv("EDGE_SHARED_SECRET", raising=False)

        from app.config import Settings

        s = Settings()
        assert s.startup_errors == []


class TestHealthEndpointDegraded:
    """Health endpoint reports degraded status when startup_errors exist."""

    @pytest.mark.anyio
    async def test_health_reports_degraded_with_startup_errors(self, monkeypatch):
        """Health endpoint returns degraded when config has startup errors."""
        from app.config import settings
        from app.main import app

        monkeypatch.setattr(settings, "startup_errors", ["test error 1", "test error 2"])

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
            data = response.json()
            assert data["status"] == "degraded"
            assert data["config_error_count"] == 2
            assert "config_errors" not in data  # Raw messages should NOT be exposed

    @pytest.mark.anyio
    async def test_health_reports_healthy_without_startup_errors(self, monkeypatch):
        """Health endpoint returns healthy when no startup errors."""
        from app.config import settings
        from app.main import app

        monkeypatch.setattr(settings, "startup_errors", [])

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
            data = response.json()
            assert data["status"] == "healthy"
            assert "config_error_count" not in data
            assert "config_errors" not in data
