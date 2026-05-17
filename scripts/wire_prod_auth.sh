#!/usr/bin/env bash
# =============================================================================
# wire_prod_auth.sh — Wire Supabase Google + Email auth into ACA production
# =============================================================================
#
# Fixes the following production auth gaps:
#   1. Populate KV secrets: SUPABASE-SERVICE-ROLE-KEY, SUPABASE-ANON-KEY,
#      TURNSTILE-SECRET-KEY, TURNSTILE-SITE-KEY
#   2. Add missing ACA secret refs for Turnstile
#   3. Add missing ACA env vars: COOKIE_DOMAIN, FRONTEND_URL, TURNSTILE_ON,
#      TURNSTILE_SITE_KEY, TURNSTILE_SECRET_KEY, SECURE_COOKIES
#   4. Force a new ACA revision so the changes take effect
#   5. Verify the backend /api/health and /api/auth/supabase-session respond correctly
#
# Prerequisites:
#   az login (or az login --use-device-code)
#   az account set --subscription <your-subscription-id>
#
# Secrets you must fill in before running:
#   SUPABASE_SERVICE_ROLE_KEY_VAL  — from Supabase dashboard → Settings → API →
#                                    "service_role" key (secret key, NOT anon key)
#   SUPABASE_ANON_KEY_VAL          — from Supabase dashboard → Settings → API →
#                                    "anon" key (safe to expose to browser)
#   TURNSTILE_SECRET_KEY_VAL       — from Cloudflare dashboard → Turnstile →
#                                    your site → Secret Key
#   TURNSTILE_SITE_KEY_VAL         — from Cloudflare dashboard → Turnstile →
#                                    your site → Site Key (public)
#
# Supabase Google OAuth — do this in the Supabase dashboard BEFORE running:
#   1. Go to https://supabase.com/dashboard/project/czeznmqogtwecidhpysa
#   2. Authentication → Providers → Google → Enable
#   3. Set Google Client ID + Secret (from Google Cloud Console OAuth 2.0 creds)
#   4. Authentication → URL Configuration → Add these Redirect URLs:
#        https://syrabit.ai/signup
#        https://syrabit.ai/login
#        https://syrabit.ai/
#        https://www.syrabit.ai/
#        https://admin.syrabit.ai/
#   5. Set Site URL = https://syrabit.ai
# =============================================================================

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
RESOURCE_GROUP="syrabit-prod"
CONTAINER_APP="syrabit-backend"
KEY_VAULT="syrabit-prod-kv"
SUPABASE_URL="https://czeznmqogtwecidhpysa.supabase.co"

# ── Secrets — FILL THESE IN ───────────────────────────────────────────────────
SUPABASE_SERVICE_ROLE_KEY_VAL="${SUPABASE_SERVICE_ROLE_KEY_VAL:-REPLACE_ME_service_role_key}"
SUPABASE_ANON_KEY_VAL="${SUPABASE_ANON_KEY_VAL:-REPLACE_ME_anon_key}"
TURNSTILE_SECRET_KEY_VAL="${TURNSTILE_SECRET_KEY_VAL:-REPLACE_ME_turnstile_secret}"
TURNSTILE_SITE_KEY_VAL="${TURNSTILE_SITE_KEY_VAL:-REPLACE_ME_turnstile_site_key}"

# ── Guard — abort if placeholders are still set ───────────────────────────────
for VAR in SUPABASE_SERVICE_ROLE_KEY_VAL SUPABASE_ANON_KEY_VAL TURNSTILE_SECRET_KEY_VAL TURNSTILE_SITE_KEY_VAL; do
  if [[ "${!VAR}" == REPLACE_ME* ]]; then
    echo "ERROR: ${VAR} is not set. Export it before running:"
    echo "  export ${VAR}='<value>'"
    exit 1
  fi
done

echo ""
echo "======================================================="
echo " Phase 1 — Populate Key Vault secrets"
echo "======================================================="

# Supabase service-role key (admin JWT exchange for Google OAuth + user lookups)
az keyvault secret set \
  --vault-name "$KEY_VAULT" \
  --name "SUPABASE-SERVICE-ROLE-KEY" \
  --value "$SUPABASE_SERVICE_ROLE_KEY_VAL" \
  --output none
echo "✓ KV: SUPABASE-SERVICE-ROLE-KEY"

# Supabase anon key (browser-side SDK + unauthenticated REST calls)
az keyvault secret set \
  --vault-name "$KEY_VAULT" \
  --name "SUPABASE-ANON-KEY" \
  --value "$SUPABASE_ANON_KEY_VAL" \
  --output none
echo "✓ KV: SUPABASE-ANON-KEY"

# Cloudflare Turnstile — secret key (server-side validation)
az keyvault secret set \
  --vault-name "$KEY_VAULT" \
  --name "TURNSTILE-SECRET-KEY" \
  --value "$TURNSTILE_SECRET_KEY_VAL" \
  --output none
echo "✓ KV: TURNSTILE-SECRET-KEY"

# Cloudflare Turnstile — site key (sent to browser, used in widget)
az keyvault secret set \
  --vault-name "$KEY_VAULT" \
  --name "TURNSTILE-SITE-KEY" \
  --value "$TURNSTILE_SITE_KEY_VAL" \
  --output none
echo "✓ KV: TURNSTILE-SITE-KEY"

echo ""
echo "======================================================="
echo " Phase 2 — Add new ACA secret refs (KV references)"
echo "======================================================="

# Mount Turnstile secrets into the Container App from Key Vault.
# Supabase refs (supabase-service-role-key, supabase-anon-key) are already
# declared in the Bicep template — no need to re-add them here.
az containerapp secret set \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --secrets \
    "turnstile-secret-key=keyvaultref:https://${KEY_VAULT}.vault.azure.net/secrets/TURNSTILE-SECRET-KEY,identityref:system" \
    "turnstile-site-key=keyvaultref:https://${KEY_VAULT}.vault.azure.net/secrets/TURNSTILE-SITE-KEY,identityref:system"
echo "✓ ACA secret refs: turnstile-secret-key, turnstile-site-key"

echo ""
echo "======================================================="
echo " Phase 3 — Update ACA env vars"
echo "======================================================="

# COOKIE_DOMAIN — scopes session + refresh cookies to .syrabit.ai so they work
# across syrabit.ai and admin.syrabit.ai. Without this, httpOnly cookies are
# only valid for the ACA FQDN and auth breaks on the CF-proxied domain.
#
# FRONTEND_URL — used in password-reset and verification email links.
#
# SECURE_COOKIES=true — explicit; ensures Set-Cookie always has Secure flag
# in production (the default in config.py, but made explicit to prevent any
# future config drift from silently downgrading it).
#
# TURNSTILE_ON=true — enables Cloudflare Turnstile bot-protection on the
# /auth/signup and /auth/login endpoints. Was false (disabled) in prod.
# TURNSTILE_SITE_KEY is sent to the frontend via /api/turnstile/config.
# TURNSTILE_SECRET_KEY is used server-side to verify widget tokens.
az containerapp update \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --set-env-vars \
    "COOKIE_DOMAIN=.syrabit.ai" \
    "FRONTEND_URL=https://syrabit.ai" \
    "SECURE_COOKIES=true" \
    "TURNSTILE_ON=true" \
    "TURNSTILE_SITE_KEY=secretref:turnstile-site-key" \
    "TURNSTILE_SECRET_KEY=secretref:turnstile-secret-key" \
  --output none
echo "✓ ACA env vars updated"

echo ""
echo "======================================================="
echo " Phase 4 — Force a new revision (rolling restart)"
echo "======================================================="

# Restart creates a new revision so the secret KV refs re-resolve and the new
# env vars are picked up. ACA's activeRevisionsMode=Single means traffic
# shifts automatically to the new revision once it passes readiness probes.
az containerapp revision restart \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --revision "$(az containerapp revision list \
    --name "$CONTAINER_APP" \
    --resource-group "$RESOURCE_GROUP" \
    --query "[?properties.active].name | [0]" \
    --output tsv)" \
  --output none
echo "✓ Revision restart triggered"

echo ""
echo "======================================================="
echo " Phase 5 — Wait for readiness + verify"
echo "======================================================="

BACKEND_URL="https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io"

echo "Waiting 45s for the new revision to pass readiness probes..."
sleep 45

echo ""
echo "→ GET /api/health"
curl -sf "${BACKEND_URL}/api/health" | python3 -c "import sys,json; d=json.load(sys.stdin); print('  status:', d.get('status','?')); print('  mongodb:', d.get('checks',{}).get('mongodb','?'))"

echo ""
echo "→ POST /api/auth/supabase-session (bad token — expect 401 not 503)"
DETAIL=$(curl -sf -X POST "${BACKEND_URL}/api/auth/supabase-session" \
  -H "Content-Type: application/json" \
  -d '{"supabase_token":"invalid.jwt.token"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('detail','?'))")
if [[ "$DETAIL" == "Invalid or expired Supabase token" ]]; then
  echo "  ✓ Supabase client live — returned expected 401"
else
  echo "  ✗ Unexpected response: $DETAIL"
fi

echo ""
echo "→ GET /api/turnstile/config (expect turnstile enabled + site_key present)"
curl -sf "${BACKEND_URL}/api/turnstile/config" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('  enabled:', d.get('enabled')); print('  site_key_set:', bool(d.get('site_key')))"

echo ""
echo "======================================================="
echo " Done — production auth is fully wired."
echo ""
echo " Remaining manual steps:"
echo "   1. Supabase dashboard → Authentication → Providers → Google"
echo "      Enable and paste your Google Client ID + Secret"
echo "   2. Authentication → URL Configuration → Redirect URLs:"
echo "        https://syrabit.ai/signup"
echo "        https://syrabit.ai/login"
echo "        https://syrabit.ai/"
echo "        https://www.syrabit.ai/"
echo "        https://admin.syrabit.ai/"
echo "      Site URL: https://syrabit.ai"
echo "   3. Cloudflare Pages → syrabit → Settings → Environment variables:"
echo "        VITE_SUPABASE_URL  = ${SUPABASE_URL}"
echo "        VITE_SUPABASE_ANON_KEY = <your anon key>"
echo "        VITE_TURNSTILE_SITE_KEY = <your turnstile site key>"
echo "      Redeploy Pages after adding these."
echo "======================================================="
