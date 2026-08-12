#!/usr/bin/env bash
# =============================================================================
#  SYRABIT — GCP + SARVAM STACK TEST
#  Tests the current production stack: Cloud Run, Secret Manager, Sarvam AI,
#  MongoDB, Cloudflare Worker. Vertex Search and Gemini are NOT tested here
#  as they have been removed from this project.
#
#  Usage (from repo root or Cloud Shell):
#    bash scripts/test-gcp-stack.sh
#
#  Env overrides (all optional):
#    GCP_PROJECT   default: blissful-acumen-495019-t6
#    GCP_REGION    default: asia-south1
#    GCP_SERVICE   default: syrabit-backend
#    API_URL       default: https://api.syrabit.ai
# =============================================================================
set -uo pipefail

GCP_PROJECT="${GCP_PROJECT:-blissful-acumen-495019-t6}"
GCP_REGION="${GCP_REGION:-asia-south1}"
GCP_SERVICE="${GCP_SERVICE:-syrabit-backend}"
API_URL="${API_URL:-https://api.syrabit.ai}"

# ── Colours ───────────────────────────────────────────────────────────────────
R='\033[91m'; G='\033[92m'; Y='\033[93m'; B='\033[94m'; C='\033[96m'
BOLD='\033[1m'; DIM='\033[2m'; X='\033[0m'

PASS=0; FAIL=0; WARN=0
declare -a FAILURES=()

_ok()   { echo -e "  ${G}✓${X}  $1"; ((PASS++))  || true; }
_fail() { echo -e "  ${R}✗${X}  $1"; ((FAIL++))  || true; FAILURES+=("$1"); }
_warn() { echo -e "  ${Y}⚠${X}  $1"; ((WARN++))  || true; }
_info() { echo -e "  ${DIM}·${X}  $1"; }
_head() {
  echo ""
  echo -e "${BOLD}${C}══════════════════════════════════════════════════════${X}"
  echo -e "${BOLD}${C}  $1${X}"
  echo -e "${BOLD}${C}══════════════════════════════════════════════════════${X}"
}

jq_py() {
  # jq_py <json_string> <python_expr>
  echo "$1" | python3 -c "import json,sys; d=json.load(sys.stdin); v=$2; print(v if v is not None else '')" 2>/dev/null || echo ""
}

START=$(date +%s)

echo ""
echo -e "${BOLD}${C}╔══════════════════════════════════════════════════════╗${X}"
echo -e "${BOLD}${C}║     SYRABIT GCP STACK TEST                           ║${X}"
echo -e "${BOLD}${C}║     $(date -u '+%Y-%m-%d %H:%M UTC')                          ║${X}"
echo -e "${BOLD}${C}╚══════════════════════════════════════════════════════╝${X}"
echo ""
echo -e "  ${B}Project :${X} ${GCP_PROJECT}"
echo -e "  ${B}Region  :${X} ${GCP_REGION}"
echo -e "  ${B}Service :${X} ${GCP_SERVICE}"
echo -e "  ${B}API     :${X} ${API_URL}"
echo ""
echo -e "  ${DIM}AI: Sarvam AI (sarvam-105b) primary; Gemini 2.5 Flash fallback when Sarvam billing exhausted.${X}"
echo -e "  ${DIM}RAG: MongoDB vector search (text-embedding-005).${X}"

# ── Preflight: gcloud available ───────────────────────────────────────────────
if ! command -v gcloud &>/dev/null; then
  echo -e "\n${R}ERROR: gcloud not found. Run in Google Cloud Shell or after 'gcloud init'.${X}\n"
  exit 1
fi

ACTIVE_ACCT=$(gcloud auth list --filter="status=ACTIVE" --format="value(account)" 2>/dev/null | head -1)
if [[ -z "$ACTIVE_ACCT" ]]; then
  echo -e "\n${R}ERROR: no active gcloud account. Run: gcloud auth login${X}\n"
  exit 1
fi
echo -e "  ${B}Auth    :${X} ${ACTIVE_ACCT}"

# =============================================================================
# 1. CLOUD RUN — SERVICE STATUS
# =============================================================================
_head "1. Cloud Run — Service & Revision"

SVC_URL=$(gcloud run services describe "$GCP_SERVICE" \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="value(status.url)" 2>/dev/null || echo "")

if [[ -z "$SVC_URL" ]]; then
  _fail "Service '$GCP_SERVICE' not found in $GCP_REGION"
else
  _ok "Service found: ${SVC_URL}"
fi

REV=$(gcloud run revisions list \
  --service "$GCP_SERVICE" --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --sort-by="~metadata.creationTimestamp" --limit=1 \
  --format="value(metadata.name)" 2>/dev/null || echo "")

if [[ -n "$REV" ]]; then
  _ok "Latest revision: ${REV}"
  READY=$(gcloud run revisions describe "$REV" \
    --region "$GCP_REGION" --project "$GCP_PROJECT" \
    --format="value(status.conditions[0].status)" 2>/dev/null || echo "")
  if [[ "$READY" == "True" ]]; then
    _ok "Revision ready: True"
  else
    _fail "Revision ready: ${READY:-unknown} (expected True)"
  fi
else
  _fail "Could not list revisions (check IAM: roles/run.viewer)"
fi

TRAFFIC=$(gcloud run services describe "$GCP_SERVICE" \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="value(status.traffic[0].percent)" 2>/dev/null || echo "")
if [[ "$TRAFFIC" == "100" ]]; then
  _ok "100% traffic on latest revision"
else
  _warn "Traffic=${TRAFFIC:-?}% (expected 100)"
fi

MIN_INST=$(gcloud run services describe "$GCP_SERVICE" \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="value(spec.template.metadata.annotations.'run.googleapis.com/minScale')" 2>/dev/null || echo "0")
if [[ "${MIN_INST:-0}" -ge 1 ]]; then
  _ok "min-instances=${MIN_INST} (no cold starts)"
else
  _warn "min-instances=${MIN_INST:-0} — cold starts possible; set >=1 for production"
fi

# =============================================================================
# 2. SECRET MANAGER — REQUIRED SECRETS
# =============================================================================
_head "2. Secret Manager — Required Secrets"

# Secret names must match the SM names used in cloudbuild.yaml --update-secrets,
# NOT the Cloud Run env var names (which are different — e.g. env var MONGODB_URI
# is backed by SM secret mongodb-uri).  Using the wrong name here produces false
# MISSING failures even when the deploy succeeds.
REQUIRED_SECRETS=(
  "mongodb-uri"
  "jwt-secret"
  "edge-shared-secret"
  "admin-jwt-secret"
  "sarvam-api-key"
  "resend-api-key"
  "razorpay-key-id"
  "razorpay-key-secret"
  "razorpay-webhook-secret"
  "GOOGLE_APPLICATION_CREDENTIALS_JSON"
  "reset-token-secret"
  "translate-cron-secret"
)

OPTIONAL_SECRETS=(
  "SENTRY_DSN"
  "posthog-api-key"
  "indexnow-api-key"
)

for SECRET in "${REQUIRED_SECRETS[@]}"; do
  EXISTS=$(gcloud secrets describe "$SECRET" \
    --project "$GCP_PROJECT" \
    --format="value(name)" 2>/dev/null || echo "")
  if [[ -n "$EXISTS" ]]; then
    VER=$(gcloud secrets versions list "$SECRET" \
      --project "$GCP_PROJECT" \
      --filter="state=ENABLED" \
      --format="value(name)" \
      --limit=1 2>/dev/null || echo "")
    if [[ -n "$VER" ]]; then
      _ok "${SECRET}"
    else
      _fail "${SECRET} — exists but has NO enabled version"
    fi
  else
    _fail "${SECRET} — MISSING from Secret Manager"
  fi
done

echo ""
echo -e "  ${DIM}Optional secrets:${X}"
for SECRET in "${OPTIONAL_SECRETS[@]}"; do
  EXISTS=$(gcloud secrets describe "$SECRET" \
    --project "$GCP_PROJECT" \
    --format="value(name)" 2>/dev/null || echo "")
  if [[ -n "$EXISTS" ]]; then
    _ok "${SECRET} (optional, present)"
  else
    _warn "${SECRET} — not set (optional)"
  fi
done

# Also confirm removed secrets are NOT accidentally still mounted
_head "2b. Confirming Removed Secrets Are Gone"
# Note: GEMINI_API_KEY is intentionally kept — it powers the Sarvam fallback.
DEAD_SECRETS=("VERTEX_AI_SA_KEY" "VERTEX_SEARCH_DATASTORE_ID")
for SECRET in "${DEAD_SECRETS[@]}"; do
  EXISTS=$(gcloud secrets describe "$SECRET" \
    --project "$GCP_PROJECT" \
    --format="value(name)" 2>/dev/null || echo "")
  if [[ -n "$EXISTS" ]]; then
    _warn "${SECRET} — still exists in Secret Manager (safe to delete manually)"
  else
    _ok "${SECRET} — confirmed absent"
  fi
done

# =============================================================================
# 3. CLOUD RUN — MOUNTED ENV VARS (confirm vertex vars removed)
# =============================================================================
_head "3. Cloud Run — Env Var Audit"

ENV_VARS=$(gcloud run services describe "$GCP_SERVICE" \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="json" 2>/dev/null | python3 -c "
import json,sys
spec = json.load(sys.stdin)
containers = spec.get('spec',{}).get('template',{}).get('spec',{}).get('containers',[])
for c in containers:
    for e in c.get('env',[]):
        if 'valueFrom' not in e:
            print(f\"{e['name']}={e.get('value','')}\")
" 2>/dev/null || echo "")

SARVAM_MODEL_SET=$(echo "$ENV_VARS" | grep "SARVAM_MODEL=" || echo "")
if [[ -n "$SARVAM_MODEL_SET" ]]; then
  _ok "SARVAM_MODEL env var: ${SARVAM_MODEL_SET##*=}"
else
  _warn "SARVAM_MODEL not set explicitly (will default to sarvam-105b via config.py)"
fi

TRUST_EDGE=$(echo "$ENV_VARS" | grep "TRUST_EDGE_AUTH=" || echo "")
if [[ -n "$TRUST_EDGE" ]]; then
  _ok "TRUST_EDGE_AUTH: ${TRUST_EDGE##*=}"
fi

# Check that vertex env vars are absent
for DEAD_VAR in "VERTEX_PROJECT_ID" "VERTEX_LOCATION" "VERTEX_GEMINI_MODEL" "VERTEX_SEARCH_DATASTORE_ID"; do
  if echo "$ENV_VARS" | grep -q "^${DEAD_VAR}="; then
    _fail "${DEAD_VAR} is still set in Cloud Run env vars — run deploy to clean up"
  else
    _ok "${DEAD_VAR} — not present in env vars"
  fi
done

# =============================================================================
# 4. GCP SA KEY — VALIDATE FORMAT
# =============================================================================
_head "4. GCP Service Account Key Validity"

SA_KEY_JSON=$(gcloud secrets versions access latest \
  --secret="GOOGLE_APPLICATION_CREDENTIALS_JSON" \
  --project="$GCP_PROJECT" 2>/dev/null || echo "")

if [[ -z "$SA_KEY_JSON" ]]; then
  _fail "Could not read GOOGLE_APPLICATION_CREDENTIALS_JSON from Secret Manager"
else
  SA_TYPE=$(echo "$SA_KEY_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('type',''))" 2>/dev/null || echo "")
  SA_EMAIL=$(echo "$SA_KEY_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('client_email',''))" 2>/dev/null || echo "")
  SA_PROJECT=$(echo "$SA_KEY_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('project_id',''))" 2>/dev/null || echo "")

  if [[ "$SA_TYPE" == "service_account" ]]; then
    _ok "SA key type: service_account"
    _info "SA email  : ${SA_EMAIL}"
    _info "SA project: ${SA_PROJECT}"
  else
    _fail "SA key type='${SA_TYPE}' (expected 'service_account')"
  fi

  # Verify the SA can get a token (tests actual key validity)
  TOKEN=$(echo "$SA_KEY_JSON" | python3 -c "
import json, sys, time
try:
    import google.oauth2.service_account as sa
    import google.auth.transport.requests as tr
    creds = sa.Credentials.from_service_account_info(
        json.load(sys.stdin),
        scopes=['https://www.googleapis.com/auth/cloud-platform']
    )
    req = tr.Request()
    creds.refresh(req)
    print(creds.token[:20] + '...' if creds.token else '')
except Exception as e:
    print(f'ERROR: {e}')
" 2>/dev/null || echo "")
  if [[ "$TOKEN" == ERROR:* ]]; then
    _fail "SA key auth failed: $TOKEN"
  elif [[ -n "$TOKEN" ]]; then
    _ok "SA key can obtain OAuth2 token (key is valid)"
  else
    _warn "Could not test SA key token (google-auth not installed in test env)"
  fi
fi

# =============================================================================
# 5. SARVAM AI — LIVE ENDPOINT PING
# =============================================================================
_head "5. Sarvam AI — Live Endpoint Ping"

SARVAM_KEY=$(gcloud secrets versions access latest \
  --secret="SARVAM_API_KEY" \
  --project="$GCP_PROJECT" 2>/dev/null | head -1 || echo "")

if [[ -z "$SARVAM_KEY" ]]; then
  _fail "SARVAM_API_KEY not found in Secret Manager"
else
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 10 \
    -H "API-Subscription-Key: ${SARVAM_KEY}" \
    "https://api.sarvam.ai/v1" 2>/dev/null || echo "000")

  if [[ "$HTTP_CODE" == "000" ]]; then
    _fail "Cannot reach api.sarvam.ai (timeout or DNS failure)"
  elif [[ "$HTTP_CODE" -lt 500 ]]; then
    _ok "Sarvam AI endpoint reachable (HTTP ${HTTP_CODE})"
    _info "Any non-5xx from the base /v1 URL is expected (no GET handler there)"
  else
    _fail "Sarvam AI returned HTTP ${HTTP_CODE} (5xx = server error)"
  fi
fi

# =============================================================================
# 6. MONGODB — ATLAS CONNECTIVITY VIA HEALTH ENDPOINT
# =============================================================================
_head "6. MongoDB Atlas — Connectivity via Health Endpoint"

if [[ -z "$SVC_URL" ]]; then
  _warn "No Cloud Run URL — skipping MongoDB health probe"
else
  ID_TOKEN=$(gcloud auth print-identity-token 2>/dev/null || echo "")
  HEALTH_BODY=$(curl -s --max-time 15 \
    -H "Authorization: Bearer ${ID_TOKEN}" \
    "${SVC_URL}/api/v1/health" 2>/dev/null || echo "{}")

  MONGO_INIT=$(jq_py "$HEALTH_BODY" "d.get('mongodb_initialized','?')")
  H_STATUS=$(jq_py "$HEALTH_BODY" "d.get('status','?')")

  if [[ "$MONGO_INIT" == "True" ]]; then
    _ok "MongoDB initialized (mongodb_initialized=True, status=${H_STATUS})"
  elif [[ "$MONGO_INIT" == "False" ]]; then
    _fail "MongoDB NOT initialized — check MONGODB_URI and Atlas IP allowlist (0.0.0.0/0 for Cloud Run)"
  else
    _warn "Could not read mongodb_initialized (response: ${HEALTH_BODY:0:100})"
  fi
fi

# =============================================================================
# 7. CLOUDFLARE WORKER — EDGE HEALTH
# =============================================================================
_head "7. Cloudflare Worker — Edge Endpoint"

EDGE_CODE=$(curl -s -o /tmp/syrabit_edge_health.json \
  -w "%{http_code}" \
  --max-time 15 \
  "${API_URL}/health" 2>/dev/null || echo "000")

if [[ "$EDGE_CODE" == "200" ]]; then
  EDGE_BODY=$(cat /tmp/syrabit_edge_health.json 2>/dev/null || echo "{}")
  EDGE_STATUS=$(jq_py "$EDGE_BODY" "d.get('status','?')")
  BACKEND_OK=$(jq_py "$EDGE_BODY" "str(d.get('backend_reachable','?')).lower()")
  _ok "CF Worker /health → HTTP 200 (status=${EDGE_STATUS}, backend_reachable=${BACKEND_OK})"

  CF_RAY=$(curl -sI --max-time 5 "${API_URL}/health" 2>/dev/null | grep -i "cf-ray:" | head -1 | tr -d '\r' || echo "")
  if [[ -n "$CF_RAY" ]]; then
    _ok "CF-Ray header present — traffic is flowing through Cloudflare"
    _info "${CF_RAY}"
  else
    _warn "CF-Ray header missing — traffic may be bypassing Cloudflare"
  fi
elif [[ "$EDGE_CODE" == "503" ]]; then
  EDGE_BODY=$(cat /tmp/syrabit_edge_health.json 2>/dev/null || echo "{}")
  _fail "Edge /health → HTTP 503 (${EDGE_BODY:0:120})"
else
  _fail "Edge /health → HTTP ${EDGE_CODE}"
fi

# =============================================================================
# 8. DEEP HEALTH CHECK — ALL BACKEND SERVICES
# =============================================================================
_head "8. Backend /health/deep — All Services"

if [[ -z "$SVC_URL" ]]; then
  _warn "No Cloud Run URL — skipping deep health check"
else
  ID_TOKEN=$(gcloud auth print-identity-token 2>/dev/null || echo "")
  DEEP_CODE=$(curl -s -o /tmp/syrabit_deep.json \
    -w "%{http_code}" \
    --max-time 20 \
    -H "Authorization: Bearer ${ID_TOKEN}" \
    "${SVC_URL}/api/v1/health/deep" 2>/dev/null || echo "000")

  DEEP_BODY=$(cat /tmp/syrabit_deep.json 2>/dev/null || echo "{}")

  if [[ "$DEEP_CODE" == "200" || "$DEEP_CODE" == "503" ]]; then
    DEEP_STATUS=$(jq_py "$DEEP_BODY" "d.get('status','?')")
    MONGO_STATUS=$(jq_py "$DEEP_BODY" "d.get('checks',{}).get('mongodb',{}).get('status','?')")
    MVS_STATUS=$(jq_py "$DEEP_BODY" "d.get('checks',{}).get('mongo_vector_search',{}).get('status','?')")
    SARVAM_STATUS=$(jq_py "$DEEP_BODY" "d.get('checks',{}).get('sarvam_ai',{}).get('status','?')")

    [[ "$MONGO_STATUS" == "healthy" ]]  && _ok "mongodb: healthy"  || _fail "mongodb: ${MONGO_STATUS}"
    [[ "$MVS_STATUS" == "healthy" ]]    && _ok "mongo_vector_search: healthy" || _warn "mongo_vector_search: ${MVS_STATUS} (non-fatal)"
    [[ "$SARVAM_STATUS" == "healthy" ]] && _ok "sarvam_ai: healthy" || _fail "sarvam_ai: ${SARVAM_STATUS}"

    _info "Overall deep status: ${DEEP_STATUS}"
  else
    _warn "Deep health check returned HTTP ${DEEP_CODE} (may require Cloud Run IAM)"
  fi
fi

# =============================================================================
# 9. PROVIDERS HEALTH — DETAILED AI + SEARCH
# =============================================================================
_head "9. Provider Health — /api/v1/health/providers"

PROV_CODE=$(curl -s -o /tmp/syrabit_prov.json \
  -w "%{http_code}" \
  --max-time 20 \
  "${API_URL}/api/v1/health/providers" 2>/dev/null || echo "000")

PROV_BODY=$(cat /tmp/syrabit_prov.json 2>/dev/null || echo "{}")

if [[ "$PROV_CODE" == "200" || "$PROV_CODE" == "503" ]]; then
  SARVAM_P=$(jq_py "$PROV_BODY" "d.get('providers',{}).get('sarvam_ai',{}).get('status','?')")
  VS_P=$(jq_py "$PROV_BODY" "d.get('providers',{}).get('vector_search',{}).get('status','?')")
  CF_AI_P=$(jq_py "$PROV_BODY" "d.get('providers',{}).get('cloudflare_workers_ai',{}).get('status','?')")

  [[ "$SARVAM_P" == "healthy" ]] && _ok  "sarvam_ai provider: healthy" || _fail "sarvam_ai provider: ${SARVAM_P}"
  [[ "$VS_P"     == "healthy" ]] && _ok  "vector_search provider: healthy" || _warn "vector_search: ${VS_P} (non-fatal)"
  case "$CF_AI_P" in
    healthy)         _ok "cloudflare_workers_ai: healthy";;
    not_configured)  _warn "cloudflare_workers_ai: not_configured (CF_WORKER_AI_TOKEN not set)";;
    *)               _fail "cloudflare_workers_ai: ${CF_AI_P}";;
  esac
else
  _warn "Providers endpoint returned HTTP ${PROV_CODE} (CF Worker may require auth)"
fi

# =============================================================================
# 10. RECENT CLOUD RUN ERRORS
# =============================================================================
_head "10. Cloud Run — Recent Errors (last 30 min)"

ERROR_COUNT=$(gcloud logging read \
  "resource.type=cloud_run_revision \
   AND resource.labels.service_name=${GCP_SERVICE} \
   AND resource.labels.location=${GCP_REGION} \
   AND severity>=ERROR" \
  --project "$GCP_PROJECT" \
  --freshness=30m \
  --limit=50 \
  --format="value(timestamp)" 2>/dev/null | wc -l | tr -d ' ')

if [[ "${ERROR_COUNT:-0}" -eq 0 ]]; then
  _ok "No ERROR-level logs in last 30 min"
else
  _warn "${ERROR_COUNT} ERROR log lines in last 30 min"
  gcloud logging read \
    "resource.type=cloud_run_revision \
     AND resource.labels.service_name=${GCP_SERVICE} \
     AND resource.labels.location=${GCP_REGION} \
     AND severity>=ERROR" \
    --project "$GCP_PROJECT" \
    --freshness=30m \
    --limit=5 \
    --format="table[box](timestamp,jsonPayload.message)" \
    2>/dev/null | head -20 || true
fi

# Check for stale vertex import errors (should be zero after purge)
VERTEX_ERRORS=$(gcloud logging read \
  "resource.type=cloud_run_revision \
   AND resource.labels.service_name=${GCP_SERVICE} \
   AND resource.labels.location=${GCP_REGION} \
   AND (jsonPayload.message=~\"vertex\" OR jsonPayload.message=~\"gemini\")" \
  --project "$GCP_PROJECT" \
  --freshness=30m \
  --limit=5 \
  --format="value(timestamp)" 2>/dev/null | wc -l | tr -d ' ')

if [[ "${VERTEX_ERRORS:-0}" -eq 0 ]]; then
  _ok "No vertex/gemini errors in logs — purge confirmed clean"
else
  _fail "${VERTEX_ERRORS} vertex/gemini error(s) in last 30 min — check if dead import remains"
fi

# =============================================================================
# 11. CHAT PIPELINE INTEGRATION TEST
# =============================================================================
_head "11. Chat Pipeline — End-to-End Sarvam AI + RAG"

CRON_SECRET=$(gcloud secrets versions access latest \
  --secret="translate-cron-secret" \
  --project="$GCP_PROJECT" 2>/dev/null || echo "")

if [[ -z "$CRON_SECRET" ]]; then
  _warn "translate-cron-secret not in Secret Manager — skipping chat pipeline test"
else
  if [[ -z "$SVC_URL" ]]; then
    _warn "No Cloud Run URL — skipping chat pipeline test"
  else
    # Cloud Run now requires OIDC auth (org policy blocks allUsers invoker).
    # Pass OIDC in Authorization (Cloud Run IAM) and cron secret in X-User-JWT
    # (the health endpoint checks both, mirroring the CF Worker proxy behaviour).
    ID_TOKEN=$(gcloud auth print-identity-token 2>/dev/null || echo "")
    CHAT_CODE=$(curl -s -o /tmp/syrabit_chat_pipeline.json \
      -w "%{http_code}" \
      --max-time 40 \
      -H "Authorization: Bearer ${ID_TOKEN}" \
      -H "X-User-JWT: Bearer ${CRON_SECRET}" \
      "${SVC_URL}/api/v1/health/chat-pipeline" 2>/dev/null || echo "000")

    CHAT_BODY=$(cat /tmp/syrabit_chat_pipeline.json 2>/dev/null || echo "{}")

    if [[ "$CHAT_CODE" == "200" ]]; then
      CHAT_STATUS=$(jq_py "$CHAT_BODY" "d.get('status','?')")
      AI_PROVIDER=$(jq_py "$CHAT_BODY" "d.get('provider','?')")
      AI_LAT=$(jq_py "$CHAT_BODY" "str(d.get('latency_ms','?'))")
      RESP_PREVIEW=$(jq_py "$CHAT_BODY" "d.get('response_preview','?')")
      RAG_STATUS=$(jq_py "$CHAT_BODY" "d.get('rag_status','?')")
      RAG_TOPICS=$(jq_py "$CHAT_BODY" "str(d.get('rag_topics_cached','?'))")

      _ok "Chat pipeline healthy (status=${CHAT_STATUS}, provider=${AI_PROVIDER})"
      _info "AI latency     : ${AI_LAT}ms"
      _info "Response       : ${RESP_PREVIEW}"
      _info "RAG            : ${RAG_STATUS} (${RAG_TOPICS} topics cached)"

      if [[ "$AI_PROVIDER" == "gemini-2.5-flash" ]]; then
        _warn "Chat served by Gemini fallback — Sarvam billing exhausted or circuit open. Top up at api.sarvam.ai."
      fi
      if [[ "$RAG_STATUS" == "degraded" ]]; then
        _warn "RAG degraded — no topic embeddings cached (MongoDB vector search may be empty)"
      fi
    elif [[ "$CHAT_CODE" == "401" ]]; then
      _fail "Chat pipeline → HTTP 401 (translate-cron-secret mismatch between SM and Cloud Run)"
    elif [[ "$CHAT_CODE" == "503" ]]; then
      CHAT_ERR=$(jq_py "$CHAT_BODY" "d.get('error', d.get('step','?'))")
      _fail "Chat pipeline → HTTP 503 (${CHAT_ERR})"
    elif [[ "$CHAT_CODE" == "000" ]]; then
      _fail "Chat pipeline → connection timeout (Cloud Run not responding in 40s)"
    else
      _fail "Chat pipeline → HTTP ${CHAT_CODE}"
    fi
  fi
fi

# =============================================================================
# SUMMARY
# =============================================================================
ELAPSED=$(( $(date +%s) - START ))
echo ""
echo -e "${BOLD}${C}══════════════════════════════════════════════════════${X}"
echo -e "${BOLD}  Syrabit GCP Stack Test — Summary${X}"
echo -e "${BOLD}${C}══════════════════════════════════════════════════════${X}"
printf "  ${G}✓ Pass  :${X} %d\n"  "$PASS"
printf "  ${Y}⚠ Warn  :${X} %d\n"  "$WARN"
printf "  ${R}✗ Fail  :${X} %d\n"  "$FAIL"
printf "  Elapsed: %ds\n"          "$ELAPSED"

if [[ "${#FAILURES[@]}" -gt 0 ]]; then
  echo ""
  echo -e "  ${BOLD}${R}Failures:${X}"
  for f in "${FAILURES[@]}"; do
    echo -e "    ${R}✗${X} ${f}"
  done
fi

echo ""
if [[ "$FAIL" -gt 0 ]]; then
  echo -e "  ${R}${BOLD}STACK TEST FAILED — ${FAIL} issue(s) detected${X}"
  exit 1
else
  echo -e "  ${G}${BOLD}STACK TEST PASSED${X}"
  exit 0
fi
