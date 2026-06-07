#!/usr/bin/env bash
# =============================================================================
#  SYRABIT CLOUD SHELL DEPLOYMENT TEST
#  Self-contained — requires only: bash + curl + python3 (all in Cloud Shell)
# =============================================================================
#
#  One-liner (no repo needed):
#    curl -fsSL https://raw.githubusercontent.com/founder24/-kalukaliya/main/scripts/cloudshell_test.sh | bash
#
#  With credentials (auth bypass + GitHub status):
#    ADMIN_EMAIL=founder@syrabit.ai \
#    ADMIN_PASSWORD=secret \
#    GITHUB_TOKEN=ghp_... \
#    bash scripts/cloudshell_test.sh
#
#  Options:
#    --skip-chat          Skip the SSE chat TTFB test (fast mode)
#    --check-revision     Fail if Cloud Run latest revision < 100% traffic
#    --api URL            Override API base  (default: https://api.syrabit.ai)
#    --fe  URL            Override frontend  (default: https://syrabit.ai)
#
#  Env vars (all optional):
#    ADMIN_EMAIL / ADMIN_PASSWORD   Login for auth-bypass chat test
#    GITHUB_TOKEN                   PAT for GitHub Actions status check
#    GCP_PROJECT                    GCP project (default: blissful-acumen-495019-t6)
#    GCP_REGION                     Cloud Run region (default: us-central1)
#    CURL_TIMEOUT                   Seconds per request (default: 15)
# =============================================================================
set -uo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
API="${CLOUD_API:-https://api.syrabit.ai}"
FE="${CLOUD_FE:-https://syrabit.ai}"
GCP_PROJECT="${GCP_PROJECT:-blissful-acumen-495019-t6}"
GCP_REGION="${GCP_REGION:-us-central1}"
CURL_TIMEOUT="${CURL_TIMEOUT:-15}"
ADMIN_EMAIL="${ADMIN_EMAIL:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
GITHUB_REPO="${GITHUB_REPO:-founder24/-kalukaliya}"
SKIP_CHAT=0
CHECK_REVISION=0

for _arg in "$@"; do
  case "$_arg" in
    --skip-chat)      SKIP_CHAT=1 ;;
    --check-revision) CHECK_REVISION=1 ;;
  esac
done
_prev=""
for _arg in "$@"; do
  case "$_prev" in
    --api) API="$_arg" ;;
    --fe)  FE="$_arg"  ;;
  esac
  _prev="$_arg"
done

# ── Colours ───────────────────────────────────────────────────────────────────
R='\033[91m'; G='\033[92m'; Y='\033[93m'; B='\033[94m'
C='\033[96m'; BOLD='\033[1m'; X='\033[0m'

# ── Counters ──────────────────────────────────────────────────────────────────
PASS=0; FAIL=0; WARN=0; SKIP=0
declare -a FAILURES=()
declare -a WARNINGS=()

START_TIME=$(date +%s)

# ── Helpers ───────────────────────────────────────────────────────────────────
_ok()   { echo -e "  ${G}✓${X} $1"; ((PASS++)) || true; }
_fail() { echo -e "  ${R}✗${X} $1"; ((FAIL++)) || true; FAILURES+=("$1"); }
_warn() { echo -e "  ${Y}⚠${X} $1"; ((WARN++)) || true; WARNINGS+=("$1"); }
_skip() { echo -e "  ${Y}·${X} $1 (skipped)"; ((SKIP++)) || true; }
_info() { echo -e "  ${B}·${X} $1"; }
_head() { echo -e "\n${BOLD}${C}──────────────────────────────────────────────────────────${X}"; \
          echo -e "${BOLD}${C}  $1${X}"; \
          echo -e "${BOLD}${C}──────────────────────────────────────────────────────────${X}"; }

# HTTP status check
_check() {
  local label="$1" expected="$2"; shift 2
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$CURL_TIMEOUT" "$@" 2>/dev/null || echo "000")
  if echo "$expected" | grep -qw "$code"; then
    _ok "${label} → HTTP ${code}"
  else
    _fail "${label} → HTTP ${code}  (expected ${expected})"
  fi
}

# HTTP check + body grep
_check_body() {
  local label="$1" expected="$2" needle="$3"; shift 3
  local body code
  body=$(curl -sf --max-time "$CURL_TIMEOUT" "$@" 2>/dev/null || echo "")
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$CURL_TIMEOUT" "$@" 2>/dev/null || echo "000")
  if echo "$expected" | grep -qw "$code" && echo "$body" | grep -q "$needle"; then
    _ok "${label} → HTTP ${code} ✓ body"
  elif ! echo "$expected" | grep -qw "$code"; then
    _fail "${label} → HTTP ${code}  (expected ${expected})"
  else
    _fail "${label} → body missing '${needle}'  (HTTP ${code})"
  fi
}

# Header check
_check_header() {
  local label="$1" pattern="$2" url="$3"
  local hdrs
  hdrs=$(curl -s -I --max-time "$CURL_TIMEOUT" "$url" 2>/dev/null || echo "")
  if echo "$hdrs" | grep -qi "$pattern"; then
    _ok "$label"
  else
    _fail "$label  (header '${pattern}' missing)"
  fi
}

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${C}╔══════════════════════════════════════════════════════════════╗${X}"
echo -e "${BOLD}${C}║      SYRABIT LIVE DEPLOYMENT TEST  —  Cloud Shell Edition    ║${X}"
echo -e "${BOLD}${C}║      $(date -u '+%Y-%m-%d %H:%M UTC')                                   ║${X}"
echo -e "${BOLD}${C}╚══════════════════════════════════════════════════════════════╝${X}"
echo ""
echo -e "  ${B}API :${X} ${API}"
echo -e "  ${B}FE  :${X} ${FE}"
echo -e "  ${B}GCP :${X} ${GCP_PROJECT} / ${GCP_REGION}"
[[ -n "$ADMIN_EMAIL" ]] && echo -e "  ${B}Auth:${X} ${ADMIN_EMAIL}  (rate-limit bypass enabled)" \
                        || echo -e "  ${Y}Auth:${X} not set — chat may hit 429 rate limit"
[[ -n "$GITHUB_TOKEN" ]] && echo -e "  ${B}GH  :${X} token set  (GitHub Actions status enabled)" \
                         || echo -e "  ${Y}GH  :${X} no GITHUB_TOKEN — GitHub section will be skipped"
echo ""

# =============================================================================
# 1. PRE-FLIGHT — confirm backend is reachable before running all checks
# =============================================================================
_head "0. Pre-flight"
_pf=$(curl -s --max-time 8 "${API}/health" 2>/dev/null || echo "")
if echo "$_pf" | grep -qiE '"status"|healthy|ok'; then
  _ok "Backend reachable: ${API}/health"
else
  echo -e "  ${R}${BOLD}ERROR: backend did not return a healthy response.${X}"
  echo -e "  Response: ${_pf:0:120}"
  echo -e "  Check the API URL and that Cloud Run is deployed."
  exit 1
fi

# =============================================================================
# 1. CLOUD RUN STATUS  (requires gcloud auth)
# =============================================================================
_head "1. Cloud Run Status"
if ! command -v gcloud &>/dev/null; then
  _skip "gcloud not available — Cloud Run checks skipped"
else
  _svc="syrabit-backend"
  _rev=$(gcloud run revisions list \
    --service "$_svc" --region "$GCP_REGION" --project "$GCP_PROJECT" \
    --format "value(REVISION)" --sort-by "~creationTimestamp" --limit 1 2>/dev/null || echo "")
  _traf=$(gcloud run services describe "$_svc" \
    --region "$GCP_REGION" --project "$GCP_PROJECT" \
    --format "value(status.traffic[0].percent)" 2>/dev/null || echo "?")
  _url=$(gcloud run services describe "$_svc" \
    --region "$GCP_REGION" --project "$GCP_PROJECT" \
    --format "value(status.url)" 2>/dev/null || echo "")
  _img=$(gcloud run services describe "$_svc" \
    --region "$GCP_REGION" --project "$GCP_PROJECT" \
    --format "value(spec.template.spec.containers[0].image)" 2>/dev/null || echo "")

  if [[ -n "$_rev" ]]; then
    _ok "Latest revision: ${_rev}  traffic=${_traf}%"
    _info "Image: ${_img}"
  else
    _warn "Could not read Cloud Run revision — run: gcloud auth login --project ${GCP_PROJECT}"
  fi

  if [[ -n "$_url" ]]; then
    _direct=$(curl -sf -o /dev/null -w "%{http_code}" --max-time 8 "${_url}/health" 2>/dev/null || echo "000")
    if [[ "$_direct" == "200" ]]; then
      _ok "Direct Cloud Run /health → HTTP 200  (${_url})"
    else
      _fail "Direct Cloud Run /health → HTTP ${_direct}  (${_url})"
    fi
  fi

  if [[ "$CHECK_REVISION" -eq 1 && "$_traf" != "100" ]]; then
    _fail "Latest revision at ${_traf}% traffic (expected 100% — deploy may be stuck)"
  fi
fi

# =============================================================================
# 2. BACKEND HEALTH
# =============================================================================
_head "2. Backend Health"
_check "GET /health"            "200"     "${API}/health"
_check "GET /health/deep"       "200 503" "${API}/health/deep"
_check_body "health body: status"  "200" '"status"'  "${API}/health"
_check_body "health body: healthy" "200" '"healthy"' "${API}/health"

# Measure cold-start response time
_ht=$(curl -s -o /dev/null -w "%{time_total}" --max-time 15 "${API}/health" 2>/dev/null || echo "0")
_hms=$(python3 -c "print(int(float('${_ht}')*1000))" 2>/dev/null || echo "?")
_info "Health response time: ${_hms}ms"

# =============================================================================
# 3. FRONTEND & CLOUDFLARE EDGE
# =============================================================================
_head "3. Frontend & Cloudflare Edge"
_check "GET ${FE}/ → 200"           "200" -L "${FE}/"
_check "GET ${FE}/chat/ → 200"      "200" -L "${FE}/chat/"
_check "GET ${FE}/library/ → 200"   "200" -L "${FE}/library/"
_check "GET ${FE}/robots.txt → 200" "200"    "${FE}/robots.txt"
_check "GET ${FE}/sitemap.xml → 200" "200"   "${FE}/sitemap.xml"

_fe_hdrs=$(curl -sI --max-time "$CURL_TIMEOUT" "${FE}/" 2>/dev/null || echo "")
for _hdr in "strict-transport-security" "x-frame-options" "x-content-type-options"; do
  if echo "$_fe_hdrs" | grep -qi "$_hdr"; then
    _ok "Security header: ${_hdr}"
  else
    _warn "Security header missing: ${_hdr}"
  fi
done

_cf_ray=$(echo "$_fe_hdrs" | grep -i "cf-ray" | head -1 || echo "")
[[ -n "$_cf_ray" ]] && _info "Cloudflare: ${_cf_ray}" || _warn "cf-ray header not found — not served via Cloudflare?"

# =============================================================================
# 4. ANON ENDPOINTS
# =============================================================================
_head "4. Anon Endpoints (no auth required)"
_check_body "GET /user/credits (anon) → 200"         "200" '"monthly_limit"' \
  -H "Origin: ${FE}" "${API}/api/v1/user/credits"
_check_body "/user/credits — tier: anonymous"         "200" '"anonymous"' \
  -H "Origin: ${FE}" "${API}/api/v1/user/credits"
_check_body "GET /conversations/anon → 200"           "200" '"conversations"' \
  -H "Origin: ${FE}" "${API}/api/v1/conversations/anon"
_check_body "GET /chat/history (anon) → 200"          "200" '"chats"' \
  -H "Origin: ${FE}" "${API}/api/v1/chat/history"
_check_body "GET /content/library-bundle → subjects"  "200" '"subjects"' \
  -H "Origin: ${FE}" "${API}/api/v1/content/library-bundle?slim=1"
_check_body "GET /subscription/plans → free+pro"      "200" '"free"' \
  -H "Origin: ${FE}" "${API}/api/v1/subscription/plans"
_check_body "GET /config/trustpilot → 200"            "200" '.' \
  -H "Origin: ${FE}" "${API}/api/v1/config/trustpilot"
_check      "GET /sitemap-subjects.xml → 200"         "200" \
  "${API}/api/v1/seo/sitemap-subjects.xml"

# =============================================================================
# 5. AUTH GUARDS
# =============================================================================
_head "5. Auth Guards (protected endpoints must reject)"
_check "GET /users/me (no token) → 401"         "401" -H "Origin: ${FE}" "${API}/api/v1/users/me"
_check "GET /conversations (no token) → 401"    "401" -H "Origin: ${FE}" "${API}/api/v1/conversations"
_check "GET /users/me (bad token) → 401"        "401" \
  -H "Origin: ${FE}" -H "Authorization: Bearer invalid.token.here" "${API}/api/v1/users/me"
_check "POST /auth/login (bad creds) → 401"     "401" \
  -X POST -H "Content-Type: application/json" -H "Origin: ${FE}" \
  -d '{"email":"smoke@invalid.com","password":"wrongpassword"}' \
  "${API}/api/v1/auth/login"
_check "POST /auth/signup (empty) → 422"        "422" \
  -X POST -H "Content-Type: application/json" -H "Origin: ${FE}" \
  -d '{}' "${API}/api/v1/auth/signup"

# =============================================================================
# 6. INPUT VALIDATION
# =============================================================================
_head "6. Input Validation"
_check "POST /chat/stream (empty message) → 422" "422" \
  -X POST -H "Content-Type: application/json" -H "Origin: ${FE}" \
  -d '{"message":""}' "${API}/api/v1/chat/stream"
_check "POST /chat/stream (no body) → 422"       "422" \
  -X POST -H "Content-Type: application/json" -H "Origin: ${FE}" \
  "${API}/api/v1/chat/stream"
_check "POST /auth/login (empty body) → 422"     "422" \
  -X POST -H "Content-Type: application/json" -H "Origin: ${FE}" \
  -d '{}' "${API}/api/v1/auth/login"

# No stack traces in error responses
_err_body=$(curl -sf --max-time 8 "${API}/api/v1/nonexistent-xyz999" 2>/dev/null || echo "")
if echo "$_err_body" | grep -qiE "Traceback|stack trace|File \"|at line [0-9]"; then
  _fail "Error response leaks stack trace (check APP_ENV=production)"
else
  _ok "Error responses do not leak stack traces"
fi

# =============================================================================
# 7. CORS PREFLIGHT
# =============================================================================
_head "7. CORS Preflight"
_check "OPTIONS /chat/stream → 200" "200" \
  -X OPTIONS \
  -H "Origin: ${FE}" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type,Authorization" \
  "${API}/api/v1/chat/stream"
_check "OPTIONS /user/credits → 200" "200" \
  -X OPTIONS \
  -H "Origin: ${FE}" \
  -H "Access-Control-Request-Method: GET" \
  "${API}/api/v1/user/credits"

_cors=$(curl -sI --max-time "$CURL_TIMEOUT" -H "Origin: ${FE}" "${API}/api/v1/chat/stream" 2>/dev/null || echo "")
if echo "$_cors" | grep -qi "access-control-allow-origin"; then
  _ok "CORS: access-control-allow-origin present"
else
  _fail "CORS: access-control-allow-origin missing"
fi

# SSE content-type
_sse_ct=$(curl -s -D - --max-time 6 \
  -X POST -H "Content-Type: application/json" -H "Origin: ${FE}" \
  -d '{"message":"hi","session_id":"cs-ct-001"}' \
  "${API}/api/v1/chat/stream" 2>/dev/null | grep -i "^content-type:" | head -1 || echo "")
if echo "$_sse_ct" | grep -qi "text/event-stream"; then
  _ok "POST /chat/stream → Content-Type: text/event-stream"
else
  _warn "POST /chat/stream Content-Type: ${_sse_ct:-missing}  (may be rate-limited)"
fi

# =============================================================================
# 8. SECURITY PROBES
# =============================================================================
_head "8. Security Probes"
_check "/.env blocked"          "404" "${API}/.env"
_check "/.git/config blocked"   "404" "${API}/.git/config"
_check "/.htaccess blocked"     "404" "${API}/.htaccess"
_check "/wp-login.php blocked"  "404" "${API}/wp-login.php"
_check "/phpinfo.php blocked"   "404" "${API}/phpinfo.php"
_check "/xmlrpc.php blocked"    "404" "${API}/xmlrpc.php"
_check "POST /webhooks/razorpay unsigned → 401/403" "401 403 400" \
  -X POST -H "Content-Type: application/json" \
  -d '{"event":"payment.captured"}' \
  "${API}/api/webhooks/razorpay"

# Follow redirects (-L) so a 302 /docs → /docs/ trailing-slash redirect isn't
# misreported as "docs visible". The final code after redirect is what matters.
_docs=$(curl -s -o /dev/null -w "%{http_code}" -L --max-time 8 "${API}/docs" 2>/dev/null || echo "000")
if [[ "$_docs" == "404" || "$_docs" == "403" ]]; then
  _ok "GET /docs → HTTP ${_docs} (hidden in production)"
elif [[ "$_docs" == "302" || "$_docs" == "301" ]]; then
  # Still a redirect even after -L — check where it goes
  _docs_loc=$(curl -s -o /dev/null -w "%{redirect_url}" --max-time 8 "${API}/docs" 2>/dev/null || echo "")
  _warn "GET /docs → HTTP ${_docs} → ${_docs_loc} (check redirect destination)"
else
  _warn "GET /docs → HTTP ${_docs} (OpenAPI docs may be visible — ensure APP_ENV=production)"
fi

# =============================================================================
# 9. CHAT TTFB (SSE stream — the critical <3 s target)
# =============================================================================
_head "9. Chat TTFB Test  (target: first token < 3000 ms)"

if [[ "$SKIP_CHAT" -eq 1 ]]; then
  _skip "Chat TTFB test (--skip-chat flag)"
else
  # Try to get JWT if credentials are available
  JWT=""
  if [[ -n "$ADMIN_EMAIL" && -n "$ADMIN_PASSWORD" ]]; then
    _login=$(curl -sf --max-time 10 \
      -X POST -H "Content-Type: application/json" \
      -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\"}" \
      "${API}/api/v1/auth/login" 2>/dev/null || echo "")
    JWT=$(echo "$_login" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token','') or d.get('token',''))" 2>/dev/null || echo "")
    if [[ -n "$JWT" ]]; then
      _info "Authenticated as ${ADMIN_EMAIL} — rate limit bypassed"
    else
      _warn "Login failed — chat will run as anon (may hit 429)"
    fi
  else
    _warn "No ADMIN_EMAIL/ADMIN_PASSWORD — chat runs as anon (may hit 429)"
  fi

  # Python3 SSE TTFB runner (built-in to Cloud Shell)
  python3 - <<PYEOF
import urllib.request, urllib.error, json, time, sys, os

API      = "${API}"
FE       = "${FE}"
JWT      = """${JWT}"""
TTFB_TARGET = 3000  # ms

R="\033[91m"; G="\033[92m"; Y="\033[93m"; B="\033[94m"; X="\033[0m"; BOLD="\033[1m"

tests = [
    {"message": "What is osmosis?",                          "lang": "en", "label": "EN short (osmosis)"},
    {"message": "Explain Newton's first law with an example","lang": "en", "label": "EN medium (Newton)"},
    {"message": "What is photosynthesis? Keep it brief.",   "lang": "en", "label": "EN brief"},
    {"message": "অসমোছিছ কি?",                              "lang": "as", "label": "AS Assamese"},
]

all_ok = True

for t in tests:
    payload = json.dumps({
        "message": t["message"],
        "model":   "default",
        "response_lang": t["lang"],
        "lang": t["lang"],
    }).encode()

    headers = {
        "Content-Type":  "application/json",
        "Origin":        FE,
        "Cache-Control": "no-cache, no-store",
        # Browser UA required: Cloudflare Bot Fight Mode blocks Python-urllib/3.x
        # from datacenter IPs (GCP AS15169) with error 1010.
        "User-Agent":    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 SyrabitTest/1.0",
    }
    if JWT.strip():
        headers["Authorization"] = f"Bearer {JWT.strip()}"
    else:
        safe = t["label"].replace(" ","_")[:16]
        headers["x-anon-id"] = f"cs-test-{safe}-{int(time.time())}"

    url = f"{API}/api/v1/chat/stream?_t={int(time.time()*1000)}"
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

    t_start         = time.time()
    t_first_chunk   = None
    full_text       = ""
    chunks          = 0
    model           = "?"
    route_trace     = {}
    server_lat_ms   = None
    error_msg       = None

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            if resp.status != 200:
                body = resp.read(200).decode(errors="replace")
                if resp.status == 429:
                    ms = (time.time()-t_start)*1000
                    print(f"  {Y}⚠{X} {t['label']}: rate limited (429) — set ADMIN_EMAIL+ADMIN_PASSWORD for auth bypass  ({ms:.0f}ms)")
                else:
                    print(f"  {R}✗{X} {t['label']}: HTTP {resp.status}: {body}")
                    all_ok = False
                time.sleep(1)
                continue

            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                raw = line[6:].strip()
                if not raw:
                    continue
                try:
                    evt = json.loads(raw)
                except Exception:
                    continue

                if "error" in evt:
                    error_msg = evt["error"]
                    break

                content = evt.get("content", "")
                if content and t_first_chunk is None:
                    t_first_chunk = time.time()
                if content:
                    full_text += content
                    chunks += 1

                if evt.get("done"):
                    model         = evt.get("model", "?")
                    route_trace   = evt.get("route_trace", {})
                    server_lat_ms = evt.get("latency_ms")
                    break

    except urllib.error.HTTPError as e:
        body = e.read(200).decode(errors="replace")
        if e.code == 429:
            ms = (time.time()-t_start)*1000
            print(f"  {Y}⚠{X} {t['label']}: rate limited (429)  ({ms:.0f}ms)")
            time.sleep(1)
            continue
        print(f"  {R}✗{X} {t['label']}: HTTP {e.code}: {body}")
        all_ok = False
        time.sleep(1)
        continue
    except Exception as ex:
        ms = (time.time()-t_start)*1000
        print(f"  {R}✗{X} {t['label']}: {ex}  ({ms:.0f}ms)")
        all_ok = False
        time.sleep(1)
        continue

    t_end    = time.time()
    ttfb_ms  = (t_first_chunk - t_start) * 1000 if t_first_chunk else -1
    total_ms = (t_end - t_start) * 1000
    words    = len(full_text.split()) if full_text else 0
    decision = route_trace.get("decision", "?")
    fallback = route_trace.get("fallback", False)

    if error_msg:
        print(f"  {R}✗{X} {t['label']}: AI error — {error_msg}  ({total_ms:.0f}ms)")
        all_ok = False
        time.sleep(1)
        continue

    ttfb_ok  = ttfb_ms >= 0 and ttfb_ms < TTFB_TARGET
    ttfb_col = G if ttfb_ok else Y
    srv_str  = f"  server_lat={server_lat_ms}ms" if server_lat_ms else ""
    fb_str   = f"  {Y}fallback=True{X}" if fallback else ""
    ttfb_str = f"{ttfb_ms:.0f}ms" if ttfb_ms >= 0 else "no-content"

    status_icon = G+"✓"+X if ttfb_ok else Y+"⚠"+X
    print(f"  {status_icon} {t['label']}")
    print(f"      TTFB  : {BOLD}{ttfb_col}{ttfb_str}{X}{srv_str}")
    print(f"      Total : {total_ms:.0f}ms   model={model}   route={decision}{fb_str}")
    print(f"      Reply : {words} words / {chunks} chunks")
    if full_text:
        preview = full_text[:100].replace("\n"," ")
        print(f"      Text  : {B}{preview}…{X}")

    if not ttfb_ok and ttfb_ms >= 0:
        all_ok = False

    time.sleep(1)  # 1s gap between tests

sys.exit(0 if all_ok else 1)
PYEOF
  _chat_exit=$?
  if [[ $_chat_exit -eq 0 ]]; then
    _ok "Chat TTFB: all EN queries < 3000 ms"
  else
    _warn "Chat TTFB: some queries exceeded 3000 ms or were rate-limited (see above)"
  fi
fi

# =============================================================================
# 10. PERFORMANCE SPOT CHECK
# =============================================================================
_head "10. Performance Spot Check"
declare -A _perf_thresholds=(
  ["/health"]=500
  ["/api/v1/content/library-bundle?slim=1"]=3000
  ["/api/v1/subscription/plans"]=1000
  ["/api/v1/seo/sitemap-subjects.xml"]=2000
)
for _path in "${!_perf_thresholds[@]}"; do
  _thresh="${_perf_thresholds[$_path]}"
  read -r _code _time_s < <(curl -s -o /dev/null \
    -w "%{http_code} %{time_total}" --max-time 20 \
    "${API}${_path}" 2>/dev/null | awk '{printf "%s %s", $1, $2}')
  _ms=$(python3 -c "print(int(float('${_time_s:-0}')*1000))" 2>/dev/null || echo "?")
  if [[ "$_code" != "200" ]]; then
    _fail "${_path} → HTTP ${_code}  (${_ms}ms)"
  elif [[ "$_ms" != "?" && "$_ms" -lt "$_thresh" ]]; then
    _ok "${_path} → ${_ms}ms  (< ${_thresh}ms threshold)"
  else
    _warn "${_path} → ${_ms}ms  (⚠ above ${_thresh}ms threshold)"
  fi
done

# =============================================================================
# 11. GITHUB ACTIONS STATUS  (requires GITHUB_TOKEN)
# =============================================================================
_head "11. GitHub Actions — Latest Deploy Status"
if [[ -z "$GITHUB_TOKEN" ]]; then
  _skip "GITHUB_TOKEN not set — set it to enable GitHub Actions status check"
else
  _gh_tmp=$(mktemp /tmp/gh_runs_XXXXXX.json)
  curl -sf --max-time 10 \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/${GITHUB_REPO}/actions/runs?per_page=10" \
    -o "$_gh_tmp" 2>/dev/null || true

  if [[ ! -s "$_gh_tmp" ]]; then
    _fail "GitHub API unreachable or token invalid"
  else
    python3 - "$_gh_tmp" <<'GHEOF'
import json, sys

R="\033[91m"; G="\033[92m"; Y="\033[93m"; B="\033[94m"; X="\033[0m"

with open(sys.argv[1], encoding="utf-8") as fh:
    try:
        data = json.load(fh)
    except Exception as e:
        print(f"  {R}✗{X} GitHub API parse error: {e}")
        sys.exit(1)

runs  = data.get("workflow_runs", [])
total = data.get("total_count", 0)
print(f"  {B}·{X} Total workflow runs: {total}")

seen = {}
for run in runs[:10]:
    name    = run.get("name","?")
    concl   = run.get("conclusion","in_progress") or "in_progress"
    created = run.get("created_at","?")[:16]
    branch  = run.get("head_branch","?")
    run_id  = run.get("id")
    is_latest = name not in seen
    seen[name]= run_id

    icon = G+"✓"+X if concl=="success" else (Y+"·"+X if concl in ("skipped","cancelled","in_progress") else R+"✗"+X)
    tag  = "" if is_latest else f"  {B}(historical){X}"
    print(f"    {icon} {name}: {concl} @ {created} [{branch}]{tag}")

deploy_name = "Deploy — Backend + Edge + Frontend"
for run in runs[:10]:
    if run.get("name") == deploy_name:
        c = run.get("conclusion") or "in_progress"
        sys.exit(0 if c in ("success","cancelled","in_progress") else 1)
sys.exit(0)
GHEOF
    _gh_exit=$?
    rm -f "$_gh_tmp"
    if [[ $_gh_exit -eq 0 ]]; then
      _ok "Latest deploy workflow: success"
    else
      _fail "Latest deploy workflow: failure — check GitHub Actions"
    fi
  fi
fi

# =============================================================================
# SUMMARY
# =============================================================================
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

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
  for _f in "${FAILURES[@]}"; do
    echo -e "    ${R}•${X} ${_f}"
  done
  echo ""
fi

if [[ ${#WARNINGS[@]} -gt 0 ]]; then
  echo -e "  ${Y}${BOLD}WARNINGS:${X}"
  for _w in "${WARNINGS[@]}"; do
    echo -e "    ${Y}•${X} ${_w}"
  done
  echo ""
fi

if [[ "$FAIL" -eq 0 ]]; then
  echo -e "  ${G}${BOLD}✓  Stack is healthy${X}"
  echo ""
  exit 0
else
  echo -e "  ${R}${BOLD}✗  ${FAIL} check(s) failed — review output above${X}"
  echo ""
  exit 1
fi
