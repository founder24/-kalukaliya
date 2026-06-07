#!/usr/bin/env bash
# =============================================================================
# Syrabit — Update & Full-Stack Test Runner
#
# Pulls latest commits, loads GCP secrets, waits for healthy backend,
# then runs the test suite.
#
# Usage (from repo root):
#   bash scripts/update-and-test.sh --master          # ← recommended: all 8 suites
#   bash scripts/update-and-test.sh                   # deep layer test (1000+ assertions)
#   bash scripts/update-and-test.sh --master --quick  # unauthenticated suites only
#   bash scripts/update-and-test.sh --master --only smoke
#   bash scripts/update-and-test.sh --quick           # skip stress + slow layers
#   bash scripts/update-and-test.sh --layer 3         # single layer only
#   bash scripts/update-and-test.sh --no-pull         # skip git pull
#   bash scripts/update-and-test.sh --local           # test localhost instead of prod
#   bash scripts/update-and-test.sh --export-json     # save results JSON
#
# Optional env vars:
#   GCP_PROJECT    GCP project ID  (default: blissful-acumen-495019-t6)
#   BASE_URL       backend URL     (default: https://api.syrabit.ai)
#   FRONTEND_URL   frontend URL    (default: https://syrabit.ai)
#   BRANCH         git branch to pull (default: current branch)
#
#   For --master authenticated suites, also set:
#   TEST_USER_EMAIL / TEST_USER_PASSWORD   (loaded from GCP Secret Manager automatically)
#   TEST_ADMIN_EMAIL / TEST_ADMIN_PASSWORD (loaded from GCP Secret Manager automatically)
# =============================================================================
set -uo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
GCP_PROJECT="${GCP_PROJECT:-blissful-acumen-495019-t6}"
BASE_URL="${BASE_URL:-https://api.syrabit.ai}"
FRONTEND_URL="${FRONTEND_URL:-https://syrabit.ai}"
BRANCH="${BRANCH:-}"
SKIP_PULL=0
QUICK_MODE=0
EXPORT_JSON=0
LAYER_ARG=""
LOCAL_MODE=0
MASTER_MODE=0
ONLY_SUITE=""

# ── Colours ───────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  G='\033[0;32m' R='\033[0;31m' Y='\033[1;33m' C='\033[0;36m' B='\033[1m' D='\033[2m' N='\033[0m'
else
  G='' R='' Y='' C='' B='' D='' N=''
fi

step()  { echo -e "\n${C}${B}▶ $1${N}"; }
ok()    { echo -e "  ${G}✓${N} $1"; }
warn()  { echo -e "  ${Y}△${N} $1"; }
err()   { echo -e "  ${R}✗${N} $1"; }
info()  { echo -e "  ${D}  $1${N}"; }
die()   { echo -e "\n${R}${B}FATAL: $1${N}\n"; exit 1; }
hr()    { echo -e "${D}────────────────────────────────────────────────────────${N}"; }

# ── Arg parse ─────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --master)      MASTER_MODE=1;        shift ;;
    --only)        ONLY_SUITE="$2";      shift 2 ;;
    --no-pull)     SKIP_PULL=1;          shift ;;
    --quick)       QUICK_MODE=1;         shift ;;
    --local)       LOCAL_MODE=1;         shift ;;
    --export-json) EXPORT_JSON=1;        shift ;;
    --layer)       LAYER_ARG="$2";       shift 2 ;;
    --base-url)    BASE_URL="$2";        shift 2 ;;
    --frontend)    FRONTEND_URL="$2";    shift 2 ;;
    --branch)      BRANCH="$2";         shift 2 ;;
    --project)     GCP_PROJECT="$2";     shift 2 ;;
    --help|-h)
      sed -n '2,30p' "$0" | grep '^#' | sed 's/^# \?//'
      exit 0 ;;
    *) echo "Unknown option: $1 (use --help)"; exit 1 ;;
  esac
done

if [[ $LOCAL_MODE -eq 1 ]]; then
  BASE_URL="http://localhost:8000"
  FRONTEND_URL="http://localhost:5000"
fi

START_TS=$(date +%s)

echo ""
echo -e "${B}╔══════════════════════════════════════════════════╗${N}"
echo -e "${B}║     Syrabit Update & Full-Stack Test Runner      ║${N}"
echo -e "${B}╚══════════════════════════════════════════════════╝${N}"
echo    "  GCP Project : $GCP_PROJECT"
echo    "  Backend URL : $BASE_URL"
echo    "  Frontend URL: $FRONTEND_URL"
echo    "  Date        : $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
hr


# =============================================================================
# STEP 1 — GIT PULL
# =============================================================================
step "1. Git — Pull Latest Commits"

if [[ $SKIP_PULL -eq 1 ]]; then
  warn "Skipping git pull (--no-pull)"
else
  if ! command -v git &>/dev/null; then
    warn "git not found — skipping pull"
  elif ! git rev-parse --git-dir &>/dev/null 2>&1; then
    warn "Not inside a git repo — skipping pull"
  else
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    TARGET_BRANCH="${BRANCH:-$CURRENT_BRANCH}"
    info "Branch: $TARGET_BRANCH"

    # Show current HEAD before pull
    BEFORE_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    info "Before: $BEFORE_SHA — $(git log -1 --format='%s' 2>/dev/null || echo '')"

    # Pull
    if git pull origin "$TARGET_BRANCH" --ff-only 2>&1 | tee /tmp/git_pull_output.txt; then
      AFTER_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
      if [[ "$BEFORE_SHA" == "$AFTER_SHA" ]]; then
        ok "Already up to date ($AFTER_SHA)"
      else
        ok "Updated $BEFORE_SHA → $AFTER_SHA"
        # Show what changed
        NEW_COMMITS=$(git log --oneline "${BEFORE_SHA}..HEAD" 2>/dev/null | head -10)
        if [[ -n "$NEW_COMMITS" ]]; then
          info "New commits:"
          while IFS= read -r line; do info "  $line"; done <<< "$NEW_COMMITS"
        fi
      fi
    else
      warn "git pull had issues — proceeding with current state"
      cat /tmp/git_pull_output.txt | head -5 | while IFS= read -r l; do info "$l"; done
    fi
  fi
fi


# =============================================================================
# STEP 2 — LOAD SECRETS FROM GCP SECRET MANAGER
# =============================================================================
step "2. Load Secrets from GCP Secret Manager"

# Map: GCP_SECRET_NAME → ENV_VAR_NAME
declare -A SECRET_MAP=(
  ["jwt-secret"]="JWT_SECRET"
  ["GEMINI_API_KEY"]="GEMINI_API_KEY"
  ["SARVAM_API_KEY"]="SARVAM_API_KEY"
  ["RAZORPAY_KEY_ID"]="RAZORPAY_KEY_ID"
  ["RAZORPAY_KEY_SECRET"]="RAZORPAY_KEY_SECRET"
  ["RAZORPAY_WEBHOOK_SECRET"]="RAZORPAY_WEBHOOK_SECRET"
  ["RESEND_API_KEY"]="RESEND_API_KEY"
  ["upstash-redis-url"]="UPSTASH_REDIS_REST_URL"
  ["upstash-redis-token"]="UPSTASH_REDIS_REST_TOKEN"
  ["POSTHOG_API_KEY"]="POSTHOG_API_KEY"
  ["SENTRY_DSN"]="SENTRY_DSN"
  ["VERTEX_PROJECT_ID"]="VERTEX_PROJECT_ID"
  ["edge-shared-secret"]="EDGE_SHARED_SECRET"
  ["ADMIN_JWT_SECRET"]="ADMIN_JWT_SECRET"
  ["RESET_TOKEN_SECRET"]="RESET_TOKEN_SECRET"
  ["TRANSLATE_CRON_SECRET"]="CRON_SECRET"
  ["cf-turnstile-secret"]="CF_TURNSTILE_SECRET"
  ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]="GOOGLE_APPLICATION_CREDENTIALS_JSON"
  ["INDEXNOW_API_KEY"]="INDEXNOW_API_KEY"
  ["INDEXNOW_INTERNAL_SECRET"]="INDEXNOW_INTERNAL_SECRET"
  ["TEST_USER_EMAIL"]="TEST_USER_EMAIL"
  ["TEST_USER_PASSWORD"]="TEST_USER_PASSWORD"
  ["TEST_ADMIN_EMAIL"]="TEST_ADMIN_EMAIL"
  ["TEST_ADMIN_PASSWORD"]="TEST_ADMIN_PASSWORD"
)

SECRETS_LOADED=0
SECRETS_SKIPPED=0

if ! command -v gcloud &>/dev/null; then
  warn "gcloud not found — skipping secret injection (using existing env vars)"
else
  GCLOUD_ACCOUNT=$(gcloud config get-value account 2>/dev/null || echo "unknown")
  info "Authenticated as: $GCLOUD_ACCOUNT"

  for gcp_name in "${!SECRET_MAP[@]}"; do
    env_name="${SECRET_MAP[$gcp_name]}"
    # Skip if already set in environment with a real value
    current="${!env_name:-}"
    if [[ -n "$current" && "$current" != "not-configured" && "$current" != "placeholder" ]]; then
      info "  $env_name — already set, skipping GCP fetch"
      SECRETS_SKIPPED=$((SECRETS_SKIPPED+1))
      continue
    fi

    val=$(gcloud secrets versions access latest \
      --secret="$gcp_name" \
      --project="$GCP_PROJECT" 2>/dev/null || echo "")

    if [[ -n "$val" && "$val" != "not-configured" ]]; then
      export "$env_name"="$val"
      ok "$env_name loaded from GCP ($gcp_name)"
      SECRETS_LOADED=$((SECRETS_LOADED+1))
    else
      warn "$env_name — not found in GCP ($gcp_name)"
    fi
  done

  ok "Secrets: $SECRETS_LOADED loaded, $SECRETS_SKIPPED already set"
fi

# Derive useful test vars from loaded secrets
ADMIN_EMAIL="${ADMIN_EMAIL:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
TEST_JWT_TOKEN="${TEST_JWT_TOKEN:-}"
RAZORPAY_WEBHOOK_SECRET="${RAZORPAY_WEBHOOK_SECRET:-}"
CRON_SECRET="${CRON_SECRET:-}"
CF_TURNSTILE_SECRET="${CF_TURNSTILE_SECRET:-}"


# =============================================================================
# STEP 3 — WAIT FOR BACKEND HEALTHY
# =============================================================================
step "3. Wait for Backend to Be Healthy"

HEALTH_URL="$BASE_URL/health"
MAX_WAIT=60
WAITED=0
POLL=3

info "Polling $HEALTH_URL (max ${MAX_WAIT}s)..."

while true; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$HEALTH_URL" 2>/dev/null || echo "000")
  if [[ "$STATUS" == "200" ]]; then
    BODY=$(curl -s --max-time 5 "$HEALTH_URL" 2>/dev/null)
    HEALTH_STATUS=$(echo "$BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "?")
    ok "Backend healthy in ${WAITED}s (status=$HEALTH_STATUS)"
    break
  elif [[ $WAITED -ge $MAX_WAIT ]]; then
    err "Backend did not become healthy after ${MAX_WAIT}s (last status=$STATUS)"
    echo ""
    echo -e "${Y}Hint: Start the backend workflow and re-run, or use --local for localhost.${N}"
    echo ""
    # Don't exit — layer test will report failures itself
    break
  else
    info "  Waiting... ($STATUS, ${WAITED}s elapsed)"
    sleep $POLL
    WAITED=$((WAITED+POLL))
  fi
done

# Deep health summary
DEEP_BODY=$(curl -s --max-time 8 "$BASE_URL/health/deep" 2>/dev/null || echo "{}")
printf '%s' "$DEEP_BODY" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    checks = d.get('checks', {})
    if checks:
        print('  Deep health:')
        for k, v in checks.items():
            s = v.get('status', '?')
            icon = '\033[0;32m\u2713\033[0m' if s == 'healthy' else ('\033[1;33m\u25b3\033[0m' if s == 'degraded' else '\033[0;31m\u2717\033[0m')
            print(f'    {icon} {k}: {s}')
except Exception:
    pass
" 2>/dev/null || true


# =============================================================================
# STEP 4 — FRONTEND REACHABLE?
# =============================================================================
step "4. Frontend Reachability Check"

FRONT_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$FRONTEND_URL" 2>/dev/null || echo "000")
if [[ "$FRONT_STATUS" == "200" || "$FRONT_STATUS" == "304" ]]; then
  ok "Frontend $FRONTEND_URL → $FRONT_STATUS"
else
  warn "Frontend $FRONTEND_URL → $FRONT_STATUS"
fi


# =============================================================================
# STEP 5 — RUN TESTS
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Export env vars all test scripts need
export BASE_URL FRONTEND_URL
export ADMIN_EMAIL ADMIN_PASSWORD
export TEST_JWT_TOKEN
export TEST_USER_EMAIL TEST_USER_PASSWORD
export TEST_ADMIN_EMAIL TEST_ADMIN_PASSWORD
export RAZORPAY_WEBHOOK_SECRET
export CRON_SECRET
export STRESS_TEST="${STRESS_TEST:-0}"
export VERBOSE="${VERBOSE:-0}"
export EXPORT_JSON

if [[ $MASTER_MODE -eq 1 ]]; then
  # ── Master runner: all 8 suites in order ────────────────────────────────
  step "5. Running Master Live Test Suite (all 8 suites)"
  hr

  MASTER_SCRIPT="$SCRIPT_DIR/run-all-live-tests.sh"
  if [[ ! -f "$MASTER_SCRIPT" ]]; then
    die "Master runner not found at $MASTER_SCRIPT"
  fi

  MASTER_ARGS=()
  [[ $QUICK_MODE -eq 1 ]] && MASTER_ARGS+=("--quick")
  [[ -n "$ONLY_SUITE" ]]  && MASTER_ARGS+=("--only" "$ONLY_SUITE")

  echo ""
  bash "$MASTER_SCRIPT" "${MASTER_ARGS[@]+"${MASTER_ARGS[@]}"}"
  TEST_EXIT=$?

else
  # ── Deep layer test: 1000+ assertions, 22 layers ────────────────────────
  step "5. Running Full-Stack Layer Test (1000+ assertions)"
  hr

  LAYER_TEST="$SCRIPT_DIR/fullstack-layer-test.sh"
  if [[ ! -f "$LAYER_TEST" ]]; then
    die "Layer test script not found at $LAYER_TEST"
  fi

  LAYER_TEST_ARGS=()
  [[ $QUICK_MODE  -eq 1 ]] && LAYER_TEST_ARGS+=("--quick")
  [[ $EXPORT_JSON -eq 1 ]] && LAYER_TEST_ARGS+=("--cloudshell")
  [[ -n "$LAYER_ARG" ]]    && LAYER_TEST_ARGS+=("--layer" "$LAYER_ARG")

  echo ""
  bash "$LAYER_TEST" "${LAYER_TEST_ARGS[@]+"${LAYER_TEST_ARGS[@]}"}"
  TEST_EXIT=$?
fi

# =============================================================================
# STEP 6 — SUMMARY
# =============================================================================
END_TS=$(date +%s)
ELAPSED=$((END_TS - START_TS))
hr
echo ""
echo -e "${B}Total time: ${ELAPSED}s${N}"
if [[ $TEST_EXIT -eq 0 ]]; then
  echo -e "${G}${B}✓ All tests passed${N}\n"
else
  echo -e "${R}${B}✗ Tests reported failures (exit $TEST_EXIT)${N}"
  echo    "  Review the output above for details."
  echo ""
fi

exit $TEST_EXIT
