"""Task #383 — integration tests proving the Cloudflare wins are wired
into live request paths, not just standalone modules.

These tests close the six gaps the architect flagged in code review:

  1. AI Gateway response-header counters bump on a chat completion.
  2. Turnstile dependency is enforced on auth signup when the flag is on.
  3. Turnstile dependency is enforced on the password reset request.
  4. KV cache wraps syllabus reads — second call hits the local LRU.
  5. Public ``/api/cf-web-analytics/config`` returns the frontend payload.
  6. ``/admin/vectorize-shadow`` exposes the snapshot + reset endpoints.

Each test stubs only the boundaries it cannot exercise locally (Mongo,
HTTP) and asserts on the observable side-effects of the integration —
counter increments, response codes, cache hit ratios — so a future
refactor that quietly drops the wiring will fail loudly.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Module-wide hygiene: many tests in this file flip CF_* / TURNSTILE_* /
# R2_* env vars and ``importlib.reload(config)`` to make the new values
# stick on the captured ``from config import X`` bindings inside the
# routes. ``monkeypatch.setenv`` restores the env on teardown but does
# NOT re-reload config, so the module-level booleans stay flipped and
# leak into downstream test files (e.g. educator_submit_site, whose
# TestClient peer is 127.0.0.1 and would suddenly fail Turnstile or
# tunnel checks). This autouse fixture snapshots the env, lets the
# test run, then restores the env AND reloads config + the modules
# that cache config booleans, so each test starts from a clean slate
# and downstream files are unaffected by ordering.
_FLAGS_TO_PROTECT = (
    "CF_AIGW_OBS_ON",
    "CF_AIGW_LOG_BLOCKS",
    "TURNSTILE_ON",
    "TURNSTILE_SECRET_KEY",
    "CF_WEB_ANALYTICS_ON",
    "CF_WEB_ANALYTICS_TOKEN",
    "CF_TUNNEL_ONLY_ON",
    "CF_TUNNEL_ALLOWED_IPS",
    "CF_TUNNEL_FAIL_CLOSED_ON_EMPTY",
    "CF_EDGE_CACHE_ON",
    "R2_PRIMARY_ON",
    "VECTORIZE_SHADOW_ON",
    "VECTORIZE_SHADOW_SAMPLE_RATE",
)
_MODULES_TO_REFRESH = (
    "config",
    "turnstile",
    "ai_gateway_observability",
    "cf_tunnel_only",
    "kv_cache",
    "vectorize_shadow",
)


# Capture the pristine env ONCE at module import (before any test in
# this file has had a chance to mutate config). The autouse fixture
# below restores against this baseline rather than against per-test
# state, so a polluting test cannot quietly carry its mutation into
# the next test (or into other test files run after this one).
_PRISTINE_ENV: dict = {k: os.environ.get(k) for k in _FLAGS_TO_PROTECT}


@pytest.fixture(autouse=True)
def _restore_cf_flags_after_test():
    yield
    # Restore env to the pristine, pre-suite snapshot.
    for k, v in _PRISTINE_ENV.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    # Reload config so the module-level booleans match the env again,
    # then drop downstream caches that captured ``from config import X``
    # at their own load time. Best-effort — failures here would only
    # affect this test's cleanup, not the test result.
    try:
        import config as _cfg
        importlib.reload(_cfg)
    except Exception:
        pass
    for name in _MODULES_TO_REFRESH:
        if name == "config":
            continue
        sys.modules.pop(name, None)
    # Routes that read CF_* / TURNSTILE_* booleans at import time also
    # need to be dropped so the next consumer re-imports them with
    # the restored config. ``sys.modules.pop`` alone is NOT enough for
    # submodules — Python's ``from package import submodule`` falls
    # back to the parent package's attribute when the import succeeds
    # without re-executing, so we also delete the attribute on the
    # ``routes`` parent package to force a clean re-import. Without
    # this, downstream test files (e.g. test_educator_submit_site)
    # would receive the stale ``routes.edu_browser`` module whose
    # ``require_turnstile`` reference still binds to the old
    # ``turnstile`` module with ``TURNSTILE_ON=True``.
    _routes_pkg = sys.modules.get("routes")
    for name in (
        "routes.auth", "routes.cf_web_analytics_config",
        "routes.edu_browser", "routes.admin_review_prompts",
        "routes.content",
    ):
        sys.modules.pop(name, None)
        if _routes_pkg is not None:
            attr = name.split(".", 1)[1]
            try:
                delattr(_routes_pkg, attr)
            except AttributeError:
                pass

# Ensure the backend package root is on the path when this file is
# collected from the project root.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# ──────────────────────────────────────────────────────────────────────
# 1. AI Gateway header capture — Azure OpenAI chat path RETIRED (Task #554)
# ──────────────────────────────────────────────────────────────────────
# Task #554 retired providers/azure_openai.py; the chat hot path is now
# Vertex Gemini 2.5 Flash → Workers-AI Llama-3.2-3B and the surviving
# Azure surfaces (Speech / Translator) do not parse cf-aig-* headers
# through the observability counters. The corresponding cf-aig-headers
# regression coverage moved to test_ai_gateway_observability.py.


# ──────────────────────────────────────────────────────────────────────
# 2 + 3. Turnstile dependency enforcement on auth routes
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def turnstile_app(monkeypatch):
    """Build a minimal app exposing only the turnstile-gated auth routes
    so we can assert dependency enforcement without dragging the rest of
    the auth surface (Supabase, mailer, fraud detector) into the test."""
    monkeypatch.setenv("TURNSTILE_ON", "1")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "x" * 16)

    import config as _cfg
    importlib.reload(_cfg)
    import turnstile as _ts
    importlib.reload(_ts)
    _ts.reset_for_tests()
    # Re-import auth so the fresh require_turnstile binds to it.
    sys.modules.pop("routes.auth", None)
    from routes import auth as _auth_module

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(_auth_module.router)
    return TestClient(app), _ts


def test_signup_requires_turnstile_token(turnstile_app):
    client, ts = turnstile_app
    resp = client.post(
        "/auth/signup",
        json={"name": "T", "email": "t@example.com", "password": "Pwd123456!"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "turnstile_required"
    assert ts.snapshot()["verify_missing_token"] >= 1


def test_reset_request_requires_turnstile_token(turnstile_app):
    client, ts = turnstile_app
    resp = client.post(
        "/auth/reset-request",
        json={"email": "t@example.com"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "turnstile_required"


# ──────────────────────────────────────────────────────────────────────
# 4. KV cache wraps syllabus reads
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_syllabus_read_cached_on_second_call(monkeypatch):
    from kv_cache import default_cache, reset_default_for_tests
    reset_default_for_tests()
    cache = default_cache()
    cache.reset()

    from routes import syllabus as _sy
    importlib.reload(_sy)

    call_count = {"n": 0}

    async def _mock_mongo_available():
        return True

    async def _mock_find_one(_filter, _proj):
        call_count["n"] += 1
        return {"board_id": "cbse", "class_id": "10",
                "chapters": [{"id": "c1"}], "found": True}

    fake_db = MagicMock()
    fake_db.syllabi.find_one = AsyncMock(side_effect=_mock_find_one)

    monkeypatch.setattr(_sy, "is_mongo_available", _mock_mongo_available)
    monkeypatch.setattr(_sy, "db", fake_db)

    out1 = await _sy.get_syllabus("cbse", "10")
    out2 = await _sy.get_syllabus("cbse", "10")

    assert out1 == out2
    assert out1.get("found") is True
    # Mongo only consulted once — second call served entirely from LRU.
    assert call_count["n"] == 1, call_count
    snap = cache.snapshot()
    assert snap["hits"] >= 1, snap


# ──────────────────────────────────────────────────────────────────────
# 5. Public CF Web Analytics config endpoint
# ──────────────────────────────────────────────────────────────────────

def test_cf_web_analytics_config_endpoint_returns_frontend_payload(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CF_WEB_ANALYTICS_ON", "1")
    monkeypatch.setenv("CF_WEB_ANALYTICS_TOKEN", "tok-test-123")

    import config as _cfg
    importlib.reload(_cfg)
    import cf_web_analytics as _cfwa
    importlib.reload(_cfwa)
    sys.modules.pop("routes.cf_web_analytics_config", None)
    from routes import cf_web_analytics_config as _ep

    app = FastAPI()
    app.include_router(_ep.router)
    client = TestClient(app)

    resp = client.get("/cf-web-analytics/config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    assert body["token"] == "tok-test-123"
    assert "beacon_url" in body and body["beacon_url"]


def test_cf_web_analytics_config_returns_disabled_when_flag_off(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.delenv("CF_WEB_ANALYTICS_ON", raising=False)
    monkeypatch.delenv("CF_WEB_ANALYTICS_TOKEN", raising=False)

    import config as _cfg
    importlib.reload(_cfg)
    import cf_web_analytics as _cfwa
    importlib.reload(_cfwa)
    sys.modules.pop("routes.cf_web_analytics_config", None)
    from routes import cf_web_analytics_config as _ep

    app = FastAPI()
    app.include_router(_ep.router)
    client = TestClient(app)

    resp = client.get("/cf-web-analytics/config")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


# ──────────────────────────────────────────────────────────────────────
# 6. /admin/vectorize-shadow snapshot + reset
# ──────────────────────────────────────────────────────────────────────

def test_admin_vectorize_shadow_snapshot_and_reset(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import vectorize_shadow as vs
    vs.reset_for_tests()
    vs._bump("queries_mirrored")
    vs._bump("writes_mirrored", 3)

    from routes.admin_vectorize_shadow import router
    from auth_deps import get_admin_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_admin_user] = lambda: {"id": "a", "is_admin": True}
    client = TestClient(app)

    snap_resp = client.get("/admin/vectorize-shadow")
    assert snap_resp.status_code == 200, snap_resp.text
    body = snap_resp.json()
    assert body["queries_mirrored"] == 1
    assert body["writes_mirrored"] == 3
    assert "enabled" in body

    reset_resp = client.post("/admin/vectorize-shadow/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["reset"] is True

    snap2 = client.get("/admin/vectorize-shadow").json()
    assert snap2["queries_mirrored"] == 0
    assert snap2["writes_mirrored"] == 0


# ──────────────────────────────────────────────────────────────────────
# 7. GraphQL fix regression — query parses with `String!`
# ──────────────────────────────────────────────────────────────────────

def test_cf_web_analytics_graphql_uses_capital_String_scalar():
    """Cloudflare's Analytics GraphQL schema is strict about scalar
    capitalisation — ``string!`` (lowercase) is rejected with a 400.
    Lock the file so a future refactor cannot regress."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "cf_web_analytics.py").read_text()
    assert "$siteTag: String!" in src, "GraphQL siteTag scalar must be String! (capital S)"
    assert "$siteTag: string!" not in src, "lowercase string! will be rejected by Cloudflare"


# ──────────────────────────────────────────────────────────────────────
# 8. CF Tunnel-only middleware enforcement
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def _restore_tunnel_config():
    """Snapshot CF_TUNNEL_* env + reload config back to the captured
    state after the test runs, so middleware tests cannot leak a
    flipped flag into downstream suites whose TestClient uses
    127.0.0.1 (not in our test CIDR) and would suddenly 403."""
    import os, importlib
    snap_on = os.environ.get("CF_TUNNEL_ONLY_ON")
    snap_ips = os.environ.get("CF_TUNNEL_ALLOWED_IPS")
    yield
    if snap_on is None:
        os.environ.pop("CF_TUNNEL_ONLY_ON", None)
    else:
        os.environ["CF_TUNNEL_ONLY_ON"] = snap_on
    if snap_ips is None:
        os.environ.pop("CF_TUNNEL_ALLOWED_IPS", None)
    else:
        os.environ["CF_TUNNEL_ALLOWED_IPS"] = snap_ips
    import config as _cfg
    importlib.reload(_cfg)
    sys.modules.pop("cf_tunnel_only", None)


def test_cf_tunnel_only_middleware_passthrough_when_disabled(monkeypatch, _restore_tunnel_config):
    monkeypatch.delenv("CF_TUNNEL_ONLY_ON", raising=False)
    import config as _cfg
    importlib.reload(_cfg)
    sys.modules.pop("cf_tunnel_only", None)
    from cf_tunnel_only import CfTunnelOnlyMiddleware

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/probe")
    def probe():
        return {"ok": True}

    app.add_middleware(CfTunnelOnlyMiddleware)
    client = TestClient(app)
    resp = client.get("/probe")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_cf_tunnel_only_middleware_rejects_unallowed_peer(monkeypatch, _restore_tunnel_config):
    """The middleware must consult the immediate TCP peer, not the
    user-controlled ``cf-connecting-ip`` header. We exercise the ASGI
    layer directly with a forged peer and assert the spoofable header
    cannot bypass enforcement."""
    monkeypatch.setenv("CF_TUNNEL_ONLY_ON", "1")
    monkeypatch.setenv("CF_TUNNEL_ALLOWED_IPS", "10.0.0.0/8")
    import config as _cfg
    importlib.reload(_cfg)
    sys.modules.pop("cf_tunnel_only", None)
    from cf_tunnel_only import CfTunnelOnlyMiddleware

    captured: dict = {}

    async def downstream(scope, receive, send):
        captured["called"] = True
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = CfTunnelOnlyMiddleware(downstream)

    async def _run(peer_ip: str, path: str = "/secret",
                   spoof_header: tuple | None = None) -> dict:
        captured.clear()
        sent: list = []
        headers = [(b"host", b"example.com")]
        if spoof_header:
            headers.append(spoof_header)
        scope = {
            "type": "http", "method": "GET", "path": path,
            "headers": headers, "client": (peer_ip, 12345),
            "scheme": "http", "query_string": b"",
        }

        async def send(msg): sent.append(msg)
        async def recv(): return {"type": "http.request", "body": b"", "more_body": False}
        await mw(scope, recv, send)
        return {"sent": sent, "called": captured.get("called", False)}

    import asyncio
    # 1. Peer outside CIDR → reject (header is ignored)
    out = asyncio.get_event_loop().run_until_complete(_run("8.8.8.8"))
    status = out["sent"][0]["status"]
    assert status == 403 and out["called"] is False

    # 2. Spoofed cf-connecting-ip claiming to be inside CIDR → still
    #    rejected because the immediate peer is what counts.
    out = asyncio.get_event_loop().run_until_complete(
        _run("8.8.8.8", spoof_header=(b"cf-connecting-ip", b"10.1.2.3")),
    )
    assert out["sent"][0]["status"] == 403 and out["called"] is False

    # 3. Spoofed x-forwarded-for claiming to be inside CIDR → still
    #    rejected for the same reason.
    out = asyncio.get_event_loop().run_until_complete(
        _run("8.8.8.8", spoof_header=(b"x-forwarded-for", b"10.1.2.3, 8.8.8.8")),
    )
    assert out["sent"][0]["status"] == 403 and out["called"] is False

    # 4. Real peer inside CIDR → allowed.
    out = asyncio.get_event_loop().run_until_complete(_run("10.1.2.3"))
    assert out["sent"][0]["status"] == 200 and out["called"] is True

    # 5. Open path (health probe) → always allowed, even from outside.
    out = asyncio.get_event_loop().run_until_complete(_run("8.8.8.8", path="/api/healthz"))
    assert out["sent"][0]["status"] == 200 and out["called"] is True


def test_cf_tunnel_only_middleware_warns_when_cidrs_empty(monkeypatch, caplog, _restore_tunnel_config):
    monkeypatch.setenv("CF_TUNNEL_ONLY_ON", "1")
    monkeypatch.setenv("CF_TUNNEL_ALLOWED_IPS", "")
    import config as _cfg
    importlib.reload(_cfg)
    sys.modules.pop("cf_tunnel_only", None)
    import cf_tunnel_only as _cf
    importlib.reload(_cf)
    import logging
    with caplog.at_level(logging.WARNING, logger="cf_tunnel_only"):
        # Instantiate the middleware directly — FastAPI's add_middleware
        # is lazy and doesn't construct the wrapper until startup, which
        # is where our warning fires.
        _cf.CfTunnelOnlyMiddleware(lambda *a, **k: None)
    assert any("CF_TUNNEL_ONLY_ON=1 but CF_TUNNEL_ALLOWED_IPS" in r.message
               for r in caplog.records), [r.message for r in caplog.records]


def test_cf_tunnel_only_default_cidrs_cover_ipv6_cf_edge(monkeypatch, _restore_tunnel_config):
    """Cloudflare's edge fronts both IPv4 and IPv6 traffic, and many
    managed origins (Cloud Run, Railway) terminate as IPv6 by default.
    The shipped default ``CF_TUNNEL_ALLOWED_IPS`` MUST therefore include
    the published CF IPv6 ranges, otherwise valid v6 traffic gets a 403
    on flag flip. We assert that an IPv6 address from each documented
    CF v6 prefix is accepted by the parsed default."""
    # Wipe any operator override so the config-default is what we test.
    monkeypatch.delenv("CF_TUNNEL_ALLOWED_IPS", raising=False)
    monkeypatch.setenv("CF_TUNNEL_ONLY_ON", "1")
    import config as _cfg
    importlib.reload(_cfg)
    sys.modules.pop("cf_tunnel_only", None)
    import cf_tunnel_only as _cf
    importlib.reload(_cf)

    cidrs = _cf._parse_cidrs(_cfg.CF_TUNNEL_ALLOWED_IPS)
    # One sample address from each documented Cloudflare IPv6 prefix.
    samples = [
        "2400:cb00::1", "2606:4700::1", "2803:f800::1",
        "2405:b500::1", "2405:8100::1", "2a06:98c0::1", "2c0f:f248::1",
    ]
    for addr in samples:
        assert _cf._ip_in_cidrs(addr, cidrs), (
            f"default CF_TUNNEL_ALLOWED_IPS does not cover {addr} — "
            "IPv6 origins will 403 valid CF traffic on flag flip"
        )


def test_cf_tunnel_only_fail_closed_on_empty_rejects_everything(
    monkeypatch, _restore_tunnel_config,
):
    """``CF_TUNNEL_FAIL_CLOSED_ON_EMPTY=1`` flips the empty-CIDR
    behaviour from passthrough+warn to reject-all. Operators who
    prefer lock-down over availability during misconfiguration use
    this to ensure a missing CIDR list does not silently expose the
    origin."""
    monkeypatch.setenv("CF_TUNNEL_ONLY_ON", "1")
    monkeypatch.setenv("CF_TUNNEL_ALLOWED_IPS", "")
    monkeypatch.setenv("CF_TUNNEL_FAIL_CLOSED_ON_EMPTY", "1")
    import config as _cfg
    importlib.reload(_cfg)
    sys.modules.pop("cf_tunnel_only", None)
    from cf_tunnel_only import CfTunnelOnlyMiddleware

    captured: dict = {}

    async def downstream(scope, receive, send):
        captured["called"] = True
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = CfTunnelOnlyMiddleware(downstream)

    async def _run(peer_ip: str, path: str = "/secret") -> dict:
        captured.clear()
        sent: list = []
        scope = {
            "type": "http", "method": "GET", "path": path,
            "headers": [(b"host", b"example.com")],
            "client": (peer_ip, 12345),
            "scheme": "http", "query_string": b"",
        }

        async def send(msg): sent.append(msg)
        async def recv(): return {"type": "http.request", "body": b"",
                                  "more_body": False}
        await mw(scope, recv, send)
        return {"sent": sent, "called": captured.get("called", False)}

    import asyncio
    # 1. Loopback peer → still rejected because CIDRs are empty AND
    #    fail-closed is on.
    out = asyncio.get_event_loop().run_until_complete(_run("127.0.0.1"))
    assert out["sent"][0]["status"] == 403 and out["called"] is False
    # 2. Public peer → rejected.
    out = asyncio.get_event_loop().run_until_complete(_run("8.8.8.8"))
    assert out["sent"][0]["status"] == 403 and out["called"] is False
    # 3. Open path (health probe) → still allowed even in fail-closed
    #    mode, otherwise the lockout would be unrecoverable.
    out = asyncio.get_event_loop().run_until_complete(
        _run("8.8.8.8", path="/api/healthz"),
    )
    assert out["sent"][0]["status"] == 200 and out["called"] is True


# ──────────────────────────────────────────────────────────────────────
# 9. Turnstile dependency on /edu/request-site (post-review remediation)
# ──────────────────────────────────────────────────────────────────────

def test_edu_request_site_requires_turnstile(monkeypatch):
    monkeypatch.setenv("TURNSTILE_ON", "1")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "x" * 16)
    import config as _cfg
    importlib.reload(_cfg)
    import turnstile as _ts
    importlib.reload(_ts)
    _ts.reset_for_tests()
    sys.modules.pop("routes.edu_browser", None)
    from routes import edu_browser as _edu

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(_edu.router)
    client = TestClient(app)

    resp = client.post("/edu/request-site",
                       json={"domain": "example.org", "reason": "test"})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "turnstile_required"


# ──────────────────────────────────────────────────────────────────────
# 10. Turnstile dependency on /analytics/review-prompt-event
# ──────────────────────────────────────────────────────────────────────

def test_review_prompt_event_requires_turnstile(monkeypatch):
    monkeypatch.setenv("TURNSTILE_ON", "1")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "x" * 16)
    import config as _cfg
    importlib.reload(_cfg)
    import turnstile as _ts
    importlib.reload(_ts)
    _ts.reset_for_tests()
    sys.modules.pop("routes.admin_review_prompts", None)
    from routes import admin_review_prompts as _arp

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(_arp.router)
    client = TestClient(app)

    resp = client.post("/analytics/review-prompt-event",
                       json={"event": "shown", "reason": "test"})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "turnstile_required"


# ──────────────────────────────────────────────────────────────────────
# 11. R2 primary read URL helper honours R2_PRIMARY_ON
# ──────────────────────────────────────────────────────────────────────

def test_r2_primary_read_url_falls_back_when_flag_off(monkeypatch):
    monkeypatch.delenv("R2_PRIMARY_ON", raising=False)
    import config as _cfg
    importlib.reload(_cfg)
    import r2_storage as _r2
    importlib.reload(_r2)

    url = _r2.r2_primary_read_url(
        "chapters/c1.pdf",
        s3_fallback_url="https://s3.example.com/chapters/c1.pdf",
    )
    assert url == "https://s3.example.com/chapters/c1.pdf"


def test_r2_primary_read_url_serves_r2_when_flag_on(monkeypatch):
    monkeypatch.setenv("R2_PRIMARY_ON", "1")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "AKIA-test")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret-test")
    monkeypatch.setenv("R2_BUCKET_NAME", "syrabit-media")
    monkeypatch.setenv("CF_AI_GATEWAY_ACCOUNT_ID", "test-acct")
    monkeypatch.setenv("R2_PUBLIC_URL", "https://media.example.com")
    import config as _cfg
    importlib.reload(_cfg)
    import r2_storage as _r2
    importlib.reload(_r2)

    url = _r2.r2_primary_read_url(
        "chapters/c1.pdf",
        s3_fallback_url="https://s3.example.com/chapters/c1.pdf",
    )
    assert url == "https://media.example.com/chapters/c1.pdf", url


# ──────────────────────────────────────────────────────────────────────
# 12. Vectorize shadow defaults to 100% mirror (parity-grade signal)
# ──────────────────────────────────────────────────────────────────────

def test_vectorize_shadow_default_sample_rate_is_full_mirror():
    import vectorize_shadow as _vs
    importlib.reload(_vs)
    primary = MagicMock()
    primary.name = "primary-test"
    shadow = MagicMock()
    shadow.is_configured.return_value = True
    wrapped = _vs.ShadowRetriever(primary, shadow, enabled=True)
    assert wrapped._sample_rate == 1.0, (
        "default sample rate must be 1.0 so recall@k is parity-grade, "
        "not a sampled estimate"
    )


# ──────────────────────────────────────────────────────────────────────
# 13. AI Gateway guardrail block emits a structured warning log
# ──────────────────────────────────────────────────────────────────────

def test_aig_guardrail_block_emits_structured_warning(monkeypatch, caplog):
    monkeypatch.setenv("CF_AIGW_OBS_ON", "1")
    import config as _cfg
    importlib.reload(_cfg)
    import ai_gateway_observability as obs
    importlib.reload(obs)
    obs.reset_for_tests()

    headers = {
        "cf-aig-cache-status": "miss",
        "cf-aig-guardrail-action": "block",
        "cf-aig-guardrail-category": "pii",
        "cf-aig-log-id": "log-blk-1",
    }
    import logging
    with caplog.at_level(logging.WARNING, logger="ai_gateway_observability"):
        obs.record_aig_response(headers, provider="openai", model="gpt-4o-mini")
    msgs = [r.message for r in caplog.records]
    assert any("guardrail block" in m and "pii" in m and "log-blk-1" in m
               for m in msgs), msgs


# ──────────────────────────────────────────────────────────────────────
# 14. KV mirror wraps /content/chapters/{subject_id}, cross-pod read
# path uses async cache.get(...), and admin invalidate purges the
# mirror.
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chapter_index_mirrored_to_kv(monkeypatch):
    from kv_cache import default_cache, reset_default_for_tests
    reset_default_for_tests()
    cache = default_cache()
    cache.reset()

    sys.modules.pop("routes.content", None)
    from routes import content as _c
    # Bust the in-process LRU so we exercise the KV path.
    try:
        _c._content_cache.clear()
    except Exception:
        pass

    async def _mock_mongo_available():
        return True

    chapters_doc = [
        {"id": "c1", "title": "Algebra", "subject_id": "s-test", "order_index": 1},
    ]

    class _Cursor:
        def sort(self, *a, **k): return self
        async def to_list(self, _n): return chapters_doc

    fake_db = MagicMock()
    fake_db.chapters.find = MagicMock(return_value=_Cursor())

    monkeypatch.setattr(_c, "is_mongo_available", _mock_mongo_available)
    monkeypatch.setattr(_c, "db", fake_db)

    out = await _c.get_chapters("s-test")
    assert out and out[0]["title"] == "Algebra"
    # KV mirror was populated and is reachable through the async path
    # — this is what a sibling pod (cold LRU) would see.
    kv_hit = await cache.get("chapters/s-test")
    assert kv_hit is not None and kv_hit[0]["id"] == "c1"


@pytest.mark.asyncio
async def test_chapter_index_cross_pod_read_uses_async_kv(monkeypatch):
    """Simulate a cold pod: empty in-process LRU, but KV holds the
    chapter index. The route must hit the async ``cache.get(...)``
    path and serve from KV without consulting Mongo at all."""
    from kv_cache import default_cache, reset_default_for_tests
    reset_default_for_tests()
    cache = default_cache()
    cache.reset()

    sys.modules.pop("routes.content", None)
    from routes import content as _c
    try:
        _c._content_cache.clear()
    except Exception:
        pass

    pre_baked = [{"id": "c-prebaked", "title": "Geometry",
                  "subject_id": "s-cold", "order_index": 1}]
    await cache.set("chapters/s-cold", pre_baked, ttl_s=300)
    # Bust the in-process LRU again so the only surviving copy lives
    # in the KV-backed path. (We rely on the KV mirror being the
    # cross-pod survival mechanism; in tests with no KV worker
    # configured, the local LRU set above stands in for it.)

    async def _explode():  # Mongo must NOT be called in this test
        raise AssertionError("Mongo consulted on a KV cache hit")

    fake_db = MagicMock()
    fake_db.chapters.find = MagicMock(side_effect=AssertionError(
        "chapters.find called on a KV-mirror hit"))
    monkeypatch.setattr(_c, "is_mongo_available", _explode)
    monkeypatch.setattr(_c, "db", fake_db)

    out = await _c.get_chapters("s-cold")
    assert out and out[0]["id"] == "c-prebaked", out


@pytest.mark.asyncio
async def test_invalidate_chapters_kv_purges_mirror():
    from kv_cache import default_cache, reset_default_for_tests
    reset_default_for_tests()
    cache = default_cache()
    cache.reset()

    sys.modules.pop("routes.content", None)
    from routes import content as _c
    try:
        _c._content_cache.clear()
    except Exception:
        pass
    await cache.set("chapters/s-victim",
                    [{"id": "c-old"}], ttl_s=300)
    assert await cache.get("chapters/s-victim") is not None

    await _c.invalidate_chapters_kv("s-victim")
    assert await cache.get("chapters/s-victim") is None


# ──────────────────────────────────────────────────────────────────────
# 15. r2_primary_read_url is wired into the upload return paths
# ──────────────────────────────────────────────────────────────────────

def test_admin_content_uploads_route_url_through_r2_primary_helper():
    """The R2 primary helper is dead code unless something in the
    request path actually calls it. Lock the wiring so a future
    refactor can't quietly drop the centralisation."""
    src = (Path(__file__).resolve().parent.parent
           / "routes" / "admin_content.py").read_text()
    assert "r2_primary_read_url" in src, (
        "admin_content.py must import + use r2_primary_read_url so "
        "Cloudflare R2 read-path migration is centralised"
    )
    # Both upload helpers (image + PDF) must route their emitted URLs
    # through the helper.
    assert src.count("r2_primary_read_url(") >= 3, (
        "expected at least 3 r2_primary_read_url(...) call sites "
        "in admin_content.py (R2 image, Supabase image fallback, "
        "PDF upload)"
    )


# ──────────────────────────────────────────────────────────────────────
# 16. Admin chapter writes invalidate the cross-pod KV mirror
# ──────────────────────────────────────────────────────────────────────

def test_admin_chapter_writes_invalidate_kv_mirror():
    """Lock the wiring: every chapter mutation in admin_content.py
    must purge the KV mirror so cross-pod readers don't keep serving
    stale chapter indexes."""
    src = (Path(__file__).resolve().parent.parent
           / "routes" / "admin_content.py").read_text()
    assert src.count("invalidate_chapters_kv(") >= 3, (
        "expected invalidate_chapters_kv() to be called from at "
        "least 3 chapter-write sites (insert, update, bulk delete)"
    )
