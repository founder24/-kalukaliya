#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PASS=0
FAIL=0
ERRORS=()
LIVE=false
PERF=false
PERF_BASELINE=false
PERF_COMPARE=false
# Baseline file path — override with PERF_BASELINE_FILE=path/to/file.json
PERF_BASELINE_FILE="${PERF_BASELINE_FILE:-${ROOT}/perf-baseline.json}"
# Max allowed TTFB increase over baseline before the check is a failure (percent)
PERF_REGRESSION_PCT="${PERF_REGRESSION_PCT:-20}"

# ── Parse flags ───────────────────────────────────────────────────────────────
for arg in "$@"; do
  case $arg in
    --live)           LIVE=true ;;
    --perf)           PERF=true ;;
    --perf-baseline)  PERF_BASELINE=true ;;
    --perf-compare)   PERF_COMPARE=true ;;
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
# Override any threshold at call-time: PERF_THRESHOLD_HOMEPAGE=600 bash run_tests.sh --perf
PERF_THRESHOLD_HOMEPAGE=800       # / served from Cloudflare edge cache
PERF_THRESHOLD_LIBRARY_BUNDLE=500 # /api/v1/content/library-bundle?slim=1 (cached)
PERF_THRESHOLD_HEALTH=200         # /health  (no DB query)
PERF_THRESHOLD_PLANS=300          # /api/v1/subscription/plans (tiny, cached)
PERF_THRESHOLD_CHAT_STREAM=1500   # /api/v1/chat/stream 401 TTFB (edge→backend RTT)

# Convert a curl time_starttransfer fractional-seconds string → integer ms
_ms() { awk "BEGIN { printf \"%d\", ${1:-0} * 1000 }" 2>/dev/null; }

# Guard used by both perf_check_val and perf_compare_val
_perf_guard() {
  local ms="$1"
  [ -n "$ms" ] && [ "$ms" -gt 0 ] 2>/dev/null
}

# Check a pre-measured TTFB (ms) against an absolute threshold
perf_check_val() {
  local label="$1" threshold_ms="$2" ttfb_ms="$3"
  if ! _perf_guard "$ttfb_ms"; then
    fail "$label → no response / curl error  (threshold ${threshold_ms}ms)"; return
  fi
  if [ "$ttfb_ms" -le "$threshold_ms" ]; then
    ok "$label → TTFB ${ttfb_ms}ms  (≤${threshold_ms}ms)"
  else
    fail "$label → TTFB ${ttfb_ms}ms  (exceeded ${threshold_ms}ms threshold)"
  fi
}

# Compare a pre-measured TTFB (ms) against a baseline value.
# Fails when current > baseline × (1 + PERF_REGRESSION_PCT/100).
perf_compare_val() {
  local label="$1" baseline_ms="$2" current_ms="$3"
  local pct="$PERF_REGRESSION_PCT"
  if ! _perf_guard "$current_ms"; then
    fail "$label → no response / curl error  (baseline ${baseline_ms}ms)"; return
  fi
  if [ "$baseline_ms" -le 0 ] 2>/dev/null; then
    fail "$label → baseline value is zero or missing — re-run --perf-baseline first"; return
  fi
  local allowed_ms delta_pct
  allowed_ms=$(awk "BEGIN { printf \"%d\", $baseline_ms * (1 + $pct / 100) }")
  delta_pct=$(awk "BEGIN { printf \"%+d\", ($current_ms - $baseline_ms) * 100 / $baseline_ms }")
  if [ "$current_ms" -le "$allowed_ms" ]; then
    ok "$label → ${current_ms}ms  (baseline ${baseline_ms}ms, ${delta_pct}%, budget ±${pct}%)"
  else
    fail "$label → ${current_ms}ms  (baseline ${baseline_ms}ms, ${delta_pct}% regression — limit ±${pct}%)"
  fi
}

# Run all five perf curl calls and populate TTFB_* variables.
# Called once when any of --perf / --perf-baseline / --perf-compare is active.
_measure_perf_endpoints() {
  local fe="https://syrabit.ai"
  local api="https://api.syrabit.ai"
  local _t

  echo "  → [1/5] homepage TTFB..."
  _t=$(curl -s -o /dev/null -w "%{time_starttransfer}" --max-time 15 \
        -L -H "Accept: text/html" "$fe/" 2>/dev/null)
  TTFB_HOMEPAGE=$(_ms "$_t")

  echo "  → [2/5] library-bundle TTFB..."
  _t=$(curl -s -o /dev/null -w "%{time_starttransfer}" --max-time 15 \
        -H "Origin: $fe" "$api/api/v1/content/library-bundle?slim=1" 2>/dev/null)
  TTFB_LIBRARY_BUNDLE=$(_ms "$_t")

  echo "  → [3/5] /health TTFB..."
  _t=$(curl -s -o /dev/null -w "%{time_starttransfer}" --max-time 15 \
        "$api/health" 2>/dev/null)
  TTFB_HEALTH=$(_ms "$_t")

  echo "  → [4/5] /subscription/plans TTFB..."
  _t=$(curl -s -o /dev/null -w "%{time_starttransfer}" --max-time 15 \
        -H "Origin: $fe" "$api/api/v1/subscription/plans" 2>/dev/null)
  TTFB_PLANS=$(_ms "$_t")

  echo "  → [5/5] chat/stream TTFB (unauthed → 401)..."
  _t=$(curl -s -o /dev/null -w "%{time_starttransfer}" --max-time 15 \
        -X POST -H "Content-Type: application/json" -H "Origin: $fe" \
        -d '{"message":"perf-probe","session_id":"perf-test"}' \
        "$api/api/v1/chat/stream" 2>/dev/null)
  TTFB_CHAT_STREAM=$(_ms "$_t")
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

# ── 5. PERFORMANCE CHECKS (--perf / --perf-baseline / --perf-compare) ────────
# All three flags hit syrabit.ai + api.syrabit.ai directly; --live is not required.
# Endpoints are measured ONCE per run even when multiple perf flags are combined.
#
# Env overrides:
#   PERF_THRESHOLD_HOMEPAGE=600   bash run_tests.sh --perf
#   PERF_BASELINE_FILE=ci/base.json  bash run_tests.sh --perf-compare
#   PERF_REGRESSION_PCT=10           bash run_tests.sh --perf-compare
if [ "$PERF" = true ] || [ "$PERF_BASELINE" = true ] || [ "$PERF_COMPARE" = true ]; then

  header "5  PERF — measuring 5 endpoints  (syrabit.ai + api.syrabit.ai)"
  _measure_perf_endpoints

  # ── 5a. Threshold checks (--perf) ──────────────────────────────────────────
  if [ "$PERF" = true ]; then
    header "5a  PERF — absolute TTFB thresholds"

    # Homepage — Cloudflare edge cache; miss or routing issue if > 800 ms
    perf_check_val \
      "GET /  (homepage, edge-cached HTML)" \
      "$PERF_THRESHOLD_HOMEPAGE" "$TTFB_HOMEPAGE"

    # library-bundle — Redis-cached; cold cache triggers full MongoDB aggregation
    perf_check_val \
      "GET /api/v1/content/library-bundle?slim=1  (Redis-cached)" \
      "$PERF_THRESHOLD_LIBRARY_BUNDLE" "$TTFB_LIBRARY_BUNDLE"

    # /health — no DB I/O; container cold-start or edge degradation if > 200 ms
    perf_check_val \
      "GET /health  (no DB, memory only)" \
      "$PERF_THRESHOLD_HEALTH" "$TTFB_HEALTH"

    # /subscription/plans — in-process; > 300 ms is a regression signal
    perf_check_val \
      "GET /api/v1/subscription/plans  (in-process cache)" \
      "$PERF_THRESHOLD_PLANS" "$TTFB_PLANS"

    # chat/stream — unauthed → 401; measures full edge→backend RTT without AI call
    perf_check_val \
      "POST /api/v1/chat/stream  (unauthed → 401, edge→backend RTT)" \
      "$PERF_THRESHOLD_CHAT_STREAM" "$TTFB_CHAT_STREAM"
  fi

  # ── 5b. Write baseline (--perf-baseline) ───────────────────────────────────
  if [ "$PERF_BASELINE" = true ]; then
    header "5b  PERF — writing baseline  →  $PERF_BASELINE_FILE"
    _git_sha=$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo "unknown")
    _ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || echo "unknown")
    # Write JSON without requiring jq (plain printf)
    printf '{\n  "created_at": "%s",\n  "git_sha": "%s",\n  "regression_pct_limit": %s,\n  "measurements": {\n    "homepage": %s,\n    "library_bundle": %s,\n    "health": %s,\n    "subscription_plans": %s,\n    "chat_stream": %s\n  }\n}\n' \
      "$_ts" "$_git_sha" "$PERF_REGRESSION_PCT" \
      "$TTFB_HOMEPAGE" "$TTFB_LIBRARY_BUNDLE" "$TTFB_HEALTH" \
      "$TTFB_PLANS" "$TTFB_CHAT_STREAM" \
      > "$PERF_BASELINE_FILE"
    if [ $? -eq 0 ]; then
      ok "Baseline written  →  $PERF_BASELINE_FILE  (sha: $_git_sha)"
      echo "     homepage:          ${TTFB_HOMEPAGE}ms"
      echo "     library_bundle:    ${TTFB_LIBRARY_BUNDLE}ms"
      echo "     health:            ${TTFB_HEALTH}ms"
      echo "     subscription_plans:${TTFB_PLANS}ms"
      echo "     chat_stream:       ${TTFB_CHAT_STREAM}ms"
    else
      fail "Could not write baseline to $PERF_BASELINE_FILE"
    fi
  fi

  # ── 5c. Compare against baseline (--perf-compare) ──────────────────────────
  if [ "$PERF_COMPARE" = true ]; then
    header "5c  PERF — regression check  (budget ±${PERF_REGRESSION_PCT}%)  →  $PERF_BASELINE_FILE"

    # Require the baseline file to exist
    if [ ! -f "$PERF_BASELINE_FILE" ]; then
      fail "Baseline file not found: $PERF_BASELINE_FILE — run --perf-baseline first"
    else
      # Read baseline values using python3 (always available; avoids jq dependency)
      _read_baseline() {
        python3 -c "
import json, sys
try:
    d = json.load(open('$PERF_BASELINE_FILE'))
    print(int(d['measurements']['$1']))
except Exception:
    print(0)
" 2>/dev/null
      }

      _b_homepage=$(_read_baseline homepage)
      _b_library=$(_read_baseline library_bundle)
      _b_health=$(_read_baseline health)
      _b_plans=$(_read_baseline subscription_plans)
      _b_chat=$(_read_baseline chat_stream)

      perf_compare_val "GET /  (homepage)"                            "$_b_homepage" "$TTFB_HOMEPAGE"
      perf_compare_val "GET /api/v1/content/library-bundle?slim=1"    "$_b_library"  "$TTFB_LIBRARY_BUNDLE"
      perf_compare_val "GET /health"                                  "$_b_health"   "$TTFB_HEALTH"
      perf_compare_val "GET /api/v1/subscription/plans"               "$_b_plans"    "$TTFB_PLANS"
      perf_compare_val "POST /api/v1/chat/stream  (unauthed → 401)"   "$_b_chat"     "$TTFB_CHAT_STREAM"
    fi
  fi

fi

# ── SUMMARY ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════"
_mode="unit"
[ "$LIVE"         = true ] && _mode="$_mode + live"
[ "$PERF"         = true ] && _mode="$_mode + perf"
[ "$PERF_BASELINE"= true ] && _mode="$_mode + perf-baseline"
[ "$PERF_COMPARE" = true ] && _mode="$_mode + perf-compare"
echo "  RESULTS ($_mode):  ✅ $PASS passed   ❌ $FAIL failed"
if [ "$LIVE" = false ] && [ "$PERF" = false ] && [ "$PERF_BASELINE" = false ] && [ "$PERF_COMPARE" = false ]; then
  echo "  Tips:"
  echo "    --live            HTTP checks against syrabit.ai + api.syrabit.ai"
  echo "    --perf            TTFB checks vs absolute thresholds"
  echo "    --perf-baseline   write current TTFBs to perf-baseline.json"
  echo "    --perf-compare    compare current TTFBs to saved baseline (default ±20%)"
fi
echo "════════════════════════════════════════════════════"
if [ ${#ERRORS[@]} -gt 0 ]; then
  echo "  Failed checks:"
  for e in "${ERRORS[@]}"; do echo "    • $e"; done
  echo ""
  exit 1
fi
echo ""
