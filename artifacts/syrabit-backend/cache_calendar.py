"""cache_calendar — Task #575.

Pure-function calendar that classifies the current date as one of
``"exam"`` / ``"results"`` / ``"normal"`` based on the AHSEC + SEBA
window schedule defined in ``config/exam_calendar.yaml``.

Consumers
---------
* ``ai_input_cache.set_response`` stretches deterministic-cache TTLs
  for MCQ / definition / flashcard / PYQ entries to 90 days when the
  current season is ``"exam"`` or ``"results"`` (formatter / translate
  / OCR keep their 30-day default — those are admin-edit driven and
  the longer TTL would mask a freshly polished body for too long).
* ``GET /api/health/season`` exposes the same view to the Cloudflare
  edge proxy, which applies per-route ``exam_ttl_seconds`` overrides
  from ``monitored-urls.json`` while in exam mode.
* The admin Observability cache banner reads the same endpoint and
  shows the current season + next transition + TTL multiplier.

Founder locks (NEVER touched by this module)
--------------------------------------------
* ``/api/me/quota`` 5 s edge cache TTL.
* ``/api/ai/chat`` edge bypass — the live chat hot-path is excluded
  from the deterministic cache by policy (K.2 gotcha).
* ``$100/mo`` monthly USD cap.
* ``TOKEN_BUDGETS`` ceilings in ``cost_caps.py``.

Behaviour
---------
``current_season(now=None)`` is **pure**: same input → same output, no
network or filesystem side effects beyond a single one-time YAML load
that is cached at module-import time. ``now`` defaults to
``datetime.now(timezone.utc)``; tests pin specific instants by
passing it explicitly.

The TTL helper ``ai_cache_ttl_for(content_type, season)`` returns the
90-day stretch only for the four exam-relevant deterministic content
types (``mcq``, ``flashcard``, ``definition``, ``pyq``); every other
content type keeps the 30-day default regardless of season.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

logger = logging.getLogger(__name__)

# ── Public types ──────────────────────────────────────────────────────
Season = str  # "exam" | "results" | "normal"

SEASON_NORMAL: Season = "normal"
SEASON_EXAM: Season = "exam"
SEASON_RESULTS: Season = "results"

_VALID_KINDS = (SEASON_EXAM, SEASON_RESULTS)

# 30-day default mirrors `ai_input_cache._DEFAULT_TTL_SEC`.
NORMAL_TTL_SEC = 30 * 24 * 60 * 60
# 90-day stretch during exam / results windows for high-value
# deterministic generators that are recomputed every dispatch in
# normal traffic but go read-mostly during the exam spike.
EXAM_TTL_SEC = 90 * 24 * 60 * 60
EXAM_TTL_MULTIPLIER = 3.0  # surfaced to the admin banner

# Content types whose TTL is stretched during exam / results mode.
# Formatter / translate / OCR are intentionally NOT in this set —
# they are admin-edit driven and the longer TTL would mask a freshly
# polished body for too long after a CMS re-edit.
EXAM_STRETCH_CONTENT_TYPES = frozenset({"mcq", "flashcard", "definition", "pyq"})
# NOTE on PYQ wiring scope (Task #575): the deterministic AI input
# cache (``ai_input_cache``) is wired into the MCQ, flashcard, and
# definition generators today (Task #571). The PYQ generation
# pipeline (``routes/pyq.py:admin_pyq_agentic_process``) currently
# calls Gemini Vision OCR DIRECTLY, without going through
# ``ai_input_cache``. Including ``"pyq"`` in
# ``EXAM_STRETCH_CONTENT_TYPES`` here makes the calendar READY for
# that wiring — when the PYQ generator is refactored to write through
# ``ai_input_cache.set_response(content_type="pyq", ...)`` (follow-up
# task #582), exam-mode TTL stretching will apply automatically with
# no change here. The edge-cache side of the PYQ benefit (the
# ``/api/pyq/`` route's ``exam_ttl_seconds`` stretch declared in
# ``workers/edge-proxy/monitored-urls.json``) is live today regardless.


@dataclass(frozen=True)
class ExamWindow:
    name: str
    kind: Season  # "exam" | "results"
    start: date  # inclusive
    end: date  # inclusive

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


# ── YAML loader (module-import cached) ────────────────────────────────
_CALENDAR_LOCK = threading.Lock()
_CALENDAR_CACHE: Optional[Sequence[ExamWindow]] = None
_CALENDAR_PATH_OVERRIDE: Optional[Path] = None


def _default_calendar_path() -> Path:
    return Path(
        os.environ.get(
            "EXAM_CALENDAR_PATH",
            str(Path(__file__).resolve().parent / "config" / "exam_calendar.yaml"),
        )
    )


def _parse_windows(raw: object) -> list[ExamWindow]:
    """Convert the parsed YAML payload into an ordered tuple of
    ``ExamWindow`` instances. Raises ``ValueError`` on malformed
    entries — the loader is best-effort at runtime (we degrade to an
    empty list on disk failure) but the parser itself is strict so
    malformed YAML surfaces in tests + the CI guard."""
    if not isinstance(raw, dict):
        raise ValueError("exam_calendar.yaml: top-level must be a mapping")
    items = raw.get("windows")
    if not isinstance(items, list):
        raise ValueError("exam_calendar.yaml: 'windows' must be a list")
    out: list[ExamWindow] = []
    for i, entry in enumerate(items):
        if not isinstance(entry, dict):
            raise ValueError(f"windows[{i}]: must be a mapping, got {type(entry).__name__}")
        name = entry.get("name")
        kind = entry.get("kind")
        start_raw = entry.get("start")
        end_raw = entry.get("end")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"windows[{i}]: 'name' is required and must be a non-empty string")
        if kind not in _VALID_KINDS:
            raise ValueError(
                f"windows[{i}] ({name!r}): 'kind' must be one of {list(_VALID_KINDS)!r}, got {kind!r}"
            )
        try:
            start_d = _coerce_date(start_raw)
            end_d = _coerce_date(end_raw)
        except Exception as e:
            raise ValueError(f"windows[{i}] ({name!r}): {e}") from e
        if start_d > end_d:
            raise ValueError(
                f"windows[{i}] ({name!r}): start {start_d.isoformat()} is after "
                f"end {end_d.isoformat()}"
            )
        out.append(ExamWindow(name=name.strip(), kind=kind, start=start_d, end=end_d))
    out.sort(key=lambda w: (w.start, w.end, w.name))
    _validate_no_overlap(out)
    return out


def _coerce_date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValueError(f"date must be YYYY-MM-DD or a date, got {type(value).__name__}")


def _validate_no_overlap(windows: Sequence[ExamWindow]) -> None:
    prev: Optional[ExamWindow] = None
    for w in windows:
        if prev is not None and w.start <= prev.end:
            raise ValueError(
                f"overlapping windows: {prev.name!r} ({prev.start}..{prev.end}) "
                f"overlaps {w.name!r} ({w.start}..{w.end}). Adjust one of the "
                f"end dates so windows are strictly disjoint."
            )
        prev = w


def load_windows(path: Optional[Path] = None) -> Sequence[ExamWindow]:
    """Return the parsed window list. Cached per-process; pass an
    explicit ``path`` to force a fresh parse (used by tests + the CI
    guard)."""
    global _CALENDAR_CACHE
    if path is not None:
        return _read_yaml(path)
    with _CALENDAR_LOCK:
        if _CALENDAR_CACHE is None:
            try:
                _CALENDAR_CACHE = _read_yaml(_CALENDAR_PATH_OVERRIDE or _default_calendar_path())
            except FileNotFoundError:
                logger.warning("[cache_calendar] exam_calendar.yaml not found; defaulting to normal season")
                _CALENDAR_CACHE = ()
            except Exception as e:
                logger.error("[cache_calendar] exam_calendar.yaml parse failed (%s); defaulting to normal season", e)
                _CALENDAR_CACHE = ()
        return _CALENDAR_CACHE


def _read_yaml(path: Path) -> tuple[ExamWindow, ...]:
    import yaml  # local import — tests can monkey-patch

    text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    return tuple(_parse_windows(raw))


def reset_for_tests(path: Optional[Path] = None) -> None:
    """Tests use this to point the loader at a fixture file."""
    global _CALENDAR_CACHE, _CALENDAR_PATH_OVERRIDE
    with _CALENDAR_LOCK:
        _CALENDAR_PATH_OVERRIDE = path
        _CALENDAR_CACHE = None


# ── Pure season classification ────────────────────────────────────────
def current_season(now: Optional[datetime] = None) -> Season:
    """Return ``"exam"``, ``"results"``, or ``"normal"`` for ``now``.

    ``now`` defaults to the current UTC instant. Naive datetimes are
    treated as UTC. The function is pure beyond the one-time YAML
    load cached in module state.
    """
    today = _today(now)
    for w in load_windows():
        if w.contains(today):
            return w.kind
    return SEASON_NORMAL


def next_transition(now: Optional[datetime] = None) -> Optional[dict]:
    """Return ``{"at": ISO8601 date, "to": season, "window": name}``
    describing the next season change, or ``None`` when the calendar
    has no further windows after ``now``.

    The transition is the boundary at which ``current_season`` would
    return a different value: either the start of the next future
    window (when currently in ``"normal"``) or the day after the end
    of the active window (when currently in ``"exam"`` / ``"results"``).
    """
    today = _today(now)
    active: Optional[ExamWindow] = None
    next_future: Optional[ExamWindow] = None
    for w in load_windows():
        if w.contains(today):
            active = w
        elif w.start > today and (next_future is None or w.start < next_future.start):
            next_future = w
    if active is not None:
        # Transitions back to "normal" the day after end, unless a
        # future window opens earlier (which the no-overlap guard
        # forbids — but we still defend against a misconfigured load).
        next_at = active.end + timedelta(days=1)
        if next_future is not None and next_future.start <= next_at:
            return {"at": next_future.start.isoformat(), "to": next_future.kind, "window": next_future.name}
        return {"at": next_at.isoformat(), "to": SEASON_NORMAL, "window": active.name + " ends"}
    if next_future is not None:
        return {"at": next_future.start.isoformat(), "to": next_future.kind, "window": next_future.name}
    return None


def active_window(now: Optional[datetime] = None) -> Optional[ExamWindow]:
    today = _today(now)
    for w in load_windows():
        if w.contains(today):
            return w
    return None


def _today(now: Optional[datetime]) -> date:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).date()


# ── TTL helper consumed by ai_input_cache.set_response ────────────────
def ai_cache_ttl_for(content_type: Optional[str], season: Optional[Season] = None) -> int:
    """Return the TTL in seconds for an ``ai_input_cache`` entry.

    * ``mcq`` / ``flashcard`` / ``definition`` / ``pyq`` get the
      90-day stretch when ``season`` is ``"exam"`` or ``"results"``.
    * Every other content type — and every content type during
      ``"normal"`` — gets the 30-day default.
    """
    s = season if season in (SEASON_EXAM, SEASON_RESULTS, SEASON_NORMAL) else current_season()
    if s in (SEASON_EXAM, SEASON_RESULTS) and content_type in EXAM_STRETCH_CONTENT_TYPES:
        return EXAM_TTL_SEC
    return NORMAL_TTL_SEC


# ── Task #574 — per-route TTL recommendation ──────────────────────────
#
# Consumed by ``aca_jobs.prewarm_seo_routes`` (which sends an
# ``X-Bypass-Cache`` HEAD followed by a normal GET so the worker
# stores a freshly-warmed entry under the recommended TTL) and by the
# Cloudflare worker's per-route override pass. Centralising the
# decision here keeps the prewarm job, the worker, and the admin
# coverage tile consistent — a single config edit propagates
# everywhere without per-consumer drift.
#
# The function is **pure** (modulo the same one-time YAML load that
# ``current_season`` uses) so tests pin behaviour by passing
# ``today`` explicitly.

# Per-content-type baseline + season-stretched TTL. The baseline is
# the long-tail "this content does not change between admin edits"
# value; the season-stretched TTL only applies when the active
# calendar window is ``exam`` / ``results`` AND the content type is
# in ``EXAM_STRETCH_CONTENT_TYPES``. Formatter / translate / OCR are
# intentionally NOT in the stretch set — they are admin-edit driven
# and the longer TTL would mask a freshly polished body for too long.
_CONTENT_TYPE_TTL_NORMAL: dict = {
    "mcq":        NORMAL_TTL_SEC,
    "flashcard":  NORMAL_TTL_SEC,
    "definition": NORMAL_TTL_SEC,
    "pyq":        NORMAL_TTL_SEC,
    "faq":        NORMAL_TTL_SEC,
    "quick_answer": NORMAL_TTL_SEC,
    "formatter":  NORMAL_TTL_SEC,
    "translate":  NORMAL_TTL_SEC,
    "ocr":        NORMAL_TTL_SEC,
}

# Per-route baseline + season-stretched edge TTL (seconds).
# Mirrors the ``edge_cache.ttl_seconds`` / ``exam_ttl_seconds`` pairs
# in ``workers/edge-proxy/monitored-urls.json``. Kept here so the
# Lambda prewarm job has a single source of truth — the worker
# applies the same numbers via its monitored-urls polling, and the
# CI drift gate (``tests/test_monitoring_url_drift.py``) catches the
# routes/content-types/edge-routes triad if any one of them strays.
_ROUTE_TTL_TABLE: tuple = (
    # SEO chapter pages — matched on prefix; the key is the suffix
    # that uniquely identifies the route family. The full prewarm
    # URL is built by ``aca_jobs.prewarm_seo_routes``; we only need
    # to recognise it here.
    ("/board/",                   3600,    21600),
    ("/api/seo/",                  600,     3600),
    ("/api/content/chapter-by-slug/", 3600, 21600),
    ("/api/content/topic/",       3600,    21600),
    ("/api/content/library-bundle", 1800,  14400),
    ("/api/pyq/",                 3600,    86400),
)


def recommended_ttl_seconds(
    content_type: Optional[str] = None,
    route: Optional[str] = None,
    today: Optional[datetime] = None,
) -> int:
    """Recommended TTL (seconds) for a single cache entry.

    Resolution order (first non-``None`` wins):
      1. ``route`` — longest-prefix match against ``_ROUTE_TTL_TABLE``
         (mirrors ``monitored-urls.json``). The matched row exposes a
         normal + ``exam_ttl_seconds`` pair; we pick the stretched
         value when ``current_season(today)`` is exam/results.
      2. ``content_type`` — looked up in ``_CONTENT_TYPE_TTL_NORMAL``;
         stretched to ``EXAM_TTL_SEC`` when the type is in
         ``EXAM_STRETCH_CONTENT_TYPES`` and the season is stretched.
      3. Neither supplied — falls back to the deterministic-cache
         default (``ai_cache_ttl_for(None)``) so the helper is safe to
         call as a generic "what TTL should I use right now" probe.

    Pure beyond the one-time YAML load.
    """
    season = current_season(today)
    stretched = season in (SEASON_EXAM, SEASON_RESULTS)

    if route:
        # Longest-prefix match — the most specific route wins. The
        # table is small (<10 rows) so a linear scan is fine; matches
        # the lookup pattern in ``workers/edge-proxy/src/index.ts``.
        match = max(
            (row for row in _ROUTE_TTL_TABLE if route.startswith(row[0])),
            key=lambda row: len(row[0]),
            default=None,
        )
        if match is not None:
            _, normal_ttl, stretched_ttl = match
            return int(stretched_ttl if stretched else normal_ttl)

    if content_type is not None:
        if stretched and content_type in EXAM_STRETCH_CONTENT_TYPES:
            return EXAM_TTL_SEC
        # Unknown content_type → fall through to the deterministic
        # cache default rather than 0; we'd rather over-cache by a
        # few hours than serve a TTL of 0 and stampede the origin.
        return int(_CONTENT_TYPE_TTL_NORMAL.get(content_type, NORMAL_TTL_SEC))

    return ai_cache_ttl_for(None, season)


def health_payload(now: Optional[datetime] = None) -> dict:
    """Shape consumed by ``GET /api/health/season`` and the admin
    Observability cache banner. Stable contract — any change MUST
    bump ``schema_version`` and a corresponding edge worker change."""
    season = current_season(now)
    nxt = next_transition(now)
    active = active_window(now)
    return {
        "schema_version": 1,
        "season": season,
        "ttl_multiplier": EXAM_TTL_MULTIPLIER if season in (SEASON_EXAM, SEASON_RESULTS) else 1.0,
        "ai_cache_ttl_seconds": {
            "stretched": EXAM_TTL_SEC,
            "normal": NORMAL_TTL_SEC,
            "stretched_content_types": sorted(EXAM_STRETCH_CONTENT_TYPES),
        },
        "active_window": (
            {"name": active.name, "kind": active.kind,
             "start": active.start.isoformat(), "end": active.end.isoformat()}
            if active is not None else None
        ),
        "next_transition": nxt,
    }


__all__ = [
    "Season",
    "SEASON_NORMAL", "SEASON_EXAM", "SEASON_RESULTS",
    "EXAM_STRETCH_CONTENT_TYPES",
    "NORMAL_TTL_SEC", "EXAM_TTL_SEC", "EXAM_TTL_MULTIPLIER",
    "ExamWindow",
    "current_season", "next_transition", "active_window",
    "ai_cache_ttl_for", "recommended_ttl_seconds", "health_payload",
    "load_windows", "reset_for_tests",
]
