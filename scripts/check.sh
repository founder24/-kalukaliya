#!/usr/bin/env bash
# =============================================================================
#  SYRABIT — POST-DEPLOY CHECK
#  Single authoritative validation script for the full production stack.
#  Requires: bash, curl, python3  (all present in GCP Cloud Shell)
#
#  Usage (from ~/syrabit):
#    bash scripts/check.sh
#
#  With credentials (enables auth + chat tests, bypasses rate limits):
#    TEST_USER_EMAIL="you@syrabit.ai" \
#    TEST_USER_PASSWORD="yourpassword" \
#    GITHUB_TOKEN="ghp_..." \
#    bash scripts/check.sh
#
#  Options:
#    --skip-chat        Skip the streaming chat TTFB test
#    --skip-github      Skip GitHub Actions status check
#    --fast             Equivalent to --skip-chat --skip-github
#    --api  <url>       Override API base   (default: https://api.syrabit.ai)
#    --fe   <url>       Override frontend   (default: https://syrabit.ai)
#    --region <region>  Cloud Run region    (default: asia-south1)
# =============================================================================
set -uo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
API="${API:-https://api.syrabit.ai}"
FE="${FE:-https://syrabit.ai}"
GCP_PROJECT="${GCP_PROJECT:-blissful-acumen-495019-t6}"
GCP_REGION="${GCP_REGION:-asia-south1}"
GH_REPO="${GH_REPO:-founder24/-kalukaliya}"
TEST_USER_EMAIL="${TEST_USER_EMAIL:-}"
TEST_USER_PASSWORD="${TEST_USER_PASSWORD:-}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
TIMEOUT="${TIMEOUT:-15}"
SKIP_CHAT=0; SKIP_GH=0

_prev=""
for _a in "$@"; do
  case "$_prev" in --api) API="$_a" ;; --fe) FE="$_a" ;; --region) GCP_REGION="$_a" ;; esac
  case "$_a" in
    --skip-chat)   SKIP_CHAT=1 ;;
    --skip-github) SKIP_GH=1 ;;
    --fast)        SKIP_CHAT=1; SKIP_GH=1 ;;
  esac
  _prev="$_a"
done

# ── Colour codes ──────────────────────────────────────────────────────────────
R='\033[91m'; G='\033[92m'; Y='\033[93m'; B='\033[94m'
C='\033[96m'; BOLD='\033[1m'; X='\033[0m'

# ── Counters ──────────────────────────────────────────────────────────────────
PASS=0; FAIL=0; WARN=0; SKIP=0
FAILURES=(); WARNINGS=()
START=$(date +%s)

# ── Output helpers ────────────────────────────────────────────────────────────
_ok()   { echo -e "  ${G}✓${X} $1"; ((PASS++)) || true; }
_fail() { echo -e "  ${R}✗${X} $1"; ((FAIL++)) || true; FAILURES+=("$1"); }
_warn() { echo -e "  ${Y}⚠${X} $1"; ((WARN++)) || true; WARNINGS+=("$1"); }
_skip() { echo -e "  ${Y}·${X} $1"; ((SKIP++)) || true; }
_info() { echo -e "  ${B}·${X} $1"; }
_head() {
  echo ""
  echo -e "${BOLD}${C}──────────────────────────────────────────────────────────${X}"
  echo -e "${BOLD}${C}  $1${X}"
  echo -e "${BOLD}${C}──────────────────────────────────────────────────────────${X}"
}

# HTTP check: _http LABEL EXPECTED_CODES [curl-opts...]
_http() {
  local label="$1" expected="$2"; shift 2
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT" "$@" 2>/dev/null || echo "000")
  if [[ " $expected " == *" $code "* ]]; then
    _ok "${label} → HTTP ${code}"
  else
    _fail "${label} → HTTP ${code}  (expected ${expected})"
  fi
}

# HTTP check + body must contain needle
_http_body() {
  local label="$1" expected="$2" needle="$3"; shift 3
  local body code
  body=$(curl -sf --max-time "$TIMEOUT" "$@" 2>/dev/null || echo "")
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT" "$@" 2>/dev/null || echo "000")
  if [[ " $expected " == *" $code "* ]] && echo "$body" | grep -q "$needle"; then
    _ok "${label} → HTTP ${code} ✓ body"
  elif [[ " $expected " != *" $code "* ]]; then
    _fail "${label} → HTTP ${code}  (expected ${expected})"
  else
    _fail "${label} → body missing '${needle}'  (HTTP ${code})"
  fi
}

# Response time check: _perf PATH THRESHOLD_MS
_perf() {
  local path="$1" thresh="$2"
  local code ms_raw ms
  read -r code ms_raw < <(curl -s -o /dev/null \
    -w "%{http_code} %{time_total}" --max-time 20 "${API}${path}" 2>/dev/null || echo "000 0")
  ms=$(python3 -c "print(int(float('${ms_raw:-0}')*1000))" 2>/dev/null || echo "?")
  if [[ "$code" != "200" ]]; then
    _fail "${path} → HTTP ${code}  (${ms}ms)"
  elif [[ "$ms" != "?" && "$ms" -lt "$thresh" ]]; then
    _ok "${path} → ${ms}ms  (< ${thresh}ms)"
  else
    _warn "${path} → ${ms}ms  (⚠ above ${thresh}ms threshold)"
  fi
}

# =============================================================================
#  BANNER
# =============================================================================
echo ""
echo -e "${BOLD}${C}╔══════════════════════════════════════════════════════════════╗${X}"
echo -e "${BOLD}${C}║              SYRABIT PRODUCTION CHECK                       ║${X}"
printf "${BOLD}${C}║  %-60s║${X}\n" "$(date -u '+%Y-%m-%d %H:%M UTC')"
echo -e "${BOLD}${C}╚══════════════════════════════════════════════════════════════╝${X}"
echo ""
echo -e "  API : ${B}${API}${X}"
echo -e "  FE  : ${B}${FE}${X}"
[[ -n "$TEST_USER_EMAIL" ]] && echo -e "  User: ${B}${TEST_USER_EMAIL}${X}" \
                            || echo -e "  User: ${Y}not set — auth/chat tests will be anonymous${X}"
[[ -n "$GITHUB_TOKEN" ]]   && echo -e "  GH  : ${G}token set${X}" \
                            || echo -e "  GH  : ${Y}no token — GitHub Actions check disabled${X}"

# =============================================================================
#  0. PRE-FLIGHT
# =============================================================================
_head "0. Pre-flight"
if curl -sf --max-time 10 "${API}/health" >/dev/null 2>&1; then
  _ok "Backend reachable: ${API}/health"
else
  _fail "Backend unreachable: ${API}/health"
  echo -e "\n${R}${BOLD}  Cannot reach backend — aborting.${X}\n"
  exit 1
fi

# =============================================================================
#  1. CLOUD RUN STATUS
# =============================================================================
_head "1. Cloud Run Status"
if ! command -v gcloud &>/dev/null; then
  _skip "gcloud not found — skipping Cloud Run checks"
else
  _svc=$(gcloud run services describe syrabit-backend \
    --project="${GCP_PROJECT}" --region="${GCP_REGION}" \
    --format="value(status.latestReadyRevisionName)" 2>/dev/null || echo "")
  if [[ -z "$_svc" ]]; then
    _warn "Could not read Cloud Run revision — check: gcloud auth login"
  else
    _ok "Latest revision: ${_svc}"
    _traffic=$(gcloud run services describe syrabit-backend \
      --project="${GCP_PROJECT}" --region="${GCP_REGION}" \
      --format="value(status.traffic[0].percent)" 2>/dev/null || echo "?")
    [[ "$_traffic" == "100" ]] && _ok "Traffic: 100% on latest revision" \
                                || _warn "Traffic: ${_traffic}% on latest (split traffic active?)"
    _env=$(gcloud run services describe syrabit-backend \
      --project="${GCP_PROJECT}" --region="${GCP_REGION}" \
      --format="json(spec.template.spec.containers[0].env)" 2>/dev/null \
      | python3 -c "
import json,sys
try:
  d=json.load(sys.stdin)
  envs=d.get('spec',{}).get('template',{}).get('spec',{}).get('containers',[{}])[0].get('env',[])
  print(next((e.get('value','') for e in envs if e.get('name')=='APP_ENV'),''))
except: print('')
" 2>/dev/null || echo "")
    [[ "$_env" == "production" ]] && _ok "APP_ENV=production ✓" \
                                   || _warn "APP_ENV='${_env:-not set}'  →  fix: gcloud run services update syrabit-backend --update-env-vars=APP_ENV=production --region=${GCP_REGION} --project=${GCP_PROJECT}"
  fi
fi

# =============================================================================
#  2. BACKEND HEALTH
# =============================================================================
_head "2. Backend Health"
_http_body "GET /health"          "200" '"status"'  "${API}/api/v1/health"
_http_body "GET /health deep"     "200 503" '"mongodb"' "${API}/api/v1/health/deep"
_http      "GET /health/circuit-breakers" "200" "${API}/api/v1/health/circuit-breakers"

# Circuit breaker detail
_cb=$(curl -sf --max-time 10 "${API}/api/v1/health/circuit-breakers" 2>/dev/null || echo "{}")
_vertex_state=$(echo "$_cb" | python3 -c "
import sys,json
try:
  d=json.load(sys.stdin)
  breakers=d.get('circuit_breakers',d)
  v=breakers.get('vertex_ai',breakers.get('vertex',{}))
  print(v.get('state','unknown'))
except: print('unknown')
" 2>/dev/null || echo "unknown")
if [[ "$_vertex_state" == "CLOSED" || "$_vertex_state" == "closed" ]]; then
  _ok "Vertex AI circuit breaker: CLOSED (healthy)"
elif [[ "$_vertex_state" == "unknown" ]]; then
  _info "Circuit breaker state: ${_vertex_state}"
else
  _warn "Vertex AI circuit breaker: ${_vertex_state}  (English chat may be failing — check Gemini API key/quota)"
fi

# /docs visibility (follow redirects — 404/403 is correct in production)
_docs_code=$(curl -s -o /dev/null -w "%{http_code}" -L --max-time 8 "${API}/docs" 2>/dev/null || echo "000")
if [[ "$_docs_code" == "404" || "$_docs_code" == "403" ]]; then
  _ok "GET /docs → HTTP ${_docs_code} (hidden — APP_ENV=production ✓)"
else
  _warn "GET /docs → HTTP ${_docs_code}  (docs visible — set APP_ENV=production on Cloud Run)"
fi

# =============================================================================
#  3. FRONTEND & CLOUDFLARE EDGE
# =============================================================================
_head "3. Frontend & Cloudflare Edge"
_http "GET ${FE}/"           "200 301" "${FE}/"
_http "GET /manifest.json"   "200"     "${FE}/manifest.json"
_http "GET /robots.txt"      "200"     "${FE}/robots.txt"
_http "GET /sitemap.xml"     "200"     "${FE}/sitemap.xml"
_http "GET /chat/"           "200"     "${FE}/chat/"
_http "GET /library/"        "200"     "${FE}/library/"

# Security headers
_hdrs=$(curl -s -I --max-time 10 "${FE}/" 2>/dev/null || echo "")
for _hdr in "strict-transport-security" "x-frame-options" "x-content-type-options"; do
  echo "$_hdrs" | grep -qi "$_hdr" \
    && _ok "Security header: ${_hdr}" \
    || _warn "Security header missing: ${_hdr}"
done

_ray=$(echo "$_hdrs" | grep -i "^cf-ray:" | head -1 | tr -d '\r')
[[ -n "$_ray" ]] && _info "Cloudflare: ${_ray}"

# =============================================================================
#  4. ANONYMOUS API ENDPOINTS
# =============================================================================
_head "4. Anonymous API Endpoints"
_http_body "GET /user/credits (anon)"         "200" '"tier"'      "${API}/api/v1/user/credits"
_http_body "GET /user/credits tier:anonymous" "200" '"anonymous"' "${API}/api/v1/user/credits"
_http      "GET /conversations/anon"          "200"               "${API}/api/v1/conversations/anon"
_http      "GET /chat/history (anon)"         "200"               "${API}/api/v1/chat/history"
_http_body "GET /content/library-bundle"      "200" '"subjects"'  "${API}/api/v1/content/library-bundle"
_http_body "GET /subscription/plans"          "200" '"free"'      "${API}/api/v1/subscription/plans"
_http      "GET /config/trustpilot"           "200"               "${API}/api/v1/config/trustpilot"
_http      "GET /sitemap-subjects.xml"        "200"               "${API}/api/v1/seo/sitemap-subjects.xml"

# =============================================================================
#  5. AUTH GUARDS
# =============================================================================
_head "5. Auth Guards"
_http "GET /users/me (no token) → 401"       "401" "${API}/api/v1/users/me"
_http "GET /conversations (no token) → 401"  "401" "${API}/api/v1/conversations"
_http "GET /users/me (bad token) → 401"  "401" \
  -H "Authorization: Bearer invalid.jwt.token" "${API}/api/v1/users/me"
_http "POST /auth/login (bad creds) → 401"   "401" \
  -X POST -H "Content-Type: application/json" \
  -d '{"email":"nobody@example.com","password":"wrongpassword"}' \
  "${API}/api/v1/auth/login"

# =============================================================================
#  6. INPUT VALIDATION
# =============================================================================
_head "6. Input Validation"
_http "POST /chat/stream (empty message) → 422" "422" \
  -X POST -H "Content-Type: application/json" \
  -d '{"message":""}' "${API}/api/v1/chat/stream"
_http "POST /chat/stream (no body) → 422"       "422" \
  -X POST -H "Content-Type: application/json" \
  "${API}/api/v1/chat/stream"
_http "POST /auth/login (empty body) → 422"     "422" \
  -X POST -H "Content-Type: application/json" \
  -d '{}' "${API}/api/v1/auth/login"
_http "POST /auth/signup (empty) → 422"         "422" \
  -X POST -H "Content-Type: application/json" \
  -d '{}' "${API}/api/v1/auth/signup"

# No stack traces in errors
_err_body=$(curl -sf --max-time 10 \
  -X POST -H "Content-Type: application/json" -d '{}' \
  "${API}/api/v1/auth/login" 2>/dev/null || echo "")
echo "$_err_body" | grep -qi "traceback\|stack trace\|exception at" \
  && _fail "Error responses leak stack traces" \
  || _ok "Error responses do not leak stack traces"

# =============================================================================
#  7. CORS & SSE
# =============================================================================
_head "7. CORS & SSE"
_cors=$(curl -sI --max-time 10 \
  -X OPTIONS -H "Origin: ${FE}" -H "Access-Control-Request-Method: POST" \
  "${API}/api/v1/chat/stream" 2>/dev/null || echo "")
echo "$_cors" | grep -qi "access-control-allow-origin" \
  && _ok "CORS: access-control-allow-origin present" \
  || _fail "CORS: access-control-allow-origin missing"

_sse_ct=$(curl -s -D - --max-time 8 \
  -X POST -H "Content-Type: application/json" -H "Origin: ${FE}" \
  -d '{"message":"hi","session_id":"check-ct-01"}' \
  "${API}/api/v1/chat/stream" 2>/dev/null | grep -i "^content-type:" | head -1 || echo "")
echo "$_sse_ct" | grep -qi "text/event-stream" \
  && _ok "POST /chat/stream → Content-Type: text/event-stream" \
  || _warn "POST /chat/stream content-type unexpected: ${_sse_ct:-missing}"

# =============================================================================
#  8. SECURITY PROBES
# =============================================================================
_head "8. Security Probes"
_http "/.env blocked"          "404" "${API}/.env"
_http "/.git/config blocked"   "404" "${API}/.git/config"
_http "/.htaccess blocked"     "404" "${API}/.htaccess"
_http "/wp-login.php blocked"  "404" "${API}/wp-login.php"
_http "/phpinfo.php blocked"   "404" "${API}/phpinfo.php"
_http "/xmlrpc.php blocked"    "404" "${API}/xmlrpc.php"
_http "POST /webhooks/razorpay unsigned → 400/401" "400 401 403" \
  -X POST -H "Content-Type: application/json" \
  -d '{"event":"payment.captured"}' "${API}/api/webhooks/razorpay"

# =============================================================================
#  9. CHAT TTFB  (streaming — target: first token < 3 s)
# =============================================================================
_head "9. Chat TTFB  (target: first token < 3000 ms)"

if [[ "$SKIP_CHAT" -eq 1 ]]; then
  _skip "Chat TTFB skipped (--skip-chat)"
else
  JWT=""
  if [[ -n "$TEST_USER_EMAIL" && -n "$TEST_USER_PASSWORD" ]]; then
    _login_resp=$(curl -sf --max-time 12 \
      -X POST -H "Content-Type: application/json" \
      -d "{\"email\":\"${TEST_USER_EMAIL}\",\"password\":\"${TEST_USER_PASSWORD}\"}" \
      "${API}/api/v1/auth/login" 2>/dev/null || echo "")
    JWT=$(echo "$_login_resp" | python3 -c \
      "import sys,json; d=json.load(sys.stdin); print(d.get('access_token','') or d.get('token',''))" \
      2>/dev/null || echo "")
    [[ -n "$JWT" ]] && _info "Logged in as ${TEST_USER_EMAIL} — rate-limit bypassed" \
                    || _warn "Login failed (check email/password) — chat runs as anon"
  else
    _warn "No TEST_USER_EMAIL/TEST_USER_PASSWORD — chat runs as anon (may hit 429)"
  fi

python3 - <<PYEOF
import urllib.request, urllib.error, json, time, sys

API          = "${API}"
FE           = "${FE}"
JWT          = r"""${JWT}""".strip()
TTFB_TARGET  = 3000

R="\033[91m"; G="\033[92m"; Y="\033[93m"; B="\033[94m"; X="\033[0m"; BOLD="\033[1m"

TESTS = [
    {"msg": "What is osmosis?",                           "lang": "en", "label": "EN short (osmosis)"},
    {"msg": "Explain Newton's first law with an example", "lang": "en", "label": "EN medium (Newton)"},
    {"msg": "What is photosynthesis? One paragraph.",     "lang": "en", "label": "EN brief"},
    {"msg": "অসমোছিছ কি?",                               "lang": "as", "label": "AS Assamese"},
]

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 SyrabitCheck/2")

overall_ok = True

for t in TESTS:
    payload = json.dumps({"message": t["msg"], "lang": t["lang"]}).encode()
    hdrs    = {
        "Content-Type":  "application/json",
        "Origin":        FE,
        "Cache-Control": "no-cache, no-store",
        "User-Agent":    UA,
    }
    if JWT:
        hdrs["Authorization"] = f"Bearer {JWT}"
    else:
        hdrs["x-anon-id"] = f"check-{t['lang']}-{int(time.time())}"

    url = f"{API}/api/v1/chat/stream?_t={int(time.time()*1000)}"
    req = urllib.request.Request(url, data=payload, headers=hdrs, method="POST")

    t0 = time.time(); t_first = None; text = ""; chunks = 0
    model = "?"; route = "?"; srv_ms = None; err = None

    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            if r.status != 200:
                body = r.read(300).decode(errors="replace")
                if r.status == 429:
                    print(f"  {Y}⚠{X} {t['label']}: 429 rate-limited — set TEST_USER_EMAIL + TEST_USER_PASSWORD")
                else:
                    print(f"  {R}✗{X} {t['label']}: HTTP {r.status}: {body[:120]}")
                    overall_ok = False
                time.sleep(1); continue

            for raw in r:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                try:
                    evt = json.loads(line[6:])
                except Exception:
                    continue
                if "error" in evt:
                    err = evt["error"]; break
                content = evt.get("content", "")
                if content and t_first is None:
                    t_first = time.time()
                if content:
                    text += content; chunks += 1
                if evt.get("done"):
                    model  = evt.get("model", "?")
                    rt     = evt.get("route_trace", {})
                    route  = rt.get("decision", "?")
                    srv_ms = evt.get("latency_ms")
                    break

    except urllib.error.HTTPError as e:
        body = e.read(300).decode(errors="replace")
        if e.code == 429:
            print(f"  {Y}⚠{X} {t['label']}: 429 rate-limited")
        else:
            print(f"  {R}✗{X} {t['label']}: HTTP {e.code}: {body[:120]}")
            overall_ok = False
        time.sleep(1); continue
    except Exception as ex:
        ms = int((time.time()-t0)*1000)
        print(f"  {R}✗{X} {t['label']}: {ex}  ({ms}ms)")
        overall_ok = False; time.sleep(1); continue

    elapsed = int((time.time()-t0)*1000)
    ttfb    = int((t_first-t0)*1000) if t_first else -1
    words   = len(text.split()) if text else 0
    srv_str = f"  server_lat={srv_ms}ms" if srv_ms else ""

    if err:
        print(f"  {R}✗{X} {t['label']}: AI error — {err}  ({elapsed}ms)")
        overall_ok = False; time.sleep(1); continue

    ttfb_ok  = 0 <= ttfb < TTFB_TARGET
    col      = G if ttfb_ok else Y
    icon     = f"{G}✓{X}" if ttfb_ok else f"{Y}⚠{X}"
    ttfb_str = f"{ttfb}ms" if ttfb >= 0 else "no-content"

    print(f"  {icon} {BOLD}{t['label']}{X}")
    print(f"      TTFB : {BOLD}{col}{ttfb_str}{X}  total={elapsed}ms{srv_str}")
    print(f"      model={model}  route={route}  {words}w/{chunks}chunks")
    if text:
        print(f"      text : {B}{text[:90].replace(chr(10),' ')}…{X}")

    if not ttfb_ok:
        overall_ok = False
    time.sleep(1)

sys.exit(0 if overall_ok else 1)
PYEOF
  _chat_rc=$?
  if [[ $_chat_rc -eq 0 ]]; then
    _ok "Chat TTFB: all queries within 3000 ms target"
  else
    _warn "Chat TTFB: one or more queries slow or erroring — see details above"
  fi
fi

# =============================================================================
#  10. PERFORMANCE
# =============================================================================
_head "10. Performance Spot Check"
_perf "/api/v1/health"                       500
_perf "/api/v1/subscription/plans"           1000
_perf "/api/v1/seo/sitemap-subjects.xml"     2000
_perf "/api/v1/content/library-bundle?slim=1" 3000

# =============================================================================
#  11. GITHUB ACTIONS STATUS
# =============================================================================
_head "11. GitHub Actions — Latest Deploy Status"
if [[ "$SKIP_GH" -eq 1 ]]; then
  _skip "GitHub Actions check skipped (--skip-github or --fast)"
elif [[ -z "$GITHUB_TOKEN" ]]; then
  _skip "GITHUB_TOKEN not set — set it to enable this check"
else
  _gh_tmp=$(mktemp /tmp/gh_XXXXXX.json)
  curl -sf --max-time 12 \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/${GH_REPO}/actions/runs?per_page=12" \
    -o "$_gh_tmp" 2>/dev/null || true

  if [[ ! -s "$_gh_tmp" ]]; then
    _fail "GitHub API unreachable or GITHUB_TOKEN invalid"
  else
    python3 - "$_gh_tmp" <<'GHEOF'
import json, sys

R="\033[91m"; G="\033[92m"; Y="\033[93m"; B="\033[94m"; X="\033[0m"
DEPLOY_WF = "Deploy — Backend + Edge + Frontend"

with open(sys.argv[1]) as f:
    try: data = json.load(f)
    except Exception as e: print(f"  {R}✗{X} GitHub parse error: {e}"); sys.exit(1)

runs  = data.get("workflow_runs", [])
total = data.get("total_count", 0)
print(f"  {B}·{X} {total} total workflow runs tracked")

seen = set()
latest_deploy = None
for run in runs[:12]:
    name   = run.get("name", "?")
    concl  = run.get("conclusion") or "in_progress"
    ts     = run.get("created_at", "?")[:16]
    branch = run.get("head_branch", "?")
    first  = name not in seen
    seen.add(name)
    icon = G+"✓"+X if concl == "success" else \
           Y+"·"+X if concl in ("skipped", "cancelled", "in_progress", "neutral") else \
           R+"✗"+X
    hist = f"  {B}(historical){X}" if not first else ""
    print(f"    {icon} {name}: {concl} @ {ts} [{branch}]{hist}")
    if name == DEPLOY_WF and latest_deploy is None:
        latest_deploy = concl

if latest_deploy in (None, "success", "in_progress", "cancelled"):
    sys.exit(0)
else:
    sys.exit(1)
GHEOF
    _gh_rc=$?
    rm -f "$_gh_tmp"
    if [[ $_gh_rc -eq 0 ]]; then
      _ok "Latest deploy workflow: success (or in progress)"
    else
      _fail "Latest deploy workflow: FAILED — check GitHub Actions logs"
    fi
  fi
fi

# =============================================================================
#  SUMMARY
# =============================================================================
ELAPSED=$(( $(date +%s) - START ))
echo ""
echo -e "${BOLD}${C}╔══════════════════════════════════════════════════════════════╗${X}"
echo -e "${BOLD}${C}║                        SUMMARY                              ║${X}"
echo -e "${BOLD}${C}╚══════════════════════════════════════════════════════════════╝${X}"
echo ""
echo -e "  ${G}✓ PASS:${X}   ${PASS}"
echo -e "  ${R}✗ FAIL:${X}   ${FAIL}"
echo -e "  ${Y}⚠ WARN:${X}   ${WARN}"
echo -e "  ${Y}· SKIP:${X}   ${SKIP}"
echo -e "  ${B}⏱ Time:${X}   ${ELAPSED}s"
echo ""

if [[ ${#FAILURES[@]} -gt 0 ]]; then
  echo -e "  ${R}${BOLD}FAILURES:${X}"
  for _f in "${FAILURES[@]}"; do echo -e "    ${R}•${X} ${_f}"; done
  echo ""
fi
if [[ ${#WARNINGS[@]} -gt 0 ]]; then
  echo -e "  ${Y}${BOLD}WARNINGS:${X}"
  for _w in "${WARNINGS[@]}"; do echo -e "    ${Y}•${X} ${_w}"; done
  echo ""
fi

if [[ "$FAIL" -eq 0 ]]; then
  echo -e "  ${G}${BOLD}✓  Stack is healthy${X}"
  echo ""
  exit 0
else
  echo -e "  ${R}${BOLD}✗  ${FAIL} failure(s) — review output above${X}"
  echo ""
  exit 1
fi
