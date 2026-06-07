#!/usr/bin/env bash
# setup-github-deploy-key.sh
#
# One-time setup: generate an ed25519 SSH deploy key, store the private half
# in GCP Secret Manager as GITHUB_DEPLOY_SSH_KEY, and grant both Cloud Build
# service accounts secretAccessor on it.
#
# Two SAs are granted access because the trigger may run as either one:
#   - Default Cloud Build SA  ({project_number}@cloudbuild.gserviceaccount.com)
#   - Custom trigger SA       (syrabit-backend-sa@blissful-acumen-495019-t6.iam.gserviceaccount.com)
#
# After this script finishes, follow the printed instructions to add the
# public key to GitHub (that step requires the GitHub UI).
#
# Usage (from Cloud Shell or any machine with gcloud authenticated):
#   bash infra/scripts/setup-github-deploy-key.sh
#
# For full context see: infra/runbooks/github-deploy-ssh-key-setup.md

set -euo pipefail

PROJECT="blissful-acumen-495019-t6"
SECRET_NAME="GITHUB_DEPLOY_SSH_KEY"
KEY_COMMENT="cloud-build@syrabit"
KEY_FILE="/tmp/syrabit_deploy_key"
BACKEND_SA="syrabit-backend-sa@blissful-acumen-495019-t6.iam.gserviceaccount.com"

echo ""
echo "=== Syrabit: GitHub Deploy SSH Key Setup ==="
echo ""

# ── Step 1: Generate key pair ────────────────────────────────────────────────
echo "Step 1/5 — Generating ed25519 key pair..."
if [[ -f "${KEY_FILE}" ]]; then
  echo "  Existing key found at ${KEY_FILE} — removing before regeneration."
  rm -f "${KEY_FILE}" "${KEY_FILE}.pub"
fi
ssh-keygen -t ed25519 -C "${KEY_COMMENT}" -f "${KEY_FILE}" -N ""
echo "  ✓ Key pair written to ${KEY_FILE} / ${KEY_FILE}.pub"
echo ""

# ── Step 2: Store private key in Secret Manager ──────────────────────────────
echo "Step 2/5 — Storing private key in GCP Secret Manager..."
if gcloud secrets describe "${SECRET_NAME}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "  Secret '${SECRET_NAME}' already exists — adding new version."
  gcloud secrets versions add "${SECRET_NAME}" \
    --data-file="${KEY_FILE}" \
    --project="${PROJECT}"
else
  echo "  Creating new secret '${SECRET_NAME}'."
  gcloud secrets create "${SECRET_NAME}" \
    --replication-policy=automatic \
    --data-file="${KEY_FILE}" \
    --project="${PROJECT}"
fi
echo "  ✓ Private key stored as ${SECRET_NAME}:latest"
echo ""

# ── Step 3: Grant secretAccessor to both Cloud Build SAs ─────────────────────
# The build trigger may run as either the default Cloud Build SA or the custom
# syrabit-backend-sa. Grant both so it works regardless of trigger config.
echo "Step 3/5 — Granting secretAccessor to Cloud Build service accounts..."

PROJECT_NUMBER=$(gcloud projects describe "${PROJECT}" \
  --format="value(projectNumber)")
DEFAULT_CB_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

for SA in "${DEFAULT_CB_SA}" "${BACKEND_SA}"; do
  gcloud secrets add-iam-policy-binding "${SECRET_NAME}" \
    --member="serviceAccount:${SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="${PROJECT}" \
    --quiet
  echo "  ✓ Granted roles/secretmanager.secretAccessor to ${SA}"
done
echo ""

# ── Step 4: Verify the IAM bindings ──────────────────────────────────────────
echo "Step 4/5 — Verifying IAM policy on ${SECRET_NAME}:"
gcloud secrets get-iam-policy "${SECRET_NAME}" \
  --project="${PROJECT}" \
  --format="table(bindings.role,bindings.members)"
echo ""

# ── Step 5: Print public key and instructions ─────────────────────────────────
echo "Step 5/5 — Public key (add this to GitHub):"
echo ""
echo "──────────────────────────────────────────────────────────────────────────"
cat "${KEY_FILE}.pub"
echo "──────────────────────────────────────────────────────────────────────────"
echo ""
echo "ACTION REQUIRED (manual, takes ~1 minute):"
echo ""
echo "  1. Go to: https://github.com/founder24/-kalukaliya/settings/keys/new"
echo "  2. Title:  Cloud Build (read-only)"
echo "  3. Key:    paste the line above"
echo "  4. Leave 'Allow write access' UNCHECKED"
echo "  5. Click 'Add key'"
echo ""
echo "After adding the key, trigger a Cloud Build and confirm Step 0 prints"
echo "'=== SSH setup complete ===' without errors."
echo ""
echo "=== GCP-side setup complete ==="
echo ""

# ── Cleanup: remove private key from /tmp ────────────────────────────────────
rm -f "${KEY_FILE}" "${KEY_FILE}.pub"
echo "(Temporary key files removed from /tmp)"
