#!/usr/bin/env bash
# ============================================================================
# CI/CD E2E Test Runner
# ============================================================================
#
# Runs both infrastructure and functional e2e tests.
# Designed for GitHub Actions, Replit, or any CI environment.
#
# Required Environment Variables:
#   CLOUDFLARE_API_TOKEN   - Cloudflare API token (Workers edit permission)
#   CF_ACCOUNT_ID          - Cloudflare account ID
#   GCP_SA_KEY_JSON        - GCP service account key JSON (for identity token)
#                            OR run on a machine with gcloud already authenticated
#
# Optional Environment Variables:
#   FRONTEND_URL           - Override (default: https://syrabit.ai)
#   EDGE_URL               - Override (default: https://api.syrabit.ai)
#   BACKEND_URL            - Override (default: https://syrabit-backend-851687450401.asia-south1.run.app)
#   SKIP_FUNCTIONAL        - Set to "true" to skip functional tests (faster)
#   SKIP_INFRA             - Set to "true" to skip infrastructure tests
#   VERBOSE                - Set to "true" for verbose output
#
# Usage:
#   # In GitHub Actions (with secrets):
#   ./scripts/ci-e2e-runner.sh
#
#   # With explicit env vars:
#   CLOUDFLARE_API_TOKEN=xxx GCP_SA_KEY_JSON='{"type":"service_account",...}' ./scripts/ci-e2e-runner.sh
#
#   # Skip functional tests (infra only, no GCP needed):
#   SKIP_FUNCTIONAL=true ./scripts/ci-e2e-runner.sh
#
# Exit code: 0 if all pass, 1 if any critical test fails
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERBOSE_FLAG=""
INFRA_EXIT=0
FUNC_EXIT=0

if [[ "${VERBOSE:-false}" == "true" ]]; then
    VERBOSE_FLAG="--verbose"
fi

echo "============================================================================"
echo "  SYRABIT.AI CI/CD E2E TEST RUNNER"
echo "============================================================================"
echo "  Date: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo ""

# --- GCP Authentication Setup ------------------------------------------------

if [[ -n "${GCP_SA_KEY_JSON:-}" ]]; then
    echo "[setup] Activating GCP service account..."
    SA_KEY_FILE=$(mktemp)
    echo "$GCP_SA_KEY_JSON" > "$SA_KEY_FILE"
    gcloud auth activate-service-account --key-file="$SA_KEY_FILE" --quiet 2>/dev/null
    rm -f "$SA_KEY_FILE"
    echo "[setup] GCP service account activated"
elif command -v gcloud &>/dev/null && gcloud auth print-identity-token --audiences="https://test" &>/dev/null 2>&1; then
    echo "[setup] Using existing gcloud authentication"
else
    echo "[setup] WARNING: No GCP credentials available"
    echo "[setup] Functional tests will be skipped"
    SKIP_FUNCTIONAL="true"
fi

echo ""

# --- Run Infrastructure Tests ------------------------------------------------

if [[ "${SKIP_INFRA:-false}" != "true" ]]; then
    echo "============================================================================"
    echo "  PHASE 1: Infrastructure Tests"
    echo "============================================================================"
    echo ""

    if [[ -f "$SCRIPT_DIR/e2e-live-test.sh" ]]; then
        bash "$SCRIPT_DIR/e2e-live-test.sh" $VERBOSE_FLAG || INFRA_EXIT=$?
    else
        echo "ERROR: e2e-live-test.sh not found at $SCRIPT_DIR"
        INFRA_EXIT=1
    fi
    echo ""
else
    echo "[skip] Infrastructure tests skipped (SKIP_INFRA=true)"
    echo ""
fi

# --- Run Functional Tests ----------------------------------------------------

if [[ "${SKIP_FUNCTIONAL:-false}" != "true" ]]; then
    echo "============================================================================"
    echo "  PHASE 2: Functional Tests"
    echo "============================================================================"
    echo ""

    if [[ -f "$SCRIPT_DIR/e2e-functional-test.sh" ]]; then
        bash "$SCRIPT_DIR/e2e-functional-test.sh" $VERBOSE_FLAG || FUNC_EXIT=$?
    else
        echo "ERROR: e2e-functional-test.sh not found at $SCRIPT_DIR"
        FUNC_EXIT=1
    fi
    echo ""
else
    echo "[skip] Functional tests skipped (SKIP_FUNCTIONAL=true or no GCP credentials)"
    echo ""
fi

# --- Summary -----------------------------------------------------------------

echo "============================================================================"
echo "  CI/CD E2E RUNNER SUMMARY"
echo "============================================================================"
echo ""
echo "  Infrastructure tests: $(if [[ $INFRA_EXIT -eq 0 ]]; then echo 'PASSED'; else echo 'FAILED'; fi)"
echo "  Functional tests:     $(if [[ $FUNC_EXIT -eq 0 ]]; then echo 'PASSED'; elif [[ "${SKIP_FUNCTIONAL:-false}" == "true" ]]; then echo 'SKIPPED'; else echo 'FAILED'; fi)"
echo ""

if [[ $INFRA_EXIT -ne 0 || $FUNC_EXIT -ne 0 ]]; then
    echo "  RESULT: FAILED"
    echo ""
    echo "============================================================================"
    exit 1
fi

echo "  RESULT: ALL PASSED"
echo ""
echo "============================================================================"
exit 0
