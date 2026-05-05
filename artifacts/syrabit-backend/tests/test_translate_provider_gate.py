"""Task #386 — verify ``TRANSLATE_PROVIDER=workers_indic`` short-circuits
the Google Translate / Vertex / AWS branches and pins the dispatcher
to Cloudflare Workers AI IndicTrans2.

Three behaviours we lock in here:

  1. ``vertex_services.translate`` no longer calls ``providers.google_translate``
     when the flag is on — the in-process spy on ``_gt.translate`` and
     ``providers.gcp_counters.inc_translate`` must both stay at 0.
  2. ``llm.call_translate_with_dispatch`` ignores the weighted
     fallback chain and routes straight to ``workers_ai_indic``.
  3. The translation-provider distribution counter records each call
     so the cf-health row reflects the live mix.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_metrics():
    from translate_provider_metrics import reset as _r
    _r()
    yield
    _r()


@pytest.mark.asyncio
async def test_vertex_services_translate_skips_google_when_flag_on(monkeypatch):
    """Under ``TRANSLATE_PROVIDER=workers_indic`` the google_translate
    branch must be skipped entirely — no calls to ``_gt.translate``,
    no inc_translate increments."""
    import vertex_services
    monkeypatch.setattr("config.TRANSLATE_PROVIDER", "workers_indic", raising=False)

    google_calls: list[str] = []
    counter_calls: list[int] = []

    async def _fake_gt_translate(*args, **kwargs):
        google_calls.append("called")
        return "should-not-be-used"

    def _fake_is_configured():
        return True

    def _fake_is_indic_target(_):
        return True

    def _fake_inc_translate(n):
        counter_calls.append(n)

    from providers import google_translate as _gt
    monkeypatch.setattr(_gt, "translate", _fake_gt_translate, raising=False)
    monkeypatch.setattr(_gt, "is_configured", _fake_is_configured, raising=False)
    monkeypatch.setattr(_gt, "is_indic_target", _fake_is_indic_target, raising=False)
    from providers import gcp_counters
    monkeypatch.setattr(gcp_counters, "inc_translate", _fake_inc_translate, raising=False)

    # Force the workers_indic path to succeed so the function returns a value.
    async def _fake_cf_translate(text, target_lang, source_lang):
        return "অসমীয়া"

    from providers import cloudflare_ai
    monkeypatch.setattr(cloudflare_ai, "translate", _fake_cf_translate, raising=False)

    out = await vertex_services.translate("hello", target_lang="as", source_lang="en")
    assert out == "অসমীয়া"
    assert google_calls == [], "google_translate must not be called when flag is on"
    assert counter_calls == [], "gcp_counters.inc_translate must stay at 0"


@pytest.mark.asyncio
async def test_call_translate_with_dispatch_pins_to_workers_indic(monkeypatch):
    """The dispatcher must select ``workers_ai_indic`` regardless of
    weighted PROVIDER_PRIORITY when the flag is on."""
    import llm
    monkeypatch.setattr("config.TRANSLATE_PROVIDER", "workers_indic", raising=False)

    selected: list[str] = []

    def _spy_select(_feature, **_kwargs):
        selected.append("WEIGHTED")
        return "vertex"  # would normally be picked

    monkeypatch.setattr(llm, "select_provider", _spy_select, raising=False)

    async def _fake_indic(text, **kwargs):
        return "অনুবাদ"

    import providers.workers_indic as _wi
    monkeypatch.setattr(_wi, "call_indic_trans", _fake_indic, raising=False)

    out = await llm.call_translate_with_dispatch(
        "hello", source_lang="en-IN", target_lang="as-IN", lang="as",
    )
    assert out == "অনুবাদ"
    # The weighted selector must NOT have been consulted because the
    # workers_indic-only mode bypasses it.
    assert selected == [], "select_provider must not be called under workers_indic-only mode"


@pytest.mark.asyncio
async def test_translate_provider_metric_records_workers_indic(monkeypatch):
    """A successful call routed via vertex_services.translate must
    increment the ``workers_indic`` counter so the cf-health row
    reflects the live mix."""
    import vertex_services
    monkeypatch.setattr("config.TRANSLATE_PROVIDER", "workers_indic", raising=False)

    async def _fake_cf_translate(text, target_lang, source_lang):
        return "x"

    from providers import cloudflare_ai
    monkeypatch.setattr(cloudflare_ai, "translate", _fake_cf_translate, raising=False)

    await vertex_services.translate("hi", target_lang="as", source_lang="en")
    from translate_provider_metrics import snapshot as _snap
    snap = _snap()
    assert snap["total_calls"] == 1
    assert "workers_indic" in snap["providers"]
    assert snap["providers"]["workers_indic"]["success"] == 1
    assert snap["primary_provider"] == "workers_indic"
