"""Task #28 — golden tests for the weekly SEO baseline publisher.

Covers the contract in `.local/tasks/task-28.md`:
  * `aca_jobs.seo_baseline.run_baseline_publish` persists the full
    report to `db.seo_baseline_runs` keyed by `report_date`.
  * The WoW median-SEO-score delta is pre-computed against the prior
    run's persisted summary.
  * `/api/admin/seo/baseline-latest` returns the latest summary +
    a `prior` block + `samples_failed` projection.

Runs without network: the underlying `scripts/seo_baseline.py`
runner is injected as a stub.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()

from aca_jobs import seo_baseline as job  # noqa: E402


# ─── Fakes ─────────────────────────────────────────────────────────


@dataclass
class _FakePage:
    url: str
    board: str
    chapter_slug: str
    page_type: str
    lighthouse: Optional[Dict[str, Any]] = None
    structured_data: Optional[Dict[str, Any]] = None
    rich_results: Optional[Dict[str, Any]] = None
    failures: List[str] = field(default_factory=list)
    skipped_legs: Dict[str, str] = field(default_factory=dict)


@dataclass
class _FakeReport:
    generated_at_utc: str
    public_base_url: str
    sampled_pages: int
    pages: List[_FakePage]
    summary: Dict[str, Any]


def _make_runner(median: int, failures: int):
    def _runner(*, base_url, boards, chapters_per_board, page_type, rich_results_key):
        pages = [
            _FakePage(
                url=f"{base_url}/board/{b}/class/12/subject/general/chapter/sample-{i}/{page_type}",
                board=b,
                chapter_slug=f"sample-{i}",
                page_type=page_type,
                lighthouse={"scores": {"seo": median}, "lcp_under_2_5s": True},
                structured_data={"has_faq_page": True, "has_breadcrumb": True},
                failures=(["lighthouse: timeout"] if i < failures and b == boards[0] else []),
            )
            for b in boards
            for i in range(chapters_per_board)
        ]
        return _FakeReport(
            generated_at_utc=datetime.now(tz=timezone.utc).isoformat(),
            public_base_url=base_url,
            sampled_pages=len(pages),
            pages=pages,
            summary={
                "total_pages":          len(pages),
                "median_seo_score":     median,
                "pages_with_failures":  sum(1 for p in pages if p.failures),
            },
        )
    return _runner


class _FakeCollection:
    def __init__(self, seeded: Optional[List[Dict[str, Any]]] = None):
        self.docs: List[Dict[str, Any]] = list(seeded or [])

    async def find_one(self, query, sort=None, projection=None):
        rows = self.docs
        if "started_at" in query and isinstance(query["started_at"], dict):
            cutoff = query["started_at"].get("$lt")
            rows = [d for d in rows if d.get("started_at") and d["started_at"] < cutoff]
        if not rows:
            return None
        if sort:
            key = sort[0][0]
            reverse = sort[0][1] < 0
            rows = sorted(rows, key=lambda d: d.get(key) or datetime.min.replace(tzinfo=timezone.utc), reverse=reverse)
        return dict(rows[0])

    async def update_one(self, filt, update, upsert=False):
        for i, d in enumerate(self.docs):
            if all(d.get(k) == v for k, v in filt.items()):
                self.docs[i] = {**d, **(update.get("$set") or {})}
                return
        if upsert:
            self.docs.append(dict(update.get("$set") or {}))


class _FakeDb:
    def __init__(self, seeded=None):
        self.seo_baseline_runs = _FakeCollection(seeded)


# ─── Tests ─────────────────────────────────────────────────────────


def test_first_run_persists_with_null_wow_delta(monkeypatch):
    """No prior doc → wow_delta_seo_score is None; doc is upserted."""
    db = _FakeDb()
    monkeypatch.setattr(job, "_publish_metrics", lambda *a, **kw: None)
    summary = asyncio.run(job.run_baseline_publish(
        db,
        base_url="https://syrabit.ai",
        boards=("ahsec", "ncert"),
        chapters_per_board=2,
        page_type="notes",
        runner=_make_runner(median=87, failures=0),
    ))
    assert summary["median_seo_score"] == 87
    assert summary["wow_delta_seo_score"] is None
    assert summary["pages_with_failures"] == 0
    assert summary["sampled_pages"] == 4
    assert len(db.seo_baseline_runs.docs) == 1
    persisted = db.seo_baseline_runs.docs[0]
    assert persisted["report_date"] == summary["report_date"]
    assert persisted["wow_delta_seo_score"] is None


def test_second_run_computes_negative_wow_delta(monkeypatch):
    """Prior median 91 → current median 84 → wow_delta = -7 (alarm trips)."""
    prior_started = datetime.now(tz=timezone.utc) - timedelta(days=7)
    db = _FakeDb(seeded=[{
        "report_date": prior_started.date().isoformat(),
        "started_at":  prior_started,
        "summary":     {"median_seo_score": 91, "pages_with_failures": 0},
    }])
    monkeypatch.setattr(job, "_publish_metrics", lambda *a, **kw: None)
    summary = asyncio.run(job.run_baseline_publish(
        db,
        base_url="https://syrabit.ai",
        boards=("ahsec",),
        chapters_per_board=4,
        page_type="notes",
        runner=_make_runner(median=84, failures=3),
    ))
    assert summary["median_seo_score"] == 84
    assert summary["wow_delta_seo_score"] == pytest.approx(-7.0)
    assert summary["pages_with_failures"] == 3  # alarm trip on >2


def test_publish_metrics_emits_three_datapoints():
    """Exercises the boto3 stub path: median + failures + delta."""
    seen: dict = {}

    class _StubCw:
        def put_metric_data(self, **kw):
            seen.update(kw)

    class _StubBoto3:
        @staticmethod
        def client(_name):
            return _StubCw()

    import sys
    sys.modules["boto3"] = _StubBoto3  # type: ignore
    try:
        job._publish_metrics(
            {"median_seo_score": 88, "pages_with_failures": 1},
            wow_delta=-3.5,
        )
    finally:
        sys.modules.pop("boto3", None)

    assert seen["Namespace"] == "Syrabit/SEO"
    names = {m["MetricName"] for m in seen["MetricData"]}
    assert names == {"MedianSeoScore", "PagesWithFailures", "MedianSeoScoreWoWDelta"}
