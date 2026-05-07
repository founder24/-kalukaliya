"""tests.integration.test_sarvam_smoke — Task #553.

Live-network smoke test for ``providers.sarvam.chat`` against the real
``api.sarvam.ai`` endpoint. Gated on the ``SARVAM_INTEGRATION=1``
environment variable so it never runs in normal CI / pytest invocations.

To run locally::

    SARVAM_INTEGRATION=1 SARVAM_API_KEY=... \\
      pytest -q artifacts/syrabit-backend/tests/integration/test_sarvam_smoke.py
"""
from __future__ import annotations

import asyncio
import os

import pytest

if os.environ.get("SARVAM_INTEGRATION") != "1":
    pytest.skip(
        "SARVAM_INTEGRATION not set — skipping live-network smoke test",
        allow_module_level=True,
    )

if not os.environ.get("SARVAM_API_KEY"):
    pytest.skip(
        "SARVAM_API_KEY not set — cannot run live smoke test",
        allow_module_level=True,
    )


def test_sarvam_live_assamese_reply():
    from providers.sarvam import chat, ChatResponse

    out = asyncio.run(
        chat(
            [
                {"role": "user", "content": "নমস্কাৰ"},
            ],
            language="as",
            user_id=None,
            max_tokens=80,
        )
    )

    assert isinstance(out, ChatResponse)
    assert out.text.strip(), "expected non-empty reply"
    assert out.model.startswith("sarvam"), f"unexpected model: {out.model}"
    assert out.latency_ms > 0
