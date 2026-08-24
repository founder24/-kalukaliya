#!/usr/bin/env bash
# =============================================================================
# Syrabit Full-Stack Smoke Test
# Usage:  bash scripts/fullstack-smoke-test.sh [--base-url https://api.syrabit.ai] [--chat-smoke]
# =============================================================================
set -euo pipefail

BASE_URL="${BASE_URL:-https://api.syrabit.ai}"
FRONTEND_URL="${FRONTEND_URL:-https://syrabit.ai}"
API_WORKER_URL="${API_WORKER_URL:-https://syrabit-api-prod.axomxplain.workers.dev}"

PASS=0
FAIL=0
SKIP=0
CHAT_SMOKE=false

# ── Colours ────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m';  BOLD='\033[1m';   RESET='\033[0m'

# ── Helpers ────────────────────────────────────────────────────────────────
pass() { echo -e "  ${GREEN}✓${RESET}  $1"; PASS=$((PASS+1)); }
fail() { echo -e "  ${RED}✗${RESET}  $1"; FAIL=$((FAIL+1)); }
skip() { echo -e "  ${YELLOW}–${RESET}  $1 (skipped)"; SKIP=$((SKIP+1)); }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $1"; }
header() { echo -e "\n${CYAN}${BOLD}── $1 ──${RESET}"; }

http_status()        { curl -s  -o /dev/null -w "%{http_code}" --max-time 15 "$@" || echo "000"; }
http_status_follow() { curl -sL -o /dev/null -w "%{http_code}" --max-time 15 "$@" || echo "000"; }
http_body()   { curl -s --max-time 30 "$@" || echo "{}"; }
http_headers(){ curl -sI --max-time 15 "$@" || echo ""; }

json_field() {
  # json_field <json_string> <python_expr_on_d>
  echo "$1" | python3 -c "import json,sys; d=json.load(sys.stdin); print($2)" 2>/dev/null
}

assert_eq()  { [ "$1" = "$2" ] && pass "$3" || fail "$3 (got '$1', want '$2')"; }
assert_gte() { python3 -c "import sys; sys.exit(0 if int('$1')>=$2 else 1)" 2>/dev/null \
               && pass "$3 ($1 >= $2)" || fail "$3 (got $1, want >= $2)"; }
assert_contains() { echo "$1" | grep -qi "$2" && pass "$3" || fail "$3 (missing '$2')"; }
assert_nonempty() { [ -n "$1" ] && pass "$2" || fail "$2 (empty)"; }

# ── Parse args ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url)   BASE_URL="$2";     shift 2 ;;
    --direct-url) DIRECT_URL="$2";  shift 2 ;;
    --frontend)   FRONTEND_URL="$2"; shift 2 ;;
    --chat-smoke) CHAT_SMOKE=true;   shift ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

echo -e "${BOLD}Syrabit Full-Stack Smoke Test${RESET}"
echo    "  Base URL   : $BASE_URL"
echo    "  API Worker : $API_WORKER_URL"
echo    "  Frontend   : $FRONTEND_URL"

# ═══════════════════════════════════════════════════════════════════════════
# 1. DNS
# ═══════════════════════════════════════════════════════════════════════════
header "1. DNS Resolution"

if ! command -v dig &>/dev/null; then
  skip "api.syrabit.ai resolves to Cloudflare IP (dig not in PATH — install dnsutils)"
  skip "syrabit.ai DNS resolves (dig not in PATH)"
else
  CF_IPS=$(dig api.syrabit.ai +short 2>/dev/null || echo "")
  if echo "$CF_IPS" | grep -qE "^(104\.|172\.)"; then
    pass "api.syrabit.ai resolves to Cloudflare IP"
    echo "     IPs: $(echo $CF_IPS | tr '\n' ' ')"
  else
    fail "api.syrabit.ai not resolving to Cloudflare (got: $CF_IPS)"
  fi

  FRONT_IPS=$(dig syrabit.ai +short 2>/dev/null || echo "")
  assert_nonempty "$FRONT_IPS" "syrabit.ai DNS resolves"
fi

# ═══════════════════════════════════════════════════════════════════════════
# 2. Cloudflare Worker
# ═══════════════════════════════════════════════════════════════════════════
header "2. Cloudflare Worker"

CF_RAY=$(http_headers "$BASE_URL/health" | grep -i "^cf-ray:" || echo "")
assert_nonempty "$CF_RAY" "cf-ray header present (Worker is proxying)"

SERVER=$(http_headers "$BASE_URL/health" | grep -i "^server:" | tr -d '\r\n' || echo "")
assert_contains "$SERVER" "cloudflare" "server: cloudflare header"

# ═══════════════════════════════════════════════════════════════════════════
# 3. Native API Worker / D1 Health
# ═══════════════════════════════════════════════════════════════════════════
header "3. Native API Worker / D1 Health ($API_WORKER_URL)"

STATUS=$(http_status "$API_WORKER_URL/health")
assert_eq "$STATUS" "200" "GET API Worker /health → 200"

BODY=$(http_body "$API_WORKER_URL/health")
RUNTIME=$(json_field "$BODY" "d.get('runtime','')")
assert_eq "$RUNTIME" "cloudflare-workers" "API Worker runtime is Cloudflare Workers"

D1_STATUS=$(json_field "$BODY" "d.get('components',{}).get('d1','')")
assert_eq "$D1_STATUS" "healthy" "API Worker D1 component is healthy"

# ═══════════════════════════════════════════════════════════════════════════
# 4. Legacy Cloud Run verification
# ═══════════════════════════════════════════════════════════════════════════
header "4. Legacy Cloud Run verification"
skip "Cloud Run is validated only after a successful backend rollout; native releases do not require it"

# ═══════════════════════════════════════════════════════════════════════════
# 5. Legacy MongoDB verification
# ═══════════════════════════════════════════════════════════════════════════
header "5. Legacy MongoDB verification"
skip "Native API health verifies D1; MongoDB is retained only for the Cloud Run fallback"

# ═══════════════════════════════════════════════════════════════════════════
# 5b. Legacy vector index verification
# ═══════════════════════════════════════════════════════════════════════════
header "5b. Legacy vector index verification"
skip "Native RAG and Vectorize coverage runs in the Cloudflare cutover validation"

# ═══════════════════════════════════════════════════════════════════════════
# 6. Library Bundle (core content test)
# ═══════════════════════════════════════════════════════════════════════════
header "6. Library Bundle"

LIB_STATUS=$(http_status "$BASE_URL/api/v1/content/library-bundle")
assert_eq "$LIB_STATUS" "200" "GET /api/v1/content/library-bundle → 200"

LIB_BODY=$(http_body "$BASE_URL/api/v1/content/library-bundle")
BOARDS=$(json_field   "$LIB_BODY" "len(d.get('boards',[]))")
SUBJECTS=$(json_field "$LIB_BODY" "len(d.get('subjects',[]))")
CHAPTERS=$(json_field "$LIB_BODY" "len(d.get('chapters',[]))")

assert_gte "$BOARDS"   1   "boards count"
assert_gte "$SUBJECTS" 50  "subjects count"
# The active public catalogue has 174 chapters. Keep a meaningful floor that
# catches an accidental content rollback without treating normal curation as an
# outage.
assert_gte "$CHAPTERS" 150 "chapters count"

echo "     boards=$BOARDS  subjects=$SUBJECTS  chapters=$CHAPTERS"

# Pick first available subject slug for downstream tests
FIRST_BOARD=$(json_field   "$LIB_BODY" "d['boards'][0]['slug']"   2>/dev/null || echo "")
FIRST_SUBJECT=$(json_field "$LIB_BODY" "d['subjects'][0]['slug']" 2>/dev/null || echo "")
FIRST_CLASS=$(json_field   "$LIB_BODY" "d['subjects'][0].get('class_slug','class-9')" 2>/dev/null || echo "class-9")

# ═══════════════════════════════════════════════════════════════════════════
# 7. Content Endpoints
# ═══════════════════════════════════════════════════════════════════════════
header "7. Content Endpoints"

# CMS posts
CMS_STATUS=$(http_status "$BASE_URL/api/v1/content/cms/posts")
assert_eq "$CMS_STATUS" "200" "GET /api/v1/content/cms/posts → 200"

# CMS library
CMS_LIB_STATUS=$(http_status "$BASE_URL/api/v1/content/cms-library")
assert_eq "$CMS_LIB_STATUS" "200" "GET /api/v1/content/cms-library → 200"

# Question papers
QP_STATUS=$(http_status "$BASE_URL/api/v1/content/question-papers")
assert_eq "$QP_STATUS" "200" "GET /api/v1/content/question-papers → 200"

# Subject resolve (if we got slugs)
if [ -n "$FIRST_BOARD" ] && [ -n "$FIRST_SUBJECT" ]; then
  RESOLVE_STATUS=$(http_status "$BASE_URL/api/v1/content/resolve-subject/$FIRST_BOARD/$FIRST_CLASS/$FIRST_SUBJECT")
  if [ "$RESOLVE_STATUS" = "200" ] || [ "$RESOLVE_STATUS" = "404" ]; then
    pass "GET /resolve-subject/$FIRST_BOARD/$FIRST_CLASS/$FIRST_SUBJECT → $RESOLVE_STATUS"
  else
    fail "GET /resolve-subject/$FIRST_BOARD/$FIRST_CLASS/$FIRST_SUBJECT → $RESOLVE_STATUS"
  fi
else
  skip "Subject resolve (no slug available)"
fi

# ═══════════════════════════════════════════════════════════════════════════
# 8. Auth Endpoints
# ═══════════════════════════════════════════════════════════════════════════
header "8. Auth Endpoints"

# Anonymous session ping (POST — analytics endpoint, expect 200/401/422)
ANON_STATUS=$(http_status -X POST "$BASE_URL/api/v1/analytics/session-ping" \
  -H "Content-Type: application/json" || echo "000")
if [ "$ANON_STATUS" = "200" ] || [ "$ANON_STATUS" = "401" ] || [ "$ANON_STATUS" = "422" ]; then
  pass "POST /analytics/session-ping reachable ($ANON_STATUS)"
else
  fail "POST /analytics/session-ping unexpected status ($ANON_STATUS)"
fi

# Login with bad creds → 401 or 422 (not 500)
LOGIN_STATUS=$(http_status -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"smoke-test@invalid.test","password":"badpassword"}' || echo "000")
if [ "$LOGIN_STATUS" = "401" ] || [ "$LOGIN_STATUS" = "422" ] || [ "$LOGIN_STATUS" = "400" ]; then
  pass "POST /auth/login with bad creds → $LOGIN_STATUS (not 5xx)"
else
  fail "POST /auth/login unexpected status $LOGIN_STATUS"
fi

# ═══════════════════════════════════════════════════════════════════════════
# 9. Native Workers AI Chat (opt-in; consumes one anonymous chat quota slot)
# ═══════════════════════════════════════════════════════════════════════════
header "9. Native Workers AI Chat"

if [ "$CHAT_SMOKE" = true ]; then
  CHAT_HEADERS=$(mktemp)
  CHAT_RAW=$(curl -sS --no-buffer --max-time 45 \
    -D "$CHAT_HEADERS" \
    -w '\n__STATUS__:%{http_code}' \
    -X POST "$BASE_URL/api/v1/chat/stream" \
    -H "Content-Type: application/json" \
    -d '{"message":"Reply with exactly: Worker AI is ready.","lang":"en"}' || true)
  CHAT_STATUS=$(printf '%s' "$CHAT_RAW" | sed -n 's/.*__STATUS__://p' | tail -1)
  CHAT_BODY=$(printf '%s' "$CHAT_RAW" | sed '$s/__STATUS__:[0-9][0-9][0-9]$//')
  CHAT_HEADERS_TEXT=$(cat "$CHAT_HEADERS")
  rm -f "$CHAT_HEADERS"

  assert_eq "$CHAT_STATUS" "200" "POST /chat/stream → 200"
  assert_contains "$CHAT_HEADERS_TEXT" "content-type: text/event-stream" "chat response is SSE"
  assert_contains "$CHAT_HEADERS_TEXT" "x-syrabit-route: worker-native" "chat is served by native API Worker"
  assert_contains "$CHAT_BODY" '"event":"source_card"' "chat emits source card before answer"
  assert_contains "$CHAT_BODY" '"event":"syrabit_done"' "chat emits clean completion event"
  assert_contains "$CHAT_BODY" '"content":' "chat emits answer content"
  if printf '%s' "$CHAT_BODY" | grep -q '"error":true'; then
    fail "chat stream contains an error event"
  else
    pass "chat stream contains no error event"
  fi
else
  skip "Workers AI chat generation (pass --chat-smoke to run one real anonymous request)"
fi

# ═══════════════════════════════════════════════════════════════════════════
# 10. Security Headers
# ═══════════════════════════════════════════════════════════════════════════
header "10. Security Headers"

ALL_HEADERS=$(http_headers "$BASE_URL/api/v1/content/library-bundle")

assert_contains "$ALL_HEADERS" "x-content-type-options" "x-content-type-options header"
assert_contains "$ALL_HEADERS" "x-frame-options"        "x-frame-options header"
assert_contains "$ALL_HEADERS" "strict-transport-security" "strict-transport-security (HSTS)"
assert_contains "$ALL_HEADERS" "x-robots-tag"           "x-robots-tag (API not indexed)"

# ═══════════════════════════════════════════════════════════════════════════
# 11. CORS
# ═══════════════════════════════════════════════════════════════════════════
header "11. CORS"

CORS_HEADERS=$(http_headers -X OPTIONS "$BASE_URL/api/v1/content/library-bundle" \
  -H "Origin: https://syrabit.ai" \
  -H "Access-Control-Request-Method: GET")

assert_contains "$CORS_HEADERS" "access-control-allow-origin" "CORS allow-origin header"
assert_contains "$CORS_HEADERS" "syrabit.ai"                  "CORS allows syrabit.ai origin"

# ═══════════════════════════════════════════════════════════════════════════
# 12. Frontend
# ═══════════════════════════════════════════════════════════════════════════
header "12. Frontend (${FRONTEND_URL})"

# Follow redirects: syrabit.ai → 301 → /library/ → 200
FRONT_STATUS=$(http_status_follow "$FRONTEND_URL")
if [ "$FRONT_STATUS" = "200" ] || [ "$FRONT_STATUS" = "304" ]; then
  pass "syrabit.ai → $FRONT_STATUS (after redirect)"
else
  fail "syrabit.ai → $FRONT_STATUS"
fi

# Follow redirects: /library → 308 → /library/ → 200
FRONT_LIB=$(http_status_follow "$FRONTEND_URL/library")
if [ "$FRONT_LIB" = "200" ] || [ "$FRONT_LIB" = "304" ]; then
  pass "syrabit.ai/library → $FRONT_LIB (after redirect)"
else
  fail "syrabit.ai/library → $FRONT_LIB"
fi

# ─── Main frontend domain must not impersonate the API origin ───────────────
SAWORKER_STATUS=$(http_status "$FRONTEND_URL/api/v1/health")
assert_eq "$SAWORKER_STATUS" "404" "syrabit.ai/api/v1/health stays off the frontend origin"

# ═══════════════════════════════════════════════════════════════════════════
# 13. Edge Worker Health
# ═══════════════════════════════════════════════════════════════════════════
header "13. Edge Worker Health"

EDGE_STATUS=$(http_status "$BASE_URL/health")
assert_eq "$EDGE_STATUS" "200" "GET /health (Worker edge health)"

EDGE_BODY=$(http_body "$BASE_URL/health")
EDGE_SERVICE=$(json_field "$EDGE_BODY" "d.get('service','')")
assert_nonempty "$EDGE_SERVICE" "Edge /health has service field"

BACKEND_REACHABLE=$(json_field "$EDGE_BODY" "str(d.get('backend_reachable','')).lower()")
assert_eq "$BACKEND_REACHABLE" "true" "Edge reports backend_reachable=true"

# ═══════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════
TOTAL=$((PASS + FAIL + SKIP))
echo -e "\n${BOLD}════════════════════════════════════${RESET}"
echo -e "${BOLD}  Results: $TOTAL checks${RESET}"
echo -e "  ${GREEN}✓ Passed : $PASS${RESET}"
[ $FAIL -gt 0 ] && echo -e "  ${RED}✗ Failed : $FAIL${RESET}" || echo -e "  ✗ Failed : $FAIL"
[ $SKIP -gt 0 ] && echo -e "  ${YELLOW}– Skipped: $SKIP${RESET}" || echo -e "  – Skipped: $SKIP"
echo -e "${BOLD}════════════════════════════════════${RESET}\n"

if [ $FAIL -gt 0 ]; then
  echo -e "${RED}SMOKE TEST FAILED${RESET}"
  exit 1
else
  echo -e "${GREEN}ALL CHECKS PASSED${RESET}"
  exit 0
fi
