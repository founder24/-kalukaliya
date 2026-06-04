#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PASS=0
FAIL=0
ERRORS=()

header() { echo ""; echo "════════════════════════════════════════════════════"; echo "  $1"; echo "════════════════════════════════════════════════════"; }
ok()     { echo "✅  $1"; ((PASS++)) || true; }
fail()   { echo "❌  $1"; ((FAIL++)) || true; ERRORS+=("$1"); }

# ── 1. BACKEND — pytest ───────────────────────────────────────────────────────
header "1/3  BACKEND  (pytest — 25 test files)"
cd "$ROOT/apps/backend"

echo "→ Installing all backend requirements..."
pip install -r requirements.txt --quiet --disable-pip-version-check 2>&1 | tail -3
export PATH="$HOME/.local/bin:$PATH"

echo "→ Running pytest..."
if python3 -m pytest tests/ --tb=short -q 2>&1; then
  ok "Backend pytest suite"
else
  fail "Backend pytest suite (see errors above)"
fi

# ── 2. EDGE WORKER — vitest ───────────────────────────────────────────────────
header "2/3  EDGE WORKER  (vitest — 11 test files)"
cd "$ROOT/apps/edge"

echo "→ Installing typescript..."
npm install typescript --save-dev --quiet 2>&1 | tail -2

echo "→ Running vitest..."
if npx vitest run --reporter=verbose 2>&1; then
  ok "Edge worker vitest suite"
else
  fail "Edge worker vitest suite (see errors above)"
fi

# ── 3. FRONTEND — vitest ──────────────────────────────────────────────────────
header "3/3  FRONTEND  (vitest)"
cd "$ROOT"

echo "→ Installing all workspace dependencies (pnpm install)..."
pnpm install --frozen-lockfile --silent 2>&1 | tail -3

cd "$ROOT/apps/frontend"
echo "→ Running vitest..."
if pnpm vitest run --reporter=verbose 2>&1; then
  ok "Frontend vitest suite"
else
  fail "Frontend vitest suite (see errors above)"
fi

# ── SUMMARY ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════"
echo "  RESULTS:  ✅ $PASS passed   ❌ $FAIL failed"
echo "════════════════════════════════════════════════════"
if [ ${#ERRORS[@]} -gt 0 ]; then
  echo "  Failed suites:"
  for e in "${ERRORS[@]}"; do echo "    • $e"; done
  echo ""
  exit 1
fi
echo ""
