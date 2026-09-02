#!/usr/bin/env bash
# Cloudflare-native production and retired-backend verification.
set -euo pipefail

API="${API:-https://api.syrabit.ai}"
API_WORKER="${API_WORKER:-https://syrabit-api-prod.axomxplain.workers.dev}"
FE="${FE:-https://syrabit.ai}"
GCP_PROJECT="${GCP_PROJECT:-blissful-acumen-495019-t6}"
GCP_REGION="${GCP_REGION:-asia-south1}"
TIMEOUT="${TIMEOUT:-20}"

PASS=0
FAIL=0

pass() {
  printf 'PASS %s\n' "$1"
  PASS=$((PASS + 1))
}

fail() {
  printf 'FAIL %s\n' "$1" >&2
  FAIL=$((FAIL + 1))
}

request() {
  local url="$1"
  local expected_statuses="$2"
  local expected_header="${3:-}"
  local headers body status
  headers=$(mktemp)
  body=$(mktemp)
  status=$(curl --silent --show-error --max-time "$TIMEOUT" \
    --dump-header "$headers" --output "$body" --write-out '%{http_code}' \
    "$url" || true)

  if [[ " $expected_statuses " != *" $status "* ]]; then
    fail "${url} returned ${status}; expected ${expected_statuses}"
  elif [[ -n "$expected_header" ]] && ! tr -d '\r' < "$headers" | grep -qiFx "$expected_header"; then
    fail "${url} is missing header ${expected_header}"
  else
    pass "${url} returned ${status}${expected_header:+ with ${expected_header}}"
  fi

  rm -f "$headers" "$body"
}

printf 'Syrabit Cloudflare-native production check\n'

if command -v gcloud >/dev/null 2>&1; then
  service=$(gcloud run services describe syrabit-backend \
    --project="$GCP_PROJECT" \
    --region="$GCP_REGION" \
    --format='value(metadata.name)' 2>/dev/null || true)
  if [[ -z "$service" ]]; then
    pass "retired Cloud Run service is absent"
  else
    fail "retired Cloud Run service unexpectedly exists"
  fi
else
  printf 'SKIP independent Cloud Run absence check (gcloud unavailable)\n'
fi

request "$API_WORKER/health" "200" "x-syrabit-route: worker-native"
request "$API/health" "200" "x-syrabit-health-backend: api-worker"
request "$API/api/v1/auth/me" "401" "x-syrabit-route: worker-native"
request "$API/api/v1/users/me" "401" "x-syrabit-route: worker-native"
request "$API/api/v1/payments/history" "401" "x-syrabit-route: worker-native"
request "$API/api/v1/content/library-bundle?slim=1" "200" "x-syrabit-route: worker-native"
request "$FE/" "200 301"

printf '\nSummary: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]