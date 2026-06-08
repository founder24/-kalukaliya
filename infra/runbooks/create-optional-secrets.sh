#!/usr/bin/env bash
# =============================================================================
# create-optional-secrets.sh
# =============================================================================
# Creates the 6 optional GCP Secret Manager secrets that the GitHub Actions
# "Attach optional secrets" step needs so those features are enabled in prod.
#
# Features gated behind these secrets:
#   upstash-redis-rest-url / upstash-redis-rest-token  — response caching
#   posthog-api-key                                    — analytics
#   indexnow-api-key / indexnow-internal-secret        — SEO pinging
#   VERTEX_SEARCH_DATASTORE_ID                         — RAG/search
#
# Usage (run in GCP Cloud Shell from any directory):
#   curl -fsSL https://raw.githubusercontent.com/founder24/-kalukaliya/main/infra/runbooks/create-optional-secrets.sh | bash
#   OR clone the repo and run:
#   bash infra/runbooks/create-optional-secrets.sh
#
# The script is IDEMPOTENT — it skips secrets that already exist.
# If an uppercase variant already exists it copies the value automatically.
# =============================================================================

set -euo pipefail

PROJECT="blissful-acumen-495019-t6"
SA="syrabit-backend-sa@${PROJECT}.iam.gserviceaccount.com"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC}   $1"; }
skip() { echo -e "${YELLOW}[SKIP]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERR]${NC}  $1" >&2; }
hdr()  { echo -e "\n${CYAN}══ $1 ══${NC}"; }

# _secret_exists SECRET_NAME
# Returns 0 if the secret exists, 1 otherwise
_secret_exists() {
  gcloud secrets describe "$1" --project="$PROJECT" --quiet 2>/dev/null
}

# _get_value SECRET_NAME
# Prints the latest version value of an existing secret
_get_value() {
  gcloud secrets versions access latest \
    --secret="$1" \
    --project="$PROJECT" 2>/dev/null
}

# _create_or_skip TARGET_SECRET_NAME FALLBACK_UPPERCASE_NAME DESCRIPTION
# If TARGET already exists → skip.
# Else if FALLBACK exists   → copy value from FALLBACK.
# Else                      → print a manual-fill instruction and continue.
_create_or_skip() {
  local target="$1"
  local fallback="$2"
  local desc="$3"

  if _secret_exists "$target"; then
    skip "$target already exists — skipping"
    return 0
  fi

  local value=""

  if [[ -n "$fallback" ]] && _secret_exists "$fallback"; then
    warn "$target not found, but $fallback exists — copying value"
    value=$(_get_value "$fallback")
  fi

  if [[ -z "$value" ]]; then
    err "$target not found and no fallback value available."
    echo    "  ► You must create it manually:"
    echo    "      printf 'YOUR_VALUE' | gcloud secrets create $target \\"
    echo    "        --data-file=- --project=$PROJECT"
    echo    "  ► $desc"
    MANUAL_REQUIRED+=("$target")
    return 0
  fi

  printf '%s' "$value" | gcloud secrets create "$target" \
    --data-file=- \
    --project="$PROJECT"

  ok "Created $target (value copied from $fallback)"

  # Grant access to the Cloud Run SA
  gcloud secrets add-iam-policy-binding "$target" \
    --member="serviceAccount:${SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$PROJECT" \
    --quiet
  ok "  → SA access granted"
}

# =============================================================================
# PREFLIGHT
# =============================================================================

hdr "Preflight"

if ! command -v gcloud &>/dev/null; then
  err "gcloud CLI not found. Run this in GCP Cloud Shell."
  exit 1
fi

ACTIVE=$(gcloud auth list --filter="status:ACTIVE" --format="value(account)" 2>/dev/null | head -1)
if [[ -z "$ACTIVE" ]]; then
  err "Not authenticated. Run: gcloud auth login"
  exit 1
fi
ok "Authenticated as: $ACTIVE"
ok "Target project : $PROJECT"

MANUAL_REQUIRED=()

# =============================================================================
# 1. Upstash Redis REST URL
# =============================================================================

hdr "1/6  upstash-redis-rest-url"
_create_or_skip \
  "upstash-redis-rest-url" \
  "UPSTASH_REDIS_REST_URL" \
  "Upstash Console → Database → REST API → UPSTASH_REDIS_REST_URL value (https://...upstash.io)"

# =============================================================================
# 2. Upstash Redis REST Token
# =============================================================================

hdr "2/6  upstash-redis-rest-token"
_create_or_skip \
  "upstash-redis-rest-token" \
  "UPSTASH_REDIS_REST_TOKEN" \
  "Upstash Console → Database → REST API → UPSTASH_REDIS_REST_TOKEN value"

# =============================================================================
# 3. PostHog API Key
# =============================================================================

hdr "3/6  posthog-api-key"
_create_or_skip \
  "posthog-api-key" \
  "POSTHOG_API_KEY" \
  "PostHog → Project Settings → Project API Key (phc_...)"

# =============================================================================
# 4. IndexNow API Key
# =============================================================================

hdr "4/6  indexnow-api-key"
_create_or_skip \
  "indexnow-api-key" \
  "INDEXNOW_API_KEY" \
  "IndexNow key — a random alphanumeric string; also served at /syrabit.txt on the domain"

# =============================================================================
# 5. IndexNow Internal Secret
# =============================================================================

hdr "5/6  indexnow-internal-secret"
_create_or_skip \
  "indexnow-internal-secret" \
  "INDEXNOW_INTERNAL_SECRET" \
  "Internal HMAC secret used to verify IndexNow requests — any strong random string"

# =============================================================================
# 6. Vertex Search Datastore ID
# =============================================================================

hdr "6/6  VERTEX_SEARCH_DATASTORE_ID"
_create_or_skip \
  "VERTEX_SEARCH_DATASTORE_ID" \
  "" \
  "GCP Console → Vertex AI Search → Data Stores → syrabit-edu-datastore → copy the Datastore ID (looks like: syrabit-edu-datastore_XXXXXXXXXX)"

# =============================================================================
# SUMMARY
# =============================================================================

echo ""
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  Done — optional secrets setup${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo ""

if [[ ${#MANUAL_REQUIRED[@]} -eq 0 ]]; then
  ok "All 6 secrets are present in Secret Manager."
  echo ""
  echo "  Next: trigger a GitHub Actions deploy and verify the"
  echo "  'Attach optional secrets' step shows:"
  echo "    Attaching optional secrets: UPSTASH_REDIS_REST_URL=upstash-redis-rest-url:latest,..."
  echo "  (no ⚠ lines)"
else
  warn "${#MANUAL_REQUIRED[@]} secret(s) still need manual values:"
  for s in "${MANUAL_REQUIRED[@]}"; do
    echo "    • $s"
  done
  echo ""
  echo "  Fill in those values and re-run this script — it will skip"
  echo "  the ones that already exist and only create the missing ones."
fi
echo ""
