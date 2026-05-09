"""Task #937 — Autonomous nightly topic-discovery agent.

The pipeline previously only ran when an admin opened the SEO Manager
and clicked "generate". This module turns the discovery step into a
nightly cron that:

  1. Pulls candidate queries from three pluggable sources:
       * GSC near-miss queries (positions 11-20, last 7d). Fed via the
         ``gsc_near_miss_queries`` Mongo collection so this works even
         before a full Search Console OAuth round-trip — admins can
         seed that collection from any pipeline they already trust.
       * Suggest fan-out — ``google_suggest_client.fetch_india_edu_bundle``
         applied to the leaf syllabus topics in ``seo_topics`` /
         ``chapters``.
       * Trending — read from the ``trending_topics_raw`` collection.
         Same adapter philosophy as GSC: a thin Mongo contract so any
         scraper / RSS poller can drop rows in.

  2. Grades every candidate with an LLM grader (Workers-AI gpt-oss-120b
     via ``llm.call_llm_api_content`` — V4-compliant; the legacy direct-LLM tail
     paths removed in Task #347) on four axes — intent fit,
     syllabus alignment, search difficulty (vs current Syrabit topical
     authority), and AEO readability.

  3. Decides per candidate against two thresholds:
        score >= ``auto_publish_threshold``  → ``auto_published``
        score >= ``draft_threshold``         → ``drafted``
        otherwise                            → ``rejected``
     subject to per-day caps (default 10 auto, 50 draft).

  4. Auto-enqueues approved candidates into the existing pipeline by
     writing a ``seo_topics`` row through ``seo_writes.upsert_seo_topic``
     so the existing validators / generators pick them up. The agent
     never bypasses Stage 1→3.

  5. Persists the run + per-candidate outcomes into ``topic_discovery_runs``
     and ``topic_discovery_candidates`` so the admin dashboard can show
     last night's list, scores, decision and reason. Admin overrides
     (promote / reject) feed back into the next grader as few-shot
     examples (last 7 days).

The nightly loop is leader-gated by the caller (server.py) so we never
double-run on multi-replica deployments.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


RUNS_COLLECTION = "topic_discovery_runs"
CANDIDATES_COLLECTION = "topic_discovery_candidates"
GSC_NEAR_MISS_COLLECTION = "gsc_near_miss_queries"
TRENDING_RAW_COLLECTION = "trending_topics_raw"

DEFAULT_AUTO_PUBLISH_THRESHOLD = 80
DEFAULT_DRAFT_THRESHOLD = 55
DEFAULT_AUTO_PUBLISH_CAP = 10
DEFAULT_DRAFT_CAP = 50

# Default scoring weights for the four grader axes. They sum to 1.0
# (heaviest weight on syllabus alignment because Syrabit's positioning
# is "the Indian-board syllabus assistant" — a topic that doesn't map
# to a syllabus row is worth less than one that does, even if the
# search intent is excellent). Operators can override any of these via
# TOPIC_DISCOVERY_W_<AXIS> env vars without redeploying. We re-normalise
# the weights at read-time so a partial override never accidentally
# scales the total score outside the 0-100 band.
DEFAULT_W_SYLLABUS = 0.35
DEFAULT_W_INTENT = 0.25
DEFAULT_W_AEO = 0.20
DEFAULT_W_DIFFICULTY = 0.20
DEFAULT_RUN_HOUR_UTC = 2

MAX_SUGGEST_SEEDS_PER_RUN = 0  # 0 == no cap (every leaf topic). Operators
# can set TOPIC_DISCOVERY_SUGGEST_SEED_LIMIT to a positive int to bound LLM
# cost when the syllabus is unusually large.
MAX_GSC_PER_RUN = 200
MAX_TRENDING_PER_RUN = 100
MAX_CANDIDATES_TO_GRADE = 300
GRADER_MAX_TOKENS = 400
OVERRIDE_FEW_SHOT_LIMIT = 8
OVERRIDE_FEW_SHOT_LOOKBACK_DAYS = 7

GRADER_MODEL = os.environ.get(
    "TOPIC_DISCOVERY_GRADER_MODEL",
    "meta-llama/llama-4-scout-17b-16e-instruct",
)


# ── config ───────────────────────────────────────────────────────────


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, "").strip() or default)
        return v if v >= 0 else default
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("topic_discovery: env %s=%r not a float, using %s",
                       name, raw, default)
        return float(default)


def get_config() -> Dict[str, Any]:
    """Resolved, env-overridable configuration. Computed every call so
    admins can ship a hot-reload without restarting the loop.

    Includes the four scoring weights (``w_syllabus``, ``w_intent``,
    ``w_aeo``, ``w_difficulty``). Weights are re-normalised to sum to
    1.0 so a partial override (e.g. only ``TOPIC_DISCOVERY_W_SYLLABUS``)
    cannot push the computed total outside 0-100.
    """
    raw_w = {
        "w_syllabus": _env_float("TOPIC_DISCOVERY_W_SYLLABUS", DEFAULT_W_SYLLABUS),
        "w_intent": _env_float("TOPIC_DISCOVERY_W_INTENT", DEFAULT_W_INTENT),
        "w_aeo": _env_float("TOPIC_DISCOVERY_W_AEO", DEFAULT_W_AEO),
        "w_difficulty": _env_float("TOPIC_DISCOVERY_W_DIFFICULTY", DEFAULT_W_DIFFICULTY),
    }
    # Clamp to non-negative, then re-normalise.
    clamped = {k: max(0.0, v) for k, v in raw_w.items()}
    s = sum(clamped.values()) or 1.0
    norm_w = {k: v / s for k, v in clamped.items()}
    return {
        "auto_publish_threshold": _env_int(
            "TOPIC_DISCOVERY_AUTO_PUBLISH_THRESHOLD", DEFAULT_AUTO_PUBLISH_THRESHOLD,
        ),
        "draft_threshold": _env_int(
            "TOPIC_DISCOVERY_DRAFT_THRESHOLD", DEFAULT_DRAFT_THRESHOLD,
        ),
        "auto_publish_cap": _env_int(
            "TOPIC_DISCOVERY_AUTO_PUBLISH_CAP", DEFAULT_AUTO_PUBLISH_CAP,
        ),
        "draft_cap": _env_int(
            "TOPIC_DISCOVERY_DRAFT_CAP", DEFAULT_DRAFT_CAP,
        ),
        "run_hour_utc": _env_int(
            "TOPIC_DISCOVERY_RUN_HOUR_UTC", DEFAULT_RUN_HOUR_UTC,
        ) % 24,
        "disabled": _env_int("TOPIC_DISCOVERY_DISABLED", 0),
        **norm_w,
    }


def compute_weighted_total(
    *,
    syllabus_alignment: int,
    intent_fit: int,
    aeo_readability: int,
    difficulty: int,
    weights: Optional[Dict[str, float]] = None,
) -> int:
    """Deterministic blend of the four axis scores using the configured
    weights. Replaces the LLM's self-reported ``total`` so operators
    have a single, auditable scoring formula across runs and so a
    grader-side bug can't silently bypass the configured policy."""
    w = weights if weights is not None else get_config()
    raw = (
        w.get("w_syllabus", DEFAULT_W_SYLLABUS) * syllabus_alignment
        + w.get("w_intent", DEFAULT_W_INTENT) * intent_fit
        + w.get("w_aeo", DEFAULT_W_AEO) * aeo_readability
        + w.get("w_difficulty", DEFAULT_W_DIFFICULTY) * difficulty
    )
    return max(0, min(100, int(round(raw))))


# ── normalisation ────────────────────────────────────────────────────

_NORMALISE_RE = re.compile(r"\s+")


def _normalise_query(s: str) -> str:
    return _NORMALISE_RE.sub(" ", (s or "").strip().lower())


def _hash_id(prefix: str, *parts: str) -> str:
    raw = "|".join(parts)
    # Deterministic per (run, query) so reruns don't duplicate.
    import hashlib
    h = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{prefix}_{h}"


# ── discovery sources ────────────────────────────────────────────────


async def collect_gsc_near_misses(
    db: Any,
    *,
    now: Optional[datetime] = None,
    lookback_days: int = 7,
    limit: int = MAX_GSC_PER_RUN,
) -> List[Dict[str, Any]]:
    """Pull recent GSC near-miss queries (avg position 11-20).

    Adapter contract: rows in ``gsc_near_miss_queries`` look like
    ``{query, position, impressions, clicks, ctr, recorded_at}``.
    Any pipeline that already has Search Console access (existing
    service-account, manual export, or future GSC OAuth) drops rows in.
    Returning ``[]`` on a missing/empty collection is intentional — the
    agent should still be useful from Suggest + trending alone.
    """
    if db is None:
        return []
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=lookback_days)
    out: List[Dict[str, Any]] = []
    try:
        cursor = db[GSC_NEAR_MISS_COLLECTION].find(
            {
                "recorded_at": {"$gte": since},
                "position": {"$gte": 11, "$lte": 20},
            },
            {"_id": 0},
        ).sort("impressions", -1).limit(int(limit))
        async for row in cursor:
            q = (row.get("query") or "").strip()
            if not q:
                continue
            out.append({
                "source": "gsc_near_miss",
                "query": q,
                "signal": {
                    "position": float(row.get("position") or 0.0),
                    "impressions": int(row.get("impressions") or 0),
                    "clicks": int(row.get("clicks") or 0),
                    "ctr": float(row.get("ctr") or 0.0),
                },
            })
    except Exception as exc:
        logger.info("topic_discovery: gsc collector failed: %s", exc)
    return out


async def collect_suggest_expansions(
    db: Any,
    *,
    suggest_fetcher=None,
    seed_limit: int = MAX_SUGGEST_SEEDS_PER_RUN,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Fan out Google Suggest over EVERY leaf syllabus topic (from the
    ``chapters`` collection) unioned with recent ``seo_topics`` rows.
    No cap by default — set ``TOPIC_DISCOVERY_SUGGEST_SEED_LIMIT`` to
    a positive int to bound LLM cost on very large syllabi. When a
    cap is applied, it runs AFTER the union so chapters and seo_topics
    both contribute.
    """
    if db is None:
        return []
    if suggest_fetcher is None:
        try:
            from google_suggest_client import fetch_india_edu_bundle as suggest_fetcher  # type: ignore
        except Exception as exc:
            logger.info("topic_discovery: suggest import failed: %s", exc)
            return []

    seeds: List[str] = []
    seen_norm: set = set()

    def _accept(kw: str) -> bool:
        kw = (kw or "").strip()
        if not kw:
            return False
        nk = _normalise_query(kw)
        if not nk or nk in seen_norm:
            return False
        seen_norm.add(nk)
        seeds.append(kw)
        return True

    # 1) Every leaf syllabus topic from ``chapters``. We deliberately
    # do NOT cap this read — capping at the source under-discovers
    # parts of the syllabus that happen to sort late. The grader budget
    # cap further down the pipeline is what bounds cost.
    try:
        cursor = db.chapters.find(
            {},
            {"_id": 0, "title": 1, "name": 1, "topic": 1},
        )
        async for row in cursor:
            _accept(row.get("title") or row.get("name") or row.get("topic") or "")
    except Exception as exc:
        logger.info("topic_discovery: chapters seed read failed: %s", exc)

    # 2) Freshest seo_topics keywords — union, not replacement, so
    # admin-added topics that don't yet have a chapter row still seed
    # Suggest expansions on the next nightly pass.
    try:
        cursor = db.seo_topics.find(
            {},
            {"_id": 0, "primary_keyword": 1, "topic": 1, "updated_at": 1},
        ).sort("updated_at", -1).limit(int(seed_limit) * 4)
        async for row in cursor:
            _accept(row.get("primary_keyword") or row.get("topic") or "")
    except Exception as exc:
        logger.info("topic_discovery: seo_topics seed read failed: %s", exc)

    # 3) Bound LLM cost. The cap is intentionally applied AFTER the
    # full union so chapters and seo_topics both contribute. Operators
    # who want broader coverage can raise TOPIC_DISCOVERY_SUGGEST_SEED_LIMIT.
    seed_limit = int(os.environ.get("TOPIC_DISCOVERY_SUGGEST_SEED_LIMIT", seed_limit))
    if seed_limit > 0 and len(seeds) > seed_limit:
        seeds = seeds[:seed_limit]
    if not seeds:
        return []

    out: List[Dict[str, Any]] = []
    seen = set()
    for seed in seeds:
        try:
            res = await suggest_fetcher(seed, db=db, now=now)
        except Exception as exc:
            logger.info("topic_discovery: suggest(%r) failed: %s", seed, exc)
            continue
        for s in (res or {}).get("suggestions", []) or []:
            kw = (s.get("keyword") or "").strip()
            if not kw:
                continue
            nk = _normalise_query(kw)
            if not nk or nk in seen or nk == _normalise_query(seed):
                continue
            seen.add(nk)
            out.append({
                "source": "suggest_expansion",
                "query": kw,
                "signal": {
                    "seed": seed,
                    "rank": int(s.get("rank") or 0),
                    "locales": list(s.get("locales") or []),
                },
            })
    return out


async def collect_bing_suggest(
    db: Any,
    *,
    bing_fetcher=None,
    seed_limit: int = MAX_SUGGEST_SEEDS_PER_RUN,
    api_key: Optional[str] = None,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Bing-side keyword expansion as a second Suggest surface.

    Reuses ``bing_keyword_client.fetch_top_keywords`` (already
    cache-backed). Seeds match ``collect_suggest_expansions`` for
    symmetry: chapters ∪ recent seo_topics, capped after the union.
    Gracefully no-ops when ``BING_WEBMASTER_API_KEY`` is unset.
    """
    if db is None:
        return []
    api_key = api_key if api_key is not None else os.environ.get("BING_WEBMASTER_API_KEY", "")
    api_key = (api_key or "").strip()
    if not api_key:
        return []
    if bing_fetcher is None:
        try:
            from bing_keyword_client import fetch_top_keywords as bing_fetcher  # type: ignore
        except Exception as exc:
            logger.info("topic_discovery: bing import failed: %s", exc)
            return []

    seeds: List[str] = []
    seen_norm: set = set()

    def _accept(kw: str) -> bool:
        kw = (kw or "").strip()
        if not kw:
            return False
        nk = _normalise_query(kw)
        if not nk or nk in seen_norm:
            return False
        seen_norm.add(nk)
        seeds.append(kw)
        return True

    try:
        cursor = db.chapters.find(
            {}, {"_id": 0, "title": 1, "name": 1, "topic": 1},
        )
        async for row in cursor:
            _accept(row.get("title") or row.get("name") or row.get("topic") or "")
    except Exception as exc:
        logger.info("topic_discovery: bing chapters seed read failed: %s", exc)

    try:
        cursor = db.seo_topics.find(
            {}, {"_id": 0, "primary_keyword": 1, "topic": 1, "updated_at": 1},
        ).sort("updated_at", -1).limit(int(seed_limit) * 4)
        async for row in cursor:
            _accept(row.get("primary_keyword") or row.get("topic") or "")
    except Exception as exc:
        logger.info("topic_discovery: bing seo_topics seed read failed: %s", exc)

    seed_limit = int(os.environ.get("TOPIC_DISCOVERY_BING_SEED_LIMIT", seed_limit))
    if seed_limit > 0 and len(seeds) > seed_limit:
        seeds = seeds[:seed_limit]
    if not seeds:
        return []

    out: List[Dict[str, Any]] = []
    seen = set()
    for seed in seeds:
        try:
            res = await bing_fetcher(api_key, seed, db=db, now=now)
        except Exception as exc:
            logger.info("topic_discovery: bing(%r) failed: %s", seed, exc)
            continue
        for s in (res or {}).get("keywords", []) or []:
            kw = (s.get("keyword") if isinstance(s, dict) else s) or ""
            kw = str(kw).strip()
            if not kw:
                continue
            nk = _normalise_query(kw)
            if not nk or nk in seen or nk == _normalise_query(seed):
                continue
            seen.add(nk)
            out.append({
                "source": "bing_suggest",
                "query": kw,
                "signal": {
                    "seed": seed,
                    "volume": (s.get("volume") if isinstance(s, dict) else None),
                },
            })
    return out


async def collect_trending(
    db: Any,
    *,
    now: Optional[datetime] = None,
    lookback_days: int = 2,
    limit: int = MAX_TRENDING_PER_RUN,
) -> List[Dict[str, Any]]:
    """Pull rows from the trending adapter collection.

    Adapter contract: rows in ``trending_topics_raw`` look like
    ``{query, source, score, recorded_at}`` where ``source`` is the
    upstream feed name (``google_trends``, ``rss:hindustan_times`` …).
    """
    if db is None:
        return []
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=lookback_days)
    out: List[Dict[str, Any]] = []
    try:
        cursor = db[TRENDING_RAW_COLLECTION].find(
            {"recorded_at": {"$gte": since}},
            {"_id": 0},
        ).sort([("score", -1), ("recorded_at", -1)]).limit(int(limit))
        async for row in cursor:
            q = (row.get("query") or "").strip()
            if not q:
                continue
            out.append({
                "source": "trending",
                "query": q,
                "signal": {
                    "feed": (row.get("source") or "trending").strip(),
                    "score": float(row.get("score") or 0.0),
                },
            })
    except Exception as exc:
        logger.info("topic_discovery: trending collector failed: %s", exc)
    return out


def _dedupe_candidates(
    rows: List[Dict[str, Any]],
    *,
    cap: int = MAX_CANDIDATES_TO_GRADE,
) -> List[Dict[str, Any]]:
    """Merge by normalised query; collect every contributing source."""
    bag: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        nk = _normalise_query(r.get("query", ""))
        if not nk:
            continue
        entry = bag.get(nk)
        if entry is None:
            entry = {
                "query": r.get("query", "").strip(),
                "normalised": nk,
                "sources": [],
                "signals": {},
            }
            bag[nk] = entry
        src = r.get("source") or "unknown"
        if src not in entry["sources"]:
            entry["sources"].append(src)
        # Last-writer wins per source signal.
        entry["signals"][src] = r.get("signal") or {}
    out = list(bag.values())
    # Prefer multi-source candidates; tie-break by GSC impressions then
    # trending score then suggest rank — proxy for a priori promise.
    def _score_for_sort(c: Dict[str, Any]) -> Tuple[int, float, float, int]:
        sig = c.get("signals", {})
        return (
            len(c.get("sources", [])),
            float(sig.get("gsc_near_miss", {}).get("impressions") or 0.0),
            float(sig.get("trending", {}).get("score") or 0.0),
            int(sig.get("suggest_expansion", {}).get("rank") or 0),
        )
    out.sort(key=_score_for_sort, reverse=True)
    return out[:cap]


# ── LLM grader ───────────────────────────────────────────────────────


_GRADER_SYSTEM = (
    "You are a content strategist for Syrabit.ai, an educational platform "
    "for AHSEC (Higher Secondary), SEBA (Secondary Board) and Degree "
    "students in Assam, India. Score the supplied candidate query for "
    "whether Syrabit should publish a page about it. Reply with a single "
    "valid JSON object — no prose, no markdown fences."
)

_GRADER_USER_TEMPLATE = """\
Candidate query: {query}
Discovery sources: {sources}
Signals: {signals}

Syllabus context (recent leaf topics on Syrabit):
{syllabus}

Inventory context (existing published Syrabit pages near this topic):
{inventory}

{few_shot}
Score on a 0-100 scale per criterion. Strongly prefer queries that match
syllabus intent and Assam/AHSEC/SEBA context. Penalise: brand queries,
gambling/adult, generic news, queries already covered by an inventory page.

Return EXACTLY this JSON object (no prose, no markdown):
{{
  "intent_fit": <int 0-100>,
  "syllabus_alignment": <int 0-100>,
  "difficulty": <int 0-100, where 100 = easy to rank for>,
  "aeo_readability": <int 0-100>,
  "total": <int 0-100, weighted blend>,
  "reason": "<<= 280 chars, plain prose>"
}}
"""


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _clip_int(v: Any, lo: int = 0, hi: int = 100) -> int:
    try:
        n = int(round(float(v)))
    except Exception:
        return lo
    return max(lo, min(hi, n))


def parse_grader_response(text: str) -> Optional[Dict[str, Any]]:
    """Validate + clamp the grader output. Returns None on hard parse
    failure so caller can mark the row ``error``."""
    raw = _extract_json(text)
    if not isinstance(raw, dict):
        return None
    intent = _clip_int(raw.get("intent_fit"))
    syl = _clip_int(raw.get("syllabus_alignment"))
    diff = _clip_int(raw.get("difficulty"))
    aeo = _clip_int(raw.get("aeo_readability"))
    # Always compute the total deterministically from the four axes
    # using the configured weights — we don't trust the LLM's
    # self-reported ``total`` because (a) operators want a single
    # auditable scoring formula across runs and (b) a grader-side bug
    # could otherwise silently bypass the configured policy. The LLM's
    # ``total`` is ignored on purpose.
    total = compute_weighted_total(
        syllabus_alignment=syl,
        intent_fit=intent,
        aeo_readability=aeo,
        difficulty=diff,
    )
    reason = raw.get("reason")
    if not isinstance(reason, str):
        reason = ""
    return {
        "intent_fit": intent,
        "syllabus_alignment": syl,
        "difficulty": diff,
        "aeo_readability": aeo,
        "total": total,
        "reason": reason.strip()[:280],
    }


def _build_grader_prompt(
    candidate: Dict[str, Any],
    *,
    syllabus_lines: List[str],
    inventory_lines: List[str],
    few_shot_examples: List[Dict[str, Any]],
) -> str:
    sources = ", ".join(candidate.get("sources") or []) or "unknown"
    signals = json.dumps(candidate.get("signals") or {}, ensure_ascii=False)[:600]
    syllabus = "\n".join(f"- {s}" for s in syllabus_lines[:25]) or "- (no recent topics)"
    inventory = "\n".join(f"- {s}" for s in inventory_lines[:15]) or "- (no nearby pages)"
    few_shot = ""
    if few_shot_examples:
        lines = ["Recent admin overrides — calibrate against these:"]
        for ex in few_shot_examples[:OVERRIDE_FEW_SHOT_LIMIT]:
            decision = ex.get("admin_decision", "?")
            q = (ex.get("query") or "")[:120]
            note = (ex.get("admin_reason") or "")[:140]
            lines.append(f"- query={q!r} → admin={decision} (note: {note})")
        few_shot = "\n".join(lines) + "\n\n"
    return _GRADER_USER_TEMPLATE.format(
        query=candidate.get("query", ""),
        sources=sources,
        signals=signals,
        syllabus=syllabus,
        inventory=inventory,
        few_shot=few_shot,
    )


async def grade_candidate(
    candidate: Dict[str, Any],
    *,
    syllabus_lines: List[str],
    inventory_lines: List[str],
    few_shot_examples: List[Dict[str, Any]],
    llm_caller=None,
) -> Optional[Dict[str, Any]]:
    """Run the LLM grader once. Returns the parsed score dict or None."""
    if llm_caller is None:
        try:
            from llm import call_llm_api_content as llm_caller  # type: ignore
        except Exception as exc:
            logger.info("topic_discovery: llm import failed: %s", exc)
            return None
    prompt = _build_grader_prompt(
        candidate,
        syllabus_lines=syllabus_lines,
        inventory_lines=inventory_lines,
        few_shot_examples=few_shot_examples,
    )
    messages = [
        {"role": "system", "content": _GRADER_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    try:
        text = await llm_caller(
            messages,
            model=GRADER_MODEL,
            max_tokens=GRADER_MAX_TOKENS,
        )
    except Exception as exc:
        logger.info("topic_discovery: grader call failed for %r: %s",
                    candidate.get("query"), exc)
        return None
    return parse_grader_response(text or "")


# ── decision engine + budget cap ─────────────────────────────────────


def decide(
    score: Optional[Dict[str, Any]],
    *,
    auto_publish_threshold: int,
    draft_threshold: int,
    auto_remaining: int,
    draft_remaining: int,
) -> Dict[str, Any]:
    """Pure function — return ``{decision, decision_reason, total}``.

    ``auto_remaining`` / ``draft_remaining`` clamp the daily budget; once
    a tier's cap is hit, candidates that would have landed there fall
    through to the next tier (auto → draft → reject).
    """
    if score is None:
        return {"decision": "error", "decision_reason": "grader returned no score", "total": 0}
    total = int(score.get("total") or 0)
    if total >= auto_publish_threshold and auto_remaining > 0:
        return {"decision": "auto_published", "decision_reason": "score above auto threshold", "total": total}
    if total >= auto_publish_threshold and draft_remaining > 0:
        # Auto cap hit; degrade to draft so the candidate isn't lost.
        return {"decision": "drafted", "decision_reason": "auto cap reached; demoted to draft", "total": total}
    if total >= draft_threshold and draft_remaining > 0:
        return {"decision": "drafted", "decision_reason": "score in draft band", "total": total}
    if total >= draft_threshold:
        return {"decision": "rejected", "decision_reason": "draft cap reached; below auto threshold", "total": total}
    return {"decision": "rejected", "decision_reason": "score below draft threshold", "total": total}


# ── context loaders ──────────────────────────────────────────────────


async def _load_syllabus_summary(db: Any, *, limit: int = 25) -> List[str]:
    if db is None:
        return []
    try:
        cursor = db.seo_topics.find(
            {}, {"_id": 0, "topic": 1, "primary_keyword": 1},
        ).sort("updated_at", -1).limit(int(limit))
        out: List[str] = []
        async for row in cursor:
            kw = (row.get("primary_keyword") or row.get("topic") or "").strip()
            if kw and kw not in out:
                out.append(kw)
        return out
    except Exception:
        return []


async def _load_inventory_summary(db: Any, *, limit: int = 15) -> List[str]:
    if db is None:
        return []
    try:
        cursor = db.seo_pages.find(
            {"status": "published"},
            {"_id": 0, "topic": 1, "title": 1, "primary_keyword": 1},
        ).sort("updated_at", -1).limit(int(limit))
        out: List[str] = []
        async for row in cursor:
            label = (
                row.get("title") or row.get("primary_keyword") or row.get("topic") or ""
            ).strip()
            if label and label not in out:
                out.append(label)
        return out
    except Exception:
        return []


async def _load_recent_overrides(db: Any, *, lookback_days: int = OVERRIDE_FEW_SHOT_LOOKBACK_DAYS,
                                 limit: int = OVERRIDE_FEW_SHOT_LIMIT) -> List[Dict[str, Any]]:
    if db is None:
        return []
    try:
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        cursor = db[CANDIDATES_COLLECTION].find(
            {"admin_override_at": {"$gte": since}},
            {"_id": 0, "query": 1, "admin_decision": 1, "admin_reason": 1,
             "decision": 1, "score": 1},
        ).sort("admin_override_at", -1).limit(int(limit))
        return [row async for row in cursor]
    except Exception:
        return []


# ── auto-enqueue ─────────────────────────────────────────────────────


def _kw_token_set(text: str) -> set:
    if not text:
        return set()
    return {t for t in _normalise_query(text).split() if len(t) > 2}


async def _match_to_chapter(db: Any, query: str) -> Optional[str]:
    """Find the best-matching chapter id for a discovered query so the
    enqueued ``seo_topics`` row uses the same
    ``{linked_chapter_id, topic}`` contract the existing Stage 1→3
    pipeline writes (see ``routes/admin_pipeline.py:374`` / `:1882`).
    Returns ``None`` when no chapter shares any meaningful token.
    """
    if db is None or not query:
        return None
    q_tokens = _kw_token_set(query)
    if not q_tokens:
        return None
    best_id: Optional[str] = None
    best_score = 0.0
    try:
        cursor = db.chapters.find(
            {}, {"_id": 0, "id": 1, "title": 1, "name": 1, "topic": 1},
        )
        async for row in cursor:
            cid = row.get("id")
            if not cid:
                continue
            title = row.get("title") or row.get("name") or row.get("topic") or ""
            t_tokens = _kw_token_set(title)
            if not t_tokens:
                continue
            inter = len(q_tokens & t_tokens)
            if inter == 0:
                continue
            # Jaccard-ish — biased toward the query side so that short
            # chapter titles ("Motion") still match long queries
            # ("equations of motion neet").
            score = inter / max(len(q_tokens), 1)
            if score > best_score:
                best_score = score
                best_id = cid
    except Exception as exc:
        logger.info("topic_discovery: chapter match read failed: %s", exc)
        return None
    # Require at least one shared meaningful token; arbitrary single-
    # word matches (e.g. "best") would otherwise dominate.
    return best_id if best_score > 0.0 else None


async def _enqueue_for_pipeline(db: Any, *, candidate: Dict[str, Any], decision: str,
                                run_id: str) -> Optional[str]:
    """Enqueue a candidate into the existing Stage 1→3 pipeline by
    upserting a ``seo_topics`` row with the same
    ``{linked_chapter_id, topic}`` shape ``routes/admin_pipeline.py``
    writes. Discovery-specific fields ride alongside so admins can
    audit the source. Returns the ``topic`` string on success or
    ``None`` on failure."""
    if db is None:
        return None
    try:
        from seo_writes import upsert_seo_topic
    except Exception as exc:
        logger.info("topic_discovery: upsert_seo_topic import failed: %s", exc)
        return None

    query = (candidate.get("query") or "").strip()
    if not query:
        return None

    chapter_id = await _match_to_chapter(db, query)
    topic_status = "auto_publish_pending" if decision == "auto_published" else "draft_pending"
    topic_doc = {
        "topic": query,
        "primary_keyword": query,
        "source": "topic_discovery",
        "discovery_status": topic_status,
        "discovery_run_id": run_id,
        "discovery_sources": list(candidate.get("sources") or []),
    }
    if chapter_id:
        topic_doc["linked_chapter_id"] = chapter_id
        filt = {"linked_chapter_id": chapter_id, "topic": query}
    else:
        # Fall back to source-keyed row so we still record the keyword
        # for keyword-weaving consumers; the engine just won't auto-
        # generate a page for it without a chapter.
        filt = {"topic": query, "source": "topic_discovery"}
    try:
        await upsert_seo_topic(db, filt, topic_doc)
        return query
    except Exception as exc:
        logger.info("topic_discovery: enqueue failed for %r: %s", query, exc)
        return None


async def _dequeue_from_pipeline(db: Any, *, query: str, run_id: str,
                                 admin_id: str, reason: str) -> bool:
    """Cancel a previously-enqueued discovery row when an admin
    rejects an already-queued candidate. Sets the ``seo_topics`` row
    to ``status="blocked"`` so the engine's ``_eligible_topic_filter``
    (which excludes ``status in ("blocked", "duplicate", "irrelevant")``)
    skips it on the next pass."""
    if db is None or not query:
        return False
    try:
        # Match either the chapter-linked row or the legacy fallback.
        res = await db.seo_topics.update_many(
            {
                "topic": query,
                "source": "topic_discovery",
            },
            {"$set": {
                "discovery_status": "cancelled",
                "status": "blocked",
                "cancelled_by": admin_id,
                "cancelled_reason": (reason or "")[:280],
                "cancelled_run_id": run_id,
            }},
        )
        return getattr(res, "modified_count", 0) > 0
    except Exception as exc:
        logger.info("topic_discovery: dequeue failed for %r: %s", query, exc)
        return False


# ── orchestrator ─────────────────────────────────────────────────────


async def _count_today_decisions(db: Any, *, now: datetime) -> Dict[str, int]:
    """Sum already-spent budget for the current UTC day across all runs.

    Multiple ``/run-now`` invocations and the nightly loop must share the
    daily cap; otherwise each individual run starts fresh and the
    operator-visible "10 auto / 50 draft" guarantee is silently broken.
    """
    out = {"auto_published": 0, "drafted": 0}
    if db is None:
        return out
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        for decision in ("auto_published", "drafted"):
            n = await db[CANDIDATES_COLLECTION].count_documents({
                "decision": decision,
                "created_at": {"$gte": day_start},
            })
            out[decision] = int(n or 0)
    except Exception as exc:
        logger.debug("topic_discovery: day-count read failed: %s", exc)
    return out


async def run_topic_discovery_once(
    db: Any,
    *,
    now: Optional[datetime] = None,
    suggest_fetcher=None,
    llm_caller=None,
    config: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """One full pass: collect → grade → decide → enqueue → record.

    Returns a summary dict (also persisted to ``topic_discovery_runs``).
    Safe to call from a manual admin endpoint as well as from the
    nightly loop — same code path, same persistence. Day-scoped caps
    are enforced by subtracting today's already-spent budget so multiple
    runs in a single UTC day share the operator's promised limits.
    """
    now = now or datetime.now(timezone.utc)
    cfg = config or get_config()
    run_id = _hash_id("run", now.isoformat())
    started_at = now

    # 0) Live ingest into the source-of-truth Mongo collections so the
    # collectors below see fresh data even if no out-of-band cron is
    # populating them. Each ingest gracefully no-ops when its
    # configuration is missing.
    try:
        from gsc_search_console_client import ingest_near_miss_into_mongo as _gsc_ingest
        await _gsc_ingest(db, now=now)
    except Exception as exc:
        logger.info("topic_discovery: gsc ingest failed: %s", exc)
    try:
        from trending_rss_client import ingest_trending_into_mongo as _rss_ingest
        await _rss_ingest(db, now=now)
    except Exception as exc:
        logger.info("topic_discovery: rss ingest failed: %s", exc)

    # 1) collect from all three surfaces; dedupe collapses overlap.
    gsc_rows = await collect_gsc_near_misses(db, now=now)
    suggest_rows = await collect_suggest_expansions(
        db, suggest_fetcher=suggest_fetcher, now=now,
    )
    bing_rows = await collect_bing_suggest(db, now=now)
    trending_rows = await collect_trending(db, now=now)
    raw_rows = gsc_rows + suggest_rows + bing_rows + trending_rows
    candidates = _dedupe_candidates(raw_rows)

    # 2) context
    syllabus_lines = await _load_syllabus_summary(db)
    inventory_lines = await _load_inventory_summary(db)
    overrides = await _load_recent_overrides(db)

    # 3) grade + decide with rolling budget. Day-scoped accounting:
    # subtract anything already published/drafted today across prior
    # runs so the cap is real per-day, not per-invocation.
    auto_cap = int(cfg.get("auto_publish_cap", DEFAULT_AUTO_PUBLISH_CAP))
    draft_cap = int(cfg.get("draft_cap", DEFAULT_DRAFT_CAP))
    spent = await _count_today_decisions(db, now=now)
    auto_remaining = max(0, auto_cap - spent["auto_published"])
    draft_remaining = max(0, draft_cap - spent["drafted"])
    auto_thr = int(cfg.get("auto_publish_threshold", DEFAULT_AUTO_PUBLISH_THRESHOLD))
    draft_thr = int(cfg.get("draft_threshold", DEFAULT_DRAFT_THRESHOLD))

    counts = {"auto_published": 0, "drafted": 0, "rejected": 0, "error": 0}
    persisted: List[Dict[str, Any]] = []

    for candidate in candidates:
        score = await grade_candidate(
            candidate,
            syllabus_lines=syllabus_lines,
            inventory_lines=inventory_lines,
            few_shot_examples=overrides,
            llm_caller=llm_caller,
        )
        outcome = decide(
            score,
            auto_publish_threshold=auto_thr,
            draft_threshold=draft_thr,
            auto_remaining=auto_remaining,
            draft_remaining=draft_remaining,
        )
        decision = outcome["decision"]
        if decision == "auto_published":
            auto_remaining = max(0, auto_remaining - 1)
        elif decision == "drafted":
            draft_remaining = max(0, draft_remaining - 1)

        enqueued_topic: Optional[str] = None
        enqueue_error: Optional[str] = None
        # Safety gate: ONLY auto_published candidates are auto-enqueued
        # into the Stage 1→3 pipeline. Drafted candidates remain
        # discovery records until an admin promotes them via override
        # (apply_override calls _enqueue_for_pipeline at that point).
        if decision == "auto_published":
            enqueued_topic = await _enqueue_for_pipeline(
                db, candidate=candidate, decision=decision, run_id=run_id,
            )
            if not enqueued_topic:
                # Enqueue failed — preserve the grader's verdict but
                # surface a retry signal for the admin dashboard.
                enqueue_error = "enqueue failed — pipeline upsert rejected"

        cand_id = _hash_id("cand", run_id, candidate.get("normalised") or candidate.get("query", ""))
        row = {
            "id": cand_id,
            "run_id": run_id,
            "query": candidate.get("query", ""),
            "normalised": candidate.get("normalised", ""),
            "sources": candidate.get("sources", []),
            "signals": candidate.get("signals", {}),
            "score": score,
            "decision": decision,
            "decision_reason": outcome.get("decision_reason", ""),
            "enqueued_topic": enqueued_topic,
            "enqueue_error": enqueue_error,
            "created_at": now,
        }
        persisted.append(row)
        counts[decision] = counts.get(decision, 0) + 1

        if db is not None:
            try:
                await db[CANDIDATES_COLLECTION].update_one(
                    {"id": cand_id},
                    {"$set": row},
                    upsert=True,
                )
            except Exception as exc:
                logger.debug("topic_discovery: candidate persist failed: %s", exc)

    finished_at = datetime.now(timezone.utc)
    summary = {
        "id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": round((finished_at - started_at).total_seconds(), 2),
        "config_snapshot": {
            "auto_publish_threshold": auto_thr,
            "draft_threshold": draft_thr,
            "auto_publish_cap": int(cfg.get("auto_publish_cap", DEFAULT_AUTO_PUBLISH_CAP)),
            "draft_cap": int(cfg.get("draft_cap", DEFAULT_DRAFT_CAP)),
        },
        "totals": {
            "raw": len(raw_rows),
            "deduped": len(candidates),
            **counts,
        },
        "remaining_after_run": {
            "auto_publish": auto_remaining,
            "draft": draft_remaining,
        },
    }
    if db is not None:
        try:
            await db[RUNS_COLLECTION].update_one(
                {"id": run_id},
                {"$set": summary},
                upsert=True,
            )
        except Exception as exc:
            logger.debug("topic_discovery: run persist failed: %s", exc)

    logger.info(
        "topic_discovery: run %s — raw=%d dedup=%d auto=%d draft=%d reject=%d",
        run_id, len(raw_rows), len(candidates),
        counts.get("auto_published", 0), counts.get("drafted", 0),
        counts.get("rejected", 0),
    )
    return summary


# ── nightly loop ─────────────────────────────────────────────────────


_NIGHTLY_LOOP_SLEEP_S = int(os.environ.get("TOPIC_DISCOVERY_LOOP_SLEEP_S", "1800"))


async def _try_claim_daily_lock(
    db: Any,
    *,
    lock_id: str,
    owner_token: str,
    now: datetime,
) -> bool:
    """Atomically claim today's daily lock or return False.

    Uses Mongo's intrinsic ``_id`` (unique on every collection) so a
    racing second insert hits ``DuplicateKeyError`` and is rejected
    without creating duplicate lock docs. The ``ran_at`` ownership
    re-read covers crashed-mid-run recovery from older code.
    """
    if db is None:
        return False
    try:
        # First try to insert. If a doc with this _id already exists,
        # Mongo raises DuplicateKeyError — exactly what we want.
        try:
            await db[RUNS_COLLECTION].insert_one({
                "_id": lock_id,
                "kind": "daily_lock",
                "claim_token": owner_token,
                "claimed_at": now,
            })
            return True
        except Exception as exc:
            # DuplicateKeyError or any other write error — fall
            # through to the takeover path.
            err_name = type(exc).__name__
            if "DuplicateKey" not in err_name:
                logger.debug("topic_discovery: lock insert failed (%s): %s",
                             err_name, exc)

        # A doc already exists. Try to take it over only if it has
        # never been claimed (no claim_token). If a previous run
        # already claimed and ran today, ``ran_at`` will be set and
        # we should not run again.
        existing = await db[RUNS_COLLECTION].find_one({"_id": lock_id})
        if existing and (existing.get("ran_at") or existing.get("claim_token")):
            return False
        # Stale doc with no claim_token (shouldn't happen with the
        # insert-first path above, but covers crashed-mid-claim
        # recoveries from older code). Atomic CAS on missing token.
        res = await db[RUNS_COLLECTION].update_one(
            {
                "_id": lock_id,
                "$or": [
                    {"claim_token": {"$exists": False}},
                    {"claim_token": None},
                ],
                "ran_at": {"$exists": False},
            },
            {"$set": {
                "claim_token": owner_token, "claimed_at": now,
                "kind": "daily_lock",
            }},
        )
        return getattr(res, "modified_count", 0) == 1
    except Exception as exc:
        logger.debug("topic_discovery: lock claim error: %s", exc)
        return False


_NIGHTLY_LOOP_WARMUP_S = int(os.environ.get("TOPIC_DISCOVERY_LOOP_WARMUP_S", "300"))


async def _topic_discovery_loop():
    """Sleeps until the configured UTC hour, then runs once per UTC day.

    The loop is leader-gated by the caller. A per-day Mongo lock in
    ``topic_discovery_runs`` (keyed by ``yyyy-mm-dd``) is the
    belt-and-braces guard so even if leader election misfires we cannot
    double-run.
    """
    await asyncio.sleep(_NIGHTLY_LOOP_WARMUP_S)
    while True:
        try:
            cfg = get_config()
            if cfg.get("disabled"):
                await asyncio.sleep(_NIGHTLY_LOOP_SLEEP_S)
                continue
            now = datetime.now(timezone.utc)
            target_hour = int(cfg.get("run_hour_utc", DEFAULT_RUN_HOUR_UTC))
            if now.hour != target_hour:
                await asyncio.sleep(_NIGHTLY_LOOP_SLEEP_S)
                continue
            try:
                from deps import db as _db, is_mongo_available as _ma  # type: ignore
            except Exception:
                await asyncio.sleep(_NIGHTLY_LOOP_SLEEP_S)
                continue
            if not await _ma():
                await asyncio.sleep(_NIGHTLY_LOOP_SLEEP_S)
                continue

            day_key = now.strftime("%Y-%m-%d")
            lock_id = f"daily_lock_{day_key}"
            owner_token = uuid.uuid4().hex
            claimed = await _try_claim_daily_lock(
                _db, lock_id=lock_id, owner_token=owner_token, now=now,
            )
            if not claimed:
                await asyncio.sleep(_NIGHTLY_LOOP_SLEEP_S)
                continue

            try:
                await run_topic_discovery_once(_db, now=now)
            except Exception as exc:
                logger.warning("topic_discovery: nightly run failed: %s", exc)
            finally:
                try:
                    await _db[RUNS_COLLECTION].update_one(
                        {"_id": lock_id, "claim_token": owner_token},
                        {"$set": {"ran_at": datetime.now(timezone.utc)}},
                    )
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("topic_discovery: loop iteration error: %s", exc)
        await asyncio.sleep(_NIGHTLY_LOOP_SLEEP_S)


# ── admin override helpers ───────────────────────────────────────────


VALID_OVERRIDE_DECISIONS = {"auto_published", "drafted", "rejected"}


async def apply_override(
    db: Any,
    *,
    candidate_id: str,
    new_decision: str,
    admin_reason: str,
    admin_id: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Persist an admin override and (re-)enqueue if the new decision
    requires it. Returns the updated candidate document.
    """
    if new_decision not in VALID_OVERRIDE_DECISIONS:
        raise ValueError(f"invalid decision: {new_decision!r}")
    if db is None:
        raise RuntimeError("database unavailable")
    now = now or datetime.now(timezone.utc)

    cand = await db[CANDIDATES_COLLECTION].find_one(
        {"id": candidate_id}, {"_id": 0},
    )
    if not cand:
        raise LookupError(f"candidate not found: {candidate_id!r}")

    enqueued_topic = cand.get("enqueued_topic")
    dequeued = False
    # Auto_published always (re)enqueues — covers drafted→auto_published
    # promotion AND rejected→auto_published re-activation (the upsert
    # flips status away from "blocked" set by a prior _dequeue).
    if new_decision == "auto_published":
        enqueued_topic = await _enqueue_for_pipeline(
            db,
            candidate={
                "query": cand.get("query"),
                "sources": cand.get("sources") or [],
            },
            decision=new_decision,
            run_id=cand.get("run_id", "manual_override"),
        ) or enqueued_topic
    elif new_decision == "rejected" and enqueued_topic:
        # Reject must cancel any prior queue, not just relabel.
        dequeued = await _dequeue_from_pipeline(
            db,
            query=enqueued_topic,
            run_id=cand.get("run_id", "manual_override"),
            admin_id=admin_id,
            reason=admin_reason,
        )

    update = {
        "decision": new_decision,
        "decision_reason": f"admin override: {admin_reason}".strip()[:280],
        "admin_decision": new_decision,
        "admin_reason": (admin_reason or "")[:280],
        "admin_id": admin_id,
        "admin_override_at": now,
        "enqueued_topic": enqueued_topic,
        "pipeline_dequeued": dequeued,
    }
    await db[CANDIDATES_COLLECTION].update_one(
        {"id": candidate_id},
        {"$set": update},
    )
    cand.update(update)
    return cand
