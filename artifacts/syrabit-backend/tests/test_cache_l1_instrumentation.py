"""Task #571 round-5 — L1 instrumentation accuracy regression tests.

Architect rejected round-4 because the previous `_InstrumentedTTLCache`
double-counted hits — `cachetools.Cache.get()` calls `if key in self:
return self[key]`, so a `get()`-hit incremented both the `__contains__`
counter AND the `__getitem__` counter.

These tests pin the post-fix semantics:

- `cache.get(key)` on a present key → exactly 1 hit (not 2).
- `cache.get(key)` on an absent key → exactly 1 miss (not 2).
- `cache[key]` on a present key → exactly 1 hit.
- `key in cache` on a present key → exactly 1 hit (standalone query).
- `cache[key] = value` → exactly 1 set.

The accuracy of every CloudWatch alarm and the admin panel hit-ratio
banner depends on these invariants — without them, a paging regression
in `cache-ai-hitratio-low` could be masked by an inflated ratio.
"""
from __future__ import annotations

import importlib

import cache as _c


def _reset(name: str) -> None:
    _c.l1_counters_reset_for_tests()
    # The reset wipes pre-registered rows; reaching the row through the
    # snapshot path ensures the test sees the post-init defaults.
    _c.l1_counters_snapshot()


def _row(name: str) -> dict:
    return _c.l1_counters_snapshot().get(name, {"hits": 0, "misses": 0, "sets": 0})


def test_get_hit_records_exactly_one_hit() -> None:
    ring = _c._InstrumentedTTLCache(maxsize=4, ttl=60, name="t_get_hit")
    _reset("t_get_hit")
    ring["k"] = "v"  # +1 set
    assert _row("t_get_hit")["sets"] == 1

    out = ring.get("k")
    assert out == "v"
    row = _row("t_get_hit")
    # Must be exactly 1 — the architect's rejection said this was 2.
    assert row["hits"] == 1, f"expected 1 hit on get(), got {row['hits']}"
    assert row["misses"] == 0


def test_get_miss_records_exactly_one_miss() -> None:
    ring = _c._InstrumentedTTLCache(maxsize=4, ttl=60, name="t_get_miss")
    _reset("t_get_miss")

    out = ring.get("absent", "fallback")
    assert out == "fallback"
    row = _row("t_get_miss")
    assert row["misses"] == 1, f"expected 1 miss on get(), got {row['misses']}"
    assert row["hits"] == 0


def test_subscript_hit_records_one_hit() -> None:
    ring = _c._InstrumentedTTLCache(maxsize=4, ttl=60, name="t_sub_hit")
    _reset("t_sub_hit")
    ring["k"] = "v"
    _ = ring["k"]
    row = _row("t_sub_hit")
    assert row["hits"] == 1
    assert row["misses"] == 0


def test_contains_hit_records_one_hit_independently() -> None:
    ring = _c._InstrumentedTTLCache(maxsize=4, ttl=60, name="t_contains")
    _reset("t_contains")
    ring["k"] = "v"
    assert "k" in ring
    assert "absent" not in ring
    row = _row("t_contains")
    assert row["hits"] == 1
    assert row["misses"] == 1


def test_get_then_contains_counts_each_separately() -> None:
    """A `get()` followed by an `in` check is two distinct read attempts
    and must register two counter increments — not three."""
    ring = _c._InstrumentedTTLCache(maxsize=4, ttl=60, name="t_combo")
    _reset("t_combo")
    ring["k"] = "v"
    _ = ring.get("k")        # +1 hit
    assert "k" in ring        # +1 hit
    row = _row("t_combo")
    assert row["hits"] == 2
    assert row["misses"] == 0


def test_setitem_increments_sets_only() -> None:
    ring = _c._InstrumentedTTLCache(maxsize=4, ttl=60, name="t_set")
    _reset("t_set")
    ring["a"] = 1
    ring["b"] = 2
    ring["a"] = 3  # overwrite still counts
    row = _row("t_set")
    assert row["sets"] == 3
    assert row["hits"] == 0
    assert row["misses"] == 0
