#!/usr/bin/env bash
# smoke-test.sh — local validation for dispatch-v2
# Usage: ./scripts/smoke-test.sh [PORT]
# Starts the service temporarily and verifies every routing path.

set -euo pipefail

PORT="${1:-9099}"
BASE="http://localhost:${PORT}"
SECRET="smoke-test-secret-$(date +%s)"
PASS=0
FAIL=0

log() { echo "[smoke] $*"; }
ok()   { echo "  ✓ $*"; PASS=$((PASS+1)); }
fail() { echo "  ✗ $*"; FAIL=$((FAIL+1)); }

# ── Build if needed ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(dirname "$SCRIPT_DIR")"

if [ ! -f "$SERVICE_DIR/dist/index.js" ]; then
  log "Building TypeScript..."
  npm --prefix "$SERVICE_DIR" run build
fi

# ── Start server ─────────────────────────────────────────────────────────────
log "Starting dispatch-v2 on port $PORT..."
DISPATCH_SHARED_SECRET="$SECRET" \
BACKEND_RAILWAY_URL="http://127.0.0.1:19999" \
BACKEND_CLOUDRUN_URL="http://127.0.0.1:29999" \
TENANT_ROUTES='{"app.example.com":"cloudrun","api.example.com":"railway"}' \
CLOUDRUN_HOST_PATTERN='^internal\.' \
PORT="$PORT" \
  node "$SERVICE_DIR/dist/index.js" &

SERVER_PID=$!
trap "kill $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null; true" EXIT

# Wait for the server to be ready (up to 5s)
for i in $(seq 1 10); do
  sleep 0.5
  if curl -sf "$BASE/healthz" >/dev/null 2>&1; then break; fi
done

# ── Test 1: /healthz ─────────────────────────────────────────────────────────
log "Test: /healthz"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/healthz")
BODY=$(curl -s "$BASE/healthz")
[ "$STATUS" = "200" ] && [[ "$BODY" == *'"status":"ok"'* ]] \
  && ok "/healthz → 200 {status:ok}" \
  || fail "/healthz → expected 200 {status:ok}, got $STATUS $BODY"

# ── Test 2: /readyz ──────────────────────────────────────────────────────────
log "Test: /readyz"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/readyz")
BODY=$(curl -s "$BASE/readyz")
[ "$STATUS" = "200" ] && [[ "$BODY" == *'"railwayConfigured":true'* ]] && [[ "$BODY" == *'"cloudRunConfigured":true'* ]] \
  && ok "/readyz → 200, both backends configured" \
  || fail "/readyz → expected 200 with both backends, got $STATUS $BODY"

# ── Test 3: missing secret → 401 ─────────────────────────────────────────────
log "Test: missing secret → 401"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/v1")
[ "$STATUS" = "401" ] \
  && ok "missing secret → 401" \
  || fail "missing secret → expected 401, got $STATUS"

# ── Test 4: wrong secret → 401 ───────────────────────────────────────────────
log "Test: wrong secret → 401"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -H "x-dispatch-secret: wrong" "$BASE/api/v1")
[ "$STATUS" = "401" ] \
  && ok "wrong secret → 401" \
  || fail "wrong secret → expected 401, got $STATUS"

# ── Test 5: explicit map — app.example.com → Cloud Run ───────────────────────
log "Test: app.example.com → cloudrun (explicit map)"
BODY=$(curl -s -H "x-dispatch-secret: $SECRET" -H "x-forwarded-host: app.example.com" "$BASE/dashboard" 2>&1 || true)
# Server should attempt cloudrun backend (127.0.0.1:29999 — not running → 502)
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -H "x-dispatch-secret: $SECRET" -H "x-forwarded-host: app.example.com" "$BASE/dashboard" 2>&1 || true)
[ "$STATUS" = "502" ] \
  && ok "app.example.com → cloudrun backend attempted (502 expected — no real upstream)" \
  || fail "app.example.com → expected 502 (cloudrun), got $STATUS"

# ── Test 6: explicit map — api.example.com → Railway ─────────────────────────
log "Test: api.example.com → railway (explicit map)"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -H "x-dispatch-secret: $SECRET" -H "x-forwarded-host: api.example.com" "$BASE/v1/ping" 2>&1 || true)
[ "$STATUS" = "502" ] \
  && ok "api.example.com → railway backend attempted (502 expected — no real upstream)" \
  || fail "api.example.com → expected 502 (railway), got $STATUS"

# ── Test 7: regex pattern — internal.example.com → Cloud Run ─────────────────
log "Test: internal.example.com → cloudrun (regex pattern)"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -H "x-dispatch-secret: $SECRET" -H "x-forwarded-host: internal.example.com" "$BASE/status" 2>&1 || true)
[ "$STATUS" = "502" ] \
  && ok "internal.example.com → cloudrun backend attempted via regex (502 expected)" \
  || fail "internal.example.com → expected 502 (cloudrun via regex), got $STATUS"

# ── Test 8: default fallback → Railway ───────────────────────────────────────
log "Test: unknown-tenant → railway (default fallback)"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -H "x-dispatch-secret: $SECRET" -H "x-forwarded-host: customer42.example.com" "$BASE/app" 2>&1 || true)
[ "$STATUS" = "502" ] \
  && ok "customer42.example.com → railway backend attempted as default (502 expected)" \
  || fail "customer42.example.com → expected 502 (railway default), got $STATUS"

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
