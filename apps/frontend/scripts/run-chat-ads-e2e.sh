#!/usr/bin/env bash
set -euo pipefail

export E2E_CHAT_ADS=1
export VITE_ADS_ADSENSE_CHAT_AFTER_ASSISTANT_SLOT=e2e-chat-slot
export BASE_URL=http://localhost:4173
export PLAYWRIGHT_WEB_SERVER_URL=http://localhost:4173
export PLAYWRIGHT_WEB_SERVER_COMMAND='pnpm build:client && pnpm exec vite preview --host 0.0.0.0 --port 4173'

exec bash scripts/run-e2e.sh chat-ads.spec.ts "$@"