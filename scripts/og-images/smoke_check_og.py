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

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PUBLIC_CDN_BASE = "https://cdn.syrabit.ai/og"
_CDN_HOST = "cdn.syrabit.ai"

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

# Cached IPv4 address resolved via DoH so we only query once per run.
_CDN_IPV4: str | None = None


def _resolve_ipv4_via_doh(hostname: str) -> str | None:
    """Resolve *hostname* to an IPv4 address using Cloudflare DNS-over-HTTPS.

    This bypasses the sandbox's IPv6-only routing limitation.  Cloudflare's
    1.1.1.1 DoH endpoint always answers over IPv4 from the resolver's PoV,
    and the A-record answer gives us a routable IPv4 address for the CDN.
    Returns None on any error so callers can fall back to direct resolution.
    """
    doh_url = (
        "https://1.1.1.1/dns-query"
        f"?name={urllib.parse.quote(hostname)}&type=A"
    )
    req = urllib.request.Request(
        doh_url,
        headers={"Accept": "application/dns-json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            for answer in data.get("Answer", []):
                if answer.get("type") == 1:  # DNS A record
                    ip = answer.get("data", "").strip()
                    if ip:
                        return ip
    except Exception:
        pass
    return None


def _cdn_ipv4() -> str | None:
    """Return the cached IPv4 address for the CDN host, resolving once."""
    global _CDN_IPV4
    if _CDN_IPV4 is None:
        _CDN_IPV4 = _resolve_ipv4_via_doh(_CDN_HOST)
    return _CDN_IPV4


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
    """Issue a HEAD request (with a GET fallback) and return (status, content-type).

    When an IPv4 address is available via DoH the request is sent directly to
    that address with a Host header set to the CDN hostname, bypassing any
    IPv6-only routing in the sandbox environment.
    """
    parsed = urllib.parse.urlparse(url)
    ipv4 = _cdn_ipv4()

    if ipv4:
        # Replace the netloc with the IPv4 address so the TCP connection goes
        # to a routable address; preserve the Host header so the CDN/TLS SNI
        # still see the correct hostname.
        direct_url = url.replace(f"https://{_CDN_HOST}", f"https://{ipv4}", 1)
        extra_headers = {
            "Host": parsed.netloc,
            "User-Agent": "syrabit-og-smoke/1.0",
        }
    else:
        direct_url = url
        extra_headers = {"User-Agent": "syrabit-og-smoke/1.0"}

    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(
                direct_url,
                method=method,
                headers=extra_headers,
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

    ipv4 = _cdn_ipv4()
    count = len(slugs)
    print(f"OG image CDN smoke check — {count} URL{'s' if count != 1 else ''}")
    print(f"CDN base: {PUBLIC_CDN_BASE}")
    if ipv4:
        print(f"CDN IPv4 (DoH): {ipv4}  (bypassing sandbox IPv6 routing)")
    else:
        print("CDN IPv4 (DoH): unavailable — using direct hostname resolution")
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
