#!/usr/bin/env bash
#
# redeploy-all.sh — Redeploy ALL services after frontend restore
#
# Prerequisites:
#   - Azure CLI logged in:  az login
#   - Cloudflare Wrangler authenticated:  npx wrangler login
#   - pnpm installed
#   - Node.js 22+
#
# Usage:
#   chmod +x scripts/redeploy-all.sh
#   ./scripts/redeploy-all.sh
#
set -euo pipefail

echo "═══════════════════════════════════════════════════"
echo "  SYRABIT FULL-STACK REDEPLOYMENT"
echo "  (Frontend restored from pre-92f98da commit)"
echo "═══════════════════════════════════════════════════"

# ─── CONFIG ───────────────────────────────────────────
RESOURCE_GROUP="rg-syrabit-prod"
ACR_NAME="syrabitacr"
CONTAINER_APP="ca-syrabit-api"
CF_PAGES_PROJECT="syrabit-frontend"
COMMIT_SHA=$(git rev-parse --short HEAD)

echo ""
echo "Commit: ${COMMIT_SHA}"
echo ""

# ═══════════════════════════════════════════════════════
# 1. BACKEND — Azure Container Apps
# ═══════════════════════════════════════════════════════
echo "─── [1/3] Deploying Backend to Azure Container Apps ───"

# Build and push Docker image to Azure Container Registry
az acr build \
  --registry "${ACR_NAME}" \
  --image "syrabit-api:${COMMIT_SHA}" \
  --image "syrabit-api:latest" \
  --file apps/backend/Dockerfile \
  apps/backend/

# Update container app with new image
az containerapp update \
  --name "${CONTAINER_APP}" \
  --resource-group "${RESOURCE_GROUP}" \
  --image "${ACR_NAME}.azurecr.io/syrabit-api:${COMMIT_SHA}"

# Health check
echo "Waiting 30s for new revision to stabilize..."
sleep 30
BACKEND_FQDN=$(az containerapp show \
  --name "${CONTAINER_APP}" \
  --resource-group "${RESOURCE_GROUP}" \
  --query 'properties.configuration.ingress.fqdn' -o tsv)
curl -sf "https://${BACKEND_FQDN}/health" \
  && echo "Backend healthy at https://${BACKEND_FQDN}" \
  || echo "WARNING: Backend health check failed — check logs"

echo ""

# ═══════════════════════════════════════════════════════
# 2. EDGE WORKER — Cloudflare Workers
# ═══════════════════════════════════════════════════════
echo "─── [2/3] Deploying Edge Worker to Cloudflare ───"

pnpm install --no-frozen-lockfile
pnpm --filter syrabit-edge exec wrangler deploy --env production

echo "Edge worker deployed."
echo ""

# ═══════════════════════════════════════════════════════
# 3. FRONTEND — Cloudflare Pages
# ═══════════════════════════════════════════════════════
echo "─── [3/3] Deploying Frontend to Cloudflare Pages ───"

# Build the frontend
pnpm --filter syrabit-frontend run build

# Deploy to Cloudflare Pages
npx wrangler pages deploy apps/frontend/dist \
  --project-name="${CF_PAGES_PROJECT}"

echo "Frontend deployed to Cloudflare Pages."
echo ""

# ═══════════════════════════════════════════════════════
# 4. POST-DEPLOY SMOKE TESTS
# ═══════════════════════════════════════════════════════
echo "─── [VERIFY] Running smoke tests ───"

# Backend health
HTTP_BACKEND=$(curl -s -o /dev/null -w "%{http_code}" "https://${BACKEND_FQDN}/health" --max-time 15)
echo "Backend /health: HTTP ${HTTP_BACKEND}"

# Edge health
HTTP_EDGE=$(curl -s -o /dev/null -w "%{http_code}" "https://edge.syrabit.ai/health" --max-time 15)
echo "Edge /health: HTTP ${HTTP_EDGE}"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  DEPLOYMENT COMPLETE"
echo "═══════════════════════════════════════════════════"
echo ""
echo "Services:"
echo "  Backend:  https://${BACKEND_FQDN}"
echo "  Edge:     https://edge.syrabit.ai"
echo "  Frontend: https://syrabit.ai (Cloudflare Pages)"
echo ""
