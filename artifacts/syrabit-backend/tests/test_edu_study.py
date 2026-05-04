"""Task #594 — backend tests for the Phase 3 study surfaces.

Coverage:
  * Pure helpers: ``_sm2_step`` (SM-2 scheduling math), ``_split_front_back``
    (notebook → flashcard heuristic), ``_norm_tags``, ``_coerce_quiz_payload``.
  * Route ``POST /edu/quiz/generate`` — happy path with mocked LLM,
    parser tolerance for fenced JSON, validation rejection of malformed
    questions, rate-limiter cap.
  * Route ``GET /edu/notes`` (search) and ``GET /edu/notes/export`` (md/csv)
    against a fake asyncpg pool so we exercise the real SQL parameter
    plumbing without standing up Postgres.
  * Route ``POST /edu/guardian/pin/verify`` — exercises the Task #594
    rate limiter (8 attempts per 5 min) so a brute-force attacker can't
    walk the 10⁴ PIN space in seconds.
  * Route ``POST /edu/flashcards/build`` — Task #215: generated-note path
    (Q&A extraction + mnemonic cards) and manual-note heuristic path.
"""
from __future__ import annotations

import os
import sys
import time
import asyncio
import pathlib
import importlib
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

# Pin backend root on sys.path so ``import deps`` etc. resolve.
_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ───────────────────────── Fake asyncpg pool ─────────────────────────

class _FakeConn:
    """Minimal AsyncConnection stand-in. Each method records the SQL it
    was handed plus the args, and returns whatever the fixture pre-loaded
    in ``self._responses`` (a list, FIFO)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []  # list[(method, sql, args)]

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return self._next("execute")

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self._next("fetch")

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self._next("fetchrow")

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self._next("fetchval")

    def transaction(self):
        # used by /edu/sync/claim; not exercised here but harmless
        conn = self
        class _T:
            async def __aenter__(self_inner):
                return conn
            async def __aexit__(self_inner, *a):
                return False
        return _T()

    def _next(self, _method):
        if not self._responses:
            return None
        return self._responses.pop(0)


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn
        class _Cm:
            async def __aenter__(self):
                return conn
            async def __aexit__(self, *a):
                return False
        return _Cm()


@pytest.fixture
def fake_conn_factory():
    """Returns a builder that takes a ``responses`` list and patches
    ``deps.pg_pool`` with a pool acquiring a recording fake conn."""
    saved = []

    def _build(responses):
        # Bypass _ensure_schema's schema bootstrap by pre-marking it ready.
        from routes import edu_study
        edu_study._SCHEMA_READY = True
        conn = _FakeConn(responses)
        import deps
        deps.pg_pool = _FakePool(conn)
        saved.append(deps)
        return conn

    yield _build
    for d in saved:
        d.pg_pool = None


# ───────────────────────── App fixture ─────────────────────────

@pytest.fixture
def edu_app():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.edu_study import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


@pytest.fixture(autouse=True)
def _deterministic_quiz_shuffle(monkeypatch):
    """Disable the question / choice shuffling that ``_sample_and_shuffle``
    performs so the assertions in this file (which compare against the
    raw LLM payload order) remain stable. Production behaviour is
    exercised separately in dedicated shuffle tests; here we want to
    verify pass-through of the cleaned payload."""
    import routes.edu_study as _es
    monkeypatch.setattr(_es.random, "shuffle", lambda seq: None)
    monkeypatch.setattr(_es.random, "sample",
                        lambda population, k: list(population)[:k])
    yield


# ───────────────────────── Pure helpers ─────────────────────────

def test_sm2_failed_recall_resets_progress():
    """A grade < 3 must reset reps to 0 and schedule the card for tomorrow
    (interval=1) — this is the "Again" pile in the UI."""
    from routes.edu_study import _sm2_step
    ef, reps, interval = _sm2_step(ef=2.5, reps=4, interval=15, q=2)
    assert reps == 0
    assert interval == 1
    # EF must drop on a lapse but never below the 1.3 floor.
    assert 1.3 <= ef <= 2.4


def test_sm2_easiness_floor_holds():
    """Repeated failures cannot push EF below 1.3 — the SM-2 spec floor."""
    from routes.edu_study import _sm2_step
    ef = 2.5
    for _ in range(20):
        ef, _r, _i = _sm2_step(ef, 0, 1, 0)
    assert ef == pytest.approx(1.3)


def test_sm2_first_two_reviews_use_canonical_intervals():
    """First successful review → 1 day. Second → 6 days. Third onward →
    interval * EF (rounded)."""
    from routes.edu_study import _sm2_step
    ef1, reps1, int1 = _sm2_step(ef=2.5, reps=0, interval=0, q=4)
    assert reps1 == 1 and int1 == 1
    ef2, reps2, int2 = _sm2_step(ef=ef1, reps=reps1, interval=int1, q=4)
    assert reps2 == 2 and int2 == 6
    ef3, reps3, int3 = _sm2_step(ef=ef2, reps=reps2, interval=int2, q=4)
    assert reps3 == 3
    assert int3 == round(int2 * ef2)


def test_split_front_back_handles_definition_pattern():
    from routes.edu_study import _split_front_back
    f, b = _split_front_back("Photosynthesis — process by which plants make food")
    assert f == "Photosynthesis"
    assert "process by which plants make food" in b


def test_split_front_back_falls_back_to_first_sentence():
    from routes.edu_study import _split_front_back
    f, b = _split_front_back("Mitochondria are the powerhouse of the cell. They make ATP.")
    assert f == "Recall this idea:"
    assert "powerhouse" in b


def test_norm_tags_dedupes_lowercases_caps_at_twelve():
    from routes.edu_study import _norm_tags
    tags = _norm_tags(["Bio", "bio", "  Genetics ", "DNA replication"] + [f"x{i}" for i in range(20)])
    assert "bio" in tags
    assert tags.count("bio") == 1
    assert "genetics" in tags
    assert "dna-replication" in tags
    assert len(tags) <= 12


def test_coerce_quiz_payload_strips_code_fences():
    from routes.edu_study import _coerce_quiz_payload
    raw = """Some preamble
```json
{"questions": [{"q": "1?", "choices": ["a","b","c","d"], "answer": 0}]}
```
trailing chatter"""
    out = _coerce_quiz_payload(raw)
    assert out["questions"][0]["q"] == "1?"


def test_coerce_quiz_payload_raises_on_garbage():
    from fastapi import HTTPException
    from routes.edu_study import _coerce_quiz_payload
    with pytest.raises(HTTPException) as exc:
        _coerce_quiz_payload("definitely not json at all")
    assert exc.value.status_code == 502


# ───────────────────────── /edu/quiz/generate ─────────────────────────

_GOOD_LLM = (
    '{"questions": ['
    '  {"q":"What is 2+2?","choices":["3","4","5","6"],"answer":1,"explanation":"Basic addition."},'
    '  {"q":"Capital of France?","choices":["Berlin","Madrid","Paris","Rome"],"answer":2,"explanation":"Paris is the capital."},'
    '  {"q":"H2O is?","choices":["Water","Oil","Acid","Salt"],"answer":0,"explanation":"H2O is water."}'
    ']}'
)


def test_quiz_generate_returns_cleaned_questions(edu_app):
    with patch("routes.edu_study._az_quiz_chat", new=AsyncMock(return_value=_GOOD_LLM)):
        res = edu_app.post("/api/edu/quiz/generate", json={
            "context": "Some chapter text about basic facts." * 5,
            "topic": "General knowledge",
            "count": 3,
        })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["count"] == 3
    qs = body["questions"]
    assert qs[0]["choices"] == ["3", "4", "5", "6"]
    assert qs[0]["answer"] == 1
    # Each cleaned question gets a stable id assigned server-side.
    assert all(isinstance(q["id"], str) and q["id"] for q in qs)


def test_quiz_generate_drops_questions_with_wrong_choice_count(edu_app):
    """The LLM occasionally returns 3 or 5 choices. The route must skip
    those entries instead of returning a malformed payload (which would
    crash the front-end MCQ UI)."""
    bad = (
        '{"questions": ['
        '  {"q":"only three?","choices":["a","b","c"],"answer":0},'
        '  {"q":"good one","choices":["a","b","c","d"],"answer":3,"explanation":"d wins"}'
        ']}'
    )
    with patch("routes.edu_study._az_quiz_chat", new=AsyncMock(return_value=bad)):
        res = edu_app.post("/api/edu/quiz/generate", json={
            "context": "Lorem ipsum dolor sit amet " * 10,
            "count": 3,
        })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["count"] == 1
    assert body["questions"][0]["q"] == "good one"
    assert body["questions"][0]["answer"] == 3


def test_quiz_generate_clamps_out_of_range_answer_index(edu_app):
    """If the LLM returns ``answer: 7`` for a 4-choice MCQ, the route
    must clamp to 0 rather than render an unscoreable question."""
    bad = (
        '{"questions": ['
        '  {"q":"x?","choices":["a","b","c","d"],"answer":7,"explanation":""}'
        ']}'
    )
    with patch("routes.edu_study._az_quiz_chat", new=AsyncMock(return_value=bad)):
        res = edu_app.post("/api/edu/quiz/generate", json={
            "context": "ctx " * 30, "count": 3,
        })
    assert res.status_code == 200
    assert res.json()["questions"][0]["answer"] == 0


def test_quiz_generate_rejects_empty_request(edu_app):
    res = edu_app.post("/api/edu/quiz/generate", json={"count": 3})
    assert res.status_code == 400
    assert res.json()["detail"] == "context or topic required"


def test_quiz_generate_returns_502_when_llm_returns_no_valid_questions(edu_app):
    """When every question in the LLM payload fails validation, the
    route must surface a 502 instead of returning an empty list (which
    the frontend would render as "0 / 0", a confusing dead-end)."""
    bad = '{"questions": [{"q":"x","choices":["a","b","c"],"answer":0}]}'
    with patch("routes.edu_study._az_quiz_chat", new=AsyncMock(return_value=bad)):
        res = edu_app.post("/api/edu/quiz/generate", json={
            "context": "ctx " * 30, "count": 3,
        })
    assert res.status_code == 502
    assert res.json()["detail"] == "quiz_no_questions"


def test_quiz_generate_rate_limiter_caps_at_15_per_5_min(edu_app):
    """The route allows 15 quiz requests per IP per 5 minutes. The 16th
    must 429 — guards against an unbounded LLM bill from a single
    misbehaving client."""
    from auth_deps import _rate_windows
    _rate_windows.clear()
    with patch("routes.edu_study._az_quiz_chat", new=AsyncMock(return_value=_GOOD_LLM)):
        for _ in range(15):
            r = edu_app.post("/api/edu/quiz/generate",
                             json={"context": "abc " * 30, "count": 3})
            assert r.status_code == 200
        r = edu_app.post("/api/edu/quiz/generate",
                         json={"context": "abc " * 30, "count": 3})
    assert r.status_code == 429


# ───────────────────────── /edu/notes (search + export) ─────────────────────────

def _make_note_row(idx, text="hello world", tags=None, source_title="Source",
                   source_url="https://example.org/x", chapter_ref="ch-1"):
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    return {
        "id": f"note-{idx}",
        "actor_kind": "anon",
        "actor": "ip:abc",
        "text": text,
        "source_url": source_url,
        "source_title": source_title,
        "chapter_ref": chapter_ref,
        "tags": list(tags or []),
        "created_at": now,
        "updated_at": now,
    }


def test_notes_search_passes_q_and_tag_filters(edu_app, fake_conn_factory):
    rows = [_make_note_row(1, text="photosynthesis", tags=["bio"]),
            _make_note_row(2, text="photo basics",   tags=["bio"])]
    conn = fake_conn_factory(responses=[rows])

    res = edu_app.get("/api/edu/notes?q=photo&tag=bio&limit=50")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["count"] == 2
    assert {n["id"] for n in body["notes"]} == {"note-1", "note-2"}

    # The route must have driven its SQL with the user's q + tag filters
    # AND the limit/offset args. Catching a regression here is the
    # whole point of this test — a bug in the parametrised SQL builder
    # would silently return the wrong rows.
    sql, args = conn.calls[-1][1], conn.calls[-1][2]
    assert "ILIKE" in sql
    assert "tags &&" in sql
    assert "%photo%" in args
    assert ["bio"] in args
    assert 50 in args
    assert 0 in args


def test_notes_export_csv_streams_attachment(edu_app, fake_conn_factory):
    rows = [
        _make_note_row(1, text="note one", tags=["bio", "exam"],
                       source_title="Chapter 1", source_url="https://e/1"),
        _make_note_row(2, text="note two", tags=[],
                       source_title="", source_url=""),
    ]
    fake_conn_factory(responses=[rows])

    res = edu_app.get("/api/edu/notes/export?format=csv")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "syrabit-notebook.csv" in res.headers["content-disposition"]
    body = res.text
    assert "note one" in body
    assert "note two" in body
    # Tags must be joined with commas in the CSV cell.
    assert "bio,exam" in body
    # Header row.
    assert body.splitlines()[0].startswith("created_at,text,")


def test_notes_export_md_default(edu_app, fake_conn_factory):
    rows = [_make_note_row(1, text="markdown body", tags=["alpha"])]
    fake_conn_factory(responses=[rows])

    res = edu_app.get("/api/edu/notes/export")  # no format → md
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
    body = res.text
    assert body.startswith("# Syrabit Notebook")
    assert "markdown body" in body
    assert "`alpha`" in body  # tag rendered as inline code


# ───────────────────────── /edu/guardian/pin/verify rate limit ─────────────────────────

def _patch_no_pin_set():
    """Patch deps.pg_pool so pin/verify thinks no PIN is set — the route
    will then return ``valid=True, set=False`` very fast, letting us
    pound on it without other DB-related noise."""
    import deps
    from routes import edu_study
    edu_study._SCHEMA_READY = True
    conn = _FakeConn(responses=[None] * 100)  # fetchrow always returns None
    deps.pg_pool = _FakePool(conn)
    return conn


def test_pin_verify_rate_limit_after_eight_attempts(edu_app):
    """Task #594: PIN verify is capped at 8 attempts per 5 min per actor.
    The 9th attempt must 429 — without this, an attacker could brute the
    10⁴ PIN space in a few seconds."""
    from auth_deps import _rate_windows
    _rate_windows.clear()
    _patch_no_pin_set()

    headers = {"x-anon-id": "device-attacker-1"}
    for i in range(8):
        r = edu_app.post("/api/edu/guardian/pin/verify",
                         json={"pin": f"{1000 + i}"}, headers=headers)
        assert r.status_code == 200, f"attempt {i} got {r.status_code}: {r.text}"

    blocked = edu_app.post("/api/edu/guardian/pin/verify",
                           json={"pin": "9999"}, headers=headers)
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "pin_verify_rate_limited"


def test_pin_verify_rate_limit_is_per_actor(edu_app):
    """A different anon device id gets its own bucket — one attacker
    must not lock out another guardian on a shared NAT."""
    from auth_deps import _rate_windows
    _rate_windows.clear()
    _patch_no_pin_set()

    h1 = {"x-anon-id": "device-A"}
    h2 = {"x-anon-id": "device-B"}
    for _ in range(8):
        edu_app.post("/api/edu/guardian/pin/verify",
                     json={"pin": "1234"}, headers=h1)
    blocked = edu_app.post("/api/edu/guardian/pin/verify",
                           json={"pin": "1234"}, headers=h1)
    assert blocked.status_code == 429

    # Second device starts fresh.
    fresh = edu_app.post("/api/edu/guardian/pin/verify",
                         json={"pin": "1234"}, headers=h2)
    assert fresh.status_code == 200


# ─────────────── /edu/flashcards/build — Task #215 ───────────────

import json as _json_mod


def _make_generated_note_row(
    note_id: str = "gen-note-1",
    *,
    generated: bool = True,
    structured=None,
    text: str = "",
):
    """Build a fake asyncpg row dict for a generated or manual note.

    *structured* is serialised to a JSON string (as Postgres stores it)
    when provided and *generated* is True; it is left as ``None`` for
    manual notes so the route exercises the ``"structured" not in keys``
    fallback.
    """
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    row: dict = {
        "id": note_id,
        "actor_kind": "anon",
        "actor": "ip:test",
        "generated": generated,
        "text": text,
        "source_url": "https://example.org",
        "source_title": "Chapter 1",
        "chapter_ref": "ch-1",
        "tags": [],
        "created_at": now,
        "updated_at": now,
    }
    if structured is not None:
        row["structured"] = _json_mod.dumps(structured)
    return row


def _flashcard_inserts(conn) -> list[tuple[str, str]]:
    """Return (front, back) tuples for every edu_flashcards INSERT recorded."""
    return [
        (c[2][4], c[2][5])
        for c in conn.calls
        if c[0] == "execute" and "edu_flashcards" in c[1]
    ]


def test_build_flashcards_mnemonic_produces_correct_front_and_back(
    edu_app, fake_conn_factory
):
    """A generated note with a mnemonic must produce a card whose front is
    'Mnemonic for: <topic>' and whose back contains the phrase and explanation."""
    structured = {
        "qa": [],
        "mnemonics": [
            {
                "for": "Laws of Motion",
                "mnemonic": "FANF – Force Accelerates Non-Frozen objects",
                "explanation": "Newton's second law: F = ma",
            }
        ],
    }
    conn = fake_conn_factory(responses=[[_make_generated_note_row(structured=structured)]])

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "created": 1}

    cards = _flashcard_inserts(conn)
    assert len(cards) == 1
    front, back = cards[0]
    assert front == "Mnemonic for: Laws of Motion"
    assert "FANF" in back
    assert "Newton's second law" in back


def test_build_flashcards_qa_pairs_produce_one_card_each(
    edu_app, fake_conn_factory
):
    """Each Q&A pair in a generated note must become exactly one flashcard
    with front=question and back=answer."""
    structured = {
        "qa": [
            {"q": "What is photosynthesis?",
             "a": "The process plants use to make food from sunlight."},
            {"q": "Where does photosynthesis occur?",
             "a": "In the chloroplasts."},
        ],
        "mnemonics": [],
    }
    conn = fake_conn_factory(
        responses=[[_make_generated_note_row(note_id="gen-qa", structured=structured)]]
    )

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 2

    cards = _flashcard_inserts(conn)
    assert len(cards) == 2
    fronts = {c[0] for c in cards}
    backs = {c[1] for c in cards}
    assert "What is photosynthesis?" in fronts
    assert "Where does photosynthesis occur?" in fronts
    assert any("chloroplasts" in b for b in backs)


def test_build_flashcards_manual_note_uses_split_heuristic(
    edu_app, fake_conn_factory
):
    """A manual (non-generated) note must use _split_front_back, not the
    structured extraction path. A 'X — Y' note → front=X, back=Y."""
    row = _make_generated_note_row(
        note_id="manual-1",
        generated=False,
        text="Photosynthesis — process by which plants make food using sunlight",
    )
    conn = fake_conn_factory(responses=[[row]])

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 1

    cards = _flashcard_inserts(conn)
    assert len(cards) == 1
    front, back = cards[0]
    assert front == "Photosynthesis"
    assert "plants make food" in back


def test_build_flashcards_generated_note_no_content_emits_zero_cards(
    edu_app, fake_conn_factory
):
    """A generated note with no Q&A pairs and no mnemonics must produce
    zero flashcards — a graceful no-op, not a crash or junk card."""
    structured = {"qa": [], "mnemonics": []}
    conn = fake_conn_factory(
        responses=[[_make_generated_note_row(note_id="gen-empty", structured=structured)]]
    )

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "created": 0}
    assert _flashcard_inserts(conn) == []


def test_build_flashcards_mnemonic_without_explanation_back_is_phrase_only(
    edu_app, fake_conn_factory
):
    """When a mnemonic has no explanation, the card back must be the
    phrase alone — no trailing newlines, empty lines, or 'None' text."""
    structured = {
        "qa": [],
        "mnemonics": [
            {"for": "Colour bands", "mnemonic": "ROY G BIV", "explanation": ""},
        ],
    }
    conn = fake_conn_factory(responses=[[_make_generated_note_row(structured=structured)]])

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text

    _front, back = _flashcard_inserts(conn)[0]
    assert back == "ROY G BIV"
    assert "None" not in back


def test_build_flashcards_respects_qa_cap_of_eight(edu_app, fake_conn_factory):
    """Q&A extraction is capped at 8 cards — more than 8 pairs in the
    structured payload must not produce extra cards, must keep the first 8
    pairs in order, and must not include pairs 8-11."""
    structured = {
        "qa": [{"q": f"Q{i}?", "a": f"Answer {i}"} for i in range(12)],
        "mnemonics": [],
    }
    conn = fake_conn_factory(responses=[[_make_generated_note_row(structured=structured)]])

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 8

    cards = _flashcard_inserts(conn)
    fronts = [front for front, _back in cards]

    assert fronts == [f"Q{i}?" for i in range(8)], (
        f"Expected first 8 questions in order (Q0?–Q7?), got: {fronts}"
    )

    excluded = {f"Q{i}?" for i in range(8, 12)}
    assert not excluded.intersection(fronts), (
        f"Pairs 8–11 must not appear in stored cards, but found: "
        f"{excluded.intersection(fronts)}"
    )


def test_build_flashcards_structured_as_json_string_is_decoded(
    edu_app, fake_conn_factory
):
    """When n['structured'] is stored as a JSON string (Postgres text column),
    the route must decode it before extracting Q&A and mnemonics."""
    row = _make_generated_note_row(
        structured={
            "qa": [{"q": "Define osmosis.", "a": "Movement of water across a membrane."}],
            "mnemonics": [],
        }
    )
    conn = fake_conn_factory(responses=[[row]])

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 1

    cards = _flashcard_inserts(conn)
    assert cards[0][0] == "Define osmosis."
    assert "membrane" in cards[0][1]


def test_build_flashcards_mixed_note_list_routes_each_correctly(
    edu_app, fake_conn_factory
):
    """When a generated note and a manual note appear together, each must
    take its own extraction path independently."""
    gen_structured = {
        "qa": [{"q": "What is mitosis?", "a": "Cell division producing two identical cells."}],
        "mnemonics": [],
    }
    gen_row = _make_generated_note_row(note_id="gen", structured=gen_structured)
    manual_row = _make_generated_note_row(
        note_id="man",
        generated=False,
        text="ATP — adenosine triphosphate, the energy currency of the cell",
    )
    conn = fake_conn_factory(responses=[[gen_row, manual_row]])

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 2

    cards = _flashcard_inserts(conn)
    fronts = {c[0] for c in cards}
    backs = " ".join(c[1] for c in cards)
    assert "What is mitosis?" in fronts   # generated Q&A path
    assert "ATP" in fronts                # manual heuristic path
    assert "two identical cells" in backs


# ─────────────── Task #224 — edge-case fallbacks ───────────────

def test_build_flashcards_missing_structured_field_produces_zero_cards(
    edu_app, fake_conn_factory
):
    """A generated note where the ``structured`` column is absent from the DB
    row (``"structured" not in n.keys()``) must produce 0 cards and return
    normally — no KeyError, no crash.

    ``_make_generated_note_row()`` with no ``structured`` argument omits the
    key entirely, exercising the ``else None`` branch in build_flashcards.
    """
    row = _make_generated_note_row(note_id="gen-no-struct", generated=True)
    assert "structured" not in row, "Precondition: key must be absent from row"

    conn = fake_conn_factory(responses=[[row]])

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "created": 0}
    assert _flashcard_inserts(conn) == [], "No INSERTs should happen when structured is absent"


def test_build_flashcards_qa_with_empty_strings_are_skipped(
    edu_app, fake_conn_factory
):
    """Q&A pairs where ``q`` or ``a`` is an empty string after stripping must
    be silently skipped — only pairs with both non-empty strings become cards.

    Mix:
      - {"q": "", "a": "Valid answer"} → skipped (empty question)
      - {"q": "Valid question?", "a": ""} → skipped (empty answer)
      - {"q": "What is osmosis?", "a": "Movement of water across a membrane."} → card
    Expected: exactly 1 card created, no junk fronts/backs.
    """
    structured = {
        "qa": [
            {"q": "", "a": "Valid answer but no question"},
            {"q": "Valid question?", "a": ""},
            {"q": "What is osmosis?", "a": "Movement of water across a membrane."},
        ],
        "mnemonics": [],
    }
    conn = fake_conn_factory(
        responses=[[_make_generated_note_row(note_id="gen-empty-qa", structured=structured)]]
    )

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "created": 1}, (
        "Only the one fully-populated Q&A pair should produce a card"
    )

    cards = _flashcard_inserts(conn)
    assert len(cards) == 1
    front, back = cards[0]
    assert front == "What is osmosis?"
    assert "membrane" in back


def test_build_flashcards_corrupt_json_in_structured_produces_zero_cards(
    edu_app, fake_conn_factory, caplog
):
    """When a generated note's ``structured`` column contains invalid JSON
    (e.g. a truncated AI response), ``json.loads()`` raises and the
    ``except Exception: structured_raw = None`` branch fires (~line 2104 of
    ``routes/edu_study.py``).  The route must return
    ``{"ok": True, "created": 0}`` — no 500, no crash, no junk card.

    We set ``row["structured"]`` directly to a malformed string, bypassing
    ``_make_generated_note_row``'s JSON serialisation so the corrupt payload
    reaches the parser intact.

    A WARNING log must also be emitted so corrupted notes are visible in
    production logs rather than silently producing 0 cards.
    """
    import logging

    row = _make_generated_note_row(note_id="gen-corrupt", generated=True)
    row["structured"] = "{invalid json"  # truncated / corrupt AI output

    conn = fake_conn_factory(responses=[[row]])

    with caplog.at_level(logging.WARNING):
        res = edu_app.post("/api/edu/flashcards/build", json={})

    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "created": 0}, (
        "Corrupt JSON in structured must degrade to 0 cards, not a 500"
    )
    assert _flashcard_inserts(conn) == [], "No edu_flashcards INSERTs on corrupt JSON"

    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("gen-corrupt" in m and "[edu_study]" in m for m in warning_msgs), (
        f"Expected a [edu_study] WARNING mentioning note id 'gen-corrupt'; got: {warning_msgs}"
    )


def test_build_flashcards_mnemonic_with_empty_topic_or_phrase_is_skipped(
    edu_app, fake_conn_factory
):
    """Mnemonic entries where ``for`` (topic) or ``mnemonic`` (phrase) is blank
    must be silently skipped — only entries with both non-empty strings become cards.

    Mix:
      - {"for": "", "mnemonic": "HOMES"} → skipped (empty topic)
      - {"for": "Great Lakes", "mnemonic": ""} → skipped (empty phrase)
      - {"for": "Cranial nerves", "mnemonic": "On Old Olympus Towering Tops..."} → card
    Expected: exactly 1 card created, no blank fronts like "Mnemonic for: ".
    """
    structured = {
        "qa": [],
        "mnemonics": [
            {"for": "", "mnemonic": "HOMES", "explanation": "Great Lakes"},
            {"for": "Great Lakes", "mnemonic": "", "explanation": "Huron, Ontario..."},
            {
                "for": "Cranial nerves",
                "mnemonic": "On Old Olympus Towering Tops...",
                "explanation": "I Olfactory, II Optic, III Oculomotor...",
            },
        ],
    }
    conn = fake_conn_factory(
        responses=[[_make_generated_note_row(note_id="gen-empty-mn", structured=structured)]]
    )

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "created": 1}, (
        "Only the one fully-populated mnemonic entry should produce a card"
    )

    cards = _flashcard_inserts(conn)
    assert len(cards) == 1
    front, back = cards[0]
    assert front == "Mnemonic for: Cranial nerves"
    assert "On Old Olympus" in back


def test_build_flashcards_respects_mnemonic_cap_of_four(edu_app, fake_conn_factory):
    """Mnemonic extraction is capped at 4 cards — providing 6 valid mnemonic
    entries must produce exactly 4 cards, and the first 4 (in order) are kept.

    The cap is the ``[:4]`` slice at ~line 2122 of ``routes/edu_study.py``.
    Its removal would let a single AI-generated note inject an unbounded number
    of mnemonic cards into a user's study deck.
    """
    mnemonics = [
        {"for": f"Topic {i}", "mnemonic": f"Phrase {i}", "explanation": f"Exp {i}"}
        for i in range(6)
    ]
    structured = {"qa": [], "mnemonics": mnemonics}
    conn = fake_conn_factory(
        responses=[[_make_generated_note_row(note_id="gen-mn-cap", structured=structured)]]
    )

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 4, (
        "Mnemonic cap must be 4; topics 0–3 kept, topics 4–5 dropped"
    )

    cards = _flashcard_inserts(conn)
    assert len(cards) == 4

    # First 4 topics are retained in order; topics 4 and 5 are dropped.
    fronts = [c[0] for c in cards]
    for i in range(4):
        assert fronts[i] == f"Mnemonic for: Topic {i}", (
            f"Expected card {i} to be 'Mnemonic for: Topic {i}'; got {fronts[i]!r}"
        )
    assert all(f"Topic {j}" not in f for f in fronts for j in (4, 5)), (
        "Topics 4 and 5 must not appear in the capped deck"
    )


def test_build_flashcards_mnemonic_cap_preserves_insertion_order(
    edu_app, fake_conn_factory
):
    """The mnemonic cap must keep the *first* 4 entries in their original order.

    Given 5 mnemonics (topics 0–4), the stored flashcard fronts must appear
    exactly as 'Mnemonic for: Topic 0', 'Mnemonic for: Topic 1', …,
    'Mnemonic for: Topic 3' — in that positional order.  A shuffle or
    arbitrary-pick regression would violate this ordering guarantee even if
    the count remained 4.
    """
    mnemonics = [
        {"for": f"Topic {i}", "mnemonic": f"Phrase {i}", "explanation": f"Exp {i}"}
        for i in range(5)
    ]
    structured = {"qa": [], "mnemonics": mnemonics}
    conn = fake_conn_factory(
        responses=[[_make_generated_note_row(note_id="mn-order", structured=structured)]]
    )

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 4, (
        "Exactly the first 4 mnemonics must be kept; topic 4 must be dropped"
    )

    cards = _flashcard_inserts(conn)
    assert len(cards) == 4

    fronts = [c[0] for c in cards]

    # Each position must match the corresponding input mnemonic, in order.
    for i in range(4):
        assert fronts[i] == f"Mnemonic for: Topic {i}", (
            f"Position {i}: expected 'Mnemonic for: Topic {i}', got {fronts[i]!r}; "
            "the cap must preserve insertion order, not pick arbitrarily"
        )


def test_build_flashcards_mnemonic_cap_excludes_later_entries(
    edu_app, fake_conn_factory
):
    """Mnemonics beyond position 3 (0-indexed) must never appear in the deck.

    Providing 7 mnemonics (topics 0–6) and capping at 4 means topics 4, 5, and
    6 must be entirely absent from the inserted flashcard fronts.  This is the
    targeted regression guard: a faulty implementation that picks *any* 4
    (instead of the first 4) could accidentally include a later topic while
    still passing a count-only assertion.
    """
    mnemonics = [
        {"for": f"Topic {i}", "mnemonic": f"Phrase {i}", "explanation": f"Exp {i}"}
        for i in range(7)
    ]
    structured = {"qa": [], "mnemonics": mnemonics}
    conn = fake_conn_factory(
        responses=[[_make_generated_note_row(note_id="mn-excl", structured=structured)]]
    )

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 4, (
        "Cap of 4 must be enforced even when 7 mnemonics are supplied"
    )

    cards = _flashcard_inserts(conn)
    assert len(cards) == 4

    fronts = [c[0] for c in cards]

    # Positions 0–3 must be present in order.
    for i in range(4):
        assert fronts[i] == f"Mnemonic for: Topic {i}", (
            f"Position {i}: expected 'Mnemonic for: Topic {i}', got {fronts[i]!r}"
        )

    # Topics 4, 5, and 6 must not appear anywhere in the stored fronts.
    excluded = {4, 5, 6}
    for front in fronts:
        for j in excluded:
            assert f"Topic {j}" not in front, (
                f"Topic {j} must not appear in the capped deck; got front {front!r}"
            )


def test_build_flashcards_qa_truncation(edu_app, fake_conn_factory):
    """Q&A pairs with overlong fields must be stored at exactly the limit.

    A question of 600 chars must be truncated to 400 chars (front), and an
    answer of 1000 chars must be truncated to 800 chars (back).  The stored
    values must not be blank.
    """
    long_q = "Q" * 600
    long_a = "A" * 1000
    structured = {"qa": [{"q": long_q, "a": long_a}], "mnemonics": []}
    conn = fake_conn_factory(
        responses=[[_make_generated_note_row(structured=structured)]]
    )

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "created": 1}

    cards = _flashcard_inserts(conn)
    assert len(cards) == 1
    front, back = cards[0]

    assert len(front) == 400, (
        f"Front must be exactly 400 chars after truncation; got {len(front)}"
    )
    assert front == "Q" * 400, "Front must not be blank and must match the first 400 chars"

    assert len(back) == 800, (
        f"Back must be exactly 800 chars after truncation; got {len(back)}"
    )
    assert back == "A" * 800, "Back must not be blank and must match the first 800 chars"


def test_build_flashcards_qa_field_order(edu_app, fake_conn_factory):
    """Q&A cards must store the question in front and the answer in back.

    A regression that swaps the two fields (e.g. a→front, q→back) must be
    caught.  Each card is checked individually so a per-pair swap cannot hide
    behind set membership.  The test also asserts that neither field starts
    with the other's content.
    """
    qa_pairs = [
        {
            "q": "What is the powerhouse of the cell?",
            "a": "The mitochondria.",
        },
        {
            "q": "Which pigment makes plants green?",
            "a": "Chlorophyll.",
        },
    ]
    structured = {"qa": qa_pairs, "mnemonics": []}
    conn = fake_conn_factory(
        responses=[[_make_generated_note_row(note_id="gen-qa-order", structured=structured)]]
    )

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 2

    cards = _flashcard_inserts(conn)
    assert len(cards) == 2

    for idx, (front, back) in enumerate(cards):
        expected_q = qa_pairs[idx]["q"]
        expected_a = qa_pairs[idx]["a"]

        assert front == expected_q, (
            f"Card {idx}: front must be the question {expected_q!r}; got {front!r}"
        )
        assert back == expected_a, (
            f"Card {idx}: back must be the answer {expected_a!r}; got {back!r}"
        )

        assert not front.startswith(expected_a[:10]), (
            f"Card {idx}: front must not start with the answer text"
        )
        assert not back.startswith(expected_q[:10]), (
            f"Card {idx}: back must not start with the question text"
        )


def test_build_flashcards_mnemonic_topic_truncation(edu_app, fake_conn_factory):
    """A mnemonic with a 300-char 'for' field must be trimmed to 200 chars.

    The stored front must start with 'Mnemonic for: ', contain only the first
    200 chars of the topic, and must not exceed 300 chars in total.  It must
    not be blank.
    """
    long_topic = "T" * 300
    structured = {
        "qa": [],
        "mnemonics": [{"for": long_topic, "mnemonic": "Some phrase", "explanation": ""}],
    }
    conn = fake_conn_factory(
        responses=[[_make_generated_note_row(structured=structured)]]
    )

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "created": 1}

    cards = _flashcard_inserts(conn)
    assert len(cards) == 1
    front, _back = cards[0]

    prefix = "Mnemonic for: "
    assert front.startswith(prefix), (
        f"Front must start with {prefix!r}; got {front[:30]!r}"
    )

    topic_portion = front[len(prefix):]
    assert len(topic_portion) == 200, (
        f"Topic portion must be exactly 200 chars after truncation; got {len(topic_portion)}"
    )
    assert topic_portion == "T" * 200, (
        "Topic portion must be the first 200 chars of the topic string"
    )

    assert len(front) <= 300, (
        f"Total front must be at most 300 chars; got {len(front)}"
    )
    assert front.startswith(prefix + "T"), "Front must not be blank after the prefix"


def test_build_flashcards_mnemonic_back_combined_truncation(edu_app, fake_conn_factory):
    """A mnemonic whose phrase is 300 chars and explanation is 500 chars must have
    its combined back field capped at 800 chars and must not be blank.

    phrase[:300] + "\\n\\n" + explanation[:500] = 804 chars, so the final [:800]
    guard is what keeps the stored value within the column limit.
    """
    long_phrase = "P" * 300
    long_explanation = "E" * 500
    structured = {
        "qa": [],
        "mnemonics": [
            {
                "for": "Some Topic",
                "mnemonic": long_phrase,
                "explanation": long_explanation,
            }
        ],
    }
    conn = fake_conn_factory(
        responses=[[_make_generated_note_row(structured=structured)]]
    )

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "created": 1}

    cards = _flashcard_inserts(conn)
    assert len(cards) == 1
    _front, back = cards[0]

    assert len(back) <= 800, (
        f"Combined back must be at most 800 chars; got {len(back)}"
    )
    assert back, "Combined back must not be blank"
    assert back.startswith("P"), (
        f"Back must begin with the mnemonic phrase; got {back[:20]!r}"
    )
    first_explanation = back.find("E")
    first_phrase = back.find("P")
    assert first_phrase < first_explanation, (
        "Phrase characters must appear before explanation characters in back; "
        f"first 'P' at {first_phrase}, first 'E' at {first_explanation}"
    )


def test_build_flashcards_qa_cap_preserves_first_eight_in_order(edu_app, fake_conn_factory):
    """The Q&A cap must keep the *first* 8 pairs and must preserve insertion order.

    Supplying 10 pairs with distinct, sequentially-numbered questions means that
    a shuffle or arbitrary-pick implementation would fail: the stored fronts must
    be exactly Q0..Q7 in that order, not merely any 8 of the 10.
    """
    qa_pairs = [{"q": f"Question {i}", "a": f"Answer {i}"} for i in range(10)]
    structured = {"qa": qa_pairs, "mnemonics": []}
    conn = fake_conn_factory(
        responses=[[_make_generated_note_row(note_id="qa-cap-order", structured=structured)]]
    )

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "created": 8}

    cards = _flashcard_inserts(conn)
    assert len(cards) == 8, f"Expected exactly 8 cards; got {len(cards)}"

    for idx in range(8):
        expected_front = f"Question {idx}"
        actual_front = cards[idx][0]
        assert actual_front == expected_front, (
            f"Card at position {idx} must have front {expected_front!r}; "
            f"got {actual_front!r} — cap must keep the first 8 pairs in order"
        )


def test_build_flashcards_qa_cap_excludes_pairs_beyond_position_seven(edu_app, fake_conn_factory):
    """Q&A pairs at positions 8 and 9 must not appear in the stored cards.

    This is the complement of the ordering test: we explicitly check that none
    of the stored card fronts match a question that was supplied after the 8-pair
    cap boundary, ruling out any scheme that picks later entries instead of
    earlier ones.
    """
    qa_pairs = [{"q": f"Question {i}", "a": f"Answer {i}"} for i in range(10)]
    structured = {"qa": qa_pairs, "mnemonics": []}
    conn = fake_conn_factory(
        responses=[[_make_generated_note_row(note_id="qa-cap-exclude", structured=structured)]]
    )

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text

    cards = _flashcard_inserts(conn)
    stored_fronts = [front for front, _back in cards]

    for excluded_idx in (8, 9):
        excluded_front = f"Question {excluded_idx}"
        assert excluded_front not in stored_fronts, (
            f"{excluded_front!r} (position {excluded_idx}) must not be stored; "
            f"only the first 8 pairs are allowed — stored fronts: {stored_fronts}"
        )


def test_build_flashcards_qa_cap_counts_positions_not_valid_pairs(edu_app, fake_conn_factory):
    """The [:8] cap applies to raw list positions, not to the count of valid pairs.

    Positions 0 and 1 have blank q/a fields — they consume slots but produce no
    cards.  Positions 2–7 each produce one valid card (6 total).  Positions 8
    and 9 are never reached regardless of how many valid pairs were found, so
    those questions must not appear in the stored cards.

    A regression that changed the logic to "collect 8 valid pairs" would store
    8 cards and include pairs from positions 8 and 9 — this test would catch it.
    """
    qa_pairs = (
        [{"q": "", "a": ""}] * 2  # positions 0–1: blank, consume slots
        + [{"q": f"Question {i}", "a": f"Answer {i}"} for i in range(2, 10)]  # positions 2–9
    )
    structured = {"qa": qa_pairs, "mnemonics": []}
    conn = fake_conn_factory(
        responses=[[_make_generated_note_row(note_id="qa-cap-blank", structured=structured)]]
    )

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text

    cards = _flashcard_inserts(conn)
    assert len(cards) <= 6, (
        f"Expected at most 6 cards (positions 2–7 only); got {len(cards)} — "
        f"the cap must count raw positions, not valid pairs"
    )

    stored_fronts = [front for front, _back in cards]
    for excluded_idx in (8, 9):
        excluded_front = f"Question {excluded_idx}"
        assert excluded_front not in stored_fronts, (
            f"{excluded_front!r} (position {excluded_idx}) must not be stored; "
            f"blank entries at positions 0–1 already consumed two slots of the 8-pair cap"
        )


def test_build_flashcards_mnemonic_cap_counts_positions_not_valid_entries(
    edu_app, fake_conn_factory
):
    """The [:4] mnemonic cap applies to raw list positions, not to the count of
    valid (non-blank) entries.

    Positions 0 and 1 have blank ``for``/``mnemonic`` fields — they consume
    slots inside the [:4] window but produce no cards because the blank-filter
    (``if for_text and mnemonic_str``) skips them.  Positions 2 and 3 each
    hold a valid entry and are within the cap, so they produce exactly 2 cards.
    Positions 4 and 5 hold valid entries but are beyond the raw [:4] boundary
    and must never be stored.

    A regression that changed the logic to "collect 4 *valid* entries" would
    reach into positions 4 and 5 to fill the quota — this test would catch it.
    """
    mnemonics = (
        [{"for": "", "mnemonic": ""}] * 2  # positions 0–1: blank, consume slots
        + [
            {"for": f"Topic {i}", "mnemonic": f"Phrase {i}", "explanation": f"Exp {i}"}
            for i in range(2, 6)  # positions 2–5: valid entries
        ]
    )
    structured = {"qa": [], "mnemonics": mnemonics}
    conn = fake_conn_factory(
        responses=[[_make_generated_note_row(note_id="mn-cap-blank", structured=structured)]]
    )

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text

    cards = _flashcard_inserts(conn)
    assert len(cards) <= 2, (
        f"Expected at most 2 mnemonic cards (positions 2–3 only); got {len(cards)} — "
        "blank entries at positions 0–1 must consume cap slots, not be skipped over"
    )

    stored_fronts = [front for front, _back in cards]
    for excluded_idx in (4, 5):
        excluded_front = f"Mnemonic for: Topic {excluded_idx}"
        assert excluded_front not in stored_fronts, (
            f"{excluded_front!r} (position {excluded_idx}) must not be stored; "
            f"blank entries at positions 0–1 already consumed two of the four cap slots"
        )


def test_build_flashcards_qa_all_capped_positions_blank_yields_zero_cards(
    edu_app, fake_conn_factory
):
    """All 8 capped positions (0–7) are blank; positions 8–9 are valid but unreachable.

    The [:8] slice is applied before any validity filtering, so when every entry
    in positions 0–7 has an empty ``q`` or ``a`` field, zero cards are created.
    Positions 8 and 9 carry valid pairs but are never evaluated because they fall
    outside the cap window.

    A regression that changed the logic to "collect 8 valid pairs" would reach
    positions 8–9 and return created: 2 — this test would catch it.
    """
    qa_pairs = (
        [{"q": "", "a": ""}] * 8  # positions 0–7: all blank, fill the cap
        + [{"q": "Valid Question 8", "a": "Valid Answer 8"},  # position 8: valid but out of cap
           {"q": "Valid Question 9", "a": "Valid Answer 9"}]  # position 9: valid but out of cap
    )
    structured = {"qa": qa_pairs, "mnemonics": []}
    conn = fake_conn_factory(
        responses=[[_make_generated_note_row(note_id="qa-all-blank-cap", structured=structured)]]
    )

    res = edu_app.post("/api/edu/flashcards/build", json={})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "created": 0}, (
        f"Expected created: 0 when all 8 capped positions are blank; "
        f"got {res.json()!r} — pairs at positions 8–9 must not be reached"
    )

    cards = _flashcard_inserts(conn)
    assert len(cards) == 0, (
        f"Expected 0 stored cards when all capped positions are blank; "
        f"got {len(cards)} — the cap must apply before validity filtering"
    )
