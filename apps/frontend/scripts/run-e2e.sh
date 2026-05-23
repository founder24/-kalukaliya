#!/usr/bin/env bash
# scripts/run-e2e.sh — Replit/NixOS-friendly Playwright launcher.
#
# Why this wrapper exists (Task #904)
# -----------------------------------
# Playwright bundles its own Chromium / chrome-headless-shell. On the
# Replit NixOS image those binaries fail to load with one of:
#
#   error while loading shared libraries: libgbm.so.1: cannot open shared
#     object file: No such file or directory
#   libatk-bridge-2.0.so.0: undefined symbol: atk_object_get_help_text
#
# The cause is twofold:
#   (1) `libgbm.so.1` lives in a transitively-pulled `mesa-libgbm-*` nix
#       store path that is *not* on the default loader search path here.
#   (2) Only `libudev.so.0` is shipped (via `libudev0-shim`); Chromium
#       needs `libudev.so.1`. The two ABIs are compatible enough for
#       headless Chromium's minimal udev usage, so we present a
#       `libudev.so.1 -> libudev.so.0` symlink in a writable shim dir.
#
# Control flow (in priority order)
# --------------------------------
# 1. If `REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE` is set, the Playwright
#    config will launch that pre-staged Replit Chromium directly via
#    `launchOptions.executablePath` — its dependencies are already
#    satisfied by the surrounding nix derivation, so we skip every shim
#    and just exec `playwright test`.
# 2. If we're not on a Nix environment at all (`/nix/store` missing —
#    e.g. CI runners on Ubuntu, contributor laptops with apt Chromium),
#    we also skip the shim and exec `playwright test`. Those hosts
#    rely on Playwright's own `--with-deps` install, which works fine.
# 3. Otherwise we try to assemble the LD_LIBRARY_PATH shim. If a piece
#    is missing we *warn* and still hand off to Playwright so its own
#    error message surfaces — never hard-exit, which would mask the
#    real failure on hosts the wrapper doesn't fully understand.
#
# Doing the fix here (instead of in `replit.nix`) avoids forcing a Nix
# rebuild on every contributor and keeps the workaround localised to
# the e2e command.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
PKG_DIR="$(cd -- "$SCRIPT_DIR/.." &> /dev/null && pwd)"

debug() {
  if [ "${DEBUG_E2E_LIBS:-0}" = "1" ]; then
    echo "run-e2e.sh: $*" >&2
  fi
}

exec_playwright() {
  cd "${PKG_DIR}"
  exec ./node_modules/.bin/playwright test "$@"
}

# --- Path 1: Replit pre-staged Chromium ------------------------------------
if [ -n "${REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE:-}" ] \
   && [ -x "${REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE}" ]; then
  debug "using REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE; skipping shim setup"
  exec_playwright "$@"
fi

# --- Path 2: not a Nix host -> nothing to shim -----------------------------
if [ ! -d /nix/store ]; then
  debug "not on a Nix host; running playwright with system defaults"
  exec_playwright "$@"
fi

# --- Path 3: Nix host without the Replit env var -> try LD shim ------------
GBM_LIB="$(ls -d /nix/store/*-mesa-libgbm-*/lib 2>/dev/null | sort | tail -1 || true)"
if [ -z "${GBM_LIB}" ] || [ ! -e "${GBM_LIB}/libgbm.so.1" ]; then
  GBM_LIB="$(ls -d /nix/store/*-mesa-*/lib 2>/dev/null \
    | while read -r d; do [ -e "$d/libgbm.so.1" ] && echo "$d"; done \
    | sort | tail -1 || true)"
fi

UDEV0="$(ls /nix/store/*-libudev0-shim-*/lib/libudev.so.0 2>/dev/null | sort | tail -1 || true)"

if [ -z "${GBM_LIB:-}" ] || [ -z "${UDEV0:-}" ]; then
  echo "run-e2e.sh: Nix host detected but couldn't assemble the libgbm" >&2
  echo "  / libudev shim (gbm='${GBM_LIB:-MISSING}', udev='${UDEV0:-MISSING}')." >&2
  echo "  Falling through to Playwright as-is — if it fails to launch," >&2
  echo "  set REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE or add 'pkgs.mesa'" >&2
  echo "  + a libudev source to replit.nix." >&2
  exec_playwright "$@"
fi

SHIM_DIR="${HOME}/.cache/playwright-libs"
mkdir -p "${SHIM_DIR}"
ln -sfn "${UDEV0}" "${SHIM_DIR}/libudev.so.1"

export LD_LIBRARY_PATH="${GBM_LIB}:${SHIM_DIR}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
debug "LD_LIBRARY_PATH=${LD_LIBRARY_PATH}"

exec_playwright "$@"
