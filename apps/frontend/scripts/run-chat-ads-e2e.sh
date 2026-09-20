#!/usr/bin/env bash
set -euo pipefail

export E2E_CHAT_ADS=1
# Must satisfy ADSENSE_SLOT_ID_PATTERN (/^\d{5,20}$/) — real AdSense slot
# IDs are numeric, and getAdConfig() treats a non-matching value as an
# unconfigured slot (enabled: false), which silently hid the sponsored
# card from this suite after the pattern was introduced.
export VITE_ADS_ADSENSE_CHAT_AFTER_ASSISTANT_SLOT=1234567890
export BASE_URL=http://localhost:4173
export PLAYWRIGHT_WEB_SERVER_URL=http://localhost:4173
export PLAYWRIGHT_WEB_SERVER_COMMAND='pnpm build:client && pnpm exec vite preview --host 0.0.0.0 --port 4173'

exec bash scripts/run-e2e.sh chat-ads.spec.ts "$@"