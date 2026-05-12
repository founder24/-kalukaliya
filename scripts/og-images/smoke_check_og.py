#!/usr/bin/env python3
"""
Task #49 — Standalone OG image CDN smoke check.

Verifies that a sample of OG banner images are publicly reachable on the
production CDN (https://cdn.syrabit.ai/og) and return a valid image/png
response.  This script is intentionally dependency-free (stdlib only) so it
can run in any environment without installing boto3 or other R2 tooling.

Usage:
    python scripts/og-images/smoke_check_og.py [--sample N]

Flags:
    --sample N   Check only N slugs chosen deterministically across the full
                 alphabet (default: 5; 0 = check all).

Environment:
    OG_SMOKE_SKIP=1   Skip the check entirely (exit 0).  Set this in forks or
                      PR-preview environments where the CDN is intentionally
                      not populated.

Exit codes:
    0   All checked URLs returned HTTP 200 image/*   (or check was skipped)
    1   One or more URLs failed or returned a non-image content-type
"""
from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

PUBLIC_CDN_BASE = "https://cdn.syrabit.ai/og"

# Deterministic fallback list used when scripts/og-images/generated/ is absent
# (e.g. a fresh CI checkout that has not yet run generate_og_images.py).
# These slugs were live and verified at the Task #49 implementation date.
# Update this list if slugs are retired or renamed.
_FALLBACK_SLUGS = [
    "accountancy",
    "biology",
    "chemistry",
    "economics",
    "english",
    "mathematics",
    "physics",
    "political-science",
]

_GENERATED_DIR = Path(__file__).parent / "generated"


def _resolve_slugs(sample: int) -> list[str]:
    """Return the list of slugs to check, honouring --sample.

    Reads from the generated/ directory when available so the check covers
    the actual uploaded set.  Falls back to _FALLBACK_SLUGS for environments
    that have not run generate_og_images.py (e.g. a CI checkout without the
    generation step).
    """
    if _GENERATED_DIR.is_dir():
        all_slugs = sorted(p.stem for p in _GENERATED_DIR.glob("*.png"))
    else:
        all_slugs = list(_FALLBACK_SLUGS)

    if not all_slugs:
        return []

    if sample == 0:
        return all_slugs

    if sample >= len(all_slugs):
        return all_slugs

    # Deterministic evenly-spaced sample so the check is representative
    # across the full slug alphabet regardless of the total count.
    step = len(all_slugs) / sample
    return [all_slugs[round(i * step)] for i in range(sample)]


def _check_url(url: str) -> tuple[int, str]:
    """Issue a HEAD request (with a GET fallback) and return (status, content-type)."""
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(
                url,
                method=method,
                headers={"User-Agent": "syrabit-og-smoke/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                ct = resp.headers.get("Content-Type", "")
                return resp.status, ct
        except urllib.error.HTTPError as exc:
            # 405 / 501 → server doesn't support HEAD; fall through to GET.
            if exc.code not in (405, 501):
                return exc.code, exc.headers.get("Content-Type", "")
        except Exception:
            pass
    return 0, ""


def main() -> int:
    # Honour the skip flag first so forks / PR previews never fail.
    if os.environ.get("OG_SMOKE_SKIP", "").strip().lower() in ("1", "true", "yes"):
        print("OG_SMOKE_SKIP is set — skipping OG image CDN smoke check.")
        return 0

    # Parse --sample N
    sample = 5
    args = sys.argv[1:]
    if "--sample" in args:
        idx = args.index("--sample")
        try:
            sample = int(args[idx + 1])
        except (IndexError, ValueError):
            print("--sample requires an integer argument", file=sys.stderr)
            return 1

    slugs = _resolve_slugs(sample)
    if not slugs:
        print("No OG image slugs found — skipping smoke check.")
        return 0

    count = len(slugs)
    print(f"OG image CDN smoke check — {count} URL{'s' if count != 1 else ''}")
    print(f"CDN base: {PUBLIC_CDN_BASE}")
    print()

    failures: list[str] = []
    for slug in slugs:
        url = f"{PUBLIC_CDN_BASE}/{slug}.png"
        status, ct = _check_url(url)
        if status in (200, 206) and "image" in ct:
            print(f"  \033[32m✓\033[0m  {url}  →  {status}  {ct}")
        else:
            label = f"{status} {ct}".strip() if status else "unreachable"
            print(f"  \033[31m✗\033[0m  {url}  →  {label}")
            failures.append(url)

    print()
    if failures:
        print(f"\033[31m{len(failures)} URL(s) failed smoke check:\033[0m")
        for u in failures:
            print(f"  {u}")
        print()
        print("Possible causes:")
        print("  • R2 bucket public access has been revoked")
        print("  • CDN / cache-purge left a stale 403/404 in edge PoPs")
        print("  • OG images were not uploaded (run upload_to_r2.py)")
        return 1

    print(f"\033[32mAll {count} URL(s) returned 200 image/*  ✓\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
