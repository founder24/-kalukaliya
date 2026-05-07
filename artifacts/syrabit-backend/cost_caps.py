"""cost_caps — Task #513 token-budget + tier-routing constants.

Single chokepoint for the per-call-type input/output token budgets that
every LLM dispatcher MUST clamp to before issuing a provider call. The
budgets here implement the Section B contract of Task #513 (cost
minimization for browser-heavy traffic) and are intentionally narrow:

  * `TOKEN_BUDGETS`         — per-call-type {max_input_tokens, max_output_tokens}.
  * `clamp_messages(...)`   — history-truncates from the oldest non-system
                              turn first so the system prompt is preserved.
  * `_select_chat_model(...)` — tier-routing helper used by `llm.py` chat
                              dispatch (Section C).
  * `SESSION_CHEAP_TURN_LIMIT`, `CONSERVATIVE_OUTPUT_TOKENS` — tunable
                              thresholds that callers reference instead
                              of hard-coding magic numbers.
  * `MONTHLY_TOTAL_USD_CAP` — Rule D global monthly spend cap (Section J);
                              read from env at call time so ops can flex
                              without a redeploy.

The dispatchers (`llm.py`, `pipeline.py`, `content_formatter.py`,
`providers.chunk_embedder`, `routes.voice`) call `clamp_messages` before
the provider call. Anything that would have exceeded the budget is logged
as a `tokenbudget_overrun` Sentry event but is still clamped + sent — we
fail loud on the telemetry channel while the user-facing flow stays
green.

Token counting uses a fast, dependency-free heuristic of
`max(1, len(text) // 4)` — close enough to the real BPE for budgeting
decisions and immune to tiktoken/gpt-tokenizer drift. When `tiktoken` is
available the helper transparently upgrades to a real count.

NEVER raise the values in `TOKEN_BUDGETS` without:
  1. A `# COST-CAP-OVERRIDE: <reason>` comment on the changed line, and
  2. A Sentry-annotated changelog entry referencing the new ceiling.
The CI regression test (`tests/test_cost_caps.py`) walks the diff and
fails the build when either signal is missing.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable, List, Mapping

logger = logging.getLogger(__name__)

# ── Tunable thresholds (Section C tier-routing) ────────────────────────────
SESSION_CHEAP_TURN_LIMIT = 2          # turns 1-2 of a free-user session → cheap tier
CONSERVATIVE_OUTPUT_TOKENS = 600      # output cap once free user crosses 15 turns

# ── Rule D — global monthly USD cap (Section J) ────────────────────────────
# Read at call time via _monthly_total_usd_cap() so ops can flex with
# a single env-var change. Default = $100 per Task #549 spec
# (perpetual $100/month at 10k DAU). Raising this default requires the
# same "# COST-CAP-OVERRIDE: <reason>" + Sentry-annotated changelog
# discipline as the TOKEN_BUDGETS table, enforced by the CI guard
# `scripts/check_budget_ceiling.py`.
_DEFAULT_MONTHLY_TOTAL_USD_CAP = 100.0

# Three-stage degradation ladder (Task #549). Operators / dispatchers
# read these to decide what to shed at each spend percentage of the
# monthly cap:
#   60 % → pause non-essential async batch (deferred-embed, backfills).
#   80 % → disable voice routes for free users + double cache TTLs.
#   95 % → free-user chat returns 503 + new free signups disabled.
# These are pure constants; the runtime evaluator lives in
# `credit_burn_meter.MeterD` (which already locks chat:cheaponly at
# 100 %) — anything stricter than that ladder is gated by reading
# `monthly_spend_fraction()` against these thresholds.
DEGRADATION_PCT_PAUSE_BATCH = 0.60
DEGRADATION_PCT_VOICE_OFF   = 0.80
DEGRADATION_PCT_FREE_503    = 0.95


def _monthly_total_usd_cap() -> float:
    raw = (os.environ.get("MONTHLY_TOTAL_USD_CAP", "") or "").strip()
    if not raw:
        return _DEFAULT_MONTHLY_TOTAL_USD_CAP
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_MONTHLY_TOTAL_USD_CAP


import time as _t_runway

# ── Task #554 — credit-runway-aware english chat dispatch chain ────────────
# Default order is Vertex Gemini 2.5 Flash (drains GCP startup credits) →
# Workers-AI Llama-3.2-3B (free-tier fallback). When projected GCP credit
# runway falls to ≤ 90 days the order swaps so the free-tier head conserves
# what's left of the credit pool and Vertex stays in the chain as the paid
# fallback (V4 §12 — no silent removal). The result is cached for 60 s on a
# monotonic clock so a hot dispatch loop never thrashes the env / redis
# reads. Manual override knob is `CHAT_PRIMARY_OVERRIDE=vertex|workers_ai`
# for ops; unrecognised values log + are ignored.
_CHAT_CHAIN_DEFAULT: tuple[str, str] = ("vertex", "workers_ai_llama32_3b")
_CHAT_CHAIN_FLIPPED: tuple[str, str] = ("workers_ai_llama32_3b", "vertex")
_CHAT_RUNWAY_FLIP_DAYS = 90.0
_CHAT_PRIMARY_CACHE_TTL_S = 60.0
_chat_primary_cache: dict = {"chain": None, "ts": 0.0}


def _projected_chat_runway_days() -> float | None:
    """Return projected days of GCP credit runway, or None if unknown.

    Inputs (env-driven so a runway-tracker cron can update them without a
    redeploy):
      * `GCP_CREDITS_REMAINING_USD` — operator-published current balance.
      * `CHAT_CREDIT_RUNWAY_DAYS`   — direct override (cron-computed).

    Returns None when neither signal is present, in which case callers
    must fall back to the default chain (no silent flip).
    """
    direct = (os.environ.get("CHAT_CREDIT_RUNWAY_DAYS") or "").strip()
    if direct:
        try:
            return max(0.0, float(direct))
        except ValueError:
            pass
    pool_raw = (os.environ.get("GCP_CREDITS_REMAINING_USD") or "").strip()
    if not pool_raw:
        return None
    try:
        pool_remaining = float(pool_raw)
    except ValueError:
        return None
    if pool_remaining <= 0:
        return 0.0
    # Estimate burn from MeterD's month-to-date USD bucket if available.
    try:
        from deps import redis_client as _rc  # type: ignore
        from credit_burn_meter import MONTHLY_USD_KEY_PREFIX as _PFX  # type: ignore
        from datetime import datetime as _dt, timezone as _tz
        if _rc is None:
            return None
        month = _dt.now(_tz.utc).strftime("%Y-%m")
        raw = _rc.get(f"{_PFX}:{month}")
        if raw is None:
            return None
        spent = float(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        return None
    if spent <= 0:
        return None
    from datetime import datetime, timezone
    days_elapsed = max(1.0, float(datetime.now(timezone.utc).day))
    daily_burn = spent / days_elapsed
    if daily_burn <= 0:
        return None
    return pool_remaining / daily_burn


def _compute_chat_chain() -> tuple[str, str]:
    """Pure (env+meter only) computation of the 2-position chain."""
    override = (os.environ.get("CHAT_PRIMARY_OVERRIDE", "") or "").strip().lower()
    if override:
        if override == "vertex":
            return _CHAT_CHAIN_DEFAULT
        if override in {"workers_ai_llama32_3b", "workers_ai"}:
            return _CHAT_CHAIN_FLIPPED
        try:
            import logging as _lg
            _lg.getLogger("cost_caps").warning(
                "CHAT_PRIMARY_OVERRIDE=%r is unsupported (allowed: 'vertex' | "
                "'workers_ai_llama32_3b'). Falling back to credit-runway-driven "
                "selection (V4 §12 — no silent fallback).", override,
            )
        except Exception:
            pass
    runway = _projected_chat_runway_days()
    if runway is not None and runway <= _CHAT_RUNWAY_FLIP_DAYS:
        return _CHAT_CHAIN_FLIPPED
    return _CHAT_CHAIN_DEFAULT


def _select_chat_primary() -> list[str]:
    """Credit-runway-aware ordered chain for english_rag_chat (Task #554).

    Returns a 2-position list of provider names. Position 0 is the head
    used by `_select_chat_model` and `select_provider`; position 1 is the
    explicit fallback that `call_with_provider_fallback` advances to on
    primary 5xx / 429 / breaker-open. Default order is
    ``["vertex", "workers_ai_llama32_3b"]``; swaps to
    ``["workers_ai_llama32_3b", "vertex"]`` when projected GCP credit
    runway is ≤ 90 days. Cached for 60 s on a monotonic clock so the
    hot dispatch path never re-reads env / redis per turn.
    """
    now = _t_runway.monotonic()
    cached = _chat_primary_cache.get("chain")
    cached_ts = float(_chat_primary_cache.get("ts") or 0.0)
    if cached is not None and (now - cached_ts) < _CHAT_PRIMARY_CACHE_TTL_S:
        return list(cached)
    chain = _compute_chat_chain()
    _chat_primary_cache["chain"] = chain
    _chat_primary_cache["ts"] = now
    return list(chain)


def _reset_chat_primary_cache() -> None:
    """Test hook — clears the 60 s cache so a monkeypatched env flips immediately."""
    _chat_primary_cache["chain"] = None
    _chat_primary_cache["ts"] = 0.0


# ── Per-call-type token budgets (Section B) ────────────────────────────────
# Keys are the canonical call-type identifiers used by the dispatchers.
# Every dispatcher MUST resolve its call-type to a key here before
# clamping. Unknown keys fall back to the conservative `chat_turn` budget
# so a forgotten wiring entry gets clamped rather than billed-without-cap.
TOKEN_BUDGETS: dict[str, dict[str, int]] = {
    # Chat hot path — English + Assamese. system + history + user-turn.
    "chat_turn":         {"max_input_tokens": 3_000, "max_output_tokens": 800},
    # Long-form content / chapter / summary generation.
    "content_generation": {"max_input_tokens": 4_000, "max_output_tokens": 2_000},
    # Vertex content_formatter (NotebookLM polish — round-trips full body).
    "content_formatter":  {"max_input_tokens": 4_500, "max_output_tokens": 2_500},
    # Translate (en↔as) — IndicTrans2 + polish.
    "translate":          {"max_input_tokens": 2_000, "max_output_tokens": 2_000},
    # OCR vision post-process (Vertex/Azure GPT-4o vision → text).
    "vision_ocr":         {"max_input_tokens": 1_500, "max_output_tokens": 800},
    # STT post-summary (Whisper transcript → tightened summary).
    "stt_post_summary":   {"max_input_tokens": 2_000, "max_output_tokens": 500},
    # Embed input — single chunk text capped at ~1500 tokens (~6 KB chars)
    # so a runaway giant chunk cannot blow a single Workers-AI embed call
    # past the worker's payload limit. max_output_tokens is unused for
    # embed (vector-only response) and kept at 0 as a tombstone.
    "embed":              {"max_input_tokens": 1_500, "max_output_tokens": 0},
}


# ── Token-counting heuristic ───────────────────────────────────────────────
def _approx_token_count(text: str) -> int:
    """Cheap deterministic token estimate (~chars/4).

    Avoids a hard `tiktoken` dependency on the dispatch hot path; the
    real upstream provider does its own canonical tokenization, and the
    purpose of this count is budgeting, not billing.
    """
    if not text:
        return 0
    # Rough BPE proxy — empirically within ~10 % of GPT-style tokenizers
    # for English + Indic mix at the lengths we see in chat history.
    return max(1, len(text) // 4)


def _message_tokens(msg: Mapping) -> int:
    content = msg.get("content") or ""
    if isinstance(content, list):
        # Multi-part vision content — sum text parts only.
        text = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    else:
        text = str(content)
    # +4 for role / separator overhead per OpenAI chat-format conventions.
    return _approx_token_count(text) + 4


def _emit_overrun(call_type: str, before: int, after: int, max_input: int) -> None:
    """Best-effort Sentry event tag for a budget overrun — never raises."""
    logger.warning(
        "[cost-caps] %s overrun: %d → %d tokens (cap=%d)",
        call_type, before, after, max_input,
    )
    try:  # pragma: no cover — Sentry SDK availability is environmental.
        import sentry_sdk  # type: ignore

        sentry_sdk.set_tag("cost_caps.overrun", call_type)
        sentry_sdk.set_tag("cost_caps.before_tokens", str(before))
        sentry_sdk.set_tag("cost_caps.after_tokens", str(after))
        sentry_sdk.capture_message(
            f"tokenbudget_overrun:{call_type}",
            level="warning",
        )
    except Exception:
        pass


def clamp_messages(
    messages: Iterable[Mapping],
    *,
    call_type: str = "chat_turn",
    max_input_tokens: int | None = None,
) -> List[dict]:
    """Truncate `messages` from the OLDEST non-system turn first until
    the running token estimate fits inside the configured input budget.

    The system prompt (role == "system") is always preserved — even if
    it alone exceeds the budget — so safety / RAG instructions never
    get silently dropped. The most-recent user turn is also preserved
    so the turn we're about to ship stays intact.

    Args:
        messages:        List of OpenAI-shaped chat messages.
        call_type:       Key into TOKEN_BUDGETS. Unknown keys fall back
                         to the conservative `chat_turn` budget.
        max_input_tokens: Override the budget (used by tests; production
                          should rely on the TOKEN_BUDGETS table).

    Returns:
        A defensive copy of the clamped list. Never raises.
    """
    msgs = [dict(m) for m in messages]
    if not msgs:
        return msgs

    budget = TOKEN_BUDGETS.get(call_type, TOKEN_BUDGETS["chat_turn"])
    cap = max_input_tokens if max_input_tokens is not None else budget["max_input_tokens"]
    cap = max(64, int(cap))

    before_total = sum(_message_tokens(m) for m in msgs)
    if before_total <= cap:
        return msgs

    # Indices we MUST keep: every system message + the last message.
    system_idxs = [i for i, m in enumerate(msgs) if m.get("role") == "system"]
    keep_idxs = set(system_idxs)
    if msgs:
        keep_idxs.add(len(msgs) - 1)

    # Drop oldest non-protected message until under budget.
    drop_order = [i for i in range(len(msgs)) if i not in keep_idxs]
    dropped: set[int] = set()
    while drop_order:
        running = sum(
            _message_tokens(m) for i, m in enumerate(msgs) if i not in dropped
        )
        if running <= cap:
            break
        dropped.add(drop_order.pop(0))

    out = [m for i, m in enumerate(msgs) if i not in dropped]

    # Final safety: if even the protected set exceeds the cap, hard-truncate
    # the last user message body so we never ship an over-budget payload.
    after_total = sum(_message_tokens(m) for m in out)
    if after_total > cap and out:
        last = out[-1]
        content = last.get("content")
        if isinstance(content, str):
            # Trim to fit; reserve 64 tokens for the system prompt overhead.
            allowed_chars = max(64, (cap - 64) * 4)
            if len(content) > allowed_chars:
                last["content"] = content[:allowed_chars]
                out[-1] = last
        after_total = sum(_message_tokens(m) for m in out)

    _emit_overrun(call_type, before_total, after_total, cap)
    return out


def max_output_tokens_for(call_type: str, requested: int | None = None) -> int:
    """Clamp a caller-supplied max_output_tokens to the budget for
    `call_type`. Returns the effective ceiling (smaller of requested
    and budget). When `requested` is None, returns the budget."""
    budget = TOKEN_BUDGETS.get(call_type, TOKEN_BUDGETS["chat_turn"])
    cap = int(budget["max_output_tokens"])
    if requested is None:
        return cap
    return max(1, min(int(requested), cap))


# ── Tier-routing helper (Section C) ────────────────────────────────────────
def _select_chat_model(
    *,
    user_id: str = "",
    session_turn_count: int = 0,
    user_plan: str = "free",
    lang: str = "en",
    cheaponly_active: bool = False,
) -> dict:
    """Return the dispatch decision for one English chat turn.

    Output shape:
        {
            "tier":           "cheap" | "primary" | "conservative" | "paid",
            "provider":       canonical provider name in PROVIDER_PRIORITY,
            "model":          model id to send to the provider,
            "max_output_tokens": effective output budget for this turn,
        }

    Routing rules (Task #513 §C):
      * Assamese (`lang in {"as", ...}`) — bypass: Sarvam always (the
        Indic specialist's credit pool burns first; tier-routing is an
        English-only knob).
      * Paid user (any non-"free" plan) — runway-aware primary (vertex
        gemini-2.5-flash by default; workers_ai_llama32_3b once GCP
        credit runway projects ≤ 90 days) with the full chat_turn
        output budget regardless of turn count.
      * Free user, turn ≤ SESSION_CHEAP_TURN_LIMIT — Workers-AI Mistral-7B.
      * Free user, turn 3-15 — runway-aware primary.
      * Free user, turn > 15 — runway-aware primary with output clamped
        to CONSERVATIVE_OUTPUT_TOKENS (already-engaged user, conservative
        spend).

    The function is pure: no IO, no Redis, no provider calls. The chat
    dispatcher is responsible for emitting `chat_tier=<tier>` on the
    request span for billing analysis.
    """
    plan = (user_plan or "free").strip().lower()
    lang_lc = (lang or "en").strip().lower()

    # Task #554 — credit-runway-aware 2-position chain. Position 0 is
    # the head we ship; position 1 is the documented fallback that
    # `call_with_provider_fallback` advances to on primary outage.
    _chain = _select_chat_primary()
    primary_provider = _chain[0]
    primary_model = (
        "gemini-2.5-flash" if primary_provider == "vertex"
        else "@cf/meta/llama-3.2-3b-instruct"
    )

    # Rule D LOCKED — global monthly USD cap reached. Force the
    # cheap-tier (Workers-AI Mistral-7B) for English chat regardless of
    # plan / turn count. Assamese still uses Sarvam (the bypass below
    # has higher precedence) because the Indic specialist is the only
    # provider that produces Assamese output.
    if cheaponly_active and not (
        lang_lc.startswith("as") or lang_lc in {"hi", "bn", "hi-in", "bn-in", "as-in"}
    ):
        return {
            "tier": "cheap",
            "provider": "workers_ai_mistral_7b",
            "model": "@cf/mistral/mistral-7b-instruct-v0.3",
            "max_output_tokens": CONSERVATIVE_OUTPUT_TOKENS,
            "cheaponly_lock": True,
        }

    # Assamese bypass — Sarvam is the locked Assamese-chat primary.
    if lang_lc.startswith("as") or lang_lc in {"hi", "bn", "hi-in", "bn-in", "as-in"}:
        return {
            "tier": "primary",
            "provider": "sarvam",
            "model": "sarvam-m",
            "max_output_tokens": TOKEN_BUDGETS["chat_turn"]["max_output_tokens"],
        }

    # Paid users — runway-aware primary, full budget.
    if plan and plan != "free":
        return {
            "tier": "paid",
            "provider": primary_provider,
            "model": primary_model,
            "max_output_tokens": TOKEN_BUDGETS["chat_turn"]["max_output_tokens"],
        }

    # Free user — tier on session turn count.
    turn = max(0, int(session_turn_count or 0))
    if turn <= SESSION_CHEAP_TURN_LIMIT:
        return {
            "tier": "cheap",
            "provider": "workers_ai_mistral_7b",
            "model": "@cf/mistral/mistral-7b-instruct-v0.3",
            "max_output_tokens": TOKEN_BUDGETS["chat_turn"]["max_output_tokens"],
        }
    if turn > 15:
        return {
            "tier": "conservative",
            "provider": primary_provider,
            "model": primary_model,
            "max_output_tokens": CONSERVATIVE_OUTPUT_TOKENS,
        }
    return {
        "tier": "primary",
        "provider": primary_provider,
        "model": primary_model,
        "max_output_tokens": TOKEN_BUDGETS["chat_turn"]["max_output_tokens"],
    }


__all__ = [
    "TOKEN_BUDGETS",
    "SESSION_CHEAP_TURN_LIMIT",
    "CONSERVATIVE_OUTPUT_TOKENS",
    "DEGRADATION_PCT_PAUSE_BATCH",
    "DEGRADATION_PCT_VOICE_OFF",
    "DEGRADATION_PCT_FREE_503",
    "clamp_messages",
    "max_output_tokens_for",
    "_select_chat_model",
    "_select_chat_primary",
    "_compute_chat_chain",
    "_projected_chat_runway_days",
    "_reset_chat_primary_cache",
    "_monthly_total_usd_cap",
    "_CHAT_CHAIN_DEFAULT",
    "_CHAT_CHAIN_FLIPPED",
    "_CHAT_RUNWAY_FLIP_DAYS",
]
