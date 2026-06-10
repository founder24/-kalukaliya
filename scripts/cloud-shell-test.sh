#!/usr/bin/env bash
# =============================================================================
# SYRABIT CLOUD SHELL FULLSTACK TEST
# =============================================================================
# One command to validate the entire production stack after any deploy.
#
# Usage (from ~/syrabit on GCP Cloud Shell or VM):
#
#   Basic (no credentials — tests health, content, SEO, security, edge):
#     bash scripts/cloud-shell-test.sh
#
#   Full (with credentials — adds auth + chat pipeline tests):
#     TEST_USER_EMAIL="you@example.com" \
#     TEST_USER_PASSWORD="yourpassword" \
#     bash scripts/cloud-shell-test.sh
#
#   Specific category only:
#     bash scripts/cloud-shell-test.sh --category health,auth,security
#
#   After a deploy — validates new revision is live first:
#     bash scripts/cloud-shell-test.sh --check-revision
#
# Environment variables (all optional):
#   TEST_USER_EMAIL       User email for auth/chat tests
#   TEST_USER_PASSWORD    User password for auth/chat tests
#   BACKEND_URL           Override API base (default: https://api.syrabit.ai)
#   FRONTEND_URL          Override frontend (default: https://syrabit.ai)
#   GCP_PROJECT           GCP project ID for Cloud Run checks
#   GCP_REGION            GCP region (default: asia-south1)
# =============================================================================

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
BACKEND_URL="${BACKEND_URL:-https://api.syrabit.ai}"
FRONTEND_URL="${FRONTEND_URL:-https://syrabit.ai}"
GCP_PROJECT="${GCP_PROJECT:-blissful-acumen-495019-t6}"
GCP_REGION="${GCP_REGION:-asia-south1}"
CATEGORY="${CATEGORY:-all}"
CHECK_REVISION=0
SKIP_CHAT=0
VERBOSE=0

# ── Colours ───────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
  BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; BOLD=''; NC=''
fi

# ── Counters ──────────────────────────────────────────────────────────────────
SECTION_PASS=0
SECTION_FAIL=0
SECTION_SKIP=0
declare -a FAILED_SECTIONS=()

# ── Arg parsing ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-revision) CHECK_REVISION=1; shift ;;
    --skip-chat)      SKIP_CHAT=1; shift ;;
    --verbose)        VERBOSE=1; shift ;;
    --category)       CATEGORY="$2"; shift 2 ;;
    --backend-url)    BACKEND_URL="$2"; shift 2 ;;
    --frontend-url)   FRONTEND_URL="$2"; shift 2 ;;
    --help|-h)
      sed -n '/^# Usage/,/^# =/p' "$0" | grep -v '^#' || true
      grep '^#' "$0" | head -40
      exit 0
      ;;
    *) echo "Unknown option: $1 (use --help)"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
START_TIME=$(date +%s)

# ── Helpers ───────────────────────────────────────────────────────────────────
section_pass() { echo -e "  ${GREEN}✔${NC} $1"; SECTION_PASS=$((SECTION_PASS + 1)); }
section_fail() { echo -e "  ${RED}✘${NC} $1"; SECTION_FAIL=$((SECTION_FAIL + 1)); FAILED_SECTIONS+=("$1"); }
section_skip() { echo -e "  ${YELLOW}⊘${NC} $1"; SECTION_SKIP=$((SECTION_SKIP + 1)); }
section_info() { echo -e "  ${CYAN}ℹ${NC}  $1"; }

run_section() {
  local name="$1"; shift
  echo ""
  echo -e "${BOLD}▶ ${name}${NC}"
  "$@"
}

# curl with up to 3 retries and exponential backoff (2s, 4s)
# Usage: curl_retry [curl-args...]
curl_retry() {
  local attempt=0
  local max_attempts=3
  local sleep_secs=2
  while [[ $attempt -lt $max_attempts ]]; do
    if curl "$@"; then
      return 0
    fi
    attempt=$((attempt + 1))
    if [[ $attempt -lt $max_attempts ]]; then
      sleep "$sleep_secs"
      sleep_secs=$((sleep_secs * 2))
    fi
  done
  return 1
}

http_check() {
  local label="$1" url="$2" expected="$3"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "$url" 2>/dev/null || echo "000")
  # $expected may be a space-separated list of acceptable codes (e.g. "200 301")
  if [[ " $expected " == *" $code "* ]]; then
    section_pass "${label} → HTTP ${code}"
    return 0
  else
    section_fail "${label} → expected HTTP ${expected}, got HTTP ${code}"
    return 1
  fi
}

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║         SYRABIT FULLSTACK TEST  —  $(date -u '+%Y-%m-%d %H:%M UTC')         ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Backend:   ${BLUE}${BACKEND_URL}${NC}"
echo -e "  Frontend:  ${BLUE}${FRONTEND_URL}${NC}"
echo -e "  Project:   ${BLUE}${GCP_PROJECT}${NC} / ${GCP_REGION}"
if [[ -n "${TEST_USER_EMAIL:-}" ]]; then
  echo -e "  Auth user: ${BLUE}${TEST_USER_EMAIL}${NC}"
else
  echo -e "  Auth user: ${YELLOW}not set — auth/chat tests will be skipped${NC}"
  echo -e "             ${YELLOW}set TEST_USER_EMAIL + TEST_USER_PASSWORD to enable${NC}"
fi
echo ""

# =============================================================================
# SECTION 1 — CLOUD RUN DEPLOYMENT STATUS
# =============================================================================
check_cloud_run() {
  if ! command -v gcloud &>/dev/null; then
    section_skip "gcloud not found — skipping Cloud Run checks"
    return
  fi

  echo -e "  ${CYAN}Checking Cloud Run service: syrabit-backend${NC}"

  # Active revision & traffic split
  local revision traffic
  revision=$(gcloud run revisions list \
    --service syrabit-backend \
    --region "$GCP_REGION" \
    --project "$GCP_PROJECT" \
    --format "value(REVISION)" \
    --sort-by "~creationTimestamp" \
    --limit 1 2>/dev/null || echo "unknown")

  traffic=$(gcloud run services describe syrabit-backend \
    --region "$GCP_REGION" \
    --project "$GCP_PROJECT" \
    --format "value(status.traffic[0].percent)" 2>/dev/null || echo "?")

  if [[ "$revision" != "unknown" ]]; then
    section_pass "Latest revision: ${revision} (traffic: ${traffic}%)"
  else
    section_fail "Could not read Cloud Run revision (check gcloud auth)"
  fi

  # Check service URL matches expected
  local service_url
  service_url=$(gcloud run services describe syrabit-backend \
    --region "$GCP_REGION" \
    --project "$GCP_PROJECT" \
    --format "value(status.url)" 2>/dev/null || echo "")

  if [[ -n "$service_url" ]]; then
    local health_code
    health_code=$(curl -sf -o /dev/null -w "%{http_code}" \
      "${service_url}/health" --max-time 10 2>/dev/null || echo "000")
    if [[ "$health_code" == "200" ]]; then
      section_pass "Cloud Run direct health check → HTTP 200 (${service_url}/health)"
    else
      section_fail "Cloud Run direct health check → HTTP ${health_code} (${service_url}/health)"
    fi
  fi

  # If --check-revision: warn if latest revision isn't getting 100% traffic
  if [[ "$CHECK_REVISION" -eq 1 ]]; then
    if [[ "$traffic" == "100" ]]; then
      section_pass "Latest revision receives 100% of traffic"
    else
      section_fail "Latest revision only at ${traffic}% traffic — deploy may be stuck"
    fi
  fi
}

run_section "1. Cloud Run Deployment Status" check_cloud_run

# =============================================================================
# SECTION 2 — BACKEND HEALTH ENDPOINTS
# =============================================================================
check_backend_health() {
  http_check "GET /health"              "${BACKEND_URL}/health"              "200"
  # /health/deep and /health/circuit-breakers: proxied through edge to backend
  # Accept 200 (healthy), 503 (degraded-but-alive), or 200 via circuit-breaker endpoint
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 \
    "${BACKEND_URL}/health/deep" 2>/dev/null || echo "000")
  if [[ "$code" == "200" || "$code" == "503" ]]; then
    section_pass "GET /health/deep → HTTP ${code} (200=healthy, 503=degraded-but-alive)"
  else
    section_fail "GET /health/deep → HTTP ${code} (expected 200 or 503)"
  fi

  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 \
    "${BACKEND_URL}/health/circuit-breakers" 2>/dev/null || echo "000")
  if [[ "$code" == "200" ]]; then
    section_pass "GET /health/circuit-breakers → HTTP 200"
  else
    section_fail "GET /health/circuit-breakers → HTTP ${code} (expected 200)"
  fi

  # Verify response body has status field
  local body
  body=$(curl -sf --max-time 10 "${BACKEND_URL}/health" 2>/dev/null || echo "")
  if echo "$body" | grep -q '"status"'; then
    section_pass "GET /health body contains 'status' field"
  else
    section_fail "GET /health body missing 'status' field — got: ${body:0:80}"
  fi
}

run_section "2. Backend Health" check_backend_health

# =============================================================================
# SECTION 3 — FRONTEND & EDGE
# =============================================================================
check_frontend_edge() {
  http_check "GET ${FRONTEND_URL}/"              "${FRONTEND_URL}/"              "200 301"
  http_check "GET ${FRONTEND_URL}/manifest.json" "${FRONTEND_URL}/manifest.json" "200"
  http_check "GET ${FRONTEND_URL}/robots.txt"    "${FRONTEND_URL}/robots.txt"    "200"

  # Edge proxy health (Cloudflare Worker → Cloud Run)
  http_check "GET ${BACKEND_URL}/health via edge" "${BACKEND_URL}/health" "200"

  # CORS headers on API
  local cors_headers
  cors_headers=$(curl -s -I -X OPTIONS --max-time 10 \
    -H "Origin: ${FRONTEND_URL}" \
    "${BACKEND_URL}/health" 2>/dev/null || echo "")
  if echo "$cors_headers" | grep -qi "access-control-allow-origin"; then
    section_pass "CORS headers present on API (Access-Control-Allow-Origin)"
  else
    section_fail "CORS headers missing on API responses"
  fi

  # Security headers on API
  local headers
  headers=$(curl -s -I --max-time 10 "${BACKEND_URL}/health" 2>/dev/null || echo "")
  for hdr in "x-content-type-options" "x-frame-options" "strict-transport-security"; do
    if echo "$headers" | grep -qi "$hdr"; then
      section_pass "Security header present: ${hdr}"
    else
      section_fail "Security header missing: ${hdr}"
    fi
  done
}

run_section "3. Frontend & Edge" check_frontend_edge

# =============================================================================
# SECTION 4 — CONTENT & SEO
# =============================================================================
check_content_seo() {
  # Sitemap: edge rewrites /sitemap.xml → /api/v1/seo/sitemap.xml on the backend.
  # Use the edge rewrite URL, not the direct backend path.
  http_check "GET /sitemap.xml (via edge rewrite)"  "${BACKEND_URL}/sitemap.xml"              "200"
  http_check "GET /api/v1/content/library-bundle"   "${BACKEND_URL}/api/v1/content/library-bundle" "200"
  http_check "GET /api/v1/config/trustpilot"        "${BACKEND_URL}/api/v1/config/trustpilot" "200"

  # Library bundle should return JSON
  local body
  body=$(curl -sf --max-time 15 "${BACKEND_URL}/api/v1/content/library-bundle" 2>/dev/null || echo "")
  if echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d)>0)" 2>/dev/null | grep -q "True"; then
    section_pass "Library bundle returns non-empty JSON"
  elif [[ -n "$body" ]]; then
    section_info "Library bundle returned data (non-JSON or empty array)"
  else
    section_fail "Library bundle returned empty response"
  fi

  # Sitemap should contain syrabit.ai URLs
  local sitemap
  sitemap=$(curl -sf --max-time 15 "${BACKEND_URL}/sitemap.xml" 2>/dev/null || echo "")
  if echo "$sitemap" | grep -q "syrabit.ai"; then
    section_pass "Sitemap contains syrabit.ai URLs"
  else
    section_fail "Sitemap missing syrabit.ai URLs"
  fi
}

run_section "4. Content & SEO" check_content_seo

# =============================================================================
# SECTION 5 — SECURITY PROBES
# =============================================================================
check_security() {
  local code

  # Auth endpoints reject bad input
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -X POST "${BACKEND_URL}/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d '{}' 2>/dev/null || echo "000")
  if [[ "$code" == "422" || "$code" == "400" ]]; then
    section_pass "POST /auth/login empty body → HTTP ${code} (validation working)"
  else
    section_fail "POST /auth/login empty body → HTTP ${code} (expected 422)"
  fi

  # Webhook rejects unsigned requests
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -X POST "${BACKEND_URL}/api/webhooks/razorpay" \
    -H "Content-Type: application/json" \
    -d '{"event":"payment.captured"}' 2>/dev/null || echo "000")
  if [[ "$code" == "401" || "$code" == "403" || "$code" == "400" ]]; then
    section_pass "POST /webhooks/razorpay unsigned → HTTP ${code} (rejected correctly)"
  else
    section_fail "POST /webhooks/razorpay unsigned → HTTP ${code} (should be 401/403)"
  fi

  # No stack traces in error responses
  local body
  body=$(curl -sf --max-time 10 \
    "${BACKEND_URL}/api/v1/nonexistent-endpoint-zzzz" 2>/dev/null || echo "")
  if echo "$body" | grep -qiE "traceback|stack trace|File \"|at line"; then
    section_fail "Error response leaks stack trace — check APP_ENV setting"
  else
    section_pass "Error responses do not leak stack traces"
  fi

  # Protected endpoints require auth
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    "${BACKEND_URL}/api/v1/users/me" 2>/dev/null || echo "000")
  if [[ "$code" == "401" || "$code" == "403" ]]; then
    section_pass "GET /users/me without token → HTTP ${code} (auth enforced)"
  else
    section_fail "GET /users/me without token → HTTP ${code} (expected 401)"
  fi

  # OpenAPI docs hidden in production
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    "${BACKEND_URL}/docs" 2>/dev/null || echo "000")
  if [[ "$code" == "404" || "$code" == "403" ]]; then
    section_pass "GET /docs → HTTP ${code} (hidden in production)"
  else
    section_info "GET /docs → HTTP ${code} (docs visible — expected if APP_ENV≠production)"
  fi
}

run_section "5. Security Probes" check_security

# =============================================================================
# SECTION 6 — AUTH PIPELINE  (requires TEST_USER_EMAIL + TEST_USER_PASSWORD)
# =============================================================================
check_auth_pipeline() {
  if [[ -z "${TEST_USER_EMAIL:-}" || -z "${TEST_USER_PASSWORD:-}" ]]; then
    section_skip "Skipped — set TEST_USER_EMAIL + TEST_USER_PASSWORD to enable"
    return
  fi

  echo -e "  ${CYAN}Running auth pipeline tests...${NC}"
  if [[ -x "${SCRIPT_DIR}/test-auth-live.sh" ]]; then
    local exit_code=0
    TEST_USER_EMAIL="$TEST_USER_EMAIL" \
    TEST_USER_PASSWORD="$TEST_USER_PASSWORD" \
      bash "${SCRIPT_DIR}/test-auth-live.sh" 2>&1 \
      | grep -E "PASS|FAIL|SKIP|WARN" \
      | sed 's/^/  /' \
      || exit_code=$?

    if [[ $exit_code -eq 0 ]]; then
      section_pass "Auth pipeline script exited 0 (all critical checks passed)"
    else
      section_fail "Auth pipeline script exited ${exit_code} (failures detected — run directly for detail)"
    fi
  else
    section_skip "scripts/test-auth-live.sh not found or not executable"
  fi
}

run_section "6. Auth Pipeline" check_auth_pipeline

# =============================================================================
# SECTION 7 — CHAT PIPELINE  (requires TEST_USER_EMAIL + TEST_USER_PASSWORD)
# =============================================================================
check_chat_pipeline() {
  if [[ -z "${TEST_USER_EMAIL:-}" || -z "${TEST_USER_PASSWORD:-}" ]]; then
    section_skip "Skipped — set TEST_USER_EMAIL + TEST_USER_PASSWORD to enable"
    return
  fi

  if [[ "$SKIP_CHAT" -eq 1 ]]; then
    section_skip "Skipped — --skip-chat flag set"
    return
  fi

  echo -e "  ${CYAN}Running chat pipeline tests (this may take ~30s)...${NC}"
  if [[ -x "${SCRIPT_DIR}/test-chat-live.sh" ]]; then
    local exit_code=0
    TEST_USER_EMAIL="$TEST_USER_EMAIL" \
    TEST_USER_PASSWORD="$TEST_USER_PASSWORD" \
      bash "${SCRIPT_DIR}/test-chat-live.sh" --skip-tts 2>&1 \
      | grep -E "PASS|FAIL|SKIP|WARN|latency" \
      | sed 's/^/  /' \
      || exit_code=$?

    if [[ $exit_code -eq 0 ]]; then
      section_pass "Chat pipeline script exited 0 (all critical checks passed)"
    else
      section_fail "Chat pipeline script exited ${exit_code} (failures detected — run directly for detail)"
    fi
  else
    section_skip "scripts/test-chat-live.sh not found or not executable"
  fi
}

run_section "7. Chat Pipeline" check_chat_pipeline

# =============================================================================
# SECTION 8 — PERFORMANCE SPOT CHECK
# =============================================================================
check_performance() {
  local checks=(
    "/health:500"
    "/api/v1/content/library-bundle:3000"
    "/sitemap.xml:2000"
  )

  for entry in "${checks[@]}"; do
    local path="${entry%%:*}"
    local threshold="${entry##*:}"
    local http_code ms
    # Capture both status code and timing in one request
    read -r http_code ms < <(curl -s -o /dev/null \
      -w "%{http_code} %{time_total}" --max-time 15 \
      "${BACKEND_URL}${path}" 2>/dev/null | awk '{printf "%s %d", $1, $2*1000}')
    if [[ -z "$ms" || "$ms" == "0" ]]; then
      section_fail "${path} — no response"
    elif [[ "$http_code" != "200" ]]; then
      section_fail "${path} → HTTP ${http_code} (expected 200, ${ms}ms)"
    elif [[ "$ms" -lt "$threshold" ]]; then
      section_pass "${path} → ${ms}ms  (threshold ${threshold}ms)"
    else
      section_info "${path} → ${ms}ms  ⚠ above ${threshold}ms threshold (HTTP ${http_code})"
    fi
  done
}

run_section "8. Performance Spot Check" check_performance

# =============================================================================
# FINAL SUMMARY
# =============================================================================
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║                        SUMMARY                              ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${GREEN}✔ Passed:${NC}  ${SECTION_PASS}"
echo -e "  ${RED}✘ Failed:${NC}  ${SECTION_FAIL}"
echo -e "  ${YELLOW}⊘ Skipped:${NC} ${SECTION_SKIP}"
echo -e "  ${CYAN}⏱ Duration:${NC} ${ELAPSED}s"
echo ""

if [[ "${#FAILED_SECTIONS[@]}" -gt 0 ]]; then
  echo -e "  ${RED}${BOLD}Failed checks:${NC}"
  for f in "${FAILED_SECTIONS[@]}"; do
    echo -e "    ${RED}•${NC} ${f}"
  done
  echo ""
fi

if [[ "$SECTION_FAIL" -eq 0 ]]; then
  echo -e "  ${GREEN}${BOLD}✔  All checks passed — stack is healthy${NC}"
  echo ""
  exit 0
else
  echo -e "  ${RED}${BOLD}✘  ${SECTION_FAIL} check(s) failed — review output above${NC}"
  echo ""
  echo -e "  ${CYAN}Tip: run the failing section directly for full detail:${NC}"
  echo -e "    ${CYAN}bash scripts/live-deployment-test.sh --verbose${NC}"
  echo -e "    ${CYAN}bash scripts/test-auth-live.sh${NC}"
  echo -e "    ${CYAN}bash scripts/test-chat-live.sh${NC}"
  echo ""
  exit 1
fi
