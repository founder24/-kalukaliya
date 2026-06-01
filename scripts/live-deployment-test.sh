#!/usr/bin/env bash
# ===========================================================================
# SYRABIT COMPREHENSIVE LIVE DEPLOYMENT TEST
# ===========================================================================
#
# Tests all layers of the live production deployment:
#   - Backend API (Cloud Run)
#   - Frontend/Edge (Cloudflare Workers)
#
# Usage:
#   ./scripts/live-deployment-test.sh
#   ./scripts/live-deployment-test.sh --verbose
#   ./scripts/live-deployment-test.sh --category health,auth,seo
#   ./scripts/live-deployment-test.sh --backend-url https://custom-backend.run.app
#
# Exit code: 0 if all critical tests pass, 1 if any critical test fails
# ===========================================================================

set -uo pipefail

# --- Defaults ---
BACKEND_URL="https://syrabit-backend-851687450401.asia-south1.run.app"
FRONTEND_URL="https://syrabit.ai"
VERBOSE=0
CATEGORIES=""
ALL_CATEGORIES="health,auth,content,chat,seo,payments,admin,edge,security,performance,webhook"

# --- Counters ---
TOTAL=0
PASSED=0
FAILED=0
WARNINGS=0
CRITICAL_FAILURES=0

# --- Colors ---
if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' BOLD='' NC=''
fi

# --- Argument Parsing ---
show_help() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --backend-url URL   Backend URL (default: $BACKEND_URL)"
    echo "  --frontend-url URL  Frontend URL (default: $FRONTEND_URL)"
    echo "  --verbose           Show detailed curl output"
    echo "  --category LIST     Comma-separated categories to run"
    echo "                      Available: $ALL_CATEGORIES"
    echo "  --help              Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --verbose"
    echo "  $0 --category health,auth,seo"
    echo "  $0 --backend-url https://staging-api.example.com"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backend-url)  BACKEND_URL="$2"; shift 2 ;;
        --frontend-url) FRONTEND_URL="$2"; shift 2 ;;
        --verbose)      VERBOSE=1; shift ;;
        --category)     CATEGORIES="$2"; shift 2 ;;
        --help|-h)      show_help ;;
        *) echo "Unknown option: $1"; show_help ;;
    esac
done

if [[ -z "$CATEGORIES" ]]; then
    CATEGORIES="$ALL_CATEGORIES"
fi

# --- Utility Functions ---
verbose() {
    if [[ "$VERBOSE" -eq 1 ]]; then
        echo -e "    ${BLUE}[verbose]${NC} $1"
    fi
}

pass_test() {
    TOTAL=$((TOTAL + 1))
    PASSED=$((PASSED + 1))
    echo -e "  ${GREEN}PASS${NC} $1"
}

fail_test() {
    local critical="${2:-no}"
    TOTAL=$((TOTAL + 1))
    FAILED=$((FAILED + 1))
    if [[ "$critical" == "yes" ]]; then
        CRITICAL_FAILURES=$((CRITICAL_FAILURES + 1))
        echo -e "  ${RED}FAIL [CRITICAL]${NC} $1"
    else
        echo -e "  ${RED}FAIL${NC} $1"
    fi
}

warn_test() {
    TOTAL=$((TOTAL + 1))
    WARNINGS=$((WARNINGS + 1))
    echo -e "  ${YELLOW}WARN${NC} $1"
}

# Perform a curl request, sets: HTTP_CODE, RESPONSE_BODY, RESPONSE_HEADERS, RESPONSE_TIME_MS
do_request() {
    local method="$1"
    local url="$2"
    shift 2
    local extra_args=("$@")

    local tmp_body tmp_headers
    tmp_body=$(mktemp)
    tmp_headers=$(mktemp)

    local curl_args=(-s -S -X "$method" -o "$tmp_body" -D "$tmp_headers" -w '%{http_code}\n%{time_total}' --max-time 30)
    if [[ ${#extra_args[@]} -gt 0 ]]; then
        curl_args+=("${extra_args[@]}")
    fi
    curl_args+=("$url")

    verbose "curl -X $method $url ${extra_args[*]:-}"

    local output
    output=$(curl "${curl_args[@]}" 2>/dev/null) || output=$'000\n0'

    HTTP_CODE=$(echo "$output" | head -1)
    local time_sec
    time_sec=$(echo "$output" | tail -1)
    # Convert to ms using awk (no bc dependency)
    RESPONSE_TIME_MS=$(echo "$time_sec" | awk '{printf "%d", $1 * 1000}')
    RESPONSE_BODY=$(cat "$tmp_body" 2>/dev/null || echo "")
    RESPONSE_HEADERS=$(cat "$tmp_headers" 2>/dev/null || echo "")

    rm -f "$tmp_body" "$tmp_headers"
}

# Check if a category should run
should_run() {
    echo ",$CATEGORIES," | grep -q ",$1,"
}

# --- Header ---
echo ""
echo -e "${BOLD}=================================================================${NC}"
echo -e "${BOLD}  SYRABIT LIVE DEPLOYMENT TEST${NC}"
echo -e "${BOLD}=================================================================${NC}"
echo "  Date:       $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "  Backend:    $BACKEND_URL"
echo "  Frontend:   $FRONTEND_URL"
echo "  Categories: $CATEGORIES"
echo -e "${BOLD}=================================================================${NC}"
echo ""

# ===========================================================================
# CATEGORY: health
# ===========================================================================
if should_run "health"; then
    echo -e "${BOLD}--- [health] Health & Basics ---${NC}"
    echo ""

    # GET /health - expect 200
    do_request GET "$BACKEND_URL/health"
    verbose "Status: $HTTP_CODE, Body: $RESPONSE_BODY"
    if [[ "$HTTP_CODE" == "200" ]] && echo "$RESPONSE_BODY" | grep -qi "status"; then
        pass_test "/health returns 200 with status field"
    elif [[ "$HTTP_CODE" == "200" ]]; then
        warn_test "/health returns 200 but no 'status' in response"
    else
        fail_test "/health returned HTTP $HTTP_CODE (expected 200)" "yes"
    fi

    # GET /health/deep - expect 200 or 503
    do_request GET "$BACKEND_URL/health/deep"
    verbose "Status: $HTTP_CODE, Body: ${RESPONSE_BODY:0:200}"
    if [[ "$HTTP_CODE" == "200" || "$HTTP_CODE" == "503" ]]; then
        # Verify JSON response
        if echo "$RESPONSE_BODY" | python3 -m json.tool >/dev/null 2>&1 || echo "$RESPONSE_BODY" | grep -q '{'; then
            pass_test "/health/deep returns $HTTP_CODE with JSON response"
        else
            warn_test "/health/deep returns $HTTP_CODE but response is not JSON"
        fi
    else
        fail_test "/health/deep returned HTTP $HTTP_CODE (expected 200 or 503)"
    fi

    # GET /health/circuit-breakers - expect 200
    do_request GET "$BACKEND_URL/health/circuit-breakers"
    verbose "Status: $HTTP_CODE"
    if [[ "$HTTP_CODE" == "200" ]]; then
        if echo "$RESPONSE_BODY" | python3 -m json.tool >/dev/null 2>&1 || echo "$RESPONSE_BODY" | grep -q '{'; then
            pass_test "/health/circuit-breakers returns 200 with JSON"
        else
            warn_test "/health/circuit-breakers returns 200 but not JSON"
        fi
    elif [[ "$HTTP_CODE" == "404" ]]; then
        warn_test "/health/circuit-breakers not found (may not be deployed)"
    else
        fail_test "/health/circuit-breakers returned HTTP $HTTP_CODE (expected 200)"
    fi

    echo ""
fi

# ===========================================================================
# CATEGORY: auth
# ===========================================================================
if should_run "auth"; then
    echo -e "${BOLD}--- [auth] Authentication Flow ---${NC}"
    echo ""

    # POST /api/v1/auth/signup with invalid data - expect 422
    do_request POST "$BACKEND_URL/api/v1/auth/signup" \
        -H "Content-Type: application/json" \
        -d '{"bad":"data"}'
    if [[ "$HTTP_CODE" == "422" ]]; then
        pass_test "signup with invalid data returns 422"
    else
        fail_test "signup with invalid data returned $HTTP_CODE (expected 422)" "yes"
    fi

    # POST /api/v1/auth/login with wrong creds - expect 401
    do_request POST "$BACKEND_URL/api/v1/auth/login" \
        -H "Content-Type: application/json" \
        -d '{"email":"nonexistent@test.invalid","password":"wrongpass123"}'
    if [[ "$HTTP_CODE" == "401" ]]; then
        pass_test "login with wrong creds returns 401"
    elif [[ "$HTTP_CODE" == "422" ]]; then
        warn_test "login with wrong creds returns 422 (validation before auth check)"
    else
        fail_test "login with wrong creds returned $HTTP_CODE (expected 401)" "yes"
    fi

    # POST /api/v1/auth/login with missing body - expect 422
    do_request POST "$BACKEND_URL/api/v1/auth/login" \
        -H "Content-Type: application/json" \
        -d '{}'
    if [[ "$HTTP_CODE" == "422" ]]; then
        pass_test "login with empty body returns 422"
    else
        fail_test "login with empty body returned $HTTP_CODE (expected 422)" "yes"
    fi

    # POST /api/v1/auth/forgot-password with nonexistent email - expect 200
    do_request POST "$BACKEND_URL/api/v1/auth/forgot-password" \
        -H "Content-Type: application/json" \
        -d '{"email":"nobody-exists-here@test.invalid"}'
    if [[ "$HTTP_CODE" == "200" ]]; then
        pass_test "forgot-password does not reveal if email exists (returns 200)"
    elif [[ "$HTTP_CODE" == "422" ]]; then
        warn_test "forgot-password returns 422 (may have validation)"
    else
        fail_test "forgot-password returned $HTTP_CODE (expected 200 to not leak info)" "yes"
    fi

    # POST /api/v1/auth/refresh with invalid token - expect 401 or 422
    do_request POST "$BACKEND_URL/api/v1/auth/refresh" \
        -H "Content-Type: application/json" \
        -d '{"refresh_token":"invalid-token-value"}'
    if [[ "$HTTP_CODE" == "401" || "$HTTP_CODE" == "422" ]]; then
        pass_test "refresh with invalid token returns $HTTP_CODE"
    else
        fail_test "refresh with invalid token returned $HTTP_CODE (expected 401 or 422)" "yes"
    fi

    # Rate limiting: send 15 rapid requests
    echo -e "  ${BLUE}Testing rate limiting (15 rapid requests to /api/v1/auth/login)...${NC}"
    RATE_LIMITED=0
    for i in $(seq 1 15); do
        do_request POST "$BACKEND_URL/api/v1/auth/login" \
            -H "Content-Type: application/json" \
            -d '{"email":"ratelimit-test@test.invalid","password":"wrong"}'
        if [[ "$HTTP_CODE" == "429" ]]; then
            RATE_LIMITED=1
            break
        fi
    done
    if [[ "$RATE_LIMITED" -eq 1 ]]; then
        pass_test "rate limiting active (got 429 after rapid requests)"
    else
        warn_test "rate limiting not triggered after 15 requests (may need more or different IP)"
    fi

    echo ""
fi
# ===========================================================================
# CATEGORY: content
# ===========================================================================
if should_run "content"; then
    echo -e "${BOLD}--- [content] Content Delivery ---${NC}"
    echo ""

    # GET content render endpoint
    do_request GET "$BACKEND_URL/api/v1/content/render/seba/10/science/chemical-reactions"
    if [[ "$HTTP_CODE" == "200" || "$HTTP_CODE" == "404" ]]; then
        pass_test "content render returns $HTTP_CODE (valid response)"
    else
        fail_test "content render returned $HTTP_CODE (expected 200 or 404)"
    fi

    # Path traversal injection test
    do_request GET "$BACKEND_URL/api/v1/content/render/INJECTION/../../etc/passwd"
    if [[ "$HTTP_CODE" == "400" || "$HTTP_CODE" == "404" || "$HTTP_CODE" == "422" ]]; then
        pass_test "path traversal blocked (returned $HTTP_CODE)" 
    else
        fail_test "path traversal NOT blocked (returned $HTTP_CODE, expected 400)" "yes"
    fi

    # Subject listing endpoint
    do_request GET "$BACKEND_URL/api/v1/content/subject/seba/10/science"
    if [[ "$HTTP_CODE" == "200" || "$HTTP_CODE" == "404" ]]; then
        pass_test "content subject returns $HTTP_CODE"
    else
        fail_test "content subject returned $HTTP_CODE (expected 200 or 404)"
    fi

    # Check Cache-Control headers on content responses
    do_request GET "$BACKEND_URL/api/v1/content/render/seba/10/science/chemical-reactions"
    if echo "$RESPONSE_HEADERS" | grep -qi "cache-control"; then
        pass_test "Cache-Control header present on content responses"
    else
        warn_test "Cache-Control header missing on content responses"
    fi

    echo ""
fi

# ===========================================================================
# CATEGORY: chat
# ===========================================================================
if should_run "chat"; then
    echo -e "${BOLD}--- [chat] Chat/AI Pipeline ---${NC}"
    echo ""

    # POST /api/v1/chat/ without auth - expect 401 or 403
    do_request POST "$BACKEND_URL/api/v1/chat/" \
        -H "Content-Type: application/json" \
        -d '{"message":"test","language":"en"}'
    if [[ "$HTTP_CODE" == "401" || "$HTTP_CODE" == "403" ]]; then
        pass_test "chat without auth returns $HTTP_CODE"
    else
        fail_test "chat without auth returned $HTTP_CODE (expected 401 or 403)" "yes"
    fi

    # POST /api/v1/chat/stream without auth - expect 401 or 403
    do_request POST "$BACKEND_URL/api/v1/chat/stream" \
        -H "Content-Type: application/json" \
        -d '{"message":"test","language":"en","stream":true}'
    if [[ "$HTTP_CODE" == "401" || "$HTTP_CODE" == "403" ]]; then
        pass_test "chat/stream without auth returns $HTTP_CODE"
    else
        fail_test "chat/stream without auth returned $HTTP_CODE (expected 401 or 403)" "yes"
    fi

    # GET /api/v1/chat/history without auth - expect 401 or 403
    do_request GET "$BACKEND_URL/api/v1/chat/history"
    if [[ "$HTTP_CODE" == "401" || "$HTTP_CODE" == "403" ]]; then
        pass_test "chat/history without auth returns $HTTP_CODE"
    else
        fail_test "chat/history without auth returned $HTTP_CODE (expected 401 or 403)" "yes"
    fi

    echo ""
fi

# ===========================================================================
# CATEGORY: seo
# ===========================================================================
if should_run "seo"; then
    echo -e "${BOLD}--- [seo] SEO & Sitemaps ---${NC}"
    echo ""

    # GET /api/v1/seo/sitemap.xml
    do_request GET "$BACKEND_URL/api/v1/seo/sitemap.xml"
    if [[ "$HTTP_CODE" == "200" ]]; then
        if echo "$RESPONSE_HEADERS" | grep -qi "xml"; then
            pass_test "sitemap.xml returns 200 with XML content-type"
        else
            warn_test "sitemap.xml returns 200 but Content-Type may not be XML"
        fi
    else
        fail_test "sitemap.xml returned $HTTP_CODE (expected 200)"
    fi

    # GET /api/v1/seo/sitemap-static.xml
    do_request GET "$BACKEND_URL/api/v1/seo/sitemap-static.xml"
    if [[ "$HTTP_CODE" == "200" ]]; then
        pass_test "sitemap-static.xml returns 200"
    else
        fail_test "sitemap-static.xml returned $HTTP_CODE (expected 200)"
    fi

    # GET /api/v1/seo/sitemap-subjects.xml
    do_request GET "$BACKEND_URL/api/v1/seo/sitemap-subjects.xml"
    if [[ "$HTTP_CODE" == "200" ]]; then
        pass_test "sitemap-subjects.xml returns 200"
    else
        fail_test "sitemap-subjects.xml returned $HTTP_CODE (expected 200)"
    fi

    # GET /api/v1/seo/sitemap-chapters.xml
    do_request GET "$BACKEND_URL/api/v1/seo/sitemap-chapters.xml"
    if [[ "$HTTP_CODE" == "200" ]]; then
        pass_test "sitemap-chapters.xml returns 200"
    else
        fail_test "sitemap-chapters.xml returned $HTTP_CODE (expected 200)"
    fi

    # GET /api/v1/seo/sitemap-topics.xml
    do_request GET "$BACKEND_URL/api/v1/seo/sitemap-topics.xml"
    if [[ "$HTTP_CODE" == "200" ]]; then
        pass_test "sitemap-topics.xml returns 200"
    else
        fail_test "sitemap-topics.xml returned $HTTP_CODE (expected 200)"
    fi

    # Verify XML is well-formed
    do_request GET "$BACKEND_URL/api/v1/seo/sitemap.xml"
    if echo "$RESPONSE_BODY" | grep -q '<?xml\|<urlset\|<sitemapindex'; then
        pass_test "sitemap.xml contains valid XML markers"
    else
        fail_test "sitemap.xml does not contain expected XML structure"
    fi

    echo ""
fi

# ===========================================================================
# CATEGORY: payments
# ===========================================================================
if should_run "payments"; then
    echo -e "${BOLD}--- [payments] Payment Endpoints ---${NC}"
    echo ""

    # POST /api/v1/payments/create-order without auth
    do_request POST "$BACKEND_URL/api/v1/payments/create-order" \
        -H "Content-Type: application/json" \
        -d '{"plan_id":"test"}'
    if [[ "$HTTP_CODE" == "401" || "$HTTP_CODE" == "403" ]]; then
        pass_test "payments/create-order without auth returns $HTTP_CODE"
    elif [[ "$HTTP_CODE" == "404" ]]; then
        warn_test "payments/create-order endpoint not found (may not be deployed)"
    else
        fail_test "payments/create-order without auth returned $HTTP_CODE (expected 401 or 403)" "yes"
    fi

    # POST /api/v1/payments/verify without auth
    do_request POST "$BACKEND_URL/api/v1/payments/verify" \
        -H "Content-Type: application/json" \
        -d '{"order_id":"test","payment_id":"test","signature":"test"}'
    if [[ "$HTTP_CODE" == "401" || "$HTTP_CODE" == "403" ]]; then
        pass_test "payments/verify without auth returns $HTTP_CODE"
    elif [[ "$HTTP_CODE" == "404" ]]; then
        warn_test "payments/verify endpoint not found (may not be deployed)"
    else
        fail_test "payments/verify without auth returned $HTTP_CODE (expected 401 or 403)" "yes"
    fi

    echo ""
fi

# ===========================================================================
# CATEGORY: admin
# ===========================================================================
if should_run "admin"; then
    echo -e "${BOLD}--- [admin] Admin Panel ---${NC}"
    echo ""

    # GET /api/v1/admin/verify without cookie
    do_request GET "$BACKEND_URL/api/v1/admin/verify"
    if [[ "$HTTP_CODE" == "401" || "$HTTP_CODE" == "403" ]]; then
        pass_test "admin/verify without cookie returns $HTTP_CODE"
    elif [[ "$HTTP_CODE" == "404" ]]; then
        warn_test "admin/verify endpoint not found"
    else
        fail_test "admin/verify without cookie returned $HTTP_CODE (expected 401)" "yes"
    fi

    # POST /api/v1/admin/login with wrong creds
    do_request POST "$BACKEND_URL/api/v1/admin/login" \
        -H "Content-Type: application/json" \
        -d '{"username":"wrongadmin","password":"wrongpass"}'
    if [[ "$HTTP_CODE" == "401" || "$HTTP_CODE" == "403" ]]; then
        pass_test "admin/login with wrong creds returns $HTTP_CODE"
    elif [[ "$HTTP_CODE" == "404" ]]; then
        warn_test "admin/login endpoint not found"
    else
        fail_test "admin/login with wrong creds returned $HTTP_CODE (expected 401)" "yes"
    fi

    # Admin rate limiting test
    echo -e "  ${BLUE}Testing admin rate limiting (10 rapid requests)...${NC}"
    ADMIN_RATE_LIMITED=0
    for i in $(seq 1 10); do
        do_request POST "$BACKEND_URL/api/v1/admin/login" \
            -H "Content-Type: application/json" \
            -d '{"username":"attacker","password":"attempt'$i'"}'
        if [[ "$HTTP_CODE" == "429" ]]; then
            ADMIN_RATE_LIMITED=1
            break
        fi
    done
    if [[ "$ADMIN_RATE_LIMITED" -eq 1 ]]; then
        pass_test "admin rate limiting active (got 429)"
    else
        warn_test "admin rate limiting not triggered after 10 requests"
    fi

    echo ""
fi

# ===========================================================================
# CATEGORY: edge
# ===========================================================================
if should_run "edge"; then
    echo -e "${BOLD}--- [edge] Edge Worker / Frontend ---${NC}"
    echo ""

    # GET / - expect 200 with HTML
    do_request GET "$FRONTEND_URL/" \
        -H "User-Agent: Mozilla/5.0 SyrabitTest/1.0"
    if [[ "$HTTP_CODE" == "200" ]]; then
        if echo "$RESPONSE_BODY" | grep -qi '<meta\|<!DOCTYPE\|<html'; then
            pass_test "frontend / returns 200 with HTML content"
        else
            warn_test "frontend / returns 200 but may not contain expected HTML"
        fi
    else
        fail_test "frontend / returned $HTTP_CODE (expected 200)"
    fi

    # GET /chat - SPA routing
    do_request GET "$FRONTEND_URL/chat" \
        -H "User-Agent: Mozilla/5.0 SyrabitTest/1.0"
    if [[ "$HTTP_CODE" == "200" ]]; then
        pass_test "frontend /chat returns 200 (SPA routing works)"
    else
        fail_test "frontend /chat returned $HTTP_CODE (expected 200)"
    fi

    # GET /render/seba/10/science/chemical-reactions - ISR
    do_request GET "$FRONTEND_URL/render/seba/10/science/chemical-reactions" \
        -H "User-Agent: Mozilla/5.0 SyrabitTest/1.0"
    if [[ "$HTTP_CODE" == "200" || "$HTTP_CODE" == "404" ]]; then
        pass_test "frontend render page returns $HTTP_CODE (ISR)"
    else
        fail_test "frontend render page returned $HTTP_CODE (expected 200 or 404)"
    fi

    # GET /robots.txt
    do_request GET "$FRONTEND_URL/robots.txt"
    if [[ "$HTTP_CODE" == "200" ]]; then
        if echo "$RESPONSE_BODY" | grep -qi "Sitemap:"; then
            pass_test "robots.txt returns 200 and contains Sitemap directive"
        else
            warn_test "robots.txt returns 200 but missing Sitemap directive"
        fi
    else
        fail_test "robots.txt returned $HTTP_CODE (expected 200)"
    fi

    # GET /manifest.json
    do_request GET "$FRONTEND_URL/manifest.json"
    if [[ "$HTTP_CODE" == "200" ]]; then
        if echo "$RESPONSE_BODY" | python3 -m json.tool >/dev/null 2>&1; then
            pass_test "manifest.json returns 200 with valid JSON"
        elif echo "$RESPONSE_BODY" | grep -q '{'; then
            pass_test "manifest.json returns 200 with JSON-like content"
        else
            warn_test "manifest.json returns 200 but may not be valid JSON"
        fi
    else
        fail_test "manifest.json returned $HTTP_CODE (expected 200)"
    fi

    # Security headers on frontend
    do_request GET "$FRONTEND_URL/" \
        -H "User-Agent: Mozilla/5.0 SyrabitTest/1.0"
    if echo "$RESPONSE_HEADERS" | grep -qi "x-content-type-options"; then
        pass_test "X-Content-Type-Options header present on frontend"
    else
        warn_test "X-Content-Type-Options header missing on frontend"
    fi

    if echo "$RESPONSE_HEADERS" | grep -qi "x-frame-options"; then
        pass_test "X-Frame-Options header present on frontend"
    else
        warn_test "X-Frame-Options header missing on frontend"
    fi

    # CORS headers on API proxy
    do_request GET "$BACKEND_URL/health" \
        -H "Origin: $FRONTEND_URL"
    if echo "$RESPONSE_HEADERS" | grep -qi "access-control-allow-origin"; then
        pass_test "CORS headers present on API responses"
    else
        warn_test "CORS Access-Control-Allow-Origin header missing"
    fi

    echo ""
fi

# ===========================================================================
# CATEGORY: security
# ===========================================================================
if should_run "security"; then
    echo -e "${BOLD}--- [security] Security Checks ---${NC}"
    echo ""

    # Check no server version headers leaked
    do_request GET "$BACKEND_URL/health"
    SERVER_HEADER=$(echo "$RESPONSE_HEADERS" | grep -i "^server:" | head -1)
    if [[ -z "$SERVER_HEADER" ]]; then
        pass_test "no Server header exposed"
    elif echo "$SERVER_HEADER" | grep -qiE '[0-9]+\.[0-9]+'; then
        fail_test "Server header leaks version info: $SERVER_HEADER" "yes"
    else
        pass_test "Server header present but no version leaked"
    fi

    # Verify HSTS header
    do_request GET "$BACKEND_URL/health"
    if echo "$RESPONSE_HEADERS" | grep -qi "strict-transport-security"; then
        pass_test "HSTS header present on HTTPS responses"
    else
        warn_test "HSTS header missing on backend responses"
    fi

    # NoSQL injection test
    do_request GET "$BACKEND_URL/api/v1/content/render/%7B%24gt%3A%22%22%7D/10/science/test"
    if [[ "$HTTP_CODE" == "400" || "$HTTP_CODE" == "422" || "$HTTP_CODE" == "404" ]]; then
        pass_test "NoSQL injection attempt blocked (returned $HTTP_CODE)"
    else
        fail_test "NoSQL injection may not be blocked (returned $HTTP_CODE)" "yes"
    fi

    # Path traversal test
    do_request GET "$BACKEND_URL/api/v1/content/render/../../../etc/passwd"
    if [[ "$HTTP_CODE" == "400" || "$HTTP_CODE" == "404" || "$HTTP_CODE" == "422" ]]; then
        pass_test "path traversal blocked (returned $HTTP_CODE)"
    else
        fail_test "path traversal NOT blocked (returned $HTTP_CODE, expected 400)" "yes"
    fi

    # Webhook endpoint rejects unsigned requests
    do_request POST "$BACKEND_URL/api/webhooks/razorpay" \
        -H "Content-Type: application/json" \
        -d '{"event":"payment.captured","payload":{}}'
    if [[ "$HTTP_CODE" == "401" || "$HTTP_CODE" == "403" || "$HTTP_CODE" == "400" ]]; then
        pass_test "webhook rejects unsigned request (returned $HTTP_CODE)"
    elif [[ "$HTTP_CODE" == "404" ]]; then
        warn_test "webhook endpoint not found (may not be deployed)"
    else
        fail_test "webhook accepted unsigned request (returned $HTTP_CODE)" "yes"
    fi

    # Check error responses don't leak stack traces
    do_request GET "$BACKEND_URL/api/v1/nonexistent-endpoint-xyz"
    if echo "$RESPONSE_BODY" | grep -qi "traceback\|stack trace\|at line\|File \""; then
        fail_test "error response leaks stack trace" "yes"
    elif echo "$RESPONSE_BODY" | grep -q '{'; then
        pass_test "error responses are JSON (no stack trace leaked)"
    else
        pass_test "error response does not leak internal details"
    fi

    echo ""
fi

# ===========================================================================
# CATEGORY: performance
# ===========================================================================
if should_run "performance"; then
    echo -e "${BOLD}--- [performance] Response Times ---${NC}"
    echo ""

    # /health response time
    do_request GET "$BACKEND_URL/health"
    if [[ "$RESPONSE_TIME_MS" -lt 500 ]]; then
        pass_test "/health response time: ${RESPONSE_TIME_MS}ms (< 500ms)"
    else
        warn_test "/health response time: ${RESPONSE_TIME_MS}ms (> 500ms threshold)"
    fi

    # sitemap.xml response time
    do_request GET "$BACKEND_URL/api/v1/seo/sitemap.xml"
    if [[ "$RESPONSE_TIME_MS" -lt 2000 ]]; then
        pass_test "sitemap.xml response time: ${RESPONSE_TIME_MS}ms (< 2000ms)"
    else
        warn_test "sitemap.xml response time: ${RESPONSE_TIME_MS}ms (> 2000ms threshold)"
    fi

    # Frontend response time
    do_request GET "$FRONTEND_URL/" \
        -H "User-Agent: Mozilla/5.0 SyrabitTest/1.0"
    if [[ "$RESPONSE_TIME_MS" -lt 1500 ]]; then
        pass_test "frontend / response time: ${RESPONSE_TIME_MS}ms (< 1500ms)"
    else
        warn_test "frontend / response time: ${RESPONSE_TIME_MS}ms (> 1500ms threshold)"
    fi

    # Content render response time
    do_request GET "$BACKEND_URL/api/v1/content/render/seba/10/science/chemical-reactions"
    echo -e "  ${BLUE}INFO${NC} content render response time: ${RESPONSE_TIME_MS}ms"
    if [[ "$RESPONSE_TIME_MS" -lt 3000 ]]; then
        pass_test "content render response time: ${RESPONSE_TIME_MS}ms (< 3000ms)"
    else
        warn_test "content render response time: ${RESPONSE_TIME_MS}ms (> 3000ms)"
    fi

    echo ""
fi

# ===========================================================================
# CATEGORY: webhook
# ===========================================================================
if should_run "webhook"; then
    echo -e "${BOLD}--- [webhook] Webhook Security ---${NC}"
    echo ""

    # POST /api/webhooks/razorpay with empty body
    do_request POST "$BACKEND_URL/api/webhooks/razorpay" \
        -H "Content-Type: application/json" \
        -d '{}'
    if [[ "$HTTP_CODE" == "400" || "$HTTP_CODE" == "401" || "$HTTP_CODE" == "403" ]]; then
        pass_test "webhook with empty body returns $HTTP_CODE"
    elif [[ "$HTTP_CODE" == "404" ]]; then
        warn_test "webhook endpoint not found (may not be deployed)"
    else
        fail_test "webhook with empty body returned $HTTP_CODE (expected 400 or 401)" "yes"
    fi

    # POST /api/webhooks/razorpay with invalid signature header
    do_request POST "$BACKEND_URL/api/webhooks/razorpay" \
        -H "Content-Type: application/json" \
        -H "X-Razorpay-Signature: invalid-signature-value" \
        -d '{"event":"payment.captured","payload":{"payment":{"entity":{"id":"pay_fake"}}}}'
    if [[ "$HTTP_CODE" == "401" || "$HTTP_CODE" == "403" || "$HTTP_CODE" == "400" ]]; then
        pass_test "webhook with invalid signature returns $HTTP_CODE"
    elif [[ "$HTTP_CODE" == "404" ]]; then
        warn_test "webhook endpoint not found"
    else
        fail_test "webhook accepted invalid signature (returned $HTTP_CODE)" "yes"
    fi

    echo ""
fi

# ===========================================================================
# SUMMARY
# ===========================================================================
echo -e "${BOLD}=================================================================${NC}"
echo -e "${BOLD}  TEST SUMMARY${NC}"
echo -e "${BOLD}=================================================================${NC}"
echo ""
echo -e "  PASSED: ${GREEN}${PASSED}${NC}/${TOTAL} | FAILED: ${RED}${FAILED}${NC} | WARNINGS: ${YELLOW}${WARNINGS}${NC}"
echo ""
if [[ "$CRITICAL_FAILURES" -gt 0 ]]; then
    echo -e "  ${RED}${BOLD}CRITICAL FAILURES: ${CRITICAL_FAILURES}${NC}"
    echo -e "  ${RED}Security and auth tests have critical failures.${NC}"
else
    echo -e "  ${GREEN}${BOLD}All critical tests passed.${NC}"
fi
echo ""
echo -e "${BOLD}=================================================================${NC}"
echo ""

# Exit code
if [[ "$CRITICAL_FAILURES" -gt 0 ]]; then
    exit 1
fi
exit 0
