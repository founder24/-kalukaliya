"""Task #360 — Validation sampler (Latency Rule 12).

Vertex Gemini 2.5 Flash runs on a 10% sample of completed turns
*post-response*, off the critical path. Per-turn synchronous Vertex
calls on the live chat path are forbidden — they add 200–500 ms
cross-cloud latency.

The sample rate is configurable via ``VALIDATION_SAMPLE_RATE`` env var
(default 0.10) and overridable at runtime via the Redis key
``validation:sample_rate`` (sub-ms propagation, no redeploy needed).
A floor of 0.005 is enforced — any lower and a 1pp regression takes
> 30 days to detect.

Calls to :func:`should_validate` are pure — no I/O, no side effects.
The caller is responsible for actually enqueueing the validation job
when this function returns True.
"""
from __future__ import annotations

import logging
import os
import random
from typing import Any, Optional


logger = logging.getLogger(__name__)


# Floor enforced even if the env / Redis override goes lower. See the
# task spec: "never go below 0.5% — at lower rates a 1pp regression
# takes >30 days to detect".
SAMPLE_RATE_FLOOR = 0.005
DEFAULT_SAMPLE_RATE = 0.10
REDIS_OVERRIDE_KEY = "validation:sample_rate"


def _parse_rate(raw: Any, *, fallback: float) -> float:
    if raw is None:
        return fallback
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "ignore")
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return fallback
    if v <= 0:
        return 0.0
    if v > 1:
        # Tolerate "10" meaning 10%.
        v = v / 100.0
    if 0 < v < SAMPLE_RATE_FLOOR:
        logger.warning(
            "validation_sampler: requested rate %.4f below floor %.4f — "
            "clamping. See task #360 / runbook §6.",
            v, SAMPLE_RATE_FLOOR,
        )
        v = SAMPLE_RATE_FLOOR
    return v


def env_sample_rate() -> float:
    """Read the env-var-configured sample rate (no Redis lookup)."""
    return _parse_rate(os.environ.get("VALIDATION_SAMPLE_RATE"),
                       fallback=DEFAULT_SAMPLE_RATE)


def effective_sample_rate(redis: Optional[Any] = None) -> float:
    """Resolve the live sample rate.

    Priority: Redis override > env var > default. Redis lookup failure
    silently falls through to env so a Redis outage doesn't change
    sampling behaviour.
    """
    env_rate = env_sample_rate()
    if redis is None:
        return env_rate
    try:
        raw = redis.get(REDIS_OVERRIDE_KEY)
    except Exception:
        return env_rate
    if raw is None:
        return env_rate
    return _parse_rate(raw, fallback=env_rate)


def should_validate(*, rate: Optional[float] = None,
                    redis: Optional[Any] = None,
                    rng: Optional[random.Random] = None) -> bool:
    """Return True if this turn should be sampled for post-response validation.

    Pure function — no I/O, no enqueue. The caller fires the actual
    Vertex job after the user-visible response has been written.
    """
    r = rate if rate is not None else effective_sample_rate(redis)
    if r <= 0:
        return False
    if r >= 1:
        return True
    rng = rng or random
    return rng.random() < r


__all__ = [
    "DEFAULT_SAMPLE_RATE",
    "REDIS_OVERRIDE_KEY",
    "SAMPLE_RATE_FLOOR",
    "effective_sample_rate",
    "env_sample_rate",
    "should_validate",
]
