"""Task #383 — nightly Vectorize parity comparison job.

Runs the bench/grounded_recall fixture queries against both the primary
retriever and the Vectorize shadow, and records the recall@k overlap
to the shadow snapshot so the admin panel shows a stable number that
doesn't depend on whether real chat traffic has hit the wrapper today.

Usage::

    python scripts/vectorize_parity_nightly.py

Exit code is 0 on success, 1 if VECTORIZE_SHADOW_ON is off (so the
cron alerter can page when the flag was accidentally flipped off), 2
if the shadow retriever is not configured.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("vectorize_parity_nightly")


async def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import VECTORIZE_SHADOW_ON
    if not VECTORIZE_SHADOW_ON:
        logger.error("VECTORIZE_SHADOW_ON is off — refusing to run parity job")
        return 1

    try:
        from retrievers.factory import get_retriever_by_name
        from retrievers.vectorize import VectorizeRetriever
        from vectorize_shadow import ShadowRetriever, snapshot
    except Exception as exc:
        logger.error("import failed: %s", exc)
        return 2

    primary = get_retriever_by_name(os.environ.get("PARITY_PRIMARY", "pinecone"))
    if primary is None:
        logger.error("primary retriever not available")
        return 2
    shadow = VectorizeRetriever()
    if not shadow.is_configured():
        logger.error("vectorize shadow not configured")
        return 2

    wrapped = ShadowRetriever(primary, shadow, enabled=True,
                              shadow_sample_rate=1.0)

    fixtures = Path(__file__).resolve().parent.parent / "bench" / "fixtures" / "grounded_recall.json"
    if not fixtures.exists():
        logger.error("fixture file missing: %s", fixtures)
        return 2

    queries = json.loads(fixtures.read_text())
    if isinstance(queries, dict):
        queries = queries.get("queries", [])
    if not queries:
        logger.warning("no queries in fixture — nothing to compare")
        return 0

    from providers.workers_embed import embed_text_sync
    sampled = 0
    for entry in queries[:50]:
        q = entry.get("query") if isinstance(entry, dict) else entry
        if not q:
            continue
        try:
            vec = embed_text_sync(q)
        except Exception as exc:
            logger.warning("embed failed for %r: %s", q, exc)
            continue
        try:
            await wrapped.query(vec, top_k=10)
            sampled += 1
        except Exception as exc:
            logger.warning("query failed for %r: %s", q, exc)
        # Give the background task a moment to flush.
        await asyncio.sleep(0.05)

    snap = snapshot()
    logger.info("parity run sampled=%d snapshot=%s", sampled,
                json.dumps(snap, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
