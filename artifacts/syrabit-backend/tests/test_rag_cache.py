"""Task #361 §1 — RAG result cache (shadow mode) tests."""
from __future__ import annotations

import os
import sys

os.environ.setdefault("RAG_CACHE_ENABLED", "1")
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

    def delete(self, k):
        self.kv.pop(k, None)
        return 1

    def scan(self, cursor, match=None, count=200):
        # very small glob: exact prefix up to first '*'
        if not match:
            return [0, list(self.kv.keys())]
        # match like "rag:answer:*:v1:*"
        parts = match.split("*")
        keys = [
            k for k in self.kv.keys()
            if all(p in k for p in parts if p) and k.startswith(parts[0])
        ]
        return [0, keys]


def _install(monkeypatch):
    fake = _FakeRedis()
    import deps as _deps
    monkeypatch.setattr(_deps, "redis_client", fake, raising=False)
    return fake


def test_shadow_mode_records_but_does_not_serve(monkeypatch):
    fake = _install(monkeypatch)
    import rag_cache
    payload = {"chunks": [{"id": "c1", "text": "x"}], "answer": "Photosynthesis is..."}
    assert rag_cache.record_rag_result(
        "What is photosynthesis?", payload,
        retriever="vertex", top_k=8, lang="en", curriculum_version="v1",
    ) is True
    # Even though the entry is cached, shadow mode must NOT serve it.
    got = rag_cache.get_cached_rag_result(
        "What is photosynthesis?",
        retriever="vertex", top_k=8, lang="en", curriculum_version="v1",
    )
    assert got is None
    # But the hit counter must have incremented (operator metric).
    assert int(fake.kv.get("rag:cache:hits", "0")) == 1
    assert int(fake.kv.get("rag:cache:shadow_writes", "0")) == 1


def test_serve_flag_graduates_to_live(monkeypatch):
    fake = _install(monkeypatch)
    fake.kv["cache:rag_serve_enabled"] = "1"
    import rag_cache
    payload = {"chunks": [], "answer": "Mitosis is..."}
    rag_cache.record_rag_result(
        "What is mitosis?", payload,
        retriever="vertex", top_k=8, lang="en", curriculum_version="v1",
    )
    got = rag_cache.get_cached_rag_result(
        "What is mitosis?",
        retriever="vertex", top_k=8, lang="en", curriculum_version="v1",
    )
    assert got == payload


def test_kill_switch_disables_writes(monkeypatch):
    fake = _install(monkeypatch)
    fake.kv["cache:rag_enabled"] = "0"
    import rag_cache
    assert rag_cache.record_rag_result("q", {"x": 1}) is False


def test_curriculum_version_isolates_keys(monkeypatch):
    fake = _install(monkeypatch)
    fake.kv["cache:rag_serve_enabled"] = "1"
    import rag_cache
    rag_cache.record_rag_result("q", {"v": 1}, curriculum_version="v1")
    rag_cache.record_rag_result("q", {"v": 2}, curriculum_version="v2")
    assert rag_cache.get_cached_rag_result("q", curriculum_version="v1") == {"v": 1}
    assert rag_cache.get_cached_rag_result("q", curriculum_version="v2") == {"v": 2}


def test_invalidate_curriculum_version(monkeypatch):
    fake = _install(monkeypatch)
    fake.kv["cache:rag_serve_enabled"] = "1"
    import rag_cache
    rag_cache.record_rag_result("q1", {"a": 1}, curriculum_version="v1")
    rag_cache.record_rag_result("q2", {"a": 2}, curriculum_version="v1")
    rag_cache.record_rag_result("q3", {"a": 3}, curriculum_version="v2")
    deleted = rag_cache.invalidate_curriculum_version("v1")
    assert deleted == 2
    # v2 entry should still be retrievable.
    assert rag_cache.get_cached_rag_result("q3", curriculum_version="v2") == {"a": 3}
    # v1 entries should be gone.
    assert rag_cache.get_cached_rag_result("q1", curriculum_version="v1") is None


def test_no_redis_means_no_op(monkeypatch):
    import deps as _deps
    monkeypatch.setattr(_deps, "redis_client", None, raising=False)
    import rag_cache
    assert rag_cache.record_rag_result("q", {"x": 1}) is False
    assert rag_cache.get_cached_rag_result("q") is None
    assert rag_cache.invalidate_curriculum_version("v1") == 0


def test_oversized_entry_skipped(monkeypatch):
    _install(monkeypatch)
    import rag_cache
    huge = {"chunks": [{"text": "a" * 1000}] * 1000}  # well over 256 KB
    assert rag_cache.record_rag_result("q", huge) is False


def test_empty_inputs_safe(monkeypatch):
    _install(monkeypatch)
    import rag_cache
    assert rag_cache.record_rag_result("", {"a": 1}) is False
    assert rag_cache.record_rag_result("q", None) is False
    assert rag_cache.get_cached_rag_result("") is None
