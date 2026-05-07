"""Task #557 — `/push/subscribe` accepts the W3C PushSubscription shape
(endpoint + keys.p256dh + keys.auth) and rejects malformed bodies.

Complements the legacy `tests/test_push_subscribe.py` by pinning the
*W3C field names* explicitly — a future refactor that drops `keys.auth`
or `keys.p256dh` from the stored subscription_info would break
pywebpush dispatch silently otherwise.
"""

import asyncio

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock

from tests._deps_stub import install_deps_stub

install_deps_stub(force=True)

import deps  # noqa: E402
from routes import admin_notifications as notif  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


W3C_SUBSCRIPTION = {
    "endpoint": "https://fcm.googleapis.com/wp/abc-vapid-not-fcm",
    "expirationTime": None,
    "keys": {
        "p256dh": "BNjLs9mITqnCmqbpNxmUaEMb3zF8QKbZ-w3c-shape",
        "auth": "auth-secret-base64url",
    },
}


def _student():
    return {"id": "student-9", "email": "s@example.com", "is_admin": False}


class TestW3CSubscriptionShape:
    def test_full_w3c_shape_persists_keys_and_endpoint(self):
        deps.db.push_subscriptions.update_one = AsyncMock(return_value=None)
        _run(notif.push_subscribe({"subscription": W3C_SUBSCRIPTION},
                                  user=_student()))
        set_doc = deps.db.push_subscriptions.update_one.call_args.args[1]["$set"]
        info = set_doc["subscription_info"]
        assert info["endpoint"] == W3C_SUBSCRIPTION["endpoint"]
        assert info["keys"]["p256dh"] == W3C_SUBSCRIPTION["keys"]["p256dh"]
        assert info["keys"]["auth"] == W3C_SUBSCRIPTION["keys"]["auth"]

    def test_endpoint_field_indexed_for_pruner(self):
        """The dead-endpoint pruner deletes by `{"endpoint": <str>}`,
        so the top-level endpoint field MUST be persisted alongside
        the nested one."""
        deps.db.push_subscriptions.update_one = AsyncMock(return_value=None)
        _run(notif.push_subscribe({"subscription": W3C_SUBSCRIPTION},
                                  user=_student()))
        filt = deps.db.push_subscriptions.update_one.call_args.args[0]
        set_doc = deps.db.push_subscriptions.update_one.call_args.args[1]["$set"]
        assert filt == {"endpoint": W3C_SUBSCRIPTION["endpoint"]}
        assert set_doc["endpoint"] == W3C_SUBSCRIPTION["endpoint"]

    def test_subscription_active_flag_set_true(self):
        deps.db.push_subscriptions.update_one = AsyncMock(return_value=None)
        _run(notif.push_subscribe({"subscription": W3C_SUBSCRIPTION},
                                  user=_student()))
        set_doc = deps.db.push_subscriptions.update_one.call_args.args[1]["$set"]
        assert set_doc["active"] is True


class TestRejectsMalformedSubscriptions:
    def test_missing_endpoint_returns_400(self):
        bad = {"keys": W3C_SUBSCRIPTION["keys"]}
        with pytest.raises(HTTPException) as exc:
            _run(notif.push_subscribe({"subscription": bad}, user=_student()))
        assert exc.value.status_code == 400

    def test_empty_body_returns_400(self):
        with pytest.raises(HTTPException) as exc:
            _run(notif.push_subscribe({}, user=_student()))
        assert exc.value.status_code == 400

    def test_missing_keys_block_returns_400(self):
        bad = {"endpoint": W3C_SUBSCRIPTION["endpoint"]}  # no keys at all
        with pytest.raises(HTTPException) as exc:
            _run(notif.push_subscribe({"subscription": bad}, user=_student()))
        assert exc.value.status_code == 400
        assert "keys" in (exc.value.detail or "").lower()

    def test_missing_p256dh_returns_400(self):
        bad = {
            "endpoint": W3C_SUBSCRIPTION["endpoint"],
            "keys": {"auth": W3C_SUBSCRIPTION["keys"]["auth"]},
        }
        with pytest.raises(HTTPException) as exc:
            _run(notif.push_subscribe({"subscription": bad}, user=_student()))
        assert exc.value.status_code == 400
        assert "p256dh" in (exc.value.detail or "").lower()

    def test_missing_auth_returns_400(self):
        bad = {
            "endpoint": W3C_SUBSCRIPTION["endpoint"],
            "keys": {"p256dh": W3C_SUBSCRIPTION["keys"]["p256dh"]},
        }
        with pytest.raises(HTTPException) as exc:
            _run(notif.push_subscribe({"subscription": bad}, user=_student()))
        assert exc.value.status_code == 400
        assert "auth" in (exc.value.detail or "").lower()
