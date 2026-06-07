#!/usr/bin/env bash
# verify-step0-passed.sh
#
# Post-build check: confirm the most recent Cloud Build run completed Step 0
# (SSH deploy key setup) successfully by grepping its logs for the success
# marker ("=== SSH setup complete ===") and the absence of fatal error patterns.
#
# Run this in Cloud Shell immediately after a build completes to get a
# machine-readable pass/fail verdict on Step 0 without manual log browsing.
#
# Usage (from repo root in Cloud Shell):
#   bash infra/scripts/verify-step0-passed.sh
#
# To check a specific build ID instead of the latest:
#   BUILD_ID=<id> bash infra/scripts/verify-step0-passed.sh
#
# Exit code: 0 = Step 0 passed, 1 = Step 0 failed or inconclusive

set -uo pipefail

PROJECT="blissful-acumen-495019-t6"
SUCCESS_MARKER="=== SSH setup complete ==="
FAILURE_PATTERNS=(
  "secret.*not.*found"
  "Permission denied"
  "RESOURCE_NOT_FOUND"
  "secretmanager.*403"
  "secretmanager.*404"
)

ok()   { echo "  ✓ $*"; }
fail() { echo "  ✗ $*"; }
info() { echo "    $*"; }

echo ""
echo "=== Syrabit: Cloud Build Step 0 Verification ==="
echo "    Project : ${PROJECT}"
echo ""

# ── Resolve build ID ─────────────────────────────────────────────────────────
if [[ -n "${BUILD_ID:-}" ]]; then
  echo "Using provided BUILD_ID: ${BUILD_ID}"
else
  echo "Resolving most recent build..."
  BUILD_ID=$(gcloud builds list \
    --project="${PROJECT}" \
    --limit=1 \
    --format="value(id)" 2>/dev/null || echo "")
  if [[ -z "${BUILD_ID}" ]]; then
    fail "Could not retrieve any builds for project ${PROJECT}"
    echo ""
    echo "RESULT: INCONCLUSIVE — no builds found"
    exit 1
  fi
  echo "  Most recent build: ${BUILD_ID}"
fi

# ── Fetch Step 0 logs ─────────────────────────────────────────────────────────
echo ""
echo "Fetching Step 0 log lines from build ${BUILD_ID}..."
LOGS=$(gcloud builds log "${BUILD_ID}" \
  --project="${PROJECT}" 2>/dev/null || echo "")

if [[ -z "${LOGS}" ]]; then
  fail "Could not retrieve logs for build ${BUILD_ID}"
  info "The build may still be running, or you may lack logging permissions."
  echo ""
  echo "RESULT: INCONCLUSIVE — logs unavailable"
  exit 1
fi

# Isolate Step 0 output (lines between the two step banners)
STEP0_LOGS=$(echo "${LOGS}" \
  | awk '/=== SSH setup: configuring deploy key/{found=1} found{print} /=== SSH setup complete ==={exit}')

echo ""
echo "── Step 0 log excerpt ──────────────────────────────"
echo "${STEP0_LOGS}" | head -30
echo "────────────────────────────────────────────────────"
echo ""

# ── Check 1: Success marker present ──────────────────────────────────────────
echo "[ 1/2 ] Success marker present..."
if echo "${STEP0_LOGS}" | grep -qF "${SUCCESS_MARKER}"; then
  ok "Found: '${SUCCESS_MARKER}'"
else
  fail "NOT found: '${SUCCESS_MARKER}'"
  info "Step 0 did not reach the success marker — see log excerpt above."
fi

# ── Check 2: No fatal error patterns ─────────────────────────────────────────
echo ""
echo "[ 2/2 ] No fatal error patterns in Step 0 logs..."
ERRORS_FOUND=0
for pattern in "${FAILURE_PATTERNS[@]}"; do
  if echo "${STEP0_LOGS}" | grep -qi "${pattern}"; then
    fail "Error pattern matched: '${pattern}'"
    ERRORS_FOUND=$((ERRORS_FOUND + 1))
  fi
done
if [[ ${ERRORS_FOUND} -eq 0 ]]; then
  ok "No fatal error patterns detected"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "───────────────────────────────────────────────────"
echo "Build ID : ${BUILD_ID}"
echo "Console  : https://console.cloud.google.com/cloud-build/builds/${BUILD_ID}?project=${PROJECT}"
echo ""

if echo "${STEP0_LOGS}" | grep -qF "${SUCCESS_MARKER}" && [[ ${ERRORS_FOUND} -eq 0 ]]; then
  echo "RESULT: PASSED — Step 0 completed successfully"
  echo ""
  echo "  SSH connectivity line from log:"
  echo "${STEP0_LOGS}" | grep -E "(✓ GitHub|⚠ GitHub)" | head -1 | sed 's/^/    /'
  exit 0
else
  echo "RESULT: FAILED — Step 0 did not pass (see log excerpt and checks above)"
  echo ""
  echo "Common causes:"
  echo "  - Secret GITHUB_DEPLOY_SSH_KEY missing or disabled → run verify-github-deploy-key.sh"
  echo "  - SA lacks secretAccessor role → run setup-github-deploy-key.sh"
  echo "  - Public key not registered on GitHub → check Settings → Deploy keys"
  exit 1
fi
