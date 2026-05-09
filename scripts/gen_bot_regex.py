#!/usr/bin/env python3
"""Task #9 — Render the canonical bot registry as the four regex
sources Syrabit relies on.

Reads ``infra/bot-rules.yaml`` (the canonical bot registry — see its
header for the bucket semantics) and emits one regex string per
target. The output is consumed by ``scripts/check_bot_rules_drift.py``
(which compares the regex/token sets against the four runtime files)
and by humans reviewing what each runtime should accept after a
registry edit.

Usage:
    python scripts/gen_bot_regex.py            # print all targets
    python scripts/gen_bot_regex.py search     # print one target

Targets (each maps to exactly one runtime location):
    search         → SEARCH_BOT_UA / _SEARCH_BOT_UA_RE union
                     (verified_search + citation_ai + training_ai)
    verified       → verified_search bucket only
    citation       → citation_ai bucket only
    training       → training_ai bucket only (worker AI_BOT_UA)
    abusive        → abusive bucket only

Why a generator instead of in-place codegen? The four runtimes use
slightly different regex flavours (Python ``re`` vs JS ``RegExp``)
and embed extra non-bot tokens (``ia_archiver``, ``rogerbot``, …) for
historical reasons we don't want this script to touch. Drift is
enforced by ``check_bot_rules_drift.py`` checking that every YAML
token APPEARS in the corresponding regex source — not by overwriting
the file. That keeps the generator non-destructive while still
catching the only failure mode that matters: somebody adding a bot
to the YAML and forgetting one of the four files.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "infra" / "bot-rules.yaml"


def _load_yaml() -> dict:
    """Tiny tolerant YAML loader so this script has no third-party
    runtime dependency. Supports the exact subset bot-rules.yaml uses:
    top-level keys mapping to lists of dicts whose values are scalars
    or YAML flow-style lists. Comments (`# …`) and blank lines are
    stripped. If PyYAML happens to be installed we use it instead.
    """
    text = RULES_PATH.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text) or {}
    except Exception:
        pass
    return _mini_yaml_parse(text)


def _strip_comment(line: str) -> str:
    """Remove a trailing ``# …`` comment, preserving ``#`` inside
    quoted strings (the registry uses double-quoted ``note:`` values
    that may contain ``#``)."""
    in_str = False
    quote = ""
    for i, ch in enumerate(line):
        if in_str:
            if ch == quote and line[i - 1] != "\\":
                in_str = False
        elif ch in ('"', "'"):
            in_str = True
            quote = ch
        elif ch == "#":
            return line[:i].rstrip()
    return line.rstrip()


def _unquote(s: str) -> str:
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    return s


def _parse_flow_list(s: str) -> list[str]:
    inner = s.strip()[1:-1]
    if not inner.strip():
        return []
    return [_unquote(p.strip()) for p in inner.split(",") if p.strip()]


def _mini_yaml_parse(text: str) -> dict:
    out: dict = {}
    cur_bucket: str | None = None
    cur_entry: dict | None = None
    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        # Top-level key: `bucket:` (no leading whitespace)
        if not raw.startswith((" ", "\t")) and line.endswith(":"):
            cur_bucket = line[:-1].strip()
            if cur_bucket in ("version", "source_blueprint"):
                cur_bucket = None
                continue
            out[cur_bucket] = []
            cur_entry = None
            continue
        # `version: 1` style top-level scalar — ignore.
        if not raw.startswith((" ", "\t")) and ":" in line and not line.endswith(":"):
            continue
        if cur_bucket is None:
            continue
        stripped = line.lstrip()
        # New entry: `  - token: foo`
        if stripped.startswith("- "):
            cur_entry = {}
            out[cur_bucket].append(cur_entry)
            stripped = stripped[2:]
        if cur_entry is None or ":" not in stripped:
            continue
        k, _, v = stripped.partition(":")
        k = k.strip()
        v = v.strip()
        if v.startswith("["):
            cur_entry[k] = _parse_flow_list(v)
        else:
            cur_entry[k] = _unquote(v)
    return out


def all_tokens(rules: dict) -> dict[str, list[str]]:
    """Return ``{bucket: [token, …]}`` with stable order."""
    return {
        bucket: [e["token"] for e in entries if isinstance(e, dict) and e.get("token")]
        for bucket, entries in rules.items()
        if isinstance(entries, list)
    }


def _escape_for_regex(token: str) -> str:
    """Escape regex metachars in a UA token. Bot tokens are
    case-insensitive substrings — the only metachar we have to defend
    against today is ``/`` (in ``java/``)."""
    return re.escape(token)


def render_regex(tokens: list[str]) -> str:
    """Render a Python/JS-compatible alternation regex (no flags)."""
    return "|".join(_escape_for_regex(t) for t in tokens)


TARGETS = ("search", "verified", "citation", "training", "abusive")


def render_target(target: str, tokens: dict[str, list[str]]) -> str:
    if target == "search":
        # SEARCH_BOT_UA spans all three "allowed or counted" buckets.
        # Order: verified_search, citation_ai, training_ai (training
        # tokens are still recognised by the regex so the worker can
        # 403 them rather than letting them fall through as unknown).
        merged: list[str] = []
        seen: set[str] = set()
        for b in ("verified_search", "citation_ai", "training_ai"):
            for t in tokens.get(b, []):
                if t not in seen:
                    merged.append(t)
                    seen.add(t)
        return render_regex(merged)
    bucket_map = {
        "verified": "verified_search",
        "citation": "citation_ai",
        "training": "training_ai",
        "abusive": "abusive",
    }
    return render_regex(tokens[bucket_map[target]])


def render_manifest(tokens: dict[str, list[str]]) -> str:
    """Render the canonical regex artifact committed at
    ``infra/bot-regex.generated.json``. CI runs ``--apply`` and then
    ``git diff --exit-code`` on this file AND on the four runtime
    regex sources — that's the lock-step codegen contract: a YAML
    edit MUST produce a regenerated artifact AND regenerated runtime
    regex literals in the same commit, otherwise CI fails.

    Codegen model — IN-PLACE SOURCE OVERWRITE (round-11 reviewer
    requirement). ``--apply`` walks ``APPLY_SPECS`` and rewrites the
    body of each runtime regex literal from YAML buckets +
    per-target benign extras (declared in ``APPLY_SPECS`` itself,
    not in YAML, because they are not crawlers under bucket
    semantics — they are social-card / archival / legacy-SEO UAs
    that historically appeared in the union regex). The bidirectional
    drift guard remains as belt-and-braces enforcement.
    """
    import json
    payload = {
        "_generator": "scripts/gen_bot_regex.py --apply",
        "_source": "infra/bot-rules.yaml",
        "_note": (
            "Regenerated by CI. Do not edit by hand. The four runtime "
            "regex sources MUST contain every token below (enforced by "
            "scripts/check_bot_rules_drift.py)."
        ),
        "buckets": {b: list(toks) for b, toks in tokens.items()},
        "targets": {t: render_target(t, tokens) for t in TARGETS},
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# In-place codegen (round-11 requirement).
# ---------------------------------------------------------------------------
# Each spec rewrites the body of one runtime regex literal. The body is
# computed as the concatenation, in this order, of:
#   1. tokens from the YAML buckets listed in ``buckets``,
#   2. the ``extras`` list (per-target benign UAs that are not crawlers
#      under the four canonical buckets — social-card crawlers, archival,
#      legacy SEO — kept here so YAML stays a pure crawler registry).
# ``locator`` is a regex with three capturing groups: prefix, body,
# suffix. ``--apply`` replaces ``body`` with the rendered alternation,
# preserving prefix/suffix verbatim.

# Social/preview crawlers + archival + legacy SEO. Order is the order
# they appear in the alternation (after the YAML tokens).
_SOCIAL_PREVIEW = [
    "facebookexternalhit", "facebookbot", "twitterbot", "linkedinbot",
    "telegrambot", "whatsapp", "discordbot", "slackbot", "redditbot",
]
_ARCHIVAL = ["ia_archiver"]
_LEGACY_SEO = ["ahrefsbot", "semrushbot", "rogerbot", "mj12bot", "dotbot"]
_SOCIAL_EXTRAS = [
    "embedly", "quora link preview", "showyoubot", "outbrain",
    r"pinterest/0\.", r"developers\.google\.com/\+/web/snippet",
    "vkshare", "w3c_validator", "googleweblight",
]
# Generic abusive-bucket extras (not in YAML; legacy SEO bots that
# share the abusive bucket's 120 RPM ceiling).
_ABUSIVE_EXTRAS: list[str] = []

APPLY_SPECS: list[dict] = [
    # ---- artifacts/syrabit-backend/utils.py: _SEARCH_BOT_UA_RE ----
    {
        "name": "utils._SEARCH_BOT_UA_RE",
        "path": ROOT / "artifacts" / "syrabit-backend" / "utils.py",
        "locator": re.compile(
            r"(_SEARCH_BOT_UA_RE\s*=\s*re\.compile\(\n)"
            r"((?:[ \t]+(?:r\"[^\"]*\",?\s*(?:#[^\n]*)?|#[^\n]*)\n)+)"
            r"([ \t]+re\.IGNORECASE,\n\))",
        ),
        "buckets": ["verified_search", "citation_ai", "training_ai"],
        "extras": (
            _SOCIAL_PREVIEW + _ARCHIVAL + _LEGACY_SEO + _SOCIAL_EXTRAS
        ),
        "render": "python_raw_string_block",
    },
    # ---- artifacts/syrabit-backend/utils.py: _ABUSIVE_SCRAPER_UA_RE ----
    {
        "name": "utils._ABUSIVE_SCRAPER_UA_RE",
        "path": ROOT / "artifacts" / "syrabit-backend" / "utils.py",
        "locator": re.compile(
            r"(_ABUSIVE_SCRAPER_UA_RE\s*=\s*re\.compile\(\n)"
            r"((?:[ \t]+(?:r\"[^\"]*\",?\s*(?:#[^\n]*)?|#[^\n]*)\n)+)"
            r"([ \t]+re\.IGNORECASE,\n\))",
        ),
        "buckets": ["abusive"],
        "extras": _ABUSIVE_EXTRAS,
        "render": "python_raw_string_block",
    },
    # ---- artifacts/syrabit/vite.config.js: BOT_UA ----
    {
        "name": "vite.BOT_UA",
        "path": ROOT / "artifacts" / "syrabit" / "vite.config.js",
        "locator": re.compile(r"(const BOT_UA = /)([^/\n]+)(/i;)"),
        "buckets": ["verified_search", "citation_ai", "training_ai"],
        "extras": _SOCIAL_PREVIEW + _ARCHIVAL + _LEGACY_SEO,
        "render": "js_alternation",
    },
    # ---- artifacts/syrabit/public/_worker.js: SEARCH_BOT_UA ----
    {
        "name": "_worker.SEARCH_BOT_UA",
        "path": ROOT / "artifacts" / "syrabit" / "public" / "_worker.js",
        "locator": re.compile(r"(const SEARCH_BOT_UA = /)([^/\n]+)(/i;)"),
        "buckets": ["verified_search", "citation_ai", "training_ai"],
        "extras": _SOCIAL_PREVIEW,
        "render": "js_alternation",
    },
    # ---- workers/edge-proxy/src/index.ts: SEARCH_BOT_UA ----
    {
        "name": "edge.SEARCH_BOT_UA",
        "path": ROOT / "workers" / "edge-proxy" / "src" / "index.ts",
        "locator": re.compile(r"(const SEARCH_BOT_UA = /)([^/\n]+)(/i;)"),
        "buckets": ["verified_search", "citation_ai", "training_ai"],
        "extras": _SOCIAL_PREVIEW,
        "render": "js_alternation",
    },
    # ---- workers/edge-proxy/src/index.ts: AI_BOT_UA ----
    {
        "name": "edge.AI_BOT_UA",
        "path": ROOT / "workers" / "edge-proxy" / "src" / "index.ts",
        "locator": re.compile(r"(const AI_BOT_UA = /)([^/\n]+)(/i;)"),
        "buckets": ["training_ai"],
        "extras": [],
        "render": "js_word_boundary_group",
    },
]


def _render_body(spec: dict, tokens: dict[str, list[str]]) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for b in spec["buckets"]:
        for t in tokens.get(b, []):
            if t not in seen:
                merged.append(t)
                seen.add(t)
    for t in spec["extras"]:
        if t not in seen:
            merged.append(t)
            seen.add(t)
    if spec["render"] == "js_alternation":
        # Bot tokens are mostly safe — only `/` needs escaping for JS
        # regex literals. We keep extras that already contain regex
        # metachars (e.g. `pinterest/0\.`) verbatim.
        return "|".join(_js_token(t) for t in merged)
    if spec["render"] == "js_word_boundary_group":
        return r"\b(?:" + "|".join(_js_token(t) for t in merged) + r")\b"
    if spec["render"] == "python_raw_string_block":
        # Multi-line raw-string concatenation. 4-space indent, 75-col
        # soft wrap.
        chunks: list[str] = []
        cur = ""
        for tok in merged:
            piece = tok + "|"
            if len(cur) + len(piece) > 70 and cur:
                chunks.append(cur)
                cur = piece
            else:
                cur += piece
        if cur:
            chunks.append(cur)
        # Drop trailing `|` from last chunk.
        chunks[-1] = chunks[-1].rstrip("|")
        # Trailing comma on the LAST raw-string segment — required so
        # the surrounding `re.compile(...)` arglist stays valid Python
        # (without it `re.IGNORECASE` becomes implicit string
        # concatenation rather than a separate positional arg, raising
        # SyntaxError at import time).
        out_lines = [f'    r"{c}"' for c in chunks]
        out_lines[-1] += ","
        return "\n".join(out_lines) + "\n"
    raise ValueError(f"unknown render: {spec['render']}")


def _js_token(tok: str) -> str:
    """Escape a token for embedding inside a JS regex literal. We only
    escape `/` (the literal terminator) — every other char in our token
    set is regex-safe, and pre-escaped sequences like ``pinterest/0\\.``
    are intentionally preserved verbatim by callers."""
    if "\\" in tok:  # already-escaped extras pass through
        return tok
    return tok.replace("/", r"\/")


def apply_in_place(tokens: dict[str, list[str]]) -> list[str]:
    """Rewrite the body of each runtime regex literal from YAML +
    extras. Returns a list of human-readable change descriptions
    (one per spec). Raises ``RuntimeError`` if a locator misses."""
    changes: list[str] = []
    for spec in APPLY_SPECS:
        path: Path = spec["path"]
        text = path.read_text(encoding="utf-8")
        m = spec["locator"].search(text)
        if not m:
            raise RuntimeError(
                f"apply: locator missed in {path.relative_to(ROOT)} "
                f"for {spec['name']}",
            )
        new_body = _render_body(spec, tokens)
        new_text = text[:m.start()] + m.group(1) + new_body + m.group(3) + text[m.end():]
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            changes.append(f"rewrote {spec['name']} in {path.relative_to(ROOT)}")
        else:
            changes.append(f"unchanged {spec['name']} in {path.relative_to(ROOT)}")
    return changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", choices=TARGETS)
    ap.add_argument(
        "--apply", action="store_true",
        help="Rewrite the four runtime regex sources in-place from YAML "
             "and emit infra/bot-regex.generated.json (CI lock-step codegen).",
    )
    args = ap.parse_args()
    rules = _load_yaml()
    tokens = all_tokens(rules)
    if args.apply:
        out_path = ROOT / "infra" / "bot-regex.generated.json"
        out_path.write_text(render_manifest(tokens), encoding="utf-8")
        print(f"wrote {out_path.relative_to(ROOT)} "
              f"({sum(len(v) for v in tokens.values())} tokens, "
              f"{len(TARGETS)} targets)")
        for line in apply_in_place(tokens):
            print(line)
        return 0
    if args.target:
        print(render_target(args.target, tokens))
        return 0
    for t in TARGETS:
        print(f"# {t}")
        print(render_target(t, tokens))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
