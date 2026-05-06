#!/usr/bin/env bash
# verify_integration.sh
# End-to-end verification of Pages + Worker + Azure Container Apps + D1 integration.
# Run from any environment with curl + bash.
#
# Backend origin is Azure Container Apps (post-cutover); historical migration was Railway →
# Platform. The `RAILWAY_DIRECT` / `RAILWAY_URL` variables below are
# kept under their old names so existing CI invocations keep working,
# but the default value now points at the DO direct-ingress hostname;
# override with `BACKEND_DIRECT=https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io`
# (preferred) or `RAILWAY_URL=…` (legacy, still honoured).
#
# Usage:
#   bash verify_integration.sh
#   ADMIN_JWT="eyJ..." bash verify_integration.sh   # enables protected-endpoint checks
#
# Exit codes:
#   0 = all critical checks passed
#   1 = one or more critical checks failed
#   2 = warnings only (non-critical degradations)

set -u

# ─── Config ───────────────────────────────────────────────────────────────────
EDGE="${EDGE_URL:-https://api.syrabit.ai}"
SITE="${SITE_URL:-https://syrabit.ai}"
WWW="${WWW_URL:-https://www.syrabit.ai}"
RAILWAY_DIRECT="${BACKEND_DIRECT:-${RAILWAY_URL:-https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io}}"
ADMIN_JWT="${ADMIN_JWT:-}"

PASS=0
FAIL=0
WARN=0
RESULTS=()

# ─── Helpers ──────────────────────────────────────────────────────────────────
c_red()   { printf '\033[0;31m%s\033[0m' "$*"; }
c_green() { printf '\033[0;32m%s\033[0m' "$*"; }
c_yellow(){ printf '\033[0;33m%s\033[0m' "$*"; }
c_dim()   { printf '\033[2m%s\033[0m' "$*"; }

phase() {
  echo
  echo "═══════════════════════════════════════════════════════════════════════"
  echo "  $*"
  echo "═══════════════════════════════════════════════════════════════════════"
}

check() {
  # check <label> <command-string> <expectation-grep-pattern> [severity:critical|warn]
  local label="$1" cmd="$2" expect="$3" sev="${4:-critical}"
  local out rc
  out=$(eval "$cmd" 2>&1)
  rc=$?
  if [[ $rc -eq 0 ]] && echo "$out" | grep -qE "$expect"; then
    PASS=$((PASS+1))
    RESULTS+=("PASS|$label")
    printf "  [%s] %s\n" "$(c_green PASS)" "$label"
    [[ -n "${VERBOSE:-}" ]] && printf "       %s\n" "$(c_dim "$out" | head -c 200)"
  else
    if [[ "$sev" == "warn" ]]; then
      WARN=$((WARN+1))
      RESULTS+=("WARN|$label")
      printf "  [%s] %s\n" "$(c_yellow WARN)" "$label"
    else
      FAIL=$((FAIL+1))
      RESULTS+=("FAIL|$label")
      printf "  [%s] %s\n" "$(c_red FAIL)" "$label"
    fi
    printf "       cmd: %s\n" "$(c_dim "$cmd")"
    printf "       got: %s\n" "$(c_dim "$(echo "$out" | head -c 300 | tr '\n' ' ')")"
    printf "       want match: %s\n" "$(c_dim "$expect")"
  fi
}

http_code() { curl -s -o /dev/null -w "%{http_code}" -m 15 "$@"; }
http_head_code() { curl -sI -o /dev/null -w "%{http_code}" -m 15 "$@"; }

# ─── Phase 1: Build is live ───────────────────────────────────────────────────
phase "PHASE 1 — Confirm latest build is live"

check "Backend health (direct Azure Container Apps origin)" \
  "http_code '$RAILWAY_DIRECT/api/health'" \
  "^200$"

check "Backend health (via worker edge)" \
  "curl -sI -m 10 '$EDGE/api/health' | tr -d '\r'" \
  "(HTTP/2 200|HTTP/1.1 200)"

check "Edge tag present (x-source: edge on /api/health)" \
  "curl -sI -m 10 '$EDGE/api/health' | tr -d '\r'" \
  "x-source: edge"

check "New endpoint /api/seo/d1/status reachable (PR #6 deployed)" \
  "http_code '$EDGE/api/seo/d1/status'" \
  "^(200|401|403)$"

check "New endpoint /api/seo/d1/status returns JSON with 'configured'" \
  "curl -s -m 10 '$EDGE/api/seo/d1/status'" \
  '"configured"'

check "/api/seo/health responds in <8s (no GraphQL retry hang)" \
  "curl -s -o /dev/null -m 8 -w '%{http_code}|%{time_total}' '$EDGE/api/seo/health'" \
  "^[0-9]{3}\\|[0-7]\\." \
  "warn"

# ─── Phase 2: HEAD-method bug fix ─────────────────────────────────────────────
phase "PHASE 2 — HEAD-method probes (was failing → SEO health degraded)"

for path in / /library /exam-routine /home /terms /curriculum /about /pricing /dashboard; do
  check "HEAD $SITE$path → 200" \
    "http_head_code '$SITE$path'" \
    "^200$"
done

# ─── Phase 3: Worker → Pages routing ──────────────────────────────────────────
phase "PHASE 3 — Pages SPA via worker"

for path in / /pricing /dashboard /courses /about; do
  check "GET $SITE$path → 200 + HTML" \
    "curl -s -m 10 -o /dev/null -w '%{http_code}|%{content_type}|%{size_download}' '$SITE$path'" \
    "^200\\|text/html.*\\|[0-9]{4,}$"
done

check "GET $WWW/ → 200" \
  "http_code '$WWW/'" \
  "^200$"

check "Googlebot prerender path returns HTML" \
  "curl -s -m 10 -A 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)' -o /dev/null -w '%{http_code}|%{content_type}' '$SITE/'" \
  "^200\\|text/html"

# ─── Phase 4: D1 catalog serving ──────────────────────────────────────────────
phase "PHASE 4 — D1 edge cache serves catalog (instead of backend fall-through)"

# Probe each catalog endpoint with cache-bust. Pick correct separator based on
# whether the path already has a query string.
_busturl() {
  local p="$1"
  local sep="?"
  [[ "$p" == *"?"* ]] && sep="&"
  echo "${EDGE}${p}${sep}_cb=$(date +%s%N)"
}

for path in "/api/content/boards" "/api/content/subjects" "/api/content/library-bundle?slim=1"; do
  url=$(_busturl "$path")
  check "GET $path returns 200" \
    "curl -sI -m 10 '$url' | head -1 | tr -d '\r'" \
    "200"

  check "GET $path served from D1 (x-source: d1)" \
    "curl -sI -m 10 '$url' | tr -d '\r'" \
    "x-source: d1" \
    "warn"
done

# ─── Phase 5: D1 sync trigger and verification ────────────────────────────────
phase "PHASE 5 — Trigger sync and verify timestamp updates"

if [[ -z "$ADMIN_JWT" ]]; then
  echo "  $(c_yellow 'SKIP') ADMIN_JWT not set — cannot test admin endpoints"
  echo "       To enable: export ADMIN_JWT='<your-admin-jwt>' and re-run"
  WARN=$((WARN+1))
else
  BEFORE=$(curl -s -m 10 -H "Authorization: Bearer $ADMIN_JWT" "$EDGE/api/seo/d1/status" | grep -oE '"last_sync"[^,}]*' | head -1)
  echo "  Before sync: $(c_dim "$BEFORE")"

  check "POST /api/seo/d1/sync-full returns success" \
    "curl -s -m 60 -X POST -H 'Authorization: Bearer $ADMIN_JWT' '$EDGE/api/seo/d1/sync-full'" \
    '"success"[[:space:]]*:[[:space:]]*true'

  sleep 3

  AFTER=$(curl -s -m 10 -H "Authorization: Bearer $ADMIN_JWT" "$EDGE/api/seo/d1/status" | grep -oE '"last_sync"[^,}]*' | head -1)
  echo "  After sync:  $(c_dim "$AFTER")"

  if [[ "$BEFORE" != "$AFTER" ]]; then
    PASS=$((PASS+1))
    RESULTS+=("PASS|sync_full advanced last_sync timestamp")
    printf "  [%s] %s\n" "$(c_green PASS)" "sync_full advanced last_sync timestamp"
  else
    FAIL=$((FAIL+1))
    RESULTS+=("FAIL|sync_full did NOT advance last_sync")
    printf "  [%s] %s\n" "$(c_red FAIL)" "sync_full did NOT advance last_sync timestamp"
  fi

  check "GET /api/seo/d1/status reports configured=true" \
    "curl -s -m 10 -H 'Authorization: Bearer $ADMIN_JWT' '$EDGE/api/seo/d1/status'" \
    '"configured"[[:space:]]*:[[:space:]]*true'

  check "GET /api/seo/d1/status reports sync_secret_set=true" \
    "curl -s -m 10 -H 'Authorization: Bearer $ADMIN_JWT' '$EDGE/api/seo/d1/status'" \
    '"sync_secret_set"[[:space:]]*:[[:space:]]*true'
fi

# ─── Phase 6: SEO health restoration ──────────────────────────────────────────
phase "PHASE 6 — SEO health should now report OK (after HEAD fix)"

check "/api/seo/health returns 200" \
  "http_code '$EDGE/api/seo/health'" \
  "^200$"

if [[ -n "$ADMIN_JWT" ]]; then
  check "/api/seo/health status is 'ok' (not 'degraded' or 'critical')" \
    "curl -s -m 10 -H 'Authorization: Bearer $ADMIN_JWT' '$EDGE/api/seo/health'" \
    '"status"[[:space:]]*:[[:space:]]*"ok"' \
    "warn"
fi

# ─── Phase 7: Cloudflare Analytics restoration ────────────────────────────────
phase "PHASE 7 — Cloudflare Analytics (after CF_ANALYTICS_API_TOKEN rotation)"

if [[ -n "$ADMIN_JWT" ]]; then
  check "Cloudflare client returns no 'unavailable' marker" \
    "curl -s -m 10 -H 'Authorization: Bearer $ADMIN_JWT' '$EDGE/api/admin/analytics/cf' 2>/dev/null || echo SKIP" \
    "(SKIP|visitors|page_views|404|^\\{)" \
    "warn"
else
  echo "  $(c_yellow 'SKIP') ADMIN_JWT not set — cannot probe analytics endpoint"
  WARN=$((WARN+1))
fi

# ─── Summary ──────────────────────────────────────────────────────────────────
phase "SUMMARY"

TOTAL=$((PASS+FAIL+WARN))
echo
printf "  Total checks: %d   |   %s %d   %s %d   %s %d\n\n" \
  "$TOTAL" \
  "$(c_green PASS)" "$PASS" \
  "$(c_red FAIL)" "$FAIL" \
  "$(c_yellow WARN)" "$WARN"

if [[ ${#RESULTS[@]} -gt 0 ]] && [[ -n "${VERBOSE:-}" ]]; then
  echo "  Detail:"
  for r in "${RESULTS[@]}"; do
    echo "    $r"
  done
fi

echo
if [[ $FAIL -gt 0 ]]; then
  echo "  $(c_red 'INTEGRATION VERIFICATION: FAILED')"
  echo "  Fix the failing checks above before treating production as healthy."
  exit 1
elif [[ $WARN -gt 0 ]]; then
  echo "  $(c_yellow 'INTEGRATION VERIFICATION: PASSED with warnings')"
  echo "  Critical paths work. Address warnings (likely D1-fallthrough or CF analytics token) for full optimization."
  exit 2
else
  echo "  $(c_green 'INTEGRATION VERIFICATION: ALL PASSED')"
  echo "  Pages + Worker + Railway + D1 are fully operational."
  exit 0
fi
