"""
rag_live_bench.py — Live RAG speed + quality benchmark.

Hits the running backend's /api/ai/internal-chat endpoint with a set of
AHSEC/SEBA-relevant questions and measures:

  Speed:    time-to-first-token, total stream time, cache hit/miss
  Quality:  rag_source, rag_quality tier, rag_chunks count, reranking active

Usage:
    cd artifacts/syrabit-backend
    python -m bench.rag_live_bench [--host http://localhost:8080] [--n 8]
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from typing import Any

import httpx

BASE_URL = "http://localhost:8080"
_STREAM_ENDPOINT = "/api/ai/chat/stream"   # was /api/ai/internal-chat (renamed)

TEST_QUERIES = [
    {"q": "What is photosynthesis?",               "subject": "Biology"},
    {"q": "Explain Newton's third law of motion",   "subject": "Physics"},
    {"q": "What causes acid rain?",                 "subject": "Chemistry"},
    {"q": "Describe the structure of DNA",          "subject": "Biology"},
    {"q": "What is the significance of 1857 revolt","subject": "History"},
    {"q": "Explain the Indian independence movement","subject": "History"},
    {"q": "What is an ecosystem?",                  "subject": "Biology"},
    {"q": "Define the law of conservation of energy","subject": "Physics"},
    {"q": "What are the causes of World War 1?",    "subject": "History"},
    {"q": "Explain ionic bonding",                  "subject": "Chemistry"},
]

COLORS = {
    "green":  "\033[92m",
    "yellow": "\033[93m",
    "red":    "\033[91m",
    "cyan":   "\033[96m",
    "bold":   "\033[1m",
    "reset":  "\033[0m",
}


def c(text, color):
    return f"{COLORS[color]}{text}{COLORS['reset']}"


async def bench_query(client: httpx.AsyncClient, query: str, idx: int) -> dict[str, Any]:
    payload = {
        "message": query,
        "conversation_id": None,
        "context": {},
        "response_lang": "en",
    }
    result = {
        "query": query,
        "ttft_ms": None,      # time to first token
        "total_ms": None,     # full stream time
        "from_cache": False,
        "rag_source": "none",
        "rag_quality": "none",
        "rag_chunks": 0,
        "ok": False,
        "error": None,
    }
    t0 = time.perf_counter()
    try:
        async with client.stream(
            "POST",
            "/api/ai/internal-chat",
            json=payload,
            timeout=httpx.Timeout(30.0, connect=5.0),
        ) as resp:
            if resp.status_code != 200:
                result["error"] = f"HTTP {resp.status_code}"
                return result

            first_token = False
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    frame = json.loads(raw)
                except Exception:
                    continue

                event = frame.get("event")
                if event == "meta":
                    result["from_cache"]   = frame.get("from_cache", False)
                    result["rag_source"]   = frame.get("rag_source", "none")
                    result["rag_quality"]  = frame.get("rag_quality", "none")
                    result["rag_chunks"]   = frame.get("rag_chunks", 0)
                elif frame.get("content") and not first_token:
                    first_token = True
                    result["ttft_ms"] = round((time.perf_counter() - t0) * 1000)

        result["total_ms"] = round((time.perf_counter() - t0) * 1000)
        result["ok"] = True
    except Exception as exc:
        result["error"] = str(exc)[:120]
        result["total_ms"] = round((time.perf_counter() - t0) * 1000)

    return result


def quality_score(r: dict) -> str:
    src = r["rag_source"]
    q   = r["rag_quality"]
    chunks = r["rag_chunks"]
    if r["from_cache"]:
        return c("⚡ CACHE HIT", "cyan")
    if src == "internal" and q in ("tier1", "tier0"):
        return c(f"✓ INTERNAL  ({chunks} chunks)", "green")
    if src in ("library", "document") and chunks > 0:
        return c(f"◎ LIBRARY   ({chunks} chunks)", "green")
    if src == "web":
        return c(f"~ WEB       ({chunks} chunks)", "yellow")
    if q == "none" or src == "none":
        return c("✗ NO RAG    (general knowledge)", "red")
    return c(f"? {src}/{q} ({chunks} chunks)", "yellow")


async def run_bench(host: str, n: int):
    queries = TEST_QUERIES[:n]
    print(c(f"\n{'─'*62}", "bold"))
    print(c(f"  Syrabit RAG — Speed & Quality Benchmark  ({n} queries)", "bold"))
    print(c(f"  Host: {host}", "cyan"))
    print(c(f"{'─'*62}\n", "bold"))

    results = []
    async with httpx.AsyncClient(base_url=host, http2=True) as client:
        for i, item in enumerate(queries, 1):
            q = item["q"]
            subj = item["subject"]
            print(f"  [{i:2d}/{n}] {subj:12s}  {q[:48]}")
            r = await bench_query(client, q, i)
            results.append(r)

            ttft  = f"{r['ttft_ms']}ms"  if r["ttft_ms"]  else "—"
            total = f"{r['total_ms']}ms" if r["total_ms"] else "—"
            qual  = quality_score(r)
            err   = f"  {c('ERROR: '+r['error'], 'red')}" if r["error"] else ""
            print(f"         TTFT={c(ttft, 'cyan')}  total={c(total,'cyan')}  {qual}{err}")

    # ── Summary ──────────────────────────────────────────────────────────────
    ok  = [r for r in results if r["ok"]]
    err = [r for r in results if not r["ok"]]

    print(c(f"\n{'─'*62}", "bold"))
    print(c("  SUMMARY", "bold"))
    print(c(f"{'─'*62}", "bold"))
    print(f"  Queries run:    {len(results)}  ({c(str(len(ok))+' OK', 'green')} / {c(str(len(err))+' errors', 'red')})")

    if ok:
        ttfts  = [r["ttft_ms"]  for r in ok if r["ttft_ms"]  is not None]
        totals = [r["total_ms"] for r in ok if r["total_ms"] is not None]
        if ttfts:
            print(f"  TTFT (ms):      "
                  f"p50={c(str(round(statistics.median(ttfts))),'cyan')}  "
                  f"p95={c(str(round(sorted(ttfts)[int(len(ttfts)*.95)])  if len(ttfts)>1 else ttfts[0]),'cyan')}  "
                  f"mean={c(str(round(statistics.mean(ttfts))),'cyan')}")
        if totals:
            print(f"  Total (ms):     "
                  f"p50={c(str(round(statistics.median(totals))),'cyan')}  "
                  f"p95={c(str(round(sorted(totals)[int(len(totals)*.95)]) if len(totals)>1 else totals[0]),'cyan')}  "
                  f"mean={c(str(round(statistics.mean(totals))),'cyan')}")

        cache_hits  = sum(1 for r in ok if r["from_cache"])
        internal    = sum(1 for r in ok if r["rag_source"] == "internal")
        no_rag      = sum(1 for r in ok if r["rag_source"] in ("none",""))
        web_hits    = sum(1 for r in ok if r["rag_source"] == "web")
        avg_chunks  = round(statistics.mean(r["rag_chunks"] for r in ok), 1) if ok else 0

        print(f"\n  Cache hits:     {c(str(cache_hits), 'cyan')} / {len(ok)}")
        print(f"  Internal RAG:   {c(str(internal), 'green')} queries used DB content")
        print(f"  Web fallback:   {c(str(web_hits), 'yellow')} queries")
        print(f"  No RAG:         {c(str(no_rag), 'red')} queries (general-knowledge fallback)")
        print(f"  Avg chunks:     {c(str(avg_chunks), 'cyan')} per query")

        # Quality rating
        rag_pct = round((internal + web_hits) / len(ok) * 100) if ok else 0
        internal_pct = round(internal / len(ok) * 100) if ok else 0
        grade = (
            c("A — Excellent", "green") if rag_pct >= 80 and internal_pct >= 60
            else c("B — Good",      "green") if rag_pct >= 60
            else c("C — Fair",      "yellow") if rag_pct >= 40
            else c("D — Needs work","red")
        )
        print(f"\n  RAG coverage:   {rag_pct}% of queries grounded  →  {grade}")

    if err:
        print(f"\n  Errors:")
        for r in err:
            print(f"    • {r['query'][:50]}  →  {r['error']}")

    print(c(f"\n{'─'*62}\n", "bold"))


def main():
    ap = argparse.ArgumentParser(description="Live RAG benchmark")
    ap.add_argument("--host", default=BASE_URL)
    ap.add_argument("--n",    type=int, default=8, help="Number of queries to run (max 10)")
    args = ap.parse_args()
    asyncio.run(run_bench(args.host, min(args.n, len(TEST_QUERIES))))


if __name__ == "__main__":
    main()
