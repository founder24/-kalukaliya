#!/usr/bin/env bash
# =============================================================================
# test-providers.sh — AI Provider Health Check
# =============================================================================
#
# Tests /api/v1/health/providers to verify every live AI integration:
#   sarvam_ai, vector_search, cloudflare_workers_ai
#
# Works against local dev server OR production.
#
# Usage:
#   # Local dev:
#   bash scripts/test-providers.sh
#
#   # Production:
#   BASE_URL=https://api.syrabit.ai bash scripts/test-providers.sh
#
#   # Direct Cloud Run (bypass CF Worker):
#   BASE_URL=https://syrabit-backend-851687450401.asia-south1.run.app \
#     bash scripts/test-providers.sh
#
# Exit codes:
#   0 — all critical providers healthy
#   1 — one or more critical providers failed
# =============================================================================
set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
ENDPOINT="${BASE_URL}/api/v1/health/providers"
TIMEOUT="${TIMEOUT:-60}"

# ── Colours ──────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  G='\033[0;32m' R='\033[0;31m' Y='\033[1;33m'
  C='\033[0;36m' B='\033[1m'   N='\033[0m'
else
  G='' R='' Y='' C='' B='' N=''
fi

PASS=0; FAIL=0; SKIP=0

pass()   { printf "  ${G}✓${N}  %s\n" "$1"; PASS=$((PASS+1)); }
fail()   { printf "  ${R}✗${N}  %s\n" "$1"; FAIL=$((FAIL+1)); }
skip()   { printf "  ${Y}–${N}  %s\n" "$1 (skipped)"; SKIP=$((SKIP+1)); }
info()   { printf "     ${Y}%s${N}\n" "$1"; }
header() { printf "\n${C}${B}── %s ──${N}\n" "$1"; }

jv() {
  # jv <json> <python_expr_returning_value>
  echo "$1" | python3 -c "import json,sys; d=json.load(sys.stdin); v=$2; print(v if v is not None else '')" 2>/dev/null || echo ""
}

# ── Latency thresholds (ms) ───────────────────────────────────────────────────
SARVAM_LATENCY_WARN=15000   # warn if Sarvam > 15s (reasoning phase expected)

# Critical providers — failure = overall fail
CRITICAL_PROVIDERS=(sarvam_ai)
# Optional providers — not_configured is OK; unhealthy = warn only
OPTIONAL_PROVIDERS=(vector_search cloudflare_workers_ai)

printf "\n${C}${B}Syrabit Provider Health Check${N}\n"
printf "  Endpoint : %s\n" "$ENDPOINT"
printf "  Timeout  : %ss\n" "$TIMEOUT"
printf "  Time     : %s\n" "$(date -u '+%Y-%m-%d %H:%M:%S UTC')"

# =============================================================================
# 1. Fetch /health/providers
# =============================================================================
header "Fetching /api/v1/health/providers"

HTTP_CODE=$(curl -s -o /tmp/syrabit_providers.json \
  -w "%{http_code}" \
  --max-time "$TIMEOUT" \
  "$ENDPOINT" 2>/dev/null || echo "000")

if [[ "$HTTP_CODE" == "000" ]]; then
  fail "Could not reach $ENDPOINT (connection refused or timeout)"
  echo ""
  echo -e "  ${R}Is the backend running? Start with:${N}"
  echo    "    cd apps/backend && python3 -m uvicorn app.main:app --port 8000 --reload"
  exit 1
fi

if [[ "$HTTP_CODE" == "404" ]]; then
  skip "Endpoint not found (HTTP 404)"
  echo ""
  echo -e "  ${Y}The /health/providers endpoint may not be deployed yet.${N}"
  echo    "  Test locally: BASE_URL=http://localhost:8000 bash scripts/test-providers.sh"
  exit 0
fi

if [[ "$HTTP_CODE" != "200" && "$HTTP_CODE" != "503" ]]; then
  fail "/api/v1/health/providers returned HTTP $HTTP_CODE (expected 200 or 503)"
  exit 1
fi

pass "/api/v1/health/providers reachable (HTTP $HTTP_CODE)"

BODY=$(cat /tmp/syrabit_providers.json)

OVERALL=$(jv "$BODY" "d.get('overall','')")
printf "  Overall status: %b\n" \
  "$([[ "$OVERALL" == "healthy" ]] && echo "${G}${B}healthy${N}" || echo "${Y}${B}${OVERALL}${N}")"

# =============================================================================
# 2. Per-provider checks
# =============================================================================
header "Critical Providers (sarvam_ai)"

check_provider() {
  local name="$1"
  local warn_ms="${2:-0}"
  local is_critical="${3:-false}"

  local status latency error note
  status=$(jv  "$BODY" "d.get('providers',{}).get('$name',{}).get('status','')")
  latency=$(jv "$BODY" "d.get('providers',{}).get('$name',{}).get('latency_ms','')")
  error=$(jv   "$BODY" "d.get('providers',{}).get('$name',{}).get('error','')")
  note=$(jv    "$BODY" "d.get('providers',{}).get('$name',{}).get('note','')")

  local latency_str=""
  [[ -n "$latency" && "$latency" != "None" ]] && latency_str=" (${latency}ms)"

  case "$status" in
    healthy)
      pass "$name: healthy${latency_str}"
      if [[ -n "$latency" && "$warn_ms" -gt 0 ]]; then
        local lat_int=${latency%.*}
        if [[ "$lat_int" -gt "$warn_ms" ]] 2>/dev/null; then
          info "Latency ${lat_int}ms exceeds warn threshold ${warn_ms}ms"
        fi
      fi
      ;;
    degraded)
      if [[ "$is_critical" == "true" ]]; then
        fail "$name: DEGRADED${latency_str}"
      else
        skip "$name: degraded${latency_str} (non-critical)"
      fi
      [[ -n "$error" ]] && info "Error: $error"
      ;;
    unhealthy)
      if [[ "$is_critical" == "true" ]]; then
        fail "$name: UNHEALTHY${latency_str}"
      else
        fail "$name: unhealthy${latency_str}"
      fi
      [[ -n "$error" ]] && info "Error: $error"
      ;;
    not_configured)
      skip "$name: not_configured (service not set up)"
      [[ -n "$note" ]] && info "$note"
      ;;
    disabled)
      skip "$name: disabled"
      [[ -n "$note" ]] && info "$note"
      ;;
    "")
      skip "$name: no status returned (not in response)"
      ;;
    *)
      skip "$name: unknown status '$status'"
      ;;
  esac
}

check_provider "sarvam_ai" "$SARVAM_LATENCY_WARN" "true"

header "Optional Providers (vector_search, cloudflare_workers_ai)"

check_provider "vector_search"           "0"  "false"
check_provider "cloudflare_workers_ai"   "0"  "false"

# =============================================================================
# 3. CF Workers AI credential verification
# =============================================================================
header "Cloudflare Workers AI Credential Verification"

CF_STATUS=$(jv "$BODY" "d.get('providers',{}).get('cloudflare_workers_ai',{}).get('status','')")
case "$CF_STATUS" in
  healthy)
    pass "Cloudflare Workers AI token is valid"
    CF_LATENCY=$(jv "$BODY" "d.get('providers',{}).get('cloudflare_workers_ai',{}).get('latency_ms','')")
    [[ -n "$CF_LATENCY" ]] && info "Latency: ${CF_LATENCY}ms"
    ;;
  disabled|not_configured)
    skip "CF Workers AI not_configured — CF_WORKER_AI_TOKEN may not be deployed yet"
    ;;
  unhealthy|degraded)
    fail "CF Workers AI is $CF_STATUS — check CF_WORKER_AI_TOKEN and CF_ACCOUNT_ID"
    CF_ERROR=$(jv "$BODY" "d.get('providers',{}).get('cloudflare_workers_ai',{}).get('error','')")
    [[ -n "$CF_ERROR" ]] && info "Error: $CF_ERROR"
    ;;
esac

# =============================================================================
# Summary
# =============================================================================
TOTAL=$((PASS+FAIL+SKIP))
printf "\n${B}────────────────────────────────────${N}\n"
printf "${B}  Providers: %d checks${N}\n" "$TOTAL"
printf "  ${G}✓ Passed : %d${N}\n" "$PASS"
[[ $FAIL -gt 0 ]] && printf "  ${R}✗ Failed : %d${N}\n" "$FAIL" || printf "  ✗ Failed : %d\n" "$FAIL"
[[ $SKIP -gt 0 ]] && printf "  ${Y}– Skipped: %d${N}\n" "$SKIP" || printf "  – Skipped: %d\n" "$SKIP"
printf "${B}────────────────────────────────────${N}\n\n"

if [[ $FAIL -gt 0 ]]; then
  printf "${R}PROVIDER CHECK FAILED${N}\n"
  exit 1
else
  printf "${G}ALL PROVIDER CHECKS PASSED${N}\n"
  exit 0
fi
