#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# SYRABIT E2E FULLSTACK TEST
# ═══════════════════════════════════════════════════════════════════════════════
#
# Comprehensive end-to-end test covering every layer and endpoint of the
# Syrabit.ai production system. Self-contained - requires only curl, openssl,
# and python3 (for JSON parsing fallback).
#
# Usage:
#   ./scripts/e2e-fullstack-test.sh
#
# With authenticated tests:
#   TEST_JWT_TOKEN="eyJ..." TEST_TURNSTILE_TOKEN="0.xxx" ./scripts/e2e-fullstack-test.sh
#
# Environment Variables (all optional):
#   FRONTEND       - Override frontend URL (default: https://syrabit.ai)
#   EDGE           - Override edge/API URL (default: https://api.syrabit.ai)
#   TEST_JWT_TOKEN - JWT token for authenticated chat tests
#   TEST_TURNSTILE_TOKEN - Turnstile token for chat tests
#   VERBOSE        - Set to 1 for detailed output
#
# Requirements: bash, curl, openssl, python3 (or awk for JSON)
# Exit code: 0 if all critical checks pass, 1 if any fail
# ═══════════════════════════════════════════════════════════════════════════════

set -uo pipefail

# ─── Configuration ────────────────────────────────────────────────────────────

FRONTEND="${FRONTEND:-https://syrabit.ai}"
EDGE="${EDGE:-https://api.syrabit.ai}"
TEST_JWT_TOKEN="${TEST_JWT_TOKEN:-}"
TEST_TURNSTILE_TOKEN="${TEST_TURNSTILE_TOKEN:-}"
VERBOSE="${VERBOSE:-0}"
ANON_ID="anon_e2etest_000000000000000000000001"

# ─── Colors ───────────────────────────────────────────────────────────────────

if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    CYAN=''
    BOLD=''
    NC=''
fi

# ─── State Tracking ──────────────────────────────────────────────────────────

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
FAILURES=()

# ─── Utility Functions ────────────────────────────────────────────────────────

pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    echo -e "  ${GREEN}[PASS]${NC} $1"
}

fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILURES+=("$1")
    echo -e "  ${RED}[FAIL]${NC} $1"
}

warn() {
    WARN_COUNT=$((WARN_COUNT + 1))
    echo -e "  ${YELLOW}[WARN]${NC} $1"
}

header() {
    echo ""
    echo -e "${BOLD}${CYAN}━━━ $1 ━━━${NC}"
    echo ""
}

verbose() {
    if [[ "$VERBOSE" == "1" ]]; then
        echo -e "  ${CYAN}[DEBUG]${NC} $1"
    fi
}

# Extract JSON value using python3 (no jq dependency)
json_val() {
    local json="$1"
    local key="$2"
    echo "$json" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    keys = '$key'.split('.')
    val = data
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            val = None
            break
    if val is None:
        print('')
    else:
        print(val)
except:
    print('')
" 2>/dev/null
}

# Float comparison using awk
float_lt() {
    echo "$1 $2" | awk '{exit ($1 < $2) ? 0 : 1}'
}

# ─── Header ──────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}$(printf '%.0s=' {1..70})${NC}"
echo -e "${BOLD}  SYRABIT E2E FULLSTACK TEST${NC}"
echo -e "  Date:     $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo -e "  Frontend: ${FRONTEND}"
echo -e "  Edge:     ${EDGE}"
[[ -n "$TEST_JWT_TOKEN" ]] && echo -e "  Auth:     JWT token provided"
echo -e "${BOLD}$(printf '%.0s=' {1..70})${NC}"


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 1: DNS & TLS
# ═══════════════════════════════════════════════════════════════════════════════

header "LAYER 1: DNS & TLS"

# DNS resolution
for domain in syrabit.ai api.syrabit.ai; do
    ip=$(dig +short "$domain" A 2>/dev/null | head -1)
    if [[ -z "$ip" ]]; then
        ip=$(getent hosts "$domain" 2>/dev/null | awk '{print $1}' | head -1)
    fi
    if [[ -z "$ip" ]]; then
        ip=$(python3 -c "import socket; print(socket.gethostbyname('$domain'))" 2>/dev/null)
    fi

    if [[ -n "$ip" ]]; then
        pass "DNS resolves $domain -> $ip"
        # Check if Cloudflare IP range (104.x, 172.64-71.x, 173.245.x)
        if echo "$ip" | grep -qE '^(104\.|172\.(64|65|66|67|68|69|70|71)\.|173\.245\.|108\.162\.)'; then
            pass "$domain resolves to Cloudflare IP"
        else
            warn "$domain IP $ip may not be Cloudflare"
        fi
    else
        fail "DNS resolution failed for $domain"
    fi
done

# TLS certificate check
for domain in syrabit.ai api.syrabit.ai; do
    cert_info=$(echo | openssl s_client -servername "$domain" -connect "$domain:443" 2>/dev/null | openssl x509 -noout -dates 2>/dev/null)
    if [[ -n "$cert_info" ]]; then
        expiry_str=$(echo "$cert_info" | grep "notAfter" | cut -d= -f2)
        if [[ -n "$expiry_str" ]]; then
            expiry_epoch=$(date -d "$expiry_str" +%s 2>/dev/null || date -j -f "%b %d %T %Y %Z" "$expiry_str" +%s 2>/dev/null || echo "0")
            now_epoch=$(date +%s)
            days_left=$(echo "$expiry_epoch $now_epoch" | awk '{printf "%d", ($1 - $2) / 86400}')
            if [[ "$days_left" -gt 30 ]]; then
                pass "TLS cert $domain valid ($days_left days remaining)"
            elif [[ "$days_left" -gt 0 ]]; then
                warn "TLS cert $domain expires in $days_left days (< 30)"
            else
                fail "TLS cert $domain expired or invalid"
            fi
        else
            warn "Could not parse TLS cert expiry for $domain"
        fi
    else
        fail "TLS connection failed for $domain"
    fi
done


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 2: CLOUDFLARE PAGES (FRONTEND)
# ═══════════════════════════════════════════════════════════════════════════════

header "LAYER 2: CLOUDFLARE PAGES (FRONTEND)"

# Homepage check
status=$(curl -s -o /tmp/e2e_homepage.html -w "%{http_code}" --max-time 15 "$FRONTEND/")
if [[ "$status" == "200" ]]; then
    pass "Homepage (/) returns 200"
    # Check for HTML
    if grep -qi "<html" /tmp/e2e_homepage.html; then
        pass "Homepage contains HTML"
    else
        fail "Homepage missing HTML content"
    fi
else
    fail "Homepage returned $status (expected 200)"
fi

# Static assets
for path in /manifest.json /robots.txt /sw.js /ai.txt /llms.txt /favicon.ico /favicon.svg; do
    status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$FRONTEND$path")
    if [[ "$status" == "200" ]]; then
        pass "Static asset $path returns 200"
    elif [[ "$status" == "304" ]]; then
        pass "Static asset $path returns 304 (cached)"
    else
        warn "Static asset $path returned $status"
    fi
done

# SPA routes
for route in /library /chat /pricing /about /terms /privacy /login; do
    status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -L "$FRONTEND$route")
    if [[ "$status" == "200" || "$status" == "308" ]]; then
        pass "SPA route $route returns $status"
    else
        warn "SPA route $route returned $status (expected 200 or 308)"
    fi
done

# JSON-LD structured data
homepage_html=$(cat /tmp/e2e_homepage.html 2>/dev/null || echo "")
for schema_type in EducationalOrganization WebSite Person LocalBusiness; do
    if echo "$homepage_html" | grep -q "$schema_type"; then
        pass "JSON-LD: $schema_type present"
    else
        warn "JSON-LD: $schema_type not found"
    fi
done

# Open Graph tags
for og_tag in "og:title" "og:image" "og:description"; do
    if echo "$homepage_html" | grep -qi "$og_tag"; then
        pass "OG tag $og_tag present"
    else
        warn "OG tag $og_tag not found"
    fi
done

# Twitter Card tags
for tw_tag in "twitter:card" "twitter:image"; do
    if echo "$homepage_html" | grep -qi "$tw_tag"; then
        pass "Twitter tag $tw_tag present"
    else
        warn "Twitter tag $tw_tag not found"
    fi
done

# Preconnect to api.syrabit.ai
if echo "$homepage_html" | grep -qi "preconnect.*api\.syrabit\.ai\|api\.syrabit\.ai.*preconnect"; then
    pass "Preconnect to api.syrabit.ai present"
else
    warn "Preconnect to api.syrabit.ai not found"
fi

# Critical CSS inlined
if echo "$homepage_html" | grep -qi "<style"; then
    pass "Critical CSS inlined (<style> tag in HTML)"
else
    warn "No inlined <style> tag found"
fi

# Modulepreload hints
if echo "$homepage_html" | grep -qi "modulepreload"; then
    pass "modulepreload hints present"
else
    warn "modulepreload hints not found"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 3: CLOUDFLARE EDGE WORKER
# ═══════════════════════════════════════════════════════════════════════════════

header "LAYER 3: CLOUDFLARE EDGE WORKER"

# GET /health
health_body=$(curl -s --max-time 10 "$EDGE/health")
health_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$EDGE/health")
if [[ "$health_status" == "200" ]]; then
    pass "GET /health returns 200"
    if echo "$health_body" | grep -q '"backend_reachable".*true\|"backend_reachable":true'; then
        pass "/health shows backend_reachable:true"
    else
        warn "/health: backend_reachable:true not found"
    fi
else
    fail "GET /health returned $health_status"
fi

# GET /health/full
full_health_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "$EDGE/health/full")
if [[ "$full_health_status" == "200" || "$full_health_status" == "503" ]]; then
    pass "GET /health/full returns $full_health_status"
else
    fail "GET /health/full returned $full_health_status"
fi

# CORS preflight
cors_headers=$(curl -s -D - -o /dev/null --max-time 10 \
    -X OPTIONS \
    -H "Origin: https://syrabit.ai" \
    -H "Access-Control-Request-Method: POST" \
    -H "Access-Control-Request-Headers: content-type, authorization, x-anon-id, traceparent" \
    "$EDGE/api/v1/chat/stream")

# Check CORS headers
cors_origin=$(echo "$cors_headers" | grep -i "access-control-allow-origin" | head -1 | tr -d '\r')
if echo "$cors_origin" | grep -qi "https://syrabit.ai"; then
    pass "CORS: access-control-allow-origin: https://syrabit.ai"
else
    fail "CORS: allow-origin missing or wrong: $cors_origin"
fi

if echo "$cors_headers" | grep -qi "access-control-allow-credentials.*true"; then
    pass "CORS: access-control-allow-credentials: true"
else
    fail "CORS: allow-credentials not true"
fi

cors_allow_headers=$(echo "$cors_headers" | grep -i "access-control-allow-headers" | head -1 | tr '[:upper:]' '[:lower:]')
for h in "content-type" "authorization" "x-anon-id" "traceparent"; do
    if echo "$cors_allow_headers" | grep -qi "$h"; then
        pass "CORS: allow-headers includes $h"
    else
        fail "CORS: allow-headers missing $h"
    fi
done

cors_methods=$(echo "$cors_headers" | grep -i "access-control-allow-methods" | head -1 | tr '[:upper:]' '[:lower:]')
for m in "get" "post" "put" "delete" "options"; do
    if echo "$cors_methods" | grep -qi "$m"; then
        pass "CORS: allow-methods includes $(echo "$m" | tr '[:lower:]' '[:upper:]')"
    else
        fail "CORS: allow-methods missing $(echo "$m" | tr '[:lower:]' '[:upper:]')"
    fi
done

cors_expose=$(echo "$cors_headers" | grep -i "access-control-expose-headers" | head -1 | tr '[:upper:]' '[:lower:]')
for eh in "x-ratelimit-limit" "x-ratelimit-remaining" "x-ratelimit-reset" "x-request-id"; do
    if echo "$cors_expose" | grep -qi "$eh"; then
        pass "CORS: expose-headers includes $eh"
    else
        warn "CORS: expose-headers missing $eh"
    fi
done

if echo "$cors_headers" | grep -qi "access-control-max-age"; then
    pass "CORS: access-control-max-age present"
else
    warn "CORS: access-control-max-age not found"
fi

# Invalid JWT returns 401
jwt_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -H "Authorization: Bearer invalid.token.here" \
    "$EDGE/api/v1/users/me")
if [[ "$jwt_status" == "401" ]]; then
    pass "Invalid JWT on /api/v1/users/me returns 401"
else
    fail "Invalid JWT returned $jwt_status (expected 401)"
fi

# Bot user-agent not blocked
bot_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -H "User-Agent: Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)" \
    "$EDGE/health")
if [[ "$bot_status" != "403" ]]; then
    pass "Bot user-agent (Googlebot) not blocked (status: $bot_status)"
else
    fail "Bot user-agent blocked with 403"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 4: GCP CLOUD RUN BACKEND HEALTH
# ═══════════════════════════════════════════════════════════════════════════════

header "LAYER 4: GCP CLOUD RUN BACKEND HEALTH"

full_health_body=$(curl -s --max-time 15 "$EDGE/health/full")

for svc in mongodb redis vertex_ai vertex_search; do
    # Try multiple JSON paths for service status
    svc_status=$(echo "$full_health_body" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    # Try common structures
    for path in [
        lambda d: d.get('services', {}).get('$svc', {}).get('status'),
        lambda d: d.get('checks', {}).get('$svc'),
        lambda d: d.get('$svc'),
        lambda d: d.get('components', {}).get('$svc', {}).get('status'),
    ]:
        val = path(data)
        if val:
            print(val)
            sys.exit(0)
    print('not_found')
except:
    print('parse_error')
" 2>/dev/null)

    if [[ "$svc_status" == "healthy" || "$svc_status" == "ok" || "$svc_status" == "up" || "$svc_status" == "connected" ]]; then
        pass "/health/full: $svc is $svc_status"
    elif [[ "$svc_status" == "not_found" || "$svc_status" == "parse_error" ]]; then
        warn "/health/full: $svc status not found in response"
    else
        warn "/health/full: $svc status is '$svc_status'"
    fi
done


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 5: AUTHENTICATION & USER FLOW
# ═══════════════════════════════════════════════════════════════════════════════

header "LAYER 5: AUTHENTICATION & USER FLOW"

# Signup with empty email/password returns 422
signup_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"email":"","password":""}' \
    "$EDGE/api/v1/auth/signup")
if [[ "$signup_status" == "422" ]]; then
    pass "POST /auth/signup empty fields returns 422"
else
    fail "POST /auth/signup empty fields returned $signup_status (expected 422)"
fi

# Signup with short password returns 422
short_pw_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"email":"test@example.com","password":"ab"}' \
    "$EDGE/api/v1/auth/signup")
if [[ "$short_pw_status" == "422" ]]; then
    pass "POST /auth/signup short password returns 422"
else
    fail "POST /auth/signup short password returned $short_pw_status (expected 422)"
fi

# Login with wrong credentials returns 401
login_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"email":"nonexistent_e2e_test@example.com","password":"WrongP@ssw0rd!123"}' \
    "$EDGE/api/v1/auth/login")
if [[ "$login_status" == "401" ]]; then
    pass "POST /auth/login wrong credentials returns 401"
else
    fail "POST /auth/login wrong credentials returned $login_status (expected 401)"
fi

# Protected endpoints without token return 401
for endpoint in "/api/v1/users/me" "/api/v1/users/credits" "/api/v1/conversations"; do
    noauth_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$EDGE$endpoint")
    if [[ "$noauth_status" == "401" ]]; then
        pass "GET $endpoint without token returns 401"
    else
        fail "GET $endpoint without token returned $noauth_status (expected 401)"
    fi
done


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 6: CHAT - ENGLISH (VERTEX AI GEMINI)
# ═══════════════════════════════════════════════════════════════════════════════

header "LAYER 6: CHAT - ENGLISH (VERTEX AI GEMINI)"

if [[ -n "$TEST_JWT_TOKEN" && -n "$TEST_TURNSTILE_TOKEN" ]]; then
    # Streaming chat
    en_stream_start=$(date +%s%N)
    en_stream_body=$(curl -s --max-time 30 \
        -X POST \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $TEST_JWT_TOKEN" \
        -H "X-Turnstile-Token: $TEST_TURNSTILE_TOKEN" \
        -H "x-anon-id: $ANON_ID" \
        -w "\n__HTTP_STATUS__%{http_code}__TTFB__%{time_starttransfer}__TOTAL__%{time_total}" \
        -d '{"message":"What is photosynthesis?","language":"en","stream":true}' \
        "$EDGE/api/v1/chat/stream")

    en_status=$(echo "$en_stream_body" | grep "__HTTP_STATUS__" | sed 's/.*__HTTP_STATUS__\([0-9]*\).*/\1/')
    en_ttfb=$(echo "$en_stream_body" | grep "__TTFB__" | sed 's/.*__TTFB__\([0-9.]*\).*/\1/')
    en_total=$(echo "$en_stream_body" | grep "__TOTAL__" | sed 's/.*__TOTAL__\([0-9.]*\).*/\1/')
    en_ttfb_ms=$(echo "$en_ttfb" | awk '{printf "%d", $1 * 1000}')
    en_total_ms=$(echo "$en_total" | awk '{printf "%d", $1 * 1000}')

    if [[ "$en_status" == "200" ]]; then
        pass "POST /chat/stream (en) returns 200"
    elif [[ "$en_status" == "429" ]]; then
        warn "POST /chat/stream (en) rate limited (429)"
    else
        fail "POST /chat/stream (en) returned $en_status (expected 200)"
    fi

    # Check for "done": true in stream
    if echo "$en_stream_body" | grep -q '"done".*true\|"done": true'; then
        pass "English stream contains done:true"
    else
        warn "English stream done:true not found"
    fi

    # Check for gemini model
    if echo "$en_stream_body" | grep -qi "gemini"; then
        pass "English stream contains gemini model reference"
    else
        warn "English stream: gemini model not found in response"
    fi

    echo -e "  ${CYAN}[INFO]${NC} English TTFB: ${en_ttfb_ms}ms, Total: ${en_total_ms}ms"

    # Non-streaming chat
    en_nonsream_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 30 \
        -X POST \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $TEST_JWT_TOKEN" \
        -H "X-Turnstile-Token: $TEST_TURNSTILE_TOKEN" \
        -H "x-anon-id: $ANON_ID" \
        -d '{"message":"What is photosynthesis?","language":"en","stream":false}' \
        "$EDGE/api/v1/chat/")
    if [[ "$en_nonsream_status" == "200" || "$en_nonsream_status" == "429" ]]; then
        pass "POST /chat/ non-streaming (en) returns $en_nonsream_status"
    else
        fail "POST /chat/ non-streaming (en) returned $en_nonsream_status"
    fi
else
    warn "SKIPPED: English chat tests (TEST_JWT_TOKEN/TEST_TURNSTILE_TOKEN not set)"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 7: CHAT - ASSAMESE (SARVAM AI)
# ═══════════════════════════════════════════════════════════════════════════════

header "LAYER 7: CHAT - ASSAMESE (SARVAM AI)"

if [[ -n "$TEST_JWT_TOKEN" && -n "$TEST_TURNSTILE_TOKEN" ]]; then
    as_stream_body=$(curl -s --max-time 30 \
        -X POST \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $TEST_JWT_TOKEN" \
        -H "X-Turnstile-Token: $TEST_TURNSTILE_TOKEN" \
        -H "x-anon-id: $ANON_ID" \
        -w "\n__HTTP_STATUS__%{http_code}__TTFB__%{time_starttransfer}__TOTAL__%{time_total}" \
        -d "{\"message\":\"সালোক সংশ্লেষণ কি?\",\"language\":\"as\",\"stream\":true}" \
        "$EDGE/api/v1/chat/stream")

    as_status=$(echo "$as_stream_body" | grep "__HTTP_STATUS__" | sed 's/.*__HTTP_STATUS__\([0-9]*\).*/\1/')
    as_ttfb=$(echo "$as_stream_body" | grep "__TTFB__" | sed 's/.*__TTFB__\([0-9.]*\).*/\1/')
    as_total=$(echo "$as_stream_body" | grep "__TOTAL__" | sed 's/.*__TOTAL__\([0-9.]*\).*/\1/')
    as_ttfb_ms=$(echo "$as_ttfb" | awk '{printf "%d", $1 * 1000}')
    as_total_ms=$(echo "$as_total" | awk '{printf "%d", $1 * 1000}')

    if [[ "$as_status" == "200" ]]; then
        pass "POST /chat/stream (as) returns 200"
    elif [[ "$as_status" == "429" ]]; then
        warn "POST /chat/stream (as) rate limited (429)"
    else
        fail "POST /chat/stream (as) returned $as_status (expected 200)"
    fi

    # Check for sarvam model
    if echo "$as_stream_body" | grep -qi "sarvam"; then
        pass "Assamese stream contains sarvam model reference"
    else
        warn "Assamese stream: sarvam model not found in response"
    fi

    echo -e "  ${CYAN}[INFO]${NC} Assamese TTFB: ${as_ttfb_ms}ms, Total: ${as_total_ms}ms"
else
    warn "SKIPPED: Assamese chat tests (TEST_JWT_TOKEN/TEST_TURNSTILE_TOKEN not set)"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 8: RAG PIPELINE (VERTEX AI SEARCH)
# ═══════════════════════════════════════════════════════════════════════════════

header "LAYER 8: RAG PIPELINE (VERTEX AI SEARCH)"

if [[ -n "$TEST_JWT_TOKEN" && -n "$TEST_TURNSTILE_TOKEN" ]]; then
    rag_body=$(curl -s --max-time 30 \
        -X POST \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $TEST_JWT_TOKEN" \
        -H "X-Turnstile-Token: $TEST_TURNSTILE_TOKEN" \
        -H "x-anon-id: $ANON_ID" \
        -w "\n__HTTP_STATUS__%{http_code}" \
        -d '{"message":"Explain the NCERT Class 10 Science chapter on Light - Reflection and Refraction","language":"en","stream":true}' \
        "$EDGE/api/v1/chat/stream")

    rag_status=$(echo "$rag_body" | grep "__HTTP_STATUS__" | sed 's/.*__HTTP_STATUS__\([0-9]*\).*/\1/')

    if [[ "$rag_status" == "200" ]]; then
        pass "RAG query returns 200"
    elif [[ "$rag_status" == "429" ]]; then
        warn "RAG query rate limited (429)"
    else
        fail "RAG query returned $rag_status"
    fi

    # Check for done:true
    if echo "$rag_body" | grep -q '"done".*true\|"done": true'; then
        pass "RAG stream contains done:true"
    else
        warn "RAG stream: done:true not found"
    fi

    # Check for citations
    if echo "$rag_body" | grep -qE '\[1\]|\[2\]'; then
        pass "RAG response contains citations [1] or [2]"
    else
        warn "RAG response: no citations [1]/[2] found"
    fi
else
    warn "SKIPPED: RAG pipeline tests (TEST_JWT_TOKEN/TEST_TURNSTILE_TOKEN not set)"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 9: MONGODB CONTENT & DATA
# ═══════════════════════════════════════════════════════════════════════════════

header "LAYER 9: MONGODB CONTENT & DATA"

# Library bundle
lib_status=$(curl -s -o /tmp/e2e_library.json -w "%{http_code}" --max-time 15 \
    -H "x-anon-id: $ANON_ID" \
    "$EDGE/api/v1/content/library-bundle")
if [[ "$lib_status" == "200" ]]; then
    pass "GET /content/library-bundle returns 200"
    boards_count=$(python3 -c "
import sys, json
try:
    with open('/tmp/e2e_library.json') as f:
        data = json.load(f)
    if isinstance(data, dict) and 'boards' in data:
        print(len(data['boards']))
    elif isinstance(data, list):
        print(len(data))
    else:
        print('unknown_structure')
except Exception as e:
    print(f'parse_error: {e}')
" 2>/dev/null)
    echo -e "  ${CYAN}[INFO]${NC} Library bundle boards count: $boards_count"
else
    fail "GET /content/library-bundle returned $lib_status"
fi

# Subscription plans
plans_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$EDGE/api/v1/subscription/plans")
if [[ "$plans_status" == "200" ]]; then
    pass "GET /subscription/plans returns 200"
else
    fail "GET /subscription/plans returned $plans_status"
fi

# Changelog
changelog_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$EDGE/api/v1/changelog")
if [[ "$changelog_status" == "200" ]]; then
    pass "GET /changelog returns 200"
else
    warn "GET /changelog returned $changelog_status"
fi

# Chat history with anon ID
history_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -H "x-anon-id: $ANON_ID" \
    "$EDGE/api/v1/chat/history")
if [[ "$history_status" == "200" ]]; then
    pass "GET /chat/history with x-anon-id returns 200"
else
    warn "GET /chat/history returned $history_status"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 10: ADMIN PANEL ACCESS CONTROL
# ═══════════════════════════════════════════════════════════════════════════════

header "LAYER 10: ADMIN PANEL ACCESS CONTROL"

# Admin endpoints without auth should return 401
for admin_ep in dashboard users analytics/daily settings notifications conversations ai alerts; do
    admin_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$EDGE/api/v1/admin/$admin_ep")
    if [[ "$admin_status" == "401" ]]; then
        pass "GET /admin/$admin_ep without auth returns 401"
    elif [[ "$admin_status" == "403" ]]; then
        pass "GET /admin/$admin_ep without auth returns 403 (forbidden)"
    else
        fail "GET /admin/$admin_ep without auth returned $admin_status (expected 401)"
    fi
done

# Admin login with wrong credentials
admin_login_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"email":"fake_admin@test.com","password":"WrongAdminP@ss123"}' \
    "$EDGE/api/v1/admin/login")
if [[ "$admin_login_status" == "401" ]]; then
    pass "POST /admin/login wrong credentials returns 401"
else
    fail "POST /admin/login wrong credentials returned $admin_login_status (expected 401)"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 11: SEO / GEO / AEO
# ═══════════════════════════════════════════════════════════════════════════════

header "LAYER 11: SEO / GEO / AEO"

# Sitemaps
for sitemap in sitemap.xml sitemap-static.xml sitemap-subjects.xml sitemap-chapters.xml sitemap-topics.xml; do
    sm_status=$(curl -s -o /tmp/e2e_sitemap_"$sitemap" -w "%{http_code}" --max-time 10 "$FRONTEND/$sitemap")
    if [[ "$sm_status" == "200" ]]; then
        pass "Sitemap $sitemap returns 200"
    else
        warn "Sitemap $sitemap returned $sm_status"
    fi
done

# Sitemap index valid XML
if [[ -f /tmp/e2e_sitemap_sitemap.xml ]]; then
    if grep -q "<sitemapindex" /tmp/e2e_sitemap_sitemap.xml; then
        pass "Sitemap index contains <sitemapindex> tag"
    elif grep -q "<urlset" /tmp/e2e_sitemap_sitemap.xml; then
        pass "Sitemap contains <urlset> (flat sitemap format)"
    else
        warn "Sitemap index: no <sitemapindex> or <urlset> tag found"
    fi

    # Check references to sub-sitemaps
    for sub in sitemap-static sitemap-subjects sitemap-chapters sitemap-topics; do
        if grep -q "$sub" /tmp/e2e_sitemap_sitemap.xml; then
            pass "Sitemap index references $sub"
        else
            warn "Sitemap index missing reference to $sub"
        fi
    done
fi

# RSS feed
rss_body=$(curl -s -o /tmp/e2e_rss.xml -w "%{http_code}" --max-time 10 "$FRONTEND/rss.xml")
if [[ "$rss_body" == "200" ]]; then
    pass "RSS feed (/rss.xml) returns 200"
    if grep -q "<rss" /tmp/e2e_rss.xml; then
        pass "RSS feed contains <rss> tag"
    else
        warn "RSS feed missing <rss> tag"
    fi
    if grep -q "<item" /tmp/e2e_rss.xml; then
        pass "RSS feed has <item> elements"
    else
        warn "RSS feed has no <item> elements (empty)"
    fi
else
    warn "RSS feed returned $rss_body"
fi

# JSON feed
json_feed_status=$(curl -s -o /tmp/e2e_feed.json -w "%{http_code}" --max-time 10 "$FRONTEND/feed.json")
if [[ "$json_feed_status" == "200" ]]; then
    pass "JSON feed (/feed.json) returns 200"
    if python3 -c "import json; json.load(open('/tmp/e2e_feed.json'))" 2>/dev/null; then
        pass "JSON feed is valid JSON"
        has_items=$(python3 -c "
import json
data = json.load(open('/tmp/e2e_feed.json'))
print('yes' if 'items' in data and isinstance(data['items'], list) else 'no')
" 2>/dev/null)
        if [[ "$has_items" == "yes" ]]; then
            pass "JSON feed has items array"
        else
            warn "JSON feed: no items array found"
        fi
    else
        warn "JSON feed is not valid JSON"
    fi
else
    warn "JSON feed returned $json_feed_status"
fi

# RSS link in homepage HTML
if echo "$homepage_html" | grep -qi 'rel="alternate".*application/rss+xml\|type="application/rss+xml"'; then
    pass "Homepage has <link rel=\"alternate\" type=\"application/rss+xml\">"
else
    warn "Homepage missing RSS alternate link"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 12: SECURITY HEADERS
# ═══════════════════════════════════════════════════════════════════════════════

header "LAYER 12: SECURITY HEADERS"

sec_headers=$(curl -s -D - -o /dev/null --max-time 10 "$FRONTEND/")

# X-Content-Type-Options: nosniff
if echo "$sec_headers" | grep -qi "x-content-type-options.*nosniff"; then
    pass "X-Content-Type-Options: nosniff"
else
    fail "X-Content-Type-Options: nosniff not found"
fi

# X-Frame-Options: DENY
if echo "$sec_headers" | grep -qi "x-frame-options.*DENY"; then
    pass "X-Frame-Options: DENY"
elif echo "$sec_headers" | grep -qi "x-frame-options"; then
    frame_val=$(echo "$sec_headers" | grep -i "x-frame-options" | head -1 | tr -d '\r')
    warn "X-Frame-Options present but not DENY: $frame_val"
else
    fail "X-Frame-Options not found"
fi

# Strict-Transport-Security
if echo "$sec_headers" | grep -qi "strict-transport-security"; then
    pass "Strict-Transport-Security present"
else
    fail "Strict-Transport-Security not found"
fi

# Content-Security-Policy
if echo "$sec_headers" | grep -qi "content-security-policy"; then
    pass "Content-Security-Policy present"
else
    warn "Content-Security-Policy not found"
fi

# Referrer-Policy
if echo "$sec_headers" | grep -qi "referrer-policy"; then
    pass "Referrer-Policy present"
else
    warn "Referrer-Policy not found"
fi

# X-XSS-Protection: 0
if echo "$sec_headers" | grep -qi "x-xss-protection.*0"; then
    pass "X-XSS-Protection: 0"
elif echo "$sec_headers" | grep -qi "x-xss-protection"; then
    xss_val=$(echo "$sec_headers" | grep -i "x-xss-protection" | head -1 | tr -d '\r')
    warn "X-XSS-Protection present but not 0: $xss_val"
else
    warn "X-XSS-Protection header not found"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 13: PERFORMANCE
# ═══════════════════════════════════════════════════════════════════════════════

header "LAYER 13: PERFORMANCE"

# Frontend TTFB
fe_ttfb=$(curl -s -o /dev/null -w "%{time_starttransfer}" --max-time 15 "$FRONTEND/")
fe_ttfb_ms=$(echo "$fe_ttfb" | awk '{printf "%d", $1 * 1000}')
if [[ "$fe_ttfb_ms" -lt 500 ]]; then
    pass "Frontend / TTFB: ${fe_ttfb_ms}ms (< 500ms)"
else
    fail "Frontend / TTFB: ${fe_ttfb_ms}ms (>= 500ms target)"
fi

# Edge /health TTFB
edge_ttfb=$(curl -s -o /dev/null -w "%{time_starttransfer}" --max-time 15 "$EDGE/health")
edge_ttfb_ms=$(echo "$edge_ttfb" | awk '{printf "%d", $1 * 1000}')
if [[ "$edge_ttfb_ms" -lt 1000 ]]; then
    pass "Edge /health TTFB: ${edge_ttfb_ms}ms (< 1000ms)"
else
    fail "Edge /health TTFB: ${edge_ttfb_ms}ms (>= 1000ms target)"
fi

# Content /library-bundle TTFB
content_ttfb=$(curl -s -o /dev/null -w "%{time_starttransfer}" --max-time 20 \
    -H "x-anon-id: $ANON_ID" \
    "$EDGE/api/v1/content/library-bundle")
content_ttfb_ms=$(echo "$content_ttfb" | awk '{printf "%d", $1 * 1000}')
if [[ "$content_ttfb_ms" -lt 2000 ]]; then
    pass "Content /library-bundle TTFB: ${content_ttfb_ms}ms (< 2000ms)"
else
    fail "Content /library-bundle TTFB: ${content_ttfb_ms}ms (>= 2000ms target)"
fi

# Chat stream TTFB (unauthenticated - just reports routing time)
chat_ttfb=$(curl -s -o /dev/null -w "%{time_starttransfer}" --max-time 15 \
    -X POST \
    -H "Content-Type: application/json" \
    -H "x-anon-id: $ANON_ID" \
    -d '{"message":"hi","language":"en","stream":true}' \
    "$EDGE/api/v1/chat/stream")
chat_ttfb_ms=$(echo "$chat_ttfb" | awk '{printf "%d", $1 * 1000}')
echo -e "  ${CYAN}[INFO]${NC} Chat stream TTFB (routing): ${chat_ttfb_ms}ms (target <3000ms)"
if [[ "$chat_ttfb_ms" -lt 3000 ]]; then
    pass "Chat stream TTFB: ${chat_ttfb_ms}ms (< 3000ms)"
else
    warn "Chat stream TTFB: ${chat_ttfb_ms}ms (>= 3000ms target)"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 14: RATE LIMITING
# ═══════════════════════════════════════════════════════════════════════════════

header "LAYER 14: RATE LIMITING"

# 3 rapid requests to library-bundle should all return 200
rate_all_pass=true
for i in 1 2 3; do
    rl_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
        -H "x-anon-id: $ANON_ID" \
        "$EDGE/api/v1/content/library-bundle")
    if [[ "$rl_status" == "200" ]]; then
        pass "Rapid request $i/3 to /content/library-bundle: 200"
    else
        fail "Rapid request $i/3 to /content/library-bundle: $rl_status (expected 200)"
        rate_all_pass=false
    fi
done

if [[ "$rate_all_pass" == "true" ]]; then
    echo -e "  ${CYAN}[INFO]${NC} All 3 rapid requests passed without rate limiting"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 15: PAYMENTS & SUBSCRIPTION
# ═══════════════════════════════════════════════════════════════════════════════

header "LAYER 15: PAYMENTS & SUBSCRIPTION"

# Subscription plans (public)
plans_status2=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$EDGE/api/v1/subscription/plans")
if [[ "$plans_status2" == "200" ]]; then
    pass "GET /subscription/plans returns 200"
else
    fail "GET /subscription/plans returned $plans_status2"
fi

# Create order without auth returns 401
order_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"plan_id":"test_plan"}' \
    "$EDGE/api/v1/payments/create-order")
if [[ "$order_status" == "401" ]]; then
    pass "POST /payments/create-order without auth returns 401"
else
    fail "POST /payments/create-order without auth returned $order_status (expected 401)"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 16: WEBHOOKS
# ═══════════════════════════════════════════════════════════════════════════════

header "LAYER 16: WEBHOOKS"

# Razorpay webhook with empty body should not 500
webhook_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{}' \
    "$EDGE/api/webhooks/razorpay")
if [[ "$webhook_status" == "400" || "$webhook_status" == "401" || "$webhook_status" == "422" ]]; then
    pass "POST /webhooks/razorpay empty body returns $webhook_status (not 500)"
elif [[ "$webhook_status" == "500" ]]; then
    fail "POST /webhooks/razorpay empty body returns 500 (server error)"
else
    warn "POST /webhooks/razorpay returned $webhook_status (expected 400/401/422)"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

echo ""
echo -e "${BOLD}$(printf '%.0s=' {1..70})${NC}"
echo -e "${BOLD}  FINAL REPORT${NC}"
echo -e "${BOLD}$(printf '%.0s=' {1..70})${NC}"
echo ""

TOTAL=$((PASS_COUNT + FAIL_COUNT + WARN_COUNT))
if [[ "$TOTAL" -gt 0 ]]; then
    PASS_PCT=$(echo "$PASS_COUNT $TOTAL" | awk '{printf "%.1f", ($1 / $2) * 100}')
else
    PASS_PCT="0.0"
fi

echo -e "  Total tests:  $TOTAL"
echo -e "  ${GREEN}PASS:${NC}         $PASS_COUNT"
echo -e "  ${RED}FAIL:${NC}         $FAIL_COUNT"
echo -e "  ${YELLOW}WARN:${NC}         $WARN_COUNT"
echo -e "  Pass rate:    ${PASS_PCT}%"
echo ""

if [[ ${#FAILURES[@]} -gt 0 ]]; then
    echo -e "${BOLD}${RED}  FAILURES:${NC}"
    for f in "${FAILURES[@]}"; do
        echo -e "    - $f"
    done
    echo ""
fi

# Cleanup temp files
rm -f /tmp/e2e_homepage.html /tmp/e2e_library.json /tmp/e2e_rss.xml /tmp/e2e_feed.json /tmp/e2e_sitemap_*

echo -e "${BOLD}$(printf '%.0s=' {1..70})${NC}"

if [[ "$FAIL_COUNT" -eq 0 ]]; then
    echo -e "  ${GREEN}${BOLD}ALL TESTS PASSED${NC}"
    echo -e "${BOLD}$(printf '%.0s=' {1..70})${NC}"
    echo ""
    exit 0
else
    echo -e "  ${RED}${BOLD}$FAIL_COUNT TEST(S) FAILED${NC}"
    echo -e "${BOLD}$(printf '%.0s=' {1..70})${NC}"
    echo ""
    exit 1
fi
