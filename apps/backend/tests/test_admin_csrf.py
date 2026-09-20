import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.admin import _csrf_check
from app.config import settings


def _request(*, cookie: str | None = None, origin: str | None = None) -> Request:
    headers = []
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    if origin:
        headers.append((b"origin", origin.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/admin/content/chapters",
            "headers": headers,
            "query_string": b"",
            "scheme": "https",
            "server": ("syrabit.ai", 443),
            "client": ("127.0.0.1", 12345),
        }
    )


@pytest.mark.anyio
async def test_cookie_authenticated_mutation_requires_origin_or_referer(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")

    with pytest.raises(HTTPException) as exc_info:
        await _csrf_check(_request(cookie="syrabit_admin_session=token"))

    assert exc_info.value.status_code == 403
    assert "origin or referer required" in str(exc_info.value.detail)


@pytest.mark.anyio
async def test_bearer_style_request_without_cookie_remains_compatible(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")

    await _csrf_check(_request())