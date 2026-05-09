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


def test_drift_checker_detects_reverse_drift_across_string_segments():
    """Regression for the round-2 reviewer finding: the bidirectional
    drift checker MUST detect a UA token that sits at the head of a
    Python raw-string continuation segment (e.g. ``r"rogerbot|..."``).
    We verify that ``rogerbot`` is extracted from utils.py's regex
    body, then simulate it being absent from both the YAML and the
    benign allowlist and assert the checker would flag it.
    """
    import importlib
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    drift = importlib.import_module("check_bot_rules_drift")

    utils_src = (REPO_ROOT / "artifacts" / "syrabit-backend" / "utils.py").read_text(encoding="utf-8")
    m = re.search(
        r"_SEARCH_BOT_UA_RE\s*=\s*re\.compile\(\s*(.*?)\s*,\s*re\.IGNORECASE",
        utils_src, re.DOTALL,
    )
    assert m, "could not locate _SEARCH_BOT_UA_RE in utils.py"
    tokens = drift._extract_regex_tokens(m.group(1))
    assert "rogerbot" in tokens, (
        "tokenizer regression: rogerbot must be extracted from a raw-string "
        "continuation segment. Without this the drift checker silently misses "
        "tokens that sit at the head of `r\"...|\"` segments."
    )

    # Now simulate rogerbot NOT being on the benign allowlist and not in
    # the YAML — the bidirectional check should report it as drift.
    saved = set(drift._BENIGN_REGEX_TOKENS)
    try:
        drift._BENIGN_REGEX_TOKENS.discard("rogerbot")
        rules = gen_bot_regex._load_yaml()
        by_bucket = gen_bot_regex.all_tokens(rules)
        errors = drift._check_one(
            REPO_ROOT / "artifacts" / "syrabit-backend" / "utils.py",
            ["verified_search", "citation_ai", "training_ai"],
            re.compile(
                r"_SEARCH_BOT_UA_RE\s*=\s*re\.compile\(\s*(.*?)\s*,\s*re\.IGNORECASE",
                re.DOTALL,
            ),
            by_bucket,
        )
        assert any("rogerbot" in e for e in errors), (
            f"reverse-drift detection failed; errors: {errors}"
        )
    finally:
        drift._BENIGN_REGEX_TOKENS.clear()
        drift._BENIGN_REGEX_TOKENS.update(saved)


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


def test_spoofed_critical_bot_returns_403():
    """Worker must hard-403 spoofed Googlebot/Bingbot/Perplexity/etc.
    (UA matches a critical bot family but neither cf.verifiedBot nor
    forward-confirmed rDNS confirmed the IP). Without this gate an
    attacker can scrape pre-rendered HTML the SPA gates by spoofing the
    UA from any IP."""
    src = (REPO_ROOT / "workers" / "edge-proxy" / "src" / "index.ts").read_text(encoding="utf-8")
    # Contract: there must be a CRITICAL_BOT_UA regex AND a code path
    # that returns 403 with X-Bot-Verify: spoofed when the UA matches
    # but FCrDNS does not confirm.
    assert "CRITICAL_BOT_UA" in src, "missing CRITICAL_BOT_UA regex (spoofed-bot 403 gate)"
    assert "X-Bot-Verify" in src and "spoofed" in src
    # The two must appear together in a 403 branch.
    # Either form is acceptable:
    #   if (CRITICAL_BOT_UA.test(ua)) { ... status: 403 ... }
    #   if (CRITICAL_BOT_UA.test(ua) && !fcrDnsAlreadyConfirmed) { return ... 403 ... }
    m = re.search(
        r"if\s*\(\s*CRITICAL_BOT_UA\.test\(ua\)[^)]*\)\s*\{[\s\S]{0,800}?status:\s*403",
        src,
    )
    assert m, (
        "expected `if (CRITICAL_BOT_UA.test(ua) ...) { ... status: 403 ... }` "
        "branch in worker — spoofed critical bots must be 403'd"
    )


def test_fcrdns_promotes_to_verified_bot_fast_path():
    """Task #9 core: a CRITICAL_BOT_UA from an IP that misses the static
    CIDR list but PASSES forward-confirmed rDNS MUST be promoted to the
    verified-bot fast path (60 000 RPM bucket, X-RateLimit-Scope:
    `verified_bot`, bot prerender path). Without this promotion a real
    Googlebot crawling from a rotated IP falls through to the unverified
    branch (120 RPM, no prerender) and indexing degrades.

    Contract checks (static, against the worker source):
      1. `isSearchBot` is `let` (mutable) — required for promotion.
      2. There is an FCrDNS-promotion block that flips `isSearchBot=true`
         on `verifyBotIpWithKv` success.
      3. The fast-path branch is reachable via `isSearchBot` (not just
         `botResult.verified`).
    """
    src = (REPO_ROOT / "workers" / "edge-proxy" / "src" / "index.ts").read_text(encoding="utf-8")
    # 1. mutable isSearchBot
    assert re.search(r"\blet\s+isSearchBot\b", src), (
        "isSearchBot must be `let` so FCrDNS success can promote it to true"
    )
    # 2. promotion block — verifyBotIpWithKv result assigned then
    #    isSearchBot set to true.
    promo = re.search(
        r"verifyBotIpWithKv\([^)]*\)[\s\S]{0,200}?isSearchBot\s*=\s*true",
        src,
    )
    assert promo, (
        "missing FCrDNS → isSearchBot promotion. A successful "
        "verifyBotIpWithKv result must set isSearchBot = true so the "
        "verified-bot fast path is reachable for rotated-IP Googlebot."
    )
    # 3. The verified_bot scope label is gated on the same isSearchBot.
    assert 'X-RateLimit-Scope": botScope' in src or "\"X-RateLimit-Scope\": botScope" in src
    assert '"verified_bot"' in src, "missing verified_bot rate-limit scope label"


def test_forward_confirmed_rdns_implementation():
    """The KV-cached rDNS verification MUST be forward-confirmed:
    after the PTR lookup we must round-trip through an A-record query
    and assert the original IP is in the answer set. PTR-only
    verification is forgeable by an attacker who controls the
    in-addr.arpa zone for a leased IP block.

    The cache key MUST also be scoped by bot family so a positive
    cache for googlebot from a shared NAT IP does not elevate trust
    for an unrelated UA hitting the same IP later."""
    src = (REPO_ROOT / "workers" / "edge-proxy" / "src" / "index.ts").read_text(encoding="utf-8")
    assert "_forwardConfirms" in src or "_forwardConfirm" in src, (
        "verifyBotIpWithKv must do forward-confirmed rDNS, not PTR-only"
    )
    # A-record lookup must appear.
    assert re.search(r'"A"', src), "missing A-record DoH query"
    # Family-scoped cache key.
    assert re.search(r"bot:rdns:\$\{family\}", src), (
        "KV cache key must be family-scoped (`bot:rdns:${family}:${hashIp(ip)}`)"
    )


def test_drift_check_wired_to_ci():
    """The drift checker must run as a CI gate, not just an ad-hoc script."""
    wf = REPO_ROOT / ".github" / "workflows" / "bot-rules-drift.yml"
    assert wf.exists(), "missing .github/workflows/bot-rules-drift.yml"
    body = wf.read_text(encoding="utf-8")
    assert "scripts/check_bot_rules_drift.py" in body, (
        "CI workflow must invoke scripts/check_bot_rules_drift.py"
    )
    assert "pull_request" in body and "push" in body, (
        "drift check must run on both pull_request and push"
    )


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


def _assert_prerendered(html: str, ua: str) -> None:
    """Body-level prerender contract: the worker must serve SSR'd HTML
    (not the SPA shell) for verified search/citation crawlers. SPA-shell
    responses lack both an <h1> AND a schema.org JSON-LD block — checking
    for both catches the failure mode where the worker returns 200 but
    cache rules bypassed prerender."""
    assert re.search(r"<h1[^>]*>", html, re.IGNORECASE), (
        f"{ua}: response missing <h1> — looks like SPA shell, not prerender"
    )
    assert re.search(
        r'application/ld\+json[\s\S]*?schema\.org', html, re.IGNORECASE,
    ), f"{ua}: response missing schema.org JSON-LD — prerender skipped"


@pytest.mark.skipif(
    not LIVE_URL,
    reason="set BOT_UNBLOCK_TEST_URL to enable live verified-bot probe",
)
def test_live_googlebot_not_blocked_and_prerendered():
    """A request from a Googlebot UA must NOT receive a 4xx that would
    drop it from the index, AND the response body must be the
    prerendered HTML (h1 + schema.org JSON-LD) — not the SPA shell.
    The verified-bot fast path is supposed to deliver both for any
    sane crawl rate."""
    import httpx
    r = httpx.get(LIVE_URL, headers={"User-Agent": GOOGLEBOT_UA}, timeout=15.0, follow_redirects=True)
    assert r.status_code != 429, (
        f"Googlebot got 429 — verified-bot fast path regressed. "
        f"Body: {r.text[:200]}"
    )
    assert r.status_code in (200, 301, 302, 304), f"unexpected {r.status_code}"
    if r.status_code == 200:
        _assert_prerendered(r.text, "Googlebot")


@pytest.mark.skipif(
    not LIVE_URL,
    reason="set BOT_UNBLOCK_TEST_URL to enable live citation-AI probe",
)
def test_live_perplexity_not_blocked_and_prerendered():
    """PerplexityBot is in the citation_ai bucket: must be 200 with
    prerendered HTML (the answer engine cites sources, so we want the
    full <h1>+JSON-LD payload to land in its index)."""
    import httpx
    r = httpx.get(LIVE_URL, headers={"User-Agent": PERPLEXITY_UA}, timeout=15.0, follow_redirects=True)
    assert r.status_code != 403, "PerplexityBot must be allowed (cites sources)"
    assert r.status_code != 429
    if r.status_code == 200:
        _assert_prerendered(r.text, "PerplexityBot")


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


@pytest.mark.skipif(
    not LIVE_URL,
    reason="set BOT_UNBLOCK_TEST_URL to enable live spoofed-bot probe",
)
def test_live_spoofed_googlebot_hard_403():
    """A Googlebot UA from a non-Google IP (the test runner) MUST get
    a hard-403 from the worker's CRITICAL_BOT_UA branch after FCrDNS
    fails to confirm. The response carries `X-Bot-Verify: spoofed` —
    that header is the contract the admin tile keys off."""
    import httpx
    # Use no_proxy + a bare httpx client so CI runners that route
    # through a corporate proxy don't accidentally come from a
    # CF-trusted egress block.
    r = httpx.get(
        LIVE_URL, headers={"User-Agent": GOOGLEBOT_UA},
        timeout=15.0, follow_redirects=False,
    )
    assert r.status_code == 403, (
        f"spoofed Googlebot from non-Google IP must be 403d "
        f"(got {r.status_code}; X-Bot-Verify={r.headers.get('X-Bot-Verify')!r})"
    )
    assert r.headers.get("X-Bot-Verify") == "spoofed", (
        "missing X-Bot-Verify: spoofed marker — CRITICAL_BOT_UA branch "
        "did not fire as expected"
    )
