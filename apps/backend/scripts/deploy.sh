#!/usr/bin/env bash
# deploy.sh — Deploy syrabit-backend to Cloud Run
#
# Build strategy: Cloud Build (gcloud builds submit) — no local Docker needed.
# Works from: Replit, Cloud Shell, any machine with gcloud authenticated.
#
# Prerequisites:
#   - gcloud authenticated (service account key OR gcloud auth login)
#   - JWT_SECRET env var set (min 32 chars)
#   - GCP_SERVICE_ACCOUNT_KEY or GOOGLE_APPLICATION_CREDENTIALS_JSON (optional —
#     only needed when not already authenticated via gcloud auth login / Cloud Shell)
#
# Usage:
#   bash apps/backend/scripts/deploy.sh            # build + deploy
#   bash apps/backend/scripts/deploy.sh --no-build # skip build, re-deploy latest image
#
# After deploy, sync the Cloudflare Worker secret to match:
#   cd apps/edge && npx wrangler secret put JWT_SECRET --env production

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
# If a service account key is provided (Replit env) use it; otherwise trust the
# existing gcloud session (Cloud Shell / local terminal with `gcloud auth login`).
KEY_JSON="${GCP_SERVICE_ACCOUNT_KEY:-${GOOGLE_APPLICATION_CREDENTIALS_JSON:-}}"
if [ -n "$KEY_JSON" ]; then
  KEY_FILE="$(mktemp /tmp/gcp-key-XXXXXX.json)"
  trap 'rm -f "$KEY_FILE"' EXIT
  printf '%s' "$KEY_JSON" > "$KEY_FILE"
  echo "🔑 Authenticating with service account key..."
  gcloud auth activate-service-account "$SA_EMAIL" --key-file="$KEY_FILE" --quiet
else
  echo "🔑 Using existing gcloud session ($(gcloud config get-value account 2>/dev/null))..."
fi
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

# ── Build & push via Cloud Build (no local Docker required) ───────────────────
if [ "$NO_BUILD" = false ]; then
  echo "🔨 Building image via Cloud Build (no local Docker needed)..."
  gcloud builds submit apps/backend/ \
    --tag="$TAG" \
    --project="$PROJECT" \
    --quiet
  echo "📤 Tagging as :latest..."
  gcloud artifacts tags create latest \
    --package=backend \
    --version="$(basename "$TAG")" \
    --location=us-central1 \
    --repository=syrabit \
    --project="$PROJECT" 2>/dev/null || true
else
  echo "⏭  Skipping build (--no-build). Using existing: $LATEST"
  TAG="$LATEST"
fi

# ── Deploy to Cloud Run ───────────────────────────────────────────────────────
# JWT_SECRET is a plain env var (NOT in Secret Manager).
# --remove-secrets ensures we clear any old Secret Manager reference for JWT_SECRET
# before setting it as a literal env var (mixing types causes a Cloud Run error).
# MONGODB_URI, GOOGLE_APPLICATION_CREDENTIALS_JSON, SENTRY_DSN, and
# EDGE_SHARED_SECRET live in Secret Manager. Keep EDGE_SHARED_SECRET attached
# on every deploy so Cloud Run can call the API Worker's private generation route.
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
  --remove-secrets="JWT_SECRET" \
  --update-env-vars="APP_ENV=production,MONGODB_DB_NAME=${MONGODB_DB_NAME:-syrabit_prod},JWT_ALGORITHM=${JWT_ALGORITHM:-HS256},JWT_EXPIRY_MINUTES=${JWT_EXPIRY_MINUTES:-60},REFRESH_TOKEN_EXPIRY_DAYS=${REFRESH_TOKEN_EXPIRY_DAYS:-7},JWT_SECRET=${JWT_SECRET}" \
  --update-secrets="MONGODB_URI=MONGODB_URI:latest,GOOGLE_APPLICATION_CREDENTIALS_JSON=GOOGLE_APPLICATION_CREDENTIALS_JSON:latest,SENTRY_DSN=SENTRY_DSN:latest,EDGE_SHARED_SECRET=edge-shared-secret:latest" \
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
