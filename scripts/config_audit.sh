#!/usr/bin/env bash
# =============================================================================
# Syrabit Configuration Audit & Test Script
#
# Covers all 15 failure categories from the production checklist:
#   1.  Authentication / Authorization config
#   2.  DNS resolution
#   3.  TLS / SSL
#   4.  Worker / proxy BACKEND_URL wiring
#   5.  CORS headers
#   6.  Network layers (direct backend vs proxy)
#   7.  Database connectivity (MongoDB)
#   8.  Environment variables completeness
#   9.  Cache headers
#   10. API response shape / version mismatch
#   11. Rate limiting headers
#   12. Security headers
#   13. Health check mismatch (shallow ≠ deep)
#   14. Frontend proxy URL config
#   15. JWT / browser auth token validation
#
# Usage:
#   bash scripts/config_audit.sh                     # dev (localhost)
#   bash scripts/config_audit.sh --prod              # production endpoints
#   bash scripts/config_audit.sh --base-url http://localhost:8000
#   bash scripts/config_audit.sh --fix-hints         # show remediation hints
#
# Environment vars:
#   BASE_URL          backend base URL          (default: http://localhost:8000)
#   FRONTEND_URL      frontend base URL         (default: http://localhost:5000)
#   PROD_URL          production backend URL    (default: https://api.syrabit.ai)
#   ADMIN_EMAIL       admin email for auth test (optional)
#   ADMIN_PASSWORD    admin password            (optional)
#   JWT_SECRET        JWT secret for validation (read from env automatically)
#   FIX_HINTS         set to 1 to print remediation hints after failures
#
# Exit codes:
#   0  All checks passed (warnings are OK)
#   1  One or more FAIL checks
#   2  Critical failures (backend unreachable, DB down)
# =============================================================================

set -uo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
BASE_URL="${BASE_URL:-http://localhost:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:5000}"
PROD_URL="${PROD_URL:-https://api.syrabit.ai}"
ADMIN_EMAIL="${ADMIN_EMAIL:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
FIX_HINTS="${FIX_HINTS:-0}"
PROD_MODE=0
SHOW_FIX_HINTS=0

# ── Arg parsing ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prod)         PROD_MODE=1; BASE_URL="$PROD_URL"; FRONTEND_URL="https://syrabit.ai"; shift ;;
    --base-url)     BASE_URL="$2"; shift 2 ;;
    --frontend-url) FRONTEND_URL="$2"; shift 2 ;;
    --fix-hints)    SHOW_FIX_HINTS=1; shift ;;
    --help|-h)
      sed -n '2,40p' "$0" | grep '^#' | sed 's/^# \?//'
      exit 0
      ;;
    *) echo "Unknown option: $1  (use --help)"; exit 1 ;;
  esac
done

[[ "$FIX_HINTS" == "1" ]] && SHOW_FIX_HINTS=1

# ── Colours ──────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
  CYAN='\033[0;36m';  BOLD='\033[1m';   DIM='\033[2m'; RESET='\033[0m'
  BLUE='\033[0;34m'
else
  GREEN=''; RED=''; YELLOW=''; CYAN=''; BOLD=''; DIM=''; RESET=''; BLUE=''
fi

# ── Counters ─────────────────────────────────────────────────────────────────
PASS=0; FAIL=0; WARN=0; SKIP=0; CRITICAL=0
declare -a FAILURES=()
declare -a HINTS=()

# ── Helpers ──────────────────────────────────────────────────────────────────
pass()  { echo -e "  ${GREEN}✓${RESET} $1"; PASS=$((PASS+1)); }
fail()  {
  local msg="$1" hint="${2:-}"
  echo -e "  ${RED}✗${RESET} $msg"
  FAIL=$((FAIL+1))
  FAILURES+=("$msg")
  [[ -n "$hint" ]] && HINTS+=("${RED}✗${RESET} $msg\n  ${DIM}→ $hint${RESET}")
}
crit()  {
  local msg="$1" hint="${2:-}"
  echo -e "  ${RED}✗ [CRITICAL]${RESET} $msg"
  FAIL=$((FAIL+1)); CRITICAL=$((CRITICAL+1))
  FAILURES+=("CRITICAL: $msg")
  [[ -n "$hint" ]] && HINTS+=("${RED}✗ CRITICAL${RESET} $msg\n  ${DIM}→ $hint${RESET}")
}
warn()  { echo -e "  ${YELLOW}△${RESET} $1"; WARN=$((WARN+1)); }
skip()  { echo -e "  ${BLUE}–${RESET} $1"; SKIP=$((SKIP+1)); }
info()  { echo -e "  ${DIM}  $1${RESET}"; }
header(){ echo -e "\n${CYAN}${BOLD}━━ $1 ━━${RESET}"; }

# HTTP helpers
http_status()  { curl -s -o /dev/null -w "%{http_code}" --max-time 12 "$@" 2>/dev/null || echo "000"; }
http_body()    { curl -s --max-time 12 "$@" 2>/dev/null || echo ""; }
http_headers() { curl -sI --max-time 12 "$@" 2>/dev/null || echo ""; }
http_all()     {
  # Sets: _STATUS _BODY _HEADERS _TTFB
  local tmp; tmp=$(mktemp); local hdr; hdr=$(mktemp)
  local timing
  timing=$(curl -s -w '%{http_code} %{time_starttransfer}' -o "$tmp" -D "$hdr" \
    --max-time 12 "$@" 2>/dev/null) || timing="000 0"
  _STATUS=$(echo "$timing" | awk '{print $1}')
  _TTFB=$(echo "$timing" | awk '{printf "%d", $2*1000}')
  _BODY=$(cat "$tmp" 2>/dev/null)
  _HEADERS=$(cat "$hdr" 2>/dev/null)
  rm -f "$tmp" "$hdr"
}

json_val() {
  # json_val <json_string> <python_expr>   (d = parsed dict)
  printf '%s' "$1" | python3 -c "import json,sys; d=json.load(sys.stdin); print($2)" 2>/dev/null || echo ""
}

has_header() { echo "$_HEADERS" | grep -qi "$1"; }
hdr_val()    { echo "$_HEADERS" | grep -i "^$1:" | head -1 | sed 's/^[^:]*: //' | tr -d '\r\n'; }

env_set()    { [[ -n "${!1:-}" ]]; }
env_secret() {
  # returns true if the env var looks like it's set to a real value (not a placeholder)
  local val="${!1:-}"
  [[ -n "$val" ]] && \
  [[ "$val" != *"CHANGE_ME"* ]] && \
  [[ "$val" != *"placeholder"* ]] && \
  [[ "$val" != *"dev-only"* ]] && \
  [[ "$val" != *"test-secret"* ]] && \
  [[ "$val" != *"super_secret"* ]]
}

# ─────────────────────────────────────────────────────────────────────────────
echo -e "\n${BOLD}Syrabit Configuration Audit${RESET}"
echo    "  Target : $BASE_URL"
echo    "  Frontend: $FRONTEND_URL"
[[ $PROD_MODE -eq 1 ]] && echo -e "  ${YELLOW}Mode: PRODUCTION${RESET}" \
                       || echo    "  Mode: Development (localhost)"
echo    "  Date   : $(date -u '+%Y-%m-%d %H:%M:%S UTC')"


# =============================================================================
# SECTION 1 — PREREQUISITES & TOOLS
# =============================================================================
header "1. Prerequisites"

command -v curl    &>/dev/null && pass "curl installed ($(curl --version | head -1 | awk '{print $2}'))" \
                               || { crit "curl not installed" "apt-get install curl"; exit 2; }
command -v python3 &>/dev/null && pass "python3 available ($(python3 --version 2>&1))" \
                               || { crit "python3 not found — JSON parsing will fail"; }
command -v dig     &>/dev/null && pass "dig available" || warn "dig not found (DNS checks limited)"
command -v openssl &>/dev/null && pass "openssl available" || warn "openssl not found (HMAC tests limited)"

[[ "$BASE_URL" != */ ]]     && pass "BASE_URL has no trailing slash" \
                            || warn "BASE_URL has trailing slash — may cause //path issues"
[[ "$FRONTEND_URL" != */ ]] && pass "FRONTEND_URL has no trailing slash" \
                            || warn "FRONTEND_URL has trailing slash"


# =============================================================================
# SECTION 2 — ENVIRONMENT VARIABLES (Checklist #8)
# =============================================================================
header "2. Environment Variables"

# Critical — app won't function without these
CRITICAL_VARS=(MONGODB_URI JWT_SECRET)
OPTIONAL_VARS=(GEMINI_API_KEY SARVAM_API_KEY RAZORPAY_KEY_ID RAZORPAY_KEY_SECRET
               RESEND_API_KEY UPSTASH_REDIS_REST_URL UPSTASH_REDIS_REST_TOKEN
               POSTHOG_API_KEY SENTRY_DSN ADMIN_EMAIL ADMIN_PASSWORD
               EDGE_SHARED_SECRET VERTEX_PROJECT_ID GOOGLE_APPLICATION_CREDENTIALS_JSON)

for v in "${CRITICAL_VARS[@]}"; do
  if env_set "$v"; then
    if env_secret "$v"; then
      pass "$v is set"
    else
      fail "$v is set to a PLACEHOLDER value" \
           "Replace the placeholder. For JWT_SECRET: openssl rand -base64 32"
    fi
  else
    crit "$v is NOT SET — core functionality broken" \
         "Add $v to Replit Secrets (sidebar → Secrets tab)"
  fi
done

# JWT_SECRET length check
JWT_SECRET="${JWT_SECRET:-}"
if [[ -n "$JWT_SECRET" && ${#JWT_SECRET} -lt 32 ]]; then
  fail "JWT_SECRET is too short (${#JWT_SECRET} chars, need ≥ 32)" \
       "Generate a strong secret: openssl rand -base64 32"
elif [[ -n "$JWT_SECRET" && ${#JWT_SECRET} -ge 32 ]]; then
  pass "JWT_SECRET length OK (${#JWT_SECRET} chars)"
fi

# JWT algorithm
JWT_ALG="${JWT_ALGORITHM:-HS256}"
if [[ "$JWT_ALG" == "HS256" || "$JWT_ALG" == "RS256" ]]; then
  pass "JWT_ALGORITHM=$JWT_ALG"
else
  warn "JWT_ALGORITHM=$JWT_ALG — expected HS256 or RS256"
fi

for v in "${OPTIONAL_VARS[@]}"; do
  if env_set "$v"; then
    pass "$v is set"
  else
    warn "$v is not set (feature degraded)"
  fi
done

APP_ENV="${APP_ENV:-production}"
if [[ "$APP_ENV" == "development" ]]; then
  pass "APP_ENV=development"
elif [[ "$APP_ENV" == "production" ]]; then
  warn "APP_ENV=production — CSRF origin checks are STRICT. Set APP_ENV=development for Replit dev."
fi


# =============================================================================
# SECTION 3 — BACKEND REACHABILITY (Checklist #4, #6)
# =============================================================================
header "3. Backend Reachability"

http_all "$BASE_URL/health"
if [[ "$_STATUS" == "200" ]]; then
  pass "GET /health → 200 (${_TTFB}ms TTFB)"
  HEALTH_BODY="$_BODY"
  SVC=$(json_val "$HEALTH_BODY" "d.get('service','')")
  [[ -n "$SVC" ]] && pass "Service name: $SVC" || warn "/health missing 'service' field"
elif [[ "$_STATUS" == "000" ]]; then
  crit "Backend unreachable at $BASE_URL — connection refused" \
       "Start the Backend API workflow. Check it's running on port 8000."
  echo -e "\n${RED}Cannot continue — backend is down.${RESET}\n"; exit 2
else
  fail "GET /health → $_STATUS (expected 200)" \
       "Check backend logs for startup errors"
fi

# Legacy redirect check (Checklist #13 — health check mismatch)
http_all "$BASE_URL/api/health"
if [[ "$_STATUS" == "200" || "$_STATUS" == "301" || "$_STATUS" == "307" ]]; then
  pass "GET /api/health → $_STATUS (legacy redirect OK)"
else
  warn "GET /api/health → $_STATUS (legacy health redirect unexpected)"
fi

# API version header
APIVER=$(hdr_val "x-api-version")
[[ -n "$APIVER" ]] && pass "X-API-Version header present: $APIVER" || warn "X-API-Version header missing"


# =============================================================================
# SECTION 4 — HEALTH CHECK MISMATCH (Checklist #13)
# =============================================================================
header "4. Health Check Depth (Shallow vs Deep)"

http_all "$BASE_URL/health"
SHALLOW_STATUS=$(json_val "$_BODY" "d.get('status','')")

http_all "$BASE_URL/health/deep"
DEEP_BODY="$_BODY"
DEEP_STATUS=$(json_val "$DEEP_BODY" "d.get('status','')")

info "Shallow /health → status=$SHALLOW_STATUS"
info "Deep /health/deep → status=$DEEP_STATUS"

if [[ "$SHALLOW_STATUS" == "healthy" && "$DEEP_STATUS" == "healthy" ]]; then
  pass "Both shallow and deep health checks: healthy"
elif [[ "$SHALLOW_STATUS" == "healthy" && "$DEEP_STATUS" != "healthy" ]]; then
  warn "MISMATCH: /health=healthy but /health/deep=$DEEP_STATUS — a dependency is degraded"
  info "This is checklist issue #13: healthy health checks ≠ healthy application"
  # Print degraded services
  printf '%s' "$DEEP_BODY" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    checks = d.get('checks', {})
    for k, v in checks.items():
        status = v.get('status', '?')
        err = v.get('error', '')
        icon = '\u2713' if status == 'healthy' else ('\u25b3' if status == 'degraded' else '\u2717')
        print(f'    {icon} {k}: {status}' + (f' — {err}' if err else ''))
except Exception:
    pass
" 2>/dev/null || true
else
  warn "Shallow /health status=$SHALLOW_STATUS"
fi

# Individual dependency status
MONGO_DEEP=$(json_val "$DEEP_BODY" "d.get('checks',{}).get('mongodb',{}).get('status','')")
REDIS_DEEP=$(json_val "$DEEP_BODY" "d.get('checks',{}).get('redis',{}).get('status','')")
VERTEX_DEEP=$(json_val "$DEEP_BODY" "d.get('checks',{}).get('vertex_ai',{}).get('status','')")

[[ "$MONGO_DEEP" == "healthy" ]]  && pass "MongoDB: healthy" \
  || { [[ -n "$MONGO_DEEP" ]] && fail "MongoDB: $MONGO_DEEP" "Check MONGODB_URI secret. Verify Atlas IP allowlist includes 0.0.0.0/0 for Replit." \
                               || warn "MongoDB status unknown (deep health check missing)"; }

[[ "$REDIS_DEEP" == "healthy" ]]   && pass "Redis: healthy" \
  || warn "Redis: ${REDIS_DEEP:-unknown} — set UPSTASH_REDIS_REST_URL + TOKEN for rate limiting"

[[ "$VERTEX_DEEP" == "healthy" ]]  && pass "Vertex AI: healthy" \
  || warn "Vertex AI: ${VERTEX_DEEP:-unknown} — set GEMINI_API_KEY for AI features"


# =============================================================================
# SECTION 5 — DATABASE CONNECTIVITY (Checklist #7)
# =============================================================================
header "5. Database Connectivity"

http_all "$BASE_URL/api/v1/content/library-bundle?slim=1"
if [[ "$_STATUS" == "200" ]]; then
  LIB_BODY="$_BODY"
  BOARDS=$(json_val "$LIB_BODY" "len(d.get('boards',[]))")
  SUBJECTS=$(json_val "$LIB_BODY" "len(d.get('subjects',[]))")
  CLASSES=$(json_val "$LIB_BODY" "len(d.get('classes',[]))")

  pass "GET /api/v1/content/library-bundle → 200"
  info "boards=$BOARDS  subjects=$SUBJECTS  classes=$CLASSES"

  # Checklist #7: empty library = DB query failure even if 200
  if [[ "$BOARDS" -gt 0 && "$SUBJECTS" -gt 0 ]] 2>/dev/null; then
    pass "Library data is non-empty (boards=$BOARDS subjects=$SUBJECTS)"
  else
    fail "Library returned 200 but data is EMPTY (boards=$BOARDS subjects=$SUBJECTS)" \
         "This is checklist issue #7. DB is connected but collection may be empty or MONGODB_DB_NAME is wrong. Check MONGODB_DB_NAME env var."
  fi

  # Checklist #10: shape validation
  SHAPE_OK=$(printf '%s' "$LIB_BODY" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    required = {'boards', 'classes', 'subjects'}
    missing = required - d.keys()
    print('ok' if not missing else 'missing:' + ','.join(missing))
except Exception as e:
    print('error:' + str(e))
" 2>/dev/null)
  if [[ "$SHAPE_OK" == "ok" ]]; then
    pass "Library response shape has all expected keys (boards, classes, subjects)"
  else
    fail "Library response shape unexpected ($SHAPE_OK) — frontend may show '0 subjects'" \
         "API response must include: boards, classes, subjects. Check public_content.py /library-bundle endpoint."
  fi

else
  fail "GET /api/v1/content/library-bundle → $_STATUS" \
       "Check MongoDB connection and that DB has content. Run: curl $BASE_URL/api/v1/content/library-bundle"
fi


# =============================================================================
# SECTION 6 — API RESPONSE SHAPES (Checklist #10)
# =============================================================================
header "6. API Response Shapes"

# CMS posts
http_all "$BASE_URL/api/v1/content/cms/posts"
[[ "$_STATUS" == "200" ]] && pass "GET /api/v1/content/cms/posts → 200" \
  || warn "GET /api/v1/content/cms/posts → $_STATUS"

# Question papers
http_all "$BASE_URL/api/v1/content/question-papers"
[[ "$_STATUS" == "200" ]] && pass "GET /api/v1/content/question-papers → 200" \
  || warn "GET /api/v1/content/question-papers → $_STATUS"

# Subscription plans
http_all "$BASE_URL/api/v1/subscription/plans"
[[ "$_STATUS" == "200" ]] && pass "GET /api/v1/subscription/plans → 200" \
  || warn "GET /api/v1/subscription/plans → $_STATUS"

# Config endpoint
http_all "$BASE_URL/api/v1/config/trustpilot"
[[ "$_STATUS" == "200" || "$_STATUS" == "404" ]] && pass "GET /api/v1/config/trustpilot → $_STATUS (OK)" \
  || warn "GET /api/v1/config/trustpilot → $_STATUS"

# Changelog
http_all "$BASE_URL/api/v1/changelog"
[[ "$_STATUS" == "200" ]] && pass "GET /api/v1/changelog → 200" \
  || warn "GET /api/v1/changelog → $_STATUS"


# =============================================================================
# SECTION 7 — AUTHENTICATION (Checklist #1, #15)
# =============================================================================
header "7. Authentication & JWT"

# Bad credentials → must be 401/422, never 500
http_all -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"audit-probe@invalid.test","password":"BadPass1"}'
if [[ "$_STATUS" == "401" || "$_STATUS" == "422" || "$_STATUS" == "400" ]]; then
  pass "POST /api/v1/auth/login bad creds → $_STATUS (not 5xx)"
else
  fail "POST /api/v1/auth/login → $_STATUS (expected 401/422)" \
       "Login endpoint should return 401 for bad credentials, not 5xx. Check auth.py."
fi

# Registration endpoint reachable
http_all -X POST "$BASE_URL/api/v1/auth/signup" \
  -H "Content-Type: application/json" \
  -d '{"email":"","password":""}'
[[ "$_STATUS" == "422" || "$_STATUS" == "400" ]] && pass "POST /api/v1/auth/signup validation works → $_STATUS" \
  || warn "POST /api/v1/auth/signup → $_STATUS (expected 422 for empty body)"

# GET /users/me without token → 401 (not 500)
http_all "$BASE_URL/api/v1/users/me"
if [[ "$_STATUS" == "401" || "$_STATUS" == "403" ]]; then
  pass "GET /api/v1/users/me without token → $_STATUS (correct)"
else
  fail "GET /api/v1/users/me without token → $_STATUS (expected 401)" \
       "Authentication middleware not working. Check JWT dependencies.py."
fi

# Invalid JWT token → 401
FAKE_JWT="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmYWtlIiwiZXhwIjoxfQ.invalidsig"
http_all "$BASE_URL/api/v1/users/me" -H "Authorization: Bearer $FAKE_JWT"
if [[ "$_STATUS" == "401" || "$_STATUS" == "403" ]]; then
  pass "GET /users/me with invalid JWT → $_STATUS (token rejected correctly)"
else
  fail "GET /users/me with invalid JWT → $_STATUS (expected 401)" \
       "JWT validation not working. Check JWT_SECRET and jwt library configuration."
fi

# Optional: admin login test
if [[ -n "$ADMIN_EMAIL" && -n "$ADMIN_PASSWORD" ]]; then
  http_all -X POST "$BASE_URL/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}"
  if [[ "$_STATUS" == "200" ]]; then
    AUTH_TOKEN=$(json_val "$_BODY" "d.get('access_token','')")
    [[ -n "$AUTH_TOKEN" ]] && pass "Admin login OK — JWT token received" \
                           || fail "Admin login 200 but no access_token in response"
  else
    fail "Admin login → $_STATUS (expected 200)" \
         "Check ADMIN_EMAIL / ADMIN_PASSWORD and that admin user exists in DB."
  fi
else
  skip "Admin login test (set ADMIN_EMAIL + ADMIN_PASSWORD to enable)"
fi


# =============================================================================
# SECTION 8 — CORS (Checklist #5)
# =============================================================================
header "8. CORS"

ALLOWED_ORIGIN="https://syrabit.ai"
BLOCKED_ORIGIN="https://evil-attacker.com"

# Preflight from allowed origin
_HEADERS=$(curl -sI --max-time 12 -X OPTIONS "$BASE_URL/api/v1/auth/login" \
  -H "Origin: $ALLOWED_ORIGIN" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type,Authorization" 2>/dev/null)

ACA_ORIGIN=$(echo "$_HEADERS" | grep -i "access-control-allow-origin" | head -1 | tr -d '\r\n')
ACA_METHODS=$(echo "$_HEADERS" | grep -i "access-control-allow-methods" | head -1 | tr -d '\r\n')
ACA_CREDS=$(echo "$_HEADERS" | grep -i "access-control-allow-credentials" | head -1 | tr -d '\r\n')

if echo "$ACA_ORIGIN" | grep -q "syrabit.ai"; then
  pass "CORS allows syrabit.ai origin"
  info "$ACA_ORIGIN"
else
  fail "CORS does not allow $ALLOWED_ORIGIN" \
       "Add https://syrabit.ai to ALLOWED_ORIGINS in config.py or set the ALLOWED_ORIGINS env var."
fi

[[ -n "$ACA_METHODS" ]] && pass "CORS allow-methods header present" \
  || warn "CORS allow-methods header missing"

echo "$ACA_CREDS" | grep -qi "true" && pass "CORS allow-credentials: true" \
  || warn "CORS allow-credentials not set (cookies/auth headers won't work cross-origin)"

# Replit dev domain CORS (development mode only)
REPLIT_DEV_DOMAIN="${REPLIT_DEV_DOMAIN:-}"
if [[ -n "$REPLIT_DEV_DOMAIN" ]]; then
  REPLIT_ORIGIN="https://$REPLIT_DEV_DOMAIN"
  _HEADERS=$(curl -sI --max-time 12 -X OPTIONS "$BASE_URL/api/v1/auth/login" \
    -H "Origin: $REPLIT_ORIGIN" \
    -H "Access-Control-Request-Method: POST" 2>/dev/null)
  RD_ORIGIN=$(echo "$_HEADERS" | grep -i "access-control-allow-origin" | head -1)
  if echo "$RD_ORIGIN" | grep -qi "replit"; then
    pass "CORS allows Replit dev domain ($REPLIT_DEV_DOMAIN)"
  else
    warn "CORS may not allow Replit dev domain — set APP_ENV=development in config"
    info "Expected: is_origin_allowed() to match *.sisko.replit.dev"
  fi
else
  skip "Replit dev domain CORS (REPLIT_DEV_DOMAIN not in env)"
fi


# =============================================================================
# SECTION 9 — SECURITY HEADERS (Checklist #12)
# =============================================================================
header "9. Security Headers"

http_all "$BASE_URL/health"

has_header "x-content-type-options"    && pass "X-Content-Type-Options present" \
  || fail "X-Content-Type-Options missing" "Add to unified_middleware in main.py"
has_header "x-frame-options"           && pass "X-Frame-Options present" \
  || fail "X-Frame-Options missing" "Add X-Frame-Options: DENY in unified_middleware"
has_header "strict-transport-security" && pass "HSTS (Strict-Transport-Security) present" \
  || warn "HSTS missing — acceptable in dev, required in prod"
has_header "x-request-id"             && pass "X-Request-ID header present (tracing OK)" \
  || warn "X-Request-ID missing — request tracing not working"
has_header "x-api-version"            && pass "X-API-Version header present" \
  || warn "X-API-Version missing"

# Verify no server software disclosure on API
SERVER_HDR=$(hdr_val "server")
if [[ -z "$SERVER_HDR" || "$SERVER_HDR" == "cloudflare" ]]; then
  pass "Server header safe (${SERVER_HDR:-not exposed})"
elif [[ "$SERVER_HDR" == "uvicorn" ]]; then
  warn "Server: uvicorn exposed — acceptable in dev, mask via proxy in prod"
else
  warn "Server header discloses: $SERVER_HDR"
fi


# =============================================================================
# SECTION 10 — CACHE HEADERS (Checklist #9)
# =============================================================================
header "10. Cache Headers"

http_all "$BASE_URL/api/v1/content/library-bundle"
CC=$(hdr_val "cache-control")
if [[ -n "$CC" ]]; then
  pass "Cache-Control header present: $CC"
  # Warn if cache is too long (could serve stale empty data per checklist #9)
  if echo "$CC" | grep -Eqi "max-age=[0-9]{6,}|public.*max-age=[0-9]{5,}"; then
    warn "Cache-Control max-age may be too long — stale empty responses could persist"
    info "Checklist #9: purge Cloudflare cache if content is empty after backend fix"
  fi
else
  warn "No Cache-Control header on library-bundle — browser/CDN may cache aggressively"
fi

# Health endpoint should not be cached
http_all "$BASE_URL/health"
CC_HEALTH=$(hdr_val "cache-control")
if echo "$CC_HEALTH" | grep -qi "no-store\|no-cache\|private"; then
  pass "Health endpoint not publicly cached ($CC_HEALTH)"
else
  warn "Health endpoint cache-control: '${CC_HEALTH:-not set}' — monitors may get stale status"
fi


# =============================================================================
# SECTION 11 — RATE LIMITING (Checklist #11)
# =============================================================================
header "11. Rate Limiting"

# Check if rate limit headers are exposed
http_all "$BASE_URL/api/v1/content/library-bundle"
has_header "x-ratelimit-limit"     && pass "X-RateLimit-Limit header exposed (rate limiting active)" \
  || { REDIS_DEEP="${REDIS_DEEP:-}"; [[ "$REDIS_DEEP" == "healthy" ]] && warn "Rate limit headers missing despite Redis being healthy" \
    || warn "Rate limit headers absent — expected (Redis/Upstash not configured)"; }
has_header "x-ratelimit-remaining" && pass "X-RateLimit-Remaining header present" \
  || skip "X-RateLimit-Remaining not present (Redis not configured)"

# Rapid-fire burst test (10 requests) — only for localhost to avoid self-DoS
if [[ "$BASE_URL" == *"localhost"* || "$BASE_URL" == *"127.0.0.1"* ]]; then
  BLOCKED=0
  for i in $(seq 1 10); do
    S=$(http_status "$BASE_URL/api/v1/content/library-bundle")
    [[ "$S" == "429" ]] && BLOCKED=$((BLOCKED+1))
  done
  if [[ $BLOCKED -gt 0 ]]; then
    pass "Rate limiter triggered after burst ($BLOCKED/10 requests blocked)"
  else
    info "10-request burst: no 429s (Redis rate limiter not active in dev — expected)"
  fi
else
  skip "Burst rate limit test (skipped for non-localhost targets)"
fi


# =============================================================================
# SECTION 12 — FRONTEND PROXY / BACKEND_URL WIRING (Checklist #4, #14)
# =============================================================================
header "12. Frontend Proxy & BACKEND_URL"

# Frontend reachable
http_all "$FRONTEND_URL"
if [[ "$_STATUS" == "200" || "$_STATUS" == "304" ]]; then
  pass "Frontend loads → $_STATUS (${_TTFB}ms)"
elif [[ "$_STATUS" == "000" ]]; then
  crit "Frontend unreachable at $FRONTEND_URL" \
       "Start the 'Start application' workflow. Ensure pnpm dev runs on port 5000."
else
  warn "Frontend → $_STATUS (expected 200)"
fi

# Proxy test: frontend /api/* → backend
http_all "$FRONTEND_URL/api/v1/health"
if [[ "$_STATUS" == "200" ]]; then
  PROXY_SVC=$(json_val "$_BODY" "d.get('service','')")
  pass "Frontend proxy → backend /health works (service=$PROXY_SVC)"
elif [[ "$_STATUS" == "000" ]]; then
  fail "Frontend proxy → backend: connection refused" \
       "Backend is not running. Start 'Backend API' workflow."
else
  fail "Frontend proxy → backend returned $_STATUS" \
       "Check Vite proxy config in vite.config.js line ~645. BACKEND_TARGET must resolve to http://localhost:8000."
fi

# Check Vite proxy BACKEND_TARGET variable
if [[ -f "apps/frontend/vite.config.js" ]]; then
  VITE_TARGET=$(grep "BACKEND_TARGET\s*=" apps/frontend/vite.config.js | head -1 || echo "")
  if echo "$VITE_TARGET" | grep -q "localhost:8000\|BACKEND_URL\|BACKEND_PROXY_URL"; then
    pass "vite.config.js BACKEND_TARGET references localhost:8000 or env var"
    info "$VITE_TARGET"
  else
    warn "vite.config.js BACKEND_TARGET may be misconfigured"
    info "$VITE_TARGET"
  fi
else
  skip "vite.config.js not found (not in apps/frontend/)"
fi

# Library bundle via proxy (end-to-end frontend→proxy→backend→DB)
http_all "$FRONTEND_URL/api/v1/content/library-bundle?slim=1"
if [[ "$_STATUS" == "200" ]]; then
  E2E_BOARDS=$(json_val "$_BODY" "len(d.get('boards',[]))")
  E2E_SUBJ=$(json_val "$_BODY" "len(d.get('subjects',[]))")
  if [[ "$E2E_BOARDS" -gt 0 ]] 2>/dev/null; then
    pass "End-to-end: Frontend→Proxy→Backend→DB works (boards=$E2E_BOARDS subjects=$E2E_SUBJ)"
  else
    fail "End-to-end: library-bundle returns 200 but empty data via proxy" \
         "Data is available direct to backend but empty via proxy. Check Vite proxy config and CORS."
  fi
else
  fail "End-to-end: library-bundle via frontend proxy → $_STATUS" \
       "Proxy is not forwarding requests correctly. Check BACKEND_TARGET in vite.config.js"
fi


# =============================================================================
# SECTION 13 — TLS / SSL (Checklist #3)
# =============================================================================
header "13. TLS / SSL"

if [[ "$BASE_URL" == https://* ]]; then
  # Certificate validity
  DOMAIN=$(echo "$BASE_URL" | sed 's|https://||' | cut -d/ -f1)
  CERT_INFO=$(echo | openssl s_client -connect "$DOMAIN:443" -servername "$DOMAIN" 2>/dev/null | \
              openssl x509 -noout -dates 2>/dev/null || echo "")
  if [[ -n "$CERT_INFO" ]]; then
    NOT_AFTER=$(echo "$CERT_INFO" | grep notAfter | cut -d= -f2)
    EXPIRY_EPOCH=$(date -d "$NOT_AFTER" +%s 2>/dev/null || date -j -f "%b %d %T %Y %Z" "$NOT_AFTER" +%s 2>/dev/null || echo "0")
    NOW_EPOCH=$(date +%s)
    DAYS_LEFT=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))
    if [[ $DAYS_LEFT -gt 30 ]]; then
      pass "TLS certificate valid — expires in $DAYS_LEFT days ($NOT_AFTER)"
    elif [[ $DAYS_LEFT -gt 0 ]]; then
      warn "TLS certificate expires SOON: $DAYS_LEFT days ($NOT_AFTER)"
    else
      fail "TLS certificate EXPIRED ($NOT_AFTER)" \
           "Renew the certificate immediately. Check Cloudflare SSL settings."
    fi
  else
    warn "Could not read TLS certificate (openssl unavailable or connection failed)"
  fi
else
  info "TLS check skipped — BASE_URL is HTTP (dev mode)"
  if [[ $PROD_MODE -eq 0 ]]; then
    pass "HTTP is fine for local development"
  else
    fail "Production URL should use HTTPS" "Set BASE_URL to https://..."
  fi
fi


# =============================================================================
# SECTION 14 — DNS (Checklist #2)
# =============================================================================
header "14. DNS Resolution"

if [[ $PROD_MODE -eq 1 ]]; then
  DOMAIN=$(echo "$BASE_URL" | sed 's|https\?://||' | cut -d/ -f1)
  if command -v dig &>/dev/null; then
    IPS=$(dig "$DOMAIN" +short 2>/dev/null | head -5)
    if [[ -n "$IPS" ]]; then
      pass "$DOMAIN resolves: $(echo $IPS | tr '\n' ' ')"
      # Cloudflare IPs start with 104.x / 172.x / 198.x
      if echo "$IPS" | grep -qE "^(104\.|172\.(6[4-9]|7[0-9]|8[0-9]|9[0-9]|1[0-2][0-9]|1[3-5][0-9]|160|16[0-9]|17[0-9]|18[0-9]|19[0-1])\.|198\.41\.)"; then
        pass "$DOMAIN points to Cloudflare IP (CDN active)"
      else
        warn "$DOMAIN may not be behind Cloudflare — IPs: $IPS"
      fi
    else
      fail "$DOMAIN DNS did not resolve" "Check DNS records in Cloudflare dashboard."
    fi

    # Frontend domain
    FRONT_DOMAIN="syrabit.ai"
    FRONT_IPS=$(dig "$FRONT_DOMAIN" +short 2>/dev/null | head -5)
    [[ -n "$FRONT_IPS" ]] && pass "$FRONT_DOMAIN DNS OK" || fail "$FRONT_DOMAIN DNS did not resolve"

  else
    skip "DNS check (dig not available)"
  fi
else
  skip "DNS check (run with --prod for production DNS tests)"
fi


# =============================================================================
# SECTION 15 — CHAT & AI ENDPOINTS (sanity)
# =============================================================================
header "15. AI / Chat Endpoints"

# Chat endpoint exists and rejects invalid input gracefully
http_all -X POST "$BASE_URL/api/v1/chat/stream" \
  -H "Content-Type: application/json" \
  -d '{}'
if [[ "$_STATUS" == "422" || "$_STATUS" == "401" || "$_STATUS" == "400" ]]; then
  pass "POST /api/v1/chat/stream reachable, validation works → $_STATUS"
else
  warn "POST /api/v1/chat/stream → $_STATUS (expected 422/401 for empty body)"
fi

# Analytics endpoint
http_all -X POST "$BASE_URL/api/v1/analytics/page-view" \
  -H "Content-Type: application/json" \
  -d '{"path":"/","session_id":"audit-test"}'
if [[ "$_STATUS" == "200" || "$_STATUS" == "204" || "$_STATUS" == "422" ]]; then
  pass "POST /api/v1/analytics/page-view → $_STATUS"
else
  warn "POST /api/v1/analytics/page-view → $_STATUS"
fi

# Edu endpoint (501 stub expected)
http_all "$BASE_URL/api/v1/quiz/generate"
if [[ "$_STATUS" == "501" || "$_STATUS" == "422" || "$_STATUS" == "401" || "$_STATUS" == "404" || "$_STATUS" == "405" ]]; then
  pass "GET /api/v1/quiz/generate → $_STATUS (expected non-500)"
else
  warn "GET /api/v1/quiz/generate → $_STATUS"
fi


# =============================================================================
# SECTION 16 — PRODUCTION LAYER COMPARISON (Checklist #4 — 3-layer test)
# =============================================================================
header "16. Layer Comparison"

if [[ $PROD_MODE -eq 0 ]]; then
  echo ""
  echo -e "  ${DIM}Run with --prod to compare direct-backend vs Worker vs frontend responses${RESET}"
  echo    "  Comparing: direct backend vs frontend proxy"

  DIRECT=$(http_status "$BASE_URL/api/v1/content/library-bundle")
  PROXY=$(http_status "$FRONTEND_URL/api/v1/content/library-bundle")

  info "Direct backend (:8000) → $DIRECT"
  info "Via Vite proxy  (:5000) → $PROXY"

  if [[ "$DIRECT" == "200" && "$PROXY" == "200" ]]; then
    pass "Direct backend and proxy both return 200"
  elif [[ "$DIRECT" == "200" && "$PROXY" != "200" ]]; then
    fail "Direct backend=200 but proxy=$PROXY — proxy is broken" \
         "Vite proxy is not forwarding requests. Check BACKEND_TARGET in vite.config.js"
  elif [[ "$DIRECT" != "200" ]]; then
    fail "Direct backend=$DIRECT — database or backend issue" \
         "Fix the backend first, then check the proxy"
  fi
else
  # 3-layer test: Direct Cloud Run → via api.syrabit.ai Worker → via syrabit.ai frontend
  DIRECT_GCR="${DIRECT_GCR:-}"
  if [[ -n "$DIRECT_GCR" ]]; then
    D_STATUS=$(http_status "$DIRECT_GCR/health")
    info "Direct Cloud Run ($DIRECT_GCR) → $D_STATUS"
    [[ "$D_STATUS" == "200" ]] && pass "Direct Cloud Run reachable" \
      || warn "Direct Cloud Run → $D_STATUS (may need IAM token)"
  else
    skip "Direct Cloud Run test (set DIRECT_GCR=https://your-service.run.app)"
  fi

  WORKER_STATUS=$(http_status "$PROD_URL/api/v1/content/library-bundle")
  FRONT_STATUS=$(http_status "https://syrabit.ai/api/v1/content/library-bundle")

  info "Via Worker ($PROD_URL) → $WORKER_STATUS"
  info "Via Frontend (syrabit.ai) → $FRONT_STATUS"

  [[ "$WORKER_STATUS" == "200" ]] && pass "Worker layer: library-bundle 200" \
    || fail "Worker layer: library-bundle → $WORKER_STATUS" \
            "Checklist #4: Worker BACKEND_URL may point to wrong Cloud Run service"
  [[ "$FRONT_STATUS" == "200" ]] && pass "Frontend layer: library-bundle 200" \
    || fail "Frontend layer: library-bundle → $FRONT_STATUS"

  if [[ "$WORKER_STATUS" != "$FRONT_STATUS" ]]; then
    warn "Layer mismatch: Worker=$WORKER_STATUS Frontend=$FRONT_STATUS — routing inconsistency"
  fi
fi


# =============================================================================
# SUMMARY
# =============================================================================
TOTAL=$((PASS + FAIL + WARN + SKIP))
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}  Audit Complete — $TOTAL checks${RESET}"
echo -e "  ${GREEN}✓ Pass   : $PASS${RESET}"
if [[ $FAIL -gt 0 ]]; then
  echo -e "  ${RED}✗ Fail   : $FAIL${RESET}"
else
  echo -e "  ✗ Fail   : $FAIL"
fi
if [[ $WARN -gt 0 ]]; then
  echo -e "  ${YELLOW}△ Warn   : $WARN${RESET}"
else
  echo -e "  △ Warn   : $WARN"
fi
echo -e "  ${BLUE}– Skip   : $SKIP${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

if [[ ${#FAILURES[@]} -gt 0 ]]; then
  echo -e "\n${RED}${BOLD}Failures:${RESET}"
  for f in "${FAILURES[@]}"; do
    echo -e "  ${RED}✗${RESET} $f"
  done
fi

if [[ $SHOW_FIX_HINTS -eq 1 && ${#HINTS[@]} -gt 0 ]]; then
  echo -e "\n${CYAN}${BOLD}Remediation Hints:${RESET}"
  for h in "${HINTS[@]}"; do
    echo -e "  $h"
    echo ""
  done
fi

if [[ $FAIL -eq 0 && $WARN -eq 0 ]]; then
  echo -e "\n${GREEN}${BOLD}ALL CHECKS PASSED${RESET}\n"
elif [[ $FAIL -eq 0 ]]; then
  echo -e "\n${YELLOW}Passed with warnings — review △ items above${RESET}\n"
  [[ $SHOW_FIX_HINTS -eq 0 ]] && echo -e "  Tip: run with ${BOLD}--fix-hints${RESET} to see remediation steps\n"
else
  echo -e "\n${RED}${BOLD}AUDIT FAILED — $FAIL check(s) need attention${RESET}"
  [[ $SHOW_FIX_HINTS -eq 0 ]] && echo -e "  Tip: run with ${BOLD}--fix-hints${RESET} to see remediation steps\n"
  [[ $CRITICAL -gt 0 ]] && exit 2 || exit 1
fi
