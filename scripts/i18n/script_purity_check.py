#!/usr/bin/env python3
"""
Script-purity check — Task #362 §2.2 Signal 2.

Computes the fraction of an input string that is in the *intended*
output script, using Unicode-block ratios. Offline, no network.

Intended scripts:
  - "as" / "bn" — Bengali / Assamese script (U+0980-U+09FF)
  - "en"        — Latin ASCII letters (U+0041-U+005A, U+0061-U+007A)

Whitespace, digits, and punctuation are excluded from the
denominator (they are script-neutral). Anything that is a *letter*
in the wrong script counts toward the failure ratio.

Exit codes:
  0 — purity ratio >= threshold (default 0.95)
  1 — purity ratio <  threshold
  2 — input invalid / no scriptable characters
  3 — usage error

Usage:
  echo "answer text" | script_purity_check.py --intended as
  script_purity_check.py --intended en --threshold 0.95 --text "..."
"""

import argparse
import sys
import unicodedata


def _is_bengali_letter(ch: str) -> bool:
    cp = ord(ch)
    return 0x0980 <= cp <= 0x09FF and unicodedata.category(ch).startswith("L")


def _is_latin_letter(ch: str) -> bool:
    cp = ord(ch)
    if 0x0041 <= cp <= 0x005A or 0x0061 <= cp <= 0x007A:
        return True
    return 0x00C0 <= cp <= 0x024F and unicodedata.category(ch).startswith("L")


def _is_other_script_letter(ch: str, intended: str) -> bool:
    if not unicodedata.category(ch).startswith("L"):
        return False
    if intended == "as":
        return not _is_bengali_letter(ch)
    if intended == "en":
        return not _is_latin_letter(ch)
    return False


def compute_purity(text: str, intended: str) -> tuple[float, int, int]:
    """Returns (purity_ratio, intended_count, total_letter_count).

    purity = intended_count / total_letter_count when total > 0.
    Returns (0.0, 0, 0) when total == 0 (no scriptable letters).
    """
    intended_count = 0
    total = 0
    for ch in text:
        if not unicodedata.category(ch).startswith("L"):
            continue
        total += 1
        if intended == "as" and _is_bengali_letter(ch):
            intended_count += 1
        elif intended == "en" and _is_latin_letter(ch):
            intended_count += 1
    if total == 0:
        return (0.0, 0, 0)
    return (intended_count / total, intended_count, total)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--intended", required=True, choices=["as", "en"],
                   help="Intended output script.")
    p.add_argument("--threshold", type=float, default=0.95,
                   help="Minimum purity ratio. Default 0.95.")
    p.add_argument("--text", default=None,
                   help="Inline text. If omitted, reads stdin.")
    p.add_argument("--quiet", action="store_true",
                   help="Print only the ratio, no labels.")
    args = p.parse_args()

    text = args.text if args.text is not None else sys.stdin.read()
    if not text.strip():
        print("ERROR: empty input", file=sys.stderr)
        return 3

    ratio, intended, total = compute_purity(text, args.intended)

    if total == 0:
        print(f"INVALID: no scriptable letters found in input "
              f"(intended={args.intended})", file=sys.stderr)
        return 2

    if args.quiet:
        print(f"{ratio:.4f}")
    else:
        print(f"intended={args.intended} threshold={args.threshold:.2f}")
        print(f"intended_letters={intended} total_letters={total}")
        print(f"purity_ratio={ratio:.4f}")

    if ratio < args.threshold:
        if not args.quiet:
            print(f"FAIL: {ratio:.4f} < {args.threshold:.2f}", file=sys.stderr)
        return 1
    if not args.quiet:
        print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
