"""Syrabit.ai — shared slug normalisation helpers.

Canonical location for /learn/ slug cleaning.  Import from here; do not
define _clean_learn_slug inline in other modules.

Task #3 (SEO): strip paper-code noise from /learn/ slugs at emit time so
sitemaps, citation URLs, and source URLs produced by the AI pipeline use
clean human-readable paths without altering the underlying MongoDB documents.
"""
import re

# Step 1: parenthesised codes — (2025), (bcm0200304), (fyugp), etc.
_LEARN_SLUG_PARENS_RE = re.compile(r"\([^)]*\)")

# Step 2: double-hyphen-prefixed code tokens that survive step 1.
# Matches: --<up-to-6-lowercase-letters><2-or-more-digits>
# Handles: --03 (paper number), --2025 (year), ---fa0200101 (compact ID),
#          ---bcm0200304 (compact paper code).
# Does NOT match: --2nd (only 1 digit before letter suffix), --sem (no digits).
_LEARN_SLUG_CODE_RE = re.compile(r"--+[a-z]{0,6}\d{2,}")

# Step 3: collapse any remaining consecutive hyphens.
_LEARN_SLUG_DASH_RE = re.compile(r"-{2,}")

# Step 4: strip trailing segments that are code abbreviations orphaned by
# steps 1–3.  Two shapes are caught:
#   • Exactly 2 letters — paper-prefix abbreviations like "fa", "ba", "ma"
#     (also covers pure-consonant 2-char pairs like "bc", "bm").
#   • 3–5 pure-consonant letters (no a/e/i/o/u) — e.g. "bcm", "srm".
# Real English subject words almost always contain a vowel *and* are 4+
# chars ("sem", "core", "math"), so they are not matched.
# Character class [b-df-hj-np-tv-z] = all lowercase letters except a,e,i,o,u.
_LEARN_SLUG_TRAILING_CODE_RE = re.compile(
    r"(?:-(?:[a-z]{2}|[b-df-hj-np-tv-z]{3,5}))+$"
)


def clean_learn_slug(slug: str) -> str:
    """Return a clean, human-readable /learn/ slug.

    Strips paper-code noise from a raw ``seo_slug`` or ``slug`` value
    without modifying the database field itself.

    Processing order:
    1. Remove parenthesised codes: ``(2025)``, ``(bcm0200304)``, ``(fyugp)``
    2. Remove double-hyphen-prefixed code tokens: ``--03``, ``--2025``,
       ``---fa0200101``, ``---bcm0200304``
    3. Collapse consecutive hyphens to one and strip edge hyphens
    4. Drop trailing pure-consonant abbreviation segments (e.g. ``-bcm``,
       ``-fa``) that are orphaned after steps 1–3

    Falls back to the original ``slug`` if cleaning produces an empty string
    (defensive — should not happen with well-formed slugs).

    Examples::

        "bcom--2nd-sem---bcm--03-(2025)--(bcm0200304)"
          → "bcom-2nd-sem"
        "financial-accounting-(fyugp)-2024---fa0200101"
          → "financial-accounting"
        "economics--fa--05-(2025)--(fa0200501)"
          → "economics"
        "physical-world"   → "physical-world"   (unchanged)
        "bcom-2nd-sem"     → "bcom-2nd-sem"     (unchanged — "sem" is 3 chars
                                                  with vowel, not 2-char code)
    """
    if not slug:
        return slug
    cleaned = _LEARN_SLUG_PARENS_RE.sub("", slug)
    cleaned = _LEARN_SLUG_CODE_RE.sub("", cleaned)
    cleaned = _LEARN_SLUG_DASH_RE.sub("-", cleaned).strip("-")
    cleaned = _LEARN_SLUG_TRAILING_CODE_RE.sub("", cleaned)
    return cleaned or slug


# Back-compat alias used by middleware.py normaliser (same logic, same name).
_clean_learn_slug = clean_learn_slug
