"""Unit tests for vertex_services.ocr_image provider fallback chain.

Task #268 — isolated from the route-level mock injection in
test_ocr_image_route.py so sys.modules is not polluted across test files.

Covers:
  - When Workers AI (analyze_image) returns None → falls through to
    Google Vision and returns the extracted text from that provider.
  - When Google Vision is not configured → falls through to cloud rotation.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests._deps_stub import install_deps_stub

install_deps_stub(force=True)


@pytest.mark.asyncio
async def test_ocr_image_falls_back_to_google_vision_on_workers_ai_unavailable(monkeypatch):
    """When Workers AI returns None (unavailable), ocr_image routes to
    Google Vision and returns the extracted text from that provider."""
    import vertex_services as vs
    from providers import google_vision as gv

    # Workers AI path: circuit-breaker reports OK but analyze_image yields nothing.
    monkeypatch.setattr(vs, "_ok", lambda: True, raising=False)
    monkeypatch.setattr(vs, "analyze_image", AsyncMock(return_value=None), raising=False)

    # Google Vision path: configured and returning valid text.
    monkeypatch.setattr(gv, "is_configured", lambda: True)
    monkeypatch.setattr(gv, "should_use_google_vision", lambda **kw: True)
    monkeypatch.setattr(
        gv,
        "ocr_document",
        AsyncMock(
            return_value={
                "text": "Q3. What is mitosis?",
                "confidence": 0.96,
                "pages": 1,
                "provider": "google_vision",
            }
        ),
    )

    result = await vs.ocr_image(b"\xff\xd8\xff\xe0" + b"\x00" * 100, mime_type="image/jpeg")

    assert result.get("provider") == "google_vision"
    assert "mitosis" in (result.get("raw_text") or "")


@pytest.mark.asyncio
async def test_ocr_image_skips_google_vision_when_not_configured(monkeypatch):
    """When Google Vision is not configured, ocr_image does not call it and
    proceeds to the cloud OCR rotation step instead."""
    import vertex_services as vs
    from providers import google_vision as gv

    monkeypatch.setattr(vs, "_ok", lambda: True, raising=False)
    monkeypatch.setattr(vs, "analyze_image", AsyncMock(return_value=None), raising=False)

    monkeypatch.setattr(gv, "is_configured", lambda: False)
    gv_spy = AsyncMock()
    monkeypatch.setattr(gv, "ocr_document", gv_spy)

    cloud_text = "Cloud OCR extracted text"
    monkeypatch.setattr(
        vs,
        "_cloud_ocr_with_rotation",
        AsyncMock(return_value=cloud_text),
        raising=False,
    )

    result = await vs.ocr_image(b"\xff\xd8\xff\xe0" + b"\x00" * 100, mime_type="image/jpeg")

    gv_spy.assert_not_called()
    assert cloud_text in (result.get("raw_text") or "")
