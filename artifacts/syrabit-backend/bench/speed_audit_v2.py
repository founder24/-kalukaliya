"""
speed_audit_v2.py — Post-overhaul speed audit (T001–T007 verification).

Tests the 7 improvements introduced in the May 2026 speed audit sprint:

  S1 — Azure OpenAI streaming TTFT  (T007)
  S2 — HyDE generation latency      (T001 + T005)
  S3 — Phase-0 Redis warm check     (T006)
  S4 — Full RAG pipeline (10 q)     (T001 + T004 + T005)
  S5 — Embed latency                (T001)
  S6 — Rerank latency               (T001)

Usage:
    cd artifacts/syrabit-backend
    python -m bench.speed_audit_v2
"""
from __future__ import annotations

import asyncio
import statistics
import time
import hashlib
import json
from typing import Optional

C = {
    "g": "\033[92m", "y": "\033[93m", "r": "\033[91m",
    "c": "\033[96m", "b": "\033[1m",  "x": "\033[0m",
    "m": "\033[95m",
}


def fg(text, col): return f"{C[col]}{text}{C['x']}"
def bold(t):        return fg(t, "b")
def sep(char="─", n=68): return bold(char * n)
def ok(ms):         return fg(f"{ms}ms", "g" if ms < 500 else "y" if ms < 1500 else "r")


QUERIES = [
    ("What is photosynthesis?",                         "Biology"),
    ("Explain Newton's third law of motion",             "Physics"),
    ("What causes acid rain?",                           "Chemistry"),
    ("Describe the structure of DNA",                    "Biology"),
    ("What is the significance of the 1857 revolt?",    "History"),
    ("Explain the Indian independence movement",         "History"),
    ("What is an ecosystem?",                            "Biology"),
    ("Define the law of conservation of energy",         "Physics"),
    ("What are the causes of World War 1?",             "History"),
    ("Explain ionic bonding with examples",              "Chemistry"),
]


# ─── S1: Azure OpenAI streaming TTFT ──────────────────────────────────────────

async def s1_azure_ttft() -> dict:
    print(f"\n{sep()}")
    print(bold("  S1 — Azure OpenAI Streaming TTFT  (T007)"))
    print(sep())

    try:
        from providers.azure_openai import stream_chat, ENABLED, _MODEL
        if not ENABLED:
            print(fg("  SKIP — Azure OpenAI not configured (azure-openai CF slug missing)", "y"))
            return {"enabled": False}

        print(f"  Model: {fg(_MODEL, 'c')}  Gateway: CF-BYOK")

        test_messages = [
            {"role": "system", "content": "You are a concise study assistant. Answer in 2-3 sentences."},
            {"role": "user",   "content": "What is photosynthesis?"},
        ]

        ttfts = []
        totals = []
        for run in range(3):
            t0 = time.perf_counter()
            ttft_ms: Optional[int] = None
            token_count = 0
            try:
                async for token in stream_chat(test_messages, max_tokens=80):
                    if ttft_ms is None:
                        ttft_ms = round((time.perf_counter() - t0) * 1000)
                    token_count += 1
                total_ms = round((time.perf_counter() - t0) * 1000)
                if ttft_ms is not None:
                    ttfts.append(ttft_ms)
                    totals.append(total_ms)
                    print(f"  Run {run+1}: TTFT={ok(ttft_ms)}  total={ok(total_ms)}  tokens≈{token_count}")
                else:
                    print(f"  Run {run+1}: {fg('no tokens received', 'y')}")
            except Exception as e:
                elapsed = round((time.perf_counter() - t0) * 1000)
                print(f"  Run {run+1}: {fg(f'ERROR ({elapsed}ms): {str(e)[:80]}', 'r')}")
            await asyncio.sleep(0.5)

        if ttfts:
            p50_ttft  = round(statistics.median(ttfts))
            p50_total = round(statistics.median(totals))
            print(f"\n  p50 TTFT={ok(p50_ttft)}  p50 total={ok(p50_total)}")
            return {"enabled": True, "p50_ttft_ms": p50_ttft, "p50_total_ms": p50_total}
    except Exception as e:
        print(fg(f"  FATAL: {e}", "r"))
    return {"enabled": False}


# ─── S2: HyDE generation latency ──────────────────────────────────────────────

async def s2_hyde_latency() -> dict:
    print(f"\n{sep()}")
    print(bold("  S2 — HyDE Generation Latency  (T001 timeout=1.5s, T005 parallelized)"))
    print(sep())

    try:
        from rag import _generate_hyde_passage, _HYDE_TIMEOUT

        print(f"  Timeout budget: {fg(str(_HYDE_TIMEOUT)+'s', 'c')}")

        times = []
        for q, subj in QUERIES[:5]:
            t0 = time.perf_counter()
            try:
                passage = await asyncio.wait_for(_generate_hyde_passage(q), timeout=_HYDE_TIMEOUT + 0.1)
                ms = round((time.perf_counter() - t0) * 1000)
                snippet = (passage or "")[:60].replace("\n", " ")
                within = fg("✓", "g") if ms <= _HYDE_TIMEOUT * 1000 else fg("⚠", "y")
                times.append(ms)
                print(f"  [{subj:10s}] {q[:42]:<42}  {ok(ms)}  {within}  \"{snippet}...\"")
            except asyncio.TimeoutError:
                ms = round((time.perf_counter() - t0) * 1000)
                print(f"  [{subj:10s}] {q[:42]:<42}  {fg('TIMEOUT '+str(ms)+'ms', 'r')}")
                times.append(ms)
            except Exception as e:
                ms = round((time.perf_counter() - t0) * 1000)
                print(f"  [{subj:10s}] {q[:42]:<42}  {fg('ERR: '+str(e)[:50], 'r')}")

        if times:
            p50 = round(statistics.median(times))
            worst = max(times)
            print(f"\n  p50={ok(p50)}  worst={ok(worst)}  budget={fg(str(int(_HYDE_TIMEOUT*1000))+'ms', 'c')}")
            pct_within = sum(1 for t in times if t <= _HYDE_TIMEOUT * 1000) / len(times) * 100
            print(f"  Within budget: {fg(f'{pct_within:.0f}%', 'g' if pct_within >= 80 else 'y')}")
            return {"p50_ms": p50, "worst_ms": worst, "pct_within_budget": pct_within}
    except Exception as e:
        import traceback; traceback.print_exc()
        print(fg(f"  FATAL: {e}", "r"))
    return {}


# ─── S3: Phase-0 Redis warm check ─────────────────────────────────────────────

async def s3_redis_warm_check() -> dict:
    print(f"\n{sep()}")
    print(bold("  S3 — Phase-0 Redis Warm Check  (T006 _check_warm_redis, <5ms target)"))
    print(sep())

    try:
        from deps import redis_client

        if not redis_client:
            print(fg("  SKIP — Redis not available", "y"))
            return {}

        # Simulate what _check_warm_redis() does: just an HGET on a cold key
        loop = asyncio.get_event_loop()

        # Measure cold key — run_in_executor so we don't block the event loop
        # (mirrors the production fix applied to _check_warm_redis)
        times_cold = []
        for q, subj in QUERIES[:5]:
            _sid = ""
            _wkey = f"warm_ch:{hashlib.md5(f'{q.strip()}|{_sid}'.encode()).hexdigest()}"
            t0 = time.perf_counter()
            _wdata = await loop.run_in_executor(None, redis_client.get, _wkey)
            ms_cold = round((time.perf_counter() - t0) * 1000, 2)
            times_cold.append(ms_cold)
            label = fg(str(ms_cold)+"ms", "g" if ms_cold < 10 else "y" if ms_cold < 50 else "r")
            print(f"  [{subj:10s}] {q[:40]:<40}  cold={label}")

        # Seed a warm entry and measure warm hit
        q_warm, s_warm = QUERIES[0]
        warm_key = f"warm_ch:{hashlib.md5(f'{q_warm.strip()}|'.encode()).hexdigest()}"
        await loop.run_in_executor(
            None, lambda: redis_client.set(warm_key, json.dumps([{"id": "test-ch", "name": "Test Chapter"}]), ex=20)
        )

        times_warm = []
        for _ in range(3):
            t0 = time.perf_counter()
            _wdata = await loop.run_in_executor(None, redis_client.get, warm_key)
            ms_warm = round((time.perf_counter() - t0) * 1000, 2)
            times_warm.append(ms_warm)

        # Cleanup
        await loop.run_in_executor(None, redis_client.delete, warm_key)

        p50_cold = round(statistics.median(times_cold), 2)
        p50_warm = round(statistics.median(times_warm), 2)
        print(f"\n  Cold key (miss)  p50={fg(str(p50_cold)+'ms', 'g' if p50_cold < 15 else 'y')}")
        print(f"  Warm key (hit)   p50={fg(str(p50_warm)+'ms', 'g' if p50_warm < 15 else 'y')}  {fg('✓ well within 5ms HTTP budget', 'g') if p50_warm < 5 else ''}")
        return {"p50_cold_ms": p50_cold, "p50_warm_ms": p50_warm}
    except Exception as e:
        print(fg(f"  FATAL: {e}", "r"))
    return {}


# ─── S4: Full RAG pipeline ─────────────────────────────────────────────────────

async def s4_full_rag() -> dict:
    print(f"\n{sep()}")
    print(bold("  S4 — Full RAG Pipeline (T001 timeouts + T004 pool + T005 HyDE∥keyword)"))
    print(sep())

    try:
        from rag import _fetch_internal_chapters, resolve_rag_context
        from deps import is_mongo_available

        if not await is_mongo_available():
            print(fg("  SKIP — MongoDB not reachable", "r"))
            return {}

        times = []
        results = []
        for q, subj in QUERIES:
            t0 = time.perf_counter()
            try:
                chapters = await asyncio.wait_for(
                    _fetch_internal_chapters(q, subject_name=subj),
                    timeout=12.0,
                )
                ctx = await resolve_rag_context(
                    q, subject_name=subj,
                    prefetched_chapters=chapters,
                    intent="notes",
                )
            except asyncio.TimeoutError:
                ms = round((time.perf_counter() - t0) * 1000)
                print(f"  [{subj:10s}] {q[:40]:<40}  {fg('TIMEOUT >12s', 'r')}")
                times.append(ms); results.append({"ok": False}); continue
            except Exception as e:
                ms = round((time.perf_counter() - t0) * 1000)
                print(f"  [{subj:10s}] {q[:40]:<40}  {fg('ERR: '+str(e)[:50], 'r')}")
                times.append(ms); results.append({"ok": False}); continue

            ms = round((time.perf_counter() - t0) * 1000)
            times.append(ms)
            source = ctx.get("source", "none")
            chunks = len(ctx.get("chunks") or [])
            quality = ctx.get("quality", "?")
            src_str = fg("✓ internal", "g") if source in ("internal", "document") else fg("~ web", "y") if source == "web" else fg("✗ none", "r")
            print(f"  [{subj:10s}] {q[:40]:<40}  {ok(ms):>10}  {src_str}  {chunks}ch  q={quality}")
            results.append({"ok": True, "source": source, "ms": ms, "chunks": chunks})

        ok_results = [r for r in results if r.get("ok")]
        if ok_results:
            good_times = [r["ms"] for r in ok_results]
            p50  = round(statistics.median(good_times))
            p95  = round(sorted(good_times)[int(len(good_times) * 0.95)] if len(good_times) > 1 else good_times[0])
            mean = round(statistics.mean(good_times))
            internal = sum(1 for r in ok_results if r.get("source") in ("internal", "document"))
            rag_pct  = round(internal / len(results) * 100)
            avg_ch   = round(statistics.mean(r.get("chunks", 0) for r in ok_results), 1)

            print(f"\n  Speed   p50={ok(p50)}  p95={ok(p95)}  mean={ok(mean)}")
            print(f"  RAG hits:  {fg(str(internal), 'g')} / {len(results)}  ({rag_pct}%)")
            print(f"  Avg chunks/query: {fg(str(avg_ch), 'c')}")
            grade = (
                fg("A — Excellent", "g") if rag_pct >= 80
                else fg("B — Good", "g") if rag_pct >= 60
                else fg("C — Fair", "y") if rag_pct >= 40
                else fg("D — Needs work", "r")
            )
            print(f"  Coverage grade: {grade}")
            return {"p50_ms": p50, "p95_ms": p95, "mean_ms": mean, "rag_pct": rag_pct}
    except Exception as e:
        import traceback; traceback.print_exc()
    return {}


# ─── S5: Embed latency ─────────────────────────────────────────────────────────

async def s5_embed() -> dict:
    print(f"\n{sep()}")
    print(bold("  S5 — Embed Latency  (T001 timeout=2.0s)"))
    print(sep())

    try:
        from providers.cloudflare_ai import embed, _ENABLED
        if not _ENABLED:
            print(fg("  SKIP — Cloudflare AI not configured", "r"))
            return {}

        times = []
        for q, subj in QUERIES[:5]:
            await asyncio.sleep(0.2)
            t0 = time.perf_counter()
            try:
                vecs = await embed([q])
                ms = round((time.perf_counter() - t0) * 1000)
                if vecs and vecs[0]:
                    times.append(ms)
                    print(f"  [{subj:10s}] {q[:42]:<42}  {ok(ms)}  {len(vecs[0])}d")
                else:
                    print(f"  [{subj:10s}] {q[:42]:<42}  {fg('no vector', 'y')}")
            except Exception as e:
                print(f"  [{subj:10s}] {q[:42]:<42}  {fg('ERR: '+str(e)[:50], 'r')}")

        if times:
            p50 = round(statistics.median(times))
            print(f"\n  p50={ok(p50)}  mean={ok(round(statistics.mean(times)))}")
            return {"p50_ms": p50}
    except Exception as e:
        print(fg(f"  FATAL: {e}", "r"))
    return {}


# ─── S6: Rerank latency ────────────────────────────────────────────────────────

async def s6_rerank() -> dict:
    print(f"\n{sep()}")
    print(bold("  S6 — Rerank Latency  (T001 timeout=3.0s)"))
    print(sep())

    try:
        from providers.cloudflare_ai import rerank as cf_rerank, _ENABLED
        if not _ENABLED:
            print(fg("  SKIP — Cloudflare AI not configured", "r"))
            return {}

        docs = [
            "Photosynthesis is the process by which green plants convert sunlight into food.",
            "Plants absorb carbon dioxide and release oxygen during photosynthesis.",
            "The light-dependent reactions occur in the thylakoid membranes.",
            "Chlorophyll absorbs light primarily in the red and blue wavelengths.",
            "The Calvin cycle is the light-independent stage of photosynthesis.",
            "Water is split during the light reactions releasing oxygen as byproduct.",
            "ATP and NADPH are produced in the light reactions.",
            "Glucose is synthesized in the Calvin cycle using CO2.",
        ]
        q = "What is photosynthesis?"
        times = []
        for run in range(3):
            t0 = time.perf_counter()
            try:
                scores = await cf_rerank(q, docs)
                ms = round((time.perf_counter() - t0) * 1000)
                if scores:
                    times.append(ms)
                    print(f"  Run {run+1}: {ok(ms)}  {len(docs)} docs scored  top={max(scores):.4f}")
                else:
                    print(f"  Run {run+1}: {fg('no scores', 'y')}")
            except Exception as e:
                print(f"  Run {run+1}: {fg('ERR: '+str(e)[:60], 'r')}")

        if times:
            p50 = round(statistics.median(times))
            print(f"\n  p50={ok(p50)}  ({len(docs)} docs, bge-reranker-base)")
            return {"p50_ms": p50}
    except Exception as e:
        print(fg(f"  FATAL: {e}", "r"))
    return {}


# ─── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print(bold("\n" + "═" * 68))
    print(bold("  Syrabit.ai — Speed Audit v2  (May 2026, T001–T007 verification)"))
    print(bold("═" * 68))

    r_azure  = await s1_azure_ttft()
    r_hyde   = await s2_hyde_latency()
    r_redis  = await s3_redis_warm_check()
    r_embed  = await s5_embed()
    r_rerank = await s6_rerank()
    r_rag    = await s4_full_rag()

    print(f"\n{sep('═')}")
    print(bold("  SCORECARD"))
    print(sep("═"))

    def row(label, val, unit="ms", good=500, bad=1500):
        if val is None or val == 0:
            return f"  {label:<32}  {fg('—', 'y')}"
        col = "g" if val < good else "y" if val < bad else "r"
        return f"  {label:<32}  {fg(str(val)+unit, col)}"

    if r_azure.get("enabled"):
        print(row("Azure TTFT p50         (T007)", r_azure.get("p50_ttft_ms"), good=400, bad=800))
        print(row("Azure total p50        (T007)", r_azure.get("p50_total_ms"), good=2000, bad=5000))
    else:
        print(f"  {'Azure TTFT':<32}  {fg('NOT CONFIGURED', 'y')}")

    print(row("HyDE generation p50    (T001)", r_hyde.get("p50_ms"), good=800, bad=1500))
    print(row("Redis warm check p50   (T006)", r_redis.get("p50_cold_ms"), good=5, bad=15))
    print(row("Embed latency p50      (T001)", r_embed.get("p50_ms"), good=350, bad=700))
    print(row("Rerank latency p50     (T001)", r_rerank.get("p50_ms"), good=400, bad=800))
    print(row("Full RAG pipeline p50  (T004)", r_rag.get("p50_ms"), good=3000, bad=6000))
    print(row("Full RAG pipeline p95  (T004)", r_rag.get("p95_ms"), good=5000, bad=9000))

    if r_rag.get("rag_pct") is not None:
        pct = r_rag["rag_pct"]
        col = "g" if pct >= 80 else "y" if pct >= 60 else "r"
        print(f"  {'RAG coverage':<32}  {fg(str(pct)+'%', col)}")

    # Impact summary
    print(f"\n{sep()}")
    print(bold("  BOTTLENECK SUMMARY  (estimated TTFT path for English RAG chat)"))
    print(sep())
    azure_ttft  = r_azure.get("p50_ttft_ms", 300)
    embed_p50   = r_embed.get("p50_ms", 350)
    redis_p50   = r_redis.get("p50_cold_ms", 5)
    rag_p50     = r_rag.get("p50_ms", 4200)

    if r_azure.get("enabled"):
        print(f"  Phase-0 Redis check  : {ok(int(redis_p50))} (new — T006)")
        print(f"  RAG pipeline (∥)     : {ok(rag_p50)} (parallelized HyDE — T005)")
        print(f"  LLM TTFT (Azure)     : {ok(azure_ttft)} (new fast-path — T007)")
        print(f"\n  → Estimated chat TTFB: ~{ok(azure_ttft + int(redis_p50))} (Phase-0 hit)  ~{ok(rag_p50 + azure_ttft)} (RAG path)")
    else:
        print(f"  Phase-0 Redis check  : {ok(int(redis_p50))} (new — T006)")
        print(f"  RAG pipeline (∥)     : {ok(rag_p50)} (parallelized HyDE — T005)")
        print(f"  LLM TTFT (Workers AI): ~400-800ms (SLM pool)")
        print(f"\n  → Estimated chat TTFB: ~{ok(rag_p50 + 600)} (RAG path, SLM pool)")

    print(sep("═") + "\n")


if __name__ == "__main__":
    asyncio.run(main())
