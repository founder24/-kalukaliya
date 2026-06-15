#!/usr/bin/env bash
# =============================================================================
#  SYRABIT — POST-DEPLOY VERIFICATION
#  Quick check (~30 s) to run immediately after each Cloud Run deployment.
#  Tests the exact flows that have historically failed in production.
#
#  Usage:
#    bash scripts/post-deploy-check.sh
#
#  With authenticated chat test:
#    ADMIN_EMAIL=you@syrabit.ai ADMIN_PASSWORD=yourpass \
#      bash scripts/post-deploy-check.sh
#
#  Env overrides:
#    API_URL         default: https://api.syrabit.ai
#    GCP_PROJECT     default: blissful-acumen-495019-t6
#    GCP_REGION      default: asia-south1
#    GCP_SERVICE     default: syrabit-backend
#    ADMIN_EMAIL     (optional) login email for auth chat test
#    ADMIN_PASSWORD  (optional) login password
# =============================================================================
set -euo pipefail

API_URL="${API_URL:-https://api.syrabit.ai}"
ORIGIN="${ORIGIN:-https://syrabit.ai}"
GCP_PROJECT="${GCP_PROJECT:-blissful-acumen-495019-t6}"
GCP_REGION="${GCP_REGION:-asia-south1}"
GCP_SERVICE="${GCP_SERVICE:-syrabit-backend}"
ADMIN_EMAIL="${ADMIN_EMAIL:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
TIMEOUT=20

# ── Colours ───────────────────────────────────────────────────────────────────
R='\033[91m'; G='\033[92m'; Y='\033[93m'; B='\033[94m'; C='\033[96m'
BOLD='\033[1m'; X='\033[0m'

PASS=0; FAIL=0; WARN=0
declare -a FAILURES=()

_ok()   { echo -e "  ${G}✓${X}  $1"; ((PASS++)) || true; }
_fail() { echo -e "  ${R}✗${X}  $1"; ((FAIL++)) || true; FAILURES+=("$1"); }
_warn() { echo -e "  ${Y}⚠${X}  $1"; ((WARN++)) || true; }
_info() { echo -e "     ${B}·${X}  $1"; }
_head() { echo -e "\n${BOLD}${C}── $1 ──${X}"; }

START=$(date +%s)

echo ""
echo -e "${BOLD}${C}╔══════════════════════════════════════════════════════╗${X}"
echo -e "${BOLD}${C}║     SYRABIT — POST-DEPLOY CHECK                      ║${X}"
echo -e "${BOLD}${C}║     $(date -u '+%Y-%m-%d %H:%M UTC')                          ║${X}"
echo -e "${BOLD}${C}╚══════════════════════════════════════════════════════╝${X}"
echo ""
echo -e "  ${B}API :${X} ${API_URL}"

# Helper: get HTTP status code
http_code() { curl -s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT" "$@" 2>/dev/null || echo "000"; }
# Helper: get response body
http_body() { curl -s --max-time "$TIMEOUT" "$@" 2>/dev/null || echo "{}"; }
# Helper: get headers
http_hdrs() { curl -sI --max-time "$TIMEOUT" "$@" 2>/dev/null || echo ""; }

# Helper: assert HTTP code
assert_code() {
  local label="$1" want="$2"; shift 2
  local got; got=$(http_code "$@")
  if echo "$want" | grep -qw "$got"; then
    _ok "${label} → HTTP ${got}"
  else
    _fail "${label} → HTTP ${got}  (expected ${want})"
  fi
}

# Helper: assert body contains JSON key
assert_json() {
  local label="$1" key="$2" want="$3"; shift 3
  local body; body=$(http_body "$@")
  local val; val=$(echo "$body" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('${key}',''))" 2>/dev/null || echo "")
  if [[ "$val" == "$want" ]]; then
    _ok "${label}"
  else
    _fail "${label}  (${key}='${val}', expected '${want}')"
  fi
}

# =============================================================================
# 0. CLOUD RUN — CURRENT REVISION
# =============================================================================
_head "0. Cloud Run revision"
if command -v gcloud &>/dev/null; then
  REV=$(gcloud run revisions list \
    --service "$GCP_SERVICE" --region "$GCP_REGION" --project "$GCP_PROJECT" \
    --sort-by="~metadata.creationTimestamp" --limit=1 \
    --format="value(metadata.name)" 2>/dev/null || echo "?")
  TRAFFIC=$(gcloud run services describe "$GCP_SERVICE" \
    --region "$GCP_REGION" --project "$GCP_PROJECT" \
    --format="value(status.traffic[0].percent)" 2>/dev/null || echo "?")
  CREATED=$(gcloud run revisions describe "$REV" \
    --region "$GCP_REGION" --project "$GCP_PROJECT" \
    --format="value(metadata.creationTimestamp)" 2>/dev/null || echo "?")
  if [[ "$TRAFFIC" == "100" ]]; then
    _ok "Revision ${REV} at 100% traffic"
  else
    _warn "Revision ${REV} at ${TRAFFIC}% traffic"
  fi
  _info "Deployed : ${CREATED}"
else
  _warn "gcloud not found — skipping revision check"
fi

# =============================================================================
# 1. BASIC HEALTH
# =============================================================================
_head "1. Basic health"

HEALTH=$(http_body "${API_URL}/api/v1/health" -H "Origin: ${ORIGIN}")
STATUS=$(echo "$HEALTH" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "?")
MONGO=$(echo "$HEALTH" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('mongodb_initialized','?'))" 2>/dev/null || echo "?")

if [[ "$STATUS" == "healthy" ]]; then
  _ok "/api/v1/health → status=healthy"
else
  _fail "/api/v1/health → status=${STATUS} (expected healthy)"
fi

if [[ "$MONGO" == "True" || "$MONGO" == "true" ]]; then
  _ok "mongodb_initialized=True"
else
  _fail "mongodb_initialized=${MONGO} — MongoDB is not connected"
fi

# Cloudflare is in front
CF_RAY=$(http_hdrs "${API_URL}/api/v1/health" -H "Origin: ${ORIGIN}" | grep -i "cf-ray" | head -1 || echo "")
if [[ -n "$CF_RAY" ]]; then
  _ok "CF-Ray header present  (CF Worker is proxying)"
  _info "${CF_RAY//[$'\r\n']/}"
else
  _warn "CF-Ray header missing — traffic may not be going through Cloudflare"
fi

# =============================================================================
# 2. FIX VERIFICATION — Auth Rate Limiting (MongoDB, not Redis)
#    Bug: _check_rate_limit was still importing app.db.redis (removed June 11)
#         → RuntimeError logged on every login/signup.
#    Fix: migrated to MongoDB auth_rate_limit collection with 90s TTL buckets.
# =============================================================================
_head "2. Auth rate-limit (MongoDB fix — must NOT log RuntimeError)"

TS=$(date +%s)
SIGNUP_BODY=$(curl -s --max-time "$TIMEOUT" \
  -X POST "${API_URL}/api/v1/auth/signup" \
  -H "Content-Type: application/json" \
  -H "Origin: ${ORIGIN}" \
  -d "{\"email\":\"pdc_${TS}@verify.syrabit.ai\",\"password\":\"Test12345!\",\"name\":\"PDC\"}" \
  2>/dev/null || echo "{}")

SIGNUP_TOKEN=$(echo "$SIGNUP_BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null || echo "")
SIGNUP_ERR=$(echo "$SIGNUP_BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('detail',''))" 2>/dev/null || echo "")

if [[ -n "$SIGNUP_TOKEN" ]]; then
  _ok "POST /auth/signup → JWT issued  (rate-limit not throwing RuntimeError)"
  _info "Token prefix: ${SIGNUP_TOKEN:0:20}…"
elif echo "$SIGNUP_ERR" | grep -qi "already"; then
  _ok "POST /auth/signup → duplicate email (rate-limit ran without RuntimeError)"
else
  _fail "POST /auth/signup → unexpected response: ${SIGNUP_BODY:0:120}"
fi

# Also verify login (separate rate-limit counter)
LOGIN_CODE=$(http_code \
  -X POST "${API_URL}/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -H "Origin: ${ORIGIN}" \
  -d '{"email":"nonexistent_pdc@verify.syrabit.ai","password":"WrongPass1!"}')

if [[ "$LOGIN_CODE" == "401" || "$LOGIN_CODE" == "400" ]]; then
  _ok "POST /auth/login with bad creds → ${LOGIN_CODE}  (rate-limit did not error)"
else
  _fail "POST /auth/login → unexpected HTTP ${LOGIN_CODE} (expected 401)"
fi

# Check Cloud Run logs for RuntimeError (requires gcloud)
if command -v gcloud &>/dev/null; then
  RL_ERRS=$(gcloud logging read \
    "resource.type=cloud_run_revision AND resource.labels.service_name=${GCP_SERVICE} AND resource.labels.location=${GCP_REGION} AND jsonPayload.message=~\"Rate limiting unavailable\"" \
    --project "$GCP_PROJECT" --freshness=5m --limit=5 \
    --format="value(timestamp)" 2>/dev/null | wc -l | tr -d ' ')
  if [[ "${RL_ERRS:-0}" -eq 0 ]]; then
    _ok "Cloud Run logs: zero auth rate-limit RuntimeErrors in last 5 min"
  else
    _fail "Cloud Run logs: ${RL_ERRS} rate-limit RuntimeErrors in last 5 min"
  fi
fi

# =============================================================================
# 3. FIX VERIFICATION — Sarvam health (no misleading http:404 field)
#    Bug: sarvam_ping returned {"status":"healthy","http":404} — the 404 was
#         expected (base URL has no GET handler) but looked like an error.
#    Fix: removed `http` field from healthy responses.
# =============================================================================
_head "3. Sarvam health display (no http:404 in healthy response)"

DEEP_BODY=$(http_body "${API_URL}/api/v1/health/deep" -H "Origin: ${ORIGIN}" 2>/dev/null || echo "{}")
SARVAM_STATUS=$(echo "$DEEP_BODY" | python3 -c "
import json,sys
d=json.load(sys.stdin)
s=d.get('checks',{}).get('sarvam_ai',{})
print(s.get('status','not_found'))
" 2>/dev/null || echo "?")

SARVAM_HTTP=$(echo "$DEEP_BODY" | python3 -c "
import json,sys
d=json.load(sys.stdin)
s=d.get('checks',{}).get('sarvam_ai',{})
print(s.get('http','ABSENT'))
" 2>/dev/null || echo "ABSENT")

if [[ "$SARVAM_STATUS" == "healthy" ]]; then
  _ok "Sarvam health status=healthy"
else
  _warn "Sarvam health status=${SARVAM_STATUS}"
fi

if [[ "$SARVAM_HTTP" == "ABSENT" ]]; then
  _ok "Sarvam healthy response has no 'http' field  (fix verified)"
elif [[ "$SARVAM_HTTP" == "404" ]]; then
  _fail "Sarvam healthy response still contains http:404  (fix not active)"
else
  _warn "Sarvam response http field = '${SARVAM_HTTP}'  (unexpected)"
fi

# =============================================================================
# 4. AUTHENTICATED USER FLOW
# =============================================================================
_head "4. Authenticated user flow"

JWT="${SIGNUP_TOKEN}"

# If a fresh signup token is available, use it; else try admin login
if [[ -z "$JWT" && -n "$ADMIN_EMAIL" && -n "$ADMIN_PASSWORD" ]]; then
  LOGIN_BODY=$(http_body \
    -X POST "${API_URL}/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -H "Origin: ${ORIGIN}" \
    -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\"}")
  JWT=$(echo "$LOGIN_BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null || echo "")
fi

if [[ -n "$JWT" ]]; then
  ME_CODE=$(http_code "${API_URL}/api/v1/users/me" \
    -H "Authorization: Bearer ${JWT}" \
    -H "Origin: ${ORIGIN}")
  if [[ "$ME_CODE" == "200" ]]; then
    ME_BODY=$(http_body "${API_URL}/api/v1/users/me" \
      -H "Authorization: Bearer ${JWT}" \
      -H "Origin: ${ORIGIN}")
    ME_ROLE=$(echo "$ME_BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('role','?'))" 2>/dev/null || echo "?")
    _ok "GET /users/me → 200  (role=${ME_ROLE})"
  else
    _fail "GET /users/me → HTTP ${ME_CODE}  (expected 200)"
  fi

  # Quick chat stream TTFB
  TTFB_OUTPUT=$(python3 - <<PYEOF 2>/dev/null || echo "ERROR"
import urllib.request, json, time, sys

url = "${API_URL}/api/v1/chat/stream"
payload = json.dumps({
    "message": "What is 2+2? One word answer.",
    "lang": "en",
    "session_id": "pdc-verify-$(date +%s)",
}).encode()
headers = {
    "Content-Type": "application/json",
    "Origin": "${ORIGIN}",
    "Authorization": "Bearer ${JWT}",
    "User-Agent": "Mozilla/5.0 SyrabitPostDeployCheck/1.0",
}
req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
t_start = time.time()
t_first = None
text = ""
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        for raw in r:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data: "): continue
            try: evt = json.loads(line[6:])
            except: continue
            if evt.get("content") and t_first is None:
                t_first = time.time()
            if evt.get("content"):
                text += evt["content"]
            if evt.get("done"):
                break
    ttfb = (t_first - t_start) * 1000 if t_first else -1
    total = (time.time() - t_start) * 1000
    print(f"{ttfb:.0f}|{total:.0f}|{text.strip()[:40]}")
except Exception as e:
    print(f"ERROR:{e}")
PYEOF
)

  if echo "$TTFB_OUTPUT" | grep -q "^ERROR\|^$"; then
    _warn "Chat stream test skipped or failed: ${TTFB_OUTPUT}"
  else
    TTFB_MS=$(echo "$TTFB_OUTPUT" | cut -d'|' -f1)
    TOTAL_MS=$(echo "$TTFB_OUTPUT" | cut -d'|' -f2)
    REPLY=$(echo "$TTFB_OUTPUT" | cut -d'|' -f3)
    if python3 -c "exit(0 if int('${TTFB_MS}') < 3000 else 1)" 2>/dev/null; then
      _ok "Chat TTFB: ${TTFB_MS}ms  total: ${TOTAL_MS}ms"
    else
      _warn "Chat TTFB: ${TTFB_MS}ms  (target <3000ms)  total: ${TOTAL_MS}ms"
    fi
    _info "Reply: ${REPLY}…"
  fi
else
  _warn "No JWT available — authenticated chat test skipped"
  _info "Set ADMIN_EMAIL + ADMIN_PASSWORD, or check /auth/signup response above"
fi

# =============================================================================
# 5. SECURITY GUARDS
# =============================================================================
_head "5. Security guards"

assert_code "GET /users/me (no token) → 401"    "401" "${API_URL}/api/v1/users/me" -H "Origin: ${ORIGIN}"
assert_code "POST /chat/stream (empty) → 422"   "422" \
  -X POST -H "Content-Type: application/json" -H "Origin: ${ORIGIN}" \
  -d '{}' "${API_URL}/api/v1/chat/stream"
assert_code "GET /.env → 404"                   "404" "${API_URL}/.env"
assert_code "GET /docs (hidden in prod) → 403 404" "403 404" -L "${API_URL}/docs"

# Stack trace leak check
ERR_BODY=$(http_body "${API_URL}/api/v1/nonexistent-endpoint-xyz" -H "Origin: ${ORIGIN}")
if echo "$ERR_BODY" | grep -qiE "Traceback|File \"|at line [0-9]"; then
  _fail "Error response leaks Python traceback"
else
  _ok "Error responses do not leak stack traces"
fi

# =============================================================================
# 6. CORS
# =============================================================================
_head "6. CORS"

CORS_HDRS=$(http_hdrs \
  -X OPTIONS "${API_URL}/api/v1/chat/stream" \
  -H "Origin: ${ORIGIN}" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type,Authorization")

if echo "$CORS_HDRS" | grep -qi "access-control-allow-origin"; then
  _ok "access-control-allow-origin present"
else
  _fail "access-control-allow-origin missing on OPTIONS preflight"
fi

if echo "$CORS_HDRS" | grep -qi "syrabit.ai"; then
  _ok "CORS allows syrabit.ai"
else
  _fail "CORS does not include syrabit.ai"
fi

# Disallowed origin
BAD_CORS=$(http_hdrs "${API_URL}/api/v1/health" -H "Origin: https://evil.example.com" | \
  grep -i "access-control-allow-origin" | head -1 || echo "")
if echo "$BAD_CORS" | grep -qi "evil.example.com"; then
  _fail "CORS reflects unauthorized origin (evil.example.com)"
else
  _ok "CORS blocks unauthorized origin"
fi

# =============================================================================
# SUMMARY
# =============================================================================
ELAPSED=$(( $(date +%s) - START ))
echo ""
echo -e "${BOLD}${C}══════════════════════════════════════════════════════${X}"
echo -e "${BOLD}  Post-Deploy Check — Summary${X}"
echo -e "${BOLD}${C}══════════════════════════════════════════════════════${X}"
printf "  ${G}✓ Pass  :${X} %d\n"  "$PASS"
printf "  ${Y}⚠ Warn  :${X} %d\n"  "$WARN"
printf "  ${R}✗ Fail  :${X} %d\n"  "$FAIL"
printf "  Elapsed : %ds\n"          "$ELAPSED"

if [[ "${#FAILURES[@]}" -gt 0 ]]; then
  echo ""
  echo -e "  ${BOLD}${R}Failures:${X}"
  for f in "${FAILURES[@]}"; do
    echo -e "    ${R}✗${X} ${f}"
  done
fi

echo ""
if [[ "$FAIL" -gt 0 ]]; then
  echo -e "  ${R}${BOLD}POST-DEPLOY CHECK FAILED — rollback may be needed${X}"
  echo -e "  ${R}  gcloud run services update-traffic ${GCP_SERVICE} --to-revisions=PREV_REV=100 --region=${GCP_REGION} --project=${GCP_PROJECT}${X}"
  exit 1
else
  echo -e "  ${G}${BOLD}POST-DEPLOY CHECK PASSED — revision is healthy${X}"
  exit 0
fi
