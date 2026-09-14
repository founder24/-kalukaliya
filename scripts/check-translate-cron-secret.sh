#!/usr/bin/env bash
# Authenticate against the Worker-native translation probe without enqueueing
# a seed run or exposing the shared secret in logs.
set -euo pipefail

: "${API_ORIGIN:?API_ORIGIN is required}"
: "${TRANSLATE_SECRET:?TRANSLATE_SECRET is required}"

probe_url="${API_ORIGIN%/}/api/v1/admin/cron/translate/probe"
response_file="$(mktemp)"
trap 'rm -f "$response_file"' EXIT

status="$(curl --silent --show-error --max-time 30 \
  --output "$response_file" \
  --write-out '%{http_code}' \
  --request POST "$probe_url" \
  --header "Authorization: Bearer ${TRANSLATE_SECRET}" \
  --header 'Content-Type: application/json' \
  --data '{}')"

case "$status" in
  200)
    jq -e '
      .authenticated == true
      and .mutation_free == true
      and .work_enqueued == false
      and .probe == "translation-cron-authentication"
    ' "$response_file" >/dev/null || {
      echo "::error::Translation cron probe returned an invalid success envelope."
      exit 1
    }
    echo "Translation cron secret alignment: aligned (read-only probe; no work enqueued)."
    ;;
  401|403)
    echo "::error::Translation cron secret alignment failed (HTTP ${status}); GitHub and Worker secrets differ or the binding is unavailable."
    exit 1
    ;;
  *)
    echo "::error::Translation cron alignment probe failed with HTTP ${status}."
    exit 1
    ;;
esac