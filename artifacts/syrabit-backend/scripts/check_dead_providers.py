#!/usr/bin/env python3
"""Task #297 — CI guard against banned/dead provider tokens.

Fails the build if any of the following appear outside an allowlist:

  - ``cartesia``     (purged 2026-05-03)
  - ``os.environ.get('GEMINI_API_KEY')`` outside ``config.py``
    (Gemini is reached via the CF AI Gateway slug
    ``google-ai-studio/v1beta/openai``; direct env-var reads are banned
    to keep the BYOK lifecycle honest)

Intentionally NOT scanned:

  - ``perplexity`` — every hit refers to PerplexityBot, the AI
    search-engine crawler we want to serve content TO (robots.txt,
    GEO/JSONLD, bot-discovery dashboards). Not used as an LLM provider.
  - ``groq`` / ``openrouter`` / ``cerebras`` — still referenced by the
    BYOK secret-audit lifecycle in ``server.py`` and by historical
    comments / model registries. Removal is tracked by the Railway
    env-var audit table that already prints on every boot.

Allowlisted paths (banned tokens may legitimately appear here):
  - attached_assets/**   (raw user uploads / log snapshots)
  - .local/**            (agent scratch / session plans)
  - **/CHANGELOG*        (historical release notes)
  - tests/test_provider_dispatch.py  (asserts cartesia is ABSENT)
  - this script itself
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # repo root (script is at artifacts/syrabit-backend/scripts/)
BACKEND = ROOT / "artifacts" / "syrabit-backend"
FRONTEND = ROOT / "artifacts" / "syrabit"

# ``cartesia`` is fully purged from the active provider chain (Task #297).
# ``perplexity`` is intentionally NOT scanned: the only hits in the repo
# refer to PerplexityBot — the AI search-engine crawler we want to serve
# content TO (robots.txt, GEO/JSONLD, bot-discovery dashboards). It is not
# in use as an LLM provider anywhere.
BANNED_LITERAL = re.compile(r"\bcartesia\b", re.IGNORECASE)
DIRECT_GEMINI = re.compile(r"""os\.environ\.get\(\s*['"]GEMINI_API_KEY""")

ALLOWLIST_PARTS = {
    "attached_assets",
    ".local",
    "node_modules",
    "build",
    "dist",
}
ALLOWLIST_FILES = {
    "artifacts/syrabit-backend/scripts/check_dead_providers.py",
    "artifacts/syrabit-backend/tests/test_provider_dispatch.py",       # asserts absence
    "artifacts/syrabit-backend/tests/test_dead_providers_guard.py",    # the test wrapper
    "artifacts/syrabit-backend/server.py",                              # banned-token comment in BYOK audit
}
ALLOWLIST_NAME_PREFIXES = ("CHANGELOG",)


def _is_allowlisted(p: Path) -> bool:
    parts = set(p.parts)
    if parts & ALLOWLIST_PARTS:
        return True
    rel = p.relative_to(ROOT).as_posix()
    if rel in ALLOWLIST_FILES:
        return True
    if any(p.name.startswith(prefix) for prefix in ALLOWLIST_NAME_PREFIXES):
        return True
    return False


def _scan_file(p: Path) -> list[str]:
    failures: list[str] = []
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return failures
    is_code = p.suffix in (".py", ".js", ".jsx", ".ts", ".tsx")
    for ln, line in enumerate(text.splitlines(), 1):
        if BANNED_LITERAL.search(line):
            failures.append(f"{p.relative_to(ROOT)}:{ln}: banned token → {line.strip()[:120]}")
        if is_code and DIRECT_GEMINI.search(line) and p.name != "config.py":
            failures.append(f"{p.relative_to(ROOT)}:{ln}: direct GEMINI_API_KEY env read → {line.strip()[:120]}")
    return failures


def main() -> int:
    targets: list[Path] = []
    for base in (BACKEND, FRONTEND):
        if not base.exists():
            continue
        for ext in ("*.py", "*.jsx", "*.js", "*.ts", "*.tsx", "*.md"):
            targets.extend(base.rglob(ext))

    failures: list[str] = []
    for p in targets:
        if _is_allowlisted(p):
            continue
        failures.extend(_scan_file(p))

    if failures:
        print("Dead-provider guard FAILED:")
        for f in failures:
            print(f"  {f}")
        print(f"\n{len(failures)} violation(s). See artifacts/syrabit-backend/scripts/check_dead_providers.py for the allowlist.")
        return 1

    print(f"Dead-provider guard OK — scanned {len(targets)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
