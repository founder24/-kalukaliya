"""Recall benchmark for the grounded-answer pipeline.

Given a hand-labelled fixture of (query, expected-source) pairs, this
module computes recall@K over the citation list that the pipeline would
surface, so retrieval regressions can be caught before students notice.

Two retrievers are supported:

* ``offline`` — feeds each case's embedded ``offline_corpus`` (web
  results, internal chapters, page context) directly into
  ``grounded_answer._build_citations``. Deterministic, hermetic, runs
  in CI with zero network.

* ``live`` — calls the real ``rag.web_search_with_fallback`` and, if a
  subject id is provided, ``rag._fetch_internal_chapters``. Useful for
  nightly monitoring against production indices.

Usage (CLI)::

    python -m bench.grounded_recall                 # offline, pretty JSON
    python -m bench.grounded_recall --live          # hit real retrievers
    python -m bench.grounded_recall --save-results  # archive to results/
    python -m bench.grounded_recall --compare baseline  # gate vs baseline

A tiny admin GET at /api/admin/grounded-recall/latest reads the newest
JSON file under results/ so the metric is observable in the admin UI.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_BENCH_DIR = Path(__file__).resolve().parent
FIXTURE_PATH = _BENCH_DIR / "fixtures" / "grounded_recall.json"
BASELINE_PATH = _BENCH_DIR / "fixtures" / "baseline.json"
RESULTS_DIR = _BENCH_DIR / "results"

K_VALUES = (1, 3, 5)


# ───────────────────────── Data types ─────────────────────────

@dataclass
class BenchCase:
    id: str
    query: str
    context: dict
    expected: dict
    offline_corpus: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "BenchCase":
        return cls(
            id=d["id"],
            query=d["query"],
            context=d.get("context", {}),
            expected=d.get("expected", {}),
            offline_corpus=d.get("offline_corpus", {}),
        )


@dataclass
class CaseResult:
    id: str
    query: str
    citations_count: int
    first_match_rank: Optional[int]  # 1-indexed; None = no match in any citation
    matched: bool
    elapsed_ms: int

    is_adversarial: bool = False
    allow_weak: int = 0  # adversarial-only quality floor (max tolerated citations)

    def recall_at(self, k: int) -> bool:
        # Adversarial negatives are "recalled" at any K when the retriever
        # correctly surfaced *no* matching citation — i.e. matched=True via
        # the no-match path.
        if self.is_adversarial:
            return self.matched
        return self.first_match_rank is not None and self.first_match_rank <= k


@dataclass
class BenchReport:
    started_at: str
    retriever: str
    total_cases: int
    metrics: dict  # {"recall@1": 0.82, "recall@3": ..., "recall@5": ..., ...}
    per_case: list[dict]
    mean_citation_count: float
    mean_latency_ms: float

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "retriever": self.retriever,
            "total_cases": self.total_cases,
            "metrics": self.metrics,
            "mean_citation_count": self.mean_citation_count,
            "mean_latency_ms": self.mean_latency_ms,
            "per_case": self.per_case,
        }


# ───────────────────────── Matching ─────────────────────────

def _domain_of(url: str) -> str:
    try:
        d = (urlparse(url).hostname or "").lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return ""


def citation_matches_expected(citation: dict, expected: dict) -> bool:
    """Return True if the citation satisfies *any* of the expected patterns."""
    cit_url = (citation.get("url") or "").lower()
    cit_domain = (citation.get("domain") or _domain_of(citation.get("url", ""))).lower()
    cit_anchor = (citation.get("anchor") or "").lower()

    # Domain match: exact or subdomain of expected.
    for d in expected.get("domains", []) or []:
        d = d.lower().lstrip(".")
        if cit_domain == d or cit_domain.endswith("." + d):
            return True

    # URL substring.
    for s in expected.get("url_substrings", []) or []:
        if s and s.lower() in cit_url:
            return True

    # Chapter slug (internal retrieval).
    for slug in expected.get("chapter_slugs", []) or []:
        if slug and slug.lower() == cit_anchor:
            return True

    return False


# ───────────────────────── Retrievers ─────────────────────────

def _build_offline_citations(case: BenchCase) -> list[dict]:
    """Feed the case's offline corpus through the real citation builder."""
    # Imported here so the module can be imported without the full backend
    # dependency graph (e.g. from CI that doesn't install every extra).
    from grounded_answer import _build_citations  # type: ignore

    corpus = case.offline_corpus or {}
    page_ctx = corpus.get("page_context") or None
    internal = corpus.get("internal_chapters") or []
    web = corpus.get("web_results") or []
    return _build_citations(web, internal, page_ctx)


async def _build_live_citations(case: BenchCase) -> list[dict]:
    """Hit the real retrievers and build citations from what comes back."""
    from grounded_answer import _build_citations  # type: ignore
    from rag import web_search_with_fallback, _fetch_internal_chapters  # type: ignore
    from edu_reader import fetch_and_extract  # type: ignore
    from guardrails.web_safety import filter_web_results, score_text_kid_safety

    ctx = case.context or {}
    page_url = ctx.get("page_url", "")
    subject_id = ctx.get("subject_id", "")
    subject_name = ctx.get("subject_name", "")

    async def _maybe_page():
        if not page_url:
            return None
        try:
            return await fetch_and_extract(page_url, actor="bench", ip_hash="bench")
        except Exception:
            return None

    async def _maybe_internal():
        if not (subject_id or subject_name):
            return []
        try:
            return await _fetch_internal_chapters(
                case.query, subject_id=subject_id, subject_name=subject_name
            )
        except Exception:
            return []

    async def _maybe_web():
        try:
            return await web_search_with_fallback(
                case.query,
                board_name=ctx.get("board_name", ""),
                class_name=ctx.get("class_name", ""),
                subject_name=subject_name,
                chapter_name=ctx.get("chapter_name", ""),
            )
        except Exception:
            return []

    page_ctx, internal, web_raw = await asyncio.gather(
        _maybe_page(), _maybe_internal(), _maybe_web()
    )

    web_kept, _ = filter_web_results(web_raw or [])
    page_payload = None
    if page_ctx and page_ctx.get("ok"):
        safe, _, _ = score_text_kid_safety(page_ctx.get("text", ""))
        if safe:
            page_payload = page_ctx
    return _build_citations(web_kept, internal, page_payload)


# ───────────────────────── Runner ─────────────────────────

def _score_case(case: BenchCase, citations: list[dict], elapsed_ms: int) -> CaseResult:
    is_adversarial = bool(case.expected.get("none"))
    first_match: Optional[int] = None
    for i, cit in enumerate(citations, start=1):
        if citation_matches_expected(cit, case.expected):
            first_match = i
            break
    # Quality floor for adversarial negatives.
    #
    # The retriever ideally surfaces *zero* citations on a trick query, but in
    # production it often leaks 1-2 weak citations even for off-topic prompts
    # (low-confidence web hits, partial keyword matches, etc.). Treating any
    # output as a hard fail conflates "leaked one borderline source" with
    # "confidently hallucinated a full answer" — both are scored 0.
    #
    # ``expected.allow_weak`` (default 0) lets a case soft-tolerate up to N
    # citations as still "correctly abstained". Default 0 preserves the old
    # strict behaviour for cases that don't opt in.
    try:
        allow_weak = max(0, int(case.expected.get("allow_weak", 0) or 0))
    except (TypeError, ValueError):
        allow_weak = 0
    if is_adversarial:
        matched = len(citations) <= allow_weak
    else:
        matched = first_match is not None
    return CaseResult(
        id=case.id,
        query=case.query,
        citations_count=len(citations),
        first_match_rank=first_match,
        matched=matched,
        elapsed_ms=elapsed_ms,
        is_adversarial=is_adversarial,
        allow_weak=allow_weak,
    )


async def run_benchmark(
    cases: list[BenchCase],
    *,
    retriever: str = "offline",
) -> BenchReport:
    """Run every case and return an aggregate report."""
    results: list[CaseResult] = []
    for case in cases:
        t0 = time.perf_counter()
        try:
            if retriever == "offline":
                citations = _build_offline_citations(case)
            elif retriever == "live":
                citations = await _build_live_citations(case)
            else:
                raise ValueError(f"unknown retriever {retriever!r}")
        except Exception as e:
            logger.warning(f"[bench] case {case.id!r} failed: {e}")
            citations = []
        elapsed = int((time.perf_counter() - t0) * 1000)
        results.append(_score_case(case, citations, elapsed))

    total = len(results) or 1
    # Split positives vs adversarial negatives so recall@K reflects only
    # the retriever's ability to surface real sources, not its no-match
    # behaviour on trick queries (which has its own metric).
    pos_results = [r for r in results if not r.is_adversarial]
    adv_results = [r for r in results if r.is_adversarial]
    pos_total = len(pos_results) or 1

    metrics: dict[str, float] = {}
    for k in K_VALUES:
        hits = sum(1 for r in pos_results if r.recall_at(k))
        metrics[f"recall@{k}"] = round(hits / pos_total, 4)
    metrics["match_rate"] = round(sum(1 for r in results if r.matched) / total, 4)

    if adv_results:
        adv_correct = sum(1 for r in adv_results if r.matched)
        # ``adversarial_no_match_rate`` honours each case's ``allow_weak``
        # floor (i.e. counts a case as correct when citation count is at or
        # below the declared tolerance). Kept under the historical key so
        # existing dashboards/baselines keep working.
        metrics["adversarial_no_match_rate"] = round(adv_correct / len(adv_results), 4)
        # Strict variant: fraction of adversarial cases with *zero* citations
        # surfaced. Always tighter than (or equal to) the floored rate; lets
        # the admin tile show how many trick queries the retriever fully
        # abstained on, independent of the per-case tolerance.
        adv_clean = sum(1 for r in adv_results if r.citations_count == 0)
        metrics["adversarial_clean_rate"] = round(adv_clean / len(adv_results), 4)
        # Mean citations leaked on adversarial cases — the signal we actually
        # want to drive down. Surfaces regressions where the floored pass-rate
        # stays at 1.0 but the retriever starts leaking more weak hits.
        metrics["adversarial_mean_citations"] = round(
            sum(r.citations_count for r in adv_results) / len(adv_results), 2
        )

    mean_cc = round(sum(r.citations_count for r in results) / total, 2)
    mean_lat = round(sum(r.elapsed_ms for r in results) / total, 2)

    return BenchReport(
        started_at=datetime.now(timezone.utc).isoformat(),
        retriever=retriever,
        total_cases=total,
        metrics=metrics,
        per_case=[
            {
                "id": r.id,
                "query": r.query,
                "citations_count": r.citations_count,
                "first_match_rank": r.first_match_rank,
                "matched": r.matched,
                "elapsed_ms": r.elapsed_ms,
                "adversarial": r.is_adversarial,
                "allow_weak": r.allow_weak,
            }
            for r in results
        ],
        mean_citation_count=mean_cc,
        mean_latency_ms=mean_lat,
    )


# ───────────────────────── Fixture / baseline I/O ─────────────────────────

def load_cases(path: Path = FIXTURE_PATH, *, language: Optional[str] = None) -> list[BenchCase]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cases = [BenchCase.from_dict(c) for c in data["cases"]]
    if language:
        lang = language.lower()
        cases = [c for c in cases if (c.context or {}).get("language", "").lower() == lang]
    return cases


def _baseline_path_for(language: Optional[str]) -> Path:
    if not language:
        return BASELINE_PATH
    return _BENCH_DIR / "fixtures" / f"baseline_{language.lower()}.json"


def load_baseline(path: Optional[Path] = None, *, language: Optional[str] = None) -> Optional[dict]:
    p = path if path is not None else _baseline_path_for(language)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _latest_filename(language: Optional[str]) -> str:
    if not language:
        return "latest.json"
    return f"latest_{language.lower()}.json"


def save_report(
    report: BenchReport,
    results_dir: Path = RESULTS_DIR,
    *,
    language: Optional[str] = None,
) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lang_tag = f"-{language.lower()}" if language else ""
    filename = results_dir / f"grounded_recall-{report.retriever}{lang_tag}-{ts}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2)
    # also update "latest[ _<lang>].json" for easy admin read
    latest = results_dir / _latest_filename(language)
    with open(latest, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2)
    return filename


def find_latest_result(
    results_dir: Path = RESULTS_DIR,
    *,
    language: Optional[str] = None,
) -> Optional[dict]:
    latest = results_dir / _latest_filename(language)
    if latest.exists():
        with open(latest, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# ───────────────────────── CLI ─────────────────────────

def _format_report(report: BenchReport, baseline: Optional[dict]) -> str:
    lines = [
        f"Grounded-answer recall benchmark — retriever={report.retriever}",
        f"  cases: {report.total_cases}",
        f"  mean citations per case: {report.mean_citation_count}",
        f"  mean latency: {report.mean_latency_ms} ms",
        "  metrics:",
    ]
    for k, v in report.metrics.items():
        line = f"    {k:12s}  {v:.4f}"
        if baseline and "metrics" in baseline and k in baseline["metrics"]:
            delta = v - baseline["metrics"][k]
            sign = "+" if delta >= 0 else ""
            line += f"   ({sign}{delta:+.4f} vs baseline {baseline['metrics'][k]:.4f})"
        lines.append(line)
    misses = [c for c in report.per_case if not c["matched"]]
    if misses:
        lines.append(f"  misses ({len(misses)}):")
        for m in misses[:10]:
            lines.append(f"    - {m['id']}: {m['query'][:60]}")
        if len(misses) > 10:
            lines.append(f"    … and {len(misses) - 10} more")
    return "\n".join(lines)


def _main_cli() -> int:
    _BACKEND = _BENCH_DIR.parent
    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))

    parser = argparse.ArgumentParser(description="Grounded-answer recall benchmark")
    parser.add_argument("--live", action="store_true", help="Use live retrievers instead of the offline corpus")
    parser.add_argument("--fixture", default=str(FIXTURE_PATH))
    parser.add_argument("--language", default=None, help="Filter to cases whose context.language matches (e.g. 'as' for Assamese)")
    parser.add_argument("--save-results", action="store_true", help="Archive results to bench/results/")
    parser.add_argument("--compare-baseline", action="store_true", help="Compare against bench/fixtures/baseline[ _<lang>].json")
    parser.add_argument("--gate", type=float, default=None, help="Exit non-zero if recall@5 drops below baseline by more than GATE")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON to stdout")
    args = parser.parse_args()

    language = (args.language or "").strip().lower() or None
    cases = load_cases(Path(args.fixture), language=language)
    if not cases:
        print(
            f"No cases matched filter language={language!r}", file=sys.stderr
        )
        return 1
    retriever = "live" if args.live else "offline"
    report = asyncio.run(run_benchmark(cases, retriever=retriever))

    baseline = load_baseline(language=language) if args.compare_baseline or args.gate is not None else None

    if args.save_results:
        saved = save_report(report, language=language)
        print(f"Saved results → {saved}", file=sys.stderr)

    if args.json:
        json.dump(report.to_dict(), sys.stdout, indent=2)
        print()
    else:
        print(_format_report(report, baseline))

    # Structured log line (nightly job can grep this)
    logger.info(
        "[bench.grounded_recall] metrics=%s retriever=%s cases=%d mean_latency_ms=%.1f",
        json.dumps(report.metrics), report.retriever, report.total_cases, report.mean_latency_ms,
    )

    if args.gate is not None and baseline:
        b5 = baseline.get("metrics", {}).get("recall@5")
        if b5 is not None:
            drop = b5 - report.metrics["recall@5"]
            if drop > args.gate:
                print(
                    f"GATE FAILED: recall@5 dropped {drop:.4f} (> {args.gate}). "
                    f"current={report.metrics['recall@5']:.4f} baseline={b5:.4f}",
                    file=sys.stderr,
                )
                return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main_cli())


# ───────────────────────── Nightly run + alerting ─────────────────────────
#
# Task #587: scheduler that runs the bench against the *live* retrievers
# once per day, persists ``results/latest.json`` (so the admin tile shows
# the production number rather than the committed offline baseline), and
# fires an admin alert when recall@5 drops more than ``gate`` versus the
# committed baseline.
#
# Two entry points:
#
#   * ``run_and_alert_live(...)`` — single-shot helper. Used by the
#     in-process loop below *and* by the standalone CLI / GH Action so a
#     gate failure path is identical regardless of trigger.
#   * ``_grounded_recall_nightly_loop()`` — long-running asyncio task
#     wired into ``server.py`` lifespan. Polls every 5 min; only the
#     replica that wins an atomic CAS on ``db.job_locks`` actually runs
#     the bench, so multi-worker deployments don't N×-page or N×-bench.

_GROUNDED_RECALL_LOCK_ID = "grounded_recall_nightly_marker"
_GROUNDED_RECALL_LAST_RUN_KEY = "last_run_tag"
_GROUNDED_RECALL_LOOP_SLEEP_S = 5 * 60   # poll every 5 minutes
_GROUNDED_RECALL_WINDOW_MINUTES = 30     # ±30 min around target hour
_GROUNDED_RECALL_DEFAULT_GATE = 0.05     # max allowed recall@5 drop
_GROUNDED_RECALL_DEFAULT_HOUR_UTC = 3    # 03:00 UTC = 08:30 IST


def _bench_enabled() -> bool:
    val = (os.environ.get("GROUNDED_RECALL_NIGHTLY_ENABLED", "true") or "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _bench_target_hour_utc() -> int:
    try:
        h = int(os.environ.get("GROUNDED_RECALL_NIGHTLY_HOUR_UTC", str(_GROUNDED_RECALL_DEFAULT_HOUR_UTC)))
    except (TypeError, ValueError):
        h = _GROUNDED_RECALL_DEFAULT_HOUR_UTC
    return max(0, min(h, 23))


def _bench_gate() -> float:
    try:
        g = float(os.environ.get("GROUNDED_RECALL_NIGHTLY_GATE", str(_GROUNDED_RECALL_DEFAULT_GATE)))
    except (TypeError, ValueError):
        g = _GROUNDED_RECALL_DEFAULT_GATE
    return max(0.0, min(g, 1.0))


def _bench_run_tag(now_utc: datetime) -> str:
    return now_utc.strftime("%Y-%m-%d")


def _should_run_grounded_recall_now(now_utc: datetime, last_run_tag: str) -> bool:
    if not _bench_enabled():
        return False
    target_hour = _bench_target_hour_utc()
    minutes_from_target = (now_utc.hour - target_hour) * 60 + now_utc.minute
    if abs(minutes_from_target) > _GROUNDED_RECALL_WINDOW_MINUTES:
        return False
    return _bench_run_tag(now_utc) != (last_run_tag or "")


def _format_alert_body(
    report: BenchReport,
    baseline: dict,
    drop: float,
    gate: float,
    *,
    language: Optional[str] = None,
    recall_gate_failed: bool = True,
    leak_gate_failed: bool = False,
) -> str:
    """Plain-text body for the admin alert. The Slack/Email dispatcher
    wraps this with the rich formatter; we keep the diff + miss list
    here so the on-call admin sees the same diagnostic info regardless
    of channel."""
    cur = report.metrics
    base = baseline.get("metrics", {}) if baseline else {}
    if recall_gate_failed and leak_gate_failed:
        headline = (
            f"Nightly grounded-recall benchmark tripped recall AND leak gates "
            f"(recall gate {gate:.2%})."
        )
    elif leak_gate_failed and not recall_gate_failed:
        headline = (
            "Nightly grounded-recall benchmark tripped the adversarial leak "
            "gate (recall@5 still within bounds)."
        )
    else:
        headline = (
            f"Nightly grounded-recall benchmark regressed beyond the {gate:.2%} gate."
        )
    lines = [
        headline,
        "",
        f"Retriever: {report.retriever}   cases: {report.total_cases}",
        f"Mean citations/case: {report.mean_citation_count}   mean latency: {report.mean_latency_ms} ms",
        "",
        "Metrics (current → baseline, delta):",
    ]
    for k in ("recall@1", "recall@3", "recall@5", "match_rate"):
        cv = cur.get(k)
        bv = base.get(k)
        if cv is None or bv is None:
            continue
        d = cv - bv
        sign = "+" if d >= 0 else ""
        lines.append(f"  {k:12s} {cv:.4f} → {bv:.4f}  ({sign}{d:.4f})")
    # Adversarial leak signals — surface separately so on-call admins can
    # see weak-citation leakage even when the floored pass-rate hides it.
    for k in ("adversarial_clean_rate", "adversarial_mean_citations"):
        cv = cur.get(k)
        bv = base.get(k)
        if cv is None and bv is None:
            continue
        cv_s = f"{cv:.4f}" if isinstance(cv, (int, float)) else "n/a"
        bv_s = f"{bv:.4f}" if isinstance(bv, (int, float)) else "n/a"
        if isinstance(cv, (int, float)) and isinstance(bv, (int, float)):
            d = cv - bv
            sign = "+" if d >= 0 else ""
            lines.append(f"  {k:30s} {cv_s} → {bv_s}  ({sign}{d:.4f})")
        else:
            lines.append(f"  {k:30s} {cv_s} → {bv_s}")
    misses = [c for c in report.per_case if not c.get("matched")]
    if misses:
        lines.append("")
        lines.append(f"Misses ({len(misses)}):")
        for m in misses[:10]:
            q = (m.get("query") or "")[:80]
            lines.append(f"  - {m.get('id')}: {q}")
        if len(misses) > 10:
            lines.append(f"  … and {len(misses) - 10} more")
    # Top adversarial leakers — cases that returned the most weak citations
    # despite being trick queries with no syllabus answer.
    leakers = [
        c for c in report.per_case
        if c.get("adversarial") and (c.get("citations_count") or 0) > 0
    ]
    leakers.sort(key=lambda c: c.get("citations_count") or 0, reverse=True)
    if leakers:
        lines.append("")
        lines.append(f"Top adversarial leakers ({len(leakers)}):")
        for m in leakers[:5]:
            q = (m.get("query") or "")[:80]
            n = int(m.get("citations_count") or 0)
            lines.append(f"  - {m.get('id')} [{n} citations]: {q}")
    lines.append("")
    src_name = _latest_filename(language)
    lines.append(f"Source: bench/results/{src_name}   gate: --gate "
                 f"{gate} (recall@5 drop > gate triggers this alert).")
    return "\n".join(lines)


def _leak_gate() -> float:
    """Mean extra adversarial citations above baseline that triggers an alert.
    Default 0.5 — means we tolerate a ~half-citation drift per trick query
    before paging."""
    try:
        return float(os.environ.get("GROUNDED_RECALL_LEAK_GATE", "0.5"))
    except (TypeError, ValueError):
        return 0.5


async def run_and_alert_live(
    *,
    gate: Optional[float] = None,
    leak_gate: Optional[float] = None,
    save: bool = True,
    fixture_path: Path = FIXTURE_PATH,
    dispatch: Optional[Callable[..., Any]] = None,
    language: Optional[str] = None,
    alert_type: str = "grounded_recall_regression",
) -> dict:
    """Run the live bench, save results, and fire an alert on gate failure.

    Returns a dict::

        {
            "ran": True,
            "report": <report dict>,
            "saved_to": "<path or None>",
            "gate": 0.05,
            "drop": 0.07,
            "gate_failed": True,
            "alert_dispatched": True,
            "alert_outcomes": {...},  # from metrics._dispatch_alert
        }

    ``dispatch`` is the alert dispatcher (defaults to
    ``metrics._dispatch_alert``). Tests inject a stub.
    """
    if gate is None:
        gate = _bench_gate()
    if leak_gate is None:
        leak_gate = _leak_gate()
    lang = (language or "").strip().lower() or None
    cases = load_cases(fixture_path, language=lang)
    if not cases:
        logger.warning(f"[bench.nightly] no cases matched language={lang!r}")
        return {
            "ran": False,
            "reason": "no_cases_for_language",
            "language": lang,
            "report": None,
            "saved_to": None,
            "gate": gate,
            "drop": 0.0,
            "gate_failed": False,
            "alert_dispatched": False,
            "alert_outcomes": None,
        }
    report = await run_benchmark(cases, retriever="live")
    saved_to: Optional[Path] = None
    if save:
        try:
            saved_to = save_report(report, language=lang)
        except Exception as exc:
            logger.warning(f"[bench.nightly] save_report failed: {exc}")

    baseline = load_baseline(language=lang) or {}
    base_metrics = baseline.get("metrics") or {}
    baseline_recall_5 = base_metrics.get("recall@5")
    cur_recall_5 = report.metrics.get("recall@5")
    drop = 0.0
    recall_gate_failed = False
    if baseline_recall_5 is not None and cur_recall_5 is not None:
        drop = baseline_recall_5 - cur_recall_5
        recall_gate_failed = drop > gate

    # Leak gate — fires when adversarial mean-citations creeps above
    # baseline + leak_gate even though recall stays high. Catches the
    # "retriever quietly leaks 2-3 weak citations per trick query" case
    # that the floored adversarial pass-rate hides.
    base_leak = base_metrics.get("adversarial_mean_citations")
    cur_leak = report.metrics.get("adversarial_mean_citations")
    leak_overshoot = 0.0
    leak_gate_failed = False
    if isinstance(base_leak, (int, float)) and isinstance(cur_leak, (int, float)):
        leak_overshoot = cur_leak - base_leak
        leak_gate_failed = leak_overshoot > leak_gate

    gate_failed = recall_gate_failed or leak_gate_failed

    out: dict = {
        "ran": True,
        "report": report.to_dict(),
        "saved_to": str(saved_to) if saved_to else None,
        "gate": gate,
        "drop": round(drop, 4),
        "leak_gate": leak_gate,
        "leak_overshoot": round(leak_overshoot, 4),
        "leak_gate_failed": leak_gate_failed,
        "recall_gate_failed": recall_gate_failed,
        "gate_failed": gate_failed,
        "alert_dispatched": False,
        "alert_outcomes": None,
    }

    if not gate_failed:
        return out

    # Lazy import — keeps the module importable from CI without the
    # full backend dependency graph (e.g. pymongo/boto3/etc).  # Task #556 — legacy email SDK retired.
    if dispatch is None:
        try:
            from metrics import _dispatch_alert as dispatch  # type: ignore
        except Exception as exc:
            logger.warning(f"[bench.nightly] alert dispatcher unavailable: {exc}")
            return out

    lang_label = f" [{lang}]" if lang else ""
    if recall_gate_failed:
        title = (
            f"Grounded-recall regression{lang_label}: recall@5 dropped {drop:.4f} "
            f"(> gate {gate:.2f})"
        )
    else:
        # Pure leak-gate trigger.
        title = (
            f"Grounded-recall leak{lang_label}: adversarial mean citations "
            f"+{leak_overshoot:.2f} (> leak_gate {leak_gate:.2f})"
        )
    body = _format_alert_body(
        report, baseline, drop, gate, language=lang,
        recall_gate_failed=recall_gate_failed,
        leak_gate_failed=leak_gate_failed,
    )
    if leak_gate_failed:
        body += (
            f"\n\nLeak gate fired: adversarial_mean_citations went from "
            f"{base_leak:.4f} to {cur_leak:.4f} "
            f"(+{leak_overshoot:.4f} > leak_gate {leak_gate:.2f})."
        )
    if lang:
        body = f"Language subset: {lang}\n\n" + body
    if recall_gate_failed:
        threshold_snapshot = {
            "metric": f"recall@5{lang_label}",
            "value": round(baseline_recall_5 - gate, 4),  # min acceptable
            "actual": round(cur_recall_5, 4),
        }
    else:
        threshold_snapshot = {
            "metric": f"adversarial_mean_citations{lang_label}",
            "value": round((base_leak or 0.0) + leak_gate, 4),  # max acceptable
            "actual": round(cur_leak or 0.0, 4),
        }
    try:
        outcomes = await dispatch(
            alert_type,
            title,
            body,
            threshold_snapshot=threshold_snapshot,
            force=False,
        )
        out["alert_dispatched"] = True
        out["alert_outcomes"] = outcomes
    except Exception as exc:
        logger.warning(f"[bench.nightly] alert dispatch failed: {exc}")
    return out


async def _claim_grounded_recall_slot(
    db, cur_tag: str, *, lock_id: str = _GROUNDED_RECALL_LOCK_ID
) -> bool:
    """Atomic CAS on ``db.job_locks[lock_id]`` so only one replica per
    day actually runs the bench (mirrors the dedup pattern used by
    ``_seo_auto_publish_loop``). The ``lock_id`` parameter lets us run
    multiple independent nightly bench schedules (e.g. global + an
    Assamese-only subset, Task #599) without them stepping on each
    other's slot."""
    from pymongo.errors import DuplicateKeyError
    try:
        res = await db.job_locks.find_one_and_update(
            {
                "_id": lock_id,
                _GROUNDED_RECALL_LAST_RUN_KEY: {"$ne": cur_tag},
            },
            {"$set": {
                _GROUNDED_RECALL_LAST_RUN_KEY: cur_tag,
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=False,
        )
        if res is not None:
            return True
    except Exception as exc:
        logger.debug(f"[bench.nightly] CAS update failed: {exc}")
        return False
    try:
        await db.job_locks.insert_one({
            "_id": lock_id,
            _GROUNDED_RECALL_LAST_RUN_KEY: cur_tag,
            "claimed_at": datetime.now(timezone.utc).isoformat(),
        })
        return True
    except DuplicateKeyError:
        return False
    except Exception as exc:
        logger.debug(f"[bench.nightly] bootstrap insert failed: {exc}")
        return False


async def _try_run_grounded_recall_once(
    db,
    now_utc: Optional[datetime] = None,
    *,
    language: Optional[str] = None,
    lock_id: str = _GROUNDED_RECALL_LOCK_ID,
    gate: Optional[float] = None,
    alert_type: str = "grounded_recall_regression",
    log_prefix: str = "[bench.nightly]",
) -> dict:
    """One iteration of the scheduler. Factored out so tests can drive
    it deterministically without a real wall clock.

    ``language`` / ``lock_id`` / ``alert_type`` let the same machinery
    drive multiple independent nightly schedules — e.g. the global bench
    plus an Assamese-only subset (Task #599) that gates against its own
    baseline file and pages on its own alert channel."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if not _bench_enabled():
        return {"ran": False, "reason": "disabled"}
    try:
        cfg = await db.job_locks.find_one(
            {"_id": lock_id},
            {"_id": 0, _GROUNDED_RECALL_LAST_RUN_KEY: 1},
        ) or {}
    except Exception:
        cfg = {}
    last_tag = cfg.get(_GROUNDED_RECALL_LAST_RUN_KEY, "")
    if not _should_run_grounded_recall_now(now_utc, last_tag):
        return {"ran": False, "reason": "outside_window_or_dedup"}

    cur_tag = _bench_run_tag(now_utc)
    if not await _claim_grounded_recall_slot(db, cur_tag, lock_id=lock_id):
        return {"ran": False, "reason": "lost_race"}

    eff_gate = gate if gate is not None else _bench_gate()
    logger.info(
        f"{log_prefix} starting live grounded-recall bench tag={cur_tag} "
        f"language={language or 'all'} gate={eff_gate}"
    )
    result = await run_and_alert_live(
        gate=eff_gate,
        save=True,
        language=language,
        alert_type=alert_type,
    )
    logger.info(
        "%s finished tag=%s language=%s gate_failed=%s drop=%.4f alert_dispatched=%s",
        log_prefix, cur_tag, language or "all",
        result.get("gate_failed"), result.get("drop", 0.0),
        result.get("alert_dispatched"),
    )
    return {"ran": True, "tag": cur_tag, "language": language, **result}


async def _grounded_recall_nightly_loop():
    """Background loop wired into ``server.py`` lifespan.

    Sleeps a few minutes after boot to let the rest of the app warm up,
    then polls every ``_GROUNDED_RECALL_LOOP_SLEEP_S``. Cross-replica
    dedup is handled inside ``_try_run_grounded_recall_once`` so this
    loop does not need a leader gate.
    """
    from deps import db, is_mongo_available  # type: ignore
    await asyncio.sleep(300)  # let the app warm up
    while True:
        try:
            if await is_mongo_available():
                await _try_run_grounded_recall_once(db)
        except Exception as exc:
            logger.debug(f"[bench.nightly] loop iteration error: {exc}")
        await asyncio.sleep(_GROUNDED_RECALL_LOOP_SLEEP_S)


# ──────────────── Per-language nightly subsets (Tasks #599 / #618) ────────
#
# The full bench masks language-specific regressions: each Indian-language
# subset has only ~5–8 cases out of >100, so a complete failure on any
# one subset moves the global recall@5 by a fraction of a percent —
# well inside the global gate. We therefore spawn a second nightly
# loop per language that filters to ``context.language == "<code>"``,
# gates against its own committed baseline (``baseline_<code>.json``),
# and fires an alert via its own channel so on-call admins notice when
# (e.g.) as.wikipedia stops surfacing for Assamese queries or
# hi.wikipedia falls out of the index for Hindi queries.
#
# Configuration is per-language via env:
#   GROUNDED_RECALL_<CODE>_NIGHTLY_ENABLED   default: true
#   GROUNDED_RECALL_<CODE>_NIGHTLY_GATE      default: 0.15 (more slack —
#                                                          small N means
#                                                          one miss moves
#                                                          recall@5 by
#                                                          ~0.125)
#   The enabled flag falls back to the global bench kill-switch so
#   operators can disable every nightly run at once if needed.

_PER_LANGUAGE_DEFAULT_GATE = 0.15

# Languages that ship with tagged fixture cases + committed baseline.
# Keep this list in sync with ``bench/fixtures/baseline_<code>.json``
# and the lifespan registrations in ``server.py``.
PER_LANGUAGE_NIGHTLY_SUBSETS: tuple[str, ...] = ("as", "bn", "hi")


def _bench_language_enabled(language: str) -> bool:
    env_key = f"GROUNDED_RECALL_{language.upper()}_NIGHTLY_ENABLED"
    val = (os.environ.get(env_key, "true") or "").strip().lower()
    if val not in ("1", "true", "yes", "on"):
        return False
    # Respect the global kill-switch — if the operator turned the whole
    # nightly bench off, the per-language subsets go silent too.
    return _bench_enabled()


def _bench_language_gate(language: str) -> float:
    env_key = f"GROUNDED_RECALL_{language.upper()}_NIGHTLY_GATE"
    try:
        g = float(os.environ.get(env_key, str(_PER_LANGUAGE_DEFAULT_GATE)))
    except (TypeError, ValueError):
        g = _PER_LANGUAGE_DEFAULT_GATE
    return max(0.0, min(g, 1.0))


def _language_lock_id(language: str) -> str:
    return f"grounded_recall_nightly_marker_{language.lower()}"


def _language_alert_type(language: str) -> str:
    return f"grounded_recall_regression_{language.lower()}"


async def _try_run_grounded_recall_language_once(
    db,
    language: str,
    now_utc: Optional[datetime] = None,
) -> dict:
    """Per-language variant of ``_try_run_grounded_recall_once``.

    Independent lock + baseline + gate per language so no subset ever
    blocks (or is masked by) the global bench or another language's
    nightly run.
    """
    lang = language.lower()
    if not _bench_language_enabled(lang):
        # Match the shape of the global bench's disabled return so
        # existing callers/tests can compare against a literal dict.
        return {"ran": False, "reason": "disabled"}
    return await _try_run_grounded_recall_once(
        db,
        now_utc=now_utc,
        language=lang,
        lock_id=_language_lock_id(lang),
        gate=_bench_language_gate(lang),
        alert_type=_language_alert_type(lang),
        log_prefix=f"[bench.nightly.{lang}]",
    )


def _make_language_nightly_loop(language: str, stagger_minutes: int):
    """Build a nightly background loop bound to one language subset.

    ``stagger_minutes`` offsets boot so the per-language loops don't
    slam the live retrievers in the same minute as the global bench
    (or each other). Cross-replica dedup is handled by the per-language
    lock inside ``_try_run_grounded_recall_language_once``.
    """
    lang = language.lower()

    async def _loop():
        from deps import db, is_mongo_available  # type: ignore
        await asyncio.sleep(300 + stagger_minutes * 60)
        while True:
            try:
                if await is_mongo_available():
                    await _try_run_grounded_recall_language_once(db, lang)
            except Exception as exc:
                logger.debug(f"[bench.nightly.{lang}] loop iteration error: {exc}")
            await asyncio.sleep(_GROUNDED_RECALL_LOOP_SLEEP_S)

    _loop.__name__ = f"_grounded_recall_{lang}_nightly_loop"
    _loop.__doc__ = (
        f"Background loop for the '{lang}'-language subset "
        f"(Tasks #599 / #618). Staggered +{stagger_minutes}m after boot."
    )
    return _loop


# Backward-compat exports — keep the Assamese-specific names that
# existing callers (server.py lifespan, tests) import. These were the
# public symbols before the factory refactor.
_GROUNDED_RECALL_AS_LOCK_ID = _language_lock_id("as")
_GROUNDED_RECALL_AS_LANGUAGE = "as"
_GROUNDED_RECALL_AS_ALERT_TYPE = _language_alert_type("as")
_GROUNDED_RECALL_AS_DEFAULT_GATE = _PER_LANGUAGE_DEFAULT_GATE


def _bench_as_enabled() -> bool:
    """Back-compat wrapper — Assamese shortcut for the generic
    ``_bench_language_enabled`` check. Kept for existing tests."""
    return _bench_language_enabled("as")


def _bench_as_gate() -> float:
    """Back-compat wrapper for the Assamese nightly gate."""
    return _bench_language_gate("as")


async def _try_run_grounded_recall_assamese_once(
    db, now_utc: Optional[datetime] = None
) -> dict:
    return await _try_run_grounded_recall_language_once(db, "as", now_utc=now_utc)


_grounded_recall_assamese_nightly_loop = _make_language_nightly_loop("as", stagger_minutes=5)

# Bengali (`bn`) and Hindi (`hi`) nightly subsets (Task #618). Each
# gets its own boot stagger so three extra retriever runs don't pile
# up in the same minute.
_grounded_recall_bengali_nightly_loop = _make_language_nightly_loop("bn", stagger_minutes=10)
_grounded_recall_hindi_nightly_loop = _make_language_nightly_loop("hi", stagger_minutes=15)

# Single-source-of-truth registry used by server.py lifespan — adding
# a new language means adding one entry here (plus the fixture +
# baseline file) and nothing needs to change in server.py.
_PER_LANGUAGE_NIGHTLY_LOOPS: dict[str, "callable"] = {
    "as": _grounded_recall_assamese_nightly_loop,
    "bn": _grounded_recall_bengali_nightly_loop,
    "hi": _grounded_recall_hindi_nightly_loop,
}


def per_language_nightly_loops() -> dict[str, "callable"]:
    """Return the {language_code: loop_coroutine_factory} registry.

    Kept as a function (not a bare dict export) so future callers can
    rebuild the mapping dynamically if needed, and so a typo in the
    registered codes shows up as a test failure rather than silently
    leaving a language without a nightly loop.
    """
    missing = [lang for lang in PER_LANGUAGE_NIGHTLY_SUBSETS
               if lang not in _PER_LANGUAGE_NIGHTLY_LOOPS]
    if missing:
        raise RuntimeError(
            f"PER_LANGUAGE_NIGHTLY_SUBSETS declares {missing!r} but no "
            f"nightly loop was registered in _PER_LANGUAGE_NIGHTLY_LOOPS"
        )
    return dict(_PER_LANGUAGE_NIGHTLY_LOOPS)


__all__ = [
    "BenchCase", "CaseResult", "BenchReport",
    "load_cases", "load_baseline", "save_report", "find_latest_result",
    "citation_matches_expected", "run_benchmark",
    "FIXTURE_PATH", "BASELINE_PATH", "RESULTS_DIR", "K_VALUES",
    "run_and_alert_live", "_grounded_recall_nightly_loop",
    "_try_run_grounded_recall_once", "_should_run_grounded_recall_now",
    "_bench_run_tag", "_GROUNDED_RECALL_LOCK_ID",
    "_GROUNDED_RECALL_LAST_RUN_KEY",
    # Per-language factory (Task #618)
    "PER_LANGUAGE_NIGHTLY_SUBSETS",
    "_try_run_grounded_recall_language_once",
    "_make_language_nightly_loop",
    # Assamese-subset nightly (Task #599)
    "_grounded_recall_assamese_nightly_loop",
    "_try_run_grounded_recall_assamese_once",
    "_GROUNDED_RECALL_AS_LOCK_ID",
    "_GROUNDED_RECALL_AS_LANGUAGE",
    "_GROUNDED_RECALL_AS_ALERT_TYPE",
    # Bengali / Hindi nightlies (Task #618)
    "_grounded_recall_bengali_nightly_loop",
    "_grounded_recall_hindi_nightly_loop",
]
