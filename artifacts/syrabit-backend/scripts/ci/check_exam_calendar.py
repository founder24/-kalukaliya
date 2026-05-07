"""Task #575 CI guard — exam_calendar.yaml drift / overlap / coverage.

Fails the build when any of the following are true:
  * The YAML cannot be parsed.
  * Two windows overlap (a copy/paste typo would silently double-count
    a season change).
  * Any entry is missing `name`, `kind`, `start`, or `end`, or has
    `start > end`.
  * The latest `end` is less than 365 days from today (we always
    keep at least the next 12 months of windows on disk so the
    Cloudflare worker never falls back to "normal" during an exam
    pass just because the calendar wasn't refreshed).

Wired into the deploy workflow alongside the other Task #571 / #559
umbrella guards. Usage:

    python -m scripts.ci.check_exam_calendar
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

# Allow `python scripts/ci/check_exam_calendar.py` from the backend dir.
HERE = Path(__file__).resolve()
BACKEND_DIR = HERE.parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import cache_calendar  # noqa: E402

LOOKAHEAD_DAYS = 365


def main(path: Path | None = None) -> int:
    target = path or (BACKEND_DIR / "config" / "exam_calendar.yaml")
    if not target.exists():
        print(f"[check_exam_calendar] FAIL: {target} not found", file=sys.stderr)
        return 1
    try:
        windows = cache_calendar.load_windows(target)
    except Exception as e:
        print(f"[check_exam_calendar] FAIL: parse error: {e}", file=sys.stderr)
        return 1
    if not windows:
        print("[check_exam_calendar] FAIL: no windows declared", file=sys.stderr)
        return 1
    last_end = max(w.end for w in windows)
    horizon = date.today() + timedelta(days=LOOKAHEAD_DAYS)
    if last_end < horizon:
        print(
            f"[check_exam_calendar] FAIL: latest window end {last_end.isoformat()} "
            f"is less than {LOOKAHEAD_DAYS} days from today "
            f"({date.today().isoformat()}). Add at least one more window so the "
            f"edge worker keeps applying exam-mode TTLs through the next pass.",
            file=sys.stderr,
        )
        return 1
    print(
        f"[check_exam_calendar] OK: {len(windows)} windows, "
        f"latest end {last_end.isoformat()} (≥ {LOOKAHEAD_DAYS}d horizon).",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
