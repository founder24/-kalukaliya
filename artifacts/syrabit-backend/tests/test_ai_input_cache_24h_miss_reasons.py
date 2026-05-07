"""Task #571 round-7 — 24h-windowed miss-reason aggregation tests.

Round-6 architect rejection said the panel + alarms were reading
process-lifetime miss-reason counters, so a one-off flood after a
deploy could dominate the "top miss" view for weeks. The fix adds a
per-CT `miss_reasons_24h` ring that ages out on each append and a
`totals.top_miss_reasons_24h` ranked aggregate in `snapshot()`.

These tests pin:
  1. A miss bumps both the lifetime counter AND the 24h ring.
  2. Entries older than 24h are dropped from the 24h aggregate.
  3. `snapshot().totals.top_miss_reasons_24h` is sorted desc by count
     and only carries reasons with count > 0.
  4. The per-CT `miss_reasons_24h` block matches the rolled-up totals.
"""
from __future__ import annotations

import ai_input_cache as aic


def _miss(ct: str, key: str = "k1") -> None:
    """Force a miss by calling get_response with no prior set."""
    aic.get_response(
        [{"role": "user", "content": key}],
        model=f"test-model-{key}",
        content_type=ct,
        template_version="v1",
    )


def test_miss_bumps_lifetime_and_24h_counters() -> None:
    aic.reset_for_tests()
    _miss("mcq", "fresh-1")
    snap = aic.snapshot()
    row = snap["content_types"]["mcq"]
    assert row["misses"] == 1
    assert sum(row["miss_reasons"].values()) == 1
    assert sum(row["miss_reasons_24h"].values()) == 1


def test_aged_entries_drop_from_24h_aggregate() -> None:
    aic.reset_for_tests()
    _miss("flashcard", "old-1")
    _miss("flashcard", "old-2")
    # Backdate both entries past the 24h cutoff.
    bucket = aic._COUNTERS["flashcard"]["miss_reasons_24h"]
    aged = [(ts - 86_500.0, r) for ts, r in bucket]
    aic._COUNTERS["flashcard"]["miss_reasons_24h"] = aged
    # Trigger a fresh miss → the append-side age-out drops the old
    # entries, leaving only the new one in the 24h ring.
    _miss("flashcard", "fresh")
    snap = aic.snapshot()
    row = snap["content_types"]["flashcard"]
    # Lifetime counters retain everything.
    assert row["misses"] == 3
    # 24h aggregate carries only the fresh miss.
    assert sum(row["miss_reasons_24h"].values()) == 1


def test_top_miss_reasons_24h_is_sorted_desc_and_filters_zero() -> None:
    aic.reset_for_tests()
    # 3 cold misses + 1 template_version_bump.
    _miss("definition", "a")
    _miss("definition", "b")
    _miss("definition", "c")
    # Force template_version_bump via a different template_version
    # after a set on the same content_type.
    aic.set_response(
        [{"role": "user", "content": "x"}],
        model="m1", text="v",
        content_type="definition", template_version="v1",
    )
    aic.get_response(
        [{"role": "user", "content": "x"}],
        model="m1",
        content_type="definition", template_version="v2",
    )
    snap = aic.snapshot()
    top = snap["totals"]["top_miss_reasons_24h"]
    # All entries non-zero, sorted desc by count.
    assert all(item["count"] > 0 for item in top)
    counts = [item["count"] for item in top]
    assert counts == sorted(counts, reverse=True)
    # Reason names are JSON-safe strings.
    assert all(isinstance(item["reason"], str) for item in top)


def test_per_ct_24h_matches_totals_rollup() -> None:
    aic.reset_for_tests()
    _miss("mcq", "k1")
    _miss("mcq", "k2")
    _miss("flashcard", "k3")
    snap = aic.snapshot()
    per_ct_total = 0
    for entry in snap["content_types"].values():
        per_ct_total += sum(entry.get("miss_reasons_24h", {}).values())
    rollup_total = sum(snap["totals"]["miss_reasons_24h"].values())
    assert per_ct_total == rollup_total == 3
