#!/usr/bin/env bash
# =============================================================================
#  SYRABIT — GCP INFRASTRUCTURE AUDIT
#  Requires: gcloud (authenticated), curl, python3
#
#  Usage (from repo root or Cloud Shell):
#    bash scripts/gcp-infra-audit.sh
#
#  Env overrides (all optional):
#    GCP_PROJECT   default: blissful-acumen-495019-t6
#    GCP_REGION    default: asia-south1
#    GCP_SERVICE   default: syrabit-backend
#    API_URL       default: https://api.syrabit.ai
# =============================================================================
set -euo pipefail

GCP_PROJECT="${GCP_PROJECT:-blissful-acumen-495019-t6}"
GCP_REGION="${GCP_REGION:-asia-south1}"
GCP_SERVICE="${GCP_SERVICE:-syrabit-backend}"
API_URL="${API_URL:-https://api.syrabit.ai}"
ARTIFACT_REPO="syrabit"

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

START=$(date +%s)

echo ""
echo -e "${BOLD}${C}╔══════════════════════════════════════════════════════╗${X}"
echo -e "${BOLD}${C}║     SYRABIT GCP INFRASTRUCTURE AUDIT                 ║${X}"
echo -e "${BOLD}${C}║     $(date -u '+%Y-%m-%d %H:%M UTC')                          ║${X}"
echo -e "${BOLD}${C}╚══════════════════════════════════════════════════════╝${X}"
echo ""
echo -e "  ${B}Project :${X} ${GCP_PROJECT}"
echo -e "  ${B}Region  :${X} ${GCP_REGION}"
echo -e "  ${B}Service :${X} ${GCP_SERVICE}"
echo -e "  ${B}API     :${X} ${API_URL}"

# ── Preflight: gcloud available and authenticated ─────────────────────────────
if ! command -v gcloud &>/dev/null; then
  echo -e "\n${R}ERROR: gcloud not found. Run this in Google Cloud Shell or after gcloud init.${X}\n"
  exit 1
fi

ACTIVE_ACCT=$(gcloud auth list --filter="status=ACTIVE" --format="value(account)" 2>/dev/null | head -1)
if [[ -z "$ACTIVE_ACCT" ]]; then
  echo -e "\n${R}ERROR: no active gcloud account. Run: gcloud auth login${X}\n"
  exit 1
fi
echo -e "  ${B}Auth    :${X} ${ACTIVE_ACCT}"

# =============================================================================
# 1. CLOUD RUN — REVISION & TRAFFIC
# =============================================================================
_head "1. Cloud Run — Revision & Traffic"

REV=$(gcloud run revisions list \
  --service "$GCP_SERVICE" --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --sort-by="~metadata.creationTimestamp" --limit=1 \
  --format="value(metadata.name)" 2>/dev/null || echo "")

if [[ -z "$REV" ]]; then
  _fail "Could not list revisions — check IAM (roles/run.viewer needed)"
else
  _ok "Latest revision: ${REV}"
fi

TRAFFIC=$(gcloud run services describe "$GCP_SERVICE" \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="value(status.traffic[0].percent)" 2>/dev/null || echo "")
REV_TRAFFIC=$(gcloud run services describe "$GCP_SERVICE" \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="value(status.traffic[0].revisionName)" 2>/dev/null || echo "?")

if [[ "$TRAFFIC" == "100" ]]; then
  _ok "100% traffic on ${REV_TRAFFIC}"
else
  _warn "Traffic split detected: ${REV_TRAFFIC} at ${TRAFFIC}% (expected 100%)"
fi

SVC_URL=$(gcloud run services describe "$GCP_SERVICE" \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="value(status.url)" 2>/dev/null || echo "")
_info "Cloud Run URL : ${SVC_URL}"

IMAGE=$(gcloud run services describe "$GCP_SERVICE" \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="value(spec.template.spec.containers[0].image)" 2>/dev/null || echo "")
_info "Image digest  : ${IMAGE##*@}"

REV_CREATED=$(gcloud run revisions describe "$REV" \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="value(metadata.creationTimestamp)" 2>/dev/null || echo "")
_info "Deployed at   : ${REV_CREATED}"

# Condition: Ready
READY=$(gcloud run revisions describe "$REV" \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="value(status.conditions[0].status)" 2>/dev/null || echo "")
if [[ "$READY" == "True" ]]; then
  _ok "Revision ready: True"
else
  _fail "Revision ready: ${READY} (expected True)"
fi

# Min instances
MIN_INST=$(gcloud run services describe "$GCP_SERVICE" \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="value(spec.template.metadata.annotations.'run.googleapis.com/minScale')" 2>/dev/null || echo "0")
if [[ "${MIN_INST:-0}" -ge 1 ]]; then
  _ok "min-instances=${MIN_INST} (no cold starts)"
else
  _warn "min-instances=${MIN_INST:-0} — cold starts possible (set to >=1 for production)"
fi

# Max instances
MAX_INST=$(gcloud run services describe "$GCP_SERVICE" \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="value(spec.template.metadata.annotations.'run.googleapis.com/maxScale')" 2>/dev/null || echo "?")
_info "max-instances : ${MAX_INST}"

# CPU / Memory
CPU=$(gcloud run services describe "$GCP_SERVICE" \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="value(spec.template.spec.containers[0].resources.limits.cpu)" 2>/dev/null || echo "?")
MEM=$(gcloud run services describe "$GCP_SERVICE" \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="value(spec.template.spec.containers[0].resources.limits.memory)" 2>/dev/null || echo "?")
_info "Resources     : cpu=${CPU}  memory=${MEM}"

# Concurrency
CONC=$(gcloud run services describe "$GCP_SERVICE" \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="value(spec.template.spec.containerConcurrency)" 2>/dev/null || echo "?")
_info "Concurrency   : ${CONC}"

# =============================================================================
# 2. CLOUD BUILD — TRIGGER & LAST BUILDS
# =============================================================================
_head "2. Cloud Build — Trigger & Build History"

TRIGGER_NAME=$(gcloud builds triggers list \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="value(name)" 2>/dev/null | head -1 || echo "")

if [[ -n "$TRIGGER_NAME" ]]; then
  _ok "Cloud Build trigger exists: ${TRIGGER_NAME}"
  TRIGGER_BRANCH=$(gcloud builds triggers list \
    --region "$GCP_REGION" --project "$GCP_PROJECT" \
    --format="value(github.push.branch)" 2>/dev/null | head -1 || echo "?")
  TRIGGER_REPO=$(gcloud builds triggers list \
    --region "$GCP_REGION" --project "$GCP_PROJECT" \
    --format="value(github.name)" 2>/dev/null | head -1 || echo "?")
  _info "Watches       : ${TRIGGER_REPO} @ branch ${TRIGGER_BRANCH}"
  TRIGGER_DISABLED=$(gcloud builds triggers list \
    --region "$GCP_REGION" --project "$GCP_PROJECT" \
    --format="value(disabled)" 2>/dev/null | head -1 || echo "")
  if [[ "$TRIGGER_DISABLED" == "True" ]]; then
    _warn "Trigger is DISABLED — pushes to main will NOT auto-deploy"
  else
    _ok "Trigger is enabled (auto-deploys on push to ${TRIGGER_BRANCH})"
  fi
else
  _fail "No Cloud Build trigger found — deploys are manual-only"
fi

echo ""
echo -e "  ${BOLD}Recent builds:${X}"
gcloud builds list \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --limit=5 \
  --format="table[box](id[:8],status,createTime.date('%Y-%m-%d %H:%M'),duration,substitutions.SHORT_SHA)" \
  2>/dev/null || _warn "Could not list builds (needs roles/cloudbuild.builds.viewer)"

LAST_STATUS=$(gcloud builds list \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --limit=1 \
  --format="value(status)" 2>/dev/null || echo "")
if [[ "$LAST_STATUS" == "SUCCESS" ]]; then
  _ok "Last build: SUCCESS"
elif [[ "$LAST_STATUS" == "WORKING" ]]; then
  _warn "Last build still WORKING (in progress)"
elif [[ -n "$LAST_STATUS" ]]; then
  _fail "Last build: ${LAST_STATUS}"
fi

# Artifact Registry — image count
IMG_COUNT=$(gcloud artifacts docker images list \
  "${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${ARTIFACT_REPO}/backend" \
  --format="value(IMAGE)" 2>/dev/null | wc -l || echo "?")
_info "Backend images in AR: ${IMG_COUNT}"

# =============================================================================
# 3. SECRET MANAGER — REQUIRED SECRETS
# =============================================================================
_head "3. Secret Manager — Required Secrets"

REQUIRED_SECRETS=(
  "MONGODB_URI"
  "JWT_SECRET"
  "SARVAM_API_KEY"
  "RESEND_API_KEY"
  "RAZORPAY_KEY_ID"
  "RAZORPAY_KEY_SECRET"
  "RAZORPAY_WEBHOOK_SECRET"
  "GOOGLE_APPLICATION_CREDENTIALS_JSON"
  "EDGE_SHARED_SECRET"
)

OPTIONAL_SECRETS=(
  "SENTRY_DSN"
  "TRANSLATE_CRON_SECRET"
  "RESET_TOKEN_SECRET"
)

for SECRET in "${REQUIRED_SECRETS[@]}"; do
  EXISTS=$(gcloud secrets describe "$SECRET" \
    --project "$GCP_PROJECT" \
    --format="value(name)" 2>/dev/null || echo "")
  if [[ -n "$EXISTS" ]]; then
    # Check it has at least one version
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

# =============================================================================
# 4. CLOUD RUN — MOUNTED SECRETS (live service binding check)
# =============================================================================
_head "4. Cloud Run — Mounted Secret Bindings"

SECRET_BINDINGS=$(gcloud run services describe "$GCP_SERVICE" \
  --region "$GCP_REGION" --project "$GCP_PROJECT" \
  --format="json" 2>/dev/null | \
  python3 -c "
import json,sys
spec = json.load(sys.stdin)
containers = spec.get('spec',{}).get('template',{}).get('spec',{}).get('containers',[])
bound = []
for c in containers:
    for e in c.get('env',[]):
        ref = e.get('valueFrom',{}).get('secretKeyRef',{})
        if ref:
            bound.append(e['name'])
    for v in c.get('volumeMounts',[]):
        bound.append(v['name'])
print('\n'.join(sorted(set(bound))))
" 2>/dev/null || echo "")

BOUND_COUNT=$(echo "$SECRET_BINDINGS" | grep -c '[^[:space:]]' 2>/dev/null; true)
if [[ "$BOUND_COUNT" -gt 0 ]]; then
  _ok "${BOUND_COUNT} secret bindings active in Cloud Run service"
  echo "$SECRET_BINDINGS" | while IFS= read -r s; do
    [[ -n "$s" ]] && _info "  bound: ${s}"
  done
else
  _warn "Could not enumerate secret bindings (check IAM)"
fi

# Critical: MONGODB_URI must be bound
if echo "$SECRET_BINDINGS" | grep -q "MONGODB_URI"; then
  _ok "MONGODB_URI is mounted"
else
  _fail "MONGODB_URI is NOT mounted in Cloud Run — MongoDB will fail to init"
fi

if echo "$SECRET_BINDINGS" | grep -q "JWT_SECRET"; then
  _ok "JWT_SECRET is mounted"
else
  _fail "JWT_SECRET is NOT mounted in Cloud Run — auth will fail"
fi

# =============================================================================
# 5. CLOUD RUN — LIVE HEALTH PROBE (direct, bypasses CF Worker)
# =============================================================================
_head "5. Cloud Run — Live Health Probe (direct URL)"

if [[ -z "$SVC_URL" ]]; then
  _warn "Could not get Cloud Run URL — skipping direct health probe"
else
  ID_TOKEN=$(gcloud auth print-identity-token 2>/dev/null || echo "")
  if [[ -z "$ID_TOKEN" ]]; then
    _warn "No identity token — direct Cloud Run call may return 403"
  fi

  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 15 \
    -H "Authorization: Bearer ${ID_TOKEN}" \
    "${SVC_URL}/health" 2>/dev/null || echo "000")

  if [[ "$HTTP_CODE" == "200" ]]; then
    HEALTH_BODY=$(curl -s --max-time 15 \
      -H "Authorization: Bearer ${ID_TOKEN}" \
      "${SVC_URL}/health" 2>/dev/null || echo "{}")
    HEALTH_STATUS=$(echo "$HEALTH_BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "?")
    MONGO_INIT=$(echo "$HEALTH_BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('mongodb_initialized','?'))" 2>/dev/null || echo "?")
    _ok "Direct Cloud Run /health → 200  (status=${HEALTH_STATUS}, mongodb_initialized=${MONGO_INIT})"
    if [[ "$MONGO_INIT" != "True" && "$MONGO_INIT" != "true" ]]; then
      _fail "mongodb_initialized=${MONGO_INIT} — check MONGODB_URI and Atlas IP allowlist"
    fi
  elif [[ "$HTTP_CODE" == "403" || "$HTTP_CODE" == "401" ]]; then
    _warn "Direct Cloud Run → HTTP ${HTTP_CODE} (IAM-protected — normal if auth via CF Worker only)"
  else
    _fail "Direct Cloud Run /health → HTTP ${HTTP_CODE}"
  fi
fi

# =============================================================================
# 6. CLOUD RUN — RECENT ERROR LOGS
# =============================================================================
_head "6. Cloud Run — Recent Errors (last 30 min)"

ERROR_COUNT=$(gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=${GCP_SERVICE} AND resource.labels.location=${GCP_REGION} AND severity>=ERROR" \
  --project "$GCP_PROJECT" \
  --freshness=30m \
  --limit=50 \
  --format="value(timestamp)" 2>/dev/null | wc -l | tr -d ' ')

if [[ "${ERROR_COUNT:-0}" -eq 0 ]]; then
  _ok "No ERROR-level logs in last 30 min"
else
  _warn "${ERROR_COUNT} ERROR log lines in last 30 min"
  echo ""
  echo -e "  ${BOLD}Recent errors:${X}"
  gcloud logging read \
    "resource.type=cloud_run_revision AND resource.labels.service_name=${GCP_SERVICE} AND resource.labels.location=${GCP_REGION} AND severity>=ERROR" \
    --project "$GCP_PROJECT" \
    --freshness=30m \
    --limit=10 \
    --format="table[box](timestamp,jsonPayload.level,jsonPayload.message)" \
    2>/dev/null | head -20 || true
fi

# Specific check: rate-limit RuntimeError (should be zero after fix)
RL_ERRORS=$(gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=${GCP_SERVICE} AND resource.labels.location=${GCP_REGION} AND jsonPayload.message=~\"Rate limiting unavailable\"" \
  --project "$GCP_PROJECT" \
  --freshness=15m \
  --limit=5 \
  --format="value(timestamp)" 2>/dev/null | wc -l | tr -d ' ')

if [[ "${RL_ERRORS:-0}" -eq 0 ]]; then
  _ok "No auth rate-limit RuntimeErrors in last 15 min  (MongoDB rate-limit fix verified)"
else
  _fail "${RL_ERRORS} auth rate-limit errors in last 15 min — Redis still being called"
fi

# =============================================================================
# 7. ARTIFACT REGISTRY — IMAGE FRESHNESS
# =============================================================================
_head "7. Artifact Registry — Latest Image"

LATEST_DIGEST=$(gcloud artifacts docker images list \
  "${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${ARTIFACT_REPO}/backend" \
  --sort-by="~updateTime" --limit=1 \
  --format="value(version)" 2>/dev/null || echo "")

LATEST_DATE=$(gcloud artifacts docker images list \
  "${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${ARTIFACT_REPO}/backend" \
  --sort-by="~updateTime" --limit=1 \
  --format="value(updateTime)" 2>/dev/null || echo "")

if [[ -n "$LATEST_DIGEST" ]]; then
  _ok "Latest image: ${LATEST_DIGEST:0:19}…  (${LATEST_DATE})"
else
  _warn "Could not list Artifact Registry images (check roles/artifactregistry.reader)"
fi

# Verify running image matches latest AR image
if [[ -n "$IMAGE" && -n "$LATEST_DIGEST" ]]; then
  RUNNING_DIGEST=$(echo "$IMAGE" | sed 's/.*@//')
  if [[ "$RUNNING_DIGEST" == "$LATEST_DIGEST" ]]; then
    _ok "Cloud Run is running the latest image"
  else
    _warn "Cloud Run image differs from latest AR image — may need redeploy"
    _info "Running : ${RUNNING_DIGEST:0:20}…"
    _info "Latest  : ${LATEST_DIGEST:0:20}…"
  fi
fi

# =============================================================================
# SUMMARY
# =============================================================================
ELAPSED=$(( $(date +%s) - START ))
echo ""
echo -e "${BOLD}${C}══════════════════════════════════════════════════════${X}"
echo -e "${BOLD}  GCP Infrastructure Audit — Summary${X}"
echo -e "${BOLD}${C}══════════════════════════════════════════════════════${X}"
printf "  ${G}✓ Pass  :${X} %d\n"  "$PASS"
printf "  ${Y}⚠ Warn  :${X} %d\n"  "$WARN"
printf "  ${R}✗ Fail  :${X} %d\n"  "$FAIL"
printf "  Elapsed: %ds\n"           "$ELAPSED"

if [[ "${#FAILURES[@]}" -gt 0 ]]; then
  echo ""
  echo -e "  ${BOLD}${R}Failures:${X}"
  for f in "${FAILURES[@]}"; do
    echo -e "    ${R}✗${X} ${f}"
  done
fi

echo ""
if [[ "$FAIL" -gt 0 ]]; then
  echo -e "  ${R}${BOLD}INFRA AUDIT FAILED — ${FAIL} critical issue(s) detected${X}"
  exit 1
else
  echo -e "  ${G}${BOLD}INFRA AUDIT PASSED${X}"
  exit 0
fi
