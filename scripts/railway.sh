#!/usr/bin/env bash
#
# scripts/railway.sh — DEPRECATED.
#
# Task #336 migrated the syrabit-backend FastAPI service and the
# backend/rust-core gRPC+HTTP service off Railway and onto Digital
# Ocean App Platform (with an optional Droplet path for rust-core).
# This script is kept only as a deprecation shim so anyone (or any
# CI job) that still calls `pnpm run railway:*` or runs
# `bash scripts/railway.sh ...` directly gets a clear pointer to the
# replacement instead of a confusing missing-binary error.
#
# Replacement: scripts/digitalocean.sh (same subcommand surface).
# Runbook   : docs/DIGITALOCEAN-DEPLOYMENT.md
#               + docs/ops/digitalocean-cutover.md (cutover/rollback).

set -Eeuo pipefail

cat >&2 <<'EOF'
[railway.sh] ❌  Railway is no longer the syrabit hosting target.

  Task #336 replaced the Railway-hosted backend + rust-core with
  Digital Ocean App Platform. The legacy script that drove the
  Railway GraphQL API was removed.

  Equivalent commands (every old subcommand maps 1:1):

    OLD                                    NEW
    bash scripts/railway.sh deploy         bash scripts/digitalocean.sh deploy syrabit-backend
    bash scripts/railway.sh redeploy       bash scripts/digitalocean.sh redeploy syrabit-backend
    bash scripts/railway.sh logs           bash scripts/digitalocean.sh logs syrabit-backend
    bash scripts/railway.sh status         bash scripts/digitalocean.sh status syrabit-backend
    bash scripts/railway.sh vars           bash scripts/digitalocean.sh vars syrabit-backend
    bash scripts/railway.sh var-set K=V    bash scripts/digitalocean.sh var-set syrabit-backend K=V
    bash scripts/railway.sh var-unset K    bash scripts/digitalocean.sh var-unset syrabit-backend K

  pnpm aliases were renamed too:
    pnpm run railway:deploy       →  pnpm run do:deploy
    pnpm run railway:redeploy     →  pnpm run do:redeploy
    pnpm run railway:logs         →  pnpm run do:logs
    ... etc.

  Required env on the new path:
    DIGITALOCEAN_ACCESS_TOKEN     PAT with App Platform + DOCR write
    DO_APP_ID_SYRABIT_BACKEND     UUID of the syrabit-backend DO app
    DO_APP_ID_RUST_CORE           UUID of the rust-core DO app

  See docs/DIGITALOCEAN-DEPLOYMENT.md for first-time setup.
EOF

exit 1
