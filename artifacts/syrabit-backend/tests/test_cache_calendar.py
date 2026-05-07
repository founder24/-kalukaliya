"""Task #575 — pinned tests for `cache_calendar`.

Exercises the YAML parser, season classification at every relevant
boundary, the next-transition helper, the season-aware TTL helper,
and the `/api/health/season` payload shape. The file path overrides
guarantee tests don't depend on the production calendar (so a real
calendar edit can't silently green a broken release).
"""
from __future__ import annotations

import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

import cache_calendar
from cache_calendar import (
    EXAM_STRETCH_CONTENT_TYPES,
    EXAM_TTL_SEC,
    NORMAL_TTL_SEC,
    SEASON_EXAM,
    SEASON_NORMAL,
    SEASON_RESULTS,
    ai_cache_ttl_for,
    current_season,
    health_payload,
    load_windows,
    next_transition,
    reset_for_tests,
)


@pytest.fixture
def fixture_calendar(tmp_path: Path) -> Path:
    p = tmp_path / "exam_calendar.yaml"
    p.write_text(
        textwrap.dedent(
            """
            windows:
              - name: "AHSEC HS Final 2026"
                kind: "exam"
                start: "2026-02-10"
                end:   "2026-03-15"
              - name: "AHSEC + SEBA Results 2026"
                kind: "results"
                start: "2026-05-25"
                end:   "2026-06-15"
              - name: "AHSEC HS Final 2027"
                kind: "exam"
                start: "2027-02-09"
                end:   "2027-03-14"
            """
        ).strip()
    )
    reset_for_tests(p)
    yield p
    reset_for_tests(None)


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


# ── current_season pinned boundaries ──────────────────────────────────
def test_normal_before_first_window(fixture_calendar):
    assert current_season(_ts("2026-01-15T00:00:00")) == SEASON_NORMAL


def test_exam_on_start_day(fixture_calendar):
    assert current_season(_ts("2026-02-10T00:00:00")) == SEASON_EXAM


def test_exam_on_end_day(fixture_calendar):
    assert current_season(_ts("2026-03-15T23:00:00")) == SEASON_EXAM


def test_normal_day_after_exam(fixture_calendar):
    assert current_season(_ts("2026-03-16T00:00:00")) == SEASON_NORMAL


def test_results_window(fixture_calendar):
    assert current_season(_ts("2026-05-25T00:00:00")) == SEASON_RESULTS
    assert current_season(_ts("2026-06-01T12:00:00")) == SEASON_RESULTS
    assert current_season(_ts("2026-06-15T23:30:00")) == SEASON_RESULTS


def test_normal_after_results_before_next_exam(fixture_calendar):
    assert current_season(_ts("2026-09-01T00:00:00")) == SEASON_NORMAL


def test_naive_datetime_treated_as_utc(fixture_calendar):
    assert current_season(datetime(2026, 2, 10, 0, 0, 0)) == SEASON_EXAM


def test_default_now_uses_utc(fixture_calendar):
    # Just verify the default-arg branch doesn't crash — the value is
    # whatever today happens to be against the fixture calendar.
    assert current_season() in (SEASON_NORMAL, SEASON_EXAM, SEASON_RESULTS)


# ── next_transition ───────────────────────────────────────────────────
def test_next_transition_in_normal(fixture_calendar):
    nxt = next_transition(_ts("2026-01-15T00:00:00"))
    assert nxt == {"at": "2026-02-10", "to": SEASON_EXAM, "window": "AHSEC HS Final 2026"}


def test_next_transition_inside_exam(fixture_calendar):
    nxt = next_transition(_ts("2026-02-20T00:00:00"))
    assert nxt["at"] == "2026-03-16"
    assert nxt["to"] == SEASON_NORMAL


def test_next_transition_inside_results(fixture_calendar):
    nxt = next_transition(_ts("2026-06-01T00:00:00"))
    assert nxt["at"] == "2026-06-16"
    assert nxt["to"] == SEASON_NORMAL


def test_next_transition_after_last_window(fixture_calendar):
    assert next_transition(_ts("2030-01-01T00:00:00")) is None


# ── TTL helper ────────────────────────────────────────────────────────
@pytest.mark.parametrize("ct", sorted(EXAM_STRETCH_CONTENT_TYPES))
def test_stretched_ttls_in_exam(fixture_calendar, ct):
    assert ai_cache_ttl_for(ct, season=SEASON_EXAM) == EXAM_TTL_SEC


@pytest.mark.parametrize("ct", sorted(EXAM_STRETCH_CONTENT_TYPES))
def test_stretched_ttls_in_results(fixture_calendar, ct):
    assert ai_cache_ttl_for(ct, season=SEASON_RESULTS) == EXAM_TTL_SEC


@pytest.mark.parametrize("ct", sorted(EXAM_STRETCH_CONTENT_TYPES))
def test_normal_ttl_for_stretched_in_normal(fixture_calendar, ct):
    assert ai_cache_ttl_for(ct, season=SEASON_NORMAL) == NORMAL_TTL_SEC


@pytest.mark.parametrize("ct", ["formatter", "translate", "ocr", "stage3_polish", "unknown", None])
def test_non_stretched_keeps_normal_ttl(fixture_calendar, ct):
    assert ai_cache_ttl_for(ct, season=SEASON_EXAM) == NORMAL_TTL_SEC
    assert ai_cache_ttl_for(ct, season=SEASON_RESULTS) == NORMAL_TTL_SEC
    assert ai_cache_ttl_for(ct, season=SEASON_NORMAL) == NORMAL_TTL_SEC


def test_ttl_helper_uses_current_season_when_omitted(fixture_calendar, monkeypatch):
    monkeypatch.setattr(cache_calendar, "current_season", lambda now=None: SEASON_EXAM)
    assert ai_cache_ttl_for("mcq") == EXAM_TTL_SEC
    monkeypatch.setattr(cache_calendar, "current_season", lambda now=None: SEASON_NORMAL)
    assert ai_cache_ttl_for("mcq") == NORMAL_TTL_SEC


# ── Loader / validation ───────────────────────────────────────────────
def test_overlap_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "windows:\n"
        "  - {name: A, kind: exam, start: 2026-02-10, end: 2026-03-15}\n"
        "  - {name: B, kind: exam, start: 2026-03-10, end: 2026-04-01}\n"
    )
    with pytest.raises(ValueError, match="overlapping"):
        load_windows(p)


def test_inverted_dates_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "windows:\n"
        "  - {name: A, kind: exam, start: 2026-03-15, end: 2026-02-10}\n"
    )
    with pytest.raises(ValueError, match="after end"):
        load_windows(p)


def test_unknown_kind_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "windows:\n"
        "  - {name: A, kind: holiday, start: 2026-02-10, end: 2026-03-15}\n"
    )
    with pytest.raises(ValueError, match="kind"):
        load_windows(p)


def test_missing_end_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "windows:\n"
        "  - {name: A, kind: exam, start: 2026-02-10}\n"
    )
    with pytest.raises(ValueError):
        load_windows(p)


def test_missing_file_returns_normal(tmp_path):
    reset_for_tests(tmp_path / "does_not_exist.yaml")
    try:
        assert current_season(_ts("2026-02-10T00:00:00")) == SEASON_NORMAL
        assert next_transition(_ts("2026-02-10T00:00:00")) is None
    finally:
        reset_for_tests(None)


# ── health_payload contract ───────────────────────────────────────────
def test_health_payload_in_exam(fixture_calendar):
    payload = health_payload(_ts("2026-02-15T00:00:00"))
    assert payload["schema_version"] == 1
    assert payload["season"] == SEASON_EXAM
    assert payload["ttl_multiplier"] == 3.0
    assert payload["ai_cache_ttl_seconds"]["stretched"] == EXAM_TTL_SEC
    assert payload["ai_cache_ttl_seconds"]["normal"] == NORMAL_TTL_SEC
    assert set(payload["ai_cache_ttl_seconds"]["stretched_content_types"]) == EXAM_STRETCH_CONTENT_TYPES
    assert payload["active_window"]["name"] == "AHSEC HS Final 2026"
    assert payload["next_transition"]["at"] == "2026-03-16"


def test_health_payload_in_normal(fixture_calendar):
    payload = health_payload(_ts("2026-01-15T00:00:00"))
    assert payload["season"] == SEASON_NORMAL
    assert payload["ttl_multiplier"] == 1.0
    assert payload["active_window"] is None
    assert payload["next_transition"]["to"] == SEASON_EXAM


def test_production_calendar_loads():
    """Smoke: the on-disk production calendar must parse cleanly so a
    bad merge surfaces immediately (the dedicated CI guard checks the
    365-day horizon separately)."""
    reset_for_tests(None)
    windows = load_windows()
    assert len(windows) >= 3
    for w in windows:
        assert w.kind in (SEASON_EXAM, SEASON_RESULTS)
        assert w.start <= w.end


def test_set_response_uses_calendar_ttl(monkeypatch):
    """Task #575 wiring: `ai_input_cache.set_response` picks the
    season-aware TTL when `ttl=None`, but an explicit `ttl=` wins."""
    import ai_input_cache

    captured: dict = {}

    def fake_inproc_set(key, text):
        captured["inproc"] = (key, text)

    def fake_cf_kv_set(key, text, ttl):
        captured["cf_ttl"] = ttl

    monkeypatch.setattr(ai_input_cache, "_inproc_set", fake_inproc_set)
    monkeypatch.setattr(ai_input_cache, "_cf_kv_set", fake_cf_kv_set)
    monkeypatch.setattr(ai_input_cache, "_redis_client", lambda: None)
    # Force exam season independent of today's date.
    monkeypatch.setattr(cache_calendar, "current_season", lambda now=None: SEASON_EXAM)

    ai_input_cache.set_response(
        [{"role": "user", "content": "x"}], "model-x", "answer",
        content_type="mcq", template_version="v1",
    )
    assert captured["cf_ttl"] == EXAM_TTL_SEC

    captured.clear()
    ai_input_cache.set_response(
        [{"role": "user", "content": "y"}], "model-x", "answer",
        content_type="formatter", template_version="v1",
    )
    assert captured["cf_ttl"] == NORMAL_TTL_SEC

    captured.clear()
    ai_input_cache.set_response(
        [{"role": "user", "content": "z"}], "model-x", "answer",
        content_type="mcq", template_version="v1", ttl=99,
    )
    assert captured["cf_ttl"] == 99
