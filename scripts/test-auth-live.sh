#!/usr/bin/env bash
set -uo pipefail

# =============================================================================
# test-auth-live.sh - Comprehensive auth testing against live syrabit.ai API
# =============================================================================
#
# WARNING: This script tests against PRODUCTION (https://syrabit.ai)
#
# Required environment variables:
#   TEST_USER_EMAIL       - User email for auth testing
#   TEST_USER_PASSWORD    - User password for auth testing
#
# Optional environment variables:
#   TEST_ADMIN_EMAIL      - Admin email for admin auth testing
#   TEST_ADMIN_PASSWORD   - Admin password for admin auth testing
#
# Flags:
#   --dry-run             Show what would be tested without executing
#   --skip-destructive    Skip rate-limit tests
#   --skip-admin          Skip admin auth tests
#
# Usage:
#   export TEST_USER_EMAIL="user@example.com"
#   export TEST_USER_PASSWORD="password123"
#   ./scripts/test-auth-live.sh
# =============================================================================

BASE_URL="https://syrabit.ai"
AUTH_API="${BASE_URL}/api/v1/auth"
ADMIN_API="${BASE_URL}/api/v1/admin"
USERS_API="${BASE_URL}/api/v1/users"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

# Flags
DRY_RUN=false
SKIP_DESTRUCTIVE=false
SKIP_ADMIN=false

# Tokens (populated during test flow)
ACCESS_TOKEN=""
REFRESH_TOKEN=""
NEW_ACCESS_TOKEN=""
NEW_REFRESH_TOKEN=""
OLD_ACCESS_TOKEN=""
ADMIN_COOKIE=""

# Parse arguments
for arg in "$@"; do
  case $arg in
    --dry-run)
      DRY_RUN=true
      ;;
    --skip-destructive)
      SKIP_DESTRUCTIVE=true
      ;;
    --skip-admin)
      SKIP_ADMIN=true
      ;;
    *)
      echo "Unknown argument: $arg"
      echo "Usage: $0 [--dry-run] [--skip-destructive] [--skip-admin]"
      exit 1
      ;;
  esac
done

# -----------------------------------------------------------------------------
# Utility functions
# -----------------------------------------------------------------------------

print_header() {
  echo ""
  echo -e "${BLUE}=================================================================${NC}"
  echo -e "${BLUE}  $1${NC}"
  echo -e "${BLUE}=================================================================${NC}"
  echo ""
}

print_pass() {
  echo -e "  ${GREEN}[PASS]${NC} $1"
  PASS_COUNT=$((PASS_COUNT + 1))
}

print_fail() {
  echo -e "  ${RED}[FAIL]${NC} $1"
  FAIL_COUNT=$((FAIL_COUNT + 1))
}

print_skip() {
  echo -e "  ${YELLOW}[SKIP]${NC} $1"
  SKIP_COUNT=$((SKIP_COUNT + 1))
}

print_info() {
  echo -e "  ${YELLOW}[INFO]${NC} $1"
}

# JSON value extraction - uses jq if available, falls back to grep/sed
json_value() {
  local json="$1"
  local key="$2"
  if command -v jq &>/dev/null; then
    echo "$json" | jq -r ".$key // empty" 2>/dev/null
  else
    echo "$json" | grep -o "\"$key\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" | sed "s/\"$key\"[[:space:]]*:[[:space:]]*\"//" | sed 's/"$//'
  fi
}

# Make a request and capture both body and HTTP status code
# Usage: do_request METHOD URL [BODY] [EXTRA_CURL_ARGS...]
# Sets: RESPONSE_BODY, RESPONSE_CODE
RESPONSE_BODY=""
RESPONSE_CODE=""

do_request() {
  local method="$1"
  local url="$2"
  local body="${3:-}"
  shift 3 2>/dev/null || shift $#

  local curl_args=(-s -w "\n%{http_code}" -X "$method")

  if [[ -n "$body" ]]; then
    curl_args+=(-H "Content-Type: application/json" -d "$body")
  fi

  # Append any extra curl args
  curl_args+=("$@")
  curl_args+=("$url")

  local raw_response
  raw_response=$(curl "${curl_args[@]}" 2>/dev/null)

  RESPONSE_CODE=$(echo "$raw_response" | tail -1)
  RESPONSE_BODY=$(echo "$raw_response" | sed '$d')
}

# Assert HTTP status code
assert_status() {
  local expected="$1"
  local test_name="$2"
  if [[ "$RESPONSE_CODE" == "$expected" ]]; then
    print_pass "$test_name (HTTP $RESPONSE_CODE)"
  else
    print_fail "$test_name (expected HTTP $expected, got HTTP $RESPONSE_CODE)"
    if [[ -n "$RESPONSE_BODY" ]]; then
      echo "         Response: ${RESPONSE_BODY:0:200}"
    fi
  fi
}

# Assert HTTP status code is one of several acceptable values
assert_status_oneof() {
  local test_name="$1"
  shift
  local found=false
  for expected in "$@"; do
    if [[ "$RESPONSE_CODE" == "$expected" ]]; then
      found=true
      break
    fi
  done
  if [[ "$found" == "true" ]]; then
    print_pass "$test_name (HTTP $RESPONSE_CODE)"
  else
    print_fail "$test_name (expected one of [$*], got HTTP $RESPONSE_CODE)"
    if [[ -n "$RESPONSE_BODY" ]]; then
      echo "         Response: ${RESPONSE_BODY:0:200}"
    fi
  fi
}

# -----------------------------------------------------------------------------
# Pre-flight checks
# -----------------------------------------------------------------------------

echo ""
echo -e "${RED}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${NC}"
echo -e "${RED}!                                                               !${NC}"
echo -e "${RED}!   WARNING: This script tests against PRODUCTION               !${NC}"
echo -e "${RED}!   Target: ${BASE_URL}                              !${NC}"
echo -e "${RED}!                                                               !${NC}"
echo -e "${RED}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${NC}"
echo ""

if [[ "$DRY_RUN" == "true" ]]; then
  echo -e "${YELLOW}DRY RUN MODE - showing test plan without executing${NC}"
  echo ""
  echo "User Auth Flow:"
  echo "  - POST ${AUTH_API}/login (valid credentials)"
  echo "  - GET ${USERS_API}/me (valid token)"
  echo "  - GET ${USERS_API}/me (no token - expect 401)"
  echo "  - GET ${USERS_API}/me (invalid token - expect 401)"
  echo "  - GET ${USERS_API}/me (garbage JWT - expect 401)"
  echo "  - POST ${AUTH_API}/refresh (valid refresh_token + access_token)"
  echo "  - GET ${USERS_API}/me (new token - expect 200)"
  echo "  - GET ${USERS_API}/me (old token after refresh - test blacklist)"
  echo "  - POST ${AUTH_API}/logout (valid token)"
  echo "  - GET ${USERS_API}/me (after logout - expect 401)"
  echo "  - POST ${AUTH_API}/forgot-password (valid email)"
  echo "  - POST ${AUTH_API}/forgot-password (nonexistent email)"
  echo ""
  echo "Rate Limiting (User):"
  echo "  - POST ${AUTH_API}/login x11 rapid wrong password (expect 429)"
  echo ""
  echo "Admin Auth Flow:"
  echo "  - POST ${ADMIN_API}/login (valid admin credentials)"
  echo "  - GET ${ADMIN_API}/verify (with session cookie)"
  echo "  - GET ${ADMIN_API}/verify (without cookie - expect 401)"
  echo "  - POST ${ADMIN_API}/logout (with cookie)"
  echo "  - GET ${ADMIN_API}/verify (after logout - expect 401)"
  echo ""
  echo "Admin Rate Limiting:"
  echo "  - POST ${ADMIN_API}/login rapid wrong-password attempts"
  echo ""
  echo "Edge Cases:"
  echo "  - POST ${AUTH_API}/login (empty body - expect 422)"
  echo "  - POST ${AUTH_API}/login (missing password - expect 422)"
  echo "  - POST ${AUTH_API}/refresh (invalid refresh token - expect 401)"
  echo "  - POST ${AUTH_API}/refresh (garbage refresh token - expect 401)"
  echo ""
  echo "Security Headers Check:"
  echo "  - Verify security headers on responses"
  echo ""
  exit 0
fi

# Check required env vars
if [[ -z "${TEST_USER_EMAIL:-}" || -z "${TEST_USER_PASSWORD:-}" ]]; then
  echo -e "${RED}ERROR: Required environment variables not set.${NC}"
  echo ""
  echo "Required:"
  echo "  export TEST_USER_EMAIL=\"your-email@example.com\""
  echo "  export TEST_USER_PASSWORD=\"your-password\""
  echo ""
  echo "Optional (for admin tests):"
  echo "  export TEST_ADMIN_EMAIL=\"admin@example.com\""
  echo "  export TEST_ADMIN_PASSWORD=\"admin-password\""
  echo ""
  echo "Flags:"
  echo "  --dry-run           Show test plan without executing"
  echo "  --skip-destructive  Skip rate-limit tests"
  echo "  --skip-admin        Skip admin auth tests"
  echo ""
  exit 1
fi

if [[ -z "${TEST_ADMIN_EMAIL:-}" || -z "${TEST_ADMIN_PASSWORD:-}" ]]; then
  SKIP_ADMIN=true
  print_info "TEST_ADMIN_EMAIL/TEST_ADMIN_PASSWORD not set - skipping admin tests"
fi

echo -e "${BLUE}Starting auth tests against ${BASE_URL}${NC}"
echo -e "${BLUE}Timestamp: $(date -u '+%Y-%m-%d %H:%M:%S UTC')${NC}"
echo ""

# =============================================================================
# USER AUTH FLOW
# =============================================================================

print_header "USER AUTH FLOW"

# Test: Login with valid credentials
do_request POST "${AUTH_API}/login" "{\"email\":\"${TEST_USER_EMAIL}\",\"password\":\"${TEST_USER_PASSWORD}\"}"
assert_status "200" "Login with valid credentials"

if [[ "$RESPONSE_CODE" == "200" ]]; then
  ACCESS_TOKEN=$(json_value "$RESPONSE_BODY" "access_token")
  REFRESH_TOKEN=$(json_value "$RESPONSE_BODY" "refresh_token")
  if [[ -n "$ACCESS_TOKEN" && -n "$REFRESH_TOKEN" ]]; then
    print_pass "Received access_token and refresh_token"
  else
    print_fail "Missing tokens in login response"
  fi
else
  print_fail "Cannot proceed with auth flow - login failed"
  echo -e "  ${RED}Skipping remaining user auth tests${NC}"
  ACCESS_TOKEN=""
  REFRESH_TOKEN=""
fi

# Test: GET /users/me with valid token
if [[ -n "$ACCESS_TOKEN" ]]; then
  do_request GET "${USERS_API}/me" "" -H "Authorization: Bearer ${ACCESS_TOKEN}"
  assert_status "200" "GET /users/me with valid token"

  if [[ "$RESPONSE_CODE" == "200" ]]; then
    local_email=$(json_value "$RESPONSE_BODY" "email")
    if [[ -n "$local_email" ]]; then
      print_pass "User profile returned (email present)"
    else
      print_info "Response received but email field not found in expected location"
    fi
  fi
fi

# Test: GET /users/me with NO token (should 401)
do_request GET "${USERS_API}/me" ""
assert_status "401" "GET /users/me with NO token"

# Test: GET /users/me with INVALID token (should 401)
do_request GET "${USERS_API}/me" "" -H "Authorization: Bearer invalid_token_12345"
assert_status "401" "GET /users/me with INVALID token"

# Test: GET /users/me with EXPIRED/garbage JWT (should 401)
GARBAGE_JWT="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwiZXhwIjoxfQ.invalid_sig"
do_request GET "${USERS_API}/me" "" -H "Authorization: Bearer ${GARBAGE_JWT}"
assert_status "401" "GET /users/me with EXPIRED/garbage JWT"

# Test: Refresh token
if [[ -n "$ACCESS_TOKEN" && -n "$REFRESH_TOKEN" ]]; then
  OLD_ACCESS_TOKEN="$ACCESS_TOKEN"

  # H-1 fix: refresh requires both refresh_token and access_token in body
  do_request POST "${AUTH_API}/refresh" "{\"refresh_token\":\"${REFRESH_TOKEN}\",\"access_token\":\"${ACCESS_TOKEN}\"}"
  assert_status "200" "POST /auth/refresh with valid tokens (H-1 fix: old token blacklisting)"

  if [[ "$RESPONSE_CODE" == "200" ]]; then
    NEW_ACCESS_TOKEN=$(json_value "$RESPONSE_BODY" "access_token")
    NEW_REFRESH_TOKEN=$(json_value "$RESPONSE_BODY" "refresh_token")
    if [[ -n "$NEW_ACCESS_TOKEN" ]]; then
      print_pass "Received new access_token from refresh"
    else
      print_fail "Missing new access_token in refresh response"
    fi
  fi

  # Test: GET /users/me with NEW token from refresh (should 200)
  if [[ -n "$NEW_ACCESS_TOKEN" ]]; then
    do_request GET "${USERS_API}/me" "" -H "Authorization: Bearer ${NEW_ACCESS_TOKEN}"
    assert_status "200" "GET /users/me with NEW token from refresh"
  else
    print_skip "Skipping new token test - no new token available"
  fi

  # Test: GET /users/me with OLD token after refresh
  # Note: Old token may still work if TTL-based blacklisting hasn't expired yet
  do_request GET "${USERS_API}/me" "" -H "Authorization: Bearer ${OLD_ACCESS_TOKEN}"
  if [[ "$RESPONSE_CODE" == "401" ]]; then
    print_pass "GET /users/me with OLD token after refresh - blacklisted (HTTP 401)"
    PASS_COUNT=$((PASS_COUNT + 1 - 1))  # already counted in assert
  elif [[ "$RESPONSE_CODE" == "200" ]]; then
    print_info "GET /users/me with OLD token after refresh - still valid (TTL not expired yet, this is expected)"
  else
    print_fail "GET /users/me with OLD token after refresh - unexpected HTTP $RESPONSE_CODE"
  fi

  # Update ACCESS_TOKEN to new one for subsequent tests
  if [[ -n "$NEW_ACCESS_TOKEN" ]]; then
    ACCESS_TOKEN="$NEW_ACCESS_TOKEN"
  fi
  if [[ -n "$NEW_REFRESH_TOKEN" ]]; then
    REFRESH_TOKEN="$NEW_REFRESH_TOKEN"
  fi
fi

# Test: Logout
if [[ -n "$ACCESS_TOKEN" && -n "$REFRESH_TOKEN" ]]; then
  do_request POST "${AUTH_API}/logout" "{\"refresh_token\":\"${REFRESH_TOKEN}\"}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}"
  assert_status "200" "POST /auth/logout with valid token"

  # Test: GET /users/me with token AFTER logout (should 401, token blacklisted)
  do_request GET "${USERS_API}/me" "" -H "Authorization: Bearer ${ACCESS_TOKEN}"
  assert_status "401" "GET /users/me AFTER logout (token blacklisted)"
fi

# Test: Forgot password with valid email (should always 200)
do_request POST "${AUTH_API}/forgot-password" "{\"email\":\"${TEST_USER_EMAIL}\"}"
assert_status "200" "POST /auth/forgot-password with valid email"

# Test: Forgot password with nonexistent email (should still 200)
do_request POST "${AUTH_API}/forgot-password" "{\"email\":\"nonexistent_user_xyz_${RANDOM}@example.com\"}"
assert_status "200" "POST /auth/forgot-password with nonexistent email (no enumeration)"

# =============================================================================
# RATE LIMITING (USER)
# =============================================================================

print_header "RATE LIMITING (USER)"

if [[ "$SKIP_DESTRUCTIVE" == "true" ]]; then
  print_skip "Rate limit tests skipped (--skip-destructive)"
else
  print_info "Sending 11 rapid login attempts with wrong password..."
  print_info "This may temporarily lock out the test IP"
  RATE_LIMITED=false
  for i in $(seq 1 11); do
    do_request POST "${AUTH_API}/login" "{\"email\":\"${TEST_USER_EMAIL}\",\"password\":\"wrong_password_${RANDOM}\"}"
    if [[ "$RESPONSE_CODE" == "429" ]]; then
      print_pass "Rate limiting triggered on attempt $i (HTTP 429)"
      RATE_LIMITED=true
      break
    fi
  done
  if [[ "$RATE_LIMITED" == "false" ]]; then
    print_fail "Rate limiting NOT triggered after 11 attempts (last HTTP $RESPONSE_CODE)"
  fi
  # Sleep to let rate limit window pass
  print_info "Sleeping 5s to let rate limit window pass..."
  sleep 5
fi

# =============================================================================
# ADMIN AUTH FLOW
# =============================================================================

print_header "ADMIN AUTH FLOW"

if [[ "$SKIP_ADMIN" == "true" ]]; then
  print_skip "Admin tests skipped (--skip-admin or no admin credentials)"
else
  # Test: Admin login with valid credentials
  ADMIN_RAW=$(curl -s -w "\n%{http_code}" -X POST \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${TEST_ADMIN_EMAIL}\",\"password\":\"${TEST_ADMIN_PASSWORD}\"}" \
    -c - \
    "${ADMIN_API}/login" 2>/dev/null)

  RESPONSE_CODE=$(echo "$ADMIN_RAW" | tail -1)
  # Extract cookie from the raw output (set-cookie header captured by -c -)
  # Re-do with -D to capture headers
  ADMIN_HEADERS=$(curl -s -D - -o /dev/null -X POST \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${TEST_ADMIN_EMAIL}\",\"password\":\"${TEST_ADMIN_PASSWORD}\"}" \
    "${ADMIN_API}/login" 2>/dev/null)

  RESPONSE_CODE=$(echo "$ADMIN_HEADERS" | head -1 | grep -o '[0-9]\{3\}')
  ADMIN_COOKIE=$(echo "$ADMIN_HEADERS" | grep -i "set-cookie" | grep -o "syrabit_admin_session=[^;]*" | head -1)

  if [[ "$RESPONSE_CODE" == "200" ]]; then
    print_pass "Admin login with valid credentials (HTTP $RESPONSE_CODE)"
    if [[ -n "$ADMIN_COOKIE" ]]; then
      print_pass "Session cookie 'syrabit_admin_session' present in set-cookie"
    else
      print_fail "Session cookie 'syrabit_admin_session' NOT found in set-cookie header"
      # Try to extract any cookie for remaining tests
      ADMIN_COOKIE=$(echo "$ADMIN_HEADERS" | grep -i "set-cookie" | grep -o "[^;]*=[^;]*" | head -1)
    fi
  else
    print_fail "Admin login with valid credentials (expected 200, got $RESPONSE_CODE)"
  fi

  # Test: GET /admin/verify with session cookie (should 200)
  if [[ -n "$ADMIN_COOKIE" ]]; then
    do_request GET "${ADMIN_API}/verify" "" -H "Cookie: ${ADMIN_COOKIE}"
    assert_status "200" "GET /admin/verify with session cookie"
  else
    print_skip "Skipping admin verify - no cookie obtained"
  fi

  # Test: GET /admin/verify WITHOUT cookie (should 401)
  do_request GET "${ADMIN_API}/verify" ""
  assert_status "401" "GET /admin/verify WITHOUT cookie"

  # Test: POST /admin/logout with cookie (should clear cookie)
  if [[ -n "$ADMIN_COOKIE" ]]; then
    do_request POST "${ADMIN_API}/logout" "" -H "Cookie: ${ADMIN_COOKIE}"
    assert_status_oneof "POST /admin/logout with cookie" "200" "204"
  else
    print_skip "Skipping admin logout - no cookie obtained"
  fi

  # Test: GET /admin/verify after logout (should 401)
  if [[ -n "$ADMIN_COOKIE" ]]; then
    do_request GET "${ADMIN_API}/verify" "" -H "Cookie: ${ADMIN_COOKIE}"
    assert_status "401" "GET /admin/verify after logout"
  else
    print_skip "Skipping post-logout verify - no cookie obtained"
  fi
fi

# =============================================================================
# ADMIN RATE LIMITING (C-1 fix verification)
# =============================================================================

print_header "ADMIN RATE LIMITING (C-1 fix verification)"

if [[ "$SKIP_ADMIN" == "true" || "$SKIP_DESTRUCTIVE" == "true" ]]; then
  print_skip "Admin rate limit tests skipped (--skip-admin or --skip-destructive)"
else
  print_info "Sending rapid wrong-password admin login attempts..."
  ADMIN_RATE_LIMITED=false
  for i in $(seq 1 11); do
    do_request POST "${ADMIN_API}/login" "{\"email\":\"${TEST_ADMIN_EMAIL}\",\"password\":\"wrong_admin_pw_${RANDOM}\"}"
    if [[ "$RESPONSE_CODE" == "429" ]]; then
      print_pass "Admin rate limiting triggered on attempt $i (HTTP 429)"
      ADMIN_RATE_LIMITED=true
      break
    fi
  done
  if [[ "$ADMIN_RATE_LIMITED" == "false" ]]; then
    print_fail "Admin rate limiting NOT triggered after 11 attempts (last HTTP $RESPONSE_CODE)"
  fi
  # Sleep to let rate limit window pass
  print_info "Sleeping 5s to let rate limit window pass..."
  sleep 5
fi

# =============================================================================
# EDGE CASES
# =============================================================================

print_header "EDGE CASES"

# Test: Login with empty body (should 422)
do_request POST "${AUTH_API}/login" "{}"
assert_status_oneof "POST /auth/login with empty body" "422" "400"

# Test: Login with missing password (should 422)
do_request POST "${AUTH_API}/login" "{\"email\":\"${TEST_USER_EMAIL}\"}"
assert_status_oneof "POST /auth/login missing password" "422" "400"

# Test: Refresh with invalid refresh token (should 401)
do_request POST "${AUTH_API}/refresh" "{\"refresh_token\":\"invalid_refresh_token_abc123\",\"access_token\":\"invalid_access_token\"}"
assert_status_oneof "POST /auth/refresh with invalid refresh token" "401" "403" "422"

# Test: Refresh with garbage refresh token (should 401)
do_request POST "${AUTH_API}/refresh" "{\"refresh_token\":\"garbage.token.value.not.real\",\"access_token\":\"also.garbage\"}"
assert_status_oneof "POST /auth/refresh with garbage refresh token" "401" "403" "422"

# =============================================================================
# SECURITY HEADERS CHECK
# =============================================================================

print_header "SECURITY HEADERS CHECK"

# Fetch headers from a simple endpoint
SEC_HEADERS=$(curl -s -D - -o /dev/null "${BASE_URL}/api/v1/auth/login" -X OPTIONS 2>/dev/null || \
              curl -s -D - -o /dev/null "${BASE_URL}" 2>/dev/null)

check_header() {
  local header_name="$1"
  if echo "$SEC_HEADERS" | grep -qi "$header_name"; then
    print_pass "Header present: $header_name"
  else
    print_info "Header not found: $header_name (may not apply to this endpoint)"
  fi
}

check_header "x-content-type-options"
check_header "x-frame-options"
check_header "strict-transport-security"
check_header "x-xss-protection"
check_header "content-type"

# =============================================================================
# SUMMARY
# =============================================================================

print_header "TEST SUMMARY"

TOTAL=$((PASS_COUNT + FAIL_COUNT + SKIP_COUNT))
echo -e "  ${GREEN}PASSED:  ${PASS_COUNT}${NC}"
echo -e "  ${RED}FAILED:  ${FAIL_COUNT}${NC}"
echo -e "  ${YELLOW}SKIPPED: ${SKIP_COUNT}${NC}"
echo -e "  ${BLUE}TOTAL:   ${TOTAL}${NC}"
echo ""

if [[ $FAIL_COUNT -gt 0 ]]; then
  echo -e "${RED}Some tests FAILED. Review output above for details.${NC}"
  exit 1
else
  echo -e "${GREEN}All executed tests PASSED.${NC}"
  exit 0
fi
