"""Task #557 — `_dispatch_push` calls pywebpush correctly and maps
upstream errors onto the dead-endpoint pruner.

Asserts:
  * The webpush call carries the env-derived private PEM, the stored
    `subscription_info`, and a `vapid_claims["sub"]` sourced from
    `config.WEB_PUSH_CONTACT`.
  * 404 / 410 from the push gateway prunes the row from
    `db.push_subscriptions` (W3C "subscription gone").
  * 5xx / network errors do NOT prune (transient — retried next
    dispatch).
  * Missing private PEM short-circuits with a `_log_skip` entry.
"""

import asyncio
import json

from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, NoEncryption,
)

from tests._deps_stub import install_deps_stub

install_deps_stub(force=True)

import deps  # noqa: E402
import config  # noqa: E402
from routes import admin_notifications as notif  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _fresh_pem() -> str:
    priv = ec.generate_private_key(ec.SECP256R1())
    # Strip trailing newline — the env-var read in
    # `_get_or_create_vapid_keys` calls `.strip()`, so the PEM that
    # actually reaches `pywebpush.webpush` matches the stripped form.
    return priv.private_bytes(
        Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()
    ).decode().strip()


def _make_subscription(endpoint: str = "https://push.example.com/abc") -> dict:
    return {
        "endpoint": endpoint,
        "subscription_info": {
            "endpoint": endpoint,
            "keys": {
                "p256dh": "BNjLs9mITqnCmqbpNxmUaEMb3zF8QKbZ",
                "auth": "secret-auth-key-base64url",
            },
        },
        "user_id": "user-1",
        "role": "student",
        "active": True,
    }


class _SubsCursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, _n):
        return self._rows


def _wire_db_with(subs):
    deps.db.push_subscriptions.find = MagicMock(return_value=_SubsCursor(subs))
    deps.db.push_subscriptions.delete_one = AsyncMock(return_value=None)
    deps.db.push_delivery_log.insert_one = AsyncMock(return_value=None)
    deps.db.push_delivery_log.insert_many = AsyncMock(return_value=None)


class TestDispatchPushSuccess:
    def test_success_calls_webpush_with_env_pem_and_contact_sub(self):
        sub = _make_subscription()
        _wire_db_with([sub])
        pem = _fresh_pem()

        captured = {}

        def fake_webpush(**kwargs):
            captured.update(kwargs)
            return MagicMock(status_code=201)

        with patch.object(config, "WEB_PUSH_VAPID_PRIVATE_KEY", pem), \
             patch.object(config, "WEB_PUSH_CONTACT", "mailto:ops@syrabit.ai"), \
             patch("pywebpush.webpush", side_effect=fake_webpush):
            _run(notif._dispatch_push({"title": "hi", "body": "test"},
                                      admin_only=False))

        assert captured["vapid_private_key"] == pem
        assert captured["vapid_claims"] == {"sub": "mailto:ops@syrabit.ai"}
        assert captured["subscription_info"] == sub["subscription_info"]
        # Payload must be JSON-encoded.
        assert json.loads(captured["data"])["title"] == "hi"

    def test_skip_logged_when_private_key_missing(self):
        sub = _make_subscription()
        _wire_db_with([sub])
        # Force `_get_or_create_vapid_keys` to return empty.
        with patch.object(notif, "_get_or_create_vapid_keys",
                          new=AsyncMock(return_value={})), \
             patch("pywebpush.webpush") as wp:
            _run(notif._dispatch_push({"title": "hi"}, admin_only=False))
        wp.assert_not_called()
        deps.db.push_delivery_log.insert_one.assert_called()


class TestDispatchPushPruning:
    def _run_with_status(self, status_code: int):
        sub = _make_subscription("https://push.example.com/dead")
        _wire_db_with([sub])
        pem = _fresh_pem()

        from pywebpush import WebPushException

        fake_resp = MagicMock(status_code=status_code, text="gone")
        exc = WebPushException("subscription expired", response=fake_resp)

        with patch.object(config, "WEB_PUSH_VAPID_PRIVATE_KEY", pem), \
             patch("pywebpush.webpush", side_effect=exc):
            _run(notif._dispatch_push({"title": "hi"}, admin_only=False))

    def test_410_prunes_subscription(self):
        self._run_with_status(410)
        deps.db.push_subscriptions.delete_one.assert_called_once_with(
            {"endpoint": "https://push.example.com/dead"}
        )

    def test_404_prunes_subscription(self):
        self._run_with_status(404)
        deps.db.push_subscriptions.delete_one.assert_called_once_with(
            {"endpoint": "https://push.example.com/dead"}
        )

    def test_500_does_not_prune(self):
        self._run_with_status(500)
        deps.db.push_subscriptions.delete_one.assert_not_called()

    def test_429_does_not_prune(self):
        self._run_with_status(429)
        deps.db.push_subscriptions.delete_one.assert_not_called()


class TestVapidClaimsSubResolver:
    def test_uses_config_value_when_set(self):
        with patch.object(config, "WEB_PUSH_CONTACT", "mailto:alerts@syrabit.ai"):
            assert notif._vapid_claims_sub() == "mailto:alerts@syrabit.ai"

    def test_falls_back_to_default_when_blank(self):
        with patch.object(config, "WEB_PUSH_CONTACT", ""):
            assert notif._vapid_claims_sub() == "mailto:admin@syrabit.ai"
