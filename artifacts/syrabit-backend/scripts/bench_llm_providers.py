"""Head-to-head LLM speed benchmark for chat & content providers.

Task #279 — Run a deterministic, repeatable speed benchmark across the
provider matrix used by Syrabit chat & content pipelines and write a
Markdown + JSON report to ``artifacts/syrabit-backend/bench_results/``.

Metrics per (suite, provider):
  - TTFT (time-to-first-token)         — p50 / p95 in ms
  - Tokens per second (decode rate)    — p50 only
  - Total latency end-to-end           — p50 / p95 in ms
  - Success rate                       — successful / total samples

Suites (prompt families):
  - english_chat   : short ENGLISH user query (chat-style)
  - assamese_chat  : short ASSAMESE user query (chat-style)
  - long_form      : long-form content generation (~1500 words)

Provider matrix (uses the SAME client modules production traffic uses,
so the benchmark exercises the real code path including CF AI Gateway
routing, retries and BYOK):
  english_chat / long_form:
    azure_openai          — providers.azure_openai.stream_chat (gpt-4.1-mini default)
    workers_ai_oss20      — providers.cloudflare_ai.chat_stream(model_key="chat_gpt_oss")
    workers_ai_oss120     — providers.cloudflare_ai.chat_stream(model_key="chat_long")
    vertex_chat           — vertex_chat.stream_chat (Vertex/Gemini path, currently
                            configured to llama-3.3-70b-instruct via Workers AI)
  assamese_chat:
    sarvam                — sarvam-m via the sarvam_llm_client streaming pipeline
    vertex_chat           — vertex_chat.stream_chat (same Vertex/Gemini path)

Providers that fail to initialise (missing keys, unreachable gateway,
etc.) are recorded as ``skipped`` with a short reason string instead of
crashing the run, so the benchmark still produces a useful report in
partial-credentials environments.

CLI::

    python -m scripts.bench_llm_providers \\
        --runs 5 --warm 1 \\
        --suites english_chat,assamese_chat,long_form \\
        --providers azure_openai,workers_ai_oss20,...

Output::

    bench_results/<UTC-ts>_provider_speed.json
    bench_results/<UTC-ts>_provider_speed.md
    bench_results/latest.json   (symlink-style copy for the admin endpoint)
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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

# Make sure we can ``from providers import …`` regardless of where the
# script is invoked from (CI, dev shell, cron). The benchmark lives in
# scripts/ so we add the parent (artifacts/syrabit-backend/) to sys.path.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

logger = logging.getLogger("bench.llm_providers")

RESULTS_DIR = _BACKEND_ROOT / "bench_results"

# ── Prompt suites ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are Syra, a concise factual study mentor. Answer accurately and stop."
)
SYSTEM_PROMPT_INDIC = (
    "তুমি Syra — এজন সংক্ষিপ্ত আৰু সঠিক অসমীয়া অধ্যয়ন সহায়ক।"
    " সম্পূৰ্ণ উত্তৰ অসমীয়াত দিয়া।"
)

SUITES: dict[str, dict[str, Any]] = {
    "english_chat": {
        "label": "English chat",
        "system": SYSTEM_PROMPT,
        "user": "Explain photosynthesis in 3 sentences.",
        "max_tokens": 256,
    },
    "assamese_chat": {
        "label": "Assamese chat",
        "system": SYSTEM_PROMPT_INDIC,
        "user": "ফটোসিনথেছিছ ৩টা বাক্যত বুজাই দিয়া।",
        "max_tokens": 384,
        "response_lang": "as",
    },
    "long_form": {
        "label": "Long-form content (~1500 words)",
        "system": SYSTEM_PROMPT,
        "user": (
            "Write detailed exam notes on Newton's three laws of motion in "
            "approximately 1500 words. Include the statement of each law, the "
            "underlying intuition, two real-world examples per law, the SI "
            "units involved, and a worked numerical problem for the second law."
        ),
        "max_tokens": 2048,
    },
}


# ── Sample bookkeeping ────────────────────────────────────────────────────────

@dataclass
class Sample:
    ttft_ms: float
    total_ms: float
    output_tokens: int     # rough estimate via .split() length
    output_chars: int

    @property
    def tokens_per_sec(self) -> float:
        decode_ms = max(self.total_ms - self.ttft_ms, 1.0)
        return (self.output_tokens / decode_ms) * 1000.0


@dataclass
class ProviderResult:
    provider: str
    model: str
    # `samples` are warm runs (post warm-up). `cold_samples` are warm-up runs
    # we successfully timed — they isolate cold-start TTFT (gateway connect,
    # CF cache miss, Lambda spin-up, etc.) from steady-state warm TTFT.
    samples: list[Sample] = field(default_factory=list)
    cold_samples: list[Sample] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    skipped_reason: Optional[str] = None

    def to_dict(self) -> dict:
        if self.skipped_reason:
            return {
                "provider": self.provider,
                "model": self.model,
                "skipped": True,
                "reason": self.skipped_reason,
                "samples": 0,
            }
        cold_ttft = sorted(s.ttft_ms for s in self.cold_samples)
        cold_block = {
            "cold_samples": len(self.cold_samples),
            "ttft_cold_p50_ms": _percentile(cold_ttft, 50) if cold_ttft else None,
            "ttft_cold_p95_ms": _percentile(cold_ttft, 95) if cold_ttft else None,
        }
        if not self.samples:
            return {
                "provider": self.provider,
                "model": self.model,
                "samples": 0,
                "success_rate": 0.0,
                "failures": self.failures[:5],
                **cold_block,
            }
        ttft = sorted(s.ttft_ms for s in self.samples)
        total = sorted(s.total_ms for s in self.samples)
        toksec = sorted(s.tokens_per_sec for s in self.samples)
        attempted = len(self.samples) + len(self.failures)
        return {
            "provider": self.provider,
            "model": self.model,
            "samples": len(self.samples),
            "attempted": attempted,
            "success_rate": round(len(self.samples) / attempted, 3) if attempted else 0.0,
            # Warm (steady-state) TTFT — what users see on the Nth message.
            "ttft_p50_ms": _percentile(ttft, 50),
            "ttft_p95_ms": _percentile(ttft, 95),
            "ttft_warm_p50_ms": _percentile(ttft, 50),
            "ttft_warm_p95_ms": _percentile(ttft, 95),
            "total_p50_ms": _percentile(total, 50),
            "total_p95_ms": _percentile(total, 95),
            "tokens_per_sec_p50": round(_percentile(toksec, 50), 2),
            "mean_output_chars": round(statistics.mean(s.output_chars for s in self.samples)),
            "failures": self.failures[:5],
            **cold_block,
        }


def _percentile(sorted_values: list[float], pct: int) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return round(sorted_values[0], 1)
    k = (pct / 100) * (len(sorted_values) - 1)
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = k - lo
    return round(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac, 1)


# ── Provider adapters ─────────────────────────────────────────────────────────
# Each adapter returns a coroutine that yields (ttft_ms, total_ms, text)
# given the messages and max_tokens. Adapters must raise on failure.

async def _stream_and_time(
    stream_factory: Callable[[], AsyncIterator[str]],
) -> tuple[float, float, str]:
    """Consume a token stream and return (ttft_ms, total_ms, full_text)."""
    t0 = time.perf_counter()
    ttft_ms: Optional[float] = None
    parts: list[str] = []
    async for token in stream_factory():
        # Some providers yield non-str events (ints, dicts, None for keep-alives).
        # Coerce defensively so "".join() never trips on mixed payloads.
        if token is None:
            continue
        if not isinstance(token, str):
            try:
                token = str(token)
            except Exception:
                continue
        if ttft_ms is None and token:
            ttft_ms = (time.perf_counter() - t0) * 1000.0
        parts.append(token)
    total_ms = (time.perf_counter() - t0) * 1000.0
    if ttft_ms is None:
        # Stream produced no tokens — treat first-token == total.
        ttft_ms = total_ms
    return ttft_ms, total_ms, "".join(parts)


async def _run_azure_openai(messages: list, max_tokens: int, **_):
    """Bench Azure OpenAI through the production candidate chain.

    The provider's ``ENABLED`` flag is true when *either* the CF AI Gateway
    azure-openai slug is registered *or* AZURE_OPENAI_ENDPOINT + at least one
    of AZURE_OPENAI_KEY_1/2 are configured (Task #290 direct-mode failover).
    A fully unconfigured environment is reported as a clean skip.
    """
    from providers import azure_openai
    if not azure_openai.ENABLED:
        raise RuntimeError(
            "azure_openai disabled (no CF gateway slug AND no "
            "AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_KEY_1/2)"
        )
    return await _stream_and_time(
        lambda: azure_openai.stream_chat(messages, max_tokens=max_tokens),
    )


def _cf_oss_stream(model: str, messages: list, max_tokens: int):
    """Stream from Workers AI gpt-oss via the OpenAI-compatible v1 endpoint.

    Workers AI exposes ``/ai/v1/chat/completions`` (OpenAI-compat) for the
    ``@cf/openai/gpt-oss-*`` family.  These are reasoning models that emit
    text on TWO channels: ``delta.content`` (final answer) and
    ``delta.reasoning_content`` (chain-of-thought).  When ``content`` is
    empty/null we fall back to ``reasoning_content`` so the bench measures
    actual model output instead of recording an empty-completion failure.
    """
    import httpx
    acct = os.environ.get("CF_AI_GATEWAY_ACCOUNT_ID", "").strip()
    tok  = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not (acct and tok):
        raise RuntimeError(
            f"workers_ai_oss disabled (CF_AI_GATEWAY_ACCOUNT_ID / CLOUDFLARE_API_TOKEN missing)"
        )
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    async def _gen():
        async with httpx.AsyncClient(timeout=180.0) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise RuntimeError(
                        f"workers_ai_oss HTTP {resp.status_code} — "
                        f"{body.decode(errors='replace')[:200]}"
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                        delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                        token = delta.get("content") or delta.get("reasoning_content") or ""
                        if token:
                            yield token
                    except json.JSONDecodeError:
                        continue

    return _gen


async def _run_cf_chat_oss20(messages: list, max_tokens: int, **_):
    return await _stream_and_time(_cf_oss_stream("@cf/openai/gpt-oss-20b", messages, max_tokens))


async def _run_cf_chat_oss120(messages: list, max_tokens: int, **_):
    return await _stream_and_time(_cf_oss_stream("@cf/openai/gpt-oss-120b", messages, max_tokens))


async def _run_vertex_chat(messages: list, max_tokens: int, **_):
    """Stream from the production Vertex/Gemini chat path (``vertex_chat.py``).

    The module currently delegates streaming to Workers AI
    (``@cf/meta/llama-3.3-70b-instruct-fp8-fast``) with the same public
    interface used by ``llm.py`` and ``routes/edu_study.py``.  Benchmarking
    via this module — instead of hitting Google's endpoint directly —
    measures the exact code path production traffic takes for the
    ``vertex_chat`` SLM slot.
    """
    import vertex_chat as _vc
    if not _vc.is_configured():
        raise RuntimeError(
            "vertex_chat disabled (CF_AI_GATEWAY_ACCOUNT_ID / CLOUDFLARE_API_TOKEN missing)"
        )
    return await _stream_and_time(
        lambda: _vc.stream_chat(messages, max_tokens=max_tokens, temperature=0.1),
    )


# Task #347 — _run_bedrock_nova removed; AWS Bedrock has been
# decommissioned and providers/bedrock.py is gone.


async def _run_sarvam(messages: list, max_tokens: int, response_lang: str = "as", **_):
    # Direct streaming call against sarvam_llm_client. We avoid importing
    # the heavyweight ``llm`` module so the bench script can run in a thin
    # subprocess without booting the whole FastAPI stack.
    from deps import sarvam_llm_client, sarvam_llm_client_direct
    client = sarvam_llm_client_direct or sarvam_llm_client
    if client is None:
        raise RuntimeError("sarvam_llm_client not initialised (SARVAM_API_KEY missing)")
    payload = {
        "model": "sarvam-m",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "stream": True,
    }
    if response_lang == "as":
        payload["response_language"] = "as-IN"

    async def _gen():
        async with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                raise RuntimeError(f"sarvam HTTP {resp.status_code} — {body.decode()[:200]}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                    delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                    token = delta.get("content") or ""
                    if token:
                        yield token
                except Exception:
                    continue

    return await _stream_and_time(_gen)


# Adapter registry: ``provider_id -> (callable, model_label)``.
ADAPTERS: dict[str, tuple[Callable[..., Awaitable[tuple[float, float, str]]], str]] = {
    "azure_openai":      (_run_azure_openai,   os.environ.get("AZURE_OPENAI_DEPLOYMENT", os.environ.get("AZURE_OPENAI_MODEL", "gpt-4o-mini"))),
    "workers_ai_oss20":  (_run_cf_chat_oss20,  "@cf/openai/gpt-oss-20b"),
    "workers_ai_oss120": (_run_cf_chat_oss120, "@cf/openai/gpt-oss-120b"),
    "vertex_chat":            (_run_vertex_chat,            "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
    "sarvam":                 (_run_sarvam,                 "sarvam-m"),
    # Task #347 — bedrock_nova entry removed (provider decommissioned).
}

SUITE_PROVIDER_DEFAULTS: dict[str, list[str]] = {
    "english_chat":  ["azure_openai", "workers_ai_oss20", "vertex_chat"],
    "assamese_chat": ["azure_openai", "sarvam", "vertex_chat"],
    "long_form":     ["azure_openai", "workers_ai_oss120", "vertex_chat"],
}


# ── Bench loop ────────────────────────────────────────────────────────────────

async def _run_one_provider(
    provider_id: str,
    suite_id: str,
    suite: dict,
    runs: int,
    warm: int,
) -> ProviderResult:
    adapter, model = ADAPTERS[provider_id]
    result = ProviderResult(provider=provider_id, model=model)

    messages = [
        {"role": "system", "content": suite["system"]},
        {"role": "user",   "content": suite["user"]},
    ]
    max_tokens = suite["max_tokens"]
    extras = {"response_lang": suite.get("response_lang", "")}

    # Warm-ups: timed but bookkept separately so we can report cold-start
    # TTFT (gateway connect, CF cache miss, Lambda spin-up) distinctly from
    # the steady-state warm TTFT users see on the Nth message.
    warmup_failures = 0
    for i in range(warm):
        try:
            ttft_ms, total_ms, text = await adapter(messages, max_tokens, **extras)
            tokens = len((text or "").split())
            if tokens > 0 and (text or "").strip():
                result.cold_samples.append(Sample(
                    ttft_ms=ttft_ms,
                    total_ms=total_ms,
                    output_tokens=tokens,
                    output_chars=len(text),
                ))
                logger.info(
                    "[bench] %-15s %-18s warm %d/%d — TTFT %5.0fms total %6.0fms (cold)",
                    suite_id, provider_id, i + 1, warm, ttft_ms, total_ms,
                )
        except Exception as exc:
            warmup_failures += 1
            reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            logger.warning("[bench] %s/%s warm-up #%d failed — %s",
                           suite_id, provider_id, i + 1, reason)
            # If ALL warm-ups failed, the provider is unreachable — bail.
            if warm > 0 and warmup_failures == warm:
                result.skipped_reason = reason
                return result

    for i in range(runs):
        try:
            ttft_ms, total_ms, text = await adapter(messages, max_tokens, **extras)
            tokens = len((text or "").split())
            if tokens == 0 or not (text or "").strip():
                # Empty completion — treat as failed sample so we don't inflate
                # tokens/sec or success_rate with phantom successes.
                reason = f"empty_completion (chars={len(text or '')})"
                result.failures.append(reason)
                logger.warning("[bench] %s/%s run %d empty completion — %s",
                               suite_id, provider_id, i + 1, reason)
                continue
            result.samples.append(Sample(
                ttft_ms=ttft_ms,
                total_ms=total_ms,
                output_tokens=tokens,
                output_chars=len(text),
            ))
            logger.info(
                "[bench] %-15s %-18s run %d/%d — TTFT %5.0fms total %6.0fms (%d chars)",
                suite_id, provider_id, i + 1, runs, ttft_ms, total_ms, len(text),
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            result.failures.append(reason)
            logger.warning("[bench] %s/%s run %d failed — %s",
                           suite_id, provider_id, i + 1, reason)

    if not result.samples and result.failures and not result.skipped_reason:
        # All runs failed — surface the first reason as skipped_reason so the
        # report can render a single-line excuse instead of N copies.
        result.skipped_reason = result.failures[0]

    return result


def _winner(suite_results: dict[str, dict], metric: str = "ttft_p50_ms") -> Optional[dict]:
    candidates = [
        (pid, r[metric]) for pid, r in suite_results.items()
        if not r.get("skipped") and r.get("samples", 0) > 0 and metric in r
    ]
    if not candidates:
        return None
    pid, value = min(candidates, key=lambda kv: kv[1])
    return {"provider": pid, "metric": metric, "value": value}


# ── Report writers ────────────────────────────────────────────────────────────

def _markdown_report(report: dict) -> str:
    lines: list[str] = []
    lines.append(f"# LLM provider speed benchmark — {report['generated_at']}")
    lines.append("")
    lines.append(
        f"**Runs/suite:** {report['runs_per_suite']} · "
        f"**Warm-ups:** {report['warmups']} · "
        f"**Host:** `{report.get('host', 'unknown')}`"
    )
    lines.append("")

    # ── Top-level winner-by-metric summary across all suites ──
    metric_labels: list[tuple[str, str, bool]] = [
        ("ttft_cold_p50_ms",  "TTFT cold p50",  True),   # lower is better
        ("ttft_warm_p50_ms",  "TTFT warm p50",  True),
        ("ttft_warm_p95_ms",  "TTFT warm p95",  True),
        ("total_p50_ms",      "Total p50",      True),
        ("tokens_per_sec_p50","tok/s p50",      False),  # higher is better
    ]
    lines.append("## Winner by metric (across all suites)")
    lines.append("")
    lines.append("| Suite | " + " | ".join(lbl for _, lbl, _ in metric_labels) + " |")
    lines.append("|---" * (len(metric_labels) + 1) + "|")
    for suite_id, suite in report["suites"].items():
        row = [f"`{suite_id}`"]
        for key, _, lower_better in metric_labels:
            cands = [
                (pid, r[key]) for pid, r in suite["providers"].items()
                if not r.get("skipped") and r.get("samples", 0) > 0
                and r.get(key) is not None
            ]
            if not cands:
                row.append("—")
                continue
            pid, val = (min if lower_better else max)(cands, key=lambda kv: kv[1])
            unit = "ms" if key.endswith("_ms") else ""
            row.append(f"`{pid}` ({val:.0f}{unit})" if unit else f"`{pid}` ({val:.1f})")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    for suite_id, suite in report["suites"].items():
        lines.append(f"## {suite['label']}  (`{suite_id}`)")
        lines.append("")
        lines.append(f"_Prompt:_ `{suite['prompt'][:120]}{'…' if len(suite['prompt']) > 120 else ''}`")
        lines.append("")
        lines.append("| Provider | Model | TTFT cold p50 | TTFT warm p50 | TTFT warm p95 | Total p50 | tok/s p50 | Success | Samples |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
        for pid, r in suite["providers"].items():
            if r.get("skipped"):
                lines.append(
                    f"| `{pid}` | `{r.get('model', '?')}` | — | — | — | — | — | _skipped_ | "
                    f"_{r.get('reason', '')[:80]}_ |"
                )
                continue
            if not r.get("samples"):
                lines.append(
                    f"| `{pid}` | `{r.get('model', '?')}` | — | — | — | — | — | 0% | 0 |"
                )
                continue
            cold = r.get("ttft_cold_p50_ms")
            cold_s = f"{cold:.0f}ms" if cold is not None else "—"
            lines.append(
                f"| `{pid}` | `{r['model']}` | "
                f"{cold_s} | "
                f"{r['ttft_warm_p50_ms']:.0f}ms | {r['ttft_warm_p95_ms']:.0f}ms | "
                f"{r['total_p50_ms']:.0f}ms | {r['tokens_per_sec_p50']:.1f} | "
                f"{r['success_rate']*100:.0f}% | {r['samples']} |"
            )
        winner = suite.get("winner")
        if winner:
            lines.append("")
            lines.append(
                f"**Winner (lowest TTFT p50):** `{winner['provider']}` — "
                f"{winner['value']:.0f}ms"
            )
        lines.append("")

    return "\n".join(lines)


def _write_outputs(
    report: dict,
    out_dir: Path,
    *,
    json_override: Optional[Path] = None,
    md_override: Optional[Path] = None,
) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = report["generated_at"].replace(":", "").replace("-", "").replace(".", "_")
    json_path = (json_override or (out_dir / f"{stamp}_provider_speed.json")).resolve()
    md_path = (md_override or (out_dir / f"{stamp}_provider_speed.md")).resolve()
    latest = (out_dir / "latest.json").resolve()
    for p in (json_path, md_path, latest):
        p.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(_markdown_report(report), encoding="utf-8")
    latest.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, md_path, latest


# ── Main ──────────────────────────────────────────────────────────────────────

async def run_benchmark(
    *,
    runs: int,
    warm: int,
    suites: list[str],
    providers_filter: Optional[set[str]] = None,
    out_dir: Path = RESULTS_DIR,
) -> dict:
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runs_per_suite": runs,
        "warmups": warm,
        "host": os.environ.get("HOSTNAME", "local"),
        "suites": {},
    }

    for suite_id in suites:
        suite = SUITES[suite_id]
        provider_ids = [
            pid for pid in SUITE_PROVIDER_DEFAULTS[suite_id]
            if providers_filter is None or pid in providers_filter
        ]
        provider_results: dict[str, dict] = {}
        for pid in provider_ids:
            r = await _run_one_provider(pid, suite_id, suite, runs, warm)
            provider_results[pid] = r.to_dict()

        winner = _winner(provider_results, metric="ttft_p50_ms")
        report["suites"][suite_id] = {
            "label": suite["label"],
            "prompt": suite["user"],
            "max_tokens": suite["max_tokens"],
            "providers": provider_results,
            "winner": winner,
        }

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Head-to-head LLM speed benchmark")
    parser.add_argument("--runs", type=int, default=5,
                        help="Sampled runs per (suite, provider). Default 5.")
    parser.add_argument("--warm", type=int, default=1,
                        help="Warm-up runs (not counted). Default 1.")
    parser.add_argument(
        "--suites", default="english_chat,assamese_chat,long_form",
        help="Comma-separated suite ids. Default all three.",
    )
    parser.add_argument(
        "--providers", default="",
        help="Comma-separated provider ids to filter (default: all in matrix).",
    )
    parser.add_argument(
        "--output-dir", default=str(RESULTS_DIR),
        help="Directory for the timestamped JSON+MD pair and latest.json. Default bench_results/.",
    )
    parser.add_argument(
        "--output", default=None,
        help="Explicit JSON output path (overrides the default <ts>_provider_speed.json).",
    )
    parser.add_argument(
        "--markdown", default=None,
        help="Explicit Markdown output path (overrides the default <ts>_provider_speed.md).",
    )
    parser.add_argument("--quiet", action="store_true", help="Reduce log verbosity.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    suites = [s.strip() for s in args.suites.split(",") if s.strip()]
    unknown = [s for s in suites if s not in SUITES]
    if unknown:
        parser.error(f"unknown suites: {unknown}. Known: {list(SUITES)}")

    providers_filter: Optional[set[str]] = None
    if args.providers.strip():
        providers_filter = {p.strip() for p in args.providers.split(",") if p.strip()}
        unknown_p = providers_filter - set(ADAPTERS)
        if unknown_p:
            parser.error(f"unknown providers: {unknown_p}. Known: {list(ADAPTERS)}")

    out_dir = Path(args.output_dir).resolve()
    report = asyncio.run(run_benchmark(
        runs=args.runs, warm=args.warm,
        suites=suites, providers_filter=providers_filter,
        out_dir=out_dir,
    ))
    json_override = Path(args.output).resolve() if args.output else None
    md_override = Path(args.markdown).resolve() if args.markdown else None
    json_path, md_path, latest = _write_outputs(
        report, out_dir,
        json_override=json_override,
        md_override=md_override,
    )
    print(f"\n✔ Wrote JSON   → {json_path}")
    print(f"✔ Wrote Markdown → {md_path}")
    print(f"✔ Wrote latest  → {latest}\n")
    print(_markdown_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
