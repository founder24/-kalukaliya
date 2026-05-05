"""Task #383 — CF Web Analytics beacon + config helpers."""
from __future__ import annotations

import pytest


def test_beacon_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr("cf_web_analytics.CF_WEB_ANALYTICS_ON", False)
    monkeypatch.setattr("cf_web_analytics.CF_WEB_ANALYTICS_TOKEN", "tok123")
    from cf_web_analytics import beacon_snippet_html, frontend_config, is_enabled
    assert is_enabled() is False
    assert beacon_snippet_html() == ""
    cfg = frontend_config()
    assert cfg == {"enabled": False, "beacon_url": None, "token": None}


def test_beacon_enabled_renders_token(monkeypatch):
    monkeypatch.setattr("cf_web_analytics.CF_WEB_ANALYTICS_ON", True)
    monkeypatch.setattr("cf_web_analytics.CF_WEB_ANALYTICS_TOKEN", "tok123")
    from cf_web_analytics import beacon_snippet_html, frontend_config, is_enabled
    assert is_enabled() is True
    snippet = beacon_snippet_html()
    assert "static.cloudflareinsights.com/beacon.min.js" in snippet
    assert '"token":"tok123"' in snippet
    cfg = frontend_config()
    assert cfg["enabled"] is True
    assert cfg["token"] == "tok123"


def test_beacon_escapes_token(monkeypatch):
    monkeypatch.setattr("cf_web_analytics.CF_WEB_ANALYTICS_ON", True)
    # An operator pasting a stray quote into the env must not be able
    # to break out of the data-cf-beacon attribute.
    monkeypatch.setattr("cf_web_analytics.CF_WEB_ANALYTICS_TOKEN", 'a"b')
    from cf_web_analytics import beacon_snippet_html
    snippet = beacon_snippet_html()
    assert 'a"b' not in snippet
    assert "&quot;" in snippet


@pytest.mark.asyncio
async def test_fetch_pageviews_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr("cf_web_analytics.CF_WEB_ANALYTICS_ON", False)
    monkeypatch.setattr("cf_web_analytics.CF_WEB_ANALYTICS_TOKEN", "tok")
    from cf_web_analytics import fetch_recent_pageviews
    assert await fetch_recent_pageviews() is None


@pytest.mark.asyncio
async def test_fetch_pageviews_returns_none_when_creds_missing(monkeypatch):
    monkeypatch.setattr("cf_web_analytics.CF_WEB_ANALYTICS_ON", True)
    monkeypatch.setattr("cf_web_analytics.CF_WEB_ANALYTICS_TOKEN", "tok")
    monkeypatch.delenv("CF_ANALYTICS_API_TOKEN", raising=False)
    monkeypatch.delenv("CF_WEB_ANALYTICS_SITE_TAG", raising=False)
    from cf_web_analytics import fetch_recent_pageviews
    assert await fetch_recent_pageviews() is None


@pytest.mark.asyncio
async def test_fetch_pageviews_parses_graphql(monkeypatch):
    monkeypatch.setattr("cf_web_analytics.CF_WEB_ANALYTICS_ON", True)
    monkeypatch.setattr("cf_web_analytics.CF_WEB_ANALYTICS_TOKEN", "tok")
    monkeypatch.setenv("CF_ANALYTICS_API_TOKEN", "api-token")
    monkeypatch.setenv("CF_WEB_ANALYTICS_SITE_TAG", "tag-1")

    class _Resp:
        status_code = 200
        def json(self):
            return {
                "data": {
                    "viewer": {
                        "accounts": [{
                            "rumPageloadEventsAdaptiveGroups": [
                                {"count": 12}, {"count": 8},
                            ],
                        }],
                    },
                },
            }

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            assert headers["Authorization"] == "Bearer api-token"
            assert json["variables"]["siteTag"] == "tag-1"
            return _Resp()

    from cf_web_analytics import fetch_recent_pageviews
    out = await fetch_recent_pageviews(hours=2, http_client_factory=_Client)
    assert out is not None
    assert out["pageviews"] == 20
    assert out["hours"] == 2
