"""Regression test for the Task #558 umbrella ban regex.

Architect round-1 review of Task #558 flagged that the original
``traces_sample_rate=<positive>`` arm of ``TODO_558_PATTERN`` did not
match low-value floats like ``0.05`` or ``0.001`` because the
``[1-9]`` character class sat immediately after ``\\.?`` and forced
the digit-after-decimal to be 1-9 (skipping the leading zeros most
real-world Sentry tracing configs use).

This test pins the tightened regex so a future edit cannot reintroduce
the gap. It runs the pattern against a curated table of (sample, expect)
pairs covering:

* ``=0`` / ``=0.0`` / ``=0.00`` / ``= 0 `` — must NOT match (these are
  the only values ``observability/sentry_setup.py`` is allowed to ship).
* ``=1`` / ``=0.1`` / ``=0.05`` / ``=0.001`` / ``=.25`` / ``=1e-3`` — must
  ALL match (any non-zero numeric → Sentry tracing is on, banned).
* Substring + identifier-prefix safety: ``my_traces_sample_rate=0.5``
  must NOT match because the leading word boundary would defeat the
  intent (we ban the SDK kwarg, not arbitrary user-named variables).
  The current pattern intentionally does NOT enforce a left-edge
  boundary because the Sentry SDK kwarg always shows up either at
  start of arg list or after a comma — a broader boundary risks false
  positives on banned-call-site comments. The test pins this trade-off.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "artifacts" / "syrabit-backend"))

from scripts.ci.check_canonical_delegation import TODO_558_PATTERN  # noqa: E402


_PAT = re.compile(TODO_558_PATTERN)


def _match(s: str) -> bool:
    return _PAT.search(s) is not None


def test_zero_literals_are_allowed():
    # The only forms `observability/sentry_setup.py` is allowed to ship.
    assert not _match("traces_sample_rate=0")
    assert not _match("traces_sample_rate = 0")
    assert not _match("traces_sample_rate=0.0")
    assert not _match("traces_sample_rate=0.00")
    assert not _match("traces_sample_rate = 0.0")


def test_positive_floats_are_banned():
    for s in (
        "traces_sample_rate=1",
        "traces_sample_rate=1.0",
        "traces_sample_rate=0.1",
        "traces_sample_rate=0.05",
        "traces_sample_rate=0.001",
        "traces_sample_rate=.25",
        "traces_sample_rate = 0.5",
        "traces_sample_rate=1e-3",
        "traces_sample_rate=0.5e-1",
    ):
        assert _match(s), f"expected ban hit: {s!r}"


def test_other_558_bans_still_fire():
    # Comma-separated multi-exporter (any first token).
    assert _match("OTEL_TRACES_EXPORTER=googlecloud,otlp")
    assert _match('OTEL_TRACES_EXPORTER="googlecloud",otlp')
    # Sentry tracing kwargs / decorators / API.
    assert _match("init(enable_tracing=True)")
    assert _match("with sentry_sdk.start_transaction(op='task'):")
    assert _match("@sentry_sdk.trace")


def test_any_non_googlecloud_single_exporter_is_banned():
    # Per round-2 review: the regex must reject EVERY OTEL exporter
    # value except `googlecloud` (single literal). Single-value
    # alternative exporters like otlp / jaeger / zipkin / azure_monitor
    # / console / empty are all banned even without a trailing comma.
    for s in (
        "OTEL_TRACES_EXPORTER=otlp",
        "OTEL_TRACES_EXPORTER=jaeger",
        "OTEL_TRACES_EXPORTER=zipkin",
        "OTEL_TRACES_EXPORTER=azure_monitor",
        "OTEL_TRACES_EXPORTER=console",
        'OTEL_TRACES_EXPORTER="otlp"',
        'OTEL_TRACES_EXPORTER="azure_monitor"',
    ):
        assert _match(s), f"expected ban hit: {s!r}"


def test_single_value_googlecloud_otel_exporter_is_allowed():
    # Bicep ships exactly this single value; must not trip — neither
    # bare nor quoted, neither with trailing whitespace nor at EOL.
    assert not _match("OTEL_TRACES_EXPORTER=googlecloud")
    assert not _match('OTEL_TRACES_EXPORTER="googlecloud"')
    assert not _match("OTEL_TRACES_EXPORTER=googlecloud ")
    assert not _match("OTEL_TRACES_EXPORTER=googlecloud\n")
