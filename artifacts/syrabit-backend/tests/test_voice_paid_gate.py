"""Task #549 — voice routes must return 402 for free-plan callers.

The body shape is part of the contract: the SPA reads
`detail.error == "voice_requires_paid_plan"` and `detail.upgrade_url`
to route the user to the pricing page. Admin / staff / educator users
bypass the gate; paying users pass through.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from auth_deps import require_paid_plan


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


def test_voice_routes_wired_to_require_paid_plan():
    """Static check — every /voice/* paid endpoint must declare
    Depends(require_paid_plan). Mirrors the CI guard so a single
    pytest run catches a forgotten gate before deploy."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "routes" / "voice.py").read_text()
    assert "require_paid_plan" in src
    for route in ("/voice/tts", "/voice/stt", "/voice/voice"):
        idx = src.find(f'"{route}"')
        assert idx >= 0, f"route decorator for {route} missing"
        assert "Depends(require_paid_plan)" in src[idx: idx + 2000], (
            f"{route} must use Depends(require_paid_plan)"
        )
