"""cache_fingerprint — Task #10 (Spec §9, blueprint #572 + #573).

Semantic cache fingerprint that collapses paraphrased / bilingual variants
of the same educational query onto a single cache identity. The literal
``ai_input_cache`` SHA256 hash treats "Explain photosynthesis", "What is
photosynthesis?", "Define photosynthesis", and "ফটোসিন্থেসিস কি" as four
separate cache keys; this module collapses them into one stable 16-byte
BLAKE3 hex string.

Pipeline (pure function — no I/O beyond an optional D1 syllabus-graph
lookup which is best-effort + cached):

  1. NFKC unicode normalize.
  2. Lowercase + strip outer whitespace.
  3. Drop terminal punctuation (``? ! . ,`` etc.).
  4. Strip language-specific stopwords / articles ("the", "a", "of",
     "কি", "কেনে", "কেনেদৰে", ...).
  5. Canonicalize the query verb to one of the small ``QueryVerb`` set:
       ``DEFINE``  — explain, define, what is, describe, summarise, কি, ব্যাখ্যা
       ``LIST``    — list, name, give, enumerate, mention, তালিকা
       ``COMPARE`` — compare, difference between, vs, contrast, পাৰ্থক্য
       ``HOW``     — how, কেনেকৈ, কেনেদৰে
       ``WHY``     — why, কিয়
       ``GENERATE_MCQ`` / ``GENERATE_FLASHCARD`` (kept after the
       prompt_normalizer rewrite so MCQ / flashcard generators share an
       identity with their bilingual peers).
       ``UNKNOWN``  — fallthrough (still fingerprintable).
  6. Resolve the residual topic noun across English ↔ Assamese using
     the syllabus synonym map (D1 ``syllabus_topic_synonyms`` table when
     reachable; falls back to a small built-in dictionary for the canon
     test pairs and any topic the caller explicitly passes in).
  7. Compose ``"<verb>|<board>|<class>|<subject>|<chapter>|<query_type>|<topic>"``
     and BLAKE3 it (16-byte / 32-hex digest).

The 16-byte digest is short enough to slot into the existing
``ai_response_cache:v1:`` Redis / KV key prefix while still giving
2^128 collision space — overkill for the few-thousand-key working
set this cache holds at any moment.

Public API (frozen by ``tests/test_cache_fingerprint.py``)::

    fingerprint(text, *, language="en", board=None, class_=None,
                subject=None, chapter=None, query_type=None) -> str  # 32 hex chars
    canonical_form(text, language="en") -> tuple[str, str]            # (verb, topic)
    resolve_topic_synonym(topic, language) -> str                     # canonical topic key
    QUERY_VERBS                                                       # frozenset of verb tokens

The function is **pure** (modulo a process-local LRU on the synonym
lookup) — same input always yields the same output. The synonym
lookup table is loaded at module-import time from a small built-in
dictionary; the optional D1 sync (gated on ``CACHE_FINGERPRINT_D1_SYNC``)
runs in a background task at startup if the D1 client is wired.

NOT in scope (per Task #10 "Out of scope"):
  * Embedding-based fuzzy match — orthogonal to the literal canonicalizer.
  * Live ``/api/ai_chat.py`` dispatch — excluded by the K.2 policy.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import unicodedata
from typing import Optional

logger = logging.getLogger(__name__)


# ── BLAKE3 with sha256 fallback ───────────────────────────────────────
# blake3 is in requirements.txt; the fallback only fires in environments
# that strip optional native deps. The fallback path is deterministic
# and 16-byte truncated so the key shape stays identical — but it WILL
# produce different bytes than the BLAKE3 path, so a redeploy that loses
# the dep silently invalidates every prior fingerprint cache entry. The
# legacy literal-hash dual-read in `ai_input_cache` covers that gap for
# 30 days.
try:
    from blake3 import blake3 as _blake3  # type: ignore
    _BLAKE3_AVAILABLE = True
except Exception:  # pragma: no cover — optional native dep
    _blake3 = None  # type: ignore[assignment]
    _BLAKE3_AVAILABLE = False
    logger.warning(
        "[cache_fingerprint] blake3 unavailable — falling back to sha256:16; "
        "fingerprints will not be cross-compatible with BLAKE3 builds.",
    )


def _hash_16(data: str) -> str:
    """Return a stable 16-byte hex (32 chars) digest of ``data``."""
    raw = data.encode("utf-8", errors="ignore")
    if _BLAKE3_AVAILABLE and _blake3 is not None:
        return _blake3(raw).digest(length=16).hex()
    return hashlib.sha256(raw).digest()[:16].hex()


# ── Unicode + punctuation ─────────────────────────────────────────────
# Keep `-`, `_`, `/` because they appear inside chemistry / formula names
# where stripping them would change the meaning ("H2/SO4" → "H2 SO4").
_PUNCT_RE = re.compile(r"[\.\?\!\,\;\:\"'`\(\)\[\]\{\}\*~]+")
_WS_RE = re.compile(r"\s+")


# ── Stopword sets ─────────────────────────────────────────────────────
_STOPWORDS_EN = frozenset({
    "a", "an", "the", "of", "for", "to", "in", "on", "at", "is", "are",
    "was", "were", "be", "been", "being", "and", "or", "but", "with",
    "from", "by", "as", "that", "this", "these", "those", "it", "its",
    "me", "my", "you", "your", "we", "our", "please", "kindly", "can",
    "could", "would", "should", "do", "does", "did", "have", "has",
    "had", "some", "any", "about", "regarding",
})
_STOPWORDS_AS = frozenset({
    # Common Assamese function words / postpositions / pronouns that do
    # not change the topic identity. Matches the practical set seen in
    # student bilingual queries — not a linguistic exhaustive list.
    "এটা", "এই", "সেই", "মোক", "তোক", "মোৰ", "তোমাৰ", "আমাৰ",
    "অনুগ্ৰহ", "কৰি", "এটা", "ওপৰত", "বিষয়ে", "সম্পৰ্কে",
})


# ── Verb canonicalization ─────────────────────────────────────────────
# (matcher_pattern, canonical_verb). Order matters — longest-first so
# "difference between" wins over "between". Patterns are matched as
# whole-word substrings (anywhere in the canonicalized text) so a
# query like "explain photosynthesis briefly" or "ফটোসিন্থেসিস ব্যাখ্যা
# কৰা" both surface as ``DEFINE``.
_VERB_RULES: list[tuple[re.Pattern, str]] = [
    # COMPARE — must come before LIST so "compare and list" → COMPARE.
    (re.compile(r"\b(?:compare|contrast|difference between|differences between|vs|versus)\b"), "COMPARE"),
    (re.compile(r"পাৰ্থক্য|তুলনা"), "COMPARE"),
    # GENERATE_MCQ / GENERATE_FLASHCARD — the prompt_normalizer already
    # rewrites these to "generate mcqs for X" / "generate flashcards for
    # X" so we recognise the canonical form here. Bilingual MCQ/flashcard
    # generation phrasings are also caught.
    (re.compile(r"\bgenerate mcqs?\b|\bmultiple choice\b|\bmcq\b"), "GENERATE_MCQ"),
    (re.compile(r"\bgenerate flashcards?\b|\bflashcard\b"), "GENERATE_FLASHCARD"),
    # HOW / WHY before DEFINE so "how does X work" stays HOW.
    (re.compile(r"\bhow\b"), "HOW"),
    (re.compile(r"কেনেকৈ|কেনেদৰে"), "HOW"),
    (re.compile(r"\bwhy\b"), "WHY"),
    (re.compile(r"কিয়"), "WHY"),
    # LIST — naming / enumerating verbs.
    (re.compile(r"\b(?:list|name|give|enumerate|mention|state)\b"), "LIST"),
    (re.compile(r"তালিকা|উল্লেখ"), "LIST"),
    # DEFINE — the largest family. Every "what is X" / "explain X" /
    # "describe X" / "summarise X" / "define X" / Assamese "X কি" /
    # "X ব্যাখ্যা কৰা" collapses here.
    (re.compile(r"\b(?:define|explain|describe|summarise|summarize|tell me about|what is|what are|what was|what were)\b"), "DEFINE"),
    (re.compile(r"কি$|কি\b|ব্যাখ্যা|বৰ্ণনা|সংজ্ঞা"), "DEFINE"),
]

QUERY_VERBS = frozenset({
    "DEFINE", "LIST", "COMPARE", "HOW", "WHY",
    "GENERATE_MCQ", "GENERATE_FLASHCARD", "UNKNOWN",
})


# Regex matching every verb-trigger token so we can excise them from
# the topic residue once the verb is canonicalized. Built once from
# ``_VERB_RULES`` so adding a verb pattern also updates the residue
# stripper.
_VERB_STRIP_RE = re.compile(
    "|".join(p.pattern for p, _ in _VERB_RULES),
)


# ── Topic synonym map (built-in seed; D1 sync extends at runtime) ─────
# Keyed by language → {raw_topic_string: canonical_topic_key}. The
# canonical key is normally the lowercase English topic name; bilingual
# pairs collapse onto the same canonical key.
#
# This seed covers the Task #10 unit-test pairs + a handful of common
# Class 11/12 Biology / Physics / Chemistry topics so the fingerprint
# behaves correctly even when the D1 mirror is cold. Operators extend
# the runtime table by writing rows into the D1 ``syllabus_topic_synonyms``
# table (board / class / subject / chapter / topic_en / topic_as).
_BUILTIN_SYNONYMS: dict[str, dict[str, str]] = {
    "en": {
        "photosynthesis":     "photosynthesis",
        "respiration":        "cellular respiration",
        "cellular respiration": "cellular respiration",
        "mitosis":            "mitosis",
        "meiosis":            "meiosis",
        "newton's laws":      "newtons laws of motion",
        "newtons laws":       "newtons laws of motion",
        "laws of motion":     "newtons laws of motion",
    },
    "as": {
        "ফটোসিন্থেসিস":      "photosynthesis",
        "সালোকসংশ্লেষণ":     "photosynthesis",
        "শ্বসন":              "cellular respiration",
        "কোষীয় শ্বসন":       "cellular respiration",
        "মাইটোছিছ":          "mitosis",
        "মিয়োছিছ":           "meiosis",
        "নিউটনৰ গতিৰ সূত্ৰ": "newtons laws of motion",
    },
}


_SYNONYM_LOCK = threading.Lock()
_RUNTIME_SYNONYMS: dict[str, dict[str, str]] = {"en": {}, "as": {}}


def register_synonym(language: str, raw: str, canonical: str) -> None:
    """Extend the runtime synonym map (called by the D1 mirror on sync
    completion). Best-effort — bad input is dropped silently."""
    if not raw or not canonical:
        return
    lang = (language or "en").lower()
    if lang not in _RUNTIME_SYNONYMS:
        return
    raw_n = _basic_normalize(raw)
    if not raw_n:
        return
    with _SYNONYM_LOCK:
        _RUNTIME_SYNONYMS[lang][raw_n] = canonical.strip().lower()


def reset_runtime_synonyms_for_tests() -> None:
    """Test-only — wipe the runtime layer so a fixture can pin the
    synonym table to a known shape."""
    with _SYNONYM_LOCK:
        for lang in _RUNTIME_SYNONYMS:
            _RUNTIME_SYNONYMS[lang].clear()


def resolve_topic_synonym(topic: str, language: str = "en") -> str:
    """Return the canonical English topic key for ``topic`` in
    ``language``. Falls through to the normalized input when no
    mapping matches.
    """
    if not topic:
        return ""
    lang = (language or "en").lower()
    raw_n = _basic_normalize(topic)
    if not raw_n:
        return ""
    # Runtime layer wins (operator-curated D1 rows beat the built-in
    # seed in case the canonical name was renamed at the source of
    # truth).
    with _SYNONYM_LOCK:
        rt = _RUNTIME_SYNONYMS.get(lang, {})
        if raw_n in rt:
            return rt[raw_n]
    seed = _BUILTIN_SYNONYMS.get(lang, {})
    if raw_n in seed:
        return seed[raw_n]
    # Final fallback — try the OTHER language too so an Assamese topic
    # that the caller mis-tagged as `language="en"` still collapses.
    other = "as" if lang == "en" else "en"
    seed_other = _BUILTIN_SYNONYMS.get(other, {})
    if raw_n in seed_other:
        return seed_other[raw_n]
    return raw_n


# ── Core normalize / canonicalize helpers ─────────────────────────────
def _basic_normalize(text: str) -> str:
    """NFKC + lowercase + strip punctuation + collapse whitespace."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    s = s.lower().strip()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def _strip_stopwords(text: str, language: str) -> str:
    if not text:
        return text
    lang = (language or "en").lower()
    stop = _STOPWORDS_AS if lang == "as" else _STOPWORDS_EN
    return " ".join(tok for tok in text.split(" ") if tok and tok not in stop).strip()


def _detect_verb(text: str) -> str:
    """Return the canonical ``QueryVerb`` for ``text``. Falls back to
    ``UNKNOWN`` when no rule matches."""
    if not text:
        return "UNKNOWN"
    for pattern, verb in _VERB_RULES:
        if pattern.search(text):
            return verb
    return "UNKNOWN"


def _strip_verbs(text: str) -> str:
    """Excise every verb-trigger token so the residue is purely topic
    noun(s). Keeps the word order of the survivors so multi-word topics
    like "newtons laws of motion" stay contiguous."""
    if not text:
        return text
    out = _VERB_STRIP_RE.sub(" ", text)
    return _WS_RE.sub(" ", out).strip()


def canonical_form(text: str, language: str = "en") -> tuple[str, str]:
    """Return ``(verb, topic_key)`` for ``text`` in ``language``.

    Topic key is the synonym-resolved canonical English form when one
    is registered; otherwise the stopword-stripped residue.
    """
    base = _basic_normalize(text)
    verb = _detect_verb(base)
    residue = _strip_verbs(base)
    residue = _strip_stopwords(residue, language)
    topic_key = resolve_topic_synonym(residue, language) if residue else ""
    return verb, topic_key


def fingerprint(
    text: str,
    *,
    language: str = "en",
    board: Optional[str] = None,
    class_: Optional[str] = None,
    subject: Optional[str] = None,
    chapter: Optional[str] = None,
    query_type: Optional[str] = None,
) -> str:
    """Return the 16-byte BLAKE3 hex (32 chars) fingerprint for ``text``.

    The fingerprint folds the syllabus scope (board / class / subject /
    chapter) into the digest so two queries that share a verb + topic
    but live under different chapters do NOT collide. ``query_type`` is
    a free-form caller hint (``definition`` / ``mcq`` / ``flashcard`` /
    ``glossary`` / ``chapter_summary`` / ...) folded into the key so a
    "Define photosynthesis" question and a "Generate flashcards for
    photosynthesis" generator never share an identity.
    """
    verb, topic = canonical_form(text or "", language)
    canon = "|".join((
        verb,
        (board or "").strip().lower(),
        str(class_ or "").strip().lower(),
        (subject or "").strip().lower(),
        (chapter or "").strip().lower(),
        (query_type or "").strip().lower(),
        topic,
    ))
    return _hash_16(canon)


# ── Optional: dual-read knob (read by ai_input_cache._key) ────────────
def dual_read_enabled() -> bool:
    """``CACHE_FINGERPRINT_DUAL_READ`` env knob. Default True for the
    30-day legacy-key bridge described in Task #10 §Done-looks-like.

    Set to ``"0"`` / ``"false"`` to drop the legacy read — every
    fingerprint miss will be a true miss and downstream LLM dispatch
    will fire.
    """
    raw = (os.environ.get("CACHE_FINGERPRINT_DUAL_READ") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


# ── D1-backed synonym sync (Task #10 §6 — syllabus graph lookup) ─────
# Reads the ``syllabus_topic_synonyms`` table out of the D1 mirror
# (Mongo fallback via the existing ``d1_mirror.read_with_fallback``
# helper) and feeds every row into the runtime synonym layer. The
# table contract is a single row per canonical topic with English /
# Assamese aliases:
#
#     {
#       "canonical_topic": "photosynthesis",
#       "topic_en": "photosynthesis",
#       "topic_as": "ফটোসিন্থেসিস",
#       "aliases_en": ["photo synthesis", "photo-synthesis"],
#       "aliases_as": ["সালোকসংশ্লেষণ"],
#     }
#
# The function is best-effort — a D1 outage / Mongo outage / missing
# table just yields zero rows so the canonical "fail loud" rule does
# not apply (the built-in seed map keeps the canonical CI pairs
# working). Callers that demand fresh data can inspect the returned
# row count and decide.
async def sync_synonyms_from_d1(db=None) -> int:
    """Pull syllabus topic synonyms from D1 (Mongo fallback) and
    register them into the runtime synonym layer.

    Returns the number of (language, alias → canonical) pairs that
    were registered. Idempotent.
    """
    try:
        from d1_mirror import read_with_fallback as _d1_read
    except Exception as e:
        logger.debug("[cache_fingerprint] d1_mirror unavailable: %s", e)
        return 0

    async def _mongo_loader():
        if db is None:
            return []
        try:
            cursor = db.syllabus_topic_synonyms.find({}, {"_id": 0})
            return await cursor.to_list(10000)
        except Exception as e:
            logger.warning("[cache_fingerprint] mongo fallback failed: %s", e)
            return []

    try:
        rows = await _d1_read(
            "syllabus_topic_synonyms", "all", "1", _mongo_loader,
        )
    except Exception as e:
        logger.warning("[cache_fingerprint] d1 sync error: %s", e)
        return 0
    if not isinstance(rows, list):
        return 0
    registered = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        canonical = str(
            r.get("canonical_topic") or r.get("topic_en") or ""
        ).strip().lower()
        if not canonical:
            continue
        en_aliases: list[str] = []
        if r.get("topic_en"):
            en_aliases.append(str(r["topic_en"]))
        en_aliases.extend(str(a) for a in (r.get("aliases_en") or []) if a)
        as_aliases: list[str] = []
        if r.get("topic_as"):
            as_aliases.append(str(r["topic_as"]))
        as_aliases.extend(str(a) for a in (r.get("aliases_as") or []) if a)
        for raw in en_aliases:
            register_synonym("en", raw, canonical)
            registered += 1
        for raw in as_aliases:
            register_synonym("as", raw, canonical)
            registered += 1
    if registered:
        logger.info(
            "[cache_fingerprint] D1 synonym sync registered %d aliases", registered,
        )
    return registered


__all__ = [
    "fingerprint",
    "canonical_form",
    "resolve_topic_synonym",
    "register_synonym",
    "sync_synonyms_from_d1",
    "dual_read_enabled",
    "QUERY_VERBS",
    "reset_runtime_synonyms_for_tests",
]
