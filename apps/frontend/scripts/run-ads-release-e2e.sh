#!/usr/bin/env bash
set -euo pipefail

export E2E_ADS_RELEASE=1
export BASE_URL=http://localhost:4173
export PLAYWRIGHT_WEB_SERVER_URL=http://localhost:4173
export PLAYWRIGHT_WEB_SERVER_COMMAND='pnpm exec vite preview --host 0.0.0.0 --port 4173'

# Build a production-shaped test bundle with deterministic, unique numeric
# slot IDs. Do not depend on a stale dist/ directory or copy live publisher
# configuration into this local test build.
while IFS='=' read -r key value; do
  export "$key=$value"
done < <(
  node --input-type=module -e '
    import { ADSENSE_SLOT_ENV_KEYS } from "./src/utils/adsenseSlotConfig.js";
    ADSENSE_SLOT_ENV_KEYS.forEach((key, index) => {
      console.log(`${key}=910000000000000${String(index + 1).padStart(3, "0")}`);
    });
  '
)

pnpm exec vite build
exec bash scripts/run-e2e.sh ads-release.spec.ts "$@"