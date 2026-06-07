#!/usr/bin/env bash
# Verify the live syrabit.ai JS bundle is correctly configured.
#
# Architecture:
#   VITE_BACKEND_URL      — raw Cloud Run URL (direct backend, used for most API calls)
#   VITE_WORKER_API_URL   — CF Worker URL (api.syrabit.ai), used when set for secured routes
#
# This script verifies:
#   1. The bundle filename is discoverable (Vite rebuilt successfully)
#   2. The bundle contains the EXPECTED Cloud Run URL (not a stale/wrong one)
#   3. The Cloud Run URL in the bundle matches the current backend hostname
#   4. WARN if api.syrabit.ai is not referenced (CF edge bypassed for all calls)
#
# Note: The presence of the raw Cloud Run URL is expected — it is VITE_BACKEND_URL.
# The check is that it is the CURRENT Cloud Run URL, not a stale one from a
# previous deploy (Cloud Run assigns new URL hashes on new services).
#
# Usage:
#   bash scripts/verify-cf-pages-url.sh
#   bash scripts/verify-cf-pages-url.sh https://staging.syrabit.ai
#
# Exit codes:
#   0 — bundle is correctly configured
#   1 — bundle missing, stale Cloud Run URL, or network failure

set -euo pipefail

EXPECTED_CLOUD_RUN_HOST="syrabit-backend-bl6wu3psza-el.a.run.app"
EXPECTED_WORKER_HOST="api.syrabit.ai"
BASE="${1:-https://syrabit.ai}"

PASS=0; FAIL=0; WARN=0

ok()   { PASS=$((PASS+1));  printf "  \033[0;32m✔\033[0m  %s\n" "$*"; }
fail() { FAIL=$((FAIL+1));  printf "  \033[0;31m✖\033[0m  %s\n" "$*"; }
warn() { WARN=$((WARN+1));  printf "  \033[1;33m⚠\033[0m  %s\n" "$*"; }

echo ""
echo "  CF Pages Bundle Verification"
echo "  Target : $BASE"
echo ""

# 1. Discover bundle filename dynamically
HTML=$(curl -sf -L --compressed --max-time 15 "${BASE}/" || echo "")
if [[ -z "$HTML" ]]; then
  fail "Could not fetch ${BASE}/ (network failure or non-200)"
  exit 1
fi

BUNDLE_PATH=$(printf '%s' "$HTML" | grep -oE 'src="/assets/index-[^"]+\.js"' | sed 's/src="//;s/"//' | head -1)

if [[ -z "$BUNDLE_PATH" ]]; then
  fail "No /assets/index-*.js bundle found in HTML — Vite build may have failed"
  exit 1
fi
ok "Bundle found: ${BUNDLE_PATH}"

# 2. Fetch the bundle
BUNDLE=$(curl -sf --max-time 25 "${BASE}${BUNDLE_PATH}" || echo "")
if [[ -z "$BUNDLE" ]]; then
  fail "Bundle returned empty (404 or network failure): ${BASE}${BUNDLE_PATH}"
  exit 1
fi
ok "Bundle fetched successfully"

# 3. Check VITE_BACKEND_URL points to the correct Cloud Run host
if printf '%s' "$BUNDLE" | grep -q "$EXPECTED_CLOUD_RUN_HOST"; then
  ok "VITE_BACKEND_URL → correct Cloud Run host ($EXPECTED_CLOUD_RUN_HOST)"
else
  # Check if it has ANY .run.app URL (stale from a different service)
  OTHER_HOST=$(printf '%s' "$BUNDLE" | grep -oE '[a-z0-9-]+\.a\.run\.app' | head -1 || echo "")
  if [[ -n "$OTHER_HOST" ]]; then
    fail "Bundle has STALE Cloud Run URL: $OTHER_HOST (expected $EXPECTED_CLOUD_RUN_HOST)"
  else
    warn "No Cloud Run URL found in bundle — VITE_BACKEND_URL may be empty or relative"
  fi
fi

# 4. Check if CF Worker URL (api.syrabit.ai) is referenced
if printf '%s' "$BUNDLE" | grep -q "$EXPECTED_WORKER_HOST"; then
  ok "CF Worker URL ($EXPECTED_WORKER_HOST) is referenced in bundle (VITE_WORKER_API_URL set)"
else
  warn "CF Worker URL ($EXPECTED_WORKER_HOST) NOT in bundle — all API calls go directly to Cloud Run"
  warn "Consider setting VITE_WORKER_API_URL=$EXPECTED_WORKER_HOST in CF Pages env vars"
  warn "Current: all browser → Cloud Run (no CF WAF/rate-limit for UI traffic)"
fi

# 5. Summary
echo ""
echo "  ─────────────────────────────────────"
printf "  ✔ Passed : %d\n" $PASS
printf "  ✖ Failed : %d\n" $FAIL
printf "  ⚠ Warned : %d\n" $WARN
echo "  ─────────────────────────────────────"
if [[ $FAIL -gt 0 ]]; then
  printf "\n  \033[0;31mBUNDLE VERIFICATION FAILED\033[0m\n\n"
  exit 1
else
  printf "\n  \033[0;32mBUNDLE VERIFIED\033[0m\n\n"
  exit 0
fi
