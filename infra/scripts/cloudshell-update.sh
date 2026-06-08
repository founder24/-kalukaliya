#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# cloudshell-update.sh  — Pull latest commits + re-deploy from Cloud Shell
#
# ONE-LINER (run straight from Cloud Shell without cloning first):
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/founder24/-kalukaliya/main/infra/scripts/cloudshell-update.sh)
#
# OR if already inside the repo:
#
#   bash infra/scripts/cloudshell-update.sh
#
# What it does:
#   1. Clone or pull latest main from GitHub
#   2. Show what changed (git log)
#   3. Trigger Cloud Run deploy via gcloud builds submit  (or GitHub Actions)
#   4. Wait for /api/v1/health to go green
#   5. Run fullstack-audit.sh Layer 7+8 (live health + code checks)
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_URL="https://github.com/founder24/-kalukaliya"
REPO_DIR="${REPO_DIR:-syrabit}"
PROJECT="blissful-acumen-495019-t6"
REGION="asia-south1"
SERVICE="syrabit-backend"
HEALTH_URL="https://api.syrabit.ai/health"

G="\033[92m"; R="\033[91m"; B="\033[94m"; Y="\033[93m"; X="\033[0m"
ok()   { echo -e "  ${G}✓${X} $*"; }
info() { echo -e "  ${B}→${X} $*"; }
warn() { echo -e "  ${Y}⚠${X}  $*"; }
fail() { echo -e "  ${R}✗${X} $*"; }

banner() {
  echo ""
  echo -e "${B}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${X}"
  echo -e "${B}  $*${X}"
  echo -e "${B}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${X}"
}

# ── Step 1: Clone or pull ──────────────────────────────────────────────────────
banner "Step 1 — Sync repo to latest main"

if [ -f "infra/scripts/fullstack-audit.sh" ]; then
  REPO_DIR="."
  info "Already inside the repo. Pulling latest main..."
  git fetch origin
  git reset --hard origin/main
  ok "Reset to $(git rev-parse --short HEAD) — $(git log -1 --format='%s')"
elif [ -d "$REPO_DIR/.git" ]; then
  info "Repo found at ./$REPO_DIR — pulling latest main..."
  git -C "$REPO_DIR" fetch origin
  git -C "$REPO_DIR" reset --hard origin/main
  ok "Reset to $(git -C "$REPO_DIR" rev-parse --short HEAD)"
  cd "$REPO_DIR"
else
  info "Cloning $REPO_URL ..."
  git clone "$REPO_URL" "$REPO_DIR"
  ok "Cloned into ./$REPO_DIR"
  cd "$REPO_DIR"
fi

echo ""
echo "Last 5 commits:"
git log --oneline -5 | sed 's/^/    /'

# ── Step 2: Choose deploy path ────────────────────────────────────────────────
banner "Step 2 — Deploy to Cloud Run"

echo ""
echo "  How do you want to deploy?"
echo "    [1] gcloud builds submit  (Cloud Build — recommended, ~5 min)"
echo "    [2] Skip deploy           (just run health check + audit)"
echo ""
read -r -p "  Choice [1]: " DEPLOY_CHOICE
DEPLOY_CHOICE="${DEPLOY_CHOICE:-1}"

if [[ "$DEPLOY_CHOICE" == "1" ]]; then
  info "Submitting Cloud Build job..."
  gcloud builds submit \
    --config cloudbuild.yaml \
    --project="$PROJECT" \
    --region="$REGION"
  ok "Cloud Build job completed"
else
  warn "Deploy skipped — running health check only"
fi

# ── Step 3: Health check ──────────────────────────────────────────────────────
banner "Step 3 — Wait for healthy backend"

info "Polling $HEALTH_URL (up to 3 min)..."
for i in $(seq 1 36); do
  HTTP=$(curl -sf -o /tmp/health.json -w "%{http_code}" \
    "$HEALTH_URL" --max-time 8 2>/dev/null || echo "000")
  if [[ "$HTTP" == "200" ]]; then
    MONGO=$(python3 -c "import json; d=json.load(open('/tmp/health.json')); print(d.get('mongodb_initialized','?'))" 2>/dev/null || echo "?")
    ok "Backend healthy (HTTP 200, mongodb_initialized=$MONGO) — after $((i*5))s"
    break
  fi
  printf "    attempt %2d/36 — HTTP %s, waiting 5s...\n" "$i" "$HTTP"
  sleep 5
done

if [[ "$HTTP" != "200" ]]; then
  fail "Backend did not become healthy after 180s (last HTTP=$HTTP)"
  echo ""
  echo "  Diagnose with:"
  echo "    gcloud run services describe $SERVICE --region=$REGION --project=$PROJECT"
  echo "    gcloud logging read 'resource.type=cloud_run_revision AND severity>=ERROR' --limit=20 --project=$PROJECT"
  exit 1
fi

# ── Step 4: Deep health ───────────────────────────────────────────────────────
banner "Step 4 — Deep health check"

DEEP=$(curl -sf "https://api.syrabit.ai/api/v1/health/deep" --max-time 10 2>/dev/null || echo '{}')
python3 -c "
import json, sys
d = json.loads('''$DEEP''')
checks = d.get('checks', {})
overall = d.get('status', '?')
print(f'  Overall: {overall}')
for svc, result in checks.items():
    status = result.get('status', '?')
    mark = '✓' if status == 'healthy' else ('⚠' if status == 'degraded' else '✗')
    extra = ''
    if 'latency_ms' in result:
        extra = f\" ({result['latency_ms']}ms)\"
    elif 'error' in result:
        extra = f\" [{result['error'][:60]}]\"
    print(f'  {mark}  {svc:25} {status}{extra}')
" 2>/dev/null || warn "Deep health parse failed — raw: ${DEEP:0:200}"

# ── Step 5: Fullstack audit (Layer 7+8 only — no GCP creds needed) ───────────
banner "Step 5 — Fullstack audit (Layers 7+8)"

if [ -f "infra/scripts/fullstack-audit.sh" ]; then
  bash infra/scripts/fullstack-audit.sh 2>&1 | grep -E "PASS|FAIL|SKIP|Layer [78]|✓|✗|⚠" || true
else
  warn "fullstack-audit.sh not found — skipping"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${G}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${X}"
echo -e "${G}  Update complete  •  $(date -u '+%Y-%m-%d %H:%M UTC')${X}"
echo -e "${G}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${X}"
echo ""
echo "  Live URLs:"
echo "    Backend health : $HEALTH_URL"
echo "    Frontend       : https://syrabit.ai"
echo ""
echo "  Run full audit (all 8 layers, needs GCP creds):"
echo "    bash infra/scripts/fullstack-audit.sh"
echo ""
echo "  Create missing optional secrets (Redis, PostHog, IndexNow, Vertex Search):"
echo "    bash infra/runbooks/create-optional-secrets.sh"
echo ""
