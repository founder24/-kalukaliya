"""Task #571 round-8 — fleet-wide rolling 24h hit-ratio tests.

Round-8 architect rejection: `HitRatio` was a process-lifetime
cumulative ratio that the alarm could never trip after a long warmup
period. Fix: every hit/miss writes a single Redis INCR to an hourly
bucket key with 25h TTL; snapshot reads the last 24 buckets and
reports `hit_ratio_24h`. Because Redis is shared across all backend
replicas, this is also fleet-wide — addresses the second blocking
finding in the same round.

Tests pin:
  1. A hit + a miss write to the right Redis hourly bucket keys.
  2. snapshot() reads the 24h buckets and reports hit_ratio_24h
     correctly + provides a totals.hit_ratio_24h rollup.
  3. Redis outage falls back to 0/0/0.0 with explicit
     `hr24_source = "redis_unavailable"` (no silent inversion of the
     alarm direction).
  4. Aggregation across replicas is implicit because Redis is shared
     — verified by simulating two "replicas" writing to the same fake
     Redis and the snapshot returning the SUM.
"""
from __future__ import annotations

import time

import ai_input_cache as aic


class _FakeRedis:
    """Minimal stand-in for the Upstash/redis-py client. Implements
    only what `_record_24h_event` + `_read_24h_totals` need."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    def expire(self, key: str, ttl: int) -> None:
        self.expires[key] = ttl

    def get(self, key: str):
        v = self.store.get(key)
        return None if v is None else str(v).encode()

    def set(self, key: str, value, ex: int | None = None) -> None:
        self.store[key] = value
        if ex is not None:
            self.expires[key] = ex


def _install_fake_redis(monkeypatch) -> _FakeRedis:
    fr = _FakeRedis()
    monkeypatch.setattr(aic, "_redis_client", lambda: fr)
    return fr


def test_hit_and_miss_write_hourly_bucket_keys(monkeypatch) -> None:
    aic.reset_for_tests()
    fr = _install_fake_redis(monkeypatch)
    bucket = aic._hr24_bucket()

    aic.set_response(
        [{"role": "user", "content": "x"}],
        model="m1", text="answer-1",
        content_type="mcq", template_version="v1",
    )
    val = aic.get_response(
        [{"role": "user", "content": "x"}],
        model="m1", content_type="mcq", template_version="v1",
    )
    assert val == "answer-1"
    aic.get_response(
        [{"role": "user", "content": "y"}],  # cold miss
        model="m1", content_type="mcq", template_version="v1",
    )

    assert fr.store.get(f"aic:hr24:mcq:{bucket}:hits") == 1
    assert fr.store.get(f"aic:hr24:mcq:{bucket}:misses") == 1
    # TTL recorded.
    assert fr.expires.get(f"aic:hr24:mcq:{bucket}:hits") == 25 * 3600


def test_snapshot_reports_rolling_24h_ratio(monkeypatch) -> None:
    aic.reset_for_tests()
    fr = _install_fake_redis(monkeypatch)
    bucket = aic._hr24_bucket()
    # 7 hits + 3 misses spread across two prior hour buckets.
    fr.store[f"aic:hr24:mcq:{bucket}:hits"] = 4
    fr.store[f"aic:hr24:mcq:{bucket - 1}:hits"] = 3
    fr.store[f"aic:hr24:mcq:{bucket}:misses"] = 2
    fr.store[f"aic:hr24:mcq:{bucket - 5}:misses"] = 1

    snap = aic.snapshot()
    row = snap["content_types"]["mcq"]
    assert row["hits_24h"] == 7
    assert row["misses_24h"] == 3
    assert row["hit_ratio_24h"] == round(7 / 10, 4)

    totals = snap["totals"]
    assert totals["hits_24h"] == 7
    assert totals["misses_24h"] == 3
    assert totals["hit_ratio_24h"] == round(7 / 10, 4)
    assert totals["hr24_source"] == "redis_hourly_buckets"


def test_redis_outage_falls_back_explicitly(monkeypatch) -> None:
    aic.reset_for_tests()
    monkeypatch.setattr(aic, "_redis_client", lambda: None)
    snap = aic.snapshot()
    totals = snap["totals"]
    assert totals["hits_24h"] == 0
    assert totals["misses_24h"] == 0
    assert totals["hit_ratio_24h"] == 0.0
    # Explicit signal so the alarm "missing data = breaching" semantics
    # cannot be confused for a healthy 0% reading.
    assert totals["hr24_source"] == "redis_unavailable"


def test_two_replicas_aggregate_via_shared_redis(monkeypatch) -> None:
    """Simulates two backend replicas: each calls _record_24h_event
    with the SAME fake Redis. snapshot() must return the SUM, proving
    the aggregation is fleet-wide rather than per-process."""
    aic.reset_for_tests()
    fr = _install_fake_redis(monkeypatch)
    bucket = aic._hr24_bucket()

    # "Replica A" records 3 hits + 1 miss.
    for _ in range(3):
        aic._record_24h_event("flashcard", "hits")
    aic._record_24h_event("flashcard", "misses")
    # "Replica B" records 2 more hits + 1 more miss.
    for _ in range(2):
        aic._record_24h_event("flashcard", "hits")
    aic._record_24h_event("flashcard", "misses")

    assert fr.store[f"aic:hr24:flashcard:{bucket}:hits"] == 5
    assert fr.store[f"aic:hr24:flashcard:{bucket}:misses"] == 2

    snap = aic.snapshot()
    row = snap["content_types"]["flashcard"]
    assert row["hits_24h"] == 5
    assert row["misses_24h"] == 2
    assert row["hit_ratio_24h"] == round(5 / 7, 4)
