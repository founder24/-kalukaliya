#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# SYRABIT LIVE DEPLOYMENT TEST
# ═══════════════════════════════════════════════════════════════════════════════
#
# Comprehensive fullstack deployment test measuring real latency metrics
# for all layers: frontend, edge worker, backend API, and chat endpoints.
#
# Usage:
#   ./scripts/live-deployment-test.sh
#
# With authenticated chat tests:
#   TEST_JWT_TOKEN="eyJ..." TEST_TURNSTILE_TOKEN="0.xxx" ./scripts/live-deployment-test.sh
#
# Against staging:
#   BASE_URL="https://staging-api.syrabit.ai" FRONTEND_URL="https://staging.syrabit.ai" ./scripts/live-deployment-test.sh
#
# Environment Variables (all optional):
#   BASE_URL            - Override backend/edge URL (default: https://api.syrabit.ai)
#   FRONTEND_URL        - Override frontend URL (default: https://syrabit.ai)
#   TEST_JWT_TOKEN      - JWT token for authenticated chat tests
#   TEST_TURNSTILE_TOKEN - Turnstile token for chat tests
#   VERBOSE             - Set to 1 for detailed curl output
#
# Requirements: bash, curl, jq
# Exit code: 0 if all critical checks pass, 1 if any critical check fails
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Configuration ────────────────────────────────────────────────────────────

BASE_URL="${BASE_URL:-https://api.syrabit.ai}"
FRONTEND_URL="${FRONTEND_URL:-https://syrabit.ai}"
TEST_JWT_TOKEN="${TEST_JWT_TOKEN:-}"
TEST_TURNSTILE_TOKEN="${TEST_TURNSTILE_TOKEN:-}"
VERBOSE="${VERBOSE:-0}"

# Performance targets (in milliseconds)
TARGET_FRONTEND_TTFB=800
TARGET_FRONTEND_TOTAL=2000
TARGET_EDGE_HEALTH_TTFB=200
TARGET_DEEP_HEALTH=2000
TARGET_CORS_PREFLIGHT=100
TARGET_AUTH_ENDPOINT=500
TARGET_CHAT_ROUTING=1000
TARGET_CHAT_FULL=3000
TARGET_CHAT_STREAM_TTFB=1000

# ─── State Tracking ──────────────────────────────────────────────────────────

TOTAL_TESTS=0
PASSED_TESTS=0
WARNING_TESTS=0
FAILED_TESTS=0
CRITICAL_FAILED=0

# ─── Utility Functions ────────────────────────────────────────────────────────

# Colors (if terminal supports them)
if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    BOLD=''
    NC=''
fi

pass() {
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    PASSED_TESTS=$((PASSED_TESTS + 1))
    echo -e "    Result:     ${GREEN}PASS${NC} $1"
}

warn() {
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    WARNING_TESTS=$((WARNING_TESTS + 1))
    echo -e "    Result:     ${YELLOW}WARN${NC} $1"
}

fail() {
    local is_critical="${2:-no}"
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    FAILED_TESTS=$((FAILED_TESTS + 1))
    if [[ "$is_critical" == "yes" ]]; then
        CRITICAL_FAILED=$((CRITICAL_FAILED + 1))
        echo -e "    Result:     ${RED}FAIL [CRITICAL]${NC} $1"
    else
        echo -e "    Result:     ${RED}FAIL${NC} $1"
    fi
}

verbose_log() {
    if [[ "$VERBOSE" == "1" ]]; then
        echo -e "    [DEBUG] $1"
    fi
}

# Perform a timed curl request and extract timing metrics
# Arguments: url [extra_curl_args...]
# Sets global variables: CURL_STATUS, CURL_DNS, CURL_TLS, CURL_TTFB, CURL_TOTAL, CURL_BODY, CURL_HEADERS
perform_request() {
    local url="$1"
    shift
    local extra_args=("$@")

    local timing_format='{"dns":%{time_namelookup},"tls":%{time_appconnect},"ttfb":%{time_starttransfer},"total":%{time_total},"status":%{http_code},"size":%{size_download}}'

    local tmpfile
    tmpfile=$(mktemp)
    local header_file
    header_file=$(mktemp)

    local curl_cmd=(curl -sS -w "$timing_format" -o "$tmpfile" -D "$header_file" --max-time 30)

    if [[ ${#extra_args[@]} -gt 0 ]]; then
        curl_cmd+=("${extra_args[@]}")
    fi

    curl_cmd+=("$url")

    verbose_log "curl ${curl_cmd[*]}"

    local timing_json
    timing_json=$("${curl_cmd[@]}" 2>/dev/null) || timing_json='{"dns":0,"tls":0,"ttfb":0,"total":0,"status":0,"size":0}'

    CURL_STATUS=$(echo "$timing_json" | jq -r '.status // 0')
    CURL_DNS=$(echo "$timing_json" | jq -r '(.dns * 1000) | floor')
    CURL_TLS=$(echo "$timing_json" | jq -r '(.tls * 1000) | floor')
    CURL_TTFB=$(echo "$timing_json" | jq -r '(.ttfb * 1000) | floor')
    CURL_TOTAL=$(echo "$timing_json" | jq -r '(.total * 1000) | floor')
    CURL_BODY=$(cat "$tmpfile" 2>/dev/null || echo "")
    CURL_HEADERS=$(cat "$header_file" 2>/dev/null || echo "")

    rm -f "$tmpfile" "$header_file"
}

ms_display() {
    echo "${1}ms"
}

# ─── Header ──────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}$(printf '%.0s=' {1..65})${NC}"
echo -e "${BOLD}  SYRABIT LIVE DEPLOYMENT TEST${NC}"
echo -e "  Date: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo -e "  Target: ${BASE_URL}"
echo -e "  Frontend: ${FRONTEND_URL}"
if [[ -n "$TEST_JWT_TOKEN" ]]; then
    echo -e "  Auth: JWT token provided (authenticated tests enabled)"
fi
echo -e "${BOLD}$(printf '%.0s=' {1..65})${NC}"
echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# 1. FRONTEND PERFORMANCE
# ═══════════════════════════════════════════════════════════════════════════════

echo -e "${BOLD}-- FRONTEND ────────────────────────────────────────────────${NC}"
echo ""
echo "  Page Load (${FRONTEND_URL})"

perform_request "$FRONTEND_URL" \
    -H "Accept-Encoding: gzip, deflate, br" \
    -H "User-Agent: SyrabitDeployTest/1.0"

echo "    DNS:        $(ms_display "$CURL_DNS")"
echo "    TLS:        $(ms_display "$CURL_TLS")"
echo "    TTFB:       $(ms_display "$CURL_TTFB")"
echo "    Total:      $(ms_display "$CURL_TOTAL")"
echo "    Status:     ${CURL_STATUS}"

# Check compression
COMPRESSION="none"
if echo "$CURL_HEADERS" | grep -qi "content-encoding: br"; then
    COMPRESSION="yes (br)"
elif echo "$CURL_HEADERS" | grep -qi "content-encoding: gzip"; then
    COMPRESSION="yes (gzip)"
elif echo "$CURL_HEADERS" | grep -qi "content-encoding: deflate"; then
    COMPRESSION="yes (deflate)"
fi
echo "    Compressed: ${COMPRESSION}"

# Check security headers
HSTS="no"
XFRAME="none"
if echo "$CURL_HEADERS" | grep -qi "strict-transport-security"; then
    HSTS="yes"
fi
if echo "$CURL_HEADERS" | grep -qi "x-frame-options: DENY"; then
    XFRAME="DENY"
elif echo "$CURL_HEADERS" | grep -qi "x-frame-options: SAMEORIGIN"; then
    XFRAME="SAMEORIGIN"
fi
echo "    Headers:    HSTS=${HSTS}, X-Frame=${XFRAME}"

# Evaluate result
if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 400 && "$CURL_TTFB" -lt "$TARGET_FRONTEND_TTFB" ]]; then
    pass "(TTFB ${CURL_TTFB}ms < ${TARGET_FRONTEND_TTFB}ms)"
elif [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 400 && "$CURL_TTFB" -ge "$TARGET_FRONTEND_TTFB" ]]; then
    fail "(TTFB ${CURL_TTFB}ms >= ${TARGET_FRONTEND_TTFB}ms)" "yes"
else
    fail "(HTTP ${CURL_STATUS} - page not reachable)" "yes"
fi

echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# 2. EDGE WORKER PERFORMANCE
# ═══════════════════════════════════════════════════════════════════════════════

echo -e "${BOLD}-- EDGE WORKER ─────────────────────────────────────────────${NC}"
echo ""

# --- Health Check ---
echo "  Health Check (/health)"

perform_request "${BASE_URL}/health"

echo "    TTFB:       $(ms_display "$CURL_TTFB")"
echo "    Status:     ${CURL_STATUS}"

if [[ "$CURL_STATUS" -eq 200 && "$CURL_TTFB" -lt "$TARGET_EDGE_HEALTH_TTFB" ]]; then
    pass "(${CURL_TTFB}ms < ${TARGET_EDGE_HEALTH_TTFB}ms)"
elif [[ "$CURL_STATUS" -eq 200 ]]; then
    fail "(TTFB ${CURL_TTFB}ms >= ${TARGET_EDGE_HEALTH_TTFB}ms)" "yes"
else
    fail "(HTTP ${CURL_STATUS} - edge not healthy)" "yes"
fi

echo ""

# --- Deep Health ---
echo "  Deep Health (/health/deep)"

perform_request "${BASE_URL}/health/deep"

echo "    TTFB:       $(ms_display "$CURL_TTFB")"
echo "    Status:     ${CURL_STATUS}"

# Parse deep health response for component status
if [[ -n "$CURL_BODY" ]] && echo "$CURL_BODY" | jq . >/dev/null 2>&1; then
    MONGO_STATUS=$(echo "$CURL_BODY" | jq -r '.mongodb // .mongo // .database // "unknown"' 2>/dev/null)
    REDIS_STATUS=$(echo "$CURL_BODY" | jq -r '.redis // .cache // "unknown"' 2>/dev/null)
    SEARCH_STATUS=$(echo "$CURL_BODY" | jq -r '.search // .azure_search // "unknown"' 2>/dev/null)
    VERTEX_STATUS=$(echo "$CURL_BODY" | jq -r '.vertex_ai // .vertexai // "unknown"' 2>/dev/null)

    # Try alternate JSON structures
    if [[ "$MONGO_STATUS" == "null" || "$MONGO_STATUS" == "unknown" ]]; then
        MONGO_STATUS=$(echo "$CURL_BODY" | jq -r '.services.mongodb.status // .checks.mongodb // "unknown"' 2>/dev/null)
    fi
    if [[ "$REDIS_STATUS" == "null" || "$REDIS_STATUS" == "unknown" ]]; then
        REDIS_STATUS=$(echo "$CURL_BODY" | jq -r '.services.redis.status // .checks.redis // "unknown"' 2>/dev/null)
    fi
    if [[ "$SEARCH_STATUS" == "null" || "$SEARCH_STATUS" == "unknown" ]]; then
        SEARCH_STATUS=$(echo "$CURL_BODY" | jq -r '.services.search.status // .checks.search // "unknown"' 2>/dev/null)
    fi
    if [[ "$VERTEX_STATUS" == "null" || "$VERTEX_STATUS" == "unknown" ]]; then
        VERTEX_STATUS=$(echo "$CURL_BODY" | jq -r '.services.vertex_ai.status // .checks.vertex_ai // "unknown"' 2>/dev/null)
    fi

    echo "    MongoDB:    ${MONGO_STATUS}"
    echo "    Redis:      ${REDIS_STATUS}"
    echo "    Search:     ${SEARCH_STATUS}"
    echo "    Vertex AI:  ${VERTEX_STATUS}"
else
    echo "    Body:       (non-JSON or empty response)"
fi

if [[ "$CURL_STATUS" -eq 200 && "$CURL_TTFB" -lt "$TARGET_DEEP_HEALTH" ]]; then
    pass "(${CURL_TTFB}ms < ${TARGET_DEEP_HEALTH}ms)"
elif [[ "$CURL_STATUS" -eq 503 ]]; then
    warn "(HTTP 503 - some dependencies unhealthy, TTFB ${CURL_TTFB}ms)"
elif [[ "$CURL_STATUS" -eq 200 ]]; then
    fail "(TTFB ${CURL_TTFB}ms >= ${TARGET_DEEP_HEALTH}ms)" "yes"
else
    fail "(HTTP ${CURL_STATUS} - deep health endpoint error)" "yes"
fi

echo ""

# --- Circuit Breakers ---
echo "  Circuit Breakers (/health/circuit-breakers)"

perform_request "${BASE_URL}/health/circuit-breakers"

echo "    Status:     ${CURL_STATUS}"

if [[ -n "$CURL_BODY" ]] && echo "$CURL_BODY" | jq . >/dev/null 2>&1; then
    # Try to extract circuit breaker states
    VERTEX_CB=$(echo "$CURL_BODY" | jq -r '.vertex_ai // .vertexAI // .breakers.vertex_ai // "unknown"' 2>/dev/null)
    SARVAM_CB=$(echo "$CURL_BODY" | jq -r '.sarvam_ai // .sarvamAI // .breakers.sarvam_ai // "unknown"' 2>/dev/null)
    SEARCH_CB=$(echo "$CURL_BODY" | jq -r '.azure_search // .azureSearch // .breakers.azure_search // "unknown"' 2>/dev/null)

    echo "    Vertex AI:  ${VERTEX_CB}"
    echo "    Sarvam AI:  ${SARVAM_CB}"
    echo "    Azure Search: ${SEARCH_CB}"
else
    echo "    Body:       (non-JSON or empty response)"
fi

if [[ "$CURL_STATUS" -eq 200 ]]; then
    pass "(circuit breakers endpoint reachable)"
elif [[ "$CURL_STATUS" -eq 404 ]]; then
    warn "(endpoint not found - may not be implemented)"
else
    warn "(HTTP ${CURL_STATUS})"
fi

echo ""

# --- CORS Preflight ---
echo "  CORS Preflight (OPTIONS ${BASE_URL}/health)"

perform_request "${BASE_URL}/health" \
    -X OPTIONS \
    -H "Origin: ${FRONTEND_URL}" \
    -H "Access-Control-Request-Method: GET" \
    -H "Access-Control-Request-Headers: Content-Type,Authorization"

echo "    TTFB:       $(ms_display "$CURL_TTFB")"

# Extract CORS headers
ALLOW_ORIGIN=$(echo "$CURL_HEADERS" | grep -i "access-control-allow-origin" | head -1 | sed 's/.*: //' | tr -d '\r\n')
echo "    Allow-Origin: ${ALLOW_ORIGIN:-none}"

if [[ "$CURL_TTFB" -lt "$TARGET_CORS_PREFLIGHT" ]]; then
    pass "(${CURL_TTFB}ms < ${TARGET_CORS_PREFLIGHT}ms)"
elif [[ "$CURL_TTFB" -lt 500 ]]; then
    warn "(TTFB ${CURL_TTFB}ms >= ${TARGET_CORS_PREFLIGHT}ms but < 500ms)"
else
    fail "(TTFB ${CURL_TTFB}ms >= ${TARGET_CORS_PREFLIGHT}ms)" "yes"
fi

echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# 3. BACKEND API PERFORMANCE
# ═══════════════════════════════════════════════════════════════════════════════

echo -e "${BOLD}-- BACKEND API ─────────────────────────────────────────────${NC}"
echo ""

# --- Backend Health via Edge ---
echo "  Backend Health (/api/v1/health)"

perform_request "${BASE_URL}/api/v1/health"

echo "    TTFB:       $(ms_display "$CURL_TTFB")"
echo "    Total:      $(ms_display "$CURL_TOTAL")"
echo "    Status:     ${CURL_STATUS}"

if [[ "$CURL_STATUS" -eq 200 ]]; then
    pass "(backend reachable via edge, ${CURL_TTFB}ms)"
elif [[ "$CURL_STATUS" -eq 404 ]]; then
    warn "(404 - endpoint may use different path)"
else
    warn "(HTTP ${CURL_STATUS} - backend may be degraded)"
fi

echo ""

# --- Auth Endpoint ---
echo "  Auth Endpoint (POST /api/v1/auth/login)"

perform_request "${BASE_URL}/api/v1/auth/login" \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"invalid":"body"}'

echo "    TTFB:       $(ms_display "$CURL_TTFB")"
echo "    Status:     ${CURL_STATUS} (expected 422 for invalid body)"

if [[ "$CURL_TTFB" -lt "$TARGET_AUTH_ENDPOINT" ]]; then
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 ]]; then
        pass "(auth responds in ${CURL_TTFB}ms < ${TARGET_AUTH_ENDPOINT}ms, status ${CURL_STATUS})"
    else
        pass "(routing works in ${CURL_TTFB}ms, status ${CURL_STATUS})"
    fi
else
    fail "(auth TTFB ${CURL_TTFB}ms >= ${TARGET_AUTH_ENDPOINT}ms)" "no"
fi

echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# 4. CHAT PERFORMANCE (Routing / Unauthenticated)
# ═══════════════════════════════════════════════════════════════════════════════

echo -e "${BOLD}-- CHAT PERFORMANCE ────────────────────────────────────────${NC}"
echo ""

# --- English Chat Routing ---
echo "  English (Vertex AI routing)"

perform_request "${BASE_URL}/api/v1/chat/stream" \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"message":"Hello","language":"en","stream":true}'

echo "    Status:     ${CURL_STATUS} (expected 403 - Turnstile required, endpoint alive)"
echo "    TTFB:       $(ms_display "$CURL_TTFB")"

if [[ "$CURL_TTFB" -lt "$TARGET_CHAT_ROUTING" ]]; then
    pass "(routing < ${TARGET_CHAT_ROUTING}ms, ${CURL_TTFB}ms)"
else
    fail "(routing ${CURL_TTFB}ms >= ${TARGET_CHAT_ROUTING}ms)" "no"
fi

echo ""

# --- Assamese Chat Routing ---
echo "  Assamese (Sarvam AI routing)"

perform_request "${BASE_URL}/api/v1/chat/stream" \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"message":"নমস্কাৰ","language":"as","stream":true}'

echo "    Status:     ${CURL_STATUS} (expected 403 - Turnstile required, endpoint alive)"
echo "    TTFB:       $(ms_display "$CURL_TTFB")"

if [[ "$CURL_TTFB" -lt "$TARGET_CHAT_ROUTING" ]]; then
    pass "(routing < ${TARGET_CHAT_ROUTING}ms, ${CURL_TTFB}ms)"
else
    fail "(routing ${CURL_TTFB}ms >= ${TARGET_CHAT_ROUTING}ms)" "no"
fi

echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# 5. AUTHENTICATED CHAT TESTS (if tokens provided)
# ═══════════════════════════════════════════════════════════════════════════════

if [[ -n "$TEST_JWT_TOKEN" && -n "$TEST_TURNSTILE_TOKEN" ]]; then
    echo -e "  ${BOLD}[WITH AUTH TOKEN - authenticated chat tests]${NC}"
    echo ""

    AUTH_HEADERS=(
        -H "Authorization: Bearer ${TEST_JWT_TOKEN}"
        -H "X-Turnstile-Token: ${TEST_TURNSTILE_TOKEN}"
        -H "Content-Type: application/json"
    )

    # --- English Full Response (non-streaming) ---
    echo "  English Full Response (non-streaming)"

    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        "${AUTH_HEADERS[@]}" \
        -d '{"message":"What is Assam known for? Reply in one sentence.","language":"en","stream":false}'

    echo "    TTFB:       $(ms_display "$CURL_TTFB")"
    echo "    Total:      $(ms_display "$CURL_TOTAL")"
    echo "    Status:     ${CURL_STATUS}"

    if [[ -n "$CURL_BODY" ]] && echo "$CURL_BODY" | jq . >/dev/null 2>&1; then
        CHAT_MODEL=$(echo "$CURL_BODY" | jq -r '.model // .metadata.model // "unknown"' 2>/dev/null)
        echo "    Model:      ${CHAT_MODEL}"
    fi

    if [[ "$CURL_STATUS" -eq 200 && "$CURL_TOTAL" -lt "$TARGET_CHAT_FULL" ]]; then
        pass "(< ${TARGET_CHAT_FULL}ms, total ${CURL_TOTAL}ms)"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        fail "(total ${CURL_TOTAL}ms >= ${TARGET_CHAT_FULL}ms)" "no"
    elif [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        warn "(auth rejected - token may be expired, status ${CURL_STATUS})"
    else
        warn "(HTTP ${CURL_STATUS} - chat may be degraded)"
    fi

    echo ""

    # --- Assamese Full Response (non-streaming) ---
    echo "  Assamese Full Response (non-streaming)"

    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        "${AUTH_HEADERS[@]}" \
        -d '{"message":"অসমৰ বিষয়ে কওক। এটা বাক্যত উত্তৰ দিয়ক।","language":"as","stream":false}'

    echo "    TTFB:       $(ms_display "$CURL_TTFB")"
    echo "    Total:      $(ms_display "$CURL_TOTAL")"
    echo "    Status:     ${CURL_STATUS}"

    if [[ -n "$CURL_BODY" ]] && echo "$CURL_BODY" | jq . >/dev/null 2>&1; then
        CHAT_MODEL=$(echo "$CURL_BODY" | jq -r '.model // .metadata.model // "unknown"' 2>/dev/null)
        echo "    Model:      ${CHAT_MODEL}"
    fi

    if [[ "$CURL_STATUS" -eq 200 && "$CURL_TOTAL" -lt "$TARGET_CHAT_FULL" ]]; then
        pass "(< ${TARGET_CHAT_FULL}ms, total ${CURL_TOTAL}ms)"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        fail "(total ${CURL_TOTAL}ms >= ${TARGET_CHAT_FULL}ms)" "no"
    elif [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        warn "(auth rejected - token may be expired, status ${CURL_STATUS})"
    else
        warn "(HTTP ${CURL_STATUS} - chat may be degraded)"
    fi

    echo ""

    # --- English Streaming TTFB ---
    echo "  English Streaming (TTFB measurement)"

    perform_request "${BASE_URL}/api/v1/chat/stream" \
        -X POST \
        "${AUTH_HEADERS[@]}" \
        -d '{"message":"Say hello in one word.","language":"en","stream":true}'

    echo "    TTFB:       $(ms_display "$CURL_TTFB")"
    echo "    Total:      $(ms_display "$CURL_TOTAL")"
    echo "    Status:     ${CURL_STATUS}"

    if [[ "$CURL_STATUS" -eq 200 && "$CURL_TTFB" -lt "$TARGET_CHAT_STREAM_TTFB" ]]; then
        pass "(streaming TTFB ${CURL_TTFB}ms < ${TARGET_CHAT_STREAM_TTFB}ms)"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        fail "(streaming TTFB ${CURL_TTFB}ms >= ${TARGET_CHAT_STREAM_TTFB}ms)" "no"
    elif [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        warn "(auth rejected - status ${CURL_STATUS})"
    else
        warn "(HTTP ${CURL_STATUS})"
    fi

    echo ""

    # --- Assamese Streaming TTFB ---
    echo "  Assamese Streaming (TTFB measurement)"

    perform_request "${BASE_URL}/api/v1/chat/stream" \
        -X POST \
        "${AUTH_HEADERS[@]}" \
        -d '{"message":"নমস্কাৰ বুলি কওক।","language":"as","stream":true}'

    echo "    TTFB:       $(ms_display "$CURL_TTFB")"
    echo "    Total:      $(ms_display "$CURL_TOTAL")"
    echo "    Status:     ${CURL_STATUS}"

    if [[ "$CURL_STATUS" -eq 200 && "$CURL_TTFB" -lt "$TARGET_CHAT_STREAM_TTFB" ]]; then
        pass "(streaming TTFB ${CURL_TTFB}ms < ${TARGET_CHAT_STREAM_TTFB}ms)"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        fail "(streaming TTFB ${CURL_TTFB}ms >= ${TARGET_CHAT_STREAM_TTFB}ms)" "no"
    elif [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        warn "(auth rejected - status ${CURL_STATUS})"
    else
        warn "(HTTP ${CURL_STATUS})"
    fi

    echo ""

else
    echo "  [SKIPPED] Authenticated chat tests (TEST_JWT_TOKEN and TEST_TURNSTILE_TOKEN not set)"
    echo ""
fi

# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

echo -e "${BOLD}-- SUMMARY ─────────────────────────────────────────────────${NC}"
echo ""
echo "  Total tests:    ${TOTAL_TESTS}"
echo -e "  Passed:         ${GREEN}${PASSED_TESTS}${NC}"
if [[ "$WARNING_TESTS" -gt 0 ]]; then
    echo -e "  Warnings:       ${YELLOW}${WARNING_TESTS}${NC}"
else
    echo "  Warnings:       ${WARNING_TESTS}"
fi
if [[ "$FAILED_TESTS" -gt 0 ]]; then
    echo -e "  Failed:         ${RED}${FAILED_TESTS}${NC}"
else
    echo "  Failed:         ${FAILED_TESTS}"
fi
echo ""

if [[ "$CRITICAL_FAILED" -eq 0 ]]; then
    echo -e "  Critical checks: ${GREEN}${BOLD}ALL PASSED${NC}"
else
    echo -e "  Critical checks: ${RED}${BOLD}${CRITICAL_FAILED} FAILED${NC}"
fi

echo -e "${BOLD}$(printf '%.0s=' {1..65})${NC}"
echo ""

# Exit with appropriate code
if [[ "$CRITICAL_FAILED" -gt 0 ]]; then
    exit 1
fi

exit 0
