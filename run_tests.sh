#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PASS=0
FAIL=0
ERRORS=()
LIVE=false
PERF=false

# ── Parse flags ───────────────────────────────────────────────────────────────
for arg in "$@"; do
  case $arg in
    --live) LIVE=true ;;
    --perf) PERF=true ;;
  esac
done

header() { echo ""; echo "════════════════════════════════════════════════════"; echo "  $1"; echo "════════════════════════════════════════════════════"; }
ok()     { echo "  ✅  $1"; ((PASS++)) || true; }
fail()   { echo "  ❌  $1"; ((FAIL++)) || true; ERRORS+=("$1"); }

check() {
  # check <label> <expected_codes> <curl_args...>
  local label="$1" expected="$2"; shift 2
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$@" 2>/dev/null)
  if echo "$expected" | grep -qw "$code"; then
    ok "$label → HTTP $code"
  else
    fail "$label → HTTP $code  (expected $expected)"
  fi
}

check_contains() {
  # check_contains <label> <substring> <url>
  local label="$1" needle="$2" url="$3"
  local body
  body=$(curl -s --max-time 10 "$url" 2>/dev/null)
  if echo "$body" | grep -q "$needle"; then
    ok "$label"
  else
    fail "$label  (pattern '$needle' not found)"
  fi
}

check_header() {
  # check_header <label> <header_pattern> <url>
  local label="$1" pattern="$2" url="$3"
  local headers
  headers=$(curl -s -I --max-time 10 "$url" 2>/dev/null)
  if echo "$headers" | grep -qi "$pattern"; then
    ok "$label"
  else
    fail "$label  (header '$pattern' missing)"
  fi
}

# ── Performance thresholds (milliseconds) ─────────────────────────────────────
# TTFB = time_starttransfer in curl: time from request sent to first response byte.
# Thresholds reflect expected CDN-cached or lightly-computed responses in production.
PERF_THRESHOLD_HOMEPAGE=800       # / served from Cloudflare edge cache
PERF_THRESHOLD_LIBRARY_BUNDLE=500 # /api/v1/content/library-bundle?slim=1 (cached)
PERF_THRESHOLD_HEALTH=200         # /health  (no DB query)
PERF_THRESHOLD_PLANS=300          # /api/v1/subscription/plans (tiny, cached)
PERF_THRESHOLD_CHAT_STREAM=1500   # /api/v1/chat/stream 401 TTFB (edge→backend RTT)

perf_check() {
  # perf_check <label> <threshold_ms> <curl_args...>
  # Measures TTFB (time_starttransfer) and fails if it exceeds the threshold.
  local label="$1" threshold_ms="$2"; shift 2
  local ttfb_s ttfb_ms
  ttfb_s=$(curl -s -o /dev/null -w "%{time_starttransfer}" --max-time 15 "$@" 2>/dev/null)
  # Convert fractional seconds → integer milliseconds (awk handles float arithmetic)
  ttfb_ms=$(awk "BEGIN { printf \"%d\", $ttfb_s * 1000 }" 2>/dev/null)
  # Guard: if curl failed or returned empty, treat as timeout
  if [ -z "$ttfb_ms" ] || [ "$ttfb_ms" -le 0 ] 2>/dev/null; then
    fail "$label → no response / curl error  (threshold ${threshold_ms}ms)"
    return
  fi
  if [ "$ttfb_ms" -le "$threshold_ms" ]; then
    ok "$label → TTFB ${ttfb_ms}ms  (≤${threshold_ms}ms)"
  else
    fail "$label → TTFB ${ttfb_ms}ms  (exceeded ${threshold_ms}ms threshold)"
  fi
}

# ── 1. BACKEND — pytest ───────────────────────────────────────────────────────
header "1/3  BACKEND  (pytest — 25 test files)"
cd "$ROOT/apps/backend"

echo "  → Installing all backend requirements..."
pip install -r requirements.txt --quiet --disable-pip-version-check 2>&1 | tail -3
export PATH="$HOME/.local/bin:$PATH"

echo "  → Running pytest..."
if python3 -m pytest tests/ --tb=short -q 2>&1; then
  ok "Backend pytest suite"
else
  fail "Backend pytest suite (see errors above)"
fi

# ── 2. EDGE WORKER — vitest ───────────────────────────────────────────────────
header "2/3  EDGE WORKER  (vitest — 11 test files)"
cd "$ROOT/apps/edge"

echo "  → Installing typescript..."
npm install typescript --save-dev --quiet 2>&1 | tail -2

echo "  → Running vitest..."
if npx vitest run --reporter=verbose 2>&1; then
  ok "Edge worker vitest suite"
else
  fail "Edge worker vitest suite (see errors above)"
fi

# ── 3. FRONTEND — vitest ──────────────────────────────────────────────────────
header "3/3  FRONTEND  (vitest)"

if ! command -v pnpm &>/dev/null; then
  echo "  → Installing pnpm..."
  npm install -g pnpm --quiet 2>&1 | tail -2
fi
export PATH="$HOME/.local/bin:$(npm root -g)/.bin:$PATH"

cd "$ROOT"
echo "  → Installing all workspace dependencies..."
pnpm install --silent 2>&1 | tail -3

cd "$ROOT/apps/frontend"
echo "  → Running vitest..."
if pnpm vitest run --reporter=verbose 2>&1; then
  ok "Frontend vitest suite"
else
  fail "Frontend vitest suite (see errors above)"
fi

# ── 4. LIVE HTTP CHECKS (--live only) ────────────────────────────────────────
if [ "$LIVE" = true ]; then

  FE="https://syrabit.ai"
  API="https://api.syrabit.ai"
  ORIGIN="-H 'Origin: https://syrabit.ai'"

  # ── 4a. Frontend routes ────────────────────────────────────────────────────
  header "4a/7  FRONTEND ROUTES  ($FE)"
  check "/ — homepage"                        "200"     -L "$FE/"
  check "/library/ — SPA route"              "200"     -L "$FE/library/"
  check "/chat/ — SPA route"                 "200"     -L "$FE/chat/"
  check "/profile/ — SPA route"              "200"     -L "$FE/profile/"
  check "/robots.txt"                         "200"     "$FE/robots.txt"
  check "/sitemap.xml"                        "200"     "$FE/sitemap.xml"

  # ── 4b. Security headers ───────────────────────────────────────────────────
  header "4b/7  SECURITY HEADERS  ($FE)"
  check_header "HSTS header present"          "strict-transport-security"        "$FE/"
  check_header "X-Frame-Options: DENY"        "x-frame-options: DENY"            "$FE/"
  check_header "X-Content-Type-Options"       "x-content-type-options: nosniff"  "$FE/"
  check_header "Content-Security-Policy"      "content-security-policy"          "$FE/"
  check_header "Referrer-Policy"              "referrer-policy"                  "$FE/"

  # ── 4c. Edge health ────────────────────────────────────────────────────────
  header "4c/7  EDGE + BACKEND HEALTH  ($API)"
  check          "GET /health → 200"          "200"     "$API/health"
  check_contains "backend_reachable: true"    'backend_reachable.*true'    "$API/health"
  check          "GET /health/deep → not 500" "200 503" "$API/health/deep"

  # ── 4d. Content API ────────────────────────────────────────────────────────
  header "4d/7  CONTENT API"
  check_contains "library-bundle → boards"     '"boards"'    "$API/api/v1/content/library-bundle?slim=1"
  check_contains "library-bundle → subjects"   '"subjects"'  "$API/api/v1/content/library-bundle?slim=1"
  check_contains "subscription plans → free"   '"free"'      "$API/api/v1/subscription/plans"
  check_contains "subscription plans → pro"    '"pro"'       "$API/api/v1/subscription/plans"
  check          "sitemap.xml via API → 200"   "200"         "$API/api/v1/seo/sitemap.xml"

  # ── 4e. Auth endpoints ─────────────────────────────────────────────────────
  header "4e/7  AUTH ENDPOINTS"
  check "POST /auth/login (bad creds) → 401"      "401" \
    -X POST -H "Content-Type: application/json" -H "Origin: https://syrabit.ai" \
    -d '{"email":"audit@test.com","password":"wrongpass"}' \
    "$API/api/v1/auth/login"
  check "POST /auth/signup (empty body) → 422"    "422" \
    -X POST -H "Content-Type: application/json" -H "Origin: https://syrabit.ai" \
    -d '{}' "$API/api/v1/auth/signup"
  check "GET /user/me (no token) → 401"            "401" \
    -H "Origin: https://syrabit.ai" "$API/api/v1/user/me"
  check "GET /conversations (no token) → 401"      "401" \
    -H "Origin: https://syrabit.ai" "$API/api/v1/conversations"

  # ── 4f. Chat CORS preflight ────────────────────────────────────────────────
  header "4f/7  CHAT CORS PREFLIGHT"
  check "OPTIONS /api/v1/chat/stream → 200"  "200" \
    -X OPTIONS \
    -H "Origin: https://syrabit.ai" \
    -H "Access-Control-Request-Method: POST" \
    -H "Access-Control-Request-Headers: Content-Type,Authorization" \
    "$API/api/v1/chat/stream"
  check_header "CORS: allow-origin = syrabit.ai" \
    "access-control-allow-origin: https://syrabit.ai" \
    "$API/api/v1/chat/stream"

  # ── 4g. Security — path blocking ──────────────────────────────────────────
  header "4g/7  SECURITY — SENSITIVE PATH BLOCKING  ($API)"
  check "/.env blocked → 404"           "404" "$API/.env"
  check "/.git/config blocked → 404"    "404" "$API/.git/config"
  check "/.htaccess blocked → 404"      "404" "$API/.htaccess"
  check "/wp-admin blocked → 404"       "404" "$API/wp-admin"
  check "/wp-login.php blocked → 404"   "404" "$API/wp-login.php"
  check "/phpinfo.php blocked → 404"    "404" "$API/phpinfo.php"
  check "/server-status blocked → 404"  "404" "$API/server-status"
  check "/xmlrpc.php blocked → 404"     "404" "$API/xmlrpc.php"
  check "/openapi.json not exposed"     "302 404" "$API/openapi.json"

fi

# ── 5. PERFORMANCE CHECKS (--perf only) ──────────────────────────────────────
# Measures TTFB (time_starttransfer) on critical production paths.
# Does NOT require --live; runs independently against syrabit.ai + api.syrabit.ai.
# Override any threshold at call-time:
#   PERF_THRESHOLD_HOMEPAGE=600 bash run_tests.sh --perf
if [ "$PERF" = true ]; then

  FE_PERF="https://syrabit.ai"
  API_PERF="https://api.syrabit.ai"

  # ── 5a. Homepage TTFB ──────────────────────────────────────────────────────
  # Measures time to first byte for the root HTML document served by Cloudflare.
  # Cloudflare should serve this from edge cache; >800 ms indicates a cache miss
  # or an edge routing issue.
  header "5a/4  PERF — HOMEPAGE TTFB  (threshold ${PERF_THRESHOLD_HOMEPAGE}ms)"
  perf_check \
    "GET /  (homepage, edge-cached HTML)" \
    "$PERF_THRESHOLD_HOMEPAGE" \
    -L -H "Accept: text/html" \
    "$FE_PERF/"

  # ── 5b. Library-bundle TTFB ────────────────────────────────────────────────
  # /api/v1/content/library-bundle?slim=1 is the single most latency-sensitive
  # API call: it blocks the /library page render and is preloaded on every page.
  # The backend caches this in Redis; >500 ms means the cache is cold or Redis
  # is missing, causing a full MongoDB aggregation on the critical path.
  header "5b/4  PERF — LIBRARY-BUNDLE TTFB  (threshold ${PERF_THRESHOLD_LIBRARY_BUNDLE}ms)"
  perf_check \
    "GET /api/v1/content/library-bundle?slim=1  (Redis-cached)" \
    "$PERF_THRESHOLD_LIBRARY_BUNDLE" \
    -H "Origin: https://syrabit.ai" \
    "$API_PERF/api/v1/content/library-bundle?slim=1"

  # ── 5c. Backend health + subscription plans ────────────────────────────────
  # /health has no DB I/O; >200 ms means the backend container itself is cold or
  # the edge-to-backend path is degraded.
  # /subscription/plans is tiny and should be served from memory/cache; >300 ms
  # is a regression signal.
  header "5c/4  PERF — BACKEND ENDPOINTS"
  perf_check \
    "GET /health  (no DB, memory only)" \
    "$PERF_THRESHOLD_HEALTH" \
    "$API_PERF/health"

  perf_check \
    "GET /api/v1/subscription/plans  (in-process cache)" \
    "$PERF_THRESHOLD_PLANS" \
    -H "Origin: https://syrabit.ai" \
    "$API_PERF/api/v1/subscription/plans"

  # ── 5d. Chat stream TTFB ───────────────────────────────────────────────────
  # Sends an unauthenticated POST to /api/v1/chat/stream and measures the TTFB
  # of the 401 rejection.  This exercises the full edge→backend path without
  # requiring a real user token or triggering an AI call.
  # >1500 ms means the edge proxy is slow to reach the backend or the backend
  # startup/auth middleware is sluggish.
  header "5d/4  PERF — CHAT STREAM TTFB  (threshold ${PERF_THRESHOLD_CHAT_STREAM}ms)"
  perf_check \
    "POST /api/v1/chat/stream  (unauthed → 401, measures edge→backend RTT)" \
    "$PERF_THRESHOLD_CHAT_STREAM" \
    -X POST \
    -H "Content-Type: application/json" \
    -H "Origin: https://syrabit.ai" \
    -d '{"message":"perf-probe","session_id":"perf-test"}' \
    "$API_PERF/api/v1/chat/stream"

fi

# ── SUMMARY ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════"
_mode="unit"
[ "$LIVE" = true ] && _mode="$_mode + live"
[ "$PERF" = true ] && _mode="$_mode + perf"
echo "  RESULTS ($_mode):  ✅ $PASS passed   ❌ $FAIL failed"
if [ "$LIVE" = false ] && [ "$PERF" = false ]; then
  echo "  Tips:"
  echo "    --live   also tests syrabit.ai + api.syrabit.ai HTTP checks"
  echo "    --perf   measures TTFB on homepage, library-bundle, health, chat/stream"
fi
echo "════════════════════════════════════════════════════"
if [ ${#ERRORS[@]} -gt 0 ]; then
  echo "  Failed checks:"
  for e in "${ERRORS[@]}"; do echo "    • $e"; done
  echo ""
  exit 1
fi
echo ""
