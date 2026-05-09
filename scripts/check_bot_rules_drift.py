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


# Match a UA token at an actual regex-alternation boundary: preceded
# by `|`, `(`, `"`, `'`, `\b`, or start; followed by `|`, `)`, `"`,
# `'`, `\b`, or end. The Python `re.compile()` regex sources are
# multiline raw-string concatenations such as
# ``re.compile(r"(googlebot|" r"rogerbot|" ...)`` — so a token at
# the start of a continuation segment is preceded by `"` or `'`,
# not by `|` or `(`. Including the quote chars catches that case
# (this was the gap the reviewer flagged: `rogerbot` slipping past
# the boundary because it sat at the head of a raw-string segment).
_REGEX_ALT_TOKEN = re.compile(
    r"""(?:^|[|(\"']|\\b)\s*([A-Za-z][A-Za-z0-9_\-]{2,})\s*(?=[|)\"']|\\b|$)""",
)
# Strip Python `# …` comment lines from a multi-line capture before we
# tokenise. The captured body for `re.compile(...)` may span comment
# lines that contain words like `task` or `before` which would
# otherwise look like extraneous regex tokens.
_PY_COMMENT_LINE = re.compile(r"^\s*#.*$", re.MULTILINE)
# Tokens we deliberately allow in regex sources even though they are
# not in the YAML. Keep tiny; expand only with a comment justifying
# the entry.
_BENIGN_REGEX_TOKENS = {
    # Generic preview / social-card crawlers — matched by SEARCH_BOT_UA
    # so the worker can prerender for them, but they're neither
    # search engines nor citation engines so they don't fit the four
    # canonical buckets.
    "twitterbot", "facebookbot", "facebookexternalhit", "linkedinbot",
    "telegrambot", "whatsapp", "discordbot", "slackbot", "redditbot",
    "pinterest", "vkshare", "w3c_validator", "embedly", "outbrain",
    "quora", "showyoubot", "googleweblight",
    # Historical training-AI aliases retained in the union regex.
    "anthropic-ai", "anthropic_ai", "img2dataset", "omgili",
    # Internet Archive — neither search, citation, training, nor
    # abusive; kept in the SEARCH_BOT_UA union so the worker
    # prerenders for archival snapshots.
    "ia_archiver",
    # Historical SEO/marketing crawlers retained in the union regex
    # for log-classification (counted, never blocked, never on the
    # 60K RPM fast path). Not in YAML because they're neither
    # verified search nor citation engines.
    "rogerbot", "ahrefsbot", "semrushbot", "mj12bot", "blexbot",
    "dotbot", "exabot", "sogou", "yacy", "yacybot",
    # JS regex flag / control-char detritus.
    "true", "false", "null",
}


def _extract_regex_tokens(body: str) -> set[str]:
    """Pull alternation-boundary tokens from a regex source body.
    Strips Python `#` comment lines first so the wrapper code/notes
    don't pollute the token set."""
    cleaned = _PY_COMMENT_LINE.sub("", body)
    return {m.group(1).lower() for m in _REGEX_ALT_TOKEN.finditer(cleaned)}


def _check_one(
    path: Path, buckets: list[str], locator: re.Pattern[str],
    by_bucket: dict[str, list[str]],
) -> list[str]:
    """Bidirectional check: every YAML token for the bucket(s) MUST
    appear in the regex source, AND every UA-shaped token in the regex
    source MUST appear in the YAML (or be on the small benign
    allowlist above). Returns a list of human-readable errors; empty
    list == in sync."""
    if not path.exists():
        return [f"{path.relative_to(ROOT)}: file missing"]
    text = path.read_text(encoding="utf-8", errors="replace")
    m = locator.search(text)
    if not m:
        return [f"{path.relative_to(ROOT)}: regex literal not found "
                f"(locator: {locator.pattern[:60]}…)"]
    body = m.group(1).lower()

    # Forward direction: YAML → regex. Use alternation-boundary
    # matching (token must be preceded/followed by `|`, `(`, `)`, `/`,
    # `"`, `'`, `^`, `$`, or string boundary) — NOT plain substring —
    # so a YAML token like `yandex` can't be falsely satisfied by the
    # presence of `yandexbot` in the regex body. Closes the audit
    # comment from the round-6 review.
    expected: list[str] = []
    seen: set[str] = set()
    for b in buckets:
        for t in by_bucket.get(b, []):
            if t not in seen:
                expected.append(t)
                seen.add(t)
    # `:` is included so tokens inside JS non-capturing groups
    # (`/\b(?:gptbot|ccbot|...)\b/`) are counted as boundary-aligned.
    _BOUNDARY = r'(?:^|[|()/"\'\\\^\$:])'
    _BOUNDARY_END = r'(?:[|()/"\'\\\^\$:]|$)'
    missing = [
        t for t in expected
        if not re.search(_BOUNDARY + re.escape(t.lower()) + _BOUNDARY_END, body)
    ]

    # Reverse direction: regex → YAML. Anything matched by the regex
    # but absent from EVERY bucket of the YAML is drift in the
    # other direction (a runtime token that no longer corresponds to
    # a bucket → either remove it from the regex or add it to the
    # YAML). We compare against the union of ALL buckets, not just
    # the buckets the regex is supposed to cover, because a
    # _SEARCH_BOT_UA_RE may legitimately reference an abusive token
    # if that token was added to the union for triage logging.
    all_yaml_tokens = {t.lower() for toks in by_bucket.values() for t in toks}
    regex_tokens = _extract_regex_tokens(body)
    extras = sorted(
        tok for tok in regex_tokens
        if tok not in all_yaml_tokens
        and tok not in _BENIGN_REGEX_TOKENS
        # Skip tokens that are sub-strings of any YAML token (handles
        # 'baidu' when YAML has 'baiduspider'); reverse drift is only
        # meaningful for *standalone* identifiers.
        and not any(tok in y for y in all_yaml_tokens)
    )

    errors: list[str] = []
    if missing:
        errors.append(
            f"{path.relative_to(ROOT)}: missing canonical tokens for "
            f"{'+'.join(buckets)}: {', '.join(missing)}"
        )
    if extras:
        errors.append(
            f"{path.relative_to(ROOT)}: extraneous tokens not in "
            f"infra/bot-rules.yaml (add to YAML or to "
            f"_BENIGN_REGEX_TOKENS): {', '.join(extras)}"
        )
    return errors


# Enforce parity between YAML `rdns_suffixes` and the worker's
# BOT_RDNS_SUFFIXES map: every YAML token with non-empty rdns_suffixes
# must have a matching family in the worker covering all listed suffixes.

_WORKER_PATH = ROOT / "workers" / "edge-proxy" / "src" / "index.ts"
_RDNS_BLOCK = re.compile(
    r"const BOT_RDNS_SUFFIXES[\s\S]*?\n\];", re.MULTILINE,
)
_RDNS_ROW = re.compile(
    r'\["[^"]+",\s*/[^/]+/i,\s*\[([^\]]*)\]\]',
)
_RDNS_FAMILY_PATTERN = re.compile(
    r'\["([^"]+)",\s*/([^/]+)/i,\s*\[([^\]]*)\]\]',
)


def _check_rdns_suffix_parity(rules: dict) -> list[str]:
    """Verify every YAML entry with rdns_suffixes has a matching row
    in the worker's BOT_RDNS_SUFFIXES map covering all listed
    suffixes (trailing-dot-normalised). Returns a list of errors
    (empty list == in sync)."""
    if not _WORKER_PATH.exists():
        return []
    block_match = _RDNS_BLOCK.search(_WORKER_PATH.read_text(encoding="utf-8"))
    if not block_match:
        return [f"{_WORKER_PATH.relative_to(ROOT)}: BOT_RDNS_SUFFIXES block not found"]
    block_text = block_match.group(0)
    # Build a flat set of (token, suffix) pairs from the worker
    # (token is the regex source — we'll do substring match against
    # YAML tokens). Lowercased + trailing-dot-normalised.
    worker_pairs: list[tuple[str, set[str]]] = []
    for m in _RDNS_FAMILY_PATTERN.finditer(block_text):
        # group(2) is the regex body between `/.../i` slashes — match
        # against YAML tokens via substring-on-alternation. Each
        # alternation arm may carry anchors (`^yandex`) which we strip.
        regex_src = m.group(2).lower().replace("^", "")
        suffixes_raw = m.group(3)
        suffixes = {
            s.strip().strip('"').rstrip(".").lower()
            for s in suffixes_raw.split(",") if s.strip()
        }
        worker_pairs.append((regex_src, suffixes))

    errors: list[str] = []
    for bucket_entries in rules.values():
        if not isinstance(bucket_entries, list):
            continue
        for entry in bucket_entries:
            if not isinstance(entry, dict):
                continue
            tok = entry.get("token", "").lower()
            suffixes = entry.get("rdns_suffixes") or []
            if not tok or not suffixes:
                continue
            yaml_suffixes = {s.strip().rstrip(".").lower() for s in suffixes}
            # Find a worker row whose regex source contains this token.
            matched = next(
                (sfxs for src, sfxs in worker_pairs if tok in src),
                None,
            )
            if matched is None:
                errors.append(
                    f"BOT_RDNS_SUFFIXES missing family covering YAML "
                    f"token '{tok}' (suffixes: {sorted(yaml_suffixes)})"
                )
                continue
            missing = yaml_suffixes - matched
            if missing:
                errors.append(
                    f"BOT_RDNS_SUFFIXES family for '{tok}' missing "
                    f"suffixes: {sorted(missing)} "
                    f"(worker has {sorted(matched)})"
                )
    return errors


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
    errors.extend(_check_rdns_suffix_parity(rules))

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
