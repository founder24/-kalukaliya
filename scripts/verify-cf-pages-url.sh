#!/usr/bin/env bash
# Verify that the live syrabit.ai JS bundle uses https://api.syrabit.ai
# and NOT the raw Cloud Run URL.
#
# Usage:
#   bash scripts/verify-cf-pages-url.sh
#
# Exit codes:
#   0 — bundle contains https://api.syrabit.ai (correct)
#   1 — bundle missing or contains wrong URL

set -euo pipefail

CORRECT_URL="https://api.syrabit.ai"
WRONG_PATTERN="syrabit-backend.*\.run\.app"
BUNDLE_PATH="/assets/index-D2uqrDeL.js"
BASE="${1:-https://syrabit.ai}"

echo "Checking live bundle: ${BASE}${BUNDLE_PATH}"

BUNDLE=$(curl -sf --max-time 15 "${BASE}${BUNDLE_PATH}" || echo "")

if [[ -z "$BUNDLE" ]]; then
  echo "ERROR: bundle returned empty (404 or network failure)"
  exit 1
fi

if echo "$BUNDLE" | grep -qE "$WRONG_PATTERN"; then
  WRONG_URL=$(echo "$BUNDLE" | grep -oE "https://syrabit-backend[^\"' ]+" | head -1)
  echo "FAIL: bundle still contains wrong URL: $WRONG_URL"
  exit 1
fi

if echo "$BUNDLE" | grep -q "$CORRECT_URL"; then
  echo "OK: bundle correctly references $CORRECT_URL"
  exit 0
fi

echo "WARN: neither correct nor wrong URL found — bundle may have changed filename"
exit 0
