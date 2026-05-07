"""Task #557 — VAPID public-key endpoint sources from env-var first.

Asserts the contract documented in `_get_or_create_vapid_keys`:

  * When `config.WEB_PUSH_VAPID_PRIVATE_KEY` is set, the public key is
    *derived* from the PEM (no Mongo round-trip) and the endpoint
    returns it as urlsafe-base64.
  * When the env var is absent, the legacy Mongo-backed
    `db.api_config.push_vapid` document is honoured (one-shot bootstrap
    fallback).
  * When neither source yields a key, the endpoint raises 503
    (V4 §12 — fail loud).
  * An unparseable env var refuses to fall back through to Mongo
    (loud failure path).
"""

import asyncio
import base64

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, PublicFormat, NoEncryption,
)

from tests._deps_stub import install_deps_stub

install_deps_stub(force=True)

import deps  # noqa: E402
import config  # noqa: E402
from routes import admin_notifications as notif  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _fresh_keypair_pem() -> tuple[str, str]:
    """Return (private_pem, expected_public_b64) for a fresh EC P-256 key.

    The PEM is `.strip()`-ed to match the env-var read in
    `_get_or_create_vapid_keys` (which calls `.strip()` on the env value).
    """
    priv = ec.generate_private_key(ec.SECP256R1())
    private_pem = priv.private_bytes(
        Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()
    ).decode().strip()
    pub_raw = priv.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )
    expected_b64 = base64.urlsafe_b64encode(pub_raw).rstrip(b"=").decode()
    return private_pem, expected_b64


class TestVapidEnvVarSource:
    def test_env_var_present_derives_public_key_from_pem(self):
        private_pem, expected_b64 = _fresh_keypair_pem()
        with patch.object(config, "WEB_PUSH_VAPID_PRIVATE_KEY", private_pem):
            keys = _run(notif._get_or_create_vapid_keys())
        assert keys["public_key"] == expected_b64
        assert keys["private_key_pem"] == private_pem

    def test_env_var_present_skips_mongo_lookup(self):
        private_pem, _ = _fresh_keypair_pem()
        deps.db.api_config.find_one = AsyncMock(return_value={"push_vapid": {
            "public_key": "STALE", "private_key_pem": "STALE",
        }})
        with patch.object(config, "WEB_PUSH_VAPID_PRIVATE_KEY", private_pem):
            keys = _run(notif._get_or_create_vapid_keys())
        # The stale Mongo doc must NOT bleed through.
        assert keys["private_key_pem"] == private_pem
        assert keys["public_key"] != "STALE"
        deps.db.api_config.find_one.assert_not_called()

    def test_unparseable_env_var_returns_empty_no_mongo_fallback(self):
        deps.db.api_config.find_one = AsyncMock(return_value={"push_vapid": {
            "public_key": "BACKUP", "private_key_pem": "BACKUP-PEM",
        }})
        with patch.object(config, "WEB_PUSH_VAPID_PRIVATE_KEY", "not-a-pem"):
            keys = _run(notif._get_or_create_vapid_keys())
        # V4 §12 — fail loud, do NOT silently fall back to Mongo.
        assert keys == {}
        deps.db.api_config.find_one.assert_not_called()

    def test_endpoint_returns_env_derived_public_key(self):
        private_pem, expected_b64 = _fresh_keypair_pem()
        with patch.object(config, "WEB_PUSH_VAPID_PRIVATE_KEY", private_pem):
            result = _run(notif.push_vapid_public_key())
        assert result == {"public_key": expected_b64}


class TestVapidMongoFallback:
    def test_env_absent_uses_mongo_doc(self):
        deps.db.api_config.find_one = AsyncMock(return_value={"push_vapid": {
            "public_key": "BNjLs9mI-mongo-pub",
            "private_key_pem": "-----BEGIN EC PRIVATE KEY-----\nMONGO\n-----END EC PRIVATE KEY-----\n",
        }})
        with patch.object(config, "WEB_PUSH_VAPID_PRIVATE_KEY", ""):
            keys = _run(notif._get_or_create_vapid_keys())
        assert keys["public_key"] == "BNjLs9mI-mongo-pub"

    def test_env_absent_and_no_mongo_doc_raises_503(self):
        deps.db.api_config.find_one = AsyncMock(return_value=None)
        deps.db.api_config.update_one = AsyncMock(return_value=None)
        # Force the bootstrap branch to fail so the endpoint returns 503.
        with patch.object(config, "WEB_PUSH_VAPID_PRIVATE_KEY", ""), \
             patch.object(notif, "_get_or_create_vapid_keys",
                          new=AsyncMock(return_value={})):
            with pytest.raises(HTTPException) as exc:
                _run(notif.push_vapid_public_key())
        assert exc.value.status_code == 503
