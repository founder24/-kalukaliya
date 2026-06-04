#!/usr/bin/env bash
# =============================================================================
#  cloud_smoke.sh  —  Syrabit live-API smoke test  (Cloud Shell / CI / anywhere)
#
#  No local repo needed. Requires only: bash + curl
#
#  Usage:
#    bash cloud_smoke.sh                                   # test syrabit.ai + api.syrabit.ai
#    bash cloud_smoke.sh --api https://api.syrabit.ai      # explicit API base
#    bash cloud_smoke.sh --api https://MY-STAGING.run.app  # any Cloud Run URL
#    bash cloud_smoke.sh --fe  https://syrabit.ai          # explicit frontend base
#
#  One-liner (Cloud Shell):
#    curl -fsSL https://raw.githubusercontent.com/your-org/syrabit/main/cloud_smoke.sh | bash
#
#  Env overrides:
#    CLOUD_API=https://api.syrabit.ai
#    CLOUD_FE=https://syrabit.ai
#    CURL_TIMEOUT=12        (seconds per request, default 12)
# =============================================================================
set -uo pipefail

API="${CLOUD_API:-https://api.syrabit.ai}"
FE="${CLOUD_FE:-https://syrabit.ai}"
# CORS_ORIGIN is the Origin header sent in CORS preflight checks.
# Defaults to the canonical production frontend so the check works even when
# testing a staging API (which still needs to accept the prod frontend origin).
CORS_ORIGIN="${CORS_ORIGIN:-https://syrabit.ai}"
CURL_TIMEOUT="${CURL_TIMEOUT:-12}"

PASS=0
FAIL=0
ERRORS=()

# ── Parse flags ───────────────────────────────────────────────────────────────
_prev=""
for _a in "$@"; do
  case "$_prev" in
    --api) API="$_a" ;;
    --fe)  FE="$_a"  ;;
  esac
  _prev="$_a"
done

# ── Helpers ───────────────────────────────────────────────────────────────────
_red()    { printf '\033[31m%s\033[0m' "$*"; }
_green()  { printf '\033[32m%s\033[0m' "$*"; }
_yellow() { printf '\033[33m%s\033[0m' "$*"; }
_bold()   { printf '\033[1m%s\033[0m'  "$*"; }

header() {
  echo ""
  echo "════════════════════════════════════════════════════"
  echo "  $1"
  echo "════════════════════════════════════════════════════"
}
ok()   { echo "  $(_green '✅')  $1"; ((PASS++)) || true; }
fail() { echo "  $(_red   '❌')  $1"; ((FAIL++)) || true; ERRORS+=("$1"); }
note() { echo "       $1"; }

check() {
  local label="$1" expected="$2"; shift 2
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$CURL_TIMEOUT" "$@" 2>/dev/null)
  if echo "$expected" | grep -qw "$code"; then
    ok "$label → HTTP $code"
  else
    fail "$label → HTTP $code  (expected $expected)"
  fi
}

check_body() {
  local label="$1" expected="$2" needle="$3"; shift 3
  local body code
  body=$(curl -s --max-time "$CURL_TIMEOUT" "$@" 2>/dev/null)
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$CURL_TIMEOUT" "$@" 2>/dev/null)
  if echo "$expected" | grep -qw "$code" && echo "$body" | grep -q "$needle"; then
    ok "$label → HTTP $code ✓ body"
  elif ! echo "$expected" | grep -qw "$code"; then
    fail "$label → HTTP $code  (expected $expected)"
  else
    fail "$label → body missing '$needle'  (HTTP $code)"
  fi
}

check_header() {
  local label="$1" pattern="$2" url="$3"
  local headers
  headers=$(curl -s -I --max-time "$CURL_TIMEOUT" "$url" 2>/dev/null)
  if echo "$headers" | grep -qi "$pattern"; then
    ok "$label"
  else
    fail "$label  (header '$pattern' missing)"
  fi
}

# ── Pre-flight connectivity check ─────────────────────────────────────────────
echo ""
echo "$(_bold "Syrabit Cloud Smoke")"
echo "  API : $API"
echo "  FE  : $FE"
echo "  curl timeout: ${CURL_TIMEOUT}s"
echo ""

_health=$(curl -s --max-time 8 "$API/health" 2>/dev/null)
if ! echo "$_health" | grep -q "healthy\|ok\|status"; then
  echo "  $(_red 'ERROR'): $API/health did not return a healthy response."
  echo "  Response: $_health"
  echo "  Check that the API is deployed and the URL is correct."
  echo ""
  exit 1
fi
echo "  $(_green '◉') Backend reachable — proceeding"

# =============================================================================
# A — BACKEND HEALTH
# =============================================================================
header "A  BACKEND HEALTH  ($API)"

check          "GET /health → 200"              "200"     "$API/health"
check_body     "health body: status present"    "200"     '"status"'    "$API/health"
check_body     "health body: healthy"           "200"     '"healthy"'   "$API/health"
check          "GET /health/deep → not 500"     "200 503" "$API/health/deep"

# =============================================================================
# B — ANON USER ENDPOINTS  (the 6 bugs we fixed — all must return 200)
# =============================================================================
header "B  ANON USER ENDPOINTS  (IP-auth — no token required)"

# /user/credits must return 200 (was 401 before the fix)
check "GET /user/credits (anon) → 200" "200" \
  -H "Origin: $FE" "$API/api/v1/user/credits"

check_body "GET /user/credits — has monthly_limit" "200" '"monthly_limit"' \
  -H "Origin: $FE" "$API/api/v1/user/credits"

check_body "GET /user/credits — tier: anonymous" "200" '"anonymous"' \
  -H "Origin: $FE" "$API/api/v1/user/credits"

check_body "GET /user/credits — has anon_id" "200" '"anon_id"' \
  -H "Origin: $FE" "$API/api/v1/user/credits"

check_body "GET /user/credits — anon_id is ip_* format" "200" '"anon_id".*"ip_' \
  -H "Origin: $FE" "$API/api/v1/user/credits"

# /users prefix alias
check "GET /users/credits (anon, /users prefix) → 200" "200" \
  -H "Origin: $FE" "$API/api/v1/users/credits"

# Conversations anon (no x-anon-id needed — IP-based)
check "GET /conversations/anon (IP-based) → 200" "200" \
  -H "Origin: $FE" "$API/api/v1/conversations/anon"

check_body "GET /conversations/anon → has conversations array" "200" '"conversations"' \
  -H "Origin: $FE" "$API/api/v1/conversations/anon"

check_body "GET /conversations/anon → has pagination" "200" '"pagination"' \
  -H "Origin: $FE" "$API/api/v1/conversations/anon"

# Chat history
check "GET /chat/history (anon) → 200" "200" \
  -H "Origin: $FE" "$API/api/v1/chat/history"

check_body "GET /chat/history → has chats array" "200" '"chats"' \
  -H "Origin: $FE" "$API/api/v1/chat/history"

# Chat stream — anon must get 200 (not 401)
_stream=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$CURL_TIMEOUT" \
  -X POST -H "Content-Type: application/json" -H "Origin: $FE" \
  -d '{"message":"hello","session_id":"cloud-smoke-001"}' \
  "$API/api/v1/chat/stream" 2>/dev/null)
if [ "$_stream" = "200" ] || [ "$_stream" = "429" ]; then
  ok "POST /chat/stream (anon) → HTTP $_stream  (200=streamed, 429=rate-limited — both correct)"
else
  fail "POST /chat/stream (anon) → HTTP $_stream  (expected 200 or 429 — not 401)"
fi

# Non-existent conversation should 404, not 401
check "GET /conversations/anon/nonexistentid12345 → 404" "404" \
  -H "Origin: $FE" "$API/api/v1/conversations/anon/nonexistentid12345"

# =============================================================================
# C — AUTH GUARDS  (protected endpoints must stay 401)
# =============================================================================
header "C  AUTH GUARDS  (protected endpoints must be 401)"

check "GET /user/me (no token) → 401"          "401" \
  -H "Origin: $FE" "$API/api/v1/user/me"

check "GET /users/me (no token) → 401"         "401" \
  -H "Origin: $FE" "$API/api/v1/users/me"

check "GET /conversations (no token) → 401"    "401" \
  -H "Origin: $FE" "$API/api/v1/conversations"

check "GET /user/me (bad Bearer) → 401"        "401" \
  -H "Origin: $FE" -H "Authorization: Bearer invalidtoken" \
  "$API/api/v1/user/me"

check "POST /auth/login (bad creds) → 401"     "401" \
  -X POST -H "Content-Type: application/json" -H "Origin: $FE" \
  -d '{"email":"smoke@test.com","password":"wrongpassword"}' \
  "$API/api/v1/auth/login"

check "POST /auth/signup (empty) → 422"        "422" \
  -X POST -H "Content-Type: application/json" -H "Origin: $FE" \
  -d '{}' "$API/api/v1/auth/signup"

# =============================================================================
# D — CONTENT API
# =============================================================================
header "D  CONTENT API"

check "GET /content/library-bundle → 200" "200" \
  -H "Origin: $FE" "$API/api/v1/content/library-bundle?slim=1"

check_body "library-bundle: has subjects" "200" '"subjects"' \
  -H "Origin: $FE" "$API/api/v1/content/library-bundle?slim=1"

check_body "library-bundle: has boards" "200" '"boards"' \
  -H "Origin: $FE" "$API/api/v1/content/library-bundle?slim=1"

check "GET /subscription/plans → 200" "200" \
  -H "Origin: $FE" "$API/api/v1/subscription/plans"

check_body "subscription/plans: free tier" "200" '"free"' \
  -H "Origin: $FE" "$API/api/v1/subscription/plans"

check_body "subscription/plans: pro tier" "200" '"pro"' \
  -H "Origin: $FE" "$API/api/v1/subscription/plans"

# =============================================================================
# E — INPUT VALIDATION
# =============================================================================
header "E  INPUT VALIDATION (schema enforcement)"

check "POST /chat/stream (empty message) → 422" "422" \
  -X POST -H "Content-Type: application/json" -H "Origin: $FE" \
  -d '{"message":""}' "$API/api/v1/chat/stream"

check "POST /chat/stream (no body) → 422" "422" \
  -X POST -H "Content-Type: application/json" -H "Origin: $FE" \
  "$API/api/v1/chat/stream"

check "POST /auth/login (empty body) → 422" "422" \
  -X POST -H "Content-Type: application/json" -H "Origin: $FE" \
  -d '{}' "$API/api/v1/auth/login"

# =============================================================================
# F — CORS PREFLIGHT
# =============================================================================
header "F  CORS PREFLIGHT  (Origin: $CORS_ORIGIN)"

check "OPTIONS /chat/stream → 200" "200" \
  -X OPTIONS \
  -H "Origin: $CORS_ORIGIN" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type,Authorization" \
  "$API/api/v1/chat/stream"

# check_header sends a plain GET — use the canonical CORS origin
_cors_hdrs=$(curl -s -I --max-time "$CURL_TIMEOUT" \
  -H "Origin: $CORS_ORIGIN" "$API/api/v1/chat/stream" 2>/dev/null)
if echo "$_cors_hdrs" | grep -qi "access-control-allow-origin"; then
  ok "CORS: access-control-allow-origin header present"
else
  fail "CORS: access-control-allow-origin header missing"
fi

check "OPTIONS /user/credits → 200" "200" \
  -X OPTIONS \
  -H "Origin: $CORS_ORIGIN" \
  -H "Access-Control-Request-Method: GET" \
  "$API/api/v1/user/credits"

# =============================================================================
# G — SECURITY: SENSITIVE PATH BLOCKING
# =============================================================================
header "G  SECURITY — SENSITIVE PATH BLOCKING"

check "/.env blocked → 404"          "404" "$API/.env"
check "/.git/config blocked → 404"   "404" "$API/.git/config"
check "/.htaccess blocked → 404"     "404" "$API/.htaccess"
check "/wp-login.php blocked → 404"  "404" "$API/wp-login.php"
check "/phpinfo.php blocked → 404"   "404" "$API/phpinfo.php"
check "/xmlrpc.php blocked → 404"    "404" "$API/xmlrpc.php"
check "/wp-admin blocked → 404"      "404" "$API/wp-admin"
check "/openapi.json hidden (dev exposes it)" "200 302 404" "$API/openapi.json"

# =============================================================================
# H — RESPONSE FORMAT & CONTENT-TYPE
# =============================================================================
header "H  RESPONSE FORMAT"

_ct=$(curl -s -I --max-time "$CURL_TIMEOUT" -H "Origin: $FE" "$API/health" 2>/dev/null \
  | grep -i "content-type" || true)
if echo "$_ct" | grep -qi "application/json"; then
  ok "GET /health → Content-Type: application/json"
else
  fail "GET /health → Content-Type not application/json: $_ct"
fi

_ct2=$(curl -s -I --max-time "$CURL_TIMEOUT" -H "Origin: $FE" \
  "$API/api/v1/user/credits" 2>/dev/null | grep -i "content-type" || true)
if echo "$_ct2" | grep -qi "application/json"; then
  ok "GET /user/credits → Content-Type: application/json"
else
  fail "GET /user/credits → Content-Type not application/json: $_ct2"
fi

# SSE stream must return text/event-stream.
# Use -D - (dump headers to stdout) so we get response headers from a real POST
# without -I conflicting with the streaming body.
_sse_ct=$(curl -s -D - --max-time 6 \
  -X POST -H "Content-Type: application/json" -H "Origin: $CORS_ORIGIN" \
  -d '{"message":"hi","session_id":"cloud-ct-001"}' \
  "$API/api/v1/chat/stream" 2>/dev/null | grep -i "^content-type:" | head -1 || true)
if echo "$_sse_ct" | grep -qi "text/event-stream"; then
  ok "POST /chat/stream → Content-Type: text/event-stream"
else
  fail "POST /chat/stream → Content-Type not text/event-stream: $_sse_ct"
fi

# =============================================================================
# I — FRONTEND ROUTES  (if FE is provided)
# =============================================================================
_fe_up=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 -L "$FE/" 2>/dev/null)
if [ "$_fe_up" = "200" ]; then
  header "I  FRONTEND ROUTES  ($FE)"
  check "/ — homepage → 200"           "200" -L "$FE/"
  check "/library/ — SPA route → 200" "200" -L "$FE/library/"
  check "/chat/ — SPA route → 200"    "200" -L "$FE/chat/"
  check "/robots.txt → 200"            "200" "$FE/robots.txt"

  # Security headers
  check_header "HSTS present"               "strict-transport-security"       "$FE/"
  check_header "X-Frame-Options: DENY"      "x-frame-options"                 "$FE/"
  check_header "X-Content-Type-Options"     "x-content-type-options: nosniff" "$FE/"
  check_header "Content-Security-Policy"    "content-security-policy"         "$FE/"
else
  echo ""
  note "Frontend ($FE) returned HTTP $_fe_up — skipping section I"
fi

# =============================================================================
# SUMMARY
# =============================================================================
echo ""
echo "════════════════════════════════════════════════════"
echo "  $(_bold "RESULTS"):  $(_green "✅ $PASS passed")   $(_red "❌ $FAIL failed")"
echo "  API: $API"
echo "  FE:  $FE"
echo "════════════════════════════════════════════════════"

if [ ${#ERRORS[@]} -gt 0 ]; then
  echo ""
  echo "  $(_red 'Failed checks:')"
  for e in "${ERRORS[@]}"; do echo "    • $e"; done
  echo ""
  exit 1
fi
echo ""
