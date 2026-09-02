#!/usr/bin/env bash
# =============================================================================
# SYRABIT — Master Live Test Runner
# =============================================================================
#
# Chains every live-deployment test in the correct order and prints a
# unified pass/fail/skip summary.  Works without credentials (unauthenticated
# layers run automatically); authenticated layers are enabled when creds are set.
#
# Usage (from repo root in Cloud Shell or anywhere with internet access):
#
#   # Unauthenticated only:
#   bash scripts/run-all-live-tests.sh
#
#   # Full suite (authenticated chat + auth + admin):
#   export TEST_USER_EMAIL="founder@syrabit.ai"
#   export TEST_USER_PASSWORD="your-password"
#   export TEST_ADMIN_EMAIL="founder@syrabit.ai"
#   export TEST_ADMIN_PASSWORD="your-password"
#   bash scripts/run-all-live-tests.sh
#
#   # Quick (skip slow scripts):
#   bash scripts/run-all-live-tests.sh --quick
#
#   # Single suite only:
#   bash scripts/run-all-live-tests.sh --only smoke
#   bash scripts/run-all-live-tests.sh --only frontend
#   bash scripts/run-all-live-tests.sh --only auth
#   bash scripts/run-all-live-tests.sh --only chat
#   bash scripts/run-all-live-tests.sh --only deployment
#   bash scripts/run-all-live-tests.sh --only bundle
#   bash scripts/run-all-live-tests.sh --only uptime
#
# What runs (in order):
#   1  uptime-check.sh              — 5 endpoint liveness probes (~5s)
#   2  fullstack-smoke-test.sh      — 30+ unauthenticated infra checks (~30s)
#   3  test-frontend-features.sh    — 79 frontend/SEO/PWA checks (~60s)
#   4  live-deployment-test.sh      — health,seo,security,performance (~30s)
#   5  test-auth-live.sh            — full auth + admin flow [NEEDS CREDS]
#   6  test-chat-live.sh            — full chat pipeline [NEEDS CREDS]
#
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── JSON output ───────────────────────────────────────────────────────────────
JSON_OUTPUT="${JSON_OUTPUT:-0}"
JSON_FILE="${JSON_FILE:-/tmp/syrabit-test-results-$(date +%Y%m%d-%H%M%S).json}"

# ── Colours ──────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  G='\033[0;32m' R='\033[0;31m' Y='\033[1;33m'
  C='\033[0;36m' B='\033[1m' D='\033[2m' N='\033[0m'
else
  G='' R='' Y='' C='' B='' D='' N=''
fi

# ── Args ──────────────────────────────────────────────────────────────────────
QUICK=0
ONLY=""
for arg in "$@"; do
  case "$arg" in
    --quick)       QUICK=1 ;;
    --json-output) JSON_OUTPUT=1 ;;
    --only)        ;;
    *) [[ "${PREV_ARG:-}" == "--only" ]] && ONLY="$arg" ;;
  esac
  PREV_ARG="$arg"
done

# ── Credential detection ──────────────────────────────────────────────────────
HAS_USER_CREDS=0
HAS_ADMIN_CREDS=0
[[ -n "${TEST_USER_EMAIL:-}" && -n "${TEST_USER_PASSWORD:-}" ]] && HAS_USER_CREDS=1
[[ -n "${TEST_ADMIN_EMAIL:-}" && -n "${TEST_ADMIN_PASSWORD:-}" ]] && HAS_ADMIN_CREDS=1

# ── Suite tracking ────────────────────────────────────────────────────────────
declare -A SUITE_STATUS=()
declare -A SUITE_NOTES=()
TOTAL_SUITES=0
PASSED_SUITES=0
FAILED_SUITES=0
SKIPPED_SUITES=0

# ── Helpers ───────────────────────────────────────────────────────────────────
hr()     { printf "\n${C}${B}%s${N}\n" "$(printf '═%.0s' {1..64})"; }
suite_hdr() {
  local n="$1" title="$2"
  printf "\n${C}${B}══ Suite %s: %s ══${N}\n" "$n" "$title"
  printf "   ${D}%s${N}\n" "$(date -u '+%H:%M:%S UTC')"
}

run_suite() {
  local key="$1" label="$2" script="$3"
  shift 3
  local args=("$@")

  TOTAL_SUITES=$((TOTAL_SUITES + 1))

  # --only filter
  if [[ -n "$ONLY" && "$ONLY" != "$key" ]]; then
    SUITE_STATUS[$key]="skip"
    SUITE_NOTES[$key]="--only $ONLY"
    SKIPPED_SUITES=$((SKIPPED_SUITES + 1))
    return 0
  fi

  suite_hdr "$TOTAL_SUITES" "$label"

  local logfile; logfile=$(mktemp /tmp/syrabit-suite-XXXXXX.log)
  local exit_code=0

  bash "$SCRIPT_DIR/$script" "${args[@]}" 2>&1 | tee "$logfile" || exit_code=$?

  if [[ $exit_code -eq 0 ]]; then
    printf "\n  ${G}${B}✔ SUITE PASSED${N}\n"
    SUITE_STATUS[$key]="pass"
    PASSED_SUITES=$((PASSED_SUITES + 1))
  else
    printf "\n  ${R}${B}✖ SUITE FAILED (exit $exit_code)${N}\n"
    SUITE_STATUS[$key]="fail"
    FAILED_SUITES=$((FAILED_SUITES + 1))
  fi

  rm -f "$logfile"
}

skip_suite() {
  local key="$1" label="$2" reason="$3"
  TOTAL_SUITES=$((TOTAL_SUITES + 1))

  if [[ -n "$ONLY" && "$ONLY" != "$key" ]]; then
    SUITE_STATUS[$key]="skip"
    SUITE_NOTES[$key]="--only $ONLY"
    SKIPPED_SUITES=$((SKIPPED_SUITES + 1))
    return 0
  fi

  printf "\n  ${Y}–${N}  ${B}Suite: %s${N}\n" "$label"
  printf "     ${D}Skipped: %s${N}\n" "$reason"
  SUITE_STATUS[$key]="skip"
  SUITE_NOTES[$key]="$reason"
  SKIPPED_SUITES=$((SKIPPED_SUITES + 1))
}

# ── Header ────────────────────────────────────────────────────────────────────
hr
printf "${C}${B}  SYRABIT — Master Live Test Runner${N}\n"
printf "  Target  : %s\n" "${FRONTEND:-https://syrabit.ai}"
printf "  Backend : %s\n" "${BACKEND_URL:-https://api.syrabit.ai}"
printf "  Time    : %s\n" "$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
printf "  Creds   : user=%s  admin=%s\n" \
  "$([[ $HAS_USER_CREDS -eq 1 ]] && echo 'YES' || echo 'NO')" \
  "$([[ $HAS_ADMIN_CREDS -eq 1 ]] && echo 'YES' || echo 'NO')"
[[ $QUICK -eq 1 ]] && printf "  Mode    : QUICK\n"
hr

# =============================================================================
# Suite 1 — Uptime (liveness probes, 5 endpoints)
# =============================================================================
run_suite "uptime" \
  "Uptime Check (5 liveness probes)" \
  "uptime-check.sh"

# =============================================================================
# Suite 2 — Fullstack Smoke (30+ unauthenticated infra checks)
# =============================================================================
run_suite "smoke" \
  "Fullstack Smoke Test (infra, content, security, CORS)" \
  "fullstack-smoke-test.sh"

# =============================================================================
# Suite 3 — Frontend Features (79 checks: pages, SEO, PWA, headers)
# =============================================================================
run_suite "frontend" \
  "Frontend Feature Test (79 checks: pages, SEO files, PWA, headers)" \
  "test-frontend-features.sh"

# =============================================================================
# Suite 4 — Live Deployment (health, SEO, security, performance categories)
# =============================================================================
run_suite "deployment" \
  "Live Deployment Test (health, SEO, security, performance)" \
  "live-deployment-test.sh" \
  "--category" "health,seo,security,performance"

# =============================================================================
# Suite 5 — Auth Live (full auth + admin flow)
# =============================================================================
if [[ $HAS_USER_CREDS -eq 1 ]]; then
  AUTH_ARGS=()
  [[ $HAS_ADMIN_CREDS -eq 0 ]] && AUTH_ARGS+=("--skip-admin")
  run_suite "auth" \
    "Auth Live Test (login, refresh, blacklist, logout, rate-limit, admin)" \
    "test-auth-live.sh" \
    "${AUTH_ARGS[@]}"
else
  skip_suite "auth" \
    "Auth Live Test (test-auth-live.sh)" \
    "TEST_USER_EMAIL / TEST_USER_PASSWORD not set"
fi

# =============================================================================
# Suite 9 — Provider Health (vertex, sarvam, redis, CF Workers AI, TTS, RAG)
# =============================================================================
run_suite "providers" \
  "AI Provider Health (/health/providers: all 6 integrations)" \
  "test-providers.sh"

# =============================================================================
# Suite 10 — Sarvam TTFB (verify streaming fix: any-language → Assamese < 3s)
# =============================================================================
run_suite "ttfb" \
  "Sarvam TTFB (EN/HI/AS input → Assamese, first token < 3s)" \
  "test-sarvam-ttfb.sh"

# =============================================================================
# Suite 8 — Chat Live (full chat pipeline)
# NOTE: Suite 7 (auth) deliberately hammers the login rate limiter with
# ~11 bad-password attempts. The rate-limit window is 60 seconds. Wait 70s
# before starting the chat suite so the window resets and the chat login
# attempt doesn't land in the same bucket.
# =============================================================================
if [[ $HAS_USER_CREDS -eq 1 ]]; then
  if [[ -n "$ONLY" && "$ONLY" != "chat" ]]; then
    : # --only filter handled inside run_suite — no sleep needed
  elif [[ "${SUITE_STATUS[auth]:-skip}" != "skip" ]]; then
    printf "\n  ${Y}…${N}  Waiting 70s for auth rate-limit window to reset before chat suite…\n"
    sleep 70
  fi
  run_suite "chat" \
    "Chat Pipeline Live Test (EN, AS, stream, history, TTS, multi-turn)" \
    "test-chat-live.sh"
else
  skip_suite "chat" \
    "Chat Pipeline Live Test (test-chat-live.sh)" \
    "TEST_USER_EMAIL / TEST_USER_PASSWORD not set"
fi

# =============================================================================
# Final Summary
# =============================================================================
hr
printf "${C}${B}  Master Test Runner — Final Summary${N}\n"
printf "  Completed : %s\n" "$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
hr

printf "\n  %-12s  %-52s  %s\n" "SUITE" "DESCRIPTION" "RESULT"
printf "  %-12s  %-52s  %s\n" "$(printf '─%.0s' {1..12})" "$(printf '─%.0s' {1..52})" "$(printf '─%.0s' {1..8})"

declare -A SUITE_LABELS=(
  [uptime]="Uptime (5 liveness probes)"
  [smoke]="Fullstack Smoke (infra, content, security, CORS)"
  [frontend]="Frontend Features (79 checks)"
  [bundle]="CF Pages Bundle URL"
  [deployment]="Live Deployment (health, SEO, security, perf)"
  [live]="End-to-End Layers (CF · Library · Auth · Chat)"
  [auth]="Auth Live (login, refresh, blacklist, logout)"
  [providers]="AI Provider Health (vertex, sarvam, redis, CF AI, TTS)"
  [ttfb]="Sarvam TTFB (EN/HI/AS→Assamese, first token <3s)"
  [chat]="Chat Pipeline (EN, AS, stream, TTS, multi-turn)"
)

for key in uptime smoke frontend bundle deployment live auth providers ttfb chat; do
  status="${SUITE_STATUS[$key]:-skip}"
  label="${SUITE_LABELS[$key]:-$key}"
  note="${SUITE_NOTES[$key]:-}"
  case "$status" in
    pass) icon="${G}PASS${N}" ;;
    fail) icon="${R}FAIL${N}" ;;
    skip) icon="${Y}SKIP${N}" ;;
    *)    icon="${D}????${N}" ;;
  esac
  printf "  %-12s  %-52s  %b" "$key" "$label" "$icon"
  [[ -n "$note" ]] && printf "  ${D}%s${N}" "$note"
  printf "\n"
done

printf "\n"
printf "  Suites : %d total\n" $TOTAL_SUITES
printf "  ${G}Passed${N} : %d\n" $PASSED_SUITES
printf "  ${R}Failed${N} : %d\n" $FAILED_SUITES
printf "  ${Y}Skipped${N}: %d\n" $SKIPPED_SUITES

# ── Machine-readable JSON output ─────────────────────────────────────────────
if [[ "$JSON_OUTPUT" -eq 1 ]]; then
  {
    printf '{\n'
    printf '  "timestamp": "%s",\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf '  "target_frontend": "%s",\n' "${FRONTEND:-https://syrabit.ai}"
    printf '  "target_backend": "%s",\n' "${BACKEND_URL:-https://api.syrabit.ai}"
    printf '  "totals": {"passed": %d, "failed": %d, "skipped": %d},\n' \
      "$PASSED_SUITES" "$FAILED_SUITES" "$SKIPPED_SUITES"
    printf '  "suites": {\n'
    first=1
    for key in uptime smoke frontend bundle deployment live auth providers ttfb chat; do
      status="${SUITE_STATUS[$key]:-skip}"
      note="${SUITE_NOTES[$key]:-}"
      [[ $first -eq 0 ]] && printf ',\n'
      printf '    "%s": {"status": "%s", "note": "%s"}' "$key" "$status" "$note"
      first=0
    done
    printf '\n  }\n'
    printf '}\n'
  } > "$JSON_FILE"
  printf "\n  ${C}JSON results written to: %s${N}\n" "$JSON_FILE"
fi

if [[ $FAILED_SUITES -gt 0 ]]; then
  printf "\n  ${R}${B}%d SUITE(S) FAILED${N}\n\n" $FAILED_SUITES
  exit 1
elif [[ $PASSED_SUITES -eq 0 ]]; then
  printf "\n  ${Y}No suites ran (all skipped)${N}\n\n"
  exit 0
else
  printf "\n  ${G}${B}ALL ACTIVE SUITES PASSED${N}\n\n"
  exit 0
fi
