"""Unit tests for slug_utils.clean_learn_slug (Task #3 SEO).

Run with:  pytest tests/test_slug_utils.py -v
"""
import pytest
from slug_utils import clean_learn_slug


@pytest.mark.parametrize("slug,expected,label", [
    (
        "bcom--2nd-sem---bcm--03-(2025)--(bcm0200304)",
        "bcom-2nd-sem",
        "full noisy BCom slug — parens + double-dash codes + orphaned prefix",
    ),
    (
        "financial-accounting-(fyugp)-2024---fa0200101",
        "financial-accounting",
        "FYUGP parenthesised token + year code + compact paper ID",
    ),
    (
        "mathematics--bcm--01-(2024)",
        "mathematics",
        "short subject + trailing abbreviated prefix + parenthesised year",
    ),
    (
        "bcom--2nd-sem",
        "bcom-2nd-sem",
        "double-dash separator only — no parentheses",
    ),
    (
        "physical-world",
        "physical-world",
        "already clean — unchanged",
    ),
    (
        "english-core",
        "english-core",
        "no noise of any kind — unchanged",
    ),
    (
        "bcom-2nd-sem",
        "bcom-2nd-sem",
        "'sem' contains vowel 'e' — NOT stripped by consonant filter",
    ),
    (
        "bsc-1st-year",
        "bsc-1st-year",
        "'bsc' has vowel? No — but 'year' and '1st' anchor it; bsc is leading",
    ),
    (
        "",
        "",
        "empty string — fallback returns original (empty)",
    ),
    (
        "bcom-2nd-sem-(2024)",
        "bcom-2nd-sem",
        "parenthesised year only — no double-dash codes",
    ),
    (
        "economics--fa--05-(2025)--(fa0200501)",
        "economics",
        "2-letter FA prefix + paper number + two parenthesised codes",
    ),
])
def test_clean_learn_slug(slug: str, expected: str, label: str) -> None:
    assert clean_learn_slug(slug) == expected, label


def test_clean_learn_slug_no_db_mutation() -> None:
    original = "bcom--2nd-sem-(2025)--(bcm0200304)"
    result = clean_learn_slug(original)
    assert original == "bcom--2nd-sem-(2025)--(bcm0200304)", \
        "clean_learn_slug must not mutate its input argument"
    assert result == "bcom-2nd-sem"


def test_clean_learn_slug_fallback_not_empty() -> None:
    result = clean_learn_slug("a")
    assert result == "a", "single-char slug must survive (fallback guard)"
