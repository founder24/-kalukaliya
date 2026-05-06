"""Task #465 — unit tests for ``aca_jobs.as_translation_backfill``.

Covers the post-rebase implementation that delegates translation to
the centralized V4 §4 chain in
``routes.ai_chat._assamese_translate_gemini_main_sarvam_polish``:

  * Selection rule — ``_bengali_letter_ratio`` + ``_doc_needs_translation``
    (script-ratio gate, source-hash invalidation, English-fallback
    detection)
  * Per-pass loop — ``_process_one_collection`` advances the
    ``last_processed_id`` cursor and persists state under
    ``as_translation_state``
  * Failure isolation — when ``_translate_to_assamese`` returns "" or
    a non-Assamese string, the doc's ``*_as`` field stays UNTOUCHED
    (no silent English fallback per V4 §12)
  * Progress payload covers every collection in ``FIELD_MAP``
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aca_jobs import as_translation_backfill as bf  # noqa: E402


# ── Minimal motor-shaped stub ───────────────────────────────────────────────
class _AsyncCursor:
    def __init__(self, items):
        self._items = list(items)

    def sort(self, *_a, **_kw):
        return self

    def limit(self, n):
        self._items = self._items[: int(n)]
        return self

    async def to_list(self, length=None):
        items = self._items
        if length is not None:
            items = items[: int(length)]
        # Drain so the next find() yields nothing — simulates batch
        # exhaustion in the resumable loop.
        self._items = []
        return items


class _Collection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find(self, cond, projection=None):
        gt = None
        if isinstance(cond, dict) and "_id" in cond:
            gt = (cond.get("_id") or {}).get("$gt")
        items = [d for d in self.docs if gt is None or d["_id"] > gt]
        return _AsyncCursor(items)

    async def count_documents(self, cond=None):
        return len(self.docs)

    async def update_one(self, filt, update, upsert=False):
        target = None
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                target = d
                break
        if target is None and upsert:
            target = dict(filt)
            for k, v in (update.get("$setOnInsert") or {}).items():
                target.setdefault(k, v)
            self.docs.append(target)
        if target is not None and "$set" in update:
            target.update(update["$set"])
        return MagicMock()

    async def find_one(self, filt, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                return d
        return None

    async def bulk_write(self, ops, ordered=False):
        # Each op is a pymongo.UpdateOne; for tests we only need
        # to apply the $set against the matching doc.
        for op in ops:
            try:
                filt = op._filter      # pymongo internal — fine for tests
                update = op._doc
            except AttributeError:
                continue
            for d in self.docs:
                if all(d.get(k) == v for k, v in filt.items()):
                    if "$set" in update:
                        d.update(update["$set"])
                    break
        return MagicMock(modified_count=len(ops))


class _DB:
    def __init__(self):
        self._colls: dict[str, _Collection] = {
            bf.STATE_COLLECTION: _Collection(),
        }

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _Collection()
        return self._colls[name]


@pytest.fixture
def db():
    return _DB()


# ── _bengali_letter_ratio ───────────────────────────────────────────────────
def test_bengali_ratio_pure_english_is_zero():
    assert bf._bengali_letter_ratio("Newton's laws of motion") == 0.0


def test_bengali_ratio_pure_assamese_is_one():
    assert bf._bengali_letter_ratio("নিউটনৰ গতিৰ সূত্ৰসমূহ") == 1.0


def test_bengali_ratio_mixed_below_threshold_for_mostly_english():
    # Mostly English, a sprinkle of Assamese — must NOT clear 0.60.
    txt = "Newton's laws of motion ক"
    assert bf._bengali_letter_ratio(txt) < bf.MIN_AS_SCRIPT_RATIO


# ── _doc_needs_translation ──────────────────────────────────────────────────
def test_needs_translation_missing_as_field():
    doc = {"name": "Physics study guide content body"}
    pending = bf._doc_needs_translation(doc, ["name"])
    assert pending == [("name", "Physics study guide content body")]


def test_needs_translation_english_in_as_field_treated_as_stale():
    """An ``_as`` populated with English (the silent-fallback bug
    Task #465 is fixing) MUST be re-translated."""
    doc = {"name": "Physics study guide", "name_as": "Physics study guide"}
    pending = bf._doc_needs_translation(doc, ["name"])
    assert len(pending) == 1


def test_needs_translation_satisfied_when_assamese_present_and_hash_matches():
    src = "Physics study guide content body"
    doc = {
        "name":              src,
        "name_as":           "পদাৰ্থ বিজ্ঞান অধ্যয়ন গাইড সম্পূৰ্ণ",
        "name_as_src_hash":  bf._hash_source(src),
    }
    assert bf._doc_needs_translation(doc, ["name"]) == []


def test_needs_translation_re_translates_when_source_hash_drifts():
    """Editing the English source must force re-translation."""
    doc = {
        "name":              "Physics edited body",
        "name_as":           "পদাৰ্থ বিজ্ঞান অধ্যয়ন গাইড সম্পূৰ্ণ",
        "name_as_src_hash":  bf._hash_source("Physics original body"),
    }
    assert bf._doc_needs_translation(doc, ["name"]) == [
        ("name", "Physics edited body")
    ]


def test_needs_translation_skips_when_english_field_too_short():
    doc = {"name": "P"}
    assert bf._doc_needs_translation(doc, ["name"]) == []


# ── _process_one_collection — happy path + cursor advance ───────────────────
async def test_process_one_collection_writes_as_fields_and_advances_cursor(
    db, monkeypatch
):
    db["subjects"].docs = [
        {"_id": 1, "name": "Physics study guide content body for AHSEC"},
        {"_id": 2, "name": "Maths study guide content body for AHSEC"},
    ]

    async def _fake_translate(text):
        return "অনুবাদ অনুবাদ অনুবাদ অনুবাদ অনুবাদ অনুবাদ অনুবাদ অনুবাদ"

    monkeypatch.setattr(bf, "_translate_to_assamese", _fake_translate)
    monkeypatch.setattr(bf, "INTER_DOC_SLEEP_S", 0)

    summary = await bf._process_one_collection(
        db, "subjects", max_docs=10, batch_size=5,
    )
    assert summary["processed"] == 2
    assert summary["translated"] == 2
    assert summary["failed"] == 0

    s1 = next(d for d in db["subjects"].docs if d["_id"] == 1)
    assert s1["name_as"].startswith("অনুবাদ")
    assert "name_as_src_hash" in s1
    assert "name_as_translated_at" in s1

    state = await db[bf.STATE_COLLECTION].find_one({"_id": "subjects"})
    assert state is not None
    assert state.get("last_processed_id") == 2
    assert state.get("running") is False
    assert "last_run" in state


# ── Failure isolation: V4 §12, no silent fallback ───────────────────────────
async def test_failed_translation_does_not_write_as_field(db, monkeypatch):
    db["subjects"].docs = [
        {"_id": 1, "name": "Physics study guide content body for AHSEC"},
    ]

    async def _always_fail(_text):
        return ""  # Provider chain failed; helper returns "" per V4 §12.

    monkeypatch.setattr(bf, "_translate_to_assamese", _always_fail)
    monkeypatch.setattr(bf, "INTER_DOC_SLEEP_S", 0)

    summary = await bf._process_one_collection(
        db, "subjects", max_docs=5, batch_size=5,
    )
    assert summary["translated"] == 0
    assert summary["failed"] == 1
    doc = db["subjects"].docs[0]
    assert "name_as" not in doc, (
        "translation failed but driver silently wrote a fallback — "
        "violates V4 §12"
    )


async def test_low_assamese_ratio_translation_rejected(db, monkeypatch):
    """If the provider returns mostly-Latin text (e.g. echoed English),
    the doc must be marked failed without writing anything."""
    db["subjects"].docs = [
        {"_id": 1, "name": "Physics study guide content body for AHSEC"},
    ]

    async def _english_echo(_text):
        return "Physics in Assamese"

    monkeypatch.setattr(bf, "_translate_to_assamese", _english_echo)
    monkeypatch.setattr(bf, "INTER_DOC_SLEEP_S", 0)

    summary = await bf._process_one_collection(
        db, "subjects", max_docs=5, batch_size=5,
    )
    assert summary["failed"] == 1
    assert "name_as" not in db["subjects"].docs[0]


# ── get_progress covers every managed collection ────────────────────────────
async def test_get_progress_includes_every_managed_collection(db):
    progress = await bf.get_progress(db)
    assert set(progress["collections"]) == set(bf.FIELD_MAP)
    for coll, info in progress["collections"].items():
        assert info["fields"] == bf.FIELD_MAP[coll]
        assert "remaining" in info
        assert "translated" in info


# ── run_backfill end-to-end ─────────────────────────────────────────────────
async def test_run_backfill_returns_per_collection_results(db, monkeypatch):
    db["subjects"].docs = [
        {"_id": 1, "name": "Physics study guide content body"},
    ]

    async def _ok(text):
        return "অনুবাদ " * 20

    monkeypatch.setattr(bf, "_translate_to_assamese", _ok)
    monkeypatch.setattr(bf, "INTER_DOC_SLEEP_S", 0)

    out = await bf.run_backfill(
        db, collections=["subjects"], max_docs=5, batch_size=5,
    )
    assert "results" in out
    assert out["results"][0]["collection"] == "subjects"
    assert out["results"][0]["translated"] == 1


async def test_run_backfill_rejects_unknown_collection(db):
    out = await bf.run_backfill(db, collections=["does_not_exist"])
    assert out.get("error") == "unknown_collection"
    assert out.get("unknown") == ["does_not_exist"]
