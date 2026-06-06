#!/usr/bin/env bash
# =============================================================================
# Syrabit Live Deployment Test Script
# Layers: CF Edge · Library · User Auth · Chat EN · Chat AS · Admin Auth · Admin Panel
#
# Usage:
#   chmod +x scripts/test-live.sh && ./scripts/test-live.sh
#
# Overrides:
#   EDGE_URL=https://api.syrabit.ai  USER_EMAIL=...  USER_PASS=...
#   ADMIN_EMAIL=...  ADMIN_PASS=...  ./scripts/test-live.sh
# =============================================================================

EDGE_URL="${EDGE_URL:-https://api.syrabit.ai}"
SITE_URL="${SITE_URL:-https://syrabit.ai}"
USER_EMAIL="${USER_EMAIL:-founder@syrabit.ai}"
USER_PASS="${USER_PASS:-Rimjhimiya@325544}"
ADMIN_EMAIL="${ADMIN_EMAIL:-founder@syrabit.ai}"
ADMIN_PASS="${ADMIN_PASS:-Rimjhimiya@325544}"
TIMEOUT=30
CHAT_TIMEOUT=45

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m' GREEN='\033[0;32m' YELLOW='\033[1;33m'
CYAN='\033[0;36m' BOLD='\033[1m' DIM='\033[2m' NC='\033[0m'

# ── Counters ──────────────────────────────────────────────────────────────────
PASS=0 FAIL=0 SKIP=0

# ── Global response store ─────────────────────────────────────────────────────
# Never set these inside $() — that creates a subshell and changes are lost.
RESP_STATUS=""
RESP_BODY=""
RESP_MS=0

# ── UI helpers ────────────────────────────────────────────────────────────────
banner() {
  printf "\n${BOLD}${CYAN}══════════════════════════════════════════════════════${NC}\n"
  printf "${BOLD}${CYAN}  LAYER %s: %s${NC}\n" "$1" "$2"
  printf "${BOLD}${CYAN}══════════════════════════════════════════════════════${NC}\n"
}
section() { printf "\n${BOLD}▸ %s${NC}\n" "$1"; }
ok()   { PASS=$((PASS+1)); printf "  ${GREEN}✔${NC}  %s  ${DIM}%s${NC}\n" "$1" "${2:-}"; }
fail() { FAIL=$((FAIL+1)); printf "  ${RED}✖${NC}  %s  ${DIM}%s${NC}\n" "$1" "${2:-}"; }
skip() { SKIP=$((SKIP+1)); printf "  ${YELLOW}⚠${NC}  %s  ${DIM}(skipped — prereq missing)${NC}\n" "$1"; }
info() { printf "     ${DIM}%s${NC}\n" "$1"; }

# ── HTTP call — sets RESP_STATUS, RESP_BODY, RESP_MS (NO subshell) ────────────
# Usage:  http_call <METHOD> <URL> [curl args...]
http_call() {
  local method="$1" url="$2"; shift 2
  local tmpfile; tmpfile=$(mktemp)
  local t0 t1
  t0=$(python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null || date +%s)
  curl -s -X "$method" "$url" --max-time "$TIMEOUT" \
    -w "\n__STATUS__%{http_code}__" \
    "$@" > "$tmpfile" 2>/dev/null || true
  t1=$(python3 -c "import time; print(int(time.time()*1000))" 2>/dev/null || date +%s)
  RESP_MS=$(( t1 - t0 ))
  local raw; raw=$(cat "$tmpfile"); rm -f "$tmpfile"
  # Extract status from sentinel
  RESP_STATUS=$(printf '%s' "$raw" | grep -oP '(?<=__STATUS__)\d+' | tail -1 || echo "000")
  # Body = everything before the sentinel line
  RESP_BODY=$(printf '%s' "$raw" | sed 's/__STATUS__[0-9]*__//' | sed '${ /^[[:space:]]*$/d }')
}

# Call + assert status; returns 0 on pass, 1 on fail
check() {
  local label="$1" want="$2"; shift 2   # remaining: METHOD URL [curl args]
  http_call "$@"
  if [[ "$RESP_STATUS" == "$want" ]]; then
    ok "$label" "[${RESP_STATUS}] ${RESP_MS}ms"
    return 0
  else
    fail "$label" "[got ${RESP_STATUS}, want ${want}] ${RESP_MS}ms"
    [[ -n "$RESP_BODY" ]] && info "${RESP_BODY:0:150}"
    return 1
  fi
}

# Extract value from RESP_BODY JSON by dot-path (e.g. "access_token", "boards.0.slug")
jval() {
  printf '%s' "$RESP_BODY" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    v = d
    for k in '$1'.split('.'):
        v = v[int(k)] if isinstance(v, list) else v[k]
    sys.stdout.write(str(v) if v is not None else '')
except: sys.stdout.write('')
" 2>/dev/null
}

# Count array length in RESP_BODY at dot-path
jlen() {
  printf '%s' "$RESP_BODY" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    v = d
    if '$1':
        for k in '$1'.split('.'): v = v[k]
    print(len(v) if isinstance(v, (list, dict)) else 0)
except: print(0)
" 2>/dev/null
}

# ── Runtime state ─────────────────────────────────────────────────────────────
USER_TOKEN="" REFRESH_TOKEN="" CONV_ID=""
BOARD_SLUG="" CLASS_SLUG="" SUBJ_SLUG=""
ADMIN_JAR=""

# =============================================================================
banner 0 "CF Edge Connectivity  ($EDGE_URL)"
# =============================================================================

section "Backend reachability"
http_call GET "${EDGE_URL}/health"
if [[ "$RESP_STATUS" == "200" ]]; then
  ok "GET /health → 200" "${RESP_MS}ms"
  backend_ok=$(printf '%s' "$RESP_BODY" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('backend_reachable','?'))" 2>/dev/null)
  [[ "$backend_ok" == "True" || "$backend_ok" == "true" ]] \
    && ok "backend_reachable: true" \
    || info "backend_reachable=${backend_ok} (may be edge-only health check)"
else
  fail "GET /health" "[${RESP_STATUS}] ${RESP_MS}ms"
  info "${RESP_BODY:0:100}"
fi

section "CORS preflight"
CORS_HEADERS=$(curl -sI -X OPTIONS "${EDGE_URL}/api/v1/content/library-bundle" \
  -H "Origin: https://syrabit.ai" \
  -H "Access-Control-Request-Method: GET" \
  --max-time "$TIMEOUT" 2>/dev/null || echo "")
acao=$(printf '%s' "$CORS_HEADERS" | grep -i "access-control-allow-origin" | head -1 | tr -d '\r')
cors_st=$(printf '%s' "$CORS_HEADERS" | head -1 | awk '{print $2}')
if [[ -n "$acao" ]]; then
  ok "CORS preflight → ${cors_st}" "$acao"
else
  fail "CORS preflight" "[${cors_st}] no Access-Control-Allow-Origin"
fi

section "Frontend (CF Pages)"
site_st=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT" \
  -L "${SITE_URL}/library" 2>/dev/null || echo "000")
[[ "$site_st" == "200" ]] \
  && ok "GET ${SITE_URL}/library → 200" \
  || fail "GET ${SITE_URL}/library" "[${site_st}]"

# =============================================================================
banner 1 "Library Page Content"
# =============================================================================

section "Library bundle (unauthenticated)"
if check "GET /api/v1/content/library-bundle?slim=1 → 200" "200" \
    GET "${EDGE_URL}/api/v1/content/library-bundle?slim=1"; then
  BOARD_SLUG=$(jval "boards.0.slug")
  CLASS_SLUG=$(jval "classes.0.slug")
  SUBJ_SLUG=$(jval "subjects.0.slug")
  boards=$(jlen "boards"); classes=$(jlen "classes")
  streams=$(jlen "streams"); subjects=$(jlen "subjects")
  if [[ "${boards:-0}" -gt 0 ]]; then
    ok "Content counts" "${boards} boards · ${classes} classes · ${streams} streams · ${subjects} subjects"
    info "Board=${BOARD_SLUG}  Class=${CLASS_SLUG}  Subject=${SUBJ_SLUG}"
  else
    fail "Library bundle empty" "boards=0"
  fi
fi

section "Subject drill-down resolution"
if [[ -n "$BOARD_SLUG" && -n "$CLASS_SLUG" && -n "$SUBJ_SLUG" ]]; then
  check "GET /content/resolve-subject/${BOARD_SLUG}/${CLASS_SLUG}/${SUBJ_SLUG} → 200" "200" \
    GET "${EDGE_URL}/api/v1/content/resolve-subject/${BOARD_SLUG}/${CLASS_SLUG}/${SUBJ_SLUG}" || true
else
  skip "resolve-subject (library-bundle slugs unavailable)"
fi

section "Content search"
check "GET /content/search?q=science → 200" "200" \
  GET "${EDGE_URL}/api/v1/content/search?q=science&limit=5" || true

section "Question papers"
check "GET /content/question-papers → 200" "200" \
  GET "${EDGE_URL}/api/v1/content/question-papers?limit=3" || true

# =============================================================================
banner 2 "User Auth  ($USER_EMAIL)"
# =============================================================================

section "Login"
if check "POST /api/v1/auth/login → 200" "200" \
    POST "${EDGE_URL}/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${USER_EMAIL}\",\"password\":\"${USER_PASS}\"}"; then
  USER_TOKEN=$(jval "access_token")
  REFRESH_TOKEN=$(jval "refresh_token")
  [[ -n "$USER_TOKEN" ]] \
    && ok "access_token received" "${USER_TOKEN:0:25}…" \
    || fail "access_token missing in response"
fi

section "GET /api/v1/users/me (authenticated)"
if [[ -n "$USER_TOKEN" ]]; then
  if check "GET /api/v1/users/me → 200" "200" \
      GET "${EDGE_URL}/api/v1/users/me" \
      -H "Authorization: Bearer ${USER_TOKEN}"; then
    info "email=$(jval email)  role=$(jval role)"
  fi
else
  skip "GET /users/me"
fi

section "Unauthenticated guard"
check "GET /api/v1/users/me without token → 401" "401" \
  GET "${EDGE_URL}/api/v1/users/me" || true

section "Token refresh"
if [[ -n "$REFRESH_TOKEN" ]]; then
  if check "POST /api/v1/auth/refresh → 200" "200" \
      POST "${EDGE_URL}/api/v1/auth/refresh" \
      -H "Content-Type: application/json" \
      -d "{\"refresh_token\":\"${REFRESH_TOKEN}\"}"; then
    new_tok=$(jval "access_token")
    [[ -n "$new_tok" ]] && USER_TOKEN="$new_tok" \
      && ok "Refreshed token" "${new_tok:0:25}…"
  fi
else
  skip "Token refresh (no refresh_token)"
fi

section "List conversations"
if [[ -n "$USER_TOKEN" ]]; then
  if check "GET /api/v1/conversations → 200" "200" \
      GET "${EDGE_URL}/api/v1/conversations?limit=5" \
      -H "Authorization: Bearer ${USER_TOKEN}"; then
    count=$(printf '%s' "$RESP_BODY" | python3 -c "
import sys,json; d=json.load(sys.stdin)
items = d if isinstance(d,list) else d.get('conversations', d.get('items',[]))
print(len(items))
" 2>/dev/null || echo 0)
    ok "Conversations" "${count} items"
    CONV_ID=$(printf '%s' "$RESP_BODY" | python3 -c "
import sys,json; d=json.load(sys.stdin)
items = d if isinstance(d,list) else d.get('conversations', d.get('items',[]))
if items: print(items[0].get('id') or items[0].get('session_id',''))
" 2>/dev/null || echo "")
  fi
else
  skip "GET /api/v1/conversations"
fi

section "Get single conversation"
if [[ -n "$USER_TOKEN" && -n "$CONV_ID" ]]; then
  check "GET /api/v1/conversations/${CONV_ID:0:16}… → 200" "200" \
    GET "${EDGE_URL}/api/v1/conversations/${CONV_ID}" \
    -H "Authorization: Bearer ${USER_TOKEN}" || true
else
  skip "GET /conversations/{id} (no conversation ID)"
fi

# =============================================================================
banner 3 "Chat — English"
# =============================================================================

CHAT_SID_EN="test-en-$(date +%s)"

section "Non-streaming (authenticated)"
if [[ -n "$USER_TOKEN" ]]; then
  TIMEOUT=$CHAT_TIMEOUT
  if check "POST /api/v1/chat/ (EN, auth) → 200" "200" \
      POST "${EDGE_URL}/api/v1/chat/" \
      -H "Authorization: Bearer ${USER_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{\"message\":\"What is photosynthesis? Answer in 2 sentences.\",\"lang\":\"en\",\"session_id\":\"${CHAT_SID_EN}\"}"; then
    reply=$(printf '%s' "$RESP_BODY" | python3 -c "
import sys,json; d=json.load(sys.stdin)
r = d.get('response') or d.get('message') or d.get('content') or ''
print(str(r)[:100])
" 2>/dev/null || echo "")
    [[ -n "$reply" ]] && ok "AI reply (EN, auth)" "${reply}…" \
      || fail "AI reply empty" "$(printf '%s' "$RESP_BODY" | head -c 150)"
  fi
  TIMEOUT=30

  section "Multi-turn follow-up (same session)"
  TIMEOUT=$CHAT_TIMEOUT
  if check "POST /api/v1/chat/ (EN follow-up, same session_id) → 200" "200" \
      POST "${EDGE_URL}/api/v1/chat/" \
      -H "Authorization: Bearer ${USER_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{\"message\":\"Give one real-world example.\",\"lang\":\"en\",\"session_id\":\"${CHAT_SID_EN}\"}"; then
    reply2=$(printf '%s' "$RESP_BODY" | python3 -c "
import sys,json; d=json.load(sys.stdin)
r = d.get('response') or d.get('message') or d.get('content') or ''
print(str(r)[:100])
" 2>/dev/null || echo "")
    [[ -n "$reply2" ]] && ok "Multi-turn follow-up (EN)" "${reply2}…" \
      || fail "Multi-turn reply empty"
  fi
  TIMEOUT=30
else
  skip "Authenticated EN chat"
fi

section "Anonymous chat (no token)"
TIMEOUT=$CHAT_TIMEOUT
if check "POST /api/v1/chat/ (EN, anonymous) → 200" "200" \
    POST "${EDGE_URL}/api/v1/chat/" \
    -H "Content-Type: application/json" \
    -d "{\"message\":\"Hello! What subjects can you help with?\",\"lang\":\"en\",\"session_id\":\"anon-en-$(date +%s)\"}"; then
  reply=$(printf '%s' "$RESP_BODY" | python3 -c "
import sys,json; d=json.load(sys.stdin)
r = d.get('response') or d.get('message') or d.get('content') or ''
print(str(r)[:100])
" 2>/dev/null || echo "")
  [[ -n "$reply" ]] && ok "Anonymous AI reply (EN)" "${reply}…" \
    || fail "Anonymous reply empty" "$(printf '%s' "$RESP_BODY" | head -c 150)"
fi
TIMEOUT=30

# =============================================================================
banner 4 "Chat — Assamese (অসমীয়া)"
# =============================================================================

CHAT_SID_AS="test-as-$(date +%s)"

section "Explicit lang=as (authenticated)"
if [[ -n "$USER_TOKEN" ]]; then
  TIMEOUT=$CHAT_TIMEOUT
  if check "POST /api/v1/chat/ (AS, explicit lang, auth) → 200" "200" \
      POST "${EDGE_URL}/api/v1/chat/" \
      -H "Authorization: Bearer ${USER_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{\"message\":\"সালোকসংশ্লেষণ কি? চমুকৈ বুজাই দিয়া।\",\"lang\":\"as\",\"session_id\":\"${CHAT_SID_AS}\"}"; then
    reply=$(printf '%s' "$RESP_BODY" | python3 -c "
import sys,json; d=json.load(sys.stdin)
r = d.get('response') or d.get('message') or d.get('content') or ''
lang = d.get('lang') or d.get('language','?')
print(f'[lang={lang}] {str(r)[:90]}')
" 2>/dev/null || echo "")
    [[ -n "$reply" ]] && ok "AI reply (AS, explicit lang)" "${reply}…" \
      || fail "AI reply (AS) empty" "$(printf '%s' "$RESP_BODY" | head -c 150)"
  fi
  TIMEOUT=30

  section "Assamese auto-detect (no lang field)"
  TIMEOUT=$CHAT_TIMEOUT
  if check "POST /api/v1/chat/ (AS auto-detect, auth) → 200" "200" \
      POST "${EDGE_URL}/api/v1/chat/" \
      -H "Authorization: Bearer ${USER_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{\"message\":\"বিজ্ঞান মানে কি?\",\"session_id\":\"${CHAT_SID_AS}-auto\"}"; then
    reply=$(printf '%s' "$RESP_BODY" | python3 -c "
import sys,json; d=json.load(sys.stdin)
r = d.get('response') or d.get('message') or d.get('content') or ''
lang = d.get('lang') or d.get('language','?')
print(f'[detected={lang}] {str(r)[:90]}')
" 2>/dev/null || echo "")
    [[ -n "$reply" ]] && ok "Auto-detect Assamese" "${reply}…" \
      || fail "Auto-detect AS empty"
  fi
  TIMEOUT=30
else
  skip "Authenticated AS chat"
fi

section "Anonymous Assamese chat"
TIMEOUT=$CHAT_TIMEOUT
if check "POST /api/v1/chat/ (AS, anonymous) → 200" "200" \
    POST "${EDGE_URL}/api/v1/chat/" \
    -H "Content-Type: application/json" \
    -d "{\"message\":\"গণিত শিকাত সহায় কৰা।\",\"lang\":\"as\",\"session_id\":\"anon-as-$(date +%s)\"}"; then
  reply=$(printf '%s' "$RESP_BODY" | python3 -c "
import sys,json; d=json.load(sys.stdin)
r = d.get('response') or d.get('message') or d.get('content') or ''
print(str(r)[:100])
" 2>/dev/null || echo "")
  [[ -n "$reply" ]] && ok "Anonymous AI reply (AS)" "${reply}…" \
    || fail "Anonymous reply (AS) empty" "$(printf '%s' "$RESP_BODY" | head -c 150)"
fi
TIMEOUT=30

# =============================================================================
banner 5 "Admin Auth  ($ADMIN_EMAIL)"
# =============================================================================

ADMIN_JAR=$(mktemp)

section "Admin login"
http_call POST "${EDGE_URL}/api/v1/admin/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASS}\"}" \
  -c "$ADMIN_JAR"

if [[ "$RESP_STATUS" == "200" ]]; then
  ok "POST /api/v1/admin/login → 200" "${RESP_MS}ms"
  st=$(jval "status")
  [[ "$st" == "ok" ]] && ok "Response status=ok" || fail "Response status" "got '${st}'"
  grep -qi "syrabit_admin_session" "$ADMIN_JAR" 2>/dev/null \
    && ok "Admin session cookie stored in jar" \
    || info "Cookie may be in-memory — proceeding with jar"
else
  fail "POST /api/v1/admin/login" "[${RESP_STATUS}] ${RESP_BODY:0:100}"
fi

section "Admin verify (session check)"
http_call GET "${EDGE_URL}/api/v1/admin/verify" -b "$ADMIN_JAR"
if [[ "$RESP_STATUS" == "200" ]]; then
  ok "GET /api/v1/admin/verify → 200" "(session valid)"
  info "$(printf '%s' "$RESP_BODY" | head -c 80)"
else
  fail "GET /api/v1/admin/verify" "[${RESP_STATUS}] ${RESP_BODY:0:100}"
fi

section "Admin guard (no session)"
http_call GET "${EDGE_URL}/api/v1/admin/verify"  # No -b jar
[[ "$RESP_STATUS" == "401" || "$RESP_STATUS" == "403" ]] \
  && ok "GET /api/v1/admin/verify without session → ${RESP_STATUS}" "(guard active)" \
  || fail "Admin guard" "expected 401/403, got ${RESP_STATUS}"

# =============================================================================
banner 6 "Admin Panel Endpoints"
# =============================================================================

# Admin GET helper
admin_get() {
  local path="$1" note="${2:-}"
  http_call GET "${EDGE_URL}${path}" -b "$ADMIN_JAR"
  if [[ "$RESP_STATUS" == "200" ]]; then
    ok "GET ${path} → 200" "${RESP_MS}ms${note:+  $note}"
    return 0
  else
    fail "GET ${path}" "[${RESP_STATUS}] ${RESP_BODY:0:100}"
    return 1
  fi
}

section "Dashboard"
if admin_get "/api/v1/admin/dashboard"; then
  total=$(printf '%s' "$RESP_BODY" | python3 -c "
import sys,json
d=json.load(sys.stdin)
v = d.get('total_users') or d.get('users',{}).get('total') \
    or d.get('stats',{}).get('total_users','?')
print(f'total_users={v}')
" 2>/dev/null || echo "")
  [[ -n "$total" ]] && info "$total"
fi

section "Users list"
if admin_get "/api/v1/admin/users?limit=5"; then
  count=$(printf '%s' "$RESP_BODY" | python3 -c "
import sys,json; d=json.load(sys.stdin)
items = d if isinstance(d,list) else d.get('users', d.get('items',[]))
print(f'{len(items)} users returned')
" 2>/dev/null || echo ""); [[ -n "$count" ]] && info "$count"
fi

section "Analytics overview"
admin_get "/api/v1/admin/analytics" || true

section "Analytics daily"
admin_get "/api/v1/admin/analytics/daily" || true

section "Conversations"
if admin_get "/api/v1/admin/conversations?limit=5"; then
  count=$(printf '%s' "$RESP_BODY" | python3 -c "
import sys,json; d=json.load(sys.stdin)
items = d if isinstance(d,list) else d.get('conversations', d.get('items',[]))
print(f'{len(items)} conversations')
" 2>/dev/null || echo ""); [[ -n "$count" ]] && info "$count"
fi

section "Internal health"
if admin_get "/api/v1/admin/health"; then
  printf '%s' "$RESP_BODY" | python3 -c "
import sys,json
try:
  d=json.load(sys.stdin)
  for k,v in d.items():
    st = v.get('status','?') if isinstance(v,dict) else str(v)
    print(f'     {k}: {st}')
except: pass
" 2>/dev/null || true
fi

section "AI providers"
admin_get "/api/v1/admin/ai/providers" || true

section "Admin logout"
http_call POST "${EDGE_URL}/api/v1/admin/logout" -b "$ADMIN_JAR"
[[ "$RESP_STATUS" == "200" || "$RESP_STATUS" == "204" ]] \
  && ok "POST /api/v1/admin/logout → ${RESP_STATUS}" "(session cleared)" \
  || fail "POST /api/v1/admin/logout" "[${RESP_STATUS}]"
rm -f "$ADMIN_JAR"

# =============================================================================
banner 7 "Security Guards"
# =============================================================================

section "Tampered JWT rejected"
FAKE_JWT="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmYWtlIiwidHlwZSI6ImFjY2VzcyJ9.badsig"
http_call GET "${EDGE_URL}/api/v1/users/me" -H "Authorization: Bearer ${FAKE_JWT}"
[[ "$RESP_STATUS" == "401" ]] \
  && ok "GET /users/me with tampered JWT → 401" "(correctly rejected)" \
  || fail "Tampered JWT guard" "expected 401, got ${RESP_STATUS} — ${RESP_BODY:0:80}"

section "Admin endpoint without session"
http_call GET "${EDGE_URL}/api/v1/admin/dashboard"  # No cookie jar
[[ "$RESP_STATUS" == "401" || "$RESP_STATUS" == "403" ]] \
  && ok "GET /admin/dashboard without session → ${RESP_STATUS}" "(correctly blocked)" \
  || fail "Admin guard" "expected 401/403, got ${RESP_STATUS}"

# =============================================================================
# RESULTS
# =============================================================================
TOTAL=$((PASS + FAIL + SKIP))
printf "\n${BOLD}${CYAN}══════════════════════════════════════════════════════${NC}\n"
printf "${BOLD}  RESULTS${NC}\n"
printf "${BOLD}${CYAN}══════════════════════════════════════════════════════${NC}\n"
printf "  Total   : %s\n" "$TOTAL"
printf "  ${GREEN}Passed${NC}  : %s\n" "$PASS"
printf "  ${RED}Failed${NC}  : %s\n" "$FAIL"
printf "  ${YELLOW}Skipped${NC} : %s\n" "$SKIP"
printf "\n"
if [[ $FAIL -eq 0 ]]; then
  printf "  ${GREEN}${BOLD}ALL CHECKS PASSED ✔${NC}\n\n"
else
  printf "  ${RED}${BOLD}%s CHECK(S) FAILED ✖${NC}\n\n" "$FAIL"
fi

exit $((FAIL > 0 ? 1 : 0))
