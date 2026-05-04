"""Task #258 — CF Gateway BYOK smoke-test for every PROVIDER_PRIORITY feature.

Tests for the ``GET /admin/credits/smoke-test`` endpoint and its supporting
helpers in ``routes.admin_credits``:

* ``_PROVIDER_PROBE_SPECS`` — registered for all 7 CF-slug providers;
  each entry has method/path/body/extra_headers/description.
* ``_build_probe_headers`` — merges provider extra_headers with CF BYOK
  headers (cf-aig-byok-key, cf-aig-cache-ttl, cf-aig-authorization).
* ``_probe_provider`` — makes a minimal real API call (GET or POST) through
  the CF AI Gateway slug; returns (http_status, latency_ms); returns (0, ms)
  on connection error / timeout; never raises.
* ``_run_feature_smoke`` — calls select_provider() + _probe_provider for
  CF-slugged providers with a spec; marks no-slug or no-spec providers "skip".
* ``_post_smoke_slack_alert`` — best-effort POST to SMOKE_TEST_SLACK_WEBHOOK;
  no-op when unset; never raises on transport errors.
* ``admin_credits_smoke_test`` — endpoint: every PROVIDER_PRIORITY feature
  runs concurrently; overall="fail" when any feature fails; schedules Slack
  alert. Counts are derived from ``len(PROVIDER_PRIORITY)`` (Task #368) so
  the panel doesn't drift when features are added or removed.
* ``SMOKE_TEST_SLACK_WEBHOOK_ENV`` — constant in slack_alerter_config.
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logging
logging.disable(logging.CRITICAL)

import routes.admin_credits as mod
from routes.slack_alerter_config import SMOKE_TEST_SLACK_WEBHOOK_ENV


# ── Fixtures ──────────────────────────────────────────────────────────────────

class _Resp:
    """Minimal httpx-response stub."""
    def __init__(self, status_code: int = 200, text: str = "ok"):
        self.status_code = status_code
        self.text = text


def _make_httpx_client(get_status: int = 200, post_status: int = 200):
    """Return a fake async context-manager httpx.AsyncClient.

    Records the last ``method``, ``url``, ``headers``, and ``json`` seen.
    """
    class _Client:
        captured: dict = {}

        def __init__(self, *a, **kw):
            _Client.captured = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, **kw):
            _Client.captured = {"method": "GET", "url": url, "headers": headers or {}}
            return _Resp(get_status)

        async def post(self, url, headers=None, json=None, **kw):
            _Client.captured = {"method": "POST", "url": url,
                                 "headers": headers or {}, "json": json}
            return _Resp(post_status)

    return _Client


# ── SMOKE_TEST_SLACK_WEBHOOK_ENV constant ─────────────────────────────────────

def test_smoke_test_slack_webhook_env_constant():
    """SMOKE_TEST_SLACK_WEBHOOK_ENV must equal 'SMOKE_TEST_SLACK_WEBHOOK'."""
    assert SMOKE_TEST_SLACK_WEBHOOK_ENV == "SMOKE_TEST_SLACK_WEBHOOK"


def test_smoke_test_slack_webhook_env_in_slack_alerter_config():
    """The constant must be importable from routes.slack_alerter_config."""
    from routes.slack_alerter_config import SMOKE_TEST_SLACK_WEBHOOK_ENV as _env
    assert _env  # non-empty string


# ── _PROVIDER_PROBE_SPECS coverage ───────────────────────────────────────────

_CF_SLUG_PROVIDERS = frozenset(
    {"cohere", "assemblyai", "elevenlabs", "sarvam", "bedrock", "azure_openai"}
)


def test_probe_specs_registered_for_all_cf_slug_providers():
    """All 6 CF-slug PROVIDER_PRIORITY providers must have a probe spec."""
    specs = mod._PROVIDER_PROBE_SPECS
    missing = _CF_SLUG_PROVIDERS - set(specs.keys())
    assert not missing, f"Missing probe specs for: {missing}"


def test_probe_specs_have_required_fields():
    """Every probe spec must define method, path, extra_headers, description."""
    for provider, spec in mod._PROVIDER_PROBE_SPECS.items():
        assert "method" in spec, f"{provider}: missing 'method'"
        assert "path" in spec, f"{provider}: missing 'path'"
        assert "extra_headers" in spec, f"{provider}: missing 'extra_headers'"
        assert "description" in spec, f"{provider}: missing 'description'"
        assert spec["method"] in ("GET", "POST"), (
            f"{provider}: method must be GET or POST, got {spec['method']!r}"
        )


def test_probe_specs_post_providers_have_body():
    """Specs with method=POST must define a non-None body."""
    for provider, spec in mod._PROVIDER_PROBE_SPECS.items():
        if spec["method"] == "POST":
            assert spec.get("body") is not None, (
                f"{provider}: POST spec must include a non-None body"
            )


def test_probe_specs_get_providers():
    """assemblyai, elevenlabs must use GET (lightweight list endpoints)."""
    for p in ("assemblyai", "elevenlabs"):
        assert mod._PROVIDER_PROBE_SPECS[p]["method"] == "GET", (
            f"{p} should use GET for its lightweight list endpoint"
        )


def test_probe_specs_post_providers():
    """sarvam, bedrock, azure_openai, cohere must use POST (LLM/embed calls)."""
    for p in ("sarvam", "bedrock", "azure_openai", "cohere"):
        assert mod._PROVIDER_PROBE_SPECS[p]["method"] == "POST", (
            f"{p} should use POST for its minimal operation call"
        )


def test_probe_spec_cohere_uses_empty_authorization():
    """cohere spec must send Authorization: '' so CF BYOK substitutes the key."""
    spec = mod._PROVIDER_PROBE_SPECS["cohere"]
    assert spec["extra_headers"].get("Authorization") == "", (
        "cohere: Authorization must be '' (empty) to trigger CF BYOK"
    )


def test_probe_spec_assemblyai_uses_empty_authorization():
    """assemblyai spec must send Authorization: '' so CF BYOK substitutes the key."""
    spec = mod._PROVIDER_PROBE_SPECS["assemblyai"]
    assert spec["extra_headers"].get("Authorization") == "", (
        "assemblyai: Authorization must be '' (empty) to trigger CF BYOK"
    )


def test_probe_spec_elevenlabs_uses_xi_api_key():
    """elevenlabs spec must send xi-api-key: '' so CF BYOK substitutes the key."""
    spec = mod._PROVIDER_PROBE_SPECS["elevenlabs"]
    assert spec["extra_headers"].get("xi-api-key") == "", (
        "elevenlabs: xi-api-key must be '' (empty) to trigger CF BYOK"
    )


def test_probe_spec_sarvam_uses_api_subscription_key():
    """sarvam spec must send api-subscription-key: '' for CF BYOK."""
    spec = mod._PROVIDER_PROBE_SPECS["sarvam"]
    assert spec["extra_headers"].get("api-subscription-key") == "", (
        "sarvam: api-subscription-key must be '' (empty) to trigger CF BYOK"
    )


def test_probe_spec_bedrock_no_upstream_auth_header():
    """bedrock spec must NOT send an upstream auth header (CF handles SigV4 BYOK)."""
    spec = mod._PROVIDER_PROBE_SPECS["bedrock"]
    extras = spec.get("extra_headers", {})
    assert "Authorization" not in extras, (
        "bedrock: upstream Authorization must NOT be set — CF handles SigV4 BYOK"
    )
    assert "api-key" not in extras, (
        "bedrock: api-key header must NOT be set for bedrock"
    )


def test_probe_spec_azure_openai_uses_empty_authorization_and_api_key():
    """azure_openai spec must send api-key + empty Authorization for CF BYOK."""
    spec = mod._PROVIDER_PROBE_SPECS["azure_openai"]
    extras = spec["extra_headers"]
    assert "api-key" in extras, "azure_openai: api-key must be present"
    assert extras.get("Authorization") == "", (
        "azure_openai: Authorization must be '' (empty) to trigger CF BYOK"
    )


def test_probe_spec_sarvam_body_max_tokens_1():
    """sarvam POST body must use max_tokens=1 to minimise cost."""
    spec = mod._PROVIDER_PROBE_SPECS["sarvam"]
    assert spec["body"]["max_tokens"] == 1


def test_probe_spec_bedrock_converse_path():
    """bedrock path must contain '/model/' and '/converse'."""
    path = mod._PROVIDER_PROBE_SPECS["bedrock"]["path"]
    assert "/model/" in path, f"bedrock path should contain /model/, got {path!r}"
    assert "/converse" in path, f"bedrock path should contain /converse, got {path!r}"


def test_probe_spec_bedrock_body_max_tokens_1():
    """bedrock POST body must use maxTokens=1 to minimise cost."""
    body = mod._PROVIDER_PROBE_SPECS["bedrock"]["body"]
    assert body["inferenceConfig"]["maxTokens"] == 1


def test_probe_spec_azure_chat_completions_path():
    """azure_openai path must include /chat/completions and api-version param."""
    path = mod._PROVIDER_PROBE_SPECS["azure_openai"]["path"]
    assert "chat/completions" in path, (
        f"azure_openai path should contain chat/completions, got {path!r}"
    )
    assert "api-version" in path, (
        f"azure_openai path should include api-version query param, got {path!r}"
    )


def test_probe_spec_azure_body_max_tokens_1():
    """azure_openai POST body must use max_tokens=1 to minimise cost."""
    body = mod._PROVIDER_PROBE_SPECS["azure_openai"]["body"]
    assert body["max_tokens"] == 1


# ── _build_probe_headers ──────────────────────────────────────────────────────

def test_build_probe_headers_adds_byok_key():
    """_build_probe_headers must always set cf-aig-byok-key: 'true'."""
    with patch("config.CF_AI_GATEWAY_TOKEN", ""):
        h = mod._build_probe_headers("cohere", {"Authorization": ""})
    assert h["cf-aig-byok-key"] == "true"


def test_build_probe_headers_merges_extra_headers():
    """Provider-specific extra_headers must be present in the merged result."""
    with patch("config.CF_AI_GATEWAY_TOKEN", ""):
        h = mod._build_probe_headers("elevenlabs", {"xi-api-key": "", "Content-Type": "application/json"})
    assert h.get("xi-api-key") == ""
    assert h.get("Content-Type") == "application/json"
    assert h["cf-aig-byok-key"] == "true"


def test_build_probe_headers_adds_authorization_when_token_set():
    """cf-aig-authorization must be added when CF_AI_GATEWAY_TOKEN is set."""
    with patch("config.CF_AI_GATEWAY_TOKEN", "my-gateway-token"):
        h = mod._build_probe_headers("cohere", {})
    assert h.get("cf-aig-authorization") == "Bearer my-gateway-token"


def test_build_probe_headers_no_authorization_when_token_empty():
    """No cf-aig-authorization header when CF_AI_GATEWAY_TOKEN is unset/empty."""
    with patch("config.CF_AI_GATEWAY_TOKEN", ""):
        h = mod._build_probe_headers("cohere", {})
    assert "cf-aig-authorization" not in h


def test_build_probe_headers_sets_cache_ttl():
    """cf-aig-cache-ttl must be set (uses CF_CACHE_TTL from config)."""
    with patch("config.CF_AI_GATEWAY_TOKEN", ""), \
         patch("config.CF_CACHE_TTL", 42):
        h = mod._build_probe_headers("cohere", {})
    assert h.get("cf-aig-cache-ttl") == "42"


# ── _probe_provider ───────────────────────────────────────────────────────────

def test_probe_provider_get_returns_200():
    """GET probe returning 200 → (200, positive_latency_ms)."""
    spec = {
        "method": "GET",
        "path": "/v1/models",
        "body": None,
        "extra_headers": {"xi-api-key": ""},
    }
    _Client = _make_httpx_client(get_status=200)
    with patch("httpx.AsyncClient", _Client), \
         patch("config.CF_AI_GATEWAY_TOKEN", ""):
        status, latency = asyncio.run(
            mod._probe_provider(
                "elevenlabs",
                "https://gateway.ai.cloudflare.com/v1/a/g/elevenlabs/v1",
                spec,
            )
        )
    assert status == 200
    assert latency >= 0


def test_probe_provider_post_returns_200():
    """POST probe returning 200 → (200, positive_latency_ms)."""
    spec = {
        "method": "POST",
        "path": "/embed",
        "body": {"model": "embed-multilingual-v3.0", "texts": ["smoke"],
                 "input_type": "search_query", "embedding_types": ["float"]},
        "extra_headers": {"Authorization": ""},
    }
    _Client = _make_httpx_client(post_status=200)
    with patch("httpx.AsyncClient", _Client), \
         patch("config.CF_AI_GATEWAY_TOKEN", ""):
        status, latency = asyncio.run(
            mod._probe_provider(
                "cohere",
                "https://gateway.ai.cloudflare.com/v1/a/g/cohere/v1",
                spec,
            )
        )
    assert status == 200


def test_probe_provider_sends_post_body():
    """POST probe must send spec['body'] as JSON."""
    spec = {
        "method": "POST",
        "path": "/v1/chat/completions",
        "body": {"model": "sarvam-m",
                 "messages": [{"role": "user", "content": "hi"}],
                 "max_tokens": 1},
        "extra_headers": {"api-subscription-key": ""},
    }
    _Client = _make_httpx_client(post_status=200)
    with patch("httpx.AsyncClient", _Client), \
         patch("config.CF_AI_GATEWAY_TOKEN", ""):
        asyncio.run(
            mod._probe_provider(
                "sarvam",
                "https://gateway.ai.cloudflare.com/v1/a/g/custom-sarvam",
                spec,
            )
        )
    captured_json = _Client.captured.get("json", {})
    assert captured_json.get("max_tokens") == 1
    assert captured_json.get("model") == "sarvam-m"


def test_probe_provider_builds_correct_url():
    """URL must be gateway_url + spec['path']."""
    spec = {
        "method": "GET",
        "path": "/v2/transcript",
        "body": None,
        "extra_headers": {"Authorization": ""},
    }
    _Client = _make_httpx_client(get_status=200)
    with patch("httpx.AsyncClient", _Client), \
         patch("config.CF_AI_GATEWAY_TOKEN", ""):
        asyncio.run(
            mod._probe_provider(
                "assemblyai",
                "https://gateway.ai.cloudflare.com/v1/a/g/assemblyai/v2",
                spec,
            )
        )
    called_url = _Client.captured.get("url", "")
    assert called_url.endswith("/v2/transcript"), (
        f"Expected URL ending in /v2/transcript, got {called_url!r}"
    )


def test_probe_provider_returns_non_200_status():
    """Non-200 responses are forwarded as-is, not raised."""
    spec = {"method": "GET", "path": "/v1/models", "body": None,
            "extra_headers": {"xi-api-key": ""}}
    _Client = _make_httpx_client(get_status=401)
    with patch("httpx.AsyncClient", _Client), \
         patch("config.CF_AI_GATEWAY_TOKEN", ""):
        status, _ = asyncio.run(
            mod._probe_provider(
                "elevenlabs",
                "https://gateway.ai.cloudflare.com/v1/a/g/elevenlabs/v1",
                spec,
            )
        )
    assert status == 401


def test_probe_provider_returns_0_on_connection_error():
    """Connection/timeout errors → status=0, never raises."""
    class _BoomClient:
        def __init__(self, *a, **kw): pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            raise ConnectionError("network dead")

        async def post(self, *a, **kw):
            raise ConnectionError("network dead")

    spec = {"method": "GET", "path": "/v1/models", "body": None,
            "extra_headers": {}}
    with patch("httpx.AsyncClient", _BoomClient), \
         patch("config.CF_AI_GATEWAY_TOKEN", ""):
        status, latency = asyncio.run(
            mod._probe_provider(
                "elevenlabs",
                "https://gateway.ai.cloudflare.com/v1/a/g/elevenlabs/v1",
                spec,
            )
        )
    assert status == 0
    assert latency >= 0


def test_probe_provider_includes_byok_headers_in_request():
    """Probe must include cf-aig-byok-key in the outgoing request headers."""
    spec = {
        "method": "GET",
        "path": "/v1/models",
        "body": None,
        "extra_headers": {"xi-api-key": ""},
    }
    _Client = _make_httpx_client(get_status=200)
    with patch("httpx.AsyncClient", _Client), \
         patch("config.CF_AI_GATEWAY_TOKEN", "tok"):
        asyncio.run(
            mod._probe_provider(
                "elevenlabs",
                "https://gateway.ai.cloudflare.com/v1/a/g/elevenlabs/v1",
                spec,
            )
        )
    sent_headers = _Client.captured.get("headers", {})
    assert sent_headers.get("cf-aig-byok-key") == "true"
    assert sent_headers.get("cf-aig-authorization") == "Bearer tok"


# ── _run_feature_smoke ────────────────────────────────────────────────────────

def test_run_feature_smoke_pass_when_slug_exists_and_200():
    """Feature with a CF slug + probe spec returns outcome='pass' on HTTP 200."""
    _Client = _make_httpx_client(get_status=200)
    with patch("llm.select_provider", return_value="elevenlabs"), \
         patch("config.CF_GATEWAY_ENABLED", True), \
         patch("config.cf_gateway_url",
               return_value="https://gateway.ai.cloudflare.com/v1/a/g/elevenlabs/v1"), \
         patch("httpx.AsyncClient", _Client), \
         patch("config.CF_AI_GATEWAY_TOKEN", ""):
        result = asyncio.run(mod._run_feature_smoke("tts"))
    assert result["outcome"] == "pass"
    assert result["status"] == 200
    assert result["feature"] == "tts"
    assert result["provider"] == "elevenlabs"
    assert result["error"] == ""


def test_run_feature_smoke_fail_when_probe_returns_503():
    """503 response → outcome='fail'."""
    _Client = _make_httpx_client(get_status=503)
    with patch("llm.select_provider", return_value="elevenlabs"), \
         patch("config.CF_GATEWAY_ENABLED", True), \
         patch("config.cf_gateway_url",
               return_value="https://gateway.ai.cloudflare.com/v1/a/g/elevenlabs/v1"), \
         patch("httpx.AsyncClient", _Client), \
         patch("config.CF_AI_GATEWAY_TOKEN", ""):
        result = asyncio.run(mod._run_feature_smoke("tts"))
    assert result["outcome"] == "fail"
    assert result["status"] == 503
    assert "503" in result["error"]


def test_run_feature_smoke_fail_when_connection_error():
    """Connection error (status=0) → outcome='fail'."""
    class _BoomClient:
        def __init__(self, *a, **kw): pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            raise ConnectionError("boom")

        async def post(self, *a, **kw):
            raise ConnectionError("boom")

    with patch("llm.select_provider", return_value="sarvam"), \
         patch("config.CF_GATEWAY_ENABLED", True), \
         patch("config.cf_gateway_url",
               return_value="https://gateway.ai.cloudflare.com/v1/a/g/custom-sarvam"), \
         patch("httpx.AsyncClient", _BoomClient), \
         patch("config.CF_AI_GATEWAY_TOKEN", ""):
        result = asyncio.run(mod._run_feature_smoke("assamese_rag_chat"))
    assert result["outcome"] == "fail"
    assert result["status"] == 0
    assert "connection error" in result["error"]


def test_run_feature_smoke_skip_when_no_cf_slug():
    """Provider without a CF slug (e.g. vertex) → outcome='skip', no HTTP call."""
    probed = {"called": False}
    _orig_probe = mod._probe_provider

    async def _spy_probe(*a, **kw):
        probed["called"] = True
        return await _orig_probe(*a, **kw)

    with patch("llm.select_provider", return_value="vertex"), \
         patch("config.CF_GATEWAY_ENABLED", True), \
         patch("config.cf_gateway_url", return_value=""), \
         patch.object(mod, "_probe_provider", _spy_probe):
        result = asyncio.run(mod._run_feature_smoke("vision"))
    assert result["outcome"] == "skip"
    assert result["gateway_url"] is None
    assert probed["called"] is False


def test_run_feature_smoke_skip_when_no_probe_spec():
    """Provider with a CF slug but no probe spec → outcome='skip'."""
    probed = {"called": False}

    async def _spy_probe(*a, **kw):
        probed["called"] = True
        return (200, 10.0)

    with patch("llm.select_provider", return_value="workers_ai"), \
         patch("config.CF_GATEWAY_ENABLED", True), \
         patch("config.cf_gateway_url",
               return_value="https://gateway.ai.cloudflare.com/v1/a/g/workers-ai"), \
         patch.object(mod, "_probe_provider", _spy_probe):
        result = asyncio.run(mod._run_feature_smoke("embed"))
    assert result["outcome"] == "skip"
    assert probed["called"] is False


def test_run_feature_smoke_skip_when_cf_gateway_disabled():
    """When CF_GATEWAY_ENABLED=False → outcome='skip' for all providers."""
    with patch("llm.select_provider", return_value="bedrock"), \
         patch("config.CF_GATEWAY_ENABLED", False), \
         patch("config.cf_gateway_url", return_value=""):
        result = asyncio.run(mod._run_feature_smoke("safety"))
    assert result["outcome"] == "skip"
    assert "CF gateway disabled" in result["error"]


def test_run_feature_smoke_fail_when_select_provider_raises():
    """select_provider() raising → outcome='fail', no HTTP call."""
    with patch("llm.select_provider", side_effect=RuntimeError("pool empty")):
        result = asyncio.run(mod._run_feature_smoke("embed"))
    assert result["outcome"] == "fail"
    assert result["provider"] is None
    assert "select_provider raised" in result["error"]


def test_run_feature_smoke_uses_as_lang_for_assamese_features():
    """assamese_rag_chat / assamese_content must call select_provider with lang='as'."""
    captured = {}

    def _fake_select(feature, lang=""):
        captured["lang"] = lang
        return "sarvam"

    _Client = _make_httpx_client(post_status=200)
    with patch("llm.select_provider", _fake_select), \
         patch("config.CF_GATEWAY_ENABLED", True), \
         patch("config.cf_gateway_url",
               return_value="https://gateway.ai.cloudflare.com/v1/a/g/custom-sarvam"), \
         patch("httpx.AsyncClient", _Client), \
         patch("config.CF_AI_GATEWAY_TOKEN", ""):
        asyncio.run(mod._run_feature_smoke("assamese_rag_chat"))
    assert captured["lang"] == "as"


def test_run_feature_smoke_uses_en_lang_for_non_assamese_features():
    """Non-assamese features must call select_provider with lang='en'."""
    captured = {}

    def _fake_select(feature, lang=""):
        captured["lang"] = lang
        return "vertex"

    with patch("llm.select_provider", _fake_select), \
         patch("config.CF_GATEWAY_ENABLED", False), \
         patch("config.cf_gateway_url", return_value=""):
        asyncio.run(mod._run_feature_smoke("english_rag_chat"))
    assert captured["lang"] == "en"


def test_run_feature_smoke_probe_dispatched_for_cohere():
    """cohere provider → _probe_provider is called (not a slug-root ping)."""
    probed = {}

    async def _capture_probe(provider, gateway_url, spec):
        probed["provider"] = provider
        probed["method"] = spec["method"]
        probed["path"] = spec["path"]
        return 200, 30.0

    with patch("llm.select_provider", return_value="cohere"), \
         patch("config.CF_GATEWAY_ENABLED", True), \
         patch("config.cf_gateway_url",
               return_value="https://gateway.ai.cloudflare.com/v1/a/g/cohere/v1"), \
         patch.object(mod, "_probe_provider", _capture_probe):
        result = asyncio.run(mod._run_feature_smoke("embed"))

    assert result["outcome"] == "pass"
    assert probed["provider"] == "cohere"
    assert probed["method"] == "POST"
    assert "/embed" in probed["path"]


def test_run_feature_smoke_probe_dispatched_for_bedrock():
    """bedrock provider → _probe_provider is called with converse path."""
    probed = {}

    async def _capture_probe(provider, gateway_url, spec):
        probed["provider"] = provider
        probed["path"] = spec["path"]
        return 200, 80.0

    with patch("llm.select_provider", return_value="bedrock"), \
         patch("config.CF_GATEWAY_ENABLED", True), \
         patch("config.cf_gateway_url",
               return_value="https://gateway.ai.cloudflare.com/v1/a/g/aws-bedrock"), \
         patch.object(mod, "_probe_provider", _capture_probe):
        result = asyncio.run(mod._run_feature_smoke("safety"))

    assert result["outcome"] == "pass"
    assert probed["provider"] == "bedrock"
    assert "/converse" in probed["path"]


def test_run_feature_smoke_result_shape():
    """Result dict must include all required keys."""
    _Client = _make_httpx_client(get_status=200)
    with patch("llm.select_provider", return_value="assemblyai"), \
         patch("config.CF_GATEWAY_ENABLED", True), \
         patch("config.cf_gateway_url",
               return_value="https://gateway.ai.cloudflare.com/v1/a/g/assemblyai/v2"), \
         patch("httpx.AsyncClient", _Client), \
         patch("config.CF_AI_GATEWAY_TOKEN", ""):
        result = asyncio.run(mod._run_feature_smoke("stt"))
    for key in ("feature", "provider", "gateway_url", "probe_description",
                "status", "latency_ms", "outcome", "error"):
        assert key in result, f"result is missing key: {key!r}"


# ── _post_smoke_slack_alert ───────────────────────────────────────────────────

def test_post_smoke_slack_alert_noop_when_env_unset():
    """No SMOKE_TEST_SLACK_WEBHOOK → no HTTP call, never raises."""
    called = {"post": False}

    class _Client:
        def __init__(self, *a, **kw):
            called["post"] = True

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop(SMOKE_TEST_SLACK_WEBHOOK_ENV, None)
        with patch("httpx.AsyncClient", _Client):
            asyncio.run(mod._post_smoke_slack_alert([
                {"feature": "embed", "provider": "cohere", "status": 503,
                 "latency_ms": 120.0, "probe_description": "1-word embed"},
            ]))
    assert called["post"] is False


def test_post_smoke_slack_alert_posts_when_env_set():
    """When env var is set the helper POSTs JSON to that webhook URL."""
    posted: dict = {}

    class _Client:
        def __init__(self, *a, **kw): pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **kw):
            posted["url"] = url
            posted["json"] = json
            return _Resp(200)

    failures = [
        {"feature": "tts", "provider": "elevenlabs", "status": 503,
         "latency_ms": 200.0, "probe_description": "model list"},
        {"feature": "stt", "provider": "assemblyai", "status": 0,
         "latency_ms": 8001.0, "probe_description": "transcript list"},
    ]
    with patch.dict(
        os.environ,
        {SMOKE_TEST_SLACK_WEBHOOK_ENV: "https://hooks.slack.test/smoke"},
        clear=False,
    ):
        with patch("httpx.AsyncClient", _Client):
            asyncio.run(mod._post_smoke_slack_alert(failures))

    assert posted.get("url") == "https://hooks.slack.test/smoke"
    payload = posted.get("json", {})
    assert ":red_circle:" in payload.get("text", "")
    assert payload.get("blocks")
    body_text = payload["blocks"][1]["text"]["text"]
    assert "tts" in body_text
    assert "stt" in body_text


def test_post_smoke_slack_alert_swallows_transport_failures():
    """A network error from the webhook must NOT propagate — never raises."""
    class _BoomClient:
        def __init__(self, *a, **kw): pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            raise RuntimeError("network dead")

    with patch.dict(
        os.environ,
        {SMOKE_TEST_SLACK_WEBHOOK_ENV: "https://hooks.slack.test/smoke"},
        clear=False,
    ):
        with patch("httpx.AsyncClient", _BoomClient):
            asyncio.run(mod._post_smoke_slack_alert([
                {"feature": "embed", "provider": "cohere", "status": 0,
                 "latency_ms": 100.0, "probe_description": "1-word embed"},
            ]))


def test_post_smoke_slack_alert_message_body_is_capped():
    """The mrkdwn section must not exceed 2900 chars (Slack limit)."""
    many_failures = [
        {"feature": f"feature_{i}", "provider": "cohere",
         "status": 503, "latency_ms": 1.0, "probe_description": "probe"}
        for i in range(100)
    ]
    posted: dict = {}

    class _Client:
        def __init__(self, *a, **kw): pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **kw):
            posted["json"] = json
            return _Resp(200)

    with patch.dict(
        os.environ,
        {SMOKE_TEST_SLACK_WEBHOOK_ENV: "https://hooks.slack.test/smoke"},
        clear=False,
    ):
        with patch("httpx.AsyncClient", _Client):
            asyncio.run(mod._post_smoke_slack_alert(many_failures))

    body_md = posted["json"]["blocks"][1]["text"]["text"]
    assert len(body_md) <= 2900


# ── admin_credits_smoke_test (endpoint) ───────────────────────────────────────

def _all_pass_results():
    """Synthesise 'pass' result dicts — one per PROVIDER_PRIORITY feature."""
    from config import PROVIDER_PRIORITY
    return [
        {
            "feature": f, "provider": "cohere",
            "gateway_url": "https://gateway.ai.cloudflare.com/v1/a/g/cohere/v1",
            "probe_description": "1-word embed → validates BYOK cohere key",
            "status": 200, "latency_ms": 42.0,
            "outcome": "pass", "error": "",
        }
        for f in PROVIDER_PRIORITY
    ]


def _all_skip_results():
    """Synthesise 'skip' result dicts — one per PROVIDER_PRIORITY feature."""
    from config import PROVIDER_PRIORITY
    return [
        {
            "feature": f, "provider": "vertex",
            "gateway_url": None,
            "probe_description": None,
            "status": None,
            "latency_ms": 1.0, "outcome": "skip",
            "error": "no CF gateway slug for provider",
        }
        for f in PROVIDER_PRIORITY
    ]


def test_smoke_test_endpoint_overall_pass_when_all_pass():
    """overall='pass' when every feature returns outcome='pass'."""
    from config import PROVIDER_PRIORITY

    async def _run():
        with patch.object(mod, "_run_feature_smoke",
                          new=AsyncMock(side_effect=_all_pass_results())):
            return await mod.admin_credits_smoke_test(_admin={})

    result = asyncio.run(_run())
    assert result["overall"] == "pass"
    assert result["fail_count"] == 0
    # Pass count must reflect the *current* number of PROVIDER_PRIORITY feature
    # keys — historically hard-coded to 15, drifted to 17 once embed_en/
    # embed_indic were split out of embed (Task #368).
    assert result["pass_count"] == len(PROVIDER_PRIORITY)


def test_smoke_test_endpoint_overall_fail_when_any_fail():
    """overall='fail' when at least one feature outcome='fail'."""
    results = _all_pass_results()
    results[0]["outcome"] = "fail"
    results[0]["status"] = 503
    results[0]["error"] = "HTTP 503"

    async def _run():
        with patch.object(mod, "_run_feature_smoke",
                          new=AsyncMock(side_effect=results)), \
             patch.object(mod, "_post_smoke_slack_alert", new=AsyncMock()):
            return await mod.admin_credits_smoke_test(_admin={})

    result = asyncio.run(_run())
    assert result["overall"] == "fail"
    assert result["fail_count"] == 1


def test_smoke_test_endpoint_counts_skip_correctly():
    """skip_count is reported correctly when providers have no CF slug."""
    from config import PROVIDER_PRIORITY

    results = _all_skip_results()
    # Flip one to pass so overall="pass" and Slack doesn't fire.
    results[0]["outcome"] = "pass"
    results[0]["status"] = 200

    async def _run():
        with patch.object(mod, "_run_feature_smoke",
                          new=AsyncMock(side_effect=results)):
            return await mod.admin_credits_smoke_test(_admin={})

    result = asyncio.run(_run())
    # All-but-one should be skipped → skip_count is total feature count - 1.
    # Derived from PROVIDER_PRIORITY (Task #368) so this stays correct as
    # features are added/removed.
    assert result["skip_count"] == len(PROVIDER_PRIORITY) - 1
    assert result["pass_count"] == 1
    assert result["fail_count"] == 0
    assert result["overall"] == "pass"


def test_smoke_test_endpoint_covers_every_feature_key():
    """The endpoint must probe every key in PROVIDER_PRIORITY (current count)."""
    from config import PROVIDER_PRIORITY
    probed_features: list[str] = []

    async def _fake_probe(feature: str):
        probed_features.append(feature)
        return {
            "feature": feature, "provider": "cohere",
            "gateway_url": None, "probe_description": None,
            "status": None, "latency_ms": 1.0,
            "outcome": "skip", "error": "",
        }

    async def _run():
        with patch.object(mod, "_run_feature_smoke", side_effect=_fake_probe):
            return await mod.admin_credits_smoke_test(_admin={})

    asyncio.run(_run())
    # Probe set must equal PROVIDER_PRIORITY exactly — Task #368 swapped the
    # historical magic number 15 for the dynamic ``len(PROVIDER_PRIORITY)``
    # so embed_en/embed_indic (and any future additions) are covered.
    assert set(probed_features) == set(PROVIDER_PRIORITY.keys())
    assert len(probed_features) == len(PROVIDER_PRIORITY)


def test_smoke_test_endpoint_schedules_slack_alert_on_failure():
    """A failing result must schedule _post_smoke_slack_alert (ensure_future)."""
    results = _all_pass_results()
    results[0]["outcome"] = "fail"
    results[0]["status"] = 503
    results[0]["error"] = "HTTP 503"

    slack_called: dict = {"args": None}

    async def _fake_slack(failures):
        slack_called["args"] = failures

    async def _run():
        with patch.object(mod, "_run_feature_smoke",
                          new=AsyncMock(side_effect=results)), \
             patch.object(mod, "_post_smoke_slack_alert",
                          side_effect=_fake_slack):
            resp = await mod.admin_credits_smoke_test(_admin={})
            # Give the ensure_future task a tick to run.
            await asyncio.sleep(0)
            return resp

    asyncio.run(_run())
    assert slack_called["args"] is not None
    assert len(slack_called["args"]) == 1


def test_smoke_test_endpoint_does_not_schedule_slack_when_no_failures():
    """When all outcomes are pass/skip, _post_smoke_slack_alert must not be called."""
    slack_called: dict = {"called": False}

    async def _fake_slack(failures):
        slack_called["called"] = True

    async def _run():
        with patch.object(mod, "_run_feature_smoke",
                          new=AsyncMock(side_effect=_all_skip_results())), \
             patch.object(mod, "_post_smoke_slack_alert",
                          side_effect=_fake_slack):
            resp = await mod.admin_credits_smoke_test(_admin={})
            await asyncio.sleep(0)
            return resp

    asyncio.run(_run())
    assert slack_called["called"] is False


def test_smoke_test_endpoint_response_shape():
    """Response must include the expected top-level keys."""
    from config import PROVIDER_PRIORITY

    async def _run():
        with patch.object(mod, "_run_feature_smoke",
                          new=AsyncMock(side_effect=_all_pass_results())):
            return await mod.admin_credits_smoke_test(_admin={})

    result = asyncio.run(_run())
    for key in ("overall", "total_features", "pass_count", "fail_count",
                "skip_count", "cf_gateway_enabled", "slack", "run_at_epoch",
                "results", "note"):
        assert key in result, f"missing key: {key}"
    assert isinstance(result["results"], list)
    # total_features comes from len(PROVIDER_PRIORITY) (Task #368) — must
    # not silently drift back to a hard-coded constant.
    assert result["total_features"] == len(PROVIDER_PRIORITY)


def test_smoke_test_endpoint_slack_config_shape():
    """slack field must carry slackConfigured and slackWebhookEnv."""
    async def _run():
        with patch.object(mod, "_run_feature_smoke",
                          new=AsyncMock(side_effect=_all_pass_results())):
            return await mod.admin_credits_smoke_test(_admin={})

    result = asyncio.run(_run())
    slack = result["slack"]
    assert "slackConfigured" in slack
    assert slack["slackWebhookEnv"] == SMOKE_TEST_SLACK_WEBHOOK_ENV


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
