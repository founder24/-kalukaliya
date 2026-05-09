"""Task #37 — Smart per-turn chat router.

A single decision point that the chat endpoints (``routes/ai_chat.py``)
consult exactly once per turn to choose between three branches:

* ``direct`` — casual / small-talk; answer straight from the LLM with no
  retrieval and no web fetch.
* ``rag``    — study question with a strong topic-embedding match;
  query Pinecone in the language-correct namespace using the
  language-correct embed provider.
* ``web``    — study question with no strong match; fall back to the
  existing DDG → Exa path.

The language selector is the **single source of truth** for:

* the LLM provider chain (``english_rag_chat`` vs. ``assamese_rag_chat``),
* the Pinecone namespace (``en`` vs. ``as``), and
* the embed provider (English → Workers-AI custom worker;
  Assamese → Cohere ``embed-multilingual-v3`` via AWS Bedrock per Task #27).

No silent fallbacks — every decision records a ``reason`` in
``RouteDecision.reason`` for the trace and the dev-mode QA badge.
Founder locks (Pinecone dim 1024, Sarvam = sole Assamese head, $100/mo
cap, voice paywall, Bedrock Indic sub-cap $5/mo) are unaffected.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger("chat_router")

# Strong-topic threshold. Defaults to the legacy ``rag_router`` gate so
# behaviour is preserved on existing traffic; operators can tune via env
# without a redeploy.
_DEFAULT_TOPIC_THRESHOLD = 0.55


def _topic_threshold() -> float:
    raw = (os.environ.get("CHAT_ROUTER_TOPIC_THRESHOLD", "") or "").strip()
    if not raw:
        return _DEFAULT_TOPIC_THRESHOLD
    try:
        v = float(raw)
    except ValueError:
        logger.warning(
            "chat_router: ignoring non-numeric CHAT_ROUTER_TOPIC_THRESHOLD=%r",
            raw,
        )
        return _DEFAULT_TOPIC_THRESHOLD
    # Hard clamp into (0, 1) — anything outside means a misconfiguration
    # that would either disable the RAG branch (>1) or always trigger it
    # (<=0). Either is a silent-fallback risk; clamp + warn.
    if not (0.0 < v < 1.0):
        logger.warning(
            "chat_router: CHAT_ROUTER_TOPIC_THRESHOLD=%.3f outside (0,1) — "
            "clamping to default %.3f", v, _DEFAULT_TOPIC_THRESHOLD,
        )
        return _DEFAULT_TOPIC_THRESHOLD
    return v


# Intents that ALWAYS short-circuit to ``direct``. ``classify_intent``
# returns these for greetings / thanks / meta questions / "tell me a joke".
_DIRECT_INTENTS = frozenset({"casual"})

# Intents that warrant the topic probe → rag/web branching. Everything
# else (general / chapter_meta / etc.) also gets the probe so bare
# study questions like "what is photosynthesis" still route to RAG when
# their embedding lands inside a chapter centroid.
_SKIP_PROBE_INTENTS = frozenset({"casual", "syllabus"})

# Canonical (provider_chain, namespace, embed_provider) tuple per
# language. ``provider_chain`` mirrors the strict 3-position walks in
# ``llm.select_provider`` for ``english_rag_chat`` / ``assamese_rag_chat``;
# ``embed_provider`` mirrors ``llm._embed_feature_for`` so the QA badge
# can show the actual route the dispatcher will take. The RAG response
# formatter is unaffected — Sarvam stays the sole Assamese head.
_LANG_PROFILES: dict[str, dict[str, object]] = {
    "en": {
        "feature": "english_rag_chat",
        "provider_chain": ("vertex", "vertex_flash_lite", "workers_ai_llama32_3b"),
        "pinecone_namespace": "en",
        "embed_provider": "workers_ai_custom",
    },
    "as": {
        "feature": "assamese_rag_chat",
        "provider_chain": ("sarvam", "vertex_assamese", "retrieval_only"),
        "pinecone_namespace": "as",
        # Task #27 — Indic queries route to Cohere via Bedrock unless the
        # operator flips ``EMBED_INDIC_PROVIDER`` / sub-cap kill-switch.
        "embed_provider": "cohere_multilingual_v3_bedrock",
    },
}


def _normalize_lang(lang: Optional[str]) -> str:
    """Return a canonical, supported language code.

    Anything that isn't a recognised Indic code defaults to English. The
    chat surface only exposes ``en`` / ``as`` today; future Indic
    additions (``bn``, ``hi``…) get an English profile until they have
    their own provider chain.
    """
    code = (lang or "").lower().strip()
    if code in _LANG_PROFILES:
        return code
    return "en"


@dataclass
class RouteDecision:
    """Single-turn router output. Serialisable so it can be:

    * recorded in GCP Cloud Trace span attributes,
    * embedded in the non-stream JSON response under ``route_trace``,
    * surfaced in the dev-mode QA badge on the chat bubble.
    """

    decision: str                           # "direct" | "rag" | "web"
    reason: str                             # human-readable trace reason
    lang: str                               # normalized lang ("en" | "as")
    intent: str                             # classify_intent output
    topic_score: Optional[float] = None     # similarity (0-1) when probed
    topic_threshold: float = _DEFAULT_TOPIC_THRESHOLD
    provider_chain: tuple[str, ...] = ()    # for QA badge / trace
    pinecone_namespace: str = ""            # "" for direct branch
    embed_provider: str = ""                # "" for direct branch
    feature: str = ""                       # PROVIDER_PRIORITY key
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["provider_chain"] = list(self.provider_chain)
        return d


def lang_profile(lang: Optional[str]) -> dict:
    """Return the ``(feature, provider_chain, pinecone_namespace,
    embed_provider)`` profile for *lang*.

    Exposed publicly so callers (the ``ai_chat`` dispatcher, the
    Pinecone retriever, and the embed dispatcher) can derive their
    config from the same source of truth as the router.
    """
    code = _normalize_lang(lang)
    p = _LANG_PROFILES[code]
    return {
        "lang": code,
        "feature": p["feature"],
        "provider_chain": tuple(p["provider_chain"]),
        "pinecone_namespace": p["pinecone_namespace"],
        "embed_provider": p["embed_provider"],
    }


def route(
    query: str,
    *,
    lang: Optional[str] = "en",
    intent: Optional[str] = None,
    topic_score: Optional[float] = None,
    threshold: Optional[float] = None,
) -> RouteDecision:
    """Decide which branch to take for the current chat turn.

    Parameters
    ----------
    query        : raw user message (used only for intent classification
                   when *intent* is not supplied).
    lang         : the response-language selector value (single source
                   of truth). Anything outside ``en``/``as`` collapses
                   to English.
    intent       : pre-computed ``prompts.classify_intent`` result. When
                   ``None`` we classify here so callers don't double-call.
    topic_score  : best chapter-centroid similarity (0-1) from the topic
                   probe. ``None`` means the probe wasn't run yet (the
                   caller should run it AFTER seeing decision != direct).
    threshold    : override for the strong-match threshold (defaults to
                   ``CHAT_ROUTER_TOPIC_THRESHOLD`` env / 0.55).

    Returns
    -------
    A :class:`RouteDecision`. The caller MUST honour ``decision`` exactly
    — no silent fallbacks per V4 §12.
    """
    profile = lang_profile(lang)
    th = threshold if threshold is not None else _topic_threshold()

    # Resolve intent lazily so the casual short-circuit is cheap.
    if intent is None:
        try:
            from prompts import classify_intent as _classify
            intent_val, _ = _classify(query or "")
        except Exception as exc:
            logger.warning("chat_router: classify_intent failed (%s) — "
                           "treating as 'general'", exc)
            intent_val = "general"
    else:
        intent_val = intent

    # 1. Casual short-circuit — no embed, no Pinecone, no web.
    if intent_val in _DIRECT_INTENTS:
        return RouteDecision(
            decision="direct",
            reason=f"intent={intent_val} → casual short-circuit",
            lang=profile["lang"],
            intent=intent_val,
            topic_score=None,
            topic_threshold=th,
            provider_chain=profile["provider_chain"],
            # Direct branch must NOT touch Pinecone or the embed worker;
            # the empty namespace/provider strings make that auditable.
            pinecone_namespace="",
            embed_provider="",
            feature=profile["feature"],
        )

    # 2. Some non-RAG intents (pure syllabus listings) skip the probe
    #    and answer directly from the LLM + DB metadata.
    if intent_val in _SKIP_PROBE_INTENTS:
        return RouteDecision(
            decision="direct",
            reason=f"intent={intent_val} → metadata-only (probe skipped)",
            lang=profile["lang"],
            intent=intent_val,
            topic_score=None,
            topic_threshold=th,
            provider_chain=profile["provider_chain"],
            pinecone_namespace="",
            embed_provider="",
            feature=profile["feature"],
        )

    # 3. Topic probe gate. When the caller hasn't run the probe yet we
    #    return a sentinel ``rag`` decision with score=None so the
    #    caller knows to run the probe; once the probe is back the
    #    caller calls route() again with topic_score=<f>.
    if topic_score is None:
        return RouteDecision(
            decision="rag",
            reason="topic probe pending — caller must embed query and re-route",
            lang=profile["lang"],
            intent=intent_val,
            topic_score=None,
            topic_threshold=th,
            provider_chain=profile["provider_chain"],
            pinecone_namespace=profile["pinecone_namespace"],
            embed_provider=profile["embed_provider"],
            feature=profile["feature"],
            extra={"probe_pending": True},
        )

    if topic_score >= th:
        return RouteDecision(
            decision="rag",
            reason=(
                f"topic_score={topic_score:.3f} ≥ threshold={th:.3f} "
                f"→ Pinecone namespace={profile['pinecone_namespace']!r}"
            ),
            lang=profile["lang"],
            intent=intent_val,
            topic_score=topic_score,
            topic_threshold=th,
            provider_chain=profile["provider_chain"],
            pinecone_namespace=profile["pinecone_namespace"],
            embed_provider=profile["embed_provider"],
            feature=profile["feature"],
        )

    return RouteDecision(
        decision="web",
        reason=(
            f"topic_score={topic_score:.3f} < threshold={th:.3f} "
            f"→ web search fallback"
        ),
        lang=profile["lang"],
        intent=intent_val,
        topic_score=topic_score,
        topic_threshold=th,
        provider_chain=profile["provider_chain"],
        # Web branch still uses the embed provider for the deterministic
        # cache key; namespace stays empty since Pinecone isn't queried.
        pinecone_namespace="",
        embed_provider=profile["embed_provider"],
        feature=profile["feature"],
    )


def score_from_classify_result(result: Optional[dict]) -> Optional[float]:
    """Convert a ``wai_chapter_index.classify(...)`` return value into the
    same [0, 1] / 0.0 / None tri-state the router gate consumes.

    Exposed as a separate entry point so call-sites that already run
    :func:`wai_chapter_index.classify` in the background (e.g. the
    streaming chat handler's ``_wai_chapter_task``) can reuse that
    result instead of paying for a second classifier round-trip — this
    is the "embed once / probe once" guarantee the 3s p95 budget
    relies on.

    * ``None``  → caller passed nothing (probe-pending sentinel; router
      defaults to ``rag`` so we never silently downgrade).
    * ``0.0``   → classifier explicitly returned no match within its
      own ``_SIM_LOW`` gate (real "weak match → web" signal).
    * ``float`` → clamped centroid similarity in [0, 1].
    """
    if result is None:
        return 0.0  # classify() returns None on a hard miss
    if not isinstance(result, dict):
        return None
    sim = result.get("similarity")
    try:
        return max(0.0, min(1.0, float(sim)))
    except (TypeError, ValueError):
        return None


async def _probe_assamese_via_bedrock_cohere(
    query: str, subject_id: str, *, timeout_s: float,
) -> Optional[float]:
    """Assamese topic probe — Task #27 Bedrock-Cohere embed path.

    Embeds the query via ``llm.call_embed_with_dispatch(lang="as")``
    which routes through Cohere ``embed-multilingual-v3`` on AWS
    Bedrock when ``EMBED_INDIC_PROVIDER=cohere_multilingual_v3_bedrock``
    (the production default), and through the Workers-AI custom worker
    only when the operator has explicitly downgraded the Indic route
    via ``RAG_EMBEDDING_PROVIDER_FORCE`` / ``EMBED_INDIC_PROVIDER`` —
    matching the chat-time embed pool the router advertises in
    ``RouteDecision.embed_provider``.

    Returns the top-1 cosine score from a subject-filtered
    ``namespace="as"`` query in [0, 1], or ``None`` on failure.
    """
    import asyncio
    try:
        from llm import call_embed_with_dispatch as _embed
        from retrievers.pinecone_vector import PineconeVectorRetriever
    except Exception as exc:  # pragma: no cover
        logger.warning("probe_assamese: import failed: %s", exc)
        return None
    try:
        half = max(0.05, timeout_s / 2.0)
        q_vec = await asyncio.wait_for(
            _embed(query, task_type="RETRIEVAL_QUERY", lang="as"),
            timeout=half,
        )
        if not q_vec:
            return None
        retriever = PineconeVectorRetriever()
        if not retriever.is_configured():
            return None
        matches = await asyncio.wait_for(
            retriever.query(
                q_vec, top_k=1,
                metadata_filter={"subject_id": {"$eq": subject_id}},
                return_metadata=False, namespace="as",
            ),
            timeout=max(0.05, timeout_s - half),
        )
    except asyncio.TimeoutError:
        return None
    except Exception as exc:
        logger.warning("probe_assamese: query failed: %s", exc)
        return None
    if not matches:
        return 0.0
    try:
        return max(0.0, min(1.0, float(matches[0].get("score", 0.0))))
    except (TypeError, ValueError):
        return None


async def probe_topic_score(
    query: str,
    *,
    subject_id: Optional[str] = None,
    lang: Optional[str] = "en",
    timeout_s: float = 0.5,
) -> Optional[float]:
    """Run the **language-correct** topic probe and return the centroid
    similarity in [0, 1], or ``None`` when the probe is unavailable.

    Language dispatch (Task #37 reviewer iteration 3 ask)
    -----------------------------------------------------
    * ``en`` (default) → :func:`wai_chapter_index.classify` (Workers-AI
      ``@cf/baai/bge-small-en-v1.5`` per-subject centroid index — the
      original fast English path). Embed runs once and is reused by
      ``resolve_rag_context``.
    * ``as``           → :func:`_probe_assamese_via_pinecone` (Pinecone
      Inference multilingual-e5-large + ``namespace="as"`` topK=1).
      This is the ONLY embed pool whose vector space matches the
      Assamese index, and matches the language-correct embed contract
      from Task #27. The English-oriented bge-small-en-v1.5 path is
      never taken for Assamese — no silent fallback.

    Common semantics
    ----------------
    * **No subject context.** Returns ``None`` (probe-pending sentinel
      → router defaults to ``rag``; never silent web downgrade).
    * **Latency budget.** Hard-capped at 500ms by default so a slow
      probe cannot blow the 3s p95 first-token target. Any timeout
      surfaces as ``None``, never a silent "weak match → web".
    """
    import asyncio
    if not (query or "").strip() or not subject_id:
        return None
    lang_norm = _normalize_lang(lang)
    if lang_norm == "as":
        return await _probe_assamese_via_bedrock_cohere(
            query, subject_id, timeout_s=timeout_s,
        )
    try:
        import wai_chapter_index as _wai
    except Exception as exc:  # pragma: no cover — module always present
        logger.warning("chat_router.probe_topic_score: wai import failed: %s", exc)
        return None
    try:
        result = await asyncio.wait_for(
            _wai.classify(query, subject_id),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.info(
            "chat_router.probe_topic_score: probe timed out (>%.2fs) for %r — "
            "returning None (probe-pending sentinel)", timeout_s, query[:40],
        )
        return None
    except Exception as exc:
        logger.warning("chat_router.probe_topic_score: classify error: %s", exc)
        return None
    if not result:
        # Centroid index returned NO match within its own _SIM_LOW gate.
        # Treat as a hard zero so the router routes to ``web`` rather
        # than the probe-pending sentinel; this is the actual "weak
        # match" signal we want.
        return 0.0
    sim = result.get("similarity")
    try:
        return max(0.0, min(1.0, float(sim)))
    except (TypeError, ValueError):
        return None


__all__ = [
    "RouteDecision",
    "lang_profile",
    "route",
    "probe_topic_score",
    "score_from_classify_result",
]
