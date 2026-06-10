#!/usr/bin/env bash
# =============================================================================
# Syrabit Full-Stack Smoke Test
# Usage:  bash scripts/fullstack-smoke-test.sh [--base-url https://api.syrabit.ai]
# =============================================================================
set -euo pipefail

BASE_URL="${BASE_URL:-https://api.syrabit.ai}"
FRONTEND_URL="${FRONTEND_URL:-https://syrabit.ai}"
# Resolve the live Cloud Run URL dynamically; fall back to the last-known URL
# if gcloud is unavailable (local runs without ADC, or older CI images).
if [[ -z "${DIRECT_URL:-}" ]]; then
  DIRECT_URL=$(gcloud run services describe syrabit-backend \
    --region=asia-south1 \
    --project=blissful-acumen-495019-t6 \
    --format="value(status.url)" 2>/dev/null || echo "")
  if [[ -z "$DIRECT_URL" ]]; then
    DIRECT_URL="https://syrabit-backend-bl6wu3psza-el.a.run.app"
    echo "⚠  Could not resolve Cloud Run URL via gcloud — using cached fallback"
  fi
fi

PASS=0
FAIL=0
SKIP=0

# ── Colours ────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m';  BOLD='\033[1m';   RESET='\033[0m'

# ── Helpers ────────────────────────────────────────────────────────────────
pass() { echo -e "  ${GREEN}✓${RESET}  $1"; PASS=$((PASS+1)); }
fail() { echo -e "  ${RED}✗${RESET}  $1"; FAIL=$((FAIL+1)); }
skip() { echo -e "  ${YELLOW}–${RESET}  $1 (skipped)"; SKIP=$((SKIP+1)); }
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
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

echo -e "${BOLD}Syrabit Full-Stack Smoke Test${RESET}"
echo    "  Base URL   : $BASE_URL"
echo    "  Frontend   : $FRONTEND_URL"
echo    "  Direct GCR : $DIRECT_URL"

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

CF_RAY=$(http_headers "$BASE_URL/api/v1/health" | grep -i "^cf-ray:" || echo "")
assert_nonempty "$CF_RAY" "cf-ray header present (Worker is proxying)"

SERVER=$(http_headers "$BASE_URL/api/v1/health" | grep -i "^server:" | tr -d '\r\n' || echo "")
assert_contains "$SERVER" "cloudflare" "server: cloudflare header"

# ═══════════════════════════════════════════════════════════════════════════
# 3. Backend Health (via Worker)
# ═══════════════════════════════════════════════════════════════════════════
header "3. Backend Health (via Worker — $BASE_URL)"

STATUS=$(http_status "$BASE_URL/api/v1/health")
assert_eq "$STATUS" "200" "GET /api/v1/health → 200"

BODY=$(http_body "$BASE_URL/api/v1/health")
STATUS_FIELD=$(json_field "$BODY" "d.get('status','')")
assert_eq "$STATUS_FIELD" "healthy" "/api/v1/health status=healthy"

SERVICE=$(json_field "$BODY" "d.get('service','')")
assert_nonempty "$SERVICE" "/api/v1/health has service field"

# ═══════════════════════════════════════════════════════════════════════════
# 4. Backend Health (direct Cloud Run — bypasses Worker)
# ═══════════════════════════════════════════════════════════════════════════
header "4. Backend Health (direct Cloud Run — $DIRECT_URL)"

D_STATUS=$(http_status "$DIRECT_URL/health" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token 2>/dev/null || echo 'notoken')" || echo "000")
if [ "$D_STATUS" = "200" ]; then
  pass "Direct Cloud Run /health → 200"
elif [ "$D_STATUS" = "403" ] || [ "$D_STATUS" = "401" ]; then
  skip "Direct Cloud Run requires IAM auth ($D_STATUS) — expected in production"
else
  fail "Direct Cloud Run /health → $D_STATUS"
fi

# ═══════════════════════════════════════════════════════════════════════════
# 5. MongoDB Deep Health (must pass before content tests)
# ═══════════════════════════════════════════════════════════════════════════
header "5. MongoDB / Deep Health"

DEEP_BODY=$(http_body "$BASE_URL/api/v1/health/deep")
MONGO_STATUS=$(json_field "$DEEP_BODY" "d.get('checks',{}).get('mongodb',{}).get('status','')")
if [ "$MONGO_STATUS" = "healthy" ]; then
  MONGO_LATENCY=$(json_field "$DEEP_BODY" "d.get('checks',{}).get('mongodb',{}).get('latency_ms','')")
  pass "MongoDB healthy (${MONGO_LATENCY}ms)"
else
  MONGO_ERR=$(json_field "$DEEP_BODY" "d.get('checks',{}).get('mongodb',{}).get('error','')")
  fail "MongoDB UNHEALTHY — $MONGO_ERR"
  echo ""
  echo -e "  ${RED}CRITICAL: MongoDB is not initialized in production.${RESET}"
  echo    "  Likely causes:"
  echo    "    1. MONGODB_URI secret not set in GCP Secret Manager (secret name: MONGODB_URI)"
  echo    "    2. Atlas cluster IP allowlist not allowing Cloud Run outbound IPs"
  echo    "    3. Atlas cluster paused / credentials rotated"
  echo    "  Action: check /health/deep for full status, review Cloud Run logs for init error."
fi

REDIS_STATUS=$(json_field "$DEEP_BODY" "d.get('checks',{}).get('redis',{}).get('status','')")
if [ "$REDIS_STATUS" = "healthy" ]; then
  pass "Redis healthy"
elif [ "$REDIS_STATUS" = "disabled" ]; then
  pass "Redis disabled (UPSTASH credentials not configured — expected in this environment)"
else
  fail "Redis unhealthy: $(json_field "$DEEP_BODY" "d.get('checks',{}).get('redis',{}).get('error','')")"
fi

# ═══════════════════════════════════════════════════════════════════════════
# 5b. Vector Index Verification (Atlas Search / Vector Search)
# ═══════════════════════════════════════════════════════════════════════════
header "5b. Vector Index Verification"

# Strategy A: check /health/deep for vector_index or search_index status
VECTOR_STATUS=$(json_field "$DEEP_BODY" "d.get('checks',{}).get('vector_index',{}).get('status','')")
SEARCH_STATUS=$(json_field "$DEEP_BODY" "d.get('checks',{}).get('atlas_search',{}).get('status','')")

if [ -n "$VECTOR_STATUS" ] && [ "$VECTOR_STATUS" != "None" ]; then
  if [ "$VECTOR_STATUS" = "healthy" ] || [ "$VECTOR_STATUS" = "READY" ]; then
    pass "Vector index: status=${VECTOR_STATUS} (READY)"
  else
    fail "Vector index: status=${VECTOR_STATUS} — RAG quality may degrade silently"
    echo ""
    echo -e "  ${RED}Vector index is not READY. A degraded index can still return results${RESET}"
    echo    "  while answer quality collapses. Check Atlas Search → Indexes in the console."
  fi
elif [ -n "$SEARCH_STATUS" ] && [ "$SEARCH_STATUS" != "None" ]; then
  if [ "$SEARCH_STATUS" = "healthy" ] || [ "$SEARCH_STATUS" = "READY" ]; then
    pass "Atlas Search index: status=${SEARCH_STATUS} (READY)"
  else
    fail "Atlas Search index: status=${SEARCH_STATUS} (expected READY)"
  fi
else
  # Strategy B: probe a RAG-dependent endpoint and validate response quality
  # If the vector index is broken, the RAG search returns empty contexts and the
  # answer will be generic / very short.
  RAG_BODY=$(http_body "$BASE_URL/api/v1/chat/" \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"message":"What is photosynthesis? Answer in one sentence.","lang":"en"}' 2>/dev/null || echo "{}")

  RAG_RESPONSE=$(json_field "$RAG_BODY" "str(d.get('response',''))" 2>/dev/null || echo "")
  RAG_LEN=${#RAG_RESPONSE}

  if [ "$RAG_LEN" -ge 30 ]; then
    pass "Vector index proxy check: RAG returned ${RAG_LEN}-char response (index likely READY)"
  elif [ "$RAG_LEN" -gt 0 ]; then
    warn "Vector index proxy check: very short RAG response (${RAG_LEN} chars) — index may be degraded"
    echo "  Response: ${RAG_RESPONSE:0:120}"
  else
    warn "Vector index status not exposed in /health/deep — add 'vector_index' check to deep health endpoint"
    echo "  Tip: add db.getCollection('chapters').getSearchIndexes() probe to /health/deep"
  fi
fi

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
assert_gte "$CHAPTERS" 200 "chapters count"

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
# 9. Security Headers
# ═══════════════════════════════════════════════════════════════════════════
header "9. Security Headers"

ALL_HEADERS=$(http_headers "$BASE_URL/api/v1/health")

assert_contains "$ALL_HEADERS" "x-content-type-options" "x-content-type-options header"
assert_contains "$ALL_HEADERS" "x-frame-options"        "x-frame-options header"
assert_contains "$ALL_HEADERS" "strict-transport-security" "strict-transport-security (HSTS)"
assert_contains "$ALL_HEADERS" "x-robots-tag"           "x-robots-tag (API not indexed)"

# ═══════════════════════════════════════════════════════════════════════════
# 10. CORS
# ═══════════════════════════════════════════════════════════════════════════
header "10. CORS"

CORS_HEADERS=$(http_headers -X OPTIONS "$BASE_URL/api/v1/content/library-bundle" \
  -H "Origin: https://syrabit.ai" \
  -H "Access-Control-Request-Method: GET")

assert_contains "$CORS_HEADERS" "access-control-allow-origin" "CORS allow-origin header"
assert_contains "$CORS_HEADERS" "syrabit.ai"                  "CORS allows syrabit.ai origin"

# ═══════════════════════════════════════════════════════════════════════════
# 11. Frontend
# ═══════════════════════════════════════════════════════════════════════════
header "11. Frontend (${FRONTEND_URL})"

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

# ─── Worker route: syrabit.ai/api/* ───────────────────────────────────────
SAWORKER_STATUS=$(http_status "$FRONTEND_URL/api/v1/health")
assert_eq "$SAWORKER_STATUS" "200" "syrabit.ai/api/v1/health (Worker route on main domain)"

# ═══════════════════════════════════════════════════════════════════════════
# 12. Edge Worker Health
# ═══════════════════════════════════════════════════════════════════════════
header "12. Edge Worker Health"

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
