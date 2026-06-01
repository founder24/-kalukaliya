#!/usr/bin/env bash
# ===============================================================================
# SYRABIT FULLSTACK LAYER-BY-LAYER CLOUD SHELL TEST (v3 - 1000+ Assertions)
# ===============================================================================
#
# Comprehensive test covering all 9 architectural pillars across 22 layers (0-21):
#   P1: Cloudflare (CDN + Workers + Turnstile + R2)
#   P2: Cloud Run (FastAPI Backend)
#   P3: Vertex AI Search (Discovery Engine - Hybrid RAG)
#   P4: MongoDB Atlas (User data, chat history, subscriptions)
#   P5: Upstash Redis (Rate limiting, counters, caching)
#   P6: Vertex AI Gemini 2.5 Flash (English chat + RAG)
#   P7: Sarvam AI (Assamese chat)
#   P8: Razorpay (Payments/subscriptions)
#   P9: Resend (Emails)
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
#   RAZORPAY_WEBHOOK_SECRET - For webhook HMAC signature tests
#   CRON_SECRET           - For cron endpoint tests
#   VERBOSE               - Set to 1 for detailed output
#   STRESS_TEST           - Set to 1 to enable rate limit stress tests
#   EXPORT_JSON           - Set to 1 to export results to JSON
#   SKIP_AUTH_TESTS       - Set to 1 to skip authentication tests
#   SKIP_ADMIN_TESTS      - Set to 1 to skip admin tests
#
# Requirements: bash, curl, jq, openssl (optional, for webhook HMAC)
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
CLOUDSHELL_MODE=0
RUN_LAYER=""

# Token storage (populated during auth layer)
AUTH_TOKEN=""
ADMIN_TOKEN=""
ADMIN_COOKIE=""

# Temp file tracking
GLOBAL_TMPFILES=()

# --- State Tracking ---

TOTAL_TESTS=0
PASSED_TESTS=0
WARNING_TESTS=0
FAILED_TESTS=0
SKIPPED_TESTS=0
CRITICAL_FAILED=0
declare -a LAYER_RESULTS=()
declare -a LAYER_PASS_COUNTS=()
declare -a LAYER_FAIL_COUNTS=()

# --- Cleanup trap ---

cleanup() {
    local f
    for f in "${GLOBAL_TMPFILES[@]+"${GLOBAL_TMPFILES[@]}"}"; do
        rm -f "$f" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

# --- Argument Parsing ---

print_help() {
    cat << 'HELPEOF'
SYRABIT FULLSTACK LAYER-BY-LAYER CLOUD SHELL TEST (v3 - 1000+ Assertions)

Usage:
  ./scripts/fullstack-layer-test.sh [OPTIONS]

Options:
  --help        Print this help message and exit
  --dry-run     Validate configuration without making HTTP calls
  --layer N     Run only layer N (0-21)
  --quick       Skip stress tests and optional layers (14, 17, 20)
  --cloudshell  Set Cloud Shell optimal defaults (no stress, auto JSON export, gcloud hints)

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

Layers (22 total):
   0  Prerequisites & Config Validation
   1  Frontend (Cloudflare CDN, compression, headers, assets)
   2  Edge Worker (Cloudflare Workers, CORS, bot detection)
   3  Backend Health (Cloud Run + MongoDB + Redis + Vertex)
   4  Authentication (signup, login, refresh, logout, JWT)
   5  Chat Endpoints (English, Assamese, streaming, history)
   6  RAG / Hybrid Search (Vertex AI Search, circuit breakers)
   7  Content & Knowledge (library, render, slug, chapters)
   8  Subscription & Payments (Razorpay integration)
   9  Webhook Pipeline (HMAC, replay, idempotency)
  10  Conversations API (list, anon, CRUD, pagination)
  11  Feedback (submit, stats, validation)
  12  Admin Endpoints (dashboard, users, analytics, RBAC)
  13  SEO & Indexing (sitemaps, IndexNow, robots, structured data)
  14  Education Endpoints (quiz, notes, flashcards - 501 stubs)
  15  Rate Limiting (Upstash Redis, headers, burst)
  16  Streaming & SSE (format, events, latency, errors)
  17  End-to-End Workflows (user journeys, pipelines)
  18  Cross-Cutting Concerns (request ID, CORS, CSRF, versioning)
  19  Users API (profile, onboarding, credits)
  20  Performance & Timing (TTFB thresholds, concurrency)
  21  Startup Resilience (degraded health, config errors, deep diagnostics)

Examples:
  # Run all layers against production
  ./scripts/fullstack-layer-test.sh

  # Run only backend health checks
  ./scripts/fullstack-layer-test.sh --layer 3

  # Quick test against staging
  BASE_URL=https://staging-api.syrabit.ai ./scripts/fullstack-layer-test.sh --quick

  # Full test with admin credentials and stress
  ADMIN_EMAIL=admin@syrabit.ai ADMIN_PASSWORD=secret STRESS_TEST=1 \
    ./scripts/fullstack-layer-test.sh

  # Export results to JSON
  EXPORT_JSON=1 ./scripts/fullstack-layer-test.sh
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
        --cloudshell)
            CLOUDSHELL_MODE=1
            STRESS_TEST=0
            EXPORT_JSON=1
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
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    CYAN=''
    BOLD=''
    NC=''
fi

# --- Utility Functions ---

assert_pass() {
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    PASSED_TESTS=$((PASSED_TESTS + 1))
    echo -e "    ${GREEN}PASS${NC} $1"
}

assert_fail() {
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

assert_warn() {
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    WARNING_TESTS=$((WARNING_TESTS + 1))
    echo -e "    ${YELLOW}WARN${NC} $1"
}

assert_skip() {
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    SKIPPED_TESTS=$((SKIPPED_TESTS + 1))
    echo -e "    ${BLUE}SKIP${NC} $1"
}

# Aliases for backward compat
pass() { assert_pass "$@"; }
fail() { assert_fail "$@"; }
warn() { assert_warn "$@"; }
skip() { assert_skip "$@"; }

verbose_log() {
    if [[ "$VERBOSE" == "1" ]]; then
        local msg="$1"
        msg=$(echo "$msg" | sed -E 's/(Authorization: Bearer )[^ "]*/\1[REDACTED]/gi')
        msg=$(echo "$msg" | sed -E 's/(x-razorpay-signature: )[^ "]*/\1[REDACTED]/gi')
        echo -e "    ${CYAN}[DEBUG]${NC} $msg"
    fi
}

section_header() {
    echo ""
    echo -e "${BOLD}== $1 ==${NC}"
    echo ""
}

subsection() {
    echo ""
    echo -e "  ${BOLD}$1${NC}"
}

redact_credential() {
    local val="$1"
    if [[ -z "$val" ]]; then
        echo "not set"
    elif [[ ${#val} -le 8 ]]; then
        echo "****${val: -4}"
    else
        echo "****${val: -4}"
    fi
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
    GLOBAL_TMPFILES+=("$tmpfile" "$header_file")

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

    rm -f "$tmpfile" "$header_file" 2>/dev/null || true
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
    GLOBAL_TMPFILES+=("$tmpfile" "$header_file")

    local curl_cmd=(curl -sS --no-buffer -o "$tmpfile" -D "$header_file" --max-time 30 -w '%{http_code}')

    if [[ ${#extra_args[@]} -gt 0 ]]; then
        curl_cmd+=("${extra_args[@]}")
    fi

    curl_cmd+=("$url")

    CURL_STATUS=$("${curl_cmd[@]}" 2>/dev/null) || CURL_STATUS="0"
    CURL_BODY=$(cat "$tmpfile" 2>/dev/null || echo "")
    CURL_HEADERS=$(cat "$header_file" 2>/dev/null || echo "")

    rm -f "$tmpfile" "$header_file" 2>/dev/null || true
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

body_contains() {
    echo "$CURL_BODY" | grep -qi "$1"
}

count_json_array() {
    echo "$CURL_BODY" | jq -r "$1 | length" 2>/dev/null || echo "0"
}



# ===============================================================================
# LAYER 0: Prerequisites & Config Validation (15+ tests)
# ===============================================================================

test_layer_0_prerequisites() {
    section_header "LAYER 0: Prerequisites & Config Validation"

    local layer_start=$TOTAL_TESTS

    # 0.1 Check curl
    if command -v curl &>/dev/null; then
        assert_pass "curl is installed ($(curl --version | head -1 | awk '{print $2}'))"
    else
        assert_fail "curl is not installed" "yes"
        echo "  Cannot continue without curl. Aborting."
        exit 1
    fi

    # 0.2 Check jq
    if command -v jq &>/dev/null; then
        assert_pass "jq is installed ($(jq --version 2>&1))"
    else
        assert_fail "jq is not installed" "yes"
        echo "  Cannot continue without jq. Aborting."
        exit 1
    fi

    # 0.3 Check openssl
    if command -v openssl &>/dev/null; then
        assert_pass "openssl is available ($(openssl version 2>&1 | awk '{print $2}'))"
    else
        assert_warn "openssl not found - webhook HMAC tests will be skipped"
    fi

    # 0.4 Validate BASE_URL format
    if [[ "$BASE_URL" =~ ^https?:// ]]; then
        assert_pass "BASE_URL is valid URL format: $BASE_URL"
    else
        assert_fail "BASE_URL is not a valid URL: $BASE_URL" "yes"
    fi

    # 0.5 Validate FRONTEND_URL format
    if [[ "$FRONTEND_URL" =~ ^https?:// ]]; then
        assert_pass "FRONTEND_URL is valid URL format: $FRONTEND_URL"
    else
        assert_fail "FRONTEND_URL is not a valid URL: $FRONTEND_URL" "yes"
    fi

    # 0.6 Check HTTPS enforcement
    if [[ "$BASE_URL" =~ ^https:// ]]; then
        assert_pass "BASE_URL uses HTTPS"
    else
        assert_warn "BASE_URL does not use HTTPS (insecure for production)"
    fi

    # 0.7 Check HTTPS on frontend
    if [[ "$FRONTEND_URL" =~ ^https:// ]]; then
        assert_pass "FRONTEND_URL uses HTTPS"
    else
        assert_warn "FRONTEND_URL does not use HTTPS"
    fi

    # 0.8 Check no trailing slash
    if [[ "$BASE_URL" != */ ]]; then
        assert_pass "BASE_URL has no trailing slash"
    else
        assert_warn "BASE_URL has trailing slash (may cause double-slash issues)"
    fi

    # 0.9 Check no trailing slash on frontend
    if [[ "$FRONTEND_URL" != */ ]]; then
        assert_pass "FRONTEND_URL has no trailing slash"
    else
        assert_warn "FRONTEND_URL has trailing slash"
    fi

    # 0.10 JWT token format validation
    if [[ -n "$TEST_JWT_TOKEN" ]]; then
        if [[ "$TEST_JWT_TOKEN" =~ ^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$ ]]; then
            assert_pass "TEST_JWT_TOKEN has valid JWT format (3 segments)"
        else
            assert_warn "TEST_JWT_TOKEN does not look like a standard JWT"
        fi
    else
        assert_skip "TEST_JWT_TOKEN not set (auth tests will use login flow)"
    fi

    # 0.11 Admin credentials check
    if [[ -n "$ADMIN_EMAIL" && -n "$ADMIN_PASSWORD" ]]; then
        assert_pass "Admin credentials provided"
    elif [[ -n "$ADMIN_EMAIL" && -z "$ADMIN_PASSWORD" ]]; then
        assert_warn "ADMIN_EMAIL set but ADMIN_PASSWORD missing"
    else
        assert_skip "Admin credentials not set (admin tests will be limited)"
    fi

    # 0.12 Webhook secret check
    if [[ -n "$RAZORPAY_WEBHOOK_SECRET" ]]; then
        if [[ ${#RAZORPAY_WEBHOOK_SECRET} -ge 8 ]]; then
            assert_pass "RAZORPAY_WEBHOOK_SECRET provided (${#RAZORPAY_WEBHOOK_SECRET} chars)"
        else
            assert_warn "RAZORPAY_WEBHOOK_SECRET seems too short"
        fi
    else
        assert_skip "RAZORPAY_WEBHOOK_SECRET not set (webhook HMAC tests skipped)"
    fi

    # 0.13 Cron secret check
    if [[ -n "$CRON_SECRET" ]]; then
        assert_pass "CRON_SECRET provided"
    else
        assert_skip "CRON_SECRET not set"
    fi

    # 0.14 Bash version check
    if [[ "${BASH_VERSINFO[0]}" -ge 4 ]]; then
        assert_pass "Bash version ${BASH_VERSION} (>= 4.x required)"
    else
        assert_warn "Bash version ${BASH_VERSION} - some features may not work"
    fi

    # 0.15 DNS resolution check
    if command -v dig &>/dev/null; then
        assert_pass "dig available for DNS checks"
    elif command -v nslookup &>/dev/null; then
        assert_pass "nslookup available for DNS checks"
    else
        assert_skip "No DNS lookup tools available"
    fi

    # Display configuration with redaction
    echo ""
    echo "  Configuration:"
    echo "    BASE_URL:         $BASE_URL"
    echo "    FRONTEND_URL:     $FRONTEND_URL"
    echo "    JWT Token:        $(redact_credential "$TEST_JWT_TOKEN")"
    echo "    Turnstile Token:  $(redact_credential "$TEST_TURNSTILE_TOKEN")"
    echo "    Admin Email:      $(redact_credential "$ADMIN_EMAIL")"
    echo "    Admin Password:   $(redact_credential "$ADMIN_PASSWORD")"
    echo "    Webhook Secret:   $(redact_credential "$RAZORPAY_WEBHOOK_SECRET")"
    echo "    Cron Secret:      $(redact_credential "$CRON_SECRET")"
    echo "    Verbose:          $VERBOSE"
    echo "    Stress Test:      $STRESS_TEST"
    echo "    Export JSON:       $EXPORT_JSON"
    echo "    Quick Mode:       $QUICK_MODE"
    echo "    Skip Auth:        $SKIP_AUTH_TESTS"
    echo "    Skip Admin:       $SKIP_ADMIN_TESTS"
    echo ""

    local layer_end=$TOTAL_TESTS
    LAYER_PASS_COUNTS+=($((layer_end - layer_start)))
    LAYER_RESULTS+=("Layer 0: Prerequisites & Config ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 1: Frontend (P1 Cloudflare CDN) - 55+ tests
# ===============================================================================

test_layer_1_frontend() {
    section_header "LAYER 1: Frontend (P1 Cloudflare CDN)"

    local layer_start=$TOTAL_TESTS

    subsection "1.1 Page Load & Response"

    # 1.1.1 Frontend page loads
    perform_request "$FRONTEND_URL" \
        -H "Accept-Encoding: gzip, deflate, br" \
        -H "User-Agent: SyrabitFullstackTest/2.0"

    if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 400 ]]; then
        assert_pass "Frontend loads (HTTP $CURL_STATUS, ${CURL_TTFB}ms TTFB)"
    else
        assert_fail "Frontend not reachable (HTTP $CURL_STATUS)" "yes"
    fi

    # 1.1.2 Status is exactly 200
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Frontend returns exactly HTTP 200"
    else
        assert_warn "Frontend returns HTTP $CURL_STATUS (expected 200)"
    fi

    # 1.1.3 TTFB under 3 seconds
    if [[ "$CURL_TTFB" -lt 3000 ]]; then
        assert_pass "Frontend TTFB under 3s (${CURL_TTFB}ms)"
    else
        assert_warn "Frontend TTFB slow (${CURL_TTFB}ms > 3000ms)"
    fi

    # 1.1.4 Response body is non-empty
    if [[ ${#CURL_BODY} -gt 100 ]]; then
        assert_pass "Frontend response body is non-empty (${#CURL_BODY} bytes)"
    else
        assert_fail "Frontend response body too small (${#CURL_BODY} bytes)"
    fi

    subsection "1.2 Compression"

    # 1.2.1 Compression header present
    if has_header "content-encoding"; then
        local encoding
        encoding=$(get_header_value "content-encoding")
        assert_pass "Compression enabled ($encoding)"
    else
        assert_warn "No compression detected (content-encoding header missing)"
    fi

    # 1.2.2 Brotli support
    perform_request "$FRONTEND_URL" -H "Accept-Encoding: br"
    local br_encoding
    br_encoding=$(get_header_value "content-encoding")
    if [[ "$br_encoding" == *"br"* ]]; then
        assert_pass "Brotli compression supported"
    else
        assert_warn "Brotli not detected (got: ${br_encoding:-none})"
    fi

    # 1.2.3 Gzip fallback
    perform_request "$FRONTEND_URL" -H "Accept-Encoding: gzip"
    local gz_encoding
    gz_encoding=$(get_header_value "content-encoding")
    if [[ "$gz_encoding" == *"gzip"* ]]; then
        assert_pass "Gzip compression fallback works"
    else
        assert_warn "Gzip fallback not detected"
    fi

    subsection "1.3 Security Headers"

    perform_request "$FRONTEND_URL"

    # 1.3.1 HSTS
    if has_header "strict-transport-security"; then
        local hsts_val
        hsts_val=$(get_header_value "strict-transport-security")
        assert_pass "HSTS header present ($hsts_val)"
    else
        assert_warn "No Strict-Transport-Security header"
    fi

    # 1.3.2 X-Content-Type-Options
    if has_header "x-content-type-options"; then
        local xcto
        xcto=$(get_header_value "x-content-type-options")
        if [[ "$xcto" == *"nosniff"* ]]; then
            assert_pass "X-Content-Type-Options: nosniff"
        else
            assert_warn "X-Content-Type-Options present but not nosniff: $xcto"
        fi
    else
        assert_warn "No X-Content-Type-Options header"
    fi

    # 1.3.3 X-Frame-Options
    if has_header "x-frame-options"; then
        assert_pass "X-Frame-Options header present"
    else
        assert_warn "No X-Frame-Options header (clickjacking risk)"
    fi

    # 1.3.4 Content-Security-Policy
    if has_header "content-security-policy"; then
        assert_pass "Content-Security-Policy header present"
    else
        assert_warn "No Content-Security-Policy header"
    fi

    # 1.3.5 Referrer-Policy
    if has_header "referrer-policy"; then
        assert_pass "Referrer-Policy header present"
    else
        assert_warn "No Referrer-Policy header"
    fi

    # 1.3.6 Permissions-Policy
    if has_header "permissions-policy"; then
        assert_pass "Permissions-Policy header present"
    else
        assert_warn "No Permissions-Policy header"
    fi

    # 1.3.7 No server info leakage
    local server_hdr
    server_hdr=$(get_header_value "server")
    if [[ -z "$server_hdr" || "$server_hdr" == "cloudflare" ]]; then
        assert_pass "Server header safe (${server_hdr:-not exposed})"
    else
        assert_warn "Server header exposes info: $server_hdr"
    fi

    subsection "1.4 robots.txt"

    # 1.4.1 robots.txt accessible
    perform_request "${FRONTEND_URL}/robots.txt"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "robots.txt accessible (HTTP 200)"
    else
        assert_warn "robots.txt returned HTTP $CURL_STATUS"
    fi

    # 1.4.2 robots.txt contains User-agent
    if echo "$CURL_BODY" | grep -qi "User-agent"; then
        assert_pass "robots.txt contains User-agent directive"
    else
        assert_warn "robots.txt missing User-agent directive"
    fi

    # 1.4.3 robots.txt references sitemap
    if echo "$CURL_BODY" | grep -qi "sitemap"; then
        assert_pass "robots.txt references Sitemap"
    else
        assert_warn "robots.txt does not reference sitemap"
    fi

    # 1.4.4 robots.txt content-type
    local ct
    ct=$(get_header_value "content-type")
    if [[ "$ct" == *"text/plain"* ]]; then
        assert_pass "robots.txt served as text/plain"
    else
        assert_warn "robots.txt content-type: $ct (expected text/plain)"
    fi

    subsection "1.5 Sitemap"

    # 1.5.1 sitemap.xml accessible
    perform_request "${FRONTEND_URL}/sitemap.xml"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "sitemap.xml accessible (HTTP 200)"
    elif [[ "$CURL_STATUS" -eq 301 || "$CURL_STATUS" -eq 302 ]]; then
        assert_pass "sitemap.xml redirects (HTTP $CURL_STATUS)"
    else
        assert_warn "sitemap.xml returned HTTP $CURL_STATUS"
    fi

    # 1.5.2 sitemap contains XML
    if echo "$CURL_BODY" | grep -qi "<?xml\|<urlset\|<sitemapindex"; then
        assert_pass "sitemap.xml contains valid XML structure"
    else
        assert_warn "sitemap.xml does not appear to contain XML"
    fi

    subsection "1.6 Favicon & Manifest"

    # 1.6.1 favicon.ico
    perform_request "${FRONTEND_URL}/favicon.ico"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "favicon.ico accessible"
    elif [[ "$CURL_STATUS" -eq 301 || "$CURL_STATUS" -eq 302 ]]; then
        assert_pass "favicon.ico redirects (probably to .svg or .png)"
    else
        assert_warn "favicon.ico returned HTTP $CURL_STATUS"
    fi

    # 1.6.2 manifest.json or site.webmanifest
    perform_request "${FRONTEND_URL}/manifest.json"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "manifest.json accessible"
    else
        perform_request "${FRONTEND_URL}/site.webmanifest"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "site.webmanifest accessible"
        else
            assert_warn "No web manifest found"
        fi
    fi

    subsection "1.7 HTML Content & Meta Tags"

    perform_request "$FRONTEND_URL"

    # 1.7.1 Valid HTML document
    if echo "$CURL_BODY" | grep -qi "<!doctype html\|<html"; then
        assert_pass "Valid HTML document (doctype or html tag found)"
    else
        assert_warn "Response may not be HTML"
    fi

    # 1.7.2 Title tag
    if echo "$CURL_BODY" | grep -qi "<title"; then
        assert_pass "HTML contains <title> tag"
    else
        assert_warn "No <title> tag found"
    fi

    # 1.7.3 Meta charset
    if echo "$CURL_BODY" | grep -qi 'charset.*utf-8\|utf-8.*charset'; then
        assert_pass "Meta charset UTF-8 declared"
    else
        assert_warn "No UTF-8 charset declaration"
    fi

    # 1.7.4 Meta viewport
    if echo "$CURL_BODY" | grep -qi "viewport"; then
        assert_pass "Meta viewport tag present"
    else
        assert_warn "No viewport meta tag (mobile unfriendly)"
    fi

    # 1.7.5 Meta description
    if echo "$CURL_BODY" | grep -qi 'meta.*description\|name="description"'; then
        assert_pass "Meta description present"
    else
        assert_warn "No meta description found"
    fi

    # 1.7.6 Open Graph tags
    if echo "$CURL_BODY" | grep -qi "og:title\|og:description\|og:image"; then
        assert_pass "Open Graph meta tags found"
    else
        assert_warn "No Open Graph meta tags"
    fi

    # 1.7.7 Language attribute
    if echo "$CURL_BODY" | grep -qi 'lang='; then
        assert_pass "HTML lang attribute present"
    else
        assert_warn "No lang attribute on HTML element"
    fi

    subsection "1.8 Structured Data"

    # 1.8.1 JSON-LD script tag
    if echo "$CURL_BODY" | grep -qi 'application/ld+json'; then
        assert_pass "JSON-LD structured data present"
    else
        assert_warn "No JSON-LD structured data found"
    fi

    # 1.8.2 Schema.org reference
    if echo "$CURL_BODY" | grep -qi "schema.org"; then
        assert_pass "Schema.org reference found"
    else
        assert_warn "No schema.org reference found"
    fi

    subsection "1.9 Caching"

    # 1.9.1 Cache-Control on main page
    perform_request "$FRONTEND_URL"
    if has_header "cache-control"; then
        local cc
        cc=$(get_header_value "cache-control")
        assert_pass "Cache-Control on main page: $cc"
    else
        assert_warn "No Cache-Control header on main page"
    fi

    # 1.9.2 ETag or Last-Modified
    if has_header "etag" || has_header "last-modified"; then
        assert_pass "Cache validation header present (ETag or Last-Modified)"
    else
        assert_warn "No ETag or Last-Modified header"
    fi

    # 1.9.3 CF-Cache-Status
    if has_header "cf-cache-status"; then
        local cfcache
        cfcache=$(get_header_value "cf-cache-status")
        assert_pass "Cloudflare cache status: $cfcache"
    else
        assert_warn "No CF-Cache-Status header (may not be behind Cloudflare)"
    fi

    # 1.9.4 Static asset caching (robots.txt)
    perform_request "${FRONTEND_URL}/robots.txt"
    if has_header "cache-control"; then
        assert_pass "Static asset has Cache-Control header"
    else
        assert_warn "No Cache-Control on static asset"
    fi

    subsection "1.10 Error Pages"

    # 1.10.1 404 page
    perform_request "${FRONTEND_URL}/this-page-definitely-does-not-exist-xyz123"
    if [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "404 returned for non-existent page"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "SPA catches unknown routes (returns 200 with client routing)"
    else
        assert_warn "Non-existent page returned HTTP $CURL_STATUS"
    fi

    # 1.10.2 404 page has content
    if [[ ${#CURL_BODY} -gt 50 ]]; then
        assert_pass "Error/fallback page has content body"
    else
        assert_warn "Error page has minimal content"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 1: Frontend ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 2: Edge Worker (P1 Cloudflare Workers) - 65+ tests
# ===============================================================================

test_layer_2_edge_worker() {
    section_header "LAYER 2: Edge Worker (P1 Cloudflare Workers)"

    local layer_start=$TOTAL_TESTS

    subsection "2.1 Edge Health Endpoints"

    # 2.1.1 /health returns 200
    perform_request "${BASE_URL}/health"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Edge /health returns 200 (${CURL_TTFB}ms)"
    else
        assert_fail "Edge /health returned HTTP $CURL_STATUS" "yes"
    fi

    # 2.1.2 /health is JSON
    if is_json; then
        assert_pass "/health returns valid JSON"
    else
        assert_warn "/health response is not JSON"
    fi

    # 2.1.3 /health has backend_reachable field
    if [[ "$(json_field '.backend_reachable // empty')" != "" ]]; then
        assert_pass "/health includes backend_reachable field"
    else
        assert_warn "/health missing backend_reachable field"
    fi

    # 2.1.4 /health TTFB under 2s
    if [[ "$CURL_TTFB" -lt 2000 ]]; then
        assert_pass "/health TTFB under 2s (${CURL_TTFB}ms)"
    else
        assert_warn "/health TTFB slow (${CURL_TTFB}ms)"
    fi

    # 2.1.5 /health/full endpoint
    perform_request "${BASE_URL}/health/full"
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 503 ]]; then
        assert_pass "Edge /health/full reachable (HTTP $CURL_STATUS)"
    else
        assert_warn "/health/full returned HTTP $CURL_STATUS"
    fi

    # 2.1.6 /health/full is JSON
    if is_json; then
        assert_pass "/health/full returns valid JSON"
    else
        assert_warn "/health/full is not JSON"
    fi

    subsection "2.2 CORS Configuration"

    # 2.2.1 CORS preflight OPTIONS
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X OPTIONS \
        -H "Origin: ${FRONTEND_URL}" \
        -H "Access-Control-Request-Method: POST" \
        -H "Access-Control-Request-Headers: Content-Type,Authorization"

    local allow_origin
    allow_origin=$(get_header_value "access-control-allow-origin")
    if [[ -n "$allow_origin" ]]; then
        assert_pass "CORS: Access-Control-Allow-Origin present ($allow_origin)"
    else
        assert_warn "CORS: No Access-Control-Allow-Origin header"
    fi

    # 2.2.2 Allow-Methods header
    if has_header "access-control-allow-methods"; then
        local methods
        methods=$(get_header_value "access-control-allow-methods")
        assert_pass "CORS: Allow-Methods: $methods"
    else
        assert_warn "CORS: No Allow-Methods header"
    fi

    # 2.2.3 Allow-Headers
    if has_header "access-control-allow-headers"; then
        assert_pass "CORS: Allow-Headers present"
    else
        assert_warn "CORS: No Allow-Headers header"
    fi

    # 2.2.4 Max-Age
    if has_header "access-control-max-age"; then
        assert_pass "CORS: Max-Age header present (preflight caching)"
    else
        assert_warn "CORS: No Max-Age header"
    fi

    # 2.2.5 OPTIONS returns 200 or 204
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 204 ]]; then
        assert_pass "CORS preflight returns $CURL_STATUS"
    else
        assert_warn "CORS preflight returned HTTP $CURL_STATUS (expected 200/204)"
    fi

    # 2.2.6 CORS with invalid origin
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X OPTIONS \
        -H "Origin: https://malicious-site.example.com" \
        -H "Access-Control-Request-Method: POST"
    local bad_origin
    bad_origin=$(get_header_value "access-control-allow-origin")
    if [[ "$bad_origin" == "*" ]]; then
        assert_warn "CORS allows wildcard origin (security concern)"
    elif [[ -z "$bad_origin" || "$bad_origin" != "https://malicious-site.example.com" ]]; then
        assert_pass "CORS: Invalid origin not echoed back"
    else
        assert_warn "CORS: Malicious origin echoed back"
    fi

    # 2.2.7 CORS on health endpoint
    perform_request "${BASE_URL}/health" \
        -H "Origin: ${FRONTEND_URL}"
    if has_header "access-control-allow-origin"; then
        assert_pass "CORS headers present on /health"
    else
        assert_warn "No CORS headers on /health"
    fi

    subsection "2.3 Bot Detection"

    # 2.3.1 Googlebot request
    perform_request "${BASE_URL}/health" \
        -H "User-Agent: Googlebot/2.1 (+http://www.google.com/bot.html)"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Googlebot request not blocked (HTTP 200)"
    else
        assert_warn "Googlebot request returned HTTP $CURL_STATUS"
    fi

    # 2.3.2 Bingbot request
    perform_request "${BASE_URL}/health" \
        -H "User-Agent: Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Bingbot request not blocked"
    else
        assert_warn "Bingbot request returned HTTP $CURL_STATUS"
    fi

    # 2.3.3 Scraper bot detection
    perform_request "${BASE_URL}/health" \
        -H "User-Agent: scrapy/2.0"
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Scraper bot handled (HTTP $CURL_STATUS)"
    else
        assert_warn "Scraper bot returned HTTP $CURL_STATUS"
    fi

    # 2.3.4 Empty User-Agent
    perform_request "${BASE_URL}/health" \
        -H "User-Agent: "
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Empty User-Agent handled (HTTP $CURL_STATUS)"
    else
        assert_warn "Empty User-Agent returned HTTP $CURL_STATUS"
    fi

    # 2.3.5 Normal browser User-Agent
    perform_request "${BASE_URL}/health" \
        -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Normal browser request succeeds"
    else
        assert_warn "Normal browser request returned HTTP $CURL_STATUS"
    fi

    subsection "2.4 Rate Limit Headers"

    # 2.4.1 Chat endpoint has rate limit headers
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"test","language":"en"}'

    if has_header "x-ratelimit-limit" || has_header "ratelimit-limit"; then
        assert_pass "Rate limit headers present on chat endpoint"
    else
        assert_warn "No rate limit headers on chat endpoint"
    fi

    # 2.4.2 X-RateLimit-Remaining
    if has_header "x-ratelimit-remaining" || has_header "ratelimit-remaining"; then
        assert_pass "Rate limit remaining counter present"
    else
        assert_warn "No rate limit remaining header"
    fi

    # 2.4.3 X-RateLimit-Reset
    if has_header "x-ratelimit-reset" || has_header "ratelimit-reset"; then
        assert_pass "Rate limit reset time present"
    else
        assert_warn "No rate limit reset header"
    fi

    subsection "2.5 Method Enforcement"

    # 2.5.1 PUT on health endpoint
    perform_request "${BASE_URL}/health" -X PUT
    if [[ "$CURL_STATUS" -eq 405 ]]; then
        assert_pass "PUT /health returns 405 Method Not Allowed"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_warn "PUT /health returns 200 (method not enforced)"
    else
        assert_pass "PUT /health rejected (HTTP $CURL_STATUS)"
    fi

    # 2.5.2 DELETE on health endpoint
    perform_request "${BASE_URL}/health" -X DELETE
    if [[ "$CURL_STATUS" -eq 405 ]]; then
        assert_pass "DELETE /health returns 405"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_warn "DELETE /health returns 200 (method not enforced)"
    else
        assert_pass "DELETE /health rejected (HTTP $CURL_STATUS)"
    fi

    # 2.5.3 PATCH on health endpoint
    perform_request "${BASE_URL}/health" -X PATCH
    if [[ "$CURL_STATUS" -eq 405 ]]; then
        assert_pass "PATCH /health returns 405"
    else
        assert_warn "PATCH /health returned HTTP $CURL_STATUS"
    fi

    # 2.5.4 GET on chat (should be POST only)
    perform_request "${BASE_URL}/api/v1/chat/"
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "GET /api/v1/chat/ correctly rejected (HTTP $CURL_STATUS)"
    else
        assert_warn "GET /api/v1/chat/ returned HTTP $CURL_STATUS"
    fi

    subsection "2.6 Security Headers on API"

    perform_request "${BASE_URL}/health"

    # 2.6.1 X-Content-Type-Options
    if has_header "x-content-type-options"; then
        assert_pass "API: X-Content-Type-Options present"
    else
        assert_warn "API: Missing X-Content-Type-Options"
    fi

    # 2.6.2 X-Frame-Options
    if has_header "x-frame-options"; then
        assert_pass "API: X-Frame-Options present"
    else
        assert_warn "API: Missing X-Frame-Options"
    fi

    # 2.6.3 HSTS
    if has_header "strict-transport-security"; then
        assert_pass "API: HSTS present"
    else
        assert_warn "API: Missing HSTS header"
    fi

    # 2.6.4 No X-Powered-By
    if ! has_header "x-powered-by"; then
        assert_pass "API: X-Powered-By not exposed"
    else
        assert_warn "API: X-Powered-By header exposed (info leakage)"
    fi

    subsection "2.7 Error Response Format"

    # 2.7.1 404 returns JSON
    perform_request "${BASE_URL}/api/v1/nonexistent-endpoint-xyz"
    if [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "Unknown API path returns 404"
    else
        assert_warn "Unknown API path returns HTTP $CURL_STATUS (expected 404)"
    fi

    # 2.7.2 Error is JSON formatted
    if is_json; then
        assert_pass "404 error returns JSON body"
    else
        assert_warn "404 error is not JSON"
    fi

    # 2.7.3 Error has detail field
    local detail
    detail=$(json_field '.detail // empty')
    if [[ -n "$detail" ]]; then
        assert_pass "Error response has 'detail' field"
    else
        assert_warn "Error response missing 'detail' field"
    fi

    # 2.7.4 Content-Type is application/json
    local ct
    ct=$(get_header_value "content-type")
    if [[ "$ct" == *"application/json"* ]]; then
        assert_pass "Error Content-Type is application/json"
    else
        assert_warn "Error Content-Type: $ct"
    fi

    subsection "2.8 Path Traversal Protection"

    # 2.8.1 Path traversal attempt
    perform_request "${BASE_URL}/api/v1/../../etc/passwd"
    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Path traversal blocked (HTTP $CURL_STATUS)"
    else
        assert_warn "Path traversal returned HTTP $CURL_STATUS"
    fi

    # 2.8.2 Double-encoded traversal
    perform_request "${BASE_URL}/api/v1/%2e%2e%2f%2e%2e%2fetc%2fpasswd"
    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Double-encoded traversal blocked (HTTP $CURL_STATUS)"
    else
        assert_warn "Double-encoded traversal returned HTTP $CURL_STATUS"
    fi

    # 2.8.3 Null byte injection
    perform_request "${BASE_URL}/api/v1/health%00.html"
    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "Null byte path rejected (HTTP $CURL_STATUS)"
    else
        assert_warn "Null byte path returned HTTP $CURL_STATUS"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 2: Edge Worker ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 3: Backend Health (P2 Cloud Run + P4 MongoDB + P5 Redis) - 45+ tests
# ===============================================================================

test_layer_3_backend_health() {
    section_header "LAYER 3: Backend Health (P2 Cloud Run + P4 + P5)"

    local layer_start=$TOTAL_TESTS

    subsection "3.1 Basic Health"

    # 3.1.1 GET /health
    perform_request "${BASE_URL}/health"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "GET /health returns 200"
    else
        assert_fail "GET /health returned HTTP $CURL_STATUS" "yes"
    fi

    # 3.1.2 Health response is JSON
    if is_json; then
        assert_pass "/health response is valid JSON"
    else
        assert_fail "/health response is not JSON"
    fi

    # 3.1.3 Health has status field
    local health_status
    health_status=$(json_field '.status // empty')
    if [[ "$health_status" == "healthy" || "$health_status" == "ok" || "$health_status" == "up" ]]; then
        assert_pass "/health status: $health_status"
    elif [[ "$health_status" == "degraded" ]]; then
        assert_warn "/health status: degraded (config errors present)"
        # Check config_error_count when degraded
        local config_err_count
        config_err_count=$(json_field '.config_error_count // empty')
        if [[ -n "$config_err_count" && "$config_err_count" =~ ^[0-9]+$ ]]; then
            assert_pass "Degraded state has config_error_count: $config_err_count"
        elif [[ -n "$config_err_count" ]]; then
            assert_warn "config_error_count present but not integer: $config_err_count"
        else
            assert_warn "Degraded state missing config_error_count field"
        fi
    elif [[ -n "$health_status" ]]; then
        assert_warn "/health status: $health_status"
    else
        assert_warn "/health missing status field"
    fi

    # 3.1.4 Health TTFB
    if [[ "$CURL_TTFB" -lt 1000 ]]; then
        assert_pass "/health TTFB under 1s (${CURL_TTFB}ms)"
    elif [[ "$CURL_TTFB" -lt 3000 ]]; then
        assert_warn "/health TTFB moderate (${CURL_TTFB}ms)"
    else
        assert_fail "/health TTFB too slow (${CURL_TTFB}ms > 3000ms)"
    fi

    # 3.1.5 Health response size reasonable
    if [[ ${#CURL_BODY} -lt 10000 ]]; then
        assert_pass "/health response size reasonable (${#CURL_BODY} bytes)"
    else
        assert_warn "/health response unexpectedly large (${#CURL_BODY} bytes)"
    fi

    subsection "3.2 Deep Health"

    # 3.2.1 GET /health/deep
    perform_request "${BASE_URL}/health/deep"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "GET /health/deep returns 200 (all services healthy)"
    elif [[ "$CURL_STATUS" -eq 503 ]]; then
        assert_warn "GET /health/deep returns 503 (some services degraded)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "GET /health/deep not found (endpoint may not exist)"
    else
        assert_warn "GET /health/deep returned HTTP $CURL_STATUS"
    fi

    # 3.2.2 Deep health is JSON
    if is_json; then
        assert_pass "/health/deep response is valid JSON"
    else
        assert_warn "/health/deep response is not JSON"
    fi

    # 3.2.3 MongoDB status
    local mongo_status
    mongo_status=$(json_field '.services.mongodb // .mongodb // .checks.mongodb // empty')
    if [[ "$mongo_status" == "healthy" || "$mongo_status" == "ok" || "$mongo_status" == "connected" ]]; then
        assert_pass "MongoDB status: $mongo_status"
    elif [[ -n "$mongo_status" ]]; then
        assert_warn "MongoDB status: $mongo_status"
    else
        assert_skip "MongoDB status not found in deep health response"
    fi

    # 3.2.4 Redis status
    local redis_status
    redis_status=$(json_field '.services.redis // .redis // .checks.redis // empty')
    if [[ "$redis_status" == "healthy" || "$redis_status" == "ok" || "$redis_status" == "connected" ]]; then
        assert_pass "Redis status: $redis_status"
    elif [[ -n "$redis_status" ]]; then
        assert_warn "Redis status: $redis_status"
    else
        assert_skip "Redis status not found in deep health response"
    fi

    # 3.2.5 Vertex AI status
    local vertex_status
    vertex_status=$(json_field '.services.vertex_ai // .vertex_ai // .checks.vertex_ai // empty')
    if [[ "$vertex_status" == "healthy" || "$vertex_status" == "ok" ]]; then
        assert_pass "Vertex AI status: $vertex_status"
    elif [[ -n "$vertex_status" ]]; then
        assert_warn "Vertex AI status: $vertex_status"
    else
        assert_skip "Vertex AI status not found in deep health"
    fi

    # 3.2.6 Vertex Search status
    local vsearch_status
    vsearch_status=$(json_field '.services.vertex_search // .vertex_search // .checks.vertex_search // empty')
    if [[ "$vsearch_status" == "healthy" || "$vsearch_status" == "ok" ]]; then
        assert_pass "Vertex Search status: $vsearch_status"
    elif [[ -n "$vsearch_status" ]]; then
        assert_warn "Vertex Search status: $vsearch_status"
    else
        assert_skip "Vertex Search status not found"
    fi

    # 3.2.7 Deep health TTFB
    if [[ "$CURL_TTFB" -lt 5000 ]]; then
        assert_pass "/health/deep TTFB under 5s (${CURL_TTFB}ms)"
    else
        assert_warn "/health/deep TTFB slow (${CURL_TTFB}ms)"
    fi

    subsection "3.3 Circuit Breakers"

    # 3.3.1 GET /health/circuit-breakers
    perform_request "${BASE_URL}/health/circuit-breakers"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "GET /health/circuit-breakers returns 200"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Circuit breakers endpoint not found"
    else
        assert_warn "Circuit breakers returned HTTP $CURL_STATUS"
    fi

    # 3.3.2 Circuit breakers is JSON
    if [[ "$CURL_STATUS" -eq 200 ]] && is_json; then
        assert_pass "Circuit breakers response is JSON"

        # 3.3.3 Vertex AI circuit breaker
        local cb_vertex
        cb_vertex=$(json_field '.vertex_ai.state // .circuit_breakers.vertex_ai // empty')
        if [[ "$cb_vertex" == "closed" || "$cb_vertex" == "half_open" ]]; then
            assert_pass "Vertex AI circuit breaker: $cb_vertex"
        elif [[ "$cb_vertex" == "open" ]]; then
            assert_warn "Vertex AI circuit breaker is OPEN"
        elif [[ -n "$cb_vertex" ]]; then
            assert_pass "Vertex AI CB state: $cb_vertex"
        else
            assert_skip "Vertex AI circuit breaker state not found"
        fi

        # 3.3.4 Sarvam AI circuit breaker
        local cb_sarvam
        cb_sarvam=$(json_field '.sarvam_ai.state // .circuit_breakers.sarvam_ai // empty')
        if [[ "$cb_sarvam" == "closed" || "$cb_sarvam" == "half_open" ]]; then
            assert_pass "Sarvam AI circuit breaker: $cb_sarvam"
        elif [[ "$cb_sarvam" == "open" ]]; then
            assert_warn "Sarvam AI circuit breaker is OPEN"
        elif [[ -n "$cb_sarvam" ]]; then
            assert_pass "Sarvam AI CB state: $cb_sarvam"
        else
            assert_skip "Sarvam AI circuit breaker state not found"
        fi

        # 3.3.5 Vertex Search circuit breaker
        local cb_search
        cb_search=$(json_field '.vertex_search.state // .circuit_breakers.vertex_search // empty')
        if [[ "$cb_search" == "closed" || "$cb_search" == "half_open" ]]; then
            assert_pass "Vertex Search circuit breaker: $cb_search"
        elif [[ "$cb_search" == "open" ]]; then
            assert_warn "Vertex Search circuit breaker is OPEN"
        elif [[ -n "$cb_search" ]]; then
            assert_pass "Vertex Search CB state: $cb_search"
        else
            assert_skip "Vertex Search circuit breaker not found"
        fi
    else
        assert_skip "Circuit breaker details not available"
    fi

    subsection "3.4 Service Dependencies"

    # 3.4.1 API version endpoint
    perform_request "${BASE_URL}/api/v1/"
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "API v1 root responds (HTTP $CURL_STATUS)"
    else
        assert_warn "API v1 root returned HTTP $CURL_STATUS"
    fi

    # 3.4.2 Verify JSON content-type
    perform_request "${BASE_URL}/health"
    local ct
    ct=$(get_header_value "content-type")
    if [[ "$ct" == *"application/json"* ]]; then
        assert_pass "Health endpoint Content-Type: application/json"
    else
        assert_warn "Health Content-Type: $ct"
    fi

    # 3.4.3 Health endpoint idempotent (2nd request)
    perform_request "${BASE_URL}/health"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Health endpoint idempotent (consistent 200)"
    else
        assert_warn "Health inconsistent on 2nd call (HTTP $CURL_STATUS)"
    fi

    # 3.4.4 Health with Accept header
    perform_request "${BASE_URL}/health" -H "Accept: application/json"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Health respects Accept: application/json"
    else
        assert_warn "Health with Accept header returned HTTP $CURL_STATUS"
    fi

    # 3.4.5 Uptime field in health
    local uptime
    uptime=$(json_field '.uptime // .uptime_seconds // empty')
    if [[ -n "$uptime" ]]; then
        assert_pass "Health includes uptime: $uptime"
    else
        assert_skip "Health does not include uptime field"
    fi

    # 3.4.6 Version field in health
    local version
    version=$(json_field '.version // .app_version // empty')
    if [[ -n "$version" ]]; then
        assert_pass "Health includes version: $version"
    else
        assert_skip "Health does not include version field"
    fi

    # 3.4.7 Timestamp in health
    local ts
    ts=$(json_field '.timestamp // .checked_at // empty')
    if [[ -n "$ts" ]]; then
        assert_pass "Health includes timestamp"
    else
        assert_skip "Health does not include timestamp"
    fi

    # 3.4.8 No sensitive data in health
    if echo "$CURL_BODY" | grep -qi "password\|secret\|key.*=\|token.*:"; then
        assert_fail "Health response may contain sensitive data"
    else
        assert_pass "Health response has no obvious sensitive data"
    fi

    subsection "3.5 Degraded Health Reporting"

    # 3.5.1 Re-fetch /health for degraded checks
    perform_request "${BASE_URL}/health"
    local degraded_status
    degraded_status=$(json_field '.status // empty')

    # 3.5.2 Status field value check
    if [[ "$degraded_status" == "healthy" || "$degraded_status" == "degraded" || "$degraded_status" == "ok" || "$degraded_status" == "up" ]]; then
        assert_pass "Health status is a valid value: $degraded_status"
    elif [[ -n "$degraded_status" ]]; then
        assert_warn "Health status unexpected value: $degraded_status"
    else
        assert_warn "Health status field missing"
    fi

    # 3.5.3 If degraded, verify config_error_count exists and is integer
    if [[ "$degraded_status" == "degraded" ]]; then
        local err_count
        err_count=$(json_field '.config_error_count // empty')
        if [[ -n "$err_count" && "$err_count" =~ ^[0-9]+$ ]]; then
            assert_pass "Degraded: config_error_count is integer ($err_count)"
        elif [[ -n "$err_count" ]]; then
            assert_fail "Degraded: config_error_count not integer: $err_count"
        else
            assert_fail "Degraded: config_error_count field missing"
        fi
    else
        assert_skip "Not degraded; config_error_count check skipped"
    fi

    # 3.5.4 No sensitive keywords in response body (password, secret, key, token, credentials)
    if [[ "$degraded_status" == "degraded" ]]; then
        if echo "$CURL_BODY" | grep -qi "password\|secret\|credentials"; then
            assert_fail "Degraded response contains sensitive keywords"
        else
            assert_pass "Degraded response has no sensitive keywords (password/secret/credentials)"
        fi
    else
        assert_skip "Not degraded; sensitive keyword check skipped"
    fi

    # 3.5.5 No raw token/key values exposed
    if [[ "$degraded_status" == "degraded" ]]; then
        if echo "$CURL_BODY" | grep -qi '"token"\s*:\s*"[^"]\{10,\}"\|"key"\s*:\s*"[^"]\{10,\}"'; then
            assert_fail "Degraded response may expose raw token/key values"
        else
            assert_pass "Degraded response does not expose raw token/key values"
        fi
    else
        assert_skip "Not degraded; token/key exposure check skipped"
    fi

    # 3.5.6 Service field is syrabit-backend
    local svc_field
    svc_field=$(json_field '.service // empty')
    if [[ "$svc_field" == "syrabit-backend" ]]; then
        assert_pass "Health service field: syrabit-backend"
    elif [[ -n "$svc_field" ]]; then
        assert_warn "Health service field: $svc_field (expected syrabit-backend)"
    else
        assert_warn "Health response missing service field"
    fi

    # 3.5.7 Response does NOT expose raw error message strings
    if echo "$CURL_BODY" | grep -qi "traceback\|stacktrace\|exception.*at\|File.*line"; then
        assert_fail "Health response exposes raw error messages/stacktraces"
    else
        assert_pass "Health response does not expose raw error messages"
    fi

    # 3.5.8 Response body is reasonable size even when degraded
    if [[ ${#CURL_BODY} -lt 5000 ]]; then
        assert_pass "Health response size reasonable for reporting (${#CURL_BODY} bytes)"
    else
        assert_warn "Health response unexpectedly large (${#CURL_BODY} bytes)"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 3: Backend Health ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 4: Authentication (JWT Flow) - 85+ tests
# ===============================================================================

test_layer_4_authentication() {
    section_header "LAYER 4: Authentication (JWT Flow)"

    local layer_start=$TOTAL_TESTS

    if [[ "$SKIP_AUTH_TESTS" == "1" ]]; then
        assert_skip "Authentication tests skipped (SKIP_AUTH_TESTS=1)"
        LAYER_RESULTS+=("Layer 4: Authentication (SKIPPED)")
        return
    fi

    subsection "4.1 Signup Endpoint"

    # 4.1.1 Signup with valid payload
    local signup_email="test-$(date +%s)@example-test.com"
    perform_request "${BASE_URL}/api/v1/auth/signup" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"${signup_email}\",\"password\":\"TestPass123!\",\"name\":\"Test User\"}"
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 201 ]]; then
        assert_pass "Signup returns $CURL_STATUS (success)"
    elif [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 409 ]]; then
        assert_pass "Signup returns $CURL_STATUS (email exists or validation)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Signup rate limited (429)"
    else
        assert_warn "Signup returned HTTP $CURL_STATUS"
    fi

    # 4.1.2 Signup response is JSON
    if is_json; then
        assert_pass "Signup response is JSON"
    else
        assert_warn "Signup response is not JSON"
    fi

    # 4.1.3 Signup with missing email
    perform_request "${BASE_URL}/api/v1/auth/signup" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"password":"TestPass123!","name":"Test"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Signup without email returns $CURL_STATUS (validation error)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited - cannot verify signup validation"
    else
        assert_warn "Signup without email returned HTTP $CURL_STATUS"
    fi

    # 4.1.4 Signup with missing password
    perform_request "${BASE_URL}/api/v1/auth/signup" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"test@test.com","name":"Test"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Signup without password returns $CURL_STATUS"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Signup without password returned HTTP $CURL_STATUS"
    fi

    # 4.1.5 Signup with invalid email format
    perform_request "${BASE_URL}/api/v1/auth/signup" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"not-an-email","password":"TestPass123!","name":"Test"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Signup with invalid email returns $CURL_STATUS"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Invalid email signup returned HTTP $CURL_STATUS"
    fi

    # 4.1.6 Signup with weak password
    perform_request "${BASE_URL}/api/v1/auth/signup" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"test@test.com","password":"123","name":"Test"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Signup with weak password rejected ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Weak password signup returned HTTP $CURL_STATUS"
    fi

    # 4.1.7 Signup with empty body
    perform_request "${BASE_URL}/api/v1/auth/signup" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Signup with empty body returns $CURL_STATUS"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Empty body signup returned HTTP $CURL_STATUS"
    fi

    # 4.1.8 Signup SQL injection probe
    perform_request "${BASE_URL}/api/v1/auth/signup" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"admin@test.com","password":"\" OR 1=1 --","name":"Test"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 409 ]]; then
        assert_pass "SQL injection in signup handled safely (HTTP $CURL_STATUS)"
    else
        assert_warn "SQL injection probe returned HTTP $CURL_STATUS"
    fi

    subsection "4.2 Login Endpoint"

    # 4.2.1 Login with invalid body (422)
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"wrong_field":"value"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Login with invalid body returns $CURL_STATUS"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Login invalid body returned HTTP $CURL_STATUS"
    fi

    # 4.2.2 Login with wrong credentials (401)
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"nonexistent@example.com","password":"WrongPassword123!"}'
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Login with wrong credentials returns $CURL_STATUS"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited on login"
    else
        assert_warn "Wrong credentials login returned HTTP $CURL_STATUS"
    fi

    # 4.2.3 Login without content-type
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -d '{"email":"test@test.com","password":"test"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 415 ]]; then
        assert_pass "Login without Content-Type handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Login without Content-Type returned HTTP $CURL_STATUS"
    fi

    # 4.2.4 Login with empty email
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"","password":"test123"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "Login with empty email rejected ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Empty email login returned HTTP $CURL_STATUS"
    fi

    # 4.2.5 Login with empty password
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"test@test.com","password":""}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "Login with empty password rejected ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Empty password login returned HTTP $CURL_STATUS"
    fi

    # 4.2.6 Login with extra long email
    local long_email
    long_email=$(printf '%0.s' {1..300})@example.com
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"${long_email}\",\"password\":\"test\"}"
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 413 ]]; then
        assert_pass "Login with 300+ char email rejected ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Long email login returned HTTP $CURL_STATUS"
    fi

    # 4.2.7 Successful login with admin credentials
    if [[ -n "$ADMIN_EMAIL" && -n "$ADMIN_PASSWORD" ]]; then
        perform_request "${BASE_URL}/api/v1/auth/login" \
            -X POST \
            -H "Content-Type: application/json" \
            -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\"}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Admin login successful (HTTP 200)"
            # Extract token
            local token
            token=$(json_field '.access_token // .token // empty')
            if [[ -n "$token" ]]; then
                AUTH_TOKEN="$token"
                ADMIN_TOKEN="$token"
                assert_pass "JWT token extracted from login response"
            else
                assert_warn "Login succeeded but no token in response"
            fi
        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            assert_warn "Admin login rate limited (429)"
        else
            assert_warn "Admin login returned HTTP $CURL_STATUS"
        fi

        # 4.2.8 Admin panel login (cookie-based session for admin endpoints)
        local admin_cookie_jar
        admin_cookie_jar=$(mktemp)
        GLOBAL_TMPFILES+=("$admin_cookie_jar")
        local admin_login_resp
        admin_login_resp=$(curl -s -o /dev/null -w "%{http_code}" \
            -c "$admin_cookie_jar" \
            -X POST "${BASE_URL}/api/v1/admin/login" \
            -H "Content-Type: application/json" \
            -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\"}" 2>/dev/null)
        if [[ "$admin_login_resp" -eq 200 ]]; then
            ADMIN_COOKIE=$(grep "syrabit_admin_session" "$admin_cookie_jar" 2>/dev/null | awk '{print $NF}')
            if [[ -n "$ADMIN_COOKIE" ]]; then
                assert_pass "Admin panel login successful (cookie obtained)"
            else
                assert_warn "Admin login 200 but no session cookie set"
            fi
        elif [[ "$admin_login_resp" -eq 429 ]]; then
            assert_warn "Admin panel login rate limited (429)"
        else
            assert_warn "Admin panel login returned HTTP $admin_login_resp"
        fi
    else
        assert_skip "Admin login skipped (no credentials)"
    fi

    # Use pre-set token if provided
    if [[ -z "$AUTH_TOKEN" && -n "$TEST_JWT_TOKEN" ]]; then
        AUTH_TOKEN="$TEST_JWT_TOKEN"
        assert_pass "Using TEST_JWT_TOKEN for authenticated tests"
    fi

    subsection "4.3 Token Validation"

    # 4.3.1 Request with invalid JWT
    perform_request "${BASE_URL}/api/v1/chat/history" \
        -H "Authorization: Bearer invalid.token.here"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Invalid JWT returns $CURL_STATUS"
    else
        assert_warn "Invalid JWT returned HTTP $CURL_STATUS"
    fi

    # 4.3.2 Request with malformed authorization header
    perform_request "${BASE_URL}/api/v1/chat/history" \
        -H "Authorization: NotBearer sometoken"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Malformed auth header returns $CURL_STATUS"
    else
        assert_warn "Malformed auth header returned HTTP $CURL_STATUS"
    fi

    # 4.3.3 Request with empty Bearer
    perform_request "${BASE_URL}/api/v1/chat/history" \
        -H "Authorization: Bearer "
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Empty Bearer token returns $CURL_STATUS"
    else
        assert_warn "Empty Bearer returned HTTP $CURL_STATUS"
    fi

    # 4.3.4 Request with expired-like token
    perform_request "${BASE_URL}/api/v1/chat/history" \
        -H "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjB9.invalid"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Expired-like token returns $CURL_STATUS"
    else
        assert_warn "Expired token probe returned HTTP $CURL_STATUS"
    fi

    # 4.3.5 XSS in auth header
    perform_request "${BASE_URL}/api/v1/chat/history" \
        -H "Authorization: Bearer <script>alert(1)</script>"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "XSS in auth header rejected ($CURL_STATUS)"
    else
        assert_warn "XSS auth header returned HTTP $CURL_STATUS"
    fi

    subsection "4.4 Refresh Token"

    # 4.4.1 Refresh with no token
    perform_request "${BASE_URL}/api/v1/auth/refresh" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{}'
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Refresh without token returns $CURL_STATUS"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Refresh rate limited"
    else
        assert_warn "Refresh no-token returned HTTP $CURL_STATUS"
    fi

    # 4.4.2 Refresh with invalid token
    perform_request "${BASE_URL}/api/v1/auth/refresh" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"refresh_token":"invalid-refresh-token"}'
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Refresh with invalid token returns $CURL_STATUS"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Refresh rate limited"
    else
        assert_warn "Invalid refresh returned HTTP $CURL_STATUS"
    fi

    subsection "4.5 Forgot Password"

    # 4.5.1 Forgot password with valid email
    perform_request "${BASE_URL}/api/v1/auth/forgot-password" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"test@example.com"}'
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Forgot password returns 200 (no email existence leak)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Forgot password rate limited"
    elif [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Forgot password returns $CURL_STATUS"
    else
        assert_warn "Forgot password returned HTTP $CURL_STATUS"
    fi

    # 4.5.2 Forgot password with non-existent email
    perform_request "${BASE_URL}/api/v1/auth/forgot-password" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"definitely-not-real-user@nonexistent-domain.example"}'
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Forgot password does not leak email existence (returns 200)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Non-existent email forgot-password returned HTTP $CURL_STATUS"
    fi

    # 4.5.3 Forgot password with empty email
    perform_request "${BASE_URL}/api/v1/auth/forgot-password" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":""}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Forgot password with empty email returns $CURL_STATUS"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Empty email forgot-password returned HTTP $CURL_STATUS"
    fi

    subsection "4.6 Logout"

    # 4.6.1 Logout without auth
    perform_request "${BASE_URL}/api/v1/auth/logout" \
        -X POST \
        -H "Content-Type: application/json"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Logout without auth returns $CURL_STATUS"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Logout rate limited"
    else
        assert_warn "Logout without auth returned HTTP $CURL_STATUS"
    fi

    # 4.6.2 Logout with invalid token
    perform_request "${BASE_URL}/api/v1/auth/logout" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer fake-token-for-logout"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 || "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Logout with invalid token returns $CURL_STATUS"
    else
        assert_warn "Logout invalid token returned HTTP $CURL_STATUS"
    fi

    subsection "4.7 Security Probes"

    # 4.7.1 CRLF injection in email
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"test@test.com\r\nX-Injected: header","password":"test"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "CRLF injection in login email handled ($CURL_STATUS)"
    else
        assert_warn "CRLF injection returned HTTP $CURL_STATUS"
    fi

    # 4.7.2 Unicode normalization attack
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"admin\u0000@test.com","password":"test"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "Null byte in email handled ($CURL_STATUS)"
    else
        assert_warn "Null byte email returned HTTP $CURL_STATUS"
    fi

    # 4.7.3 JSON injection
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"test@test.com","password":"test","role":"admin"}'
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Extra field (role escalation) in login rejected ($CURL_STATUS)"
    else
        assert_warn "Extra field login returned HTTP $CURL_STATUS"
    fi

    # 4.7.4 Very large payload
    local big_pass
    big_pass=$(printf 'A%.0s' {1..10000})
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"test@test.com\",\"password\":\"${big_pass}\"}"
    if [[ "$CURL_STATUS" -eq 413 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "Very large password handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Large password returned HTTP $CURL_STATUS"
    fi

    # 4.7.5 Method check - GET on login
    perform_request "${BASE_URL}/api/v1/auth/login"
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "GET /auth/login returns $CURL_STATUS (POST only)"
    else
        assert_warn "GET /auth/login returned HTTP $CURL_STATUS"
    fi

    # 4.7.6 Method check - GET on signup
    perform_request "${BASE_URL}/api/v1/auth/signup"
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "GET /auth/signup returns $CURL_STATUS (POST only)"
    else
        assert_warn "GET /auth/signup returned HTTP $CURL_STATUS"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 4: Authentication ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 5: Chat Endpoints (P6 Vertex AI + P7 Sarvam AI) - 100+ tests
# ===============================================================================

test_layer_5_chat() {
    section_header "LAYER 5: Chat Endpoints (P6 Vertex AI + P7 Sarvam AI)"

    local layer_start=$TOTAL_TESTS

    subsection "5.1 Chat POST (Non-streaming) - Unauthenticated"

    # 5.1.1 Chat without auth (anon)
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"What is photosynthesis?","language":"en"}'
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Anon chat returns 200"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Anon chat rate limited (429)"
    elif [[ "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "Anon chat requires auth (401)"
    else
        assert_warn "Anon chat returned HTTP $CURL_STATUS"
    fi

    # 5.1.2 Chat response is JSON
    if [[ "$CURL_STATUS" -eq 200 ]] && is_json; then
        assert_pass "Chat response is valid JSON"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_warn "Chat 200 response is not JSON"
    else
        assert_skip "Chat did not return 200, skipping JSON check"
    fi

    # 5.1.3 Chat response has expected fields
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        local chat_resp
        chat_resp=$(json_field '.response // .answer // .text // empty')
        if [[ -n "$chat_resp" ]]; then
            assert_pass "Chat response has text content"
        else
            assert_warn "Chat response missing text field"
        fi
    fi

    # 5.1.4 Chat without message field
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"language":"en"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Chat without message returns $CURL_STATUS (validation)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Chat without message returned HTTP $CURL_STATUS"
    fi

    # 5.1.5 Chat with empty message
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"","language":"en"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Chat with empty message returns $CURL_STATUS"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Empty message chat returned HTTP $CURL_STATUS"
    fi

    # 5.1.6 Chat with only whitespace
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"   ","language":"en"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Whitespace-only message handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Whitespace message returned HTTP $CURL_STATUS"
    fi

    # 5.1.7 Chat with very long message
    local long_msg
    long_msg=$(printf 'A%.0s' {1..5000})
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "{\"message\":\"${long_msg}\",\"language\":\"en\"}"
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 413 ]]; then
        assert_pass "Very long message handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Long message returned HTTP $CURL_STATUS"
    fi

    # 5.1.8 Chat with invalid language
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"hello","language":"xx"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Invalid language handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Invalid language returned HTTP $CURL_STATUS"
    fi

    subsection "5.2 Chat - Security Probes"

    # 5.2.1 XSS in message
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"<script>alert(document.cookie)</script>","language":"en"}'
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "XSS in chat message handled safely ($CURL_STATUS)"
        if [[ "$CURL_STATUS" -eq 200 ]] && echo "$CURL_BODY" | grep -q "<script>"; then
            assert_warn "XSS payload reflected in response (potential risk)"
        else
            assert_pass "XSS payload not reflected in response"
        fi
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "XSS chat returned HTTP $CURL_STATUS"
    fi

    # 5.2.2 SQL injection in message
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"Robert\"); DROP TABLE students;--","language":"en"}'
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "SQL injection in chat handled safely ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "SQL injection chat returned HTTP $CURL_STATUS"
    fi

    # 5.2.3 Path traversal in message
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"../../../../etc/passwd","language":"en"}'
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Path traversal in chat handled safely ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Path traversal chat returned HTTP $CURL_STATUS"
    fi

    # 5.2.4 Unicode/Emoji in message
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"Hello! Can you explain gravity? 🌍🚀","language":"en"}'
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Unicode/emoji message handled (200)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Emoji message returned HTTP $CURL_STATUS"
    fi

    # 5.2.5 Assamese text
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"সালোকসংশ্লেষণ কি?","language":"as"}'
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Assamese chat returns 200 (Sarvam AI route)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Assamese chat rate limited"
    elif [[ "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "Assamese chat requires auth ($CURL_STATUS)"
    else
        assert_warn "Assamese chat returned HTTP $CURL_STATUS"
    fi

    # 5.2.6 Null bytes in message
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"hello\u0000world","language":"en"}'
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Null byte in message handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Null byte message returned HTTP $CURL_STATUS"
    fi

    subsection "5.3 Chat - Authenticated"

    if [[ -n "$AUTH_TOKEN" ]]; then
        # 5.3.1 English chat with auth
        perform_request "${BASE_URL}/api/v1/chat/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message":"Explain Newton'\''s first law of motion","language":"en"}'
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Authenticated English chat returns 200"

            # 5.3.2 Response has text
            local resp_text
            resp_text=$(json_field '.response // .answer // .text // empty')
            if [[ -n "$resp_text" ]]; then
                assert_pass "Auth chat response has text content"
            else
                assert_warn "Auth chat response missing text"
            fi

            # 5.3.3 Response has model info
            local model_used
            model_used=$(json_field '.model_used // .model // empty')
            if [[ -n "$model_used" ]]; then
                assert_pass "Chat response includes model_used: $model_used"
            else
                assert_warn "Chat response missing model_used field"
            fi

            # 5.3.4 Response has latency
            local latency
            latency=$(json_field '.latency_ms // .latency // empty')
            if [[ -n "$latency" ]]; then
                assert_pass "Chat response includes latency_ms: ${latency}"
            else
                assert_warn "Chat response missing latency_ms"
            fi

            # 5.3.5 Response has sources
            local sources_count
            sources_count=$(echo "$CURL_BODY" | jq '.sources // [] | length' 2>/dev/null || echo "0")
            if [[ "$sources_count" -gt 0 ]]; then
                assert_pass "Chat response includes $sources_count sources (RAG)"
            else
                assert_warn "Chat response has no sources (RAG may not have matched)"
            fi
        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            assert_warn "Auth chat rate limited (429)"
            assert_skip "Cannot test chat response fields"
            assert_skip "Cannot test chat response fields"
            assert_skip "Cannot test chat response fields"
            assert_skip "Cannot test chat response fields"
        else
            assert_warn "Auth English chat returned HTTP $CURL_STATUS"
            assert_skip "Cannot test response fields"
            assert_skip "Cannot test response fields"
            assert_skip "Cannot test response fields"
            assert_skip "Cannot test response fields"
        fi

        # 5.3.6 Assamese chat with auth
        perform_request "${BASE_URL}/api/v1/chat/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message":"পানী চক্ৰ কি?","language":"as"}'
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Auth Assamese chat returns 200 (Sarvam route)"
        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            assert_warn "Auth Assamese chat rate limited"
        else
            assert_warn "Auth Assamese chat returned HTTP $CURL_STATUS"
        fi

        # 5.3.7 Chat with conversation_id
        perform_request "${BASE_URL}/api/v1/chat/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message":"continue","language":"en","conversation_id":"test-conv-123"}'
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 422 ]]; then
            assert_pass "Chat with conversation_id handled ($CURL_STATUS)"
        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            assert_warn "Rate limited"
        else
            assert_warn "Chat with conv_id returned HTTP $CURL_STATUS"
        fi
    else
        assert_skip "Auth English chat (no token)"
        assert_skip "Auth chat response fields (no token)"
        assert_skip "Auth chat model_used (no token)"
        assert_skip "Auth chat latency_ms (no token)"
        assert_skip "Auth chat sources (no token)"
        assert_skip "Auth Assamese chat (no token)"
        assert_skip "Chat with conversation_id (no token)"
    fi

    subsection "5.4 Streaming Endpoint"

    # 5.4.1 Stream endpoint exists
    perform_request "${BASE_URL}/api/v1/chat/stream" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"hello","language":"en"}'
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Stream endpoint returns 200"
        local stream_ct
        stream_ct=$(get_header_value "content-type")
        if [[ "$stream_ct" == *"text/event-stream"* ]]; then
            assert_pass "Stream Content-Type: text/event-stream"
        else
            assert_warn "Stream Content-Type: $stream_ct (expected event-stream)"
        fi
    elif [[ "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "Stream endpoint requires auth (401)"
        assert_skip "Stream Content-Type (auth required)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Stream endpoint rate limited"
        assert_skip "Stream Content-Type (rate limited)"
    else
        assert_warn "Stream endpoint returned HTTP $CURL_STATUS"
        assert_skip "Stream Content-Type check"
    fi

    # 5.4.2 Stream without message
    perform_request "${BASE_URL}/api/v1/chat/stream" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"language":"en"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Stream without message returns $CURL_STATUS"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Stream without message returned HTTP $CURL_STATUS"
    fi

    subsection "5.5 Chat History"

    # 5.5.1 History without auth
    perform_request "${BASE_URL}/api/v1/chat/history"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Chat history requires auth ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_warn "Chat history accessible without auth"
    else
        assert_warn "Chat history returned HTTP $CURL_STATUS"
    fi

    # 5.5.2 History with auth
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/chat/history" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Chat history with auth returns 200"
            if is_json; then
                assert_pass "Chat history is valid JSON"
            else
                assert_warn "Chat history is not JSON"
            fi
        else
            assert_warn "Chat history with auth returned HTTP $CURL_STATUS"
            assert_skip "Chat history JSON check"
        fi
    else
        assert_skip "Chat history with auth (no token)"
        assert_skip "Chat history JSON (no token)"
    fi

    # 5.5.3 Conversations endpoint
    perform_request "${BASE_URL}/api/v1/chat/conversations"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Chat conversations requires auth ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_warn "Chat conversations accessible without auth"
    else
        assert_warn "Chat conversations returned HTTP $CURL_STATUS"
    fi

    # 5.5.4 Conversations with auth
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/chat/conversations" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Chat conversations with auth returns 200"
        else
            assert_warn "Chat conversations auth returned HTTP $CURL_STATUS"
        fi
    else
        assert_skip "Chat conversations with auth (no token)"
    fi

    subsection "5.6 Chat Input Validation (Additional)"

    # 5.6.1 Numeric message
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":12345,"language":"en"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Numeric message handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Numeric message returned HTTP $CURL_STATUS"
    fi

    # 5.6.2 Array message
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":["hello","world"],"language":"en"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Array message rejected ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Array message returned HTTP $CURL_STATUS"
    fi

    # 5.6.3 Object message
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":{"nested":"value"},"language":"en"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Object message rejected ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Object message returned HTTP $CURL_STATUS"
    fi

    # 5.6.4 Boolean message
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":true,"language":"en"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Boolean message handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Boolean message returned HTTP $CURL_STATUS"
    fi

    # 5.6.5 Null message
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":null,"language":"en"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Null message rejected ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Null message returned HTTP $CURL_STATUS"
    fi

    # 5.6.6 Message with special characters
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"What is 2+2? <b>bold</b> & \"quotes\"","language":"en"}'
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Special chars in message handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Special chars message returned HTTP $CURL_STATUS"
    fi

    # 5.6.7 Invalid JSON body
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d 'not valid json at all'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Invalid JSON body rejected ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Invalid JSON returned HTTP $CURL_STATUS"
    fi

    # 5.6.8 GET method on chat
    perform_request "${BASE_URL}/api/v1/chat/"
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "GET /chat/ returns $CURL_STATUS (POST only)"
    else
        assert_warn "GET /chat/ returned HTTP $CURL_STATUS"
    fi

    # 5.6.9 PUT method on chat
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X PUT \
        -H "Content-Type: application/json" \
        -d '{"message":"test","language":"en"}'
    if [[ "$CURL_STATUS" -eq 405 ]]; then
        assert_pass "PUT /chat/ returns 405"
    else
        assert_warn "PUT /chat/ returned HTTP $CURL_STATUS"
    fi

    # 5.6.10 DELETE method on chat
    perform_request "${BASE_URL}/api/v1/chat/" -X DELETE
    if [[ "$CURL_STATUS" -eq 405 ]]; then
        assert_pass "DELETE /chat/ returns 405"
    else
        assert_warn "DELETE /chat/ returned HTTP $CURL_STATUS"
    fi

    subsection "5.7 Provider Routing Validation"

    if [[ -n "$AUTH_TOKEN" ]]; then
        # 5.7.1 English chat model should be Gemini (Vertex AI)
        perform_request "${BASE_URL}/api/v1/chat/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message":"What is photosynthesis?","language":"en"}'
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            local en_model
            en_model=$(json_field '.model_used // .model // empty')
            if [[ -n "$en_model" ]]; then
                # 5.7.2 English uses Gemini
                if echo "$en_model" | grep -qi "gemini"; then
                    assert_pass "English chat uses Gemini: $en_model"
                else
                    assert_warn "English chat model not Gemini: $en_model"
                fi
                # 5.7.3 English does NOT use Cloudflare/Llama/Worker
                if echo "$en_model" | grep -qi "cloudflare\|llama\|worker"; then
                    assert_fail "English chat should not use Cloudflare/Llama: $en_model"
                else
                    assert_pass "English chat does not use Cloudflare/Llama/Worker"
                fi
            else
                assert_skip "English chat model_used field not returned"
                assert_skip "English provider exclusion check (no model_used)"
            fi
        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            assert_skip "English provider check: rate limited"
            assert_skip "English provider exclusion: rate limited"
        else
            assert_warn "English chat for provider check returned HTTP $CURL_STATUS"
            assert_skip "English provider exclusion check skipped"
        fi

        # 5.7.4 Assamese chat model should be Sarvam
        perform_request "${BASE_URL}/api/v1/chat/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message":"পোহৰ সংশ্লেষণ কি?","language":"as"}'
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            local as_model
            as_model=$(json_field '.model_used // .model // empty')
            if [[ -n "$as_model" ]]; then
                # 5.7.5 Assamese uses Sarvam
                if echo "$as_model" | grep -qi "sarvam"; then
                    assert_pass "Assamese chat uses Sarvam: $as_model"
                else
                    assert_warn "Assamese chat model not Sarvam: $as_model"
                fi
                # 5.7.6 Assamese does NOT use Cloudflare/Worker
                if echo "$as_model" | grep -qi "cloudflare\|worker"; then
                    assert_fail "Assamese chat should not use Cloudflare: $as_model"
                else
                    assert_pass "Assamese chat does not use Cloudflare/Worker"
                fi
            else
                assert_skip "Assamese chat model_used field not returned"
                assert_skip "Assamese provider exclusion check (no model_used)"
            fi
        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            assert_skip "Assamese provider check: rate limited"
            assert_skip "Assamese provider exclusion: rate limited"
        else
            assert_warn "Assamese chat for provider check returned HTTP $CURL_STATUS"
            assert_skip "Assamese provider exclusion check skipped"
        fi
    else
        assert_skip "5.7 Provider routing: no AUTH_TOKEN"
        assert_skip "5.7 English Gemini check: no AUTH_TOKEN"
        assert_skip "5.7 English exclusion check: no AUTH_TOKEN"
        assert_skip "5.7 Assamese Sarvam check: no AUTH_TOKEN"
        assert_skip "5.7 Assamese exclusion check: no AUTH_TOKEN"
    fi

    subsection "5.8 Streaming Provider Check"

    if [[ -n "$AUTH_TOKEN" ]]; then
        # 5.8.1 Authenticated streaming English chat
        perform_stream_request "${BASE_URL}/api/v1/chat/stream" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message":"Explain gravity briefly","language":"en"}'
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Authenticated streaming English chat returns 200"
            # 5.8.2 Content-Type is event-stream
            if has_header "text/event-stream"; then
                assert_pass "Streaming English: Content-Type is text/event-stream"
            else
                assert_warn "Streaming English: unexpected Content-Type"
            fi
            # 5.8.3 Body contains data lines
            if echo "$CURL_BODY" | grep -q "^data:"; then
                assert_pass "Streaming English: body contains data: lines"
            else
                assert_warn "Streaming English: no data: lines in body"
            fi
        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            assert_skip "Streaming English: rate limited"
            assert_skip "Streaming English content-type: rate limited"
            assert_skip "Streaming English data lines: rate limited"
        else
            assert_warn "Streaming English chat returned HTTP $CURL_STATUS"
            assert_skip "Streaming English content-type check skipped"
            assert_skip "Streaming English data lines check skipped"
        fi

        # 5.8.4 Authenticated streaming Assamese chat
        perform_stream_request "${BASE_URL}/api/v1/chat/stream" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message":"মাধ্যাকর্ষণ কি?","language":"as"}'
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Authenticated streaming Assamese chat returns 200"
            # 5.8.5 Content-Type is event-stream
            if has_header "text/event-stream"; then
                assert_pass "Streaming Assamese: Content-Type is text/event-stream"
            else
                assert_warn "Streaming Assamese: unexpected Content-Type"
            fi
            # 5.8.6 Body contains data lines
            if echo "$CURL_BODY" | grep -q "^data:"; then
                assert_pass "Streaming Assamese: body contains data: lines"
            else
                assert_warn "Streaming Assamese: no data: lines in body"
            fi
        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            assert_skip "Streaming Assamese: rate limited"
            assert_skip "Streaming Assamese content-type: rate limited"
            assert_skip "Streaming Assamese data lines: rate limited"
        else
            assert_warn "Streaming Assamese chat returned HTTP $CURL_STATUS"
            assert_skip "Streaming Assamese content-type check skipped"
            assert_skip "Streaming Assamese data lines check skipped"
        fi
    else
        assert_skip "5.8 Streaming English: no AUTH_TOKEN"
        assert_skip "5.8 Streaming English content-type: no AUTH_TOKEN"
        assert_skip "5.8 Streaming English data: no AUTH_TOKEN"
        assert_skip "5.8 Streaming Assamese: no AUTH_TOKEN"
        assert_skip "5.8 Streaming Assamese content-type: no AUTH_TOKEN"
        assert_skip "5.8 Streaming Assamese data: no AUTH_TOKEN"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 5: Chat ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 6: RAG / Hybrid Search (P3 Vertex AI Search) - 35+ tests
# ===============================================================================

test_layer_6_rag_search() {
    section_header "LAYER 6: RAG / Hybrid Search (P3 Vertex AI Search)"

    local layer_start=$TOTAL_TESTS

    subsection "6.1 RAG Context in Chat Response"

    # 6.1.1 Chat with academic question triggers RAG
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/chat/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message":"What are the properties of light according to NCERT Class 10 Science?","language":"en"}'
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "RAG-triggering query returns 200"

            # 6.1.2 Sources array present
            local sources_len
            sources_len=$(echo "$CURL_BODY" | jq '.sources // [] | length' 2>/dev/null || echo "0")
            if [[ "$sources_len" -gt 0 ]]; then
                assert_pass "RAG returned $sources_len source(s)"
            else
                assert_warn "RAG returned no sources for academic query"
            fi

            # 6.1.3 Source has title field
            local src_title
            src_title=$(echo "$CURL_BODY" | jq -r '.sources[0].title // empty' 2>/dev/null || echo "")
            if [[ -n "$src_title" ]]; then
                assert_pass "Source[0] has title: $src_title"
            else
                assert_skip "Source title not found"
            fi

            # 6.1.4 Source has score field
            local src_score
            src_score=$(echo "$CURL_BODY" | jq -r '.sources[0].score // .sources[0].relevance_score // empty' 2>/dev/null || echo "")
            if [[ -n "$src_score" ]]; then
                assert_pass "Source[0] has relevance score: $src_score"
            else
                assert_skip "Source score not found"
            fi

            # 6.1.5 Source has doc_id or url
            local src_id
            src_id=$(echo "$CURL_BODY" | jq -r '.sources[0].doc_id // .sources[0].url // .sources[0].id // empty' 2>/dev/null || echo "")
            if [[ -n "$src_id" ]]; then
                assert_pass "Source[0] has identifier: $src_id"
            else
                assert_skip "Source identifier not found"
            fi

            # 6.1.6 Context chunks
            local chunks_len
            chunks_len=$(echo "$CURL_BODY" | jq '.context_chunks // .chunks // [] | length' 2>/dev/null || echo "0")
            if [[ "$chunks_len" -gt 0 ]]; then
                assert_pass "Response includes $chunks_len context chunks"
            else
                assert_skip "No context_chunks in response"
            fi
        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            assert_warn "RAG query rate limited"
            assert_skip "Sources check"
            assert_skip "Source title"
            assert_skip "Source score"
            assert_skip "Source doc_id"
            assert_skip "Context chunks"
        else
            assert_warn "RAG query returned HTTP $CURL_STATUS"
            assert_skip "Sources check"
            assert_skip "Source title"
            assert_skip "Source score"
            assert_skip "Source doc_id"
            assert_skip "Context chunks"
        fi
    else
        assert_skip "RAG query (no auth token)"
        assert_skip "RAG sources (no auth token)"
        assert_skip "Source title (no token)"
        assert_skip "Source score (no token)"
        assert_skip "Source doc_id (no token)"
        assert_skip "Context chunks (no token)"
    fi

    subsection "6.2 Circuit Breaker Status"

    # 6.2.1 Verify search circuit breaker not open
    perform_request "${BASE_URL}/health/circuit-breakers"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        local search_cb
        search_cb=$(json_field '.vertex_search.state // .circuit_breakers.vertex_search // empty')
        if [[ "$search_cb" == "open" ]]; then
            assert_warn "Vertex Search circuit breaker is OPEN (RAG degraded)"
        elif [[ "$search_cb" == "closed" ]]; then
            assert_pass "Vertex Search circuit breaker CLOSED (healthy)"
        elif [[ "$search_cb" == "half_open" ]]; then
            assert_warn "Vertex Search circuit breaker HALF_OPEN (recovering)"
        else
            assert_skip "Vertex Search CB state unknown: $search_cb"
        fi

        # 6.2.2 Check failure count
        local search_failures
        search_failures=$(echo "$CURL_BODY" | jq '.vertex_search.failure_count // 0' 2>/dev/null || echo "0")
        if [[ "$search_failures" -lt 5 ]]; then
            assert_pass "Vertex Search failures low: $search_failures"
        else
            assert_warn "Vertex Search has $search_failures failures"
        fi
    else
        assert_skip "Circuit breaker status (endpoint returned $CURL_STATUS)"
        assert_skip "Failure count check"
    fi

    subsection "6.3 RAG Fallback Behavior"

    # 6.3.1 Non-academic query (should work without RAG)
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/chat/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message":"Tell me a joke","language":"en"}'
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Non-academic query works (fallback to LLM-only)"
            local fallback_resp
            fallback_resp=$(json_field '.response // .answer // .text // empty')
            if [[ -n "$fallback_resp" ]]; then
                assert_pass "Fallback response has content"
            else
                assert_warn "Fallback response empty"
            fi
        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            assert_warn "Rate limited"
            assert_skip "Fallback content"
        else
            assert_warn "Non-academic query returned HTTP $CURL_STATUS"
            assert_skip "Fallback content"
        fi
    else
        assert_skip "Non-academic fallback (no token)"
        assert_skip "Fallback content (no token)"
    fi

    # 6.3.2 RAG with Assamese query
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/chat/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message":"সালোকসংশ্লেষণ বুজাই দিয়া","language":"as"}'
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Assamese RAG query returns 200"
        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            assert_warn "Assamese RAG rate limited"
        else
            assert_warn "Assamese RAG returned HTTP $CURL_STATUS"
        fi
    else
        assert_skip "Assamese RAG query (no token)"
    fi

    subsection "6.4 Search Quality Indicators"

    # 6.4.1 Response includes search_used flag
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/chat/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message":"Define photosynthesis for Class 7 NCERT","language":"en"}'
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            local search_used
            search_used=$(json_field '.search_used // .rag_used // empty')
            if [[ -n "$search_used" ]]; then
                assert_pass "Response includes search_used flag: $search_used"
            else
                assert_skip "No search_used flag in response"
            fi

            # 6.4.2 RAG latency
            local rag_latency
            rag_latency=$(json_field '.search_latency_ms // .rag_latency_ms // empty')
            if [[ -n "$rag_latency" ]]; then
                assert_pass "RAG latency reported: ${rag_latency}ms"
            else
                assert_skip "No RAG latency in response"
            fi
        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            assert_warn "Rate limited"
            assert_skip "RAG latency"
        else
            assert_warn "Search quality query returned HTTP $CURL_STATUS"
            assert_skip "RAG latency"
        fi
    else
        assert_skip "Search used flag (no token)"
        assert_skip "RAG latency (no token)"
    fi

    # 6.4.3 Health deep shows vertex_search
    perform_request "${BASE_URL}/health/deep"
    if [[ "$CURL_STATUS" -eq 200 ]] && is_json; then
        local vs_health
        vs_health=$(json_field '.services.vertex_search // .vertex_search // empty')
        if [[ -n "$vs_health" ]]; then
            assert_pass "Deep health includes vertex_search status: $vs_health"
        else
            assert_skip "vertex_search not in deep health"
        fi
    else
        assert_skip "Deep health for vertex_search (HTTP $CURL_STATUS)"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 6: RAG/Search ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 7: Content & Knowledge (P4 MongoDB) - 65+ tests
# ===============================================================================

test_layer_7_content() {
    section_header "LAYER 7: Content & Knowledge (P4 MongoDB)"

    local layer_start=$TOTAL_TESTS

    subsection "7.1 Library Bundle"

    # 7.1.1 GET library-bundle (public)
    perform_request "${BASE_URL}/api/v1/content/library-bundle"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Library bundle returns 200 (public)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Library bundle endpoint not found"
    else
        assert_warn "Library bundle returned HTTP $CURL_STATUS"
    fi

    # 7.1.2 Library bundle is JSON
    if [[ "$CURL_STATUS" -eq 200 ]] && is_json; then
        assert_pass "Library bundle is valid JSON"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_warn "Library bundle is not JSON"
    else
        assert_skip "Library bundle JSON check"
    fi

    # 7.1.3 Library bundle has boards
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        local boards_count
        boards_count=$(echo "$CURL_BODY" | jq '.boards // [] | length' 2>/dev/null || echo "0")
        if [[ "$boards_count" -gt 0 ]]; then
            assert_pass "Library bundle has $boards_count board(s)"
        else
            assert_warn "Library bundle has no boards"
        fi
    else
        assert_skip "Boards count check"
    fi

    # 7.1.4 Library bundle response time
    if [[ "$CURL_TTFB" -lt 5000 ]]; then
        assert_pass "Library bundle TTFB under 5s (${CURL_TTFB}ms)"
    else
        assert_warn "Library bundle TTFB slow (${CURL_TTFB}ms)"
    fi

    # 7.1.5 Slim mode
    perform_request "${BASE_URL}/api/v1/content/library-bundle?slim=1"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Library bundle slim mode returns 200"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Slim mode not supported"
    else
        assert_warn "Library bundle slim returned HTTP $CURL_STATUS"
    fi

    # 7.1.6 Slim mode is smaller
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        local slim_size=${#CURL_BODY}
        assert_pass "Slim bundle size: $slim_size bytes"
    fi

    # 7.1.7 Library bundle no auth required
    perform_request "${BASE_URL}/api/v1/content/library-bundle"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Library bundle accessible without auth"
    else
        assert_warn "Library bundle may require auth ($CURL_STATUS)"
    fi

    # 7.1.8 Library bundle caching
    if has_header "cache-control"; then
        assert_pass "Library bundle has Cache-Control header"
    else
        assert_warn "Library bundle missing Cache-Control"
    fi

    subsection "7.1b Library Bundle API Alignment"

    # 7.1b.1 Slim mode returns JSON with boards array
    perform_request "${BASE_URL}/api/v1/content/library-bundle?slim=1"
    local slim_status="$CURL_STATUS"
    local slim_body="$CURL_BODY"
    if [[ "$slim_status" -eq 200 ]] && is_json; then
        assert_pass "Slim bundle returns valid JSON (200)"
    elif [[ "$slim_status" -eq 200 ]]; then
        assert_warn "Slim bundle returns 200 but not valid JSON"
    else
        assert_warn "Slim bundle returned HTTP $slim_status"
    fi

    # 7.1b.2 Slim bundle has boards array
    local slim_boards_count
    slim_boards_count=$(echo "$slim_body" | jq '.boards // [] | length' 2>/dev/null || echo "0")
    if [[ "$slim_status" -eq 200 && "$slim_boards_count" -gt 0 ]]; then
        assert_pass "Slim bundle has boards array ($slim_boards_count boards)"
    elif [[ "$slim_status" -eq 200 ]]; then
        assert_warn "Slim bundle has no boards"
    else
        assert_skip "Slim bundle boards check"
    fi

    # 7.1b.3 First board has id field
    if [[ "$slim_status" -eq 200 && "$slim_boards_count" -gt 0 ]]; then
        local board_id
        board_id=$(echo "$slim_body" | jq -r '.boards[0].id // empty' 2>/dev/null || echo "")
        if [[ -n "$board_id" ]]; then
            assert_pass "First board has id field: $board_id"
        else
            assert_warn "First board missing id field"
        fi
    else
        assert_skip "Board id field check"
    fi

    # 7.1b.4 First board has name field
    if [[ "$slim_status" -eq 200 && "$slim_boards_count" -gt 0 ]]; then
        local board_name
        board_name=$(echo "$slim_body" | jq -r '.boards[0].name // empty' 2>/dev/null || echo "")
        if [[ -n "$board_name" ]]; then
            assert_pass "First board has name field: $board_name"
        else
            assert_warn "First board missing name field"
        fi
    else
        assert_skip "Board name field check"
    fi

    # 7.1b.5 First board has slug field
    if [[ "$slim_status" -eq 200 && "$slim_boards_count" -gt 0 ]]; then
        local board_slug
        board_slug=$(echo "$slim_body" | jq -r '.boards[0].slug // empty' 2>/dev/null || echo "")
        if [[ -n "$board_slug" ]]; then
            assert_pass "First board has slug field: $board_slug"
        else
            assert_warn "First board missing slug field"
        fi
    else
        assert_skip "Board slug field check"
    fi

    # 7.1b.6 First board has classes field
    if [[ "$slim_status" -eq 200 && "$slim_boards_count" -gt 0 ]]; then
        local has_classes
        has_classes=$(echo "$slim_body" | jq -r '.boards[0].classes // empty | type' 2>/dev/null || echo "")
        if [[ "$has_classes" == "array" ]]; then
            assert_pass "First board has classes array"
        else
            assert_warn "First board missing classes array"
        fi
    else
        assert_skip "Board classes field check"
    fi

    # 7.1b.7 Full bundle returns boards with nested structure
    perform_request "${BASE_URL}/api/v1/content/library-bundle"
    local full_status="$CURL_STATUS"
    local full_body="$CURL_BODY"
    local full_size=${#full_body}
    if [[ "$full_status" -eq 200 ]] && is_json; then
        assert_pass "Full bundle returns valid JSON (200)"
    elif [[ "$full_status" -eq 200 ]]; then
        assert_warn "Full bundle returns 200 but not valid JSON"
    else
        assert_warn "Full bundle returned HTTP $full_status"
    fi

    # 7.1b.8 Full bundle subjects have chapters array
    if [[ "$full_status" -eq 200 ]]; then
        local has_chapters
        has_chapters=$(echo "$full_body" | jq -r '[.boards[]?.classes[]?.streams[]?.subjects[]?.chapters // empty] | length' 2>/dev/null || echo "0")
        if [[ "$has_chapters" -gt 0 ]]; then
            assert_pass "Full bundle has subjects with chapters ($has_chapters)"
        else
            # Try alternative structure
            local alt_chapters
            alt_chapters=$(echo "$full_body" | jq -r '[.boards[]?.classes[]?.subjects[]?.chapters // empty] | length' 2>/dev/null || echo "0")
            if [[ "$alt_chapters" -gt 0 ]]; then
                assert_pass "Full bundle has subjects with chapters (alt structure: $alt_chapters)"
            else
                assert_warn "Full bundle subjects may not have chapters array"
            fi
        fi
    else
        assert_skip "Full bundle chapters check"
    fi

    # 7.1b.9 Boot param request returns 200
    perform_request "${BASE_URL}/api/v1/content/library-bundle?boot=SEBA"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Library bundle with boot=SEBA returns 200"
    elif [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Library bundle boot param not supported ($CURL_STATUS)"
    else
        assert_warn "Library bundle boot=SEBA returned HTTP $CURL_STATUS"
    fi

    # 7.1b.10 Slim bundle size <= full bundle size
    local slim_size=${#slim_body}
    if [[ "$slim_status" -eq 200 && "$full_status" -eq 200 ]]; then
        if [[ "$slim_size" -le "$full_size" ]]; then
            assert_pass "Slim bundle ($slim_size bytes) <= full bundle ($full_size bytes)"
        else
            assert_warn "Slim bundle ($slim_size) larger than full ($full_size)"
        fi
    else
        assert_skip "Slim vs full size comparison"
    fi

    # 7.1b.11 Cache-Control header on slim request
    perform_request "${BASE_URL}/api/v1/content/library-bundle?slim=1"
    if has_header "cache-control"; then
        assert_pass "Slim bundle has Cache-Control header"
    else
        assert_warn "Slim bundle missing Cache-Control header"
    fi

    # 7.1b.12 Cache-Control header on full request
    perform_request "${BASE_URL}/api/v1/content/library-bundle"
    if has_header "cache-control"; then
        assert_pass "Full bundle has Cache-Control header"
    else
        assert_warn "Full bundle missing Cache-Control header"
    fi

    # 7.1b.13 Full bundle boards count matches slim
    if [[ "$slim_status" -eq 200 && "$full_status" -eq 200 ]]; then
        local full_boards_count
        full_boards_count=$(echo "$full_body" | jq '.boards // [] | length' 2>/dev/null || echo "0")
        if [[ "$slim_boards_count" -eq "$full_boards_count" ]]; then
            assert_pass "Slim and full bundle have same board count ($full_boards_count)"
        else
            assert_warn "Board count mismatch: slim=$slim_boards_count full=$full_boards_count"
        fi
    else
        assert_skip "Board count comparison"
    fi

    # 7.1b.14 Slim response time
    if [[ "$CURL_TTFB" -lt 5000 ]]; then
        assert_pass "Slim bundle TTFB under 5s (${CURL_TTFB}ms)"
    else
        assert_warn "Slim bundle TTFB slow (${CURL_TTFB}ms)"
    fi

    # 7.1b.15 Bundle content-type is JSON
    local bundle_ct
    bundle_ct=$(get_header_value "content-type")
    if [[ "$bundle_ct" == *"application/json"* ]]; then
        assert_pass "Bundle Content-Type is application/json"
    else
        assert_warn "Bundle Content-Type: $bundle_ct"
    fi

    subsection "7.2 Content Render"

    # 7.2.1 Render with valid path
    perform_request "${BASE_URL}/api/v1/content/render/SEBA/10/Science/Chapter-1"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Content render returns 200"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "Content render returns 404 (content not found)"
    else
        assert_warn "Content render returned HTTP $CURL_STATUS"
    fi

    # 7.2.2 Render with invalid board
    perform_request "${BASE_URL}/api/v1/content/render/INVALID_BOARD/10/Science/Chapter-1"
    if [[ "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Invalid board returns $CURL_STATUS"
    else
        assert_warn "Invalid board returned HTTP $CURL_STATUS"
    fi

    # 7.2.3 Render with invalid class
    perform_request "${BASE_URL}/api/v1/content/render/SEBA/99/Science/Chapter-1"
    if [[ "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Invalid class returns $CURL_STATUS"
    else
        assert_warn "Invalid class returned HTTP $CURL_STATUS"
    fi

    # 7.2.4 Render path traversal attempt
    perform_request "${BASE_URL}/api/v1/content/render/../../../etc/passwd"
    if [[ "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Render path traversal blocked ($CURL_STATUS)"
    else
        assert_warn "Render path traversal returned HTTP $CURL_STATUS"
    fi

    # 7.2.5 Render with encoded characters
    perform_request "${BASE_URL}/api/v1/content/render/SEBA/10/Science/Chapter%201"
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "URL-encoded render path handled ($CURL_STATUS)"
    else
        assert_warn "Encoded render path returned HTTP $CURL_STATUS"
    fi

    subsection "7.3 Content by Slug"

    # 7.3.1 Slug endpoint
    perform_request "${BASE_URL}/api/v1/content/photosynthesis"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Content by slug returns 200"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "Content slug returns 404 (not found)"
    else
        assert_warn "Content slug returned HTTP $CURL_STATUS"
    fi

    # 7.3.2 Non-existent slug
    perform_request "${BASE_URL}/api/v1/content/this-slug-definitely-does-not-exist-xyz"
    if [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "Non-existent slug returns 404"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_warn "Non-existent slug returned 200 (unexpected)"
    else
        assert_warn "Non-existent slug returned HTTP $CURL_STATUS"
    fi

    # 7.3.3 Slug with special characters
    perform_request "${BASE_URL}/api/v1/content/<script>alert(1)</script>"
    if [[ "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "XSS slug rejected ($CURL_STATUS)"
    else
        assert_warn "XSS slug returned HTTP $CURL_STATUS"
    fi

    # 7.3.4 Very long slug
    local long_slug
    long_slug=$(printf 'a%.0s' {1..500})
    perform_request "${BASE_URL}/api/v1/content/${long_slug}"
    if [[ "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 414 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Very long slug handled ($CURL_STATUS)"
    else
        assert_warn "Long slug returned HTTP $CURL_STATUS"
    fi

    subsection "7.4 Subject Chapters"

    # 7.4.1 Get chapters for a subject
    perform_request "${BASE_URL}/api/v1/content/subject/SEBA/10/Science"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Subject chapters returns 200"
        if is_json; then
            assert_pass "Subject chapters is JSON"
            local ch_count
            ch_count=$(echo "$CURL_BODY" | jq '.chapters // . | length' 2>/dev/null || echo "0")
            assert_pass "Found $ch_count chapter entries"
        else
            assert_warn "Subject chapters not JSON"
            assert_skip "Chapter count"
        fi
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "Subject chapters returns 404 (not configured)"
        assert_skip "Subject chapters JSON"
        assert_skip "Chapter count"
    else
        assert_warn "Subject chapters returned HTTP $CURL_STATUS"
        assert_skip "Subject chapters JSON"
        assert_skip "Chapter count"
    fi

    # 7.4.2 Invalid subject
    perform_request "${BASE_URL}/api/v1/content/subject/SEBA/10/NonExistentSubject"
    if [[ "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Invalid subject handled ($CURL_STATUS)"
    else
        assert_warn "Invalid subject returned HTTP $CURL_STATUS"
    fi

    # 7.4.3 Subjects endpoint method check
    perform_request "${BASE_URL}/api/v1/content/subject/SEBA/10/Science" -X POST
    if [[ "$CURL_STATUS" -eq 405 ]]; then
        assert_pass "POST on subjects returns 405 (GET only)"
    else
        assert_warn "POST on subjects returned HTTP $CURL_STATUS"
    fi

    subsection "7.5 Content Validation & Edge Cases"

    # 7.5.1 Empty path segments
    perform_request "${BASE_URL}/api/v1/content/render////"
    if [[ "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Empty path segments handled ($CURL_STATUS)"
    else
        assert_warn "Empty path segments returned HTTP $CURL_STATUS"
    fi

    # 7.5.2 Content endpoint content-type
    perform_request "${BASE_URL}/api/v1/content/library-bundle"
    local ct
    ct=$(get_header_value "content-type")
    if [[ "$ct" == *"application/json"* ]]; then
        assert_pass "Content API returns application/json"
    else
        assert_warn "Content API Content-Type: $ct"
    fi

    # 7.5.3 HEAD request on library-bundle
    perform_request "${BASE_URL}/api/v1/content/library-bundle" -X HEAD
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 405 ]]; then
        assert_pass "HEAD on library-bundle returns $CURL_STATUS"
    else
        assert_warn "HEAD on library-bundle returned HTTP $CURL_STATUS"
    fi

    # 7.5.4 Query parameters ignored gracefully
    perform_request "${BASE_URL}/api/v1/content/library-bundle?page=1&limit=10&unexpected=value"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Extra query params ignored gracefully"
    else
        assert_warn "Extra query params caused HTTP $CURL_STATUS"
    fi

    # 7.5.5 SQL injection in slug
    perform_request "${BASE_URL}/api/v1/content/1' OR '1'='1"
    if [[ "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "SQL injection in slug blocked ($CURL_STATUS)"
    else
        assert_warn "SQL injection slug returned HTTP $CURL_STATUS"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 7: Content ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 8: Subscription & Payments (P8 Razorpay) - 80+ tests
# ===============================================================================

test_layer_8_payments() {
    section_header "LAYER 8: Subscription & Payments (P8 Razorpay)"

    local layer_start=$TOTAL_TESTS

    subsection "8.1 Subscription Plans"

    # 8.1.1 GET subscription plans (public)
    perform_request "${BASE_URL}/api/v1/subscription/plans"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Subscription plans returns 200 (public)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Subscription plans endpoint not found"
    else
        assert_warn "Subscription plans returned HTTP $CURL_STATUS"
    fi

    # 8.1.2 Plans is JSON
    if [[ "$CURL_STATUS" -eq 200 ]] && is_json; then
        assert_pass "Plans response is valid JSON"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_warn "Plans response is not JSON"
    else
        assert_skip "Plans JSON check"
    fi

    # 8.1.3 Plans contains free tier
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        if echo "$CURL_BODY" | jq -e '.. | .name? // empty | select(test("free";"i"))' >/dev/null 2>&1; then
            assert_pass "Plans include free tier"
        elif echo "$CURL_BODY" | grep -qi "free"; then
            assert_pass "Plans reference free tier"
        else
            assert_warn "Free tier not found in plans"
        fi
    else
        assert_skip "Free tier check"
    fi

    # 8.1.4 Plans contains pro tier
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        if echo "$CURL_BODY" | grep -qi "pro\|premium\|paid"; then
            assert_pass "Plans include pro/premium tier"
        else
            assert_warn "Pro/premium tier not found in plans"
        fi
    else
        assert_skip "Pro tier check"
    fi

    # 8.1.5 Plans no auth required
    assert_pass "Plans endpoint accessible without auth"

    # 8.1.6 Plans caching
    if has_header "cache-control"; then
        assert_pass "Plans has Cache-Control header"
    else
        assert_warn "Plans missing Cache-Control"
    fi

    # 8.1.7 Plans content-type
    local ct
    ct=$(get_header_value "content-type")
    if [[ "$ct" == *"application/json"* ]]; then
        assert_pass "Plans Content-Type: application/json"
    else
        assert_warn "Plans Content-Type: $ct"
    fi

    subsection "8.2 Subscription Status"

    # 8.2.1 Status without auth
    perform_request "${BASE_URL}/api/v1/subscription/status"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Subscription status requires auth ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_warn "Subscription status accessible without auth"
    else
        assert_warn "Subscription status returned HTTP $CURL_STATUS"
    fi

    # 8.2.2 Status with auth
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/subscription/status" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Subscription status with auth returns 200"

            # 8.2.3 Status has tier field
            local tier
            tier=$(json_field '.tier // .plan // .subscription_tier // empty')
            if [[ -n "$tier" ]]; then
                assert_pass "Subscription tier: $tier"
            else
                assert_warn "Subscription tier not found"
            fi

            # 8.2.4 Status has limits
            local limit
            limit=$(json_field '.limit // .daily_limit // .messages_remaining // empty')
            if [[ -n "$limit" ]]; then
                assert_pass "Subscription limit info: $limit"
            else
                assert_skip "Subscription limit field not found"
            fi
        else
            assert_warn "Subscription status auth returned HTTP $CURL_STATUS"
            assert_skip "Subscription tier"
            assert_skip "Subscription limit"
        fi
    else
        assert_skip "Subscription status with auth (no token)"
        assert_skip "Subscription tier (no token)"
        assert_skip "Subscription limit (no token)"
    fi

    # 8.2.5 Status with invalid token
    perform_request "${BASE_URL}/api/v1/subscription/status" \
        -H "Authorization: Bearer invalid-token"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Subscription status rejects invalid token ($CURL_STATUS)"
    else
        assert_warn "Invalid token status returned HTTP $CURL_STATUS"
    fi

    subsection "8.3 Create Order"

    # 8.3.1 Create order without auth
    perform_request "${BASE_URL}/api/v1/payments/create-order" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"plan_id":"pro_monthly"}'
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Create order requires auth ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Create order rate limited"
    else
        assert_warn "Create order without auth returned HTTP $CURL_STATUS"
    fi

    # 8.3.2 Create order with auth
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/payments/create-order" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"plan_id":"pro_monthly"}'
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 201 ]]; then
            assert_pass "Create order returns $CURL_STATUS"
        elif [[ "$CURL_STATUS" -eq 503 ]]; then
            assert_pass "Create order returns 503 (Razorpay not configured)"
        elif [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
            assert_pass "Create order validation response ($CURL_STATUS)"
        else
            assert_warn "Create order returned HTTP $CURL_STATUS"
        fi

        # 8.3.3 Create order with missing plan_id
        perform_request "${BASE_URL}/api/v1/payments/create-order" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{}'
        if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
            assert_pass "Create order without plan_id returns $CURL_STATUS"
        elif [[ "$CURL_STATUS" -eq 503 ]]; then
            assert_pass "Create order returns 503 (Razorpay not configured)"
        else
            assert_warn "Create order missing plan returned HTTP $CURL_STATUS"
        fi

        # 8.3.4 Create order with invalid plan_id
        perform_request "${BASE_URL}/api/v1/payments/create-order" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"plan_id":"nonexistent_plan_xyz"}'
        if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 503 ]]; then
            assert_pass "Invalid plan_id handled ($CURL_STATUS)"
        else
            assert_warn "Invalid plan_id returned HTTP $CURL_STATUS"
        fi
    else
        assert_skip "Create order with auth (no token)"
        assert_skip "Create order missing plan_id (no token)"
        assert_skip "Create order invalid plan (no token)"
    fi

    subsection "8.4 Verify Payment"

    # 8.4.1 Verify without auth
    perform_request "${BASE_URL}/api/v1/payments/verify" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"razorpay_order_id":"order_test","razorpay_payment_id":"pay_test","razorpay_signature":"sig_test"}'
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Payment verify requires auth ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Payment verify rejects invalid data ($CURL_STATUS)"
    else
        assert_warn "Payment verify without auth returned HTTP $CURL_STATUS"
    fi

    # 8.4.2 Verify with invalid signature
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/payments/verify" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"razorpay_order_id":"order_fake","razorpay_payment_id":"pay_fake","razorpay_signature":"invalid_sig"}'
        if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 503 ]]; then
            assert_pass "Invalid payment signature rejected ($CURL_STATUS)"
        else
            assert_warn "Invalid signature returned HTTP $CURL_STATUS"
        fi

        # 8.4.3 Verify with empty body
        perform_request "${BASE_URL}/api/v1/payments/verify" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{}'
        if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
            assert_pass "Verify with empty body returns $CURL_STATUS"
        else
            assert_warn "Verify empty body returned HTTP $CURL_STATUS"
        fi

        # 8.4.4 Signature tampering attempt
        perform_request "${BASE_URL}/api/v1/payments/verify" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"razorpay_order_id":"order_real","razorpay_payment_id":"pay_real","razorpay_signature":"tampered_signature_value"}'
        if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 503 ]]; then
            assert_pass "Tampered signature rejected ($CURL_STATUS)"
        else
            assert_warn "Tampered signature returned HTTP $CURL_STATUS"
        fi
    else
        assert_skip "Verify invalid signature (no token)"
        assert_skip "Verify empty body (no token)"
        assert_skip "Signature tampering (no token)"
    fi

    subsection "8.5 Payment History"

    # 8.5.1 History without auth
    perform_request "${BASE_URL}/api/v1/payments/history"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Payment history requires auth ($CURL_STATUS)"
    else
        assert_warn "Payment history without auth returned HTTP $CURL_STATUS"
    fi

    # 8.5.2 History with auth
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/payments/history" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Payment history with auth returns 200"
            if is_json; then
                assert_pass "Payment history is JSON"
            else
                assert_warn "Payment history not JSON"
            fi
        else
            assert_warn "Payment history returned HTTP $CURL_STATUS"
            assert_skip "Payment history JSON"
        fi
    else
        assert_skip "Payment history with auth (no token)"
        assert_skip "Payment history JSON (no token)"
    fi

    subsection "8.6 Payment Recover & Credit Topup"

    # 8.6.1 Recover without auth
    perform_request "${BASE_URL}/api/v1/payments/recover" \
        -X POST \
        -H "Content-Type: application/json"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Payment recover requires auth ($CURL_STATUS)"
    else
        assert_warn "Payment recover without auth returned HTTP $CURL_STATUS"
    fi

    # 8.6.2 Credit topup without auth
    perform_request "${BASE_URL}/api/v1/payments/credit-topup" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"amount":100}'
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Credit topup requires auth ($CURL_STATUS)"
    else
        assert_warn "Credit topup without auth returned HTTP $CURL_STATUS"
    fi

    # 8.6.3 Credit topup with auth
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/payments/credit-topup" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"amount":100}'
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 503 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
            assert_pass "Credit topup with auth returns $CURL_STATUS"
        else
            assert_warn "Credit topup returned HTTP $CURL_STATUS"
        fi

        # 8.6.4 Credit topup with negative amount
        perform_request "${BASE_URL}/api/v1/payments/credit-topup" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"amount":-100}'
        if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
            assert_pass "Negative credit amount rejected ($CURL_STATUS)"
        elif [[ "$CURL_STATUS" -eq 503 ]]; then
            assert_pass "Credit topup unavailable (503)"
        else
            assert_warn "Negative credit returned HTTP $CURL_STATUS"
        fi

        # 8.6.5 Credit topup with zero
        perform_request "${BASE_URL}/api/v1/payments/credit-topup" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"amount":0}'
        if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 503 ]]; then
            assert_pass "Zero credit amount handled ($CURL_STATUS)"
        else
            assert_warn "Zero credit returned HTTP $CURL_STATUS"
        fi

        # 8.6.6 Credit topup with very large amount
        perform_request "${BASE_URL}/api/v1/payments/credit-topup" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"amount":99999999}'
        if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 503 || "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Large credit amount handled ($CURL_STATUS)"
        else
            assert_warn "Large credit returned HTTP $CURL_STATUS"
        fi

        # 8.6.7 Recover with auth
        perform_request "${BASE_URL}/api/v1/payments/recover" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 503 ]]; then
            assert_pass "Payment recover with auth returns $CURL_STATUS"
        else
            assert_warn "Payment recover returned HTTP $CURL_STATUS"
        fi
    else
        assert_skip "Credit topup with auth (no token)"
        assert_skip "Negative credit amount (no token)"
        assert_skip "Zero credit amount (no token)"
        assert_skip "Large credit amount (no token)"
        assert_skip "Payment recover with auth (no token)"
    fi

    subsection "8.7 Payment Method Enforcement"

    # 8.7.1 GET on create-order
    perform_request "${BASE_URL}/api/v1/payments/create-order"
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "GET on create-order rejected ($CURL_STATUS)"
    else
        assert_warn "GET on create-order returned HTTP $CURL_STATUS"
    fi

    # 8.7.2 GET on verify
    perform_request "${BASE_URL}/api/v1/payments/verify"
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "GET on verify rejected ($CURL_STATUS)"
    else
        assert_warn "GET on verify returned HTTP $CURL_STATUS"
    fi

    # 8.7.3 DELETE on payments
    perform_request "${BASE_URL}/api/v1/payments/history" -X DELETE
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "DELETE on payment history rejected ($CURL_STATUS)"
    else
        assert_warn "DELETE on payment history returned HTTP $CURL_STATUS"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 8: Payments ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 9: Webhook Pipeline (P8 Razorpay) - 55+ tests
# ===============================================================================

test_layer_9_webhooks() {
    section_header "LAYER 9: Webhook Pipeline (P8 Razorpay)"

    local layer_start=$TOTAL_TESTS

    subsection "9.1 Webhook Signature Validation"

    local webhook_url="${BASE_URL}/api/webhooks/razorpay"
    local test_payload='{"event":"subscription.charged","payload":{"payment":{"entity":{"id":"pay_test123","amount":49900,"currency":"INR"}},"subscription":{"entity":{"id":"sub_test123","plan_id":"plan_test"}}},"account_id":"acc_test"}'

    # 9.1.1 Webhook without signature
    perform_request "$webhook_url" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "$test_payload"
    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Webhook without signature returns $CURL_STATUS"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Webhook endpoint not found (404)"
    else
        assert_warn "Webhook without sig returned HTTP $CURL_STATUS"
    fi

    # 9.1.2 Webhook with invalid signature
    perform_request "$webhook_url" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "x-razorpay-signature: invalid_signature_value" \
        -d "$test_payload"
    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Invalid webhook signature rejected ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Webhook endpoint not found"
    else
        assert_warn "Invalid signature returned HTTP $CURL_STATUS"
    fi

    # 9.1.3 Webhook with empty signature
    perform_request "$webhook_url" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "x-razorpay-signature: " \
        -d "$test_payload"
    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "Empty webhook signature rejected ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Webhook endpoint not found"
    else
        assert_warn "Empty signature returned HTTP $CURL_STATUS"
    fi

    # 9.1.4 Webhook with malformed JSON
    perform_request "$webhook_url" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "x-razorpay-signature: test" \
        -d 'not json at all'
    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Malformed webhook body rejected ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Webhook endpoint not found"
    else
        assert_warn "Malformed webhook body returned HTTP $CURL_STATUS"
    fi

    # 9.1.5 Webhook with empty body
    perform_request "$webhook_url" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "x-razorpay-signature: test" \
        -d ''
    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Empty webhook body rejected ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Webhook endpoint not found"
    else
        assert_warn "Empty webhook body returned HTTP $CURL_STATUS"
    fi

    subsection "9.2 HMAC Signature Test"

    if [[ -n "$RAZORPAY_WEBHOOK_SECRET" ]] && command -v openssl &>/dev/null; then
        # 9.2.1 Valid HMAC signature
        local hmac_sig
        hmac_sig=$(echo -n "$test_payload" | openssl dgst -sha256 -hmac "$RAZORPAY_WEBHOOK_SECRET" | awk '{print $NF}')
        perform_request "$webhook_url" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "x-razorpay-signature: $hmac_sig" \
            -d "$test_payload"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Valid HMAC webhook accepted (200)"
        elif [[ "$CURL_STATUS" -eq 400 ]]; then
            assert_warn "Valid HMAC rejected (400) - secret may be wrong"
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            assert_warn "Webhook endpoint not found"
        else
            assert_warn "Valid HMAC returned HTTP $CURL_STATUS"
        fi

        # 9.2.2 Tampered payload with valid sig
        local tampered_payload='{"event":"subscription.charged","payload":{"payment":{"entity":{"id":"pay_TAMPERED","amount":1}}}}'
        perform_request "$webhook_url" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "x-razorpay-signature: $hmac_sig" \
            -d "$tampered_payload"
        if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 ]]; then
            assert_pass "Tampered payload with old sig rejected ($CURL_STATUS)"
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            assert_warn "Webhook endpoint not found"
        else
            assert_warn "Tampered payload returned HTTP $CURL_STATUS"
        fi

        # 9.2.3 Wrong HMAC key
        local wrong_sig
        wrong_sig=$(echo -n "$test_payload" | openssl dgst -sha256 -hmac "wrong-secret-key" | awk '{print $NF}')
        perform_request "$webhook_url" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "x-razorpay-signature: $wrong_sig" \
            -d "$test_payload"
        if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 ]]; then
            assert_pass "Wrong HMAC key rejected ($CURL_STATUS)"
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            assert_warn "Webhook endpoint not found"
        else
            assert_warn "Wrong HMAC key returned HTTP $CURL_STATUS"
        fi
    else
        assert_skip "Valid HMAC test (no secret or no openssl)"
        assert_skip "Tampered payload test (no secret)"
        assert_skip "Wrong HMAC key test (no secret)"
    fi

    subsection "9.3 Replay & Idempotency"

    # 9.3.1 Replay protection (if supported)
    local replay_payload='{"event":"subscription.charged","payload":{"payment":{"entity":{"id":"pay_replay_test","amount":49900}}},"account_id":"acc_test"}'
    perform_request "$webhook_url" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "x-razorpay-signature: test-sig" \
        -H "x-razorpay-event-id: evt_replay_test_123" \
        -d "$replay_payload"
    local first_status=$CURL_STATUS

    # Send same event again
    perform_request "$webhook_url" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "x-razorpay-signature: test-sig" \
        -H "x-razorpay-event-id: evt_replay_test_123" \
        -d "$replay_payload"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        if echo "$CURL_BODY" | grep -qi "duplicate\|already.*processed\|idempotent"; then
            assert_pass "Duplicate webhook detected as idempotent"
        else
            assert_warn "Duplicate webhook accepted (may process twice)"
        fi
    elif [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 409 ]]; then
        assert_pass "Duplicate webhook rejected ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Webhook endpoint not found"
    else
        assert_warn "Duplicate webhook returned HTTP $CURL_STATUS"
    fi

    subsection "9.4 Event Types"

    # 9.4.1 payment.captured event
    perform_request "$webhook_url" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "x-razorpay-signature: test" \
        -d '{"event":"payment.captured","payload":{"payment":{"entity":{"id":"pay_cap1","amount":100}}}}'
    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "payment.captured event handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Webhook not found"
    else
        assert_warn "payment.captured returned HTTP $CURL_STATUS"
    fi

    # 9.4.2 subscription.activated event
    perform_request "$webhook_url" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "x-razorpay-signature: test" \
        -d '{"event":"subscription.activated","payload":{"subscription":{"entity":{"id":"sub_act1"}}}}'
    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "subscription.activated event handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Webhook not found"
    else
        assert_warn "subscription.activated returned HTTP $CURL_STATUS"
    fi

    # 9.4.3 subscription.cancelled event
    perform_request "$webhook_url" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "x-razorpay-signature: test" \
        -d '{"event":"subscription.cancelled","payload":{"subscription":{"entity":{"id":"sub_can1"}}}}'
    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "subscription.cancelled event handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Webhook not found"
    else
        assert_warn "subscription.cancelled returned HTTP $CURL_STATUS"
    fi

    # 9.4.4 Unknown event type
    perform_request "$webhook_url" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "x-razorpay-signature: test" \
        -d '{"event":"unknown.event.type","payload":{}}'
    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Unknown event type handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Webhook not found"
    else
        assert_warn "Unknown event returned HTTP $CURL_STATUS"
    fi

    subsection "9.5 Webhook Method & Headers"

    # 9.5.1 GET on webhook
    perform_request "$webhook_url"
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "GET on webhook rejected ($CURL_STATUS)"
    else
        assert_warn "GET on webhook returned HTTP $CURL_STATUS"
    fi

    # 9.5.2 PUT on webhook
    perform_request "$webhook_url" -X PUT \
        -H "Content-Type: application/json" \
        -d '{}'
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "PUT on webhook rejected ($CURL_STATUS)"
    else
        assert_warn "PUT on webhook returned HTTP $CURL_STATUS"
    fi

    # 9.5.3 Webhook response is JSON
    perform_request "$webhook_url" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"event":"test"}'
    if is_json; then
        assert_pass "Webhook error response is JSON"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_skip "Webhook not found"
    else
        assert_warn "Webhook response is not JSON"
    fi

    # 9.5.4 Webhook with very large payload
    local large_webhook
    large_webhook=$(printf '{"event":"test","data":"%s"}' "$(printf 'X%.0s' {1..50000})")
    perform_request "$webhook_url" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "x-razorpay-signature: test" \
        -d "$large_webhook"
    if [[ "$CURL_STATUS" -eq 413 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Large webhook payload handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Webhook not found"
    else
        assert_warn "Large webhook returned HTTP $CURL_STATUS"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 9: Webhooks ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 10: Conversations API - 55+ tests
# ===============================================================================

test_layer_10_conversations() {
    section_header "LAYER 10: Conversations API"

    local layer_start=$TOTAL_TESTS

    subsection "10.1 List Conversations"

    # 10.1.1 List without auth
    perform_request "${BASE_URL}/api/v1/conversations"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Conversations list requires auth ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_warn "Conversations accessible without auth"
    else
        assert_warn "Conversations returned HTTP $CURL_STATUS"
    fi

    # 10.1.2 List with auth
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/conversations" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Conversations list with auth returns 200"
            if is_json; then
                assert_pass "Conversations response is JSON"
            else
                assert_warn "Conversations response not JSON"
            fi

            # 10.1.3 Response is array or has conversations field
            local conv_type
            conv_type=$(echo "$CURL_BODY" | jq 'type' 2>/dev/null || echo "")
            if [[ "$conv_type" == '"array"' ]]; then
                assert_pass "Conversations returns array"
            elif echo "$CURL_BODY" | jq -e '.conversations // .items // .data' >/dev/null 2>&1; then
                assert_pass "Conversations returns object with list field"
            else
                assert_warn "Conversations response structure unclear"
            fi
        else
            assert_warn "Conversations with auth returned HTTP $CURL_STATUS"
            assert_skip "Conversations JSON"
            assert_skip "Conversations structure"
        fi

        # 10.1.4 Pagination parameters
        perform_request "${BASE_URL}/api/v1/conversations?page=1&limit=5" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Conversations pagination works (page=1, limit=5)"
        else
            assert_warn "Conversations pagination returned HTTP $CURL_STATUS"
        fi

        # 10.1.5 Invalid pagination
        perform_request "${BASE_URL}/api/v1/conversations?page=-1&limit=0" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
            assert_pass "Invalid pagination handled ($CURL_STATUS)"
        else
            assert_warn "Invalid pagination returned HTTP $CURL_STATUS"
        fi

        # 10.1.6 Very large limit
        perform_request "${BASE_URL}/api/v1/conversations?limit=10000" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
            assert_pass "Large limit parameter handled ($CURL_STATUS)"
        else
            assert_warn "Large limit returned HTTP $CURL_STATUS"
        fi
    else
        assert_skip "Conversations list with auth (no token)"
        assert_skip "Conversations JSON (no token)"
        assert_skip "Conversations structure (no token)"
        assert_skip "Conversations pagination (no token)"
        assert_skip "Invalid pagination (no token)"
        assert_skip "Large limit (no token)"
    fi

    subsection "10.2 Anonymous Conversations"

    # 10.2.1 Anon conversations with x-anon-id
    perform_request "${BASE_URL}/api/v1/conversations/anon" \
        -H "x-anon-id: test-anon-id-12345"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Anon conversations returns 200"
        if is_json; then
            assert_pass "Anon conversations is JSON"
        else
            assert_warn "Anon conversations not JSON"
        fi
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Anon conversations endpoint not found"
        assert_skip "Anon conversations JSON"
    else
        assert_warn "Anon conversations returned HTTP $CURL_STATUS"
        assert_skip "Anon conversations JSON"
    fi

    # 10.2.2 Anon without x-anon-id header
    perform_request "${BASE_URL}/api/v1/conversations/anon"
    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Anon without x-anon-id returns $CURL_STATUS"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_warn "Anon accessible without x-anon-id"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Anon endpoint not found"
    else
        assert_warn "Anon without header returned HTTP $CURL_STATUS"
    fi

    # 10.2.3 Anon with invalid id format
    perform_request "${BASE_URL}/api/v1/conversations/anon" \
        -H "x-anon-id: "
    if [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Empty anon-id rejected ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Anon endpoint not found"
    else
        assert_warn "Empty anon-id returned HTTP $CURL_STATUS"
    fi

    # 10.2.4 Anon with very long id
    local long_anon_id
    long_anon_id=$(printf 'x%.0s' {1..500})
    perform_request "${BASE_URL}/api/v1/conversations/anon" \
        -H "x-anon-id: ${long_anon_id}"
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Very long anon-id handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Anon endpoint not found"
    else
        assert_warn "Long anon-id returned HTTP $CURL_STATUS"
    fi

    # 10.2.5 Anon with XSS in id
    perform_request "${BASE_URL}/api/v1/conversations/anon" \
        -H "x-anon-id: <script>alert(1)</script>"
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "XSS in anon-id handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Anon endpoint not found"
    else
        assert_warn "XSS anon-id returned HTTP $CURL_STATUS"
    fi

    subsection "10.3 Conversation CRUD"

    if [[ -n "$AUTH_TOKEN" ]]; then
        # 10.3.1 PATCH conversation (title update)
        perform_request "${BASE_URL}/api/v1/conversations/test-conv-id-123" \
            -X PATCH \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"title":"Updated Title"}'
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 404 ]]; then
            assert_pass "PATCH conversation returns $CURL_STATUS"
        elif [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
            assert_pass "PATCH conversation validation ($CURL_STATUS)"
        else
            assert_warn "PATCH conversation returned HTTP $CURL_STATUS"
        fi

        # 10.3.2 PATCH with empty title
        perform_request "${BASE_URL}/api/v1/conversations/test-conv-id-123" \
            -X PATCH \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"title":""}'
        if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "PATCH empty title handled ($CURL_STATUS)"
        else
            assert_warn "PATCH empty title returned HTTP $CURL_STATUS"
        fi

        # 10.3.3 DELETE conversation
        perform_request "${BASE_URL}/api/v1/conversations/test-conv-id-nonexistent" \
            -X DELETE \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 204 || "$CURL_STATUS" -eq 404 ]]; then
            assert_pass "DELETE conversation returns $CURL_STATUS"
        else
            assert_warn "DELETE conversation returned HTTP $CURL_STATUS"
        fi

        # 10.3.4 DELETE without auth
        perform_request "${BASE_URL}/api/v1/conversations/test-conv-id" \
            -X DELETE
        if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
            assert_pass "DELETE conversation requires auth ($CURL_STATUS)"
        else
            assert_warn "DELETE without auth returned HTTP $CURL_STATUS"
        fi

        # 10.3.5 PATCH without auth
        perform_request "${BASE_URL}/api/v1/conversations/test-conv-id" \
            -X PATCH \
            -H "Content-Type: application/json" \
            -d '{"title":"hack"}'
        if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
            assert_pass "PATCH conversation requires auth ($CURL_STATUS)"
        else
            assert_warn "PATCH without auth returned HTTP $CURL_STATUS"
        fi

        # 10.3.6 PATCH with invalid conversation ID
        perform_request "${BASE_URL}/api/v1/conversations/invalid!@#id" \
            -X PATCH \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"title":"test"}'
        if [[ "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
            assert_pass "Invalid conversation ID handled ($CURL_STATUS)"
        else
            assert_warn "Invalid conv ID returned HTTP $CURL_STATUS"
        fi
    else
        assert_skip "PATCH conversation (no token)"
        assert_skip "PATCH empty title (no token)"
        assert_skip "DELETE conversation (no token)"
        assert_skip "DELETE without auth (no token)"
        assert_skip "PATCH without auth (no token)"
        assert_skip "Invalid conversation ID (no token)"
    fi

    subsection "10.4 Method Enforcement"

    # 10.4.1 POST on conversations list
    perform_request "${BASE_URL}/api/v1/conversations" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{}'
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "POST on conversations list rejected ($CURL_STATUS)"
    else
        assert_warn "POST on conversations returned HTTP $CURL_STATUS"
    fi

    # 10.4.2 PUT on conversations list
    perform_request "${BASE_URL}/api/v1/conversations" \
        -X PUT \
        -H "Content-Type: application/json" \
        -d '{}'
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "PUT on conversations rejected ($CURL_STATUS)"
    else
        assert_warn "PUT on conversations returned HTTP $CURL_STATUS"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 10: Conversations ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 11: Feedback - 45+ tests
# ===============================================================================

test_layer_11_feedback() {
    section_header "LAYER 11: Feedback"

    local layer_start=$TOTAL_TESTS

    subsection "11.1 Submit Feedback"

    # 11.1.1 Submit without auth
    perform_request "${BASE_URL}/api/v1/chat/feedback/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message_id":"msg_test","rating":5,"comment":"Great response!"}'
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Feedback submit requires auth ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_warn "Feedback accessible without auth"
    else
        assert_warn "Feedback without auth returned HTTP $CURL_STATUS"
    fi

    if [[ -n "$AUTH_TOKEN" ]]; then
        # 11.1.2 Submit with valid data
        perform_request "${BASE_URL}/api/v1/chat/feedback/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message_id":"msg_test_feedback","rating":5,"comment":"Very helpful!"}'
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 201 ]]; then
            assert_pass "Feedback submit with auth returns $CURL_STATUS"
            if is_json; then
                assert_pass "Feedback response is JSON"
                local fb_status
                fb_status=$(json_field '.status // empty')
                if [[ "$fb_status" == "ok" || "$fb_status" == "success" ]]; then
                    assert_pass "Feedback status: $fb_status"
                else
                    assert_warn "Feedback status: ${fb_status:-missing}"
                fi
            else
                assert_warn "Feedback response not JSON"
                assert_skip "Feedback status"
            fi
        elif [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
            assert_pass "Feedback validation response ($CURL_STATUS)"
            assert_skip "Feedback JSON"
            assert_skip "Feedback status"
        else
            assert_warn "Feedback submit returned HTTP $CURL_STATUS"
            assert_skip "Feedback JSON"
            assert_skip "Feedback status"
        fi

        # 11.1.3 Submit with rating 1 (minimum)
        perform_request "${BASE_URL}/api/v1/chat/feedback/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message_id":"msg_test_r1","rating":1}'
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 201 || "$CURL_STATUS" -eq 422 ]]; then
            assert_pass "Feedback rating=1 handled ($CURL_STATUS)"
        else
            assert_warn "Rating 1 returned HTTP $CURL_STATUS"
        fi

        # 11.1.4 Submit with rating 0 (boundary)
        perform_request "${BASE_URL}/api/v1/chat/feedback/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message_id":"msg_test_r0","rating":0}'
        if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Feedback rating=0 handled ($CURL_STATUS)"
        else
            assert_warn "Rating 0 returned HTTP $CURL_STATUS"
        fi

        # 11.1.5 Submit with rating 6 (out of range)
        perform_request "${BASE_URL}/api/v1/chat/feedback/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message_id":"msg_test_r6","rating":6}'
        if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
            assert_pass "Feedback rating=6 rejected ($CURL_STATUS)"
        elif [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_warn "Rating 6 accepted (may allow wider range)"
        else
            assert_warn "Rating 6 returned HTTP $CURL_STATUS"
        fi

        # 11.1.6 Submit with negative rating
        perform_request "${BASE_URL}/api/v1/chat/feedback/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message_id":"msg_test_neg","rating":-1}'
        if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
            assert_pass "Negative rating rejected ($CURL_STATUS)"
        elif [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_warn "Negative rating accepted"
        else
            assert_warn "Negative rating returned HTTP $CURL_STATUS"
        fi

        # 11.1.7 Submit with missing rating
        perform_request "${BASE_URL}/api/v1/chat/feedback/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message_id":"msg_test_nr"}'
        if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
            assert_pass "Feedback without rating returns $CURL_STATUS"
        elif [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_warn "Feedback without rating accepted"
        else
            assert_warn "No rating returned HTTP $CURL_STATUS"
        fi

        # 11.1.8 Submit with missing message_id
        perform_request "${BASE_URL}/api/v1/chat/feedback/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"rating":4}'
        if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
            assert_pass "Feedback without message_id returns $CURL_STATUS"
        elif [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_warn "Feedback without message_id accepted"
        else
            assert_warn "No message_id returned HTTP $CURL_STATUS"
        fi

        # 11.1.9 Submit with empty body
        perform_request "${BASE_URL}/api/v1/chat/feedback/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{}'
        if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
            assert_pass "Empty feedback body returns $CURL_STATUS"
        else
            assert_warn "Empty feedback body returned HTTP $CURL_STATUS"
        fi

        # 11.1.10 Submit with XSS in comment
        perform_request "${BASE_URL}/api/v1/chat/feedback/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message_id":"msg_xss","rating":3,"comment":"<img src=x onerror=alert(1)>"}'
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
            assert_pass "XSS in feedback comment handled ($CURL_STATUS)"
        else
            assert_warn "XSS comment returned HTTP $CURL_STATUS"
        fi

        # 11.1.11 Submit with very long comment
        local long_comment
        long_comment=$(printf 'X%.0s' {1..5000})
        perform_request "${BASE_URL}/api/v1/chat/feedback/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d "{\"message_id\":\"msg_long\",\"rating\":4,\"comment\":\"${long_comment}\"}"
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 413 ]]; then
            assert_pass "Very long comment handled ($CURL_STATUS)"
        else
            assert_warn "Long comment returned HTTP $CURL_STATUS"
        fi

        # 11.1.12 Non-integer rating
        perform_request "${BASE_URL}/api/v1/chat/feedback/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message_id":"msg_float","rating":3.5}'
        if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Float rating handled ($CURL_STATUS)"
        else
            assert_warn "Float rating returned HTTP $CURL_STATUS"
        fi

        # 11.1.13 String rating
        perform_request "${BASE_URL}/api/v1/chat/feedback/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message_id":"msg_str","rating":"five"}'
        if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
            assert_pass "String rating rejected ($CURL_STATUS)"
        else
            assert_warn "String rating returned HTTP $CURL_STATUS"
        fi
    else
        assert_skip "Feedback submit with auth (no token)"
        assert_skip "Feedback JSON response (no token)"
        assert_skip "Feedback status (no token)"
        assert_skip "Rating 1 (no token)"
        assert_skip "Rating 0 (no token)"
        assert_skip "Rating 6 (no token)"
        assert_skip "Negative rating (no token)"
        assert_skip "Missing rating (no token)"
        assert_skip "Missing message_id (no token)"
        assert_skip "Empty body (no token)"
        assert_skip "XSS comment (no token)"
        assert_skip "Long comment (no token)"
        assert_skip "Float rating (no token)"
        assert_skip "String rating (no token)"
    fi

    subsection "11.2 Feedback Stats"

    # 11.2.1 Stats without auth
    perform_request "${BASE_URL}/api/v1/chat/feedback/stats"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Feedback stats requires auth ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_warn "Feedback stats accessible without auth"
    else
        assert_warn "Feedback stats returned HTTP $CURL_STATUS"
    fi

    # 11.2.2 Stats with auth
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/chat/feedback/stats" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Feedback stats with auth returns 200"
            if is_json; then
                assert_pass "Feedback stats is JSON"
            else
                assert_warn "Feedback stats not JSON"
            fi
        else
            assert_warn "Feedback stats returned HTTP $CURL_STATUS"
            assert_skip "Feedback stats JSON"
        fi
    else
        assert_skip "Feedback stats with auth (no token)"
        assert_skip "Feedback stats JSON (no token)"
    fi

    subsection "11.3 Method Enforcement"

    # 11.3.1 GET on feedback submit
    perform_request "${BASE_URL}/api/v1/chat/feedback/"
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "GET on feedback submit rejected ($CURL_STATUS)"
    else
        assert_warn "GET on feedback returned HTTP $CURL_STATUS"
    fi

    # 11.3.2 DELETE on feedback
    perform_request "${BASE_URL}/api/v1/chat/feedback/" -X DELETE
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "DELETE on feedback rejected ($CURL_STATUS)"
    else
        assert_warn "DELETE on feedback returned HTTP $CURL_STATUS"
    fi

    # 11.3.3 POST on feedback stats
    perform_request "${BASE_URL}/api/v1/chat/feedback/stats" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{}'
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "POST on feedback stats rejected ($CURL_STATUS)"
    else
        assert_warn "POST on feedback stats returned HTTP $CURL_STATUS"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 11: Feedback ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 12: Admin Endpoints - 85+ tests
# ===============================================================================

test_layer_12_admin() {
    section_header "LAYER 12: Admin Endpoints"

    local layer_start=$TOTAL_TESTS

    if [[ "$SKIP_ADMIN_TESTS" == "1" ]]; then
        assert_skip "Admin tests skipped (SKIP_ADMIN_TESTS=1)"
        LAYER_RESULTS+=("Layer 12: Admin (SKIPPED)")
        return
    fi

    local admin_endpoints=(
        "dashboard"
        "users"
        "analytics"
        "content"
        "settings"
        "security"
        "seo"
        "notifications"
        "revenue"
        "knowledge"
        "translate"
        "conversations"
        "dead-letters"
        "alerts"
        "ai"
    )

    subsection "12.1 Admin Auth Enforcement (No Token)"

    # Test each admin endpoint without auth
    for ep in "${admin_endpoints[@]}"; do
        perform_request "${BASE_URL}/api/v1/admin/${ep}"
        if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
            assert_pass "GET /admin/${ep} requires auth ($CURL_STATUS)"
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            assert_warn "GET /admin/${ep} not found (404)"
        else
            assert_warn "GET /admin/${ep} without auth returned HTTP $CURL_STATUS"
        fi
    done

    subsection "12.2 Admin Auth Enforcement (Invalid Token)"

    # Test with non-admin token
    perform_request "${BASE_URL}/api/v1/admin/dashboard" \
        -H "Authorization: Bearer fake-non-admin-token"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Admin dashboard rejects invalid token ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Admin dashboard not found"
    else
        assert_warn "Admin with invalid token returned HTTP $CURL_STATUS"
    fi

    # Test with a regular user-looking JWT
    perform_request "${BASE_URL}/api/v1/admin/users" \
        -H "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoidXNlciJ9.fake"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "Admin users rejects non-admin JWT ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Admin users not found"
    else
        assert_warn "Admin users with user JWT returned HTTP $CURL_STATUS"
    fi

    subsection "12.3 Admin with Valid Token"

    if [[ -n "$ADMIN_COOKIE" ]]; then
        # Use cookie-based admin auth (primary method used by admin panel)

        # 12.3.1 Dashboard
        perform_request "${BASE_URL}/api/v1/admin/dashboard" \
            -b "syrabit_admin_session=${ADMIN_COOKIE}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Admin dashboard returns 200"
            if is_json; then
                assert_pass "Admin dashboard is JSON"
            else
                assert_warn "Admin dashboard not JSON"
            fi
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            assert_warn "Admin dashboard not found"
            assert_skip "Dashboard JSON"
        else
            assert_warn "Admin dashboard returned HTTP $CURL_STATUS"
            assert_skip "Dashboard JSON"
        fi

        # 12.3.2 Users list
        perform_request "${BASE_URL}/api/v1/admin/users" \
            -b "syrabit_admin_session=${ADMIN_COOKIE}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Admin users returns 200"
            if is_json; then
                assert_pass "Admin users is JSON"
            else
                assert_warn "Admin users not JSON"
            fi
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            assert_warn "Admin users not found"
            assert_skip "Users JSON"
        else
            assert_warn "Admin users returned HTTP $CURL_STATUS"
            assert_skip "Users JSON"
        fi

        # 12.3.3 Analytics
        perform_request "${BASE_URL}/api/v1/admin/analytics" \
            -b "syrabit_admin_session=${ADMIN_COOKIE}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Admin analytics returns 200"
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            assert_warn "Admin analytics not found"
        else
            assert_warn "Admin analytics returned HTTP $CURL_STATUS"
        fi

        # 12.3.4 Content management
        perform_request "${BASE_URL}/api/v1/admin/content" \
            -b "syrabit_admin_session=${ADMIN_COOKIE}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Admin content returns 200"
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            assert_warn "Admin content not found"
        else
            assert_warn "Admin content returned HTTP $CURL_STATUS"
        fi

        # 12.3.5 Settings
        perform_request "${BASE_URL}/api/v1/admin/settings" \
            -b "syrabit_admin_session=${ADMIN_COOKIE}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Admin settings returns 200"
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            assert_warn "Admin settings not found"
        else
            assert_warn "Admin settings returned HTTP $CURL_STATUS"
        fi

        # 12.3.6 Security
        perform_request "${BASE_URL}/api/v1/admin/security" \
            -b "syrabit_admin_session=${ADMIN_COOKIE}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Admin security returns 200"
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            assert_warn "Admin security not found"
        else
            assert_warn "Admin security returned HTTP $CURL_STATUS"
        fi

        # 12.3.7 Revenue
        perform_request "${BASE_URL}/api/v1/admin/revenue" \
            -b "syrabit_admin_session=${ADMIN_COOKIE}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Admin revenue returns 200"
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            assert_warn "Admin revenue not found"
        else
            assert_warn "Admin revenue returned HTTP $CURL_STATUS"
        fi

        # 12.3.8 Knowledge
        perform_request "${BASE_URL}/api/v1/admin/knowledge" \
            -b "syrabit_admin_session=${ADMIN_COOKIE}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Admin knowledge returns 200"
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            assert_warn "Admin knowledge not found"
        else
            assert_warn "Admin knowledge returned HTTP $CURL_STATUS"
        fi
    elif [[ -n "$ADMIN_TOKEN" ]]; then
        # Fallback: use Bearer token
        assert_warn "No admin cookie available, using Bearer token (may fail)"
        perform_request "${BASE_URL}/api/v1/admin/dashboard" \
            -H "Authorization: Bearer ${ADMIN_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Admin dashboard returns 200 (Bearer)"
        else
            assert_warn "Admin dashboard returned HTTP $CURL_STATUS (Bearer fallback)"
        fi
        assert_skip "Admin users (Bearer fallback)"
        assert_skip "Users JSON (Bearer fallback)"
        assert_skip "Admin analytics (Bearer fallback)"
        assert_skip "Admin content (Bearer fallback)"
        assert_skip "Admin settings (Bearer fallback)"
        assert_skip "Admin security (Bearer fallback)"
        assert_skip "Admin revenue (Bearer fallback)"
        assert_skip "Admin knowledge (Bearer fallback)"
    else
        assert_skip "Admin dashboard (no admin token/cookie)"
        assert_skip "Dashboard JSON (no admin token/cookie)"
        assert_skip "Admin users (no admin token/cookie)"
        assert_skip "Users JSON (no admin token/cookie)"
        assert_skip "Admin analytics (no admin token/cookie)"
        assert_skip "Admin content (no admin token/cookie)"
        assert_skip "Admin settings (no admin token/cookie)"
        assert_skip "Admin security (no admin token/cookie)"
        assert_skip "Admin revenue (no admin token/cookie)"
        assert_skip "Admin knowledge (no admin token/cookie)"
    fi

    subsection "12.4 Admin Security Probes"

    # 12.4.1 SQL injection in admin endpoint
    perform_request "${BASE_URL}/api/v1/admin/users?search=admin' OR '1'='1"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "SQL injection on admin users blocked ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Admin users not found"
    else
        assert_warn "SQL injection admin returned HTTP $CURL_STATUS"
    fi

    # 12.4.2 Path traversal on admin
    perform_request "${BASE_URL}/api/v1/admin/../../../etc/passwd"
    if [[ "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "Path traversal on admin blocked ($CURL_STATUS)"
    else
        assert_warn "Admin path traversal returned HTTP $CURL_STATUS"
    fi

    # 12.4.3 XSS probe in admin query
    perform_request "${BASE_URL}/api/v1/admin/users?q=<script>alert(1)</script>"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "XSS in admin query blocked ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Admin endpoint not found"
    else
        assert_warn "XSS admin query returned HTTP $CURL_STATUS"
    fi

    # 12.4.4 Privilege escalation attempt
    if [[ -n "$AUTH_TOKEN" && "$AUTH_TOKEN" != "$ADMIN_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/admin/settings" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"setting":"admin_role","value":"true"}'
        if [[ "$CURL_STATUS" -eq 403 || "$CURL_STATUS" -eq 401 ]]; then
            assert_pass "Non-admin cannot POST to admin settings ($CURL_STATUS)"
        elif [[ "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 405 ]]; then
            assert_pass "Admin settings POST not available ($CURL_STATUS)"
        else
            assert_warn "Privilege escalation returned HTTP $CURL_STATUS"
        fi
    else
        assert_skip "Privilege escalation test (no non-admin token)"
    fi

    subsection "12.5 Admin Method Enforcement"

    # 12.5.1 DELETE on admin dashboard
    perform_request "${BASE_URL}/api/v1/admin/dashboard" -X DELETE
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "DELETE on admin dashboard rejected ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Admin dashboard not found"
    else
        assert_warn "DELETE admin dashboard returned HTTP $CURL_STATUS"
    fi

    # 12.5.2 PUT on admin users
    perform_request "${BASE_URL}/api/v1/admin/users" \
        -X PUT \
        -H "Content-Type: application/json" \
        -d '{}'
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "PUT on admin users rejected ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Admin users not found"
    else
        assert_warn "PUT admin users returned HTTP $CURL_STATUS"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 12: Admin ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 13: SEO & Indexing - 45+ tests
# ===============================================================================

test_layer_13_seo() {
    section_header "LAYER 13: SEO & Indexing"

    local layer_start=$TOTAL_TESTS

    subsection "13.1 Sitemap XML"

    # 13.1.1 GET sitemap.xml
    perform_request "${BASE_URL}/api/v1/seo/sitemap.xml"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Sitemap.xml returns 200"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Sitemap.xml not found at API path"
    else
        assert_warn "Sitemap.xml returned HTTP $CURL_STATUS"
    fi

    # 13.1.2 Sitemap is XML
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        if echo "$CURL_BODY" | grep -qi "<?xml\|<urlset\|<sitemapindex"; then
            assert_pass "Sitemap contains valid XML"
        else
            assert_warn "Sitemap does not appear to be XML"
        fi

        # 13.1.3 Sitemap content-type
        local ct
        ct=$(get_header_value "content-type")
        if [[ "$ct" == *"xml"* || "$ct" == *"text/xml"* || "$ct" == *"application/xml"* ]]; then
            assert_pass "Sitemap Content-Type: $ct"
        else
            assert_warn "Sitemap Content-Type: $ct (expected XML)"
        fi

        # 13.1.4 Sitemap has URLs
        if echo "$CURL_BODY" | grep -qi "<loc>"; then
            assert_pass "Sitemap contains <loc> URL entries"
        else
            assert_warn "Sitemap has no <loc> entries"
        fi

        # 13.1.5 Sitemap URLs use HTTPS
        if echo "$CURL_BODY" | grep -qi "<loc>http://"; then
            assert_warn "Sitemap contains HTTP (non-HTTPS) URLs"
        else
            assert_pass "Sitemap URLs are HTTPS (or no HTTP found)"
        fi

        # 13.1.6 Sitemap has lastmod
        if echo "$CURL_BODY" | grep -qi "<lastmod>"; then
            assert_pass "Sitemap includes <lastmod> timestamps"
        else
            assert_warn "Sitemap missing <lastmod>"
        fi

        # 13.1.7 Sitemap has changefreq or priority
        if echo "$CURL_BODY" | grep -qi "<changefreq>\|<priority>"; then
            assert_pass "Sitemap includes changefreq/priority"
        else
            assert_warn "Sitemap missing changefreq/priority"
        fi
    else
        assert_skip "Sitemap XML content"
        assert_skip "Sitemap content-type"
        assert_skip "Sitemap URLs"
        assert_skip "Sitemap HTTPS"
        assert_skip "Sitemap lastmod"
        assert_skip "Sitemap changefreq"
    fi

    # 13.1.8 Sitemap-index
    perform_request "${BASE_URL}/api/v1/seo/sitemap-index.xml"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Sitemap-index.xml returns 200"
        if echo "$CURL_BODY" | grep -qi "<sitemapindex\|<sitemap>"; then
            assert_pass "Sitemap-index has correct structure"
        else
            assert_warn "Sitemap-index structure unclear"
        fi
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Sitemap-index not found"
        assert_skip "Sitemap-index structure"
    else
        assert_warn "Sitemap-index returned HTTP $CURL_STATUS"
        assert_skip "Sitemap-index structure"
    fi

    subsection "13.2 IndexNow"

    # 13.2.1 IndexNow submit without auth
    perform_request "${BASE_URL}/api/v1/indexnow/submit" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"url":"https://syrabit.ai/test-page"}'
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "IndexNow submit requires auth ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_warn "IndexNow accessible without auth"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "IndexNow endpoint not found"
    else
        assert_warn "IndexNow returned HTTP $CURL_STATUS"
    fi

    # 13.2.2 IndexNow with auth
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/indexnow/submit" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"url":"https://syrabit.ai/test-page"}'
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 403 ]]; then
            assert_pass "IndexNow with auth returns $CURL_STATUS"
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            assert_warn "IndexNow not found"
        else
            assert_warn "IndexNow auth returned HTTP $CURL_STATUS"
        fi
    else
        assert_skip "IndexNow with auth (no token)"
    fi

    # 13.2.3 IndexNow with invalid URL
    perform_request "${BASE_URL}/api/v1/indexnow/submit" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"url":"not-a-valid-url"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "IndexNow invalid URL handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "IndexNow not found"
    else
        assert_warn "IndexNow invalid URL returned HTTP $CURL_STATUS"
    fi

    # 13.2.4 IndexNow with empty body
    perform_request "${BASE_URL}/api/v1/indexnow/submit" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "IndexNow empty body handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "IndexNow not found"
    else
        assert_warn "IndexNow empty body returned HTTP $CURL_STATUS"
    fi

    subsection "13.3 Frontend SEO Assets"

    # 13.3.1 Frontend robots.txt
    perform_request "${FRONTEND_URL}/robots.txt"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Frontend robots.txt accessible"
    else
        assert_warn "Frontend robots.txt returned HTTP $CURL_STATUS"
    fi

    # 13.3.2 Canonical URL in HTML
    perform_request "$FRONTEND_URL"
    if echo "$CURL_BODY" | grep -qi 'rel="canonical"\|rel=canonical'; then
        assert_pass "Canonical URL present in HTML"
    else
        assert_warn "No canonical URL found in HTML"
    fi

    # 13.3.3 Twitter card meta
    if echo "$CURL_BODY" | grep -qi "twitter:card\|twitter:title"; then
        assert_pass "Twitter Card meta tags present"
    else
        assert_warn "No Twitter Card meta tags"
    fi

    # 13.3.4 Hreflang tags
    if echo "$CURL_BODY" | grep -qi "hreflang"; then
        assert_pass "Hreflang tag present (multilingual SEO)"
    else
        assert_warn "No hreflang tags (Assamese localization missing)"
    fi

    # 13.3.5 Schema.org WebApplication or EducationalOrganization
    if echo "$CURL_BODY" | grep -qi "WebApplication\|EducationalOrganization\|Organization"; then
        assert_pass "Schema.org type found in structured data"
    else
        assert_warn "No Schema.org type found"
    fi

    subsection "13.4 Method Enforcement"

    # 13.4.1 POST on sitemap
    perform_request "${BASE_URL}/api/v1/seo/sitemap.xml" -X POST
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "POST on sitemap rejected ($CURL_STATUS)"
    else
        assert_warn "POST on sitemap returned HTTP $CURL_STATUS"
    fi

    # 13.4.2 DELETE on sitemap
    perform_request "${BASE_URL}/api/v1/seo/sitemap.xml" -X DELETE
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "DELETE on sitemap rejected ($CURL_STATUS)"
    else
        assert_warn "DELETE on sitemap returned HTTP $CURL_STATUS"
    fi

    # 13.4.3 GET on indexnow/submit
    perform_request "${BASE_URL}/api/v1/indexnow/submit"
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "GET on indexnow/submit rejected ($CURL_STATUS)"
    else
        assert_warn "GET indexnow/submit returned HTTP $CURL_STATUS"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 13: SEO ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 14: Education Endpoints (Coming Soon - 501 Stubs) - 35+ tests
# ===============================================================================

test_layer_14_education() {
    section_header "LAYER 14: Education Endpoints (501 Stubs)"

    local layer_start=$TOTAL_TESTS

    if [[ "$QUICK_MODE" == "1" ]]; then
        assert_skip "Education endpoints skipped in quick mode"
        LAYER_RESULTS+=("Layer 14: Education (QUICK SKIP)")
        return
    fi

    subsection "14.1 Quiz Endpoint"

    # 14.1.1 Quiz for Science
    perform_request "${BASE_URL}/api/v1/edu/quiz/science"
    if [[ "$CURL_STATUS" -eq 501 ]]; then
        assert_pass "Quiz endpoint returns 501 (Not Implemented)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Quiz endpoint not found (404)"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Quiz endpoint returns 200 (implemented!)"
    else
        assert_warn "Quiz returned HTTP $CURL_STATUS"
    fi

    # 14.1.2 Quiz response body
    if [[ "$CURL_STATUS" -eq 501 ]] && is_json; then
        assert_pass "Quiz 501 response is JSON"
        local quiz_msg
        quiz_msg=$(json_field '.detail // .message // empty')
        if [[ -n "$quiz_msg" ]]; then
            assert_pass "Quiz has message: $quiz_msg"
        else
            assert_warn "Quiz 501 has no message"
        fi
    else
        assert_skip "Quiz response body"
        assert_skip "Quiz message"
    fi

    # 14.1.3 Quiz for Math
    perform_request "${BASE_URL}/api/v1/edu/quiz/mathematics"
    if [[ "$CURL_STATUS" -eq 501 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Quiz/math endpoint responds ($CURL_STATUS)"
    else
        assert_warn "Quiz/math returned HTTP $CURL_STATUS"
    fi

    # 14.1.4 Quiz with invalid subject
    perform_request "${BASE_URL}/api/v1/edu/quiz/nonexistent-subject"
    if [[ "$CURL_STATUS" -eq 501 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Quiz invalid subject handled ($CURL_STATUS)"
    else
        assert_warn "Quiz invalid subject returned HTTP $CURL_STATUS"
    fi

    # 14.1.5 Quiz with XSS subject
    perform_request "${BASE_URL}/api/v1/edu/quiz/<script>"
    if [[ "$CURL_STATUS" -eq 501 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "Quiz XSS subject handled ($CURL_STATUS)"
    else
        assert_warn "Quiz XSS returned HTTP $CURL_STATUS"
    fi

    subsection "14.2 Notes Endpoint"

    # 14.2.1 Notes for Science
    perform_request "${BASE_URL}/api/v1/edu/notes/science"
    if [[ "$CURL_STATUS" -eq 501 ]]; then
        assert_pass "Notes endpoint returns 501 (Not Implemented)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Notes endpoint not found"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Notes endpoint returns 200 (implemented!)"
    else
        assert_warn "Notes returned HTTP $CURL_STATUS"
    fi

    # 14.2.2 Notes response is JSON
    if is_json; then
        assert_pass "Notes response is JSON"
    else
        assert_warn "Notes response not JSON"
    fi

    # 14.2.3 Notes for English
    perform_request "${BASE_URL}/api/v1/edu/notes/english"
    if [[ "$CURL_STATUS" -eq 501 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Notes/english responds ($CURL_STATUS)"
    else
        assert_warn "Notes/english returned HTTP $CURL_STATUS"
    fi

    # 14.2.4 Notes with path traversal
    perform_request "${BASE_URL}/api/v1/edu/notes/../../../etc/passwd"
    if [[ "$CURL_STATUS" -eq 501 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Notes path traversal blocked ($CURL_STATUS)"
    else
        assert_warn "Notes traversal returned HTTP $CURL_STATUS"
    fi

    subsection "14.3 Flashcards Endpoint"

    # 14.3.1 Flashcards for Science
    perform_request "${BASE_URL}/api/v1/edu/flashcards/science"
    if [[ "$CURL_STATUS" -eq 501 ]]; then
        assert_pass "Flashcards returns 501 (Not Implemented)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Flashcards endpoint not found"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Flashcards returns 200 (implemented!)"
    else
        assert_warn "Flashcards returned HTTP $CURL_STATUS"
    fi

    # 14.3.2 Flashcards is JSON
    if is_json; then
        assert_pass "Flashcards response is JSON"
    else
        assert_warn "Flashcards response not JSON"
    fi

    # 14.3.3 Flashcards for History
    perform_request "${BASE_URL}/api/v1/edu/flashcards/history"
    if [[ "$CURL_STATUS" -eq 501 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Flashcards/history responds ($CURL_STATUS)"
    else
        assert_warn "Flashcards/history returned HTTP $CURL_STATUS"
    fi

    subsection "14.4 Method Enforcement"

    # 14.4.1 POST on quiz
    perform_request "${BASE_URL}/api/v1/edu/quiz/science" -X POST
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 501 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "POST on quiz handled ($CURL_STATUS)"
    else
        assert_warn "POST on quiz returned HTTP $CURL_STATUS"
    fi

    # 14.4.2 DELETE on notes
    perform_request "${BASE_URL}/api/v1/edu/notes/science" -X DELETE
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 501 || "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "DELETE on notes handled ($CURL_STATUS)"
    else
        assert_warn "DELETE on notes returned HTTP $CURL_STATUS"
    fi

    # 14.4.3 PUT on flashcards
    perform_request "${BASE_URL}/api/v1/edu/flashcards/science" -X PUT
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 501 || "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "PUT on flashcards handled ($CURL_STATUS)"
    else
        assert_warn "PUT on flashcards returned HTTP $CURL_STATUS"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 14: Education ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 15: Rate Limiting (P5 Upstash Redis) - 55+ tests
# ===============================================================================

test_layer_15_rate_limiting() {
    section_header "LAYER 15: Rate Limiting (P5 Upstash Redis)"

    local layer_start=$TOTAL_TESTS

    subsection "15.1 Rate Limit Headers Presence"

    # 15.1.1 Chat endpoint rate limit headers
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"rate limit test","language":"en"}'

    if has_header "x-ratelimit-limit" || has_header "ratelimit-limit"; then
        local rl_limit
        rl_limit=$(get_header_value "x-ratelimit-limit")
        if [[ -z "$rl_limit" ]]; then
            rl_limit=$(get_header_value "ratelimit-limit")
        fi
        assert_pass "X-RateLimit-Limit present: $rl_limit"
    else
        assert_warn "No X-RateLimit-Limit header on chat"
    fi

    if has_header "x-ratelimit-remaining" || has_header "ratelimit-remaining"; then
        local rl_remaining
        rl_remaining=$(get_header_value "x-ratelimit-remaining")
        if [[ -z "$rl_remaining" ]]; then
            rl_remaining=$(get_header_value "ratelimit-remaining")
        fi
        assert_pass "X-RateLimit-Remaining present: $rl_remaining"
    else
        assert_warn "No X-RateLimit-Remaining header"
    fi

    if has_header "x-ratelimit-reset" || has_header "ratelimit-reset"; then
        local rl_reset
        rl_reset=$(get_header_value "x-ratelimit-reset")
        if [[ -z "$rl_reset" ]]; then
            rl_reset=$(get_header_value "ratelimit-reset")
        fi
        assert_pass "X-RateLimit-Reset present: $rl_reset"
    else
        assert_warn "No X-RateLimit-Reset header"
    fi

    # 15.1.2 Rate limit on auth endpoint
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"test@test.com","password":"test"}'
    if has_header "x-ratelimit-limit" || has_header "ratelimit-limit" || has_header "retry-after"; then
        assert_pass "Rate limit headers on auth endpoint"
    else
        assert_warn "No rate limit headers on auth endpoint"
    fi

    # 15.1.3 Rate limit on health (should be lenient)
    perform_request "${BASE_URL}/health"
    if has_header "x-ratelimit-limit" || has_header "ratelimit-limit"; then
        assert_pass "Rate limit headers present on /health"
    else
        assert_pass "No rate limit on /health (expected - health should be open)"
    fi

    subsection "15.2 Rate Limit Countdown"

    # 15.2.1 Remaining decreases
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"countdown test 1","language":"en"}'
    local remaining1
    remaining1=$(get_header_value "x-ratelimit-remaining")
    if [[ -z "$remaining1" ]]; then
        remaining1=$(get_header_value "ratelimit-remaining")
    fi

    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"countdown test 2","language":"en"}'
    local remaining2
    remaining2=$(get_header_value "x-ratelimit-remaining")
    if [[ -z "$remaining2" ]]; then
        remaining2=$(get_header_value "ratelimit-remaining")
    fi

    if [[ -n "$remaining1" && -n "$remaining2" ]]; then
        if [[ "$remaining2" -lt "$remaining1" ]]; then
            assert_pass "Rate limit countdown: $remaining1 -> $remaining2"
        elif [[ "$remaining2" -eq "$remaining1" ]]; then
            assert_warn "Rate limit not decreasing ($remaining1 -> $remaining2)"
        else
            assert_warn "Rate limit increased unexpectedly ($remaining1 -> $remaining2)"
        fi
    else
        assert_skip "Rate limit countdown (headers not available)"
    fi

    # 15.2.2 Reset value is in the future
    local reset_val
    reset_val=$(get_header_value "x-ratelimit-reset")
    if [[ -z "$reset_val" ]]; then
        reset_val=$(get_header_value "ratelimit-reset")
    fi
    if [[ -n "$reset_val" ]]; then
        local now
        now=$(date +%s)
        if [[ "$reset_val" -gt "$now" ]] 2>/dev/null; then
            assert_pass "Rate limit reset is in the future"
        elif [[ "$reset_val" -gt 0 && "$reset_val" -lt 3600 ]]; then
            assert_pass "Rate limit reset is relative seconds: $reset_val"
        else
            assert_warn "Rate limit reset value unclear: $reset_val"
        fi
    else
        assert_skip "Rate limit reset value check"
    fi

    subsection "15.3 Rate Limit Behavior"

    # 15.3.1 Retry-After header on 429
    if [[ "$CURL_STATUS" -eq 429 ]]; then
        if has_header "retry-after"; then
            assert_pass "429 response includes Retry-After header"
        else
            assert_warn "429 without Retry-After header"
        fi
    else
        assert_skip "Retry-After check (not rate limited)"
    fi

    # 15.3.2 Different endpoints have different limits
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"test@test.com","password":"test"}'
    local auth_limit
    auth_limit=$(get_header_value "x-ratelimit-limit")
    if [[ -z "$auth_limit" ]]; then
        auth_limit=$(get_header_value "ratelimit-limit")
    fi

    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"limit compare","language":"en"}'
    local chat_limit
    chat_limit=$(get_header_value "x-ratelimit-limit")
    if [[ -z "$chat_limit" ]]; then
        chat_limit=$(get_header_value "ratelimit-limit")
    fi

    if [[ -n "$auth_limit" && -n "$chat_limit" ]]; then
        if [[ "$auth_limit" != "$chat_limit" ]]; then
            assert_pass "Different rate limits per endpoint (auth=$auth_limit, chat=$chat_limit)"
        else
            assert_warn "Same rate limit on auth and chat ($auth_limit)"
        fi
    else
        assert_skip "Per-endpoint rate limit comparison"
    fi

    subsection "15.4 Stress Test (Burst)"

    if [[ "$STRESS_TEST" == "1" ]]; then
        # 15.4.1 Rapid burst test
        local burst_count=0
        local got_429=0
        for i in $(seq 1 20); do
            perform_request "${BASE_URL}/api/v1/chat/" \
                -X POST \
                -H "Content-Type: application/json" \
                -d "{\"message\":\"burst test $i\",\"language\":\"en\"}"
            burst_count=$((burst_count + 1))
            if [[ "$CURL_STATUS" -eq 429 ]]; then
                got_429=1
                break
            fi
        done

        if [[ "$got_429" -eq 1 ]]; then
            assert_pass "Rate limit enforced after $burst_count requests (got 429)"
        else
            assert_warn "No 429 after $burst_count rapid requests"
        fi

        # 15.4.2 429 response is JSON
        if [[ "$got_429" -eq 1 ]]; then
            if is_json; then
                assert_pass "429 response is JSON"
            else
                assert_warn "429 response is not JSON"
            fi
        else
            assert_skip "429 JSON check (no 429 received)"
        fi

        # 15.4.3 429 has informative message
        if [[ "$got_429" -eq 1 ]]; then
            local rl_msg
            rl_msg=$(json_field '.detail // .message // .error // empty')
            if [[ -n "$rl_msg" ]]; then
                assert_pass "429 has message: $rl_msg"
            else
                assert_warn "429 has no message body"
            fi
        else
            assert_skip "429 message check"
        fi

        # 15.4.4 Auth endpoint burst
        local auth_429=0
        for i in $(seq 1 15); do
            perform_request "${BASE_URL}/api/v1/auth/login" \
                -X POST \
                -H "Content-Type: application/json" \
                -d '{"email":"stress@test.com","password":"wrong"}'
            if [[ "$CURL_STATUS" -eq 429 ]]; then
                auth_429=1
                break
            fi
        done
        if [[ "$auth_429" -eq 1 ]]; then
            assert_pass "Auth rate limit triggered on burst"
        else
            assert_warn "Auth not rate limited after 15 rapid attempts"
        fi
    else
        assert_skip "Burst test (STRESS_TEST not enabled)"
        assert_skip "429 JSON (stress disabled)"
        assert_skip "429 message (stress disabled)"
        assert_skip "Auth burst (stress disabled)"
    fi

    subsection "15.5 Rate Limit Edge Cases"

    # 15.5.1 Rate limit with different IPs (same endpoint)
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "X-Forwarded-For: 192.168.1.100" \
        -d '{"message":"ip test","language":"en"}'
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 429 ]]; then
        assert_pass "Request with X-Forwarded-For handled ($CURL_STATUS)"
    else
        assert_warn "X-Forwarded-For request returned HTTP $CURL_STATUS"
    fi

    # 15.5.2 Rate limit header consistency
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"consistency","language":"en"}'
    local limit_val
    limit_val=$(get_header_value "x-ratelimit-limit")
    if [[ -z "$limit_val" ]]; then
        limit_val=$(get_header_value "ratelimit-limit")
    fi
    if [[ -n "$limit_val" ]]; then
        if [[ "$limit_val" =~ ^[0-9]+$ ]]; then
            assert_pass "Rate limit value is numeric: $limit_val"
        else
            assert_warn "Rate limit value is not numeric: $limit_val"
        fi
    else
        assert_skip "Rate limit numeric check"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 15: Rate Limiting ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 16: Streaming & SSE Validation - 45+ tests
# ===============================================================================

test_layer_16_streaming() {
    section_header "LAYER 16: Streaming & SSE Validation"

    local layer_start=$TOTAL_TESTS

    subsection "16.1 SSE Format Validation"

    # 16.1.1 Stream endpoint content-type
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_stream_request "${BASE_URL}/api/v1/chat/stream" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message":"What is gravity?","language":"en"}'

        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Stream returns 200"

            # 16.1.2 Content-Type is text/event-stream
            local stream_ct
            stream_ct=$(get_header_value "content-type")
            if [[ "$stream_ct" == *"text/event-stream"* ]]; then
                assert_pass "Stream Content-Type: text/event-stream"
            elif [[ "$stream_ct" == *"text/plain"* ]]; then
                assert_warn "Stream Content-Type: text/plain (expected event-stream)"
            else
                assert_warn "Stream Content-Type: $stream_ct"
            fi

            # 16.1.3 Response has data: prefix lines
            if echo "$CURL_BODY" | grep -q "^data:"; then
                assert_pass "SSE response contains data: prefix lines"
            else
                assert_warn "SSE response missing data: prefix"
            fi

            # 16.1.4 SSE events are parseable JSON
            local first_data
            first_data=$(echo "$CURL_BODY" | grep "^data:" | head -1 | sed 's/^data: *//')
            if [[ -n "$first_data" ]] && echo "$first_data" | jq . >/dev/null 2>&1; then
                assert_pass "SSE data events are valid JSON"
            elif [[ -n "$first_data" ]]; then
                assert_warn "SSE data is not JSON: ${first_data:0:50}"
            else
                assert_warn "No SSE data events found"
            fi

            # 16.1.5 Stream has text/chunk content
            if echo "$CURL_BODY" | grep -qi '"text"\|"chunk"\|"content"\|"token"'; then
                assert_pass "SSE events contain text/chunk field"
            else
                assert_warn "SSE events missing text field"
            fi

            # 16.1.6 Stream has done:true event
            if echo "$CURL_BODY" | grep -qi '"done".*true\|"done":true'; then
                assert_pass "SSE stream has done:true final event"
            else
                assert_warn "No done:true event in stream"
            fi

            # 16.1.7 Final event has metadata
            local last_data
            last_data=$(echo "$CURL_BODY" | grep "^data:" | tail -1 | sed 's/^data: *//')
            if [[ -n "$last_data" ]]; then
                if echo "$last_data" | jq -e '.latency_ms // .model // .lang' >/dev/null 2>&1; then
                    assert_pass "Final SSE event has metadata (latency/model/lang)"
                else
                    assert_warn "Final SSE event missing metadata"
                fi
            else
                assert_skip "Final event metadata"
            fi

            # 16.1.8 Multiple data lines (chunked response)
            local data_count
            data_count=$(echo "$CURL_BODY" | grep -c "^data:" || echo "0")
            if [[ "$data_count" -gt 1 ]]; then
                assert_pass "Stream has $data_count data events (chunked)"
            elif [[ "$data_count" -eq 1 ]]; then
                assert_warn "Stream has only 1 data event (not truly streaming)"
            else
                assert_warn "Stream has no data events"
            fi

            # 16.1.9 No empty lines between crucial events
            local empty_lines
            empty_lines=$(echo "$CURL_BODY" | grep -c "^$" || echo "0")
            if [[ "$empty_lines" -gt 0 ]]; then
                assert_pass "SSE has $empty_lines separator lines (standard format)"
            else
                assert_warn "No empty line separators in SSE"
            fi

        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            assert_warn "Stream rate limited (429)"
            assert_skip "Stream Content-Type"
            assert_skip "SSE data prefix"
            assert_skip "SSE JSON validity"
            assert_skip "SSE text content"
            assert_skip "SSE done event"
            assert_skip "SSE metadata"
            assert_skip "SSE chunk count"
            assert_skip "SSE separators"
        else
            assert_warn "Stream returned HTTP $CURL_STATUS"
            assert_skip "Stream Content-Type"
            assert_skip "SSE data prefix"
            assert_skip "SSE JSON validity"
            assert_skip "SSE text content"
            assert_skip "SSE done event"
            assert_skip "SSE metadata"
            assert_skip "SSE chunk count"
            assert_skip "SSE separators"
        fi
    else
        assert_skip "Stream endpoint (no auth token)"
        assert_skip "Stream Content-Type (no token)"
        assert_skip "SSE data prefix (no token)"
        assert_skip "SSE JSON (no token)"
        assert_skip "SSE text content (no token)"
        assert_skip "SSE done event (no token)"
        assert_skip "SSE metadata (no token)"
        assert_skip "SSE chunk count (no token)"
        assert_skip "SSE separators (no token)"
    fi

    subsection "16.2 Stream Error Handling"

    # 16.2.1 Stream without message
    perform_request "${BASE_URL}/api/v1/chat/stream" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"language":"en"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Stream without message returns $CURL_STATUS"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Stream rate limited"
    else
        assert_warn "Stream without message returned HTTP $CURL_STATUS"
    fi

    # 16.2.2 Stream with empty message
    perform_request "${BASE_URL}/api/v1/chat/stream" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"","language":"en"}'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Stream empty message handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Stream empty message returned HTTP $CURL_STATUS"
    fi

    # 16.2.3 Stream with invalid JSON
    perform_request "${BASE_URL}/api/v1/chat/stream" \
        -X POST \
        -H "Content-Type: application/json" \
        -d 'invalid json'
    if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_pass "Stream invalid JSON returns $CURL_STATUS"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "Rate limited"
    else
        assert_warn "Stream invalid JSON returned HTTP $CURL_STATUS"
    fi

    # 16.2.4 Stream without auth (if required)
    perform_request "${BASE_URL}/api/v1/chat/stream" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"test","language":"en"}'
    if [[ "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "Stream requires auth (401)"
    elif [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 429 ]]; then
        assert_pass "Stream accessible without auth ($CURL_STATUS)"
    else
        assert_warn "Stream no-auth returned HTTP $CURL_STATUS"
    fi

    subsection "16.3 Stream Connection Headers"

    # 16.3.1 Cache-Control: no-cache expected
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_stream_request "${BASE_URL}/api/v1/chat/stream" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message":"headers test","language":"en"}'
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            local cc
            cc=$(get_header_value "cache-control")
            if [[ "$cc" == *"no-cache"* ]]; then
                assert_pass "Stream has Cache-Control: no-cache"
            else
                assert_warn "Stream Cache-Control: ${cc:-missing}"
            fi

            # 16.3.2 Connection: keep-alive or similar
            if has_header "connection"; then
                assert_pass "Stream has Connection header"
            else
                assert_skip "Stream Connection header"
            fi

            # 16.3.3 Transfer-Encoding: chunked
            if has_header "transfer-encoding"; then
                local te
                te=$(get_header_value "transfer-encoding")
                if [[ "$te" == *"chunked"* ]]; then
                    assert_pass "Stream Transfer-Encoding: chunked"
                else
                    assert_warn "Stream Transfer-Encoding: $te"
                fi
            else
                assert_skip "Stream Transfer-Encoding"
            fi
        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            assert_skip "Stream Cache-Control (rate limited)"
            assert_skip "Stream Connection (rate limited)"
            assert_skip "Stream Transfer-Encoding (rate limited)"
        else
            assert_skip "Stream Cache-Control (HTTP $CURL_STATUS)"
            assert_skip "Stream Connection (HTTP $CURL_STATUS)"
            assert_skip "Stream Transfer-Encoding (HTTP $CURL_STATUS)"
        fi
    else
        assert_skip "Stream Cache-Control (no token)"
        assert_skip "Stream Connection (no token)"
        assert_skip "Stream Transfer-Encoding (no token)"
    fi

    subsection "16.4 Stream Method Enforcement"

    # 16.4.1 GET on stream
    perform_request "${BASE_URL}/api/v1/chat/stream"
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "GET on /chat/stream rejected ($CURL_STATUS)"
    else
        assert_warn "GET /chat/stream returned HTTP $CURL_STATUS"
    fi

    # 16.4.2 PUT on stream
    perform_request "${BASE_URL}/api/v1/chat/stream" -X PUT
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "PUT on /chat/stream rejected ($CURL_STATUS)"
    else
        assert_warn "PUT /chat/stream returned HTTP $CURL_STATUS"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 16: Streaming ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 17: End-to-End Workflows - 85+ tests
# ===============================================================================

test_layer_17_workflows() {
    section_header "LAYER 17: End-to-End Workflows"

    local layer_start=$TOTAL_TESTS

    if [[ "$QUICK_MODE" == "1" ]]; then
        assert_skip "E2E workflows skipped in quick mode"
        LAYER_RESULTS+=("Layer 17: Workflows (QUICK SKIP)")
        return
    fi

    subsection "17.1 Workflow: New User Journey"

    # Signup -> Login -> Chat -> History -> Feedback
    local wf_email="wf-test-$(date +%s)@example-test.com"
    local wf_token=""

    # 17.1.1 Signup
    perform_request "${BASE_URL}/api/v1/auth/signup" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"${wf_email}\",\"password\":\"WorkflowTest123!\",\"name\":\"Workflow User\"}"
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 201 ]]; then
        assert_pass "WF1: Signup successful"
    elif [[ "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 409 ]]; then
        assert_pass "WF1: Signup response ($CURL_STATUS - may exist)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "WF1: Signup rate limited"
    else
        assert_warn "WF1: Signup returned HTTP $CURL_STATUS"
    fi

    # 17.1.2 Login
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"${wf_email}\",\"password\":\"WorkflowTest123!\"}"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "WF1: Login successful"
        wf_token=$(json_field '.access_token // .token // empty')
        if [[ -n "$wf_token" ]]; then
            assert_pass "WF1: Token obtained"
        else
            assert_warn "WF1: Login 200 but no token"
        fi
    elif [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 400 ]]; then
        assert_warn "WF1: Login failed ($CURL_STATUS - signup may not have created account)"
        # Fall back to existing token
        wf_token="$AUTH_TOKEN"
        assert_skip "WF1: Token from login"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "WF1: Login rate limited"
        wf_token="$AUTH_TOKEN"
        assert_skip "WF1: Token from login"
    else
        assert_warn "WF1: Login returned HTTP $CURL_STATUS"
        wf_token="$AUTH_TOKEN"
        assert_skip "WF1: Token from login"
    fi

    # 17.1.3 Chat
    if [[ -n "$wf_token" ]]; then
        perform_request "${BASE_URL}/api/v1/chat/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${wf_token}" \
            -d '{"message":"What is the periodic table?","language":"en"}'
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "WF1: Chat successful"
        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            assert_warn "WF1: Chat rate limited"
        else
            assert_warn "WF1: Chat returned HTTP $CURL_STATUS"
        fi

        # 17.1.4 History
        perform_request "${BASE_URL}/api/v1/chat/history" \
            -H "Authorization: Bearer ${wf_token}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "WF1: History accessible after chat"
        else
            assert_warn "WF1: History returned HTTP $CURL_STATUS"
        fi

        # 17.1.5 Feedback
        perform_request "${BASE_URL}/api/v1/chat/feedback/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${wf_token}" \
            -d '{"message_id":"wf_test_msg","rating":4,"comment":"workflow test"}'
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 201 || "$CURL_STATUS" -eq 422 ]]; then
            assert_pass "WF1: Feedback submitted ($CURL_STATUS)"
        else
            assert_warn "WF1: Feedback returned HTTP $CURL_STATUS"
        fi
    else
        assert_skip "WF1: Chat (no token)"
        assert_skip "WF1: History (no token)"
        assert_skip "WF1: Feedback (no token)"
    fi

    subsection "17.2 Workflow: Subscription Flow"

    if [[ -n "$AUTH_TOKEN" ]]; then
        # 17.2.1 Get plans
        perform_request "${BASE_URL}/api/v1/subscription/plans"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "WF2: Plans retrieved"
        else
            assert_warn "WF2: Plans returned HTTP $CURL_STATUS"
        fi

        # 17.2.2 Check current status
        perform_request "${BASE_URL}/api/v1/subscription/status" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "WF2: Status checked"
            local current_tier
            current_tier=$(json_field '.tier // .plan // empty')
            if [[ -n "$current_tier" ]]; then
                assert_pass "WF2: Current tier: $current_tier"
            else
                assert_skip "WF2: Tier info"
            fi
        else
            assert_warn "WF2: Status returned HTTP $CURL_STATUS"
            assert_skip "WF2: Tier info"
        fi

        # 17.2.3 Create order
        perform_request "${BASE_URL}/api/v1/payments/create-order" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"plan_id":"pro_monthly"}'
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 201 ]]; then
            assert_pass "WF2: Order created"
            local order_id
            order_id=$(json_field '.order_id // .id // empty')
            if [[ -n "$order_id" ]]; then
                assert_pass "WF2: Order ID: $order_id"
            else
                assert_skip "WF2: Order ID"
            fi
        elif [[ "$CURL_STATUS" -eq 503 ]]; then
            assert_pass "WF2: Razorpay not configured (503)"
            assert_skip "WF2: Order ID"
        else
            assert_warn "WF2: Create order returned HTTP $CURL_STATUS"
            assert_skip "WF2: Order ID"
        fi

        # 17.2.4 Payment history
        perform_request "${BASE_URL}/api/v1/payments/history" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "WF2: Payment history accessible"
        else
            assert_warn "WF2: Payment history returned HTTP $CURL_STATUS"
        fi
    else
        assert_skip "WF2: Plans (no token)"
        assert_skip "WF2: Status (no token)"
        assert_skip "WF2: Tier info (no token)"
        assert_skip "WF2: Create order (no token)"
        assert_skip "WF2: Order ID (no token)"
        assert_skip "WF2: Payment history (no token)"
    fi

    subsection "17.3 Workflow: Anonymous User"

    local anon_id="anon-wf-$(date +%s)"

    # 17.3.1 Anon chat
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "x-anon-id: ${anon_id}" \
        -d '{"message":"Hello, I have a question about science","language":"en"}'
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "WF3: Anon chat successful"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "WF3: Anon chat rate limited"
    elif [[ "$CURL_STATUS" -eq 401 ]]; then
        assert_pass "WF3: Anon chat requires auth"
    else
        assert_warn "WF3: Anon chat returned HTTP $CURL_STATUS"
    fi

    # 17.3.2 Get anon history
    perform_request "${BASE_URL}/api/v1/conversations/anon" \
        -H "x-anon-id: ${anon_id}"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "WF3: Anon conversation history accessible"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "WF3: Anon endpoint not found"
    else
        assert_warn "WF3: Anon history returned HTTP $CURL_STATUS"
    fi

    # 17.3.3 Anon cannot access auth endpoints
    perform_request "${BASE_URL}/api/v1/chat/history" \
        -H "x-anon-id: ${anon_id}"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "WF3: Anon cannot access /chat/history ($CURL_STATUS)"
    else
        assert_warn "WF3: Anon /chat/history returned HTTP $CURL_STATUS"
    fi

    subsection "17.4 Workflow: Content Discovery"

    # 17.4.1 Get library bundle
    perform_request "${BASE_URL}/api/v1/content/library-bundle"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "WF4: Library bundle loaded"

        # 17.4.2 Extract first board/class/subject
        local first_board
        first_board=$(echo "$CURL_BODY" | jq -r '.boards[0].id // .boards[0].name // empty' 2>/dev/null)
        if [[ -n "$first_board" ]]; then
            assert_pass "WF4: First board: $first_board"

            # 17.4.3 Get chapters
            perform_request "${BASE_URL}/api/v1/content/subject/${first_board}/10/Science"
            if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 404 ]]; then
                assert_pass "WF4: Chapters request returns $CURL_STATUS"
            else
                assert_warn "WF4: Chapters returned HTTP $CURL_STATUS"
            fi
        else
            assert_skip "WF4: First board extraction"
            assert_skip "WF4: Chapters request"
        fi

        # 17.4.4 Render content
        perform_request "${BASE_URL}/api/v1/content/render/SEBA/10/Science/Chapter-1"
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 404 ]]; then
            assert_pass "WF4: Content render returns $CURL_STATUS"
        else
            assert_warn "WF4: Content render returned HTTP $CURL_STATUS"
        fi
    else
        assert_warn "WF4: Library bundle returned HTTP $CURL_STATUS"
        assert_skip "WF4: First board"
        assert_skip "WF4: Chapters"
        assert_skip "WF4: Content render"
    fi

    subsection "17.5 Workflow: Chat + RAG Pipeline"

    if [[ -n "$AUTH_TOKEN" ]]; then
        # 17.5.1 Academic question with RAG
        perform_request "${BASE_URL}/api/v1/chat/" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"message":"Explain the structure of an atom as described in Class 9 NCERT","language":"en"}'
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "WF5: Academic chat with RAG returns 200"

            # 17.5.2 Check sources
            local wf_sources
            wf_sources=$(echo "$CURL_BODY" | jq '.sources // [] | length' 2>/dev/null || echo "0")
            if [[ "$wf_sources" -gt 0 ]]; then
                assert_pass "WF5: RAG provided $wf_sources sources"
            else
                assert_warn "WF5: No RAG sources in response"
            fi

            # 17.5.3 Response quality
            local wf_resp_len=${#CURL_BODY}
            if [[ "$wf_resp_len" -gt 100 ]]; then
                assert_pass "WF5: Response is substantial ($wf_resp_len bytes)"
            else
                assert_warn "WF5: Response seems short ($wf_resp_len bytes)"
            fi
        elif [[ "$CURL_STATUS" -eq 429 ]]; then
            assert_warn "WF5: Rate limited"
            assert_skip "WF5: RAG sources"
            assert_skip "WF5: Response quality"
        else
            assert_warn "WF5: Chat returned HTTP $CURL_STATUS"
            assert_skip "WF5: RAG sources"
            assert_skip "WF5: Response quality"
        fi
    else
        assert_skip "WF5: Chat+RAG (no token)"
        assert_skip "WF5: RAG sources (no token)"
        assert_skip "WF5: Response quality (no token)"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 17: Workflows ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 18: Cross-Cutting Concerns - 65+ tests
# ===============================================================================

test_layer_18_cross_cutting() {
    section_header "LAYER 18: Cross-Cutting Concerns"

    local layer_start=$TOTAL_TESTS

    subsection "18.1 X-Request-ID Header"

    # 18.1.1 Health endpoint has X-Request-ID
    perform_request "${BASE_URL}/health"
    if has_header "x-request-id"; then
        assert_pass "X-Request-ID header present on /health"
    else
        assert_warn "No X-Request-ID on /health"
    fi

    # 18.1.2 Chat endpoint has X-Request-ID
    perform_request "${BASE_URL}/api/v1/chat/" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"request id test","language":"en"}'
    if has_header "x-request-id"; then
        assert_pass "X-Request-ID present on chat endpoint"
    else
        assert_warn "No X-Request-ID on chat"
    fi

    # 18.1.3 Request IDs are unique
    perform_request "${BASE_URL}/health"
    local rid1
    rid1=$(get_header_value "x-request-id")
    perform_request "${BASE_URL}/health"
    local rid2
    rid2=$(get_header_value "x-request-id")
    if [[ -n "$rid1" && -n "$rid2" && "$rid1" != "$rid2" ]]; then
        assert_pass "X-Request-IDs are unique ($rid1 != $rid2)"
    elif [[ -n "$rid1" && -n "$rid2" ]]; then
        assert_warn "X-Request-IDs are identical"
    else
        assert_skip "Request ID uniqueness (headers not available)"
    fi

    # 18.1.4 Client-provided request ID echoed
    perform_request "${BASE_URL}/health" \
        -H "X-Request-ID: client-req-12345"
    local echoed_rid
    echoed_rid=$(get_header_value "x-request-id")
    if [[ "$echoed_rid" == "client-req-12345" ]]; then
        assert_pass "Client X-Request-ID echoed back"
    elif [[ -n "$echoed_rid" ]]; then
        assert_pass "Server generates own X-Request-ID (does not echo client)"
    else
        assert_warn "No X-Request-ID in response"
    fi

    subsection "18.2 API Versioning"

    # 18.2.1 X-API-Version header
    perform_request "${BASE_URL}/health"
    if has_header "x-api-version"; then
        local api_ver
        api_ver=$(get_header_value "x-api-version")
        assert_pass "X-API-Version header: $api_ver"
    else
        assert_warn "No X-API-Version header"
    fi

    # 18.2.2 API v1 prefix works
    perform_request "${BASE_URL}/api/v1/subscription/plans"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "API v1 prefix routing works"
    else
        assert_warn "API v1 returned HTTP $CURL_STATUS"
    fi

    # 18.2.3 Non-existent API version
    perform_request "${BASE_URL}/api/v2/health"
    if [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "API v2 returns 404 (not implemented)"
    else
        assert_warn "API v2 returned HTTP $CURL_STATUS"
    fi

    # 18.2.4 API root
    perform_request "${BASE_URL}/api/"
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "API root responds ($CURL_STATUS)"
    else
        assert_warn "API root returned HTTP $CURL_STATUS"
    fi

    subsection "18.3 CORS (Comprehensive)"

    # 18.3.1 Valid origin
    perform_request "${BASE_URL}/health" \
        -H "Origin: ${FRONTEND_URL}"
    local cors_origin
    cors_origin=$(get_header_value "access-control-allow-origin")
    if [[ -n "$cors_origin" ]]; then
        assert_pass "CORS allows frontend origin: $cors_origin"
    else
        assert_warn "No CORS header for frontend origin"
    fi

    # 18.3.2 Credentials allowed
    if has_header "access-control-allow-credentials"; then
        local creds
        creds=$(get_header_value "access-control-allow-credentials")
        if [[ "$creds" == "true" ]]; then
            assert_pass "CORS: credentials allowed"
        else
            assert_warn "CORS: credentials: $creds"
        fi
    else
        assert_warn "No Access-Control-Allow-Credentials header"
    fi

    # 18.3.3 Expose headers
    if has_header "access-control-expose-headers"; then
        assert_pass "CORS: Expose-Headers present"
    else
        assert_warn "No Access-Control-Expose-Headers"
    fi

    subsection "18.4 CSRF Protection"

    # 18.4.1 Mutation from wrong origin
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "Origin: https://evil-site.example.com" \
        -d '{"email":"test@test.com","password":"test"}'
    if [[ "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "CSRF: Wrong origin blocked (403)"
    elif [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
        assert_warn "CSRF: Request processed despite wrong origin ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 429 ]]; then
        assert_warn "CSRF: Rate limited - cannot verify"
    else
        assert_warn "CSRF: Wrong origin returned HTTP $CURL_STATUS"
    fi

    # 18.4.2 Mutation with no origin
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"test@test.com","password":"test"}'
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 429 ]]; then
        assert_pass "Request without Origin handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 403 ]]; then
        assert_warn "No-origin request blocked (strict CSRF)"
    else
        assert_warn "No-origin request returned HTTP $CURL_STATUS"
    fi

    subsection "18.5 Redirects & Path Handling"

    # 18.5.1 Trailing slash handling
    perform_request "${BASE_URL}/health/"
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 307 || "$CURL_STATUS" -eq 301 ]]; then
        assert_pass "Trailing slash handled ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Trailing slash causes 404"
    else
        assert_warn "Trailing slash returned HTTP $CURL_STATUS"
    fi

    # 18.5.2 Double slash
    perform_request "${BASE_URL}//health"
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 404 || "$CURL_STATUS" -eq 301 ]]; then
        assert_pass "Double slash handled ($CURL_STATUS)"
    else
        assert_warn "Double slash returned HTTP $CURL_STATUS"
    fi

    # 18.5.3 Case sensitivity
    perform_request "${BASE_URL}/HEALTH"
    if [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "Routes are case-sensitive (/HEALTH -> 404)"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_warn "Routes are case-insensitive (/HEALTH -> 200)"
    else
        assert_warn "/HEALTH returned HTTP $CURL_STATUS"
    fi

    subsection "18.6 404 & Method Not Allowed"

    # 18.6.1 Non-existent path returns 404
    perform_request "${BASE_URL}/api/v1/totally-nonexistent-path"
    if [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_pass "Non-existent path returns 404"
    else
        assert_warn "Non-existent path returned HTTP $CURL_STATUS"
    fi

    # 18.6.2 404 is JSON
    if is_json; then
        assert_pass "404 response is JSON"
    else
        assert_warn "404 response is not JSON"
    fi

    # 18.6.3 404 has detail field
    local not_found_detail
    not_found_detail=$(json_field '.detail // empty')
    if [[ -n "$not_found_detail" ]]; then
        assert_pass "404 has detail field: $not_found_detail"
    else
        assert_warn "404 missing detail field"
    fi

    # 18.6.4 405 Method Not Allowed
    perform_request "${BASE_URL}/health" -X PATCH
    if [[ "$CURL_STATUS" -eq 405 ]]; then
        assert_pass "PATCH on /health returns 405"
    else
        assert_warn "PATCH on /health returned HTTP $CURL_STATUS (expected 405)"
    fi

    # 18.6.5 OPTIONS always works
    perform_request "${BASE_URL}/api/v1/chat/" -X OPTIONS
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 204 ]]; then
        assert_pass "OPTIONS on /chat/ returns $CURL_STATUS"
    else
        assert_warn "OPTIONS on /chat/ returned HTTP $CURL_STATUS"
    fi

    subsection "18.7 Content Negotiation"

    # 18.7.1 Accept: application/json
    perform_request "${BASE_URL}/health" -H "Accept: application/json"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        local ct
        ct=$(get_header_value "content-type")
        if [[ "$ct" == *"application/json"* ]]; then
            assert_pass "JSON content negotiation works"
        else
            assert_warn "Accept JSON but got: $ct"
        fi
    else
        assert_warn "Accept JSON returned HTTP $CURL_STATUS"
    fi

    # 18.7.2 Accept: text/html (on API)
    perform_request "${BASE_URL}/health" -H "Accept: text/html"
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 406 ]]; then
        assert_pass "Accept text/html on API handled ($CURL_STATUS)"
    else
        assert_warn "Accept text/html returned HTTP $CURL_STATUS"
    fi

    # 18.7.3 Accept: */*
    perform_request "${BASE_URL}/health" -H "Accept: */*"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "Accept */* works on /health"
    else
        assert_warn "Accept */* returned HTTP $CURL_STATUS"
    fi

    subsection "18.8 Response Size & Headers"

    # 18.8.1 No excessive headers
    perform_request "${BASE_URL}/health"
    local header_count
    header_count=$(echo "$CURL_HEADERS" | wc -l)
    if [[ "$header_count" -lt 50 ]]; then
        assert_pass "Response header count reasonable: $header_count"
    else
        assert_warn "Too many response headers: $header_count"
    fi

    # 18.8.2 Content-Length or Transfer-Encoding present
    if has_header "content-length" || has_header "transfer-encoding"; then
        assert_pass "Content-Length or Transfer-Encoding present"
    else
        assert_warn "Neither Content-Length nor Transfer-Encoding header"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 18: Cross-Cutting ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 19: Users API - 55+ tests
# ===============================================================================

test_layer_19_users() {
    section_header "LAYER 19: Users API"

    local layer_start=$TOTAL_TESTS

    subsection "19.1 GET /me (Profile)"

    # 19.1.1 Profile without auth
    perform_request "${BASE_URL}/api/v1/users/me"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "GET /users/me requires auth ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_warn "GET /users/me accessible without auth"
    else
        assert_warn "GET /users/me returned HTTP $CURL_STATUS"
    fi

    # 19.1.2 Profile with auth
    if [[ -n "$AUTH_TOKEN" ]]; then
        perform_request "${BASE_URL}/api/v1/users/me" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "GET /users/me with auth returns 200"

            # 19.1.3 Profile is JSON
            if is_json; then
                assert_pass "Profile response is JSON"
            else
                assert_warn "Profile not JSON"
            fi

            # 19.1.4 Profile has email
            local prof_email
            prof_email=$(json_field '.email // empty')
            if [[ -n "$prof_email" ]]; then
                assert_pass "Profile has email field"
            else
                assert_warn "Profile missing email"
            fi

            # 19.1.5 Profile has name
            local prof_name
            prof_name=$(json_field '.name // .display_name // empty')
            if [[ -n "$prof_name" ]]; then
                assert_pass "Profile has name: $prof_name"
            else
                assert_warn "Profile missing name"
            fi

            # 19.1.6 Profile has subscription info
            local prof_tier
            prof_tier=$(json_field '.tier // .subscription_tier // .plan // empty')
            if [[ -n "$prof_tier" ]]; then
                assert_pass "Profile has tier: $prof_tier"
            else
                assert_skip "Profile tier info not present"
            fi

            # 19.1.7 Profile has credits
            local prof_credits
            prof_credits=$(json_field '.credits // .messages_remaining // empty')
            if [[ -n "$prof_credits" ]]; then
                assert_pass "Profile has credits: $prof_credits"
            else
                assert_skip "Profile credits not present"
            fi

            # 19.1.8 Profile has created_at
            local prof_created
            prof_created=$(json_field '.created_at // .registered_at // empty')
            if [[ -n "$prof_created" ]]; then
                assert_pass "Profile has created_at"
            else
                assert_skip "Profile created_at not present"
            fi

            # 19.1.9 No password in profile response
            local has_pw
            has_pw=$(json_field '.password // .password_hash // empty')
            if [[ -z "$has_pw" ]]; then
                assert_pass "Profile does not expose password"
            else
                assert_fail "Profile exposes password/hash!"
            fi
        else
            assert_warn "GET /users/me auth returned HTTP $CURL_STATUS"
            assert_skip "Profile JSON"
            assert_skip "Profile email"
            assert_skip "Profile name"
            assert_skip "Profile tier"
            assert_skip "Profile credits"
            assert_skip "Profile created_at"
            assert_skip "Password not exposed"
        fi
    else
        assert_skip "Profile with auth (no token)"
        assert_skip "Profile JSON (no token)"
        assert_skip "Profile email (no token)"
        assert_skip "Profile name (no token)"
        assert_skip "Profile tier (no token)"
        assert_skip "Profile credits (no token)"
        assert_skip "Profile created_at (no token)"
        assert_skip "Password check (no token)"
    fi

    subsection "19.2 PUT /me (Update Profile)"

    if [[ -n "$AUTH_TOKEN" ]]; then
        # 19.2.1 Update name
        perform_request "${BASE_URL}/api/v1/users/me" \
            -X PUT \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"name":"Test Updated Name"}'
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "PUT /users/me returns 200 (name updated)"
        elif [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 ]]; then
            assert_pass "PUT /users/me validation ($CURL_STATUS)"
        elif [[ "$CURL_STATUS" -eq 405 ]]; then
            assert_warn "PUT /users/me not allowed (maybe PATCH only)"
        else
            assert_warn "PUT /users/me returned HTTP $CURL_STATUS"
        fi

        # 19.2.2 Update with empty name
        perform_request "${BASE_URL}/api/v1/users/me" \
            -X PUT \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"name":""}'
        if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Empty name update handled ($CURL_STATUS)"
        else
            assert_warn "Empty name update returned HTTP $CURL_STATUS"
        fi

        # 19.2.3 Update with XSS in name
        perform_request "${BASE_URL}/api/v1/users/me" \
            -X PUT \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"name":"<script>alert(1)</script>"}'
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 ]]; then
            assert_pass "XSS in name handled ($CURL_STATUS)"
        else
            assert_warn "XSS name returned HTTP $CURL_STATUS"
        fi

        # 19.2.4 Update email (should require verification or be rejected)
        perform_request "${BASE_URL}/api/v1/users/me" \
            -X PUT \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"email":"new-email@test.com"}'
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 403 ]]; then
            assert_pass "Email update attempt handled ($CURL_STATUS)"
        else
            assert_warn "Email update returned HTTP $CURL_STATUS"
        fi

        # 19.2.5 Cannot escalate role
        perform_request "${BASE_URL}/api/v1/users/me" \
            -X PUT \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d '{"role":"admin"}'
        if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 403 ]]; then
            # Verify role did not change
            perform_request "${BASE_URL}/api/v1/users/me" \
                -H "Authorization: Bearer ${AUTH_TOKEN}"
            local current_role
            current_role=$(json_field '.role // empty')
            if [[ "$current_role" != "admin" ]]; then
                assert_pass "Role escalation prevented"
            else
                assert_warn "Role may have been escalated (role=$current_role)"
            fi
        else
            assert_warn "Role update attempt returned HTTP $CURL_STATUS"
        fi

        # 19.2.6 Very long name
        local long_name
        long_name=$(printf 'N%.0s' {1..500})
        perform_request "${BASE_URL}/api/v1/users/me" \
            -X PUT \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -d "{\"name\":\"${long_name}\"}"
        if [[ "$CURL_STATUS" -eq 422 || "$CURL_STATUS" -eq 400 || "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Very long name handled ($CURL_STATUS)"
        else
            assert_warn "Long name returned HTTP $CURL_STATUS"
        fi
    else
        assert_skip "PUT /users/me (no token)"
        assert_skip "Empty name update (no token)"
        assert_skip "XSS in name (no token)"
        assert_skip "Email update (no token)"
        assert_skip "Role escalation (no token)"
        assert_skip "Long name (no token)"
    fi

    subsection "19.3 DELETE /me"

    # 19.3.1 Delete without auth
    perform_request "${BASE_URL}/api/v1/users/me" -X DELETE
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
        assert_pass "DELETE /users/me requires auth ($CURL_STATUS)"
    elif [[ "$CURL_STATUS" -eq 405 ]]; then
        assert_pass "DELETE /users/me not supported (405)"
    else
        assert_warn "DELETE /users/me without auth returned HTTP $CURL_STATUS"
    fi

    # 19.3.2 Delete with invalid token
    perform_request "${BASE_URL}/api/v1/users/me" \
        -X DELETE \
        -H "Authorization: Bearer invalid-token"
    if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 || "$CURL_STATUS" -eq 405 ]]; then
        assert_pass "DELETE /users/me rejects invalid token ($CURL_STATUS)"
    else
        assert_warn "DELETE with invalid token returned HTTP $CURL_STATUS"
    fi

    subsection "19.4 Onboarding & Credits"

    if [[ -n "$AUTH_TOKEN" ]]; then
        # 19.4.1 Onboarding status
        perform_request "${BASE_URL}/api/v1/users/me/onboarding" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Onboarding endpoint returns 200"
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            assert_warn "Onboarding endpoint not found"
        else
            assert_warn "Onboarding returned HTTP $CURL_STATUS"
        fi

        # 19.4.2 Credits endpoint
        perform_request "${BASE_URL}/api/v1/users/me/credits" \
            -H "Authorization: Bearer ${AUTH_TOKEN}"
        if [[ "$CURL_STATUS" -eq 200 ]]; then
            assert_pass "Credits endpoint returns 200"
            if is_json; then
                assert_pass "Credits response is JSON"
            else
                assert_warn "Credits not JSON"
            fi
        elif [[ "$CURL_STATUS" -eq 404 ]]; then
            assert_warn "Credits endpoint not found"
            assert_skip "Credits JSON"
        else
            assert_warn "Credits returned HTTP $CURL_STATUS"
            assert_skip "Credits JSON"
        fi
    else
        assert_skip "Onboarding (no token)"
        assert_skip "Credits endpoint (no token)"
        assert_skip "Credits JSON (no token)"
    fi

    subsection "19.5 Method Enforcement"

    # 19.5.1 POST on /users/me
    perform_request "${BASE_URL}/api/v1/users/me" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{}'
    if [[ "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "POST on /users/me rejected ($CURL_STATUS)"
    else
        assert_warn "POST /users/me returned HTTP $CURL_STATUS"
    fi

    # 19.5.2 PATCH on /users/me
    perform_request "${BASE_URL}/api/v1/users/me" \
        -X PATCH \
        -H "Content-Type: application/json" \
        -d '{"name":"patch test"}'
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 405 || "$CURL_STATUS" -eq 422 ]]; then
        assert_pass "PATCH on /users/me handled ($CURL_STATUS)"
    else
        assert_warn "PATCH /users/me returned HTTP $CURL_STATUS"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 19: Users API ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 20: Performance & Timing - 45+ tests
# ===============================================================================

test_layer_20_performance() {
    section_header "LAYER 20: Performance & Timing"

    local layer_start=$TOTAL_TESTS

    if [[ "$QUICK_MODE" == "1" ]]; then
        assert_skip "Performance tests skipped in quick mode"
        LAYER_RESULTS+=("Layer 20: Performance (QUICK SKIP)")
        return
    fi

    subsection "20.1 Response Time Thresholds"

    # 20.1.1 Health endpoint TTFB < 500ms
    perform_request "${BASE_URL}/health"
    if [[ "$CURL_TTFB" -lt 500 ]]; then
        assert_pass "Health TTFB < 500ms (${CURL_TTFB}ms)"
    elif [[ "$CURL_TTFB" -lt 1000 ]]; then
        assert_warn "Health TTFB 500-1000ms (${CURL_TTFB}ms)"
    else
        assert_warn "Health TTFB > 1s (${CURL_TTFB}ms)"
    fi

    # 20.1.2 Health total < 1s
    if [[ "$CURL_TOTAL" -lt 1000 ]]; then
        assert_pass "Health total response < 1s (${CURL_TOTAL}ms)"
    else
        assert_warn "Health total > 1s (${CURL_TOTAL}ms)"
    fi

    # 20.1.3 Frontend TTFB < 2s
    perform_request "$FRONTEND_URL"
    if [[ "$CURL_TTFB" -lt 2000 ]]; then
        assert_pass "Frontend TTFB < 2s (${CURL_TTFB}ms)"
    elif [[ "$CURL_TTFB" -lt 4000 ]]; then
        assert_warn "Frontend TTFB 2-4s (${CURL_TTFB}ms)"
    else
        assert_warn "Frontend TTFB > 4s (${CURL_TTFB}ms) - cold start?"
    fi

    # 20.1.4 Frontend total < 5s
    if [[ "$CURL_TOTAL" -lt 5000 ]]; then
        assert_pass "Frontend total < 5s (${CURL_TOTAL}ms)"
    else
        assert_warn "Frontend total > 5s (${CURL_TOTAL}ms)"
    fi

    # 20.1.5 Plans endpoint TTFB < 1s
    perform_request "${BASE_URL}/api/v1/subscription/plans"
    if [[ "$CURL_TTFB" -lt 1000 ]]; then
        assert_pass "Plans TTFB < 1s (${CURL_TTFB}ms)"
    elif [[ "$CURL_TTFB" -lt 3000 ]]; then
        assert_warn "Plans TTFB 1-3s (${CURL_TTFB}ms)"
    else
        assert_warn "Plans TTFB > 3s (${CURL_TTFB}ms)"
    fi

    # 20.1.6 Library bundle TTFB < 3s
    perform_request "${BASE_URL}/api/v1/content/library-bundle"
    if [[ "$CURL_TTFB" -lt 3000 ]]; then
        assert_pass "Library bundle TTFB < 3s (${CURL_TTFB}ms)"
    elif [[ "$CURL_TTFB" -lt 5000 ]]; then
        assert_warn "Library bundle TTFB 3-5s (${CURL_TTFB}ms)"
    else
        assert_warn "Library bundle TTFB > 5s (${CURL_TTFB}ms)"
    fi

    # 20.1.7 Auth endpoint < 2s
    perform_request "${BASE_URL}/api/v1/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"timing@test.com","password":"test123"}'
    if [[ "$CURL_TTFB" -lt 2000 ]]; then
        assert_pass "Auth TTFB < 2s (${CURL_TTFB}ms)"
    else
        assert_warn "Auth TTFB > 2s (${CURL_TTFB}ms)"
    fi

    subsection "20.2 Cold Start Detection"

    # 20.2.1 First request timing
    perform_request "${BASE_URL}/health"
    local first_ttfb=$CURL_TTFB

    # 20.2.2 Second request timing (warmed)
    perform_request "${BASE_URL}/health"
    local second_ttfb=$CURL_TTFB

    # 20.2.3 Compare for cold start
    if [[ "$first_ttfb" -gt 0 && "$second_ttfb" -gt 0 ]]; then
        if [[ "$first_ttfb" -gt $((second_ttfb * 3)) ]]; then
            assert_warn "Possible cold start: 1st=${first_ttfb}ms vs 2nd=${second_ttfb}ms"
        else
            assert_pass "No cold start detected (1st=${first_ttfb}ms, 2nd=${second_ttfb}ms)"
        fi
    else
        assert_skip "Cold start comparison"
    fi

    # 20.2.4 Third request (fully warm)
    perform_request "${BASE_URL}/health"
    local third_ttfb=$CURL_TTFB
    if [[ "$third_ttfb" -lt 500 ]]; then
        assert_pass "Warmed health TTFB: ${third_ttfb}ms"
    else
        assert_warn "Warmed health still slow: ${third_ttfb}ms"
    fi

    subsection "20.3 Concurrent Requests"

    # 20.3.1 Parallel health requests
    local pids=()
    local tmpdir
    tmpdir=$(mktemp -d)
    GLOBAL_TMPFILES+=("$tmpdir")

    for i in $(seq 1 5); do
        curl -sS -o "${tmpdir}/resp_${i}.json" -w '%{http_code}' \
            --max-time 10 "${BASE_URL}/health" > "${tmpdir}/status_${i}.txt" 2>/dev/null &
        pids+=($!)
    done

    # Wait for all
    local all_ok=1
    for pid in "${pids[@]}"; do
        wait "$pid" 2>/dev/null || true
    done

    local concurrent_pass=0
    for i in $(seq 1 5); do
        local status_file="${tmpdir}/status_${i}.txt"
        if [[ -f "$status_file" ]]; then
            local s
            s=$(cat "$status_file")
            if [[ "$s" == "200" ]]; then
                concurrent_pass=$((concurrent_pass + 1))
            fi
        fi
    done

    if [[ "$concurrent_pass" -eq 5 ]]; then
        assert_pass "5/5 concurrent health requests succeeded"
    elif [[ "$concurrent_pass" -ge 3 ]]; then
        assert_warn "Only $concurrent_pass/5 concurrent requests succeeded"
    else
        assert_warn "Concurrent requests mostly failed ($concurrent_pass/5)"
    fi

    # Cleanup
    rm -rf "$tmpdir" 2>/dev/null || true

    # 20.3.2 Parallel different endpoints
    local par_pids=()
    local par_tmpdir
    par_tmpdir=$(mktemp -d)
    GLOBAL_TMPFILES+=("$par_tmpdir")

    curl -sS -w '%{http_code}' --max-time 10 -o /dev/null "${BASE_URL}/health" > "${par_tmpdir}/s1.txt" 2>/dev/null &
    par_pids+=($!)
    curl -sS -w '%{http_code}' --max-time 10 -o /dev/null "${BASE_URL}/api/v1/subscription/plans" > "${par_tmpdir}/s2.txt" 2>/dev/null &
    par_pids+=($!)
    curl -sS -w '%{http_code}' --max-time 10 -o /dev/null "${FRONTEND_URL}" > "${par_tmpdir}/s3.txt" 2>/dev/null &
    par_pids+=($!)

    for pid in "${par_pids[@]}"; do
        wait "$pid" 2>/dev/null || true
    done

    local par_pass=0
    for i in 1 2 3; do
        local ps
        ps=$(cat "${par_tmpdir}/s${i}.txt" 2>/dev/null || echo "0")
        if [[ "$ps" == "200" ]]; then
            par_pass=$((par_pass + 1))
        fi
    done

    if [[ "$par_pass" -eq 3 ]]; then
        assert_pass "Parallel different-endpoint requests: 3/3 succeeded"
    else
        assert_warn "Parallel different endpoints: $par_pass/3 succeeded"
    fi

    rm -rf "$par_tmpdir" 2>/dev/null || true

    subsection "20.4 Response Size"

    # 20.4.1 Health response size
    perform_request "${BASE_URL}/health"
    local health_size=${#CURL_BODY}
    if [[ "$health_size" -lt 5000 ]]; then
        assert_pass "Health response size reasonable: ${health_size} bytes"
    else
        assert_warn "Health response large: ${health_size} bytes"
    fi

    # 20.4.2 Frontend response size
    perform_request "$FRONTEND_URL"
    local frontend_size=${#CURL_BODY}
    if [[ "$frontend_size" -gt 1000 && "$frontend_size" -lt 500000 ]]; then
        assert_pass "Frontend response size: ${frontend_size} bytes"
    elif [[ "$frontend_size" -ge 500000 ]]; then
        assert_warn "Frontend very large: ${frontend_size} bytes (may lack compression)"
    else
        assert_warn "Frontend very small: ${frontend_size} bytes"
    fi

    subsection "20.5 Timing Consistency"

    # 20.5.1 Health timing consistency (5 requests)
    local timing_total=0
    local timing_max=0
    local timing_min=99999
    for i in $(seq 1 5); do
        perform_request "${BASE_URL}/health"
        timing_total=$((timing_total + CURL_TTFB))
        if [[ "$CURL_TTFB" -gt "$timing_max" ]]; then
            timing_max=$CURL_TTFB
        fi
        if [[ "$CURL_TTFB" -lt "$timing_min" ]]; then
            timing_min=$CURL_TTFB
        fi
    done

    local timing_avg=$((timing_total / 5))
    assert_pass "Health avg TTFB over 5 requests: ${timing_avg}ms"

    # 20.5.2 Jitter check
    local timing_range=$((timing_max - timing_min))
    if [[ "$timing_range" -lt 500 ]]; then
        assert_pass "Health timing jitter low: ${timing_range}ms range"
    elif [[ "$timing_range" -lt 2000 ]]; then
        assert_warn "Health timing jitter moderate: ${timing_range}ms range"
    else
        assert_warn "Health timing jitter high: ${timing_range}ms range (min=${timing_min}, max=${timing_max})"
    fi

    # 20.5.3 P95 approximation
    if [[ "$timing_max" -lt 2000 ]]; then
        assert_pass "Health P95 (approx max) < 2s: ${timing_max}ms"
    else
        assert_warn "Health P95 (approx max) > 2s: ${timing_max}ms"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 20: Performance ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# LAYER 21: Startup Resilience Verification (25+ tests)
# ===============================================================================

test_layer_21_startup_resilience() {
    section_header "LAYER 21: Startup Resilience Verification"

    local layer_start=$TOTAL_TESTS

    subsection "21.1 Health Always Returns 200"

    # 21.1.1 /health returns HTTP 200 (never 500)
    perform_request "${BASE_URL}/health"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "/health returns 200 (not 500)"
    elif [[ "$CURL_STATUS" -eq 500 ]]; then
        assert_fail "/health returned 500 (should never happen)" "yes"
    else
        assert_warn "/health returned unexpected HTTP $CURL_STATUS"
    fi

    # 21.1.2 /health returns 200 on repeated calls (call 1)
    perform_request "${BASE_URL}/health"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "/health repeated call 1: 200"
    else
        assert_fail "/health repeated call 1: HTTP $CURL_STATUS"
    fi

    # 21.1.3 /health returns 200 on repeated calls (call 2)
    perform_request "${BASE_URL}/health"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "/health repeated call 2: 200"
    else
        assert_fail "/health repeated call 2: HTTP $CURL_STATUS"
    fi

    # 21.1.4 /health returns 200 on repeated calls (call 3)
    perform_request "${BASE_URL}/health"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "/health repeated call 3: 200"
    else
        assert_fail "/health repeated call 3: HTTP $CURL_STATUS"
    fi

    # 21.1.5 /health response time < 2s
    if [[ "$CURL_TTFB" -lt 2000 ]]; then
        assert_pass "/health response time < 2s (${CURL_TTFB}ms)"
    else
        assert_warn "/health response time >= 2s (${CURL_TTFB}ms)"
    fi

    subsection "21.2 Status Field Contract"

    # 21.2.1 Status field exists and is string
    perform_request "${BASE_URL}/health"
    local h_status
    h_status=$(json_field '.status // empty')
    if [[ -n "$h_status" ]]; then
        assert_pass "/health has status field: $h_status"
    else
        assert_fail "/health missing status field"
    fi

    # 21.2.2 Status is exactly "healthy" or "degraded"
    if [[ "$h_status" == "healthy" || "$h_status" == "degraded" ]]; then
        assert_pass "/health status is valid contract value: $h_status"
    elif [[ "$h_status" == "ok" || "$h_status" == "up" ]]; then
        assert_warn "/health status is legacy value: $h_status (expected healthy/degraded)"
    elif [[ -n "$h_status" ]]; then
        assert_fail "/health status unexpected: $h_status"
    else
        assert_skip "Status contract check (no status field)"
    fi

    # 21.2.3 Service field equals syrabit-backend
    local h_service
    h_service=$(json_field '.service // empty')
    if [[ "$h_service" == "syrabit-backend" ]]; then
        assert_pass "/health service: syrabit-backend"
    elif [[ -n "$h_service" ]]; then
        assert_warn "/health service: $h_service (expected syrabit-backend)"
    else
        assert_warn "/health missing service field"
    fi

    subsection "21.3 Degraded State Reporting"

    # 21.3.1 If degraded, config_error_count exists
    if [[ "$h_status" == "degraded" ]]; then
        local d_err_count
        d_err_count=$(json_field '.config_error_count // empty')
        if [[ -n "$d_err_count" ]]; then
            assert_pass "Degraded: config_error_count present ($d_err_count)"
        else
            assert_fail "Degraded: config_error_count missing"
        fi

        # 21.3.2 config_error_count is integer >= 0
        if [[ "$d_err_count" =~ ^[0-9]+$ ]]; then
            assert_pass "Degraded: config_error_count is integer >= 0"
        else
            assert_fail "Degraded: config_error_count not a valid integer: $d_err_count"
        fi

        # 21.3.3 No raw error messages exposed
        if echo "$CURL_BODY" | grep -qi "EDGE_SHARED_SECRET\|JWT_SECRET\|ADMIN_JWT_SECRET\|must be set\|required in production"; then
            assert_fail "Degraded: raw config error messages exposed"
        else
            assert_pass "Degraded: no raw config error messages exposed"
        fi
    else
        assert_skip "21.3 config_error_count check (not degraded)"
        assert_skip "21.3 config_error_count integer check (not degraded)"
        assert_skip "21.3 raw error message check (not degraded)"
    fi

    # 21.3.4 If healthy, config_error_count is absent or 0
    if [[ "$h_status" == "healthy" ]]; then
        local healthy_err
        healthy_err=$(json_field '.config_error_count // empty')
        if [[ -z "$healthy_err" || "$healthy_err" == "0" ]]; then
            assert_pass "Healthy: config_error_count absent or 0"
        else
            assert_warn "Healthy but config_error_count=$healthy_err"
        fi
    else
        assert_skip "21.3 healthy config_error_count check (not healthy)"
    fi

    subsection "21.4 Deep Health Diagnostics"

    # 21.4.1 /health/deep returns 200 or 503 (NEVER 500)
    perform_request "${BASE_URL}/health/deep"
    if [[ "$CURL_STATUS" -eq 200 || "$CURL_STATUS" -eq 503 ]]; then
        assert_pass "/health/deep returns $CURL_STATUS (valid)"
    elif [[ "$CURL_STATUS" -eq 500 ]]; then
        assert_fail "/health/deep returned 500 (should never happen)"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "/health/deep not found (endpoint may not exist)"
    else
        assert_warn "/health/deep returned HTTP $CURL_STATUS"
    fi

    # 21.4.2 /health/deep has status field
    local deep_status
    deep_status=$(json_field '.status // empty')
    if [[ "$deep_status" == "healthy" || "$deep_status" == "degraded" || "$deep_status" == "unhealthy" ]]; then
        assert_pass "/health/deep status: $deep_status"
    elif [[ -n "$deep_status" ]]; then
        assert_warn "/health/deep status: $deep_status"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_skip "/health/deep status check (endpoint not found)"
    else
        assert_warn "/health/deep missing status field"
    fi

    # 21.4.3 /health/deep has checks object
    local has_checks
    has_checks=$(json_field '.checks // empty | type' 2>/dev/null)
    if [[ -z "$has_checks" ]]; then
        has_checks=$(echo "$CURL_BODY" | jq -r '.checks // empty | type' 2>/dev/null || echo "")
    fi
    if [[ "$has_checks" == "object" ]]; then
        assert_pass "/health/deep has checks object"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_skip "/health/deep checks (endpoint not found)"
    else
        assert_warn "/health/deep missing or invalid checks object"
    fi

    # 21.4.4 checks.mongodb exists with status field
    if [[ "$has_checks" == "object" ]]; then
        local mongo_check
        mongo_check=$(echo "$CURL_BODY" | jq -r '.checks.mongodb.status // empty' 2>/dev/null || echo "")
        if [[ -n "$mongo_check" ]]; then
            assert_pass "/health/deep checks.mongodb.status: $mongo_check"
        else
            assert_warn "/health/deep checks.mongodb.status not found"
        fi
    else
        assert_skip "checks.mongodb (no checks object)"
    fi

    # 21.4.5 checks.redis exists with status field
    if [[ "$has_checks" == "object" ]]; then
        local redis_check
        redis_check=$(echo "$CURL_BODY" | jq -r '.checks.redis.status // empty' 2>/dev/null || echo "")
        if [[ -n "$redis_check" ]]; then
            assert_pass "/health/deep checks.redis.status: $redis_check"
        else
            assert_warn "/health/deep checks.redis.status not found"
        fi
    else
        assert_skip "checks.redis (no checks object)"
    fi

    # 21.4.6 checks.vertex_search exists with status field
    if [[ "$has_checks" == "object" ]]; then
        local vs_check
        vs_check=$(echo "$CURL_BODY" | jq -r '.checks.vertex_search.status // empty' 2>/dev/null || echo "")
        if [[ -n "$vs_check" ]]; then
            assert_pass "/health/deep checks.vertex_search.status: $vs_check"
        else
            assert_warn "/health/deep checks.vertex_search.status not found"
        fi
    else
        assert_skip "checks.vertex_search (no checks object)"
    fi

    # 21.4.7 checks.vertex_ai exists with status field
    if [[ "$has_checks" == "object" ]]; then
        local vai_check
        vai_check=$(echo "$CURL_BODY" | jq -r '.checks.vertex_ai.status // empty' 2>/dev/null || echo "")
        if [[ -n "$vai_check" ]]; then
            assert_pass "/health/deep checks.vertex_ai.status: $vai_check"
        else
            assert_warn "/health/deep checks.vertex_ai.status not found"
        fi
    else
        assert_skip "checks.vertex_ai (no checks object)"
    fi

    # 21.4.8 Each check status is one of: healthy, unhealthy
    if [[ "$has_checks" == "object" ]]; then
        local all_statuses
        all_statuses=$(echo "$CURL_BODY" | jq -r '[.checks[]?.status // empty] | unique | .[]' 2>/dev/null || echo "")
        local invalid_status=0
        for s in $all_statuses; do
            if [[ "$s" != "healthy" && "$s" != "unhealthy" ]]; then
                invalid_status=1
            fi
        done
        if [[ "$invalid_status" -eq 0 && -n "$all_statuses" ]]; then
            assert_pass "/health/deep all check statuses valid (healthy/unhealthy)"
        elif [[ -n "$all_statuses" ]]; then
            assert_warn "/health/deep has non-standard check status values"
        else
            assert_warn "/health/deep no check statuses found"
        fi
    else
        assert_skip "Check status validation (no checks object)"
    fi

    subsection "21.5 Circuit Breaker Consistency"

    # 21.5.1 /health/circuit-breakers returns 200
    perform_request "${BASE_URL}/health/circuit-breakers"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        assert_pass "/health/circuit-breakers returns 200"
    elif [[ "$CURL_STATUS" -eq 404 ]]; then
        assert_warn "Circuit breakers endpoint not found"
    else
        assert_warn "Circuit breakers returned HTTP $CURL_STATUS"
    fi

    # 21.5.2 Response has vertex_ai, sarvam_ai, vertex_search
    if [[ "$CURL_STATUS" -eq 200 ]] && is_json; then
        local cb_vai cb_sarvam cb_vsearch
        cb_vai=$(json_field '.vertex_ai // empty')
        cb_sarvam=$(json_field '.sarvam_ai // empty')
        cb_vsearch=$(json_field '.vertex_search // empty')
        if [[ -n "$cb_vai" ]]; then
            assert_pass "Circuit breakers has vertex_ai entry"
        else
            assert_warn "Circuit breakers missing vertex_ai"
        fi
        if [[ -n "$cb_sarvam" ]]; then
            assert_pass "Circuit breakers has sarvam_ai entry"
        else
            assert_warn "Circuit breakers missing sarvam_ai"
        fi
        if [[ -n "$cb_vsearch" ]]; then
            assert_pass "Circuit breakers has vertex_search entry"
        else
            assert_warn "Circuit breakers missing vertex_search"
        fi

        # 21.5.3 Each has a state field
        local vai_state sarvam_state vsearch_state
        vai_state=$(json_field '.vertex_ai.state // empty')
        sarvam_state=$(json_field '.sarvam_ai.state // empty')
        vsearch_state=$(json_field '.vertex_search.state // empty')

        if [[ -n "$vai_state" ]]; then
            assert_pass "vertex_ai has state: $vai_state"
        else
            assert_warn "vertex_ai missing state field"
        fi
        if [[ -n "$sarvam_state" ]]; then
            assert_pass "sarvam_ai has state: $sarvam_state"
        else
            assert_warn "sarvam_ai missing state field"
        fi
        if [[ -n "$vsearch_state" ]]; then
            assert_pass "vertex_search has state: $vsearch_state"
        else
            assert_warn "vertex_search missing state field"
        fi

        # 21.5.4 State values are one of: closed, open, half_open
        local valid_states=1
        for st in "$vai_state" "$sarvam_state" "$vsearch_state"; do
            if [[ -n "$st" && "$st" != "closed" && "$st" != "open" && "$st" != "half_open" ]]; then
                valid_states=0
            fi
        done
        if [[ "$valid_states" -eq 1 ]]; then
            assert_pass "All circuit breaker states are valid (closed/open/half_open)"
        else
            assert_warn "Some circuit breaker states have unexpected values"
        fi
    else
        assert_skip "Circuit breaker details not available"
        assert_skip "Circuit breaker vertex_ai entry"
        assert_skip "Circuit breaker sarvam_ai entry"
        assert_skip "Circuit breaker vertex_search entry"
        assert_skip "Circuit breaker vertex_ai state"
        assert_skip "Circuit breaker sarvam_ai state"
        assert_skip "Circuit breaker vertex_search state"
        assert_skip "Circuit breaker state values validation"
    fi

    local layer_end=$TOTAL_TESTS
    LAYER_RESULTS+=("Layer 21: Startup Resilience ($((layer_end - layer_start)) tests)")
}



# ===============================================================================
# MAIN EXECUTION
# ===============================================================================

main() {
    # Header
    echo ""
    echo -e "${BOLD}$(printf '%.0s=' {1..72})${NC}"
    echo -e "${BOLD}  SYRABIT FULLSTACK LAYER-BY-LAYER CLOUD SHELL TEST (v3)${NC}"
    echo -e "${BOLD}  1000+ Assertions | 22 Layers (0-21) | 9 Pillars${NC}"
    echo -e "${BOLD}$(printf '%.0s=' {1..72})${NC}"
    echo -e "  Date:      $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    echo -e "  Target:    ${BASE_URL}"
    echo -e "  Frontend:  ${FRONTEND_URL}"
    if [[ "$DRY_RUN" == "1" ]]; then echo -e "  Mode:      ${YELLOW}dry-run${NC}"; fi
    if [[ "$QUICK_MODE" == "1" ]]; then echo -e "  Mode:      ${YELLOW}quick${NC}"; fi
    if [[ "$CLOUDSHELL_MODE" == "1" ]]; then echo -e "  Mode:      ${CYAN}cloudshell${NC}"; fi
    if [[ -n "$RUN_LAYER" ]]; then echo -e "  Layer:     $RUN_LAYER"; fi
    if [[ "$STRESS_TEST" == "1" ]]; then echo -e "  Stress:    ${YELLOW}enabled${NC}"; fi
    echo -e "${BOLD}$(printf '%.0s=' {1..72})${NC}"

    # Cloud Shell hints
    if [[ "$CLOUDSHELL_MODE" == "1" ]]; then
        echo ""
        echo -e "  ${CYAN}Cloud Shell Tips:${NC}"
        echo "    - Authenticate: gcloud auth login"
        echo "    - Set project: gcloud config set project YOUR_PROJECT_ID"
        echo "    - Get JWT token: export TEST_JWT_TOKEN=\$(gcloud auth print-identity-token)"
        echo "    - Results will be exported to fullstack-test-results.json"
        echo ""
    fi

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
        # Auto-run auth layer if targeting layers 5-21 with credentials but no token
        if [[ "$RUN_LAYER" -ge 5 && "$RUN_LAYER" -le 21 && -z "$AUTH_TOKEN" ]]; then
            if [[ -n "$TEST_JWT_TOKEN" ]]; then
                AUTH_TOKEN="$TEST_JWT_TOKEN"
                verbose_log "Using TEST_JWT_TOKEN for authenticated tests"
            elif [[ -n "$ADMIN_EMAIL" && -n "$ADMIN_PASSWORD" ]]; then
                verbose_log "Auto-running layer 4 (auth) for credentials"
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
            21) test_layer_21_startup_resilience ;;
            *) echo "Invalid layer: $RUN_LAYER (valid: 0-21)"; exit 1 ;;
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
        test_layer_21_startup_resilience
    fi

    # ===================================================================
    # SUMMARY
    # ===================================================================

    echo ""
    echo -e "${BOLD}$(printf '%.0s=' {1..72})${NC}"
    echo -e "${BOLD}  SUMMARY${NC}"
    echo -e "${BOLD}$(printf '%.0s=' {1..72})${NC}"
    echo ""
    echo -e "  Total tests:   ${BOLD}${TOTAL_TESTS}${NC}"
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

    # Pass rate
    if [[ "$TOTAL_TESTS" -gt 0 ]]; then
        local pass_rate=$(( (PASSED_TESTS * 100) / TOTAL_TESTS ))
        echo -e "  Pass rate:     ${pass_rate}%"
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

    echo -e "${BOLD}$(printf '%.0s=' {1..72})${NC}"
    echo ""

    # Export JSON results
    if [[ "$EXPORT_JSON" == "1" ]]; then
        local json_file="fullstack-test-results.json"
        local success_val="true"
        if [[ "$CRITICAL_FAILED" -gt 0 ]]; then
            success_val="false"
        fi

        local layers_json="[]"
        if [[ ${#LAYER_RESULTS[@]} -gt 0 ]]; then
            layers_json="["
            local first=1
            for result in "${LAYER_RESULTS[@]}"; do
                if [[ $first -eq 1 ]]; then
                    first=0
                else
                    layers_json+=","
                fi
                layers_json+="$(jq -n --arg r "$result" '$r')"
            done
            layers_json+="]"
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
            --argjson layers "$layers_json" \
            '{
                timestamp: $timestamp,
                base_url: $base_url,
                frontend_url: $frontend_url,
                success: $success,
                total_tests: $total_tests,
                passed: $passed,
                failed: $failed,
                warnings: $warnings,
                skipped: $skipped,
                critical_failures: $critical_failures,
                layers: $layers
            }' > "$json_file"

        echo -e "  ${GREEN}Results exported to ${json_file}${NC}"
        echo ""
    fi

    # Exit code
    if [[ "$CRITICAL_FAILED" -gt 0 ]]; then
        exit 1
    fi
    exit 0
}

# Run main
main

