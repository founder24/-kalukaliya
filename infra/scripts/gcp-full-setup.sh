#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# gcp-full-setup.sh — Run once in Cloud Shell to wire ALL GCP permissions
# for syrabit-backend-sa and create any missing Secret Manager secrets.
#
# Usage:
#   bash infra/scripts/gcp-full-setup.sh
#
# What this does:
#   1. Grants syrabit-backend-sa all required IAM roles (idempotent)
#   2. Grants Cloud Build SA permission to deploy as the backend SA
#   3. Ensures Vertex AI, Discovery Engine, GCS, Secret Manager APIs are enabled
#   4. Creates missing optional secrets in Secret Manager (prompts for values)
#   5. Grants the backend SA access to its own Secret Manager secrets
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT="blissful-acumen-495019-t6"
PROJECT_NUM="851687450401"
REGION="asia-south1"
SA="syrabit-backend-sa@${PROJECT}.iam.gserviceaccount.com"
CB_SA="${PROJECT_NUM}@cloudbuild.gserviceaccount.com"

G="\033[92m"; R="\033[91m"; Y="\033[93m"; B="\033[94m"; X="\033[0m"

echo -e "${B}=== Syrabit GCP Full Setup ===${X}"
echo "Project : $PROJECT"
echo "SA      : $SA"
echo ""

# ── 1. Enable required APIs ───────────────────────────────────────────────────
echo -e "${B}[1/5] Enabling required APIs...${X}"
APIS=(
  run.googleapis.com
  cloudbuild.googleapis.com
  artifactregistry.googleapis.com
  secretmanager.googleapis.com
  aiplatform.googleapis.com
  discoveryengine.googleapis.com
  storage.googleapis.com
  iam.googleapis.com
  cloudresourcemanager.googleapis.com
  logging.googleapis.com
  monitoring.googleapis.com
  cloudtrace.googleapis.com
)
gcloud services enable "${APIS[@]}" --project="$PROJECT" --quiet
echo -e "  ${G}✓ All APIs enabled${X}"

# ── 2. Grant backend SA all required project-level IAM roles ──────────────────
echo -e "\n${B}[2/5] Granting IAM roles to $SA ...${X}"
ROLES=(
  # Vertex AI: model inference (Gemini text + vision + TTS)
  "roles/aiplatform.user"
  # Vertex AI Search / Discovery Engine: index + search documents
  "roles/discoveryengine.admin"
  # Cloud Storage: read/write educational content bucket
  "roles/storage.objectAdmin"
  # Secret Manager: read secrets at runtime (Cloud Run mounts)
  "roles/secretmanager.secretAccessor"
  # Cloud Run: allow identity-token callers (Edge Worker OIDC) to invoke
  "roles/run.invoker"
  # Logging / Tracing / Metrics
  "roles/logging.logWriter"
  "roles/cloudtrace.agent"
  "roles/monitoring.metricWriter"
  # Service usage (required to make API calls against the project)
  "roles/serviceusage.serviceUsageConsumer"
)
for ROLE in "${ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:$SA" \
    --role="$ROLE" \
    --quiet 2>/dev/null && echo -e "  ${G}✓${X} $ROLE" || echo -e "  ${Y}⚠${X} $ROLE (already bound or error)"
done

# ── 3. Grant Cloud Build SA permission to use the backend SA ──────────────────
echo -e "\n${B}[3/5] Granting Cloud Build SA permission to act as backend SA...${X}"
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --member="serviceAccount:$CB_SA" \
  --role="roles/iam.serviceAccountUser" \
  --project="$PROJECT" \
  --quiet && echo -e "  ${G}✓${X} Cloud Build SA → serviceAccountUser on backend SA"

# Cloud Build SA also needs to deploy Cloud Run
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$CB_SA" \
  --role="roles/run.admin" \
  --quiet && echo -e "  ${G}✓${X} Cloud Build SA → roles/run.admin"

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$CB_SA" \
  --role="roles/secretmanager.secretAccessor" \
  --quiet && echo -e "  ${G}✓${X} Cloud Build SA → roles/secretmanager.secretAccessor"

# ── 4. Create missing optional Secret Manager secrets ────────────────────────
echo -e "\n${B}[4/5] Checking optional Secret Manager secrets...${X}"

create_secret_if_missing() {
  local SECRET_NAME="$1"
  local PROMPT="$2"
  local DEFAULT="${3:-}"

  if gcloud secrets describe "$SECRET_NAME" --project="$PROJECT" >/dev/null 2>&1; then
    echo -e "  ${G}✓${X} $SECRET_NAME (already exists)"
    return
  fi

  echo ""
  echo -e "  ${Y}⚠${X} $SECRET_NAME not found."
  echo "    $PROMPT"
  if [ -n "$DEFAULT" ]; then
    read -r -p "    Enter value (or press Enter to skip): " VALUE
  else
    read -r -p "    Enter value (required — or press Enter to skip): " VALUE
  fi

  if [ -z "$VALUE" ]; then
    echo -e "    ${Y}Skipped${X} — create later with:"
    echo "    echo -n 'VALUE' | gcloud secrets create $SECRET_NAME --replication-policy=automatic --data-file=- --project=$PROJECT"
    return
  fi

  echo -n "$VALUE" | gcloud secrets create "$SECRET_NAME" \
    --replication-policy=automatic \
    --data-file=- \
    --project="$PROJECT" \
    --quiet
  echo -e "  ${G}✓${X} $SECRET_NAME created"
}

create_secret_if_missing "ADMIN_EMAIL" \
  "Admin account email (e.g. admin@syrabit.ai)"

create_secret_if_missing "ADMIN_PASSWORD" \
  "Admin account password (min 12 chars, used for first-run bootstrap)"

create_secret_if_missing "CF_PAGES_DEPLOY_HOOK" \
  "Cloudflare Pages deploy hook URL (from CF Pages → Settings → Deploy Hooks)"

create_secret_if_missing "GITHUB_DEPLOY_SSH_KEY" \
  "GitHub deploy SSH private key — run 'bash infra/scripts/setup-github-deploy-key.sh' first"

# ── 5. Grant backend SA access to ALL its own secrets ────────────────────────
echo -e "\n${B}[5/5] Granting backend SA secretAccessor on all project secrets...${X}"

ALL_SECRETS=$(gcloud secrets list --project="$PROJECT" --format="value(name)" 2>/dev/null || echo "")
COUNT=0
for SECRET_PATH in $ALL_SECRETS; do
  SECRET_NAME=$(basename "$SECRET_PATH")
  gcloud secrets add-iam-policy-binding "$SECRET_PATH" \
    --member="serviceAccount:$SA" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$PROJECT" \
    --quiet 2>/dev/null && COUNT=$((COUNT+1))
done
echo -e "  ${G}✓${X} Granted secretAccessor on $COUNT secrets"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${G}=== Setup complete! ===${X}"
echo ""
echo "Next steps:"
echo "  1. Push a commit to GitHub to trigger Cloud Build"
echo "     (or: gcloud builds submit --config cloudbuild.yaml --project=$PROJECT)"
echo ""
echo "  2. Set Cloudflare Worker secrets (Edge Worker → Cloud Run auth):"
echo "     bash infra/scripts/cloudflare-worker-secrets.sh"
echo ""
echo "  3. Verify backend health:"
echo "     curl https://syrabit-backend-bl6wu3psza-el.a.run.app/health"
echo "     curl https://api.syrabit.ai/health"
echo ""
echo "  4. To set up GitHub deploy key (optional, for SSH-based deploys):"
echo "     bash infra/scripts/setup-github-deploy-key.sh"
