#!/usr/bin/env bash
#
# SES burn-threshold smoke runbook (Task #556 rewrite — SES is the sole
# transactional provider; the legacy `EMAIL_PROVIDER=sendgrid|ses` flip
# is retired).
#
# What it does, in order:
#   1. Records the current `SES_REGION` ACA env var.
#   2. Flips ACA to the secondary verified region (us-east-1 ↔ ap-south-1)
#      so the on-call can prove SES failover works without leaving the
#      single-provider contract.
#   3. Sends one synthetic transactional email through the live API
#      (`POST /api/admin/diagnostics/email-smoke`).
#   4. Tails the SES `Send` CloudWatch metric for 60 s in the new region
#      to confirm the message went through.
#   5. Restores the original `SES_REGION` value.
#   6. Appends the result to `docs/ops/dr-drills/ses-burn-<date>.log`.
#
# Required env:
#   ACA_RG, ACA_NAME           — Azure Container Apps target
#   PROD_BASE_URL              — public API base, e.g. https://api.syrabit.ai
#   PROD_ADMIN_JWT             — admin JWT for the diagnostics route
#   SMOKE_RECIPIENT            — to: address (must be SES-verified)
#   SMOKE_SES_REGION_FLIP_TO   — region to flip to (default: ap-south-1)
#
# Usage (manual quarterly DR drill):
#   bash scripts/ses_burn_smoke.sh

set -euo pipefail

: "${ACA_RG:?ACA_RG required}"
: "${ACA_NAME:?ACA_NAME required}"
: "${PROD_BASE_URL:?PROD_BASE_URL required}"
: "${PROD_ADMIN_JWT:?PROD_ADMIN_JWT required}"
: "${SMOKE_RECIPIENT:?SMOKE_RECIPIENT required}"
FLIP_TO="${SMOKE_SES_REGION_FLIP_TO:-ap-south-1}"

DRILL_DIR="docs/ops/dr-drills"
mkdir -p "$DRILL_DIR"
DRILL_LOG="$DRILL_DIR/ses-burn-$(date -u +%Y-%m-%dT%H%M%SZ).log"

echo "[ses-burn-smoke] writing run log to $DRILL_LOG"
exec > >(tee -a "$DRILL_LOG") 2>&1

echo "=== ses-burn-smoke @ $(date -u --iso-8601=seconds) ==="

ORIG_REGION="$(az containerapp show -g "$ACA_RG" -n "$ACA_NAME" \
  --query 'properties.template.containers[0].env[?name==`SES_REGION`].value | [0]' -o tsv)"
echo "[ses-burn-smoke] original SES_REGION=$ORIG_REGION"

restore_region() {
  echo "[ses-burn-smoke] restoring SES_REGION=$ORIG_REGION"
  az containerapp update -g "$ACA_RG" -n "$ACA_NAME" \
    --set-env-vars "SES_REGION=${ORIG_REGION:-us-east-1}" >/dev/null
}
trap restore_region EXIT

echo "[ses-burn-smoke] flipping SES_REGION=$FLIP_TO"
az containerapp update -g "$ACA_RG" -n "$ACA_NAME" \
  --set-env-vars "SES_REGION=$FLIP_TO" >/dev/null

# Wait for the new revision to settle.
sleep 15

echo "[ses-burn-smoke] sending synthetic email to $SMOKE_RECIPIENT"
SMOKE_RESP="$(curl -fsS -X POST "$PROD_BASE_URL/api/admin/diagnostics/email-smoke" \
  -H "Authorization: Bearer $PROD_ADMIN_JWT" \
  -H 'Content-Type: application/json' \
  -d "{\"to\":\"$SMOKE_RECIPIENT\",\"subject\":\"[smoke] SES burn-threshold drill\",\"html\":\"<p>drill ok</p>\"}")"
echo "[ses-burn-smoke] api response: $SMOKE_RESP"

echo "[ses-burn-smoke] tailing SES Send metric in $FLIP_TO for 60s"
SES_SENDS_BEFORE="$(aws cloudwatch get-metric-statistics --region "$FLIP_TO" \
  --namespace AWS/SES --metric-name Send --statistics Sum \
  --start-time "$(date -u -d '-2 minutes' --iso-8601=seconds)" \
  --end-time "$(date -u --iso-8601=seconds)" --period 60 \
  --query 'Datapoints[].Sum' --output text || echo 0)"
sleep 60
SES_SENDS_AFTER="$(aws cloudwatch get-metric-statistics --region "$FLIP_TO" \
  --namespace AWS/SES --metric-name Send --statistics Sum \
  --start-time "$(date -u -d '-2 minutes' --iso-8601=seconds)" \
  --end-time "$(date -u --iso-8601=seconds)" --period 60 \
  --query 'Datapoints[].Sum' --output text || echo 0)"

echo "[ses-burn-smoke] SES Send delta: before=$SES_SENDS_BEFORE after=$SES_SENDS_AFTER"

if [ "$SES_SENDS_AFTER" = "0" ] || [ "$SES_SENDS_AFTER" = "$SES_SENDS_BEFORE" ]; then
  echo "[ses-burn-smoke] FAIL — no SES Send recorded in $FLIP_TO after smoke"
  exit 1
fi

echo "[ses-burn-smoke] PASS"
