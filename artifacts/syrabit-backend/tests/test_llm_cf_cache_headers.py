"""Regression for the chat-broken triage on 2026-04-26.

The CF AI Gateway path for Gemini (and every other OpenAI-compat
provider) was sending ``Authorization: ""`` (empty) to CF, which then
forwarded an empty Authorization header to Google AI Studio,
producing the long-running 400 "Missing or invalid Authorization
header" error EVEN THOUGH ``GEMINI_API_KEY`` was healthy.

Fix shape (revised after architect review on the same day):
* ``_cf_cache_headers`` now decides whether to clear the upstream
  ``Authorization`` header *per call*, derived from the api_key the
  caller is about to send:
    - real provider key  → keep Authorization (CF forwards it)
    - BYOK_PLACEHOLDER   → clear Authorization (CF substitutes the
                           dashboard-stored key)
* The legacy ``clear_upstream_auth=...`` kwarg still works as an
  explicit override for tests / special bypass paths.

These tests guard both branches so a future "let's clean up the
function signature" refactor cannot silently re-break either chat
(real-key path) or BYOK substitution (placeholder path).
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def llm_module(monkeypatch):
    # CF gateway must be enabled or the helper short-circuits to {}.
    monkeypatch.setenv("CF_AI_GATEWAY_ACCOUNT_ID", "test-acct")
    monkeypatch.setenv("CF_AI_GATEWAY_ID", "test-gw")
    monkeypatch.setenv("CF_AI_GATEWAY_TOKEN", "test-cf-token")
    # Re-import so the env-derived module-level constants pick up the
    # test values.
    import config as cfg
    importlib.reload(cfg)
    import llm as llm_mod
    importlib.reload(llm_mod)
    # Force the in-process gateway-health flag to UP so the helper does
    # not short-circuit to {} (which would mask the real header shape).
    cfg._cf_gw_healthy = True
    return llm_mod, cfg


def test_cf_cache_headers_real_key_does_not_clear_authorization(llm_module):
    llm_mod, _ = llm_module
    # The whole bug: previously ``Authorization`` was being forced to
    # the empty string here, clobbering the OpenAI SDK's bearer auth.
    # Passing a real provider key MUST leave Authorization alone so the
    # SDK's Bearer header reaches the upstream provider.
    h = llm_mod._cf_cache_headers(api_key="real-provider-key")
    assert "Authorization" not in h, (
        f"_cf_cache_headers(real_key) MUST NOT include Authorization "
        f"(real api_key callers rely on the SDK's bearer auth reaching "
        f"upstream); got: {h!r}"
    )
    # Cache-control + gateway-auth + BYOK opt-in still attach.
    assert h.get("cf-aig-cache-ttl"), h
    assert h.get("cf-aig-byok-key") == "true", h
    assert h.get("cf-aig-authorization", "").startswith("Bearer "), h


def test_cf_cache_headers_no_args_does_not_clear_authorization(llm_module):
    """No api_key passed (legacy callsite) → treated as not-BYOK."""
    llm_mod, _ = llm_module
    h = llm_mod._cf_cache_headers()
    assert "Authorization" not in h, h


def test_cf_cache_headers_byok_placeholder_clears_authorization(llm_module):
    """BYOK runtime regression — placeholder api_key MUST clear auth so
    CF substitutes the dashboard-stored key. Without this, CF would
    forward ``Bearer x`` to the upstream provider and 401."""
    llm_mod, cfg = llm_module
    h = llm_mod._cf_cache_headers(api_key=cfg.BYOK_PLACEHOLDER)
    assert h.get("Authorization") == "", (
        f"BYOK_PLACEHOLDER MUST emit Authorization='' so CF injects "
        f"the BYOK-stored key upstream; got: {h!r}"
    )
    assert h.get("cf-aig-byok-key") == "true", h


def test_cf_cache_headers_explicit_override_wins(llm_module):
    """Explicit ``clear_upstream_auth`` overrides the api_key heuristic
    in both directions, for tests and special bypass paths."""
    llm_mod, cfg = llm_module
    # Real key + force-clear → must clear (test/bypass case).
    h_force_clear = llm_mod._cf_cache_headers(
        api_key="real-provider-key", clear_upstream_auth=True
    )
    assert h_force_clear.get("Authorization") == "", h_force_clear
    # Placeholder + force-keep → must NOT clear (test case).
    h_force_keep = llm_mod._cf_cache_headers(
        api_key=cfg.BYOK_PLACEHOLDER, clear_upstream_auth=False
    )
    assert "Authorization" not in h_force_keep, h_force_keep


def test_cf_cache_headers_empty_when_gateway_down(llm_module):
    llm_mod, cfg = llm_module
    cfg.mark_cf_gateway_down()
    # Gateway down → no CF-specific headers at all so the SDK's default
    # ``Authorization: Bearer <key>`` reaches the direct-URL upstream
    # untouched. Verified for both real-key and BYOK-placeholder calls.
    assert llm_mod._cf_cache_headers(api_key="real-provider-key") == {}
    assert llm_mod._cf_cache_headers(api_key=cfg.BYOK_PLACEHOLDER) == {}
    assert llm_mod._cf_cache_headers() == {}


def _emergent_chat_module(monkeypatch):
    monkeypatch.setenv("CF_AI_GATEWAY_ACCOUNT_ID", "test-acct")
    monkeypatch.setenv("CF_AI_GATEWAY_ID", "test-gw")
    monkeypatch.setenv("CF_AI_GATEWAY_TOKEN", "test-cf-token")
    import config as cfg
    importlib.reload(cfg)
    cfg._cf_gw_healthy = True
    from emergentintegrations.llm import chat as chat_mod
    importlib.reload(chat_mod)
    return chat_mod, cfg


def test_emergent_real_key_does_not_clear_authorization(monkeypatch):
    """Same regression for the LlmChat path (different module, same
    bug). LlmChat decides per-instance from ``self.api_key``."""
    chat_mod, _ = _emergent_chat_module(monkeypatch)
    inst = chat_mod.LlmChat(
        api_key="real-provider-key",
        session_id="t",
        system_message="t",
    ).with_model("gemini", "gemini-2.5-flash")
    h = inst._cf_cache_headers() or {}
    assert "Authorization" not in h, (
        f"emergent LlmChat with REAL api_key MUST NOT include "
        f"Authorization in extra_headers; got: {h!r}"
    )
    assert h.get("cf-aig-byok-key") == "true", h


def test_emergent_byok_placeholder_clears_authorization(monkeypatch):
    """Architect-flagged BYOK regression: ``LlmChat`` with the placeholder
    api_key MUST clear Authorization so CF substitutes the stored key."""
    chat_mod, cfg = _emergent_chat_module(monkeypatch)
    inst = chat_mod.LlmChat(
        api_key=cfg.BYOK_PLACEHOLDER,
        session_id="t",
        system_message="t",
    ).with_model("gemini", "gemini-2.5-flash")
    h = inst._cf_cache_headers() or {}
    assert h.get("Authorization") == "", (
        f"emergent LlmChat with BYOK_PLACEHOLDER MUST emit "
        f"Authorization='' so CF injects the dashboard-stored key; "
        f"got: {h!r}"
    )
    assert h.get("cf-aig-byok-key") == "true", h


# ───────────────────────────────────────────────────────────────────────────
# Integration-level guard: the actual `_call_gemini` / `_call_openai_compat`
# callsites inside llm.py must forward the runtime api_key into the BYOK-aware
# helper so a placeholder key produces Authorization='' and a real key
# produces no Authorization injection. Architect 2026-04-26 flagged that a
# helper-only test can pass while production callsites bypass the dynamic
# branch entirely (which was the original shape of this fix).
# ───────────────────────────────────────────────────────────────────────────
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock


def _patched_oai_response():
    """Build a minimal OpenAI-style response object the llm.py callers consume."""
    msg = MagicMock()
    msg.content = "ok"
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# NOTE: The two `test_call_gemini_*` regression tests were removed in the
# 2026-05-03 vertex-only Gemini migration. `_call_gemini` (direct
# generativelanguage.googleapis.com client) no longer exists; all Gemini
# calls now route through `vertex_format.format_with_vertex` (Task #490 — Vertex is content_format only)
# which use SA-minted OAuth bearers and bypass the CF AI Gateway BYOK path
# entirely, so the cache-header forwarding contract is no longer applicable
# to Gemini. The generic `_call_openai_compat` BYOK-forwarding test below
# still exercises the same helper for the providers that still use BYOK.


def test_call_openai_compat_forwards_api_key_byok(llm_module):
    """Same regression guard for the generic OpenAI-compat callsite."""
    llm_mod, cfg = llm_module
    captured = {}

    async def _fake_create(**kwargs):
        captured["extra_headers"] = kwargs.get("extra_headers")
        return _patched_oai_response()

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=_fake_create)

    with patch.object(llm_mod, "_get_oai_client", return_value=fake_client):
        asyncio.run(
            llm_mod._call_openai_compat(
                messages=[{"role": "user", "content": "hi"}],
                api_key=cfg.BYOK_PLACEHOLDER,
                model="x-model",
                max_tokens=8,
                provider="openrouter",
                fallback_base="https://openrouter.ai/api/v1",
            )
        )

    headers = captured["extra_headers"] or {}
    assert headers.get("Authorization") == "", headers
