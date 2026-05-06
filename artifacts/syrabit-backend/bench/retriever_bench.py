"""
retriever_bench.py — side-by-side latency + retrieval-overlap benchmark.

Runs an identical query set against every available retriever
(`pinecone` and `vectorize` by default), reports p50 / p95 / p99 latency
plus pairwise top-k overlap (Jaccard + intersection-at-k as proxy
metrics for recall in the absence of a ground-truth gold set).

Task #490: the legacy `vertex` retriever was deleted (Vertex Vector
Search retired). The active retriever set is the Pinecone primary +
the Cloudflare Vectorize cache.

Usage:
    cd artifacts/syrabit-backend
    python -m bench.retriever_bench [--queries N] [--top-k K] \\
        [--retrievers pinecone,vectorize] [--out path.json]

Exit codes:
    0 — at least one configured retriever served queries
    2 — every requested retriever short-circuited (not configured)

Output JSON shape:
    {
        "queries": ["…", "…", …],
        "top_k": 10,
        "retrievers": {
            "pinecone": {
                "configured": true,
                "latency_ms": {"p50": …, "p95": …, "p99": …, "mean": …, "min": …, "max": …, "n": …},
                "results": [{"query": "…", "ids": [...], "scores": [...], "ms": …}, …],
                "errors": 0
            },
            "vectorize": { … }
        },
        "overlap": {
            "pinecone_vs_vectorize": {
                "jaccard_mean": …,
                "intersection_at_k_mean": …,
                "perfect_overlap_pct": …,
                "n": …
            }
        }
    }
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import sys
import time
from pathlib import Path

logger = logging.getLogger("retriever_bench")

# Curated benchmark queries — light, broad coverage of AHSEC/SEBA topics.
# Operators can override via `--queries-file` (one query per line).
DEFAULT_QUERIES = [
    "what is photosynthesis",
    "explain newton's third law of motion",
    "derivation of quadratic formula",
    "structure of the human heart",
    "causes of the french revolution",
    "redox reactions in chemistry",
    "fundamental rights in indian constitution",
    "cell division mitosis vs meiosis",
    "law of demand microeconomics",
    "balancing chemical equations",
    "pythagoras theorem proof",
    "electric circuit ohm's law",
    "differential equations first order",
    "ahsec class 12 mathematics integration",
    "human digestive system functions",
    "english grammar tenses",
    "indian independence movement 1857",
    "trigonometric identities sin cos",
    "alternating current electromagnetic induction",
    "organic chemistry alkanes alkenes",
]


def _percentile(samples: list[float], p: float) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def _latency_summary(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0, "min": 0.0, "max": 0.0, "n": 0}
    return {
        "p50": round(_percentile(samples, 50), 2),
        "p95": round(_percentile(samples, 95), 2),
        "p99": round(_percentile(samples, 99), 2),
        "mean": round(statistics.mean(samples), 2),
        "min": round(min(samples), 2),
        "max": round(max(samples), 2),
        "n": len(samples),
    }


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / max(1, len(sa | sb))


async def _embed_query(text: str) -> list[float]:
    try:
        from vertex_services import embed_text
    except ImportError:
        return []
    try:
        return await asyncio.wait_for(
            embed_text(text, task_type="RETRIEVAL_QUERY"), timeout=10.0,
        )
    except Exception as exc:
        logger.warning("embed query failed (%s): %s", text[:30], exc)
        return []


async def _bench_retriever(name: str, queries: list[str], top_k: int) -> dict:
    from retrievers import get_retriever_by_name
    try:
        r = get_retriever_by_name(name)
    except ValueError as exc:
        return {"configured": False, "error": str(exc)}
    if not r.is_configured():
        return {"configured": False, "results": [], "latency_ms": _latency_summary([])}

    latencies: list[float] = []
    results: list[dict] = []
    errors = 0
    for q in queries:
        vec = await _embed_query(q)
        if not vec:
            errors += 1
            results.append({"query": q, "ids": [], "scores": [], "ms": 0.0, "error": "embed_failed"})
            continue
        t0 = time.monotonic()
        try:
            matches = await r.query(vector=vec, top_k=top_k, return_metadata=False)
        except Exception as exc:
            errors += 1
            results.append({"query": q, "ids": [], "scores": [], "ms": 0.0, "error": str(exc)})
            continue
        ms = (time.monotonic() - t0) * 1000.0
        latencies.append(ms)
        results.append({
            "query": q,
            "ids": [m.get("id") for m in matches],
            "scores": [round(float(m.get("score", 0.0)), 4) for m in matches],
            "ms": round(ms, 2),
        })

    return {
        "configured": True,
        "latency_ms": _latency_summary(latencies),
        "results": results,
        "errors": errors,
    }


def _pairwise_overlap(
    a: list[dict], b: list[dict], top_k: int,
) -> dict[str, float]:
    pairs = []
    for ra, rb in zip(a, b):
        if ra.get("error") or rb.get("error"):
            continue
        ia = ra.get("ids", []) or []
        ib = rb.get("ids", []) or []
        if not ia and not ib:
            continue
        inter = len(set(ia) & set(ib))
        jacc = _jaccard(ia, ib)
        pairs.append((jacc, inter))
    if not pairs:
        return {"jaccard_mean": 0.0, "intersection_at_k_mean": 0.0, "perfect_overlap_pct": 0.0, "n": 0}
    jaccs = [p[0] for p in pairs]
    inters = [p[1] for p in pairs]
    perfect = sum(1 for p in pairs if p[0] >= 0.999)
    return {
        "jaccard_mean": round(statistics.mean(jaccs), 4),
        "intersection_at_k_mean": round(statistics.mean(inters), 2),
        "perfect_overlap_pct": round(100.0 * perfect / len(pairs), 1),
        "n": len(pairs),
    }


async def _run(retriever_names: list[str], queries: list[str], top_k: int, out: str | None) -> int:
    report = {
        "queries": queries,
        "top_k": top_k,
        "retrievers": {},
        "overlap": {},
    }
    for name in retriever_names:
        logger.info("benchmarking %s …", name)
        report["retrievers"][name] = await _bench_retriever(name, queries, top_k)

    # Pairwise overlap for any two configured retrievers.
    configured = [n for n, r in report["retrievers"].items() if r.get("configured")]
    for i in range(len(configured)):
        for j in range(i + 1, len(configured)):
            a, b = configured[i], configured[j]
            key = f"{a}_vs_{b}"
            report["overlap"][key] = _pairwise_overlap(
                report["retrievers"][a].get("results", []),
                report["retrievers"][b].get("results", []),
                top_k,
            )

    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text)
        logger.info("wrote %s", out)

    if not configured:
        logger.error("no requested retriever was configured")
        return 2
    # Reject benchmarks where every query failed embedding/retrieval —
    # an "all errors" run is worse than no data because it will be
    # mis-read as "both backends are equally fast at returning nothing".
    any_success = False
    for name in configured:
        latn = report["retrievers"][name].get("latency_ms", {}).get("n", 0)
        if latn > 0:
            any_success = True
            break
    if not any_success:
        logger.error("all queries failed for every configured retriever")
        return 2
    return 0


def _load_queries(path: str | None, n_limit: int) -> list[str]:
    if not path:
        return DEFAULT_QUERIES[:n_limit] if n_limit > 0 else DEFAULT_QUERIES
    lines = [ln.strip() for ln in Path(path).read_text().splitlines() if ln.strip()]
    return lines[:n_limit] if n_limit > 0 else lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=int, default=int(os.environ.get("BENCH_QUERIES", "0")),
                        help="Cap number of queries (0 = all).")
    parser.add_argument("--queries-file", default=os.environ.get("BENCH_QUERIES_FILE"))
    parser.add_argument("--top-k", type=int, default=int(os.environ.get("BENCH_TOP_K", "10")))
    parser.add_argument("--retrievers", default=os.environ.get("BENCH_RETRIEVERS", "pinecone,vectorize"))
    parser.add_argument("--out", default=os.environ.get("BENCH_OUT"))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    names = [n.strip().lower() for n in args.retrievers.split(",") if n.strip()]
    queries = _load_queries(args.queries_file, args.queries)
    return asyncio.run(_run(names, queries, args.top_k, args.out))


if __name__ == "__main__":
    sys.exit(main())
