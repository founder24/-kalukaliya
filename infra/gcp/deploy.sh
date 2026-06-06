#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Safe Cloud Run deploy for Syrabit backend
# =============================================================================
# Wraps `gcloud run deploy --source` and ALWAYS re-applies every secret
# immediately after, so a deploy can never silently drop credentials.
#
# Usage:
#   ./infra/gcp/deploy.sh                  # deploy from repo root
#   ./infra/gcp/deploy.sh --dry-run        # print commands, execute nothing
#   ./infra/gcp/deploy.sh --secrets-only   # re-apply secrets without deploying
#   ./infra/gcp/deploy.sh --help
#
# Run from the repository root (not from inside infra/gcp/).
# Requires: gcloud CLI authenticated, billing active.
# =============================================================================

set -euo pipefail

# =============================================================================
# CONFIGURATION
# =============================================================================

PROJECT_ID="blissful-acumen-495019-t6"
REGION="asia-south1"
SERVICE="syrabit-backend"
SOURCE_DIR="apps/backend"

# Plain env vars (not secrets — safe to hardcode in this script)
ENV_VARS=(
  "APP_ENV=production"
  "JWT_ALGORITHM=HS256"
  "VERTEX_PROJECT_ID=${PROJECT_ID}"
  "VERTEX_LOCATION=us-central1"
  "VERTEX_SEARCH_DATASTORE_ID=syrabit-edu-datastore"
  "VERTEX_SEARCH_LOCATION=global"
  "VERTEX_SEARCH_SERVING_CONFIG=default_search"
  # sarvam-m was deprecated; confirmed models: sarvam-m1, sarvam-105b, sarvam-30b
  "SARVAM_MODEL=sarvam-m1"
)

# =============================================================================
# SECRETS — format: "ENV_VAR_NAME=secret-manager-name"
# Left side  = env var name in Cloud Run container
# Right side = Secret Manager secret name (NOT the value)
# =============================================================================

# Required secrets — deploy will warn loudly if any are missing from Secret Manager
REQUIRED_SECRETS=(
  "MONGODB_URI=MONGODB_URI"
  "JWT_SECRET=jwt-secret"
  "EDGE_SHARED_SECRET=edge-shared-secret"
  "ADMIN_JWT_SECRET=ADMIN_JWT_SECRET"
  "GOOGLE_APPLICATION_CREDENTIALS_JSON=GOOGLE_APPLICATION_CREDENTIALS_JSON"
  "SARVAM_API_KEY=SARVAM_API_KEY"
  "UPSTASH_REDIS_REST_URL=UPSTASH_REDIS_REST_URL"
  "UPSTASH_REDIS_REST_TOKEN=UPSTASH_REDIS_REST_TOKEN"
  "GEMINI_API_KEY=gemini-api-key"
  "RAZORPAY_KEY_ID=RAZORPAY_KEY_ID"
  "RAZORPAY_KEY_SECRET=RAZORPAY_KEY_SECRET"
  "RAZORPAY_WEBHOOK_SECRET=RAZORPAY_WEBHOOK_SECRET"
  "RESEND_API_KEY=RESEND_API_KEY"
  "RESET_TOKEN_SECRET=RESET_TOKEN_SECRET"
  "TRANSLATE_CRON_SECRET=TRANSLATE_CRON_SECRET"
  "INDEXNOW_API_KEY=INDEXNOW_API_KEY"
  "INDEXNOW_INTERNAL_SECRET=INDEXNOW_INTERNAL_SECRET"
  "JWT_PRIVATE_KEY=jwt-private-key"
  "JWT_PUBLIC_KEY=jwt-public-key"
)

# Optional secrets — silently skipped if they don't exist in Secret Manager yet
OPTIONAL_SECRETS=(
  "SENTRY_DSN=SENTRY_DSN"
  "POSTHOG_API_KEY=POSTHOG_API_KEY"
  "ADMIN_EMAIL=ADMIN_EMAIL"
  "ADMIN_PASSWORD=ADMIN_PASSWORD"
  "CF_PAGES_DEPLOY_HOOK=CF_PAGES_DEPLOY_HOOK"
  "VERTEX_SEARCH_DATASTORE_ID_OPTIONAL=VERTEX_SEARCH_DATASTORE_ID"
)

# =============================================================================
# FLAGS
# =============================================================================

DRY_RUN=false
SECRETS_ONLY=false

for arg in "$@"; do
  case "$arg" in
    --dry-run)      DRY_RUN=true ;;
    --secrets-only) SECRETS_ONLY=true ;;
    --help|-h)
      sed -n '2,12p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg — use --help"
      exit 1
      ;;
  esac
done

# =============================================================================
# OUTPUT HELPERS
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}    $1"; }
success() { echo -e "${GREEN}[OK]${NC}      $1"; }
warning() { echo -e "${YELLOW}[WARN]${NC}    $1"; }
error()   { echo -e "${RED}[ERROR]${NC}   $1" >&2; }
step()    { echo -e "\n${CYAN}══ $1 ══${NC}"; }

run() {
  if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}[DRY-RUN]${NC} $*"
  else
    "$@"
  fi
}

# =============================================================================
# PREREQUISITES
# =============================================================================

step "Checking prerequisites"

if ! command -v gcloud &>/dev/null; then
  error "gcloud CLI not found. Install: https://cloud.google.com/sdk/docs/install"
  exit 1
fi

if [ "$DRY_RUN" = false ]; then
  ACTIVE_ACCOUNT=$(gcloud auth list --filter="status:ACTIVE" --format="value(account)" 2>/dev/null | head -1)
  if [ -z "$ACTIVE_ACCOUNT" ]; then
    error "Not authenticated. Run: gcloud auth login"
    exit 1
  fi
  success "Authenticated as: ${ACTIVE_ACCOUNT}"
else
  info "[DRY-RUN] Skipping auth check."
fi

if [ ! -d "$SOURCE_DIR" ]; then
  error "Source dir '${SOURCE_DIR}' not found. Run from the repository root."
  exit 1
fi
success "Source directory: ${SOURCE_DIR}"

# =============================================================================
# STEP 1 — VERIFY ALL REQUIRED SECRETS EXIST IN SECRET MANAGER
# =============================================================================

step "Verifying secrets in Secret Manager"

MISSING_SECRETS=()
if [ "$DRY_RUN" = false ]; then
  for entry in "${REQUIRED_SECRETS[@]}"; do
    secret_name="${entry#*=}"
    if ! gcloud secrets describe "$secret_name" --project="$PROJECT_ID" &>/dev/null; then
      MISSING_SECRETS+=("$secret_name")
      warning "MISSING required secret: ${secret_name}"
    else
      success "Found: ${secret_name}"
    fi
  done

  if [ ${#MISSING_SECRETS[@]} -gt 0 ]; then
    echo ""
    error "The following required secrets are missing from Secret Manager:"
    for s in "${MISSING_SECRETS[@]}"; do
      echo "    • $s"
    done
    echo ""
    error "Create them with:"
    for s in "${MISSING_SECRETS[@]}"; do
      echo "    printf 'YOUR_VALUE' | gcloud secrets create ${s} --data-file=-"
    done
    echo ""
    error "Aborting deploy to prevent broken production environment."
    exit 1
  fi

  success "All required secrets verified."
else
  info "[DRY-RUN] Would verify ${#REQUIRED_SECRETS[@]} required secrets in Secret Manager."
  for entry in "${REQUIRED_SECRETS[@]}"; do
    info "  Would check: ${entry#*=}"
  done
fi

# =============================================================================
# STEP 2 — DEPLOY FROM SOURCE (skipped with --secrets-only)
# =============================================================================

if [ "$SECRETS_ONLY" = false ]; then
  step "Deploying from source: ${SOURCE_DIR}"

  ENV_VARS_ARG=$(IFS=,; echo "${ENV_VARS[*]}")

  run gcloud run deploy "$SERVICE" \
    --source="$SOURCE_DIR" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --no-allow-unauthenticated \
    --set-env-vars="$ENV_VARS_ARG"

  success "Source deploy complete."
else
  info "Skipping source deploy (--secrets-only mode)."
fi

# =============================================================================
# STEP 3 — RE-APPLY ALL SECRETS (always runs — the whole point of this script)
# =============================================================================

step "Re-applying all secrets to Cloud Run service"

# Build --update-secrets argument from required secrets
SECRETS_ARG=""
for entry in "${REQUIRED_SECRETS[@]}"; do
  env_var="${entry%=*}"
  secret_name="${entry#*=}"
  SECRETS_ARG+="${env_var}=${secret_name}:latest,"
done

# Check and append optional secrets that exist
OPTIONAL_APPLIED=()
OPTIONAL_SKIPPED=()
for entry in "${OPTIONAL_SECRETS[@]}"; do
  env_var="${entry%=*}"
  secret_name="${entry#*=}"
  if [ "$DRY_RUN" = true ] || gcloud secrets describe "$secret_name" --project="$PROJECT_ID" &>/dev/null; then
    SECRETS_ARG+="${env_var}=${secret_name}:latest,"
    OPTIONAL_APPLIED+=("$secret_name")
  else
    OPTIONAL_SKIPPED+=("$secret_name")
  fi
done

# Strip trailing comma
SECRETS_ARG="${SECRETS_ARG%,}"

run gcloud run services update "$SERVICE" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --update-secrets="$SECRETS_ARG"

success "All secrets re-applied."

if [ ${#OPTIONAL_APPLIED[@]} -gt 0 ]; then
  info "Optional secrets applied: ${OPTIONAL_APPLIED[*]}"
fi
if [ ${#OPTIONAL_SKIPPED[@]} -gt 0 ]; then
  warning "Optional secrets not yet in Secret Manager (skipped): ${OPTIONAL_SKIPPED[*]}"
fi

# =============================================================================
# STEP 4 — VERIFY DEPLOYMENT
# =============================================================================

step "Verifying deployment"

SERVICE_URL="https://syrabit-backend-851687450401.${REGION}.run.app"

if [ "$DRY_RUN" = false ]; then
  info "Waiting 10s for new revision to serve traffic..."
  sleep 10

  HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "${SERVICE_URL}/health" || echo "000")

  if [ "$HTTP_STATUS" = "200" ]; then
    success "Health check passed: ${SERVICE_URL}/health → HTTP ${HTTP_STATUS}"
  else
    warning "Health check returned HTTP ${HTTP_STATUS} — service may still be starting."
    info "Check manually: curl ${SERVICE_URL}/health"
  fi

  # Show the active revision
  REVISION=$(gcloud run services describe "$SERVICE" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --format="value(status.latestReadyRevisionName)" 2>/dev/null || echo "unknown")
  info "Active revision: ${REVISION}"
else
  info "[DRY-RUN] Would verify: curl ${SERVICE_URL}/health"
fi

# =============================================================================
# SUMMARY
# =============================================================================

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Deploy complete — ${SERVICE}${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Service URL : ${SERVICE_URL}"
echo -e "  Health      : ${SERVICE_URL}/health"
echo -e "  Full health : https://api.syrabit.ai/health/full"
echo -e "  Region      : ${REGION}"
echo -e "  Project     : ${PROJECT_ID}"
echo ""
echo -e "  Required secrets applied : ${#REQUIRED_SECRETS[@]}"
echo -e "  Optional secrets applied : ${#OPTIONAL_APPLIED[@]}"
if [ ${#OPTIONAL_SKIPPED[@]} -gt 0 ]; then
  echo -e "  Optional secrets skipped : ${#OPTIONAL_SKIPPED[@]} (${OPTIONAL_SKIPPED[*]})"
fi
echo ""
