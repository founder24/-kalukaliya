#!/usr/bin/env bash
# scripts/populate-azure-secrets.sh
#
# Populate the Azure Key Vault that backs the cron + observability
# landing zone (Task #329) from 1Password. Mirrors the AWS Secrets
# Manager `put-secret-value` flow in `docs/infra/aws-landing-zone.md`.
#
# Why this exists
# ───────────────
# `infra/azure/key-vault.tf` only declares the secret containers with
# a `_placeholder` value — Terraform must never see plaintext. This
# script is the *enforceable* completion path for the landing zone's
# "Key Vault populated" requirement: it reads each secret from
# 1Password, pushes it to Key Vault, then verifies that no secret
# still holds the placeholder sentinel. Exit code is non-zero unless
# every secret in the manifest is populated.
#
# Pre-requisites
# ──────────────
# • `az login --tenant syrabit.onmicrosoft.com` (operator, not CI).
# • `az account set --subscription syrabit-prod`.
# • `op signin` (1Password CLI; uses the syrabit shared vault).
# • Operator must hold `Key Vault Secrets Officer` on the vault — this
#   is granted automatically by `terraform apply` to the principal
#   that ran terraform (see kv_secrets_officer_terraform_runner in
#   key-vault.tf).
#
# Usage
# ─────
#   ./scripts/populate-azure-secrets.sh                     # populate + verify
#   ./scripts/populate-azure-secrets.sh --verify-only       # verify only
#   ./scripts/populate-azure-secrets.sh --secret resend-api-key   # one secret

set -euo pipefail

VAULT_NAME="${SYRABIT_AZURE_KV_NAME:-syrabit-cron-obs-kv}"
PLACEHOLDER_SENTINEL="set-via-1password-rotation"

VERIFY_ONLY=0
ONE_SECRET=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --verify-only) VERIFY_ONLY=1; shift ;;
    --secret)
      if [[ $# -lt 2 || -z "${2:-}" || "${2:0:2}" == "--" ]]; then
        echo "ERROR: --secret requires a non-empty secret name argument." >&2
        exit 2
      fi
      ONE_SECRET="$2"; shift 2 ;;
    -h|--help)
      sed -n '1,40p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Manifest: <kv-secret-name>:<1password-reference>
# Each 1Password reference uses the `op://` URI scheme. The values
# stored in Key Vault are JSON objects so cron jobs can read structured
# fields (e.g. `.url`, `.token`) without a second parse step — keep the
# JSON shape stable across rotations.
# Third field is the JSON field name that holds the raw secret value
# inside the Key Vault payload, OR the literal "@raw" to store the
# 1Password value verbatim (used for vertex-service-account, which is
# already a JSON document). The script builds the payload via
# `jq -Rn --arg v "$raw"` so quotes/newlines/backslashes inside the
# secret can never produce invalid JSON — never use printf-style
# templating for secret material.
MANIFEST=(
  "supabase-service-role-key|op://syrabit/Supabase/service-role-key|key"
  "upstash-redis-rest-token|op://syrabit/Upstash/redis-rest-token|token"
  "resend-api-key|op://syrabit/Resend/api-key|key"
  "sentry-dsn-cron|op://syrabit/Sentry/dsn-cron|dsn"
  "axiom-ingest-token|op://syrabit/Axiom/ingest-token|token"
  "slack-ops-webhook|op://syrabit/Slack/ops-webhook-url|url"
  "pinecone-api-key|op://syrabit/Pinecone/api-key|key"
  "cohere-api-key|op://syrabit/Cohere/api-key|key"
  "mongodb-atlas-uri|op://syrabit/MongoDB-Atlas/connection-string|uri"
  "cf-logpush-shared-secret|op://syrabit/Azure-LAW/primary-shared-key|key"
  "vertex-service-account|op://syrabit/GCP-Vertex/service-account-json|@raw"
  "bing-webmaster-api-key|op://syrabit/Bing-Webmaster/api-key|key"
  "indexnow-key|op://syrabit/IndexNow/key|key"
)

# Validate `--secret <name>` against the manifest before any work.
# Without this guard the loops below silently no-op and the script
# exits 0 with the misleading "All 13 cron-tier secrets populated"
# success line.
if [[ -n "$ONE_SECRET" ]]; then
  found=0
  for entry in "${MANIFEST[@]}"; do
    IFS='|' read -r name _ _ <<<"$entry"
    if [[ "$name" == "$ONE_SECRET" ]]; then found=1; break; fi
  done
  if [[ "$found" -eq 0 ]]; then
    echo "ERROR: --secret '${ONE_SECRET}' is not in the manifest." >&2
    echo "Known secrets:" >&2
    for entry in "${MANIFEST[@]}"; do
      IFS='|' read -r name _ _ <<<"$entry"
      echo "  - ${name}" >&2
    done
    exit 2
  fi
fi

populate_one() {
  local name="$1" op_ref="$2" field="$3"
  echo "↻ ${name} ← ${op_ref}"
  local raw
  raw=$(op read "$op_ref")
  if [[ -z "$raw" ]]; then
    echo "  ✗ 1Password returned empty value for ${op_ref}" >&2
    return 1
  fi
  local payload
  if [[ "$field" == "@raw" ]]; then
    # Value is already valid JSON (e.g. a service-account doc); pass
    # it through after a parse-check so a malformed export is caught
    # here instead of at runtime.
    if ! printf '%s' "$raw" | jq -e . >/dev/null 2>&1; then
      echo "  ✗ ${op_ref} is marked @raw but is not valid JSON" >&2
      return 1
    fi
    payload="$raw"
  else
    # Build `{"<field>": "<raw>"}` via jq so quotes / newlines /
    # backslashes inside the secret are escaped correctly. printf
    # templating would silently produce broken JSON for any value
    # containing `"` or `\`.
    payload=$(jq -Rn --arg k "$field" --arg v "$raw" '{($k): $v}')
  fi
  az keyvault secret set \
    --vault-name "$VAULT_NAME" \
    --name "$name" \
    --value "$payload" \
    --content-type application/json \
    --output none
  echo "  ✓ pushed to Key Vault"
}

verify_one() {
  local name="$1"
  local current
  current=$(az keyvault secret show \
    --vault-name "$VAULT_NAME" \
    --name "$name" \
    --query value -o tsv)
  if [[ "$current" == *"$PLACEHOLDER_SENTINEL"* ]]; then
    echo "  ✗ ${name} still holds the placeholder sentinel" >&2
    return 1
  fi
  echo "  ✓ ${name} populated"
}

# ─── Run ─────────────────────────────────────────────────────────────────────

if [[ "$VERIFY_ONLY" -eq 0 ]]; then
  for entry in "${MANIFEST[@]}"; do
    IFS='|' read -r name op_ref tmpl <<<"$entry"
    if [[ -n "$ONE_SECRET" && "$name" != "$ONE_SECRET" ]]; then continue; fi
    populate_one "$name" "$op_ref" "$tmpl"
  done
fi

echo
echo "=== Verification ==="
fail=0
for entry in "${MANIFEST[@]}"; do
  IFS='|' read -r name _ _ <<<"$entry"
  if [[ -n "$ONE_SECRET" && "$name" != "$ONE_SECRET" ]]; then continue; fi
  if ! verify_one "$name"; then fail=$((fail+1)); fi
done

if [[ "$fail" -gt 0 ]]; then
  echo
  echo "FAIL: ${fail} secret(s) still hold the placeholder. Re-run without --verify-only." >&2
  exit 1
fi

echo
echo "All ${#MANIFEST[@]} cron-tier secrets populated in ${VAULT_NAME}."
