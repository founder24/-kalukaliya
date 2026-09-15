#!/usr/bin/env bash
set -euo pipefail

export E2E_ADS_RELEASE=1
export BASE_URL=http://localhost:4173
export PLAYWRIGHT_WEB_SERVER_URL=http://localhost:4173
export PLAYWRIGHT_WEB_SERVER_COMMAND='pnpm exec vite preview --host 0.0.0.0 --port 4173'

exec bash scripts/run-e2e.sh ads-release.spec.ts "$@"