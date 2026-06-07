#!/usr/bin/env bash
# verify-github-deploy-key.sh
#
# Pre-flight check: confirm every GCP-side prerequisite for Cloud Build Step 0
# is in place BEFORE triggering a full build.
#
# Checks:
#   1. Secret GITHUB_DEPLOY_SSH_KEY exists in Secret Manager
#   2. Secret has at least one enabled version
#   3. Secret value looks like a valid SSH private key
#   4. Default Cloud Build SA has roles/secretmanager.secretAccessor on the secret
#   5. Custom trigger SA (syrabit-backend-sa) has roles/secretmanager.secretAccessor
#
# IAM checks (4 & 5) are role-scoped: the SA must appear specifically under
# roles/secretmanager.secretAccessor, not just anywhere in the policy JSON.
#
# Does NOT check:
#   - Whether the public key is registered as a GitHub deploy key
#     (that requires a live SSH test via Cloud Build or outbound SSH to github.com)
#
# Usage (from repo root in Cloud Shell):
#   bash infra/scripts/verify-github-deploy-key.sh
#
# Exit code: 0 = all checks passed, 1 = one or more checks failed

set -uo pipefail

PROJECT="blissful-acumen-495019-t6"
SECRET_NAME="GITHUB_DEPLOY_SSH_KEY"
BACKEND_SA="syrabit-backend-sa@blissful-acumen-495019-t6.iam.gserviceaccount.com"
TARGET_ROLE="roles/secretmanager.secretAccessor"

PASS=0
FAIL=0

ok()   { echo "  ✓ $*"; PASS=$((PASS + 1)); }
fail() { echo "  ✗ $*"; FAIL=$((FAIL + 1)); }
info() { echo "    $*"; }

echo ""
echo "=== Syrabit: Cloud Build SSH Key Pre-flight Check ==="
echo "    Project : ${PROJECT}"
echo "    Secret  : ${SECRET_NAME}"
echo ""

# ── Check 1: Secret exists ──────────────────────────────────────────────────
echo "[ 1/5 ] Secret exists in Secret Manager..."
if gcloud secrets describe "${SECRET_NAME}" \
     --project="${PROJECT}" >/dev/null 2>&1; then
  ok "Secret '${SECRET_NAME}' exists"
else
  fail "Secret '${SECRET_NAME}' NOT found — run setup-github-deploy-key.sh first"
  echo ""
  echo "RESULT: ${PASS} passed, ${FAIL} failed — CANNOT CONTINUE (secret missing)"
  exit 1
fi

# ── Check 2: At least one enabled version ───────────────────────────────────
echo ""
echo "[ 2/5 ] Secret has an enabled version..."
LATEST_STATE=$(gcloud secrets versions describe latest \
  --secret="${SECRET_NAME}" \
  --project="${PROJECT}" \
  --format="value(state)" 2>/dev/null || echo "MISSING")

if [[ "${LATEST_STATE}" == "ENABLED" ]]; then
  ok "Secret version 'latest' is ENABLED"
else
  fail "Secret version 'latest' state is '${LATEST_STATE}' (expected ENABLED)"
  info "To add a new version:"
  info "  gcloud secrets versions add ${SECRET_NAME} --data-file=<key_file> --project=${PROJECT}"
fi

# ── Check 3: Value looks like an SSH private key ────────────────────────────
echo ""
echo "[ 3/5 ] Secret value is a valid SSH private key..."
SECRET_VALUE=$(gcloud secrets versions access latest \
  --secret="${SECRET_NAME}" \
  --project="${PROJECT}" 2>/dev/null || echo "")

if echo "${SECRET_VALUE}" | grep -q "BEGIN.*PRIVATE KEY"; then
  KEY_TYPE=$(echo "${SECRET_VALUE}" | grep "BEGIN" | sed 's/.*BEGIN //;s/ PRIVATE.*//')
  ok "Secret value is an SSH private key (type: ${KEY_TYPE})"
else
  fail "Secret value does not look like an SSH private key (missing 'BEGIN PRIVATE KEY' header)"
  info "Re-run setup-github-deploy-key.sh to regenerate and re-store the key"
fi

# ── Fetch IAM policy once for checks 4 & 5 ──────────────────────────────────
POLICY_JSON=$(gcloud secrets get-iam-policy "${SECRET_NAME}" \
  --project="${PROJECT}" \
  --format=json 2>/dev/null || echo "{}")

# Helper: returns 0 if <member> appears in the binding for <role>, else 1
# Usage: sa_has_role "$POLICY_JSON" "roles/secretmanager.secretAccessor" "serviceAccount:foo@..."
sa_has_role() {
  local policy="$1" role="$2" member="$3"
  # Use jq to extract members for the specific role, then grep for the member.
  # Falls back gracefully if jq is absent (Cloud Shell always has jq).
  if command -v jq >/dev/null 2>&1; then
    echo "${policy}" \
      | jq -r --arg role "${role}" \
          '.bindings[]? | select(.role == $role) | .members[]?' 2>/dev/null \
      | grep -qF "${member}"
  else
    # jq not available — fall back to a stricter grep anchored to the role block.
    # This is a best-effort fallback; jq is strongly preferred.
    echo "${policy}" | python3 -c "
import sys, json
data = json.load(sys.stdin)
role = '${role}'
member = '${member}'
for b in data.get('bindings', []):
    if b.get('role') == role and member in b.get('members', []):
        sys.exit(0)
sys.exit(1)
" 2>/dev/null
  fi
}

# ── Check 4: Default Cloud Build SA has secretAccessor ─────────────────────
echo ""
echo "[ 4/5 ] Default Cloud Build SA has ${TARGET_ROLE}..."
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT}" \
  --format="value(projectNumber)" 2>/dev/null || echo "")

if [[ -z "${PROJECT_NUMBER}" ]]; then
  fail "Could not resolve project number for ${PROJECT}"
else
  DEFAULT_CB_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
  if sa_has_role "${POLICY_JSON}" "${TARGET_ROLE}" "serviceAccount:${DEFAULT_CB_SA}"; then
    ok "serviceAccount:${DEFAULT_CB_SA} → ${TARGET_ROLE}"
  else
    fail "serviceAccount:${DEFAULT_CB_SA} NOT bound to ${TARGET_ROLE}"
    info "Fix:"
    info "  gcloud secrets add-iam-policy-binding ${SECRET_NAME} \\"
    info "    --member=serviceAccount:${DEFAULT_CB_SA} \\"
    info "    --role=${TARGET_ROLE} \\"
    info "    --project=${PROJECT}"
  fi
fi

# ── Check 5: Custom trigger SA has secretAccessor ───────────────────────────
echo ""
echo "[ 5/5 ] Custom trigger SA (syrabit-backend-sa) has ${TARGET_ROLE}..."
if sa_has_role "${POLICY_JSON}" "${TARGET_ROLE}" "serviceAccount:${BACKEND_SA}"; then
  ok "serviceAccount:${BACKEND_SA} → ${TARGET_ROLE}"
else
  fail "serviceAccount:${BACKEND_SA} NOT bound to ${TARGET_ROLE}"
  info "Fix:"
  info "  gcloud secrets add-iam-policy-binding ${SECRET_NAME} \\"
  info "    --member=serviceAccount:${BACKEND_SA} \\"
  info "    --role=${TARGET_ROLE} \\"
  info "    --project=${PROJECT}"
fi

# ── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "───────────────────────────────────────────────────"
if [[ ${FAIL} -eq 0 ]]; then
  echo "RESULT: ${PASS}/5 checks passed — GCP side is ready"
  echo ""
  echo "NEXT STEPS:"
  echo "  1. Confirm the GitHub deploy key is registered:"
  echo "     https://github.com/founder24/-kalukaliya/settings/keys"
  echo "     'Cloud Build (read-only)' must appear in the list."
  echo ""
  echo "  2. Trigger a build and watch Step 0:"
  echo "     gcloud builds submit --no-source \\"
  echo "       --config=cloudbuild.yaml \\"
  echo "       --project=${PROJECT}"
  echo ""
  echo "     Step 0 in the Cloud Build log should end with:"
  echo "       === SSH setup complete ==="
  echo ""
  echo "  3. Verify the most recent build's Step 0 passed (run after the build):"
  echo "     bash infra/scripts/verify-step0-passed.sh"
  exit 0
else
  echo "RESULT: ${PASS} passed, ${FAIL} failed — fix the issues above and re-run"
  exit 1
fi
