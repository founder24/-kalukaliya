#!/usr/bin/env bash
set -euo pipefail

PASS=0
FAIL=0
ERRORS=()

header() { echo ""; echo "════════════════════════════════════════"; echo "  $1"; echo "════════════════════════════════════════"; }
ok()     { echo "✅  $1"; ((PASS++)) || true; }
fail()   { echo "❌  $1"; ((FAIL++)) || true; ERRORS+=("$1"); }

# ── 1. BACKEND — pytest ───────────────────────────────────────────────────────
header "1/3  BACKEND  (pytest)"
cd "$(dirname "$0")/apps/backend"

if ! python3 -m pytest --version &>/dev/null; then
  echo "Installing pytest + deps..."
  pip install pytest pytest-asyncio anyio httpx --quiet
fi

if python3 -m pytest tests/ -x --tb=short -q 2>&1; then
  ok "Backend pytest suite"
else
  fail "Backend pytest suite"
fi

# ── 2. EDGE WORKER — vitest ───────────────────────────────────────────────────
header "2/3  EDGE WORKER  (vitest)"
cd "$(dirname "$0")/apps/edge"

if pnpm vitest run --reporter=verbose 2>&1; then
  ok "Edge worker vitest suite"
else
  fail "Edge worker vitest suite"
fi

# ── 3. FRONTEND — vitest ──────────────────────────────────────────────────────
header "3/3  FRONTEND  (vitest)"
cd "$(dirname "$0")/apps/frontend"

if pnpm vitest run --reporter=verbose 2>&1; then
  ok "Frontend vitest suite"
else
  fail "Frontend vitest suite"
fi

# ── SUMMARY ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════"
echo "  RESULTS:  ✅ $PASS passed   ❌ $FAIL failed"
echo "════════════════════════════════════════"
if [ ${#ERRORS[@]} -gt 0 ]; then
  echo "  Failed suites:"
  for e in "${ERRORS[@]}"; do echo "    • $e"; done
  echo ""
  exit 1
fi
echo ""
