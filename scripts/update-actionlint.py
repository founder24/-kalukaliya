#!/usr/bin/env python3
"""Check or atomically update the pinned actionlint release and checksums."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
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


def download_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def latest_release() -> tuple[str, str]:
    release = json.loads(
        download_text(f"https://api.github.com/repos/{REPOSITORY}/releases/latest")
    )
    tag = release["tag_name"]
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError(f"Unexpected actionlint release tag: {tag!r}")

    version = tag.removeprefix("v")
    expected_asset = f"actionlint_{version}_checksums.txt"
    for asset in release["assets"]:
        if asset["name"] == expected_asset:
            return version, asset["browser_download_url"]
    raise ValueError(f"Release {tag} does not contain {expected_asset}")


def release_checksums(version: str, checksum_url: str) -> dict[str, str]:
    available: dict[str, str] = {}
    for line in download_text(checksum_url).splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match:
            available[match.group(2)] = match.group(1)

    checksums: dict[str, str] = {}
    for platform in SUPPORTED_ARCHIVES:
        archive = f"actionlint_{version}_{platform}.tar.gz"
        try:
            checksums[platform] = available[archive]
        except KeyError as error:
            raise ValueError(f"Release checksums do not contain {archive}") from error
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
    latest, checksum_url = latest_release()
    checksums = release_checksums(latest, checksum_url)
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