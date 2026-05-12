#!/usr/bin/env bash
# Task #14 — dev environment health check.
#
# Verifies the canonical dev workflow set is healthy:
#   1. backend  `import server` smoke test (catches import-time regressions)
#   2. backend  GET /api/health        (artifacts/syrabit: api on :8080)
#   3. frontend GET /                  (artifacts/syrabit: web on :25144)
#   4. mockup   GET /__mockup/         (artifacts/mockup-sandbox on :8081)
#   5. frontend `pnpm build` (skipped when DEV_HEALTH_SKIP_BUILD=1)
#   6. env-var contract: `docs/infra/env-vars.md` matches code (Task #89)
#   7. PATCH-route contract: @patch_route_contract on all *Patch(BaseModel) (Task #86)
#   8. OG image CDN smoke check (skipped by default; set OG_SMOKE_SKIP=0)
#
# Wired as the `dev_health` validation step (validation skill).
# Aggregates failures (does not exit on the first one) and returns a
# non-zero status iff at least one probe failed.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_PORT="${WEB_PORT:-25144}"
API_PORT="${API_PORT:-8080}"
MOCKUP_PORT="${MOCKUP_PORT:-8081}"
TIMEOUT_S="${HEALTH_TIMEOUT_S:-10}"

fail=0
pass() { printf "  \033[32mPASS\033[0m  %s\n" "$1"; }
warn() { printf "  \033[33mWARN\033[0m  %s\n" "$1"; }
err()  { printf "  \033[31mFAIL\033[0m  %s\n" "$1"; fail=$((fail + 1)); }

http_check() {
  local label="$1" url="$2" expect="${3:-200}"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT_S" "$url" || echo 000)"
  if [[ "$code" == "$expect" ]]; then
    pass "$label ($url -> $code)"
  else
    err "$label ($url -> $code, expected $expect)"
  fi
}

echo "[1/8] backend import smoke test"
( cd "$REPO_ROOT/artifacts/syrabit-backend" && python3 -c "import server" >/dev/null 2>&1 ) \
  && pass "python -c 'import server'" \
  || err  "python -c 'import server' (cd artifacts/syrabit-backend && python -c 'import server')"

echo "[2/8] backend /api/health"
http_check "artifacts/syrabit: api" "http://localhost:${API_PORT}/api/health" 200

echo "[3/8] frontend /"
http_check "artifacts/syrabit: web" "http://localhost:${WEB_PORT}/" 200

echo "[4/8] mockup sandbox /__mockup/"
http_check "artifacts/mockup-sandbox" "http://localhost:${MOCKUP_PORT}/__mockup/" 200

echo "[5/8] frontend build"
if [[ "${DEV_HEALTH_SKIP_BUILD:-0}" == "1" ]]; then
  warn "frontend build skipped (DEV_HEALTH_SKIP_BUILD=1)"
else
  # `scripts/check-build-env.mjs` (gate before vite build) requires the prod
  # backend origin to be set so the bundle does not silently hard-code
  # localhost:8000. In dev we point it at the canonical local api workflow.
  export VITE_BACKEND_URL="${VITE_BACKEND_URL:-http://localhost:${API_PORT}}"
  if ( cd "$REPO_ROOT" && pnpm --filter @workspace/syrabit run build >/tmp/dev_health_build.log 2>&1 ); then
    pass "pnpm --filter @workspace/syrabit run build"
  else
    err "pnpm --filter @workspace/syrabit run build (see /tmp/dev_health_build.log)"
    tail -n 40 /tmp/dev_health_build.log || true
  fi
fi

echo "[6/8] env-var contract doc"
# Task #89 — docs/infra/env-vars.md is auto-generated from code refs +
# bicep / wrangler / TF wiring. This step fails if anyone added a new
# env var (or removed one) without running
# `python scripts/ci/check_env_vars_doc.py --write`.
if ( cd "$REPO_ROOT" && python3 scripts/ci/check_env_vars_doc.py >/tmp/dev_health_envdoc.log 2>&1 ); then
  pass "docs/infra/env-vars.md matches code"
else
  err "docs/infra/env-vars.md drifted (run: python scripts/ci/check_env_vars_doc.py --write)"
  tail -n 30 /tmp/dev_health_envdoc.log || true
fi

echo "[7/8] PATCH-route contract guard"
# Task #86 — every *Patch(BaseModel) class in routes/admin_edge_*.py must
# carry @patch_route_contract (registered as the `patch_contract_guard`
# validation step).  Pure-stdlib; fast enough to run unconditionally.
if ( cd "$REPO_ROOT" && python3 scripts/ci/check_patch_route_contract.py >/tmp/dev_health_patch_contract.log 2>&1 ); then
  pass "PATCH-route contract check"
else
  err "PATCH-route contract check failed (run: python3 scripts/ci/check_patch_route_contract.py)"
  cat /tmp/dev_health_patch_contract.log || true
fi

echo "[8/8] OG image CDN smoke check"
# Task #49 — verify a sample of OG banner images are reachable on the
# public CDN (https://cdn.syrabit.ai/og).  Skipped by default in dev
# because the check makes live network requests that can fail when offline
# or in sandboxed CI environments.  Set OG_SMOKE_SKIP=0 to enable it.
if [[ "${OG_SMOKE_SKIP:-1}" == "1" ]]; then
  warn "OG smoke check skipped (set OG_SMOKE_SKIP=0 to enable)"
else
  if ( cd "$REPO_ROOT" && python3 scripts/og-images/smoke_check_og.py --sample 3 >/tmp/dev_health_og_smoke.log 2>&1 ); then
    pass "OG image CDN smoke check (3 sample URLs)"
  else
    err "OG image CDN smoke check failed (see /tmp/dev_health_og_smoke.log)"
    cat /tmp/dev_health_og_smoke.log || true
  fi
fi

echo
if (( fail == 0 )); then
  echo "dev_health_check: OK"
  exit 0
fi
echo "dev_health_check: $fail failure(s)"
exit 1
