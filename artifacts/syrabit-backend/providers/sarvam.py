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

  1. **Typed exceptions** — ``SarvamUnavailable`` (5xx, timeout,
     transport, no client) and ``SarvamRateLimited`` (upstream 429
     OR per-user-monthly cap exhaustion) so callers can reason about
     why the chain advanced to ``workers_ai_indic`` without parsing
     raw HTTP.
  2. **Per-user 30-calls/month cap** — defensive backstop for the
     edge worker's ``CHAT_CAP_MONTHLY=30`` enforcement
     (``workers/edge-proxy/src/index.ts``). The cap lives in
     ``cost_caps.record_sarvam_user_call(user_id)`` (the canonical
     interceptor) so the same Redis bucket is used by anything
     calling Sarvam, not just this facade.
  3. **Real cost + token usage reporting under the ``sarvam_chat``
     metric** — every call records ``metrics.record_sarvam_chat(
     success, latency_ms, prompt_tokens, completion_tokens, error)``
     which feeds the AdminHealth tile + the <95 %/1 h Sentry alert
     (``metrics.maybe_emit_sarvam_alert``).

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

# Re-exported from cost_caps so callers / tests can introspect the cap
# without crossing module boundaries themselves. Stays in sync with
# the canonical interceptor in ``cost_caps.py``.
try:
    from cost_caps import SARVAM_PER_USER_MONTHLY_CAP as _CAP_DEFAULT
except Exception:
    _CAP_DEFAULT = int(os.environ.get("SARVAM_PER_USER_MONTHLY_CAP", "30") or "30")
PER_USER_MONTHLY_CAP = _CAP_DEFAULT

_THINK_RE_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_RE_OPEN = re.compile(r"<think>.*$", re.DOTALL)

# Sarvam dialect tag for ``response_language``. We accept the short
# ``"as"`` (Assamese, the canonical Task #553 contract) and pass the
# BCP-47 ``"as-IN"`` to the upstream endpoint.
_LANGUAGE_TAGS = {
    "as": "as-IN",
    "as-in": "as-IN",
    "assamese": "as-IN",
}


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
    cost_usd: float = 0.0


# ── Snapshot pass-through (legacy callers) ─────────────────────────────────
def success_rate_snapshot(window_seconds: int = 3600) -> dict:
    """Backwards-compatible accessor. The canonical store now lives in
    ``metrics.sarvam_chat_snapshot`` so the same numbers feed the admin
    tile *and* the Sentry alert."""
    try:
        from metrics import sarvam_chat_snapshot as _snap

        return _snap(window_seconds)
    except Exception:
        # Last-ditch shape so callers don't crash if metrics is being
        # reloaded. Mirrors the empty-window default.
        return {
            "window_s": window_seconds,
            "ok": 0,
            "err": 0,
            "total": 0,
            "success_rate": 1.0,
            "success_rate_5m": 1.0,
            "total_5m": 0,
            "p50_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "cost_usd_24h": 0.0,
            "tokens_24h": 0,
            "last_error": "",
            "alert": False,
            "alert_floor": 0.95,
            "min_samples": 20,
        }


SUCCESS_RATE_ALERT_FLOOR = 0.95


# ── Public chat() entry point ──────────────────────────────────────────────
async def chat(
    messages: list[dict[str, str]],
    *,
    language: str = "as",
    user_id: Optional[str] = None,
    max_tokens: int = 800,
    temperature: float = _DEFAULT_TEMPERATURE,
    thinking_budget: int = 256,
    **kwargs: Any,
) -> ChatResponse:
    """Non-streaming Assamese chat completion via Sarvam ``sarvam-m``.

    Args:
        messages: OpenAI-style ``[{"role": "...", "content": "..."}, ...]``.
        language: Target reply language. Defaults to ``"as"`` (Assamese,
            the only locked use case for this facade today). Mapped to
            Sarvam's BCP-47 ``response_language``; pass-through if the
            short tag is unknown so future Indic languages don't need a
            facade rev.
        user_id: Authenticated user id; used for the 30/mo cap. Anonymous
            callers should pass ``None`` (edge enforces their cap).
        max_tokens: Caller's *answer* budget. ``SARVAM_THINK_BUFFER`` is
            added on top so the ``<think>`` block never crowds it out.
        temperature: Default ``0.1`` matches ``llm._call_sarvam_llm``.
        **kwargs: Reserved for future-proofing (``top_p``, ``stop``, …);
            silently ignored today so callers can pre-emptively pass
            them without hitting an unexpected-kwarg TypeError.

    Returns:
        ``ChatResponse`` with ``<think>...</think>`` reasoning stripped
        and a real ``cost_usd`` derived from upstream token usage.

    Raises:
        SarvamRateLimited: 429 from Sarvam OR per-user cap exhausted.
        SarvamUnavailable: 5xx, timeout, transport error, or the client
            isn't initialised (no ``SARVAM_API_KEY`` configured).
    """
    # Local imports — keep module-import safe in test environments
    # that stub deps before the real client is built.
    from deps import sarvam_llm_client
    from cost_caps import record_sarvam_user_call
    from metrics import maybe_emit_sarvam_alert, record_sarvam_chat

    if sarvam_llm_client is None:
        # Surface as Unavailable so the dispatcher advances to the
        # workers_ai_indic fallback. We deliberately do NOT raise
        # SarvamRateLimited here — missing key is configuration drift,
        # not a transient cap.
        record_sarvam_chat(success=False, latency_ms=0.0, error="no_client")
        raise SarvamUnavailable("sarvam_llm_client not initialised — SARVAM_API_KEY missing?")

    if not record_sarvam_user_call(user_id):
        record_sarvam_chat(success=False, latency_ms=0.0, error="per_user_monthly_cap")
        raise SarvamRateLimited("per_user_monthly_cap")

    # Language → BCP-47 tag for Sarvam.
    response_language = _LANGUAGE_TAGS.get((language or "").lower(), language or "as-IN")

    api_max = max_tokens + _THINK_BUFFER
    payload: dict[str, Any] = {
        "model": _MODEL,
        "messages": messages,
        "max_tokens": api_max,
        "temperature": temperature,
        "stream": False,
        "response_language": response_language,
    }
    if thinking_budget > 0:
        payload["thinking_budget"] = thinking_budget

    t0 = time.perf_counter()
    success = False
    err_str: Optional[str] = None
    prompt_tokens = completion_tokens = 0
    try:
        try:
            resp = await sarvam_llm_client.post("/v1/chat/completions", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            err_str = f"transport: {type(exc).__name__}: {exc}"
            raise SarvamUnavailable(err_str) from exc

        if resp.status_code == 429:
            ra = resp.headers.get("retry-after") if hasattr(resp, "headers") else None
            try:
                ra_int = int(ra) if ra is not None else None
            except (TypeError, ValueError):
                ra_int = None
            err_str = "upstream_429"
            raise SarvamRateLimited("upstream_429", retry_after=ra_int)
        if resp.status_code >= 500:
            err_str = f"upstream {resp.status_code}: {resp.text[:200]}"
            raise SarvamUnavailable(err_str)
        if resp.status_code >= 400:
            # 4xx other than 429 — propagate as Unavailable so the
            # dispatcher advances rather than infinite-retrying.
            err_str = f"upstream {resp.status_code}: {resp.text[:200]}"
            raise SarvamUnavailable(err_str)

        try:
            data = resp.json() or {}
        except Exception as exc:
            err_str = f"non-JSON body: {exc}"
            raise SarvamUnavailable(err_str) from exc

        choices = data.get("choices") or []
        if not choices:
            err_str = "empty choices in response"
            raise SarvamUnavailable(err_str)
        choice = choices[0].get("message") or {}
        content = choice.get("content") or ""
        reasoning = choice.get("reasoning_content") or ""
        text = content if content else reasoning
        text = _THINK_RE_BLOCK.sub("", text)
        text = _THINK_RE_OPEN.sub("", text).strip()

        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        normalised_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": int(usage.get("total_tokens") or (prompt_tokens + completion_tokens)),
        }

        success = True
        latency_ms = (time.perf_counter() - t0) * 1000.0
        from metrics import _sarvam_cost_usd

        return ChatResponse(
            text=text,
            model=data.get("model") or _MODEL,
            latency_ms=round(latency_ms, 1),
            usage=normalised_usage,
            cost_usd=round(_sarvam_cost_usd(prompt_tokens, completion_tokens), 6),
        )
    finally:
        latency_final = (time.perf_counter() - t0) * 1000.0
        # Canonical metrics write — feeds the admin tile + Sentry alert.
        try:
            record_sarvam_chat(
                success=success,
                latency_ms=latency_final,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                error=err_str,
            )
        except Exception:
            pass
        # Throttled Sentry alert (once per hour per replica).
        try:
            maybe_emit_sarvam_alert()
        except Exception:
            pass
        # Audit trail in the canonical LLM-call ring (Task #271 schema)
        # so this facade shows up alongside the live `_call_sarvam_llm`
        # entries in the admin RoutingPools panel and MeterD tally.
        try:
            from llm import _record_llm_call  # type: ignore

            _record_llm_call(
                "sarvam",
                _MODEL,
                latency_final,
                success,
                tokens_approx=prompt_tokens + completion_tokens,
                feature_key="sarvam_chat",
                error_type=(err_str or "")[:64],
            )
        except Exception:
            pass
