"""
Tests for cf_enterprise.py — Load Balancing, Bulk Redirects, Zaraz, Speed.

All CF REST calls are monkeypatched; no live network traffic.
"""
from __future__ import annotations

import asyncio
import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

def _run(coro):
    # Task #284 — fresh loop per call (Python 3.11+ no longer auto-creates
    # a main-thread loop on demand).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def cf_env(monkeypatch):
    """Inject minimal CF env vars so is_configured() returns True."""
    monkeypatch.delenv("CLOUDFLARE_ANALYTICS_TOKEN", raising=False)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token-abc")
    monkeypatch.setenv("CF_ZONE_ID", "zone-abc123")
    monkeypatch.setenv("CF_AI_GATEWAY_ACCOUNT_ID", "acct-abc123")


# ─── shared mock builder ───────────────────────────────────────────────────────

def _cf_ok(result):
    """Return a mock HTTP response matching CF's REST envelope."""
    import httpx

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"success": True, "result": result, "errors": [], "messages": []}

    return FakeResp()


def _cf_fail(errors=None):
    import httpx

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"success": False, "result": None, "errors": errors or [{"message": "error"}]}

    return FakeResp()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. is_configured / helpers
# ═══════════════════════════════════════════════════════════════════════════════

def test_is_configured_with_env(cf_env):
    import cf_enterprise as cfe
    assert cfe.is_configured() is True


def test_is_configured_without_token(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ANALYTICS_TOKEN", raising=False)
    monkeypatch.setenv("CF_ZONE_ID", "zone-abc123")
    import importlib, cf_enterprise as cfe
    # _token() returns '' → is_configured() False
    assert cfe._token() == ""
    assert cfe.is_configured() is False


def test_is_configured_without_zone(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.delenv("CF_ZONE_ID", raising=False)
    import cf_enterprise as cfe
    assert cfe.is_configured() is False


def test_headers_contain_bearer(cf_env):
    import cf_enterprise as cfe
    h = cfe._headers()
    assert h["Authorization"].startswith("Bearer ")
    assert "test-token-abc" in h["Authorization"]


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Load Balancing — list pools
# ═══════════════════════════════════════════════════════════════════════════════

def test_lb_list_pools_returns_list(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_get(url, **kwargs):
        return _cf_ok([{"id": "pool-1", "name": "syrabit-api-primary"}])

    monkeypatch.setattr(cfe._client(), "get", fake_get)
    pools = _run(cfe.lb_list_pools())
    assert isinstance(pools, list)
    assert pools[0]["name"] == "syrabit-api-primary"


def test_lb_list_pools_empty_when_unconfigured(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ANALYTICS_TOKEN", raising=False)
    monkeypatch.delenv("CF_ZONE_ID", raising=False)
    import cf_enterprise as cfe
    pools = _run(cfe.lb_list_pools())
    assert pools == []


def test_lb_status_shape(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_list_pools():
        return [{"id": "p1", "name": "primary"}]

    async def fake_list_monitors():
        return [{"id": "m1", "type": "https"}]

    async def fake_list_balancers():
        return [{"id": "lb1", "name": "api.syrabit.ai"}]

    monkeypatch.setattr(cfe, "lb_list_pools", fake_list_pools)
    monkeypatch.setattr(cfe, "lb_list_monitors", fake_list_monitors)
    monkeypatch.setattr(cfe, "lb_list_balancers", fake_list_balancers)

    status = _run(cfe.lb_status())
    assert status["configured"] is True
    assert status["pool_count"] == 1
    assert status["lb_count"] == 1
    assert len(status["pools"]) == 1
    assert len(status["load_balancers"]) == 1


def test_lb_create_pool_success(monkeypatch, cf_env):
    import cf_enterprise as cfe
    created = {"id": "pool-new", "name": "test-pool"}

    async def fake_post(url, **kwargs):
        return _cf_ok(created)

    monkeypatch.setattr(cfe._client(), "post", fake_post)
    result = _run(cfe.lb_create_pool("test-pool", [{"name": "o1", "address": "api.syrabit.ai", "enabled": True, "weight": 1}]))
    assert result["id"] == "pool-new"


def test_lb_create_pool_returns_none_on_failure(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_post(url, **kwargs):
        return _cf_fail()

    monkeypatch.setattr(cfe._client(), "post", fake_post)
    result = _run(cfe.lb_create_pool("fail-pool", []))
    assert result is None


def test_lb_delete_pool_success(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_delete(url, **kwargs):
        return _cf_ok({"id": "pool-1"})

    monkeypatch.setattr(cfe._client(), "delete", fake_delete)
    ok = _run(cfe.lb_delete_pool("pool-1"))
    assert ok is True


def test_lb_create_monitor_success(monkeypatch, cf_env):
    import cf_enterprise as cfe
    created = {"id": "mon-1", "type": "https", "path": "/healthz/ai"}

    async def fake_post(url, **kwargs):
        return _cf_ok(created)

    monkeypatch.setattr(cfe._client(), "post", fake_post)
    result = _run(cfe.lb_create_monitor(type="https", path="/healthz/ai"))
    assert result["path"] == "/healthz/ai"


def test_lb_setup_syrabit_skips_existing(monkeypatch, cf_env):
    """lb_setup_syrabit should mark objects as 'skipped' when they already exist."""
    import cf_enterprise as cfe

    async def fake_list_monitors():
        return [{"id": "m-existing", "type": "https", "path": "/healthz/ai"}]

    async def fake_list_pools():
        return [{"id": "p-existing", "name": "syrabit-api-primary"}]

    async def fake_list_balancers():
        return [{"id": "lb-existing", "name": "api.syrabit.ai"}]

    monkeypatch.setattr(cfe, "lb_list_monitors", fake_list_monitors)
    monkeypatch.setattr(cfe, "lb_list_pools", fake_list_pools)
    monkeypatch.setattr(cfe, "lb_list_balancers", fake_list_balancers)

    result = _run(cfe.lb_setup_syrabit())
    assert "monitor" in result["skipped"]
    assert "pool" in result["skipped"]
    assert "lb" in result["skipped"]


def test_lb_create_balancer_steering_policy(monkeypatch, cf_env):
    """Balancer payload must include the configured steering_policy."""
    import cf_enterprise as cfe
    captured = {}

    async def fake_post(url, *, json=None, **kwargs):
        captured["payload"] = json
        return _cf_ok({"id": "lb-1"})

    monkeypatch.setattr(cfe._client(), "post", fake_post)
    _run(cfe.lb_create_balancer(
        "api.syrabit.ai",
        default_pools=["p1"],
        fallback_pool="p1",
        steering_policy="proximity",
    ))
    assert captured["payload"]["steering_policy"] == "proximity"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Bulk Redirects
# ═══════════════════════════════════════════════════════════════════════════════

def test_redirect_list_lists_empty_when_unconfigured(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ANALYTICS_TOKEN", raising=False)
    monkeypatch.delenv("CF_ZONE_ID", raising=False)
    import cf_enterprise as cfe
    result = _run(cfe.redirect_list_lists())
    assert result == []


def test_redirect_get_or_create_list_returns_existing(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_list():
        return [{"id": "list-1", "name": "syrabit_redirects", "kind": "redirect"}]

    monkeypatch.setattr(cfe, "redirect_list_lists", fake_list)
    result = _run(cfe.redirect_get_or_create_list("syrabit_redirects"))
    assert result["id"] == "list-1"


def test_redirect_get_or_create_list_creates_new(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_list():
        return []

    async def fake_post(url, **kwargs):
        return _cf_ok({"id": "list-new", "name": "syrabit_redirects"})

    monkeypatch.setattr(cfe, "redirect_list_lists", fake_list)
    monkeypatch.setattr(cfe._client(), "post", fake_post)
    result = _run(cfe.redirect_get_or_create_list("syrabit_redirects"))
    assert result["id"] == "list-new"


def test_redirect_add_items_payload(monkeypatch, cf_env):
    import cf_enterprise as cfe
    captured = {}

    async def fake_post(url, *, json=None, **kwargs):
        captured["url"] = url
        captured["body"] = json
        return _cf_ok({"operation_id": "op-1"})

    monkeypatch.setattr(cfe._client(), "post", fake_post)
    items = [{
        "redirect": {
            "source_url": "https://syrabit.ai/old",
            "target_url": "https://syrabit.ai/new",
            "status_code": 301,
        }
    }]
    _run(cfe.redirect_add_items("list-1", items))
    assert "list-1/items" in captured["url"]
    assert captured["body"][0]["redirect"]["status_code"] == 301


def test_redirect_upsert_calls_activate(monkeypatch, cf_env):
    """redirect_upsert must call redirect_activate_ruleset after adding items."""
    import cf_enterprise as cfe
    activated = []

    async def fake_get_or_create(name):
        return {"id": "list-1", "name": name}

    async def fake_add_items(list_id, items):
        return {"operation_id": "op-1"}

    async def fake_activate(list_id, list_name):
        activated.append(list_id)
        return {"id": "rs-1"}

    monkeypatch.setattr(cfe, "redirect_get_or_create_list", fake_get_or_create)
    monkeypatch.setattr(cfe, "redirect_add_items", fake_add_items)
    monkeypatch.setattr(cfe, "redirect_activate_ruleset", fake_activate)

    result = _run(cfe.redirect_upsert(
        "https://syrabit.ai/old",
        "https://syrabit.ai/new",
    ))
    assert result["ok"] is True
    assert "list-1" in activated


def test_redirect_list_all_no_list(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_list():
        return []  # no lists exist

    monkeypatch.setattr(cfe, "redirect_list_lists", fake_list)
    result = _run(cfe.redirect_list_all())
    assert result["list"] is None
    assert result["items"] == []
    assert result["count"] == 0


def test_redirect_list_all_with_items(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_list():
        return [{"id": "list-1", "name": "syrabit_redirects"}]

    async def fake_items(list_id):
        return [{"id": "item-1"}, {"id": "item-2"}]

    monkeypatch.setattr(cfe, "redirect_list_lists", fake_list)
    monkeypatch.setattr(cfe, "redirect_list_items", fake_items)

    result = _run(cfe.redirect_list_all())
    assert result["count"] == 2
    assert result["list"]["id"] == "list-1"


def test_redirect_delete_items(monkeypatch, cf_env):
    import cf_enterprise as cfe
    captured = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"success": True, "result": {"operation_id": "op-del"}}

    async def fake_delete(url, *, json=None, **kwargs):
        captured["body"] = json
        return FakeResp()

    monkeypatch.setattr(cfe._client(), "delete", fake_delete)
    result = _run(cfe.redirect_delete_items("list-1", ["item-a", "item-b"]))
    assert result["operation_id"] == "op-del"
    assert len(captured["body"]["items"]) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Zaraz — Web Tag Management
# ═══════════════════════════════════════════════════════════════════════════════

def test_zaraz_get_config_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("CF_ZONE_ID", raising=False)
    import cf_enterprise as cfe
    result = _run(cfe.zaraz_get_config())
    assert result is None


def test_zaraz_status_shape(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_get_config():
        return {
            "enabled": True,
            "tools": {
                "ga4-1": {"name": "GA4", "type": "Google Analytics 4", "enabled": True},
                "pixel-1": {"name": "Meta Pixel", "type": "Meta Pixel", "enabled": False},
            },
        }

    monkeypatch.setattr(cfe, "zaraz_get_config", fake_get_config)
    status = _run(cfe.zaraz_status())
    assert status["configured"] is True
    assert status["zaraz_enabled"] is True
    assert status["tool_count"] == 2
    tool_names = {t["name"] for t in status["tools"]}
    assert "GA4" in tool_names
    assert "Meta Pixel" in tool_names


def test_zaraz_status_no_config(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_get_config():
        return None

    monkeypatch.setattr(cfe, "zaraz_get_config", fake_get_config)
    status = _run(cfe.zaraz_status())
    assert status["zaraz_enabled"] is False
    assert status["tools"] == []


def test_zaraz_add_tool_injects_entry(monkeypatch, cf_env):
    import cf_enterprise as cfe

    config = {"enabled": True, "tools": {}}
    updated = _run(cfe.zaraz_add_tool(
        config,
        tool_name="Google Analytics 4",
        tool_type="Google Analytics 4",
        tracking_id="G-ABCDEF",
    ))
    assert len(updated["tools"]) == 1
    tool = list(updated["tools"].values())[0]
    assert tool["name"] == "Google Analytics 4"
    assert tool["settings"]["trackingID"] == "G-ABCDEF"
    assert tool["enabled"] is True


def test_zaraz_add_tool_preserves_existing(monkeypatch, cf_env):
    import cf_enterprise as cfe

    config = {
        "tools": {"existing-id": {"name": "Old Tool", "type": "Custom", "enabled": True}},
    }
    updated = _run(cfe.zaraz_add_tool(config, "New Tool", "Meta Pixel"))
    assert len(updated["tools"]) == 2


def test_zaraz_update_config_success(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_put(url, **kwargs):
        return _cf_ok({"updated": True})

    monkeypatch.setattr(cfe._client(), "put", fake_put)
    result = _run(cfe.zaraz_update_config({"tools": {}}))
    assert result["updated"] is True


def test_zaraz_publish_success(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_post(url, **kwargs):
        return _cf_ok({"description": "test publish"})

    monkeypatch.setattr(cfe._client(), "post", fake_post)
    result = _run(cfe.zaraz_publish("test publish"))
    assert result is not None


def test_zaraz_list_histories(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_get(url, **kwargs):
        return _cf_ok([
            {"id": "h1", "description": "deploy 1", "created_at": "2026-01-01T00:00:00Z"},
        ])

    monkeypatch.setattr(cfe._client(), "get", fake_get)
    histories = _run(cfe.zaraz_list_histories())
    assert len(histories) == 1
    assert histories[0]["description"] == "deploy 1"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Speed & Delivery Optimisation
# ═══════════════════════════════════════════════════════════════════════════════

def test_speed_get_all_returns_dict(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_get_setting(name):
        return {"id": name, "value": "on"}

    monkeypatch.setattr(cfe, "speed_get_setting", fake_get_setting)
    result = _run(cfe.speed_get_all())
    assert isinstance(result, dict)
    assert "http3" in result
    assert result["http3"] == "on"


def test_speed_get_all_handles_none(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_get_setting(name):
        return None  # setting unavailable on plan

    monkeypatch.setattr(cfe, "speed_get_setting", fake_get_setting)
    result = _run(cfe.speed_get_all())
    assert all(v is None for v in result.values())


def test_speed_optimize_all_applies_settings(monkeypatch, cf_env):
    import cf_enterprise as cfe
    applied = []

    async def fake_get_setting(name):
        return {"id": name, "value": "off"}

    async def fake_set_setting(name, value):
        applied.append(name)
        return {"id": name, "value": value}

    async def fake_tiered_status():
        return {"value": "on"}

    async def fake_tiered_enable():
        return {"value": "on"}

    monkeypatch.setattr(cfe, "speed_get_setting", fake_get_setting)
    monkeypatch.setattr(cfe, "speed_set_setting", fake_set_setting)
    monkeypatch.setattr(cfe, "tiered_cache_status", fake_tiered_status)
    monkeypatch.setattr(cfe, "tiered_cache_enable", fake_tiered_enable)

    result = _run(cfe.speed_optimize_all())
    assert isinstance(result, dict)
    # Result includes all _SPEED_SETTINGS entries + tiered_caching (separate endpoint)
    assert len(result) == len(cfe._SPEED_SETTINGS) + 1
    assert "tiered_caching" in result
    # All standard settings applied, tiered_caching reported ok
    standard_keys = {s[0] for s in cfe._SPEED_SETTINGS}
    assert all(result[k].get("ok") for k in standard_keys)
    assert result["tiered_caching"].get("ok") is True
    assert set(applied) == standard_keys


def test_speed_optimize_all_not_configured(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ANALYTICS_TOKEN", raising=False)
    monkeypatch.delenv("CF_ZONE_ID", raising=False)
    import cf_enterprise as cfe
    result = _run(cfe.speed_optimize_all())
    assert "error" in result


def test_speed_status_detects_gaps(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_get_all():
        # Return "off" for everything — should show max gaps
        return {s[0]: "off" for s in cfe._SPEED_SETTINGS}

    monkeypatch.setattr(cfe, "speed_get_all", fake_get_all)
    status = _run(cfe.speed_status())
    assert status["gap_count"] > 0
    assert status["fully_optimized"] is False
    assert "http3" in status["gaps"]


def test_speed_status_fully_optimized(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_get_all():
        return {s[0]: s[1]["value"] for s in cfe._SPEED_SETTINGS}

    async def fake_tiered_status():
        return {"value": "on"}

    monkeypatch.setattr(cfe, "speed_get_all", fake_get_all)
    monkeypatch.setattr(cfe, "tiered_cache_status", fake_tiered_status)
    status = _run(cfe.speed_status())
    assert status["fully_optimized"] is True
    assert status["gap_count"] == 0
    assert status["current"]["tiered_caching"] == "on"


def test_speed_set_setting_sends_correct_value(monkeypatch, cf_env):
    import cf_enterprise as cfe
    captured = {}

    async def fake_patch(url, **kwargs):
        captured["url"] = url
        captured["body"] = kwargs.get("json", {})
        return _cf_ok({"id": "http3", "value": "on"})

    monkeypatch.setattr(cfe._client(), "patch", fake_patch)
    result = _run(cfe.speed_set_setting("http3", "on"))
    assert result["value"] == "on"
    assert "http3" in captured["url"]
    assert captured["body"]["value"] == "on"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Error handling / resilience
# ═══════════════════════════════════════════════════════════════════════════════

def test_get_returns_none_on_http_error(monkeypatch, cf_env):
    import cf_enterprise as cfe, httpx

    async def fake_get(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(cfe._client(), "get", fake_get)
    result = _run(cfe._get("https://api.cloudflare.com/client/v4/zones/z/settings/http3"))
    assert result is None


def test_post_returns_none_on_failure_envelope(monkeypatch, cf_env):
    import cf_enterprise as cfe

    async def fake_post(url, **kwargs):
        return _cf_fail([{"code": 1003, "message": "Invalid value"}])

    monkeypatch.setattr(cfe._client(), "post", fake_post)
    result = _run(cfe._post("https://example.com", {}))
    assert result is None


def test_lb_status_unconfigured_returns_safe_defaults(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ANALYTICS_TOKEN", raising=False)
    monkeypatch.delenv("CF_ZONE_ID", raising=False)
    import cf_enterprise as cfe
    status = _run(cfe.lb_status())
    assert status["pools"] == []
    assert status["load_balancers"] == []
    assert status["configured"] is False
