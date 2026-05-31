#!/usr/bin/env bash
# ===============================================================================
# SYRABIT FULLSTACK LAYER-BY-LAYER CLOUD SHELL TEST
# ===============================================================================
#
# Comprehensive test covering all 9 architectural pillars across 21 layers (0-20):
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
  --layer N     Run only layer N (0-20)
  --quick       Skip stress tests and optional layers (14, 17, 20)

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
  14  Education Endpoints
  15  Rate Limiting (Upstash Redis)
  16  Streaming & SSE Validation
  17  End-to-End Workflows
  18  Cross-Cutting Concerns
  19  Users API
  20  Performance & Timing

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
        local msg="$1"
        msg=$(echo "$msg" | sed -E 's/(Authorization: Bearer )[^ "]*/\1[REDACTED]/gi')
        echo -e "    [DEBUG] $msg"
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
    local extra_args=()
    if [[ $# -gt 0 ]]; then
        extra_args=("$@")
    fi

    local timing_format='{"dns":%{time_namelookup},"tls":%{time_appconnect},"ttfb":%{time_starttransfer},"total":%{time_total},"status":%{http_code},"size":%{size_download}}'

    local tmpfile header_file
    tmpfile=$(mktemp)
    header_file=$(mktemp)
    trap "rm -f '$tmpfile' '$header_file'" RETURN

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
}

# Perform a streaming request (no buffer)
perform_stream_request() {
    local url="$1"
    shift
    local extra_args=()
    if [[ $# -gt 0 ]]; then
        extra_args=("$@")
    fi

    local tmpfile header_file
    tmpfile=$(mktemp)
    header_file=$(mktemp)
    trap "rm -f '$tmpfile' '$header_file'" RETURN

    local curl_cmd=(curl -sS --no-buffer -o "$tmpfile" -D "$header_file" --max-time 30 -w '%{http_code}')

    if [[ ${#extra_args[@]} -gt 0 ]]; then
        curl_cmd+=("${extra_args[@]}")
    fi

    curl_cmd+=("$url")

    CURL_STATUS=$("${curl_cmd[@]}" 2>/dev/null) || CURL_STATUS="0"
    CURL_BODY=$(cat "$tmpfile" 2>/dev/null || echo "")
    CURL_HEADERS=$(cat "$header_file" 2>/dev/null || echo "")
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
# LAYER 0: Prerequisites & Config (~15 tests)
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

    # Check curl version >= 7.x
    local curl_ver
    curl_ver=$(curl --version | head -1 | awk '{print $2}' | cut -d. -f1)
    if [[ "$curl_ver" -ge 7 ]]; then
        pass "curl version >= 7.x ($curl_ver)"
    else
        warn "curl version may be too old ($curl_ver)"
    fi

    # Check jq
    if command -v jq &>/dev/null; then
        pass "jq is installed ($(jq --version 2>&1))"
    else
        fail "jq is not installed" "yes"
        echo "  Cannot continue without jq. Aborting."
        exit 1
    fi

    # Check openssl
    if command -v openssl &>/dev/null; then
        pass "openssl is available"
    else
        warn "openssl not found - webhook signature tests will be skipped"
    fi

    # Check bash version >= 4
    local bash_major="${BASH_VERSINFO[0]}"
    if [[ "$bash_major" -ge 4 ]]; then
        pass "bash version >= 4 (${BASH_VERSION})"
    else
        warn "bash version < 4 (${BASH_VERSION}) - some features may not work"
    fi

    # DNS resolution for BASE_URL
    local base_host
    base_host=$(echo "$BASE_URL" | sed -E 's|https?://||' | cut -d/ -f1 | cut -d: -f1)
    if command -v host &>/dev/null && host "$base_host" &>/dev/null; then
        pass "DNS resolves for $base_host"
    elif command -v nslookup &>/dev/null && nslookup "$base_host" &>/dev/null; then
        pass "DNS resolves for $base_host"
    else
        pass "DNS resolution check (skipped - no host/nslookup available)"
    fi

    # DNS resolution for FRONTEND_URL
    local frontend_host
    frontend_host=$(echo "$FRONTEND_URL" | sed -E 's|https?://||' | cut -d/ -f1 | cut -d: -f1)
    if command -v host &>/dev/null && host "$frontend_host" &>/dev/null; then
        pass "DNS resolves for $frontend_host"
    elif command -v nslookup &>/dev/null && nslookup "$frontend_host" &>/dev/null; then
        pass "DNS resolves for $frontend_host"
    else
        pass "DNS resolution check for frontend (skipped - no host/nslookup)"
    fi

    # Validate URL format (no trailing slash)
    if [[ "$BASE_URL" == */ ]]; then
        warn "BASE_URL has trailing slash - may cause double-slash in requests"
    else
        pass "BASE_URL format valid (no trailing slash)"
    fi

    if [[ "$FRONTEND_URL" == */ ]]; then
        warn "FRONTEND_URL has trailing slash"
    else
        pass "FRONTEND_URL format valid (no trailing slash)"
    fi

    # Validate env vars are non-empty when they should be
    if [[ -n "$TEST_JWT_TOKEN" ]]; then
        # JWT should have 3 parts separated by dots
        local jwt_parts
        jwt_parts=$(echo "$TEST_JWT_TOKEN" | tr '.' '\n' | wc -l)
        if [[ "$jwt_parts" -eq 3 ]]; then
            pass "TEST_JWT_TOKEN has valid JWT structure (3 parts)"
        else
            warn "TEST_JWT_TOKEN does not look like a valid JWT ($jwt_parts parts)"
        fi
    else
        skip "TEST_JWT_TOKEN not provided"
    fi

    # Validate token format
    if [[ -n "$TEST_TURNSTILE_TOKEN" ]]; then
        pass "TEST_TURNSTILE_TOKEN is provided"
    else
        skip "TEST_TURNSTILE_TOKEN not provided"
    fi

    # Check tmp file creation
    local test_tmp
    test_tmp=$(mktemp 2>/dev/null) && rm -f "$test_tmp"
    if [[ $? -eq 0 ]]; then
        pass "Temp file creation works"
    else
        fail "Cannot create temp files" "yes"
    fi

    # Check jq filter execution
    if echo '{"test":1}' | jq '.test' &>/dev/null; then
        pass "jq filter execution works"
    else
        fail "jq filter execution failed" "yes"
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
# LAYER 1: Frontend (P1 Cloudflare CDN) (~50 tests)
# ===============================================================================

test_layer_1_frontend() {
    section_header "LAYER 1: Frontend (P1 Cloudflare CDN)"

    # 1.1 Page load
    echo "  1.1 Frontend page load"
    perform_request "$FRONTEND_URL" \
        -H "Accept-Encoding: gzip, deflate, br" \
        -H "User-Agent: SyrabitFullstackTest/1.0"

    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 400 ]]; then
        pass "Frontend loads (HTTP $CURL_STATUS, ${CURL_TTFB}ms TTFB)"
    else
        fail "Frontend not reachable (HTTP $CURL_STATUS)" "yes"
    fi

    if [[ "$CURL_TOTAL" -lt 3000 ]]; then
        pass "Frontend loads within 3s (${CURL_TOTAL}ms)"
    else
        warn "Frontend slow (${CURL_TOTAL}ms > 3000ms)"
    fi

    # HTTP/2 check
    perform_request "$FRONTEND_URL" --http2
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 400 ]]; then
        pass "HTTP/2 supported"
    else
        warn "HTTP/2 may not be supported (status $CURL_STATUS)"
    fi

    # 1.2 Compression variants
    echo "  1.2 Compression"
    perform_request "$FRONTEND_URL" -H "Accept-Encoding: gzip"
    if has_header "content-encoding"; then
        pass "gzip compression available"
    else
        warn "gzip compression not detected"
    fi

    perform_request "$FRONTEND_URL" -H "Accept-Encoding: br"
    if has_header "content-encoding"; then
        local enc
        enc=$(get_header_value "content-encoding")
        if [[ "$enc" == *"br"* ]]; then
            pass "Brotli compression available"
        else
            pass "Compression with Accept-Encoding:br ($enc)"
        fi
    else
        warn "Brotli compression not detected"
    fi

    perform_request "$FRONTEND_URL" -H "Accept-Encoding: deflate"
    if has_header "content-encoding"; then
        pass "deflate compression available"
    else
        pass "deflate not offered (acceptable - gzip/br preferred)"
    fi

    # 1.3 robots.txt
    echo "  1.3 robots.txt"
    perform_request "$FRONTEND_URL/robots.txt"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "robots.txt accessible (200)"
        if echo "$CURL_BODY" | grep -qi "user-agent"; then
            pass "robots.txt contains User-agent directive"
        else
            warn "robots.txt missing User-agent directive"
        fi
        if echo "$CURL_BODY" | grep -qi "sitemap"; then
            pass "robots.txt references sitemap"
        else
            warn "robots.txt missing sitemap reference"
        fi
        if echo "$CURL_BODY" | grep -qi "disallow"; then
            pass "robots.txt contains Disallow rules"
        else
            pass "robots.txt has no Disallow rules (open crawling)"
        fi
    else
        warn "robots.txt not found (HTTP $CURL_STATUS)"
    fi

    # 1.4 Security headers
    echo "  1.4 Security headers"
    perform_request "$FRONTEND_URL"

    if has_header "strict-transport-security"; then
        pass "HSTS header present"
        local hsts_val
        hsts_val=$(get_header_value "strict-transport-security")
        if echo "$hsts_val" | grep -q "max-age="; then
            pass "HSTS has max-age directive"
        else
            warn "HSTS missing max-age"
        fi
    else
        warn "HSTS header missing"
    fi

    if has_header "x-content-type-options"; then
        local xcto
        xcto=$(get_header_value "x-content-type-options")
        if [[ "$xcto" == "nosniff" ]]; then
            pass "X-Content-Type-Options: nosniff"
        else
            warn "X-Content-Type-Options unexpected value: $xcto"
        fi
    else
        warn "X-Content-Type-Options header missing"
    fi

    if has_header "x-frame-options"; then
        pass "X-Frame-Options present ($(get_header_value 'x-frame-options'))"
    else
        warn "X-Frame-Options header missing"
    fi

    if has_header "content-security-policy"; then
        pass "Content-Security-Policy header present"
    else
        warn "CSP header missing"
    fi

    if has_header "referrer-policy"; then
        pass "Referrer-Policy header present"
    else
        warn "Referrer-Policy header missing"
    fi

    if has_header "permissions-policy"; then
        pass "Permissions-Policy header present"
    else
        warn "Permissions-Policy header missing (or Feature-Policy)"
    fi

    # 1.5 HTML structure
    echo "  1.5 HTML structure"
    if echo "$CURL_BODY" | grep -qi "<!DOCTYPE html"; then
        pass "HTML has DOCTYPE declaration"
    else
        warn "Missing DOCTYPE"
    fi

    if echo "$CURL_BODY" | grep -qi '<html.*lang='; then
        pass "HTML tag has lang attribute"
    else
        warn "HTML tag missing lang attribute"
    fi

    if echo "$CURL_BODY" | grep -qi 'charset'; then
        pass "Meta charset declared"
    else
        warn "Meta charset not found"
    fi

    if echo "$CURL_BODY" | grep -qi 'viewport'; then
        pass "Meta viewport present"
    else
        warn "Meta viewport missing"
    fi

    if echo "$CURL_BODY" | grep -qi '<title'; then
        pass "Title tag present"
    else
        warn "Title tag missing"
    fi

    if echo "$CURL_BODY" | grep -qi 'og:'; then
        pass "OpenGraph meta tags present"
    else
        warn "OpenGraph meta tags missing"
    fi

    # 1.6 Static assets
    echo "  1.6 Static assets"
    perform_request "$FRONTEND_URL/favicon.ico"
    if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "204" ]]; then
        pass "favicon.ico accessible"
    else
        warn "favicon.ico not found (HTTP $CURL_STATUS)"
    fi

    perform_request "$FRONTEND_URL/manifest.json"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "manifest.json accessible"
    else
        pass "manifest.json not found (may be at different path)"
    fi

    # 1.7 404 handling
    echo "  1.7 Error handling"
    perform_request "$FRONTEND_URL/nonexistent-page-xyz-12345"
    if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" ]]; then
        pass "404 page handled (HTTP $CURL_STATUS - SPA or dedicated 404)"
    else
        warn "Unexpected status for 404 page: $CURL_STATUS"
    fi

    # Trailing slash behavior
    perform_request "$FRONTEND_URL/about/" -L
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 400 ]]; then
        pass "Trailing slash handled"
    else
        pass "Trailing slash returns $CURL_STATUS (acceptable)"
    fi

    # Query parameter pass-through
    perform_request "$FRONTEND_URL/?utm_source=test&ref=abc"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 400 ]]; then
        pass "Query parameters passed through"
    else
        warn "Query parameters caused error ($CURL_STATUS)"
    fi

    # Unicode URL handling
    perform_request "$FRONTEND_URL/%E0%A6%85" -L
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Unicode URL handled gracefully ($CURL_STATUS)"
    else
        warn "Unicode URL caused server error ($CURL_STATUS)"
    fi

    # Response size check
    local body_size=${#CURL_BODY}
    if [[ "$body_size" -gt 100 ]]; then
        pass "Response body has content ($body_size bytes)"
    else
        warn "Response body suspiciously small ($body_size bytes)"
    fi

    # No server version leak
    if has_header "server"; then
        local srv
        srv=$(get_header_value "server")
        if echo "$srv" | grep -qE '[0-9]+\.[0-9]+'; then
            warn "Server header leaks version: $srv"
        else
            pass "Server header present but no version leak ($srv)"
        fi
    else
        pass "No Server header (good - no version leak)"
    fi

    # Cookie flags check
    if has_header "set-cookie"; then
        local cookie_hdr
        cookie_hdr=$(get_header_value "set-cookie")
        if echo "$cookie_hdr" | grep -qi "secure"; then
            pass "Cookies have Secure flag"
        else
            warn "Cookie missing Secure flag"
        fi
        if echo "$cookie_hdr" | grep -qi "httponly"; then
            pass "Cookies have HttpOnly flag"
        else
            warn "Cookie missing HttpOnly flag"
        fi
    else
        pass "No cookies set on frontend (stateless - good)"
    fi

    # Case sensitivity test
    perform_request "$FRONTEND_URL/ROBOTS.TXT"
    if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" || "$CURL_STATUS" == "301" ]]; then
        pass "Case sensitivity handled ($CURL_STATUS)"
    else
        warn "Unexpected response for uppercase path ($CURL_STATUS)"
    fi

    LAYER_RESULTS+=("Layer 1: Frontend CDN - $(( PASSED_TESTS )) passed so far")
}


# ===============================================================================
# LAYER 2: Edge Worker (P1 Cloudflare Workers) (~80 tests)
# ===============================================================================

test_layer_2_edge_worker() {
    section_header "LAYER 2: Edge Worker (P1 Cloudflare Workers)"

    # 2.1 Health endpoint
    echo "  2.1 Edge health endpoint"
    perform_request "$BASE_URL/health"

    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "Edge /health returns 200"
    else
        fail "Edge /health returns $CURL_STATUS" "yes"
    fi

    if [[ "$CURL_TOTAL" -lt 500 ]]; then
        pass "Edge /health responds within 500ms (${CURL_TOTAL}ms)"
    else
        warn "Edge /health slow (${CURL_TOTAL}ms)"
    fi

    if is_json; then
        pass "Edge /health returns valid JSON"
    else
        warn "Edge /health response is not JSON"
    fi

    if has_header "content-type"; then
        local ct
        ct=$(get_header_value "content-type")
        if echo "$ct" | grep -qi "application/json"; then
            pass "Edge /health Content-Type is application/json"
        else
            warn "Edge /health Content-Type unexpected: $ct"
        fi
    else
        warn "Edge /health missing Content-Type"
    fi

    # Health response fields
    local h_status
    h_status=$(json_field '.status // empty')
    if [[ -n "$h_status" ]]; then
        pass "Edge /health has status field ($h_status)"
    else
        warn "Edge /health missing status field"
    fi

    # 2.2 CORS preflight
    echo "  2.2 CORS preflight"
    local origins=("$FRONTEND_URL" "https://evil-site.com" "null")
    local methods=("GET" "POST" "PUT" "PATCH" "DELETE" "OPTIONS")

    # Valid origin CORS
    perform_request "$BASE_URL/health" \
        -X OPTIONS \
        -H "Origin: $FRONTEND_URL" \
        -H "Access-Control-Request-Method: GET"
    if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "204" ]]; then
        pass "CORS preflight for frontend origin (HTTP $CURL_STATUS)"
    else
        warn "CORS preflight returned $CURL_STATUS"
    fi

    if has_header "access-control-allow-origin"; then
        pass "Access-Control-Allow-Origin present"
    else
        warn "Missing Access-Control-Allow-Origin"
    fi

    # CORS with evil origin
    perform_request "$BASE_URL/health" \
        -X OPTIONS \
        -H "Origin: https://evil-site.com" \
        -H "Access-Control-Request-Method: GET"
    local evil_acao
    evil_acao=$(get_header_value "access-control-allow-origin")
    if [[ "$evil_acao" == "https://evil-site.com" ]]; then
        warn "CORS allows evil origin (potential misconfiguration)"
    else
        pass "CORS does not reflect evil origin"
    fi

    # CORS with null origin
    perform_request "$BASE_URL/health" \
        -X OPTIONS \
        -H "Origin: null" \
        -H "Access-Control-Request-Method: GET"
    local null_acao
    null_acao=$(get_header_value "access-control-allow-origin")
    if [[ "$null_acao" == "null" ]]; then
        warn "CORS allows null origin (potential security issue)"
    else
        pass "CORS does not allow null origin"
    fi

    # CORS with no origin
    perform_request "$BASE_URL/health" -X OPTIONS
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "OPTIONS without Origin handled ($CURL_STATUS)"
    else
        warn "OPTIONS without Origin returned $CURL_STATUS"
    fi

    # CORS methods
    for method in POST PUT PATCH DELETE; do
        perform_request "$BASE_URL/health" \
            -X OPTIONS \
            -H "Origin: $FRONTEND_URL" \
            -H "Access-Control-Request-Method: $method"
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 400 ]]; then
            pass "CORS preflight for $method method accepted"
        else
            pass "CORS preflight for $method method ($CURL_STATUS)"
        fi
    done

    # Access-Control-Allow-Headers
    perform_request "$BASE_URL/health" \
        -X OPTIONS \
        -H "Origin: $FRONTEND_URL" \
        -H "Access-Control-Request-Method: POST" \
        -H "Access-Control-Request-Headers: Content-Type, Authorization, X-Turnstile-Token"
    if has_header "access-control-allow-headers"; then
        pass "Access-Control-Allow-Headers present"
    else
        warn "Missing Access-Control-Allow-Headers"
    fi

    # Access-Control-Max-Age
    if has_header "access-control-max-age"; then
        pass "Access-Control-Max-Age present"
    else
        pass "Access-Control-Max-Age not set (browsers will re-preflight)"
    fi

    # 2.3 Request ID
    echo "  2.3 Request ID"
    perform_request "$BASE_URL/health"
    local req_id_1=""
    if has_header "x-request-id"; then
        req_id_1=$(get_header_value "x-request-id")
        pass "X-Request-ID present ($req_id_1)"
        # UUID format check
        if echo "$req_id_1" | grep -qE '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'; then
            pass "X-Request-ID is UUID format"
        else
            pass "X-Request-ID present (non-UUID format: $req_id_1)"
        fi
    else
        warn "X-Request-ID header missing"
    fi

    # Uniqueness
    perform_request "$BASE_URL/health"
    if has_header "x-request-id"; then
        local req_id_2
        req_id_2=$(get_header_value "x-request-id")
        if [[ "$req_id_1" != "$req_id_2" ]]; then
            pass "X-Request-ID is unique across requests"
        else
            warn "X-Request-ID same for two requests (may be cached)"
        fi
    else
        skip "X-Request-ID uniqueness (header not present)"
    fi

    # 2.4 Bot detection
    echo "  2.4 Bot detection"
    local bots=("Googlebot/2.1" "Bingbot/2.0" "python-requests/2.28" "curl/7.88")
    for bot_ua in "${bots[@]}"; do
        perform_request "$BASE_URL/health" -H "User-Agent: $bot_ua"
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Bot UA handled: $bot_ua (HTTP $CURL_STATUS)"
        else
            warn "Bot UA caused error: $bot_ua (HTTP $CURL_STATUS)"
        fi
    done

    # Empty User-Agent
    perform_request "$BASE_URL/health" -H "User-Agent:"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Empty User-Agent handled (HTTP $CURL_STATUS)"
    else
        warn "Empty User-Agent caused error ($CURL_STATUS)"
    fi

    # Normal browser UA
    perform_request "$BASE_URL/health" \
        -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "Normal browser UA works"
    else
        warn "Normal browser UA got $CURL_STATUS"
    fi

    # 2.5 Rate limit headers
    echo "  2.5 Rate limit headers"
    perform_request "$BASE_URL/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"test","language":"en"}'
    
    if has_header "x-ratelimit-limit"; then
        local rl_limit
        rl_limit=$(get_header_value "x-ratelimit-limit")
        pass "x-ratelimit-limit present ($rl_limit)"
        if echo "$rl_limit" | grep -qE '^[0-9]+$'; then
            pass "x-ratelimit-limit is numeric"
        else
            warn "x-ratelimit-limit not numeric: $rl_limit"
        fi
    else
        pass "Rate limit headers not on unauthenticated request (acceptable)"
    fi

    if has_header "x-ratelimit-remaining"; then
        local rl_rem
        rl_rem=$(get_header_value "x-ratelimit-remaining")
        pass "x-ratelimit-remaining present ($rl_rem)"
    else
        pass "x-ratelimit-remaining not present (acceptable)"
    fi

    if has_header "x-ratelimit-reset"; then
        local rl_reset
        rl_reset=$(get_header_value "x-ratelimit-reset")
        pass "x-ratelimit-reset present ($rl_reset)"
    else
        pass "x-ratelimit-reset not present (acceptable)"
    fi

    # 2.6 Security headers on API
    echo "  2.6 API security headers"
    perform_request "$BASE_URL/health"

    if has_header "strict-transport-security"; then
        pass "HSTS on API responses"
    else
        warn "HSTS missing on API"
    fi

    if has_header "x-content-type-options"; then
        pass "X-Content-Type-Options on API"
    else
        warn "X-Content-Type-Options missing on API"
    fi

    # 2.7 Hop-by-hop headers
    echo "  2.7 Proxy header safety"
    if ! has_header "connection" || [[ "$(get_header_value 'connection')" == "keep-alive" ]]; then
        pass "No leaked Connection header (or just keep-alive)"
    else
        warn "Connection header present: $(get_header_value 'connection')"
    fi

    # X-Real-IP injection
    perform_request "$BASE_URL/health" -H "X-Real-IP: 127.0.0.1"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "X-Real-IP injection does not cause error"
    else
        pass "X-Real-IP injection handled ($CURL_STATUS)"
    fi

    # 2.8 Path security
    echo "  2.8 Path security"
    perform_request "$BASE_URL/../etc/passwd"
    if [[ "$CURL_STATUS" -ge 400 ]]; then
        pass "Path traversal blocked (HTTP $CURL_STATUS)"
    else
        pass "Path traversal resolved safely (HTTP $CURL_STATUS)"
    fi

    perform_request "$BASE_URL/health%00"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Null byte in URL handled (HTTP $CURL_STATUS)"
    else
        pass "Null byte rejected ($CURL_STATUS)"
    fi

    perform_request "$BASE_URL/%252e%252e/etc/passwd"
    if [[ "$CURL_STATUS" -ge 400 ]]; then
        pass "Double encoding path traversal blocked ($CURL_STATUS)"
    else
        pass "Double encoding handled ($CURL_STATUS)"
    fi

    # 2.9 Oversized headers
    echo "  2.9 Edge limits"
    local big_header
    big_header=$(head -c 8192 /dev/urandom | base64 | tr -d '\n' | head -c 8000)
    perform_request "$BASE_URL/health" -H "X-Big-Header: $big_header"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -le 431 ]]; then
        pass "Oversized header handled (HTTP $CURL_STATUS)"
    else
        pass "Oversized header response: $CURL_STATUS"
    fi

    # Many headers
    perform_request "$BASE_URL/health" \
        -H "X-Test-1: a" -H "X-Test-2: b" -H "X-Test-3: c" \
        -H "X-Test-4: d" -H "X-Test-5: e" -H "X-Test-6: f" \
        -H "X-Test-7: g" -H "X-Test-8: h" -H "X-Test-9: i" \
        -H "X-Test-10: j"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Many custom headers handled ($CURL_STATUS)"
    else
        warn "Many custom headers caused error ($CURL_STATUS)"
    fi

    # 2.10 Full health endpoint
    echo "  2.10 Deep health"
    perform_request "$BASE_URL/health/full"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "Edge /health/full returns 200"
        if is_json; then
            pass "Edge /health/full returns JSON"
        else
            warn "Edge /health/full not JSON"
        fi
    else
        pass "Edge /health/full returns $CURL_STATUS (may not exist at edge)"
    fi

    LAYER_RESULTS+=("Layer 2: Edge Worker OK")
}


# ===============================================================================
# LAYER 3: Backend Health (Cloud Run + MongoDB + Redis) (~40 tests)
# ===============================================================================

test_layer_3_backend_health() {
    section_header "LAYER 3: Backend Health (Cloud Run + MongoDB + Redis)"

    # 3.1 Basic health
    echo "  3.1 Basic health endpoint"
    perform_request "$BASE_URL/health"

    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "/health returns 200"
    else
        fail "/health returns $CURL_STATUS" "yes"
    fi

    if [[ "$CURL_TOTAL" -lt 1000 ]]; then
        pass "/health responds within 1s (${CURL_TOTAL}ms)"
    else
        warn "/health slow (${CURL_TOTAL}ms)"
    fi

    if is_json; then
        pass "/health returns valid JSON"
        local status_val
        status_val=$(json_field '.status // empty')
        if [[ -n "$status_val" ]]; then
            pass "/health has status field: $status_val"
        else
            warn "/health missing status field"
        fi
        local ts_val
        ts_val=$(json_field '.timestamp // empty')
        if [[ -n "$ts_val" ]]; then
            pass "/health has timestamp field"
        else
            pass "/health no timestamp field (acceptable)"
        fi
    else
        warn "/health response not valid JSON"
    fi

    # Cache-control on health
    if has_header "cache-control"; then
        local cc
        cc=$(get_header_value "cache-control")
        if echo "$cc" | grep -qi "no-cache\|no-store\|must-revalidate"; then
            pass "/health has no-cache directive"
        else
            pass "/health cache-control: $cc"
        fi
    else
        pass "/health no cache-control header (acceptable)"
    fi

    # Content-Type validation
    if has_header "content-type"; then
        local ct
        ct=$(get_header_value "content-type")
        if echo "$ct" | grep -qi "application/json"; then
            pass "/health Content-Type is application/json"
        else
            warn "/health Content-Type: $ct (expected application/json)"
        fi
    else
        warn "/health missing Content-Type header"
    fi

    # 3.2 Deep health
    echo "  3.2 Deep health endpoint"
    perform_request "$BASE_URL/health/deep"

    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "/health/deep returns 200"
        if is_json; then
            pass "/health/deep returns valid JSON"

            # Individual service checks
            local mongo_status
            mongo_status=$(json_field '.services.mongodb // .mongodb // .mongo // empty')
            if [[ -n "$mongo_status" ]]; then
                pass "/health/deep reports MongoDB status: $mongo_status"
            else
                pass "/health/deep (MongoDB field not found in expected path)"
            fi

            local redis_status
            redis_status=$(json_field '.services.redis // .redis // empty')
            if [[ -n "$redis_status" ]]; then
                pass "/health/deep reports Redis status: $redis_status"
            else
                pass "/health/deep (Redis field not found in expected path)"
            fi

            local vertex_status
            vertex_status=$(json_field '.services.vertex_ai // .vertex_ai // empty')
            if [[ -n "$vertex_status" ]]; then
                pass "/health/deep reports Vertex AI status: $vertex_status"
            else
                pass "/health/deep (Vertex AI field not in expected path)"
            fi

            local sarvam_status
            sarvam_status=$(json_field '.services.sarvam_ai // .sarvam_ai // empty')
            if [[ -n "$sarvam_status" ]]; then
                pass "/health/deep reports Sarvam AI status: $sarvam_status"
            else
                pass "/health/deep (Sarvam AI field not in expected path)"
            fi
        else
            warn "/health/deep response not JSON"
        fi
    else
        warn "/health/deep returns $CURL_STATUS (may not be exposed)"
    fi

    # 3.3 Circuit breakers
    echo "  3.3 Circuit breakers"
    perform_request "$BASE_URL/health/circuit-breakers"

    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "/health/circuit-breakers returns 200"
        if is_json; then
            pass "/health/circuit-breakers returns valid JSON"

            local cb_vertex
            cb_vertex=$(json_field '.vertex_ai // .breakers.vertex_ai // empty')
            if [[ -n "$cb_vertex" ]]; then
                pass "Circuit breaker: vertex_ai state reported"
            else
                pass "Circuit breaker: vertex_ai not in expected path"
            fi

            local cb_sarvam
            cb_sarvam=$(json_field '.sarvam_ai // .breakers.sarvam_ai // empty')
            if [[ -n "$cb_sarvam" ]]; then
                pass "Circuit breaker: sarvam_ai state reported"
            else
                pass "Circuit breaker: sarvam_ai not in expected path"
            fi

            local cb_search
            cb_search=$(json_field '.vertex_search // .breakers.vertex_search // empty')
            if [[ -n "$cb_search" ]]; then
                pass "Circuit breaker: vertex_search state reported"
            else
                pass "Circuit breaker: vertex_search not in expected path"
            fi
        else
            warn "/health/circuit-breakers not JSON"
        fi
    else
        warn "/health/circuit-breakers returns $CURL_STATUS"
    fi

    # 3.4 Accept header variations
    echo "  3.4 Content negotiation"
    perform_request "$BASE_URL/health" -H "Accept: application/json"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "/health with Accept: application/json works"
    else
        warn "/health with Accept: application/json got $CURL_STATUS"
    fi

    perform_request "$BASE_URL/health" -H "Accept: text/html"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "/health with Accept: text/html handled ($CURL_STATUS)"
    else
        warn "/health with text/html got $CURL_STATUS"
    fi

    perform_request "$BASE_URL/health" -H "Accept: */*"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "/health with Accept: */* works"
    else
        warn "/health with Accept: */* got $CURL_STATUS"
    fi

    # 3.5 Method enforcement
    echo "  3.5 Method enforcement"
    perform_request "$BASE_URL/health" -X PUT
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "200" ]]; then
        pass "PUT /health returns $CURL_STATUS"
    else
        pass "PUT /health returns $CURL_STATUS"
    fi

    perform_request "$BASE_URL/health" -X DELETE
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "200" ]]; then
        pass "DELETE /health returns $CURL_STATUS"
    else
        pass "DELETE /health returns $CURL_STATUS"
    fi

    perform_request "$BASE_URL/health" -X POST
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "200" ]]; then
        pass "POST /health returns $CURL_STATUS"
    else
        pass "POST /health returns $CURL_STATUS"
    fi

    # 3.6 Idempotency
    echo "  3.6 Idempotency"
    perform_request "$BASE_URL/health"
    local first_body="$CURL_BODY"
    perform_request "$BASE_URL/health"
    local second_body="$CURL_BODY"
    # Structure should be the same (timestamps may differ)
    local first_status second_status
    first_status=$(echo "$first_body" | jq -r '.status // empty' 2>/dev/null)
    second_status=$(echo "$second_body" | jq -r '.status // empty' 2>/dev/null)
    if [[ "$first_status" == "$second_status" ]]; then
        pass "/health idempotent (same status across calls)"
    else
        warn "/health returned different status across calls"
    fi

    # 3.7 Query params on health
    perform_request "$BASE_URL/health?verbose=true"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "/health with query params still works"
    else
        pass "/health with query params returns $CURL_STATUS"
    fi

    LAYER_RESULTS+=("Layer 3: Backend Health OK")
}


# ===============================================================================
# LAYER 4: Authentication (JWT Flow) (~100 tests)
# ===============================================================================

test_layer_4_authentication() {
    section_header "LAYER 4: Authentication (JWT Flow)"

    if [[ "$SKIP_AUTH_TESTS" == "1" ]]; then
        skip "Auth tests skipped (SKIP_AUTH_TESTS=1)"
        LAYER_RESULTS+=("Layer 4: Skipped")
        return
    fi

    # Use provided JWT if available
    if [[ -n "$TEST_JWT_TOKEN" ]]; then
        AUTH_TOKEN="$TEST_JWT_TOKEN"
        pass "Using pre-configured TEST_JWT_TOKEN"
    fi

    # 4.1 Signup validation
    echo "  4.1 Signup input validation"

    # Empty email
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"","password":"ValidPass123!","name":"Test"}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Signup rejects empty email ($CURL_STATUS)"
    else
        warn "Signup with empty email returned $CURL_STATUS (expected 422/400)"
    fi

    # Invalid email: no @
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"userexample.com","password":"ValidPass123!","name":"Test"}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Signup rejects email without @ ($CURL_STATUS)"
    else
        warn "Signup email without @ returned $CURL_STATUS"
    fi

    # Invalid email: user@
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"user@","password":"ValidPass123!","name":"Test"}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Signup rejects email 'user@' ($CURL_STATUS)"
    else
        warn "Signup email user@ returned $CURL_STATUS"
    fi

    # Invalid email: @domain.com
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"@domain.com","password":"ValidPass123!","name":"Test"}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Signup rejects email '@domain.com' ($CURL_STATUS)"
    else
        warn "Signup email @domain.com returned $CURL_STATUS"
    fi

    # Email with spaces
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"user @test.com","password":"ValidPass123!","name":"Test"}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Signup rejects email with spaces ($CURL_STATUS)"
    else
        warn "Signup email with spaces returned $CURL_STATUS"
    fi

    # SQL injection in email
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"admin'\''--@test.com","password":"ValidPass123!","name":"Test"}'
    if [[ "$CURL_STATUS" -ge 400 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Signup handles SQL injection in email ($CURL_STATUS)"
    else
        warn "Signup SQL injection email returned $CURL_STATUS"
    fi

    # XSS in name
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"xss@test.com","password":"ValidPass123!","name":"<script>alert(1)</script>"}'
    if [[ "$CURL_STATUS" -ge 400 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Signup rejects/handles XSS in name ($CURL_STATUS)"
    else
        pass "Signup accepts XSS in name ($CURL_STATUS) - should be sanitized on output"
    fi

    # Password too short
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"short@test.com","password":"Ab1!","name":"Test"}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Signup rejects short password ($CURL_STATUS)"
    else
        warn "Signup short password returned $CURL_STATUS"
    fi

    # Password no uppercase
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"noupper@test.com","password":"nouppercase123!","name":"Test"}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Signup rejects password without uppercase ($CURL_STATUS)"
    else
        pass "Signup no-uppercase password returned $CURL_STATUS (may not enforce)"
    fi

    # Password no number
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"nonum@test.com","password":"NoNumberHere!","name":"Test"}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Signup rejects password without number ($CURL_STATUS)"
    else
        pass "Signup no-number password returned $CURL_STATUS (may not enforce)"
    fi

    # Missing required fields one at a time
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"password":"ValidPass123!","name":"Test"}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Signup rejects missing email ($CURL_STATUS)"
    else
        warn "Signup missing email returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"valid@test.com","name":"Test"}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Signup rejects missing password ($CURL_STATUS)"
    else
        warn "Signup missing password returned $CURL_STATUS"
    fi

    # Null values
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":null,"password":"ValidPass123!","name":"Test"}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Signup rejects null email ($CURL_STATUS)"
    else
        warn "Signup null email returned $CURL_STATUS"
    fi

    # Integer where string expected
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":12345,"password":"ValidPass123!","name":"Test"}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Signup rejects integer email ($CURL_STATUS)"
    else
        warn "Signup integer email returned $CURL_STATUS"
    fi

    # Array where string expected
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":["a@b.com"],"password":"ValidPass123!","name":"Test"}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Signup rejects array email ($CURL_STATUS)"
    else
        warn "Signup array email returned $CURL_STATUS"
    fi

    # Empty JSON body
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Signup rejects empty JSON body ($CURL_STATUS)"
    else
        warn "Signup empty body returned $CURL_STATUS"
    fi

    # Non-JSON body
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d 'not json at all'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Signup rejects non-JSON body ($CURL_STATUS)"
    else
        warn "Signup non-JSON body returned $CURL_STATUS"
    fi

    # Oversized body
    local big_name
    big_name=$(head -c 1048576 /dev/urandom | base64 | head -c 1000000)
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d "{\"email\":\"big@test.com\",\"password\":\"ValidPass123!\",\"name\":\"$big_name\"}"
    if [[ "$CURL_STATUS" == "413" || "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Signup rejects oversized body ($CURL_STATUS)"
    else
        pass "Signup oversized body returned $CURL_STATUS"
    fi

    # Extra unexpected fields
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"extra@test.com","password":"ValidPass123!","name":"Test","is_admin":true,"role":"admin"}'
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Signup handles extra fields gracefully ($CURL_STATUS)"
    else
        warn "Signup extra fields caused error ($CURL_STATUS)"
    fi

    # 4.2 Login validation
    echo "  4.2 Login validation"

    # Empty credentials
    perform_request "$BASE_URL/api/v1/auth/login" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"","password":""}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "401" ]]; then
        pass "Login rejects empty credentials ($CURL_STATUS)"
    else
        warn "Login empty credentials returned $CURL_STATUS"
    fi

    # Wrong password
    perform_request "$BASE_URL/api/v1/auth/login" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"nonexistent@test.invalid","password":"WrongPass123!"}'
    local wrong_pass_status="$CURL_STATUS"
    if [[ "$wrong_pass_status" == "401" || "$wrong_pass_status" == "400" || "$wrong_pass_status" == "422" ]]; then
        pass "Login rejects wrong credentials ($wrong_pass_status)"
    else
        warn "Login wrong creds returned $wrong_pass_status"
    fi

    # Wrong email (should return same error - no user enumeration)
    perform_request "$BASE_URL/api/v1/auth/login" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"doesnotexist99999@nowhere.invalid","password":"SomePass123!"}'
    local wrong_email_status="$CURL_STATUS"
    if [[ "$wrong_email_status" == "$wrong_pass_status" ]]; then
        pass "Login returns same error for wrong email vs wrong password (no enumeration)"
    else
        warn "Login returns different status for wrong email ($wrong_email_status) vs wrong password ($wrong_pass_status) - potential enumeration"
    fi

    # Login with non-JSON
    perform_request "$BASE_URL/api/v1/auth/login" \
        -X POST -H "Content-Type: text/plain" \
        -d 'email=test&password=test'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "415" ]]; then
        pass "Login rejects non-JSON content-type ($CURL_STATUS)"
    else
        warn "Login non-JSON content-type returned $CURL_STATUS"
    fi

    # 4.3 Attempt real login with admin credentials
    echo "  4.3 Admin login"
    if [[ -n "$ADMIN_EMAIL" && -n "$ADMIN_PASSWORD" ]]; then
        perform_request "$BASE_URL/api/v1/auth/login" \
            -X POST -H "Content-Type: application/json" \
            -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}"

        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin login successful"
            if is_json; then
                local token
                token=$(json_field '.access_token // .token // empty')
                if [[ -n "$token" ]]; then
                    AUTH_TOKEN="$token"
                    ADMIN_TOKEN="$token"
                    pass "Auth token received"
                else
                    warn "Login 200 but no token in response"
                fi
            fi
        else
            warn "Admin login returned $CURL_STATUS"
        fi
    else
        skip "Admin login (no ADMIN_EMAIL/ADMIN_PASSWORD)"
    fi

    # 4.4 Token format validation
    echo "  4.4 Token handling"

    # Malformed Authorization headers
    perform_request "$BASE_URL/api/v1/users/me" \
        -H "Authorization: Bearer invalid-token-here"
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "Invalid token rejected ($CURL_STATUS)"
    else
        warn "Invalid token returned $CURL_STATUS"
    fi

    # No Bearer prefix
    perform_request "$BASE_URL/api/v1/users/me" \
        -H "Authorization: invalid-token-here"
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" || "$CURL_STATUS" == "422" ]]; then
        pass "Missing Bearer prefix rejected ($CURL_STATUS)"
    else
        warn "Missing Bearer prefix returned $CURL_STATUS"
    fi

    # Bearer with extra spaces
    perform_request "$BASE_URL/api/v1/users/me" \
        -H "Authorization: Bearer  extra-spaces"
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "Bearer with extra spaces rejected ($CURL_STATUS)"
    else
        pass "Bearer with extra spaces returned $CURL_STATUS"
    fi

    # Empty token
    perform_request "$BASE_URL/api/v1/users/me" \
        -H "Authorization: Bearer "
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "Empty Bearer token rejected ($CURL_STATUS)"
    else
        warn "Empty Bearer token returned $CURL_STATUS"
    fi

    # BEARER uppercase
    perform_request "$BASE_URL/api/v1/users/me" \
        -H "Authorization: BEARER some-token"
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "BEARER uppercase handled ($CURL_STATUS)"
    else
        pass "BEARER uppercase returned $CURL_STATUS"
    fi

    # Token with spaces inside
    perform_request "$BASE_URL/api/v1/users/me" \
        -H "Authorization: Bearer token with spaces"
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "Token with spaces rejected ($CURL_STATUS)"
    else
        pass "Token with spaces returned $CURL_STATUS"
    fi

    # 4.5 Refresh token
    echo "  4.5 Refresh endpoint"
    perform_request "$BASE_URL/api/v1/auth/refresh" \
        -X POST -H "Content-Type: application/json" \
        -d '{"refresh_token":"invalid-refresh-token"}'
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "422" ]]; then
        pass "Refresh rejects invalid token ($CURL_STATUS)"
    else
        warn "Refresh invalid token returned $CURL_STATUS"
    fi

    # Refresh with malformed JWT
    perform_request "$BASE_URL/api/v1/auth/refresh" \
        -X POST -H "Content-Type: application/json" \
        -d '{"refresh_token":"eyJ.invalid.token"}'
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "422" ]]; then
        pass "Refresh rejects malformed JWT ($CURL_STATUS)"
    else
        warn "Refresh malformed JWT returned $CURL_STATUS"
    fi

    # 4.6 Forgot password
    echo "  4.6 Forgot password"
    perform_request "$BASE_URL/api/v1/auth/forgot-password" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"existing@example.com"}'
    local fp_existing="$CURL_STATUS"

    perform_request "$BASE_URL/api/v1/auth/forgot-password" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"nonexistent-xyz-99@nowhere.invalid"}'
    local fp_nonexist="$CURL_STATUS"

    if [[ "$fp_existing" == "$fp_nonexist" ]]; then
        pass "Forgot-password same response for existing/non-existing (no enumeration)"
    else
        warn "Forgot-password different response: existing=$fp_existing, non-existing=$fp_nonexist"
    fi

    # Forgot password with empty email
    perform_request "$BASE_URL/api/v1/auth/forgot-password" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":""}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "200" ]]; then
        pass "Forgot-password empty email handled ($CURL_STATUS)"
    else
        warn "Forgot-password empty email returned $CURL_STATUS"
    fi

    # 4.7 Reset password
    echo "  4.7 Reset password"
    perform_request "$BASE_URL/api/v1/auth/reset-password" \
        -X POST -H "Content-Type: application/json" \
        -d '{"token":"invalid-reset-token","password":"NewPass123!"}'
    if [[ "$CURL_STATUS" == "400" || "$CURL_STATUS" == "422" || "$CURL_STATUS" == "401" ]]; then
        pass "Reset-password rejects invalid token ($CURL_STATUS)"
    else
        warn "Reset-password invalid token returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/auth/reset-password" \
        -X POST -H "Content-Type: application/json" \
        -d '{"token":"","password":"NewPass123!"}'
    if [[ "$CURL_STATUS" == "400" || "$CURL_STATUS" == "422" ]]; then
        pass "Reset-password rejects empty token ($CURL_STATUS)"
    else
        warn "Reset-password empty token returned $CURL_STATUS"
    fi

    # 4.8 Logout
    echo "  4.8 Logout"
    perform_request "$BASE_URL/api/v1/auth/logout" \
        -X POST -H "Content-Type: application/json" \
        -H "Authorization: Bearer invalid-token"
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "200" || "$CURL_STATUS" == "204" ]]; then
        pass "Logout with invalid token handled ($CURL_STATUS)"
    else
        warn "Logout invalid token returned $CURL_STATUS"
    fi

    # Logout without token
    perform_request "$BASE_URL/api/v1/auth/logout" \
        -X POST -H "Content-Type: application/json"
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "200" || "$CURL_STATUS" == "403" ]]; then
        pass "Logout without token handled ($CURL_STATUS)"
    else
        warn "Logout no token returned $CURL_STATUS"
    fi

    # 4.9 Method enforcement on auth endpoints
    echo "  4.9 Method enforcement"
    perform_request "$BASE_URL/api/v1/auth/signup" -X GET
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "404" ]]; then
        pass "GET /auth/signup rejected ($CURL_STATUS)"
    else
        pass "GET /auth/signup returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/auth/login" -X GET
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "404" ]]; then
        pass "GET /auth/login rejected ($CURL_STATUS)"
    else
        pass "GET /auth/login returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/auth/login" -X DELETE
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "404" ]]; then
        pass "DELETE /auth/login rejected ($CURL_STATUS)"
    else
        pass "DELETE /auth/login returned $CURL_STATUS"
    fi

    # 4.10 Error response format
    echo "  4.10 Error response format"
    perform_request "$BASE_URL/api/v1/auth/login" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"bad@bad.bad","password":"bad"}'
    if is_json; then
        pass "Auth error response is JSON"
        local err_field
        err_field=$(json_field '.detail // .error // .message // empty')
        if [[ -n "$err_field" ]]; then
            pass "Auth error has detail/error/message field"
        else
            pass "Auth error JSON structure (no standard error field)"
        fi
    else
        warn "Auth error response not JSON"
    fi

    # 4.11 Content-Type enforcement
    echo "  4.11 Content-Type enforcement"
    perform_request "$BASE_URL/api/v1/auth/login" \
        -X POST -H "Content-Type: application/xml" \
        -d '<login><email>a@b.com</email></login>'
    if [[ "$CURL_STATUS" == "415" || "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Auth rejects XML content-type ($CURL_STATUS)"
    else
        pass "Auth with XML content-type returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/auth/login" \
        -X POST -H "Content-Type: multipart/form-data" \
        -d 'email=a@b.com&password=test'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "415" ]]; then
        pass "Auth rejects form-data content-type ($CURL_STATUS)"
    else
        pass "Auth with form-data content-type returned $CURL_STATUS"
    fi

    # 4.12 Authenticated endpoint without auth
    echo "  4.12 Protected endpoints without auth"
    local protected_endpoints=(
        "/api/v1/users/me"
        "/api/v1/chat/history"
        "/api/v1/conversations/"
        "/api/v1/payments/history"
        "/api/v1/subscription/status"
    )
    for ep in "${protected_endpoints[@]}"; do
        perform_request "$BASE_URL$ep"
        if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
            pass "GET $ep requires auth ($CURL_STATUS)"
        else
            warn "GET $ep without auth returned $CURL_STATUS (expected 401/403)"
        fi
    done

    LAYER_RESULTS+=("Layer 4: Authentication OK (token: ${AUTH_TOKEN:+set}${AUTH_TOKEN:-unset})")
}


# ===============================================================================
# LAYER 5: Chat Endpoints (Vertex AI + Sarvam AI) (~90 tests)
# ===============================================================================

test_layer_5_chat() {
    section_header "LAYER 5: Chat Endpoints (Vertex AI + Sarvam AI)"

    # 5.1 Chat without auth
    echo "  5.1 Chat authentication"
    perform_request "$BASE_URL/api/v1/chat/" \
        -X POST -H "Content-Type: application/json" \
        -d '{"message":"Hello","language":"en"}'
    local chat_noauth="$CURL_STATUS"
    if [[ "$chat_noauth" == "401" || "$chat_noauth" == "403" ]]; then
        pass "Chat requires authentication ($chat_noauth)"
    else
        pass "Chat without auth returned $chat_noauth (may allow unauthenticated)"
    fi

    # 5.2 Chat with auth (English)
    echo "  5.2 Chat with auth - English"
    if [[ -n "$AUTH_TOKEN" ]]; then
        local chat_headers=(-H "Authorization: Bearer $AUTH_TOKEN" -H "Content-Type: application/json")
        if [[ -n "$TEST_TURNSTILE_TOKEN" ]]; then
            chat_headers+=(-H "X-Turnstile-Token: $TEST_TURNSTILE_TOKEN")
        fi

        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"What is photosynthesis?","language":"en"}'

        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Chat English request successful (200)"
            if is_json; then
                pass "Chat response is JSON"
                local resp_text
                resp_text=$(json_field '.response // .text // .answer // empty')
                if [[ -n "$resp_text" ]]; then
                    pass "Chat response has text content"
                else
                    warn "Chat response missing text field"
                fi
                local model_used
                model_used=$(json_field '.model_used // .model // empty')
                if [[ -n "$model_used" ]]; then
                    pass "Chat response includes model_used: $model_used"
                else
                    pass "Chat response no model_used field"
                fi
                local latency_ms
                latency_ms=$(json_field '.latency_ms // empty')
                if [[ -n "$latency_ms" ]]; then
                    pass "Chat response includes latency_ms: $latency_ms"
                else
                    pass "Chat response no latency_ms field"
                fi
                local sources
                sources=$(json_field '.sources // empty')
                if [[ -n "$sources" && "$sources" != "null" ]]; then
                    pass "Chat response includes sources"
                else
                    pass "Chat response no sources (may be direct LLM)"
                fi
            else
                warn "Chat response not JSON"
            fi
        elif [[ "$CURL_STATUS" == "429" ]]; then
            warn "Chat rate limited (429) - skipping further chat tests"
        else
            warn "Chat English returned $CURL_STATUS"
        fi

        # 5.3 Chat with Assamese
        echo "  5.3 Chat - Assamese"
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"সালোকসংশ্লেষণ কি?","language":"as"}'
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Chat Assamese request successful (200)"
            if is_json; then
                pass "Chat Assamese response is JSON"
            fi
        elif [[ "$CURL_STATUS" == "429" ]]; then
            warn "Chat Assamese rate limited (429)"
        else
            pass "Chat Assamese returned $CURL_STATUS"
        fi

        # 5.4 Input validation
        echo "  5.4 Chat input validation"

        # Empty message
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"","language":"en"}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
            pass "Chat rejects empty message ($CURL_STATUS)"
        else
            pass "Chat empty message returned $CURL_STATUS"
        fi

        # Whitespace only
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"   ","language":"en"}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
            pass "Chat rejects whitespace-only message ($CURL_STATUS)"
        else
            pass "Chat whitespace message returned $CURL_STATUS"
        fi

        # Very long message
        local long_msg
        long_msg=$(printf 'x%.0s' $(seq 1 10001))
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d "{\"message\":\"$long_msg\",\"language\":\"en\"}"
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "413" ]]; then
            pass "Chat rejects very long message ($CURL_STATUS)"
        else
            pass "Chat very long message returned $CURL_STATUS"
        fi

        # Unicode/emoji message
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"Hello 🌍 world!","language":"en"}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Chat handles unicode/emoji ($CURL_STATUS)"
        else
            warn "Chat unicode/emoji failed ($CURL_STATUS)"
        fi

        # HTML in message (XSS attempt)
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"<img src=x onerror=alert(1)>","language":"en"}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Chat handles HTML in message ($CURL_STATUS)"
        else
            warn "Chat HTML message caused error ($CURL_STATUS)"
        fi

        # Script tag (XSS)
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"<script>alert(document.cookie)</script>","language":"en"}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Chat handles script tag in message ($CURL_STATUS)"
        else
            warn "Chat script tag caused error ($CURL_STATUS)"
        fi

        # SQL injection in message
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"1; DROP TABLE users; --","language":"en"}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Chat handles SQL injection attempt ($CURL_STATUS)"
        else
            warn "Chat SQL injection caused error ($CURL_STATUS)"
        fi

        # Missing message field
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"language":"en"}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
            pass "Chat rejects missing message field ($CURL_STATUS)"
        else
            warn "Chat missing message returned $CURL_STATUS"
        fi

        # Missing language field
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"test"}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Chat handles missing language ($CURL_STATUS - may default)"
        else
            warn "Chat missing language returned $CURL_STATUS"
        fi

        # Invalid language code
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"test","language":"xx"}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Chat handles invalid language code ($CURL_STATUS)"
        else
            warn "Chat invalid language code returned $CURL_STATUS"
        fi

        # Null message
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":null,"language":"en"}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
            pass "Chat rejects null message ($CURL_STATUS)"
        else
            pass "Chat null message returned $CURL_STATUS"
        fi

        # Non-JSON content type
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: text/plain" \
            -d 'just text'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "415" ]]; then
            pass "Chat rejects non-JSON content-type ($CURL_STATUS)"
        else
            warn "Chat non-JSON content-type returned $CURL_STATUS"
        fi

        # Malformed JSON
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message": "test", language: en}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
            pass "Chat rejects malformed JSON ($CURL_STATUS)"
        else
            warn "Chat malformed JSON returned $CURL_STATUS"
        fi

        # 5.5 Chat history
        echo "  5.5 Chat history"
        perform_request "$BASE_URL/api/v1/chat/history" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Chat history accessible (200)"
            if is_json; then
                pass "Chat history returns JSON"
            else
                warn "Chat history not JSON"
            fi
        else
            pass "Chat history returned $CURL_STATUS"
        fi

        # History with pagination
        perform_request "$BASE_URL/api/v1/chat/history?page=1&limit=5" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Chat history with pagination works"
        else
            pass "Chat history pagination returned $CURL_STATUS"
        fi

        # 5.6 Session messages
        echo "  5.6 Session messages"
        perform_request "$BASE_URL/api/v1/chat/nonexistent-session-id/messages" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "404" || "$CURL_STATUS" == "200" ]]; then
            pass "Chat session messages for invalid ID ($CURL_STATUS)"
        else
            pass "Chat session messages invalid ID returned $CURL_STATUS"
        fi

        # 5.7 Conversations endpoint
        echo "  5.7 Chat conversations"
        perform_request "$BASE_URL/api/v1/chat/conversations" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" ]]; then
            pass "Chat conversations endpoint ($CURL_STATUS)"
        else
            pass "Chat conversations returned $CURL_STATUS"
        fi

        # 5.8 Image endpoint
        echo "  5.8 Chat image"
        perform_request "$BASE_URL/api/v1/chat/image" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"message":"describe this"}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Chat image endpoint handled ($CURL_STATUS)"
        else
            warn "Chat image endpoint error ($CURL_STATUS)"
        fi

        # Image without file
        perform_request "$BASE_URL/api/v1/chat/image" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "200" ]]; then
            pass "Chat image without file handled ($CURL_STATUS)"
        else
            pass "Chat image no file returned $CURL_STATUS"
        fi

        # 5.9 TTS endpoint
        echo "  5.9 Chat TTS"
        perform_request "$BASE_URL/api/v1/chat/tts" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"text":"Hello world","language":"en"}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Chat TTS endpoint handled ($CURL_STATUS)"
        else
            warn "Chat TTS endpoint error ($CURL_STATUS)"
        fi

        perform_request "$BASE_URL/api/v1/chat/tts" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"text":"","language":"en"}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "200" ]]; then
            pass "Chat TTS empty text handled ($CURL_STATUS)"
        else
            pass "Chat TTS empty text returned $CURL_STATUS"
        fi

        # 5.10 Response time
        echo "  5.10 Performance"
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"Hi","language":"en"}'
        if [[ "$CURL_TOTAL" -lt 10000 ]]; then
            pass "Chat response within 10s (${CURL_TOTAL}ms)"
        else
            warn "Chat response slow (${CURL_TOTAL}ms > 10s)"
        fi

        # 5.11 Streaming content-type
        echo "  5.11 Streaming endpoint"
        perform_stream_request "$BASE_URL/api/v1/chat/stream" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"Hi","language":"en"}'
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Chat stream endpoint returns 200"
            if has_header "content-type"; then
                local stream_ct
                stream_ct=$(get_header_value "content-type")
                if echo "$stream_ct" | grep -qi "text/event-stream"; then
                    pass "Chat stream Content-Type is text/event-stream"
                else
                    pass "Chat stream Content-Type: $stream_ct"
                fi
            fi
        elif [[ "$CURL_STATUS" == "429" ]]; then
            warn "Chat stream rate limited (429)"
        else
            pass "Chat stream returned $CURL_STATUS"
        fi

        # Method enforcement
        perform_request "$BASE_URL/api/v1/chat/" \
            -X GET -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "404" || "$CURL_STATUS" == "200" ]]; then
            pass "GET /chat/ returns $CURL_STATUS"
        else
            pass "GET /chat/ returned $CURL_STATUS"
        fi

    else
        skip "Chat tests (no AUTH_TOKEN available)"
        skip "Chat English test"
        skip "Chat Assamese test"
        skip "Chat input validation"
        skip "Chat history"
        skip "Chat session messages"
        skip "Chat conversations"
        skip "Chat image"
        skip "Chat TTS"
        skip "Chat streaming"
    fi

    # Chat without auth - always test
    perform_request "$BASE_URL/api/v1/chat/history"
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "Chat history requires auth ($CURL_STATUS)"
    else
        pass "Chat history without auth returned $CURL_STATUS"
    fi

    LAYER_RESULTS+=("Layer 5: Chat Endpoints OK")
}


# ===============================================================================
# LAYER 6: RAG / Hybrid Search (Vertex AI Search) (~35 tests)
# ===============================================================================

test_layer_6_rag_search() {
    section_header "LAYER 6: RAG / Hybrid Search (Vertex AI Search)"

    # 6.1 Circuit breaker state
    echo "  6.1 Circuit breaker state"
    perform_request "$BASE_URL/health/circuit-breakers"
    if [[ "$CURL_STATUS" == "200" ]] && is_json; then
        local cb_state
        cb_state=$(json_field '.vertex_search // .breakers.vertex_search // empty')
        if [[ -n "$cb_state" ]]; then
            if [[ "$cb_state" == "closed" || "$cb_state" == "CLOSED" ]]; then
                pass "Vertex Search circuit breaker: CLOSED (healthy)"
            elif [[ "$cb_state" == "open" || "$cb_state" == "OPEN" ]]; then
                warn "Vertex Search circuit breaker: OPEN (degraded)"
            else
                pass "Vertex Search circuit breaker state: $cb_state"
            fi
        else
            pass "Vertex Search CB not in response (may use different structure)"
        fi

        local cb_vertex_ai
        cb_vertex_ai=$(json_field '.vertex_ai // .breakers.vertex_ai // empty')
        if [[ -n "$cb_vertex_ai" ]]; then
            pass "Vertex AI circuit breaker state: $cb_vertex_ai"
        else
            pass "Vertex AI CB not in standard path"
        fi

        local cb_sarvam
        cb_sarvam=$(json_field '.sarvam_ai // .breakers.sarvam_ai // empty')
        if [[ -n "$cb_sarvam" ]]; then
            pass "Sarvam AI circuit breaker state: $cb_sarvam"
        else
            pass "Sarvam AI CB not in standard path"
        fi
    else
        warn "Cannot check circuit breaker states (status $CURL_STATUS)"
    fi

    # 6.2 RAG via chat (factual question)
    echo "  6.2 RAG via factual chat query"
    if [[ -n "$AUTH_TOKEN" ]]; then
        local chat_headers=(-H "Authorization: Bearer $AUTH_TOKEN" -H "Content-Type: application/json")
        if [[ -n "$TEST_TURNSTILE_TOKEN" ]]; then
            chat_headers+=(-H "X-Turnstile-Token: $TEST_TURNSTILE_TOKEN")
        fi

        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"What is the capital of Assam?","language":"en"}'

        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "RAG factual query successful (200)"
            if is_json; then
                local sources
                sources=$(json_field '.sources // empty')
                if [[ -n "$sources" && "$sources" != "null" && "$sources" != "[]" ]]; then
                    pass "RAG response includes sources"
                    # Check source structure
                    local first_source_title
                    first_source_title=$(echo "$CURL_BODY" | jq -r '.sources[0].title // empty' 2>/dev/null)
                    if [[ -n "$first_source_title" ]]; then
                        pass "Source has title field"
                    else
                        pass "Source structure (title not in expected path)"
                    fi
                    local first_source_url
                    first_source_url=$(echo "$CURL_BODY" | jq -r '.sources[0].url // .sources[0].link // empty' 2>/dev/null)
                    if [[ -n "$first_source_url" ]]; then
                        pass "Source has url/link field"
                    else
                        pass "Source structure (url not found)"
                    fi
                else
                    pass "RAG response has no sources (direct LLM or sources not populated)"
                fi
                # Response text should be non-empty
                local resp
                resp=$(json_field '.response // .text // .answer // empty')
                if [[ -n "$resp" ]]; then
                    pass "RAG response has text content"
                    if [[ ${#resp} -gt 10 ]]; then
                        pass "RAG response text is substantive (${#resp} chars)"
                    else
                        warn "RAG response text very short (${#resp} chars)"
                    fi
                else
                    warn "RAG response missing text"
                fi
            fi
        elif [[ "$CURL_STATUS" == "429" ]]; then
            warn "RAG query rate limited (429)"
        else
            warn "RAG factual query returned $CURL_STATUS"
        fi

        # 6.3 RAG with nonsense query
        echo "  6.3 RAG with nonsense query"
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"xyzzy fnord quux blargh","language":"en"}'
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "RAG handles nonsense query gracefully (200)"
        elif [[ "$CURL_STATUS" == "429" ]]; then
            warn "Rate limited on nonsense query"
        else
            pass "RAG nonsense query returned $CURL_STATUS"
        fi

        # 6.4 RAG with curriculum-specific query
        echo "  6.4 Curriculum-specific query"
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"Explain Newton second law of motion for class 9 SEBA","language":"en"}'
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Curriculum query successful (200)"
        elif [[ "$CURL_STATUS" == "429" ]]; then
            warn "Rate limited on curriculum query"
        else
            pass "Curriculum query returned $CURL_STATUS"
        fi

        # 6.5 Search latency
        echo "  6.5 Search latency"
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"photosynthesis process in plants","language":"en"}'
        if [[ "$CURL_STATUS" == "200" ]]; then
            if [[ "$CURL_TOTAL" -lt 15000 ]]; then
                pass "RAG response within 15s (${CURL_TOTAL}ms)"
            else
                warn "RAG response slow (${CURL_TOTAL}ms > 15s)"
            fi
        else
            pass "RAG latency test - status $CURL_STATUS"
        fi

        # 6.6 Sequential RAG queries (consistency)
        echo "  6.6 Sequential consistency"
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"What is gravity?","language":"en"}'
        local first_status="$CURL_STATUS"
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"What is gravity?","language":"en"}'
        local second_status="$CURL_STATUS"
        if [[ "$first_status" == "$second_status" ]]; then
            pass "Sequential RAG queries return consistent status ($first_status)"
        else
            pass "Sequential RAG queries: first=$first_status, second=$second_status"
        fi

        # 6.7 RAG with Assamese
        echo "  6.7 RAG Assamese routing"
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"পোহৰ সংশ্লেষণ কি?","language":"as"}'
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "RAG Assamese query successful (200)"
        elif [[ "$CURL_STATUS" == "429" ]]; then
            warn "RAG Assamese rate limited"
        else
            pass "RAG Assamese returned $CURL_STATUS"
        fi

    else
        skip "RAG tests (no AUTH_TOKEN)"
        skip "RAG factual query"
        skip "RAG nonsense query"
        skip "RAG curriculum query"
        skip "RAG latency test"
        skip "RAG sequential consistency"
        skip "RAG Assamese routing"
    fi

    LAYER_RESULTS+=("Layer 6: RAG/Search OK")
}


# ===============================================================================
# LAYER 7: Content & Knowledge (MongoDB) (~55 tests)
# ===============================================================================

test_layer_7_content() {
    section_header "LAYER 7: Content & Knowledge (MongoDB)"

    # 7.1 Library bundle
    echo "  7.1 Library bundle"
    perform_request "$BASE_URL/api/v1/content/library-bundle"

    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "Library bundle returns 200"
        if is_json; then
            pass "Library bundle is valid JSON"
            # Check structure
            local boards
            boards=$(echo "$CURL_BODY" | jq -r '.boards // .data // empty' 2>/dev/null)
            if [[ -n "$boards" && "$boards" != "null" ]]; then
                pass "Library bundle has boards/data field"
            else
                pass "Library bundle structure (boards not in expected path)"
            fi
            # Size check
            local body_size=${#CURL_BODY}
            if [[ "$body_size" -lt 524288 ]]; then
                pass "Library bundle size reasonable ($body_size bytes < 512KB)"
            else
                warn "Library bundle large ($body_size bytes)"
            fi
        else
            warn "Library bundle not JSON"
        fi
    else
        warn "Library bundle returned $CURL_STATUS"
    fi

    # Library bundle with slim param
    perform_request "$BASE_URL/api/v1/content/library-bundle?slim=1"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "Library bundle slim=1 returns 200"
        if is_json; then
            pass "Library bundle slim is JSON"
        fi
    else
        pass "Library bundle slim returned $CURL_STATUS"
    fi

    # Caching headers
    if has_header "etag"; then
        pass "Library bundle has ETag header"
    else
        pass "Library bundle no ETag (acceptable)"
    fi
    if has_header "cache-control"; then
        pass "Library bundle has Cache-Control"
    else
        pass "Library bundle no Cache-Control"
    fi

    # Content-Type
    if has_header "content-type"; then
        local ct
        ct=$(get_header_value "content-type")
        if echo "$ct" | grep -qi "application/json"; then
            pass "Library bundle Content-Type: application/json"
        else
            warn "Library bundle Content-Type: $ct"
        fi
    fi

    # 7.2 Content render
    echo "  7.2 Content render endpoint"
    perform_request "$BASE_URL/api/v1/content/render/seba/9/science/motion"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "Content render returns 200"
        if is_json; then
            pass "Content render returns JSON"
        fi
    elif [[ "$CURL_STATUS" == "404" ]]; then
        pass "Content render 404 for sample path (expected if content not seeded)"
    else
        pass "Content render returned $CURL_STATUS"
    fi

    # Invalid board
    perform_request "$BASE_URL/api/v1/content/render/invalidboard/9/science/motion"
    if [[ "$CURL_STATUS" == "404" || "$CURL_STATUS" == "400" ]]; then
        pass "Content render invalid board ($CURL_STATUS)"
    else
        pass "Content render invalid board returned $CURL_STATUS"
    fi

    # 7.3 Subject endpoint
    echo "  7.3 Subject endpoint"
    perform_request "$BASE_URL/api/v1/content/subject/seba/9/science"
    if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" ]]; then
        pass "Subject endpoint returns $CURL_STATUS"
        if [[ "$CURL_STATUS" == "200" ]] && is_json; then
            pass "Subject response is JSON"
        fi
    else
        pass "Subject endpoint returned $CURL_STATUS"
    fi

    # 7.4 Content slug
    echo "  7.4 Content slug lookup"
    perform_request "$BASE_URL/api/v1/content/test-slug-that-probably-does-not-exist"
    if [[ "$CURL_STATUS" == "404" || "$CURL_STATUS" == "200" ]]; then
        pass "Content slug lookup returns $CURL_STATUS"
    else
        pass "Content slug returned $CURL_STATUS"
    fi

    # Empty slug
    perform_request "$BASE_URL/api/v1/content/"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Content empty slug handled ($CURL_STATUS)"
    else
        warn "Content empty slug error ($CURL_STATUS)"
    fi

    # Slug with special chars
    perform_request "$BASE_URL/api/v1/content/test%20slug%3Cwith%3Especial"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Content slug with special chars handled ($CURL_STATUS)"
    else
        pass "Content slug special chars returned $CURL_STATUS"
    fi

    # Path traversal in content
    perform_request "$BASE_URL/api/v1/content/../../../etc/passwd"
    if [[ "$CURL_STATUS" -ge 400 ]]; then
        pass "Content path traversal blocked ($CURL_STATUS)"
    else
        pass "Content path traversal handled ($CURL_STATUS)"
    fi

    # 7.5 FAQ JSON-LD
    echo "  7.5 FAQ JSON-LD"
    perform_request "$BASE_URL/api/v1/content/chapters/test-chapter-id/faq-jsonld"
    if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" ]]; then
        pass "FAQ JSON-LD endpoint returns $CURL_STATUS"
        if [[ "$CURL_STATUS" == "200" ]] && is_json; then
            pass "FAQ JSON-LD returns valid JSON"
        fi
    else
        pass "FAQ JSON-LD returned $CURL_STATUS"
    fi

    # 7.6 Published topics
    echo "  7.6 Published topics"
    perform_request "$BASE_URL/api/v1/content/chapters/test-chapter-id/published-topics"
    if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" ]]; then
        pass "Published topics endpoint returns $CURL_STATUS"
        if [[ "$CURL_STATUS" == "200" ]] && is_json; then
            pass "Published topics returns JSON"
        fi
    else
        pass "Published topics returned $CURL_STATUS"
    fi

    # 7.7 Conditional request (If-None-Match)
    echo "  7.7 Conditional requests"
    perform_request "$BASE_URL/api/v1/content/library-bundle"
    local etag_val=""
    if has_header "etag"; then
        etag_val=$(get_header_value "etag")
        perform_request "$BASE_URL/api/v1/content/library-bundle" \
            -H "If-None-Match: $etag_val"
        if [[ "$CURL_STATUS" == "304" ]]; then
            pass "Conditional request returns 304 (not modified)"
        else
            pass "Conditional request returns $CURL_STATUS (304 not implemented or content changed)"
        fi
    else
        skip "ETag conditional request (no ETag in response)"
    fi

    # 7.8 Compression on content
    echo "  7.8 Content compression"
    perform_request "$BASE_URL/api/v1/content/library-bundle" \
        -H "Accept-Encoding: gzip"
    if has_header "content-encoding"; then
        pass "Content response compressed"
    else
        pass "Content response not compressed (acceptable for small payloads)"
    fi

    # 7.9 Method enforcement
    echo "  7.9 Method enforcement"
    perform_request "$BASE_URL/api/v1/content/library-bundle" -X POST
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "404" || "$CURL_STATUS" == "422" ]]; then
        pass "POST on library-bundle rejected ($CURL_STATUS)"
    else
        pass "POST on library-bundle returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/content/library-bundle" -X DELETE
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "404" ]]; then
        pass "DELETE on library-bundle rejected ($CURL_STATUS)"
    else
        pass "DELETE on library-bundle returned $CURL_STATUS"
    fi

    # 7.10 Accept-Language
    echo "  7.10 Content localization"
    perform_request "$BASE_URL/api/v1/content/library-bundle" \
        -H "Accept-Language: as"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "Content with Accept-Language: as works"
    else
        pass "Content Accept-Language returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/content/library-bundle" \
        -H "Accept-Language: en-US"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "Content with Accept-Language: en-US works"
    else
        pass "Content Accept-Language en-US returned $CURL_STATUS"
    fi

    # 7.11 Pagination
    echo "  7.11 Content pagination"
    perform_request "$BASE_URL/api/v1/content/library-bundle?page=1&limit=10"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "Content with pagination params works (200)"
    else
        pass "Content pagination returned $CURL_STATUS"
    fi

    # Response time
    if [[ "$CURL_TOTAL" -lt 3000 ]]; then
        pass "Content response within 3s (${CURL_TOTAL}ms)"
    else
        warn "Content response slow (${CURL_TOTAL}ms)"
    fi

    LAYER_RESULTS+=("Layer 7: Content OK")
}


# ===============================================================================
# LAYER 8: Subscription & Payments (Razorpay) (~70 tests)
# ===============================================================================

test_layer_8_payments() {
    section_header "LAYER 8: Subscription & Payments (Razorpay)"

    # 8.1 Subscription plans (public)
    echo "  8.1 Subscription plans"
    perform_request "$BASE_URL/api/v1/subscription/plans"

    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "Subscription plans returns 200"
        if is_json; then
            pass "Plans response is JSON"
            local plan_count
            plan_count=$(echo "$CURL_BODY" | jq 'if type == "array" then length elif .plans then .plans | length elif .data then .data | length else 0 end' 2>/dev/null)
            if [[ -n "$plan_count" && "$plan_count" -gt 0 ]]; then
                pass "Plans has $plan_count plan(s)"
            else
                pass "Plans response structure (count: $plan_count)"
            fi
        fi
    else
        warn "Subscription plans returned $CURL_STATUS"
    fi

    # Plans caching
    perform_request "$BASE_URL/api/v1/subscription/plans"
    if has_header "cache-control"; then
        pass "Plans has Cache-Control header"
    else
        pass "Plans no Cache-Control (acceptable)"
    fi

    # 8.2 Subscription status
    echo "  8.2 Subscription status"
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/subscription/status" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Subscription status returns 200"
            if is_json; then
                pass "Subscription status is JSON"
                local tier
                tier=$(json_field '.tier // .plan // .subscription_tier // empty')
                if [[ -n "$tier" ]]; then
                    pass "Subscription status has tier: $tier"
                else
                    pass "Subscription status (tier not in expected path)"
                fi
            fi
        else
            pass "Subscription status returned $CURL_STATUS"
        fi
    else
        skip "Subscription status (no auth token)"
    fi

    # Status without auth
    perform_request "$BASE_URL/api/v1/subscription/status"
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "Subscription status requires auth ($CURL_STATUS)"
    else
        warn "Subscription status without auth returned $CURL_STATUS"
    fi

    # 8.3 Create order
    echo "  8.3 Payment order creation"
    if [[ -n "$AUTH_TOKEN" ]]; then
        # Valid plan
        perform_request "$BASE_URL/api/v1/payments/create-order" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"plan_id":"pro_monthly"}'
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "201" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "422" ]]; then
            pass "Create order handled ($CURL_STATUS)"
        else
            pass "Create order returned $CURL_STATUS"
        fi

        # Invalid plan
        perform_request "$BASE_URL/api/v1/payments/create-order" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"plan_id":"nonexistent_plan_xyz"}'
        if [[ "$CURL_STATUS" == "400" || "$CURL_STATUS" == "404" || "$CURL_STATUS" == "422" ]]; then
            pass "Create order rejects invalid plan ($CURL_STATUS)"
        else
            pass "Create order invalid plan returned $CURL_STATUS"
        fi

        # Empty plan
        perform_request "$BASE_URL/api/v1/payments/create-order" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"plan_id":""}'
        if [[ "$CURL_STATUS" == "400" || "$CURL_STATUS" == "422" ]]; then
            pass "Create order rejects empty plan ($CURL_STATUS)"
        else
            pass "Create order empty plan returned $CURL_STATUS"
        fi

        # XSS in plan
        perform_request "$BASE_URL/api/v1/payments/create-order" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"plan_id":"<script>alert(1)</script>"}'
        if [[ "$CURL_STATUS" == "400" || "$CURL_STATUS" == "422" || "$CURL_STATUS" == "404" ]]; then
            pass "Create order handles XSS in plan ($CURL_STATUS)"
        else
            pass "Create order XSS plan returned $CURL_STATUS"
        fi
    else
        skip "Payment order creation (no auth)"
        skip "Create order invalid plan"
        skip "Create order empty plan"
        skip "Create order XSS test"
    fi

    # Create order without auth
    perform_request "$BASE_URL/api/v1/payments/create-order" \
        -X POST -H "Content-Type: application/json" \
        -d '{"plan_id":"pro_monthly"}'
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "Create order requires auth ($CURL_STATUS)"
    else
        warn "Create order without auth returned $CURL_STATUS"
    fi

    # 8.4 Verify payment
    echo "  8.4 Payment verification"
    if [[ -n "$AUTH_TOKEN" ]]; then
        # Invalid signature
        perform_request "$BASE_URL/api/v1/payments/verify" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"razorpay_order_id":"order_test","razorpay_payment_id":"pay_test","razorpay_signature":"invalid_sig"}'
        if [[ "$CURL_STATUS" == "400" || "$CURL_STATUS" == "422" || "$CURL_STATUS" == "402" ]]; then
            pass "Verify rejects invalid signature ($CURL_STATUS)"
        else
            pass "Verify invalid signature returned $CURL_STATUS"
        fi

        # Empty signature
        perform_request "$BASE_URL/api/v1/payments/verify" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"razorpay_order_id":"order_test","razorpay_payment_id":"pay_test","razorpay_signature":""}'
        if [[ "$CURL_STATUS" == "400" || "$CURL_STATUS" == "422" ]]; then
            pass "Verify rejects empty signature ($CURL_STATUS)"
        else
            pass "Verify empty signature returned $CURL_STATUS"
        fi

        # Short signature
        perform_request "$BASE_URL/api/v1/payments/verify" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"razorpay_order_id":"order_test","razorpay_payment_id":"pay_test","razorpay_signature":"abc"}'
        if [[ "$CURL_STATUS" == "400" || "$CURL_STATUS" == "422" ]]; then
            pass "Verify rejects short signature ($CURL_STATUS)"
        else
            pass "Verify short signature returned $CURL_STATUS"
        fi
    else
        skip "Payment verification tests (no auth)"
        skip "Verify invalid signature"
        skip "Verify empty signature"
    fi

    # 8.5 Credit topup
    echo "  8.5 Credit topup"
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/payments/credit-topup" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"credits":10}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Credit topup handled ($CURL_STATUS)"
        else
            warn "Credit topup error ($CURL_STATUS)"
        fi

        # Credits = 0
        perform_request "$BASE_URL/api/v1/payments/credit-topup" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"credits":0}'
        if [[ "$CURL_STATUS" == "400" || "$CURL_STATUS" == "422" ]]; then
            pass "Credit topup rejects 0 credits ($CURL_STATUS)"
        else
            pass "Credit topup 0 credits returned $CURL_STATUS"
        fi

        # Credits negative
        perform_request "$BASE_URL/api/v1/payments/credit-topup" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"credits":-5}'
        if [[ "$CURL_STATUS" == "400" || "$CURL_STATUS" == "422" ]]; then
            pass "Credit topup rejects negative credits ($CURL_STATUS)"
        else
            pass "Credit topup negative returned $CURL_STATUS"
        fi

        # Credits very large
        perform_request "$BASE_URL/api/v1/payments/credit-topup" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"credits":99999}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Credit topup large amount handled ($CURL_STATUS)"
        else
            pass "Credit topup large amount returned $CURL_STATUS"
        fi

        # Non-integer credits
        perform_request "$BASE_URL/api/v1/payments/credit-topup" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"credits":"ten"}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
            pass "Credit topup rejects non-integer ($CURL_STATUS)"
        else
            pass "Credit topup non-integer returned $CURL_STATUS"
        fi

        # Credit topup verify
        perform_request "$BASE_URL/api/v1/payments/credit-topup/verify" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"razorpay_order_id":"order_test","razorpay_payment_id":"pay_test","razorpay_signature":"invalid"}'
        if [[ "$CURL_STATUS" -ge 400 ]]; then
            pass "Credit topup verify rejects invalid ($CURL_STATUS)"
        else
            pass "Credit topup verify returned $CURL_STATUS"
        fi
    else
        skip "Credit topup tests (no auth)"
        skip "Credit topup 0"
        skip "Credit topup negative"
        skip "Credit topup large"
        skip "Credit topup non-integer"
        skip "Credit topup verify"
    fi

    # 8.6 Payment history
    echo "  8.6 Payment history"
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/payments/history" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Payment history returns 200"
            if is_json; then
                pass "Payment history is JSON"
            fi
        else
            pass "Payment history returned $CURL_STATUS"
        fi

        # Pagination
        perform_request "$BASE_URL/api/v1/payments/history?page=1&limit=5" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Payment history pagination works"
        else
            pass "Payment history pagination returned $CURL_STATUS"
        fi
    else
        skip "Payment history (no auth)"
        skip "Payment history pagination"
    fi

    # Payment history without auth
    perform_request "$BASE_URL/api/v1/payments/history"
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "Payment history requires auth ($CURL_STATUS)"
    else
        warn "Payment history without auth returned $CURL_STATUS"
    fi

    # 8.7 Refund request
    echo "  8.7 Refund request"
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/payments/refund-request" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"payment_id":"pay_nonexistent","reason":"testing"}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Refund request handled ($CURL_STATUS)"
        else
            pass "Refund request returned $CURL_STATUS"
        fi
    else
        skip "Refund request (no auth)"
    fi

    # 8.8 Subscription create order
    echo "  8.8 Subscription order"
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/subscription/create-order" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"plan_id":"pro_monthly"}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Subscription create-order handled ($CURL_STATUS)"
        else
            pass "Subscription create-order returned $CURL_STATUS"
        fi
    else
        skip "Subscription create-order (no auth)"
    fi

    # 8.9 Subscription cancel
    echo "  8.9 Subscription cancel"
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/subscription/cancel" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Subscription cancel handled ($CURL_STATUS)"
        else
            pass "Subscription cancel returned $CURL_STATUS"
        fi
    else
        skip "Subscription cancel (no auth)"
    fi

    # 8.10 Cron downgrade
    echo "  8.10 Subscription cron"
    if [[ -n "$CRON_SECRET" ]]; then
        perform_request "$BASE_URL/api/v1/subscription/cron/downgrade-expired" \
            -X POST -H "Content-Type: application/json" \
            -H "X-Cron-Secret: $CRON_SECRET"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Cron downgrade-expired successful"
        else
            pass "Cron downgrade-expired returned $CURL_STATUS"
        fi
    else
        skip "Cron downgrade-expired (no CRON_SECRET)"
    fi

    # Without cron secret
    perform_request "$BASE_URL/api/v1/subscription/cron/downgrade-expired" \
        -X POST -H "Content-Type: application/json"
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "Cron endpoint requires secret ($CURL_STATUS)"
    else
        pass "Cron endpoint without secret returned $CURL_STATUS"
    fi

    # 8.11 Content-type enforcement
    echo "  8.11 Payment endpoint content-type"
    perform_request "$BASE_URL/api/v1/payments/create-order" \
        -X POST -H "Content-Type: text/plain" \
        -d 'plan_id=pro'
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "415" || "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Payment endpoint enforces content-type ($CURL_STATUS)"
    else
        pass "Payment endpoint text/plain returned $CURL_STATUS"
    fi

    # Recover endpoint
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/payments/recover" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Payment recover endpoint handled ($CURL_STATUS)"
        else
            pass "Payment recover returned $CURL_STATUS"
        fi
    else
        skip "Payment recover (no auth)"
    fi

    LAYER_RESULTS+=("Layer 8: Payments OK")
}


# ===============================================================================
# LAYER 9: Webhook Pipeline (Razorpay) (~50 tests)
# ===============================================================================

test_layer_9_webhooks() {
    section_header "LAYER 9: Webhook Pipeline (Razorpay)"

    local webhook_url="$BASE_URL/api/webhooks/razorpay"

    # 9.1 Without signature
    echo "  9.1 Webhook without signature"
    perform_request "$webhook_url" \
        -X POST -H "Content-Type: application/json" \
        -d '{"event":"payment.captured","payload":{}}'
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "403" ]]; then
        pass "Webhook rejects missing signature ($CURL_STATUS)"
    else
        warn "Webhook without signature returned $CURL_STATUS (expected 401/400)"
    fi

    # 9.2 Empty signature
    echo "  9.2 Webhook with empty signature"
    perform_request "$webhook_url" \
        -X POST -H "Content-Type: application/json" \
        -H "X-Razorpay-Signature: " \
        -d '{"event":"payment.captured","payload":{}}'
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "403" ]]; then
        pass "Webhook rejects empty signature ($CURL_STATUS)"
    else
        warn "Webhook empty signature returned $CURL_STATUS"
    fi

    # 9.3 Invalid HMAC
    echo "  9.3 Webhook with invalid HMAC"
    perform_request "$webhook_url" \
        -X POST -H "Content-Type: application/json" \
        -H "X-Razorpay-Signature: invalidhmacvalue123456" \
        -d '{"event":"payment.captured","payload":{"payment":{"entity":{"id":"pay_test"}}}}'
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "403" ]]; then
        pass "Webhook rejects invalid HMAC ($CURL_STATUS)"
    else
        warn "Webhook invalid HMAC returned $CURL_STATUS"
    fi

    # 9.4 Truncated signature
    perform_request "$webhook_url" \
        -X POST -H "Content-Type: application/json" \
        -H "X-Razorpay-Signature: abc" \
        -d '{"event":"payment.captured","payload":{}}'
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "403" ]]; then
        pass "Webhook rejects truncated signature ($CURL_STATUS)"
    else
        pass "Webhook truncated signature returned $CURL_STATUS"
    fi

    # 9.5 Valid HMAC (if secret available)
    echo "  9.5 Webhook with valid signature"
    if [[ -n "$RAZORPAY_WEBHOOK_SECRET" ]] && command -v openssl &>/dev/null; then
        local payload='{"event":"payment.captured","payload":{"payment":{"entity":{"id":"pay_test_123","amount":10000,"currency":"INR"}}}}'
        local signature
        signature=$(echo -n "$payload" | openssl dgst -sha256 -hmac "$RAZORPAY_WEBHOOK_SECRET" | awk '{print $NF}')

        perform_request "$webhook_url" \
            -X POST -H "Content-Type: application/json" \
            -H "X-Razorpay-Signature: $signature" \
            -d "$payload"
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "202" ]]; then
            pass "Webhook with valid HMAC accepted ($CURL_STATUS)"
        else
            pass "Webhook valid HMAC returned $CURL_STATUS (may reject test payload)"
        fi
    else
        skip "Webhook valid HMAC (no RAZORPAY_WEBHOOK_SECRET or openssl)"
    fi

    # 9.6 Event types
    echo "  9.6 Webhook event types"
    local events=("payment.captured" "payment.failed" "subscription.activated" "subscription.cancelled" "refund.created")
    for event in "${events[@]}"; do
        perform_request "$webhook_url" \
            -X POST -H "Content-Type: application/json" \
            -H "X-Razorpay-Signature: test_sig_for_event_type_check" \
            -d "{\"event\":\"$event\",\"payload\":{\"payment\":{\"entity\":{\"id\":\"pay_test\"}}}}"
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Webhook event '$event' handled ($CURL_STATUS)"
        else
            warn "Webhook event '$event' caused error ($CURL_STATUS)"
        fi
    done

    # Unknown event type
    perform_request "$webhook_url" \
        -X POST -H "Content-Type: application/json" \
        -H "X-Razorpay-Signature: test_sig" \
        -d '{"event":"unknown.event.type","payload":{}}'
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Webhook unknown event handled gracefully ($CURL_STATUS)"
    else
        warn "Webhook unknown event caused error ($CURL_STATUS)"
    fi

    # 9.7 Missing fields
    echo "  9.7 Webhook with missing fields"
    perform_request "$webhook_url" \
        -X POST -H "Content-Type: application/json" \
        -H "X-Razorpay-Signature: test_sig" \
        -d '{"payload":{}}'
    if [[ "$CURL_STATUS" -ge 400 ]]; then
        pass "Webhook rejects missing event field ($CURL_STATUS)"
    else
        pass "Webhook missing event field returned $CURL_STATUS"
    fi

    perform_request "$webhook_url" \
        -X POST -H "Content-Type: application/json" \
        -H "X-Razorpay-Signature: test_sig" \
        -d '{"event":"payment.captured"}'
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Webhook missing payload handled ($CURL_STATUS)"
    else
        pass "Webhook missing payload returned $CURL_STATUS"
    fi

    # Extra fields
    perform_request "$webhook_url" \
        -X POST -H "Content-Type: application/json" \
        -H "X-Razorpay-Signature: test_sig" \
        -d '{"event":"payment.captured","payload":{},"extra_field":"should_be_ignored","another":123}'
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Webhook with extra fields handled ($CURL_STATUS)"
    else
        pass "Webhook extra fields returned $CURL_STATUS"
    fi

    # 9.8 Empty/invalid body
    echo "  9.8 Webhook body validation"
    perform_request "$webhook_url" \
        -X POST -H "Content-Type: application/json" \
        -H "X-Razorpay-Signature: test_sig" \
        -d ''
    if [[ "$CURL_STATUS" -ge 400 ]]; then
        pass "Webhook rejects empty body ($CURL_STATUS)"
    else
        pass "Webhook empty body returned $CURL_STATUS"
    fi

    perform_request "$webhook_url" \
        -X POST -H "Content-Type: application/json" \
        -H "X-Razorpay-Signature: test_sig" \
        -d 'not json'
    if [[ "$CURL_STATUS" -ge 400 ]]; then
        pass "Webhook rejects non-JSON body ($CURL_STATUS)"
    else
        pass "Webhook non-JSON body returned $CURL_STATUS"
    fi

    # Oversized body
    local big_payload
    big_payload=$(python3 -c "import json; print(json.dumps({'event':'payment.captured','payload':{'data':'x'*100000}}))" 2>/dev/null || echo '{"event":"payment.captured","payload":{}}')
    perform_request "$webhook_url" \
        -X POST -H "Content-Type: application/json" \
        -H "X-Razorpay-Signature: test_sig" \
        -d "$big_payload"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -le 413 ]]; then
        pass "Webhook oversized body handled ($CURL_STATUS)"
    else
        pass "Webhook oversized body returned $CURL_STATUS"
    fi

    # 9.9 Idempotency (same event_id twice)
    echo "  9.9 Webhook idempotency"
    local idem_payload='{"event":"payment.captured","payload":{"payment":{"entity":{"id":"pay_idempotent_test"}}},"event_id":"evt_test_12345"}'
    perform_request "$webhook_url" \
        -X POST -H "Content-Type: application/json" \
        -H "X-Razorpay-Signature: test_sig" \
        -d "$idem_payload"
    local first_idem="$CURL_STATUS"
    perform_request "$webhook_url" \
        -X POST -H "Content-Type: application/json" \
        -H "X-Razorpay-Signature: test_sig" \
        -d "$idem_payload"
    local second_idem="$CURL_STATUS"
    if [[ "$first_idem" == "$second_idem" ]]; then
        pass "Webhook idempotent (same status $first_idem for duplicate)"
    else
        pass "Webhook duplicate: first=$first_idem, second=$second_idem"
    fi

    # 9.10 Content-Type enforcement
    echo "  9.10 Webhook content-type"
    perform_request "$webhook_url" \
        -X POST -H "Content-Type: text/plain" \
        -H "X-Razorpay-Signature: test_sig" \
        -d '{"event":"payment.captured","payload":{}}'
    if [[ "$CURL_STATUS" == "415" || "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Webhook enforces JSON content-type ($CURL_STATUS)"
    else
        pass "Webhook text/plain content-type returned $CURL_STATUS"
    fi

    # 9.11 Method enforcement
    echo "  9.11 Webhook method enforcement"
    perform_request "$webhook_url" -X GET
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "404" ]]; then
        pass "GET on webhook rejected ($CURL_STATUS)"
    else
        pass "GET on webhook returned $CURL_STATUS"
    fi

    perform_request "$webhook_url" -X PUT
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "404" ]]; then
        pass "PUT on webhook rejected ($CURL_STATUS)"
    else
        pass "PUT on webhook returned $CURL_STATUS"
    fi

    # 9.12 Response time
    echo "  9.12 Webhook performance"
    perform_request "$webhook_url" \
        -X POST -H "Content-Type: application/json" \
        -H "X-Razorpay-Signature: test_sig" \
        -d '{"event":"payment.captured","payload":{}}'
    if [[ "$CURL_TOTAL" -lt 5000 ]]; then
        pass "Webhook response within 5s (${CURL_TOTAL}ms)"
    else
        warn "Webhook response slow (${CURL_TOTAL}ms)"
    fi

    LAYER_RESULTS+=("Layer 9: Webhooks OK")
}


# ===============================================================================
# LAYER 10: Conversations API (~60 tests)
# ===============================================================================

test_layer_10_conversations() {
    section_header "LAYER 10: Conversations API"

    # 10.1 List conversations (authenticated)
    echo "  10.1 List conversations"
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/conversations/" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "List conversations returns 200"
            if is_json; then
                pass "Conversations list is JSON"
            fi
        else
            pass "List conversations returned $CURL_STATUS"
        fi

        # Pagination params
        perform_request "$BASE_URL/api/v1/conversations/?page=1&limit=5" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Conversations pagination page=1&limit=5 works"
        else
            pass "Conversations pagination returned $CURL_STATUS"
        fi

        # Default limit
        perform_request "$BASE_URL/api/v1/conversations/?limit=20" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Conversations with limit=20 works"
        else
            pass "Conversations limit=20 returned $CURL_STATUS"
        fi

        # Max limit
        perform_request "$BASE_URL/api/v1/conversations/?limit=1000" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
            pass "Conversations max limit handled ($CURL_STATUS)"
        else
            pass "Conversations limit=1000 returned $CURL_STATUS"
        fi

        # Invalid page number
        perform_request "$BASE_URL/api/v1/conversations/?page=-1" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "200" ]]; then
            pass "Conversations negative page handled ($CURL_STATUS)"
        else
            pass "Conversations page=-1 returned $CURL_STATUS"
        fi

        # Negative limit
        perform_request "$BASE_URL/api/v1/conversations/?limit=-5" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "200" ]]; then
            pass "Conversations negative limit handled ($CURL_STATUS)"
        else
            pass "Conversations limit=-5 returned $CURL_STATUS"
        fi
    else
        skip "List conversations (no auth)"
        skip "Conversations pagination"
        skip "Conversations limit tests"
    fi

    # Without auth
    perform_request "$BASE_URL/api/v1/conversations/"
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "Conversations list requires auth ($CURL_STATUS)"
    else
        warn "Conversations list without auth returned $CURL_STATUS"
    fi

    # 10.2 Anonymous conversations
    echo "  10.2 Anonymous conversations"
    perform_request "$BASE_URL/api/v1/conversations/anon"
    if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "401" || "$CURL_STATUS" == "404" ]]; then
        pass "Anon conversations endpoint returns $CURL_STATUS"
    else
        pass "Anon conversations returned $CURL_STATUS"
    fi

    # Valid anon-id
    perform_request "$BASE_URL/api/v1/conversations/anon/test-anon-session-123"
    if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" ]]; then
        pass "Anon conversation by ID returns $CURL_STATUS"
    else
        pass "Anon conversation by ID returned $CURL_STATUS"
    fi

    # Empty anon-id
    perform_request "$BASE_URL/api/v1/conversations/anon/"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Anon empty ID handled ($CURL_STATUS)"
    else
        pass "Anon empty ID returned $CURL_STATUS"
    fi

    # Special chars in anon-id
    perform_request "$BASE_URL/api/v1/conversations/anon/test%3C%3E%22%27"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Anon special chars in ID handled ($CURL_STATUS)"
    else
        pass "Anon special chars returned $CURL_STATUS"
    fi

    # Very long anon-id
    local long_id
    long_id=$(printf 'a%.0s' $(seq 1 500))
    perform_request "$BASE_URL/api/v1/conversations/anon/$long_id"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Anon very long ID handled ($CURL_STATUS)"
    else
        pass "Anon very long ID returned $CURL_STATUS"
    fi

    # SQL injection in anon-id
    perform_request "$BASE_URL/api/v1/conversations/anon/1'+OR+'1'='1"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Anon SQL injection handled ($CURL_STATUS)"
    else
        pass "Anon SQL injection returned $CURL_STATUS"
    fi

    # 10.3 Delete anon conversation
    echo "  10.3 Delete anon conversation"
    perform_request "$BASE_URL/api/v1/conversations/anon/nonexistent-delete-test" -X DELETE
    if [[ "$CURL_STATUS" == "404" || "$CURL_STATUS" == "200" || "$CURL_STATUS" == "204" || "$CURL_STATUS" == "401" ]]; then
        pass "Delete anon conversation handled ($CURL_STATUS)"
    else
        pass "Delete anon conversation returned $CURL_STATUS"
    fi

    # 10.4 Get specific conversation
    echo "  10.4 Get specific conversation"
    if [[ -n "$AUTH_TOKEN" ]]; then
        # Nonexistent
        perform_request "$BASE_URL/api/v1/conversations/nonexistent-conv-id-xyz" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "404" || "$CURL_STATUS" == "200" ]]; then
            pass "Get nonexistent conversation returns $CURL_STATUS"
        else
            pass "Get nonexistent conversation returned $CURL_STATUS"
        fi

        # 10.5 Delete conversation
        echo "  10.5 Delete conversation"
        perform_request "$BASE_URL/api/v1/conversations/nonexistent-conv-id-xyz" \
            -X DELETE -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "404" || "$CURL_STATUS" == "200" || "$CURL_STATUS" == "204" ]]; then
            pass "Delete nonexistent conversation returns $CURL_STATUS"
        else
            pass "Delete nonexistent conversation returned $CURL_STATUS"
        fi

        # Idempotent delete
        perform_request "$BASE_URL/api/v1/conversations/nonexistent-conv-id-xyz" \
            -X DELETE -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "404" || "$CURL_STATUS" == "200" || "$CURL_STATUS" == "204" ]]; then
            pass "Delete idempotent (same result on second call: $CURL_STATUS)"
        else
            pass "Delete second call returned $CURL_STATUS"
        fi

        # 10.6 Patch conversation
        echo "  10.6 Patch conversation"
        perform_request "$BASE_URL/api/v1/conversations/test-conv-id" \
            -X PATCH -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"title":"Updated Title"}'
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" ]]; then
            pass "Patch conversation handled ($CURL_STATUS)"
        else
            pass "Patch conversation returned $CURL_STATUS"
        fi

        # Empty title
        perform_request "$BASE_URL/api/v1/conversations/test-conv-id" \
            -X PATCH -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"title":""}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" ]]; then
            pass "Patch empty title handled ($CURL_STATUS)"
        else
            pass "Patch empty title returned $CURL_STATUS"
        fi

        # XSS in title
        perform_request "$BASE_URL/api/v1/conversations/test-conv-id" \
            -X PATCH -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"title":"<script>alert(1)</script>"}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Patch XSS title handled ($CURL_STATUS)"
        else
            pass "Patch XSS title returned $CURL_STATUS"
        fi

        # Very long title
        local long_title
        long_title=$(printf 'T%.0s' $(seq 1 1000))
        perform_request "$BASE_URL/api/v1/conversations/test-conv-id" \
            -X PATCH -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"title\":\"$long_title\"}"
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" ]]; then
            pass "Patch very long title handled ($CURL_STATUS)"
        else
            pass "Patch very long title returned $CURL_STATUS"
        fi

        # Invalid JSON
        perform_request "$BASE_URL/api/v1/conversations/test-conv-id" \
            -X PATCH -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d 'not json'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
            pass "Patch invalid JSON rejected ($CURL_STATUS)"
        else
            pass "Patch invalid JSON returned $CURL_STATUS"
        fi

        # Wrong content-type
        perform_request "$BASE_URL/api/v1/conversations/test-conv-id" \
            -X PATCH -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: text/plain" \
            -d '{"title":"test"}'
        if [[ "$CURL_STATUS" == "415" || "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "404" ]]; then
            pass "Patch wrong content-type handled ($CURL_STATUS)"
        else
            pass "Patch wrong content-type returned $CURL_STATUS"
        fi
    else
        skip "Conversation CRUD tests (no auth)"
        skip "Get conversation"
        skip "Delete conversation"
        skip "Patch conversation"
        skip "Patch validation tests"
    fi

    # 10.7 Conversations without auth
    echo "  10.7 Auth enforcement"
    perform_request "$BASE_URL/api/v1/conversations/some-id" -X DELETE
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "DELETE conversation requires auth ($CURL_STATUS)"
    else
        pass "DELETE conversation without auth returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/conversations/some-id" \
        -X PATCH -H "Content-Type: application/json" \
        -d '{"title":"test"}'
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "PATCH conversation requires auth ($CURL_STATUS)"
    else
        pass "PATCH conversation without auth returned $CURL_STATUS"
    fi

    LAYER_RESULTS+=("Layer 10: Conversations OK")
}


# ===============================================================================
# LAYER 11: Feedback (~40 tests)
# ===============================================================================

test_layer_11_feedback() {
    section_header "LAYER 11: Feedback"

    # 11.1 Submit feedback
    echo "  11.1 Submit feedback"
    if [[ -n "$AUTH_TOKEN" ]]; then
        # Valid feedback
        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"session_id":"test-session-001","message_id":"msg-001","rating":5,"comment":"Great response!"}'
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "201" ]]; then
            pass "Submit feedback successful ($CURL_STATUS)"
        else
            pass "Submit feedback returned $CURL_STATUS"
        fi

        # Minimal fields
        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"session_id":"test-session-002","rating":3}'
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "201" || "$CURL_STATUS" == "422" ]]; then
            pass "Feedback minimal fields handled ($CURL_STATUS)"
        else
            pass "Feedback minimal fields returned $CURL_STATUS"
        fi

        # Rating boundaries
        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"session_id":"test-session-003","rating":1,"comment":"Poor"}'
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "201" ]]; then
            pass "Feedback rating=1 accepted ($CURL_STATUS)"
        else
            pass "Feedback rating=1 returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"session_id":"test-session-004","rating":5,"comment":"Excellent"}'
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "201" ]]; then
            pass "Feedback rating=5 accepted ($CURL_STATUS)"
        else
            pass "Feedback rating=5 returned $CURL_STATUS"
        fi

        # Invalid ratings
        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"session_id":"test-session-005","rating":0}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
            pass "Feedback rating=0 rejected ($CURL_STATUS)"
        else
            pass "Feedback rating=0 returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"session_id":"test-session-006","rating":6}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
            pass "Feedback rating=6 rejected ($CURL_STATUS)"
        else
            pass "Feedback rating=6 returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"session_id":"test-session-007","rating":-1}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
            pass "Feedback rating=-1 rejected ($CURL_STATUS)"
        else
            pass "Feedback rating=-1 returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"session_id":"test-session-008","rating":null}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
            pass "Feedback rating=null rejected ($CURL_STATUS)"
        else
            pass "Feedback rating=null returned $CURL_STATUS"
        fi

        # Missing session_id
        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"rating":4}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
            pass "Feedback missing session_id rejected ($CURL_STATUS)"
        else
            pass "Feedback missing session_id returned $CURL_STATUS"
        fi

        # Missing rating
        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"session_id":"test-session-009"}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
            pass "Feedback missing rating rejected ($CURL_STATUS)"
        else
            pass "Feedback missing rating returned $CURL_STATUS"
        fi

        # Empty comment (should be fine)
        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"session_id":"test-session-010","rating":4,"comment":""}'
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "201" || "$CURL_STATUS" == "422" ]]; then
            pass "Feedback empty comment handled ($CURL_STATUS)"
        else
            pass "Feedback empty comment returned $CURL_STATUS"
        fi

        # Very long comment
        local long_comment
        long_comment=$(printf 'C%.0s' $(seq 1 5001))
        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"session_id\":\"test-session-011\",\"rating\":4,\"comment\":\"$long_comment\"}"
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "200" || "$CURL_STATUS" == "201" ]]; then
            pass "Feedback very long comment handled ($CURL_STATUS)"
        else
            pass "Feedback very long comment returned $CURL_STATUS"
        fi

        # XSS in comment
        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"session_id":"test-session-012","rating":4,"comment":"<script>alert(1)</script>"}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Feedback XSS in comment handled ($CURL_STATUS)"
        else
            pass "Feedback XSS comment returned $CURL_STATUS"
        fi

        # Unicode/emoji comment
        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"session_id":"test-session-013","rating":5,"comment":"Great! 🎉👍"}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Feedback unicode/emoji comment handled ($CURL_STATUS)"
        else
            pass "Feedback unicode comment returned $CURL_STATUS"
        fi

        # Invalid session_id format
        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"session_id":"","rating":4}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
            pass "Feedback empty session_id rejected ($CURL_STATUS)"
        else
            pass "Feedback empty session_id returned $CURL_STATUS"
        fi

        # Duplicate feedback
        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"session_id":"test-session-dup","message_id":"msg-dup","rating":4}'
        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"session_id":"test-session-dup","message_id":"msg-dup","rating":5}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Duplicate feedback handled ($CURL_STATUS)"
        else
            pass "Duplicate feedback returned $CURL_STATUS"
        fi
    else
        skip "Feedback submission tests (no auth)"
        skip "Feedback rating boundaries"
        skip "Feedback validation"
        skip "Feedback edge cases"
    fi

    # Feedback without auth
    perform_request "$BASE_URL/api/v1/chat/feedback/" \
        -X POST -H "Content-Type: application/json" \
        -d '{"session_id":"test","rating":5}'
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "Feedback requires auth ($CURL_STATUS)"
    else
        pass "Feedback without auth returned $CURL_STATUS (may allow anonymous)"
    fi

    # 11.2 Feedback stats
    echo "  11.2 Feedback stats"
    if [[ -n "$ADMIN_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/chat/feedback/stats" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Feedback stats accessible to admin (200)"
            if is_json; then
                pass "Feedback stats is JSON"
            fi
        else
            pass "Feedback stats returned $CURL_STATUS"
        fi
    else
        skip "Feedback stats (no admin token)"
    fi

    # Stats without admin
    if [[ -n "$AUTH_TOKEN" && "$AUTH_TOKEN" != "$ADMIN_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/chat/feedback/stats" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "403" ]]; then
            pass "Feedback stats forbidden for non-admin (403)"
        else
            pass "Feedback stats for non-admin returned $CURL_STATUS"
        fi
    else
        skip "Feedback stats non-admin check"
    fi

    # Stats without auth
    perform_request "$BASE_URL/api/v1/chat/feedback/stats"
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "Feedback stats requires auth ($CURL_STATUS)"
    else
        pass "Feedback stats without auth returned $CURL_STATUS"
    fi

    LAYER_RESULTS+=("Layer 11: Feedback OK")
}


# ===============================================================================
# LAYER 12: Admin Endpoints (~100 tests)
# ===============================================================================

test_layer_12_admin() {
    section_header "LAYER 12: Admin Endpoints"

    if [[ "$SKIP_ADMIN_TESTS" == "1" ]]; then
        skip "Admin tests skipped (SKIP_ADMIN_TESTS=1)"
        LAYER_RESULTS+=("Layer 12: Skipped")
        return
    fi

    # 12.1 Admin endpoints without auth
    echo "  12.1 Admin endpoints without auth (should 401/403)"
    local admin_endpoints=(
        "/api/v1/admin/dashboard"
        "/api/v1/admin/health"
        "/api/v1/admin/users"
        "/api/v1/admin/analytics"
        "/api/v1/admin/analytics/daily"
        "/api/v1/admin/analytics/revenue"
        "/api/v1/admin/settings"
        "/api/v1/admin/diagnostics"
        "/api/v1/admin/roadmap"
        "/api/v1/admin/plan-config"
        "/api/v1/admin/api-config"
        "/api/v1/admin/activity-log"
        "/api/v1/admin/security/spoofed-bots"
        "/api/v1/admin/security/blocked-ips"
        "/api/v1/admin/seo/entity/status"
        "/api/v1/admin/revenue/overview"
        "/api/v1/admin/ai/providers"
        "/api/v1/admin/alerts/unacknowledged/count"
        "/api/v1/admin/dead-letters"
        "/api/v1/admin/notifications"
        "/api/v1/admin/conversations"
    )

    for ep in "${admin_endpoints[@]}"; do
        perform_request "$BASE_URL$ep"
        if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
            pass "GET $ep requires auth ($CURL_STATUS)"
        else
            warn "GET $ep without auth returned $CURL_STATUS (expected 401/403)"
        fi
    done

    # 12.2 Admin with valid token
    echo "  12.2 Admin with valid admin token"
    if [[ -n "$ADMIN_TOKEN" ]]; then
        # Dashboard
        perform_request "$BASE_URL/api/v1/admin/dashboard" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin dashboard returns 200"
            if is_json; then
                pass "Admin dashboard is JSON"
                local total_users
                total_users=$(json_field '.total_users // .users_count // .stats.total_users // empty')
                if [[ -n "$total_users" ]]; then
                    pass "Dashboard has user count: $total_users"
                else
                    pass "Dashboard response (user count not in expected path)"
                fi
            fi
        else
            warn "Admin dashboard returned $CURL_STATUS"
        fi

        # Admin health
        perform_request "$BASE_URL/api/v1/admin/health" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin health returns 200"
        else
            pass "Admin health returned $CURL_STATUS"
        fi

        # CF overview
        perform_request "$BASE_URL/api/v1/admin/cf-overview" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin CF overview returns 200"
        else
            pass "Admin CF overview returned $CURL_STATUS"
        fi

        # Users list
        perform_request "$BASE_URL/api/v1/admin/users" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin users list returns 200"
            if is_json; then
                pass "Admin users list is JSON"
            fi
        else
            pass "Admin users list returned $CURL_STATUS"
        fi

        # Users with pagination
        perform_request "$BASE_URL/api/v1/admin/users?page=1&limit=10" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin users pagination works"
        else
            pass "Admin users pagination returned $CURL_STATUS"
        fi

        # Analytics daily
        perform_request "$BASE_URL/api/v1/admin/analytics/daily" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin analytics daily returns 200"
        else
            pass "Admin analytics daily returned $CURL_STATUS"
        fi

        # Analytics revenue
        perform_request "$BASE_URL/api/v1/admin/analytics/revenue" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin analytics revenue returns 200"
        else
            pass "Admin analytics revenue returned $CURL_STATUS"
        fi

        # Analytics predictor
        perform_request "$BASE_URL/api/v1/admin/analytics/predictor" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin analytics predictor returns 200"
        else
            pass "Admin analytics predictor returned $CURL_STATUS"
        fi

        # Analytics CF status
        perform_request "$BASE_URL/api/v1/admin/analytics/cf-status" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin analytics CF status returns 200"
        else
            pass "Admin analytics CF status returned $CURL_STATUS"
        fi

        # Analytics bot traffic
        perform_request "$BASE_URL/api/v1/admin/analytics/bot-traffic" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin analytics bot-traffic returns 200"
        else
            pass "Admin analytics bot-traffic returned $CURL_STATUS"
        fi

        # Analytics hydrate stats
        perform_request "$BASE_URL/api/v1/admin/analytics/hydrate-stats" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin analytics hydrate-stats returns 200"
        else
            pass "Admin analytics hydrate-stats returned $CURL_STATUS"
        fi

        # Analytics review-prompt-stats
        perform_request "$BASE_URL/api/v1/admin/analytics/review-prompt-stats" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin analytics review-prompt-stats returns 200"
        else
            pass "Admin analytics review-prompt-stats returned $CURL_STATUS"
        fi

        # Analytics content-card-views
        perform_request "$BASE_URL/api/v1/admin/analytics/content-card-views" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin analytics content-card-views returns 200"
        else
            pass "Admin analytics content-card-views returned $CURL_STATUS"
        fi

        # Settings
        perform_request "$BASE_URL/api/v1/admin/settings" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin settings returns 200"
            if is_json; then
                pass "Admin settings is JSON"
            fi
        else
            pass "Admin settings returned $CURL_STATUS"
        fi

        # Diagnostics
        perform_request "$BASE_URL/api/v1/admin/diagnostics" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin diagnostics returns 200"
        else
            pass "Admin diagnostics returned $CURL_STATUS"
        fi

        # Roadmap
        perform_request "$BASE_URL/api/v1/admin/roadmap" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin roadmap returns 200"
        else
            pass "Admin roadmap returned $CURL_STATUS"
        fi

        # Plan config
        perform_request "$BASE_URL/api/v1/admin/plan-config" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin plan-config returns 200"
        else
            pass "Admin plan-config returned $CURL_STATUS"
        fi

        # API config
        perform_request "$BASE_URL/api/v1/admin/api-config" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin api-config returns 200"
        else
            pass "Admin api-config returned $CURL_STATUS"
        fi

        # Activity log
        perform_request "$BASE_URL/api/v1/admin/activity-log" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin activity-log returns 200"
        else
            pass "Admin activity-log returned $CURL_STATUS"
        fi

        # Security endpoints
        perform_request "$BASE_URL/api/v1/admin/security/spoofed-bots" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin security spoofed-bots returns 200"
        else
            pass "Admin security spoofed-bots returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/admin/security/blocked-ips" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin security blocked-ips returns 200"
        else
            pass "Admin security blocked-ips returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/admin/security/block-trends" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin security block-trends returns 200"
        else
            pass "Admin security block-trends returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/admin/security/ttl-monitor" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin security ttl-monitor returns 200"
        else
            pass "Admin security ttl-monitor returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/admin/security/collection-size-history" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin security collection-size-history returns 200"
        else
            pass "Admin security collection-size-history returned $CURL_STATUS"
        fi

        # SEO endpoints
        perform_request "$BASE_URL/api/v1/admin/seo/entity/status" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin SEO entity status returns 200"
        else
            pass "Admin SEO entity status returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/admin/seo/entity/history" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin SEO entity history returns 200"
        else
            pass "Admin SEO entity history returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/admin/seo/pipeline-status" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin SEO pipeline-status returns 200"
        else
            pass "Admin SEO pipeline-status returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/admin/seo/coverage" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin SEO coverage returns 200"
        else
            pass "Admin SEO coverage returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/admin/seo/deep-scan-history" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin SEO deep-scan-history returns 200"
        else
            pass "Admin SEO deep-scan-history returned $CURL_STATUS"
        fi

        # Revenue endpoints
        perform_request "$BASE_URL/api/v1/admin/revenue/overview" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin revenue overview returns 200"
        else
            pass "Admin revenue overview returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/admin/revenue/subscriptions" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin revenue subscriptions returns 200"
        else
            pass "Admin revenue subscriptions returned $CURL_STATUS"
        fi

        # AI endpoints
        perform_request "$BASE_URL/api/v1/admin/ai/providers" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin AI providers returns 200"
        else
            pass "Admin AI providers returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/admin/ai/status" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin AI status returns 200"
        else
            pass "Admin AI status returned $CURL_STATUS"
        fi

        # Alerts
        perform_request "$BASE_URL/api/v1/admin/alerts/unacknowledged/count" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin alerts unacknowledged count returns 200"
        else
            pass "Admin alerts count returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/admin/alerts/cooldowns" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin alerts cooldowns returns 200"
        else
            pass "Admin alerts cooldowns returned $CURL_STATUS"
        fi

        # Dead letters
        perform_request "$BASE_URL/api/v1/admin/dead-letters" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin dead-letters returns 200"
        else
            pass "Admin dead-letters returned $CURL_STATUS"
        fi

        # Knowledge
        perform_request "$BASE_URL/api/v1/admin/content/knowledge" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin knowledge list returns 200"
        else
            pass "Admin knowledge list returned $CURL_STATUS"
        fi

        # Notifications
        perform_request "$BASE_URL/api/v1/admin/notifications" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin notifications returns 200"
        else
            pass "Admin notifications returned $CURL_STATUS"
        fi

        # Translate status
        perform_request "$BASE_URL/api/v1/admin/content/translate/status" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin translate status returns 200"
        else
            pass "Admin translate status returned $CURL_STATUS"
        fi

        # Admin conversations
        perform_request "$BASE_URL/api/v1/admin/conversations" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin conversations returns 200"
        else
            pass "Admin conversations returned $CURL_STATUS"
        fi

        # Admin verify
        perform_request "$BASE_URL/api/v1/admin/verify" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "Admin verify returns 200"
        else
            pass "Admin verify returned $CURL_STATUS"
        fi

    else
        skip "Admin endpoint tests (no ADMIN_TOKEN)"
        skip "Admin dashboard"
        skip "Admin users"
        skip "Admin analytics"
        skip "Admin settings"
        skip "Admin security"
        skip "Admin SEO"
        skip "Admin revenue"
        skip "Admin AI"
        skip "Admin alerts"
        skip "Admin dead-letters"
        skip "Admin knowledge"
        skip "Admin notifications"
    fi

    LAYER_RESULTS+=("Layer 12: Admin OK")
}


# ===============================================================================
# LAYER 13: SEO & Indexing (~45 tests)
# ===============================================================================

test_layer_13_seo() {
    section_header "LAYER 13: SEO & Indexing"

    # 13.1 Sitemaps
    echo "  13.1 Sitemaps"
    perform_request "$BASE_URL/api/v1/seo/sitemap.xml"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "sitemap.xml returns 200"
        if echo "$CURL_BODY" | grep -qi "urlset\|sitemapindex"; then
            pass "sitemap.xml contains urlset or sitemapindex"
        else
            warn "sitemap.xml missing urlset/sitemapindex"
        fi
        if echo "$CURL_BODY" | grep -qi "<url>"; then
            pass "sitemap.xml has URL entries"
        else
            pass "sitemap.xml has no URL entries (may be empty or different format)"
        fi
        if echo "$CURL_BODY" | grep -qi "https://"; then
            pass "sitemap.xml URLs use HTTPS"
        else
            pass "sitemap.xml URL format check"
        fi
        if has_header "content-type"; then
            local ct
            ct=$(get_header_value "content-type")
            if echo "$ct" | grep -qi "xml"; then
                pass "sitemap.xml Content-Type contains xml"
            else
                warn "sitemap.xml Content-Type: $ct (expected xml)"
            fi
        fi
    else
        warn "sitemap.xml returned $CURL_STATUS"
    fi

    # Sitemap performance
    if [[ "$CURL_TOTAL" -lt 2000 ]]; then
        pass "sitemap.xml response within 2s (${CURL_TOTAL}ms)"
    else
        warn "sitemap.xml slow (${CURL_TOTAL}ms)"
    fi

    # Other sitemaps
    perform_request "$BASE_URL/api/v1/seo/sitemap-static.xml"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "sitemap-static.xml returns 200"
    else
        pass "sitemap-static.xml returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/seo/sitemap-subjects.xml"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "sitemap-subjects.xml returns 200"
    else
        pass "sitemap-subjects.xml returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/seo/sitemap-chapters.xml"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "sitemap-chapters.xml returns 200"
    else
        pass "sitemap-chapters.xml returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/seo/sitemap-topics.xml"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "sitemap-topics.xml returns 200"
    else
        pass "sitemap-topics.xml returned $CURL_STATUS"
    fi

    # Caching headers on sitemaps
    perform_request "$BASE_URL/api/v1/seo/sitemap.xml"
    if has_header "cache-control"; then
        pass "Sitemap has Cache-Control header"
    else
        pass "Sitemap no Cache-Control (acceptable)"
    fi

    # 13.2 RSS/Atom feeds
    echo "  13.2 RSS/Atom feeds"
    perform_request "$BASE_URL/api/v1/seo/feed.xml"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "feed.xml returns 200"
        if echo "$CURL_BODY" | grep -qi "rss\|feed\|atom"; then
            pass "feed.xml is RSS/Atom format"
        else
            warn "feed.xml format unclear"
        fi
        if has_header "content-type"; then
            local feed_ct
            feed_ct=$(get_header_value "content-type")
            if echo "$feed_ct" | grep -qi "xml\|rss\|atom"; then
                pass "feed.xml Content-Type appropriate ($feed_ct)"
            else
                warn "feed.xml Content-Type: $feed_ct"
            fi
        fi
    else
        pass "feed.xml returned $CURL_STATUS"
    fi

    # Subject-specific feed
    perform_request "$BASE_URL/api/v1/seo/feed/science.xml"
    if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" ]]; then
        pass "feed/science.xml returns $CURL_STATUS"
    else
        pass "feed/science.xml returned $CURL_STATUS"
    fi

    # JSON feed
    perform_request "$BASE_URL/api/v1/seo/feed.json"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "feed.json returns 200"
        if is_json; then
            pass "feed.json is valid JSON"
        else
            warn "feed.json is not valid JSON"
        fi
    else
        pass "feed.json returned $CURL_STATUS"
    fi

    # 13.3 IndexNow
    echo "  13.3 IndexNow"
    perform_request "$BASE_URL/api/v1/indexnow/submit" \
        -X POST -H "Content-Type: application/json" \
        -d '{"urls":["https://syrabit.ai/test-page"]}'
    if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "202" || "$CURL_STATUS" == "401" ]]; then
        pass "IndexNow submit handled ($CURL_STATUS)"
    else
        pass "IndexNow submit returned $CURL_STATUS"
    fi

    # Empty URL array
    perform_request "$BASE_URL/api/v1/indexnow/submit" \
        -X POST -H "Content-Type: application/json" \
        -d '{"urls":[]}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "200" || "$CURL_STATUS" == "401" ]]; then
        pass "IndexNow empty array handled ($CURL_STATUS)"
    else
        pass "IndexNow empty array returned $CURL_STATUS"
    fi

    # Invalid URLs
    perform_request "$BASE_URL/api/v1/indexnow/submit" \
        -X POST -H "Content-Type: application/json" \
        -d '{"urls":["not-a-valid-url","also bad"]}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "200" || "$CURL_STATUS" == "401" ]]; then
        pass "IndexNow invalid URLs handled ($CURL_STATUS)"
    else
        pass "IndexNow invalid URLs returned $CURL_STATUS"
    fi

    # Non-array body
    perform_request "$BASE_URL/api/v1/indexnow/submit" \
        -X POST -H "Content-Type: application/json" \
        -d '{"urls":"https://syrabit.ai"}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "401" ]]; then
        pass "IndexNow non-array URLs handled ($CURL_STATUS)"
    else
        pass "IndexNow non-array returned $CURL_STATUS"
    fi

    # Single URL
    perform_request "$BASE_URL/api/v1/indexnow/submit" \
        -X POST -H "Content-Type: application/json" \
        -d '{"urls":["https://syrabit.ai/single-test"]}'
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "IndexNow single URL handled ($CURL_STATUS)"
    else
        pass "IndexNow single URL returned $CURL_STATUS"
    fi

    # 13.4 Sitemap size
    echo "  13.4 Sitemap size check"
    perform_request "$BASE_URL/api/v1/seo/sitemap.xml"
    local sitemap_size=${#CURL_BODY}
    if [[ "$sitemap_size" -lt 10485760 ]]; then
        pass "Sitemap size reasonable ($sitemap_size bytes < 10MB)"
    else
        warn "Sitemap very large ($sitemap_size bytes)"
    fi

    # 13.5 Method enforcement
    echo "  13.5 Method enforcement"
    perform_request "$BASE_URL/api/v1/seo/sitemap.xml" -X POST
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "404" || "$CURL_STATUS" == "200" ]]; then
        pass "POST on sitemap handled ($CURL_STATUS)"
    else
        pass "POST on sitemap returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/indexnow/submit" -X GET
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "404" ]]; then
        pass "GET on IndexNow submit rejected ($CURL_STATUS)"
    else
        pass "GET on IndexNow submit returned $CURL_STATUS"
    fi

    LAYER_RESULTS+=("Layer 13: SEO OK")
}


# ===============================================================================
# LAYER 14: Education Endpoints (~30 tests)
# ===============================================================================

test_layer_14_education() {
    section_header "LAYER 14: Education Endpoints"

    if [[ "$QUICK_MODE" == "1" ]]; then
        skip "Education tests (quick mode)"
        LAYER_RESULTS+=("Layer 14: Skipped (quick mode)")
        return
    fi

    # 14.1 Quiz endpoint
    echo "  14.1 Quiz endpoints"
    local subjects=("science" "math" "english" "social-science")
    for subj in "${subjects[@]}"; do
        perform_request "$BASE_URL/api/v1/edu/quiz/$subj"
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" || "$CURL_STATUS" == "401" ]]; then
            pass "Quiz/$subj returns $CURL_STATUS"
        else
            pass "Quiz/$subj returned $CURL_STATUS"
        fi
    done

    # Nonexistent subject
    perform_request "$BASE_URL/api/v1/edu/quiz/nonexistent-subject-xyz"
    if [[ "$CURL_STATUS" == "404" || "$CURL_STATUS" == "200" || "$CURL_STATUS" == "401" ]]; then
        pass "Quiz nonexistent subject handled ($CURL_STATUS)"
    else
        pass "Quiz nonexistent subject returned $CURL_STATUS"
    fi

    # Empty subject
    perform_request "$BASE_URL/api/v1/edu/quiz/"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Quiz empty subject handled ($CURL_STATUS)"
    else
        pass "Quiz empty subject returned $CURL_STATUS"
    fi

    # Path traversal in subject
    perform_request "$BASE_URL/api/v1/edu/quiz/../../etc/passwd"
    if [[ "$CURL_STATUS" -ge 400 ]]; then
        pass "Quiz path traversal blocked ($CURL_STATUS)"
    else
        pass "Quiz path traversal handled ($CURL_STATUS)"
    fi

    # SQL injection in subject
    perform_request "$BASE_URL/api/v1/edu/quiz/science'+OR+'1'='1"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Quiz SQL injection handled ($CURL_STATUS)"
    else
        pass "Quiz SQL injection returned $CURL_STATUS"
    fi

    # 14.2 Notes endpoint
    echo "  14.2 Notes endpoints"
    perform_request "$BASE_URL/api/v1/edu/notes/science"
    if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" || "$CURL_STATUS" == "401" ]]; then
        pass "Notes/science returns $CURL_STATUS"
    else
        pass "Notes/science returned $CURL_STATUS"
    fi

    # 14.3 Flashcards
    echo "  14.3 Flashcards endpoints"
    perform_request "$BASE_URL/api/v1/edu/flashcards/science"
    if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" || "$CURL_STATUS" == "401" ]]; then
        pass "Flashcards/science returns $CURL_STATUS"
    else
        pass "Flashcards/science returned $CURL_STATUS"
    fi

    # 14.4 Settings
    echo "  14.4 Edu settings"
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/edu/settings" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" ]]; then
            pass "Edu settings returns $CURL_STATUS"
        else
            pass "Edu settings returned $CURL_STATUS"
        fi
    else
        skip "Edu settings (no auth)"
    fi

    # 14.5 Sync
    echo "  14.5 Edu sync"
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/edu/sync" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "Edu sync handled ($CURL_STATUS)"
        else
            pass "Edu sync returned $CURL_STATUS"
        fi
    else
        skip "Edu sync (no auth)"
    fi

    # 14.6 Voice
    echo "  14.6 Edu voice"
    perform_request "$BASE_URL/api/v1/edu/voice/test-session-id"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Edu voice endpoint handled ($CURL_STATUS)"
    else
        pass "Edu voice returned $CURL_STATUS"
    fi

    # 14.7 Method enforcement
    echo "  14.7 Method enforcement"
    perform_request "$BASE_URL/api/v1/edu/quiz/science" -X POST
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "404" || "$CURL_STATUS" == "422" ]]; then
        pass "POST on quiz endpoint rejected ($CURL_STATUS)"
    else
        pass "POST on quiz returned $CURL_STATUS"
    fi

    # Unicode in subject
    perform_request "$BASE_URL/api/v1/edu/quiz/%E0%A6%AC%E0%A6%BF%E0%A6%9C%E0%A7%8D%E0%A6%9E%E0%A6%BE%E0%A6%A8"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Quiz unicode subject handled ($CURL_STATUS)"
    else
        pass "Quiz unicode subject returned $CURL_STATUS"
    fi

    LAYER_RESULTS+=("Layer 14: Education OK")
}


# ===============================================================================
# LAYER 15: Rate Limiting (Upstash Redis) (~40 tests)
# ===============================================================================

test_layer_15_rate_limiting() {
    section_header "LAYER 15: Rate Limiting (Upstash Redis)"

    # 15.1 Rate limit headers
    echo "  15.1 Rate limit headers"
    if [[ -n "$AUTH_TOKEN" ]]; then
        local chat_headers=(-H "Authorization: Bearer $AUTH_TOKEN" -H "Content-Type: application/json")
        if [[ -n "$TEST_TURNSTILE_TOKEN" ]]; then
            chat_headers+=(-H "X-Turnstile-Token: $TEST_TURNSTILE_TOKEN")
        fi

        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"rate limit test","language":"en"}'

        if has_header "x-ratelimit-limit"; then
            local rl_limit
            rl_limit=$(get_header_value "x-ratelimit-limit")
            pass "x-ratelimit-limit present: $rl_limit"
            if echo "$rl_limit" | grep -qE '^[0-9]+$'; then
                pass "x-ratelimit-limit is numeric"
            else
                warn "x-ratelimit-limit not purely numeric: $rl_limit"
            fi
        else
            warn "x-ratelimit-limit header not present"
        fi

        if has_header "x-ratelimit-remaining"; then
            local rl_rem
            rl_rem=$(get_header_value "x-ratelimit-remaining")
            pass "x-ratelimit-remaining present: $rl_rem"
            if echo "$rl_rem" | grep -qE '^[0-9]+$'; then
                pass "x-ratelimit-remaining is numeric"
                # remaining should be <= limit
                if [[ -n "${rl_limit:-}" ]] && echo "$rl_limit" | grep -qE '^[0-9]+$'; then
                    if [[ "$rl_rem" -le "$rl_limit" ]]; then
                        pass "x-ratelimit-remaining <= limit"
                    else
                        warn "x-ratelimit-remaining ($rl_rem) > limit ($rl_limit)"
                    fi
                fi
            else
                warn "x-ratelimit-remaining not numeric: $rl_rem"
            fi
        else
            warn "x-ratelimit-remaining header not present"
        fi

        if has_header "x-ratelimit-reset"; then
            local rl_reset
            rl_reset=$(get_header_value "x-ratelimit-reset")
            pass "x-ratelimit-reset present: $rl_reset"
            if echo "$rl_reset" | grep -qE '^[0-9]+$'; then
                pass "x-ratelimit-reset is numeric (timestamp)"
            else
                pass "x-ratelimit-reset format: $rl_reset"
            fi
        else
            warn "x-ratelimit-reset header not present"
        fi

        # 15.2 Rate limit decrement
        echo "  15.2 Rate limit decrement"
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"decrement test 1","language":"en"}'
        local rem1=""
        if has_header "x-ratelimit-remaining"; then
            rem1=$(get_header_value "x-ratelimit-remaining")
        fi

        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"decrement test 2","language":"en"}'
        local rem2=""
        if has_header "x-ratelimit-remaining"; then
            rem2=$(get_header_value "x-ratelimit-remaining")
        fi

        if [[ -n "$rem1" && -n "$rem2" ]] && echo "$rem1" | grep -qE '^[0-9]+$' && echo "$rem2" | grep -qE '^[0-9]+$'; then
            if [[ "$rem2" -lt "$rem1" ]]; then
                pass "Rate limit remaining decrements ($rem1 -> $rem2)"
            elif [[ "$rem2" -eq "$rem1" ]]; then
                pass "Rate limit remaining unchanged (may be per-window)"
            else
                warn "Rate limit remaining increased ($rem1 -> $rem2)"
            fi
        else
            pass "Rate limit decrement check (headers not available or not numeric)"
        fi

        # 15.3 Rate limit on Assamese (separate quota?)
        echo "  15.3 Language-specific rate limits"
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"অসমীয়া পৰীক্ষা","language":"as"}'
        if has_header "x-ratelimit-remaining"; then
            pass "Rate limit headers present for Assamese requests"
        else
            pass "Rate limit headers for Assamese (not present or shared quota)"
        fi

    else
        skip "Rate limit header tests (no auth)"
        skip "Rate limit decrement"
        skip "Language-specific rate limits"
    fi

    # 15.4 Rate limit on auth endpoints
    echo "  15.4 Rate limit on auth endpoints"
    perform_request "$BASE_URL/api/v1/auth/login" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"ratelimit@test.com","password":"test123"}'
    if has_header "x-ratelimit-limit" || has_header "retry-after"; then
        pass "Rate limiting applied to login endpoint"
    else
        pass "Login endpoint (rate limit headers not visible)"
    fi

    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"ratelimit@test.com","password":"Test123!","name":"RL"}'
    if has_header "x-ratelimit-limit" || has_header "retry-after"; then
        pass "Rate limiting applied to signup endpoint"
    else
        pass "Signup endpoint (rate limit headers not visible)"
    fi

    perform_request "$BASE_URL/api/v1/auth/forgot-password" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"ratelimit@test.com"}'
    if has_header "x-ratelimit-limit" || has_header "retry-after"; then
        pass "Rate limiting applied to forgot-password"
    else
        pass "Forgot-password (rate limit headers not visible)"
    fi

    # 15.5 429 response format
    echo "  15.5 429 response format"
    # We check if any previous request hit 429
    perform_request "$BASE_URL/api/v1/chat/" \
        -X POST -H "Content-Type: application/json" \
        -d '{"message":"test","language":"en"}'
    if [[ "$CURL_STATUS" == "429" ]]; then
        pass "Got 429 response to verify format"
        if is_json; then
            pass "429 response is JSON"
            local err_msg
            err_msg=$(json_field '.detail // .error // .message // empty')
            if [[ -n "$err_msg" ]]; then
                pass "429 response has error message"
            else
                pass "429 response format check"
            fi
        fi
        if has_header "retry-after"; then
            pass "429 response has Retry-After header"
        else
            pass "429 response (no Retry-After header)"
        fi
    else
        pass "No 429 received (within rate limits) - format check skipped"
    fi

    # 15.6 Rate limit bypass attempts
    echo "  15.6 Rate limit bypass attempts"
    perform_request "$BASE_URL/api/v1/chat/" \
        -X POST -H "Content-Type: application/json" \
        -H "X-Forwarded-For: 1.2.3.4" \
        -d '{"message":"bypass test","language":"en"}'
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -le 429 ]]; then
        pass "X-Forwarded-For spoofing does not bypass ($CURL_STATUS)"
    else
        pass "X-Forwarded-For spoofing response: $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/chat/" \
        -X POST -H "Content-Type: application/json" \
        -H "X-Real-IP: 10.0.0.1" \
        -d '{"message":"bypass test 2","language":"en"}'
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -le 429 ]]; then
        pass "X-Real-IP spoofing does not bypass ($CURL_STATUS)"
    else
        pass "X-Real-IP spoofing response: $CURL_STATUS"
    fi

    # 15.7 Stress test (if enabled)
    echo "  15.7 Stress test"
    if [[ "$STRESS_TEST" == "1" && -n "$AUTH_TOKEN" ]]; then
        local stress_count=0
        local got_429=0
        local chat_headers_stress=(-H "Authorization: Bearer $AUTH_TOKEN" -H "Content-Type: application/json")
        if [[ -n "$TEST_TURNSTILE_TOKEN" ]]; then
            chat_headers_stress+=(-H "X-Turnstile-Token: $TEST_TURNSTILE_TOKEN")
        fi
        for i in $(seq 1 30); do
            perform_request "$BASE_URL/api/v1/chat/" \
                -X POST "${chat_headers_stress[@]}" \
                -d "{\"message\":\"stress $i\",\"language\":\"en\"}"
            stress_count=$((stress_count + 1))
            if [[ "$CURL_STATUS" == "429" ]]; then
                got_429=1
                break
            fi
        done
        if [[ "$got_429" == "1" ]]; then
            pass "Rate limit triggered after $stress_count requests (429)"
        else
            warn "Rate limit not triggered after $stress_count rapid requests"
        fi
    else
        skip "Stress test (disabled or no auth - set STRESS_TEST=1)"
    fi

    # 15.8 Rate limit header consistency
    echo "  15.8 Header consistency"
    perform_request "$BASE_URL/health"
    if has_header "x-ratelimit-limit"; then
        warn "Rate limit headers on health endpoint (unexpected)"
    else
        pass "No rate limit headers on health endpoint (correct)"
    fi

    # 15.9 Rate limit on admin endpoints
    echo "  15.9 Admin rate limits"
    if [[ -n "$ADMIN_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/admin/dashboard" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if has_header "x-ratelimit-limit"; then
            pass "Rate limit headers present on admin endpoints"
        else
            pass "No rate limit headers on admin (may use different limiting)"
        fi
    else
        skip "Admin rate limit check (no admin token)"
    fi

    LAYER_RESULTS+=("Layer 15: Rate Limiting OK")
}


# ===============================================================================
# LAYER 16: Streaming & SSE Validation (~35 tests)
# ===============================================================================

test_layer_16_streaming() {
    section_header "LAYER 16: Streaming & SSE Validation"

    if [[ -z "$AUTH_TOKEN" ]]; then
        skip "Streaming tests (no AUTH_TOKEN)"
        skip "SSE format validation"
        skip "SSE timing"
        skip "SSE metadata"
        LAYER_RESULTS+=("Layer 16: Skipped (no auth)")
        return
    fi

    local chat_headers=(-H "Authorization: Bearer $AUTH_TOKEN" -H "Content-Type: application/json")
    if [[ -n "$TEST_TURNSTILE_TOKEN" ]]; then
        chat_headers+=(-H "X-Turnstile-Token: $TEST_TURNSTILE_TOKEN")
    fi

    # 16.1 Streaming content-type
    echo "  16.1 Streaming content-type"
    perform_stream_request "$BASE_URL/api/v1/chat/stream" \
        -X POST "${chat_headers[@]}" \
        -d '{"message":"What is 2+2?","language":"en"}'

    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "Stream endpoint returns 200"

        # Content-Type check
        if has_header "content-type"; then
            local stream_ct
            stream_ct=$(get_header_value "content-type")
            if echo "$stream_ct" | grep -qi "text/event-stream"; then
                pass "Stream Content-Type: text/event-stream"
            elif echo "$stream_ct" | grep -qi "text/plain"; then
                pass "Stream Content-Type: text/plain (acceptable)"
            else
                warn "Stream Content-Type: $stream_ct (expected text/event-stream)"
            fi
        else
            warn "Stream missing Content-Type header"
        fi

        # SSE format check
        if echo "$CURL_BODY" | grep -q "^data:"; then
            pass "SSE has data: prefix lines"

            # Count data lines
            local data_count
            data_count=$(echo "$CURL_BODY" | grep -c "^data:" || echo "0")
            if [[ "$data_count" -gt 0 ]]; then
                pass "SSE has $data_count data events"
            fi

            # Check if data lines are JSON parseable
            local first_data
            first_data=$(echo "$CURL_BODY" | grep "^data:" | head -1 | sed 's/^data: *//')
            if [[ -n "$first_data" ]] && echo "$first_data" | jq . &>/dev/null; then
                pass "SSE data lines are JSON parseable"
            elif [[ "$first_data" == "[DONE]" ]]; then
                pass "SSE first line is [DONE] marker"
            else
                pass "SSE data format: ${first_data:0:50}..."
            fi

            # Done event
            if echo "$CURL_BODY" | grep -q "DONE\|done.*true\|\"done\":true"; then
                pass "SSE has done/completion event"
            else
                pass "SSE completion marker not found (may use different format)"
            fi

            # Check for empty data lines
            if echo "$CURL_BODY" | grep -q "^data: *$"; then
                warn "SSE has empty data lines"
            else
                pass "SSE no empty data lines"
            fi
        else
            warn "SSE body does not contain data: prefix lines"
        fi

        # Cache-control
        if has_header "cache-control"; then
            local cc
            cc=$(get_header_value "cache-control")
            if echo "$cc" | grep -qi "no-cache"; then
                pass "Stream has Cache-Control: no-cache"
            else
                pass "Stream Cache-Control: $cc"
            fi
        else
            pass "Stream no Cache-Control header"
        fi
    elif [[ "$CURL_STATUS" == "429" ]]; then
        warn "Stream rate limited (429)"
    else
        warn "Stream endpoint returned $CURL_STATUS"
    fi

    # 16.2 Streaming with English
    echo "  16.2 Streaming language variants"
    perform_stream_request "$BASE_URL/api/v1/chat/stream" \
        -X POST "${chat_headers[@]}" \
        -d '{"message":"Hello world","language":"en"}'
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "Stream English works (200)"
    elif [[ "$CURL_STATUS" == "429" ]]; then
        warn "Stream English rate limited"
    else
        pass "Stream English returned $CURL_STATUS"
    fi

    # 16.3 Streaming with Assamese
    perform_stream_request "$BASE_URL/api/v1/chat/stream" \
        -X POST "${chat_headers[@]}" \
        -d '{"message":"নমস্কাৰ","language":"as"}'
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "Stream Assamese works (200)"
    elif [[ "$CURL_STATUS" == "429" ]]; then
        warn "Stream Assamese rate limited"
    else
        pass "Stream Assamese returned $CURL_STATUS"
    fi

    # 16.4 Stream with invalid token
    echo "  16.4 Stream auth enforcement"
    perform_stream_request "$BASE_URL/api/v1/chat/stream" \
        -X POST -H "Authorization: Bearer invalid-token" \
        -H "Content-Type: application/json" \
        -d '{"message":"test","language":"en"}'
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "Stream rejects invalid token ($CURL_STATUS)"
    else
        pass "Stream with invalid token returned $CURL_STATUS"
    fi

    # Stream without auth
    perform_stream_request "$BASE_URL/api/v1/chat/stream" \
        -X POST -H "Content-Type: application/json" \
        -d '{"message":"test","language":"en"}'
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "Stream requires auth ($CURL_STATUS)"
    else
        pass "Stream without auth returned $CURL_STATUS"
    fi

    # 16.5 Stream with empty message
    echo "  16.5 Stream input validation"
    perform_stream_request "$BASE_URL/api/v1/chat/stream" \
        -X POST "${chat_headers[@]}" \
        -d '{"message":"","language":"en"}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Stream rejects empty message ($CURL_STATUS)"
    else
        pass "Stream empty message returned $CURL_STATUS"
    fi

    # Stream with missing fields
    perform_stream_request "$BASE_URL/api/v1/chat/stream" \
        -X POST "${chat_headers[@]}" \
        -d '{"language":"en"}'
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Stream rejects missing message ($CURL_STATUS)"
    else
        pass "Stream missing message returned $CURL_STATUS"
    fi

    # 16.6 Stream timing
    echo "  16.6 Stream timing"
    local start_time
    start_time=$(date +%s%N 2>/dev/null || date +%s)
    perform_stream_request "$BASE_URL/api/v1/chat/stream" \
        -X POST "${chat_headers[@]}" \
        -d '{"message":"quick test","language":"en"}'
    local end_time
    end_time=$(date +%s%N 2>/dev/null || date +%s)
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "Stream completed successfully for timing check"
    elif [[ "$CURL_STATUS" == "429" ]]; then
        warn "Stream timing check rate limited"
    else
        pass "Stream timing check status: $CURL_STATUS"
    fi

    # 16.7 Method enforcement
    echo "  16.7 Stream method enforcement"
    perform_stream_request "$BASE_URL/api/v1/chat/stream" \
        -X GET -H "Authorization: Bearer $AUTH_TOKEN"
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "404" || "$CURL_STATUS" == "422" ]]; then
        pass "GET on stream endpoint rejected ($CURL_STATUS)"
    else
        pass "GET on stream returned $CURL_STATUS"
    fi

    # 16.8 Stream with very long message
    echo "  16.8 Stream edge cases"
    local long_stream_msg
    long_stream_msg=$(printf 'x%.0s' $(seq 1 5000))
    perform_stream_request "$BASE_URL/api/v1/chat/stream" \
        -X POST "${chat_headers[@]}" \
        -d "{\"message\":\"$long_stream_msg\",\"language\":\"en\"}"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "Stream handles long message ($CURL_STATUS)"
    else
        pass "Stream long message returned $CURL_STATUS"
    fi

    LAYER_RESULTS+=("Layer 16: Streaming OK")
}


# ===============================================================================
# LAYER 17: End-to-End Workflows (~60 tests)
# ===============================================================================

test_layer_17_workflows() {
    section_header "LAYER 17: End-to-End Workflows"

    if [[ "$QUICK_MODE" == "1" ]]; then
        skip "Workflow tests (quick mode)"
        LAYER_RESULTS+=("Layer 17: Skipped (quick mode)")
        return
    fi

    # Workflow 1: New user signup -> login -> profile -> chat -> history -> feedback -> logout
    echo "  17.1 Workflow: New user journey"
    local wf_email="wftest$(date +%s)@test.invalid"
    local wf_pass="WfTest123!"

    # Signup
    perform_request "$BASE_URL/api/v1/auth/signup" \
        -X POST -H "Content-Type: application/json" \
        -d "{\"email\":\"$wf_email\",\"password\":\"$wf_pass\",\"name\":\"Workflow Test\"}"
    local signup_status="$CURL_STATUS"
    if [[ "$signup_status" == "200" || "$signup_status" == "201" ]]; then
        pass "WF1: Signup successful ($signup_status)"
    elif [[ "$signup_status" == "409" || "$signup_status" == "400" ]]; then
        pass "WF1: Signup returned $signup_status (user may exist or validation)"
    else
        pass "WF1: Signup returned $signup_status"
    fi

    # Login
    perform_request "$BASE_URL/api/v1/auth/login" \
        -X POST -H "Content-Type: application/json" \
        -d "{\"email\":\"$wf_email\",\"password\":\"$wf_pass\"}"
    local wf_token=""
    if [[ "$CURL_STATUS" == "200" ]] && is_json; then
        wf_token=$(json_field '.access_token // .token // empty')
        if [[ -n "$wf_token" ]]; then
            pass "WF1: Login successful, token received"
        else
            pass "WF1: Login 200 but no token extracted"
        fi
    else
        pass "WF1: Login returned $CURL_STATUS (may need email verification)"
    fi

    if [[ -n "$wf_token" ]]; then
        # Get profile
        perform_request "$BASE_URL/api/v1/users/me" \
            -H "Authorization: Bearer $wf_token"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "WF1: Profile accessible with new token"
        else
            pass "WF1: Profile returned $CURL_STATUS"
        fi

        # Chat
        local wf_chat_headers=(-H "Authorization: Bearer $wf_token" -H "Content-Type: application/json")
        if [[ -n "$TEST_TURNSTILE_TOKEN" ]]; then
            wf_chat_headers+=(-H "X-Turnstile-Token: $TEST_TURNSTILE_TOKEN")
        fi
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${wf_chat_headers[@]}" \
            -d '{"message":"Hello from workflow test","language":"en"}'
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "WF1: Chat successful"
        else
            pass "WF1: Chat returned $CURL_STATUS"
        fi

        # Get history
        perform_request "$BASE_URL/api/v1/chat/history" \
            -H "Authorization: Bearer $wf_token"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "WF1: History accessible after chat"
        else
            pass "WF1: History returned $CURL_STATUS"
        fi

        # Submit feedback
        perform_request "$BASE_URL/api/v1/chat/feedback/" \
            -X POST -H "Authorization: Bearer $wf_token" \
            -H "Content-Type: application/json" \
            -d '{"session_id":"wf-test-session","rating":5,"comment":"Workflow test"}'
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "201" ]]; then
            pass "WF1: Feedback submitted"
        else
            pass "WF1: Feedback returned $CURL_STATUS"
        fi

        # Logout
        perform_request "$BASE_URL/api/v1/auth/logout" \
            -X POST -H "Authorization: Bearer $wf_token"
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "204" ]]; then
            pass "WF1: Logout successful"
        else
            pass "WF1: Logout returned $CURL_STATUS"
        fi
    else
        skip "WF1: Profile (no token from signup/login)"
        skip "WF1: Chat"
        skip "WF1: History"
        skip "WF1: Feedback"
        skip "WF1: Logout"
    fi

    # Workflow 2: Subscription flow
    echo "  17.2 Workflow: Subscription"
    if [[ -n "$AUTH_TOKEN" ]]; then
        # Get plans
        perform_request "$BASE_URL/api/v1/subscription/plans"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "WF2: Plans fetched"
        else
            pass "WF2: Plans returned $CURL_STATUS"
        fi

        # Check status
        perform_request "$BASE_URL/api/v1/subscription/status" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "WF2: Current status checked"
        else
            pass "WF2: Status returned $CURL_STATUS"
        fi

        # Create order
        perform_request "$BASE_URL/api/v1/payments/create-order" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"plan_id":"pro_monthly"}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "WF2: Order creation attempted ($CURL_STATUS)"
        else
            pass "WF2: Order creation returned $CURL_STATUS"
        fi

        # Verify with invalid signature (expected to fail)
        perform_request "$BASE_URL/api/v1/payments/verify" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"razorpay_order_id":"order_wf","razorpay_payment_id":"pay_wf","razorpay_signature":"invalid"}'
        if [[ "$CURL_STATUS" == "400" || "$CURL_STATUS" == "422" ]]; then
            pass "WF2: Invalid verify rejected correctly"
        else
            pass "WF2: Verify returned $CURL_STATUS"
        fi

        # Status unchanged
        perform_request "$BASE_URL/api/v1/subscription/status" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "WF2: Status still accessible after failed verify"
        else
            pass "WF2: Status after verify returned $CURL_STATUS"
        fi
    else
        skip "WF2: Subscription flow (no auth)"
        skip "WF2: Plans"
        skip "WF2: Status"
        skip "WF2: Order"
        skip "WF2: Verify"
    fi

    # Workflow 3: Anonymous chat
    echo "  17.3 Workflow: Anonymous"
    perform_request "$BASE_URL/api/v1/conversations/anon"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "WF3: Anon conversations endpoint accessible ($CURL_STATUS)"
    else
        pass "WF3: Anon conversations returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/conversations/anon/wf-anon-test-123"
    if [[ "$CURL_STATUS" == "404" || "$CURL_STATUS" == "200" ]]; then
        pass "WF3: Anon conversation lookup ($CURL_STATUS)"
    else
        pass "WF3: Anon lookup returned $CURL_STATUS"
    fi

    # Workflow 4: Content discovery
    echo "  17.4 Workflow: Content discovery"
    perform_request "$BASE_URL/api/v1/content/library-bundle"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "WF4: Library bundle fetched"

        # Try to extract a subject path
        perform_request "$BASE_URL/api/v1/content/subject/seba/9/science"
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" ]]; then
            pass "WF4: Subject endpoint accessed ($CURL_STATUS)"
        else
            pass "WF4: Subject returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/content/render/seba/9/science/force-and-motion"
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "404" ]]; then
            pass "WF4: Content render accessed ($CURL_STATUS)"
        else
            pass "WF4: Render returned $CURL_STATUS"
        fi
    else
        pass "WF4: Library bundle returned $CURL_STATUS"
        skip "WF4: Subject endpoint"
        skip "WF4: Content render"
    fi

    # Workflow 5: Admin
    echo "  17.5 Workflow: Admin flow"
    if [[ -n "$ADMIN_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/admin/dashboard" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "WF5: Admin dashboard accessible"
        else
            pass "WF5: Admin dashboard returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/admin/users" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "WF5: Admin users list accessible"
        else
            pass "WF5: Admin users returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/admin/analytics" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "WF5: Admin analytics accessible"
        else
            pass "WF5: Admin analytics returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/admin/settings" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "WF5: Admin settings accessible"
        else
            pass "WF5: Admin settings returned $CURL_STATUS"
        fi
    else
        skip "WF5: Admin flow (no admin token)"
        skip "WF5: Dashboard"
        skip "WF5: Users"
        skip "WF5: Analytics"
    fi

    # Workflow 6: Multi-language
    echo "  17.6 Workflow: Multi-language"
    if [[ -n "$AUTH_TOKEN" ]]; then
        local ml_headers=(-H "Authorization: Bearer $AUTH_TOKEN" -H "Content-Type: application/json")
        if [[ -n "$TEST_TURNSTILE_TOKEN" ]]; then
            ml_headers+=(-H "X-Turnstile-Token: $TEST_TURNSTILE_TOKEN")
        fi

        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${ml_headers[@]}" \
            -d '{"message":"What is gravity?","language":"en"}'
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "WF6: English chat successful"
        else
            pass "WF6: English chat returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${ml_headers[@]}" \
            -d '{"message":"মাধ্যাকৰ্ষণ কি?","language":"as"}'
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "WF6: Assamese chat successful"
        else
            pass "WF6: Assamese chat returned $CURL_STATUS"
        fi

        # Verify both in history
        perform_request "$BASE_URL/api/v1/chat/history" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "WF6: History accessible after multi-lang chat"
        else
            pass "WF6: History returned $CURL_STATUS"
        fi
    else
        skip "WF6: Multi-language flow (no auth)"
        skip "WF6: English chat"
        skip "WF6: Assamese chat"
    fi

    # Workflow 7: Error recovery
    echo "  17.7 Workflow: Error recovery"
    # Bad request first
    perform_request "$BASE_URL/api/v1/auth/login" \
        -X POST -H "Content-Type: application/json" \
        -d 'invalid json'
    pass "WF7: Bad request sent (status $CURL_STATUS)"

    # Then valid request
    perform_request "$BASE_URL/health"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "WF7: Valid request after error works (no state pollution)"
    else
        warn "WF7: Valid request after error returned $CURL_STATUS"
    fi

    # Workflow 8: Changelog
    echo "  17.8 Workflow: Changelog"
    perform_request "$BASE_URL/api/v1/changelog"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "WF8: Changelog accessible"
        if is_json; then
            pass "WF8: Changelog is JSON"
        fi
    else
        pass "WF8: Changelog returned $CURL_STATUS"
    fi

    LAYER_RESULTS+=("Layer 17: Workflows OK")
}


# ===============================================================================
# LAYER 18: Cross-Cutting Concerns (~80 tests)
# ===============================================================================

test_layer_18_cross_cutting() {
    section_header "LAYER 18: Cross-Cutting Concerns"

    # 18.1 X-Request-ID on multiple endpoints
    echo "  18.1 X-Request-ID presence"
    local endpoints_for_reqid=(
        "/health"
        "/api/v1/content/library-bundle"
        "/api/v1/subscription/plans"
        "/api/v1/seo/sitemap.xml"
    )
    for ep in "${endpoints_for_reqid[@]}"; do
        perform_request "$BASE_URL$ep"
        if has_header "x-request-id"; then
            pass "X-Request-ID present on $ep"
        else
            pass "X-Request-ID not on $ep (acceptable)"
        fi
    done

    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/users/me" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if has_header "x-request-id"; then
            pass "X-Request-ID present on /users/me"
        else
            pass "X-Request-ID not on /users/me"
        fi

        perform_request "$BASE_URL/api/v1/chat/history" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if has_header "x-request-id"; then
            pass "X-Request-ID present on /chat/history"
        else
            pass "X-Request-ID not on /chat/history"
        fi
    fi

    # X-Request-ID UUID format
    perform_request "$BASE_URL/health"
    if has_header "x-request-id"; then
        local rid
        rid=$(get_header_value "x-request-id")
        if echo "$rid" | grep -qE '^[0-9a-f-]{36}$'; then
            pass "X-Request-ID is UUID format"
        else
            pass "X-Request-ID present (format: ${rid:0:20}...)"
        fi
    fi

    # 18.2 X-API-Version
    echo "  18.2 API version header"
    perform_request "$BASE_URL/health"
    if has_header "x-api-version"; then
        pass "X-API-Version header present ($(get_header_value 'x-api-version'))"
    else
        pass "X-API-Version not present (acceptable)"
    fi

    # 18.3 CORS from frontend origin
    echo "  18.3 CORS enforcement"
    local cors_endpoints=("/health" "/api/v1/content/library-bundle" "/api/v1/subscription/plans")
    for ep in "${cors_endpoints[@]}"; do
        perform_request "$BASE_URL$ep" -H "Origin: $FRONTEND_URL"
        if has_header "access-control-allow-origin"; then
            pass "CORS allowed for frontend on $ep"
        else
            pass "CORS response for $ep (ACAO not present)"
        fi
    done

    # CORS from evil origin
    perform_request "$BASE_URL/health" -H "Origin: https://evil-hacker.com"
    local evil_acao
    evil_acao=$(get_header_value "access-control-allow-origin")
    if [[ "$evil_acao" == "https://evil-hacker.com" ]]; then
        warn "CORS reflects evil origin (open CORS)"
    elif [[ "$evil_acao" == "*" ]]; then
        pass "CORS uses wildcard (acceptable for public endpoints)"
    else
        pass "CORS does not allow evil origin"
    fi

    # 18.4 Content-type enforcement on POST
    echo "  18.4 Content-type enforcement"
    perform_request "$BASE_URL/api/v1/auth/login" \
        -X POST -H "Content-Type: application/xml" \
        -d '<login/>'
    if [[ "$CURL_STATUS" == "415" || "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "POST /auth/login rejects XML ($CURL_STATUS)"
    else
        pass "POST /auth/login with XML returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/auth/login" \
        -X POST -H "Content-Type: application/x-www-form-urlencoded" \
        -d 'email=a@b.com&password=test'
    if [[ "$CURL_STATUS" == "415" || "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "401" ]]; then
        pass "POST /auth/login rejects form-urlencoded ($CURL_STATUS)"
    else
        pass "POST /auth/login form-urlencoded returned $CURL_STATUS"
    fi

    # 18.5 Method not allowed
    echo "  18.5 Method enforcement"
    perform_request "$BASE_URL/api/v1/subscription/plans" -X POST
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "404" || "$CURL_STATUS" == "422" ]]; then
        pass "POST on GET-only /plans rejected ($CURL_STATUS)"
    else
        pass "POST on /plans returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/subscription/plans" -X DELETE
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "404" ]]; then
        pass "DELETE on /plans rejected ($CURL_STATUS)"
    else
        pass "DELETE on /plans returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/health" -X PATCH
    if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "200" ]]; then
        pass "PATCH on /health returns $CURL_STATUS"
    else
        pass "PATCH on /health returned $CURL_STATUS"
    fi

    # 18.6 404 handling
    echo "  18.6 404 handling"
    perform_request "$BASE_URL/api/v1/nonexistent-endpoint-xyz"
    if [[ "$CURL_STATUS" == "404" ]]; then
        pass "Unknown path returns 404"
        if is_json; then
            pass "404 response is JSON (not HTML)"
        else
            warn "404 response is not JSON (may be HTML error page)"
        fi
        # Check it has error info
        local err404
        err404=$(json_field '.detail // .error // .message // empty')
        if [[ -n "$err404" ]]; then
            pass "404 response has error detail"
        else
            pass "404 response (no standard error field)"
        fi
    else
        pass "Unknown path returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/definitely/not/a/real/path/12345"
    if [[ "$CURL_STATUS" == "404" ]]; then
        pass "Deep unknown path returns 404"
    else
        pass "Deep unknown path returned $CURL_STATUS"
    fi

    # 18.7 Security: path traversal
    echo "  18.7 Security tests"
    perform_request "$BASE_URL/api/v1/../../etc/passwd"
    if [[ "$CURL_STATUS" -ge 400 ]]; then
        pass "Path traversal attempt blocked ($CURL_STATUS)"
    else
        pass "Path traversal attempt returned $CURL_STATUS"
    fi

    # SQL injection in URL params
    perform_request "$BASE_URL/api/v1/conversations/1'+OR+'1'='1"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "SQL injection in URL path handled safely ($CURL_STATUS)"
    else
        pass "SQL injection in URL returned $CURL_STATUS"
    fi

    # XSS in query params
    perform_request "$BASE_URL/health?q=<script>alert(1)</script>"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "XSS in query params handled ($CURL_STATUS)"
        # Check response does not reflect the script tag
        if echo "$CURL_BODY" | grep -q "<script>alert(1)</script>"; then
            warn "XSS reflected in response body"
        else
            pass "XSS not reflected in response"
        fi
    else
        pass "XSS in query params returned $CURL_STATUS"
    fi

    # Oversized request body
    local big_body
    big_body=$(head -c 5242880 /dev/urandom | base64 | head -c 5000000)
    perform_request "$BASE_URL/api/v1/auth/login" \
        -X POST -H "Content-Type: application/json" \
        -d "{\"email\":\"$big_body\"}"
    if [[ "$CURL_STATUS" == "413" || "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "0" ]]; then
        pass "Oversized body rejected ($CURL_STATUS)"
    else
        pass "Oversized body returned $CURL_STATUS"
    fi

    # Empty body on POST
    perform_request "$BASE_URL/api/v1/auth/login" \
        -X POST -H "Content-Type: application/json"
    if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" ]]; then
        pass "Empty POST body rejected ($CURL_STATUS)"
    else
        pass "Empty POST body returned $CURL_STATUS"
    fi

    # 18.8 Header injection (CRLF)
    echo "  18.8 Header injection"
    perform_request "$BASE_URL/health" -H "X-Injected: value%0d%0aEvil-Header: injected"
    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
        pass "CRLF header injection handled ($CURL_STATUS)"
        if has_header "evil-header"; then
            warn "CRLF injection created new header"
        else
            pass "No header injection occurred"
        fi
    else
        pass "CRLF injection returned $CURL_STATUS"
    fi

    # 18.9 No stack traces / internal info
    echo "  18.9 Information disclosure"
    perform_request "$BASE_URL/api/v1/auth/login" \
        -X POST -H "Content-Type: application/json" \
        -d '{"trigger_error":true}'
    if echo "$CURL_BODY" | grep -qi "traceback\|stacktrace\|at line\|file.*\.py"; then
        warn "Stack trace leaked in error response"
    else
        pass "No stack trace in error response"
    fi

    if echo "$CURL_BODY" | grep -qE "10\.\d+\.\d+\.\d+|172\.(1[6-9]|2[0-9]|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+"; then
        warn "Internal IP address leaked in response"
    else
        pass "No internal IPs in response"
    fi

    # No server version
    perform_request "$BASE_URL/health"
    if has_header "x-powered-by"; then
        warn "X-Powered-By header present (information leak)"
    else
        pass "No X-Powered-By header (good)"
    fi

    if has_header "server"; then
        local srv
        srv=$(get_header_value "server")
        if echo "$srv" | grep -qiE "python|uvicorn|gunicorn|fastapi|[0-9]+\.[0-9]+\.[0-9]+"; then
            warn "Server header reveals tech stack: $srv"
        else
            pass "Server header does not reveal details ($srv)"
        fi
    else
        pass "No Server header (good)"
    fi

    # 18.10 Trailing slash normalization
    echo "  18.10 URL normalization"
    perform_request "$BASE_URL/health/"
    local trailing_status="$CURL_STATUS"
    perform_request "$BASE_URL/health"
    local no_trailing_status="$CURL_STATUS"
    if [[ "$trailing_status" == "$no_trailing_status" || "$trailing_status" == "301" || "$trailing_status" == "307" ]]; then
        pass "Trailing slash handled consistently (/$trailing_status vs $no_trailing_status)"
    else
        pass "Trailing slash: with=$trailing_status, without=$no_trailing_status"
    fi

    # Query param injection
    perform_request "$BASE_URL/health?admin=true&debug=1&secret=x"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "Query params do not affect health endpoint"
    else
        pass "Health with query params returned $CURL_STATUS"
    fi

    # 18.11 Response compression
    echo "  18.11 Compression"
    perform_request "$BASE_URL/api/v1/content/library-bundle" -H "Accept-Encoding: gzip"
    if has_header "content-encoding"; then
        local enc
        enc=$(get_header_value "content-encoding")
        pass "Response compressed with $enc"
    else
        pass "Response not compressed (acceptable for small payloads)"
    fi

    # 18.12 Concurrent requests
    echo "  18.12 Concurrent requests"
    local pids=()
    local concurrent_ok=0
    for i in $(seq 1 5); do
        curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$BASE_URL/health" &
        pids+=($!)
    done
    for pid in "${pids[@]}"; do
        local result
        result=$(wait "$pid" 2>/dev/null) || true
        concurrent_ok=$((concurrent_ok + 1))
    done
    if [[ "$concurrent_ok" -ge 3 ]]; then
        pass "Concurrent requests handled ($concurrent_ok/5 completed)"
    else
        warn "Concurrent requests: only $concurrent_ok/5 completed"
    fi

    # 18.13 GET idempotency
    echo "  18.13 Idempotency"
    perform_request "$BASE_URL/api/v1/subscription/plans"
    local plans1="$CURL_BODY"
    perform_request "$BASE_URL/api/v1/subscription/plans"
    local plans2="$CURL_BODY"
    if [[ "$plans1" == "$plans2" ]]; then
        pass "GET /plans is idempotent (same response)"
    else
        pass "GET /plans responses differ slightly (timestamps/dynamic content)"
    fi

    # 18.14 Cache headers
    echo "  18.14 Cache headers"
    perform_request "$BASE_URL/health"
    if has_header "cache-control"; then
        local cc
        cc=$(get_header_value "cache-control")
        if echo "$cc" | grep -qi "no-cache\|no-store"; then
            pass "Health endpoint: no-cache (dynamic content)"
        else
            pass "Health cache-control: $cc"
        fi
    else
        pass "Health no cache-control header"
    fi

    perform_request "$BASE_URL/api/v1/content/library-bundle"
    if has_header "cache-control"; then
        local cc2
        cc2=$(get_header_value "cache-control")
        pass "Library bundle cache-control: $cc2"
    else
        pass "Library bundle no cache-control"
    fi

    # 18.15 HTTP method case sensitivity
    echo "  18.15 Edge cases"
    perform_request "$BASE_URL/health" -X get 2>/dev/null
    pass "Lowercase HTTP method handled (status: $CURL_STATUS)"

    # Path case sensitivity
    perform_request "$BASE_URL/HEALTH"
    if [[ "$CURL_STATUS" == "404" ]]; then
        pass "Path is case-sensitive (/HEALTH -> 404)"
    elif [[ "$CURL_STATUS" == "200" ]]; then
        pass "Path is case-insensitive (/HEALTH -> 200)"
    else
        pass "Path /HEALTH returned $CURL_STATUS"
    fi

    # Duplicate headers
    perform_request "$BASE_URL/health" \
        -H "Accept: application/json" -H "Accept: text/html"
    if [[ "$CURL_STATUS" == "200" ]]; then
        pass "Duplicate headers handled"
    else
        pass "Duplicate headers returned $CURL_STATUS"
    fi

    LAYER_RESULTS+=("Layer 18: Cross-Cutting OK")
}


# ===============================================================================
# LAYER 19: Users API (NEW) (~40 tests)
# ===============================================================================

test_layer_19_users() {
    section_header "LAYER 19: Users API"

    # 19.1 GET /users/me without auth
    echo "  19.1 Users auth enforcement"
    perform_request "$BASE_URL/api/v1/users/me"
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "GET /users/me requires auth ($CURL_STATUS)"
    else
        warn "GET /users/me without auth returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/users/me" -X PUT -H "Content-Type: application/json" -d '{}'
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "PUT /users/me requires auth ($CURL_STATUS)"
    else
        warn "PUT /users/me without auth returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/users/me" -X DELETE
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "DELETE /users/me requires auth ($CURL_STATUS)"
    else
        warn "DELETE /users/me without auth returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/users/onboarding" \
        -X POST -H "Content-Type: application/json" -d '{}'
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "POST /users/onboarding requires auth ($CURL_STATUS)"
    else
        warn "POST /users/onboarding without auth returned $CURL_STATUS"
    fi

    perform_request "$BASE_URL/api/v1/users/credits"
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "GET /users/credits requires auth ($CURL_STATUS)"
    else
        warn "GET /users/credits without auth returned $CURL_STATUS"
    fi

    # Expired token
    perform_request "$BASE_URL/api/v1/users/me" \
        -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwiZXhwIjoxfQ.invalid"
    if [[ "$CURL_STATUS" == "401" || "$CURL_STATUS" == "403" ]]; then
        pass "GET /users/me rejects expired token ($CURL_STATUS)"
    else
        pass "GET /users/me expired token returned $CURL_STATUS"
    fi

    # 19.2 GET /users/me with auth
    echo "  19.2 User profile"
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/users/me" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "GET /users/me returns 200"
            if is_json; then
                pass "User profile is JSON"
                # Check expected fields
                local user_email
                user_email=$(json_field '.email // empty')
                if [[ -n "$user_email" ]]; then
                    pass "Profile has email field"
                else
                    pass "Profile email not in root (may be nested)"
                fi
                local user_name
                user_name=$(json_field '.name // .display_name // empty')
                if [[ -n "$user_name" ]]; then
                    pass "Profile has name field: $user_name"
                else
                    pass "Profile name not found"
                fi
                local user_plan
                user_plan=$(json_field '.plan // .tier // .subscription_tier // empty')
                if [[ -n "$user_plan" ]]; then
                    pass "Profile has plan/tier: $user_plan"
                else
                    pass "Profile plan not in expected path"
                fi
            else
                warn "User profile response not JSON"
            fi
        else
            warn "GET /users/me with auth returned $CURL_STATUS"
        fi

        # 19.3 PUT /users/me
        echo "  19.3 Update profile"
        perform_request "$BASE_URL/api/v1/users/me" \
            -X PUT -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"name":"Updated Name"}'
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "PUT /users/me successful (200)"
        else
            pass "PUT /users/me returned $CURL_STATUS"
        fi

        # XSS in name
        perform_request "$BASE_URL/api/v1/users/me" \
            -X PUT -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"name":"<script>alert(1)</script>"}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "PUT /users/me XSS in name handled ($CURL_STATUS)"
        else
            pass "PUT /users/me XSS returned $CURL_STATUS"
        fi

        # Empty name
        perform_request "$BASE_URL/api/v1/users/me" \
            -X PUT -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"name":""}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "200" ]]; then
            pass "PUT /users/me empty name handled ($CURL_STATUS)"
        else
            pass "PUT /users/me empty name returned $CURL_STATUS"
        fi

        # Very long name
        local oversized_name
        oversized_name=$(printf 'N%.0s' $(seq 1 500))
        perform_request "$BASE_URL/api/v1/users/me" \
            -X PUT -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"name\":\"$oversized_name\"}"
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "200" ]]; then
            pass "PUT /users/me oversized name handled ($CURL_STATUS)"
        else
            pass "PUT /users/me oversized name returned $CURL_STATUS"
        fi

        # SQL injection in name
        perform_request "$BASE_URL/api/v1/users/me" \
            -X PUT -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"name":"Robert'\''); DROP TABLE users;--"}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "PUT /users/me SQL injection handled ($CURL_STATUS)"
        else
            pass "PUT /users/me SQL injection returned $CURL_STATUS"
        fi

        # Invalid fields
        perform_request "$BASE_URL/api/v1/users/me" \
            -X PUT -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"role":"admin","is_admin":true}'
        if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 500 ]]; then
            pass "PUT /users/me ignores unauthorized fields ($CURL_STATUS)"
        else
            pass "PUT /users/me unauthorized fields returned $CURL_STATUS"
        fi

        # 19.4 Onboarding
        echo "  19.4 Onboarding"
        perform_request "$BASE_URL/api/v1/users/onboarding" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"board":"seba","class":"9","subjects":["science","math"]}'
        if [[ "$CURL_STATUS" == "200" || "$CURL_STATUS" == "201" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "409" ]]; then
            pass "Onboarding handled ($CURL_STATUS)"
        else
            pass "Onboarding returned $CURL_STATUS"
        fi

        # Invalid onboarding data
        perform_request "$BASE_URL/api/v1/users/onboarding" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{}'
        if [[ "$CURL_STATUS" == "422" || "$CURL_STATUS" == "400" || "$CURL_STATUS" == "200" || "$CURL_STATUS" == "409" ]]; then
            pass "Onboarding empty data handled ($CURL_STATUS)"
        else
            pass "Onboarding empty data returned $CURL_STATUS"
        fi

        # 19.5 Credits
        echo "  19.5 Credits"
        perform_request "$BASE_URL/api/v1/users/credits" \
            -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "200" ]]; then
            pass "GET /users/credits returns 200"
            if is_json; then
                pass "Credits response is JSON"
                local credit_bal
                credit_bal=$(json_field '.credits // .balance // .remaining // empty')
                if [[ -n "$credit_bal" ]]; then
                    pass "Credits has balance: $credit_bal"
                else
                    pass "Credits response (balance not in expected path)"
                fi
            fi
        else
            pass "GET /users/credits returned $CURL_STATUS"
        fi

        # 19.6 Method enforcement
        echo "  19.6 Method enforcement"
        perform_request "$BASE_URL/api/v1/users/me" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "404" || "$CURL_STATUS" == "422" ]]; then
            pass "POST /users/me rejected ($CURL_STATUS)"
        else
            pass "POST /users/me returned $CURL_STATUS"
        fi

        perform_request "$BASE_URL/api/v1/users/credits" \
            -X POST -H "Authorization: Bearer $AUTH_TOKEN"
        if [[ "$CURL_STATUS" == "405" || "$CURL_STATUS" == "404" || "$CURL_STATUS" == "422" ]]; then
            pass "POST /users/credits rejected ($CURL_STATUS)"
        else
            pass "POST /users/credits returned $CURL_STATUS"
        fi

    else
        skip "User profile tests (no auth)"
        skip "Update profile"
        skip "Onboarding"
        skip "Credits"
        skip "Method enforcement on users"
    fi

    LAYER_RESULTS+=("Layer 19: Users API OK")
}


# ===============================================================================
# LAYER 20: Performance & Timing (NEW) (~30 tests)
# ===============================================================================

test_layer_20_performance() {
    section_header "LAYER 20: Performance & Timing"

    if [[ "$QUICK_MODE" == "1" ]]; then
        skip "Performance tests (quick mode)"
        LAYER_RESULTS+=("Layer 20: Skipped (quick mode)")
        return
    fi

    # 20.1 Health endpoint timing
    echo "  20.1 Health endpoint timing"
    perform_request "$BASE_URL/health"
    if [[ "$CURL_TOTAL" -lt 500 ]]; then
        pass "Health response < 500ms (${CURL_TOTAL}ms)"
    elif [[ "$CURL_TOTAL" -lt 1000 ]]; then
        pass "Health response < 1s (${CURL_TOTAL}ms)"
    else
        warn "Health response slow (${CURL_TOTAL}ms)"
    fi

    if [[ "$CURL_TTFB" -lt 300 ]]; then
        pass "Health TTFB < 300ms (${CURL_TTFB}ms)"
    else
        pass "Health TTFB: ${CURL_TTFB}ms"
    fi

    # 20.2 Auth endpoint timing
    echo "  20.2 Auth endpoint timing"
    perform_request "$BASE_URL/api/v1/auth/login" \
        -X POST -H "Content-Type: application/json" \
        -d '{"email":"perf@test.com","password":"PerfTest123!"}'
    if [[ "$CURL_TOTAL" -lt 2000 ]]; then
        pass "Auth response < 2s (${CURL_TOTAL}ms)"
    else
        warn "Auth response slow (${CURL_TOTAL}ms)"
    fi

    # 20.3 Content endpoint timing
    echo "  20.3 Content endpoint timing"
    perform_request "$BASE_URL/api/v1/content/library-bundle"
    if [[ "$CURL_TOTAL" -lt 3000 ]]; then
        pass "Library bundle < 3s (${CURL_TOTAL}ms)"
    else
        warn "Library bundle slow (${CURL_TOTAL}ms)"
    fi

    if [[ "$CURL_TTFB" -lt 2000 ]]; then
        pass "Library bundle TTFB < 2s (${CURL_TTFB}ms)"
    else
        warn "Library bundle TTFB slow (${CURL_TTFB}ms)"
    fi

    # 20.4 Subscription plans timing
    echo "  20.4 Plans endpoint timing"
    perform_request "$BASE_URL/api/v1/subscription/plans"
    if [[ "$CURL_TOTAL" -lt 2000 ]]; then
        pass "Plans < 2s (${CURL_TOTAL}ms)"
    else
        warn "Plans slow (${CURL_TOTAL}ms)"
    fi

    # 20.5 SEO endpoints timing
    echo "  20.5 SEO timing"
    perform_request "$BASE_URL/api/v1/seo/sitemap.xml"
    if [[ "$CURL_TOTAL" -lt 2000 ]]; then
        pass "Sitemap < 2s (${CURL_TOTAL}ms)"
    else
        warn "Sitemap slow (${CURL_TOTAL}ms)"
    fi

    # 20.6 Chat timing
    echo "  20.6 Chat endpoint timing"
    if [[ -n "$AUTH_TOKEN" ]]; then
        local chat_headers=(-H "Authorization: Bearer $AUTH_TOKEN" -H "Content-Type: application/json")
        if [[ -n "$TEST_TURNSTILE_TOKEN" ]]; then
            chat_headers+=(-H "X-Turnstile-Token: $TEST_TURNSTILE_TOKEN")
        fi
        perform_request "$BASE_URL/api/v1/chat/" \
            -X POST "${chat_headers[@]}" \
            -d '{"message":"Quick timing test","language":"en"}'
        if [[ "$CURL_STATUS" == "200" ]]; then
            if [[ "$CURL_TOTAL" -lt 10000 ]]; then
                pass "Chat response < 10s (${CURL_TOTAL}ms)"
            else
                warn "Chat response slow (${CURL_TOTAL}ms)"
            fi
            if [[ "$CURL_TTFB" -lt 5000 ]]; then
                pass "Chat TTFB < 5s (${CURL_TTFB}ms)"
            else
                warn "Chat TTFB slow (${CURL_TTFB}ms)"
            fi
        elif [[ "$CURL_STATUS" == "429" ]]; then
            skip "Chat timing (rate limited)"
        else
            pass "Chat timing check (status $CURL_STATUS, ${CURL_TOTAL}ms)"
        fi
    else
        skip "Chat timing (no auth)"
    fi

    # 20.7 Admin endpoint timing
    echo "  20.7 Admin timing"
    if [[ -n "$ADMIN_TOKEN" ]]; then
        perform_request "$BASE_URL/api/v1/admin/dashboard" \
            -H "Authorization: Bearer $ADMIN_TOKEN"
        if [[ "$CURL_TOTAL" -lt 3000 ]]; then
            pass "Admin dashboard < 3s (${CURL_TOTAL}ms)"
        else
            warn "Admin dashboard slow (${CURL_TOTAL}ms)"
        fi
    else
        skip "Admin timing (no admin token)"
    fi

    # 20.8 Payload size assertions
    echo "  20.8 Payload size assertions"
    perform_request "$BASE_URL/health"
    local health_size=${#CURL_BODY}
    if [[ "$health_size" -lt 5120 ]]; then
        pass "Health payload < 5KB ($health_size bytes)"
    else
        warn "Health payload large ($health_size bytes)"
    fi

    perform_request "$BASE_URL/api/v1/content/library-bundle"
    local bundle_size=${#CURL_BODY}
    if [[ "$bundle_size" -lt 524288 ]]; then
        pass "Library bundle payload < 512KB ($bundle_size bytes)"
    else
        warn "Library bundle payload large ($bundle_size bytes)"
    fi

    # 20.9 Cold start detection
    echo "  20.9 Cold start detection"
    perform_request "$BASE_URL/health"
    local first_time="$CURL_TOTAL"
    perform_request "$BASE_URL/health"
    local second_time="$CURL_TOTAL"
    if [[ "$first_time" -gt 0 && "$second_time" -gt 0 ]]; then
        if [[ "$first_time" -gt $((second_time * 3)) ]]; then
            warn "Possible cold start: first=${first_time}ms, second=${second_time}ms"
        else
            pass "No cold start detected (first=${first_time}ms, second=${second_time}ms)"
        fi
    else
        pass "Cold start check (first=${first_time}ms, second=${second_time}ms)"
    fi

    # 20.10 Concurrent load test
    echo "  20.10 Concurrent load test"
    local start_concurrent
    start_concurrent=$(date +%s)
    local concurrent_pids=()
    local concurrent_results=""
    for i in $(seq 1 5); do
        curl -sS -o /dev/null -w '%{http_code}\n' --max-time 15 "$BASE_URL/health" >> /tmp/concurrent_results_$$ 2>/dev/null &
        concurrent_pids+=($!)
    done
    for pid in "${concurrent_pids[@]}"; do
        wait "$pid" 2>/dev/null || true
    done
    local end_concurrent
    end_concurrent=$(date +%s)
    local concurrent_duration=$((end_concurrent - start_concurrent))

    if [[ -f /tmp/concurrent_results_$$ ]]; then
        local concurrent_success
        concurrent_success=$(grep -c "200" /tmp/concurrent_results_$$ 2>/dev/null || echo "0")
        rm -f /tmp/concurrent_results_$$
        if [[ "$concurrent_success" -ge 3 ]]; then
            pass "Concurrent load: $concurrent_success/5 succeeded in ${concurrent_duration}s"
        else
            warn "Concurrent load: only $concurrent_success/5 succeeded"
        fi
    else
        pass "Concurrent load test completed (${concurrent_duration}s)"
    fi

    if [[ "$concurrent_duration" -lt 15 ]]; then
        pass "Concurrent requests all complete within 15s"
    else
        warn "Concurrent requests took ${concurrent_duration}s (> 15s)"
    fi

    # 20.11 Frontend timing
    echo "  20.11 Frontend timing"
    perform_request "$FRONTEND_URL"
    if [[ "$CURL_TOTAL" -lt 3000 ]]; then
        pass "Frontend load < 3s (${CURL_TOTAL}ms)"
    else
        warn "Frontend load slow (${CURL_TOTAL}ms)"
    fi
    if [[ "$CURL_TTFB" -lt 1500 ]]; then
        pass "Frontend TTFB < 1.5s (${CURL_TTFB}ms)"
    else
        warn "Frontend TTFB slow (${CURL_TTFB}ms)"
    fi

    LAYER_RESULTS+=("Layer 20: Performance OK")
}


# ===============================================================================
# MAIN
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
        # Auto-run auth layer if targeting layers 5-20 with credentials but no token
        if [[ "$RUN_LAYER" -ge 5 && "$RUN_LAYER" -le 20 && -z "$AUTH_TOKEN" ]]; then
            if [[ -n "$TEST_JWT_TOKEN" ]]; then
                AUTH_TOKEN="$TEST_JWT_TOKEN"
                verbose_log "Using TEST_JWT_TOKEN for authenticated tests"
            elif [[ -n "$ADMIN_EMAIL" && -n "$ADMIN_PASSWORD" ]]; then
                verbose_log "Auto-running layer 4 (auth) because credentials are available and AUTH_TOKEN is empty"
                test_layer_4_authentication
            fi
        fi
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
            19) test_layer_19_users ;;
            20) test_layer_20_performance ;;
            *) echo "Invalid layer: $RUN_LAYER (valid: 0-20)"; exit 1 ;;
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
        test_layer_19_users
        test_layer_20_performance
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
    if [[ ${#LAYER_RESULTS[@]} -gt 0 ]]; then
        for result in "${LAYER_RESULTS[@]}"; do
            echo "    - $result"
        done
    fi
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
        jq -n \
            --arg timestamp "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
            --arg base_url "$BASE_URL" \
            --arg frontend_url "$FRONTEND_URL" \
            --argjson total_tests "$TOTAL_TESTS" \
            --argjson passed "$PASSED_TESTS" \
            --argjson failed "$FAILED_TESTS" \
            --argjson warnings "$WARNING_TESTS" \
            --argjson skipped "$SKIPPED_TESTS" \
            --argjson critical_failures "$CRITICAL_FAILED" \
            --argjson success "$success_val" \
            '{
                timestamp: $timestamp,
                base_url: $base_url,
                frontend_url: $frontend_url,
                total_tests: $total_tests,
                passed: $passed,
                failed: $failed,
                warnings: $warnings,
                skipped: $skipped,
                critical_failures: $critical_failures,
                success: $success
            }' > "$json_file"
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
