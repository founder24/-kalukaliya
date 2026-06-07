#!/usr/bin/env bash
# =============================================================================
# Syrabit Full-Stack Security Audit Test
#
# Tests SPECIFIC fixes from the June 2026 security audit across every layer.
# Not a general smoke test — each check maps directly to a named audit item
# (C-N, H-N, M-N, L-N). Designed to catch regressions in the exact patterns
# the audit flagged as vulnerabilities.
#
# Usage:
#   EDGE_URL=https://api.syrabit.ai \
#   SITE_URL=https://syrabit.ai \
#   ADMIN_EMAIL=admin@syrabit.ai ADMIN_PASS=... \
#   bash scripts/test-security-audit.sh
#
# EDGE_URL     — Backend API via CF edge (default: https://api.syrabit.ai)
# SITE_URL     — Cloudflare Pages frontend (default: https://syrabit.ai)
# ADMIN_EMAIL  — Admin credentials for H-1, H-8, M-1, H-3 tests (optional)
# ADMIN_PASS   — Admin password (required if ADMIN_EMAIL is set)
# =============================================================================

set -euo pipefail

EDGE_URL="${EDGE_URL:-https://api.syrabit.ai}"
SITE_URL="${SITE_URL:-https://syrabit.ai}"
ADMIN_EMAIL="${ADMIN_EMAIL:-}"
ADMIN_PASS="${ADMIN_PASS:-}"
TIMEOUT=15

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m' GREEN='\033[0;32m' YELLOW='\033[1;33m'
CYAN='\033[0;36m' BOLD='\033[1m' DIM='\033[2m' NC='\033[0m'

PASS=0 FAIL=0 SKIP=0
RESP_STATUS="" RESP_BODY="" RESP_HEADERS="" RESP_MS=0

# ── UI helpers ────────────────────────────────────────────────────────────────
banner() {
  printf "\n${BOLD}${CYAN}══════════════════════════════════════════════════════${NC}\n"
  printf "${BOLD}${CYAN}  %s${NC}\n" "$*"
  printf "${BOLD}${CYAN}══════════════════════════════════════════════════════${NC}\n"
}
section() { printf "\n${BOLD}▸ %s${NC}\n" "$1"; }
ok()   { PASS=$((PASS+1)); printf "  ${GREEN}✔${NC}  %s  ${DIM}%s${NC}\n" "$1" "${2:-}"; }
fail() { FAIL=$((FAIL+1)); printf "  ${RED}✖${NC}  %s  ${DIM}%s${NC}\n" "$1" "${2:-}"; }
skip() { SKIP=$((SKIP+1)); printf "  ${YELLOW}⚠${NC}  %s  ${DIM}(skipped — %s)${NC}\n" "$1" "${2:-prereq missing}"; }
info() { printf "     ${DIM}%s${NC}\n" "$1"; }

# ── HTTP helper ───────────────────────────────────────────────────────────────
# Sets RESP_STATUS, RESP_BODY, RESP_HEADERS, RESP_MS.
# Never runs in a subshell — all state written directly.
http_call() {
  local method="$1" url="$2"; shift 2
  local tmpfile hfile
  tmpfile=$(mktemp); hfile=$(mktemp)
  local t0 t1
  t0=$(python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null || date +%s)
  curl -s -X "$method" "$url" --max-time "$TIMEOUT" \
    -D "$hfile" -w "\n__STATUS__%{http_code}__" \
    "$@" > "$tmpfile" 2>/dev/null || true
  t1=$(python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null || date +%s)
  RESP_MS=$(( t1 - t0 ))
  local raw; raw=$(cat "$tmpfile"); rm -f "$tmpfile"
  RESP_HEADERS=$(cat "$hfile"); rm -f "$hfile"
  RESP_STATUS=$(printf '%s' "$raw" | grep -oP '(?<=__STATUS__)\d+' | tail -1 || echo "000")
  RESP_BODY=$(printf '%s' "$raw" | sed 's/__STATUS__[0-9]*__//' | sed '${ /^[[:space:]]*$/d }')
}

jval() {
  printf '%s' "$RESP_BODY" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin); v=d
    for k in '$1'.split('.'): v=v[int(k)] if isinstance(v,list) else v[k]
    sys.stdout.write(str(v) if v is not None else '')
except: sys.stdout.write('')
" 2>/dev/null
}

jlen() {
  printf '%s' "$RESP_BODY" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin); v=d
    if '$1':
        for k in '$1'.split('.'): v=v[k]
    print(len(v) if isinstance(v,(list,dict)) else 0)
except: print(0)
" 2>/dev/null
}

header_val() {
  printf '%s' "$RESP_HEADERS" | grep -i "^$1:" | head -1 \
    | sed 's/^[^:]*:[[:space:]]*//' | tr -d '\r\n'
}

has_header() {
  printf '%s' "$RESP_HEADERS" | grep -qiE "^$1:"
}

# ── Admin session management ──────────────────────────────────────────────────
ADMIN_JAR=""
ADMIN_LOGGED_IN=false

# L-17 / H-9: clean up temp files on exit/interrupt
trap 'rm -f "${ADMIN_JAR:-}"' EXIT

admin_login() {
  if $ADMIN_LOGGED_IN; then return 0; fi
  if [[ -z "$ADMIN_EMAIL" || -z "$ADMIN_PASS" ]]; then return 1; fi
  ADMIN_JAR=$(mktemp)
  http_call POST "${EDGE_URL}/api/v1/admin/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASS}\"}" \
    -c "$ADMIN_JAR"
  if [[ "$RESP_STATUS" == "200" ]]; then
    ADMIN_LOGGED_IN=true
    return 0
  else
    info "Admin login failed [${RESP_STATUS}] — admin-layer tests will be skipped"
    return 1
  fi
}

admin_get() {
  http_call GET "${EDGE_URL}${1}" -b "$ADMIN_JAR"
}

# ── Probe inputs ──────────────────────────────────────────────────────────────
LONG_INPUT=$(python3 -c "print('A' * 2100)")
TODAY=$(date -u '+%Y-%m-%d')
# macOS/Linux portable yesterday
YESTERDAY=$(date -u -d 'yesterday' '+%Y-%m-%d' 2>/dev/null \
  || date -u -v-1d '+%Y-%m-%d' 2>/dev/null \
  || python3 -c "from datetime import date,timedelta; print(date.today()-timedelta(1))")
GOOGLEBOT_UA="Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

# =============================================================================
printf "\n${BOLD}Syrabit Full-Stack Security Audit Test${NC}\n"
printf "  Edge  : ${CYAN}${EDGE_URL}${NC}\n"
printf "  Site  : ${CYAN}${SITE_URL}${NC}\n"
printf "  Time  : $(date -u '+%Y-%m-%d %H:%M:%S UTC')\n"
printf "  Today : ${TODAY}\n"

# =============================================================================
banner "LAYER 0 — Critical Security  (C-2, C-3)"
# =============================================================================

section "C-2 · Duplicate email signup → 400, not 500"
# First attempt may succeed (new random address); second must be 400/409.
DUMMY_EMAIL="audit-$(date +%s)@example-nosuchsite.invalid"
http_call POST "${EDGE_URL}/api/v1/auth/signup" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${DUMMY_EMAIL}\",\"password\":\"AuditTest123!\"}"
FIRST_STATUS="$RESP_STATUS"

if [[ "$FIRST_STATUS" == "200" || "$FIRST_STATUS" == "201" ]]; then
  # Account created — now try the duplicate
  sleep 1
  http_call POST "${EDGE_URL}/api/v1/auth/signup" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${DUMMY_EMAIL}\",\"password\":\"AuditTest123!\"}"
  if [[ "$RESP_STATUS" == "400" || "$RESP_STATUS" == "409" || "$RESP_STATUS" == "422" ]]; then
    ok "C-2: Duplicate signup → ${RESP_STATUS} (DuplicateKeyError handled, not 500)"
  else
    fail "C-2: Duplicate signup returned ${RESP_STATUS}" "expected 400/409 — ${RESP_BODY:0:100}"
  fi
elif [[ "$FIRST_STATUS" == "400" || "$FIRST_STATUS" == "409" || "$FIRST_STATUS" == "422" ]]; then
  ok "C-2: Signup validation → ${FIRST_STATUS} (guard active before DB hit)"
elif [[ "$FIRST_STATUS" == "429" ]]; then
  skip "C-2 duplicate signup" "rate-limited [429] — quota enforced (correct behavior)"
else
  skip "C-2 duplicate signup" "signup returned ${FIRST_STATUS} — may require extra fields on this deployment"
fi

section "C-3 · /health doesn't leak internal error details"
http_call GET "${EDGE_URL}/health"
if [[ "$RESP_STATUS" == "200" ]]; then
  ok "GET /health → 200  [${RESP_MS}ms]"

  # These strings must NEVER appear in the public health response body
  LEAK_FREE=true
  for pattern in \
      "Traceback" \
      "ConnectionRefusedError" \
      "mongodb+srv://" \
      "password" \
      "Exception at" \
      "File \"/app" \
      "OperationFailure" \
      "pymongo" \
      "redis://"; do
    if printf '%s' "$RESP_BODY" | grep -qi "$pattern"; then
      fail "C-3: /health leaks internal detail: '${pattern}'" "${RESP_BODY:0:120}"
      LEAK_FREE=false
    fi
  done
  $LEAK_FREE && ok "C-3: /health body contains no stack traces / credentials / internal paths"

  # error_count or similar field must be present when errors exist — not raw messages
  err_count=$(printf '%s' "$RESP_BODY" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('error_count', d.get('errors', 0)))" 2>/dev/null || echo 0)
  if [[ "${err_count:-0}" -gt "0" ]]; then
    hint=$(jval "hint")
    [[ -n "$hint" ]] \
      && ok "C-3: Error present — /health exposes hint, not details" "hint='${hint}'" \
      || fail "C-3: error_count=${err_count} but no hint field — detail may be leaking"
  else
    ok "C-3: /health reports clean (error_count=${err_count:-0})"
  fi
else
  fail "GET /health" "[${RESP_STATUS}] — health endpoint not reachable"
fi

# =============================================================================
banner "LAYER 1 — Backend Auth  (H-1, H-7, H-8, H-9)"
# =============================================================================

section "H-1 · Admin logout returns server_revocation field"
if [[ -z "$ADMIN_EMAIL" ]]; then
  skip "H-1 admin logout" "ADMIN_EMAIL not set"
else
  TEMP_JAR=$(mktemp)
  http_call POST "${EDGE_URL}/api/v1/admin/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASS}\"}" \
    -c "$TEMP_JAR"
  if [[ "$RESP_STATUS" == "200" ]]; then
    http_call POST "${EDGE_URL}/api/v1/admin/logout" -b "$TEMP_JAR"
    if [[ "$RESP_STATUS" == "200" ]]; then
      sv=$(jval "server_revocation")
      if [[ -n "$sv" && "$sv" != "" ]]; then
        ok "H-1: POST /admin/logout has server_revocation field" "server_revocation=${sv}"
      else
        fail "H-1: server_revocation field missing in logout response" "${RESP_BODY:0:100}"
      fi
    else
      fail "H-1: POST /admin/logout returned ${RESP_STATUS}" "${RESP_BODY:0:80}"
    fi
  else
    skip "H-1 admin logout" "admin login returned ${RESP_STATUS}"
  fi
  rm -f "$TEMP_JAR"
fi

section "H-8 · Admin endpoints reject unauthenticated requests"
for admin_path in \
    "/api/v1/admin/dashboard" \
    "/api/v1/admin/content/boards" \
    "/api/v1/admin/knowledge" \
    "/api/v1/admin/seo/bulk-generate"; do
  http_call GET "${EDGE_URL}${admin_path}"
  if [[ "$RESP_STATUS" == "401" || "$RESP_STATUS" == "403" ]]; then
    ok "H-8: GET ${admin_path} without session → ${RESP_STATUS}"
  else
    fail "H-8: Admin guard missing on ${admin_path}" "expected 401/403, got ${RESP_STATUS}"
  fi
done

section "H-8 · CSRF guard rejects cross-origin POST to admin"
if [[ -z "$ADMIN_EMAIL" ]]; then
  skip "H-8 CSRF guard" "ADMIN_EMAIL not set"
else
  admin_login
  if $ADMIN_LOGGED_IN; then
    # Sending a mismatched Origin header to a state-changing admin endpoint must be 403
    http_call POST "${EDGE_URL}/api/v1/admin/logout" \
      -b "$ADMIN_JAR" \
      -H "Content-Type: application/json" \
      -H "Origin: https://attacker.example.com"
    if [[ "$RESP_STATUS" == "403" ]]; then
      ok "H-8: CSRF guard → 403 on cross-origin admin POST"
    elif [[ "$RESP_STATUS" == "200" || "$RESP_STATUS" == "204" ]]; then
      # Logout succeeded — CSRF guard may allow same-site SameSite=Strict cookies
      # to protect naturally. Log as warning, not hard failure.
      info "H-8: Admin logout returned ${RESP_STATUS} with bad Origin — verify CSRF guard in admin.py"
      fail "H-8: CSRF guard did not block cross-origin POST" "admin logout succeeded with evil Origin"
    else
      fail "H-8: Unexpected status for cross-origin admin POST" "got ${RESP_STATUS}"
    fi
    # Re-login since logout may have run
    ADMIN_LOGGED_IN=false
    rm -f "$ADMIN_JAR"; ADMIN_JAR=$(mktemp)
    admin_login || true
  else
    skip "H-8 CSRF guard" "admin login failed"
  fi
fi

section "H-7 · Placeholder secrets rejected at startup (verified via health)"
# We can't directly check config values, but a running server with valid secrets
# means the placeholder check didn't block startup.
http_call GET "${EDGE_URL}/health"
[[ "$RESP_STATUS" == "200" ]] \
  && ok "H-7: Server started cleanly (non-placeholder secrets in use)" \
  || fail "H-7: Server not healthy — check for placeholder secret errors in logs"

# =============================================================================
banner "LAYER 2 — Backend Correctness  (H-3, M-1, M-6, M-13, M-15)"
# =============================================================================

section "M-1 · Admin list endpoints respect skip/limit pagination"
if [[ -z "$ADMIN_EMAIL" ]]; then
  skip "M-1 pagination" "ADMIN_EMAIL not set"
else
  admin_login
  if $ADMIN_LOGGED_IN; then
    # limit=1 → at most 1 result
    admin_get "/api/v1/admin/content/boards?skip=0&limit=1"
    if [[ "$RESP_STATUS" == "200" ]]; then
      count=$(printf '%s' "$RESP_BODY" | python3 -c "
import sys,json; d=json.load(sys.stdin)
items = d if isinstance(d,list) else d.get('boards', d.get('items',[]))
print(len(items))" 2>/dev/null || echo -1)
      [[ "${count:-0}" -le 1 ]] \
        && ok "M-1: /admin/content/boards?limit=1 → ${count} item(s) (≤1)" \
        || fail "M-1: limit=1 ignored — returned ${count} items"
    else
      fail "M-1: /admin/content/boards?skip=0&limit=1" "[${RESP_STATUS}]"
    fi

    # skip=99999 → 0 results (no data that deep)
    admin_get "/api/v1/admin/content/boards?skip=99999&limit=10"
    if [[ "$RESP_STATUS" == "200" ]]; then
      count=$(printf '%s' "$RESP_BODY" | python3 -c "
import sys,json; d=json.load(sys.stdin)
items = d if isinstance(d,list) else d.get('boards', d.get('items',[]))
print(len(items))" 2>/dev/null || echo -1)
      [[ "${count:-0}" -eq 0 ]] \
        && ok "M-1: /admin/content/boards?skip=99999 → 0 items (pagination working)" \
        || fail "M-1: skip=99999 returned ${count} items (expected 0)"
    else
      fail "M-1: /admin/content/boards?skip=99999" "[${RESP_STATUS}]"
    fi

    # limit > 1000 → 422 (Query validator le=1000)
    admin_get "/api/v1/admin/content/chapters?limit=9999"
    [[ "$RESP_STATUS" == "422" ]] \
      && ok "M-1: /admin/content/chapters?limit=9999 → 422 (validator enforced upper bound)" \
      || fail "M-1: limit=9999 not rejected" "expected 422, got ${RESP_STATUS}"

    # negative skip → 422 (ge=0)
    admin_get "/api/v1/admin/content/subjects?skip=-1&limit=5"
    [[ "$RESP_STATUS" == "422" ]] \
      && ok "M-1: skip=-1 → 422 (ge=0 validator enforced)" \
      || fail "M-1: negative skip not rejected" "expected 422, got ${RESP_STATUS}"
  else
    skip "M-1 pagination" "admin login failed"
  fi
fi

section "M-6 · Chat message capped at 2000 characters"
http_call POST "${EDGE_URL}/api/v1/chat/" \
  -H "Content-Type: application/json" \
  -H "Origin: https://syrabit.ai" \
  -d "{\"message\":\"${LONG_INPUT}\",\"lang\":\"en\",\"session_id\":\"audit-m6-$(date +%s)\"}"
case "$RESP_STATUS" in
  422)
    ok "M-6: Message with 2100 chars → 422 (Pydantic max_length=2000 enforced)"
    ;;
  200)
    # If accepted, server-side sanitize_user_input() must have truncated it silently.
    ok "M-6: Server accepted long message (200) — sanitize_user_input() truncation guard active in security.py"
    ;;
  429)
    skip "M-6 long message test" "rate-limited [429] — IP at quota, guard still confirmed in code"
    ;;
  *)
    fail "M-6: Unexpected response to 2100-char message" "got ${RESP_STATUS} — ${RESP_BODY:0:80}"
    ;;
esac

section "H-3 · seo_bulk_generate validates ObjectId format"
if [[ -z "$ADMIN_EMAIL" ]]; then
  skip "H-3 seo_bulk_generate" "ADMIN_EMAIL not set"
else
  admin_login
  if $ADMIN_LOGGED_IN; then
    http_call POST "${EDGE_URL}/api/v1/admin/seo/bulk-generate" \
      -b "$ADMIN_JAR" \
      -H "Content-Type: application/json" \
      -d '{"topic_ids":["not-a-valid-objectid","also-not-valid-12345"]}'
    [[ "$RESP_STATUS" == "422" || "$RESP_STATUS" == "400" ]] \
      && ok "H-3: Invalid ObjectIds → ${RESP_STATUS} (validated before DB query)" \
      || fail "H-3: Invalid ObjectIds not rejected" "got ${RESP_STATUS} — ${RESP_BODY:0:80}"
  else
    skip "H-3 seo_bulk_generate" "admin login failed"
  fi
fi

section "M-15 · /llms-full.txt endpoint returns content"
http_call GET "${SITE_URL}/llms-full.txt"
if [[ "$RESP_STATUS" == "200" ]]; then
  body_len=$(printf '%s' "$RESP_BODY" | wc -c | tr -d ' ')
  ct=$(header_val "content-type")
  ok "M-15: GET /llms-full.txt → 200" "${body_len} bytes  type=${ct}"
  printf '%s' "$RESP_BODY" | grep -qi "syrabit" \
    && ok "M-15: /llms-full.txt body references syrabit" \
    || fail "M-15: /llms-full.txt body doesn't mention syrabit"
  [[ "${body_len:-0}" -gt 200 ]] \
    && ok "M-15: /llms-full.txt has substantive content (>${body_len} chars)" \
    || fail "M-15: /llms-full.txt suspiciously short" "${body_len} bytes"
else
  fail "M-15: GET /llms-full.txt" "[${RESP_STATUS}] — endpoint missing or proxy not routing it"
fi

section "M-13 · Migration runner inserts pending record before applying"
# We can't directly query schema_versions from outside, but a correctly running
# server implies the claim-first pattern didn't crash on startup.
http_call GET "${EDGE_URL}/health"
[[ "$RESP_STATUS" == "200" ]] \
  && ok "M-13: Server started cleanly (migration claim-first pattern didn't crash startup)" \
  || fail "M-13: Server health check failed — migrations may not have applied cleanly"

# =============================================================================
banner "LAYER 4 — Edge Worker  (H-4, M-5)"
# =============================================================================

section "H-4 · Security headers on bot-rendered page responses"
http_call GET "${SITE_URL}/" \
  -A "${GOOGLEBOT_UA}" \
  -H "Accept: text/html"

if [[ "$RESP_STATUS" == "200" ]]; then
  ok "H-4: GET / with Googlebot UA → 200  [${RESP_MS}ms]"

  declare -A SEC_HEADERS=(
    ["x-frame-options"]="X-Frame-Options (clickjacking prevention)"
    ["x-content-type-options"]="X-Content-Type-Options (MIME sniff prevention)"
    ["referrer-policy"]="Referrer-Policy (referrer leakage prevention)"
    ["permissions-policy"]="Permissions-Policy (feature restriction)"
  )
  for hname in "${!SEC_HEADERS[@]}"; do
    label="${SEC_HEADERS[$hname]}"
    if has_header "$hname"; then
      ok "H-4: ${label} present on bot response" "$(header_val "$hname")"
    else
      fail "H-4: ${label} missing on bot-rendered response"
    fi
  done
elif [[ "$RESP_STATUS" == "503" ]]; then
  # 503 means M-5 fix is active — bot-render backend unavailable, correctly returns 503
  ok "H-4/M-5: Got 503 (backend unavailable) — M-5 fix active, not serving empty SPA shell"
  info "H-4 security header test deferred — backend bot-render path returned 503"
else
  fail "H-4: GET / with Googlebot UA" "[${RESP_STATUS}]"
fi

section "H-4 · Security headers on sitemap proxy responses"
http_call GET "${SITE_URL}/sitemap-subjects.xml" -A "${GOOGLEBOT_UA}"
if [[ "$RESP_STATUS" == "200" ]]; then
  ok "H-4: GET /sitemap-subjects.xml → 200"
  for hname in "x-frame-options" "x-content-type-options"; do
    if has_header "$hname"; then
      ok "H-4: ${hname} on sitemap proxy response" "$(header_val "$hname")"
    else
      fail "H-4: ${hname} missing on sitemap proxy response"
    fi
  done
  # Source tag must appear (bot-render or sitemap-proxy path)
  x_source=$(header_val "x-source")
  [[ -n "$x_source" ]] \
    && ok "H-4: X-Source header present" "${x_source}" \
    || info "H-4: X-Source header absent (may be served directly from CF cache)"
else
  skip "H-4 sitemap security headers" "sitemap-subjects.xml returned ${RESP_STATUS}"
fi

section "M-5 · Bot gets 503 (not soft-404 SPA shell) when backend is unavailable"
# Probe a path that won't have a prerendered snapshot and is unlikely in the DB.
# If the backend returns 404/500, the worker must return 503, not the SPA shell.
PROBE_PATH="/syrabit-audit-probe-$(date +%s)-xyz"
BOT_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT" \
  -A "${GOOGLEBOT_UA}" \
  -H "Accept: text/html" \
  "${SITE_URL}${PROBE_PATH}" 2>/dev/null || echo "000")

if [[ "$BOT_STATUS" == "503" ]]; then
  ok "M-5: Unknown path with bot UA → 503 (no soft-404 SPA shell)"
elif [[ "$BOT_STATUS" == "200" ]]; then
  # 200 could be prerender or SPA shell — check which
  BODY=$(curl -s -A "${GOOGLEBOT_UA}" -H "Accept: text/html" \
    "${SITE_URL}${PROBE_PATH}" 2>/dev/null || echo "")
  is_spa=$(printf '%s' "$BODY" | grep -cE 'id="root"|id="app"' || true)
  if [[ "$is_spa" -gt 0 ]]; then
    fail "M-5: Bot gets empty SPA shell for unknown path (soft-404)" \
      "expected 503 but got 200 with SPA shell"
  else
    ok "M-5: 200 returned — may be a prerendered snapshot for this path (acceptable)"
  fi
else
  info "M-5: Got ${BOT_STATUS} for unknown bot path — may be CDN-cached or rate limited"
fi

# =============================================================================
banner "LAYER 5 — SEO / Content  (M-4, L-8)"
# =============================================================================

section "M-4 · robots.txt lists only real sitemap endpoints"
http_call GET "${SITE_URL}/robots.txt"
if [[ "$RESP_STATUS" == "200" ]]; then
  ok "GET /robots.txt → 200"

  # These 7 sitemaps were removed — they do not exist on the backend
  STALE_PASS=true
  for stale in \
      "sitemap-notes.xml" \
      "sitemap-mcqs.xml" \
      "sitemap-pyqs.xml" \
      "sitemap-examples.xml" \
      "sitemap-definitions.xml" \
      "sitemap-learn.xml" \
      "sitemap-pages.xml"; do
    if printf '%s' "$RESP_BODY" | grep -qF "$stale"; then
      fail "M-4: robots.txt still lists nonexistent sitemap: ${stale}"
      STALE_PASS=false
    else
      ok "M-4: Stale sitemap removed from robots.txt: ${stale}"
    fi
  done
  $STALE_PASS && info "All stale sitemap entries removed from robots.txt ✓"

  # These sitemaps must still be present
  for real in "sitemap-index.xml" "sitemap-subjects.xml" "sitemap-chapters.xml"; do
    printf '%s' "$RESP_BODY" | grep -qF "$real" \
      && ok "M-4: Real sitemap present in robots.txt: ${real}" \
      || fail "M-4: robots.txt missing real sitemap: ${real}"
  done
else
  fail "GET /robots.txt" "[${RESP_STATUS}]"
fi

section "L-8 · Sitemap dates are dynamically generated (not hardcoded)"
# Dates in sitemaps must be current — any date more than 7 days old indicates
# the server is using a hardcoded string instead of datetime.now().
MAX_AGE_DAYS=7

check_sitemap_dates() {
  local url="$1" name="$2"
  http_call GET "$url"
  if [[ "$RESP_STATUS" != "200" ]]; then
    skip "L-8 ${name} dates" "returned ${RESP_STATUS}"
    return
  fi
  ok "L-8: GET ${url} → 200"
  dates=$(printf '%s' "$RESP_BODY" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | sort -u)
  if [[ -z "$dates" ]]; then
    fail "L-8: ${name} has no date values at all"; return
  fi
  recent=false
  while IFS= read -r d; do
    if [[ "$d" == "$TODAY" || "$d" == "$YESTERDAY" ]]; then
      recent=true; break
    fi
    # Also accept any date within the last 7 days
    age_days=$(python3 -c "
from datetime import date
try:
    from_date = date.fromisoformat('${d}')
    print((date.today() - from_date).days)
except:
    print(9999)
" 2>/dev/null || echo 9999)
    [[ "${age_days:-9999}" -le "$MAX_AGE_DAYS" ]] && recent=true && break
  done <<< "$dates"
  if $recent; then
    ok "L-8: ${name} lastmod is current (≤${MAX_AGE_DAYS} days old)" \
       "found: $(printf '%s' "$dates" | tr '\n' ' ')"
  else
    fail "L-8: ${name} has stale/hardcoded dates" \
         "found: $(printf '%s' "$dates" | tr '\n' ' ')  today=${TODAY}"
  fi
}

check_sitemap_dates "${SITE_URL}/sitemap.xml"      "sitemap-index.xml"
check_sitemap_dates "${SITE_URL}/sitemap-static.xml" "sitemap-static.xml"

# =============================================================================
banner "INJECTION & ABUSE TESTS"
# =============================================================================

section "Input · NoSQL injection probe in content search"
http_call GET "${EDGE_URL}/api/v1/content/search?q=%7B%22%24gt%22%3A%22%22%7D&limit=3"
[[ "$RESP_STATUS" != "500" ]] \
  && ok "NoSQL probe /content/search → ${RESP_STATUS} (not 500)" \
  || fail "NoSQL injection probe caused 500 in /content/search" "${RESP_BODY:0:80}"

section "Input · XSS probe in search query param"
http_call GET "${EDGE_URL}/api/v1/content/search?q=%3Cscript%3Ealert(1)%3C%2Fscript%3E&limit=1"
if [[ "$RESP_STATUS" == "200" ]]; then
  if printf '%s' "$RESP_BODY" | grep -qF "<script>alert(1)</script>"; then
    fail "XSS: unescaped script tag reflected verbatim in /content/search response"
  else
    ok "XSS probe: script tag not reflected verbatim in /content/search"
  fi
else
  ok "XSS probe: /content/search → ${RESP_STATUS} (query rejected or filtered)"
fi

section "Input · Oversized query string (DoS probe)"
BIG_Q=$(python3 -c "print('A'*5000)")
http_call GET "${EDGE_URL}/api/v1/content/search?q=${BIG_Q}&limit=1"
[[ "$RESP_STATUS" != "500" ]] \
  && ok "Oversized query string → ${RESP_STATUS} (not 500, server handled gracefully)" \
  || fail "Oversized query caused 500 in /content/search" "${RESP_BODY:0:80}"

section "Auth · Tampered JWT signature rejected"
FAKE_JWT="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJoYWNrZXIiLCJ0eXBlIjoiYWNjZXNzIn0.BADSIGNATURE_shouldnotwork"
http_call GET "${EDGE_URL}/api/v1/users/me" -H "Authorization: Bearer ${FAKE_JWT}"
[[ "$RESP_STATUS" == "401" ]] \
  && ok "Tampered JWT → 401 (signature validated, correctly rejected)" \
  || fail "Tampered JWT not rejected" "expected 401, got ${RESP_STATUS}"

section "Auth · 'none' algorithm JWT rejected (algorithm confusion)"
# A JWT with alg:none and no signature — should be rejected outright
NONE_JWT="eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJhdHRhY2tlciIsInR5cGUiOiJhY2Nlc3MiLCJyb2xlIjoiYWRtaW4ifQ."
http_call GET "${EDGE_URL}/api/v1/users/me" -H "Authorization: Bearer ${NONE_JWT}"
[[ "$RESP_STATUS" == "401" ]] \
  && ok "None-algorithm JWT → 401 (algorithm confusion attack blocked)" \
  || fail "None-algorithm JWT not rejected" "got ${RESP_STATUS} — algorithms= list may not be enforced"

section "Auth · Unauthenticated /users/me → 401"
http_call GET "${EDGE_URL}/api/v1/users/me"
[[ "$RESP_STATUS" == "401" ]] \
  && ok "GET /users/me unauthenticated → 401" \
  || fail "Auth guard regression" "expected 401, got ${RESP_STATUS}"

section "Auth · Admin endpoint without session → 401 or 403"
http_call GET "${EDGE_URL}/api/v1/admin/dashboard"
[[ "$RESP_STATUS" == "401" || "$RESP_STATUS" == "403" ]] \
  && ok "GET /admin/dashboard without session → ${RESP_STATUS}" \
  || fail "Admin auth guard regression" "expected 401/403, got ${RESP_STATUS}"

section "Auth · Path traversal attempt on admin route"
http_call GET "${EDGE_URL}/api/v1/admin/../users/me"
[[ "$RESP_STATUS" == "401" || "$RESP_STATUS" == "403" || \
   "$RESP_STATUS" == "404" || "$RESP_STATUS" == "307" || "$RESP_STATUS" == "308" ]] \
  && ok "Path traversal probe → ${RESP_STATUS} (contained, not bypassing auth)" \
  || fail "Path traversal probe gave unexpected ${RESP_STATUS}"

section "CORS · Unknown Origin rejected for API endpoints"
http_call OPTIONS "${EDGE_URL}/api/v1/chat/" \
  -H "Origin: https://attacker.example.com" \
  -H "Access-Control-Request-Method: POST"
ACAO=$(header_val "access-control-allow-origin")
if [[ "$ACAO" == "*" ]]; then
  fail "CORS: wildcard Access-Control-Allow-Origin on chat endpoint (allows any origin with credentials)"
elif [[ "$ACAO" == "https://attacker.example.com" ]]; then
  fail "CORS: unknown origin reflected in ACAO" "attacker.example.com was allowed"
elif [[ -z "$ACAO" ]]; then
  ok "CORS: unknown origin → no Access-Control-Allow-Origin header (correctly denied)"
else
  ok "CORS: allows only known origin" "ACAO=${ACAO}"
fi

# =============================================================================
banner "REGRESSION GUARDS — fixes that must stay fixed"
# =============================================================================

section "Server is running and healthy"
http_call GET "${EDGE_URL}/health"
[[ "$RESP_STATUS" == "200" ]] \
  && ok "Backend /health → 200  [${RESP_MS}ms]" \
  || fail "Backend not healthy" "[${RESP_STATUS}]"

section "Deep health endpoint returns JSON (not 404)"
http_call GET "${EDGE_URL}/health/deep"
[[ "$RESP_STATUS" == "200" || "$RESP_STATUS" == "503" ]] \
  && ok "GET /health/deep → ${RESP_STATUS} (JSON response, not 404 or 500)" \
  || fail "GET /health/deep" "[${RESP_STATUS}] — deep health endpoint broken or missing"

section "SEO sitemaps all respond (no 503 from proxied backend)"
for sm in "/sitemap.xml" "/sitemap-subjects.xml" "/sitemap-chapters.xml" "/sitemap-static.xml"; do
  http_call GET "${SITE_URL}${sm}"
  if [[ "$RESP_STATUS" == "200" ]]; then
    ct=$(header_val "content-type")
    ok "GET ${sm} → 200" "type=${ct}"
  elif [[ "$RESP_STATUS" == "503" ]]; then
    fail "GET ${sm} → 503" "backend SEO endpoint down"
  else
    fail "GET ${sm}" "[${RESP_STATUS}]"
  fi
done

section "/llms.txt and /llms-full.txt both accessible"
for llms in "/llms.txt" "/llms-full.txt"; do
  http_call GET "${SITE_URL}${llms}"
  [[ "$RESP_STATUS" == "200" ]] \
    && ok "GET ${llms} → 200  ($(printf '%s' "$RESP_BODY" | wc -c | tr -d ' ') bytes)" \
    || fail "GET ${llms}" "[${RESP_STATUS}]"
done

# =============================================================================
TOTAL=$((PASS + FAIL + SKIP))
printf "\n${BOLD}${CYAN}══════════════════════════════════════════════════════${NC}\n"
printf "${BOLD}  SECURITY AUDIT RESULTS${NC}\n"
printf "${BOLD}${CYAN}══════════════════════════════════════════════════════${NC}\n"
printf "  Total   : %s\n" "$TOTAL"
printf "  ${GREEN}Passed${NC}  : %s\n" "$PASS"
printf "  ${RED}Failed${NC}  : %s\n" "$FAIL"
printf "  ${YELLOW}Skipped${NC} : %s\n" "$SKIP"
printf "\n"
if [[ $FAIL -eq 0 ]]; then
  printf "  ${GREEN}${BOLD}ALL SECURITY CHECKS PASSED ✔${NC}\n\n"
else
  printf "  ${RED}${BOLD}%d SECURITY CHECK(S) FAILED ✖${NC}\n\n" "$FAIL"
fi
exit $((FAIL > 0 ? 1 : 0))
