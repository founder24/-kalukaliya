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
        if "report_date" in query and isinstance(query["report_date"], dict):
            ne = query["report_date"].get("$ne")
            if ne is not None:
                rows = [d for d in rows if d.get("report_date") != ne]
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


def test_same_day_rerun_compares_against_prior_week_not_self(monkeypatch):
    """Reviewer fix (round-2): a manual same-Monday re-run must NOT
    compare its delta against the earlier same-day run; it must
    skip past it to last week's doc.
    """
    today = datetime.now(tz=timezone.utc)
    today_date = today.date().isoformat()
    last_week = today - timedelta(days=7)
    db = _FakeDb(seeded=[
        # Last week: median 90.
        {"report_date": last_week.date().isoformat(),
         "started_at":  last_week,
         "summary":     {"median_seo_score": 90, "pages_with_failures": 0}},
        # Earlier today: median 84 (the row we must NOT compare against).
        {"report_date": today_date,
         "started_at":  today - timedelta(hours=2),
         "summary":     {"median_seo_score": 84, "pages_with_failures": 1}},
    ])
    monkeypatch.setattr(job, "_publish_metrics", lambda *a, **kw: None)
    summary = asyncio.run(job.run_baseline_publish(
        db,
        base_url="https://syrabit.ai",
        boards=("ahsec",),
        chapters_per_board=2,
        page_type="notes",
        runner=_make_runner(median=82, failures=0),
    ))
    # WoW delta is 82 - 90 = -8 (vs last week), NOT 82 - 84 = -2.
    assert summary["wow_delta_seo_score"] == pytest.approx(-8.0)
    # Same-day row was overwritten in place (idempotent upsert).
    same_day_rows = [d for d in db.seo_baseline_runs.docs
                     if d.get("report_date") == today_date]
    assert len(same_day_rows) == 1


def test_run_returns_full_doc_for_post_publish(monkeypatch):
    """Reviewer fix (round-2): the Lambda POSTs the function's
    return value to /api/admin/seo/baseline-publish, so the return
    must include the FULL persisted shape (summary + pages +
    timestamps + base_url) — not just the compact summary.
    """
    db = _FakeDb()
    monkeypatch.setattr(job, "_publish_metrics", lambda *a, **kw: None)
    out = asyncio.run(job.run_baseline_publish(
        db,
        base_url="https://syrabit.ai",
        boards=("ahsec",),
        chapters_per_board=2,
        page_type="notes",
        runner=_make_runner(median=88, failures=1),
    ))
    # Convenience projections (back-compat).
    assert out["median_seo_score"] == 88
    assert out["wow_delta_seo_score"] is None
    # Full persisted doc fields (the POST contract).
    assert out["public_base_url"] == "https://syrabit.ai"
    assert isinstance(out["summary"], dict) and out["summary"]["median_seo_score"] == 88
    assert isinstance(out["pages"], list) and len(out["pages"]) == 2
    assert "started_at" in out and "finished_at" in out


def test_publish_endpoint_does_not_blank_canonical_doc(monkeypatch):
    """Reviewer fix (round-2): a same-report_date POST that omits
    `summary` / `pages` must NOT overwrite the canonical doc that
    ``run_baseline_publish`` already wrote. Hardened by the
    field-by-field merge in the route.
    """
    from routes import admin_seo_baseline as route

    today_iso = datetime.now(tz=timezone.utc).date().isoformat()
    seeded = {
        "report_date":   today_iso,
        "started_at":    datetime.now(tz=timezone.utc),
        "summary":       {"median_seo_score": 91, "pages_with_failures": 0},
        "pages":         [{"url": "/x", "page_type": "notes"}],
        "sampled_pages": 1,
    }
    fake_db = _FakeDb(seeded=[seeded])
    monkeypatch.setattr(route, "db", fake_db)

    # Thin POST: only report_date — must NOT clear summary/pages.
    asyncio.run(route.admin_baseline_publish(
        payload={"report_date": today_iso},
        admin={"sub": "test", "is_admin": True},
    ))
    persisted = fake_db.seo_baseline_runs.docs[0]
    assert persisted["summary"]["median_seo_score"] == 91  # preserved
    assert persisted["pages"] == [{"url": "/x", "page_type": "notes"}]
    assert persisted["published_via"] == "post"  # merge applied


def test_lambda_handler_resolves_admin_secret_from_arn(monkeypatch):
    """Round-3 reviewer fix: the Lambda must hydrate ADMIN_JWT_SECRET
    from ADMIN_JWT_SECRET_ARN via Secrets Manager when the direct
    env var is unset (Terraform injects only the ARN). Regression
    guard: if a future refactor breaks the SM fetch path, this test
    fails fast instead of silently skipping the POST every Monday.
    """
    import sys
    import os
    import importlib

    # Ensure the lambda_batch module path resolves under tests.
    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "..", "syrabit", "services", "backend",
        ),
    )

    # Stub `boto3` + `jwt` BEFORE the lambda module is (re)imported.
    fake_secret_returned = {"value": None}

    class _StubSm:
        def get_secret_value(self, SecretId):
            fake_secret_returned["value"] = SecretId
            return {"SecretString": "test-secret-from-sm"}

    class _StubBoto3:
        @staticmethod
        def client(name):
            assert name == "secretsmanager"
            return _StubSm()

    class _StubJwt:
        @staticmethod
        def encode(claims, secret, algorithm):
            assert secret == "test-secret-from-sm"
            assert claims["role"] == "admin"
            return "signed.jwt.token"

    sys.modules["boto3"] = _StubBoto3  # type: ignore
    sys.modules["jwt"] = _StubJwt      # type: ignore

    monkeypatch.delenv("ADMIN_JWT_SECRET", raising=False)
    monkeypatch.setenv("ADMIN_JWT_SECRET_ARN", "arn:aws:secretsmanager:test")

    try:
        mod = importlib.import_module("lambda_batch.seo_baseline")
        importlib.reload(mod)
        token = mod._mint_admin_jwt()
        assert token == "signed.jwt.token"
        assert fake_secret_returned["value"] == "arn:aws:secretsmanager:test"
    finally:
        sys.modules.pop("boto3", None)
        sys.modules.pop("jwt", None)


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
