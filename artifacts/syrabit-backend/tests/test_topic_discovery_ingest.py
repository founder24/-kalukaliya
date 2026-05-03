"""Contract tests for the live ingestion adapters that feed
topic_discovery_service. We mock the outbound HTTP/auth so the tests
run without network/secrets but exercise the full request → parse →
upsert path.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gsc_search_console_client as gsc  # noqa: E402
import trending_rss_client as rss  # noqa: E402


def _run(coro):
    # Task #284 — fresh loop per call (Python 3.11+ no longer auto-creates
    # a main-thread loop on demand).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── fakes shared with topic_discovery tests ──────────────────────────


class _FakeColl:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []
        self.upserts: List[Dict[str, Any]] = []

    async def update_one(self, q, update, upsert=False):
        self.upserts.append({"q": dict(q), "update": dict(update), "upsert": upsert})
        sets = (update.get("$set") or {})
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                d.update(sets)
                return
        if upsert:
            self.docs.append({**q, **sets})


class _FakeDb:
    def __init__(self):
        self._map: Dict[str, _FakeColl] = {}

    def __getitem__(self, name):
        return self._map.setdefault(name, _FakeColl())


# ── GSC contract tests ──────────────────────────────────────────────


class _MockResponse:
    def __init__(self, status: int, payload: Any = None, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.text = text or ""

    def json(self):
        return self._payload


class _MockGSCClient:
    """Captures the API call and replies with a static near-miss + far
    rows so we can assert the position-band filter."""

    def __init__(self, payload):
        self.payload = payload
        self.calls: List[Dict[str, Any]] = []

    async def post(self, url, *, json=None, data=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "data": data,
                           "headers": headers})
        if "/searchAnalytics/query" in url:
            return _MockResponse(200, self.payload)
        return _MockResponse(200, {"access_token": "fake-token"})

    async def aclose(self):
        pass


def test_fetch_near_miss_filters_by_position_band():
    payload = {"rows": [
        {"keys": ["projectile motion derivation"], "position": 13.4,
         "impressions": 480, "clicks": 6, "ctr": 0.012},
        {"keys": ["thermodynamics first law"], "position": 4.0,
         "impressions": 1200, "clicks": 220, "ctr": 0.18},
        {"keys": ["jee mains rank predictor"], "position": 22.0,
         "impressions": 90, "clicks": 0, "ctr": 0.0},
        {"keys": ["wave optics interference"], "position": 18.7,
         "impressions": 760, "clicks": 14, "ctr": 0.018},
    ]}
    client = _MockGSCClient(payload)

    out = _run(gsc.fetch_near_miss_rows(
        site_url="sc-domain:syrabit.ai",
        client=client,
        sa_loader=lambda: {"client_email": "x@y", "private_key": "K",
                           "token_uri": "https://t"},
        token_minter=lambda c, sa: _async_const("tok"),
    ))
    queries = sorted(r["query"] for r in out)
    assert queries == ["projectile motion derivation",
                       "wave optics interference"]
    assert all(11 <= r["position"] <= 20 for r in out)
    # The API was hit at the right path with the right body shape.
    api_call = next(c for c in client.calls if "/searchAnalytics/query" in c["url"])
    assert api_call["json"]["dimensions"] == ["query"]
    assert "startDate" in api_call["json"]
    assert "endDate" in api_call["json"]


def test_fetch_near_miss_no_op_without_service_account():
    out = _run(gsc.fetch_near_miss_rows(
        site_url="sc-domain:syrabit.ai",
        client=_MockGSCClient({"rows": []}),
        sa_loader=lambda: None,
    ))
    assert out == []


def test_fetch_near_miss_no_op_without_site_url():
    out = _run(gsc.fetch_near_miss_rows(
        site_url="",
        client=_MockGSCClient({"rows": []}),
        sa_loader=lambda: {"client_email": "x", "private_key": "K",
                           "token_uri": "https://t"},
    ))
    assert out == []


def test_ingest_near_miss_into_mongo_upserts_rows(monkeypatch):
    monkeypatch.setenv("GSC_SITE_URL", "sc-domain:syrabit.ai")
    payload = {"rows": [
        {"keys": ["projectile motion"], "position": 13.0,
         "impressions": 100, "clicks": 1, "ctr": 0.01},
    ]}
    client = _MockGSCClient(payload)
    db = _FakeDb()
    n = _run(gsc.ingest_near_miss_into_mongo(
        db,
        client=client,
        sa_loader=lambda: {"client_email": "x", "private_key": "K",
                           "token_uri": "https://t"},
        token_minter=lambda c, sa: _async_const("tok"),
    ))
    assert n == 1
    coll = db[gsc.GSC_NEAR_MISS_COLLECTION]
    assert len(coll.docs) == 1
    assert coll.docs[0]["query"] == "projectile motion"
    assert coll.docs[0]["position"] == 13.0


def _async_const(value):
    async def _f():
        return value
    return _f()


# ── RSS contract tests ──────────────────────────────────────────────


_RSS_FEED = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Edu Trending</title>
    <item><title>JEE 2026 syllabus revision tips</title></item>
    <item><title>NEET cutoff 2026 forecast</title></item>
    <item><title>Class 12 board exam pattern changes</title></item>
  </channel>
</rss>"""

_ATOM_FEED = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom News</title>
  <entry><title>Best NCERT physics references for 2026</title></entry>
  <entry><title>CUET preparation strategy</title></entry>
</feed>"""


class _MockRSSClient:
    def __init__(self, body_for_url):
        self.body_for_url = body_for_url
        self.calls: List[str] = []

    async def get(self, url, *, timeout=None, headers=None):
        self.calls.append(url)
        body = self.body_for_url.get(url, "")
        if not body:
            return _MockResponse(404, text="not found")
        return _MockResponse(200, text=body)

    async def aclose(self):
        pass


def test_parse_titles_handles_rss_and_atom():
    rss_titles = rss._parse_titles(_RSS_FEED)
    assert "JEE 2026 syllabus revision tips" in rss_titles
    assert "NEET cutoff 2026 forecast" in rss_titles
    # Channel title dropped.
    assert "Edu Trending" not in rss_titles

    atom_titles = rss._parse_titles(_ATOM_FEED)
    assert "Best NCERT physics references for 2026" in atom_titles
    assert "CUET preparation strategy" in atom_titles


def test_parse_titles_rejects_garbage():
    assert rss._parse_titles("not xml at all") == []
    assert rss._parse_titles("") == []


def test_ingest_trending_no_op_without_feeds():
    db = _FakeDb()
    n = _run(rss.ingest_trending_into_mongo(db, feeds=[]))
    assert n == 0


def test_ingest_trending_writes_each_title_once():
    db = _FakeDb()
    feed_url = "https://example.com/feed.rss"
    client = _MockRSSClient({feed_url: _RSS_FEED})

    n = _run(rss.ingest_trending_into_mongo(
        db, feeds=[feed_url], client=client,
    ))
    assert n == 3
    coll = db[rss.TRENDING_RAW_COLLECTION]
    assert len(coll.docs) == 3
    assert all(d["source"] == f"rss:{feed_url}" for d in coll.docs)
    titles = sorted(d["query"] for d in coll.docs)
    assert "JEE 2026 syllabus revision tips" in titles


def test_ingest_trending_handles_failed_feed():
    db = _FakeDb()
    client = _MockRSSClient({})  # all URLs return 404
    n = _run(rss.ingest_trending_into_mongo(
        db, feeds=["https://broken/feed"], client=client,
    ))
    assert n == 0
