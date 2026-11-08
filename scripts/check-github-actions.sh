#!/usr/bin/env bash
set -euo pipefail

ACTIONLINT_VERSION="1.7.12"
ACTIONLINT_CACHE_DIR="${ACTIONLINT_CACHE_DIR:-${RUNNER_TEMP:-/tmp}/actionlint-${ACTIONLINT_VERSION}}"
ACTIONLINT_BIN="${ACTIONLINT_CACHE_DIR}/actionlint"

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)
    archive="actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz"
    checksum="8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
    ;;
  Linux-aarch64|Linux-arm64)
    archive="actionlint_${ACTIONLINT_VERSION}_linux_arm64.tar.gz"
    checksum="325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6"
    ;;
  Darwin-x86_64)
    archive="actionlint_${ACTIONLINT_VERSION}_darwin_amd64.tar.gz"
    checksum="5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644"
    ;;
  Darwin-arm64)
    archive="actionlint_${ACTIONLINT_VERSION}_darwin_arm64.tar.gz"
    checksum="aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f"
    ;;
  *)
    echo "Unsupported platform for actionlint: $(uname -s) $(uname -m)" >&2
    exit 1
    ;;
esac

if [[ ! -x "${ACTIONLINT_BIN}" ]]; then
  mkdir -p "${ACTIONLINT_CACHE_DIR}"
  release_api_url="https://api.github.com/repos/rhysd/actionlint/releases/tags/v${ACTIONLINT_VERSION}"
  asset_api_url="$(
    curl --fail --location --silent --show-error \
      --retry 4 --retry-all-errors --retry-delay 3 --retry-max-time 120 \
      --header "User-Agent: syrabit-actionlint-checker" \
      "${release_api_url}" \
      | python3 -c '
import json, sys
archive = sys.argv[1]
release = json.load(sys.stdin)
asset = next((item for item in release.get("assets", []) if item.get("name") == archive), None)
if asset is None or not asset.get("url"):
    raise SystemExit(f"Release metadata missing asset: {archive}")
print(asset["url"])
' "${archive}"
  )"
  archive_path="${ACTIONLINT_CACHE_DIR}/${archive}"
  echo "Downloading actionlint v${ACTIONLINT_VERSION}..."
  curl --fail --location --silent --show-error \
    --retry 4 --retry-all-errors --retry-delay 3 --retry-max-time 120 \
    --header "User-Agent: syrabit-actionlint-checker" \
    --header "Accept: application/octet-stream" \
    --output "${archive_path}" "${asset_api_url}"
  if command -v sha256sum >/dev/null 2>&1; then
    echo "${checksum}  ${archive_path}" | sha256sum --check --status
  else
    echo "${checksum}  ${archive_path}" | shasum --algorithm 256 --check --status
  fi
  tar -xzf "${archive_path}" -C "${ACTIONLINT_CACHE_DIR}" actionlint
  rm "${archive_path}"
fi

# Keep results deterministic across local machines and GitHub runners.
# ShellCheck availability must not silently change this repository-wide
# workflow validator into a separate legacy shell-style gate.
"${ACTIONLINT_BIN}" -color -shellcheck=""