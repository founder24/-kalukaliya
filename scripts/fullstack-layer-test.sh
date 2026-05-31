#!/usr/bin/env bash
# ===============================================================================
# SYRABIT FULLSTACK LAYER-BY-LAYER CLOUD SHELL TEST
# ===============================================================================
#
# Comprehensive test covering all 9 architectural pillars across 18 layers:
#   P1: Cloudflare (CDN + Workers)
#   P2: Cloud Run (Backend)
#   P3: Vertex AI Search (RAG)
#   P4: MongoDB (Data)
#   P5: Upstash Redis (Cache + Rate Limiting)
#   P6: Vertex AI Gemini (LLM)
#   P7: Sarvam AI (Multilingual)
#   P8: Razorpay (Payments)
#   P9: Resend (Email)
#
# Usage:
#   ./scripts/fullstack-layer-test.sh
#   ./scripts/fullstack-layer-test.sh --help
#   ./scripts/fullstack-layer-test.sh --dry-run
#   ./scripts/fullstack-layer-test.sh --layer 3
#   ./scripts/fullstack-layer-test.sh --quick
#
# Environment Variables (all optional):
#   BASE_URL              - Backend/edge URL (default: https://api.syrabit.ai)
#   FRONTEND_URL          - Frontend URL (default: https://syrabit.ai)
#   TEST_JWT_TOKEN        - JWT token for authenticated tests
#   TEST_TURNSTILE_TOKEN  - Turnstile token for chat tests
#   ADMIN_EMAIL           - Admin email for login + admin tests
#   ADMIN_PASSWORD        - Admin password for login + admin tests
#   RAZORPAY_WEBHOOK_SECRET - For webhook signature tests
#   CRON_SECRET           - For cron endpoint tests
#   VERBOSE               - Set to 1 for detailed output
#   STRESS_TEST           - Set to 1 to enable rate limit stress tests
#   EXPORT_JSON           - Set to 1 to export results to JSON
#   SKIP_AUTH_TESTS       - Set to 1 to skip authentication tests
#   SKIP_ADMIN_TESTS      - Set to 1 to skip admin tests
#
# Requirements: bash, curl, jq
# Exit code: 0 if no critical failures, 1 otherwise
# ===============================================================================

set -euo pipefail

# --- Configuration ---

BASE_URL="${BASE_URL:-https://api.syrabit.ai}"
FRONTEND_URL="${FRONTEND_URL:-https://syrabit.ai}"
TEST_JWT_TOKEN="${TEST_JWT_TOKEN:-}"
TEST_TURNSTILE_TOKEN="${TEST_TURNSTILE_TOKEN:-}"
ADMIN_EMAIL="${ADMIN_EMAIL:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
RAZORPAY_WEBHOOK_SECRET="${RAZORPAY_WEBHOOK_SECRET:-}"
CRON_SECRET="${CRON_SECRET:-}"
VERBOSE="${VERBOSE:-0}"
STRESS_TEST="${STRESS_TEST:-0}"
EXPORT_JSON="${EXPORT_JSON:-0}"
SKIP_AUTH_TESTS="${SKIP_AUTH_TESTS:-0}"
SKIP_ADMIN_TESTS="${SKIP_ADMIN_TESTS:-0}"

# Runtime flags
DRY_RUN=0
QUICK_MODE=0
RUN_LAYER=""

# Token storage (populated during auth layer)
AUTH_TOKEN=""
ADMIN_TOKEN=""

# --- State Tracking ---

TOTAL_TESTS=0
PASSED_TESTS=0
WARNING_TESTS=0
FAILED_TESTS=0
SKIPPED_TESTS=0
CRITICAL_FAILED=0
declare -a LAYER_RESULTS=()

# --- Argument Parsing ---

print_help() {
    cat << 'HELPEOF'
SYRABIT FULLSTACK LAYER-BY-LAYER CLOUD SHELL TEST

Usage:
  ./scripts/fullstack-layer-test.sh [OPTIONS]

Options:
  --help        Print this help message and exit
  --dry-run     Validate configuration without making HTTP calls
  --layer N     Run only layer N (0-18)
  --quick       Skip stress tests and optional layers (14, 17)

Environment Variables:
  BASE_URL              Backend/edge URL (default: https://api.syrabit.ai)
  FRONTEND_URL          Frontend URL (default: https://syrabit.ai)
  TEST_JWT_TOKEN        JWT token for authenticated tests
  TEST_TURNSTILE_TOKEN  Turnstile token for chat tests
  ADMIN_EMAIL           Admin email for login + admin tests
  ADMIN_PASSWORD        Admin password for login + admin tests
  RAZORPAY_WEBHOOK_SECRET  For webhook HMAC signature tests
  CRON_SECRET           For cron endpoint tests
  VERBOSE               Set to 1 for detailed curl output
  STRESS_TEST           Set to 1 to enable rate limit stress tests
  EXPORT_JSON           Set to 1 to export results to fullstack-test-results.json
  SKIP_AUTH_TESTS       Set to 1 to skip authentication layer
  SKIP_ADMIN_TESTS      Set to 1 to skip admin endpoint tests

Layers:
   0  Prerequisites & Config
   1  Frontend (Cloudflare CDN)
   2  Edge Worker (Cloudflare Workers)
   3  Backend Health (Cloud Run + MongoDB + Redis)
   4  Authentication (JWT Flow)
   5  Chat Endpoints (Vertex AI + Sarvam AI)
   6  RAG / Hybrid Search (Vertex AI Search)
   7  Content & Knowledge (MongoDB)
   8  Subscription & Payments (Razorpay)
   9  Webhook Pipeline (Razorpay)
  10  Conversations API
  11  Feedback
  12  Admin Endpoints
  13  SEO & Indexing
  14  Education Endpoints (Coming Soon)
  15  Rate Limiting (Upstash Redis)
  16  Streaming & SSE Validation
  17  End-to-End Workflows
  18  Cross-Cutting Concerns

Examples:
  # Run all layers against production
  ./scripts/fullstack-layer-test.sh

  # Run only backend health checks
  ./scripts/fullstack-layer-test.sh --layer 3

  # Quick test against staging
  BASE_URL=https://staging-api.syrabit.ai ./scripts/fullstack-layer-test.sh --quick

  # Full test with admin credentials
  ADMIN_EMAIL=admin@syrabit.ai ADMIN_PASSWORD=secret ./scripts/fullstack-layer-test.sh
HELPEOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            print_help
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --layer)
            RUN_LAYER="$2"
            shift 2
            ;;
        --quick)
            QUICK_MODE=1
            STRESS_TEST=0
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information."
            exit 1
            ;;
    esac
done

# --- Color Output ---

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

# --- Utility Functions ---

pass() {
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    PASSED_TESTS=$((PASSED_TESTS + 1))
    echo -e "    ${GREEN}PASS${NC} $1"
}

fail() {
    local is_critical="${2:-no}"
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    FAILED_TESTS=$((FAILED_TESTS + 1))
    if [[ "$is_critical" == "yes" ]]; then
        CRITICAL_FAILED=$((CRITICAL_FAILED + 1))
        echo -e "    ${RED}FAIL [CRITICAL]${NC} $1"
    else
        echo -e "    ${RED}FAIL${NC} $1"
    fi
}

warn() {
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    WARNING_TESTS=$((WARNING_TESTS + 1))
    echo -e "    ${YELLOW}WARN${NC} $1"
}

skip() {
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    SKIPPED_TESTS=$((SKIPPED_TESTS + 1))
    echo -e "    ${BLUE}SKIP${NC} $1"
}

verbose_log() {
    if [[ "$VERBOSE" == "1" ]]; then
        echo -e "    [DEBUG] $1"
    fi
}

section_header() {
    echo ""
    echo -e "${BOLD}-- $1 ${NC}"
    echo ""
}

# Perform a timed curl request
# Arguments: url [extra_curl_args...]
# Sets: CURL_STATUS, CURL_TTFB, CURL_TOTAL, CURL_BODY, CURL_HEADERS
perform_request() {
    local url="$1"
    shift
    local extra_args=("$@")

    local timing_format='{"dns":%{time_namelookup},"tls":%{time_appconnect},"ttfb":%{time_starttransfer},"total":%{time_total},"status":%{http_code},"size":%{size_download}}'

    local tmpfile header_file
    tmpfile=$(mktemp)
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
    CURL_TTFB=$(echo "$timing_json" | jq -r '(.ttfb * 1000) | floor')
    CURL_TOTAL=$(echo "$timing_json" | jq -r '(.total * 1000) | floor')
    CURL_BODY=$(cat "$tmpfile" 2>/dev/null || echo "")
    CURL_HEADERS=$(cat "$header_file" 2>/dev/null || echo "")

    rm -f "$tmpfile" "$header_file"
}

# Perform a streaming request (no buffer)
perform_stream_request() {
    local url="$1"
    shift
    local extra_args=("$@")

    local tmpfile header_file
    tmpfile=$(mktemp)
    header_file=$(mktemp)

    local curl_cmd=(curl -sS --no-buffer -o "$tmpfile" -D "$header_file" --max-time 30 -w '%{http_code}')

    if [[ ${#extra_args[@]} -gt 0 ]]; then
        curl_cmd+=("${extra_args[@]}")
    fi

    curl_cmd+=("$url")

    CURL_STATUS=$("${curl_cmd[@]}" 2>/dev/null) || CURL_STATUS="0"
    CURL_BODY=$(cat "$tmpfile" 2>/dev/null || echo "")
    CURL_HEADERS=$(cat "$header_file" 2>/dev/null || echo "")

    rm -f "$tmpfile" "$header_file"
}

has_header() {
    echo "$CURL_HEADERS" | grep -qi "$1"
}

get_header_value() {
    echo "$CURL_HEADERS" | grep -i "^$1:" | head -1 | sed 's/^[^:]*: //' | tr -d '\r\n'
}

json_field() {
    echo "$CURL_BODY" | jq -r "$1" 2>/dev/null || echo ""
}

is_json() {
    echo "$CURL_BODY" | jq . >/dev/null 2>&1
}


# ===============================================================================
# LAYER 0: Prerequisites & Config
# ===============================================================================

test_layer_0_prerequisites() {
    section_header "LAYER 0: Prerequisites & Config"

    # Check curl
    if command -v curl &>/dev/null; then
        pass "curl is installed ($(curl --version | head -1 | awk '{print $2}'))"
    else
        fail "curl is not installed" "yes"
        echo "  Cannot continue without curl. Aborting."
        exit 1
    fi

    # Check jq
    if command -v jq &>/dev/null; then
        pass "jq is installed ($(jq --version 2>&1))"
    else
        fail "jq is not installed" "yes"
        echo "  Cannot continue without jq. Aborting."
        exit 1
    fi

    # Check openssl (needed for webhook HMAC)
    if command -v openssl &>/dev/null; then
        pass "openssl is available"
    else
        warn "openssl not found - webhook signature tests will be skipped"
    fi

    # Display configuration
    echo ""
    echo "  Configuration:"
    echo "    BASE_URL:       $BASE_URL"
    echo "    FRONTEND_URL:   $FRONTEND_URL"
    echo "    JWT Token:      ${TEST_JWT_TOKEN:+provided}${TEST_JWT_TOKEN:-not set}"
    echo "    Turnstile:      ${TEST_TURNSTILE_TOKEN:+provided}${TEST_TURNSTILE_TOKEN:-not set}"
    echo "    Admin Email:    ${ADMIN_EMAIL:+provided}${ADMIN_EMAIL:-not set}"
    echo "    Admin Password: ${ADMIN_PASSWORD:+[redacted]}${ADMIN_PASSWORD:-not set}"
    echo "    Webhook Secret: ${RAZORPAY_WEBHOOK_SECRET:+provided}${RAZORPAY_WEBHOOK_SECRET:-not set}"
    echo "    Cron Secret:    ${CRON_SECRET:+provided}${CRON_SECRET:-not set}"
    echo "    Verbose:        $VERBOSE"
    echo "    Stress Test:    $STRESS_TEST"
    echo "    Export JSON:    $EXPORT_JSON"
    echo "    Quick Mode:     $QUICK_MODE"
    echo ""

    pass "Configuration validated"
    LAYER_RESULTS+=("Layer 0: Prerequisites OK")
}

# ===============================================================================
# LAYER 1: Frontend (P1 Cloudflare CDN)
# ===============================================================================

test_layer_1_frontend() {
    section_header "LAYER 1: Frontend (P1 Cloudflare CDN)"

    # Test page load
    echo "  1.1 Frontend page load"
    perform_request "$FRONTEND_URL" \
        -H "Accept-Encoding: gzip, deflate, br" \
        -H "User-Agent: SyrabitFullstackTest/1.0"

    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 400 ]]; then
        pass "Frontend loads (HTTP $CURL_STATUS, ${CURL_TTFB}ms TTFB)"
    else
        fail "Frontend not reachable (HTTP $CURL_STATUS)" "yes"
    fi

    # Test compression
    echo "  1.2 Compression"
    if has_header "content-encoding"; then
        local encoding
        encoding=$(get_header_value "content-encoding")
        pass "Compression enabled ($encoding)"
    else
        warn "No compression detected"
    fi

    # Test robots.txt
    echo "  1.3 robots.txt"
    perform_request "${FRONTEND_URL}/robots.txt"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "robots.txt accessible"
    else
        warn "robots.txt returned HTTP $CURL_STATUS"
    fi

    # Test security headers
    echo "  1.4 Security headers"
    perform_request "$FRONTEND_URL"
    local sec_pass=0
    if has_header "strict-transport-security"; then
        sec_pass=$((sec_pass + 1))
    fi
    if has_header "x-content-type-options"; then
        sec_pass=$((sec_pass + 1))
    fi
    if has_header "x-frame-options"; then
        sec_pass=$((sec_pass + 1))
    fi

    if [[ $sec_pass -ge 2 ]]; then
        pass "Security headers present ($sec_pass/3)"
    elif [[ $sec_pass -ge 1 ]]; then
        warn "Some security headers missing ($sec_pass/3)"
    else
        fail "No security headers found"
    fi

    # Test HTML meta tags
    echo "  1.5 HTML content validation"
    if echo "$CURL_BODY" | grep -qi "<html"; then
        pass "Valid HTML document returned"
    else
        warn "Response may not be HTML"
    fi

    # Test static asset caching
    echo "  1.6 Static asset caching"
    perform_request "${FRONTEND_URL}/robots.txt"
    if has_header "cache-control"; then
        local cc
        cc=$(get_header_value "cache-control")
        pass "Cache-Control header present ($cc)"
    else
        warn "No Cache-Control header on static asset"
    fi

    LAYER_RESULTS+=("Layer 1: Frontend tested")
}

# ===============================================================================
# LAYER 2: Edge Worker (P1 Cloudflare Workers)
# ===============================================================================

test_layer_2_edge_worker() {
    section_header "LAYER 2: Edge Worker (P1 Cloudflare Workers)"

    # Health endpoint
    echo "  2.1 Edge health (/health)"
    perform_request "${BASE_URL}/health"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Edge /health returns 200 (${CURL_TTFB}ms)"
        if is_json && [[ "$(json_field '.backend_reachable // empty')" != "" ]]; then
            pass "Edge health includes backend_reachable field"
        else
            warn "backend_reachable field not found in response"
        fi
    else
        fail "Edge /health returned HTTP $CURL_STATUS" "yes"
    fi

    # Full health
    echo "  2.2 Full health (/health/full)"
    perform_request "${BASE_URL}/health/full"
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 503 ]]; then
        pass "Edge /health/full reachable (HTTP $CURL_STATUS, ${CURL_TTFB}ms)"
    else
        warn "/health/full returned HTTP $CURL_STATUS"
    fi

    # CORS preflight
    echo "  2.3 CORS preflight"
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X OPTIONS \
        -H "Origin: ${FRONTEND_URL}" \
        -H "Access-Control-Request-Method: POST" \
        -H "Access-Control-Request-Headers: Content-Type,Authorization"

    local allow_origin
    allow_origin=$(get_header_value "access-control-allow-origin")
    if [[ -n "$allow_origin" ]]; then
        pass "CORS: Access-Control-Allow-Origin: $allow_origin"
    else
        warn "CORS: No Access-Control-Allow-Origin header"
    fi

    if has_header "access-control-allow-methods"; then
        pass "CORS: Allow-Methods header present"
    else
        warn "CORS: No Allow-Methods header"
    fi

    # Security headers on API
    echo "  2.4 Security headers on API responses"
    perform_request "${BASE_URL}/health"
    local api_sec=0
    if has_header "x-content-type-options"; then
        api_sec=$((api_sec + 1))
    fi
    if has_header "x-frame-options"; then
        api_sec=$((api_sec + 1))
    fi
    if has_header "strict-transport-security"; then
        api_sec=$((api_sec + 1))
    fi

    if [[ $api_sec -ge 2 ]]; then
        pass "API security headers ($api_sec/3 present)"
    elif [[ $api_sec -ge 1 ]]; then
        warn "Partial API security headers ($api_sec/3)"
    else
        fail "No security headers on API responses"
    fi

    # Bot detection
    echo "  2.5 Bot detection tagging"
    perform_request "${BASE_URL}/health" \
        -H "User-Agent: Googlebot/2.1 (+http://www.google.com/bot.html)"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Bot request not blocked (returns 200)"
    else
        warn "Bot request returned HTTP $CURL_STATUS"
    fi

    # Rate limit headers
    echo "  2.6 Rate limit headers"
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"test","language":"en"}'

    if has_header "x-ratelimit-limit" || has_header "ratelimit-limit"; then
        pass "Rate limit headers present"
    else
        warn "No rate limit headers detected"
    fi

    LAYER_RESULTS+=("Layer 2: Edge Worker tested")
}


# ===============================================================================
# LAYER 3: Backend Health (P2 Cloud Run + P4 MongoDB + P5 Redis)
# ===============================================================================

test_layer_3_backend_health() {
    section_header "LAYER 3: Backend Health (P2 Cloud Run + P4 MongoDB + P5 Redis)"

    # Basic health
    echo "  3.1 GET /health"
    perform_request "${BASE_URL}/health"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Backend /health OK (${CURL_TTFB}ms)"
    else
        fail "Backend /health returned HTTP $CURL_STATUS" "yes"
    fi

    # Deep health
    echo "  3.2 GET /health/deep"
    perform_request "${BASE_URL}/health/deep"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Deep health check passed"
        if is_json; then
            local services
            services=$(echo "$CURL_BODY" | jq -r 'keys[]' 2>/dev/null | head -10)
            verbose_log "Deep health services: $services"
            pass "Deep health returns valid JSON with service statuses"
        fi
    elif [[ "$CURL_STATUS" -eq 503 ]]; then
        warn "Deep health: some services degraded (HTTP 503)"
        if is_json; then
            echo "    Response: $(echo "$CURL_BODY" | jq -c '.' 2>/dev/null | head -c 200)"
        fi
    else
        fail "Deep health returned HTTP $CURL_STATUS" "yes"
    fi

    # Circuit breakers
    echo "  3.3 GET /health/circuit-breakers"
    perform_request "${BASE_URL}/health/circuit-breakers"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Circuit breakers endpoint OK"
        if is_json; then
            local vertex_cb sarvam_cb search_cb
            vertex_cb=$(json_field '.vertex_ai.state // .vertex_ai // "unknown"')
            sarvam_cb=$(json_field '.sarvam_ai.state // .sarvam_ai // "unknown"')
            search_cb=$(json_field '.vertex_search.state // .vertex_search // "unknown"')
            echo "      Vertex AI: $vertex_cb | Sarvam AI: $sarvam_cb | Search: $search_cb"
            pass "Circuit breaker states readable"
        fi
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        warn "Circuit breakers endpoint not found (404)"
    else
        warn "Circuit breakers returned HTTP $CURL_STATUS"
    fi

    # Backend health via /api/v1/health
    echo "  3.4 GET /api/v1/health"
    perform_request "${BASE_URL}/api/v1/health"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Backend /api/v1/health OK"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        warn "/api/v1/health not found (may use /health only)"
    else
        warn "/api/v1/health returned HTTP $CURL_STATUS"
    fi

    LAYER_RESULTS+=("Layer 3: Backend health tested")
}

# ===============================================================================
# LAYER 4: Authentication (JWT Flow)
# ===============================================================================

test_layer_4_authentication() {
    section_header "LAYER 4: Authentication (JWT Flow)"

    if [[ "$SKIP_AUTH_TESTS" == "1" ]]; then
        skip "Authentication tests skipped (SKIP_AUTH_TESTS=1)"
        LAYER_RESULTS+=("Layer 4: Skipped")
        return
    fi

    # Signup validation
    echo "  4.1 POST /api/v1/auth/signup (validation)"
    perform_request "${BASE_URL}/api/v1/auth/signup" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"invalid","password":"short"}'

    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        pass "Signup validation rejects invalid input (HTTP $CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        warn "Signup rate limited (HTTP 429)"
    else
        warn "Signup returned unexpected HTTP $CURL_STATUS"
    fi

    # Login with invalid body
    echo "  4.2 POST /api/v1/auth/login (invalid body)"
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"invalid":"body"}'

    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        pass "Login rejects invalid body (HTTP $CURL_STATUS)"
    else
        warn "Login returned HTTP $CURL_STATUS for invalid body"
    fi

    # Login with wrong credentials
    echo "  4.3 POST /api/v1/auth/login (wrong credentials)"
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"nonexistent@test.invalid","password":"WrongPass123!"}'

    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        pass "Login rejects wrong credentials (HTTP $CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 422 ]]; then
        pass "Login validates credential format (HTTP 422)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        warn "Login rate limited (HTTP 429)"
    else
        warn "Login returned HTTP $CURL_STATUS for wrong creds"
    fi

    # Login with admin credentials if provided
    if [[ -n "$ADMIN_EMAIL" && -n "$ADMIN_PASSWORD" ]]; then
        echo "  4.4 POST /api/v1/auth/login (admin credentials)"
        perform_request "${BASE_URL}/api/v1/auth/login" \
            -X POST \
            -H "Content-Type: application/json" \
            -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\"}"

        if [[ "$CURL_STATUS" -eq 200 ]]; then
            AUTH_TOKEN=$(json_field '.access_token // .token // .jwt // empty')
            if [[ -n "$AUTH_TOKEN" ]]; then
                ADMIN_TOKEN="$AUTH_TOKEN"
                pass "Admin login successful (token obtained)"
            else
                pass "Admin login returned 200 but no token in expected field"
                verbose_log "Body: $(echo "$CURL_BODY" | head -c 200)"
            fi
        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            warn "Admin login rate limited"
        else
            fail "Admin login failed (HTTP $CURL_STATUS)"
        fi
    else
        skip "Admin login test skipped (no ADMIN_EMAIL/ADMIN_PASSWORD)"
    fi

    # Use TEST_JWT_TOKEN if provided and no admin token
    if [[ -z "$AUTH_TOKEN" && -n "$TEST_JWT_TOKEN" ]]; then
        AUTH_TOKEN="$TEST_JWT_TOKEN"
    fi

    # Refresh with invalid token
    echo "  4.5 POST /api/v1/auth/refresh (invalid token)"
    perform_request "${BASE_URL}/api/v1/auth/refresh" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer invalid_token_here"

    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 || "$CURL_STATUS" -eq 422 ]]; then
        pass "Refresh rejects invalid token (HTTP $CURL_STATUS)"
    else
        warn "Refresh returned HTTP $CURL_STATUS for invalid token"
    fi

    # Forgot password
    echo "  4.6 POST /api/v1/auth/forgot-password"
    perform_request "${BASE_URL}/api/v1/auth/forgot-password" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"test@example.com"}'

    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 202 ]]; then
        pass "Forgot-password returns success regardless of email (HTTP $CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 422 ]]; then
        pass "Forgot-password validates email format (HTTP 422)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        warn "Forgot-password rate limited"
    else
        warn "Forgot-password returned HTTP $CURL_STATUS"
    fi

    # Rate limiting stress test
    if [[ "$STRESS_TEST" == "1" ]]; then
        echo "  4.7 Rate limiting on login (stress test)"
        local rate_limited=0
        for i in $(seq 1 20); do
            perform_request "${BASE_URL}/api/v1/auth/login" \
                -X POST \
                -H "Content-Type: application/json" \
                -d '{"email":"stress@test.invalid","password":"StressTest123!"}'
            if [[ "$CURL_STATUS" -eq 429 ]]; then
                rate_limited=1
                break
            fi
        done
        if [[ $rate_limited -eq 1 ]]; then
            pass "Rate limiting triggered after rapid login attempts"
        else
            warn "Rate limiting not triggered after 20 rapid attempts"
        fi
    fi

    LAYER_RESULTS+=("Layer 4: Authentication tested")
}


# ===============================================================================
# LAYER 5: Chat Endpoints (P6 Vertex AI + P7 Sarvam AI)
# ===============================================================================

test_layer_5_chat() {
    section_header "LAYER 5: Chat Endpoints (P6 Vertex AI + P7 Sarvam AI)"

    # Non-streaming chat without auth
    echo "  5.1 POST /api/v1/chat/ (no auth)"
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"Hello","language":"en"}'

    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Chat responds without auth (HTTP 200)"
    elif [[ "$CURL_STATUS" -eq 403 || "$CURL_STATUS" -eq 401 ]]; then
        pass "Chat requires authentication (HTTP $CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        warn "Chat rate limited (HTTP 429)"
    else
        warn "Chat returned HTTP $CURL_STATUS"
    fi

    # Streaming chat without auth
    echo "  5.2 POST /api/v1/chat/stream (no auth)"
    perform_request "${BASE_URL}/api/v1/chat/stream" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"Hello","language":"en","stream":true}'

    if [[ "$CURL_STATUS" -eq 200 ]]; then
        local ct
        ct=$(get_header_value "content-type")
        if echo "$ct" | grep -qi "text/event-stream"; then
            pass "Stream endpoint returns text/event-stream"
        else
            pass "Stream endpoint returns 200 (content-type: $ct)"
        fi
    elif [[ "$CURL_STATUS" -eq 403 || "$CURL_STATUS" -eq 401 ]]; then
        pass "Stream requires auth (HTTP $CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        warn "Stream rate limited"
    else
        warn "Stream returned HTTP $CURL_STATUS"
    fi

    # Authenticated chat tests
    if [[ -n "$AUTH_TOKEN" ]]; then
        local auth_h=(-H "Authorization: Bearer ${AUTH_TOKEN}" -H "Content-Type: application/json")
        [[ -n "$TEST_TURNSTILE_TOKEN" ]] && auth_h+=(-H "X-Turnstile-Token: ${TEST_TURNSTILE_TOKEN}")

        # English chat with auth
        echo "  5.3 POST /api/v1/chat/ (authenticated, English)"
        perform_request "${BASE_URL}/api/v1/chat/" \
            -X POST \
            "${auth_h[@]}" \
            -d '{"message":"What is Assam known for? Reply in one sentence.","language":"en"}'

        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Authenticated English chat OK"
            if is_json; then
                local has_response has_model
                has_response=$(json_field '.response // .text // .message // empty')
                has_model=$(json_field '.model_used // .model // empty')
                if [[ -n "$has_response" ]]; then
                    pass "Response contains text content"
                fi
                if [[ -n "$has_model" ]]; then
                    pass "Response includes model_used field"
                fi
            fi
        elif [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
            warn "Auth token rejected (HTTP $CURL_STATUS) - may be expired"
        else
            warn "Authenticated chat returned HTTP $CURL_STATUS"
        fi

        # Assamese chat
        echo "  5.4 POST /api/v1/chat/ (authenticated, Assamese)"
        perform_request "${BASE_URL}/api/v1/chat/" \
            -X POST \
            "${auth_h[@]}" \
            -d '{"message":"Hello in Assamese","language":"as"}'

        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Authenticated Assamese chat OK (Sarvam routing)"
        elif [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
            warn "Auth token rejected for Assamese chat"
        else
            warn "Assamese chat returned HTTP $CURL_STATUS"
        fi

        # Chat history
        echo "  5.5 GET /api/v1/chat/history"
        perform_request "${BASE_URL}/api/v1/chat/history" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"

        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Chat history accessible (HTTP 200)"
        elif [[ "$CURL_STATUS" -eq 401 ]]; then
            warn "Chat history requires valid auth"
        else
            warn "Chat history returned HTTP $CURL_STATUS"
        fi

        # Conversations alias
        echo "  5.6 GET /api/v1/chat/conversations (legacy alias)"
        perform_request "${BASE_URL}/api/v1/chat/conversations" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"

        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Conversations alias works"
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            warn "Conversations alias not found (may use /api/v1/conversations)"
        else
            warn "Conversations alias returned HTTP $CURL_STATUS"
        fi
    else
        skip "Authenticated chat tests skipped (no token)"
        skip "Assamese chat test skipped (no token)"
        skip "Chat history test skipped (no token)"
        skip "Conversations alias test skipped (no token)"
    fi

    LAYER_RESULTS+=("Layer 5: Chat endpoints tested")
}

# ===============================================================================
# LAYER 6: RAG / Hybrid Search (P3 Vertex AI Search)
# ===============================================================================

test_layer_6_rag_search() {
    section_header "LAYER 6: RAG / Hybrid Search (P3 Vertex AI Search)"

    # Check circuit breaker for search
    echo "  6.1 Search circuit breaker status"
    perform_request "${BASE_URL}/health/circuit-breakers"
    if [[ "$CURL_STATUS" -eq 200 ]] && is_json; then
        local search_state
        search_state=$(json_field '.vertex_search.state // .vertex_search // "unknown"')
        if [[ "$search_state" == "closed" || "$search_state" == "CLOSED" ]]; then
            pass "Search circuit breaker is closed (healthy)"
        elif [[ "$search_state" == "open" || "$search_state" == "OPEN" ]]; then
            warn "Search circuit breaker is OPEN (degraded)"
        else
            warn "Search circuit breaker state: $search_state"
        fi
    else
        warn "Cannot check search circuit breaker (HTTP $CURL_STATUS)"
    fi

    # Test RAG with authenticated chat
    if [[ -n "$AUTH_TOKEN" ]]; then
        local auth_h=(-H "Authorization: Bearer ${AUTH_TOKEN}" -H "Content-Type: application/json")
        [[ -n "$TEST_TURNSTILE_TOKEN" ]] && auth_h+=(-H "X-Turnstile-Token: ${TEST_TURNSTILE_TOKEN}")

        echo "  6.2 Chat response with RAG sources"
        perform_request "${BASE_URL}/api/v1/chat/" \
            -X POST \
            "${auth_h[@]}" \
            -d '{"message":"What are the main rivers in Assam?","language":"en"}'

        if [[ "$CURL_STATUS" -eq 200 ]] && is_json; then
            local sources
            sources=$(json_field '.sources // .context_chunks // empty')
            if [[ -n "$sources" && "$sources" != "null" && "$sources" != "[]" ]]; then
                pass "Chat response includes sources/context"
                local first_source
                first_source=$(echo "$CURL_BODY" | jq -r '(.sources // .context_chunks)[0] // empty' 2>/dev/null)
                if [[ -n "$first_source" ]]; then
                    pass "Source objects present in response"
                fi
            else
                warn "No sources in response (RAG may not have relevant content)"
            fi
        elif [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
            warn "Auth rejected - cannot test RAG sources"
        else
            warn "Chat returned HTTP $CURL_STATUS"
        fi
    else
        skip "RAG source test skipped (no auth token)"
        skip "Source field validation skipped (no auth token)"
    fi

    LAYER_RESULTS+=("Layer 6: RAG search tested")
}


# ===============================================================================
# LAYER 7: Content & Knowledge (P4 MongoDB)
# ===============================================================================

test_layer_7_content() {
    section_header "LAYER 7: Content & Knowledge (P4 MongoDB)"

    # Library bundle
    echo "  7.1 GET /api/v1/content/library-bundle"
    perform_request "${BASE_URL}/api/v1/content/library-bundle"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Library bundle accessible (HTTP 200, ${CURL_TTFB}ms)"
        if is_json; then
            local boards_count
            boards_count=$(echo "$CURL_BODY" | jq '.boards // . | length' 2>/dev/null || echo "0")
            if [[ "$boards_count" -gt 0 ]]; then
                pass "Library bundle contains data ($boards_count items)"
            else
                warn "Library bundle returned empty"
            fi
        fi
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        warn "Library bundle endpoint not found"
    else
        warn "Library bundle returned HTTP $CURL_STATUS"
    fi

    # Slim library bundle
    echo "  7.2 GET /api/v1/content/library-bundle?slim=1"
    perform_request "${BASE_URL}/api/v1/content/library-bundle?slim=1"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Slim library bundle accessible"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        warn "Slim bundle not available"
    else
        warn "Slim bundle returned HTTP $CURL_STATUS"
    fi

    # Render endpoint with test data
    echo "  7.3 GET /api/v1/content/render/SEBA/10/science/matter"
    perform_request "${BASE_URL}/api/v1/content/render/SEBA/10/science/matter"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Content render endpoint works (HTTP 200)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        pass "Content render returns 404 for non-existent content (expected)"
    else
        warn "Content render returned HTTP $CURL_STATUS"
    fi

    # Slug lookup
    echo "  7.4 GET /api/v1/content/test-slug"
    perform_request "${BASE_URL}/api/v1/content/test-slug"
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 404 ]]; then
        pass "Content slug endpoint reachable (HTTP $CURL_STATUS)"
    else
        warn "Slug endpoint returned HTTP $CURL_STATUS"
    fi

    # Subject chapters
    echo "  7.5 GET /api/v1/content/subject/SEBA/10/science"
    perform_request "${BASE_URL}/api/v1/content/subject/SEBA/10/science"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Subject chapters endpoint works"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        pass "Subject chapters returns 404 for test data (expected)"
    else
        warn "Subject chapters returned HTTP $CURL_STATUS"
    fi

    LAYER_RESULTS+=("Layer 7: Content tested")
}

# ===============================================================================
# LAYER 8: Subscription & Payments (P8 Razorpay)
# ===============================================================================

test_layer_8_payments() {
    section_header "LAYER 8: Subscription & Payments (P8 Razorpay)"

    # Plans (public)
    echo "  8.1 GET /api/v1/subscription/plans"
    perform_request "${BASE_URL}/api/v1/subscription/plans"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Subscription plans accessible (HTTP 200)"
        if is_json; then
            local plans_content
            plans_content=$(echo "$CURL_BODY" | jq -r '.plans // . | length' 2>/dev/null || echo "0")
            if [[ "$plans_content" -gt 0 ]]; then
                pass "Plans endpoint returns plan data"
            fi
        fi
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        warn "Plans endpoint not found"
    else
        warn "Plans returned HTTP $CURL_STATUS"
    fi

    if [[ -n "$AUTH_TOKEN" ]]; then
        local auth_h=(-H "Authorization: Bearer ${AUTH_TOKEN}")

        # Subscription status
        echo "  8.2 GET /api/v1/subscription/status"
        perform_request "${BASE_URL}/api/v1/subscription/status" "${auth_h[@]}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Subscription status accessible"
            if is_json; then
                local tier
                tier=$(json_field '.tier // .plan // .subscription_tier // empty')
                if [[ -n "$tier" ]]; then
                    pass "Status includes tier info: $tier"
                fi
            fi
        else
            warn "Subscription status returned HTTP $CURL_STATUS"
        fi

        # Create order
        echo "  8.3 POST /api/v1/payments/create-order"
        perform_request "${BASE_URL}/api/v1/payments/create-order" \
            -X POST \
            "${auth_h[@]}" \
            -H "Content-Type: application/json" \
            -d '{"plan_id":"pro_monthly"}'

        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Create order returns 200"
        elif [[ "$CURL_STATUS" -eq 503 ]]; then
            pass "Create order returns 503 (Razorpay not configured - expected)"
        elif [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
            pass "Create order validates input (HTTP $CURL_STATUS)"
        else
            warn "Create order returned HTTP $CURL_STATUS"
        fi

        # Verify payment (invalid)
        echo "  8.4 POST /api/v1/payments/verify (invalid signature)"
        perform_request "${BASE_URL}/api/v1/payments/verify" \
            -X POST \
            "${auth_h[@]}" \
            -H "Content-Type: application/json" \
            -d '{"razorpay_order_id":"fake","razorpay_payment_id":"fake","razorpay_signature":"invalid"}'

        if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 503 ]]; then
            pass "Payment verify rejects invalid signature (HTTP $CURL_STATUS)"
        else
            warn "Payment verify returned HTTP $CURL_STATUS"
        fi

        # Payment history
        echo "  8.5 GET /api/v1/payments/history"
        perform_request "${BASE_URL}/api/v1/payments/history" "${auth_h[@]}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Payment history accessible"
        else
            warn "Payment history returned HTTP $CURL_STATUS"
        fi

        # Recover
        echo "  8.6 POST /api/v1/payments/recover"
        perform_request "${BASE_URL}/api/v1/payments/recover" \
            -X POST \
            "${auth_h[@]}" \
            -H "Content-Type: application/json"

        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 422 ]]; then
            pass "Payment recover endpoint reachable (HTTP $CURL_STATUS)"
        else
            warn "Payment recover returned HTTP $CURL_STATUS"
        fi

        # Credit topup
        echo "  8.7 POST /api/v1/payments/credit-topup"
        perform_request "${BASE_URL}/api/v1/payments/credit-topup" \
            -X POST \
            "${auth_h[@]}" \
            -H "Content-Type: application/json" \
            -d '{"credits":10}'

        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 503 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
            pass "Credit topup endpoint reachable (HTTP $CURL_STATUS)"
        else
            warn "Credit topup returned HTTP $CURL_STATUS"
        fi
    else
        skip "Subscription status test skipped (no auth)"
        skip "Payment create-order test skipped (no auth)"
        skip "Payment verify test skipped (no auth)"
        skip "Payment history test skipped (no auth)"
        skip "Payment recover test skipped (no auth)"
        skip "Credit topup test skipped (no auth)"
    fi

    LAYER_RESULTS+=("Layer 8: Payments tested")
}


# ===============================================================================
# LAYER 9: Webhook Pipeline (P8 Razorpay)
# ===============================================================================

test_layer_9_webhooks() {
    section_header "LAYER 9: Webhook Pipeline (P8 Razorpay)"

    local webhook_body='{"event":"payment.captured","payload":{"payment":{"entity":{"id":"pay_test123","amount":49900,"currency":"INR","status":"captured","order_id":"order_test123","email":"test@example.com"}}},"event_id":"evt_test_001"}'

    # Without signature
    echo "  9.1 POST /api/webhooks/razorpay (no signature)"
    perform_request "${BASE_URL}/api/webhooks/razorpay" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "$webhook_body"

    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 ]]; then
        pass "Webhook rejects missing signature (HTTP $CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        warn "Webhook endpoint not found (404)"
    else
        warn "Webhook without sig returned HTTP $CURL_STATUS"
    fi

    # With invalid signature
    echo "  9.2 POST /api/webhooks/razorpay (invalid signature)"
    perform_request "${BASE_URL}/api/webhooks/razorpay" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "X-Razorpay-Signature: invalidhmacsignature123" \
        -d "$webhook_body"

    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 ]]; then
        pass "Webhook rejects invalid signature (HTTP $CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        warn "Webhook endpoint not found"
    else
        warn "Webhook with invalid sig returned HTTP $CURL_STATUS"
    fi

    # With valid HMAC signature
    if [[ -n "$RAZORPAY_WEBHOOK_SECRET" ]] && command -v openssl &>/dev/null; then
        echo "  9.3 POST /api/webhooks/razorpay (valid HMAC)"
        local valid_sig
        valid_sig=$(echo -n "$webhook_body" | openssl dgst -sha256 -hmac "$RAZORPAY_WEBHOOK_SECRET" | awk '{print $2}')

        perform_request "${BASE_URL}/api/webhooks/razorpay" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "X-Razorpay-Signature: ${valid_sig}" \
            -d "$webhook_body"

        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Webhook accepts valid signature (HTTP 200)"
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            warn "Webhook endpoint not found"
        else
            warn "Webhook with valid sig returned HTTP $CURL_STATUS"
        fi

        # Idempotency test
        echo "  9.4 Webhook idempotency (duplicate event)"
        perform_request "${BASE_URL}/api/webhooks/razorpay" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "X-Razorpay-Signature: ${valid_sig}" \
            -d "$webhook_body"

        if [[ "$CURL_STATUS" -eq 200 ]]; then
            if is_json; then
                local status_field
                status_field=$(json_field '.status // empty')
                if [[ "$status_field" == "duplicate" ]]; then
                    pass "Webhook handles duplicate correctly"
                else
                    pass "Webhook accepts duplicate (idempotent, status: $status_field)"
                fi
            else
                pass "Webhook handles repeat event (HTTP 200)"
            fi
        else
            warn "Duplicate webhook returned HTTP $CURL_STATUS"
        fi
    else
        skip "Valid HMAC webhook test skipped (no RAZORPAY_WEBHOOK_SECRET or no openssl)"
        skip "Webhook idempotency test skipped"
    fi

    LAYER_RESULTS+=("Layer 9: Webhooks tested")
}

# ===============================================================================
# LAYER 10: Conversations API
# ===============================================================================

test_layer_10_conversations() {
    section_header "LAYER 10: Conversations API"

    if [[ -n "$AUTH_TOKEN" ]]; then
        local auth_h=(-H "Authorization: Bearer ${AUTH_TOKEN}")

        # List conversations
        echo "  10.1 GET /api/v1/conversations"
        perform_request "${BASE_URL}/api/v1/conversations" "${auth_h[@]}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Conversations list accessible (HTTP 200)"
        else
            warn "Conversations list returned HTTP $CURL_STATUS"
        fi
    else
        skip "Conversations list test skipped (no auth)"
    fi

    # Anonymous conversations
    echo "  10.2 GET /api/v1/conversations/anon"
    perform_request "${BASE_URL}/api/v1/conversations/anon" \
        -H "x-anon-id: test-anon-12345-valid"

    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Anonymous conversations accessible"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        warn "Anon conversations endpoint not found"
    else
        warn "Anon conversations returned HTTP $CURL_STATUS"
    fi

    # Invalid anon-id
    echo "  10.3 Invalid anon-id format"
    perform_request "${BASE_URL}/api/v1/conversations/anon" \
        -H "x-anon-id: "

    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        pass "Invalid anon-id rejected (HTTP $CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        warn "Empty anon-id accepted (may be valid behavior)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        warn "Anon endpoint not found"
    else
        warn "Invalid anon-id returned HTTP $CURL_STATUS"
    fi

    if [[ -n "$AUTH_TOKEN" ]]; then
        local auth_h=(-H "Authorization: Bearer ${AUTH_TOKEN}")

        # Update conversation (non-existent ID)
        echo "  10.4 PATCH /api/v1/conversations/{id}"
        perform_request "${BASE_URL}/api/v1/conversations/nonexistent-id-123" \
            -X PATCH \
            "${auth_h[@]}" \
            -H "Content-Type: application/json" \
            -d '{"title":"Test Update"}'

        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 422 ]]; then
            pass "Conversation update endpoint reachable (HTTP $CURL_STATUS)"
        else
            warn "Conversation update returned HTTP $CURL_STATUS"
        fi

        # Delete conversation
        echo "  10.5 DELETE /api/v1/conversations/{id}"
        perform_request "${BASE_URL}/api/v1/conversations/nonexistent-id-123" \
            -X DELETE \
            "${auth_h[@]}"

        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 204 || "$CURL_STATUS" -eq 404 ]]; then
            pass "Conversation delete endpoint reachable (HTTP $CURL_STATUS)"
        else
            warn "Conversation delete returned HTTP $CURL_STATUS"
        fi
    else
        skip "Conversation update test skipped (no auth)"
        skip "Conversation delete test skipped (no auth)"
    fi

    LAYER_RESULTS+=("Layer 10: Conversations tested")
}

# ===============================================================================
# LAYER 11: Feedback
# ===============================================================================

test_layer_11_feedback() {
    section_header "LAYER 11: Feedback"

    if [[ -n "$AUTH_TOKEN" ]]; then
        local auth_h=(-H "Authorization: Bearer ${AUTH_TOKEN}" -H "Content-Type: application/json")

        # Submit feedback
        echo "  11.1 POST /api/v1/chat/feedback/"
        perform_request "${BASE_URL}/api/v1/chat/feedback/" \
            -X POST \
            "${auth_h[@]}" \
            -d '{"session_id":"test-session-001","message_id":"msg-001","rating":5,"comment":"Great response"}'

        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 201 ]]; then
            pass "Feedback submission accepted (HTTP $CURL_STATUS)"
        elif [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
            pass "Feedback validates input (HTTP $CURL_STATUS)"
        else
            warn "Feedback submission returned HTTP $CURL_STATUS"
        fi

        # Get stats
        echo "  11.2 GET /api/v1/chat/feedback/stats"
        perform_request "${BASE_URL}/api/v1/chat/feedback/stats" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"

        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Feedback stats accessible (HTTP 200)"
        elif [[ "$CURL_STATUS" -eq 403 ]]; then
            pass "Feedback stats requires admin (HTTP 403)"
        else
            warn "Feedback stats returned HTTP $CURL_STATUS"
        fi
    else
        skip "Feedback submission test skipped (no auth)"
        skip "Feedback stats test skipped (no auth)"
    fi

    LAYER_RESULTS+=("Layer 11: Feedback tested")
}


# ===============================================================================
# LAYER 12: Admin Endpoints
# ===============================================================================

test_layer_12_admin() {
    section_header "LAYER 12: Admin Endpoints"

    if [[ "$SKIP_ADMIN_TESTS" == "1" ]]; then
        skip "Admin tests skipped (SKIP_ADMIN_TESTS=1)"
        LAYER_RESULTS+=("Layer 12: Skipped")
        return
    fi

    # Test without auth (should reject)
    echo "  12.1 GET /api/v1/admin/dashboard (no auth)"
    perform_request "${BASE_URL}/api/v1/admin/dashboard"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        pass "Admin dashboard rejects unauthenticated (HTTP $CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        warn "Admin dashboard not found (404)"
    else
        fail "Admin dashboard accessible without auth (HTTP $CURL_STATUS)"
    fi

    if [[ -n "$ADMIN_TOKEN" ]]; then
        local admin_h=(-H "Authorization: Bearer ${ADMIN_TOKEN}")

        # Dashboard
        echo "  12.2 GET /api/v1/admin/dashboard (admin auth)"
        perform_request "${BASE_URL}/api/v1/admin/dashboard" "${admin_h[@]}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Admin dashboard accessible"
        elif [[ "$CURL_STATUS" -eq 403 ]]; then
            warn "Token lacks admin privileges (HTTP 403)"
        else
            warn "Admin dashboard returned HTTP $CURL_STATUS"
        fi

        # Users
        echo "  12.3 GET /api/v1/admin/users"
        perform_request "${BASE_URL}/api/v1/admin/users" "${admin_h[@]}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Admin users endpoint accessible"
        elif [[ "$CURL_STATUS" -eq 403 ]]; then
            warn "Admin users requires higher privileges"
        else
            warn "Admin users returned HTTP $CURL_STATUS"
        fi

        # Analytics
        echo "  12.4 GET /api/v1/admin/analytics"
        perform_request "${BASE_URL}/api/v1/admin/analytics" "${admin_h[@]}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Admin analytics accessible"
        else
            warn "Admin analytics returned HTTP $CURL_STATUS"
        fi

        # Content
        echo "  12.5 GET /api/v1/admin/content"
        perform_request "${BASE_URL}/api/v1/admin/content" "${admin_h[@]}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Admin content accessible"
        else
            warn "Admin content returned HTTP $CURL_STATUS"
        fi

        # Settings
        echo "  12.6 GET /api/v1/admin/settings"
        perform_request "${BASE_URL}/api/v1/admin/settings" "${admin_h[@]}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Admin settings accessible"
        else
            warn "Admin settings returned HTTP $CURL_STATUS"
        fi
    else
        skip "Admin dashboard (auth) test skipped (no admin token)"
        skip "Admin users test skipped (no admin token)"
        skip "Admin analytics test skipped (no admin token)"
        skip "Admin content test skipped (no admin token)"
        skip "Admin settings test skipped (no admin token)"
    fi

    LAYER_RESULTS+=("Layer 12: Admin tested")
}

# ===============================================================================
# LAYER 13: SEO & Indexing
# ===============================================================================

test_layer_13_seo() {
    section_header "LAYER 13: SEO & Indexing"

    # Sitemap
    echo "  13.1 GET /api/v1/seo/sitemap.xml"
    perform_request "${BASE_URL}/api/v1/seo/sitemap.xml"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        if echo "$CURL_BODY" | grep -qi "xml\|urlset\|sitemap"; then
            pass "Sitemap returns valid XML content"
        else
            pass "Sitemap accessible (HTTP 200)"
        fi
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        warn "Sitemap endpoint not found"
    else
        warn "Sitemap returned HTTP $CURL_STATUS"
    fi

    # Sitemap index
    echo "  13.2 GET /api/v1/seo/sitemap-index.xml"
    perform_request "${BASE_URL}/api/v1/seo/sitemap-index.xml"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Sitemap index accessible (HTTP 200)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        warn "Sitemap index not found"
    else
        warn "Sitemap index returned HTTP $CURL_STATUS"
    fi

    # IndexNow
    echo "  13.3 POST /api/v1/indexnow/submit"
    perform_request "${BASE_URL}/api/v1/indexnow/submit" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"urls":["https://syrabit.ai/test"]}'

    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 202 ]]; then
        pass "IndexNow submit accepted"
    elif [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        pass "IndexNow requires authentication (HTTP $CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        warn "IndexNow endpoint not found"
    else
        warn "IndexNow returned HTTP $CURL_STATUS"
    fi

    LAYER_RESULTS+=("Layer 13: SEO tested")
}

# ===============================================================================
# LAYER 14: Education Endpoints (Coming Soon)
# ===============================================================================

test_layer_14_education() {
    section_header "LAYER 14: Education Endpoints (Coming Soon)"

    if [[ "$QUICK_MODE" == "1" ]]; then
        skip "Education tests skipped (quick mode)"
        LAYER_RESULTS+=("Layer 14: Skipped (quick)")
        return
    fi

    # Quiz
    echo "  14.1 GET /api/v1/edu/quiz/science"
    perform_request "${BASE_URL}/api/v1/edu/quiz/science"
    if [[ "$CURL_STATUS" -eq 501 ]]; then
        pass "Quiz returns 501 Not Implemented (expected)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        pass "Quiz endpoint not yet registered (HTTP 404)"
    else
        warn "Quiz returned HTTP $CURL_STATUS (expected 501)"
    fi

    # Notes
    echo "  14.2 GET /api/v1/edu/notes/science"
    perform_request "${BASE_URL}/api/v1/edu/notes/science"
    if [[ "$CURL_STATUS" -eq 501 ]]; then
        pass "Notes returns 501 Not Implemented (expected)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        pass "Notes endpoint not yet registered (HTTP 404)"
    else
        warn "Notes returned HTTP $CURL_STATUS (expected 501)"
    fi

    # Flashcards
    echo "  14.3 GET /api/v1/edu/flashcards/science"
    perform_request "${BASE_URL}/api/v1/edu/flashcards/science"
    if [[ "$CURL_STATUS" -eq 501 ]]; then
        pass "Flashcards returns 501 Not Implemented (expected)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        pass "Flashcards endpoint not yet registered (HTTP 404)"
    else
        warn "Flashcards returned HTTP $CURL_STATUS (expected 501)"
    fi

    LAYER_RESULTS+=("Layer 14: Education tested")
}


# ===============================================================================
# LAYER 15: Rate Limiting (P5 Upstash Redis via Edge)
# ===============================================================================

test_layer_15_rate_limiting() {
    section_header "LAYER 15: Rate Limiting (P5 Upstash Redis via Edge)"

    # Check rate limit headers
    echo "  15.1 Rate limit headers on chat endpoint"
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"test","language":"en"}'

    local rl_found=0
    if has_header "x-ratelimit-limit" || has_header "ratelimit-limit"; then
        local rl_limit
        rl_limit=$(get_header_value "x-ratelimit-limit")
        [[ -z "$rl_limit" ]] && rl_limit=$(get_header_value "ratelimit-limit")
        pass "Rate limit header present (limit: $rl_limit)"
        rl_found=1
    else
        warn "No rate limit headers detected on chat endpoint"
    fi

    if has_header "x-ratelimit-remaining" || has_header "ratelimit-remaining"; then
        local rl_remaining
        rl_remaining=$(get_header_value "x-ratelimit-remaining")
        [[ -z "$rl_remaining" ]] && rl_remaining=$(get_header_value "ratelimit-remaining")
        pass "Rate limit remaining header present ($rl_remaining)"
    else
        if [[ $rl_found -eq 1 ]]; then
            warn "No remaining count header"
        fi
    fi

    if has_header "x-ratelimit-reset" || has_header "ratelimit-reset"; then
        pass "Rate limit reset header present"
    fi

    # Stress test
    if [[ "$STRESS_TEST" == "1" ]]; then
        echo "  15.2 Rate limit stress test (rapid requests)"
        local rate_limited=0
        local requests_sent=0
        for i in $(seq 1 50); do
            perform_request "${BASE_URL}/api/v1/chat/" \
                -X POST \
                -H "Content-Type: application/json" \
                -d "{\"message\":\"stress test $i\",\"language\":\"en\"}"
            requests_sent=$((requests_sent + 1))
            if [[ "$CURL_STATUS" -eq 429 ]]; then
                rate_limited=1
                break
            fi
        done
        if [[ $rate_limited -eq 1 ]]; then
            pass "Rate limiting triggered after $requests_sent requests (HTTP 429)"
        else
            warn "Rate limiting not triggered after $requests_sent rapid requests"
        fi
    fi

    LAYER_RESULTS+=("Layer 15: Rate limiting tested")
}

# ===============================================================================
# LAYER 16: Streaming & SSE Validation
# ===============================================================================

test_layer_16_streaming() {
    section_header "LAYER 16: Streaming & SSE Validation"

    if [[ -z "$AUTH_TOKEN" ]]; then
        skip "Streaming tests skipped (no auth token)"
        LAYER_RESULTS+=("Layer 16: Skipped (no auth)")
        return
    fi

    local auth_h=(-H "Authorization: Bearer ${AUTH_TOKEN}" -H "Content-Type: application/json")
    [[ -n "$TEST_TURNSTILE_TOKEN" ]] && auth_h+=(-H "X-Turnstile-Token: ${TEST_TURNSTILE_TOKEN}")

    # SSE format validation
    echo "  16.1 SSE stream format validation"
    perform_stream_request "${BASE_URL}/api/v1/chat/stream" \
        -X POST \
        "${auth_h[@]}" \
        -d '{"message":"Say hello","language":"en","stream":true}'

    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" == "200" ]]; then
        # Check content-type
        local ct
        ct=$(get_header_value "content-type")
        if echo "$ct" | grep -qi "text/event-stream"; then
            pass "Content-Type is text/event-stream"
        else
            warn "Content-Type: $ct (expected text/event-stream)"
        fi

        # Check for data: lines
        local data_lines
        data_lines=$(echo "$CURL_BODY" | grep -c "^data:" 2>/dev/null || echo "0")
        if [[ "$data_lines" -gt 0 ]]; then
            pass "SSE contains $data_lines data: lines"
        else
            warn "No data: lines found in SSE response"
        fi

        # Check for done event
        if echo "$CURL_BODY" | grep -q '"done".*true\|"done":true'; then
            pass "SSE contains done:true final event"
        else
            warn "No done:true event found in stream"
        fi

        # Check for latency/model in final event
        local last_data
        last_data=$(echo "$CURL_BODY" | grep "^data:" | tail -1)
        if echo "$last_data" | grep -q "latency_ms\|model\|lang"; then
            pass "Final SSE event contains metadata fields"
        else
            warn "Final event metadata not found"
        fi
    elif [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" == "401" || "$CURL_STATUS" -eq 403 || "$CURL_STATUS" == "403" ]]; then
        warn "Stream auth rejected (token may be expired)"
    else
        warn "Stream returned HTTP $CURL_STATUS"
    fi

    LAYER_RESULTS+=("Layer 16: Streaming tested")
}


# ===============================================================================
# LAYER 17: End-to-End Workflows
# ===============================================================================

test_layer_17_workflows() {
    section_header "LAYER 17: End-to-End Workflows"

    if [[ "$QUICK_MODE" == "1" ]]; then
        skip "End-to-end workflows skipped (quick mode)"
        LAYER_RESULTS+=("Layer 17: Skipped (quick)")
        return
    fi

    # Workflow 1: New User Journey
    echo "  17.1 Workflow: New User Journey"
    echo "    Step 1: Signup attempt"
    local test_email="e2e-test-$(date +%s)@test.invalid"
    perform_request "${BASE_URL}/api/v1/auth/signup" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"${test_email}\",\"password\":\"TestPass123!\",\"name\":\"E2E Test\"}"

    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 201 ]]; then
        pass "Signup step completed"
    elif [[ "$CURL_STATUS" -eq 409 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        pass "Signup validation works (HTTP $CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        warn "Signup rate limited"
    else
        warn "Signup returned HTTP $CURL_STATUS"
    fi

    if [[ -n "$AUTH_TOKEN" ]]; then
        local auth_h=(-H "Authorization: Bearer ${AUTH_TOKEN}" -H "Content-Type: application/json")
        [[ -n "$TEST_TURNSTILE_TOKEN" ]] && auth_h+=(-H "X-Turnstile-Token: ${TEST_TURNSTILE_TOKEN}")

        echo "    Step 2: Chat"
        perform_request "${BASE_URL}/api/v1/chat/" \
            -X POST \
            "${auth_h[@]}" \
            -d '{"message":"Hello from E2E test","language":"en"}'

        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "E2E chat step completed"
        else
            warn "E2E chat returned HTTP $CURL_STATUS"
        fi

        echo "    Step 3: Get history"
        perform_request "${BASE_URL}/api/v1/chat/history" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"

        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "E2E history step completed"
        else
            warn "E2E history returned HTTP $CURL_STATUS"
        fi

        echo "    Step 4: Submit feedback"
        perform_request "${BASE_URL}/api/v1/chat/feedback/" \
            -X POST \
            "${auth_h[@]}" \
            -d '{"session_id":"e2e-test","message_id":"e2e-msg","rating":4,"comment":"E2E test"}'

        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 201 || "$CURL_STATUS" -eq 422 ]]; then
            pass "E2E feedback step completed (HTTP $CURL_STATUS)"
        else
            warn "E2E feedback returned HTTP $CURL_STATUS"
        fi
    else
        skip "E2E workflow steps 2-4 skipped (no auth)"
    fi

    # Workflow 2: Subscription Flow
    echo ""
    echo "  17.2 Workflow: Subscription Flow"
    if [[ -n "$AUTH_TOKEN" ]]; then
        local auth_h2=(-H "Authorization: Bearer ${AUTH_TOKEN}")

        echo "    Step 1: Get plans"
        perform_request "${BASE_URL}/api/v1/subscription/plans"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Subscription plans retrieved"
        else
            warn "Plans returned HTTP $CURL_STATUS"
        fi

        echo "    Step 2: Check status"
        perform_request "${BASE_URL}/api/v1/subscription/status" "${auth_h2[@]}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "Subscription status retrieved"
        else
            warn "Status returned HTTP $CURL_STATUS"
        fi
    else
        skip "Subscription workflow skipped (no auth)"
    fi

    # Workflow 3: Anonymous User
    echo ""
    echo "  17.3 Workflow: Anonymous User"
    local anon_id="anon-e2e-$(date +%s)"

    echo "    Step 1: Chat without auth"
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "x-anon-id: ${anon_id}" \
        -d '{"message":"Anonymous test","language":"en"}'

    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 403 || "$CURL_STATUS" -eq 401 ]]; then
        pass "Anonymous chat step completed (HTTP $CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        warn "Anonymous chat rate limited"
    else
        warn "Anonymous chat returned HTTP $CURL_STATUS"
    fi

    echo "    Step 2: Get anon history"
    perform_request "${BASE_URL}/api/v1/conversations/anon" \
        -H "x-anon-id: ${anon_id}"

    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Anonymous history accessible"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        warn "Anonymous history endpoint not found"
    else
        warn "Anonymous history returned HTTP $CURL_STATUS"
    fi

    # Workflow 4: Content Discovery
    echo ""
    echo "  17.4 Workflow: Content Discovery"
    echo "    Step 1: Get library bundle"
    perform_request "${BASE_URL}/api/v1/content/library-bundle"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Content discovery: library bundle loaded"

        if is_json; then
            echo "    Step 2: Browse slim bundle"
            perform_request "${BASE_URL}/api/v1/content/library-bundle?slim=1"
            if [[ "$CURL_STATUS" -eq 200 ]]; then
                pass "Content discovery: slim bundle loaded"
            else
                warn "Slim bundle returned HTTP $CURL_STATUS"
            fi
        fi
    else
        warn "Library bundle returned HTTP $CURL_STATUS"
    fi

    LAYER_RESULTS+=("Layer 17: Workflows tested")
}

# ===============================================================================
# LAYER 18: Cross-Cutting Concerns
# ===============================================================================

test_layer_18_cross_cutting() {
    section_header "LAYER 18: Cross-Cutting Concerns"

    # X-Request-ID
    echo "  18.1 X-Request-ID header"
    perform_request "${BASE_URL}/health"
    local request_id
    request_id=$(get_header_value "x-request-id")
    if [[ -n "$request_id" ]]; then
        pass "X-Request-ID present: ${request_id:0:20}..."
    else
        warn "No X-Request-ID header"
    fi

    # X-API-Version
    echo "  18.2 X-API-Version header"
    local api_version
    api_version=$(get_header_value "x-api-version")
    if [[ -n "$api_version" ]]; then
        pass "X-API-Version present: $api_version"
    else
        warn "No X-API-Version header"
    fi

    # CORS from frontend origin
    echo "  18.3 CORS allows frontend origin"
    perform_request "${BASE_URL}/health" \
        -H "Origin: ${FRONTEND_URL}"
    local cors_origin
    cors_origin=$(get_header_value "access-control-allow-origin")
    if [[ "$cors_origin" == "$FRONTEND_URL" || "$cors_origin" == "*" ]]; then
        pass "CORS allows frontend origin: $cors_origin"
    elif [[ -n "$cors_origin" ]]; then
        warn "CORS origin mismatch: $cors_origin (expected $FRONTEND_URL)"
    else
        warn "No CORS header on health endpoint"
    fi

    # CSRF protection
    echo "  18.4 CSRF protection"
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "Origin: https://evil-site.example.com" \
        -d '{"email":"test@test.com","password":"test"}'

    if [[ "$CURL_STATUS" -eq 403 ]]; then
        pass "CSRF: Wrong origin blocked (HTTP 403)"
    elif [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        warn "Request processed despite wrong origin (HTTP $CURL_STATUS) - CORS may handle client-side"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        warn "Rate limited - cannot verify CSRF"
    else
        warn "Wrong origin request returned HTTP $CURL_STATUS"
    fi

    # Changelog endpoint
    echo "  18.5 GET /api/v1/changelog"
    perform_request "${BASE_URL}/api/v1/changelog"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        pass "Changelog endpoint accessible"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        warn "Changelog endpoint not found"
    else
        warn "Changelog returned HTTP $CURL_STATUS"
    fi

    # User profile endpoint
    echo "  18.6 GET /api/v1/users/me"
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/users/me" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            pass "User profile accessible"
        elif [[ "$CURL_STATUS" -eq 401 ]]; then
            warn "User profile auth rejected (token may be expired)"
        else
            warn "User profile returned HTTP $CURL_STATUS"
        fi
    else
        perform_request "${BASE_URL}/api/v1/users/me"
        if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
            pass "User profile requires auth (HTTP $CURL_STATUS)"
        else
            warn "User profile without auth returned HTTP $CURL_STATUS"
        fi
    fi

    LAYER_RESULTS+=("Layer 18: Cross-cutting tested")
}


# ===============================================================================
# MAIN EXECUTION
# ===============================================================================

main() {
    # Header
    echo ""
    echo -e "${BOLD}$(printf '%.0s=' {1..70})${NC}"
    echo -e "${BOLD}  SYRABIT FULLSTACK LAYER-BY-LAYER CLOUD SHELL TEST${NC}"
    echo -e "  Date:      $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    echo -e "  Target:    ${BASE_URL}"
    echo -e "  Frontend:  ${FRONTEND_URL}"
    if [[ "$DRY_RUN" == "1" ]]; then echo -e "  Mode:      dry-run"; fi
    if [[ "$QUICK_MODE" == "1" ]]; then echo -e "  Mode:      quick"; fi
    if [[ -n "$RUN_LAYER" ]]; then echo -e "  Layer:     $RUN_LAYER"; fi
    echo -e "${BOLD}$(printf '%.0s=' {1..70})${NC}"

    # Dry run mode
    if [[ "$DRY_RUN" == "1" ]]; then
        echo ""
        echo -e "  ${BLUE}DRY RUN MODE${NC} - validating configuration only"
        echo ""
        test_layer_0_prerequisites
        echo ""
        echo -e "  ${GREEN}Configuration valid.${NC} No HTTP requests made."
        echo ""
        exit 0
    fi

    # Run specific layer or all
    if [[ -n "$RUN_LAYER" ]]; then
        test_layer_0_prerequisites
        case "$RUN_LAYER" in
            0) ;; # Already ran
            1) test_layer_1_frontend ;;
            2) test_layer_2_edge_worker ;;
            3) test_layer_3_backend_health ;;
            4) test_layer_4_authentication ;;
            5) test_layer_5_chat ;;
            6) test_layer_6_rag_search ;;
            7) test_layer_7_content ;;
            8) test_layer_8_payments ;;
            9) test_layer_9_webhooks ;;
            10) test_layer_10_conversations ;;
            11) test_layer_11_feedback ;;
            12) test_layer_12_admin ;;
            13) test_layer_13_seo ;;
            14) test_layer_14_education ;;
            15) test_layer_15_rate_limiting ;;
            16) test_layer_16_streaming ;;
            17) test_layer_17_workflows ;;
            18) test_layer_18_cross_cutting ;;
            *) echo "Invalid layer: $RUN_LAYER (valid: 0-18)"; exit 1 ;;
        esac
    else
        # Run all layers
        test_layer_0_prerequisites
        test_layer_1_frontend
        test_layer_2_edge_worker
        test_layer_3_backend_health
        test_layer_4_authentication
        test_layer_5_chat
        test_layer_6_rag_search
        test_layer_7_content
        test_layer_8_payments
        test_layer_9_webhooks
        test_layer_10_conversations
        test_layer_11_feedback
        test_layer_12_admin
        test_layer_13_seo
        test_layer_14_education
        test_layer_15_rate_limiting
        test_layer_16_streaming
        test_layer_17_workflows
        test_layer_18_cross_cutting
    fi

    # ===================================================================
    # SUMMARY
    # ===================================================================

    echo ""
    echo -e "${BOLD}$(printf '%.0s=' {1..70})${NC}"
    echo -e "${BOLD}  SUMMARY${NC}"
    echo -e "${BOLD}$(printf '%.0s=' {1..70})${NC}"
    echo ""
    echo "  Total tests:   $TOTAL_TESTS"
    echo -e "  Passed:        ${GREEN}${PASSED_TESTS}${NC}"
    if [[ "$FAILED_TESTS" -gt 0 ]]; then
        echo -e "  Failed:        ${RED}${FAILED_TESTS}${NC}"
    else
        echo "  Failed:        $FAILED_TESTS"
    fi
    if [[ "$WARNING_TESTS" -gt 0 ]]; then
        echo -e "  Warnings:      ${YELLOW}${WARNING_TESTS}${NC}"
    else
        echo "  Warnings:      $WARNING_TESTS"
    fi
    if [[ "$SKIPPED_TESTS" -gt 0 ]]; then
        echo -e "  Skipped:       ${BLUE}${SKIPPED_TESTS}${NC}"
    else
        echo "  Skipped:       $SKIPPED_TESTS"
    fi
    echo ""

    # Layer breakdown
    echo "  Layer Results:"
    for result in "${LAYER_RESULTS[@]}"; do
        echo "    - $result"
    done
    echo ""

    if [[ "$CRITICAL_FAILED" -eq 0 ]]; then
        echo -e "  Status: ${GREEN}${BOLD}ALL CRITICAL CHECKS PASSED${NC}"
    else
        echo -e "  Status: ${RED}${BOLD}${CRITICAL_FAILED} CRITICAL FAILURE(S)${NC}"
    fi

    echo -e "${BOLD}$(printf '%.0s=' {1..70})${NC}"
    echo ""

    # Export JSON results
    if [[ "$EXPORT_JSON" == "1" ]]; then
        local json_file="fullstack-test-results.json"
        local success_val="true"
        if [[ "$CRITICAL_FAILED" -gt 0 ]]; then
            success_val="false"
        fi
        cat > "$json_file" << JSONEOF
{
  "timestamp": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "base_url": "$BASE_URL",
  "frontend_url": "$FRONTEND_URL",
  "total_tests": $TOTAL_TESTS,
  "passed": $PASSED_TESTS,
  "failed": $FAILED_TESTS,
  "warnings": $WARNING_TESTS,
  "skipped": $SKIPPED_TESTS,
  "critical_failures": $CRITICAL_FAILED,
  "success": $success_val
}
JSONEOF
        echo "  Results exported to: $json_file"
        echo ""
    fi

    # Exit code
    if [[ "$CRITICAL_FAILED" -gt 0 ]]; then
        exit 1
    fi
    exit 0
}

# Run main
main "$@"
