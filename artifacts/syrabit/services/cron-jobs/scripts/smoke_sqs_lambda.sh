#!/usr/bin/env bash
# Task #332 — Phase 4 end-to-end smoke for the SQS+Lambda worker tier.
#
# For every queue in `infra/aws/sqs.tf`:
#   1. SendMessage with a synthetic payload tagged
#      `{"_smoke": true, "_smoke_id": "<uuid>"}` so the consumer can
#      short-circuit on the smoke flag without doing real work.
#   2. Wait `WAIT_S` seconds (Lambda is event-source-triggered, so it
#      should drain almost immediately).
#   3. Assert ApproximateNumberOfMessages == 0 AND the corresponding
#      Lambda's last `Errors` metric in the past 5 minutes is 0.
#
# Exits non-zero on the first failure.
#
# Usage:
#   AWS_REGION=ap-south-1 LZ_PROJECT=syrabit ./smoke_sqs_lambda.sh

set -euo pipefail

: "${AWS_REGION:?AWS_REGION is required}"
LZ_PROJECT="${LZ_PROJECT:-syrabit}"
WAIT_S="${WAIT_S:-30}"

# Mirror of `local.sqs_worker_queues` keys in infra/aws/sqs.tf. Keep
# this aligned with the Terraform source — the
# tests/test_admin_aws_infra_naming.py CI guard checks the backend
# side; this script is the deploy-side check.
QUEUES=(
  seo-indexnow
  seo-internal-linker
  discovery-engine-ingest
  bing-keyword-refresh
  bing-submit
  cf-bot-crosscheck
  unified-logs-cf-pull
  email-fallback
)

# Queue→AWS-name map (matches each.value.aws in sqs.tf).
declare -A AWS_NAME=(
  [seo-indexnow]=syrabit-seo-indexnow
  [seo-internal-linker]=syrabit-seo-internal-linker
  [discovery-engine-ingest]=syrabit-discovery-ingest
  [bing-keyword-refresh]=syrabit-bing-keyword
  [bing-submit]=syrabit-bing-submit
  [cf-bot-crosscheck]=syrabit-cf-bot-crosscheck
  [unified-logs-cf-pull]=syrabit-unified-logs-pull
  [email-fallback]=syrabit-email-fallback
)

failed=0
for key in "${QUEUES[@]}"; do
  qname=${AWS_NAME[$key]}
  # `email-fallback` reuses the existing email-worker Lambda; every
  # other key gets its own per-queue consumer Lambda. Mirrors the
  # special case in routes/admin_aws_infra._QUEUE_INVENTORY and
  # the lambda-workers.tf "Email-fallback wiring" block.
  if [ "$key" = "email-fallback" ]; then
    fname="${LZ_PROJECT}-email-worker"
  else
    fname="${LZ_PROJECT}-${key}-consumer"
  fi
  smoke_id=$(uuidgen)
  url=$(aws sqs get-queue-url --queue-name "$qname" --region "$AWS_REGION" \
         --query QueueUrl --output text)

  echo "[smoke] sending probe to $key ($qname) id=$smoke_id"
  aws sqs send-message --queue-url "$url" --region "$AWS_REGION" \
      --message-body "$(jq -nc --arg id "$smoke_id" '{_smoke:true,_smoke_id:$id}')" >/dev/null

  sleep "$WAIT_S"

  backlog=$(aws sqs get-queue-attributes --queue-url "$url" --region "$AWS_REGION" \
            --attribute-names ApproximateNumberOfMessages \
            --query 'Attributes.ApproximateNumberOfMessages' --output text)
  if [ "$backlog" != "0" ]; then
    echo "[smoke] FAIL $key — backlog=$backlog (expected 0); Lambda $fname did not drain in ${WAIT_S}s"
    failed=$((failed+1)); continue
  fi

  end=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  start=$(date -u -d "5 minutes ago" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null \
          || date -u -v-5M +"%Y-%m-%dT%H:%M:%SZ")
  errs=$(aws cloudwatch get-metric-statistics --region "$AWS_REGION" \
            --namespace AWS/Lambda --metric-name Errors \
            --dimensions "Name=FunctionName,Value=$fname" \
            --statistics Sum --period 60 \
            --start-time "$start" --end-time "$end" \
            --query 'Datapoints[].Sum' --output text)
  errs_total=$(echo "$errs" | awk '{s+=$1} END{print s+0}')
  if [ "$errs_total" -gt 0 ]; then
    echo "[smoke] FAIL $key — Lambda $fname Errors(5m)=$errs_total"
    failed=$((failed+1)); continue
  fi

  echo "[smoke] PASS $key (drained, no Lambda errors)"
done

if [ "$failed" -gt 0 ]; then
  echo "[smoke] $failed queue(s) failed end-to-end" >&2; exit 1
fi
echo "[smoke] all ${#QUEUES[@]} SQS→Lambda paths OK"
