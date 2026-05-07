"""Task #383 — tests for the AI Gateway header parser + counters."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_counters():
    from ai_gateway_observability import reset_for_tests
    reset_for_tests()
    yield
    reset_for_tests()


class _FakeRedisList:
    """Tiny in-process stand-in for the Upstash REST client — only the
    list ops ai_gateway_observability calls (lpush / ltrim / expire /
    lrange / delete). LPUSH puts newest at the head, like the real
    client, so we exercise the chronological-order flip in
    ``_read_shared_samples``."""

    def __init__(self) -> None:
        self.store: dict[str, list[str]] = {}
        self.expiries: dict[str, int] = {}

    def lpush(self, key: str, value: str) -> int:
        self.store.setdefault(key, []).insert(0, value)
        return len(self.store[key])

    def ltrim(self, key: str, start: int, end: int) -> str:
        if key in self.store:
            self.store[key] = self.store[key][start:end + 1]
        return "OK"

    def expire(self, key: str, ttl: int) -> int:
        self.expiries[key] = ttl
        return 1

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        return list(self.store.get(key, [])[start:end + 1])

    def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


def test_parses_cache_hit_headers():
    from ai_gateway_observability import parse_aig_response_headers
    out = parse_aig_response_headers({
        "cf-aig-cache-status": "HIT",
        "cf-aig-cache-ttl": "300",
        "cf-aig-log-id": "log-abc",
        "cf-aig-event-id": "evt-1",
    })
    assert out["present"] is True
    assert out["cache_status"] == "hit"
    assert out["cache_ttl_s"] == 300
    assert out["log_id"] == "log-abc"
    assert out["event_id"] == "evt-1"
    assert out["guardrail"]["action"] is None


def test_parses_miss_and_bypass():
    from ai_gateway_observability import parse_aig_response_headers
    miss = parse_aig_response_headers({"cf-aig-cache-status": "MISS"})
    bypass = parse_aig_response_headers({"cf-aig-cache-status": "BYPASS"})
    assert miss["cache_status"] == "miss"
    assert bypass["cache_status"] == "bypass"


def test_unknown_status_falls_back_to_bypass():
    from ai_gateway_observability import parse_aig_response_headers
    out = parse_aig_response_headers({"cf-aig-cache-status": "STALE"})
    # Unknown but non-empty values are normalised to bypass so we don't
    # silently drop telemetry when CF adds new statuses.
    assert out["cache_status"] == "bypass"


def test_no_headers_returns_present_false():
    from ai_gateway_observability import parse_aig_response_headers
    out = parse_aig_response_headers({})
    assert out["present"] is False
    assert out["cache_status"] is None


def test_record_increments_cache_counters(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import record_aig_response, snapshot
    record_aig_response({"cf-aig-cache-status": "HIT"}, provider="vertex")
    record_aig_response({"cf-aig-cache-status": "HIT"}, provider="vertex")
    record_aig_response({"cf-aig-cache-status": "MISS"}, provider="azure")
    snap = snapshot()
    assert snap["counters"]["aig_cache_hits"] == 2
    assert snap["counters"]["aig_cache_misses"] == 1
    assert snap["cache_hit_ratio"] == pytest.approx(2 / 3, rel=1e-3)
    assert len(snap["recent_samples"]) == 3


def test_record_skipped_when_flag_off(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", False)
    from ai_gateway_observability import record_aig_response, snapshot
    record_aig_response({"cf-aig-cache-status": "HIT"})
    snap = snapshot()
    assert snap["counters"]["aig_cache_hits"] == 0
    assert snap["enabled"] is False


def test_guardrail_block_counter(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import record_aig_response, snapshot
    record_aig_response({
        "cf-aig-cache-status": "MISS",
        "cf-aig-guardrail-action": "block",
        "cf-aig-guardrail-category": "pii",
    })
    record_aig_response({
        "cf-aig-cache-status": "MISS",
        "cf-aig-guardrail-action": "allow",
    })
    snap = snapshot()
    assert snap["counters"]["aig_guardrails_blocked"] == 1
    assert snap["counters"]["aig_guardrails_allowed"] == 1
    assert snap["guardrail_block_ratio"] == pytest.approx(0.5, rel=1e-3)


def test_record_returns_summary_when_disabled(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", False)
    from ai_gateway_observability import record_aig_response
    out = record_aig_response({"cf-aig-cache-status": "HIT"})
    assert out["cache_status"] == "hit"  # parse still works


# ──────────────────────────────────────────────────────────────────────
# Task #419 — per-model cache aggregation surfaced via snapshot() so the
# admin CF Health tile can show "top models by cache hit ratio" without
# re-slicing recent_samples on the frontend.
# ──────────────────────────────────────────────────────────────────────


def test_cache_by_model_aggregates_per_model_hit_ratio(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import record_aig_response, snapshot
    # llama: 2 hits, 1 miss → 2/3
    record_aig_response({"cf-aig-cache-status": "HIT"},
                        provider="workers_ai", model="llama-3.3-70b")
    record_aig_response({"cf-aig-cache-status": "HIT"},
                        provider="workers_ai", model="llama-3.3-70b")
    record_aig_response({"cf-aig-cache-status": "MISS"},
                        provider="workers_ai", model="llama-3.3-70b")
    # gpt-oss: 1 miss only → 0.0
    record_aig_response({"cf-aig-cache-status": "MISS"},
                        provider="workers_ai", model="gpt-oss-120b")
    rows = snapshot()["cache_by_model"]
    by_model = {r["model"]: r for r in rows}
    assert by_model["llama-3.3-70b"]["hits"] == 2
    assert by_model["llama-3.3-70b"]["misses"] == 1
    assert by_model["llama-3.3-70b"]["hit_ratio"] == pytest.approx(2 / 3, rel=1e-3)
    assert by_model["gpt-oss-120b"]["hit_ratio"] == 0.0
    # llama (highest ratio) should sort before gpt-oss.
    assert rows[0]["model"] == "llama-3.3-70b"


def test_cache_by_model_renders_dash_when_no_cache_status(monkeypatch):
    """A model whose samples carried no cf-aig-cache-status (e.g. only
    guardrail events) must report hit_ratio=None so the frontend can
    render '—' instead of misleadingly painting it as 0%."""
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import record_aig_response, snapshot
    # Guardrail-only event: cf-aig-* present, but no cache-status header.
    record_aig_response({
        "cf-aig-guardrail-action": "allow",
        "cf-aig-log-id": "log-x",
    }, provider="vertex", model="gemini-2.5-flash")
    rows = snapshot()["cache_by_model"]
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "vertex"
    assert row["model"] == "gemini-2.5-flash"
    assert row["samples"] == 1
    assert row["cache_status_total"] == 0
    assert row["hit_ratio"] is None


def test_cache_by_model_empty_when_no_samples(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import snapshot
    snap = snapshot()
    assert snap["cache_by_model"] == []


# ──────────────────────────────────────────────────────────────────────
# Task #448 — per-model guardrail aggregation surfaced via snapshot()
# so the admin CF Health tile can show "models by guardrail block
# ratio" without re-slicing recent_samples on the frontend. Mirrors
# the cache_by_model contract above.
# ──────────────────────────────────────────────────────────────────────


def test_guardrail_by_model_aggregates_per_model_block_ratio(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import record_aig_response, snapshot
    # llama: 1 allow, 1 rewrite, 2 blocks → 2/4 = 0.5
    record_aig_response({"cf-aig-cache-status": "MISS",
                         "cf-aig-guardrail-action": "allow"},
                        provider="workers_ai", model="llama-3.3-70b")
    record_aig_response({"cf-aig-cache-status": "MISS",
                         "cf-aig-guardrail-action": "rewrite"},
                        provider="workers_ai", model="llama-3.3-70b")
    record_aig_response({"cf-aig-cache-status": "MISS",
                         "cf-aig-guardrail-action": "block"},
                        provider="workers_ai", model="llama-3.3-70b")
    record_aig_response({"cf-aig-cache-status": "MISS",
                         "cf-aig-guardrail-action": "block"},
                        provider="workers_ai", model="llama-3.3-70b")
    # gpt-oss: 1 allow only → 0.0 block ratio
    record_aig_response({"cf-aig-cache-status": "MISS",
                         "cf-aig-guardrail-action": "allow"},
                        provider="workers_ai", model="gpt-oss-120b")
    rows = snapshot()["guardrail_by_model"]
    by_model = {r["model"]: r for r in rows}
    assert by_model["llama-3.3-70b"]["blocks"] == 2
    assert by_model["llama-3.3-70b"]["rewrites"] == 1
    assert by_model["llama-3.3-70b"]["allows"] == 1
    assert by_model["llama-3.3-70b"]["block_ratio"] == pytest.approx(0.5, rel=1e-3)
    assert by_model["gpt-oss-120b"]["block_ratio"] == 0.0
    # llama (highest block ratio) sorts before gpt-oss so on-call's
    # eye lands on the worst offender first.
    assert rows[0]["model"] == "llama-3.3-70b"


def test_guardrail_by_model_renders_dash_when_no_guardrail_action(monkeypatch):
    """A model whose samples carried no cf-aig-guardrail-action (e.g.
    only cache events) must report block_ratio=None so the frontend can
    render '—' instead of misleadingly painting it as 0% blocked."""
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import record_aig_response, snapshot
    # Cache-only event: cf-aig-* present, but no guardrail-action header.
    record_aig_response({
        "cf-aig-cache-status": "HIT",
        "cf-aig-log-id": "log-x",
    }, provider="vertex", model="gemini-2.5-flash")
    rows = snapshot()["guardrail_by_model"]
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "vertex"
    assert row["model"] == "gemini-2.5-flash"
    assert row["samples"] == 1
    assert row["guardrail_total"] == 0
    assert row["block_ratio"] is None


def test_guardrail_by_model_empty_when_no_samples(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import snapshot
    snap = snapshot()
    assert snap["guardrail_by_model"] == []


def test_guardrail_by_model_ratio_rows_sort_before_dash_rows(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import record_aig_response, snapshot
    # Model A: has guardrail telemetry (1 allow → 0.0 block ratio).
    record_aig_response({"cf-aig-cache-status": "MISS",
                         "cf-aig-guardrail-action": "allow"},
                        provider="azure", model="model-a")
    # Model B: only cache telemetry → block_ratio is None ("—").
    record_aig_response({"cf-aig-cache-status": "HIT"},
                        provider="azure", model="model-b")
    rows = snapshot()["guardrail_by_model"]
    # Even though A's block_ratio is 0.0 and B's is None, A must sort
    # first so the frontend's "models by block ratio" view does not
    # bury a real 0% under rows with no guardrail telemetry at all.
    assert [r["model"] for r in rows] == ["model-a", "model-b"]
    assert rows[0]["block_ratio"] == 0.0
    assert rows[1]["block_ratio"] is None


def test_cache_by_model_ratio_rows_sort_before_dash_rows(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import record_aig_response, snapshot
    # Model A: has cache telemetry (1 miss → 0.0 ratio).
    record_aig_response({"cf-aig-cache-status": "MISS"},
                        provider="azure", model="model-a")
    # Model B: only guardrail telemetry → ratio is None ("—").
    record_aig_response({"cf-aig-guardrail-action": "allow"},
                        provider="azure", model="model-b")
    rows = snapshot()["cache_by_model"]
    # Even though A's ratio is 0.0 and B's is None, A must sort first
    # so the frontend's "top models" view does not bury a real 0% under
    # rows with no telemetry at all.
    assert [r["model"] for r in rows] == ["model-a", "model-b"]
    assert rows[0]["hit_ratio"] == 0.0
    assert rows[1]["hit_ratio"] is None


# ──────────────────────────────────────────────────────────────────────
# Task #403 — integration test: a live chat call through the
# providers/cloudflare_ai.py path (Workers AI via CF AI Gateway) must
# bump aig_responses_total by 1. Uses httpx.MockTransport to shim the
# upstream call so the counters move without any real network traffic.
# ──────────────────────────────────────────────────────────────────────


def test_workers_ai_chat_records_aig_response_headers(monkeypatch):
    """One non-stream chat through providers.cloudflare_ai.chat() must
    feed the cf-aig-* response headers into record_aig_response() exactly
    once, bumping aig_responses_total by 1."""
    import asyncio
    import importlib

    import httpx

    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import reset_for_tests, snapshot
    reset_for_tests()

    monkeypatch.setenv("CF_AI_GATEWAY_ACCOUNT_ID", "acct-test")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-token-test")
    monkeypatch.setenv("CF_AI_GATEWAY_ID", "gw-test")

    from providers import cloudflare_ai
    importlib.reload(cloudflare_ai)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "cf-aig-cache-status": "MISS",
                "cf-aig-log-id": "log-int-1",
                "cf-aig-event-id": "evt-int-1",
            },
            json={"result": {"response": "hello"}, "success": True},
        )

    transport = httpx.MockTransport(_handler)
    fake_client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(cloudflare_ai, "_http_client", fake_client)

    before = snapshot()["counters"]["aig_responses_total"]

    text = asyncio.run(cloudflare_ai.chat(
        [{"role": "user", "content": "hi"}],
        model_key="chat_fast",
        max_tokens=4,
    ))
    assert text == "hello"

    after_snap = snapshot()
    assert after_snap["counters"]["aig_responses_total"] == before + 1, after_snap
    assert after_snap["counters"]["aig_cache_misses"] >= 1, after_snap
    samples = after_snap["recent_samples"]
    assert samples and samples[-1]["log_id"] == "log-int-1", samples


def test_workers_ai_stream_records_aig_response_headers(monkeypatch):
    """One streaming chat through providers.cloudflare_ai.chat_stream() must
    feed the cf-aig-* headers into record_aig_response() exactly once,
    even if zero tokens are emitted before the stream closes."""
    import asyncio
    import importlib

    import httpx

    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import reset_for_tests, snapshot
    reset_for_tests()

    monkeypatch.setenv("CF_AI_GATEWAY_ACCOUNT_ID", "acct-test")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-token-test")
    monkeypatch.setenv("CF_AI_GATEWAY_ID", "gw-test")

    from providers import cloudflare_ai
    importlib.reload(cloudflare_ai)

    sse_body = b'data: {"response": "hi"}\n\ndata: [DONE]\n\n'

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "cf-aig-cache-status": "HIT",
                "cf-aig-log-id": "log-stream-1",
            },
            content=sse_body,
        )

    transport = httpx.MockTransport(_handler)
    fake_client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(cloudflare_ai, "_http_client", fake_client)

    async def _drive() -> list[str]:
        out: list[str] = []
        async for chunk in cloudflare_ai.chat_stream(
            [{"role": "user", "content": "hi"}],
            model_key="chat_fast",
        ):
            out.append(chunk)
        return out

    before = snapshot()["counters"]["aig_responses_total"]
    chunks = asyncio.run(_drive())
    assert chunks == ["hi"]

    after_snap = snapshot()
    assert after_snap["counters"]["aig_responses_total"] == before + 1, after_snap
    assert after_snap["counters"]["aig_cache_hits"] >= 1, after_snap


# ──────────────────────────────────────────────────────────────────────
# Task #420 — OpenAI-compatible callsites in llm.py
# (_call_openai_compat / _stream_openai_compat) now switch to
# ``with_raw_response.create()`` so the
# cf-aig-* response headers are reachable. They must feed
# record_aig_response() ONLY when the request actually went through
# the Cloudflare AI Gateway (base URL starts with CF_GATEWAY_BASE),
# never when the direct fallback URL was used.
# ──────────────────────────────────────────────────────────────────────


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str = "ok") -> None:
        self.choices = [_FakeChoice(content)]


class _FakeRaw:
    def __init__(self, headers: dict, content: str = "ok") -> None:
        self.headers = headers
        self._content = content

    def parse(self) -> _FakeCompletion:
        return _FakeCompletion(self._content)


class _FakeWithRaw:
    def __init__(self, headers: dict) -> None:
        self._headers = headers

    async def create(self, **_: object) -> _FakeRaw:
        return _FakeRaw(self._headers)


class _FakeCompletions:
    def __init__(self, headers: dict) -> None:
        self.with_raw_response = _FakeWithRaw(headers)


class _FakeChat:
    def __init__(self, headers: dict) -> None:
        self.completions = _FakeCompletions(headers)


class _FakeOAIClient:
    def __init__(self, headers: dict) -> None:
        self.chat = _FakeChat(headers)


def _patch_llm_for_oai_compat(monkeypatch, *, base: str, headers: dict) -> None:
    """Wire llm.py so ``_call_openai_compat`` sees a fixed base URL and
    a fake openai-python client that returns ``headers`` from
    ``with_raw_response.create()``."""
    import llm
    monkeypatch.setattr(llm, "get_provider_base_url",
                        lambda _provider: base, raising=True)
    monkeypatch.setattr(llm, "_get_oai_client",
                        lambda _key, _base: _FakeOAIClient(headers),
                        raising=True)


def test_oai_compat_records_aig_when_routed_through_cf(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import reset_for_tests, snapshot
    reset_for_tests()

    import config
    monkeypatch.setattr(config, "CF_GATEWAY_BASE",
                        "https://gateway.ai.cloudflare.com/v1/acct/gw")
    cf_base = f"{config.CF_GATEWAY_BASE}/groq-slug"
    _patch_llm_for_oai_compat(
        monkeypatch,
        base=cf_base,
        headers={"cf-aig-cache-status": "HIT", "cf-aig-log-id": "log-cf-1"},
    )

    import asyncio
    import llm
    text = asyncio.run(llm._call_openai_compat(
        [{"role": "user", "content": "hi"}],
        api_key="x", model="m1", max_tokens=4,
        provider="groq", fallback_base="https://api.groq.com/openai/v1",
    ))
    assert text == "ok"

    snap = snapshot()
    assert snap["counters"]["aig_cache_hits"] == 1, snap
    assert snap["counters"]["aig_responses_total"] == 1, snap
    assert snap["recent_samples"][-1]["log_id"] == "log-cf-1"


def test_oai_compat_does_not_record_when_routed_direct(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import reset_for_tests, snapshot
    reset_for_tests()

    import config
    monkeypatch.setattr(config, "CF_GATEWAY_BASE",
                        "https://gateway.ai.cloudflare.com/v1/acct/gw")
    # Direct provider URL — must NOT bump counters even if a stray
    # cf-aig-* header somehow appears (defence-in-depth: prevents a
    # misbehaving direct upstream from polluting the gateway cache stats).
    direct_base = "https://api.groq.com/openai/v1"
    _patch_llm_for_oai_compat(
        monkeypatch,
        base=direct_base,
        headers={"cf-aig-cache-status": "HIT", "cf-aig-log-id": "log-direct"},
    )

    import asyncio
    import llm
    text = asyncio.run(llm._call_openai_compat(
        [{"role": "user", "content": "hi"}],
        api_key="key", model="m1", max_tokens=4,
        provider="groq", fallback_base=direct_base,
    ))
    assert text == "ok"

    snap = snapshot()
    assert snap["counters"]["aig_cache_hits"] == 0, snap
    assert snap["counters"]["aig_responses_total"] == 0, snap


# ── Streaming variants ───────────────────────────────────────────────


class _FakeDelta:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeStreamChoice:
    def __init__(self, content: str) -> None:
        self.delta = _FakeDelta(content)


class _FakeStreamChunk:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeStreamChoice(content)]


class _FakeHttpxResponse:
    def __init__(self, headers: dict) -> None:
        self.headers = headers


class _FakeAsyncStream:
    """Mimics openai-python AsyncStream: ``.response`` exposes the
    underlying httpx response, and the object is itself async-iterable."""

    def __init__(self, headers: dict, chunks: list[str]) -> None:
        self.response = _FakeHttpxResponse(headers)
        self._chunks = chunks

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return _FakeStreamChunk(next(self._iter))
        except StopIteration:
            raise StopAsyncIteration


class _FakeStreamingCompletions:
    def __init__(self, headers: dict, chunks: list[str]) -> None:
        self._stream = _FakeAsyncStream(headers, chunks)

    async def create(self, **_: object) -> _FakeAsyncStream:
        return self._stream


class _FakeStreamingChat:
    def __init__(self, headers: dict, chunks: list[str]) -> None:
        self.completions = _FakeStreamingCompletions(headers, chunks)


class _FakeStreamingClient:
    def __init__(self, headers: dict, chunks: list[str]) -> None:
        self.chat = _FakeStreamingChat(headers, chunks)


def test_oai_compat_stream_records_aig_when_routed_through_cf(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import reset_for_tests, snapshot
    reset_for_tests()

    import config
    monkeypatch.setattr(config, "CF_GATEWAY_BASE",
                        "https://gateway.ai.cloudflare.com/v1/acct/gw")
    cf_base = f"{config.CF_GATEWAY_BASE}/groq-slug"

    import llm
    monkeypatch.setattr(llm, "get_provider_base_url",
                        lambda _p: cf_base, raising=True)
    monkeypatch.setattr(llm, "_get_oai_client",
                        lambda _k, _b: _FakeStreamingClient(
                            {"cf-aig-cache-status": "HIT",
                             "cf-aig-log-id": "log-stream-cf"},
                            ["hello", " world"]),
                        raising=True)

    import asyncio

    async def _drive() -> list[str]:
        out: list[str] = []
        async for tok in llm._stream_openai_compat(
            [{"role": "user", "content": "hi"}],
            api_key="x", model="m1", max_tokens=4,
            provider="groq", fallback_base="https://api.groq.com/openai/v1",
        ):
            out.append(tok)
        return out

    chunks = asyncio.run(_drive())
    assert chunks == ["hello", " world"]

    snap = snapshot()
    assert snap["counters"]["aig_cache_hits"] == 1, snap
    assert snap["counters"]["aig_responses_total"] == 1, snap
    assert snap["recent_samples"][-1]["log_id"] == "log-stream-cf"


def test_oai_compat_stream_does_not_record_when_routed_direct(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import reset_for_tests, snapshot
    reset_for_tests()

    import config
    monkeypatch.setattr(config, "CF_GATEWAY_BASE",
                        "https://gateway.ai.cloudflare.com/v1/acct/gw")
    direct_base = "https://api.groq.com/openai/v1"

    import llm
    monkeypatch.setattr(llm, "get_provider_base_url",
                        lambda _p: direct_base, raising=True)
    monkeypatch.setattr(llm, "_get_oai_client",
                        lambda _k, _b: _FakeStreamingClient(
                            {"cf-aig-cache-status": "HIT"},
                            ["x"]),
                        raising=True)

    import asyncio

    async def _drive() -> list[str]:
        return [tok async for tok in llm._stream_openai_compat(
            [{"role": "user", "content": "hi"}],
            api_key="key", model="m1", max_tokens=4,
            provider="groq", fallback_base=direct_base,
        )]

    asyncio.run(_drive())

    snap = snapshot()
    assert snap["counters"]["aig_cache_hits"] == 0, snap
    assert snap["counters"]["aig_responses_total"] == 0, snap


# ──────────────────────────────────────────────────────────────────────
# Task #449 — samples are mirrored to a shared Redis store so the
# admin "cache by model" tile survives container restarts and shows
# the union across all ACA replicas (not just whichever pod the
# request landed on).
# ──────────────────────────────────────────────────────────────────────


def test_snapshot_reads_samples_from_shared_store(monkeypatch):
    """Regression: snapshot() must surface samples from the shared
    Redis store, not just the local in-process deque. We simulate a
    container restart by recording into a shared store, clearing the
    local deque, and asserting snapshot() still returns the samples
    (and the per-model breakdowns derived from them)."""
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    fake = _FakeRedisList()
    monkeypatch.setattr("ai_gateway_observability._get_redis", lambda: fake)

    from ai_gateway_observability import (
        _SAMPLES,
        record_aig_response,
        snapshot,
    )

    # Replica A records two samples — they go into both the local
    # deque and the shared store.
    record_aig_response({"cf-aig-cache-status": "HIT"},
                        provider="workers_ai", model="llama-3.3-70b")
    record_aig_response({"cf-aig-cache-status": "MISS"},
                        provider="workers_ai", model="llama-3.3-70b")

    # The shared store actually received them via lpush (newest at head).
    assert len(fake.store["aig_obs:samples"]) == 2
    assert fake.expiries["aig_obs:samples"] == 3600

    # Simulate a container restart: wipe the local deque so the only
    # surviving copy lives in the shared store.
    _SAMPLES.clear()

    snap = snapshot()
    # snapshot() pulled them back from the shared store.
    assert len(snap["recent_samples"]) == 2
    # Chronological order preserved (oldest first), not LPUSH order.
    assert snap["recent_samples"][0]["cache_status"] == "hit"
    assert snap["recent_samples"][1]["cache_status"] == "miss"
    # Per-model aggregation derived from the shared samples works too.
    rows = snap["cache_by_model"]
    assert len(rows) == 1 and rows[0]["model"] == "llama-3.3-70b"
    assert rows[0]["hits"] == 1 and rows[0]["misses"] == 1


def test_snapshot_unions_samples_across_replicas(monkeypatch):
    """When two replicas write to the same shared list, snapshot() on
    either one returns the *union* — not just the slice this pod
    happened to record locally."""
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    fake = _FakeRedisList()
    monkeypatch.setattr("ai_gateway_observability._get_redis", lambda: fake)

    from ai_gateway_observability import (
        _SAMPLES,
        record_aig_response,
        snapshot,
    )

    # Replica A records one sample.
    record_aig_response({"cf-aig-cache-status": "HIT"},
                        provider="azure", model="gpt-4.1-nano")
    # Replica B records another sample directly into the shared store
    # (we can't run two processes here, but the shared list is the
    # only thing they share, so writing straight to it is equivalent).
    import json as _json, time as _time
    fake.lpush("aig_obs:samples", _json.dumps({
        "ts": _time.time(),
        "provider": "vertex",
        "model": "gemini-2.5-flash",
        "cache_status": "miss",
        "guardrail_action": None,
        "log_id": "log-replica-b",
    }))

    # Replica A's local deque only knows about its own sample…
    assert len(_SAMPLES) == 1
    # …but snapshot() returns both, because it reads from the shared store.
    snap = snapshot()
    models = sorted(r["model"] for r in snap["cache_by_model"])
    assert models == ["gemini-2.5-flash", "gpt-4.1-nano"]


def test_snapshot_falls_back_to_local_deque_when_redis_unavailable(monkeypatch):
    """A Redis outage must degrade the tile to single-replica behaviour,
    not break it. snapshot() returns the local deque when the shared
    store is unreachable."""
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    monkeypatch.setattr("ai_gateway_observability._get_redis", lambda: None)

    from ai_gateway_observability import record_aig_response, snapshot
    record_aig_response({"cf-aig-cache-status": "HIT"},
                        provider="azure", model="gpt-4.1-nano")
    snap = snapshot()
    assert len(snap["recent_samples"]) == 1
    assert snap["recent_samples"][0]["cache_status"] == "hit"


def test_record_swallows_shared_store_errors(monkeypatch):
    """A misbehaving Redis client must never break the chat hot path.
    record_aig_response() still bumps local counters even when every
    shared-store call raises."""
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)

    class _BoomRedis:
        def lpush(self, *a, **kw):  raise RuntimeError("boom")
        def ltrim(self, *a, **kw):  raise RuntimeError("boom")
        def expire(self, *a, **kw): raise RuntimeError("boom")
        def lrange(self, *a, **kw): raise RuntimeError("boom")
        def delete(self, *a, **kw): raise RuntimeError("boom")

    monkeypatch.setattr("ai_gateway_observability._get_redis",
                        lambda: _BoomRedis())

    from ai_gateway_observability import record_aig_response, snapshot
    record_aig_response({"cf-aig-cache-status": "HIT"},
                        provider="azure", model="gpt-4.1-nano")
    snap = snapshot()
    assert snap["counters"]["aig_cache_hits"] == 1
    # Shared read also failed → falls back to local deque, which has it.
    assert len(snap["recent_samples"]) == 1
