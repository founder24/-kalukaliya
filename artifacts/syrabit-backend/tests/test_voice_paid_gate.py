"""Task #549 — voice routes must return 402 for free-plan callers.

The body shape is part of the contract: the SPA reads
`detail.error == "voice_requires_paid_plan"` and `detail.upgrade_url`
to route the user to the pricing page. Admin / staff / educator users
bypass the gate; paying users pass through.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from auth_deps import require_paid_plan


def _app_with(user: dict | None) -> TestClient:
    app = FastAPI()

    async def _fake_user():
        return user

    # Re-bind the dep to skip the JWT layer; only exercise the plan-gate
    # logic so this test stays hermetic.
    from fastapi import Depends
    app.dependency_overrides[require_paid_plan] = (
        lambda u=Depends(_fake_user): require_paid_plan.__wrapped__(u)
        if hasattr(require_paid_plan, "__wrapped__") else None
    )

    @app.get("/protected")
    async def protected(_: dict = Depends(require_paid_plan)):
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.asyncio
async def test_free_user_blocked_with_structured_402():
    with pytest.raises(HTTPException) as exc:
        await require_paid_plan(user={"id": "u1", "plan": "free"})
    assert exc.value.status_code == 402
    assert exc.value.detail["error"] == "voice_requires_paid_plan"
    assert exc.value.detail["upgrade_url"] == "/pricing"
    assert "paid plan" in exc.value.detail["message"].lower()


@pytest.mark.asyncio
async def test_missing_plan_blocked_with_402():
    with pytest.raises(HTTPException) as exc:
        await require_paid_plan(user={"id": "u1"})
    assert exc.value.status_code == 402
    assert exc.value.detail["error"] == "voice_requires_paid_plan"


@pytest.mark.asyncio
async def test_paid_user_allowed():
    out = await require_paid_plan(user={"id": "u1", "plan": "starter"})
    assert out["plan"] == "starter"

    out = await require_paid_plan(user={"id": "u2", "plan": "pro"})
    assert out["plan"] == "pro"


@pytest.mark.asyncio
async def test_admin_bypass():
    out = await require_paid_plan(user={"id": "a1", "is_admin": True, "plan": "free"})
    assert out["is_admin"] is True


@pytest.mark.asyncio
async def test_staff_and_educator_bypass():
    for role in ("staff", "educator"):
        out = await require_paid_plan(user={"id": "x", "role": role, "plan": "free"})
        assert out["role"] == role


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_free_user_blocked_with_structured_402())
    asyncio.run(test_missing_plan_blocked_with_402())
    asyncio.run(test_paid_user_allowed())
    asyncio.run(test_admin_bypass())
    asyncio.run(test_staff_and_educator_bypass())
    print("PASS")
