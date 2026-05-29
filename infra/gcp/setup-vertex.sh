#!/usr/bin/env bash
# =============================================================================
# setup-vertex.sh - Automated GCP + Vertex AI infrastructure setup for Syrabit
# =============================================================================
# This script sets up all Google Cloud Platform resources required by the
# Syrabit educational AI assistant backend, including:
#   - GCP project creation/selection and billing linkage
#   - Required API enablement
#   - Service account with correct IAM roles
#   - Vertex AI Search (Discovery Engine) datastore and search engine/app
#   - Artifact Registry Docker repository
#   - Service account key generation
#
# Usage:
#   ./setup-vertex.sh             # Run full setup
#   ./setup-vertex.sh --dry-run   # Print commands without executing
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - A GCP billing account
# =============================================================================

set -euo pipefail

# =============================================================================
# CONFIGURATION - Edit these variables before running
# =============================================================================

# REQUIRED: Must be set by the user
PROJECT_ID="${PROJECT_ID:-}"                          # e.g., "syrabit-prod-123"
BILLING_ACCOUNT_ID="${BILLING_ACCOUNT_ID:-}"          # e.g., "012345-6789AB-CDEF01"

# OPTIONAL: Defaults are provided
REGION="${REGION:-us-central1}"
DATASTORE_LOCATION="${DATASTORE_LOCATION:-global}"
DATASTORE_DISPLAY_NAME="${DATASTORE_DISPLAY_NAME:-syrabit-edu-datastore}"
SERVICE_ACCOUNT_NAME="${SERVICE_ACCOUNT_NAME:-syrabit-backend-sa}"
SEARCH_ENGINE_DISPLAY_NAME="${SEARCH_ENGINE_DISPLAY_NAME:-syrabit-search-engine}"

# Derived values (do not edit)
SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
KEY_FILE_PATH="./sa-key-${PROJECT_ID}.json"

# =============================================================================
# FLAGS
# =============================================================================

DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --dry-run)
      DRY_RUN=true
      ;;
    --help|-h)
      echo "Usage: $0 [--dry-run] [--help]"
      echo ""
      echo "Options:"
      echo "  --dry-run   Print commands without executing them"
      echo "  --help      Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg"
      echo "Use --help for usage information."
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
NC='\033[0m' # No Color

info() {
  echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
  echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warning() {
  echo -e "${YELLOW}[WARNING]${NC} $1"
}

error() {
  echo -e "${RED}[ERROR]${NC} $1" >&2
}

# Execute or print command based on DRY_RUN flag
run_cmd() {
  if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}[DRY-RUN]${NC} $*"
  else
    "$@"
  fi
}

# =============================================================================
# PREREQUISITES CHECK
# =============================================================================

info "Checking prerequisites..."

# Check gcloud is installed
if ! command -v gcloud &> /dev/null; then
  error "gcloud CLI is not installed. Please install it from https://cloud.google.com/sdk/docs/install"
  exit 1
fi

# Check gcloud is authenticated
if ! gcloud auth list --filter="status:ACTIVE" --format="value(account)" 2>/dev/null | grep -q .; then
  error "gcloud is not authenticated. Run: gcloud auth login"
  exit 1
fi

# Check curl is available (needed for REST API calls)
if ! command -v curl &> /dev/null; then
  error "curl is not installed. Please install curl."
  exit 1
fi

# Validate required configuration
if [ -z "$PROJECT_ID" ]; then
  error "PROJECT_ID is not set. Edit the script or export PROJECT_ID before running."
  exit 1
fi

if [ -z "$BILLING_ACCOUNT_ID" ]; then
  error "BILLING_ACCOUNT_ID is not set. Edit the script or export BILLING_ACCOUNT_ID before running."
  echo ""
  info "To find your billing account ID, run:"
  echo "  gcloud billing accounts list"
  exit 1
fi

# Verify the billing account exists and is accessible
if [ "$DRY_RUN" = false ]; then
  if ! gcloud billing accounts describe "$BILLING_ACCOUNT_ID" &>/dev/null; then
    error "Billing account '$BILLING_ACCOUNT_ID' not found or not accessible."
    info "Available billing accounts:"
    gcloud billing accounts list
    exit 1
  fi
fi

success "Prerequisites check passed"

# =============================================================================
# SECTION 1: PROJECT SETUP
# =============================================================================

echo ""
info "=== Section 1: Project Setup ==="

# Check if project already exists
if gcloud projects describe "$PROJECT_ID" &>/dev/null 2>&1; then
  info "Project '$PROJECT_ID' already exists, using existing project."
  run_cmd gcloud config set project "$PROJECT_ID"
else
  info "Creating project '$PROJECT_ID'..."
  run_cmd gcloud projects create "$PROJECT_ID" --name="$PROJECT_ID"
  run_cmd gcloud config set project "$PROJECT_ID"
fi

# Link billing account to project
info "Linking billing account to project..."
run_cmd gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT_ID"

success "Project setup complete"

# =============================================================================
# SECTION 2: ENABLE REQUIRED APIs
# =============================================================================

echo ""
info "=== Section 2: Enabling Required APIs ==="

REQUIRED_APIS=(
  "discoveryengine.googleapis.com"
  "aiplatform.googleapis.com"
  "run.googleapis.com"
  "artifactregistry.googleapis.com"
  "cloudbuild.googleapis.com"
  "iam.googleapis.com"
  "serviceusage.googleapis.com"
)

for api in "${REQUIRED_APIS[@]}"; do
  info "Enabling $api..."
  run_cmd gcloud services enable "$api" --project="$PROJECT_ID"
done

success "All required APIs enabled"

# =============================================================================
# SECTION 3: SERVICE ACCOUNT CREATION
# =============================================================================

echo ""
info "=== Section 3: Service Account Setup ==="

# Check if service account already exists
if gcloud iam service-accounts describe "$SERVICE_ACCOUNT_EMAIL" --project="$PROJECT_ID" &>/dev/null 2>&1; then
  info "Service account '$SERVICE_ACCOUNT_EMAIL' already exists, skipping creation."
else
  info "Creating service account '$SERVICE_ACCOUNT_NAME'..."
  run_cmd gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
    --display-name="Syrabit Backend Service Account" \
    --description="Service account for Syrabit backend (Vertex AI, Discovery Engine, Cloud Run)" \
    --project="$PROJECT_ID"
fi

# Assign required IAM roles
IAM_ROLES=(
  "roles/discoveryengine.editor"
  "roles/aiplatform.user"
  "roles/run.developer"
  "roles/artifactregistry.writer"
  "roles/logging.logWriter"
  "roles/monitoring.metricWriter"
)

info "Assigning IAM roles to service account..."
for role in "${IAM_ROLES[@]}"; do
  info "  Granting $role..."
  run_cmd gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
    --role="$role" \
    --condition=None \
    --quiet
done

success "Service account configured with all required roles"

# =============================================================================
# SECTION 4: VERTEX AI SEARCH - DATASTORE CREATION
# =============================================================================

echo ""
info "=== Section 4: Vertex AI Search - Datastore Creation ==="

# Discovery Engine datastore creation requires REST API since gcloud does not
# have full CLI support for this resource type yet.

DATASTORE_ID="${DATASTORE_DISPLAY_NAME}"

if [ "$DRY_RUN" = true ]; then
  echo -e "${YELLOW}[DRY-RUN]${NC} Would create Discovery Engine datastore via REST API:"
  echo "  POST https://discoveryengine.googleapis.com/v1/projects/${PROJECT_ID}/locations/${DATASTORE_LOCATION}/collections/default_collection/dataStores?dataStoreId=${DATASTORE_ID}"
  echo "  Body: { displayName: ${DATASTORE_DISPLAY_NAME}, industryVertical: GENERIC, solutionTypes: [SOLUTION_TYPE_SEARCH], contentConfig: CONTENT_REQUIRED }"
else
  ACCESS_TOKEN=$(gcloud auth print-access-token)

  # Check if datastore already exists
  DATASTORE_CHECK=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    "https://discoveryengine.googleapis.com/v1/projects/${PROJECT_ID}/locations/${DATASTORE_LOCATION}/collections/default_collection/dataStores/${DATASTORE_ID}")

  if [ "$DATASTORE_CHECK" = "200" ]; then
    info "Datastore '${DATASTORE_ID}' already exists, skipping creation."
  else
    info "Creating Discovery Engine datastore '${DATASTORE_DISPLAY_NAME}'..."

    DATASTORE_RESPONSE=$(curl -s -X POST \
      -H "Authorization: Bearer ${ACCESS_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{
        \"displayName\": \"${DATASTORE_DISPLAY_NAME}\",
        \"industryVertical\": \"GENERIC\",
        \"solutionTypes\": [\"SOLUTION_TYPE_SEARCH\"],
        \"contentConfig\": \"CONTENT_REQUIRED\",
        \"documentProcessingConfig\": {
          \"defaultParsingConfig\": {
            \"digitalParsingConfig\": {}
          }
        }
      }" \
      "https://discoveryengine.googleapis.com/v1/projects/${PROJECT_ID}/locations/${DATASTORE_LOCATION}/collections/default_collection/dataStores?dataStoreId=${DATASTORE_ID}")

    # Check for errors in response
    if echo "$DATASTORE_RESPONSE" | grep -q '"error"'; then
      error "Failed to create datastore. Response:"
      echo "$DATASTORE_RESPONSE"
      exit 1
    fi

    success "Datastore creation initiated"
    info "Waiting for datastore to be ready..."
    sleep 10
  fi

  # Apply schema to the datastore
  info "Applying schema to datastore..."

  SCHEMA_BODY='{
    "structSchema": {
      "type": "object",
      "properties": {
        "id": { "type": "string", "indexable": true, "retrievable": true, "searchable": false },
        "title": { "type": "string", "indexable": true, "retrievable": true, "searchable": true, "completable": true },
        "content": { "type": "string", "indexable": true, "retrievable": true, "searchable": true },
        "language": { "type": "string", "indexable": true, "retrievable": true, "searchable": false },
        "tier_access": { "type": "string", "indexable": true, "retrievable": true, "searchable": false },
        "source_url": { "type": "string", "indexable": false, "retrievable": true, "searchable": false },
        "last_updated": { "type": "string", "indexable": true, "retrievable": true, "searchable": false }
      }
    }
  }'

  SCHEMA_RESPONSE=$(curl -s -X PATCH \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$SCHEMA_BODY" \
    "https://discoveryengine.googleapis.com/v1/projects/${PROJECT_ID}/locations/${DATASTORE_LOCATION}/collections/default_collection/dataStores/${DATASTORE_ID}/schemas/default_schema")

  if echo "$SCHEMA_RESPONSE" | grep -q '"error"'; then
    warning "Schema update returned an error (this may be normal if schema already exists):"
    echo "$SCHEMA_RESPONSE"
  else
    success "Schema applied to datastore"
  fi
fi

# =============================================================================
# SECTION 5: VERTEX AI SEARCH - SEARCH ENGINE/APP CREATION
# =============================================================================

echo ""
info "=== Section 5: Vertex AI Search - Search Engine/App Creation ==="

SEARCH_ENGINE_ID="${SEARCH_ENGINE_DISPLAY_NAME}"

if [ "$DRY_RUN" = true ]; then
  echo -e "${YELLOW}[DRY-RUN]${NC} Would create Discovery Engine search engine via REST API:"
  echo "  POST https://discoveryengine.googleapis.com/v1/projects/${PROJECT_ID}/locations/${DATASTORE_LOCATION}/collections/default_collection/engines?engineId=${SEARCH_ENGINE_ID}"
  echo "  Body: { displayName: ${SEARCH_ENGINE_DISPLAY_NAME}, solutionType: SOLUTION_TYPE_SEARCH, dataStoreIds: [${DATASTORE_ID}] }"
else
  ACCESS_TOKEN=$(gcloud auth print-access-token)

  # Check if search engine already exists
  ENGINE_CHECK=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    "https://discoveryengine.googleapis.com/v1/projects/${PROJECT_ID}/locations/${DATASTORE_LOCATION}/collections/default_collection/engines/${SEARCH_ENGINE_ID}")

  if [ "$ENGINE_CHECK" = "200" ]; then
    info "Search engine '${SEARCH_ENGINE_ID}' already exists, skipping creation."
  else
    info "Creating Discovery Engine search engine '${SEARCH_ENGINE_DISPLAY_NAME}'..."

    ENGINE_RESPONSE=$(curl -s -X POST \
      -H "Authorization: Bearer ${ACCESS_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{
        \"displayName\": \"${SEARCH_ENGINE_DISPLAY_NAME}\",
        \"solutionType\": \"SOLUTION_TYPE_SEARCH\",
        \"searchEngineConfig\": {
          \"searchTier\": \"SEARCH_TIER_STANDARD\",
          \"searchAddOns\": [\"SEARCH_ADD_ON_LLM\"]
        },
        \"dataStoreIds\": [\"${DATASTORE_ID}\"]
      }" \
      "https://discoveryengine.googleapis.com/v1/projects/${PROJECT_ID}/locations/${DATASTORE_LOCATION}/collections/default_collection/engines?engineId=${SEARCH_ENGINE_ID}")

    if echo "$ENGINE_RESPONSE" | grep -q '"error"'; then
      error "Failed to create search engine. Response:"
      echo "$ENGINE_RESPONSE"
      exit 1
    fi

    success "Search engine creation initiated"
  fi
fi

# =============================================================================
# SECTION 6: ARTIFACT REGISTRY
# =============================================================================

echo ""
info "=== Section 6: Artifact Registry Setup ==="

REPO_NAME="syrabit"

# Check if repository already exists
if gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" --project="$PROJECT_ID" &>/dev/null 2>&1; then
  info "Artifact Registry repository '$REPO_NAME' already exists in $REGION."
else
  info "Creating Artifact Registry Docker repository '$REPO_NAME' in $REGION..."
  run_cmd gcloud artifacts repositories create "$REPO_NAME" \
    --repository-format=docker \
    --location="$REGION" \
    --description="Docker images for Syrabit backend" \
    --project="$PROJECT_ID"
fi

success "Artifact Registry configured"
info "Docker image path: ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/backend:latest"

# =============================================================================
# SECTION 7: SERVICE ACCOUNT KEY GENERATION
# =============================================================================

echo ""
info "=== Section 7: Service Account Key Generation ==="

if [ "$DRY_RUN" = true ]; then
  echo -e "${YELLOW}[DRY-RUN]${NC} Would generate service account key to: $KEY_FILE_PATH"
else
  if [ -f "$KEY_FILE_PATH" ]; then
    warning "Key file already exists at '$KEY_FILE_PATH'. Skipping generation."
    warning "Delete the existing file and re-run if you need a new key."
  else
    info "Generating service account key..."
    gcloud iam service-accounts keys create "$KEY_FILE_PATH" \
      --iam-account="$SERVICE_ACCOUNT_EMAIL" \
      --project="$PROJECT_ID"

    chmod 600 "$KEY_FILE_PATH"
    success "Service account key saved to: $KEY_FILE_PATH"
    warning "Keep this file secure! Do not commit it to version control."
  fi
fi

# =============================================================================
# SECTION 8: OUTPUT SUMMARY
# =============================================================================

echo ""
echo "============================================================================="
echo -e "${GREEN} SETUP COMPLETE - Environment Variables for .env${NC}"
echo "============================================================================="
echo ""
echo "Add the following to your .env file (apps/backend/.env):"
echo ""
echo "# GCP / Vertex AI Configuration"
echo "VERTEX_PROJECT_ID=${PROJECT_ID}"
echo "VERTEX_SEARCH_DATASTORE_ID=${DATASTORE_ID}"
echo "VERTEX_SEARCH_SERVING_CONFIG=default_search"
echo "VERTEX_SEARCH_LOCATION=${DATASTORE_LOCATION}"
echo "GOOGLE_APPLICATION_CREDENTIALS_JSON=$(cat "$KEY_FILE_PATH" 2>/dev/null | tr -d '\n' || echo "<content of ${KEY_FILE_PATH}>")"
echo "VERTEX_GEMINI_MODEL=gemini-2.0-flash-lite"
echo "VERTEX_LOCATION=${REGION}"
echo ""
echo "============================================================================="
echo -e "${GREEN} Service Account Key File${NC}"
echo "============================================================================="
echo ""
echo "  Path: ${KEY_FILE_PATH}"
echo "  Email: ${SERVICE_ACCOUNT_EMAIL}"
echo ""
echo "  For GOOGLE_APPLICATION_CREDENTIALS_JSON, either:"
echo "    1. Paste the entire JSON content of ${KEY_FILE_PATH} as the env var value"
echo "    2. Or set GOOGLE_APPLICATION_CREDENTIALS to the file path instead"
echo ""
echo "============================================================================="
echo -e "${GREEN} Next Steps${NC}"
echo "============================================================================="
echo ""
echo "  1. Deploy the search schema:"
echo "     python infra/scripts/deploy-search-index.py"
echo ""
echo "  2. Seed initial data into the datastore:"
echo "     python infra/scripts/seed-search.py"
echo ""
echo "  3. Build and push the Docker image:"
echo "     docker build -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/backend:latest ./apps/backend"
echo "     docker push ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/backend:latest"
echo ""
echo "  4. Deploy to Cloud Run:"
echo "     gcloud run services replace infra/gcp/clouddeploy.yaml --region=${REGION}"
echo ""
echo "============================================================================="

success "All done! Your GCP infrastructure for Syrabit is ready."
