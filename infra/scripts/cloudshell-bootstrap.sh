#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# cloudshell-bootstrap.sh — Paste this ONE command into Cloud Shell to do
# everything: clone repo → grant GCP IAM → set Cloudflare Worker secrets.
#
# PASTE THIS INTO CLOUD SHELL:
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/founder24/-kalukaliya/main/infra/scripts/cloudshell-bootstrap.sh)
#
# Or manually:
#   git clone https://github.com/founder24/-kalukaliya syrabit
#   cd syrabit
#   bash infra/scripts/cloudshell-bootstrap.sh
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_URL="https://github.com/founder24/-kalukaliya"
REPO_DIR="syrabit"
G="\033[92m"; B="\033[94m"; Y="\033[93m"; X="\033[0m"

echo -e "${B}╔══════════════════════════════════════════════════╗${X}"
echo -e "${B}║       Syrabit — Cloud Shell Bootstrap            ║${X}"
echo -e "${B}╚══════════════════════════════════════════════════╝${X}"
echo ""

# ── Step 1: Clone or update the repo ─────────────────────────────────────────
if [ -d "$REPO_DIR/.git" ]; then
  echo -e "${G}✓${X} Repo already cloned at ./$REPO_DIR — pulling latest..."
  git -C "$REPO_DIR" pull --ff-only
elif [ -f "infra/scripts/gcp-full-setup.sh" ]; then
  echo -e "${G}✓${X} Running from inside the repo already — no clone needed."
  REPO_DIR="."
else
  echo -e "${B}[1/3] Cloning $REPO_URL ...${X}"
  git clone "$REPO_URL" "$REPO_DIR"
  echo -e "  ${G}✓${X} Cloned into ./$REPO_DIR"
fi

cd "$REPO_DIR"

# ── Step 2: GCP IAM setup ─────────────────────────────────────────────────────
echo ""
echo -e "${B}[2/3] Running GCP full setup (IAM roles + Secret Manager)...${X}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
bash infra/scripts/gcp-full-setup.sh
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Step 3: Cloudflare Worker secrets ────────────────────────────────────────
echo ""
echo -e "${B}[3/3] Setting Cloudflare Worker secrets...${X}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Install wrangler if not present
if ! command -v wrangler &>/dev/null && ! npx --yes wrangler --version &>/dev/null 2>&1; then
  echo "  Installing wrangler..."
  npm install -g wrangler --quiet
fi

echo ""
read -r -p "  Run Cloudflare Worker secrets setup now? (requires wrangler login) [Y/n]: " DO_CF
DO_CF="${DO_CF:-Y}"
if [[ "$DO_CF" =~ ^[Yy]$ ]]; then
  # Authenticate wrangler if needed
  if ! npx wrangler whoami &>/dev/null 2>&1; then
    echo "  Logging into Cloudflare (browser will open)..."
    npx wrangler login
  fi
  bash infra/scripts/cloudflare-worker-secrets.sh
else
  echo -e "  ${Y}Skipped.${X} Run later with:"
  echo "    cd $(pwd) && bash infra/scripts/cloudflare-worker-secrets.sh"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${G}╔══════════════════════════════════════════════════╗${X}"
echo -e "${G}║           Bootstrap complete!                    ║${X}"
echo -e "${G}╚══════════════════════════════════════════════════╝${X}"
echo ""
echo "Verify everything is working:"
echo "  curl https://syrabit-backend-bl6wu3psza-el.a.run.app/health"
echo "  curl https://api.syrabit.ai/health"
echo ""
echo "Trigger a new Cloud Build deploy:"
echo "  gcloud builds submit --config cloudbuild.yaml --project=blissful-acumen-495019-t6 --region=asia-south1"
