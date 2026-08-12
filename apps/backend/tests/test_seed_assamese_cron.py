"""
Task 157 — Confirm the cron seed-assamese job skips chapters that already have
notes_as (or content_as) after a full re-run.

The admin-session endpoint (POST /content/seed-assamese) is already covered by
TestSeedAssameseFilterSkipsIngestedChapters in test_notes_editor_e2e.py.

This file covers the *cron* path:
    POST /api/v1/admin/cron/seed-assamese
    Auth: Bearer {TRANSLATE_CRON_SECRET}

A regression in the cron path (e.g. accidentally removing the $and from the
filter, or the Bearer-auth gate) would not be caught by the admin-session tests.

Assertions:
  force=False — Chapter.find() filter contains $and with both notes_as and
                content_as absence clauses.
  force=True  — Chapter.find() filter has no $and (all chapters with English
                content are re-queued regardless of existing Assamese fields).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


_CRON_SECRET = "test-cron-token-xyz"
_CRON_URL = "/api/v1/admin/cron/seed-assamese"


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def set_cron_secret():
    """Inject a known TRANSLATE_CRON_SECRET so Bearer auth succeeds in tests."""
    from app.config import settings
    original = settings.TRANSLATE_CRON_SECRET
    settings.TRANSLATE_CRON_SECRET = _CRON_SECRET
    yield
    settings.TRANSLATE_CRON_SECRET = original


def _cron_headers() -> dict:
    return {"Authorization": f"Bearer {_CRON_SECRET}"}


def _empty_chapter_qs():
    qs = MagicMock()
    qs.to_list = AsyncMock(return_value=[])
    return qs


# ── auth guard ────────────────────────────────────────────────────────────────

class TestCronSeedAssameseAuth:
    """The cron endpoint must reject requests without a valid Bearer token."""

    def test_missing_auth_header_returns_401(self, client):
        resp = client.post(_CRON_URL, json={})
        assert resp.status_code == 401

    def test_wrong_token_returns_403(self, client):
        resp = client.post(
            _CRON_URL,
            json={},
            headers={"Authorization": "Bearer wrong-secret"},
        )
        assert resp.status_code == 403

    def test_valid_token_passes_auth_gate(self, client):
        """A valid token should reach the endpoint logic (not 401/403)."""
        with (
            patch("app.models.content.Chapter.find", return_value=_empty_chapter_qs()),
            patch("app.models.seed_run.SeedRun", MagicMock()),
        ):
            resp = client.post(_CRON_URL, json={}, headers=_cron_headers())
        assert resp.status_code not in (401, 403)


# ── filter shape: force=False ─────────────────────────────────────────────────

class TestCronSeedAssameseFilterForceOff:
    """With force=False the Chapter.find() filter must exclude chapters that
    already have notes_as OR content_as via a $and clause covering both fields."""

    def _run(self, client, body: dict) -> dict:
        """POST the cron endpoint and return the captured Chapter.find() filter."""
        captured: dict = {}

        def _capture_find(filt=None, **kw):
            if filt:
                captured.update(filt)
            return _empty_chapter_qs()

        with (
            patch("app.models.content.Chapter.find", side_effect=_capture_find),
            patch("app.models.seed_run.SeedRun", MagicMock()),
        ):
            resp = client.post(_CRON_URL, json=body, headers=_cron_headers())

        assert resp.status_code == 200, resp.text
        return captured

    def test_filter_contains_and_clause(self, client):
        """$and must be present in the filter when force=False."""
        filt = self._run(client, {"force": False})
        assert "$and" in filt, (
            "The cron seed-assamese filter must use $and to check both "
            "notes_as and content_as absence (got: %s)" % filt
        )

    def test_and_clause_has_two_branches(self, client):
        """$and must have exactly two branches — one per Assamese field."""
        filt = self._run(client, {"force": False})
        and_clauses = filt["$and"]
        assert len(and_clauses) == 2, (
            "Expected 2 $and branches (notes_as, content_as), got %d" % len(and_clauses)
        )

    def test_and_clause_checks_notes_as_absence(self, client):
        """One $and branch must check that notes_as is absent/empty."""
        filt = self._run(client, {"force": False})
        fields: set[str] = set()
        for clause in filt["$and"]:
            assert "$or" in clause, "Each $and branch should be an $or of absence conditions"
            for cond in clause["$or"]:
                fields.update(cond.keys())
        assert "notes_as" in fields, (
            "notes_as absence must be part of the $and filter — "
            "chapters with notes_as='...' must be skipped"
        )

    def test_and_clause_checks_content_as_absence(self, client):
        """One $and branch must check that content_as is absent/empty."""
        filt = self._run(client, {"force": False})
        fields: set[str] = set()
        for clause in filt["$and"]:
            for cond in clause["$or"]:
                fields.update(cond.keys())
        assert "content_as" in fields, (
            "content_as absence must be part of the $and filter — "
            "legacy chapters with content_as='...' must be skipped"
        )

    def test_default_body_behaves_same_as_force_false(self, client):
        """An empty body (force defaults to False) must produce the same $and filter."""
        filt = self._run(client, {})
        assert "$and" in filt, "Empty body must default to force=False behaviour"


# ── filter shape: force=True ──────────────────────────────────────────────────

class TestCronSeedAssameseFilterForceOn:
    """With force=True the filter must NOT restrict by Assamese content fields —
    every chapter that has English content is eligible for re-translation."""

    def _run_force(self, client) -> dict:
        captured: dict = {}

        def _capture_find(filt=None, **kw):
            if filt:
                captured.update(filt)
            return _empty_chapter_qs()

        with (
            patch("app.models.content.Chapter.find", side_effect=_capture_find),
            patch("app.models.seed_run.SeedRun", MagicMock()),
        ):
            resp = client.post(_CRON_URL, json={"force": True}, headers=_cron_headers())

        assert resp.status_code == 200, resp.text
        return captured

    def test_force_true_omits_and_clause(self, client):
        """$and must not appear in the filter when force=True."""
        filt = self._run_force(client)
        assert "$and" not in filt, (
            "force=True must not add an $and restriction — all chapters with "
            "English content should be queued for re-translation"
        )

    def test_force_true_filter_includes_english_content_check(self, client):
        """The filter must still require at least one English field to be present."""
        filt = self._run_force(client)
        # The $or that selects English-content chapters must be present
        assert "$or" in filt, (
            "force=True filter must still require notes_en or content_en to be set"
        )
