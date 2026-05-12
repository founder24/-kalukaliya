"""Task #6 — /learn/ slug resolver regression tests.

get_public_cms_document uses a two-phase lookup:
  Phase 1: exact match on ``id`` or ``seo_slug`` — fast path, zero overhead.
  Phase 2: prefix-regex candidate scan + clean_learn_slug() Python filter —
           fires only when Phase 1 misses, covering clean sitemap slugs that
           have not yet been migrated back to the DB.

These tests guard against regressions in both phases and verify that
unknown slugs still return 404.
"""
from __future__ import annotations

import sys
import os
import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_async_cursor(rows):
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=list(rows))
    return cursor


# ---------------------------------------------------------------------------
# Published CMS document with a noisy seo_slug (Task #3 form).
# ---------------------------------------------------------------------------
_NOISY_DOC = {
    "id": "doc-bcom-001",
    "seo_slug": "bcom--2nd-sem---bcm--03-(2025)--(bcm0200304)",
    "title": "BCom 2nd Sem Paper 3",
    "meta_description": "Study materials for BCom 2nd semester.",
    "content": "...",
    "status": "published",
    "doc_type": "editorial",
    "meta": {},
}

_CLEAN_DOC = {
    "id": "doc-physics-001",
    "seo_slug": "physical-world",
    "title": "Physical World",
    "meta_description": "AHSEC Class 11 Physics chapter 1 notes.",
    "content": "...",
    "status": "published",
    "doc_type": "editorial",
    "meta": {},
}


@pytest.fixture
def cms_app(monkeypatch):
    """Install deps stub and wire up cms_sarvam_health router."""
    monkeypatch.setenv("ADMIN_JWT_SECRET", "c" * 64)

    for mod in list(sys.modules.keys()):
        if "routes.cms_sarvam_health" in mod or mod == "routes.cms_sarvam_health":
            sys.modules.pop(mod, None)

    from tests._deps_stub import install_deps_stub
    deps = install_deps_stub(force=True, is_mongo_available_value=True)
    db = deps.db

    # Default: Phase 1 exact match returns nothing.
    db.cms_documents.find_one = AsyncMock(return_value=None)
    db.cms_documents.find = MagicMock(return_value=_make_async_cursor([]))

    cms = importlib.import_module("routes.cms_sarvam_health")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(cms.router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)
    return client, db


# ---------------------------------------------------------------------------
# Phase 1: exact seo_slug hit — no fallback needed
# ---------------------------------------------------------------------------
def test_phase1_exact_seo_slug_match(cms_app):
    """Phase 1 exact match on seo_slug returns the document immediately."""
    client, db = cms_app
    db.cms_documents.find_one = AsyncMock(return_value=dict(_CLEAN_DOC))

    res = client.get("/api/content/cms-documents/physical-world")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "doc-physics-001"
    assert body["seo_slug"] == "physical-world"


# ---------------------------------------------------------------------------
# Phase 1: exact id hit
# ---------------------------------------------------------------------------
def test_phase1_exact_id_match(cms_app):
    """Phase 1 exact match on the ``id`` field works too."""
    client, db = cms_app
    db.cms_documents.find_one = AsyncMock(return_value=dict(_CLEAN_DOC))

    res = client.get("/api/content/cms-documents/doc-physics-001")
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# Phase 2: clean slug → noisy seo_slug fallback
# ---------------------------------------------------------------------------
def test_phase2_clean_slug_resolves_noisy_seo_slug(cms_app):
    """GET /learn/bcom-2nd-sem must resolve when DB stores the noisy slug.

    Phase 1 returns None (no exact match for 'bcom-2nd-sem').
    Phase 2 prefix-scans for seo_slug starting with 'bcom', finds the
    noisy doc, confirms clean_learn_slug(noisy) == 'bcom-2nd-sem', returns it.
    """
    client, db = cms_app
    db.cms_documents.find_one = AsyncMock(return_value=None)
    db.cms_documents.find = MagicMock(
        return_value=_make_async_cursor([dict(_NOISY_DOC)])
    )

    res = client.get("/api/content/cms-documents/bcom-2nd-sem")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == "doc-bcom-001"


# ---------------------------------------------------------------------------
# Phase 2: prefix narrows correctly — wrong-prefix doc is not returned
# ---------------------------------------------------------------------------
def test_phase2_prefix_narrows_candidates(cms_app):
    """A candidate whose seo_slug starts with a different first-word is
    never matched even if clean_learn_slug produces the same output."""
    client, db = cms_app
    # Candidate has prefix 'economics', not 'bcom'.
    wrong_prefix_doc = dict(_NOISY_DOC)
    wrong_prefix_doc["seo_slug"] = "economics--fa--05-(2025)--(fa0200501)"
    wrong_prefix_doc["id"] = "doc-eco-001"

    db.cms_documents.find_one = AsyncMock(return_value=None)
    # Phase 2 cursor returns only the wrong-prefix candidate.
    db.cms_documents.find = MagicMock(
        return_value=_make_async_cursor([wrong_prefix_doc])
    )

    res = client.get("/api/content/cms-documents/bcom-2nd-sem")
    # 'economics-...' cleaned to 'economics' ≠ 'bcom-2nd-sem' → 404.
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Phase 2 miss: completely unknown slug returns 404
# ---------------------------------------------------------------------------
def test_phase2_unknown_slug_returns_404(cms_app):
    """A slug with no DB match in either phase must return 404."""
    client, db = cms_app
    db.cms_documents.find_one = AsyncMock(return_value=None)
    db.cms_documents.find = MagicMock(return_value=_make_async_cursor([]))

    res = client.get("/api/content/cms-documents/totally-unknown-xyzzy")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Idempotency: already-clean slug in DB resolves via Phase 1
# ---------------------------------------------------------------------------
def test_already_clean_slug_resolves_via_phase1(cms_app):
    """An already-clean slug stored in seo_slug must resolve via Phase 1
    (exact match) without touching Phase 2 at all."""
    client, db = cms_app
    db.cms_documents.find_one = AsyncMock(return_value=dict(_CLEAN_DOC))

    res = client.get("/api/content/cms-documents/physical-world")
    assert res.status_code == 200
    # Phase 2 (find) must not be called when Phase 1 already matched.
    db.cms_documents.find.assert_not_called()
