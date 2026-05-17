#!/usr/bin/env bash
# =============================================================================
# auth_configure.sh — Local dev auth setup + verification for Replit
#
# What this does (13 checks):
#   1.  Write artifacts/syrabit/.env.local  (VITE_SUPABASE_* for local Vite)
#   2.  Verify auth_provider="email" is set in routes/auth.py signup dict
#   3.  Verify auth_provider fallback "email" in db_ops.supa_insert_user
#   4.  Verify GoogleSignInButton redirectTo uses window.location.origin
#   5.  Backend import smoke test (python -c "import server")
#   6.  GET /api/health → ok=true
#   7.  GET /api/turnstile/config → reachable
#   8.  POST /api/auth/supabase-session with bad token → 401 (not 503)
#   9.  Supabase REST API → HTTP 200
#   10. Google OAuth provider enabled in Supabase
#   11. Email signup enabled in Supabase
#   12. CORS preflight for Replit dev domain accepted
#   13. Sync VITE_* vars to Cloudflare Pages (requires CLOUDFLARE_API_TOKEN)
#
# No GitHub PAT required. Reads from Replit environment variables only.
# =============================================================================

set -uo pipefail

PASS=0
FAIL=0
RESULTS=()

ok()   { PASS=$((PASS+1)); RESULTS+=("  ✅  $1"); }
fail() { FAIL=$((FAIL+1)); RESULTS+=("  ❌  $1"); }

# ── Config from Replit env ────────────────────────────────────────────────────
SUPABASE_URL="${VITE_SUPABASE_URL:-${SUPABASE_URL:-}}"
SUPABASE_ANON_KEY="${VITE_SUPABASE_ANON_KEY:-}"
BACKEND_URL="http://localhost:8080"
CF_ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-d66e40eac539fff1db270fddf384a5ec}"
CF_PAGES_PROJECT="${CF_PAGES_PROJECT_NAME:-syrabitfrontend}"
REPLIT_DEV="${REPLIT_DEV_DOMAIN:-}"

echo ""
echo "============================================="
echo " auth_configure.sh — Syrabit.ai dev auth"
echo "============================================="
echo ""

# ── Check 1: Write .env.local ─────────────────────────────────────────────────
echo "[ 1/13] Writing artifacts/syrabit/.env.local …"
ENV_LOCAL="artifacts/syrabit/.env.local"

if [[ -z "$SUPABASE_URL" || -z "$SUPABASE_ANON_KEY" ]]; then
  fail ".env.local skipped — VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY not set in Replit env"
else
  cat > "$ENV_LOCAL" <<EOF
VITE_SUPABASE_URL=${SUPABASE_URL}
VITE_SUPABASE_ANON_KEY=${SUPABASE_ANON_KEY}
EOF
  ok ".env.local created ($(wc -l < "$ENV_LOCAL") lines)"
fi

# ── Check 2: auth_provider="email" in signup dict ────────────────────────────
echo "[ 2/13] Checking auth_provider=\"email\" in routes/auth.py …"
if grep -q '"auth_provider": "email"' artifacts/syrabit-backend/routes/auth.py; then
  ok 'auth_provider="email" present in signup user dict'
else
  fail 'auth_provider="email" MISSING from signup user dict — email signups may crash on NOT NULL'
fi

# ── Check 3: auth_provider fallback in db_ops ────────────────────────────────
echo "[ 3/13] Checking auth_provider fallback in db_ops.py …"
if grep -q 'user.get("auth_provider", "email")' artifacts/syrabit-backend/db_ops.py; then
  ok 'auth_provider fallback "email" active in supa_insert_user'
else
  fail 'auth_provider fallback MISSING in db_ops.supa_insert_user — NOT NULL guard inactive'
fi

# ── Check 4: GoogleSignInButton redirectTo ───────────────────────────────────
echo "[ 4/13] Checking GoogleSignInButton redirectTo …"
if grep -q 'redirectTo: window.location.origin' artifacts/syrabit/src/components/GoogleSignInButton.jsx; then
  ok 'GoogleSignInButton uses window.location.origin (correct)'
else
  fail 'GoogleSignInButton still uses window.location.href — OAuth may land on wrong page'
fi

# ── Check 5: Backend import smoke test ───────────────────────────────────────
echo "[ 5/13] Backend import smoke test …"
if (cd artifacts/syrabit-backend && python3 -c "import server" 2>&1); then
  ok 'Backend import clean'
else
  fail 'Backend import FAILED — check server.py / routes for syntax errors'
fi

# ── Check 6: /api/health ─────────────────────────────────────────────────────
echo "[ 6/13] GET /api/health …"
HEALTH=$(curl -sf --max-time 8 "${BACKEND_URL}/api/health" 2>/dev/null || echo "{}")
if echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('ok') or d.get('status') in ('ok','healthy') else 1)" 2>/dev/null; then
  ok '/api/health → ok=true'
else
  fail "/api/health returned unexpected: $(echo $HEALTH | head -c 120)"
fi

# ── Check 7: /api/turnstile/config ───────────────────────────────────────────
echo "[ 7/13] GET /api/turnstile/config …"
TC=$(curl -sf --max-time 8 "${BACKEND_URL}/api/turnstile/config" 2>/dev/null || echo "")
if [[ -n "$TC" ]]; then
  ok '/api/turnstile/config reachable'
else
  fail '/api/turnstile/config unreachable or returned empty'
fi

# ── Check 8: /api/auth/supabase-session (bad token → 401) ───────────────────
echo "[ 8/13] POST /api/auth/supabase-session with bad token …"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 \
  -X POST "${BACKEND_URL}/api/auth/supabase-session" \
  -H "Content-Type: application/json" \
  -d '{"supabase_token":"invalid.jwt.token"}' 2>/dev/null || echo "000")
if [[ "$HTTP_CODE" == "401" || "$HTTP_CODE" == "422" ]]; then
  ok "/api/auth/supabase-session → ${HTTP_CODE} on bad token (correct — not 503)"
elif [[ "$HTTP_CODE" == "503" ]]; then
  fail "/api/auth/supabase-session → 503 (Supabase not configured — add SUPABASE_SERVICE_ROLE_KEY to Replit Secrets)"
else
  fail "/api/auth/supabase-session → ${HTTP_CODE} (expected 401, got ${HTTP_CODE})"
fi

# ── Check 9: Supabase REST API reachable ─────────────────────────────────────
echo "[ 9/13] Supabase REST API reachable …"
if [[ -n "$SUPABASE_URL" && -n "$SUPABASE_ANON_KEY" ]]; then
  SB_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 \
    -H "apikey: ${SUPABASE_ANON_KEY}" \
    -H "Authorization: Bearer ${SUPABASE_ANON_KEY}" \
    "${SUPABASE_URL}/rest/v1/" 2>/dev/null || echo "000")
  if [[ "$SB_CODE" == "200" || "$SB_CODE" == "400" || "$SB_CODE" == "401" || "$SB_CODE" == "404" ]]; then
    ok "Supabase REST API → HTTP ${SB_CODE}"
  else
    fail "Supabase REST API → HTTP ${SB_CODE} (expected 200/401)"
  fi
else
  fail "Supabase REST API skipped — VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY not set"
fi

# ── Check 10 & 11: Supabase provider config ───────────────────────────────────
echo "[10/13] Supabase Google OAuth provider enabled …"
echo "[11/13] Supabase Email signup enabled …"
if [[ -n "$SUPABASE_URL" && -n "$SUPABASE_ANON_KEY" ]]; then
  SB_SETTINGS=$(curl -sf --max-time 8 \
    -H "apikey: ${SUPABASE_ANON_KEY}" \
    -H "Authorization: Bearer ${SUPABASE_ANON_KEY}" \
    "${SUPABASE_URL}/auth/v1/settings" 2>/dev/null || echo "{}")

  GOOGLE_ON=$(echo "$SB_SETTINGS" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('external',{}).get('google',False))" 2>/dev/null || echo "unknown")
  EMAIL_ON=$(echo "$SB_SETTINGS" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(not d.get('disable_signup',True))" 2>/dev/null || echo "unknown")

  if [[ "$GOOGLE_ON" == "True" ]]; then
    ok "Google OAuth ENABLED in Supabase"
  else
    fail "Google OAuth disabled or undetectable in Supabase (got: ${GOOGLE_ON}) — enable at ${SUPABASE_URL}/auth/providers"
  fi

  if [[ "$EMAIL_ON" == "True" ]]; then
    ok "Email signup ENABLED in Supabase"
  else
    fail "Email signup disabled or undetectable in Supabase (got: ${EMAIL_ON})"
  fi
else
  fail "Google OAuth check skipped — Supabase env vars not set"
  fail "Email signup check skipped — Supabase env vars not set"
fi

# ── Check 12: CORS preflight for Replit dev domain ───────────────────────────
echo "[12/13] CORS preflight for Replit dev domain …"
if [[ -n "$REPLIT_DEV" ]]; then
  ORIGIN="https://${REPLIT_DEV}"
else
  ORIGIN="https://localhost:5000"
fi
CORS_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 \
  -X OPTIONS "${BACKEND_URL}/api/health" \
  -H "Origin: ${ORIGIN}" \
  -H "Access-Control-Request-Method: GET" 2>/dev/null || echo "000")
if [[ "$CORS_CODE" == "200" || "$CORS_CODE" == "204" ]]; then
  ok "CORS preflight accepted for ${ORIGIN} → HTTP ${CORS_CODE}"
else
  fail "CORS preflight → HTTP ${CORS_CODE} for ${ORIGIN} (expected 200/204)"
fi

# ── Check 13: Sync VITE_* vars to Cloudflare Pages ──────────────────────────
echo "[13/13] Syncing VITE_* vars to Cloudflare Pages …"
CF_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
if [[ -z "$CF_TOKEN" ]]; then
  fail "CF Pages sync skipped — add CLOUDFLARE_API_TOKEN to Replit Secrets, then re-run"
elif [[ -z "$SUPABASE_URL" || -z "$SUPABASE_ANON_KEY" ]]; then
  fail "CF Pages sync skipped — VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY not set"
else
  PATCH_BODY=$(python3 -c "
import json, os
env = {
  'VITE_SUPABASE_URL':      {'value': os.environ.get('VITE_SUPABASE_URL',''), 'type': 'plain_text'},
  'VITE_SUPABASE_ANON_KEY': {'value': os.environ.get('VITE_SUPABASE_ANON_KEY',''), 'type': 'plain_text'},
  'VITE_SITE_URL':          {'value': os.environ.get('VITE_SITE_URL', 'https://syrabit.ai'), 'type': 'plain_text'},
}
print(json.dumps({'deployment_configs': {'production': {'env_vars': env}, 'preview': {'env_vars': env}}}))")

  CF_RESP=$(curl -sf --max-time 15 \
    -X PATCH \
    "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/pages/projects/${CF_PAGES_PROJECT}" \
    -H "Authorization: Bearer ${CF_TOKEN}" \
    -H "Content-Type: application/json" \
    --data "$PATCH_BODY" 2>/dev/null || echo '{"success":false}')

  CF_OK=$(echo "$CF_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('success',False))" 2>/dev/null || echo "False")
  if [[ "$CF_OK" == "True" ]]; then
    ok "VITE_SUPABASE_URL + VITE_SUPABASE_ANON_KEY + VITE_SITE_URL synced to CF Pages (${CF_PAGES_PROJECT})"
  else
    ERR=$(echo "$CF_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('errors',['unknown']))" 2>/dev/null || echo "parse error")
    fail "CF Pages sync FAILED: ${ERR}"
  fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "============================================="
TOTAL=$((PASS+FAIL))
echo " Results: ${PASS}/${TOTAL} passing"
echo "============================================="
for r in "${RESULTS[@]}"; do echo "$r"; done
echo ""

if [[ $FAIL -gt 0 ]]; then
  echo "Action needed for the $FAIL failing check(s) above."
  exit 1
else
  echo "All checks passed — auth is fully wired for local dev."
fi
