"""Task #940 — adapter contract tests for the entity SEO collectors.

Each collector is given a deterministic in-process ``http_get`` mock so
the test never touches the real Wikidata / Wikipedia / Crunchbase /
Knowledge Graph endpoints. We assert that:

  * the happy path returns ``status="ok"`` with the expected fields,
  * a 404 returns ``status="missing"``,
  * a transport error returns ``status="error"``,
  * the missing-claim list (Wikidata) deep-links to the correct edit URL
    and only contains props that are *not* in the live entity.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

import entity_seo_health as esh


# ─── tiny mock transport ───────────────────────────────────────────────


class _MockTransport:
    """Returns a queued response matched by URL substring."""

    def __init__(self, route_table: Dict[str, Dict[str, Any]]):
        self._routes = route_table
        self.calls: List[str] = []

    async def __call__(self, url, *, method="GET", params=None, headers=None, timeout=10.0):
        self.calls.append(url)
        for substr, resp in self._routes.items():
            if substr in url:
                return dict(resp)
        return {"status_code": 404, "json": None, "text": "", "error": None}


def _run(coro):
    # Task #284 — fresh loop per call (Python 3.11+ no longer auto-creates
    # a main-thread loop on demand).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── fetch_wikidata ────────────────────────────────────────────────────


def test_fetch_wikidata_ok_with_some_missing_claims():
    # Live entity has P31 + P17 + P856 but is missing P131, P112, P571, P1448.
    payload = {
        "entities": {
            "Q42": {
                "labels": {"en": {"value": "Syrabit.ai"}},
                "claims": {"P31": [{}], "P17": [{}], "P856": [{}]},
            },
        },
    }
    mock = _MockTransport({"Q42": {"status_code": 200, "json": payload, "text": None, "error": None}})
    sig = _run(esh.fetch_wikidata(qid="Q42", http_get=mock))
    assert sig["status"] == "ok"
    assert sig["fields"]["claim_count"] == 3
    assert set(sig["fields"]["present_claims"]) == {"P31", "P17", "P856"}
    missing_props = [c["prop"] for c in sig["fields"]["missing_claims"]]
    assert "P131" in missing_props and "P112" in missing_props
    # Each missing claim deep-links to the per-property anchor.
    for c in sig["fields"]["missing_claims"]:
        assert c["edit_url"].startswith("https://www.wikidata.org/wiki/Q42#")


def test_fetch_wikidata_404_returns_missing():
    mock = _MockTransport({"Q42": {"status_code": 404, "json": None, "text": "", "error": None}})
    sig = _run(esh.fetch_wikidata(qid="Q42", http_get=mock))
    assert sig["status"] == "missing"
    # All desired claims surfaced as missing so the panel can deep-link.
    assert len(sig["fields"]["missing_claims"]) == len(esh.DESIRED_WIKIDATA_CLAIMS)


def test_fetch_wikidata_no_qid_configured():
    sig = _run(esh.fetch_wikidata(qid="", http_get=_MockTransport({})))
    assert sig["status"] == "missing"
    # Special:NewItem deep-link when no QID exists.
    assert sig["fields"]["missing_claims"][0]["edit_url"].startswith(
        "https://www.wikidata.org/wiki/Special:NewItem")


def test_fetch_wikidata_transport_error():
    mock = _MockTransport({"Q42": {"status_code": 0, "json": None, "text": None, "error": "boom"}})
    sig = _run(esh.fetch_wikidata(qid="Q42", http_get=mock))
    assert sig["status"] == "error"
    assert "boom" in sig["summary"]


# ─── fetch_wikipedia ───────────────────────────────────────────────────


def test_fetch_wikipedia_ok():
    payload = {
        "title": "Syrabit.ai",
        "extract": "Syrabit.ai is an Indian education-technology company.",
        "timestamp": "2026-04-01T00:00:00Z",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Syrabit.ai"}},
    }
    mock = _MockTransport({"page/summary": {"status_code": 200, "json": payload, "text": None, "error": None}})
    sig = _run(esh.fetch_wikipedia(title="Syrabit.ai", http_get=mock))
    assert sig["status"] == "ok"
    assert sig["fields"]["page_url"] == "https://en.wikipedia.org/wiki/Syrabit.ai"


def test_fetch_wikipedia_missing():
    mock = _MockTransport({"page/summary": {"status_code": 404, "json": None, "text": "", "error": None}})
    sig = _run(esh.fetch_wikipedia(title="Syrabit.ai", http_get=mock))
    assert sig["status"] == "missing"
    assert sig["fields"]["draft_url"].startswith("https://en.wikipedia.org/wiki/Draft:")


# ─── fetch_crunchbase ──────────────────────────────────────────────────


def test_fetch_crunchbase_ok_with_completeness():
    body = (
        '<html><body>company description founder Dipak Rai based in Guwahati Assam '
        'website https://syrabit.ai</body></html>'
    )
    mock = _MockTransport({"crunchbase.com": {"status_code": 200, "json": None, "text": body, "error": None}})
    sig = _run(esh.fetch_crunchbase(permalink="syrabit-ai", http_get=mock))
    assert sig["status"] == "ok"
    assert sig["fields"]["completeness_pct"] == 100.0
    assert sig["fields"]["fields_present"]["founders"] is True


def test_fetch_crunchbase_missing():
    mock = _MockTransport({"crunchbase.com": {"status_code": 404, "json": None, "text": "", "error": None}})
    sig = _run(esh.fetch_crunchbase(permalink="syrabit-ai", http_get=mock))
    assert sig["status"] == "missing"
    assert sig["fields"]["submit_url"]


# ─── verify_sameas_profile / fetch_sameas ──────────────────────────────


def test_verify_sameas_profile_ok_and_broken():
    mock = _MockTransport({
        "linkedin.com": {"status_code": 200, "json": None, "text": None,
                         "error": None, "final_url": "https://www.linkedin.com/x"},
        "twitter.com":  {"status_code": 404, "json": None, "text": None,
                         "error": None, "final_url": "https://twitter.com/x"},
    })
    ok = _run(esh.verify_sameas_profile("https://www.linkedin.com/x", http_get=mock))
    bad = _run(esh.verify_sameas_profile("https://twitter.com/x", http_get=mock))
    assert ok["status"] == "ok" and ok["http_status"] == 200
    assert bad["status"] == "missing" and bad["http_status"] == 404


def test_verify_sameas_profile_head_405_falls_back_to_get():
    """Some providers (LinkedIn, Twitter behind anti-bot, certain CDNs)
    return 405 / 403 to HEAD probes even though the URL is live. The
    verifier must fall back to GET to avoid false-positive "missing"
    flags."""
    head_status: List[int] = []
    get_status:  List[int] = []
    seen_methods: List[str] = []

    async def transport(url, *, method="GET", params=None, headers=None, timeout=10.0):
        seen_methods.append(method)
        if method == "HEAD":
            head_status.append(405)
            return {"status_code": 405, "json": None, "text": None,
                    "error": None, "final_url": url}
        get_status.append(200)
        return {"status_code": 200, "json": None, "text": "ok",
                "error": None, "final_url": url}

    res = _run(esh.verify_sameas_profile(
        "https://www.linkedin.com/in/syrabit", http_get=transport))
    assert seen_methods[:2] == ["HEAD", "GET"]
    assert res["status"] == "ok"
    assert res["http_status"] == 200


def test_verify_sameas_profile_offsite_redirect_flagged_as_missing():
    """A 200 from a URL that 301'd to a different brand host is still
    a broken profile from an SEO perspective — the canonical link is
    no longer ours. Sub-host changes (www.linkedin.com vs linkedin.com)
    must NOT trip this check."""
    mock = _MockTransport({
        # 200 OK but the response landed on captcha.example.com
        "linkedin.com/company/syrabit": {
            "status_code": 200, "json": None, "text": None, "error": None,
            "final_url": "https://captcha.example.com/blocked",
        },
        # 200 OK and stayed on the same brand host (www. stripped) — fine.
        "linkedin.com/in/syrabit": {
            "status_code": 200, "json": None, "text": None, "error": None,
            "final_url": "https://www.linkedin.com/in/syrabit",
        },
    })
    bad = _run(esh.verify_sameas_profile("https://linkedin.com/company/syrabit", http_get=mock))
    ok  = _run(esh.verify_sameas_profile("https://linkedin.com/in/syrabit",     http_get=mock))
    assert bad["status"] == "missing"
    assert "off-site" in bad["summary"]
    assert ok["status"] == "ok"


def test_fetch_sameas_aggregate_status():
    mock = _MockTransport({
        "linkedin.com": {"status_code": 200, "json": None, "text": None, "error": None},
        "twitter.com":  {"status_code": 200, "json": None, "text": None, "error": None},
        "github.com":   {"status_code": 200, "json": None, "text": None, "error": None},
        "youtube.com":  {"status_code": 404, "json": None, "text": None, "error": None},
    })
    sig = _run(esh.fetch_sameas(http_get=mock))
    # One of the org profiles 404'd → aggregate is "missing".
    assert sig["status"] == "missing"
    assert any(b["http_status"] == 404 for b in sig["fields"]["broken"])


# ─── fetch_google_kg ───────────────────────────────────────────────────


def test_fetch_google_kg_no_api_key_returns_error_with_configured_false(monkeypatch):
    # No API key + no env: surfaces as configurable-but-disabled.
    # Must scrub the env var so this test is hermetic — otherwise a
    # GOOGLE_KG_API_KEY exported by the dev shell would push the call
    # past the empty-key guard and the assertion would flip.
    monkeypatch.delenv("GOOGLE_KG_API_KEY", raising=False)
    sig = _run(esh.fetch_google_kg(api_key="", http_get=_MockTransport({})))
    assert sig["status"] == "error"
    assert sig["fields"]["configured"] is False


def test_fetch_google_kg_panel_present():
    payload = {
        "itemListElement": [
            {"resultScore": 950,
             "result": {"@id": "kg:/m/syrabit", "name": "Syrabit.ai",
                        "description": "Education-technology company"}},
        ],
    }
    mock = _MockTransport({"kgsearch": {"status_code": 200, "json": payload, "text": None, "error": None}})
    sig = _run(esh.fetch_google_kg(api_key="abc", http_get=mock))
    assert sig["status"] == "ok"
    assert sig["fields"]["kg_id"] == "kg:/m/syrabit"
    assert sig["fields"]["result_score"] == 950


def test_fetch_google_kg_no_panel_entry():
    mock = _MockTransport({"kgsearch": {"status_code": 200, "json": {"itemListElement": []}, "text": None, "error": None}})
    sig = _run(esh.fetch_google_kg(api_key="abc", http_get=mock))
    assert sig["status"] == "missing"


# ─── fetch_google_kg multi-query mode ──────────────────────────────────


class _KgQueryAwareTransport:
    """Like _MockTransport but inspects the ``query`` query-string param so
    each tracked KG query can return a different response."""

    def __init__(self, by_query: Dict[str, Dict[str, Any]]):
        self._by_query = by_query
        self.calls: List[str] = []

    async def __call__(self, url, *, method="GET", params=None, headers=None, timeout=10.0):
        self.calls.append((params or {}).get("query") or "")
        q = (params or {}).get("query") or ""
        if q in self._by_query:
            return dict(self._by_query[q])
        return {"status_code": 200, "json": {"itemListElement": []}, "text": None, "error": None}


def test_fetch_google_kg_multi_query_aggregates_per_query_status():
    """Probing ('Syrabit', 'Syrabit.ai') where one query has a panel and
    the other doesn't must aggregate to ``status="missing"`` and surface
    a per-query rows array so the panel can show which name to push."""
    panel = {
        "itemListElement": [
            {"resultScore": 950,
             "result": {"@id": "kg:/m/syrabit", "name": "Syrabit.ai",
                        "description": "Education-technology company"}},
        ],
    }
    mock = _KgQueryAwareTransport({
        "Syrabit":     {"status_code": 200, "json": panel,                       "text": None, "error": None},
        "Syrabit.ai":  {"status_code": 200, "json": {"itemListElement": []},     "text": None, "error": None},
    })
    sig = _run(esh.fetch_google_kg(
        queries=("Syrabit", "Syrabit.ai"), http_get=mock, api_key="abc"))
    assert sig["status"] == "missing"
    rows = sig["fields"]["queries"]
    assert len(rows) == 2
    by_q = {r["query"]: r for r in rows}
    assert by_q["Syrabit"]["status"] == "ok"
    assert by_q["Syrabit"]["kg_id"] == "kg:/m/syrabit"
    assert by_q["Syrabit.ai"]["status"] == "missing"
    # Aggregate summary names the queries that are still missing.
    assert "Syrabit.ai" in sig["summary"]


def test_fetch_google_kg_multi_query_all_errors_aggregates_to_error():
    """When every tracked query fails in transport the aggregate must be
    ``error`` (not ``missing``) so the panel surfaces a transport
    incident instead of a content gap."""
    mock = _KgQueryAwareTransport({
        "Syrabit":    {"status_code": 0, "json": None, "text": None, "error": "boom"},
        "Syrabit.ai": {"status_code": 0, "json": None, "text": None, "error": "boom"},
    })
    sig = _run(esh.fetch_google_kg(
        queries=("Syrabit", "Syrabit.ai"), http_get=mock, api_key="abc"))
    assert sig["status"] == "error"
    assert all(r["status"] == "error" for r in sig["fields"]["queries"])


def test_fetch_google_kg_multi_query_all_ok():
    panel = {
        "itemListElement": [
            {"resultScore": 900,
             "result": {"@id": "kg:/m/x", "name": "Syrabit.ai"}},
        ],
    }
    mock = _KgQueryAwareTransport({
        "Syrabit":    {"status_code": 200, "json": panel, "text": None, "error": None},
        "Syrabit.ai": {"status_code": 200, "json": panel, "text": None, "error": None},
    })
    sig = _run(esh.fetch_google_kg(
        queries=("Syrabit", "Syrabit.ai"), http_get=mock, api_key="abc"))
    assert sig["status"] == "ok"
    assert all(r["status"] == "ok" for r in sig["fields"]["queries"])


# ─── fetch_mention_opportunities ───────────────────────────────────────


def test_fetch_mention_opportunities_partial_missing():
    """One Wikipedia page mentions us, two don't → status=missing and
    the missing rows are surfaced for the admin panel."""
    targets = (
        {"id": "p1", "label": "Page 1", "url": "https://example.com/p1", "expected_term": "Syrabit"},
        {"id": "p2", "label": "Page 2", "url": "https://example.com/p2", "expected_term": "Syrabit"},
        {"id": "p3", "label": "Page 3", "url": "https://example.com/p3", "expected_term": "Syrabit"},
    )
    mock = _MockTransport({
        "p1": {"status_code": 200, "json": None, "text": "Syrabit is great",      "error": None},
        "p2": {"status_code": 200, "json": None, "text": "no mention here",       "error": None},
        "p3": {"status_code": 200, "json": None, "text": "another article body",  "error": None},
    })
    sig = _run(esh.fetch_mention_opportunities(targets=targets, http_get=mock))
    assert sig["status"] == "missing"
    assert sig["fields"]["total"] == 3
    missing = sig["fields"]["missing"]
    assert {m["id"] for m in missing} == {"p2", "p3"}
    # Term match is case-insensitive.
    mock2 = _MockTransport({
        "p1": {"status_code": 200, "json": None, "text": "syrabit lowercase",     "error": None},
    })
    sig2 = _run(esh.fetch_mention_opportunities(
        targets=({"id": "p1", "label": "P1", "url": "https://example.com/p1", "expected_term": "Syrabit"},),
        http_get=mock2))
    assert sig2["status"] == "ok"


def test_fetch_mention_opportunities_all_errors():
    targets = (
        {"id": "p1", "label": "P1", "url": "https://example.com/p1", "expected_term": "Syrabit"},
    )
    mock = _MockTransport({
        "p1": {"status_code": 0, "json": None, "text": None, "error": "boom"},
    })
    sig = _run(esh.fetch_mention_opportunities(targets=targets, http_get=mock))
    assert sig["status"] == "error"


# ─── aggregate_snapshot wires everything ──────────────────────────────


def test_aggregate_snapshot_combines_signal_statuses():
    mock = _MockTransport({
        "Special:EntityData": {"status_code": 404, "json": None, "text": "", "error": None},
        "page/summary":       {"status_code": 200, "json": {"title": "Syrabit.ai", "content_urls": {"desktop": {"page": "x"}}}, "text": None, "error": None},
        "crunchbase.com":     {"status_code": 200, "json": None, "text": "company description founder Dipak Rai Guwahati syrabit.ai", "error": None},
        "linkedin.com":       {"status_code": 200, "json": None, "text": None, "error": None},
        "twitter.com":        {"status_code": 200, "json": None, "text": None, "error": None},
        "github.com":         {"status_code": 200, "json": None, "text": None, "error": None},
        "youtube.com":        {"status_code": 200, "json": None, "text": None, "error": None},
        "kgsearch":           {"status_code": 200, "json": {"itemListElement": []}, "text": None, "error": None},
    })
    snap = _run(esh.aggregate_snapshot(http_get=mock))
    # Wikidata QID isn't configured by default in tests → "missing".
    assert snap["signals"]["wikidata"]["status"] == "missing"
    assert snap["signals"]["wikipedia"]["status"] == "ok"
    # No GOOGLE_KG_API_KEY in test env → error path.
    assert snap["signals"]["google_kg"]["status"] in {"missing", "error"}
    assert snap["aggregate_status"] in {"missing", "degraded"}
    assert snap["summary"]["wikipedia_present"] is True
