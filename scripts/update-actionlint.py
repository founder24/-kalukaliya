#!/usr/bin/env python3
"""Check or atomically update the pinned actionlint release and checksums."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPOSITORY = "rhysd/actionlint"
CHECKER = Path(__file__).with_name("check-github-actions.sh")
SUPPORTED_ARCHIVES = (
    "linux_amd64",
    "linux_arm64",
    "darwin_amd64",
    "darwin_arm64",
)
USER_AGENT = "syrabit-actionlint-updater"
RETRYABLE_HTTP_STATUS = {500, 502, 503, 504}
MAX_DOWNLOAD_ATTEMPTS = 4


def download_text(url: str) -> str:
    for attempt in range(MAX_DOWNLOAD_ATTEMPTS):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            if error.code not in RETRYABLE_HTTP_STATUS or attempt == MAX_DOWNLOAD_ATTEMPTS - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == MAX_DOWNLOAD_ATTEMPTS - 1:
                raise
        time.sleep(min(2**attempt, 8))

    raise RuntimeError("Download retries exhausted")


def latest_release() -> tuple[str, dict[str, str]]:
    release = json.loads(
        download_text(f"https://api.github.com/repos/{REPOSITORY}/releases/latest")
    )
    tag = release["tag_name"]
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError(f"Unexpected actionlint release tag: {tag!r}")

    version = tag.removeprefix("v")
    asset_digests = {
        asset["name"]: asset["digest"]
        for asset in release.get("assets", [])
        if isinstance(asset.get("name"), str) and isinstance(asset.get("digest"), str)
    }
    return version, asset_digests


def release_checksums(version: str, asset_digests: dict[str, str]) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for platform in SUPPORTED_ARCHIVES:
        archive = f"actionlint_{version}_{platform}.tar.gz"
        digest = asset_digests.get(archive, "")
        match = re.fullmatch(r"sha256:([0-9a-f]{64})", digest)
        if not match:
            raise ValueError(f"Release metadata does not contain a SHA-256 digest for {archive}")
        checksums[platform] = match.group(1)
    return checksums


def pinned_version(checker: str) -> str:
    match = re.search(r'^ACTIONLINT_VERSION="(\d+\.\d+\.\d+)"$', checker, re.MULTILINE)
    if not match:
        raise ValueError("Could not find exactly formatted ACTIONLINT_VERSION pin")
    return match.group(1)


def updated_checker(checker: str, version: str, checksums: dict[str, str]) -> str:
    current_version = pinned_version(checker)
    updated = checker.replace(
        f'ACTIONLINT_VERSION="{current_version}"',
        f'ACTIONLINT_VERSION="{version}"',
        1,
    )

    replacements = 0
    for platform in SUPPORTED_ARCHIVES:
        archive_line = (
            rf'(archive="actionlint_\$\{{ACTIONLINT_VERSION\}}_{re.escape(platform)}'
            rf'\.tar\.gz"\n\s+checksum=")[0-9a-f]{{64}}(")'
        )
        updated, count = re.subn(
            archive_line,
            lambda match: f"{match.group(1)}{checksums[platform]}{match.group(2)}",
            updated,
        )
        replacements += count

    if replacements != len(SUPPORTED_ARCHIVES):
        raise ValueError(
            f"Expected to update {len(SUPPORTED_ARCHIVES)} checksums, updated {replacements}"
        )
    return updated


def atomic_write(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    try:
        os.chmod(temporary_path, path.stat().st_mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--update",
        action="store_true",
        help="atomically update the checker to the latest release",
    )
    args = parser.parse_args()

    checker = CHECKER.read_text()
    current = pinned_version(checker)
    latest, asset_digests = latest_release()
    checksums = release_checksums(latest, asset_digests)
    candidate = updated_checker(checker, latest, checksums)

    if args.update:
        if candidate == checker:
            print(f"actionlint v{current} is already current")
        else:
            atomic_write(CHECKER, candidate)
            print(
                f"Updated actionlint v{current} to v{latest} with "
                f"{len(checksums)} verified platform checksums"
            )
        return 0

    if candidate != checker:
        print(
            f"actionlint v{current} is outdated; v{latest} is available.\n"
            "Run: python3 scripts/update-actionlint.py --update",
            file=sys.stderr,
        )
        return 1

    print(f"actionlint v{current} and all {len(checksums)} checksums are current")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as error:
        print(f"Could not check actionlint release: {error}", file=sys.stderr)
        raise SystemExit(2)