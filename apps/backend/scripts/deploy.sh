#!/usr/bin/env bash
# deploy.sh — Deploy syrabit-backend to Cloud Run from Replit
#
# Prerequisites (all already configured):
#   - GCP_SERVICE_ACCOUNT_KEY or GOOGLE_APPLICATION_CREDENTIALS_JSON secret set in Replit
#   - JWT_SECRET secret set in Replit (passed directly to Cloud Run — no Secret Manager needed)
#   - gcloud installed (nix package: google-cloud-sdk)
#
# Usage:
#   bash apps/backend/scripts/deploy.sh            # deploy with defaults
#   bash apps/backend/scripts/deploy.sh --no-build # skip Docker build (re-deploy existing image)
#
# After deploy, ALSO sync the Cloudflare Worker secret so both sides use the same JWT key:
#   cd apps/edge && npx wrangler secret put JWT_SECRET --env production
#   (paste the same value as the Replit JWT_SECRET secret)

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────────
PROJECT="blissful-acumen-495019-t6"
REGION="asia-south1"
SERVICE="syrabit-backend"
SA_EMAIL="syrabit-backend-sa@${PROJECT}.iam.gserviceaccount.com"
# Artifact Registry repo lives in us-central1 (where it was created)
REGISTRY="us-central1-docker.pkg.dev/${PROJECT}/syrabit"
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

# ── Validate required secrets ─────────────────────────────────────────────────
if [ -z "${JWT_SECRET:-}" ]; then
  echo "❌  JWT_SECRET is not set. Add it as a Replit secret." >&2
  exit 1
fi
if [ ${#JWT_SECRET} -lt 32 ]; then
  echo "❌  JWT_SECRET must be at least 32 characters." >&2
  exit 1
fi

# ── Auth ─────────────────────────────────────────────────────────────────────
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
gcloud auth configure-docker "us-central1-docker.pkg.dev" --quiet

# ── Ensure Artifact Registry repo exists ─────────────────────────────────────
if ! gcloud artifacts repositories describe syrabit --location="us-central1" --project="$PROJECT" &>/dev/null; then
  echo "📦 Creating Artifact Registry repository 'syrabit'..."
  gcloud artifacts repositories create syrabit \
    --repository-format=docker \
    --location="us-central1" \
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
# JWT_SECRET is injected directly from the Replit environment variable.
# GCP Secret Manager does NOT have a JWT_SECRET entry — do not use --set-secrets for it.
# MONGODB_URI, GOOGLE_APPLICATION_CREDENTIALS_JSON, SENTRY_DSN live in Secret Manager.
echo "☁️  Deploying to Cloud Run ($REGION)..."
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
  --update-env-vars="APP_ENV=production" \
  --update-env-vars="MONGODB_DB_NAME=${MONGODB_DB_NAME:-syrabit_prod}" \
  --update-env-vars="JWT_ALGORITHM=${JWT_ALGORITHM:-HS256}" \
  --update-env-vars="JWT_EXPIRY_MINUTES=${JWT_EXPIRY_MINUTES:-60}" \
  --update-env-vars="REFRESH_TOKEN_EXPIRY_DAYS=${REFRESH_TOKEN_EXPIRY_DAYS:-7}" \
  --update-env-vars="VERTEX_LOCATION=${VERTEX_LOCATION:-us-central1}" \
  --update-env-vars="VERTEX_GEMINI_MODEL=${VERTEX_GEMINI_MODEL:-gemini-2.5-flash}" \
  --update-env-vars="JWT_SECRET=${JWT_SECRET}" \
  --set-secrets="MONGODB_URI=MONGODB_URI:latest" \
  --set-secrets="GOOGLE_APPLICATION_CREDENTIALS_JSON=GOOGLE_APPLICATION_CREDENTIALS_JSON:latest" \
  --set-secrets="SENTRY_DSN=SENTRY_DSN:latest" \
  --quiet

SERVICE_URL="$(gcloud run services describe "$SERVICE" \
  --region="$REGION" --project="$PROJECT" \
  --format='value(status.url)' 2>/dev/null || echo 'https://syrabit-backend-bl6wu3psza-el.a.run.app')"

echo ""
echo "✅ Deploy complete!"
echo "   URL   : $SERVICE_URL"
echo "   Health: ${SERVICE_URL}/health"
echo ""
echo "⚠️  IMPORTANT: Sync the Cloudflare Worker JWT_SECRET to match:"
echo "   cd apps/edge && npx wrangler secret put JWT_SECRET --env production"
echo "   (use the same value as your Replit JWT_SECRET secret)"
