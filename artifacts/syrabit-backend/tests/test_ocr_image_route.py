"""Tests for POST /ai/ocr-image.

Task #268 — covers:
  - Valid JPEG image → 200 with extracted text
  - Unsupported Content-Type → 415 (Unsupported Media Type, RFC 9110 §15.5.16)
  - File exceeding 8 MB → 413 (streaming read path)
  - Missing file field → 422 (FastAPI validation)
  - Rate-limit cap → 429
  - Provider failure: vertex_services.ocr_image raises → 503
  - Provider fallback: secondary-provider response is transparent to client → 200

The vertex_services stub is injected/restored via a per-test autouse fixture
so it does NOT pollute sys.modules for other test files in the session.
Unit-level fallback logic is tested in test_ocr_image_fallback_unit.py.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests._deps_stub import install_deps_stub

install_deps_stub(force=True)


# ── Minimal JPEG bytes that pass _sniff_image_mime (≥ 12 bytes required) ─────
_JPEG_HEADER = b"\xff\xd8\xff\xe0" + b"\x00" * 100


# ── Controllable vertex_services stub — rebuilt fresh for each test ───────────
# Created at module level so helpers can reference _mock_vs, but injected into
# sys.modules only for the duration of each test via the autouse fixture below.
_mock_vs = types.ModuleType("vertex_services")
_mock_vs.ocr_image = AsyncMock(
    return_value={
        "raw_text": "Q1. Define photosynthesis.",
        "content_type": "question_paper",
        "word_count": 4,
        "provider": "workers_ai",
    }
)


@pytest.fixture(autouse=True)
def _inject_vertex_stub():
    """Inject _mock_vs for each test in this file and restore afterwards.

    This prevents sys.modules pollution that would break test_vertex_services_breaker.py
    and test_ocr_image_fallback_unit.py when pytest runs all files together.
    """
    _original = sys.modules.get("vertex_services")
    sys.modules["vertex_services"] = _mock_vs
    yield
    if _original is None:
        sys.modules.pop("vertex_services", None)
    else:
        sys.modules["vertex_services"] = _original


def _build_ocr_app(dep_override=None):
    """Return a minimal (app, chat_mod) pair with just the OCR route mounted."""
    from routes import ai_chat as chat_mod
    from auth_deps import rate_limit_ocr_optional

    chat_mod.CF_TURNSTILE_ENABLED = False

    app = FastAPI()
    app.include_router(chat_mod.router, prefix="/api")

    async def _anon_no_limit():
        return None

    app.dependency_overrides[rate_limit_ocr_optional] = dep_override or _anon_no_limit
    return app, chat_mod


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────

def test_valid_image_returns_extracted_text():
    _mock_vs.ocr_image = AsyncMock(
        return_value={
            "raw_text": "Q1. What is osmosis?",
            "content_type": "question_paper",
            "word_count": 4,
            "provider": "workers_ai",
        }
    )
    app, _ = _build_ocr_app()
    with TestClient(app) as client:
        resp = client.post(
            "/api/ai/ocr-image",
            files={"file": ("question.jpg", _JPEG_HEADER, "image/jpeg")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "Q1. What is osmosis?"
    assert body["content_type"] == "question_paper"
    assert "word_count" in body


# ─────────────────────────────────────────────────────────────────────────────
# MIME validation
# ─────────────────────────────────────────────────────────────────────────────

def test_pdf_mime_returns_415():
    app, _ = _build_ocr_app()
    with TestClient(app) as client:
        resp = client.post(
            "/api/ai/ocr-image",
            files={"file": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
    assert resp.status_code == 415
    assert "Unsupported file type" in resp.json()["detail"]


def test_text_mime_returns_415():
    app, _ = _build_ocr_app()
    with TestClient(app) as client:
        resp = client.post(
            "/api/ai/ocr-image",
            files={"file": ("note.txt", b"hello world", "text/plain")},
        )
    assert resp.status_code == 415


# ─────────────────────────────────────────────────────────────────────────────
# Size validation — streaming path (no Content-Length early-reject)
# ─────────────────────────────────────────────────────────────────────────────

def test_oversized_file_returns_413():
    over_8mb = _JPEG_HEADER + b"\x00" * (8 * 1024 * 1024)
    app, _ = _build_ocr_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/api/ai/ocr-image",
            files={"file": ("big.jpg", over_8mb, "image/jpeg")},
        )
    assert resp.status_code == 413
    assert "too large" in resp.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# Missing file field → FastAPI 422 Unprocessable Entity
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_file_field_returns_422():
    app, _ = _build_ocr_app()
    with TestClient(app) as client:
        resp = client.post("/api/ai/ocr-image")
    assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# Rate-limit enforcement
# ─────────────────────────────────────────────────────────────────────────────

def test_rate_limit_returns_429():
    async def _rate_limited():
        raise HTTPException(
            status_code=429,
            detail="OCR rate limit exceeded — 10 uploads/minute. Try again in a moment.",
            headers={"Retry-After": "60", "X-RateLimit-Limit": "10"},
        )

    app, _ = _build_ocr_app(dep_override=_rate_limited)
    with TestClient(app) as client:
        resp = client.post(
            "/api/ai/ocr-image",
            files={"file": ("question.jpg", _JPEG_HEADER, "image/jpeg")},
        )
    assert resp.status_code == 429
    detail = resp.json()["detail"].lower()
    assert "rate limit" in detail or "exceeded" in detail


# ─────────────────────────────────────────────────────────────────────────────
# Provider failure → 503
# ─────────────────────────────────────────────────────────────────────────────

def test_vertex_ocr_raises_returns_503():
    _mock_vs.ocr_image = AsyncMock(side_effect=RuntimeError("all providers exhausted"))
    app, _ = _build_ocr_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/api/ai/ocr-image",
            files={"file": ("question.jpg", _JPEG_HEADER, "image/jpeg")},
        )
    assert resp.status_code == 503


# ─────────────────────────────────────────────────────────────────────────────
# Provider fallback: secondary-provider result is transparent to the client
# ─────────────────────────────────────────────────────────────────────────────

def test_fallback_provider_response_returns_200():
    """When Workers AI fails internally and Google Vision takes over, the
    route still returns 200 — the `provider` field is never exposed to callers."""
    _mock_vs.ocr_image = AsyncMock(
        return_value={
            "raw_text": "Q2. Explain the water cycle.",
            "content_type": "question_paper",
            "word_count": 6,
            "provider": "google_vision",
        }
    )
    app, _ = _build_ocr_app()
    with TestClient(app) as client:
        resp = client.post(
            "/api/ai/ocr-image",
            files={"file": ("question.jpg", _JPEG_HEADER, "image/jpeg")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "Q2. Explain the water cycle."
    assert "provider" not in body
