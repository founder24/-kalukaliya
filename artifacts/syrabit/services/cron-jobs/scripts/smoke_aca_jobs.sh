#!/usr/bin/env bash
# Task #332 — Phase 4 end-to-end smoke for the ACA Jobs cron tier.
#
# Triggers a one-shot start of every aca-job-* Container Apps Job
# defined in `infra/azure/container-apps-jobs.tf`, polls each
# execution to terminal state, and prints a pass/fail summary.
# Exits non-zero if ANY job's execution status is not "Succeeded".
#
# Usage:
#   AZ_RG=syrabit-cron-obs-rg ./smoke_aca_jobs.sh
#   AZ_RG=... JOB_FILTER=seo- ./smoke_aca_jobs.sh   # subset
#
# Requires `az` CLI logged in to the cron-obs subscription.

set -euo pipefail

: "${AZ_RG:?AZ_RG (resource group) is required}"
JOB_FILTER="${JOB_FILTER:-}"
POLL_INTERVAL_S="${POLL_INTERVAL_S:-15}"
POLL_TIMEOUT_S="${POLL_TIMEOUT_S:-1800}"

echo "[smoke] listing aca-job-* in $AZ_RG ..."
mapfile -t JOBS < <(
  az containerapp job list -g "$AZ_RG" \
     --query "[?starts_with(name, 'aca-job-')].name" -o tsv \
   | { [ -n "$JOB_FILTER" ] && grep -- "$JOB_FILTER" || cat; }
)

if [ "${#JOBS[@]}" -eq 0 ]; then
  echo "[smoke] no jobs matched filter=$JOB_FILTER — nothing to do"; exit 1
fi

declare -A EXEC
for job in "${JOBS[@]}"; do
  echo "[smoke] starting $job ..."
  exec_name=$(az containerapp job start -g "$AZ_RG" -n "$job" \
              --query name -o tsv)
  EXEC[$job]="$exec_name"
done

failed=0
deadline=$(( $(date +%s) + POLL_TIMEOUT_S ))
for job in "${JOBS[@]}"; do
  exec_name=${EXEC[$job]}
  while :; do
    status=$(az containerapp job execution show \
               -g "$AZ_RG" -n "$job" --job-execution-name "$exec_name" \
               --query "properties.status" -o tsv 2>/dev/null || echo Unknown)
    case "$status" in
      Succeeded) echo "[smoke] PASS  $job ($exec_name)"; break ;;
      Failed|Stopped|Degraded)
        echo "[smoke] FAIL  $job ($exec_name) status=$status"
        failed=$((failed+1)); break ;;
      Running|Processing|Pending|Unknown)
        if [ "$(date +%s)" -ge "$deadline" ]; then
          echo "[smoke] TIMEOUT $job (last status=$status)"
          failed=$((failed+1)); break
        fi
        sleep "$POLL_INTERVAL_S" ;;
      *) echo "[smoke] FAIL  $job ($exec_name) unexpected status=$status"
         failed=$((failed+1)); break ;;
    esac
  done
done

echo
if [ "$failed" -gt 0 ]; then
  echo "[smoke] $failed job(s) did not succeed — see Log Analytics for the failing executions" >&2
  exit 1
fi
echo "[smoke] all ${#JOBS[@]} jobs Succeeded"
