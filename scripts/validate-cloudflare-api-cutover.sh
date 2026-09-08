#!/usr/bin/env bash
# Validate the D1/Workers API directly during a staged traffic cutover.
#
# Required: API_WORKER_URL, e.g. https://syrabit-api-prod.<account>.workers.dev
# Required: PUBLIC_EDGE_URL, e.g. https://api.syrabit.ai
# Optional: PUBLIC_SITE_URL, defaults to https://syrabit.ai
# Optional: INDEXNOW_INTERNAL_SECRET enables authenticated IndexNow validation
# Required for full validation: STUDENT_TOKEN, STAFF_TOKEN,
# ADMIN_SESSION_TOKEN, EDGE_SHARED_SECRET, TRANSLATE_CRON_SECRET,
# CF_ACCESS_CLIENT_ID, and CF_ACCESS_CLIENT_SECRET.
# Set CUTOVER_RESET_ONLY=true only in the post-deploy reset job. It requires
# CUTOVER_RESET_EMAIL, CUTOVER_RESET_LINK, CUTOVER_RESET_PASSWORD, and the
# fresh CUTOVER_RESET_NONCE emitted by the preceding reset-request job.
# ADMIN_SESSION_TOKEN is the raw value of a disposable admin-session cookie,
# not a bearer token. This preserves the production admin-cookie contract
# through the public edge without exposing the token in logs.
# Set CUTOVER_STAGE=public only for a deliberately public-only preflight.
# Set CUTOVER_STAFF_AUTH_ONLY=true with CUTOVER_STAFF_EMAIL and
# CUTOVER_STAFF_PASSWORD plus its future CUTOVER_STAFF_LEASE_EXPIRES_AT Unix
# timestamp to validate a release-created disposable admin fixture.
#
# Commercial endpoints are checked as retired (HTTP 410); this validation never
# creates payment records or changes a user's entitlement.
set -euo pipefail

: "${PUBLIC_EDGE_URL:?Set PUBLIC_EDGE_URL to the deployed edge API origin}"
RESET_ONLY="${CUTOVER_RESET_ONLY:-false}"
CUTOVER_STAFF_AUTH_ONLY="${CUTOVER_STAFF_AUTH_ONLY:-false}"
if [[ "$RESET_ONLY" != "true" && "$RESET_ONLY" != "false" ]]; then
  echo "CUTOVER_RESET_ONLY must be true or false." >&2
  exit 1
fi
if [[ "$CUTOVER_STAFF_AUTH_ONLY" != "true" && "$CUTOVER_STAFF_AUTH_ONLY" != "false" ]]; then
  echo "CUTOVER_STAFF_AUTH_ONLY must be true or false." >&2
  exit 1
fi
if [[ "$RESET_ONLY" != "true" ]]; then
  : "${API_WORKER_URL:?Set API_WORKER_URL to the deployed API Worker URL}"
fi
BASE="${API_WORKER_URL:-}/api/v1"
EDGE_BASE="${PUBLIC_EDGE_URL%/}"
SITE_BASE="${PUBLIC_SITE_URL:-https://syrabit.ai}"
TMP_FILES=()
cleanup() { rm -f "${TMP_FILES[@]}"; }
trap cleanup EXIT

run_disposable_staff_auth_check() {
  local required_var cookie_jar login_body response headers status access_token refresh_token now
  for required_var in CUTOVER_STAFF_EMAIL CUTOVER_STAFF_PASSWORD CUTOVER_STAFF_LEASE_EXPIRES_AT CF_ACCESS_CLIENT_ID CF_ACCESS_CLIENT_SECRET; do
    : "${!required_var:?Set ${required_var} for disposable staff authentication validation}"
  done
  if [[ "${CUTOVER_STAFF_EMAIL,,}" != *release-staff-auth* ]]; then
    echo "CUTOVER_STAFF_EMAIL must identify a disposable release-staff-auth fixture." >&2
    exit 1
  fi
  [[ "$CUTOVER_STAFF_LEASE_EXPIRES_AT" =~ ^[0-9]+$ ]] || {
    echo "CUTOVER_STAFF_LEASE_EXPIRES_AT must be a Unix timestamp." >&2
    exit 1
  }
  now="$(date -u +%s)"
  (( CUTOVER_STAFF_LEASE_EXPIRES_AT > now )) || {
    echo "Disposable staff authentication lease is already expired." >&2
    exit 1
  }

  cookie_jar=$(mktemp)
  response=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$cookie_jar" "$response" "$headers")
  login_body=$(CUTOVER_STAFF_EMAIL="$CUTOVER_STAFF_EMAIL" CUTOVER_STAFF_PASSWORD="$CUTOVER_STAFF_PASSWORD" python3 -c '
import json, os
print(json.dumps({"email": os.environ["CUTOVER_STAFF_EMAIL"], "password": os.environ["CUTOVER_STAFF_PASSWORD"]}))
')

  status=$(curl --silent --show-error --max-time 30 \
    --request POST --header 'Content-Type: application/json' \
    --header "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" \
    --header "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}" \
    --data "$login_body" --cookie-jar "$cookie_jar" \
    --dump-header "$headers" --output "$response" --write-out '%{http_code}' \
    "${EDGE_BASE}/api/v1/admin/login")
  test "$status" = "200" || {
    echo "Disposable admin-cookie login failed with HTTP ${status}; response suppressed." >&2
    exit 1
  }
  grep -qi '^x-syrabit-route: worker-native' "$headers"
  grep -q $'\tsyrabit_admin_session\t' "$cookie_jar" || {
    echo "Disposable admin-cookie login did not set the session cookie." >&2
    exit 1
  }
  python3 - "$response" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload.get("status") == "ok" and payload.get("user_id")
PY

  for days in 7 30; do
    status=$(curl --silent --show-error --max-time 30 \
      --header "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" \
      --header "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}" \
      --cookie "$cookie_jar" --output "$response" --write-out '%{http_code}' \
      "${EDGE_BASE}/api/v1/admin/analytics/command-center?days=${days}")
    test "$status" = "200" || {
      echo "Admin-cookie ${days}-day command-center read failed with HTTP ${status}; response suppressed." >&2
      exit 1
    }
    python3 - "$response" "$days" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload.get("days") == int(sys.argv[2])
assert {"users", "content", "rag", "chat", "ads", "consent", "incidents", "audit"} <= set(payload)
PY
  done

  status=$(curl --silent --show-error --max-time 30 \
    --request POST --header 'Content-Type: application/json' \
    --header "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" \
    --header "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}" \
    --data "$login_body" --output "$response" --write-out '%{http_code}' \
    "${EDGE_BASE}/api/v1/auth/login")
  test "$status" = "200" || {
    echo "Disposable bearer login failed with HTTP ${status}; response suppressed." >&2
    exit 1
  }
  auth_tokens_output=$(python3 - "$response" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
access = payload.get("access_token")
refresh = payload.get("refresh_token")
assert isinstance(access, str) and access and isinstance(refresh, str) and refresh
print(access)
print(refresh)
PY
)
  readarray -t auth_tokens <<<"$auth_tokens_output"
  test "${#auth_tokens[@]}" = "2" || {
    echo "Bearer login response did not contain exactly two session tokens." >&2
    exit 1
  }
  access_token="${auth_tokens[0]}"
  refresh_token="${auth_tokens[1]}"

  status=$(curl --silent --show-error --max-time 30 \
    --header "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" \
    --header "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}" \
    --header "Authorization: Bearer ${access_token}" \
    --output "$response" --write-out '%{http_code}' \
    "${EDGE_BASE}/api/v1/admin/analytics/command-center?days=7")
  test "$status" = "200" || {
    echo "Bearer command-center read failed with HTTP ${status}; response suppressed." >&2
    exit 1
  }
  python3 - "$response" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload.get("days") == 7 and isinstance(payload.get("users"), dict)
PY

  logout_body=$(CUTOVER_REFRESH_TOKEN="$refresh_token" python3 -c '
import json, os
print(json.dumps({"refresh_token": os.environ["CUTOVER_REFRESH_TOKEN"]}))
')
  status=$(curl --silent --show-error --max-time 30 \
    --request POST --header 'Content-Type: application/json' \
    --header "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" \
    --header "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}" \
    --header "Authorization: Bearer ${access_token}" \
    --data "$logout_body" --output "$response" --write-out '%{http_code}' \
    "${EDGE_BASE}/api/v1/auth/logout")
  test "$status" = "200" || {
    echo "Bearer logout failed with HTTP ${status}; response suppressed." >&2
    exit 1
  }

  status=$(curl --silent --show-error --max-time 30 \
    --request POST --header "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" \
    --header "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}" \
    --cookie "$cookie_jar" --cookie-jar "$cookie_jar" \
    --output "$response" --write-out '%{http_code}' \
    "${EDGE_BASE}/api/v1/admin/logout")
  test "$status" = "200" || {
    echo "Admin-cookie logout failed with HTTP ${status}; response suppressed." >&2
    exit 1
  }
  status=$(curl --silent --show-error --max-time 30 \
    --header "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" \
    --header "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}" \
    --cookie "$cookie_jar" --output "$response" --write-out '%{http_code}' \
    "${EDGE_BASE}/api/v1/admin/analytics/command-center?days=7")
  test "$status" = "401" || {
    echo "Post-logout command-center request returned HTTP ${status}, expected 401; response suppressed." >&2
    exit 1
  }
  echo "Disposable staff cookie and bearer authentication lifecycle passed."
}

if [[ "$CUTOVER_STAFF_AUTH_ONLY" == "true" ]]; then
  run_disposable_staff_auth_check
  exit 0
fi

if [[ "$RESET_ONLY" != "true" && "${CUTOVER_STAGE:-full}" != "public" ]]; then
  : "${STUDENT_TOKEN:?Set STUDENT_TOKEN for authenticated student checks}"
  : "${STAFF_TOKEN:?Set STAFF_TOKEN for staff workflow checks}"
  : "${ADMIN_SESSION_TOKEN:?Set ADMIN_SESSION_TOKEN for admin workflow checks}"
  : "${EDGE_SHARED_SECRET:?Set EDGE_SHARED_SECRET for authenticated generation}"
  : "${TRANSLATE_CRON_SECRET:?Set TRANSLATE_CRON_SECRET for scheduled-operation checks}"
  : "${CF_ACCESS_CLIENT_ID:?Set CF_ACCESS_CLIENT_ID for public-edge admin checks}"
  : "${CF_ACCESS_CLIENT_SECRET:?Set CF_ACCESS_CLIENT_SECRET for public-edge admin checks}"
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
    -H "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" \
    -H "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}" \
    -H "Cookie: syrabit_admin_session=${ADMIN_SESSION_TOKEN}" "${EDGE_BASE}/api/v1${path}")
  test "$status" = "200" || { cat "$output"; echo "Expected public-edge admin 200 for ${path}, got ${status}" >&2; exit 1; }
  grep -qi '^x-syrabit-route: worker-native' "$headers" || {
    cat "$headers"; echo "Expected Worker-native public-edge admin route for ${path}" >&2; exit 1;
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
    --header "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" \
    --header "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}" \
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
if [[ -n "${EDGE_SHARED_SECRET:-}" ]]; then
  DEEP_HEALTH=$(curl --silent --show-error --fail --max-time 30 \
    -H "Authorization: Bearer ${EDGE_SHARED_SECRET}" "${HEALTH_URL}/deep")
  printf '%s' "$DEEP_HEALTH" | python3 -c '
import json,sys
p=json.load(sys.stdin)
required={
    "d1", "workers_ai", "vectorize", "r2",
    "content_kv", "rate_limit_kv", "cron_operations",
}
assert p["status"] == "healthy" and p["mutation_free"] is True, p
assert p["missing_bindings"] == [] and set(p["checks"]) == required, p
assert all(check["status"] == "healthy" for check in p["checks"].values()), p
'
fi
native_get "/content/library-bundle?slim=1" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert all(k in p for k in ("boards","classes","streams","subjects")), p'
native_get "/content/question-papers" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), list)'
echo "Checking Worker-native operational and crawler routes"
native_get "/analytics/top-routes" | python3 -c '
import json, sys
p = json.load(sys.stdin)
assert p.get("period") == "7d", p
routes = p.get("routes")
assert isinstance(routes, list) and len(routes) <= 20, p
assert all(isinstance(row.get("route"), str) and isinstance(row.get("count"), int) and row["count"] >= 0 for row in routes), p
'
native_get "/config/trustpilot" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert p is None or {"profileUrl","businessUnitId"} <= set(p), p'
native_get "/changelog" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), list)'
native_get "/seo/sitemap-index.xml" | grep -q '<sitemapindex'
native_get "/seo/sitemap-static.xml" | grep -q '<urlset'
native_get "/seo/feed.json" | python3 -c 'import json,sys; assert json.load(sys.stdin)["version"] == "https://jsonfeed.org/version/1.1"'
native_get "/seo/llms.txt" | grep -q 'Syrabit.ai'
# Preserve the unauthenticated contract, then prove the deployed Worker has both
# server-to-server secrets by using the safe empty-list path below.
native_status "/indexnow/submit" "403" "POST" | python3 -c 'import json,sys; assert json.load(sys.stdin)["detail"] == "Missing IndexNow secret"'
if [[ -n "${INDEXNOW_INTERNAL_SECRET:-}" ]]; then
  native_indexnow_empty_submit | python3 -c 'import json,sys; assert json.load(sys.stdin) == {"submitted":0,"failed":0,"detail":"No URLs provided"}'
else
  echo "Skipping authenticated IndexNow validation because INDEXNOW_INTERNAL_SECRET is not configured."
fi

# Publishing, content editing, RAG, and scheduled seed routes are native.
# No catch-all compatibility bridge may reintroduce Cloud Run.
grep -Eq "api\.route\\('/api/v1/admin', +adminContentRouter\\)" apps/api/src/routes/index.ts || {
  echo "Native admin content router is not mounted." >&2; exit 1;
}
if grep -ERq 'proxyToCloudRun|cloud-run-fallback|X-Cloud-Run-Token' apps/api/src apps/edge/src; then
  echo "Cloud Run compatibility code is present in the active Worker sources." >&2
  exit 1
fi

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

missing_path="/cutover-missing-$(date +%s)-${RANDOM}"
missing_output=$(mktemp)
missing_headers=$(mktemp)
TMP_FILES+=("$missing_output" "$missing_headers")
missing_status=$(curl --silent --show-error --max-time 30 \
  -A 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)' \
  -H 'Accept: text/html' --dump-header "$missing_headers" --output "$missing_output" \
  --write-out '%{http_code}' "${SITE_BASE}${missing_path}")
test "$missing_status" = "404" || {
  cat "$missing_output"; echo "Expected public Pages true 404, got ${missing_status}" >&2; exit 1;
}
grep -qi '^x-source: bot-render-not-found' "$missing_headers"
if grep -q 'id="root"\|id="app"' "$missing_output"; then
  echo "Missing crawler URL returned the SPA shell (soft 404)." >&2
  exit 1
fi

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
  edge_auth_get "/content/library-bundle?slim=1" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert all(k in p for k in ("boards","classes","streams","subjects")), p'
  # A valid student token must never grant access to the staff catalogue.
  edge_auth_status "/staff/content/subjects" "403" "${STUDENT_TOKEN}" \
    | python3 -c 'import json,sys; assert json.load(sys.stdin)["detail"] == "Staff access required"'

  # The ads-only release boundary retires every commercial route before any
  # legacy handler can read or mutate entitlement/payment records.
  edge_auth_status "/subscription/status" "410" "${STUDENT_TOKEN}"
  edge_auth_status "/payments/history" "410" "${STUDENT_TOKEN}"
  edge_auth_status "/payments/verify" "410" "${STUDENT_TOKEN}" "POST"

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
      -H "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" \
      -H "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}" \
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

echo "Checking retired webhook boundary through the public edge"
retired_webhook_status=$(curl --silent --show-error --max-time 30 \
  --request POST --header 'Content-Type: application/json' --data '{}' \
  --write-out '%{http_code}' --output /dev/null \
  "${EDGE_BASE}/api/webhooks/razorpay")
test "$retired_webhook_status" = "410" || {
  echo "Expected retired webhook endpoint to return 410, got ${retired_webhook_status}" >&2
  exit 1
}

echo "Cloudflare API cutover validation passed."