"""Task #361 §2 — embedding cache tests."""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("EMBED_CACHE_ENABLED", "1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeRedis:
    def __init__(self):
        self.kv: dict = {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v, ex=None):
        self.kv[k] = v
        return "OK"

    def incr(self, k):
        self.kv[k] = str(int(self.kv.get(k, "0") or "0") + 1)
        return int(self.kv[k])


def _install(monkeypatch):
    fake = _FakeRedis()
    import deps as _deps
    monkeypatch.setattr(_deps, "redis_client", fake, raising=False)
    return fake


def test_miss_returns_none(monkeypatch):
    _install(monkeypatch)
    import embed_cache
    assert embed_cache.get_cached_embedding("hello world") is None


def test_set_then_get_roundtrip(monkeypatch):
    _install(monkeypatch)
    import embed_cache
    vec = [0.1] * 1024
    assert embed_cache.set_cached_embedding("Photosynthesis explained", vec) is True
    got = embed_cache.get_cached_embedding("Photosynthesis explained")
    assert got == vec


def test_normalization_collapses_whitespace_and_case(monkeypatch):
    _install(monkeypatch)
    import embed_cache
    vec = [0.5, 0.5, 0.5]
    embed_cache.set_cached_embedding("What is mitosis?", vec)
    # Same content, different casing + whitespace should hit the same key.
    assert embed_cache.get_cached_embedding("WHAT  IS\tMITOSIS?") == vec


def test_different_task_types_use_different_keys(monkeypatch):
    _install(monkeypatch)
    import embed_cache
    vec_doc = [0.1, 0.2]
    vec_query = [0.9, 0.8]
    embed_cache.set_cached_embedding("x", vec_doc, task_type="RETRIEVAL_DOCUMENT")
    embed_cache.set_cached_embedding("x", vec_query, task_type="RETRIEVAL_QUERY")
    assert embed_cache.get_cached_embedding("x", task_type="RETRIEVAL_DOCUMENT") == vec_doc
    assert embed_cache.get_cached_embedding("x", task_type="RETRIEVAL_QUERY") == vec_query


def test_kill_switch_disables_cache(monkeypatch):
    fake = _install(monkeypatch)
    fake.kv["cache:embed_enabled"] = "0"
    import embed_cache
    vec = [0.1] * 4
    assert embed_cache.set_cached_embedding("x", vec) is False
    assert embed_cache.get_cached_embedding("x") is None


def test_no_redis_means_no_cache(monkeypatch):
    import deps as _deps
    monkeypatch.setattr(_deps, "redis_client", None, raising=False)
    import embed_cache
    assert embed_cache.get_cached_embedding("x") is None
    assert embed_cache.set_cached_embedding("x", [0.1]) is False


def test_empty_inputs_are_safe(monkeypatch):
    _install(monkeypatch)
    import embed_cache
    assert embed_cache.get_cached_embedding("") is None
    assert embed_cache.get_cached_embedding("   ") is None
    assert embed_cache.set_cached_embedding("x", []) is False
    assert embed_cache.set_cached_embedding("", [0.1]) is False


def test_hit_and_miss_counters_increment(monkeypatch):
    fake = _install(monkeypatch)
    import embed_cache
    embed_cache.get_cached_embedding("nope")  # miss
    assert int(fake.kv.get("embed:cache:misses", "0")) == 1
    embed_cache.set_cached_embedding("yes", [0.1, 0.2])
    embed_cache.get_cached_embedding("yes")  # hit
    assert int(fake.kv.get("embed:cache:hits", "0")) == 1


def test_oversized_entry_skipped(monkeypatch):
    _install(monkeypatch)
    import embed_cache
    # 64 KB cap — a 100k-element list of 1.0 floats serialises well past that.
    huge = [1.0] * 100_000
    assert embed_cache.set_cached_embedding("huge", huge) is False
    assert embed_cache.get_cached_embedding("huge") is None
