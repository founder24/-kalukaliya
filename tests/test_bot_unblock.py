"""Task #9 — End-to-end verification that the verified-bot fast path
is reachable.

The full Playwright story (browser-driven crawl with a faked Googlebot
UA against a live Cloudflare-fronted endpoint) requires
``BOT_UNBLOCK_TEST_URL`` to point at a deployed worker. Without it we
exercise the contract using ``httpx`` against the same URL — the
assertions here only depend on response *headers* the worker emits,
not on browser rendering, so the heavier Playwright dep is optional.
Set ``BOT_UNBLOCK_TEST_URL=https://syrabit.ai`` (or a preview deploy)
to enable the live checks; the offline checks (regex parity + robots
shape) always run.

Why this exists: the failure mode the task was opened to fix is
silent — Bot Fight Mode or a regex regression returns a 200 to the
test runner but a 429 / JS challenge to the real Googlebot. By
asserting against the *worker's response with a Googlebot UA* (not a
bare ``curl`` UA) we exercise the same code path Googlebot does.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import gen_bot_regex  # noqa: E402

LIVE_URL = os.environ.get("BOT_UNBLOCK_TEST_URL")

GOOGLEBOT_UA = (
    "Mozilla/5.0 (compatible; Googlebot/2.1; "
    "+http://www.google.com/bot.html)"
)
PERPLEXITY_UA = "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)"
GPTBOT_UA = "Mozilla/5.0 (compatible; GPTBot/1.0; +https://openai.com/gptbot)"


# ─── Offline contract checks (always run) ──────────────────────────────────────


def test_canonical_registry_loads():
    """If the YAML can't be parsed by the tolerant loader, no source
    will ever be in sync with it. Catch this early."""
    rules = gen_bot_regex._load_yaml()
    assert "verified_search" in rules
    assert "citation_ai" in rules
    assert "training_ai" in rules
    assert "abusive" in rules
    # Every entry has a token.
    for bucket, entries in rules.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            assert isinstance(entry, dict) and entry.get("token"), (
                f"{bucket}: malformed entry {entry!r}"
            )


def test_drift_checker_passes():
    """Spawn the CI guard and assert exit-0. This is the contract the
    canonical-delegation gate runs in CI."""
    import subprocess
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_bot_rules_drift.py")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"Bot drift checker failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_verified_bot_fast_path_constant_present():
    """Worker must define the high-RPM bucket constant + UA regex."""
    src = (REPO_ROOT / "workers" / "edge-proxy" / "src" / "index.ts").read_text(encoding="utf-8")
    assert "VERIFIED_BOT_RATE_LIMIT_RPM" in src, (
        "edge-proxy must define VERIFIED_BOT_RATE_LIMIT_RPM (Task #9 fast path)"
    )
    m = re.search(r"VERIFIED_BOT_RATE_LIMIT_RPM\s*=\s*(\d+)", src)
    assert m and int(m.group(1)) >= 60000, (
        f"VERIFIED_BOT_RATE_LIMIT_RPM must be ≥ 60000 (got {m.group(1) if m else 'unset'})"
    )
    assert "VERIFIED_BOT_UA" in src
    assert "verifyBotIpWithKv" in src


def test_robots_advertises_all_sitemap_shards():
    """All 9 sitemap shards + the master index must be listed in
    robots.txt so long-tail crawlers without a sitemap-index parser
    discover the full URL set."""
    body = (REPO_ROOT / "artifacts" / "syrabit" / "public" / "robots.txt").read_text(encoding="utf-8")
    expected = [
        "sitemap-index.xml",
        "sitemap-pages.xml",
        "sitemap-subjects.xml",
        "sitemap-chapters.xml",
        "sitemap-notes.xml",
        "sitemap-mcqs.xml",
        "sitemap-pyqs.xml",
        "sitemap-examples.xml",
        "sitemap-definitions.xml",
        "sitemap-learn.xml",
    ]
    for s in expected:
        assert f"Sitemap: https://syrabit.ai/{s}" in body, f"missing sitemap line: {s}"


def test_robots_advertises_crawl_delay_zero():
    """Verified search + citation-AI bots must see Crawl-delay: 0 so
    they pace at the edge bucket (60 000 RPM) rather than the
    polite-default ~1 RPS."""
    body = (REPO_ROOT / "artifacts" / "syrabit" / "public" / "robots.txt").read_text(encoding="utf-8")
    for ua in ("Googlebot", "Bingbot", "PerplexityBot", "OAI-SearchBot", "ChatGPT-User"):
        # block-by-block: find the User-agent line, then assert
        # Crawl-delay: 0 appears before the next blank line.
        block_re = re.compile(
            rf"User-agent:\s*{re.escape(ua)}\s*\n((?:.+\n)*?)\s*\n",
            re.IGNORECASE,
        )
        m = block_re.search(body + "\n\n")
        assert m, f"missing User-agent: {ua} block"
        assert "Crawl-delay: 0" in m.group(1), (
            f"{ua}: missing `Crawl-delay: 0` (Task #9)"
        )


def test_dashboard_runbook_documents_bot_fight_mode_off():
    """Bot Fight Mode is the #1 SEO regression risk. The runbook must
    document that it is DISABLED."""
    doc = (REPO_ROOT / "artifacts" / "syrabit" / "docs" / "infra" / "cloudflare-bot-config.md").read_text(encoding="utf-8")
    assert "Bot Fight Mode" in doc
    assert re.search(r"Bot Fight Mode.*DISABLED", doc, re.DOTALL | re.IGNORECASE)


# ─── Live checks (gated on BOT_UNBLOCK_TEST_URL) ───────────────────────────────


@pytest.mark.skipif(
    not LIVE_URL,
    reason="set BOT_UNBLOCK_TEST_URL to enable live verified-bot probe",
)
def test_live_googlebot_not_blocked():
    """A request from a Googlebot UA must NOT receive a 4xx that would
    drop it from the index. 429 is the one we're specifically testing
    for — the verified-bot fast path is supposed to prevent it for any
    sane crawl rate."""
    import httpx
    r = httpx.get(LIVE_URL, headers={"User-Agent": GOOGLEBOT_UA}, timeout=15.0, follow_redirects=True)
    assert r.status_code != 429, (
        f"Googlebot got 429 — verified-bot fast path regressed. "
        f"Body: {r.text[:200]}"
    )
    # 200 / 304 / 301 are all fine — anything else (403, 451) is a bug.
    assert r.status_code in (200, 301, 302, 304), f"unexpected {r.status_code}"


@pytest.mark.skipif(
    not LIVE_URL,
    reason="set BOT_UNBLOCK_TEST_URL to enable live citation-AI probe",
)
def test_live_perplexity_not_blocked():
    import httpx
    r = httpx.get(LIVE_URL, headers={"User-Agent": PERPLEXITY_UA}, timeout=15.0, follow_redirects=True)
    assert r.status_code != 403, "PerplexityBot must be allowed (cites sources)"
    assert r.status_code != 429


@pytest.mark.skipif(
    not LIVE_URL,
    reason="set BOT_UNBLOCK_TEST_URL to enable live training-AI probe",
)
def test_live_gptbot_blocked():
    """GPTBot is in the training_ai bucket: must be 403'd at the edge
    regardless of cf.verifiedBot status. This is the policy guarantee
    that protects content from silent corpus ingestion."""
    import httpx
    r = httpx.get(LIVE_URL, headers={"User-Agent": GPTBOT_UA}, timeout=15.0, follow_redirects=True)
    assert r.status_code == 403, (
        f"GPTBot was NOT blocked at the edge: status={r.status_code}"
    )
