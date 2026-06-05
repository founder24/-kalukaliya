#!/usr/bin/env bash
# deploy.sh — Deploy syrabit-backend to Cloud Run from Replit
#
# Prerequisites (all already configured):
#   - GCP_SERVICE_ACCOUNT_KEY or GOOGLE_APPLICATION_CREDENTIALS_JSON secret set in Replit
#   - gcloud installed (nix package: google-cloud-sdk)
#
# Usage:
#   bash apps/backend/scripts/deploy.sh            # deploy with defaults
#   bash apps/backend/scripts/deploy.sh --no-build # skip Docker build (re-deploy existing image)

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────────
PROJECT="blissful-acumen-495019-t6"
REGION="us-central1"
SERVICE="syrabit-backend"
SA_EMAIL="syrabit-backend-sa@${PROJECT}.iam.gserviceaccount.com"
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT}/syrabit"
IMAGE="${REGISTRY}/backend"
TAG="${IMAGE}:$(date +%Y%m%d-%H%M%S)"
LATEST="${IMAGE}:latest"
NO_BUILD=false

for arg in "$@"; do
  [ "$arg" = "--no-build" ] && NO_BUILD=true
done

echo "🚀 Syrabit backend deploy — $(date)"
echo "   Project : $PROJECT"
echo "   Region  : $REGION"
echo "   Service : $SERVICE"
echo "   Image   : $TAG"

# ── Auth ─────────────────────────────────────────────────────────────────────
# Accept the key from either secret name
KEY_JSON="${GCP_SERVICE_ACCOUNT_KEY:-${GOOGLE_APPLICATION_CREDENTIALS_JSON:-}}"
if [ -z "$KEY_JSON" ]; then
  echo "❌  Neither GCP_SERVICE_ACCOUNT_KEY nor GOOGLE_APPLICATION_CREDENTIALS_JSON is set." >&2
  exit 1
fi

KEY_FILE="$(mktemp /tmp/gcp-key-XXXXXX.json)"
trap 'rm -f "$KEY_FILE"' EXIT
printf '%s' "$KEY_JSON" > "$KEY_FILE"

echo "🔑 Authenticating with service account..."
gcloud auth activate-service-account "$SA_EMAIL" --key-file="$KEY_FILE" --quiet
gcloud config set project "$PROJECT" --quiet

# ── Docker auth ───────────────────────────────────────────────────────────────
echo "🐳 Configuring Docker auth for Artifact Registry..."
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

# ── Ensure Artifact Registry repo exists ─────────────────────────────────────
if ! gcloud artifacts repositories describe syrabit --location="$REGION" --project="$PROJECT" &>/dev/null; then
  echo "📦 Creating Artifact Registry repository 'syrabit'..."
  gcloud artifacts repositories create syrabit \
    --repository-format=docker \
    --location="$REGION" \
    --project="$PROJECT" \
    --quiet
fi

# ── Build & push ──────────────────────────────────────────────────────────────
if [ "$NO_BUILD" = false ]; then
  echo "🔨 Building Docker image..."
  docker build \
    --platform linux/amd64 \
    -t "$TAG" \
    -t "$LATEST" \
    apps/backend/

  echo "📤 Pushing image to Artifact Registry..."
  docker push "$TAG"
  docker push "$LATEST"
else
  echo "⏭  Skipping build (--no-build). Using existing: $LATEST"
  TAG="$LATEST"
fi

# ── Deploy to Cloud Run ───────────────────────────────────────────────────────
echo "☁️  Deploying to Cloud Run..."
gcloud run deploy "$SERVICE" \
  --image="$TAG" \
  --region="$REGION" \
  --project="$PROJECT" \
  --service-account="$SA_EMAIL" \
  --platform=managed \
  --allow-unauthenticated \
  --memory=1Gi \
  --cpu=1 \
  --concurrency=80 \
  --min-instances=0 \
  --max-instances=10 \
  --port=8000 \
  --timeout=30 \
  --set-env-vars="APP_ENV=production" \
  --set-env-vars="MONGODB_DB_NAME=${MONGODB_DB_NAME:-syrabit_prod}" \
  --set-env-vars="JWT_ALGORITHM=${JWT_ALGORITHM:-HS256}" \
  --set-env-vars="JWT_EXPIRY_MINUTES=${JWT_EXPIRY_MINUTES:-60}" \
  --set-env-vars="REFRESH_TOKEN_EXPIRY_DAYS=${REFRESH_TOKEN_EXPIRY_DAYS:-7}" \
  --set-env-vars="VERTEX_LOCATION=${VERTEX_LOCATION:-us-central1}" \
  --set-env-vars="VERTEX_GEMINI_MODEL=${VERTEX_GEMINI_MODEL:-gemini-2.5-flash}" \
  --set-secrets="MONGODB_URI=MONGODB_URI:latest" \
  --set-secrets="JWT_SECRET=JWT_SECRET:latest" \
  --set-secrets="GOOGLE_APPLICATION_CREDENTIALS_JSON=GOOGLE_APPLICATION_CREDENTIALS_JSON:latest" \
  --set-secrets="SENTRY_DSN=SENTRY_DSN:latest" \
  --quiet

SERVICE_URL="$(gcloud run services describe "$SERVICE" \
  --region="$REGION" --project="$PROJECT" \
  --format='value(status.url)' 2>/dev/null)"

echo ""
echo "✅ Deploy complete!"
echo "   URL: $SERVICE_URL"
echo "   Health: ${SERVICE_URL}/health"
