#!/usr/bin/env bash
# Validate the D1/Workers API directly during a staged traffic cutover.
#
# Required: API_WORKER_URL, e.g. https://syrabit-api-prod.<account>.workers.dev
# Required: PUBLIC_EDGE_URL, e.g. https://api.syrabit.ai
# Required: INDEXNOW_INTERNAL_SECRET for authenticated IndexNow validation
# Required for full validation: STUDENT_TOKEN, STAFF_TOKEN, EDGE_SHARED_SECRET
# Optional: TRANSLATE_CRON_SECRET validates the native scheduled seed status API.
# Set CUTOVER_STAGE=public only for a deliberately public-only preflight.
#
# This script is deliberately read-only. It proves native Worker routing for
# public and authenticated endpoints, and refuses a Cloud Run fallback where a
# D1-backed route is expected. It does not create payments or alter content.
set -euo pipefail

: "${API_WORKER_URL:?Set API_WORKER_URL to the deployed API Worker URL}"
: "${PUBLIC_EDGE_URL:?Set PUBLIC_EDGE_URL to the deployed edge API origin}"
: "${INDEXNOW_INTERNAL_SECRET:?Set INDEXNOW_INTERNAL_SECRET for IndexNow validation}"
BASE="${API_WORKER_URL%/}/api/v1"
EDGE_BASE="${PUBLIC_EDGE_URL%/}"
TMP_FILES=()
cleanup() { rm -f "${TMP_FILES[@]}"; }
trap cleanup EXIT

if [[ "${CUTOVER_STAGE:-full}" != "public" ]]; then
  : "${STUDENT_TOKEN:?Set STUDENT_TOKEN for authenticated student checks}"
  : "${STAFF_TOKEN:?Set STAFF_TOKEN for staff workflow checks}"
  : "${EDGE_SHARED_SECRET:?Set EDGE_SHARED_SECRET for authenticated generation}"
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

native_json_post() {
  local path="$1"
  local data="$2"
  local output headers status
  output=$(mktemp)
  headers=$(mktemp)
  TMP_FILES+=("$output" "$headers")
  status=$(curl --silent --show-error --max-time 30 \
    --request POST --header 'Content-Type: application/json' \
    --data "$data" --dump-header "$headers" --output "$output" --write-out '%{http_code}' \
    "${BASE}${path}")
  test "$status" = "200" || { cat "$output"; echo "Expected 200 for ${path}, got ${status}" >&2; exit 1; }
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

echo "Checking public D1-backed routes at ${BASE}"
HEALTH_URL="${API_WORKER_URL%/}/health"
HEALTH=$(curl --silent --show-error --max-time 30 "$HEALTH_URL")
printf '%s' "$HEALTH" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert p["runtime"] == "cloudflare-workers" and p["components"]["d1"] == "healthy", p'
native_get "/content/library-bundle?slim=1" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert all(k in p for k in ("boards","classes","streams","subjects")), p'
native_get "/content/question-papers" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), list)'
echo "Checking Worker-native operational and crawler routes"
native_json_post "/analytics/page-view" '{"path":"/cutover-check","visitor_id":"cutover","session_id":"cutover"}' | python3 -c 'import json,sys; assert json.load(sys.stdin) == {"status":"ok"}'
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

if [[ -n "${STUDENT_TOKEN:-}" ]]; then
  echo "Checking authenticated student history, quota, and subscription routes"
  native_auth_get "/users/profile" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert "id" in p and "subscription_tier" in p, p'
  native_auth_get "/conversations" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert isinstance(p.get("conversations"), list), p'
  native_auth_get "/users/credits" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), dict)'
  native_auth_get "/payments/history" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), (list, dict))'
else
  echo "STUDENT_TOKEN not set: authenticated student checks skipped."
fi

if [[ -n "${STAFF_TOKEN:-}" ]]; then
  echo "Checking Worker-native staff content list"
  native_auth_get "/staff/content/subjects" "${STAFF_TOKEN}" >/dev/null
else
  echo "STAFF_TOKEN not set: staff content check skipped."
fi

if [[ -n "${TRANSLATE_CRON_SECRET:-}" ]]; then
  echo "Checking native scheduled seed status route"
  status=$(curl --silent --show-error --max-time 30 \
    --dump-header /tmp/syrabit_seed_headers --output /tmp/syrabit_seed_response \
    --write-out '%{http_code}' -H "Authorization: Bearer ${TRANSLATE_CRON_SECRET}" \
    "${BASE}/admin/cron/seed-notes/status")
  test "$status" = "200" || { cat /tmp/syrabit_seed_response; echo "Native seed status failed" >&2; exit 1; }
  grep -qi '^x-syrabit-route: worker-native' /tmp/syrabit_seed_headers || {
    echo "Seed status did not stay Worker-native" >&2; exit 1;
  }
fi

if [[ -n "${EDGE_SHARED_SECRET:-}" ]]; then
  echo "Checking authenticated Workers AI generation"
  response=$(curl --silent --show-error --max-time 45 \
    -X POST "${BASE}/internal/generate" \
    -H "Authorization: Bearer ${EDGE_SHARED_SECRET}" \
    -H "Content-Type: application/json" \
    --data '{"system_prompt":"Reply with exactly OK.","user_message":"Cutover health check","max_output_tokens":32}')
  printf '%s' "$response" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert isinstance(p.get("text"), str) and p["text"].strip(), p'
else
  echo "EDGE_SHARED_SECRET not set: authenticated generation check skipped."
fi

echo "Cloudflare API cutover validation passed."