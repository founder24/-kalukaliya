#!/usr/bin/env python3
"""
Task #17 — Upload generated OG banner PNGs to Cloudflare R2.

Prerequisites:
  1. R2 credentials set (via Replit Secrets or exported env vars):
       R2_ACCESS_KEY_ID      — CF Dashboard → R2 → Manage R2 API Tokens
       R2_SECRET_ACCESS_KEY  — paired secret key
       CF_AI_GATEWAY_ACCOUNT_ID  — CF account ID (auto-builds endpoint URL)
                 OR
       R2_ENDPOINT_URL       — explicit endpoint, e.g.
                               https://<account>.r2.cloudflarestorage.com
       R2_BUCKET_NAME        — bucket name (default: syrabit-media)

  2. Images generated:
       python scripts/og-images/generate_og_images.py

  3. boto3 installed:
       pip install boto3

Run:
    python scripts/og-images/upload_to_r2.py [--dry-run] [--smoke-check]

Flags:
    --dry-run      Print what would be uploaded, but don't upload.
    --smoke-check  After uploading, curl-check each public URL and
                   report any that don't return HTTP 200.

Public CDN base (must match _OG_IMAGE_BASE in workers/edge-proxy/src/index.ts):
    https://cdn.syrabit.ai/og
"""
from __future__ import annotations

import os
import sys
import time
import urllib.request
from pathlib import Path

GENERATED_DIR = Path(__file__).parent / "generated"
R2_PREFIX = "og"                             # bucket prefix — files land at  og/<slug>.png
PUBLIC_CDN_BASE = "https://cdn.syrabit.ai/og"
CACHE_CONTROL = "public, max-age=2592000"    # 30 days — these images change very rarely
CONTENT_TYPE = "image/png"


def _build_client():
    """Build a boto3 S3 client pointed at R2. Raises if env vars are missing."""
    key    = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    acct   = os.environ.get("CF_AI_GATEWAY_ACCOUNT_ID", "").strip()
    endpoint = os.environ.get("R2_ENDPOINT_URL", "").strip() or (
        f"https://{acct}.r2.cloudflarestorage.com" if acct else ""
    )
    if not (key and secret and endpoint):
        raise RuntimeError(
            "\nMissing R2 credentials.  Set these environment variables:\n"
            "  R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,\n"
            "  CF_AI_GATEWAY_ACCOUNT_ID  (or R2_ENDPOINT_URL)\n"
        )
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        region_name="auto",
        config=Config(retries={"max_attempts": 3, "mode": "adaptive"}),
    )


def upload(dry_run: bool = False) -> list[str]:
    pngs = sorted(GENERATED_DIR.glob("*.png"))
    if not pngs:
        print(f"No PNG files found in {GENERATED_DIR}. Run generate_og_images.py first.")
        sys.exit(1)

    bucket = os.environ.get("R2_BUCKET_NAME", "syrabit-media").strip()
    client = None if dry_run else _build_client()

    uploaded: list[str] = []
    for png in pngs:
        key = f"{R2_PREFIX}/{png.name}"
        if dry_run:
            print(f"  [dry-run] would upload  {key}  ({png.stat().st_size:,} bytes)")
            uploaded.append(png.stem)
            continue
        data = png.read_bytes()
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=CONTENT_TYPE,
            CacheControl=CACHE_CONTROL,
        )
        print(f"  ✓  {key}  ({len(data):,} bytes)")
        uploaded.append(png.stem)

    return uploaded


def smoke_check(slugs: list[str]) -> None:
    print("\nSmoke-checking public URLs …")
    failures: list[str] = []
    for slug in slugs:
        url = f"{PUBLIC_CDN_BASE}/{slug}.png"
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=10) as resp:
                ct = resp.getheader("Content-Type", "")
                if resp.status == 200 and "image" in ct:
                    print(f"  ✓  {url}  →  {resp.status} {ct}")
                else:
                    print(f"  ✗  {url}  →  {resp.status} {ct}")
                    failures.append(url)
        except Exception as exc:
            print(f"  ✗  {url}  →  {exc}")
            failures.append(url)

    if failures:
        print(f"\n{len(failures)} URL(s) failed smoke check:")
        for u in failures:
            print(f"  {u}")
        sys.exit(1)
    else:
        print(f"\nAll {len(slugs)} URLs returned 200 image/png  ✓")


def main() -> None:
    dry_run     = "--dry-run"     in sys.argv
    do_smoke    = "--smoke-check" in sys.argv

    mode = "[DRY RUN] " if dry_run else ""
    print(f"{mode}Uploading OG banner PNGs to R2 …")
    slugs = upload(dry_run=dry_run)
    print(f"\n{mode}{len(slugs)} file(s) processed.")

    if do_smoke and not dry_run:
        # Brief wait for CDN propagation
        print("Waiting 5 s for CDN propagation …")
        time.sleep(5)
        smoke_check(slugs)


if __name__ == "__main__":
    main()
