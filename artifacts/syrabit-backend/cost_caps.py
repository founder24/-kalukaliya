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
from datetime import datetime, timezone
from typing import Iterable, List, Mapping, Optional

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

# ── Task #27 — Cohere `embed-multilingual-v3` via AWS Bedrock ──────────────
# On-demand price for `cohere.embed-multilingual-v3` in `us-east-1` is
# $0.0001 per 1k input tokens (AWS pricing page sampled 2026-05-09).
# `llm.call_embed_with_dispatch` charges this rate against MeterD's
# Indic sub-cap on every successful Bedrock call. Raising this rate
# constant requires a "# COST-CAP-OVERRIDE: <reason>" comment on the
# changed line — `scripts/check_budget_ceiling.py` enforces.
BEDROCK_COHERE_EMBED_USD_PER_1K_TOKENS = 0.0001
# Sub-cap dedicated to the Indic embed route. Sits INSIDE the $100
# global cap (counts against it) but tripping THIS sub-cap shuts down
# only the Bedrock route — `is_indic_embed_paused()` returns True and
# the dispatcher routes Indic queries to Workers AI for the rest of
# the calendar month. Default $5/mo headroom is ample for the
# expected Assamese embed volume (≤50M tokens/mo at $0.0001/1k =
# $5/mo) while staying safely below the global cap. Raising this
# requires a `# COST-CAP-OVERRIDE: <reason>` comment.
INDIC_EMBED_MONTHLY_USD_SUBCAP = 5.0

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

# ── Task #581 — free-tier-first MeterD ladder (L10) ────────────────────────
# These four thresholds run BEFORE the 60/80/95 ladder above so the
# system sheds free-user load FIRST (the cohort that drives ~80% of cost
# but contributes <5% of revenue) before touching paid features. Each
# step is consumed by `free_tier_dispatch_state(spend_fraction)`:
#   40 % → free output token cap halved (L7 caps tightened further).
#   55 % → free turns 21-30 collapse to paywall (drop the retrieval-only
#          bucket — the lambda still answers via the materialized stores
#          inside `retrieval_first.try_resolve` but no LLM is invoked).
#   70 % → free turns 11-20 collapse to paywall (drop the tight bucket).
#   85 % → ALL free chat → paywall (only turns 1-10 cheap survive… no,
#          this stage is the "all-free-chat-off" stage; turns 1-10 also
#          paywall here. The 95% DEGRADATION_PCT_FREE_503 stage above
#          is the last-resort 503 for free users, paid still works.)
# Strict ordering enforced by `scripts/check_budget_ceiling.py`:
#   0 < TIGHTEN_1 < TIGHTEN_2 < TIGHTEN_3 < TIGHTEN_4 < PAUSE_BATCH=0.60
DEGRADATION_PCT_FREE_TIGHTEN_1 = 0.40
DEGRADATION_PCT_FREE_TIGHTEN_2 = 0.50
DEGRADATION_PCT_FREE_TIGHTEN_3 = 0.55
DEGRADATION_PCT_FREE_TIGHTEN_4 = 0.58


def _monthly_total_usd_cap() -> float:
    raw = (os.environ.get("MONTHLY_TOTAL_USD_CAP", "") or "").strip()
    if not raw:
        return _DEFAULT_MONTHLY_TOTAL_USD_CAP
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_MONTHLY_TOTAL_USD_CAP


import time as _t_runway

# ── Task #2 — 2026 blueprint: 3-tier credit-runway-aware english chat chain ─
# Default order: Vertex Gemini 2.5 Flash (drains GCP startup credits) →
# Vertex Gemini 2.5 Flash Lite (cheaper Vertex SKU; same SA, same tenant,
# half the per-token cost) → Workers-AI Llama-3.2-3B (free-tier tail). When
# projected GCP credit runway falls to ≤ 90 days the order flips so the
# free-tier head conserves credits while Vertex stays in the chain as the
# paid fallback (V4 §12 — no silent removal). The result is cached for 60 s
# on a monotonic clock so a hot dispatch loop never thrashes env / redis
# reads. Operator override: `CHAT_PRIMARY_OVERRIDE=vertex|workers_ai`;
# unrecognised values log + are ignored.
_CHAT_CHAIN_DEFAULT: tuple[str, str, str] = (
    "vertex", "vertex_flash_lite", "workers_ai_llama32_3b",
)
_CHAT_CHAIN_FLIPPED: tuple[str, str, str] = (
    "workers_ai_llama32_3b", "vertex_flash_lite", "vertex",
)
_CHAT_RUNWAY_FLIP_DAYS = 90.0
_CHAT_PRIMARY_CACHE_TTL_S = 60.0
_chat_primary_cache: dict = {"chain": None, "ts": 0.0}


# Task #565 — Redis key the daily `chat-credit-runway` Lambda publishes
# the integer runway estimate to (TTL 48 h). The selector reads it
# between the operator env override and the env-derived computation so
# a cron-published value flips the chain on the next 60 s cache refresh
# without a backend redeploy. Two missed Lambda runs (>24 h) lets the
# key expire, the selector falls back to the env path, and the
# `chat-credit-runway-stale` CloudWatch alarm pages on-call.
_RUNWAY_REDIS_KEY = "chat:credit_runway_days"


def _runway_from_redis() -> float | None:
    """Read the cron-published runway integer from Upstash Redis.

    Returns None when:
      * deps.redis_client is unavailable (no Upstash creds in env), OR
      * the key is missing / expired (Lambda has not published in 48 h), OR
      * the value cannot be parsed as a positive number.

    Never raises — Redis hiccups must not break the chat hot path.
    """
    try:
        from deps import redis_client as _rc  # type: ignore
        if _rc is None:
            return None
        raw = _rc.get(_RUNWAY_REDIS_KEY)
    except Exception:
        return None
    if raw is None:
        return None
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
        return max(0.0, float(decoded.strip()))
    except (ValueError, AttributeError):
        return None


def _projected_chat_runway_days() -> float | None:
    """Return projected days of GCP credit runway, or None if unknown.

    Resolution order (highest priority first):
      1. `CHAT_CREDIT_RUNWAY_DAYS` env  — operator manual override.
      2. Redis key `chat:credit_runway_days` — daily value published by
         the Task #565 `chat-credit-runway` Lambda from the GCP Billing
         BigQuery export. Auto-expires after 48 h so a stuck Lambda
         falls back instead of pinning a stale number forever.
      3. `GCP_CREDITS_REMAINING_USD` env + MeterD MTD burn — legacy
         fallback retained so the selector still has a signal if both
         the operator override and the Redis publisher are missing.

    Returns None when nothing is available, in which case callers must
    fall back to the default chain (V4 §12 — no silent flip).
    """
    direct = (os.environ.get("CHAT_CREDIT_RUNWAY_DAYS") or "").strip()
    if direct:
        try:
            return max(0.0, float(direct))
        except ValueError:
            pass
    redis_value = _runway_from_redis()
    if redis_value is not None:
        return redis_value
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


def _compute_chat_chain() -> tuple[str, str, str]:
    """Pure (env+meter only) computation of the 3-position chain (Task #2)."""
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
    """Credit-runway-aware ordered chain for english_rag_chat (Task #2).

    Returns a 3-position list of provider names: position 0 is the head;
    positions 1 and 2 are the explicit fallbacks that
    `call_with_provider_fallback` advances to on primary 5xx / 429 /
    breaker-open. Default order is
    ``["vertex", "vertex_flash_lite", "workers_ai_llama32_3b"]``; swaps
    to ``["workers_ai_llama32_3b", "vertex_flash_lite", "vertex"]`` when
    projected GCP credit runway is ≤ 90 days. Cached for 60 s on a
    monotonic clock so the hot dispatch path never re-reads env / redis
    per turn.
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

# ── Task #581 §L7 — free-tier per-content-type output sub-caps ─────────────
# These are *additional* per-content-type ceilings applied ONLY to free
# users — paid users keep the full TOKEN_BUDGETS["chat_turn"] /
# ["content_generation"] output budget. The dispatcher resolves the
# effective output cap as min(TOKEN_BUDGETS[call_type], FREE_TIER_OUTPUT_CAPS[content_type])
# via `effective_free_tier_output_cap(content_type, plan, base_cap)`.
# Numbers come from a sample-based audit of definition / explanation /
# MCQ-explanation / PYQ-answer outputs in admin pre-gen — the p95 length
# of *useful* answers fits comfortably under the cap; longer outputs are
# almost always model rambling.
FREE_TIER_OUTPUT_CAPS: dict[str, int] = {
    "definition":      200,   # one short paragraph
    "explanation":     400,   # ~3 short paragraphs
    "mcq_explanation": 200,   # one paragraph + answer
    "pyq_answer":      500,   # one full board-style answer
}


def effective_free_tier_output_cap(
    content_type: str,
    *,
    user_plan: str = "free",
    base_cap: int | None = None,
) -> int:
    """Return the effective output-token ceiling for a single LLM call.

    Paid plans always get `base_cap` (or the chat_turn budget when
    base_cap is None). Free plans get the smaller of `base_cap` and the
    per-content-type cap from `FREE_TIER_OUTPUT_CAPS`. Unknown
    content_types fall back to base_cap (no extra clamp). Used by L7 to
    keep "give me a definition" from spending 800 output tokens on
    free-tier turns.
    """
    base = int(base_cap) if base_cap is not None else int(
        TOKEN_BUDGETS["chat_turn"]["max_output_tokens"]
    )
    plan = (user_plan or "free").strip().lower()
    if plan and plan != "free":
        return base
    sub = FREE_TIER_OUTPUT_CAPS.get((content_type or "").strip().lower())
    if sub is None:
        return base
    return max(1, min(base, int(sub)))


# ── Task #581 §L9 — long-context >8k paid-only gate ────────────────────────
# Free callers cannot ship more than `LONG_CONTEXT_FREE_MAX_INPUT_TOKENS`
# of input to chat / content_generation. The chat dispatcher invokes
# `assert_input_under_long_context_cap(input_tokens, user_plan)` before
# the provider call; on violation it raises a 402 with a "long-context
# is a paid feature" body. Helper is pure — caller is responsible for
# the HTTPException path so this module stays import-safe in the
# non-FastAPI Lambda batch jobs.
LONG_CONTEXT_FREE_MAX_INPUT_TOKENS = 8_000


def is_long_context_paid_only(input_tokens: int, user_plan: str = "free") -> bool:
    """Return True iff this call must be paywalled because it exceeds
    the free-tier long-context ceiling. Paid plans always return False.
    """
    plan = (user_plan or "free").strip().lower()
    if plan and plan != "free":
        return False
    return int(input_tokens or 0) > LONG_CONTEXT_FREE_MAX_INPUT_TOKENS


# ── Task #581 §L10 — free-tier-first MeterD ladder evaluator ───────────────
def free_tier_dispatch_state(spend_fraction: float) -> dict:
    """Resolve the active free-tier degradation step from a spend ratio.

    `spend_fraction` is `current_month_usd / monthly_cap_usd` in
    [0.0, 1.0+]. Returns:

        {
            "level": 0..4,                        # 0 = no degradation
            "free_output_multiplier": 1.0|0.5,    # halves L7 caps at L1+
            "free_turns_21_30_paywalled": bool,   # collapse retrieval bucket
            "free_turns_11_20_paywalled": bool,   # collapse tight bucket
            "free_chat_paywalled": bool,          # all free chat → 402
        }

    Pure function — no Redis, no env. The caller computes
    `spend_fraction` from MeterD's monthly bucket via the runtime
    helper `credit_burn_meter_runtime.monthly_spend_fraction()`.
    """
    try:
        f = float(spend_fraction or 0.0)
    except (TypeError, ValueError):
        f = 0.0
    if f < 0.0:
        f = 0.0
    if f >= DEGRADATION_PCT_FREE_TIGHTEN_4:
        level = 4
    elif f >= DEGRADATION_PCT_FREE_TIGHTEN_3:
        level = 3
    elif f >= DEGRADATION_PCT_FREE_TIGHTEN_2:
        level = 2
    elif f >= DEGRADATION_PCT_FREE_TIGHTEN_1:
        level = 1
    else:
        level = 0
    return {
        "level": level,
        "free_output_multiplier": 0.5 if level >= 1 else 1.0,
        "free_turns_21_30_paywalled": level >= 2,
        "free_turns_11_20_paywalled": level >= 3,
        "free_chat_paywalled": level >= 4,
    }


# ── Task #581 §L4 — free-tier 4-step turn ladder constants ─────────────────
FREE_TIER_TURN_NORMAL_CEILING = 10        # 1-10  → cheap, full chat output
FREE_TIER_TURN_TIGHT_CEILING = 20         # 11-20 → cheap + tight output
FREE_TIER_TURN_RETRIEVAL_ONLY_CEILING = 30  # 21-30 → retrieval_first only
FREE_TIER_TIGHT_OUTPUT_TOKENS = 400       # smaller than CONSERVATIVE_OUTPUT_TOKENS


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
    monthly_spend_fraction: float = 0.0,
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
    is_indic = (
        lang_lc.startswith("as")
        or lang_lc in {"hi", "bn", "hi-in", "bn-in", "as-in"}
    )

    # Task #554 — credit-runway-aware 2-position chain. Position 0 is
    # the head we ship; position 1 is the documented fallback that
    # `call_with_provider_fallback` advances to on primary outage.
    _chain = _select_chat_primary()
    primary_provider = _chain[0]
    primary_model = (
        "gemini-2.5-flash" if primary_provider == "vertex"
        else "@cf/meta/llama-3.2-1b-instruct"
    )
    # Task #581 §L1 — free users are HARD-ROUTED off Vertex. Even when
    # the credit-runway-aware chain head is `vertex`, free callers
    # always get Workers-AI Llama-3.2-1B (or Mistral-7B in the cheap
    # bucket). Vertex spend is reserved for paid traffic + admin
    # generation.  Paid users keep the runway-aware primary.
    free_primary_provider = "workers_ai_llama32_3b"
    free_primary_model = "@cf/meta/llama-3.2-1b-instruct"

    # Rule D LOCKED — global monthly USD cap reached. Force the
    # cheap-tier (Workers-AI Mistral-7B) for English chat regardless of
    # plan / turn count. Assamese still uses Sarvam (the bypass below
    # has higher precedence) because the Indic specialist is the only
    # provider that produces Assamese output.
    if cheaponly_active and not is_indic:
        return {
            "tier": "cheap",
            "provider": "workers_ai_mistral_7b",
            "model": "@cf/mistral/mistral-7b-instruct-v0.3",
            "max_output_tokens": CONSERVATIVE_OUTPUT_TOKENS,
            "cheaponly_lock": True,
        }

    # Assamese bypass — Sarvam is the locked Assamese-chat primary.
    if is_indic:
        return {
            "tier": "primary",
            "provider": "sarvam",
            "model": "sarvam-m",
            "max_output_tokens": TOKEN_BUDGETS["chat_turn"]["max_output_tokens"],
        }

    # Paid users — runway-aware primary, full budget. Long-context
    # paywall (L9) is enforced by the caller via
    # `is_long_context_paid_only(input_tokens, plan)` BEFORE invoking
    # this helper.
    if plan and plan != "free":
        return {
            "tier": "paid",
            "provider": primary_provider,
            "model": primary_model,
            "max_output_tokens": TOKEN_BUDGETS["chat_turn"]["max_output_tokens"],
        }

    # ── Free user dispatch — Task #581 §L4 four-step turn ladder ─────────
    # Combined with the §L10 free-tier-first MeterD ladder via
    # `monthly_spend_fraction`. The caller is responsible for honouring
    # tier=="paywall" / tier=="retrieval_only" by either returning a
    # 402 or invoking `retrieval_first.try_resolve(...)` before any
    # LLM call.
    turn = max(0, int(session_turn_count or 0))
    fts = free_tier_dispatch_state(monthly_spend_fraction)
    out_mult = float(fts["free_output_multiplier"])

    # §L10 stage-4 — all free chat collapsed.
    if fts["free_chat_paywalled"]:
        return {
            "tier": "paywall",
            "provider": None,
            "model": None,
            "max_output_tokens": 0,
            "reason": "meter_d_free_tighten_4",
            "free_tier_state": fts,
        }

    # §L4 hard ceiling — turn 31+ → paywall regardless of spend.
    if turn > FREE_TIER_TURN_RETRIEVAL_ONLY_CEILING:
        return {
            "tier": "paywall",
            "provider": None,
            "model": None,
            "max_output_tokens": 0,
            "reason": "free_tier_turn_ceiling_30",
            "free_tier_state": fts,
        }

    # §L4 turns 21-30 — retrieval-only bucket. The caller MUST first
    # call `retrieval_first.try_resolve(...)`; if that misses, the
    # bucket collapses to a paywall (no LLM call). At §L10 stage-2+ we
    # also collapse this bucket directly.
    if turn > FREE_TIER_TURN_TIGHT_CEILING:
        if fts["free_turns_21_30_paywalled"]:
            return {
                "tier": "paywall",
                "provider": None,
                "model": None,
                "max_output_tokens": 0,
                "reason": "meter_d_free_tighten_2_collapse_retrieval_only",
                "free_tier_state": fts,
            }
        return {
            "tier": "retrieval_only",
            "provider": None,
            "model": None,
            "max_output_tokens": 0,
            "reason": "free_tier_turn_21_30_retrieval_only",
            "free_tier_state": fts,
        }

    # §L4 turns 11-20 — tight cheap (workers_ai_mistral_7b at 400 out).
    # §L10 stage-3+ collapses this bucket to paywall.
    if turn > FREE_TIER_TURN_NORMAL_CEILING:
        if fts["free_turns_11_20_paywalled"]:
            return {
                "tier": "paywall",
                "provider": None,
                "model": None,
                "max_output_tokens": 0,
                "reason": "meter_d_free_tighten_3_collapse_tight",
                "free_tier_state": fts,
            }
        return {
            "tier": "tight",
            "provider": "workers_ai_mistral_7b",
            "model": "@cf/mistral/mistral-7b-instruct-v0.3",
            "max_output_tokens": max(1, int(FREE_TIER_TIGHT_OUTPUT_TOKENS * out_mult)),
            "free_tier_state": fts,
        }

    # §L4 turns 1-10 — normal cheap. Output cap honours §L10 stage-1
    # halving when active.
    base_out = TOKEN_BUDGETS["chat_turn"]["max_output_tokens"]
    return {
        "tier": "cheap",
        "provider": "workers_ai_mistral_7b",
        "model": "@cf/mistral/mistral-7b-instruct-v0.3",
        "max_output_tokens": max(1, int(base_out * out_mult)),
        "free_tier_state": fts,
    }


__all__ = [
    "TOKEN_BUDGETS",
    "SESSION_CHEAP_TURN_LIMIT",
    "CONSERVATIVE_OUTPUT_TOKENS",
    "DEGRADATION_PCT_PAUSE_BATCH",
    "DEGRADATION_PCT_VOICE_OFF",
    "DEGRADATION_PCT_FREE_503",
    "DEGRADATION_PCT_FREE_TIGHTEN_1",
    "DEGRADATION_PCT_FREE_TIGHTEN_2",
    "DEGRADATION_PCT_FREE_TIGHTEN_3",
    "DEGRADATION_PCT_FREE_TIGHTEN_4",
    "FREE_TIER_OUTPUT_CAPS",
    "FREE_TIER_TURN_NORMAL_CEILING",
    "FREE_TIER_TURN_TIGHT_CEILING",
    "FREE_TIER_TURN_RETRIEVAL_ONLY_CEILING",
    "FREE_TIER_TIGHT_OUTPUT_TOKENS",
    "LONG_CONTEXT_FREE_MAX_INPUT_TOKENS",
    "effective_free_tier_output_cap",
    "is_long_context_paid_only",
    "free_tier_dispatch_state",
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
    "SARVAM_PER_USER_MONTHLY_CAP",
    "record_sarvam_user_call",
]


# ── Sarvam-AI per-user monthly cap interceptor (Task #553) ─────────────────
# Defensive in-process backstop for the edge worker's CHAT_CAP_MONTHLY=30.
# `providers/sarvam.chat()` calls `record_sarvam_user_call(user_id)` before
# every dispatch; a False return surfaces as `SarvamRateLimited
# ("per_user_monthly_cap")` (V4 §12 — fail loud, no silent downgrade).
SARVAM_PER_USER_MONTHLY_CAP = int(
    os.environ.get("SARVAM_PER_USER_MONTHLY_CAP", "30") or "30"
)


def record_sarvam_user_call(user_id: Optional[str], *, cap: Optional[int] = None) -> bool:
    """Increment the per-user month bucket for Sarvam.

    Returns ``True`` when the call is allowed (under the cap or anon /
    cap disabled / Redis unavailable). Returns ``False`` ONLY when an
    authenticated user has just crossed the configured monthly cap.

    No-op (returns ``True``) when:
      • ``cap <= 0`` or `SARVAM_PER_USER_MONTHLY_CAP <= 0` (override),
      • ``user_id`` is falsy (anon — edge worker is the canonical
        enforcer; it keys on ``anon-id`` which we don't see here),
      • Redis is unavailable (best-effort by design — the edge cap is
        the authoritative shed; an unreachable in-process bucket must
        not 429 the chain since Sarvam itself is still healthy).
    """
    effective_cap = cap if cap is not None else SARVAM_PER_USER_MONTHLY_CAP
    if effective_cap <= 0 or not user_id:
        return True
    try:
        from deps import redis_client as _rc  # local import — see deps stub
    except Exception:
        return True
    if not _rc:
        return True
    month = datetime.now(timezone.utc).strftime("%Y%m")
    key = f"sarvam:user:{user_id}:{month}"
    try:
        new_val = int(_rc.incr(key))
        if new_val == 1:
            _rc.expire(key, 32 * 86400)
    except Exception:
        return True
    return new_val <= effective_cap

