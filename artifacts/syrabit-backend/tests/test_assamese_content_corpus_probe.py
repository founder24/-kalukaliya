"""Task #465 — spot-check probe: the post-backfill SSR corpus must
contain real Assamese, not English fall-throughs.

This is an extension of the SSR-coverage probe in
``test_ssr_route_families.py``. ``test_ssr_route_families`` only
asserts the Assamese routes return ``200 text/html``; it does not
catch the silent-fallback bug where ``seo_engine._localized()``
returns the English string because the ``*_as`` sibling is missing
on the underlying document.

Strategy
--------
1. Build N sample SSR URLs that map 1:1 to documents in the four
   backfilled collections (``subjects``, ``chapters``, ``seo_pages``,
   ``pyq_html_pages``) — these are exactly the URL shapes the Pages
   middleware proxies to ``/api/seo/html/...``.
2. For each sample, fetch the matching Mongo doc and assert the
   article-body fields the SSR will read for the Assamese variant
   (``content_html_as`` / ``content_as`` / ``description_as``)
   contain <5% Latin characters when normalized.

The doc-level check intentionally stops short of asserting the full
rendered HTML is Assamese: framework-label localization (publisher
block, JSON-LD, hard-coded prose) is tracked separately by Task #432
and follow-ups, not by this content-corpus backfill. Pinning the
probe to the data the backfill produces keeps the regression signal
on the corpus, not the chrome.
"""
from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest


_URL_RE = re.compile(r"https?://\S+")
# Brand / acronym tokens that legitimately stay Latin even on AS
# pages (board codes, the Syrabit brand, file-format names). They are
# excluded from the Latin-ratio denominator so a single ``AHSEC`` or
# ``MCQ`` cannot flip the assertion on an otherwise-Assamese body.
_ALLOWED_LATIN_TOKENS = {
    "syrabit", "syrabit.ai", "ahsec", "cbse", "icse",
    "pyq", "mcq", "mcqs", "rag", "ai", "ncert",
    "html", "json", "ld", "url",
}


def _latin_ratio(text: str) -> float:
    """Ratio of Latin letters to all letters (Latin + Bengali block).

    Strips URLs and allow-listed tokens before counting so brand /
    acronym noise does not dominate the signal.
    """
    if not text:
        return 0.0
    text = _URL_RE.sub(" ", text)
    for tok in _ALLOWED_LATIN_TOKENS:
        text = re.sub(rf"\b{re.escape(tok)}\b", " ", text, flags=re.IGNORECASE)
    counted = [c for c in text
               if c.isalpha() or (0x0980 <= ord(c) <= 0x09FF)]
    if not counted:
        return 0.0
    latin = sum(1 for c in counted if c.isascii() and c.isalpha())
    return latin / len(counted)


# ── Sample URL → (collection, find_query, fields_to_check) catalog ──────────
# The URL column is the SSR path the Pages middleware proxies to. The
# (collection, query) tuple identifies the doc the SSR renders the
# Assamese article body from. ``fields`` lists the post-backfill
# ``*_as`` siblings that contribute to the visible article body for
# that route family.
SAMPLE_URLS = [
    {
        "url":        "/ahsec/class-12/physics/newton-laws",
        "collection": "seo_pages",
        "query":      {"board_slug": "ahsec", "class_slug": "class-12",
                       "subject_slug": "physics", "topic_slug": "newton-laws",
                       "page_type": "notes"},
        "fields":     ["topic_title_as", "meta_description_as",
                       "content_html_as", "title_as"],
    },
    {
        "url":        "/ahsec/class-12/physics/laws-of-motion",
        "collection": "chapters",
        "query":      {"slug": "laws-of-motion"},
        "fields":     ["title_as", "description_as", "content_as"],
    },
    {
        "url":        "/ahsec/class-12/physics",
        "collection": "subjects",
        "query":      {"slug": "physics"},
        "fields":     ["name_as", "description_as"],
    },
    {
        "url":        "/pyq/ahsec-physics-2024-major",
        "collection": "pyq_html_pages",
        "query":      {"slug": "ahsec-physics-2024-major"},
        "fields":     ["title_as", "meta_description_as",
                       "content_html_as"],
    },
]

# Long-form Assamese strings used to simulate the post-backfill state
# of every sample doc. Each is ≥ 200 visible characters so the
# Latin-ratio probe operates on a realistic article-sized payload.
_AS_LONG = (
    "নিউটনৰ গতিৰ তিনিটা সূত্ৰৰ সম্পূৰ্ণ ব্যাখ্যা আৰু উদাহৰণসহ "
    "বিশদ আলোচনা। প্ৰথম সূত্ৰটোৱে জড়তাৰ ধাৰণা ব্যাখ্যা কৰে। "
    "দ্বিতীয় সূত্ৰই বল আৰু ত্বৰণৰ মাজৰ সম্পৰ্ক দেখুৱায়। "
    "তৃতীয় সূত্ৰই ক্ৰিয়া আৰু প্ৰতিক্ৰিয়াৰ কথা ক'য়। "
)


def _make_doc(sample) -> dict:
    """Synthesize the post-backfill Mongo doc for a sample URL."""
    doc = dict(sample["query"])
    doc["status"] = "published"
    # Populate the English originals so a regression that drops the
    # ``*_as`` field would still leave the SSR with *something* to
    # fall back to — that's the bug the probe is designed to catch.
    for f in sample["fields"]:
        en_field = f[:-3]  # strip the ``_as`` suffix
        doc[en_field] = "Newton's Laws of Motion — English fallback body."
        doc[f] = _AS_LONG
    return doc


@pytest.fixture
def db_with_backfilled_corpus(monkeypatch):
    """Mount a Mongo stub whose docs carry the post-Task-#465 ``*_as``
    fields populated for every sample URL."""
    import seo_engine
    from types import SimpleNamespace

    by_collection: dict[str, list[dict]] = {}
    for s in SAMPLE_URLS:
        by_collection.setdefault(s["collection"], []).append(_make_doc(s))

    def _make_coll(docs):
        coll = MagicMock()

        async def _find_one(query, *_a, **_kw):
            for d in docs:
                if all(d.get(k) == v for k, v in query.items()):
                    return d
            return None

        coll.find_one = AsyncMock(side_effect=_find_one)
        return coll

    db = SimpleNamespace(
        seo_pages=_make_coll(by_collection.get("seo_pages", [])),
        chapters=_make_coll(by_collection.get("chapters", [])),
        subjects=_make_coll(by_collection.get("subjects", [])),
        pyq_html_pages=_make_coll(by_collection.get("pyq_html_pages", [])),
    )
    monkeypatch.setattr(seo_engine, "_db", db, raising=False)
    return db


# ── _latin_ratio sanity tests ───────────────────────────────────────────────
def test_latin_ratio_zero_for_pure_assamese():
    assert _latin_ratio("নিউটনৰ গতিৰ সূত্ৰসমূহ") == 0.0


def test_latin_ratio_one_for_pure_english():
    assert _latin_ratio("Newton's laws of motion") == 1.0


def test_latin_ratio_strips_brand_tokens():
    # AHSEC and Syrabit are allow-listed so they are NOT counted as
    # Latin leakage — only the unlisted English word "rules" should
    # count, and its weight is small relative to the Assamese body.
    txt = "AHSEC Syrabit rules " + ("ক" * 200)
    assert _latin_ratio(txt) < 0.05


# ── Spot-check probe: <5% Latin in the post-backfill data ───────────────────
@pytest.mark.parametrize(
    "sample", SAMPLE_URLS,
    ids=[s["url"] for s in SAMPLE_URLS],
)
async def test_ssr_corpus_has_assamese_content(db_with_backfilled_corpus, sample):
    """For every sample SSR URL the post-backfill Mongo doc must
    carry Assamese ``*_as`` siblings with <5% Latin characters in
    each backfilled article-body field. Catches a silent regression
    where the backfill skipped a collection or fell back to the
    English string."""
    import seo_engine
    coll = getattr(seo_engine._db, sample["collection"])
    doc = await coll.find_one(sample["query"])
    assert doc is not None, (
        f"sample URL {sample['url']!r} maps to no doc in "
        f"{sample['collection']!r} — fixture / route map drifted"
    )
    leaks: list[tuple[str, float]] = []
    for field in sample["fields"]:
        value = doc.get(field) or ""
        assert isinstance(value, str) and value.strip(), (
            f"{sample['url']} → {sample['collection']}.{field} is "
            f"empty post-backfill (value={value!r}); SSR will silently "
            f"fall back to the English original"
        )
        ratio = _latin_ratio(value)
        if ratio >= 0.05:
            leaks.append((field, ratio))
    assert not leaks, (
        f"{sample['url']} → {sample['collection']} has Assamese "
        f"fields with >=5% Latin chars: {leaks!r}"
    )


def test_sample_url_catalog_covers_all_backfilled_collections():
    """Guard rail: every collection ``aca_jobs.as_translation_backfill``
    manages MUST appear in the sample-URL catalog so the probe
    catches a regression in ANY of them."""
    from aca_jobs import as_translation_backfill as bf
    covered = {s["collection"] for s in SAMPLE_URLS}
    assert covered == set(bf.FIELD_MAP), (
        f"sample URL catalog covers {covered!r} but backfill manages "
        f"{set(bf.FIELD_MAP)!r}"
    )


def test_sample_url_fields_are_subset_of_backfilled_fields():
    """Every ``*_as`` field the probe asserts on MUST correspond to a
    field the backfill driver actually translates. Otherwise the probe
    is asserting on a sibling no run will ever populate."""
    from aca_jobs import as_translation_backfill as bf
    for s in SAMPLE_URLS:
        managed = {f"{en}_as" for en in bf.FIELD_MAP[s["collection"]]}
        unknown = set(s["fields"]) - managed
        assert not unknown, (
            f"{s['url']} probes fields {unknown!r} that backfill "
            f"never writes for {s['collection']!r} (managed: {managed!r})"
        )
