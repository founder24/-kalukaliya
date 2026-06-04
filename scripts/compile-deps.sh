#!/usr/bin/env bash
# =============================================================================
# COMPILE PYTHON DEPENDENCIES
# =============================================================================
# Regenerates apps/backend/requirements.txt from apps/backend/requirements.in
# using pip-compile (from pip-tools). Run this whenever you change requirements.in
# or after a dependency bump, then commit the updated requirements.txt.
#
# Usage:
#   bash scripts/compile-deps.sh           # regenerate requirements.txt
#   bash scripts/compile-deps.sh --check   # validate without writing (CI mode)
#   bash scripts/compile-deps.sh --verify  # check pinned versions exist on PyPI
#
# Requirements:
#   pip install pip-tools   (one-time: done automatically if missing)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$REPO_ROOT/apps/backend"
REQUIREMENTS_IN="$BACKEND_DIR/requirements.in"
REQUIREMENTS_TXT="$BACKEND_DIR/requirements.txt"

MODE="compile"
if [[ "${1:-}" == "--check" ]]; then MODE="check"; fi
if [[ "${1:-}" == "--verify" ]]; then MODE="verify"; fi

# ── Colours ───────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; CYAN=''; NC=''
fi

echo ""
echo -e "${CYAN}Syrabit dep compiler — mode: ${MODE}${NC}"
echo ""

# ── Ensure pip-tools is available ─────────────────────────────────────────────
if ! command -v pip-compile &>/dev/null; then
  echo -e "${YELLOW}pip-tools not found — installing...${NC}"
  pip install pip-tools --quiet
fi

PIP_COMPILE_CMD=(
  pip-compile
  --python-version 3.12
  --strip-extras
  --no-header
  --output-file "$REQUIREMENTS_TXT"
  "$REQUIREMENTS_IN"
)

# =============================================================================
# MODE: verify — check all pinned versions in requirements.txt exist on PyPI
# =============================================================================
if [[ "$MODE" == "verify" ]]; then
  echo -e "  Verifying all pinned versions exist on PyPI..."
  echo -e "  (strips hashes, runs pip install --dry-run --no-deps)"
  echo ""

  TMP_REQ=$(mktemp)
  # Strip hash lines and multiline continuations; keep only package==version lines
  sed 's/ *\\$//' "$REQUIREMENTS_TXT" \
    | grep -v '^\s*--hash' \
    | grep -v '^\s*#' \
    | grep -v '^\s*$' \
    > "$TMP_REQ"

  FAILED=0
  while IFS= read -r pkg; do
    # Only check pinned lines (contains ==)
    if echo "$pkg" | grep -q '=='; then
      name="${pkg%%==*}"
      ver="${pkg##*==}"
      ver="${ver%%[^0-9.]*}"   # strip trailing extras like [crypto]
      if pip index versions "$name" 2>/dev/null | grep -q "$ver"; then
        echo -e "  ${GREEN}✔${NC} ${pkg}"
      else
        echo -e "  ${RED}✘${NC} ${pkg}  ← version not found on PyPI"
        FAILED=$((FAILED + 1))
      fi
    fi
  done < "$TMP_REQ"
  rm -f "$TMP_REQ"

  echo ""
  if [[ "$FAILED" -gt 0 ]]; then
    echo -e "${RED}✘  ${FAILED} package version(s) not found on PyPI.${NC}"
    echo -e "   Update the pinned version in requirements.txt (or fix requirements.in and re-run compile-deps.sh)."
    exit 1
  else
    echo -e "${GREEN}✔  All pinned versions verified.${NC}"
    exit 0
  fi
fi

# =============================================================================
# MODE: check — compile to a temp file, diff against committed requirements.txt
# =============================================================================
if [[ "$MODE" == "check" ]]; then
  TMP_OUT=$(mktemp)
  echo -e "  Running pip-compile in check mode (no files written)..."

  "${PIP_COMPILE_CMD[@]}" --dry-run --output-file "$TMP_OUT" 2>/dev/null \
    || pip-compile --python-version 3.12 --strip-extras --no-header \
        --output-file "$TMP_OUT" "$REQUIREMENTS_IN" >/dev/null 2>&1

  # Compare ignoring comment lines (timestamps differ)
  DIFF=$(diff \
    <(grep -v '^\s*#' "$REQUIREMENTS_TXT" | sort) \
    <(grep -v '^\s*#' "$TMP_OUT" | sort) \
    || true)
  rm -f "$TMP_OUT"

  if [[ -z "$DIFF" ]]; then
    echo -e "${GREEN}✔  requirements.txt is in sync with requirements.in${NC}"
    exit 0
  else
    echo -e "${RED}✘  requirements.txt is OUT OF SYNC with requirements.in${NC}"
    echo ""
    echo -e "  Diff (requirements.txt vs what pip-compile would generate):"
    echo "$DIFF" | head -40 | sed 's/^/    /'
    echo ""
    echo -e "  Fix: run  ${CYAN}bash scripts/compile-deps.sh${NC}  then commit the result."
    exit 1
  fi
fi

# =============================================================================
# MODE: compile — regenerate requirements.txt from requirements.in
# =============================================================================
echo -e "  Source:  ${CYAN}${REQUIREMENTS_IN}${NC}"
echo -e "  Output:  ${CYAN}${REQUIREMENTS_TXT}${NC}"
echo ""

"${PIP_COMPILE_CMD[@]}"

# Prepend the standard header comment
HEADER="# This file is autogenerated by pip-compile (pip-tools).
# Source: apps/backend/requirements.in
# Regenerate: bash scripts/compile-deps.sh
# Do NOT edit this file directly.
#
# pip-compile --python-version 3.12 --strip-extras --no-header \\
#     --output-file apps/backend/requirements.txt apps/backend/requirements.in
"
TMP=$(mktemp)
{ echo "$HEADER"; cat "$REQUIREMENTS_TXT"; } > "$TMP"
mv "$TMP" "$REQUIREMENTS_TXT"

echo ""
echo -e "${GREEN}✔  requirements.txt regenerated successfully.${NC}"
echo ""
echo -e "  Next: review the diff, then commit:"
echo -e "    ${CYAN}git diff apps/backend/requirements.txt${NC}"
echo -e "    ${CYAN}git add apps/backend/requirements.txt && git commit -m 'chore: regenerate requirements.txt'${NC}"
echo ""
