#!/usr/bin/env bash

# Validate the native runtime for Playwright's Chromium before a release
# browser check starts. GitHub-hosted Ubuntu runners get the libraries from
# `playwright install-deps`. Nix-based runners cannot use that apt-based
# installer, so the executable probe below reports the exact missing library
# before the release browser gate starts.

set -euo pipefail

mapfile -t browser_paths < <(
  node - <<'NODE'
const path = require("path");
const { chromium } = require("./apps/frontend/node_modules/playwright");
const executablePath = chromium.executablePath();
const browserRoot = path.dirname(path.dirname(executablePath));
const cacheRoot = path.dirname(browserRoot);
const revision = path.basename(browserRoot).replace(/^chromium-/, "");
const headlessShellPath = path.join(
  cacheRoot,
  `chromium_headless_shell-${revision}`,
  "chrome-headless-shell-linux64",
  "chrome-headless-shell",
);
process.stdout.write(`${executablePath}\n${headlessShellPath}\n`);
NODE
)

if [[ "${#browser_paths[@]}" -ne 2 ]]; then
  echo "::error::Could not resolve both Playwright Chromium executables."
  exit 1
fi

for browser_path in "${browser_paths[@]}"; do
  if [[ ! -x "$browser_path" ]]; then
    echo "::error::Playwright Chromium executable is missing or not executable: $browser_path"
    echo "::error::Install the pinned browser before running the release browser gate."
    exit 1
  fi
done

current_library_path="${LD_LIBRARY_PATH:-}"
missing_libraries=""
ldd_output=""

for browser_path in "${browser_paths[@]}"; do
  if [[ -n "$current_library_path" ]]; then
    browser_ldd="$(LD_LIBRARY_PATH="$current_library_path" ldd "$browser_path" 2>&1 || true)"
  else
    browser_ldd="$(ldd "$browser_path" 2>&1 || true)"
  fi
  ldd_output="${ldd_output}"$'\n'
  ldd_output="${ldd_output}[${browser_path}]"$'\n'
  ldd_output="${ldd_output}${browser_ldd}"
  missing_libraries="${missing_libraries}"$'\n'"$(
    printf '%s\n' "$browser_ldd" |
      awk '$NF == "not found" { print $1 }'
  )"
done
missing_libraries="$(
  printf '%s\n' "$missing_libraries" |
    sed '/^$/d' |
    sort -u
)"

if [[ -n "$missing_libraries" ]]; then
  echo "::error::Chromium runtime preflight found unresolved shared libraries:"
  while IFS= read -r library; do
    [[ -n "$library" ]] && echo "::error::  $library"
  done <<< "$missing_libraries"
  printf '%s\n' "::error::Chromium executables:"
  printf '::error::  %s\n' "${browser_paths[@]}"
  echo "::error::Loader output:"
  printf '%s\n' "$ldd_output"
  exit 1
fi

# ldd can resolve a library through a loader-specific mechanism that the
# actual Chromium process cannot use. Probe the same headless shell Playwright
# launches so that this preflight catches that case before hydration begins.
headless_shell_path="${browser_paths[1]}"
if [[ -n "$current_library_path" ]]; then
  probe_output="$(LD_LIBRARY_PATH="$current_library_path" timeout 20s "$headless_shell_path" --version 2>&1 || true)"
else
  probe_output="$(timeout 20s "$headless_shell_path" --version 2>&1 || true)"
fi
# Chrome for Testing prefixes the version with "Google Chrome for Testing",
# while some Chromium builds print the version at the start of the line.
# Validate the version token rather than assuming a particular prefix.
if ! grep -qE '(^|[[:space:]])([0-9]+\.){2,3}[0-9]+([[:space:]]|$)' <<< "$probe_output"; then
  probe_missing="$(
    printf '%s\n' "$probe_output" |
      sed -nE 's/.*shared libraries: ([^:]+):.*/\1/p' |
      sort -u
  )"
  echo "::error::Chromium headless-shell runtime probe failed before the browser gate."
  if [[ -n "$probe_missing" ]]; then
    while IFS= read -r library; do
      [[ -n "$library" ]] && echo "::error::Missing shared library: $library"
    done <<< "$probe_missing"
  fi
  echo "::error::Chromium headless shell: $headless_shell_path"
  printf '%s\n' "$probe_output"
  exit 1
fi

if [[ -n "$current_library_path" && -n "${GITHUB_ENV:-}" ]]; then
  printf 'LD_LIBRARY_PATH=%s\n' "$current_library_path" >> "$GITHUB_ENV"
fi

echo "Chromium runtime preflight passed: ${browser_paths[*]}"
if [[ -n "$current_library_path" ]]; then
  echo "Chromium loader search path includes the runner's configured and discovered library directories."
fi