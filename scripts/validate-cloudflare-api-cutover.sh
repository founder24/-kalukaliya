#!/usr/bin/env bash
# Validate the D1/Workers API directly during a staged traffic cutover.
#
# Required: API_WORKER_URL, e.g. https://syrabit-api-prod.<account>.workers.dev
# Required: PUBLIC_EDGE_URL, e.g. https://api.syrabit.ai
# Optional: PUBLIC_SITE_URL, defaults to https://syrabit.ai
# Required: INDEXNOW_INTERNAL_SECRET for authenticated IndexNow validation
# Required for full validation: STUDENT_TOKEN, STAFF_TOKEN,
# ADMIN_SESSION_TOKEN, EDGE_SHARED_SECRET, and TRANSLATE_CRON_SECRET.
# Payment validation additionally requires CUTOVER_PAYMENT_TOKEN (a dedicated
# disposable-user access token), RAZORPAY_KEY_SECRET, and
# RAZORPAY_WEBHOOK_SECRET. The API Worker must be configured with a rzp_test_
# key; this check refuses to run against live Razorpay credentials.
# Set CUTOVER_RESET_ONLY=true only in the post-deploy reset job. It requires
# CUTOVER_RESET_EMAIL, CUTOVER_RESET_LINK, CUTOVER_RESET_PASSWORD, and the
# fresh CUTOVER_RESET_NONCE emitted by the preceding reset-request job.
# ADMIN_SESSION_TOKEN is the raw value of a disposable admin-session cookie,
# not a bearer token. This preserves the production admin-cookie contract
# through the public edge without exposing the token in logs.
# Set CUTOVER_STAGE=public only for a deliberately public-only preflight.
#
# This script creates one disposable Razorpay test-mode order and payment
# record for the dedicated CUTOVER_PAYMENT_TOKEN user. Successful verification
# removes its pending order; the user and resulting payment are intentionally
# isolated to that disposable fixture. It refuses a Cloud Run fallback where a
# D1-backed route is expected and never uses live Razorpay credentials.
set -euo pipefail

: "${PUBLIC_EDGE_URL:?Set PUBLIC_EDGE_URL to the deployed edge API origin}"
RESET_ONLY="${CUTOVER_RESET_ONLY:-false}"
if [[ "$RESET_ONLY" != "true" && "$RESET_ONLY" != "false" ]]; then
  echo "CUTOVER_RESET_ONLY must be true or false." >&2
  exit 1
fi
if [[ "$RESET_ONLY" != "true" ]]; then
  : "${API_WORKER_URL:?Set API_WORKER_URL to the deployed API Worker URL}"
  : "${INDEXNOW_INTERNAL_SECRET:?Set INDEXNOW_INTERNAL_SECRET for IndexNow validation}"
fi
BASE="${API_WORKER_URL:-}/api/v1"
EDGE_BASE="${PUBLIC_EDGE_URL%/}"
SITE_BASE="${PUBLIC_SITE_URL:-https://syrabit.ai}"
TMP_FILES=()
cleanup() { rm -f "${TMP_FILES[@]}"; }
trap cleanup EXIT

if [[ "$RESET_ONLY" != "true" && "${CUTOVER_STAGE:-full}" != "public" ]]; then
  : "${STUDENT_TOKEN:?Set STUDENT_TOKEN for authenticated student checks}"
  : "${STAFF_TOKEN:?Set STAFF_TOKEN for staff workflow checks}"
  : "${ADMIN_SESSION_TOKEN:?Set ADMIN_SESSION_TOKEN for admin workflow checks}"
  : "${EDGE_SHARED_SECRET:?Set EDGE_SHARED_SECRET for authenticated generation}"
  : "${TRANSLATE_CRON_SECRET:?Set TRANSLATE_CRON_SECRET for scheduled-operation checks}"
  : "${CUTOVER_PAYMENT_TOKEN:?Set CUTOVER_PAYMENT_TOKEN for the disposable payment user}"
  : "${RAZORPAY_KEY_SECRET:?Set RAZORPAY_KEY_SECRET for test payment verification}"
  : "${RAZORPAY_WEBHOOK_SECRET:?Set RAZORPAY_WEBHOOK_SECRET for signed webhook validation}"
fi

native_get() {
  local path="$1"
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 30 \
    --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    "${BASE}${path}")
  test "$status" = "200" || { cat "$output"; echo "Expected 200 for ${path}, got ${status}" >&2; exit 1; }
  grep -qi '^x-syrabit-route: worker-native' "$headers" || {
    cat "$headers"; echo "Expected Worker-native route for ${path}" >&2; exit 1;
  }
  cat "$output"
}

native_auth_get() {
  local path="$1"
  local token="${2:-${STUDENT_TOKEN}}"
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 30 \
    --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    -H "Authorization: Bearer ${token}" "${BASE}${path}")
  test "$status" = "200" || { cat "$output"; echo "Expected authenticated 200 for ${path}, got ${status}" >&2; exit 1; }
  grep -qi '^x-syrabit-route: worker-native' "$headers" || {
    cat "$headers"; echo "Expected Worker-native route for ${path}" >&2; exit 1;
  }
  cat "$output"
}

edge_auth_get() {
  local path="$1"
  local token="${2:-${STUDENT_TOKEN}}"
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 30 \
    --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    -H "Authorization: Bearer ${token}" "${EDGE_BASE}/api/v1${path}")
  test "$status" = "200" || { cat "$output"; echo "Expected public-edge authenticated 200 for ${path}, got ${status}" >&2; exit 1; }
  grep -qi '^x-syrabit-route: worker-native' "$headers" || {
    cat "$headers"; echo "Expected Worker-native public-edge route for ${path}" >&2; exit 1;
  }
  cat "$output"
}

edge_auth_status() {
  local path="$1"
  local expected_status="$2"
  local token="$3"
  local method="${4:-GET}"
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 30 \
    --request "$method" --header "Authorization: Bearer ${token}" \
    --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    "${EDGE_BASE}/api/v1${path}")
  test "$status" = "$expected_status" || {
    cat "$output"; echo "Expected public-edge ${expected_status} for ${path}, got ${status}" >&2; exit 1;
  }
  grep -qi '^x-syrabit-route: worker-native' "$headers" || {
    cat "$headers"; echo "Expected Worker-native public-edge route for ${path}" >&2; exit 1;
  }
  cat "$output"
}

edge_anon_get_status() {
  local path="$1"
  local expected_status="$2"
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 30 \
    --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    "${EDGE_BASE}/api/v1${path}")
  test "$status" = "$expected_status" || {
    cat "$output"; echo "Expected anonymous public-edge ${expected_status} for ${path}, got ${status}" >&2; exit 1;
  }
  grep -qi '^x-syrabit-route: worker-native' "$headers" || {
    cat "$headers"; echo "Expected Worker-native public-edge route for ${path}" >&2; exit 1;
  }
  cat "$output"
}

edge_admin_get() {
  local path="$1"
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 30 \
    --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    -H "Cookie: syrabit_admin_session=${ADMIN_SESSION_TOKEN}" "${EDGE_BASE}/api/v1${path}")
  test "$status" = "200" || { cat "$output"; echo "Expected public-edge admin 200 for ${path}, got ${status}" >&2; exit 1; }
  grep -qi '^x-syrabit-route: worker-native' "$headers" || {
    cat "$headers"; echo "Expected Worker-native public-edge admin route for ${path}" >&2; exit 1;
  }
  cat "$output"
}

edge_admin_fallback_get() {
  local path="$1"
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 30 \
    --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    -H "Cookie: syrabit_admin_session=${ADMIN_SESSION_TOKEN}" "${EDGE_BASE}/api/v1${path}")
  test "$status" = "200" || {
    cat "$output"; echo "Expected public-edge Cloud Run fallback 200 for ${path}, got ${status}" >&2; exit 1;
  }
  grep -qi '^x-syrabit-route: cloud-run-fallback' "$headers" || {
    cat "$headers"; echo "Expected intentional Cloud Run fallback route for ${path}" >&2; exit 1;
  }
  cat "$output"
}

edge_auth_json_status() {
  local path="$1"
  local expected_status="$2"
  local data="$3"
  local token="${4:-${STUDENT_TOKEN}}"
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 45 \
    --request POST --header 'Content-Type: application/json' \
    --header "Authorization: Bearer ${token}" --data "$data" \
    --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    "${EDGE_BASE}/api/v1${path}")
  test "$status" = "$expected_status" || {
    cat "$output"; echo "Expected public-edge ${expected_status} for ${path}, got ${status}" >&2; exit 1;
  }
  grep -qi '^x-syrabit-route: worker-native' "$headers" || {
    cat "$headers"; echo "Expected Worker-native public-edge route for ${path}" >&2; exit 1;
  }
  cat "$output"
}

edge_json_status() {
  local path="$1"
  local expected_status="$2"
  local data="$3"
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 30 \
    --request POST --header 'Content-Type: application/json' --data "$data" \
    --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    "${EDGE_BASE}/api/v1${path}")
  test "$status" = "$expected_status" || {
    cat "$output"; echo "Expected public-edge ${expected_status} for ${path}, got ${status}" >&2; exit 1;
  }
  grep -qi '^x-syrabit-route: worker-native' "$headers" || {
    cat "$headers"; echo "Expected Worker-native public-edge route for ${path}" >&2; exit 1;
  }
  cat "$output"
}

edge_admin_json_status() {
  local path="$1"
  local expected_status="$2"
  local data="$3"
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 30 \
    --request POST --header 'Content-Type: application/json' \
    --header "Cookie: syrabit_admin_session=${ADMIN_SESSION_TOKEN}" --data "$data" \
    --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    "${EDGE_BASE}/api/v1${path}")
  test "$status" = "$expected_status" || {
    cat "$output"; echo "Expected public-edge admin ${expected_status} for ${path}, got ${status}" >&2; exit 1;
  }
  grep -qi '^x-syrabit-route: worker-native' "$headers" || {
    cat "$headers"; echo "Expected Worker-native public-edge admin route for ${path}" >&2; exit 1;
  }
  cat "$output"
}

edge_webhook_invalid_signature() {
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 30 \
    --request POST --header 'Content-Type: application/json' \
    --header 'X-Razorpay-Signature: invalid-cutover-signature' \
    --data '{"event":"payment.captured","event_id":"evt_cutover_invalid","payload":{}}' \
    --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    "${EDGE_BASE}/api/webhooks/razorpay")
  test "$status" = "400" || {
    cat "$output"; echo "Expected invalid Razorpay webhook signature to return 400, got ${status}" >&2; exit 1;
  }
  grep -qi '^x-syrabit-route: worker-native' "$headers" || {
    cat "$headers"; echo "Expected Worker-native public-edge webhook route" >&2; exit 1;
  }
  cat "$output"
}

edge_webhook_signed() {
  local payload="$1"
  local signature="$2"
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 30 \
    --request POST --header 'Content-Type: application/json' \
    --header "X-Razorpay-Signature: ${signature}" \
    --data "$payload" \
    --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    "${EDGE_BASE}/api/webhooks/razorpay")
  test "$status" = "200" || {
    cat "$output"; echo "Expected signed Razorpay webhook to return 200, got ${status}" >&2; exit 1;
  }
  grep -qi '^x-syrabit-route: worker-native' "$headers" || {
    cat "$headers"; echo "Expected Worker-native public-edge webhook route" >&2; exit 1;
  }
  cat "$output"
}

native_status() {
  local path="$1"
  local expected_status="$2"
  local method="${3:-GET}"
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 30 \
    --request "$method" --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    "${BASE}${path}")
  test "$status" = "$expected_status" || {
    cat "$output"; echo "Expected ${expected_status} for ${path}, got ${status}" >&2; exit 1;
  }
  grep -qi '^x-syrabit-route: worker-native' "$headers" || {
    cat "$headers"; echo "Expected Worker-native route for ${path}" >&2; exit 1;
  }
  cat "$output"
}

native_indexnow_empty_submit() {
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 30 \
    --request POST --header 'Content-Type: application/json' \
    --header "X-IndexNow-Secret: ${INDEXNOW_INTERNAL_SECRET}" \
    --data '{"urls":[]}' --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    "${BASE}/indexnow/submit")
  test "$status" = "200" || { cat "$output"; echo "Expected 200 for authenticated IndexNow validation, got ${status}" >&2; exit 1; }
  grep -qi '^x-syrabit-route: worker-native' "$headers" || {
    cat "$headers"; echo "Expected Worker-native IndexNow route" >&2; exit 1;
  }
  cat "$output"
}

edge_native_get() {
  local path="$1"
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 30 \
    --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    "${EDGE_BASE}${path}")
  test "$status" = "200" || { cat "$output"; echo "Expected 200 for edge ${path}, got ${status}" >&2; exit 1; }
  grep -qi '^x-syrabit-route: worker-native' "$headers" || {
    cat "$headers"; echo "Expected Worker-native edge route for ${path}" >&2; exit 1;
  }
  if grep -qi '^x-robots-tag:.*noindex' "$headers"; then
    cat "$headers"; echo "Crawler artifact must not be marked noindex: ${path}" >&2; exit 1;
  fi
  cat "$output"
}

public_site_seo_get() {
  local path="$1"
  local expected_content_type="$2"
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 30 \
    --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    "${SITE_BASE}${path}")
  test "$status" = "200" || {
    cat "$output"; echo "Expected public site 200 for ${path}, got ${status}" >&2; exit 1;
  }
  grep -qi "^content-type: ${expected_content_type}" "$headers" || {
    cat "$headers"; echo "Expected ${expected_content_type} content type for ${path}" >&2; exit 1;
  }
  if grep -qi '^x-robots-tag:.*noindex' "$headers"; then
    cat "$headers"; echo "Crawler artifact must not be marked noindex: ${path}" >&2; exit 1;
  fi
  cat "$output"
}

run_disposable_reset_check() {
  local reset_var reset_token reset_confirm_payload reset_login_payload
  for reset_var in CUTOVER_RESET_EMAIL CUTOVER_RESET_LINK CUTOVER_RESET_PASSWORD CUTOVER_RESET_NONCE; do
    : "${!reset_var:?Set ${reset_var} for the post-deploy password-reset validation}"
  done
  if [[ "${CUTOVER_RESET_EMAIL,,}" != *cutover* ]]; then
    echo "CUTOVER_RESET_EMAIL must identify a disposable fixture by containing 'cutover'." >&2
    exit 1
  fi

  # The preceding workflow job generated the nonce and requested this email.
  # Requiring the delivered link to echo that nonce prevents an older or
  # already-consumed link from satisfying a later release's validation.
  reset_token=$(python3 - "$CUTOVER_RESET_LINK" "$CUTOVER_RESET_NONCE" <<'PY'
from urllib.parse import parse_qs, urlparse
import sys

parsed = urlparse(sys.argv[1])
expected_nonce = sys.argv[2]
if (
    parsed.scheme != "https"
    or parsed.netloc != "syrabit.ai"
    or parsed.path != "/reset-password"
    or parsed.fragment
):
    raise SystemExit("CUTOVER_RESET_LINK must be an https://syrabit.ai/reset-password link")
query = parse_qs(parsed.query, keep_blank_values=True)
tokens = query.get("token", [])
nonces = query.get("cutover_nonce", [])
if len(tokens) != 1 or not tokens[0]:
    raise SystemExit("CUTOVER_RESET_LINK must contain exactly one non-empty token")
if len(nonces) != 1 or nonces[0] != expected_nonce:
    raise SystemExit("CUTOVER_RESET_LINK was not issued by this release's reset request")
print(tokens[0])
PY
)
  export CUTOVER_RESET_TOKEN="$reset_token"

  reset_confirm_payload=$(python3 <<'PY'
import json, os
print(json.dumps({
    "token": os.environ["CUTOVER_RESET_TOKEN"],
    "password": os.environ["CUTOVER_RESET_PASSWORD"],
    "cutover_nonce": os.environ["CUTOVER_RESET_NONCE"],
}))
PY
)
  edge_json_status "/auth/reset-password/confirm" "200" "$reset_confirm_payload" \
    | python3 -c 'import json,sys; p=json.load(sys.stdin); assert p == {"message":"Password reset successfully"}, p'

  # The same token must be rejected after the first successful claim.
  edge_json_status "/auth/reset-password/confirm" "400" "$reset_confirm_payload" \
    | python3 -c 'import json,sys; p=json.load(sys.stdin); assert p == {"detail":"Invalid or expired reset token"}, p'

  reset_login_payload=$(python3 <<'PY'
import json, os
print(json.dumps({
    "email": os.environ["CUTOVER_RESET_EMAIL"],
    "password": os.environ["CUTOVER_RESET_PASSWORD"],
}))
PY
)
  edge_json_status "/auth/login" "200" "$reset_login_payload" \
    | python3 -c 'import json,sys; p=json.load(sys.stdin); assert isinstance(p.get("access_token"), str) and p["access_token"], p'
  echo "Fresh disposable password-reset delivery, password change, and token replay rejection passed."
}

if [[ "$RESET_ONLY" == "true" ]]; then
  run_disposable_reset_check
  exit 0
fi

echo "Checking public D1-backed routes at ${BASE}"
HEALTH_URL="${API_WORKER_URL%/}/health"
HEALTH=$(curl --silent --show-error --max-time 30 "$HEALTH_URL")
printf '%s' "$HEALTH" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert p["runtime"] == "cloudflare-workers" and p["components"]["d1"] == "healthy", p'
native_get "/content/library-bundle?slim=1" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert all(k in p for k in ("boards","classes","streams","subjects")), p'
native_get "/content/question-papers" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), list)'
echo "Checking Worker-native operational and crawler routes"
native_get "/analytics/top-routes" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert p == {"routes":[],"period":"7d"}, p'
native_get "/config/trustpilot" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert p is None or {"profileUrl","businessUnitId"} <= set(p), p'
native_get "/changelog" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), list)'
native_get "/seo/sitemap-index.xml" | grep -q '<sitemapindex'
native_get "/seo/sitemap-static.xml" | grep -q '<urlset'
native_get "/seo/feed.json" | python3 -c 'import json,sys; assert json.load(sys.stdin)["version"] == "https://jsonfeed.org/version/1.1"'
native_get "/seo/llms.txt" | grep -q 'Syrabit.ai'
# Preserve the unauthenticated contract, then prove the deployed Worker has both
# server-to-server secrets by using the safe empty-list path below.
native_status "/indexnow/submit" "403" "POST" | python3 -c 'import json,sys; assert json.load(sys.stdin)["detail"] == "Missing IndexNow secret"'
native_indexnow_empty_submit | python3 -c 'import json,sys; assert json.load(sys.stdin) == {"submitted":0,"failed":0,"detail":"No URLs provided"}'

# Publishing, content editing, RAG, and scheduled seed routes are native. The
# explicit compatibility bridge intentionally preserves unrelated, independently
# owned admin/seed operations until their Worker replacements are ready.
rg -q "api\.route\\('/api/v1/admin', +adminContentRouter\\)" apps/api/src/routes/index.ts || {
  echo "Native admin content router is not mounted." >&2; exit 1;
}
rg -q "api\.all\\('/api/v1/admin/\\*', +proxyToCloudRun\\)" apps/api/src/routes/index.ts || {
  echo "Explicit admin compatibility bridge is missing." >&2; exit 1;
}
rg -q "api\.all\\('/api/v1/seed/\\*', +proxyToCloudRun\\)" apps/api/src/routes/index.ts || {
  echo "Explicit seed compatibility bridge is missing." >&2; exit 1;
}

echo "Checking published crawler artifacts through the edge Worker"
edge_native_get "/robots.txt" | grep -q 'User-agent:'
edge_native_get "/sitemap-index.xml" | grep -q '<sitemapindex'
edge_native_get "/feed.xml" | grep -q '<rss'
edge_native_get "/feed.json" | python3 -c 'import json,sys; assert json.load(sys.stdin)["version"] == "https://jsonfeed.org/version/1.1"'
edge_native_get "/llms.txt" | grep -q 'Syrabit.ai'

echo "Checking crawler documents through the public Pages host at ${SITE_BASE}"
public_site_seo_get "/feed.xml" "application/rss+xml" | grep -q '<rss'
public_site_seo_get "/feed/notes.xml" "application/rss+xml" | grep -q 'Study Notes'
public_site_seo_get "/llms.txt" "text/plain" | grep -q 'Full content index'
public_site_seo_get "/llms-full.txt" "text/plain" | grep -q 'Total indexed chapters'

if [[ -n "${STUDENT_TOKEN:-}" ]]; then
  echo "Checking authenticated student routes through the public edge"
  echo "Checking native password-reset request and confirmation routes through the public edge"
  edge_json_status "/auth/reset-password/request" "200" '{"email":"cutover-no-user@example.invalid"}' \
    | python3 -c 'import json,sys; p=json.load(sys.stdin); assert p == {"message":"If an account exists, a reset email has been sent"}, p'
  # An invalid token proves the confirmation contract without changing a user's
  # password or consuming a real reset token.
  edge_json_status "/auth/reset-password/confirm" "400" \
    '{"token":"cutover-invalid-reset-token","password":"cutover-safe-password"}' \
    | python3 -c 'import json,sys; p=json.load(sys.stdin); assert p == {"detail":"Invalid or expired reset token"}, p'
  edge_auth_get "/users/profile" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert "id" in p and "subscription_tier" in p, p'
  edge_auth_get "/users/me" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert "id" in p and "subscription_tier" in p, p'
  edge_auth_get "/conversations" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert isinstance(p.get("conversations"), list), p'
  edge_auth_get "/users/credits" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), dict)'
  edge_auth_get "/subscription/status" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert "tier" in p and "monthly_limit" in p, p'
  edge_auth_get "/payments/history" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), (list, dict))'
  edge_auth_get "/content/library-bundle?slim=1" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert all(k in p for k in ("boards","classes","streams","subjects")), p'
  # A valid student token must never grant access to the staff catalogue.
  edge_auth_status "/staff/content/subjects" "403" "${STUDENT_TOKEN}" \
    | python3 -c 'import json,sys; assert json.load(sys.stdin)["detail"] == "Staff access required"'

  # These deliberately-invalid test fields fail before any payment state can
  # change. They prove both authenticated verification endpoints reject forged
  # callbacks on the Worker-native public route.
  edge_auth_json_status "/payments/verify" "400" '{"razorpay_order_id":"order_cutover_invalid","razorpay_payment_id":"pay_cutover_invalid","razorpay_signature":"invalid"}' \
    | python3 -c 'import json,sys; assert json.load(sys.stdin)["detail"] == "Invalid payment signature"'
  edge_auth_json_status "/payments/credit-topup/verify" "400" '{"razorpay_order_id":"order_cutover_invalid","razorpay_payment_id":"pay_cutover_invalid","razorpay_signature":"invalid"}' \
    | python3 -c 'import json,sys; assert json.load(sys.stdin)["detail"] == "Invalid payment signature"'

  echo "Checking authenticated student chat through the public edge"
  chat_output=$(mktemp)
  chat_headers=$(mktemp)
  TMP_FILES+=("$chat_output" "$chat_headers")
  chat_status=$(curl --silent --show-error --no-buffer --max-time 60 \
    --request POST --header 'Content-Type: application/json' \
    --header "Authorization: Bearer ${STUDENT_TOKEN}" \
    --data '{"message":"Reply with exactly: cutover chat ready.","lang":"en"}' \
    --dump-header "$chat_headers" --output "$chat_output" --write-out '%{http_code}' \
    "${EDGE_BASE}/api/v1/chat/stream")
  test "$chat_status" = "200" || { cat "$chat_output"; echo "Authenticated public-edge chat failed with ${chat_status}" >&2; exit 1; }
  grep -qi '^x-syrabit-route: worker-native' "$chat_headers" || { cat "$chat_headers"; echo "Authenticated chat used a fallback route" >&2; exit 1; }
  grep -q '"event":"source_card"' "$chat_output"
  grep -q '"event":"syrabit_done"' "$chat_output"
  grep -q '"content":' "$chat_output"
  ! grep -q '"error":true' "$chat_output"
else
  echo "STUDENT_TOKEN not set: authenticated student checks skipped."
fi

if [[ -n "${CUTOVER_PAYMENT_TOKEN:-}" ]]; then
  echo "Checking a disposable Razorpay test-mode order, verification, and webhook retry through the public edge"
  edge_auth_get "/payments/test-mode-status" "${CUTOVER_PAYMENT_TOKEN}" \
    | python3 -c '
import json,sys
p=json.load(sys.stdin)
assert p.get("configured") is True, p
assert p.get("test_mode") is True, (
    "Payment validation requires a Razorpay test-mode key (rzp_test_), got " + repr(p.get("key_id"))
)
'
  payment_order=$(edge_auth_json_status "/payments/create-order" "200" '{"plan":"pro"}' "${CUTOVER_PAYMENT_TOKEN}")
  payment_order_id=$(printf '%s' "$payment_order" | python3 -c '
import json,sys
p=json.load(sys.stdin)
key_id=p.get("key_id")
assert isinstance(key_id, str) and key_id.startswith("rzp_test_"), (
    "Razorpay key changed after the test-mode preflight: " + repr(key_id)
)
assert p.get("currency") == "INR" and p.get("amount") == 9900, p
assert isinstance(p.get("order_id"), str) and p["order_id"].startswith("order_"), p
print(p["order_id"])
')
  payment_id="pay_cutover_${payment_order_id#order_}_$(date +%s)"
  payment_signature=$(printf '%s|%s' "$payment_order_id" "$payment_id" | python3 -c '
import hashlib,hmac,os,sys
print(hmac.new(os.environ["RAZORPAY_KEY_SECRET"].encode(), sys.stdin.buffer.read(), hashlib.sha256).hexdigest())
')
  verify_payload=$(python3 - "$payment_order_id" "$payment_id" "$payment_signature" <<'PY'
import json,sys
print(json.dumps({
    "razorpay_order_id": sys.argv[1],
    "razorpay_payment_id": sys.argv[2],
    "razorpay_signature": sys.argv[3],
}))
PY
)
  edge_auth_json_status "/payments/verify" "200" "$verify_payload" "${CUTOVER_PAYMENT_TOKEN}" \
    | python3 -c '
import json,sys
p=json.load(sys.stdin)
assert p.get("status") == "success", p
assert isinstance(p.get("receipt_token"), str) and p["receipt_token"], p
'
  edge_auth_json_status "/payments/recover" "404" '{}' "${CUTOVER_PAYMENT_TOKEN}" \
    | python3 -c '
import json,sys
p=json.load(sys.stdin)
assert p.get("detail") == "No pending payment found", p
'
  edge_auth_get "/subscription/status" "${CUTOVER_PAYMENT_TOKEN}" \
    | python3 -c '
import json,sys
p=json.load(sys.stdin)
assert p.get("tier") == "pro" and p.get("status") == "active", p
'

  webhook_payload=$(python3 - "$payment_order_id" "$payment_id" <<'PY'
import json,sys
print(json.dumps({
    "event": "subscription.charged",
    "id": "evt_cutover_" + sys.argv[1],
    "payload": {
        "subscription": {"id": sys.argv[1]},
        "payment": {
            "id": sys.argv[2],
            "order_id": sys.argv[1],
            "amount": 9900,
        },
    },
}, separators=(",", ":")))
PY
)
  webhook_signature=$(printf '%s' "$webhook_payload" | python3 -c '
import hashlib,hmac,os,sys
print(hmac.new(os.environ["RAZORPAY_WEBHOOK_SECRET"].encode(), sys.stdin.buffer.read(), hashlib.sha256).hexdigest())
')
  edge_webhook_signed "$webhook_payload" "$webhook_signature" \
    | python3 -c '
import json,sys
p=json.load(sys.stdin)
assert p == {"status":"ok"}, p
'
  edge_webhook_signed "$webhook_payload" "$webhook_signature" \
    | python3 -c '
import json,sys
p=json.load(sys.stdin)
assert p == {"status":"ok","duplicate":True}, p
'
  edge_auth_get "/subscription/status" "${CUTOVER_PAYMENT_TOKEN}" \
    | python3 -c '
import json,sys
p=json.load(sys.stdin)
assert p.get("tier") == "pro" and p.get("status") == "active", p
'
  edge_auth_get "/payments/history?limit=50" "${CUTOVER_PAYMENT_TOKEN}" \
    | python3 - "$payment_order_id" <<'PY'
import json,sys
order_id=sys.argv[1]
p=json.load(sys.stdin)
rows=p.get("payments", [])
matching=[row for row in rows if row.get("razorpay_order_id") == order_id]
assert len(matching) == 1, matching
assert matching[0].get("status") == "captured" and matching[0].get("plan") == "pro", matching[0]
PY
  echo "Razorpay test-mode payment verification and exactly-once webhook handling passed."
else
  echo "CUTOVER_PAYMENT_TOKEN not set: authenticated payment check skipped."
fi

if [[ -n "${STAFF_TOKEN:-}" ]]; then
  echo "Checking Worker-native staff content and RAG status through the public edge"
  edge_anon_get_status "/staff/content/subjects" "401" \
    | python3 -c 'import json,sys; assert json.load(sys.stdin)["detail"] == "Authentication required"'
  edge_auth_get "/staff/content/boards" "${STAFF_TOKEN}" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), list)'
  edge_auth_get "/staff/content/classes" "${STAFF_TOKEN}" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), list)'
  staff_streams_headers=$(mktemp)
  staff_streams_output=$(mktemp)
  TMP_FILES+=("$staff_streams_headers" "$staff_streams_output")
  staff_streams_status=$(curl --silent --show-error --max-time 30 \
    --header "Authorization: Bearer ${STAFF_TOKEN}" \
    --dump-header "$staff_streams_headers" --output "$staff_streams_output" --write-out '%{http_code}' \
    "${EDGE_BASE}/api/v1/staff/content/streams")
  test "$staff_streams_status" = "200" || { cat "$staff_streams_output"; echo "Staff streams failed" >&2; exit 1; }
  grep -qi '^content-type: application/json' "$staff_streams_headers" || {
    cat "$staff_streams_headers"; echo "Staff streams must be JSON, never SSE" >&2; exit 1;
  }
  grep -qi '^x-syrabit-route: worker-native' "$staff_streams_headers" || {
    cat "$staff_streams_headers"; echo "Staff streams did not use the Worker-native route" >&2; exit 1;
  }
  cat "$staff_streams_output" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), list)'
  staff_subjects=$(edge_auth_get "/staff/content/subjects" "${STAFF_TOKEN}")
  printf '%s' "$staff_subjects" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), list)'
  staff_subject_id=$(printf '%s' "$staff_subjects" | python3 -c 'import json,sys; rows=json.load(sys.stdin); print(rows[0]["id"] if rows else "")')
  [[ -n "$staff_subject_id" ]] || { echo "No staff subject fixture available for RAG validation" >&2; exit 1; }
  staff_chapters=$(edge_auth_get "/staff/content/chapters/${staff_subject_id}" "${STAFF_TOKEN}")
  printf '%s' "$staff_chapters" | python3 -c '
import json,sys
rows=json.load(sys.stdin)
assert isinstance(rows, list)
assert rows, "No staff chapter fixture available for RAG validation"
assert {"has_rag_en","rag_updated_at","rag_indexed_at","notes_rag_stale"} <= set(rows[0]), rows[0]
'
  staff_chapter_id=$(printf '%s' "$staff_chapters" | python3 -c 'import json,sys; rows=json.load(sys.stdin); print(rows[0]["id"] if rows else "")')
  edge_auth_get "/staff/content/chapter/${staff_chapter_id}" "${STAFF_TOKEN}" \
    | python3 -c 'import json,sys; p=json.load(sys.stdin); assert {"rag_text_en","rag_sections_en","rag_indexed_at","notes_rag_stale"} <= set(p), p'
  # A nonexistent chapter proves a representative protected mutation reaches
  # the native route without changing production content.
  edge_auth_status "/staff/content/chapter/cutover-nonexistent/reindex" "404" "${STAFF_TOKEN}" "POST" \
    | python3 -c 'import json,sys; assert json.load(sys.stdin)["detail"] == "Chapter not found"'
else
  echo "STAFF_TOKEN not set: staff content check skipped."
fi

if [[ -n "${ADMIN_SESSION_TOKEN:-}" ]]; then
  echo "Checking Worker-native admin publishing, RAG, and translation reads through the public edge"
  edge_admin_get "/admin/verify" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), dict)'
  edge_admin_get "/admin/content/translation-progress" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert {"total","translated","missing","progress"} <= set(p), p'
  edge_admin_get "/admin/content/coverage" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), dict)'
  edge_admin_get "/admin/content/seed-notes/history?limit=1" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), list)'
  edge_admin_get "/admin/cron/bulk-reindex/status" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), dict)'
  # A nonexistent chapter can never create a publish job. Its Worker-native
  # 404 confirms the write route is mounted without changing production content.
  edge_admin_json_status "/admin/content/chapters/cutover-nonexistent/publish" "404" '{}' \
    | python3 -c 'import json,sys; assert json.load(sys.stdin)["detail"] == "Chapter not found"'

  # /admin/users is intentionally not yet ported to D1. This bounded,
  # read-only request proves the edge obtains Cloud Run OIDC before the API
  # Worker's explicit compatibility bridge forwards the disposable session.
  echo "Checking one retained admin route through the authenticated Cloud Run fallback"
  edge_admin_fallback_get "/admin/users?limit=1" \
    | python3 -c '
import json,sys
p=json.load(sys.stdin)
assert {"users","total","offset","limit","has_more"} <= set(p), p
assert p["limit"] == 1, p
'
else
  echo "ADMIN_SESSION_TOKEN not set: authenticated admin checks skipped."
fi

if [[ -n "${TRANSLATE_CRON_SECRET:-}" ]]; then
  echo "Checking native scheduled seed and translation status routes through the public edge"
  cron_headers=$(mktemp)
  cron_output=$(mktemp)
  TMP_FILES+=("$cron_headers" "$cron_output")
  for cron_path in /admin/cron/seed-notes/status /admin/cron/seed-assamese/status; do
    status=$(curl --silent --show-error --max-time 30 \
      --dump-header "$cron_headers" --output "$cron_output" --write-out '%{http_code}' \
      -H "Authorization: Bearer ${TRANSLATE_CRON_SECRET}" "${EDGE_BASE}/api/v1${cron_path}")
    test "$status" = "200" || { cat "$cron_output"; echo "Native scheduled status failed for ${cron_path}" >&2; exit 1; }
    grep -qi '^x-syrabit-route: worker-native' "$cron_headers" || {
      cat "$cron_headers"; echo "Scheduled status used a fallback route for ${cron_path}" >&2; exit 1;
    }
    cat "$cron_output" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), dict)'
  done
fi

if [[ -n "${EDGE_SHARED_SECRET:-}" ]]; then
  echo "Checking authenticated Workers AI generation through the public edge"
  generation_headers=$(mktemp)
  generation_output=$(mktemp)
  TMP_FILES+=("$generation_headers" "$generation_output")
  status=$(curl --silent --show-error --max-time 45 \
    --dump-header "$generation_headers" --output "$generation_output" --write-out '%{http_code}' \
    -X POST "${EDGE_BASE}/api/v1/internal/generate" \
    -H "Authorization: Bearer ${EDGE_SHARED_SECRET}" \
    -H "Content-Type: application/json" \
    --data '{"system_prompt":"Reply with exactly OK.","user_message":"Cutover health check","max_output_tokens":32}')
  test "$status" = "200" || { cat "$generation_output"; echo "Public-edge generation check failed" >&2; exit 1; }
  grep -qi '^x-syrabit-route: worker-native' "$generation_headers" || {
    cat "$generation_headers"; echo "Public-edge generation did not stay Worker-native" >&2; exit 1;
  }
  cat "$generation_output" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert isinstance(p.get("text"), str) and p["text"].strip(), p'
else
  echo "EDGE_SHARED_SECRET not set: authenticated generation check skipped."
fi

echo "Checking invalid Razorpay webhook handling through the public edge"
edge_webhook_invalid_signature | python3 -c 'import json,sys; assert json.load(sys.stdin)["error"] == "Invalid signature"'

echo "Cloudflare API cutover validation passed."