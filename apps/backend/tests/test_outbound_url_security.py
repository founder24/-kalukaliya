"""Regression tests for request-derived outbound URL restrictions."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.api.v1 import admin_indexnow_admin, admin_pyq
from app.config import settings
from app.core.security import is_safe_url
from app.services.payment.razorpay_client import RazorpayClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/path",
        "https://syrabit.ai.evil.example/path",
        "https://syrabit.ai@127.0.0.1/path",
        "https://127.0.0.1/path",
        "https://2130706433/path",
        "https://0x7f000001/path",
        "https://[::1]/path",
        "https://169.254.169.254/latest/meta-data/",
    ],
)
async def test_indexnow_rejects_malicious_and_encoded_hosts(url: str):
    with pytest.raises(Exception) as exc_info:
        await admin_indexnow_admin._require_trusted_site_url(url)
    assert getattr(exc_info.value, "status_code", None) == 400


@pytest.mark.asyncio
async def test_safe_url_rejects_allowlisted_host_resolving_private(monkeypatch):
    loop = __import__("asyncio").get_running_loop()
    getaddrinfo = AsyncMock(
        return_value=[(2, 1, 6, "", ("10.0.0.8", 443))]
    )
    monkeypatch.setattr(loop, "getaddrinfo", getaddrinfo)

    assert (
        await is_safe_url(
            "https://assets.syrabit.ai/pyq-uploads/a/file.pdf",
            allowed_schemes=["https"],
            allowed_hosts={"assets.syrabit.ai"},
        )
        is False
    )


@pytest.mark.asyncio
async def test_pyq_redirect_cannot_escape_storage_allowlist(monkeypatch):
    original_r2_public = settings.CF_R2_PUBLIC_URL
    settings.CF_R2_PUBLIC_URL = "https://assets.syrabit.ai"
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.get.return_value = httpx.Response(
        302,
        headers={"location": "http://169.254.169.254/latest/meta-data/"},
        request=httpx.Request("GET", "https://assets.syrabit.ai/source.pdf"),
    )

    try:
        with (
            patch("app.api.v1.admin_pyq.httpx.AsyncClient", return_value=client),
            patch(
                "app.api.v1.admin_pyq.is_safe_url",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.api.v1.admin_pyq.resolve_public_ip_addresses",
                new=AsyncMock(return_value=("203.0.113.10",)),
            ),
        ):
            result = await admin_pyq._extract_text_from_pyq(
                {
                    "_id": "pyq-1",
                    "file_url": "https://assets.syrabit.ai/pyq-uploads/a/file.pdf",
                    "is_pdf": True,
                }
            )
    finally:
        settings.CF_R2_PUBLIC_URL = original_r2_public

    assert result == ""
    client.get.assert_awaited_once()


def test_pyq_gcs_target_requires_owned_bucket_and_canonical_path():
    original_bucket = settings.GCS_CONTENT_BUCKET
    settings.GCS_CONTENT_BUCKET = "syrabit-content"
    try:
        assert admin_pyq._pyq_storage_target(
            "https://storage.googleapis.com/syrabit-content/pyq-uploads/a/file.pdf"
        )[1]
        assert not admin_pyq._pyq_storage_target(
            "https://storage.googleapis.com/foreign/pyq-uploads/a/file.pdf"
        )[1]
        assert not admin_pyq._pyq_storage_target(
            "https://storage.googleapis.com/syrabit-content/pyq-uploads/../private.pdf"
        )[1]
        assert not admin_pyq._pyq_storage_target(
            "https://storage.googleapis.com/syrabit-content/pyq-uploads/%252e%252e/private.pdf"
        )[1]
        assert not admin_pyq._pyq_storage_target(
            "https://storage.googleapis.com/syrabit-content/pyq-uploads/%2525252525252e%2525252525252e/private.pdf"
        )[1]
    finally:
        settings.GCS_CONTENT_BUCKET = original_bucket


@pytest.mark.asyncio
async def test_pinned_backend_connects_only_to_validated_address():
    backend = admin_pyq._PinnedNetworkBackend(
        "assets.syrabit.ai",
        ("203.0.113.10",),
    )
    backend._backend.connect_tcp = AsyncMock(return_value=object())

    await backend.connect_tcp("assets.syrabit.ai", 443)

    backend._backend.connect_tcp.assert_awaited_once()
    assert backend._backend.connect_tcp.await_args.args[:2] == ("203.0.113.10", 443)


@pytest.mark.asyncio
async def test_razorpay_subscription_id_is_encoded_and_redirects_are_not_followed():
    original_key = settings.RAZORPAY_KEY_ID
    original_secret = settings.RAZORPAY_KEY_SECRET
    settings.RAZORPAY_KEY_ID = "rzp_test_key"
    settings.RAZORPAY_KEY_SECRET = "test_secret"
    client = None
    try:
        client = RazorpayClient()
        await client._client.aclose()
        client._client = AsyncMock()
        response = MagicMock()
        response.status_code = 200
        client._client.delete.return_value = response

        assert await client.cancel_subscription("../../http://127.0.0.1") is True
        requested_url = client._client.delete.await_args.args[0]
        assert requested_url.startswith("https://api.razorpay.com/v1/subscriptions/")
        assert "/" not in requested_url.removeprefix(
            "https://api.razorpay.com/v1/subscriptions/"
        )

        response.status_code = 302
        with pytest.raises(RuntimeError, match="unexpected redirect"):
            await client.cancel_subscription("sub_123")
    finally:
        if client is not None:
            await client.close()
        settings.RAZORPAY_KEY_ID = original_key
        settings.RAZORPAY_KEY_SECRET = original_secret