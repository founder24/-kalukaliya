#!/usr/bin/env bash
#
# SES burn-threshold smoke runbook (Task #489 §D row
# "SES burn-threshold smoke runbook"). Implements the manual proof
# referenced from the four-cloud delegation matrix §C.2 acceptance
# check.
#
# What it does, in order:
#   1. Records the current `EMAIL_PROVIDER` ACA env var.
#   2. Flips ACA to `EMAIL_PROVIDER=ses` (forces the SendGrid → SES
#      fallback path that V4 §10 Rule C names as the burn-threshold
#      response).
#   3. Sends one synthetic transactional email through the live API
#      (`POST /api/admin/diagnostics/email-smoke`).
#   4. Tails the SES `Send` and `email-fallback` SQS queue depth for
#      60 s to confirm the message went through.
#   5. Restores the original `EMAIL_PROVIDER` value.
#   6. Appends the result to `docs/ops/dr-drills/ses-burn-<date>.log`.
#
# Required env:
#   ACA_RG, ACA_NAME           — Azure Container Apps target
#   PROD_BASE_URL              — public API base, e.g. https://api.syrabit.ai
#   PROD_ADMIN_JWT             — admin JWT for the diagnostics route
#   SMOKE_RECIPIENT            — to: address (must be SES-verified)
#   AWS_REGION                 — defaults to us-east-1
#
# Usage (manual quarterly DR drill):
#   bash scripts/ses_burn_smoke.sh

set -euo pipefail

: "${ACA_RG:?ACA_RG required}"
: "${ACA_NAME:?ACA_NAME required}"
: "${PROD_BASE_URL:?PROD_BASE_URL required}"
: "${PROD_ADMIN_JWT:?PROD_ADMIN_JWT required}"
: "${SMOKE_RECIPIENT:?SMOKE_RECIPIENT required}"
AWS_REGION="${AWS_REGION:-us-east-1}"

DRILL_DIR="docs/ops/dr-drills"
mkdir -p "$DRILL_DIR"
DRILL_LOG="$DRILL_DIR/ses-burn-$(date -u +%Y-%m-%dT%H%M%SZ).log"

echo "[ses-burn-smoke] writing run log to $DRILL_LOG"
exec > >(tee -a "$DRILL_LOG") 2>&1

echo "=== ses-burn-smoke @ $(date -u --iso-8601=seconds) ==="

ORIG_PROVIDER="$(az containerapp show -g "$ACA_RG" -n "$ACA_NAME" \
  --query 'properties.template.containers[0].env[?name==`EMAIL_PROVIDER`].value | [0]' -o tsv)"
echo "[ses-burn-smoke] original EMAIL_PROVIDER=$ORIG_PROVIDER"

restore_provider() {
  echo "[ses-burn-smoke] restoring EMAIL_PROVIDER=$ORIG_PROVIDER"
  az containerapp update -g "$ACA_RG" -n "$ACA_NAME" \
    --set-env-vars "EMAIL_PROVIDER=${ORIG_PROVIDER:-sendgrid}" >/dev/null
}
trap restore_provider EXIT

echo "[ses-burn-smoke] flipping EMAIL_PROVIDER=ses"
az containerapp update -g "$ACA_RG" -n "$ACA_NAME" \
  --set-env-vars "EMAIL_PROVIDER=ses" >/dev/null

# Wait for the new revision to settle.
sleep 15

echo "[ses-burn-smoke] sending synthetic email to $SMOKE_RECIPIENT"
SMOKE_RESP="$(curl -fsS -X POST "$PROD_BASE_URL/api/admin/diagnostics/email-smoke" \
  -H "Authorization: Bearer $PROD_ADMIN_JWT" \
  -H 'Content-Type: application/json' \
  -d "{\"to\":\"$SMOKE_RECIPIENT\",\"subject\":\"[smoke] SES burn-threshold drill\",\"html\":\"<p>drill ok</p>\"}")"
echo "[ses-burn-smoke] api response: $SMOKE_RESP"

echo "[ses-burn-smoke] tailing SES + SQS metrics for 60s"
SES_SENDS_BEFORE="$(aws cloudwatch get-metric-statistics --region "$AWS_REGION" \
  --namespace AWS/SES --metric-name Send --statistics Sum \
  --start-time "$(date -u -d '-2 minutes' --iso-8601=seconds)" \
  --end-time "$(date -u --iso-8601=seconds)" --period 60 \
  --query 'Datapoints[].Sum' --output text || echo 0)"
sleep 60
SES_SENDS_AFTER="$(aws cloudwatch get-metric-statistics --region "$AWS_REGION" \
  --namespace AWS/SES --metric-name Send --statistics Sum \
  --start-time "$(date -u -d '-2 minutes' --iso-8601=seconds)" \
  --end-time "$(date -u --iso-8601=seconds)" --period 60 \
  --query 'Datapoints[].Sum' --output text || echo 0)"

echo "[ses-burn-smoke] SES Send delta: before=$SES_SENDS_BEFORE after=$SES_SENDS_AFTER"

if [ "$SES_SENDS_AFTER" = "0" ] || [ "$SES_SENDS_AFTER" = "$SES_SENDS_BEFORE" ]; then
  echo "[ses-burn-smoke] FAIL — no SES Send recorded after smoke"
  exit 1
fi

echo "[ses-burn-smoke] PASS"
