#!/usr/bin/env bash
# scripts/run-e2e.sh — Replit/NixOS-friendly Playwright launcher.
#
# Why this wrapper exists
# -----------------------
# Playwright bundles its own Chromium / chrome-headless-shell. On the
# Replit NixOS image the bundled binary gets SIGTERM'd by the sandbox
# before it can even finish downloading.
#
# The fix: install the system `chromium` nix package (added to .replit's
# nix packages list) and point Playwright at it via
# PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH.  playwright.config.ts reads that
# env var and passes it as `launchOptions.executablePath`, keeping
# Playwright's own protocol layer while using the nix-managed binary.
#
# Control flow (in priority order)
# --------------------------------
# 1. REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE (legacy env var, kept for compat)
# 2. PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH   (preferred)
# 3. Auto-detect: `which chromium` (system nix package)
# 4. Auto-detect: `which google-chrome` or `which chromium-browser` (CI)
# 5. Fall through — let Playwright use its bundled binary and surface its
#    own error message (never hard-exit so the real failure is visible).

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
PKG_DIR="$(cd -- "$SCRIPT_DIR/.." &> /dev/null && pwd)"

exec_playwright() {
  cd "${PKG_DIR}"
  exec ./node_modules/.bin/playwright test "$@"
}

# --- Path 1: legacy env var (kept for backward compat) ----------------------
if [ -n "${REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE:-}" ] \
   && [ -x "${REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE}" ]; then
  export PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH="${REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE}"
  exec_playwright "$@"
fi

# --- Path 2: explicit env var already set ------------------------------------
if [ -n "${PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH:-}" ] \
   && [ -x "${PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}" ]; then
  exec_playwright "$@"
fi

# --- Path 3: system nix chromium (installed as a nix package) ----------------
if CHROMIUM_BIN="$(which chromium 2>/dev/null)" && [ -x "${CHROMIUM_BIN}" ]; then
  export PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH="${CHROMIUM_BIN}"
  exec_playwright "$@"
fi

# --- Path 4: other common system browser names (CI / contributor laptops) ----
for CANDIDATE in google-chrome google-chrome-stable chromium-browser; do
  if CHROMIUM_BIN="$(which "${CANDIDATE}" 2>/dev/null)" && [ -x "${CHROMIUM_BIN}" ]; then
    export PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH="${CHROMIUM_BIN}"
    exec_playwright "$@"
  fi
done

# --- Path 5: fall through — let Playwright's own error surface ---------------
echo "run-e2e.sh: No system Chromium found; falling through to Playwright's" >&2
echo "  bundled browser. If it fails, install the 'chromium' nix package." >&2
exec_playwright "$@"
