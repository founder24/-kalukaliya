"""Task #383 — Cloudflare Vectorize shadow-mode wrapper.

We currently serve all RAG retrieval out of Pinecone (Task #206). To
de-risk a future cut-over to Cloudflare Vectorize we want to mirror
every write and a sampled fraction of every read into Vectorize, then
compare the top-K overlap so we know recall parity *before* flipping
the primary. The wrapper here is purpose-built so the swap can be done
later by changing one factory line, not by re-instrumenting every
caller.

Design contract (matches ``retrievers.base.Retriever``):

  * Returns from the **primary** retriever are unchanged — Vectorize
    is purely observation. A bug or outage in Vectorize cannot affect
    chat traffic.
  * ``upsert`` writes to the primary first; only on success does it
    fire-and-forget into Vectorize. A failed shadow upsert is logged
    + counted, never re-raised.
  * ``query`` is mirrored at ``shadow_sample_rate`` (default 1.0 —
    every query is shadowed for full parity measurement). Latency and
    recall@k overlap (intersection of returned IDs ÷ ``k``) are
    recorded into ``_state`` so the admin route can render a parity
    dashboard. Operators can dial the rate down via
    ``VECTORIZE_SHADOW_SAMPLE_RATE`` if Vectorize starts charging more
    than the parity signal is worth, but the default mirrors 100% so
    the recall@10 number isn't a sampled estimate.
  * Everything is gated on ``VECTORIZE_SHADOW_ON``. Off → wrapper
    becomes a transparent passthrough that never touches Vectorize.

Why fire-and-forget:

  Pinecone latency budget for chat is ~80 ms p99. Adding a synchronous
  Vectorize round-trip would bloat that by 30–60 ms (Vectorize is
  edge-resident, but our origin is in DO LON1 so the network hop is
  not free). Shadow writes happen in the background via
  ``asyncio.create_task`` so they don't add a single millisecond to
  the request path.
"""
from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from collections import deque
from typing import Any, Optional

from retrievers.base import Retriever

logger = logging.getLogger(__name__)


# Module-level state so the admin route can introspect aggregate parity
# numbers without holding a reference to the wrapper instance.
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "writes_mirrored": 0,
    "writes_failed": 0,
    "queries_mirrored": 0,
    "queries_failed": 0,
    "queries_sampled_skipped": 0,
    "primary_latency_sum_ms": 0.0,
    "shadow_latency_sum_ms": 0.0,
    "recall_overlap_sum": 0.0,
    "recall_samples": 0,
}
_RECENT: deque = deque(maxlen=64)


def _bump(field: str, by: float = 1) -> None:
    with _LOCK:
        _STATE[field] = _STATE.get(field, 0) + by


def _record_recall(primary_ids: list[str], shadow_ids: list[str],
                   primary_ms: float, shadow_ms: float) -> None:
    if not primary_ids or not shadow_ids:
        return
    k = min(len(primary_ids), len(shadow_ids))
    if k == 0:
        return
    overlap = len(set(primary_ids[:k]) & set(shadow_ids[:k])) / k
    with _LOCK:
        _STATE["recall_overlap_sum"] += overlap
        _STATE["recall_samples"] += 1
        _STATE["primary_latency_sum_ms"] += primary_ms
        _STATE["shadow_latency_sum_ms"] += shadow_ms
        _RECENT.append({
            "ts": time.time(),
            "k": k,
            "overlap": round(overlap, 3),
            "primary_ms": round(primary_ms, 1),
            "shadow_ms": round(shadow_ms, 1),
        })


def snapshot() -> dict[str, Any]:
    """Aggregate parity readout for the admin ``/admin/cf-health`` panel."""
    with _LOCK:
        st = dict(_STATE)
        recent = list(_RECENT)
    samples = st["recall_samples"] or 0
    avg_overlap = (st["recall_overlap_sum"] / samples) if samples else 0.0
    avg_primary_ms = (st["primary_latency_sum_ms"] / samples) if samples else 0.0
    avg_shadow_ms = (st["shadow_latency_sum_ms"] / samples) if samples else 0.0
    return {
        "writes_mirrored": st["writes_mirrored"],
        "writes_failed": st["writes_failed"],
        "queries_mirrored": st["queries_mirrored"],
        "queries_failed": st["queries_failed"],
        "queries_sampled_skipped": st["queries_sampled_skipped"],
        "avg_recall_overlap": round(avg_overlap, 4),
        "avg_primary_latency_ms": round(avg_primary_ms, 1),
        "avg_shadow_latency_ms": round(avg_shadow_ms, 1),
        "recent_samples": recent[-16:],
    }


def reset_for_tests() -> None:
    with _LOCK:
        for k in list(_STATE.keys()):
            _STATE[k] = 0 if isinstance(_STATE[k], (int, float)) else _STATE[k]
        _RECENT.clear()


class ShadowRetriever(Retriever):
    """Wraps a primary retriever and mirrors traffic to a shadow.

    The class implements the full ``Retriever`` ABC by delegating to the
    primary; the only behavioural change is that ``upsert`` and
    ``query`` also schedule a background shadow call when both:

      1. ``enabled`` is true (constructor arg, sourced from
         ``VECTORIZE_SHADOW_ON``).
      2. The shadow retriever reports ``is_configured() == True`` —
         no point firing requests we know will be rejected.
    """

    def __init__(self, primary: Retriever, shadow: Retriever,
                 *, enabled: bool, shadow_sample_rate: float = 1.0) -> None:
        self._primary = primary
        self._shadow = shadow
        self._enabled = bool(enabled)
        self._sample_rate = max(0.0, min(1.0, float(shadow_sample_rate)))
        self.name = f"shadow({primary.name}+{shadow.name})"
        self.dimensions = primary.dimensions

    # ── Retriever ABC delegation ─────────────────────────────────────────
    def is_configured(self) -> bool:
        return self._primary.is_configured()

    async def index_info(self) -> dict[str, Any]:
        return await self._primary.index_info()

    async def index_config(self) -> dict[str, Any]:
        return await self._primary.index_config()

    async def get_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        return await self._primary.get_by_ids(ids)

    async def delete(self, ids: list[str]) -> int:
        out = await self._primary.delete(ids)
        if self._shadow_active():
            asyncio.create_task(self._shadow_delete(ids))
        return out

    async def upsert(self, vectors: list[dict[str, Any]]) -> dict[str, Any]:
        # embed-model: legacy-model-set-by-caller (pass-through; no embedding decision here)
        out = await self._primary.upsert(vectors)
        if self._shadow_active():
            asyncio.create_task(self._shadow_upsert(vectors))
        return out

    async def query(self, vector: list[float], top_k: int = 10,
                    metadata_filter: Optional[dict[str, Any]] = None,
                    return_values: bool = False,
                    return_metadata: bool = True) -> list[dict[str, Any]]:
        # Primary first — measure its latency so we can compare against
        # the shadow side-by-side instead of timing two cold paths.
        t0 = time.perf_counter()
        primary_results = await self._primary.query(
            vector, top_k=top_k, metadata_filter=metadata_filter,
            return_values=return_values, return_metadata=return_metadata,
        )
        primary_ms = (time.perf_counter() - t0) * 1000

        if not self._shadow_active():
            return primary_results
        if random.random() > self._sample_rate:
            _bump("queries_sampled_skipped")
            return primary_results

        # Shadow query runs in a task so we can return immediately.
        asyncio.create_task(self._shadow_query_compare(
            vector, top_k, metadata_filter, primary_results, primary_ms,
        ))
        return primary_results

    async def close(self) -> None:
        await self._primary.close()
        try:
            await self._shadow.close()
        except Exception:
            pass

    # ── Shadow helpers ───────────────────────────────────────────────────
    def _shadow_active(self) -> bool:
        return self._enabled and self._shadow.is_configured()

    async def _shadow_upsert(self, vectors: list[dict[str, Any]]) -> None:
        try:
            # embed-model: legacy-model-set-by-caller (shadow mirror; no embedding decision here)
            await self._shadow.upsert(vectors)
            _bump("writes_mirrored", len(vectors))
        except Exception as exc:
            _bump("writes_failed")
            logger.warning("[vectorize-shadow] upsert failed: %s", exc)

    async def _shadow_delete(self, ids: list[str]) -> None:
        try:
            await self._shadow.delete(ids)
        except Exception as exc:
            logger.warning("[vectorize-shadow] delete failed: %s", exc)

    async def _shadow_query_compare(
        self, vector: list[float], top_k: int,
        metadata_filter: Optional[dict[str, Any]],
        primary_results: list[dict[str, Any]], primary_ms: float,
    ) -> None:
        try:
            t0 = time.perf_counter()
            shadow_results = await self._shadow.query(
                vector, top_k=top_k, metadata_filter=metadata_filter,
                return_values=False, return_metadata=False,
            )
            shadow_ms = (time.perf_counter() - t0) * 1000
            _bump("queries_mirrored")
            _record_recall(
                [str(r.get("id")) for r in primary_results],
                [str(r.get("id")) for r in shadow_results],
                primary_ms, shadow_ms,
            )
        except Exception as exc:
            _bump("queries_failed")
            logger.warning("[vectorize-shadow] query failed: %s", exc)


def maybe_wrap_with_shadow(primary: Retriever) -> Retriever:
    """Return ``primary`` wrapped in a ShadowRetriever when the flag is
    on and a Vectorize backend is available; otherwise return
    ``primary`` unchanged.

    Imported lazily inside the factory to avoid a circular import on
    module load.
    """
    from config import VECTORIZE_SHADOW_ON
    if not VECTORIZE_SHADOW_ON:
        return primary
    if primary.name == "vectorize":
        # Already pointing at vectorize — shadowing onto itself is
        # pointless and would double the load.
        return primary
    try:
        from retrievers.vectorize import VectorizeRetriever
        shadow = VectorizeRetriever()
    except Exception as exc:
        logger.warning("[vectorize-shadow] unable to construct shadow: %s", exc)
        return primary
    if not shadow.is_configured():
        logger.info("[vectorize-shadow] vectorize backend not configured; "
                    "shadow disabled")
        return primary
    # DoD: every Pinecone query/upsert is mirrored for full parity by
    # default. Operators can override via env when the parity signal is
    # already stable and Vectorize bandwidth is the bottleneck.
    import os as _os
    try:
        rate = float(_os.environ.get("VECTORIZE_SHADOW_SAMPLE_RATE", "1.0"))
    except ValueError:
        rate = 1.0
    logger.info("[vectorize-shadow] wrapping %s with vectorize shadow "
                "(sample_rate=%.2f)", primary.name, rate)
    return ShadowRetriever(primary, shadow, enabled=True,
                           shadow_sample_rate=rate)


__all__ = ["ShadowRetriever", "snapshot", "reset_for_tests",
           "maybe_wrap_with_shadow"]
