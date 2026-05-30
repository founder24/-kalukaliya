#!/usr/bin/env bash
# ============================================================================
# SYRABIT.AI - COMPREHENSIVE END-TO-END LIVE DEPLOYMENT TEST
# ============================================================================
#
# Tests the full stack live deployment:
#   - Frontend: Cloudflare Pages (https://syrabit.ai)
#   - Edge Worker: Cloudflare Workers (https://api.syrabit.ai)
#   - Backend: GCP Cloud Run (authenticated via identity token from edge)
#   - WWW redirect: www.syrabit.ai -> syrabit.ai (301)
#   - Services: MongoDB, Redis (Upstash), Vertex AI Search, Vertex AI (Gemini)
#
# Usage:
#   ./scripts/e2e-live-test.sh
#   ./scripts/e2e-live-test.sh --verbose
#
# Environment Variables (all optional):
#   FRONTEND_URL  - Override frontend URL (default: https://syrabit.ai)
#   EDGE_URL      - Override edge/API URL (default: https://api.syrabit.ai)
#   WWW_URL       - Override www URL (default: https://www.syrabit.ai)
#
# Requirements: bash 4+, curl, jq, openssl, dig (optional)
# Exit code: 0 if all critical tests pass, 1 if any critical test fails
# ============================================================================

set -euo pipefail

# --- Configuration -----------------------------------------------------------

FRONTEND_URL="${FRONTEND_URL:-https://syrabit.ai}"
EDGE_URL="${EDGE_URL:-https://api.syrabit.ai}"
WWW_URL="${WWW_URL:-https://www.syrabit.ai}"
VERBOSE=0

# Parse arguments
for arg in "$@"; do
    case "$arg" in
        --verbose|-v) VERBOSE=1 ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

# Performance thresholds (milliseconds)
THRESHOLD_FRONTEND_TTFB=500
THRESHOLD_FRONTEND_TOTAL=1000
THRESHOLD_EDGE_HEALTH=1000
THRESHOLD_EDGE_FULL_HEALTH=2000
THRESHOLD_CORS_PREFLIGHT=500


# --- State Tracking ----------------------------------------------------------

TOTAL_PASS=0
TOTAL_FAIL=0
TOTAL_WARN=0
TOTAL_TESTS=0
CRITICAL_FAIL=0

declare -a TEST_RESULTS=()

# --- Colors ------------------------------------------------------------------

if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    DIM='\033[2m'
    NC='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' CYAN='' BOLD='' DIM='' NC=''
fi

# --- Utility Functions -------------------------------------------------------

log_verbose() {
    if [[ "$VERBOSE" -eq 1 ]]; then
        echo -e "  ${DIM}[verbose] $1${NC}"
    fi
}

record_result() {
    local severity="$1"  # CRITICAL, HIGH, MEDIUM
    local category="$2"
    local name="$3"
    local status="$4"    # PASS, FAIL, WARN
    local timing="$5"
    local detail="${6:-}"

    TOTAL_TESTS=$((TOTAL_TESTS + 1))

    case "$status" in
        PASS)
            TOTAL_PASS=$((TOTAL_PASS + 1))
            local color="$GREEN"
            ;;
        FAIL)
            TOTAL_FAIL=$((TOTAL_FAIL + 1))
            local color="$RED"
            if [[ "$severity" == "CRITICAL" ]]; then
                CRITICAL_FAIL=$((CRITICAL_FAIL + 1))
            fi
            ;;
        WARN)
            TOTAL_WARN=$((TOTAL_WARN + 1))
            local color="$YELLOW"
            ;;
    esac

    local display_timing=""
    if [[ -n "$timing" && "$timing" != "0" ]]; then
        display_timing=" (${timing}ms)"
    fi

    echo -e "  ${color}${status}${NC} [${severity}] ${name}${display_timing}"
    if [[ -n "$detail" ]]; then
        echo -e "       ${DIM}${detail}${NC}"
    fi

    TEST_RESULTS+=("${severity}|${category}|${name}|${status}|${timing}|${detail}")
}


# Perform a timed curl request
# Sets: CURL_STATUS, CURL_TTFB, CURL_TOTAL, CURL_BODY, CURL_HEADERS
perform_request() {
    local url="$1"
    shift
    local extra_args=("$@")

    local timing_format='{"dns":%{time_namelookup},"tls":%{time_appconnect},"ttfb":%{time_starttransfer},"total":%{time_total},"status":%{http_code}}'

    local tmpbody tmpheaders
    tmpbody=$(mktemp)
    tmpheaders=$(mktemp)

    local curl_cmd=(curl -sS -w "$timing_format" -o "$tmpbody" -D "$tmpheaders" --max-time 10)
    if [[ ${#extra_args[@]} -gt 0 ]]; then
        curl_cmd+=("${extra_args[@]}")
    fi
    curl_cmd+=("$url")

    log_verbose "$ ${curl_cmd[*]}"

    local timing_json
    timing_json=$("${curl_cmd[@]}" 2>/dev/null) || timing_json='{"dns":0,"tls":0,"ttfb":0,"total":0,"status":0}'

    CURL_STATUS=$(echo "$timing_json" | jq -r '.status // 0')
    CURL_TTFB=$(echo "$timing_json" | jq -r '(.ttfb * 1000) | floor')
    CURL_TOTAL=$(echo "$timing_json" | jq -r '(.total * 1000) | floor')
    CURL_BODY=$(cat "$tmpbody" 2>/dev/null || echo "")
    CURL_HEADERS=$(cat "$tmpheaders" 2>/dev/null || echo "")

    log_verbose "Status: $CURL_STATUS | TTFB: ${CURL_TTFB}ms | Total: ${CURL_TOTAL}ms"
    if [[ "$VERBOSE" -eq 1 && -n "$CURL_BODY" ]]; then
        log_verbose "Body: $(echo "$CURL_BODY" | head -c 500)"
    fi
    if [[ "$VERBOSE" -eq 1 && -n "$CURL_HEADERS" ]]; then
        log_verbose "Headers: $(echo "$CURL_HEADERS" | head -20)"
    fi

    rm -f "$tmpbody" "$tmpheaders"
}

# Extract a header value (case-insensitive)
get_header() {
    local name="$1"
    echo "$CURL_HEADERS" | grep -i "^${name}:" | head -1 | sed 's/^[^:]*: *//' | tr -d '\r\n'
}


# --- Print Header ------------------------------------------------------------

echo ""
echo -e "${BOLD}============================================================================${NC}"
echo -e "${BOLD}  SYRABIT.AI - END-TO-END LIVE DEPLOYMENT TEST${NC}"
echo -e "${BOLD}============================================================================${NC}"
echo -e "  Date:      $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo -e "  Frontend:  ${FRONTEND_URL}"
echo -e "  Edge:      ${EDGE_URL}"
echo -e "  WWW:       ${WWW_URL}"
echo -e "  Verbose:   ${VERBOSE}"
echo -e "${BOLD}============================================================================${NC}"
echo ""


# ============================================================================
# 1. DNS & CONNECTIVITY
# ============================================================================

echo -e "${BOLD}[1/10] DNS & Connectivity${NC}"
echo -e "${DIM}------------------------------------------------------------------------${NC}"

# DNS resolution for syrabit.ai
if command -v dig &>/dev/null; then
    DNS_RESULT=$(dig +short syrabit.ai A 2>/dev/null | head -1)
    if [[ -n "$DNS_RESULT" ]]; then
        record_result "CRITICAL" "DNS" "Resolve syrabit.ai" "PASS" "0" "A record: ${DNS_RESULT}"
    else
        record_result "CRITICAL" "DNS" "Resolve syrabit.ai" "FAIL" "0" "No A record found"
    fi

    DNS_WWW=$(dig +short www.syrabit.ai 2>/dev/null | head -1)
    if [[ -n "$DNS_WWW" ]]; then
        record_result "CRITICAL" "DNS" "Resolve www.syrabit.ai" "PASS" "0" "Record: ${DNS_WWW}"
    else
        record_result "CRITICAL" "DNS" "Resolve www.syrabit.ai" "FAIL" "0" "No record found"
    fi

    DNS_API=$(dig +short api.syrabit.ai A 2>/dev/null | head -1)
    if [[ -n "$DNS_API" ]]; then
        record_result "CRITICAL" "DNS" "Resolve api.syrabit.ai" "PASS" "0" "A record: ${DNS_API}"
    else
        record_result "CRITICAL" "DNS" "Resolve api.syrabit.ai" "FAIL" "0" "No A record found"
    fi
else
    # Fallback: use curl to test connectivity
    record_result "MEDIUM" "DNS" "dig not available, skipping DNS resolution" "WARN" "0" "Install dnsutils for DNS tests"
fi

# HTTPS connectivity
perform_request "$FRONTEND_URL" -I
if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 400 ]]; then
    record_result "CRITICAL" "DNS" "HTTPS connectivity syrabit.ai" "PASS" "$CURL_TOTAL" "HTTP ${CURL_STATUS}"
else
    record_result "CRITICAL" "DNS" "HTTPS connectivity syrabit.ai" "FAIL" "$CURL_TOTAL" "HTTP ${CURL_STATUS}"
fi

perform_request "$EDGE_URL/health" -I
if [[ "$CURL_STATUS" -ge 200 && "$CURL_STATUS" -lt 400 ]]; then
    record_result "CRITICAL" "DNS" "HTTPS connectivity api.syrabit.ai" "PASS" "$CURL_TOTAL" "HTTP ${CURL_STATUS}"
else
    record_result "CRITICAL" "DNS" "HTTPS connectivity api.syrabit.ai" "FAIL" "$CURL_TOTAL" "HTTP ${CURL_STATUS}"
fi

# WWW redirect (301)
perform_request "$WWW_URL" -I -L --max-redirs 0
if [[ "$CURL_STATUS" -eq 301 ]]; then
    LOCATION=$(get_header "location")
    if echo "$LOCATION" | grep -q "syrabit.ai"; then
        record_result "CRITICAL" "DNS" "www.syrabit.ai redirects (301)" "PASS" "$CURL_TOTAL" "Location: ${LOCATION}"
    else
        record_result "CRITICAL" "DNS" "www.syrabit.ai redirects (301)" "FAIL" "$CURL_TOTAL" "Location: ${LOCATION} (unexpected)"
    fi
elif [[ "$CURL_STATUS" -eq 308 || "$CURL_STATUS" -eq 302 ]]; then
    LOCATION=$(get_header "location")
    record_result "HIGH" "DNS" "www.syrabit.ai redirects (got ${CURL_STATUS})" "WARN" "$CURL_TOTAL" "Expected 301, got ${CURL_STATUS}. Location: ${LOCATION}"
else
    record_result "CRITICAL" "DNS" "www.syrabit.ai redirects (301)" "FAIL" "$CURL_TOTAL" "HTTP ${CURL_STATUS} - no redirect"
fi

echo ""


# ============================================================================
# 2. SSL/TLS
# ============================================================================

echo -e "${BOLD}[2/10] SSL/TLS${NC}"
echo -e "${DIM}------------------------------------------------------------------------${NC}"

# Check TLS version
TLS_INFO=$(curl -sS -I --tlsv1.3 --max-time 10 "$FRONTEND_URL" -w '%{ssl_version}' -o /dev/null 2>/dev/null) || TLS_INFO=""
if [[ "$TLS_INFO" == *"TLSv1.3"* ]]; then
    record_result "CRITICAL" "TLS" "TLS 1.3 supported (syrabit.ai)" "PASS" "0" "Version: ${TLS_INFO}"
elif [[ -n "$TLS_INFO" ]]; then
    record_result "HIGH" "TLS" "TLS 1.3 supported (syrabit.ai)" "WARN" "0" "Version: ${TLS_INFO} (expected TLSv1.3)"
else
    record_result "CRITICAL" "TLS" "TLS 1.3 supported (syrabit.ai)" "FAIL" "0" "Could not determine TLS version"
fi

# Check certificate validity using openssl
if command -v openssl &>/dev/null; then
    CERT_INFO=$(echo | openssl s_client -servername syrabit.ai -connect syrabit.ai:443 2>/dev/null | openssl x509 -noout -dates -subject 2>/dev/null) || CERT_INFO=""

    if [[ -n "$CERT_INFO" ]]; then
        NOT_AFTER=$(echo "$CERT_INFO" | grep "notAfter" | cut -d= -f2)
        SUBJECT=$(echo "$CERT_INFO" | grep "subject" | sed 's/subject=//')

        if [[ -n "$NOT_AFTER" ]]; then
            # Check if cert is expired
            EXPIRY_EPOCH=$(date -d "$NOT_AFTER" +%s 2>/dev/null || date -j -f "%b %d %T %Y %Z" "$NOT_AFTER" +%s 2>/dev/null || echo "0")
            NOW_EPOCH=$(date +%s)
            if [[ "$EXPIRY_EPOCH" -gt "$NOW_EPOCH" ]]; then
                DAYS_LEFT=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))
                record_result "CRITICAL" "TLS" "Certificate not expired" "PASS" "0" "Expires: ${NOT_AFTER} (${DAYS_LEFT} days left)"
            else
                record_result "CRITICAL" "TLS" "Certificate not expired" "FAIL" "0" "Certificate expired on ${NOT_AFTER}"
            fi
        else
            record_result "HIGH" "TLS" "Certificate not expired" "WARN" "0" "Could not parse expiry date"
        fi

        # Check wildcard/SAN coverage
        SAN_INFO=$(echo | openssl s_client -servername syrabit.ai -connect syrabit.ai:443 2>/dev/null | openssl x509 -noout -ext subjectAltName 2>/dev/null) || SAN_INFO=""
        if echo "$SAN_INFO" | grep -q '\*.syrabit.ai'; then
            record_result "HIGH" "TLS" "Wildcard coverage (*.syrabit.ai)" "PASS" "0" "SAN includes *.syrabit.ai"
        elif echo "$SAN_INFO" | grep -q "syrabit.ai"; then
            record_result "HIGH" "TLS" "Wildcard coverage (*.syrabit.ai)" "WARN" "0" "SAN has syrabit.ai but no wildcard. Individual certs may be used."
        else
            record_result "HIGH" "TLS" "Wildcard coverage (*.syrabit.ai)" "WARN" "0" "Could not verify SAN entries"
        fi
    else
        record_result "HIGH" "TLS" "Certificate validity check" "WARN" "0" "openssl could not retrieve certificate"
    fi
else
    record_result "MEDIUM" "TLS" "Certificate checks skipped" "WARN" "0" "openssl not available"
fi

echo ""


# ============================================================================
# 3. FRONTEND (Cloudflare Pages)
# ============================================================================

echo -e "${BOLD}[3/10] Frontend (Cloudflare Pages)${NC}"
echo -e "${DIM}------------------------------------------------------------------------${NC}"

# GET homepage returns 200 with HTML
perform_request "$FRONTEND_URL" \
    -H "Accept: text/html" \
    -H "User-Agent: SyrabitE2ETest/1.0"

if [[ "$CURL_STATUS" -eq 200 ]]; then
    if echo "$CURL_BODY" | grep -qi "<html"; then
        record_result "CRITICAL" "Frontend" "Homepage returns 200 with HTML" "PASS" "$CURL_TOTAL" "HTTP 200, HTML content detected"
    else
        record_result "CRITICAL" "Frontend" "Homepage returns 200 with HTML" "FAIL" "$CURL_TOTAL" "HTTP 200 but no HTML content"
    fi
else
    record_result "CRITICAL" "Frontend" "Homepage returns 200 with HTML" "FAIL" "$CURL_TOTAL" "HTTP ${CURL_STATUS}"
fi

# Security headers
HSTS=$(get_header "strict-transport-security")
if [[ -n "$HSTS" ]]; then
    record_result "HIGH" "Frontend" "HSTS header present" "PASS" "0" "Value: ${HSTS}"
else
    record_result "HIGH" "Frontend" "HSTS header present" "FAIL" "0" "Missing Strict-Transport-Security header"
fi

CSP=$(get_header "content-security-policy")
if [[ -n "$CSP" ]]; then
    record_result "HIGH" "Frontend" "Content-Security-Policy header" "PASS" "0" "CSP is set"
else
    record_result "HIGH" "Frontend" "Content-Security-Policy header" "WARN" "0" "Missing CSP header"
fi

XFRAME=$(get_header "x-frame-options")
if [[ -n "$XFRAME" ]]; then
    record_result "HIGH" "Frontend" "X-Frame-Options header" "PASS" "0" "Value: ${XFRAME}"
else
    record_result "HIGH" "Frontend" "X-Frame-Options header" "WARN" "0" "Missing X-Frame-Options header"
fi

XCTYPE=$(get_header "x-content-type-options")
if [[ "$XCTYPE" == *"nosniff"* ]]; then
    record_result "HIGH" "Frontend" "X-Content-Type-Options: nosniff" "PASS" "0" "Value: ${XCTYPE}"
else
    record_result "HIGH" "Frontend" "X-Content-Type-Options: nosniff" "WARN" "0" "Missing or incorrect value: ${XCTYPE:-none}"
fi

# Meta tags
if echo "$CURL_BODY" | grep -qi 'name="viewport"'; then
    record_result "MEDIUM" "Frontend" "Meta viewport tag exists" "PASS" "0" ""
else
    record_result "MEDIUM" "Frontend" "Meta viewport tag exists" "WARN" "0" "No viewport meta tag found"
fi

if echo "$CURL_BODY" | grep -qi 'charset'; then
    record_result "MEDIUM" "Frontend" "Charset declaration exists" "PASS" "0" ""
else
    record_result "MEDIUM" "Frontend" "Charset declaration exists" "WARN" "0" "No charset meta found"
fi

# Response time check
if [[ "$CURL_TOTAL" -lt "$THRESHOLD_FRONTEND_TOTAL" ]]; then
    record_result "HIGH" "Frontend" "Response time < ${THRESHOLD_FRONTEND_TOTAL}ms" "PASS" "$CURL_TOTAL" ""
else
    record_result "HIGH" "Frontend" "Response time < ${THRESHOLD_FRONTEND_TOTAL}ms" "FAIL" "$CURL_TOTAL" "Took ${CURL_TOTAL}ms"
fi

echo ""


# ============================================================================
# 4. EDGE WORKER HEALTH
# ============================================================================

echo -e "${BOLD}[4/10] Edge Worker Health${NC}"
echo -e "${DIM}------------------------------------------------------------------------${NC}"

# GET /health returns 200 with backend_reachable:true
perform_request "${EDGE_URL}/health"

if [[ "$CURL_STATUS" -eq 200 ]]; then
    BACKEND_REACHABLE=""
    if echo "$CURL_BODY" | jq . &>/dev/null; then
        BACKEND_REACHABLE=$(echo "$CURL_BODY" | jq -r '.backend_reachable // .backendReachable // "unknown"' 2>/dev/null)
    fi
    if [[ "$BACKEND_REACHABLE" == "true" ]]; then
        record_result "CRITICAL" "Edge" "/health returns 200, backend_reachable:true" "PASS" "$CURL_TTFB" ""
    elif [[ "$BACKEND_REACHABLE" == "false" ]]; then
        record_result "CRITICAL" "Edge" "/health backend_reachable" "FAIL" "$CURL_TTFB" "backend_reachable is false"
    else
        record_result "CRITICAL" "Edge" "/health returns 200" "PASS" "$CURL_TTFB" "backend_reachable field not found in response"
    fi
else
    record_result "CRITICAL" "Edge" "/health returns 200" "FAIL" "$CURL_TTFB" "HTTP ${CURL_STATUS}"
fi

# GET /health/full returns all services healthy
perform_request "${EDGE_URL}/health/full"

if [[ "$CURL_STATUS" -eq 200 ]]; then
    if echo "$CURL_BODY" | jq . &>/dev/null; then
        record_result "HIGH" "Edge" "/health/full returns 200 JSON" "PASS" "$CURL_TTFB" ""
    else
        record_result "HIGH" "Edge" "/health/full returns 200 JSON" "WARN" "$CURL_TTFB" "Response is not valid JSON"
    fi
elif [[ "$CURL_STATUS" -eq 404 ]]; then
    # Try alternative path
    perform_request "${EDGE_URL}/health/deep"
    if [[ "$CURL_STATUS" -eq 200 ]]; then
        record_result "HIGH" "Edge" "/health/deep returns 200 (alt path)" "PASS" "$CURL_TTFB" ""
    else
        record_result "HIGH" "Edge" "/health/full or /health/deep" "WARN" "$CURL_TTFB" "Neither path returned 200"
    fi
else
    record_result "HIGH" "Edge" "/health/full returns 200" "FAIL" "$CURL_TTFB" "HTTP ${CURL_STATUS}"
fi

# Response time check
if [[ "$CURL_TTFB" -lt "$THRESHOLD_EDGE_FULL_HEALTH" ]]; then
    record_result "HIGH" "Edge" "Health response < ${THRESHOLD_EDGE_FULL_HEALTH}ms" "PASS" "$CURL_TTFB" ""
else
    record_result "HIGH" "Edge" "Health response < ${THRESHOLD_EDGE_FULL_HEALTH}ms" "FAIL" "$CURL_TTFB" "Took ${CURL_TTFB}ms"
fi

echo ""


# ============================================================================
# 5. CORS
# ============================================================================

echo -e "${BOLD}[5/10] CORS${NC}"
echo -e "${DIM}------------------------------------------------------------------------${NC}"

# OPTIONS preflight with valid origin
perform_request "${EDGE_URL}/health" \
    -X OPTIONS \
    -H "Origin: ${FRONTEND_URL}" \
    -H "Access-Control-Request-Method: GET" \
    -H "Access-Control-Request-Headers: Content-Type,Authorization"

ALLOW_ORIGIN=$(get_header "access-control-allow-origin")
ALLOW_METHODS=$(get_header "access-control-allow-methods")
ALLOW_CREDS=$(get_header "access-control-allow-credentials")

if [[ "$ALLOW_ORIGIN" == *"syrabit.ai"* || "$ALLOW_ORIGIN" == "$FRONTEND_URL" ]]; then
    record_result "CRITICAL" "CORS" "Valid origin returns correct CORS headers" "PASS" "$CURL_TTFB" "Allow-Origin: ${ALLOW_ORIGIN}"
elif [[ "$ALLOW_ORIGIN" == "*" ]]; then
    record_result "HIGH" "CORS" "Valid origin returns correct CORS headers" "WARN" "$CURL_TTFB" "Allow-Origin is wildcard (*) - consider restricting"
elif [[ -n "$ALLOW_ORIGIN" ]]; then
    record_result "HIGH" "CORS" "Valid origin returns correct CORS headers" "WARN" "$CURL_TTFB" "Allow-Origin: ${ALLOW_ORIGIN} (unexpected)"
else
    record_result "CRITICAL" "CORS" "Valid origin returns correct CORS headers" "FAIL" "$CURL_TTFB" "No Access-Control-Allow-Origin header"
fi

# OPTIONS with malicious origin should NOT reflect it
perform_request "${EDGE_URL}/health" \
    -X OPTIONS \
    -H "Origin: https://evil-site.com" \
    -H "Access-Control-Request-Method: GET"

EVIL_ORIGIN=$(get_header "access-control-allow-origin")
if [[ "$EVIL_ORIGIN" == "https://evil-site.com" ]]; then
    record_result "CRITICAL" "CORS" "Malicious origin NOT reflected" "FAIL" "$CURL_TTFB" "Origin https://evil-site.com was reflected back!"
elif [[ "$EVIL_ORIGIN" == "*" ]]; then
    record_result "HIGH" "CORS" "Malicious origin NOT reflected" "WARN" "$CURL_TTFB" "Wildcard (*) allows any origin"
else
    record_result "CRITICAL" "CORS" "Malicious origin NOT reflected" "PASS" "$CURL_TTFB" "Returned: ${EVIL_ORIGIN:-none}"
fi

# Access-Control-Allow-Credentials: true
if [[ "$ALLOW_CREDS" == "true" ]]; then
    record_result "HIGH" "CORS" "Access-Control-Allow-Credentials: true" "PASS" "0" ""
else
    record_result "HIGH" "CORS" "Access-Control-Allow-Credentials: true" "WARN" "0" "Value: ${ALLOW_CREDS:-not set}"
fi

# Preflight performance
if [[ "$CURL_TTFB" -lt "$THRESHOLD_CORS_PREFLIGHT" ]]; then
    record_result "HIGH" "CORS" "Preflight response < ${THRESHOLD_CORS_PREFLIGHT}ms" "PASS" "$CURL_TTFB" ""
else
    record_result "HIGH" "CORS" "Preflight response < ${THRESHOLD_CORS_PREFLIGHT}ms" "FAIL" "$CURL_TTFB" "Took ${CURL_TTFB}ms"
fi

echo ""


# ============================================================================
# 6. AUTHENTICATION & SECURITY
# ============================================================================

echo -e "${BOLD}[6/10] Authentication & Security${NC}"
echo -e "${DIM}------------------------------------------------------------------------${NC}"

# Invalid JWT returns 401 with JSON error
perform_request "${EDGE_URL}/api/v1/users/me" \
    -H "Authorization: Bearer invalid.jwt.token.here" \
    -H "Accept: application/json"

if [[ "$CURL_STATUS" -eq 401 ]]; then
    if echo "$CURL_BODY" | jq . &>/dev/null; then
        record_result "CRITICAL" "Auth" "Invalid JWT returns 401 JSON error" "PASS" "$CURL_TTFB" "Got 401 with JSON body"
    else
        record_result "HIGH" "Auth" "Invalid JWT returns 401 JSON error" "WARN" "$CURL_TTFB" "Got 401 but body is not JSON"
    fi
else
    record_result "CRITICAL" "Auth" "Invalid JWT returns 401" "FAIL" "$CURL_TTFB" "Expected 401, got HTTP ${CURL_STATUS}"
fi

# Missing Turnstile token on chat POST returns 403
perform_request "${EDGE_URL}/api/v1/chat/stream" \
    -X POST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0In0.fake" \
    -d '{"message":"test","language":"en"}'

if [[ "$CURL_STATUS" -eq 403 ]]; then
    record_result "HIGH" "Auth" "Missing Turnstile token returns 403" "PASS" "$CURL_TTFB" ""
elif [[ "$CURL_STATUS" -eq 401 ]]; then
    record_result "HIGH" "Auth" "Missing Turnstile token returns 403" "WARN" "$CURL_TTFB" "Got 401 (JWT rejected before Turnstile check)"
else
    record_result "HIGH" "Auth" "Missing Turnstile token returns 403" "FAIL" "$CURL_TTFB" "Expected 403, got HTTP ${CURL_STATUS}"
fi

# Malformed JWT returns appropriate error
perform_request "${EDGE_URL}/api/v1/users/me" \
    -H "Authorization: Bearer not-even-a-jwt" \
    -H "Accept: application/json"

if [[ "$CURL_STATUS" -eq 401 || "$CURL_STATUS" -eq 403 ]]; then
    if echo "$CURL_BODY" | jq . &>/dev/null; then
        ERROR_MSG=$(echo "$CURL_BODY" | jq -r '.error // .message // .detail // ""' 2>/dev/null)
        record_result "HIGH" "Auth" "Malformed JWT returns error message" "PASS" "$CURL_TTFB" "Error: ${ERROR_MSG}"
    else
        record_result "HIGH" "Auth" "Malformed JWT returns error message" "WARN" "$CURL_TTFB" "Got ${CURL_STATUS} but response is not JSON"
    fi
else
    record_result "HIGH" "Auth" "Malformed JWT returns error message" "FAIL" "$CURL_TTFB" "Expected 401/403, got HTTP ${CURL_STATUS}"
fi

# robots.txt accessible
perform_request "${FRONTEND_URL}/robots.txt"

if [[ "$CURL_STATUS" -eq 200 ]]; then
    record_result "MEDIUM" "Auth" "robots.txt is accessible" "PASS" "$CURL_TTFB" ""
else
    record_result "MEDIUM" "Auth" "robots.txt is accessible" "WARN" "$CURL_TTFB" "HTTP ${CURL_STATUS}"
fi

echo ""


# ============================================================================
# 7. API ROUTING
# ============================================================================

echo -e "${BOLD}[7/10] API Routing${NC}"
echo -e "${DIM}------------------------------------------------------------------------${NC}"

# /api/v1/users/me without JWT returns proper JSON error (not HTML 403)
perform_request "${EDGE_URL}/api/v1/users/me" \
    -H "Accept: application/json"

if echo "$CURL_BODY" | jq . &>/dev/null; then
    record_result "HIGH" "Routing" "/api/v1/users/me returns JSON error (not HTML)" "PASS" "$CURL_TTFB" "HTTP ${CURL_STATUS}, JSON response"
elif echo "$CURL_BODY" | grep -qi "<html"; then
    record_result "HIGH" "Routing" "/api/v1/users/me returns JSON error (not HTML)" "FAIL" "$CURL_TTFB" "Got HTML instead of JSON"
else
    record_result "HIGH" "Routing" "/api/v1/users/me returns JSON error (not HTML)" "WARN" "$CURL_TTFB" "Response is neither JSON nor HTML"
fi

# Unknown paths redirect to frontend (302)
perform_request "${EDGE_URL}/this-path-does-not-exist-xyz" \
    -H "Accept: text/html" \
    --max-redirs 0

if [[ "$CURL_STATUS" -eq 302 || "$CURL_STATUS" -eq 301 ]]; then
    LOCATION=$(get_header "location")
    record_result "MEDIUM" "Routing" "Unknown paths redirect to frontend" "PASS" "$CURL_TTFB" "Redirects to: ${LOCATION}"
elif [[ "$CURL_STATUS" -eq 404 ]]; then
    record_result "MEDIUM" "Routing" "Unknown paths redirect to frontend" "WARN" "$CURL_TTFB" "Returns 404 instead of redirect"
else
    record_result "MEDIUM" "Routing" "Unknown paths redirect to frontend" "WARN" "$CURL_TTFB" "HTTP ${CURL_STATUS}"
fi

# /assets/ path returns 404 for non-existent asset (not error page)
perform_request "${FRONTEND_URL}/assets/nonexistent-file-12345.js"

if [[ "$CURL_STATUS" -eq 404 ]]; then
    record_result "MEDIUM" "Routing" "/assets/ returns 404 for missing asset" "PASS" "$CURL_TTFB" ""
elif [[ "$CURL_STATUS" -eq 200 ]]; then
    # SPA fallback might serve index.html
    if echo "$CURL_BODY" | grep -qi "<html"; then
        record_result "MEDIUM" "Routing" "/assets/ returns 404 for missing asset" "WARN" "$CURL_TTFB" "Returns SPA fallback HTML instead of 404"
    else
        record_result "MEDIUM" "Routing" "/assets/ returns 404 for missing asset" "WARN" "$CURL_TTFB" "Returns 200"
    fi
else
    record_result "MEDIUM" "Routing" "/assets/ returns 404 for missing asset" "WARN" "$CURL_TTFB" "HTTP ${CURL_STATUS}"
fi

echo ""


# ============================================================================
# 8. BACKEND HEALTH (via Edge)
# ============================================================================

echo -e "${BOLD}[8/10] Backend Health (via Edge)${NC}"
echo -e "${DIM}------------------------------------------------------------------------${NC}"

# All backend dependencies reported healthy
perform_request "${EDGE_URL}/health/full"

# If /health/full not found, try /health/deep
if [[ "$CURL_STATUS" -eq 404 ]]; then
    perform_request "${EDGE_URL}/health/deep"
fi

if [[ "$CURL_STATUS" -eq 200 ]] && echo "$CURL_BODY" | jq . &>/dev/null; then
    record_result "HIGH" "Backend" "Backend response is JSON" "PASS" "$CURL_TTFB" ""

    # Check individual services - try multiple JSON structures
    check_service() {
        local service_name="$1"
        local severity="$2"
        shift 2
        local paths=("$@")

        local status="unknown"
        for path in "${paths[@]}"; do
            local val
            val=$(echo "$CURL_BODY" | jq -r "${path}" 2>/dev/null)
            if [[ "$val" != "null" && -n "$val" ]]; then
                status="$val"
                break
            fi
        done

        if [[ "$status" == "healthy" || "$status" == "ok" || "$status" == "connected" || "$status" == "true" ]]; then
            record_result "$severity" "Backend" "${service_name} healthy" "PASS" "0" "Status: ${status}"
        elif [[ "$status" == "unknown" || "$status" == "null" ]]; then
            record_result "$severity" "Backend" "${service_name} healthy" "WARN" "0" "Service not reported in health response"
        else
            record_result "$severity" "Backend" "${service_name} healthy" "FAIL" "0" "Status: ${status}"
        fi
    }

    check_service "MongoDB" "CRITICAL" \
        '.services.mongodb.status' '.mongodb' '.checks.mongodb' '.components.mongodb.status'

    check_service "Redis" "HIGH" \
        '.services.redis.status' '.redis' '.checks.redis' '.components.redis.status'

    check_service "Vertex AI Search" "HIGH" \
        '.services.vertex_search.status' '.vertex_search' '.checks.vertex_search' '.components.vertex_search.status' '.services.search.status'

    check_service "Vertex AI (Gemini)" "HIGH" \
        '.services.vertex_ai.status' '.vertex_ai' '.checks.vertex_ai' '.components.vertex_ai.status'

elif [[ "$CURL_STATUS" -eq 200 ]]; then
    record_result "HIGH" "Backend" "Backend response is JSON" "FAIL" "$CURL_TTFB" "Got 200 but body is not valid JSON"
elif [[ "$CURL_STATUS" -eq 503 ]]; then
    record_result "HIGH" "Backend" "Backend health endpoint" "WARN" "$CURL_TTFB" "503 - some services may be degraded"
    if echo "$CURL_BODY" | jq . &>/dev/null; then
        record_result "HIGH" "Backend" "Backend response is JSON" "PASS" "0" "JSON response even on 503"
    fi
else
    record_result "CRITICAL" "Backend" "Backend health reachable via edge" "FAIL" "$CURL_TTFB" "HTTP ${CURL_STATUS}"
fi

echo ""


# ============================================================================
# 9. PERFORMANCE
# ============================================================================

echo -e "${BOLD}[9/10] Performance${NC}"
echo -e "${DIM}------------------------------------------------------------------------${NC}"

# Frontend TTFB < 500ms
perform_request "$FRONTEND_URL" -H "User-Agent: SyrabitE2ETest/1.0"

if [[ "$CURL_TTFB" -lt "$THRESHOLD_FRONTEND_TTFB" ]]; then
    record_result "HIGH" "Perf" "Frontend TTFB < ${THRESHOLD_FRONTEND_TTFB}ms" "PASS" "$CURL_TTFB" ""
else
    record_result "HIGH" "Perf" "Frontend TTFB < ${THRESHOLD_FRONTEND_TTFB}ms" "FAIL" "$CURL_TTFB" "TTFB: ${CURL_TTFB}ms"
fi

# Edge health < 1000ms
perform_request "${EDGE_URL}/health"

if [[ "$CURL_TTFB" -lt "$THRESHOLD_EDGE_HEALTH" ]]; then
    record_result "HIGH" "Perf" "Edge health TTFB < ${THRESHOLD_EDGE_HEALTH}ms" "PASS" "$CURL_TTFB" ""
else
    record_result "HIGH" "Perf" "Edge health TTFB < ${THRESHOLD_EDGE_HEALTH}ms" "FAIL" "$CURL_TTFB" "TTFB: ${CURL_TTFB}ms"
fi

# CORS preflight < 500ms
perform_request "${EDGE_URL}/api/v1/chat/stream" \
    -X OPTIONS \
    -H "Origin: ${FRONTEND_URL}" \
    -H "Access-Control-Request-Method: POST" \
    -H "Access-Control-Request-Headers: Content-Type,Authorization"

if [[ "$CURL_TTFB" -lt "$THRESHOLD_CORS_PREFLIGHT" ]]; then
    record_result "HIGH" "Perf" "CORS preflight < ${THRESHOLD_CORS_PREFLIGHT}ms" "PASS" "$CURL_TTFB" ""
else
    record_result "HIGH" "Perf" "CORS preflight < ${THRESHOLD_CORS_PREFLIGHT}ms" "FAIL" "$CURL_TTFB" "TTFB: ${CURL_TTFB}ms"
fi

echo ""


# ============================================================================
# 10. RATE LIMITING HEADERS
# ============================================================================

echo -e "${BOLD}[10/10] Rate Limiting${NC}"
echo -e "${DIM}------------------------------------------------------------------------${NC}"

# Check if rate limit headers are present on API endpoints
perform_request "${EDGE_URL}/api/v1/chat/stream" \
    -X POST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer test-token" \
    -d '{"message":"rate limit test","language":"en"}'

RATE_LIMIT=$(get_header "x-ratelimit-limit")
RATE_REMAINING=$(get_header "x-ratelimit-remaining")
RETRY_AFTER=$(get_header "retry-after")
RATE_LIMIT_ALT=$(get_header "ratelimit-limit")

if [[ -n "$RATE_LIMIT" || -n "$RATE_LIMIT_ALT" ]]; then
    record_result "MEDIUM" "RateLimit" "Rate limit headers present" "PASS" "$CURL_TTFB" "Limit: ${RATE_LIMIT:-$RATE_LIMIT_ALT}, Remaining: ${RATE_REMAINING:-n/a}"
elif [[ -n "$RETRY_AFTER" ]]; then
    record_result "MEDIUM" "RateLimit" "Rate limit headers present" "PASS" "$CURL_TTFB" "Retry-After: ${RETRY_AFTER}"
elif [[ "$CURL_STATUS" -eq 429 ]]; then
    record_result "MEDIUM" "RateLimit" "Rate limiting active (429)" "PASS" "$CURL_TTFB" "Rate limited"
else
    record_result "MEDIUM" "RateLimit" "Rate limit headers present" "WARN" "$CURL_TTFB" "No rate limit headers detected (may need multiple requests)"
fi

echo ""


# ============================================================================
# SUMMARY
# ============================================================================

echo -e "${BOLD}============================================================================${NC}"
echo -e "${BOLD}  SUMMARY${NC}"
echo -e "${BOLD}============================================================================${NC}"
echo ""

# Print results table
printf "  %-10s %-12s %-45s %-6s %s\n" "SEVERITY" "CATEGORY" "TEST" "RESULT" "TIME"
printf "  %-10s %-12s %-45s %-6s %s\n" "--------" "--------" "----" "------" "----"

for result in "${TEST_RESULTS[@]}"; do
    IFS='|' read -r sev cat name status timing detail <<< "$result"

    case "$status" in
        PASS) status_colored="${GREEN}PASS${NC}" ;;
        FAIL) status_colored="${RED}FAIL${NC}" ;;
        WARN) status_colored="${YELLOW}WARN${NC}" ;;
        *)    status_colored="$status" ;;
    esac

    timing_str=""
    if [[ -n "$timing" && "$timing" != "0" ]]; then
        timing_str="${timing}ms"
    fi

    printf "  %-10s %-12s %-45s " "$sev" "$cat" "${name:0:45}"
    echo -en "${status_colored}"
    printf "  %s\n" "$timing_str"
done

echo ""
echo -e "${BOLD}------------------------------------------------------------------------${NC}"
echo ""
echo -e "  Total:     ${TOTAL_TESTS}"
echo -e "  ${GREEN}Passed:${NC}    ${TOTAL_PASS}"
echo -e "  ${YELLOW}Warnings:${NC}  ${TOTAL_WARN}"
echo -e "  ${RED}Failed:${NC}    ${TOTAL_FAIL}"
echo ""

if [[ "$CRITICAL_FAIL" -eq 0 ]]; then
    echo -e "  ${GREEN}${BOLD}ALL CRITICAL TESTS PASSED${NC}"
else
    echo -e "  ${RED}${BOLD}${CRITICAL_FAIL} CRITICAL TEST(S) FAILED${NC}"
fi

echo ""
echo -e "${BOLD}============================================================================${NC}"
echo ""

# Exit code
if [[ "$CRITICAL_FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0
