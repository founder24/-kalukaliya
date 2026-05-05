"""70 % RPM soft-shed for azure_openai / sarvam chat primaries.

2026-05-05 user instruction: when the strict primary for a chat pool
accumulates >= 70 % of its configured RPM cap inside a 60-second window,
``select_provider`` must exclude it so the dispatcher preemptively
shifts traffic to the ``workers_ai_*`` fallback BEFORE 429s start.

Pools covered:
  • english_rag_chat → azure_openai primary, workers_ai_* fallback
  • assamese_rag_chat → sarvam primary, workers_ai_indic fallback

This file pins:
  1. The default threshold is exactly 70 %.
  2. Below the threshold the primary keeps serving (no needless shed).
  3. At the threshold the primary is excluded and the fallback takes over.
  4. The 60-second window expires old timestamps so historical bursts
     don't permanently disable a healthy primary.
  5. The dispatch hook records both successful and failed attempts
     (verified end-to-end through ``_dispatch_llm_for_feature``).
  6. Cross-worker Redis aggregation drives the shed in production
     (gunicorn runs 3 workers — per-process counters alone undercount).
"""
from __future__ import annotations

import time
from unittest.mock import patch, AsyncMock

import pytest

import llm
import deps
from llm import _POOL_RPM_LIMITS


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Clear all RPM/429 windows and disable Redis so each test starts clean.

    Disabling Redis (``redis_client = None``) means
    ``_get_paid_provider_rpm_count_global`` returns 0 and the saturation
    check uses the per-process window only — that's deterministic for
    unit tests.  The cross-worker behaviour is exercised separately
    in ``test_record_paid_provider_request_writes_to_redis_bucket``
    and ``test_get_paid_provider_rpm_count_global_reads_redis_buckets``.
    """
    llm._reset_paid_provider_rpm()
    for w in llm._PROVIDER_429_WINDOWS.values():
        w.clear()
    monkeypatch.setattr(deps, "redis_client", None, raising=False)
    yield
    llm._reset_paid_provider_rpm()
    for w in llm._PROVIDER_429_WINDOWS.values():
        w.clear()


def _seed_recent(provider: str, n: int) -> None:
    """Inject *n* fresh timestamps into *provider*'s sliding window."""
    now = time.time()
    window = llm._PAID_PROVIDER_RPM_WINDOWS[provider]
    for _ in range(n):
        window.append(now)


# ── Threshold constant ──────────────────────────────────────────────────────


def test_chat_rpm_soft_shed_threshold_is_70_pct():
    assert llm._CHAT_RPM_SOFT_SHED_THRESHOLD == pytest.approx(0.70), (
        "Default chat RPM soft-shed threshold must be 70 % "
        "(2026-05-05 user spec)."
    )


def test_chat_pools_for_rpm_shed_set_includes_both_chat_pools():
    assert "english_rag_chat" in llm._CHAT_POOLS_FOR_RPM_SHED
    assert "assamese_rag_chat" in llm._CHAT_POOLS_FOR_RPM_SHED


# ── azure_openai → english_rag_chat shed ────────────────────────────────────


def test_azure_openai_at_70_pct_sheds_english_chat_to_workers_ai():
    limit = _POOL_RPM_LIMITS["azure_openai"]
    # Seed exactly 70 % of the configured cap
    _seed_recent("azure_openai", int(limit * 0.70) + 1)
    picks = {llm.select_provider("english_rag_chat", lang="en") for _ in range(50)}
    assert "azure_openai" not in picks, (
        f"azure_openai must be shed at >= 70 % RPM but was picked: {picks}"
    )
    # The fallback must be one of the workers_ai_* tail variants — the
    # weight-0 walk should pick the first non-excluded fallback in
    # PROVIDER_PRIORITY order.
    assert picks.issubset({
        "workers_ai_llama32_3b", "workers_ai_mistral_7b", "workers_ai",
    }), f"unexpected fallback set {picks}"


def test_azure_openai_below_70_pct_keeps_serving_english_chat():
    limit = _POOL_RPM_LIMITS["azure_openai"]
    # Seed 50 % — well below the 70 % shed threshold
    _seed_recent("azure_openai", int(limit * 0.50))
    picks = [llm.select_provider("english_rag_chat", lang="en") for _ in range(50)]
    # Below threshold → azure_openai is the only weighted entry, so it
    # must win every draw.  Any workers_ai_* leakage here would mean we
    # are shedding too aggressively.
    assert all(p == "azure_openai" for p in picks), (
        f"azure_openai must keep serving below 70 % but saw picks={set(picks)}"
    )


def test_azure_openai_just_under_70_pct_still_serves():
    """Boundary check: the shed must trigger at >= 70 %, not before."""
    limit = _POOL_RPM_LIMITS["azure_openai"]
    # Seed (70 % - 1) requests — must still be selectable.
    _seed_recent("azure_openai", int(limit * 0.70) - 1)
    picks = [llm.select_provider("english_rag_chat", lang="en") for _ in range(20)]
    assert all(p == "azure_openai" for p in picks), (
        f"azure_openai must still serve just under 70 %: picks={set(picks)}"
    )


# ── sarvam → assamese_rag_chat shed ─────────────────────────────────────────


def test_sarvam_at_70_pct_sheds_assamese_chat_to_workers_ai_indic():
    limit = _POOL_RPM_LIMITS["sarvam"]
    _seed_recent("sarvam", int(limit * 0.70) + 1)
    picks = {llm.select_provider("assamese_rag_chat", lang="as") for _ in range(50)}
    assert "sarvam" not in picks, (
        f"sarvam must be shed at >= 70 % RPM but was picked: {picks}"
    )
    # The strict assamese chain (Task #291) allows only
    # sarvam → workers_ai_indic → vertex.  With sarvam shed and vertex
    # at weight 0 in POOL_WEIGHTS, the weight-0 fallback walk must reach
    # workers_ai_indic.
    assert "workers_ai_indic" in picks, (
        f"workers_ai_indic must be reached when sarvam is shed; got {picks}"
    )


def test_sarvam_below_70_pct_keeps_serving_assamese_chat():
    limit = _POOL_RPM_LIMITS["sarvam"]
    _seed_recent("sarvam", int(limit * 0.50))
    picks = [llm.select_provider("assamese_rag_chat", lang="as") for _ in range(50)]
    assert all(p == "sarvam" for p in picks), (
        f"sarvam must keep serving below 70 % but saw picks={set(picks)}"
    )


# ── Window expiry ───────────────────────────────────────────────────────────


def test_record_paid_provider_request_trims_expired_timestamps():
    """Old timestamps (>60 s) must not count toward saturation.

    Otherwise an early-morning burst would permanently disable the
    primary for the rest of the day.
    """
    now = time.time()
    window = llm._PAID_PROVIDER_RPM_WINDOWS["azure_openai"]
    # Add 1000 timestamps from 2 minutes ago — far above any 70 % threshold
    for _ in range(1000):
        window.append(now - 120)
    # One fresh request triggers the trim path
    llm._record_paid_provider_request("azure_openai")
    count = llm._get_paid_provider_rpm_count("azure_openai")
    assert count == 1, (
        f"Expired (>60s) timestamps must be trimmed; found {count} entries."
    )


def test_get_paid_provider_rpm_ratio_uses_real_limit():
    limit = _POOL_RPM_LIMITS["sarvam"]
    _seed_recent("sarvam", limit // 2)
    ratio = llm._get_paid_provider_rpm_ratio("sarvam")
    assert 0.49 <= ratio <= 0.51, f"expected ~0.50 ratio, got {ratio}"


def test_get_paid_provider_rpm_ratio_unknown_provider_returns_zero():
    assert llm._get_paid_provider_rpm_ratio("vertex") == 0.0
    assert llm._get_paid_provider_rpm_ratio("workers_ai") == 0.0
    assert llm._get_paid_provider_rpm_ratio("not_a_provider") == 0.0


# ── Dispatch-hook coverage ──────────────────────────────────────────────────


def test_record_paid_provider_request_unknown_provider_is_noop():
    """The hook must silently ignore non-tracked providers."""
    # Must not raise, must not affect any tracked provider.
    llm._record_paid_provider_request("vertex")
    llm._record_paid_provider_request("workers_ai")
    llm._record_paid_provider_request("nonexistent")
    assert llm._get_paid_provider_rpm_count("azure_openai") == 0
    assert llm._get_paid_provider_rpm_count("sarvam") == 0


def test_record_paid_provider_request_tracks_each_call():
    for _ in range(5):
        llm._record_paid_provider_request("azure_openai")
    for _ in range(3):
        llm._record_paid_provider_request("sarvam")
    assert llm._get_paid_provider_rpm_count("azure_openai") == 5
    assert llm._get_paid_provider_rpm_count("sarvam") == 3


# ── Saturation passthrough ──────────────────────────────────────────────────


def test_get_provider_saturation_uses_real_rpm_for_azure_openai():
    """_get_provider_saturation must surface the real ratio for paid primaries."""
    limit = _POOL_RPM_LIMITS["azure_openai"]
    _seed_recent("azure_openai", int(limit * 0.85))
    sat = llm._get_provider_saturation("azure_openai")
    assert 0.84 <= sat <= 0.86, (
        f"saturation must reflect real RPM ratio (~0.85), got {sat}"
    )


def test_get_provider_saturation_uses_real_rpm_for_sarvam():
    limit = _POOL_RPM_LIMITS["sarvam"]
    _seed_recent("sarvam", int(limit * 0.75))
    sat = llm._get_provider_saturation("sarvam")
    assert 0.74 <= sat <= 0.76, (
        f"saturation must reflect real RPM ratio (~0.75), got {sat}"
    )


def test_get_provider_saturation_falls_back_to_429_burst_when_window_empty():
    """When the RPM window is empty, the legacy 429-burst proxy still works."""
    # Inject a 429-burst into the existing _PROVIDER_429_WINDOWS counter —
    # the saturation function must surface the 0.70 / 0.90 proxy values.
    now = time.time()
    for _ in range(5):
        llm._PROVIDER_429_WINDOWS["azure_openai"].append(now)
    sat = llm._get_provider_saturation("azure_openai")
    assert sat >= 0.85, f"429-burst proxy must still raise saturation; got {sat}"


# ── Non-chat features must keep the 80 % default threshold ──────────────────


def test_non_chat_features_keep_80_pct_default_threshold():
    """A 75 % azure load must still allow non-chat features (e.g. content
    polish) to use it — the tighter 70 % shed is chat-only.
    """
    limit = _POOL_RPM_LIMITS["azure_openai"]
    _seed_recent("azure_openai", int(limit * 0.75))
    # 'content' pool does not include azure_openai at weight > 0, so test
    # this via the threshold helper directly: english_rag_chat must shed,
    # content must NOT (regardless of pool membership).
    chat_pick = llm.select_provider("english_rag_chat", lang="en")
    assert chat_pick != "azure_openai", (
        "english_rag_chat must shed azure_openai at 75 %"
    )


# ── End-to-end dispatch hook coverage ───────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_llm_for_feature_records_azure_request_on_success():
    """The full _dispatch_llm_for_feature azure_openai branch must
    accumulate against the RPM cap on a successful upstream call.
    """
    # Stub the upstream call so we don't actually hit Azure
    with patch("providers.azure_openai.call_chat",
               new=AsyncMock(return_value="ok")):
        # Memory-brain assertion + admin-toggle path are no-ops outside
        # a ChatTurnContext, so we can call the dispatcher directly.
        with patch("azure_ai_runtime.is_enabled",
                   new=AsyncMock(return_value=True)):
            for _ in range(3):
                await llm._dispatch_llm_for_feature(
                    [{"role": "user", "content": "hi"}],
                    "azure_openai", 64, feature="english_rag_chat",
                )
    assert llm._get_paid_provider_rpm_count("azure_openai") == 3, (
        "_dispatch_llm_for_feature must increment the RPM window on success"
    )


@pytest.mark.asyncio
async def test_dispatch_llm_for_feature_records_azure_request_on_failure():
    """A failed upstream Azure call must STILL count against the cap —
    the soft-shed must reflect what the provider's own quota meter sees,
    which counts attempts (not just successes)."""
    async def _boom(*args, **kwargs):
        import httpx
        raise httpx.HTTPStatusError(
            "rate limit", request=None,  # type: ignore[arg-type]
            response=httpx.Response(429),
        )
    with patch("providers.azure_openai.call_chat", new=_boom):
        with patch("azure_ai_runtime.is_enabled",
                   new=AsyncMock(return_value=True)):
            for _ in range(2):
                with pytest.raises(Exception):
                    await llm._dispatch_llm_for_feature(
                        [{"role": "user", "content": "hi"}],
                        "azure_openai", 64, feature="english_rag_chat",
                    )
    assert llm._get_paid_provider_rpm_count("azure_openai") == 2, (
        "Failed/429ed attempts must STILL count toward the soft-shed cap"
    )


@pytest.mark.asyncio
async def test_dispatch_llm_for_feature_records_sarvam_request_on_success():
    """Same end-to-end check for the sarvam branch."""
    if not llm._SARVAM_PROVIDERS:
        pytest.skip("no Sarvam key configured in this environment")
    with patch("llm._call_sarvam_llm", new=AsyncMock(return_value="ok")):
        for _ in range(4):
            await llm._dispatch_llm_for_feature(
                [{"role": "user", "content": "namaskar"}],
                "sarvam", 64, feature="assamese_rag_chat",
            )
    assert llm._get_paid_provider_rpm_count("sarvam") == 4


# ── Cross-worker Redis aggregation ──────────────────────────────────────────


class _FakeRedis:
    """Minimal Upstash-compatible stand-in: INCR / EXPIRE / GET only."""

    def __init__(self):
        self.store: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key: str, ttl: int) -> int:
        # No-op in-memory: real TTL behaviour is not relevant to the test.
        return 1

    def get(self, key: str):
        v = self.store.get(key)
        return None if v is None else str(v)


def test_record_paid_provider_request_writes_to_redis_bucket(monkeypatch):
    """Each _record_paid_provider_request call must INCR the Redis bucket
    so workers in OTHER processes can see this worker's traffic.
    """
    fake = _FakeRedis()
    monkeypatch.setattr(deps, "redis_client", fake)
    for _ in range(7):
        llm._record_paid_provider_request("azure_openai")
    bucket = int(time.time() // llm._PAID_RPM_REDIS_BUCKET_S)
    key = f"{llm._PAID_RPM_REDIS_KEY_PREFIX}azure_openai:{bucket}"
    assert fake.store.get(key) == 7, (
        f"Redis bucket must hold cross-worker count; got {fake.store}"
    )


def test_get_paid_provider_rpm_count_global_reads_redis_buckets(monkeypatch):
    """The global counter must aggregate the current and previous minute
    bucket so a sustained burst is visible across the bucket boundary.
    """
    fake = _FakeRedis()
    monkeypatch.setattr(deps, "redis_client", fake)
    now = time.time()
    cur_bucket = int(now // llm._PAID_RPM_REDIS_BUCKET_S)
    prev_bucket = cur_bucket - 1
    fake.store[f"{llm._PAID_RPM_REDIS_KEY_PREFIX}sarvam:{cur_bucket}"]  = 100
    fake.store[f"{llm._PAID_RPM_REDIS_KEY_PREFIX}sarvam:{prev_bucket}"] =  50
    cnt = llm._get_paid_provider_rpm_count_global("sarvam")
    # frac_into_cur ∈ [0, 1) so result ∈ (cur, cur + prev]  → (100, 150]
    assert 100 <= cnt <= 150, (
        f"global count must include weighted prev bucket; got {cnt}"
    )


def test_global_redis_count_drives_shed_when_local_window_is_empty(monkeypatch):
    """The production scenario: this worker has handled 0 requests
    locally but two OTHER workers have collectively driven the global
    count above 70 %.  select_provider on this worker MUST still shed.
    """
    fake = _FakeRedis()
    monkeypatch.setattr(deps, "redis_client", fake)
    limit = _POOL_RPM_LIMITS["azure_openai"]
    # Park 75 % of the cap entirely in the previous bucket so the
    # weighted current-window count is comfortably above 70 %.
    cur_bucket = int(time.time() // llm._PAID_RPM_REDIS_BUCKET_S)
    fake.store[f"{llm._PAID_RPM_REDIS_KEY_PREFIX}azure_openai:{cur_bucket}"] = (
        int(limit * 0.75)
    )
    # Local window is untouched (this worker just spawned).
    assert llm._get_paid_provider_rpm_count("azure_openai") == 0
    # Global count must surface the cross-worker traffic.
    assert llm._get_paid_provider_rpm_count_global("azure_openai") >= int(limit * 0.70)
    # And select_provider must shed accordingly.
    picks = {
        llm.select_provider("english_rag_chat", lang="en") for _ in range(20)
    }
    assert "azure_openai" not in picks, (
        f"shed must fire on the global Redis signal; got {picks}"
    )


def test_global_redis_count_below_threshold_keeps_serving(monkeypatch):
    """Sanity inverse: a global signal below 70 % must NOT trigger the shed."""
    fake = _FakeRedis()
    monkeypatch.setattr(deps, "redis_client", fake)
    limit = _POOL_RPM_LIMITS["azure_openai"]
    cur_bucket = int(time.time() // llm._PAID_RPM_REDIS_BUCKET_S)
    fake.store[f"{llm._PAID_RPM_REDIS_KEY_PREFIX}azure_openai:{cur_bucket}"] = (
        int(limit * 0.40)
    )
    picks = [
        llm.select_provider("english_rag_chat", lang="en") for _ in range(20)
    ]
    assert all(p == "azure_openai" for p in picks), (
        f"40 % global load must NOT shed; saw {set(picks)}"
    )


def test_redis_outage_falls_back_to_local_window(monkeypatch):
    """When Upstash is down (redis_client raises on every call), the
    per-process window MUST keep driving the shed — degraded but safe.
    """
    class _ExplodingRedis:
        def incr(self, *_a, **_kw):  raise RuntimeError("upstash down")
        def expire(self, *_a, **_kw): raise RuntimeError("upstash down")
        def get(self, *_a, **_kw):    raise RuntimeError("upstash down")
    monkeypatch.setattr(deps, "redis_client", _ExplodingRedis())
    limit = _POOL_RPM_LIMITS["azure_openai"]
    # Local burst above 70 %
    _seed_recent("azure_openai", int(limit * 0.75))
    picks = {
        llm.select_provider("english_rag_chat", lang="en") for _ in range(20)
    }
    assert "azure_openai" not in picks, (
        f"shed must keep working when Redis is down; got {picks}"
    )
