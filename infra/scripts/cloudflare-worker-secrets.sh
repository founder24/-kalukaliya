#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# cloudflare-worker-secrets.sh — Set all required Cloudflare Worker secrets
# for the syrabitworker-prod Edge Worker.
#
# Repo: https://github.com/founder24/-kalukaliya
#
# QUICKSTART — paste into Cloud Shell (clones repo + runs GCP + CF setup):
#   bash <(curl -fsSL https://raw.githubusercontent.com/founder24/-kalukaliya/main/infra/scripts/cloudshell-bootstrap.sh)
#
# Or step-by-step (from repo root, after GCP setup):
#   git clone https://github.com/founder24/-kalukaliya syrabit
#   cd syrabit
#   bash infra/scripts/cloudflare-worker-secrets.sh
#
# Prerequisites:
#   - wrangler installed: npm install -g wrangler
#   - Logged in: npx wrangler login
#   - Run from: apps/edge/ OR pass WORKER_DIR env var
#
# What this sets:
#   REQUIRED:
#     JWT_SECRET         — must match backend JWT_SECRET exactly (≥32 chars)
#     EDGE_SHARED_SECRET — must match backend EDGE_SHARED_SECRET exactly
#     BACKEND_URL        — Cloud Run service URL
#   OPTIONAL:
#     GOOGLE_SA_KEY      — SA JSON for Cloud Run OIDC identity token auth
#                         (only needed if Cloud Run is set to require auth)
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

WORKER_DIR="${WORKER_DIR:-apps/edge}"
ENV="${CF_ENV:-production}"
PROJECT="blissful-acumen-495019-t6"
REGION="asia-south1"
BACKEND_URL="https://syrabit-backend-bl6wu3psza-el.a.run.app"

G="\033[92m"; R="\033[91m"; Y="\033[93m"; B="\033[94m"; X="\033[0m"

echo -e "${B}=== Cloudflare Worker Secrets Setup ===${X}"
echo "Worker env  : --env $ENV"
echo "Worker dir  : $WORKER_DIR"
echo "Backend URL : $BACKEND_URL"
echo ""

cd "$WORKER_DIR"

set_secret() {
  local NAME="$1"
  local VALUE="$2"
  echo -n "$VALUE" | npx wrangler secret put "$NAME" --env "$ENV"
  echo -e "  ${G}✓${X} $NAME set"
}

prompt_secret() {
  local NAME="$1"
  local PROMPT="$2"
  local OPTIONAL="${3:-false}"

  echo ""
  if [ "$OPTIONAL" = "true" ]; then
    read -r -s -p "  $NAME ($PROMPT) [Enter to skip]: " VALUE
  else
    read -r -s -p "  $NAME ($PROMPT): " VALUE
  fi
  echo ""

  if [ -z "$VALUE" ]; then
    if [ "$OPTIONAL" = "true" ]; then
      echo -e "  ${Y}Skipped${X} $NAME"
      return
    else
      echo -e "  ${R}✗${X} $NAME is required — aborting"
      exit 1
    fi
  fi

  set_secret "$NAME" "$VALUE"
}

# ── BACKEND_URL ───────────────────────────────────────────────────────────────
echo -e "${B}[1/4] BACKEND_URL${X}"
echo "  Auto-fetching from gcloud..."
LIVE_URL=$(gcloud run services describe syrabit-backend \
  --region="$REGION" --project="$PROJECT" \
  --format='value(status.url)' 2>/dev/null || echo "$BACKEND_URL")
if [ -n "$LIVE_URL" ]; then
  BACKEND_URL="$LIVE_URL"
fi
echo "  URL: $BACKEND_URL"
set_secret "BACKEND_URL" "$BACKEND_URL"

# ── JWT_SECRET ────────────────────────────────────────────────────────────────
echo -e "\n${B}[2/4] JWT_SECRET (must match backend — read from Secret Manager if possible)${X}"
JWT_VAL=$(gcloud secrets versions access latest \
  --secret=jwt-secret --project="$PROJECT" 2>/dev/null || echo "")
if [ -n "$JWT_VAL" ]; then
  set_secret "JWT_SECRET" "$JWT_VAL"
  echo -e "  ${G}(pulled from Secret Manager automatically)${X}"
else
  prompt_secret "JWT_SECRET" "must exactly match backend JWT_SECRET (≥32 chars)"
fi

# ── EDGE_SHARED_SECRET ────────────────────────────────────────────────────────
echo -e "\n${B}[3/4] EDGE_SHARED_SECRET (must match backend EDGE_SHARED_SECRET)${X}"
EDGE_VAL=$(gcloud secrets versions access latest \
  --secret=edge-shared-secret --project="$PROJECT" 2>/dev/null || echo "")
if [ -n "$EDGE_VAL" ]; then
  set_secret "EDGE_SHARED_SECRET" "$EDGE_VAL"
  echo -e "  ${G}(pulled from Secret Manager automatically)${X}"
else
  prompt_secret "EDGE_SHARED_SECRET" "must exactly match backend EDGE_SHARED_SECRET"
fi

# ── GOOGLE_SA_KEY (optional — only needed for private Cloud Run) ──────────────
echo -e "\n${B}[4/4] GOOGLE_SA_KEY (optional — Cloud Run is currently --allow-unauthenticated)${X}"
echo "  The backend SA key JSON allows the Edge Worker to obtain an OIDC identity"
echo "  token for authenticated Cloud Run invocations. Currently Cloud Run allows"
echo "  unauthenticated traffic, so this is optional but recommended for future"
echo "  tightening (--no-allow-unauthenticated)."
echo ""
SA_KEY_VAL=$(gcloud secrets versions access latest \
  --secret=GOOGLE_APPLICATION_CREDENTIALS_JSON --project="$PROJECT" 2>/dev/null || echo "")
if [ -n "$SA_KEY_VAL" ]; then
  echo "  Found GOOGLE_APPLICATION_CREDENTIALS_JSON in Secret Manager."
  read -r -p "  Use this SA key as GOOGLE_SA_KEY for the Edge Worker? [Y/n]: " CONFIRM
  CONFIRM="${CONFIRM:-Y}"
  if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
    set_secret "GOOGLE_SA_KEY" "$SA_KEY_VAL"
    echo -e "  ${G}✓${X} GOOGLE_SA_KEY set from Secret Manager"
  else
    echo -e "  ${Y}Skipped${X}"
  fi
else
  prompt_secret "GOOGLE_SA_KEY" "SA JSON string (optional — press Enter to skip)" "true"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${G}=== Cloudflare Worker secrets set! ===${X}"
echo ""
echo "Verify the Worker is live:"
echo "  curl -I https://edge.syrabit.ai/health"
echo "  curl -I https://api.syrabit.ai/health"
echo ""
echo "To deploy the Edge Worker now:"
echo "  cd apps/edge && npx wrangler deploy --env production"
