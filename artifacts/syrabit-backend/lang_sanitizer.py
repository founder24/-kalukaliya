"""Syrabit.ai — Indic (Assamese) language leakage detection and sanitisation.

The Sarvam Indic chat models occasionally emit stray English fragments inside
otherwise-Assamese replies (e.g. "me uses", "ssible", "terms"). This module
provides:

  * `measure_leakage(text)` — diagnostic ratio of non-whitelisted Latin chars.
  * `sanitize_assamese(text, threshold=...)` — strip stray Latin runs when the
    leakage ratio exceeds the threshold, while preserving allowed Latin
    fragments (numbers, units, acronyms, proper nouns, code, math, URLs).
  * `sanitize_assamese_with_optional_regenerate(...)` — async wrapper that
    additionally supports `translate`, `regenerate`, and `translate+regenerate`
    behaviours, calling out to a Sarvam `/translate` callable to substitute
    leaked English runs with their Assamese equivalents instead of just
    deleting them.

Behaviour and threshold are configurable via env so they can be tuned without
a redeploy:

  * `ASSAMESE_LEAK_THRESHOLD`   (float, default 0.05)
  * `ASSAMESE_LEAK_BEHAVIOUR`   ("off" | "strip" | "translate" |
                                 "regenerate" | "translate+regenerate",
                                 default "translate")
"""
from __future__ import annotations

import os
import re
import logging
from typing import Tuple, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# ── Run-recorder hook (Task #423) ──────────────────────────────────────
# A separate module (typically routes/cms_sarvam_health.py) registers a
# callback here at import-time. Every sanitiser run hands its diag dict
# to the callback so the admin dashboard can show how often cleanup
# fires, what action it took, and the leakage ratio distribution. The
# hook lives in this module (instead of the route layer wrapping each
# call) so EVERY caller of the sanitiser — current and future — emits
# stats automatically. Default is a no-op so unit tests / non-API uses
# don't need any wiring.
_RUN_RECORDER: Optional[Callable[[dict], None]] = None


def set_run_recorder(callback: Optional[Callable[[dict], None]]) -> None:
    """Install (or clear with None) the diag-dict recorder callback."""
    global _RUN_RECORDER
    _RUN_RECORDER = callback


def _emit_run(diag: dict) -> None:
    """Hand the diag dict to the recorder. Defensive: never let a broken
    recorder break the sanitiser response."""
    cb = _RUN_RECORDER
    if cb is None:
        return
    try:
        cb(dict(diag))
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"[INDIC-SANITIZE] run recorder failed: {e}")


ASSAMESE_RE = re.compile(r"[\u0980-\u09FF]")
LATIN_RUN_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")

_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```|`[^`\n]+`")
_MATH_BLOCK_RE = re.compile(r"\$\$[\s\S]*?\$\$|\$[^$\n]+\$|\\\([^)]+\\\)|\\\[[^\]]+\\\]")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")

_NUMERIC_RE = re.compile(r"^[A-Za-z]?\d+(?:[.,]\d+)*[A-Za-z%°]*$")
_UNIT_RE = re.compile(
    r"^(?:m|cm|mm|km|nm|µm|um|kg|g|mg|µg|ug|s|ms|min|hr|h|hz|khz|mhz|ghz|"
    r"w|kw|mw|n|pa|kpa|mpa|gpa|j|kj|mol|l|ml|°c|°f|°k|c|f|k|hp|psi|amp|a|v|"
    r"kv|mv|b|kb|mb|gb|tb|px|em|rem|pt|°|ev|kev|mev|gev|rad|deg)s?$",
    re.I,
)
_PROPER_NOUN_RE = re.compile(r"^[A-Z][A-Za-z]+$")
_ACRONYM_RE = re.compile(r"^[A-Z]{2,}(?:-?\d+[A-Za-z]?)?$")

# Common English words that *look* like proper nouns when capitalised at
# the start of a sentence ("This", "That", "Use", "Me", "It", …) but are
# not actually proper nouns. Whitelisting these would let real English
# leakage slip past the strip filter, so we explicitly deny them.
_COMMON_ENGLISH_DENYLIST = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "of",
    "in", "on", "at", "to", "from", "by", "for", "with", "as", "is",
    "are", "was", "were", "be", "been", "being", "am", "do", "does",
    "did", "have", "has", "had", "having", "will", "would", "shall",
    "should", "can", "could", "may", "might", "must", "this", "that",
    "these", "those", "it", "its", "he", "she", "they", "them", "his",
    "her", "their", "we", "us", "our", "you", "your", "i", "me", "my",
    "mine", "yours", "ours", "theirs", "use", "uses", "used", "using",
    "make", "makes", "made", "get", "gets", "got", "give", "gives",
    "given", "go", "goes", "went", "gone", "come", "comes", "came",
    "see", "sees", "saw", "seen", "know", "knows", "knew", "known",
    "think", "thinks", "thought", "say", "says", "said", "tell",
    "tells", "told", "ask", "asks", "asked", "find", "finds", "found",
    "look", "looks", "looked", "want", "wants", "wanted", "need",
    "needs", "needed", "try", "tries", "tried", "let", "lets", "put",
    "puts", "set", "sets", "run", "runs", "ran", "take", "takes",
    "took", "taken", "show", "shows", "showed", "shown", "good",
    "bad", "new", "old", "first", "last", "long", "short", "high",
    "low", "hot", "cold", "small", "large", "big", "little", "many",
    "much", "more", "less", "most", "least", "some", "any", "all",
    "every", "each", "no", "not", "yes", "very", "well", "also",
    "even", "still", "just", "only", "so", "than", "such", "what",
    "which", "who", "whom", "whose", "where", "when", "why", "how",
    "here", "there", "now", "then", "today", "yesterday", "tomorrow",
    "terms", "term", "thing", "things", "way", "ways", "time", "times",
    "year", "years", "day", "days", "month", "months", "week", "weeks",
    "people", "person", "man", "woman", "child", "children", "world",
    # Production-log additions: short Latin fragments observed slipping
    # past the previous threshold (Task #419).
    "study", "studies", "studying", "studied", "learn", "learns",
    "learning", "learned", "answer", "answers", "question", "questions",
    "problem", "problems", "solution", "solutions", "example",
    "examples", "definition", "definitions", "explanation", "topic",
    "topics", "chapter", "chapters", "lesson", "lessons", "subject",
    "subjects", "lesson", "true", "false", "correct", "incorrect",
    "right", "wrong", "important", "main", "basic", "simple", "easy",
    "hard", "difficult", "ssible", "ble", "tion", "tions", "ment",
    "ments", "able", "ness", "ity", "ies", "ing", "ed", "er", "est",
    "very", "really", "quite", "rather", "almost", "nearly", "about",
    "around", "above", "below", "between", "among", "through", "across",
    "before", "after", "during", "while", "until", "since", "because",
    "although", "though", "however", "therefore", "moreover", "further",
    "rule", "rules", "law", "laws", "method", "methods", "process",
    "processes", "step", "steps", "part", "parts", "type", "types",
    "kind", "kinds", "form", "forms", "list", "lists", "note", "notes",
    "information", "info", "data", "result", "results", "value",
    "values", "number", "numbers", "amount", "amounts",
})


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_str(name: str, default: str) -> str:
    val = os.getenv(name)
    return (val or default).strip().lower()


# Default behaviour after Task #419 is `translate` — calls Sarvam /translate
# on the suspicious Latin runs and splices the Assamese translation back in
# instead of deleting them. This adds at most one extra Sarvam round-trip
# per leaky reply (only when leakage > threshold) and produces visibly
# cleaner Assamese answers than `strip`. The stronger `translate+regenerate`
# combo trades latency for purity and is opt-in via env.
_VALID_BEHAVIOURS = ("off", "strip", "translate", "regenerate", "translate+regenerate")
_DEFAULT_BEHAVIOUR = "translate"
_DEFAULT_THRESHOLD = 0.05

# Task #492 — Indic chat is locked to the Sarvam → Workers-AI IndicTrans2
# chain (V4 §15 amendment). Vertex was removed by #490; the runtime
# `indic_provider` knob is kept for backwards-compatible admin UI but only
# accepts the single value "sarvam". Any other value is rejected upstream.
_VALID_INDIC_PROVIDERS = ("sarvam",)
_DEFAULT_INDIC_PROVIDER = "sarvam"

# Per-Task #422: in-memory runtime override layer. Admins PATCH the
# override via `/admin/assamese-purity` and it's persisted to mongo
# (db.api_config.assamese_purity_override) so it survives api restarts.
# At api boot the lifespan hook reads the persisted document and calls
# `apply_runtime_override(...)` so the in-memory copy stays in sync.
# Override beats env vars; env vars beat defaults.
_RUNTIME_OVERRIDE: dict | None = None


def _normalise_behaviour(value: str | None) -> str | None:
    if value is None:
        return None
    v = (value or "").strip().lower()
    return v if v in _VALID_BEHAVIOURS else None


def _normalise_threshold(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # Clamp to a sane range so admins can't accidentally set a value that
    # disables sanitisation (>1) or runs it on every reply (<=0).
    if f <= 0 or f >= 1:
        return None
    return f


def _normalise_indic_provider(value: str | None) -> str | None:
    if value is None:
        return None
    v = (value or "").strip().lower()
    return v if v in _VALID_INDIC_PROVIDERS else None


def apply_runtime_override(
    behaviour: str | None = None,
    threshold: float | None = None,
    *,
    indic_provider: str | None = None,
    updated_by: str | None = None,
) -> dict:
    """Update the in-memory override. Pass `None` for a field to leave it
    unchanged. Returns the new override snapshot. Admin route layers
    persist this to mongo themselves; this function only mutates the
    in-memory copy used by `get_behaviour()` / `get_threshold()` /
    `get_indic_provider()`."""
    global _RUNTIME_OVERRIDE
    current = dict(_RUNTIME_OVERRIDE or {})
    nb = _normalise_behaviour(behaviour)
    nt = _normalise_threshold(threshold)
    np_ = _normalise_indic_provider(indic_provider)
    if nb is not None:
        current["behaviour"] = nb
    if nt is not None:
        current["threshold"] = nt
    if np_ is not None:
        current["indic_provider"] = np_
    if updated_by:
        current["updated_by"] = updated_by
    if not current:
        return {}
    _RUNTIME_OVERRIDE = current
    logger.info(
        "[INDIC-SANITIZE] runtime override applied: behaviour=%s threshold=%s indic_provider=%s by=%s",
        current.get("behaviour"), current.get("threshold"),
        current.get("indic_provider"), current.get("updated_by"),
    )
    return dict(current)


def clear_runtime_override() -> None:
    """Drop the in-memory override so env/defaults take over again."""
    global _RUNTIME_OVERRIDE
    if _RUNTIME_OVERRIDE is not None:
        logger.info("[INDIC-SANITIZE] runtime override cleared")
    _RUNTIME_OVERRIDE = None


def get_runtime_override() -> dict | None:
    """Returns a defensive copy of the active override (or None)."""
    return dict(_RUNTIME_OVERRIDE) if _RUNTIME_OVERRIDE else None


def get_threshold() -> float:
    if _RUNTIME_OVERRIDE and "threshold" in _RUNTIME_OVERRIDE:
        return float(_RUNTIME_OVERRIDE["threshold"])
    return _env_float("ASSAMESE_LEAK_THRESHOLD", _DEFAULT_THRESHOLD)


def get_behaviour() -> str:
    if _RUNTIME_OVERRIDE and "behaviour" in _RUNTIME_OVERRIDE:
        b = str(_RUNTIME_OVERRIDE["behaviour"]).strip().lower()
        if b in _VALID_BEHAVIOURS:
            return b
    b = _env_str("ASSAMESE_LEAK_BEHAVIOUR", _DEFAULT_BEHAVIOUR)
    if b not in _VALID_BEHAVIOURS:
        return _DEFAULT_BEHAVIOUR
    return b


def get_indic_provider() -> str:
    """Which provider should serve Indic/Assamese chat.

    Override > env `ASSAMESE_LLM_PROVIDER` > default (`sarvam`). Used by
    `llm.call_llm_api_stream` to decide whether to route Assamese turns
    through the Vertex Gemini Flash fast-path (Task #628) or the legacy
    Sarvam hedged pool.
    """
    if _RUNTIME_OVERRIDE and "indic_provider" in _RUNTIME_OVERRIDE:
        p = str(_RUNTIME_OVERRIDE["indic_provider"]).strip().lower()
        if p in _VALID_INDIC_PROVIDERS:
            return p
    p = _env_str("ASSAMESE_LLM_PROVIDER", _DEFAULT_INDIC_PROVIDER)
    if p not in _VALID_INDIC_PROVIDERS:
        return _DEFAULT_INDIC_PROVIDER
    return p


def _effective_source(field: str) -> str:
    """Reports whether the live `field` value came from the override
    layer, env var, or the hard-coded default. Surfaced via
    `GET /admin/assamese-purity` (the V4 §15 / Task #492 replacement
    for the retired `/sarvam/status`) so admins can see at a glance
    why a value is in effect (e.g. did my PATCH stick? am I still
    on env vars?)."""
    if _RUNTIME_OVERRIDE and field in _RUNTIME_OVERRIDE:
        return "override"
    env_name = {
        "threshold": "ASSAMESE_LEAK_THRESHOLD",
        "behaviour": "ASSAMESE_LEAK_BEHAVIOUR",
        "indic_provider": "ASSAMESE_LLM_PROVIDER",
    }.get(field, "")
    return "env" if env_name and os.getenv(env_name) else "default"


def get_runtime_config() -> dict:
    """Snapshot of the live Assamese-purity config (surfaced via
    `GET /admin/assamese-purity`; the legacy `/sarvam/status` host was
    retired by V4 §15 / Task #492 and now returns 410 GONE)."""
    return {
        "threshold": get_threshold(),
        "behaviour": get_behaviour(),
        "indic_provider": get_indic_provider(),
        "valid_behaviours": list(_VALID_BEHAVIOURS),
        "valid_indic_providers": list(_VALID_INDIC_PROVIDERS),
        "default_threshold": _DEFAULT_THRESHOLD,
        "default_behaviour": _DEFAULT_BEHAVIOUR,
        "default_indic_provider": _DEFAULT_INDIC_PROVIDER,
        "override": get_runtime_override(),
        "behaviour_source": _effective_source("behaviour"),
        "threshold_source": _effective_source("threshold"),
        "indic_provider_source": _effective_source("indic_provider"),
    }


# Log the active config on import so admins can see it in the api boot log.
logger.info(
    "[INDIC-SANITIZE] startup config: behaviour=%s threshold=%.3f "
    "(env ASSAMESE_LEAK_BEHAVIOUR / ASSAMESE_LEAK_THRESHOLD)",
    get_behaviour(), get_threshold(),
)


def _is_allowed_latin_token(tok: str) -> bool:
    if not tok:
        return True
    if _NUMERIC_RE.match(tok):
        return True
    if _UNIT_RE.match(tok):
        return True
    if _ACRONYM_RE.match(tok):
        return True
    # Single uppercase letter (e.g. variable X, point A) — keep.
    if len(tok) == 1 and tok.isupper():
        return True
    # Capitalised proper noun — keep ONLY when it's at least 4 chars long
    # AND not a common English word in disguise (e.g. "This", "That", "Use").
    # Two-letter and three-letter title-case tokens are too risky — they are
    # almost always English function words ("Me", "It", "The", "And").
    if (
        _PROPER_NOUN_RE.match(tok)
        and len(tok) >= 4
        and tok.lower() not in _COMMON_ENGLISH_DENYLIST
    ):
        return True
    return False


def _protected_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for rx in (_CODE_BLOCK_RE, _MATH_BLOCK_RE, _URL_RE):
        for m in rx.finditer(text):
            spans.append((m.start(), m.end()))
    spans.sort()
    return spans


def _in_spans(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    for s, e in spans:
        if s <= start and end <= e:
            return True
        if e <= start:
            continue
        if s >= end:
            break
    return False


def _strip_protected(text: str) -> str:
    out = _CODE_BLOCK_RE.sub(" ", text)
    out = _MATH_BLOCK_RE.sub(" ", out)
    out = _URL_RE.sub(" ", out)
    return out


def measure_leakage(text: str) -> dict:
    """Diagnostic info about Latin leakage inside an Assamese reply."""
    if not text:
        return {"ratio": 0.0, "has_assamese": False, "suspicious_tokens": [], "total": 0, "leaked": 0}
    stripped = _strip_protected(text)
    has_assamese = bool(ASSAMESE_RE.search(stripped))
    if not has_assamese:
        return {"ratio": 0.0, "has_assamese": False, "suspicious_tokens": [], "total": 0, "leaked": 0}
    total_chars = sum(1 for c in stripped if not c.isspace())
    if total_chars == 0:
        return {"ratio": 0.0, "has_assamese": True, "suspicious_tokens": [], "total": 0, "leaked": 0}
    leaked = 0
    susp: list[str] = []
    for m in LATIN_RUN_RE.finditer(stripped):
        tok = m.group(0)
        if _is_allowed_latin_token(tok):
            continue
        leaked += len(tok)
        susp.append(tok)
    return {
        "ratio": leaked / total_chars,
        "has_assamese": True,
        "suspicious_tokens": susp,
        "total": total_chars,
        "leaked": leaked,
    }


def _strip_suspicious_latin(text: str) -> str:
    spans = _protected_spans(text)
    out: list[str] = []
    last = 0
    for m in LATIN_RUN_RE.finditer(text):
        if _in_spans(spans, m.start(), m.end()):
            continue
        tok = m.group(0)
        if _is_allowed_latin_token(tok):
            continue
        out.append(text[last:m.start()])
        last = m.end()
    out.append(text[last:])
    cleaned = "".join(out)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([,.!?।])", r"\1", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _suspicious_runs(text: str) -> list[tuple[int, int, str]]:
    """Find contiguous suspicious Latin runs (multiple non-allowed Latin
    tokens separated by short whitespace/punct), skipping protected spans
    and allowed-Latin tokens. Returns (start, end, fragment) tuples in
    document order. Used by the translate-fix path so we send Sarvam
    coherent multi-word fragments instead of one word at a time.
    """
    spans = _protected_spans(text)
    matches = list(LATIN_RUN_RE.finditer(text))
    runs: list[tuple[int, int, str]] = []
    j = 0
    while j < len(matches):
        m = matches[j]
        if _in_spans(spans, m.start(), m.end()) or _is_allowed_latin_token(m.group(0)):
            j += 1
            continue
        run_start, run_end = m.start(), m.end()
        k = j + 1
        while k < len(matches):
            nxt = matches[k]
            between = text[run_end:nxt.start()]
            if (
                re.fullmatch(r"[\s,'’\-]{0,4}", between)
                and not _in_spans(spans, nxt.start(), nxt.end())
                and not _is_allowed_latin_token(nxt.group(0))
            ):
                run_end = nxt.end()
                k += 1
            else:
                break
        runs.append((run_start, run_end, text[run_start:run_end]))
        j = k
    return runs


async def _translate_runs(
    text: str,
    runs: list[tuple[int, int, str]],
    *,
    translate_callable: Callable[[str], Awaitable[str]],
) -> Tuple[str, list[str]]:
    """Translate each suspicious run via `translate_callable` and splice
    the results back into `text`. Returns (new_text, translations).
    Failures collapse to the empty string (caller decides whether to fall
    back to strip)."""
    translations: list[str] = []
    for (_, _, frag) in runs:
        try:
            tr = await translate_callable(frag)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"[INDIC-SANITIZE] translate callable failed for {frag!r}: {e}")
            tr = ""
        translations.append((tr or "").strip())
    out: list[str] = []
    last = 0
    for (start, end, _), tr in zip(runs, translations):
        out.append(text[last:start])
        out.append(tr)
        last = end
    out.append(text[last:])
    new_text = "".join(out)
    new_text = re.sub(r"[ \t]{2,}", " ", new_text)
    new_text = re.sub(r" +([,.!?।])", r"\1", new_text)
    return new_text.strip(), translations


def sanitize_assamese(text: str, threshold: float | None = None) -> Tuple[str, dict]:
    """Sanitise Assamese reply if leakage exceeds threshold.

    Returns (cleaned_text, diagnostics). When `action == "stripped"`, the
    caller should prefer `cleaned_text` over the original.
    """
    if not text:
        return text, {"ratio": 0.0, "action": "noop", "has_assamese": False, "suspicious_tokens": [], "total": 0, "leaked": 0}
    thr = threshold if threshold is not None else get_threshold()
    diag = measure_leakage(text)
    diag["action"] = "noop"
    diag["threshold"] = thr
    if not diag["has_assamese"] or diag["ratio"] <= thr:
        return text, diag
    cleaned = _strip_suspicious_latin(text)
    diag["action"] = "stripped"
    diag["cleaned_len"] = len(cleaned)
    return cleaned, diag


async def sanitize_assamese_with_optional_regenerate(
    raw: str,
    *,
    threshold: float | None = None,
    behaviour: str | None = None,
    regenerate_callable: Optional[Callable[[], Awaitable[Optional[str]]]] = None,
    translate_callable: Optional[Callable[[str], Awaitable[str]]] = None,
    trace: Optional[dict] = None,
    _emit: bool = True,
) -> Tuple[str, dict]:
    """Sanitise Assamese output using the active behaviour strategy.

    Behaviours:
      * `off` — return raw, only measure.
      * `strip` — delete suspicious Latin runs (legacy, lossy).
      * `translate` — call `translate_callable(fragment)` for each
        suspicious Latin run and splice the Assamese translation back in;
        falls back to `strip` if no translate_callable is provided or all
        translations come back empty.
      * `regenerate` — strip, then if `regenerate_callable` is provided
        call the LLM once with a stronger directive and pick whichever
        retry has lower leakage.
      * `translate+regenerate` — translate first, and if leakage is still
        above threshold afterwards, also try one regenerate pass.

    Diagnostics include `regenerated`, `translated`, `behaviour`, and
    `original_ratio` so callers can log the action taken per reply.
    """
    behaviour = (behaviour or get_behaviour()).strip().lower()
    if behaviour not in _VALID_BEHAVIOURS:
        behaviour = _DEFAULT_BEHAVIOUR
    thr = threshold if threshold is not None else get_threshold()

    if behaviour == "off":
        diag = measure_leakage(raw)
        diag.update({"action": "noop", "threshold": thr,
                     "regenerated": False, "translated": False,
                     "behaviour": behaviour})
        if _emit:
            diag["raw_text"] = raw
            diag["cleaned_text"] = raw
            if trace:
                diag["trace"] = dict(trace)
            _emit_run(diag)
        return raw, diag

    # Initial measure / decide whether to act at all.
    initial_diag = measure_leakage(raw)
    initial_diag["threshold"] = thr
    initial_diag["behaviour"] = behaviour
    initial_diag["regenerated"] = False
    initial_diag["translated"] = False
    if not initial_diag["has_assamese"] or initial_diag["ratio"] <= thr:
        initial_diag["action"] = "noop"
        if _emit:
            initial_diag["raw_text"] = raw
            initial_diag["cleaned_text"] = raw
            if trace:
                initial_diag["trace"] = dict(trace)
            _emit_run(initial_diag)
        return raw, initial_diag

    cleaned = raw
    diag = dict(initial_diag)
    diag["original_ratio"] = initial_diag["ratio"]
    # Track the ratio AFTER translate-fix alone (before any destructive
    # strip) so `translate+regenerate` can decide fallback purely on
    # whether the non-destructive translate step succeeded.
    post_translate_ratio = initial_diag["ratio"]

    # ── Step 1: translate-fix when requested ───────────────────────────────
    wants_translate = behaviour in ("translate", "translate+regenerate")
    if wants_translate and translate_callable is not None:
        runs = _suspicious_runs(raw)
        if runs:
            translated_text, translations = await _translate_runs(
                raw, runs, translate_callable=translate_callable,
            )
            non_empty = sum(1 for t in translations if t)
            # Only accept the translated text if at least one fragment came
            # back non-empty AND the result has lower leakage than before.
            tr_diag = measure_leakage(translated_text)
            if non_empty > 0 and tr_diag["ratio"] < diag["ratio"]:
                cleaned = translated_text
                diag["translated"] = True
                diag["translated_runs"] = non_empty
                diag["ratio"] = tr_diag["ratio"]
                diag["suspicious_tokens"] = tr_diag["suspicious_tokens"]
                diag["action"] = "translated"
                post_translate_ratio = tr_diag["ratio"]

    # ── Step 2: strip whatever is still leaking ────────────────────────────
    if diag.get("ratio", initial_diag["ratio"]) > thr:
        stripped = _strip_suspicious_latin(cleaned)
        st_diag = measure_leakage(stripped)
        if st_diag["ratio"] < diag.get("ratio", initial_diag["ratio"]):
            cleaned = stripped
            diag["ratio"] = st_diag["ratio"]
            diag["suspicious_tokens"] = st_diag["suspicious_tokens"]
            diag["action"] = (
                "translated+stripped" if diag.get("translated") else "stripped"
            )

    # ── Step 3: optional one-shot regenerate retry ─────────────────────────
    # Behaviour-specific gating:
    #   * `regenerate` — strip is destructive, so always retry to see if a
    #     fresh attempt produces a naturally cleaner Assamese reply, even
    #     when strip already brought the post-cleanup ratio under threshold.
    #   * `translate+regenerate` — translate is non-destructive (it
    #     replaces leaked English with Assamese), so only fall back to a
    #     regenerate when the post-translate/post-strip ratio is still
    #     above threshold. This is the explicit "fallback" semantics the
    #     task requires and avoids paying for an extra LLM call when the
    #     translate step already produced a clean reply.
    wants_regenerate = behaviour in ("regenerate", "translate+regenerate")
    # `translate+regenerate` regenerate gate is based on the
    # post-translate ratio (NOT the post-strip ratio), because the
    # whole point of the combo is "if non-destructive translate didn't
    # bring the reply under threshold, ask the LLM for a fresh attempt
    # rather than relying on destructive strip". When translate alone
    # succeeded, we skip the extra LLM call.
    if behaviour == "translate+regenerate" and post_translate_ratio <= thr:
        wants_regenerate = False
    if wants_regenerate and regenerate_callable is not None:
        try:
            retry_raw = await regenerate_callable()
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"[INDIC-SANITIZE] regenerate callable failed: {e}")
            retry_raw = None
        if retry_raw:
            retry_diag = measure_leakage(retry_raw)
            diag["retry_ratio"] = retry_diag["ratio"]
            diag["retry_has_assamese"] = retry_diag["has_assamese"]
            # CRITICAL: `measure_leakage` returns ratio=0 when the text
            # contains NO Assamese script at all (pure English short-
            # circuit). Without this guard a model that ignored our
            # directive and replied entirely in English would always look
            # "better" than the original leaky Assamese reply and would
            # be emitted to the user. Require the retry to contain
            # Assamese script before considering it a candidate.
            if (
                retry_diag["has_assamese"]
                and retry_diag["ratio"] < initial_diag["ratio"]
            ):
                # Re-run the same pipeline on the retry so it also gets
                # translate/strip benefit, but skip another regenerate.
                # `_emit=False` suppresses the inner emission so each
                # top-level invocation produces exactly ONE run doc —
                # otherwise the dashboard double-counts every successful
                # regenerate (one inner translate/strip + one outer
                # regenerated event), inflating both `total` and the
                # action distribution.
                retry_clean, retry_clean_diag = await sanitize_assamese_with_optional_regenerate(
                    retry_raw,
                    threshold=thr,
                    behaviour="translate" if translate_callable else "strip",
                    translate_callable=translate_callable,
                    regenerate_callable=None,
                    _emit=False,
                )
                retry_clean_diag["regenerated"] = True
                retry_clean_diag["behaviour"] = behaviour
                retry_clean_diag["original_ratio"] = initial_diag["ratio"]
                # Force the OUTER action label so the dashboard counts
                # this as a regenerate event, not a translate/strip event.
                if retry_clean_diag.get("action", "noop") == "noop":
                    retry_clean_diag["action"] = "regenerated"
                else:
                    retry_clean_diag["action"] = (
                        f"regenerated+{retry_clean_diag['action']}"
                    )
                if _emit:
                    retry_clean_diag["raw_text"] = raw
                    retry_clean_diag["cleaned_text"] = retry_clean
                    if trace:
                        retry_clean_diag["trace"] = dict(trace)
                    _emit_run(retry_clean_diag)
                return retry_clean, retry_clean_diag

    if diag.get("action", "noop") == "noop":
        # Nothing changed — surface that explicitly so callers can skip
        # re-emitting the buffered text.
        diag["action"] = "noop"
    if _emit:
        diag["raw_text"] = raw
        diag["cleaned_text"] = cleaned
        if trace:
            diag["trace"] = dict(trace)
        _emit_run(diag)
    return cleaned, diag


__all__ = [
    "ASSAMESE_RE",
    "measure_leakage",
    "sanitize_assamese",
    "sanitize_assamese_with_optional_regenerate",
    "get_threshold",
    "get_behaviour",
    "get_runtime_config",
    "get_runtime_override",
    "apply_runtime_override",
    "clear_runtime_override",
]
