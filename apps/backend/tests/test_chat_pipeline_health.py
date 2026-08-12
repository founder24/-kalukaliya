"""
Tests for GET /health/chat-pipeline.

Covers:
  - Auth enforcement (missing / wrong token → 401; X-User-JWT header accepted)
  - Sarvam healthy + Gemini returns Assamese → 200, has_assamese_script=True
  - Gemini returns English (no Assamese script) → 503, step=assamese_probe
  - Assamese probe raises → 503, step=assamese_probe
  - Gemini not configured → assamese_probe skipped, still 200
  - Sarvam billing-exhausted → Gemini serves Step 1, probe still validates Assamese
  - Sarvam or Gemini TimeoutError in Step 1 → 503, step=ai_pipeline within budget
  - Assamese probe TimeoutError → 503, step=assamese_probe
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from httpx import AsyncClient, ASGITransport


CRON_SECRET = "probe-test-secret-abc"
AUTH = {"Authorization": f"Bearer {CRON_SECRET}"}
ENDPOINT = "/api/v1/health/chat-pipeline"

# Assamese text that passes the Unicode gate (U+0980–U+09FF)
ASSAMESE_TEXT = "মই ছ্যৰাবিট, আপোনাৰ শিক্ষামূলক সহায়ক।"
ENGLISH_TEXT = "I am Syrabit, your educational assistant."


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
async def ac():
    """Minimal async test client; patches startup-time external I/O."""
    from app.main import app

    # Mock topic_matcher so RAG step never touches MongoDB.
    mock_tm = MagicMock()
    mock_tm._is_cache_valid.return_value = True
    mock_tm._embeddings = [1, 2, 3]

    with (
        patch("app.config.settings.TRANSLATE_CRON_SECRET", CRON_SECRET),
        patch("app.services.ai.topic_matcher.topic_matcher", mock_tm),
        # Prevent lifespan from attempting live Secret Manager / MongoDB calls.
        patch("app.db.mongo.init_mongo", new_callable=AsyncMock),
        patch("app.config.settings.startup_errors", []),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            yield client


# ── Helpers ───────────────────────────────────────────────────────────────────

def sarvam_ok():
    return AsyncMock(return_value="PONG")


def sarvam_billing_exhausted():
    from app.core.circuit_breaker import SarvamBillingExhaustedError
    return AsyncMock(side_effect=SarvamBillingExhaustedError("402"))


def sarvam_timeout():
    """Sarvam hits the 6 s asyncio.wait_for deadline."""
    return AsyncMock(side_effect=asyncio.TimeoutError())


def gemini_assamese():
    return AsyncMock(return_value=ASSAMESE_TEXT)


def gemini_english():
    return AsyncMock(return_value=ENGLISH_TEXT)


def gemini_raises():
    return AsyncMock(side_effect=RuntimeError("Gemini unreachable"))


def gemini_timeout():
    """Gemini hits an asyncio.wait_for deadline."""
    return AsyncMock(side_effect=asyncio.TimeoutError())


# ── Auth tests ────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_missing_token_returns_401(ac):
    resp = await ac.get(ENDPOINT)
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_wrong_token_returns_401(ac):
    resp = await ac.get(ENDPOINT, headers={"Authorization": "Bearer bad-token"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_x_user_jwt_header_accepted(ac):
    """CF Worker sends OIDC in Authorization, cron secret in X-User-JWT."""
    with (
        patch("app.services.ai.sarvam_client.generate_with_sarvam", sarvam_ok()),
        patch("app.services.ai.gemini_fallback._available", return_value=True),
        patch("app.services.ai.gemini_fallback.generate_gemini", gemini_assamese()),
    ):
        resp = await ac.get(
            ENDPOINT,
            headers={
                "Authorization": "Bearer <oidc-token>",
                "X-User-JWT": f"Bearer {CRON_SECRET}",
            },
        )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_x_cron_token_header_accepted(ac):
    """Direct Cloud Run call sends OIDC in Authorization, secret in X-Cron-Token."""
    with (
        patch("app.services.ai.sarvam_client.generate_with_sarvam", sarvam_ok()),
        patch("app.services.ai.gemini_fallback._available", return_value=True),
        patch("app.services.ai.gemini_fallback.generate_gemini", gemini_assamese()),
    ):
        resp = await ac.get(
            ENDPOINT,
            headers={
                "Authorization": "Bearer <oidc-token>",
                "X-Cron-Token": f"Bearer {CRON_SECRET}",
            },
        )
    assert resp.status_code == 200


# ── Happy-path tests ──────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_sarvam_healthy_gemini_assamese_returns_200(ac):
    """Full happy path: Sarvam serves English ping, Gemini serves Assamese probe."""
    with (
        patch("app.services.ai.sarvam_client.generate_with_sarvam", sarvam_ok()),
        patch("app.services.ai.gemini_fallback._available", return_value=True),
        patch("app.services.ai.gemini_fallback.generate_gemini", gemini_assamese()),
    ):
        resp = await ac.get(ENDPOINT, headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["provider"] == "sarvam"
    assert body["assamese_probe"]["has_assamese_script"] is True
    assert body["rag_status"] == "healthy"


@pytest.mark.anyio
async def test_gemini_not_configured_skips_assamese_probe(ac):
    """When GEMINI_API_KEY is absent, the Assamese probe is skipped — not unhealthy."""
    with (
        patch("app.services.ai.sarvam_client.generate_with_sarvam", sarvam_ok()),
        patch("app.services.ai.gemini_fallback._available", return_value=False),
    ):
        resp = await ac.get(ENDPOINT, headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["assamese_probe"]["status"] == "skipped"
    assert body["status"] == "healthy"


# ── Assamese quality gate tests ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_gemini_english_response_gates_503(ac):
    """Gemini responds in English → Assamese students get wrong language → 503."""
    with (
        patch("app.services.ai.sarvam_client.generate_with_sarvam", sarvam_ok()),
        patch("app.services.ai.gemini_fallback._available", return_value=True),
        patch("app.services.ai.gemini_fallback.generate_gemini", gemini_english()),
    ):
        resp = await ac.get(ENDPOINT, headers=AUTH)

    assert resp.status_code == 503
    body = resp.json()
    assert body["step"] == "assamese_probe"
    assert body["assamese_probe"]["has_assamese_script"] is False


@pytest.mark.anyio
async def test_assamese_probe_exception_returns_503(ac):
    """Gemini is configured but throws during the Assamese probe → 503."""
    with (
        patch("app.services.ai.sarvam_client.generate_with_sarvam", sarvam_ok()),
        patch("app.services.ai.gemini_fallback._available", return_value=True),
        patch("app.services.ai.gemini_fallback.generate_gemini", gemini_raises()),
    ):
        resp = await ac.get(ENDPOINT, headers=AUTH)

    assert resp.status_code == 503
    body = resp.json()
    assert body["step"] == "assamese_probe"


@pytest.mark.anyio
async def test_assamese_probe_timeout_returns_503(ac):
    """asyncio.wait_for deadline on the Assamese probe → 503, step=assamese_probe.

    The Sarvam call succeeds (English ping), so Step 1 passes.  Only the
    Assamese probe (Step 2) hits its 12-second budget and fires TimeoutError.
    The endpoint must surface this as a structured 503, not a generic 500.
    """
    call_count = {"n": 0}

    async def _dispatch(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Step 1 English ping — return fast
            return "PONG"
        # Step 2 Assamese probe — simulate timeout budget exhausted
        raise asyncio.TimeoutError()

    with (
        patch("app.services.ai.sarvam_client.generate_with_sarvam", sarvam_ok()),
        patch("app.services.ai.gemini_fallback._available", return_value=True),
        patch("app.services.ai.gemini_fallback.generate_gemini", side_effect=_dispatch),
    ):
        resp = await ac.get(ENDPOINT, headers=AUTH)

    assert resp.status_code == 503
    body = resp.json()
    assert body["step"] == "assamese_probe"


# ── Sarvam fallback to Gemini tests ──────────────────────────────────────────

@pytest.mark.anyio
async def test_sarvam_billing_exhausted_falls_to_gemini(ac):
    """When Sarvam is billing-exhausted, Gemini serves Step 1; probe validates Assamese."""
    call_count = {"n": 0}

    async def _dispatch(*args, **kwargs):
        call_count["n"] += 1
        return "PONG" if call_count["n"] == 1 else ASSAMESE_TEXT

    with (
        patch("app.services.ai.sarvam_client.generate_with_sarvam", sarvam_billing_exhausted()),
        patch("app.services.ai.gemini_fallback._available", return_value=True),
        patch("app.services.ai.gemini_fallback.generate_gemini", side_effect=_dispatch),
    ):
        resp = await ac.get(ENDPOINT, headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "gemini-2.5-flash"
    assert body["assamese_probe"]["has_assamese_script"] is True


@pytest.mark.anyio
async def test_sarvam_timeout_falls_to_gemini(ac):
    """Sarvam hits the 6 s timeout → Gemini fallback serves Step 1 → 200."""
    call_count = {"n": 0}

    async def _dispatch(*args, **kwargs):
        call_count["n"] += 1
        return "PONG" if call_count["n"] == 1 else ASSAMESE_TEXT

    with (
        patch("app.services.ai.sarvam_client.generate_with_sarvam", sarvam_timeout()),
        patch("app.services.ai.gemini_fallback._available", return_value=True),
        patch("app.services.ai.gemini_fallback.generate_gemini", side_effect=_dispatch),
    ):
        resp = await ac.get(ENDPOINT, headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "gemini-2.5-flash"


@pytest.mark.anyio
async def test_both_providers_timeout_returns_503(ac):
    """Sarvam timeout then Gemini timeout in Step 1 → 503, step=ai_pipeline.

    The asyncio.TimeoutError from the Gemini wait_for must propagate up
    to the Step 1 outer except, which returns a structured 503.  The endpoint
    must never silently swallow the error or block past the CI curl budget.
    """
    with (
        patch("app.services.ai.sarvam_client.generate_with_sarvam", sarvam_timeout()),
        patch("app.services.ai.gemini_fallback._available", return_value=True),
        patch("app.services.ai.gemini_fallback.generate_gemini", gemini_timeout()),
    ):
        resp = await ac.get(ENDPOINT, headers=AUTH)

    assert resp.status_code == 503
    body = resp.json()
    # TimeoutError in Gemini fallback propagates to the Step 1 outer except.
    assert body["step"] == "ai_pipeline"
    assert body["status"] == "unhealthy"
