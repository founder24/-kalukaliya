#!/usr/bin/env bash
# scripts/gcp_api_audit.sh — Audit which Google Cloud APIs are enabled on the
# project that backs the Syrabit Vertex / Cloud Run / Cloud Storage stack.
#
# Usage:
#   PROJECT_ID=syrabit-prod ./scripts/gcp_api_audit.sh
#   ./scripts/gcp_api_audit.sh syrabit-prod
#
# Falls back to `gcloud config get-value project` when no PROJECT_ID is given.
# Lists every API the credit-weighted delegation matrix
# (docs/infra/provider-credit-matrix.md) expects to be enabled and prints a
# tick / cross next to each so disabled APIs are visible at a glance.
#
# Exit code is the number of expected APIs that are *not* enabled (capped at
# 125), so this is safe to invoke from CI as a non-blocking check.

set -u
set -o pipefail

PROJECT_ID="${PROJECT_ID:-${1:-}}"
if [[ -z "${PROJECT_ID}" ]]; then
  PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
fi

if [[ -z "${PROJECT_ID}" ]]; then
  echo "ERROR: no PROJECT_ID supplied and 'gcloud config get-value project' is empty." >&2
  echo "Usage: PROJECT_ID=<id> $0   |   $0 <project-id>" >&2
  exit 2
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "ERROR: gcloud CLI not found on PATH. Install via https://cloud.google.com/sdk/docs/install" >&2
  exit 2
fi

# APIs the credit-weighted delegation matrix expects to be enabled.
# Keep this list in sync with docs/infra/provider-credit-matrix.md.
EXPECTED_APIS=(
  # Vertex AI / Gemini / generative
  aiplatform.googleapis.com
  generativelanguage.googleapis.com
  # Cloud Run / Cloud Build / Artifact Registry (compute)
  run.googleapis.com
  cloudbuild.googleapis.com
  artifactregistry.googleapis.com
  # Cloud Storage / object-storage
  storage.googleapis.com
  storage-component.googleapis.com
  # Cloud CDN (attached to LB)
  compute.googleapis.com
  # Cloud Scheduler / Cloud Tasks (scheduler / queue)
  cloudscheduler.googleapis.com
  cloudtasks.googleapis.com
  # Pub/Sub (queue alt)
  pubsub.googleapis.com
  # BigQuery (billing export + analytics)
  bigquery.googleapis.com
  bigquerystorage.googleapis.com
  # Cloud Billing + Billing Budgets
  cloudbilling.googleapis.com
  billingbudgets.googleapis.com
  # Cloud Logging / Monitoring / Trace (log-storage + observability)
  logging.googleapis.com
  monitoring.googleapis.com
  cloudtrace.googleapis.com
  # Speech / Translation / Vision / TTS (vertex-adjacent managed APIs)
  speech.googleapis.com
  texttospeech.googleapis.com
  translate.googleapis.com
  vision.googleapis.com
  # IAM + Service Usage (table-stakes)
  iam.googleapis.com
  iamcredentials.googleapis.com
  serviceusage.googleapis.com
  # Secret Manager (creds for SA-backed providers)
  secretmanager.googleapis.com
)

echo "==> GCP API enablement audit"
echo "    project: ${PROJECT_ID}"
echo "    date:    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

ENABLED_LIST="$(gcloud services list --enabled --project "${PROJECT_ID}" \
  --format='value(config.name)' 2>/dev/null || true)"

if [[ -z "${ENABLED_LIST}" ]]; then
  echo "ERROR: 'gcloud services list --enabled' returned no rows." >&2
  echo "       Check that the active credentials have serviceusage.services.list on ${PROJECT_ID}." >&2
  exit 2
fi

missing=0
echo "Expected API                                  Status"
echo "--------------------------------------------- --------"
for api in "${EXPECTED_APIS[@]}"; do
  if grep -qx "${api}" <<<"${ENABLED_LIST}"; then
    printf "%-45s ENABLED\n" "${api}"
  else
    printf "%-45s DISABLED\n" "${api}"
    missing=$((missing + 1))
  fi
done

echo
echo "==> Summary: ${missing} of ${#EXPECTED_APIS[@]} expected APIs disabled."

# Also dump the full enabled list so the audit captures any API that's on but
# not in the expected set (useful for spotting cost surprises).
echo
echo "==> Full enabled-API list (for reference):"
echo "${ENABLED_LIST}" | sort

# Cap exit code so callers can use it as a count without blowing past 255.
if (( missing > 125 )); then
  exit 125
fi
exit "${missing}"
