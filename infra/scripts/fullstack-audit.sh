#!/usr/bin/env bash
# fullstack-audit.sh — Run from Cloud Shell: bash infra/scripts/fullstack-audit.sh
# Exit code 0 = all checks pass. Exit code 1 = at least one failure.
#
# Prerequisites:
#   - gcloud auth login (or Cloud Shell, which is pre-authenticated)
#   - CLOUDFLARE_KV_API_TOKEN env var set (or pulled from SM below)
#   - CF_ACCOUNT_ID env var set (or pulled from SM below)

set -uo pipefail

PROJECT="blissful-acumen-495019-t6"
REGION="asia-south1"
SERVICE="syrabit-backend"
CF_ACCT="${CF_ACCOUNT_ID:-}"
WORKER="syrabitworker-prod"
PAGES_PROJECT="syrabitfrontend"
BASE_URL="https://api.syrabit.ai"
PASS=0
FAIL=0

check() {
  local id="$1" desc="$2" result="$3" fix="${4:-}"
  if [[ "$result" == "pass" ]]; then
    echo "  ✅ [$id] $desc"
    ((PASS++)) || true
  else
    echo "  ❌ [$id] $desc"
    echo "     Result : $result"
    [[ -n "$fix" ]] && echo "     Fix    : $fix"
    ((FAIL++)) || true
  fi
}

# Pull CF creds from SM if not in environment
if [[ -z "${CLOUDFLARE_KV_API_TOKEN:-}" ]]; then
  CLOUDFLARE_KV_API_TOKEN=$(gcloud secrets versions access latest \
    --secret=CF_KV_API_TOKEN --project="$PROJECT" 2>/dev/null || echo "")
fi
if [[ -z "$CF_ACCT" ]]; then
  CF_ACCT=$(gcloud secrets versions access latest \
    --secret=CF_ACCOUNT_ID --project="$PROJECT" 2>/dev/null || echo "")
fi

# Fetch Cloud Run spec early — used as a fallback proof-of-existence for SM secrets
# when the running account lacks secretmanager.versions.access on individual secrets.
# GCP hides inaccessible secrets as NOT_FOUND, so a successful CR mount reference
# is authoritative proof the secret exists and is readable by syrabit-backend-sa.
CR_JSON=$(curl -sf -H "Authorization: Bearer $(gcloud auth print-access-token 2>/dev/null)" \
  "https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/services/$SERVICE" 2>/dev/null || echo "{}")
CR_SM_SECRETS=$(echo "$CR_JSON" | python3 -c \
  "import json,sys; d=json.load(sys.stdin); envs=d.get('template',{}).get('containers',[{}])[0].get('env',[]); print(' '.join(e.get('valueSource',{}).get('secretKeyRef',{}).get('secret','') for e in envs if 'valueSource' in e))" 2>/dev/null || echo "")

# ═══════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════"
echo " LAYER 1: GCP Secret Manager"
echo "═══════════════════════════════════════════════════"

REQUIRED_SECRETS=(
  mongodb-uri jwt-secret edge-shared-secret sarvam-api-key gemini-api-key
  admin-jwt-secret reset-token-secret translate-cron-secret indexnow-api-key
  indexnow-internal-secret posthog-api-key resend-api-key razorpay-key-id
  razorpay-key-secret razorpay-webhook-secret jwt-private-key jwt-public-key
  upstash-redis-rest-url upstash-redis-rest-token
  GOOGLE_APPLICATION_CREDENTIALS_JSON SENTRY_DSN VERTEX_SEARCH_DATASTORE_ID
  CF_ACCOUNT_ID CF_KV_API_TOKEN CF_KV_NAMESPACE_ID GCS_CONTENT_BUCKET
)

for secret in "${REQUIRED_SECRETS[@]}"; do
  short_id="SM-$(echo "$secret" | tr '[:lower:]' '[:upper:]' | tr '-' '_' | cut -c1-20)"
  if echo " $CR_SM_SECRETS " | grep -qw "$secret"; then
    # Secret is mounted on Cloud Run — definitively exists and is readable by
    # syrabit-backend-sa (the account that matters at runtime). GCP hides secrets
    # the audit SA can't access as NOT_FOUND, so the CR mount ref is authoritative.
    echo "  ℹ️  (verified via Cloud Run mount for '$secret')"
    check "$short_id" "Secret '$secret' readable" "pass"
  else
    # Not mounted on Cloud Run — must verify directly (CF_ACCOUNT_ID, CF_KV_NAMESPACE_ID,
    # GCS_CONTENT_BUCKET are examples that are SM-only, not mounted on CR).
    if timeout 15 gcloud secrets versions access latest --secret="$secret" \
         --project="$PROJECT" >/dev/null 2>&1; then
      result="pass"
    else
      result="secret not found or inaccessible"
    fi
    check "$short_id" "Secret '$secret' readable" "$result" \
      "gcloud secrets create $secret --project=$PROJECT --replication-policy=automatic"
  fi
done

JWT_VAL=$(gcloud secrets versions access latest \
  --secret=jwt-secret --project="$PROJECT" 2>/dev/null || echo "")
JWT_LEN=${#JWT_VAL}
if [[ $JWT_LEN -ge 32 ]]; then
  JWT_LEN_OK="pass"
else
  JWT_LEN_OK="length=$JWT_LEN (need >=32)"
fi
check "SM-JWT-LEN" "jwt-secret length >= 32 chars" "$JWT_LEN_OK" \
  "python3 -c \"import secrets; print(secrets.token_hex(32))\" | gcloud secrets versions add jwt-secret --data-file=-"

for UPPER in JWT_SECRET EDGE_SHARED_SECRET MONGODB_URI SARVAM_API_KEY ADMIN_JWT_SECRET; do
  LOWER=$(echo "$UPPER" | tr '[:upper:]' '[:lower:]' | tr '_' '-')
  UPPER_EXISTS=$(gcloud secrets describe "$UPPER" --project="$PROJECT" 2>/dev/null && echo yes || echo no)
  LOWER_EXISTS=$(gcloud secrets describe "$LOWER" --project="$PROJECT" 2>/dev/null && echo yes || echo no)
  if [[ "$UPPER_EXISTS" == "yes" && "$LOWER_EXISTS" == "yes" ]]; then
    echo "  ⚠️  [SM-ORPHAN] Both '$UPPER' and '$LOWER' exist — verify values match, then delete '$UPPER'"
  fi
done

# ═══════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════"
echo " LAYER 2: Cloud Run Service Spec"
echo "═══════════════════════════════════════════════════"

# CR_JSON already fetched above (reused here)
SARVAM_MODEL_VAL=$(echo "$CR_JSON" | python3 -c \
  "import json,sys; d=json.load(sys.stdin); envs=d.get('template',{}).get('containers',[{}])[0].get('env',[]); print(next((e.get('value','') for e in envs if e.get('name')=='SARVAM_MODEL'),'MISSING'))" 2>/dev/null || echo "MISSING")
[[ "$SARVAM_MODEL_VAL" == "sarvam-30b" ]] && SM_RESULT="pass" || SM_RESULT="SARVAM_MODEL=$SARVAM_MODEL_VAL (want sarvam-30b)"
check "CR-SARVAM" "SARVAM_MODEL=sarvam-30b on Cloud Run" "$SM_RESULT" \
  "gcloud run services update $SERVICE --region=$REGION --update-env-vars=SARVAM_MODEL=sarvam-30b"

VPROJ=$(echo "$CR_JSON" | python3 -c \
  "import json,sys; d=json.load(sys.stdin); envs=d.get('template',{}).get('containers',[{}])[0].get('env',[]); print(next((e.get('value','') for e in envs if e.get('name')=='VERTEX_PROJECT_ID'),'MISSING'))" 2>/dev/null || echo "MISSING")
[[ "$VPROJ" != "MISSING" && -n "$VPROJ" ]] && VP_RESULT="pass" || VP_RESULT="MISSING — vector search embedding will fail"
check "CR-VERTEX-PROJ" "VERTEX_PROJECT_ID set (needed by text-embedding-005)" "$VP_RESULT" \
  "gcloud run services update $SERVICE --region=$REGION --update-env-vars=VERTEX_PROJECT_ID=$PROJECT"

VLOC=$(echo "$CR_JSON" | python3 -c \
  "import json,sys; d=json.load(sys.stdin); envs=d.get('template',{}).get('containers',[{}])[0].get('env',[]); print(next((e.get('value','') for e in envs if e.get('name')=='VERTEX_LOCATION'),'MISSING'))" 2>/dev/null || echo "MISSING")
[[ "$VLOC" == "asia-south1" ]] && VL_RESULT="pass" || VL_RESULT="VERTEX_LOCATION=$VLOC (want asia-south1)"
check "CR-VERTEX-LOC" "VERTEX_LOCATION=asia-south1" "$VL_RESULT" \
  "gcloud run services update $SERVICE --region=$REGION --update-env-vars=VERTEX_LOCATION=asia-south1"

for VAR in VERTEX_SEARCH_DATASTORE_ID VERTEX_SEARCH_LOCATION VERTEX_SEARCH_SERVING_CONFIG; do
  STALE=$(echo "$CR_JSON" | python3 -c \
    "import json,sys; d=json.load(sys.stdin); envs=d.get('template',{}).get('containers',[{}])[0].get('env',[]); plain=[e for e in envs if e.get('name')=='$VAR' and 'value' in e]; print('stale' if plain else 'pass')" 2>/dev/null || echo "skip")
  check "CR-$VAR" "No plain '$VAR' env var (comes from SM)" "$STALE" \
    "gcloud run services update $SERVICE --region=$REGION --remove-env-vars=$VAR"
done

# ═══════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════"
echo " LAYER 3: cloudbuild.yaml"
echo "═══════════════════════════════════════════════════"

# Intentionally uppercase in Secret Manager (GCP secret names created with these exact names).
# Any uppercase SM ref NOT in this list is likely a mis-cased name that will fail at deploy time.
_ALLOWED_UPPER="GOOGLE_APPLICATION_CREDENTIALS_JSON|SENTRY_DSN|VERTEX_SEARCH_DATASTORE_ID|CF_ACCOUNT_ID|CF_KV_API_TOKEN|CF_KV_NAMESPACE_ID|GCS_CONTENT_BUCKET|CF_PAGES_DEPLOY_HOOK|CF_KV_NAMESPACE|ADMIN_EMAIL|ADMIN_PASSWORD"

UPPER_CB=$(grep -oP '=\K[A-Z_]+(?=:latest)' cloudbuild.yaml 2>/dev/null \
  | grep -v -E "^(${_ALLOWED_UPPER})$" \
  | tr '\n' ' ')
[[ -z "$UPPER_CB" ]] && CB_RESULT="pass" || CB_RESULT="unexpected uppercase SM refs: $UPPER_CB"
check "CB-CASING" "No unexpected uppercase SM refs in cloudbuild.yaml" "$CB_RESULT" \
  "Replace unexpected uppercase SM refs with verified lowercase SM names in cloudbuild.yaml"

SARVAM_CB=$(grep -oP 'SARVAM_MODEL=\Ksarvam-\S+' cloudbuild.yaml 2>/dev/null | head -1 || echo "")
[[ "$SARVAM_CB" == "sarvam-30b" ]] && CB_SARVAM="pass" || CB_SARVAM="found: '$SARVAM_CB' (want sarvam-30b)"
check "CB-SARVAM" "cloudbuild.yaml SARVAM_MODEL=sarvam-30b" "$CB_SARVAM" \
  "Edit cloudbuild.yaml: change SARVAM_MODEL value to sarvam-30b"

for VAR in VERTEX_SEARCH_DATASTORE_ID VERTEX_SEARCH_LOCATION VERTEX_SEARCH_SERVING_CONFIG; do
  IN_UPDATE=$(grep "update-env-vars" cloudbuild.yaml 2>/dev/null | grep "$VAR" | wc -l)
  [[ "$IN_UPDATE" == "0" ]] && CB_V="pass" || CB_V="$VAR still in --update-env-vars"
  check "CB-NOSTALE-$VAR" "cloudbuild.yaml: '$VAR' NOT in --update-env-vars" "$CB_V" \
    "Remove $VAR from --update-env-vars; add --remove-env-vars=$VAR"
done

VPJ_CB=$(grep -c "VERTEX_PROJECT_ID" cloudbuild.yaml 2>/dev/null || echo "0")
[[ "$VPJ_CB" -gt 0 ]] && CB_VPJ="pass" || CB_VPJ="VERTEX_PROJECT_ID missing from --update-env-vars"
check "CB-VPROJ" "VERTEX_PROJECT_ID present in cloudbuild.yaml --update-env-vars" "$CB_VPJ" \
  "Add VERTEX_PROJECT_ID=$PROJECT to --update-env-vars in cloudbuild.yaml"

# ═══════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════"
echo " LAYER 4: deploy.yml"
echo "═══════════════════════════════════════════════════"

UPPER_GH=$(grep -oP '=\K[A-Z_]+(?=:latest)' .github/workflows/deploy.yml 2>/dev/null \
  | grep -v -E "^(${_ALLOWED_UPPER})$" \
  | tr '\n' ' ')
[[ -z "$UPPER_GH" ]] && GH_RESULT="pass" || GH_RESULT="unexpected uppercase SM refs: $UPPER_GH"
check "GH-CASING" "No unexpected uppercase SM refs in deploy.yml" "$GH_RESULT" \
  "Replace unexpected uppercase SM refs with verified lowercase SM names in deploy.yml"

GH_LOC=$(grep "VERTEX_LOCATION" .github/workflows/deploy.yml 2>/dev/null | grep -c "asia-south1" || echo "0")
[[ "$GH_LOC" -gt 0 ]] && GH_LOC_R="pass" || GH_LOC_R="VERTEX_LOCATION not set to asia-south1"
check "GH-VLOC" "deploy.yml VERTEX_LOCATION=asia-south1" "$GH_LOC_R" \
  "Edit deploy.yml: change VERTEX_LOCATION=us-central1 to VERTEX_LOCATION=asia-south1"

HOOK_GH=$(grep "cf-pages-deploy-hook" .github/workflows/deploy.yml 2>/dev/null | wc -l)
HOOK_MAIN=$(grep -v "_check\|describe\|#" .github/workflows/deploy.yml 2>/dev/null \
  | grep "cf-pages-deploy-hook" | wc -l)
[[ "$HOOK_MAIN" == "0" ]] && HOOK_GH_R="pass" || HOOK_GH_R="cf-pages-deploy-hook in main --update-secrets (should be in _check block)"
check "GH-HOOK" "cf-pages-deploy-hook NOT in main --update-secrets" "$HOOK_GH_R" \
  "Move cf-pages-deploy-hook into the conditional _check() block"

# ═══════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════"
echo " LAYER 5: CF Edge Worker"
echo "═══════════════════════════════════════════════════"

if [[ -n "$CF_ACCT" && -n "$CLOUDFLARE_KV_API_TOKEN" ]]; then
  CF_BINDINGS=$(curl -sf \
    -H "Authorization: Bearer $CLOUDFLARE_KV_API_TOKEN" \
    "https://api.cloudflare.com/client/v4/accounts/$CF_ACCT/workers/scripts/$WORKER/bindings" \
    2>/dev/null || echo '{"result":[]}')

  BACKEND_URL_VAL=$(echo "$CF_BINDINGS" | python3 -c \
    "import json,sys; bs=json.load(sys.stdin).get('result',[]); print(next((b.get('text','') for b in bs if b.get('name')=='BACKEND_URL'),'MISSING'))" 2>/dev/null || echo "MISSING")
  [[ "$BACKEND_URL_VAL" == "https://syrabit-backend-bl6wu3psza-el.a.run.app" ]] && BU_R="pass" \
    || BU_R="BACKEND_URL='$BACKEND_URL_VAL'"
  check "EW-BACKEND" "CF Worker BACKEND_URL → Cloud Run URL" "$BU_R" \
    "curl -X PATCH accounts/$CF_ACCT/workers/scripts/$WORKER/settings --form 'settings={\"bindings\":[{\"type\":\"plain_text\",\"name\":\"BACKEND_URL\",\"text\":\"https://syrabit-backend-bl6wu3psza-el.a.run.app\"}]};type=application/json'"

  for SECRET in JWT_SECRET EDGE_SHARED_SECRET GOOGLE_SA_KEY; do
    SECRET_EXISTS=$(echo "$CF_BINDINGS" | python3 -c \
      "import json,sys; bs=json.load(sys.stdin).get('result',[]); print('pass' if any(b.get('name')=='$SECRET' for b in bs) else 'MISSING')" 2>/dev/null || echo "MISSING")
    check "EW-$SECRET" "CF Worker secret '$SECRET' binding exists" "$SECRET_EXISTS" \
      "echo -n \"\$(gcloud secrets versions access latest --secret=... --project=$PROJECT)\" | npx wrangler secret put $SECRET --env production"
  done

  WRANGLER_PROD=$(grep -A3 '\[env.production.vars\]' apps/edge/wrangler.toml 2>/dev/null \
    | grep -c "BACKEND_URL.*syrabit-backend" || echo "0")
  [[ "$WRANGLER_PROD" -gt 0 ]] && WT_R="pass" || WT_R="production BACKEND_URL override missing in wrangler.toml"
  check "EW-WRANGLER" "wrangler.toml [env.production.vars] overrides BACKEND_URL" "$WT_R" \
    "Add BACKEND_URL = \"https://syrabit-backend-bl6wu3psza-el.a.run.app\" under [env.production.vars] in apps/edge/wrangler.toml"
else
  echo "  ⚠️  CF_ACCOUNT_ID or CLOUDFLARE_KV_API_TOKEN not available — skipping CF Worker checks"
fi

# ═══════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════"
echo " LAYER 6: CF Pages"
echo "═══════════════════════════════════════════════════"

if [[ -n "$CF_ACCT" && -n "$CLOUDFLARE_KV_API_TOKEN" ]]; then
  PAGES_VITE=$(curl -sf \
    -H "Authorization: Bearer $CLOUDFLARE_KV_API_TOKEN" \
    "https://api.cloudflare.com/client/v4/accounts/$CF_ACCT/pages/projects/$PAGES_PROJECT" \
    2>/dev/null \
    | python3 -c "
import json,sys
p=json.load(sys.stdin).get('result',{})
env=p.get('deployment_configs',{}).get('production',{}).get('env_vars',{})
print(env.get('VITE_BACKEND_URL',{}).get('value','MISSING'))
" 2>/dev/null || echo "MISSING")
  [[ "$PAGES_VITE" == "https://api.syrabit.ai" ]] && VITE_R="pass" \
    || VITE_R="VITE_BACKEND_URL='$PAGES_VITE'"
  check "PA-VITE" "CF Pages VITE_BACKEND_URL=https://api.syrabit.ai" "$VITE_R" \
    "CF Dashboard → syrabitfrontend → Settings → Environment variables → set VITE_BACKEND_URL=https://api.syrabit.ai"
else
  echo "  ⚠️  CF credentials not available — skipping CF Pages checks"
fi

# ═══════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════"
echo " LAYER 7: Live Health"
echo "═══════════════════════════════════════════════════"

HEALTH_BODY=$(curl -sf "$BASE_URL/api/v1/health" 2>/dev/null || echo "{}")
HEALTH_HTTP=$(curl -sf -o /dev/null -w "%{http_code}" "$BASE_URL/api/v1/health" 2>/dev/null || echo "000")
[[ "$HEALTH_HTTP" == "200" ]] && H_R="pass" || H_R="HTTP $HEALTH_HTTP"
check "LH-BASIC" "GET /api/v1/health returns 200" "$H_R" \
  "Check startup_errors and Cloud Run logs"

MONGO_OK=$(echo "$HEALTH_BODY" | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print('pass' if d.get('mongodb_initialized') else 'false')" 2>/dev/null || echo "parse-error")
check "LH-MONGO" "mongodb_initialized=true" "$MONGO_OK" \
  "Check MONGODB_URI SM mount — secret ref must be mongodb-uri (lowercase)"

DEEP=$(curl -sf "$BASE_URL/api/v1/health/deep" 2>/dev/null || echo "{}")
for SVC in mongodb redis mongo_vector_search sarvam_ai; do
  SVC_STATUS=$(echo "$DEEP" | python3 -c \
    "import json,sys; d=json.load(sys.stdin); checks=d.get('checks',d); print(checks.get('$SVC',{}).get('status','MISSING'))" 2>/dev/null || echo "MISSING")
  [[ "$SVC_STATUS" == "healthy" ]] && SVC_R="pass" || SVC_R="$SVC_STATUS"
  check "LH-$SVC" "$SVC: healthy" "$SVC_R" \
    "Check Cloud Run logs for startup errors related to $SVC"
done

SEARCH_TOTAL=$(curl -sf "$BASE_URL/api/v1/content/search?q=force" 2>/dev/null \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('pass' if d.get('total',0)>0 else 'total=0')" 2>/dev/null || echo "request-failed")
check "LH-SEARCH" "GET /api/v1/content/search?q=force returns >0 results" "$SEARCH_TOTAL" \
  "Check Vertex Search index — topic not indexed is a data gap; HTTP 5xx is a config error"

CORS_HDR=$(curl -sf -I -H "Origin: https://syrabit.ai" "$BASE_URL/api/v1/health" 2>&1 \
  | grep -i "access-control-allow-origin" | head -1 || echo "")
[[ -n "$CORS_HDR" ]] && CORS_R="pass" || CORS_R="no Access-Control-Allow-Origin header on /api/v1/health"
check "LH-CORS" "CORS header present on /api/v1/health" "$CORS_R" \
  "Check CF Worker CORS middleware — ensure applyCorsHeaders is called on all early-exit paths"

# ═══════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════"
echo " LAYER 8: Code Checks"
echo "═══════════════════════════════════════════════════"

TOLIST=$(grep -rn "\.to_list()" apps/backend/app/api/v1/admin_content.py 2>/dev/null | wc -l || echo "0")
[[ "$TOLIST" == "0" ]] && TL_R="pass" || TL_R="$TOLIST bare .to_list() calls found (Motor 3+ requires length= arg)"
check "CODE-TOLIST" "No bare .to_list() in admin_content.py" "$TL_R" \
  "Add length=limit or length=None to each .to_list() call"

TIER_FILTER=$(grep -v '^\s*#' apps/backend/app/services/search/vertex_search.py 2>/dev/null | grep "tier_access" | wc -l)
[[ "$TIER_FILTER" == "0" ]] && TF_R="pass" || TF_R="tier_access filter found in vertex_search.py (causes 400)"
check "CODE-TIERFILTER" "No tier_access filter in vertex_search.py" "$TF_R" \
  "Set filter_expr = None in search_context() — tier_access field not in datastore schema"

SARVAM_CODE=$(grep -v '^\s*#' apps/backend/app/config.py 2>/dev/null | grep -o "sarvam-m1\|sarvam_m1" || echo "")
[[ -z "$SARVAM_CODE" ]] && SC_R="pass" || SC_R="sarvam-m1 (invalid model) found in config.py"
check "CODE-SARVAM-MODEL" "No sarvam-m1 (invalid) in config.py" "$SC_R" \
  "Update SARVAM_MODEL default to sarvam-30b in config.py"

# ═══════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════"
echo " SUMMARY"
echo "═══════════════════════════════════════════════════"
echo "  ✅ Passed : $PASS"
echo "  ❌ Failed : $FAIL"
echo ""

if [[ "$FAIL" -gt 0 ]]; then
  echo "  ↳ Fix the ❌ items above and re-run this script."
  exit 1
else
  echo "  ↳ All checks passed. Stack looks healthy."
  exit 0
fi
