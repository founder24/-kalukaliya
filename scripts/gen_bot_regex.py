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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", choices=TARGETS)
    args = ap.parse_args()
    rules = _load_yaml()
    tokens = all_tokens(rules)
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
