"""providers.sarvam — Task #553.

Typed wrapper around the Sarvam-AI ``sarvam-m`` chat completions endpoint
(``https://api.sarvam.ai/v1/chat/completions``). Sarvam is the locked
primary for the Assamese chat chain (``[sarvam, workers_ai_indic]`` —
see ``config.PROVIDER_PRIORITY['assamese_rag_chat']`` and the canonical
delegation umbrella in ``scripts/ci/check_canonical_delegation.py``).

This module is a *facade* over the existing ``deps.sarvam_llm_client``
HTTP/2 pooled client and ``llm._call_sarvam_llm`` payload semantics
(SARVAM_THINK_BUFFER reservation + ``<think>`` block stripping). It
adds three things the live dispatcher does not:

  1. **Typed exceptions** — ``SarvamUnavailable`` (5xx, timeout, transport)
     and ``SarvamRateLimited`` (429) so callers can reason about why the
     chain advanced to ``workers_ai_indic`` without parsing raw HTTP.
  2. **Per-user 30-calls/month cap** — defensive backstop for the edge
     worker's ``CHAT_CAP_MONTHLY=30`` enforcement
     (``workers/edge-proxy/src/index.ts``). The cap is keyed on
     ``(user_id, YYYY-MM)`` in Redis; cap exhaustion raises
     ``SarvamRateLimited("per_user_monthly_cap")`` so the dispatcher
     surfaces it as 429 (V4 §12 — no silent fallback).
  3. **``sarvam_chat`` metrics row** — every call records
     ``llm._record_llm_call("sarvam", "sarvam-m", ...)`` *and* a
     dedicated ``sarvam_chat`` counter pair (``ok`` / ``err``) for
     the admin success-rate widget + the <95 % / 1 h Sentry alert.

Returns a ``ChatResponse`` dataclass (NOT the FastAPI response model
``models.ChatResponseOut``, which is browser-facing and richer). This
keeps the provider boundary thin: the facade returns text + usage +
the model id; turning that into a streaming SSE response is the
dispatcher's job.

Out of scope (per Task #553):
  • Wiring this facade into ``llm._call_sarvam_llm`` — the live
    dispatcher already calls Sarvam directly. This module is the
    **canonical entry point** for *new* Assamese-chat callers; the
    legacy in-place dispatch is migrated under task B/G.
  • Streaming. ``llm._stream_sarvam`` already covers SSE.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger("providers.sarvam")

# ── Config ─────────────────────────────────────────────────────────────────
_MODEL = "sarvam-m"
_DEFAULT_TEMPERATURE = 0.1
# Mirrors `config.SARVAM_THINK_BUFFER` — kept as a local fallback so the
# module imports cleanly in test contexts that stub config out.
try:
    from config import SARVAM_THINK_BUFFER as _THINK_BUFFER  # type: ignore
except Exception:  # pragma: no cover - test stubs
    _THINK_BUFFER = 512

# Per-user monthly cap. The edge worker is the primary enforcer; this is
# a defensive backstop with the same default. Operator override knob:
# ``SARVAM_PER_USER_MONTHLY_CAP=N`` (any positive int; ``0`` disables
# the in-process check, leaving the edge as sole enforcer).
PER_USER_MONTHLY_CAP = int(os.environ.get("SARVAM_PER_USER_MONTHLY_CAP", "30") or "30")

_THINK_RE_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_RE_OPEN = re.compile(r"<think>.*$", re.DOTALL)


# ── Typed exceptions ───────────────────────────────────────────────────────
class SarvamError(Exception):
    """Base for all Sarvam-facade errors. Callers should catch this when
    they want to advance to the next leg of the chain regardless of the
    failure mode."""


class SarvamUnavailable(SarvamError):
    """Sarvam returned 5xx, timed out, or the transport raised. The
    Assamese chat dispatcher should advance to ``workers_ai_indic``."""


class SarvamRateLimited(SarvamError):
    """Sarvam returned 429 OR the per-user monthly cap is exhausted.

    The dispatcher should surface a 429 to the caller (V4 §12 — no
    silent downgrade on rate limits). ``reason`` distinguishes
    upstream 429s (``"upstream_429"``) from local cap exhaustion
    (``"per_user_monthly_cap"``)."""

    def __init__(self, reason: str, *, retry_after: Optional[int] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


# ── Response dataclass ─────────────────────────────────────────────────────
@dataclass
class ChatResponse:
    """Facade-level result. Thin on purpose — the dispatcher composes
    this into the browser-facing ``models.ChatResponseOut``."""

    text: str
    model: str = _MODEL
    provider: str = "sarvam"
    latency_ms: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)


# ── Per-user monthly cap (Redis-backed) ────────────────────────────────────
def _month_key(user_id: str, *, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"sarvam:user:{user_id}:{now.strftime('%Y%m')}"


def _check_and_bump_cap(user_id: Optional[str]) -> None:
    """Increment the per-user month bucket and raise
    ``SarvamRateLimited`` if the cap is already exhausted.

    No-op when ``PER_USER_MONTHLY_CAP <= 0`` (operator override) or
    ``user_id`` is falsy (anonymous users are capped at the edge —
    the edge worker keys on ``anon-id`` which we don't see here).
    No-op when Redis is unavailable: the edge cap is the canonical
    enforcer; this in-process backstop is best-effort by design.
    """
    if PER_USER_MONTHLY_CAP <= 0 or not user_id:
        return
    try:
        from deps import redis_client as _rc  # local import — see deps stub
    except Exception:
        return
    if not _rc:
        return
    key = _month_key(str(user_id))
    try:
        new_val = _rc.incr(key)
        # First write wins the TTL; ~32 days covers month boundaries.
        if int(new_val) == 1:
            _rc.expire(key, 32 * 86400)
    except Exception as exc:
        logger.debug("[sarvam] cap incr failed (degraded — edge enforces): %s", exc)
        return
    if int(new_val) > PER_USER_MONTHLY_CAP:
        raise SarvamRateLimited("per_user_monthly_cap")


# ── Lightweight 1h success-rate counter (for /admin/health/sarvam) ─────────
# In-memory rolling list of (ts, success_bool). Bounded; the admin
# endpoint and the alert loop both read this. We keep it process-local
# (per-replica) because the alert sensitivity is per-replica too —
# the Sentry alert fires on whichever replica first crosses the floor.
_RECENT_CALLS: list[tuple[float, bool]] = []
_RECENT_CALLS_MAX = 5000

# Floor for the <95 % / 1 h Sentry alert. Surfaced on the admin tile.
SUCCESS_RATE_ALERT_FLOOR = 0.95
SUCCESS_RATE_ALERT_WINDOW_S = 3600
SUCCESS_RATE_MIN_SAMPLES = 20  # don't alarm on a 0/1 blip


def _record_outcome(success: bool) -> None:
    ts = time.time()
    _RECENT_CALLS.append((ts, success))
    if len(_RECENT_CALLS) > _RECENT_CALLS_MAX:
        del _RECENT_CALLS[: len(_RECENT_CALLS) - _RECENT_CALLS_MAX]


def success_rate_snapshot(window_seconds: int = SUCCESS_RATE_ALERT_WINDOW_S) -> dict:
    """Return ``{ok, err, total, success_rate, window_s, alert}`` over
    the last ``window_seconds``. Used by both ``/admin/health/sarvam``
    and the Sentry alert loop. Always returns a dict; never raises."""
    cutoff = time.time() - window_seconds
    ok = err = 0
    for ts, success in _RECENT_CALLS:
        if ts < cutoff:
            continue
        if success:
            ok += 1
        else:
            err += 1
    total = ok + err
    rate = (ok / total) if total else 1.0
    alert = bool(total >= SUCCESS_RATE_MIN_SAMPLES and rate < SUCCESS_RATE_ALERT_FLOOR)
    return {
        "ok": ok,
        "err": err,
        "total": total,
        "success_rate": round(rate, 4),
        "window_s": window_seconds,
        "alert": alert,
        "alert_floor": SUCCESS_RATE_ALERT_FLOOR,
        "min_samples": SUCCESS_RATE_MIN_SAMPLES,
    }


# ── Public chat() entry point ──────────────────────────────────────────────
async def chat(
    messages: list[dict[str, str]],
    *,
    user_id: Optional[str] = None,
    max_tokens: int = 800,
    temperature: float = _DEFAULT_TEMPERATURE,
    response_language: Optional[str] = None,
) -> ChatResponse:
    """Non-streaming Assamese chat completion via Sarvam ``sarvam-m``.

    Args:
        messages: OpenAI-style ``[{"role": "...", "content": "..."}, ...]``.
        user_id: Authenticated user id; used for the 30/mo cap. Anonymous
            callers should pass ``None`` (edge enforces their cap).
        max_tokens: Caller's *answer* budget. ``SARVAM_THINK_BUFFER`` is
            added on top so the ``<think>`` block never crowds it out.
        temperature: Default ``0.1`` matches ``llm._call_sarvam_llm``.
        response_language: Optional ``"as-IN"`` to bias output to
            Assamese; passed through as Sarvam's ``response_language``.

    Returns:
        ``ChatResponse`` with ``<think>...</think>`` reasoning stripped.

    Raises:
        SarvamRateLimited: 429 from Sarvam OR per-user cap exhausted.
        SarvamUnavailable: 5xx, timeout, transport error, or the client
            isn't initialised (no ``SARVAM_API_KEY`` configured).
    """
    # Local import — keeps module-import safe in test environments
    # that stub deps before the real client is built.
    from deps import sarvam_llm_client

    if sarvam_llm_client is None:
        raise SarvamUnavailable("sarvam_llm_client not initialised — SARVAM_API_KEY missing?")

    _check_and_bump_cap(user_id)

    api_max = max_tokens + _THINK_BUFFER
    payload: dict[str, Any] = {
        "model": _MODEL,
        "messages": messages,
        "max_tokens": api_max,
        "temperature": temperature,
        "stream": False,
    }
    if response_language:
        payload["response_language"] = response_language

    t0 = time.perf_counter()
    success = False
    try:
        try:
            resp = await sarvam_llm_client.post("/v1/chat/completions", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise SarvamUnavailable(f"transport: {type(exc).__name__}: {exc}") from exc

        if resp.status_code == 429:
            ra = resp.headers.get("retry-after") if hasattr(resp, "headers") else None
            try:
                ra_int = int(ra) if ra is not None else None
            except (TypeError, ValueError):
                ra_int = None
            raise SarvamRateLimited("upstream_429", retry_after=ra_int)
        if resp.status_code >= 500:
            raise SarvamUnavailable(f"upstream {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 400:
            # 4xx other than 429 — propagate as Unavailable so the
            # dispatcher advances rather than infinite-retrying.
            raise SarvamUnavailable(f"upstream {resp.status_code}: {resp.text[:200]}")

        try:
            data = resp.json() or {}
        except Exception as exc:
            raise SarvamUnavailable(f"non-JSON body: {exc}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise SarvamUnavailable("empty choices in response")
        choice = choices[0].get("message") or {}
        content = choice.get("content") or ""
        reasoning = choice.get("reasoning_content") or ""
        text = content if content else reasoning
        text = _THINK_RE_BLOCK.sub("", text)
        text = _THINK_RE_OPEN.sub("", text).strip()

        usage = data.get("usage") or {}
        normalised_usage = {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }

        success = True
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return ChatResponse(
            text=text,
            model=data.get("model") or _MODEL,
            latency_ms=round(latency_ms, 1),
            usage=normalised_usage,
        )
    finally:
        _record_outcome(success)
        # Also feed the canonical LLM-call audit (Task #271 schema) so
        # this facade shows up in the admin RoutingPools panel and the
        # MeterD spend tally. Best-effort — failure here must not mask
        # a successful chat response.
        try:
            from llm import _record_llm_call  # type: ignore

            _record_llm_call(
                "sarvam",
                _MODEL,
                (time.perf_counter() - t0) * 1000.0,
                success,
                tokens_approx=0,
                feature_key="sarvam_chat",
            )
        except Exception:
            pass
