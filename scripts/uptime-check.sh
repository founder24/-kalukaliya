#!/bin/bash
# Syrabit Uptime Check Script
# Run via cron or external monitoring
#
# Usage: ./scripts/uptime-check.sh
# Exit code: 0 if all endpoints are healthy, 1 if any endpoint is down

set -euo pipefail

ENDPOINTS=(
  "https://api.syrabit.ai/health"
  "https://syrabit.ai"
  "https://api.syrabit.ai/api/content/boards"
)

TIMEOUT=10
FAILED=0

for endpoint in "${ENDPOINTS[@]}"; do
  status_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT" "$endpoint" 2>/dev/null || echo "000")
  
  if [[ "$status_code" -ge 200 && "$status_code" -lt 400 ]]; then
    echo "[UP]   $endpoint (HTTP $status_code)"
  else
    echo "[DOWN] $endpoint (HTTP $status_code)"
    FAILED=1
  fi
done

if [[ "$FAILED" -eq 1 ]]; then
  echo ""
  echo "ALERT: One or more endpoints are down!"
  exit 1
else
  echo ""
  echo "All endpoints healthy."
  exit 0
fi
