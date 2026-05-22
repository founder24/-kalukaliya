#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# SYRABIT — Pre-Commit Verification Script
# ═══════════════════════════════════════════════════════════════
# Run this BEFORE running `git add` / `git commit`.
# Usage: chmod +x scripts/verify-before-merge.sh && bash scripts/verify-before-merge.sh

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

pass() { echo -e "  ${GREEN}✅ $1${NC}"; PASS=$((PASS+1)); }
fail() { echo -e "  ${RED}❌ $1${NC}"; FAIL=$((FAIL+1)); }
warn() { echo -e "  ${YELLOW}⚠️  $1${NC}"; WARN=$((WARN+1)); }

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  SYRABIT — Pre-Commit Verification"
echo "═══════════════════════════════════════════════════════════"

# ── Step 1: Python syntax ──
echo ""
echo "── Step 1: Python Syntax Check ──"
PY_ERR=0
while IFS= read -r -d '' f; do
  if ! python3 -c "import ast; ast.parse(open('$f').read())" 2>/dev/null; then
    fail "$f has syntax errors"
    PY_ERR=$((PY_ERR+1))
  fi
done < <(find apps/backend/app -name "*.py" -not -path "*__pycache__*" -print0 2>/dev/null)
[ $PY_ERR -eq 0 ] && pass "All Python files pass syntax validation"

# ── Step 2: Edge TypeScript ──
echo ""
echo "── Step 2: Edge TypeScript Check ──"
if [ -d "apps/edge/node_modules" ]; then
  if (cd apps/edge && npx tsc --noEmit 2>/dev/null); then
    pass "Edge TypeScript compiles with 0 errors"
  else
    fail "Edge TypeScript has compilation errors"
  fi
else
  warn "Edge node_modules missing — run: cd apps/edge && npm install"
fi

# ── Step 3: Frontend build ──
echo ""
echo "── Step 3: Frontend Build Check ──"
if [ -d "apps/frontend/node_modules" ]; then
  if (cd apps/frontend && npx tsc --noEmit 2>/dev/null); then
    pass "Frontend TypeScript compiles"
  else
    warn "Frontend TS errors (may need fresh install)"
  fi
else
  warn "Frontend node_modules missing — run: cd apps/frontend && pnpm install"
fi

# ── Step 4: Integration wiring ──
echo ""
echo "── Step 4: Integration Wiring ──"
declare -a CHECKS=(
  "apps/backend/app/db/mongo.py:ChatFeedback:Feedback model registered"
  "apps/backend/app/main.py:feedback.router:Feedback router registered"
  "apps/backend/app/main.py:init_telemetry:OTel init present"
  "apps/backend/app/api/v1/chat.py:get_current_user_optional:Optional auth (anon support)"
  "apps/backend/app/api/v1/chat.py:StreamingResponse:SSE streaming"
  "apps/backend/app/api/v1/auth.py:auto_error=False:Optional HTTPBearer"
  "apps/backend/app/services/ai/vertex_client.py:stream_generate:Vertex streaming"
  "apps/backend/app/services/ai/sarvam_client.py:stream_generate_with_retry:Sarvam retry"
  "apps/backend/app/services/ai/router.py:stream_response:Router streaming"
  "apps/edge/src/index.ts:verifyJWT:JWT integrated"
  "apps/edge/src/index.ts:checkRateLimit:Rate limit integrated"
  "apps/edge/src/index.ts:X-Bot-Detected:Bot detection"
  "apps/edge/src/routes/api-proxy.ts:text/event-stream:Stream-aware proxy"
  "apps/edge/src/middleware/jwt.ts:/api/v1/chat:Chat in PUBLIC_PATHS"
  "apps/edge/wrangler.toml:RATE_LIMIT_KV:KV namespace"
  "apps/frontend/src/hooks/useChat.ts:/api/v1/chat/stream:Hook calls stream"
  "apps/frontend/src/components/FeedbackButton.tsx:/api/v1/chat/feedback:Feedback wired"
  ".github/workflows/deploy-all.yml:smoke-test:Smoke tests"
  ".github/workflows/deploy-all.yml:anonymous:Anon smoke test"
  ".github/workflows/ci-backend.yml:validate-llm-endpoints:LLM validation in CI"
)
for c in "${CHECKS[@]}"; do
  IFS=':' read -r fp s desc <<< "$c"
  if [ -f "$fp" ] && grep -q "$s" "$fp"; then
    pass "$desc"
  else
    fail "$desc — '$s' missing in $fp"
  fi
done

# ── Step 5: Conflict markers ──
echo ""
echo "── Step 5: Conflict Markers ──"
CONF=$(grep -rn "^<<<<<<< \|^>>>>>>> " --include="*.py" --include="*.ts" --include="*.tsx" --include="*.yml" \
  --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=.git . 2>/dev/null | wc -l)
[ "$CONF" -eq 0 ] && pass "No conflict markers" || fail "$CONF conflict markers found"

# ── Step 6: Hardcoded secrets ──
echo ""
echo "── Step 6: Hardcoded Secrets ──"
if grep -rEn "(sk-[a-zA-Z0-9]{20,}|AKIA[A-Z0-9]{16}|ghp_[a-zA-Z0-9]{36})" \
  --include="*.py" --include="*.ts" --include="*.tsx" --include="*.yml" \
  --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=.git . 2>/dev/null | grep -v "example\|placeholder"; then
  fail "Possible hardcoded secrets above"
else
  pass "No hardcoded secrets detected"
fi

# ── Step 7: .env not staged ──
echo ""
echo "── Step 7: .env Files ──"
if git status --short 2>/dev/null | grep -E "\.env$" | grep -vE "\.env\.(example|shared|otel)" > /dev/null; then
  fail ".env file is staged — REMOVE before commit!"
else
  pass "No real .env files staged"
fi

# ── Summary ──
echo ""
echo "═══════════════════════════════════════════════════════════"
echo -e "  RESULTS: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}, ${YELLOW}${WARN} warnings${NC}"
echo "═══════════════════════════════════════════════════════════"
echo ""

if [ $FAIL -eq 0 ]; then
  echo -e "  ${GREEN}🎉 READY TO COMMIT${NC}"
  echo ""
  echo "  Run these next:"
  echo "    git checkout -b feat/dual-llm-streaming"
  echo "    git add -A"
  echo '    git commit -m "feat: dual-LLM streaming architecture (Phases 1-6)"'
  echo "    git push origin feat/dual-llm-streaming"
  exit 0
else
  echo -e "  ${RED}⛔ Fix ${FAIL} failing checks before commit${NC}"
  exit 1
fi
