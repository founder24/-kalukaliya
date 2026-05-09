#!/usr/bin/env python3
"""Task #9 — CI guard: verify the four bot regex sources stay in sync
with ``infra/bot-rules.yaml``.

Failure mode this catches: an editor adds a new crawler to the
canonical YAML registry but forgets to update one of the four
runtime regexes (or vice versa). Without this check the runtime
silently drops the bot into an unknown bucket — verified bots get
ratelimited as scrapers, training scrapers slip past the 403 block.

What it does:

1. Load ``infra/bot-rules.yaml`` and roll up the canonical token set
   per bucket via ``scripts/gen_bot_regex.py``.
2. For each of the four runtime files, extract the regex literal at
   the documented sentinel location, and verify every YAML token for
   that file's expected bucket is present (case-insensitive substring
   match against the regex source string).
3. Report missing tokens per file and exit 1 if any are missing.

Usage:
    python scripts/check_bot_rules_drift.py           # check
    python scripts/check_bot_rules_drift.py --list    # print roll-up

Designed to be invoked from the same canonical-delegation gate that
runs ``scripts/check_architecture_lock.py`` so both lock files share
one CI signal.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import gen_bot_regex  # noqa: E402

# (path, expected-bucket-union, regex-locator) tuples. The expected
# union is a list of YAML bucket names whose tokens MUST all appear in
# the file's regex. The locator is a compiled regex that captures the
# regex literal source (between the slashes for JS, after `r"` for
# Python) so we can string-search within it.
TARGETS: list[tuple[Path, list[str], re.Pattern[str]]] = [
    # 1. utils.py — Python; three separate compile() calls.
    (
        ROOT / "artifacts" / "syrabit-backend" / "utils.py",
        ["verified_search", "citation_ai", "training_ai"],
        re.compile(
            r"_SEARCH_BOT_UA_RE\s*=\s*re\.compile\(\s*(.*?)\s*,\s*re\.IGNORECASE",
            re.DOTALL,
        ),
    ),
    (
        ROOT / "artifacts" / "syrabit-backend" / "utils.py",
        ["abusive"],
        re.compile(
            r"_ABUSIVE_SCRAPER_UA_RE\s*=\s*re\.compile\(\s*(.*?)\s*,\s*re\.IGNORECASE",
            re.DOTALL,
        ),
    ),
    # 2. vite.config.js — JS regex literal.
    (
        ROOT / "artifacts" / "syrabit" / "vite.config.js",
        ["verified_search", "citation_ai", "training_ai"],
        re.compile(r"const\s+BOT_UA\s*=\s*/(.+?)/i", re.DOTALL),
    ),
    # 3. _worker.js — JS regex literal.
    (
        ROOT / "artifacts" / "syrabit" / "public" / "_worker.js",
        ["verified_search", "citation_ai", "training_ai"],
        re.compile(r"const\s+SEARCH_BOT_UA\s*=\s*/(.+?)/i", re.DOTALL),
    ),
    # 4. edge-proxy worker — JS regex literal (SEARCH_BOT_UA covers
    #    verified+citation+training; AI_BOT_UA covers training only).
    (
        ROOT / "workers" / "edge-proxy" / "src" / "index.ts",
        ["verified_search", "citation_ai", "training_ai"],
        re.compile(r"const\s+SEARCH_BOT_UA\s*=\s*/(.+?)/i", re.DOTALL),
    ),
    (
        ROOT / "workers" / "edge-proxy" / "src" / "index.ts",
        ["training_ai"],
        re.compile(r"const\s+AI_BOT_UA\s*=\s*/(.+?)/i", re.DOTALL),
    ),
]


def _check_one(
    path: Path, buckets: list[str], locator: re.Pattern[str],
    by_bucket: dict[str, list[str]],
) -> list[str]:
    """Return a list of human-readable error strings; empty == ok."""
    if not path.exists():
        return [f"{path.relative_to(ROOT)}: file missing"]
    text = path.read_text(encoding="utf-8", errors="replace")
    m = locator.search(text)
    if not m:
        return [f"{path.relative_to(ROOT)}: regex literal not found "
                f"(locator: {locator.pattern[:60]}…)"]
    body = m.group(1).lower()
    expected: list[str] = []
    seen: set[str] = set()
    for b in buckets:
        for t in by_bucket.get(b, []):
            if t not in seen:
                expected.append(t)
                seen.add(t)
    missing = [t for t in expected if t.lower() not in body]
    if not missing:
        return []
    return [
        f"{path.relative_to(ROOT)}: missing canonical tokens for "
        f"{'+'.join(buckets)}: {', '.join(missing)}"
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--list", action="store_true",
        help="Print the canonical roll-up and exit.",
    )
    args = ap.parse_args()

    rules = gen_bot_regex._load_yaml()
    by_bucket = gen_bot_regex.all_tokens(rules)

    if args.list:
        for b, toks in by_bucket.items():
            print(f"# {b} ({len(toks)})")
            for t in toks:
                print(f"  {t}")
            print()
        return 0

    errors: list[str] = []
    for path, buckets, locator in TARGETS:
        errors.extend(_check_one(path, buckets, locator, by_bucket))

    if errors:
        print("Bot-rules drift detected:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(
            "\nFix: update infra/bot-rules.yaml AND the four regex "
            "sources together. Run `python scripts/gen_bot_regex.py` "
            "for the canonical regex strings.",
            file=sys.stderr,
        )
        return 1
    print(
        f"Bot rules in sync — {sum(len(v) for v in by_bucket.values())} "
        f"tokens across {len(by_bucket)} buckets, "
        f"{len(TARGETS)} regex sources verified."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
