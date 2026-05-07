"""Task #360 — SLO emission helpers.

Thin wrapper that records the four canonical SLO observations from the
v3 spec (`infra/per-cloud-feature-delegation.md` §6):

  - chat_ttfb_ms      — first-token TTFB on the live chat path
  - rag_e2e_ms        — full RAG round-trip
  - moderation_ms     — combined Llama Guard + Azure CS time
  - validation_lag_ms — Vertex post-response validation lag

Targets (p50 / p95):
  chat_ttfb_ms      300 / 1000
  rag_e2e_ms        500 / 1500
  moderation_ms      80 /  250
  validation_lag_ms 2000 / 5000

The emitter is deliberately decoupled from any specific metrics
backend — it forwards the observation to whatever sink is registered
via :func:`set_slo_sink`. Production wires this to the existing
``metrics`` module; tests use an in-memory collector.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Optional


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SloTarget:
    name: str
    p50_ms: int
    p95_ms: int


# Targets sourced from task #360 §"Realistic SLO envelope (post-#360
# baseline)" + infra/per-cloud-feature-delegation.md §15.
#   - chat_ttfb_ms:      p50 600-900ms  → 750 midpoint; p95 1.2-1.6s → 1400
#   - rag_full_ms:       p95 < 6.0 s (full response budget per §15)
#   - embed_hotpath_ms:  p95 < 60 ms (§15)
#   - pinecone_query_ms: p95 < 80 ms (§15)
#   - mongo_profile_ms:  p95 < 25 ms single-doc IXSCAN (§15)
#   - moderation_ms:     p95 ≤ 250 ms (Llama Guard + Azure CS combined)
#   - validation_lag_ms: post-response Vertex Gemini sample, off the
#     critical path (Latency Rule 12); 2-5 s is acceptable.
SLO_TARGETS: dict[str, SloTarget] = {
    "chat_ttfb_ms":       SloTarget("chat_ttfb_ms",        750, 1400),
    "rag_e2e_ms":         SloTarget("rag_e2e_ms",         1500, 6000),
    "embed_hotpath_ms":   SloTarget("embed_hotpath_ms",     30,   60),
    "pinecone_query_ms":  SloTarget("pinecone_query_ms",    40,   80),
    "mongo_profile_ms":   SloTarget("mongo_profile_ms",     12,   25),
    "moderation_ms":      SloTarget("moderation_ms",        80,  250),
    "validation_lag_ms":  SloTarget("validation_lag_ms",  2000, 5000),
    # Task #556 — SendGrid retired; SES is the sole transactional path.
    "ses_5xx_rate":       SloTarget("ses_5xx_rate",            1,    5),  # percent
}


SloSink = Callable[[str, float, dict], None]


_sink: Optional[SloSink] = None


def set_slo_sink(sink: Optional[SloSink]) -> None:
    """Register the metrics sink. Pass ``None`` to disable emission."""
    global _sink
    _sink = sink


def emit(name: str, value_ms: float, **labels) -> None:
    """Emit one SLO observation. Never raises."""
    if name not in SLO_TARGETS:
        logger.warning("slo_emitter: unknown SLO %r — emitting anyway", name)
    s = _sink
    if s is None:
        return
    try:
        s(name, float(value_ms), dict(labels))
    except Exception:
        logger.exception("slo_emitter: sink failed for %s", name)


@contextmanager
def measure(name: str, **labels) -> Iterator[None]:
    """Time a block and emit on exit."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        emit(name, (time.perf_counter() - t0) * 1000.0, **labels)


def breaches_slo(name: str, value_ms: float, *, percentile: str = "p95") -> bool:
    """Return True if the observation breaches the registered target."""
    target = SLO_TARGETS.get(name)
    if target is None:
        return False
    threshold = target.p95_ms if percentile == "p95" else target.p50_ms
    return value_ms > threshold


__all__ = [
    "SLO_TARGETS",
    "SloSink",
    "SloTarget",
    "breaches_slo",
    "emit",
    "measure",
    "set_slo_sink",
]
