#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# grant-audit-sa-secrets.sh — Run in Cloud Shell to grant the audit SA
# (cloudflare-edge-invoker) direct read access on the 6 SM secrets it cannot
# currently access.  After this runs, fullstack-audit.sh Layer 1 will verify
# every secret via gcloud secrets versions access (no Cloud Run mount fallback).
#
# Idempotent: add-iam-policy-binding is a no-op if the binding already exists.
#
# Usage:
#   bash infra/scripts/grant-audit-sa-secrets.sh
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT="blissful-acumen-495019-t6"
AUDIT_SA="cloudflare-edge-invoker@${PROJECT}.iam.gserviceaccount.com"
ROLE="roles/secretmanager.secretAccessor"

G="\033[92m"; R="\033[91m"; B="\033[94m"; X="\033[0m"

echo -e "${B}=== Grant audit SA secret read access ===${X}"
echo "Project  : $PROJECT"
echo "SA       : $AUDIT_SA"
echo "Role     : $ROLE"
echo ""

SECRETS=(
  indexnow-api-key
  indexnow-internal-secret
  posthog-api-key
  upstash-redis-rest-url
  upstash-redis-rest-token
  VERTEX_SEARCH_DATASTORE_ID
)

PASS=0
FAIL=0

for secret in "${SECRETS[@]}"; do
  echo -n "  Binding $secret ... "
  if gcloud secrets add-iam-policy-binding "$secret" \
       --project="$PROJECT" \
       --member="serviceAccount:$AUDIT_SA" \
       --role="$ROLE" \
       --quiet >/dev/null 2>&1; then
    echo -e "${G}✓${X}"
    ((PASS++)) || true
  else
    echo -e "${R}FAILED${X}"
    ((FAIL++)) || true
  fi
done

echo ""
echo "──────────────────────────────────────"
echo "Bindings applied : $PASS / ${#SECRETS[@]}"

if [[ $FAIL -gt 0 ]]; then
  echo -e "${R}$FAIL binding(s) failed — check gcloud auth and secret names.${X}"
  exit 1
fi

echo ""
echo -e "${B}Verifying direct access for each secret...${X}"
VERIFY_PASS=0
VERIFY_FAIL=0
for secret in "${SECRETS[@]}"; do
  echo -n "  gcloud secrets versions access $secret ... "
  if timeout 15 gcloud secrets versions access latest \
       --secret="$secret" --project="$PROJECT" >/dev/null 2>&1; then
    echo -e "${G}✓ readable${X}"
    ((VERIFY_PASS++)) || true
  else
    echo -e "${R}FAILED (permission or secret missing)${X}"
    ((VERIFY_FAIL++)) || true
  fi
done

echo ""
echo "Verification: $VERIFY_PASS / ${#SECRETS[@]} readable"

if [[ $VERIFY_FAIL -gt 0 ]]; then
  echo -e "${R}Some secrets still unreadable — IAM propagation may take ~60s; retry fullstack-audit.sh after a moment.${X}"
  exit 1
fi

echo -e "${G}All 6 secrets now directly readable by $AUDIT_SA.${X}"
echo "Re-run infra/scripts/fullstack-audit.sh — Layer 1 should pass without ℹ️ fallback messages."
