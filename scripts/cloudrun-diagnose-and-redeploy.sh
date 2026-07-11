#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Cloud Run diagnosis + redeploy script for syrabit-backend
#
# Run this from GCP Cloud Shell or any machine with gcloud + project access:
#   bash scripts/cloudrun-diagnose-and-redeploy.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT="blissful-acumen-495019-t6"
REGION="asia-south1"
SERVICE="syrabit-backend"

echo "============================================================"
echo " Syrabit Cloud Run Diagnosis"
echo "============================================================"

# 1. Service status
echo ""
echo "── 1. Service Status ──"
gcloud run services describe "$SERVICE" \
  --region="$REGION" \
  --project="$PROJECT" \
  --format="table(status.conditions[0].type,status.conditions[0].status,status.conditions[0].message)" \
  2>&1 || echo "⚠ Could not describe service (may not exist)"

# 2. Latest revision status
echo ""
echo "── 2. Latest Revision ──"
gcloud run revisions list \
  --service="$SERVICE" \
  --region="$REGION" \
  --project="$PROJECT" \
  --limit=3 \
  --format="table(name,status.conditions[0].status,status.conditions[0].message,spec.containers[0].image)" \
  2>&1 || echo "⚠ No revisions found"

# 3. Recent logs (last 5 minutes)
echo ""
echo "── 3. Recent Logs (last 5 min) ──"
gcloud run services logs read "$SERVICE" \
  --region="$REGION" \
  --project="$PROJECT" \
  --limit=80 \
  2>&1 | tail -80

# 4. Live health check via public API
echo ""
echo "── 4. Live Health Check ──"
echo -n "api.syrabit.ai/health: "
curl -s --max-time 10 https://api.syrabit.ai/health || echo "TIMEOUT"
echo ""
echo -n "api.syrabit.ai/health/full: "
curl -s --max-time 15 https://api.syrabit.ai/health/full || echo "TIMEOUT"
echo ""

# 5. Redeploy options
echo ""
echo "============================================================"
echo " Redeploy Options"
echo "============================================================"
echo ""
echo "Option A — Full Cloud Build (rebuilds Docker image, recommended):"
echo "  gcloud builds submit --project=$PROJECT --config=cloudbuild.yaml ."
echo ""
echo "Option B — Quick re-roll of latest image (no rebuild, fastest):"

LATEST_IMAGE=$(
  gcloud artifacts docker images list \
    "asia-south1-docker.pkg.dev/$PROJECT/syrabit/backend" \
    --sort-by=~CREATE_TIME \
    --limit=1 \
    --format="value(version)" \
    --project="$PROJECT" \
    2>/dev/null || echo ""
)

if [ -n "$LATEST_IMAGE" ]; then
  IMAGE_TAG="asia-south1-docker.pkg.dev/$PROJECT/syrabit/backend:${LATEST_IMAGE}"
  echo "  Latest image found: $IMAGE_TAG"
  echo ""
  read -rp "  Redeploy with this image now? [y/N] " CONFIRM
  if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "  Deploying..."
    gcloud run deploy "$SERVICE" \
      --image="$IMAGE_TAG" \
      --region="$REGION" \
      --project="$PROJECT" \
      --port=8000 \
      --allow-unauthenticated \
      --min-instances=1
    echo ""
    echo "✅ Redeploy triggered. Waiting 15s for health check..."
    sleep 15
    echo -n "Health: "
    curl -s --max-time 10 https://api.syrabit.ai/health
    echo ""
  fi
else
  echo "  ⚠ No existing image found in Artifact Registry."
  echo "  Run a full Cloud Build to build and push the image first:"
  echo "    gcloud builds submit --project=$PROJECT --config=cloudbuild.yaml ."
fi

echo ""
echo "Done."
