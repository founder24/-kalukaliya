"""Task #767 — regression coverage for the Razorpay verify-payment route.

The audit (FULL_APP_AUDIT_2026-04-23.md, finding T1) flagged that
``routes.admin_monetization.verify_payment`` — ~200 lines of revenue-critical
code that performs the HMAC signature check, server-side order re-fetch,
amount/plan/user cross-check and a three-store write with a compensating
rollback — had zero automated regression coverage. Any future edit there
silently risks double-charges, signature bypass, or partial activations
that leave Mongo and Postgres out of sync.

These tests cover the highest-risk branches without standing up a real
Razorpay sandbox or Postgres instance. They directly invoke the route's
async handler with an injected stub user and monkeypatched
``_get_razorpay_keys`` / ``sys.modules['razorpay']`` / ``deps.pg_pool``,
mirroring the pattern used elsewhere in this suite (see
``tests/test_atomic_deduct_race.py``).

Cases:
    * ``test_signature_tamper_rejected`` — 400 when HMAC doesn't match.
    * ``test_plan_downgrade_attempt_rejected`` — 400 when a Pro user
      tries to "verify" a Starter purchase.
    * ``test_idempotent_reverify_returns_success`` — a duplicate
      verify for an already-completed payment short-circuits to
      success without inserting another row or refetching the order.
    * ``test_amount_mismatch_rejected`` — 400 when the order amount
      returned by Razorpay disagrees with the server's plan price
      table (defends against a tampered client.)
    * ``test_pg_failure_triggers_rollback`` — when the Postgres update
      raises after the payment row is inserted, the route raises 500
      AND marks the payment row ``status="failed"`` so a stale
      ``completed`` row can never be picked up by ``/payments/recover``.
"""
import asyncio
import hashlib
import hmac
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from tests._deps_stub import install_deps_stub

install_deps_stub(force=True)

import deps  # noqa: E402
from routes import admin_monetization as mon  # noqa: E402
from tests._deps_stub import _MotorDbMock  # noqa: E402


KEY_ID = "rzp_test_key_id"
KEY_SECRET = "rzp_test_key_secret"
WEBHOOK_SECRET = "rzp_test_wh_secret"


def _sign(order_id: str, payment_id: str, secret: str = KEY_SECRET) -> str:
    return hmac.new(
        secret.encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _user(plan: str = "free", credits_limit: int = 30) -> dict:
    return {
        "id": "user-1",
        "email": "u@example.com",
        "name": "Test User",
        "plan": plan,
        "credits_limit": credits_limit,
        "document_access": "none",
    }


@pytest.fixture
def stub_keys(monkeypatch):
    async def _keys():
        return KEY_ID, KEY_SECRET, WEBHOOK_SECRET
    monkeypatch.setattr(mon, "_get_razorpay_keys", _keys)


@pytest.fixture
def fresh_db(monkeypatch):
    """Hand each test a brand-new ``_MotorDbMock`` so call history /
    return values from a previous test in the same session can't leak in
    via the conftest snapshot/restore loop. Mirror / Redis / PG side
    effects are also defanged so the happy path doesn't hit the network
    or send a real email."""
    fresh = _MotorDbMock()
    fresh.payments.find_one = AsyncMock(return_value=None)
    fresh.payments.insert_one = AsyncMock(return_value=None)
    fresh.payments.update_one = AsyncMock(return_value=None)
    fresh.users.update_one = AsyncMock(return_value=None)
    # Task #774: ``mon.deps`` and ``deps`` (this module's local import)
    # are now guaranteed to be the same module object — see
    # ``install_deps_stub``'s in-place mutation path. So a single
    # ``monkeypatch.setattr(deps, ...)`` is sufficient; the duplicate
    # ``mon.deps.*`` patches are no longer needed.
    monkeypatch.setattr(deps, "db", fresh)
    monkeypatch.setattr(mon, "db", fresh)
    monkeypatch.setattr(mon, "_supa_mirror", lambda fn: None)
    monkeypatch.setattr(mon, "_redis_invalidate_session", lambda uid: None)
    monkeypatch.setattr(deps, "pg_pool", None, raising=False)
    # Email send is fire-and-forget (asyncio.create_task) — stub so the
    # success path doesn't try to talk to SES (Task #556 — sole path).
    import email_templates
    monkeypatch.setattr(
        email_templates, "send_plan_activation",
        AsyncMock(return_value=None),
    )
    return fresh


@pytest.fixture
def fake_razorpay(monkeypatch):
    """Install a fake ``razorpay`` module so the route's lazy
    ``import razorpay`` returns a configurable client we can drive."""
    fake_mod = MagicMock(name="razorpay")
    fake_client = MagicMock(name="razorpay.Client")
    # ``order.fetch`` is sync in the real SDK — keep that contract.
    fake_client.order = MagicMock()
    fake_client.order.fetch = MagicMock(return_value={
        "amount": mon.PLAN_PRICES_INR["starter"],
        "status": "paid",
        "notes": {"plan": "starter", "user_id": "user-1"},
    })
    fake_mod.Client = MagicMock(return_value=fake_client)
    monkeypatch.setitem(sys.modules, "razorpay", fake_mod)
    return fake_client


def _run(coro):
    return asyncio.run(coro)


def test_signature_tamper_rejected(stub_keys, fresh_db):
    body = mon.PaymentVerifyRequest(
        razorpay_order_id="order_tamper",
        razorpay_payment_id="pay_tamper",
        razorpay_signature="0" * 64,  # not the real HMAC
        plan="starter",
    )
    with pytest.raises(HTTPException) as exc:
        _run(mon.verify_payment(body=body, user=_user()))
    assert exc.value.status_code == 400
    assert "signature" in exc.value.detail.lower()
    # No payment row inserted, no plan change.
    fresh_db.payments.insert_one.assert_not_called()
    fresh_db.users.update_one.assert_not_called()


def test_plan_downgrade_attempt_rejected(stub_keys, fresh_db):
    body = mon.PaymentVerifyRequest(
        razorpay_order_id="order_dg",
        razorpay_payment_id="pay_dg",
        razorpay_signature=_sign("order_dg", "pay_dg"),
        plan="starter",
    )
    with pytest.raises(HTTPException) as exc:
        _run(mon.verify_payment(body=body, user=_user(plan="pro")))
    assert exc.value.status_code == 400
    # Detail mentions the downgrade guard.
    assert "lower" in exc.value.detail.lower()
    fresh_db.payments.insert_one.assert_not_called()


def test_idempotent_reverify_returns_success(stub_keys, fresh_db, monkeypatch):
    """If the payment row is already ``status=completed``, the route
    must short-circuit to a success response without re-fetching the
    order from Razorpay or inserting/updating anything else."""
    fresh_db.payments.find_one = AsyncMock(return_value={
        "razorpay_payment_id": "pay_idem",
        "status": "completed",
        "plan": "starter",
    })
    sentinel_razorpay = MagicMock(name="razorpay-must-not-be-called")
    monkeypatch.setitem(sys.modules, "razorpay", sentinel_razorpay)

    body = mon.PaymentVerifyRequest(
        razorpay_order_id="order_idem",
        razorpay_payment_id="pay_idem",
        razorpay_signature=_sign("order_idem", "pay_idem"),
        plan="starter",
    )
    result = _run(mon.verify_payment(body=body, user=_user()))

    assert result["success"] is True
    assert result["plan"] == "starter"
    assert result["credits_added"] == mon.PLAN_CREDITS["starter"]
    assert "already" in result["message"].lower()
    # Idempotent path must NOT touch the order API or write any rows.
    sentinel_razorpay.Client.assert_not_called()
    fresh_db.payments.insert_one.assert_not_called()
    fresh_db.payments.update_one.assert_not_called()
    fresh_db.users.update_one.assert_not_called()


def test_amount_mismatch_rejected(stub_keys, fresh_db, fake_razorpay):
    """If a tampered client sends the right signature for an order whose
    amount disagrees with the server's plan price, fail loud (400)."""
    fake_razorpay.order.fetch = MagicMock(return_value={
        "amount": 1,  # one paise — would activate Pro for ₹0.01 if the check were missing
        "status": "paid",
        "notes": {"plan": "starter", "user_id": "user-1"},
    })
    body = mon.PaymentVerifyRequest(
        razorpay_order_id="order_amt",
        razorpay_payment_id="pay_amt",
        razorpay_signature=_sign("order_amt", "pay_amt"),
        plan="starter",
    )
    with pytest.raises(HTTPException) as exc:
        _run(mon.verify_payment(body=body, user=_user()))
    assert exc.value.status_code == 400
    assert "amount" in exc.value.detail.lower()
    fresh_db.payments.insert_one.assert_not_called()
    fresh_db.users.update_one.assert_not_called()


def test_pg_failure_triggers_rollback(stub_keys, fresh_db, fake_razorpay, monkeypatch):
    """If the Postgres upgrade raises after the payment row is
    inserted, the compensating rollback must:
      * mark the payment row ``status="failed"`` (so /payments/recover
        cannot later replay it as if it had succeeded),
      * not leave Mongo's ``users`` doc upgraded (PG fails before
        Mongo update is attempted in the current ordering),
      * surface a 500 with a "rolled back" hint to the caller.
    """

    class _BoomConn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *args, **kwargs):
            raise RuntimeError("simulated postgres outage")

    class _BoomPool:
        def acquire(self):
            return _BoomConn()

    boom = _BoomPool()
    # Task #774: single-site monkeypatch — see helper docstring on
    # ``install_deps_stub`` for why ``mon.deps`` and ``deps`` now share
    # identity, making the duplicate patch unnecessary.
    monkeypatch.setattr(deps, "pg_pool", boom, raising=False)

    body = mon.PaymentVerifyRequest(
        razorpay_order_id="order_pg",
        razorpay_payment_id="pay_pg",
        razorpay_signature=_sign("order_pg", "pay_pg"),
        plan="starter",
    )
    with pytest.raises(HTTPException) as exc:
        _run(mon.verify_payment(body=body, user=_user()))
    assert exc.value.status_code == 500
    assert "rolled back" in exc.value.detail.lower()

    # Payment row was inserted (step 1 succeeded).
    assert fresh_db.payments.insert_one.await_count == 1

    # The rollback path marked the payment row failed.
    update_calls = fresh_db.payments.update_one.await_args_list
    assert update_calls, "rollback did not update the payment row"
    found_failed = False
    for call in update_calls:
        args = call.args
        update_doc = args[1] if len(args) > 1 else call.kwargs.get("update", {})
        if update_doc.get("$set", {}).get("status") == "failed":
            found_failed = True
            break
    assert found_failed, "rollback should mark payment status='failed'"

    # Mongo users.update_one is never called: the route does PG first,
    # so when PG raises Mongo upgrade is skipped — and the rollback
    # branch only un-does Mongo if it had been written.
    assert fresh_db.users.update_one.await_count == 0
