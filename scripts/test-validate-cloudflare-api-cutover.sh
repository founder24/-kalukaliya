#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
FAKE_BIN="$SANDBOX/bin"
mkdir -p "$FAKE_BIN"

PASSWORD='generated-password-DO-NOT-LEAK'
HASH='$2b$12$generated-hash-DO-NOT-LEAK'
COOKIE='generated-cookie-DO-NOT-LEAK'
ACCESS='generated-access-token-DO-NOT-LEAK'
REFRESH='generated-refresh-token-DO-NOT-LEAK'
ACCESS_ID='generated-access-client-id-DO-NOT-LEAK'
ACCESS_SECRET='generated-access-client-secret-DO-NOT-LEAK'

cat >"$FAKE_BIN/openssl" <<EOF
#!/usr/bin/env bash
if [[ "\$*" == *"-hex 16"* ]]; then printf '%s\n' '0123456789abcdef0123456789abcdef'; else printf '%s\n' '$PASSWORD'; fi
EOF

cat >"$FAKE_BIN/pnpm" <<EOF
#!/usr/bin/env bash
set -euo pipefail
if [[ "\$*" == *"exec node -e"* ]]; then
  printf '%s' '$HASH'
  exit 0
fi
printf '%s\n' "\$*" >>"\${D1_LOG:?}"
if [[ "\$*" == *"DELETE FROM release_staff_auth_leases WHERE fixture_id"* ]]; then
  printf '%s\n' cleanup >>"\${CLEANUP_LOG:?}"
  [[ "\${SCENARIO:-}" != "cleanup_d1_failure" ]] || exit 1
fi
if [[ "\${SCENARIO:-}" == "reap_d1_failure" && "\$*" == *"expires_at <= unixepoch"* ]]; then
  exit 1
fi
if [[ "\${SCENARIO:-}" == "create_d1_failure" && "\$*" == *"INSERT INTO release_staff_auth_leases"* ]]; then
  exit 1
fi
exit 0
EOF

cat >"$FAKE_BIN/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
output='' headers='' cookie_jar='' url='' data=''
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  case "${args[$i]}" in
    --output) output="${args[++i]}" ;;
    --dump-header) headers="${args[++i]}" ;;
    --cookie-jar) cookie_jar="${args[++i]}" ;;
    --data) data="${args[++i]}" ;;
    http*) url="${args[$i]}" ;;
  esac
done
step=''
case "$url" in
  */admin/login) step=cookie_login ;;
  *command-center?days=30) step=cookie_read_30 ;;
  */auth/login) step=bearer_login ;;
  */auth/logout) step=bearer_logout ;;
  */admin/logout) step=cookie_logout ;;
  *command-center?days=7)
    if [[ " $* " == *" Authorization: Bearer "* ]]; then step=bearer_read
    elif [[ -f "${COOKIE_LOGGED_OUT_FILE:?}" ]]; then step=post_logout
    else step=cookie_read_7
    fi ;;
  *) echo "unexpected fake curl URL" >&2; exit 97 ;;
esac
printf '%s\n' "$step" >>"${CURL_LOG:?}"
if [[ " $* " != *" CF-Access-Client-Id: ${ACCESS_ID_VALUE:?}"* \
   || " $* " != *" CF-Access-Client-Secret: ${ACCESS_SECRET_VALUE:?}"* ]]; then
  echo "Cloudflare Access service headers are required on disposable staff API requests" >&2
  exit 96
fi
status=200
body='{}'
case "$step" in
  cookie_login)
    body='{"status":"ok","user_id":"fixture-user"}'
    [[ -n "$headers" ]] && printf 'x-syrabit-route: worker-native\r\n' >"$headers"
    [[ -n "$cookie_jar" ]] && printf '#HttpOnly_api.invalid\tTRUE\t/\tTRUE\t0\tsyrabit_admin_session\t%s\n' "$COOKIE_VALUE" >"$cookie_jar"
    ;;
  cookie_read_7) body='{"days":7,"users":{},"content":{},"rag":{},"chat":{},"ads":{},"consent":{},"incidents":{},"audit":{}}' ;;
  cookie_read_30) body='{"days":30,"users":{},"content":{},"rag":{},"chat":{},"ads":{},"consent":{},"incidents":{},"audit":{}}' ;;
  bearer_login) body='{"access_token":"'"$ACCESS_VALUE"'","refresh_token":"'"$REFRESH_VALUE"'"}' ;;
  bearer_read) body='{"days":7,"users":{}}' ;;
  bearer_logout) body='{"status":"ok"}' ;;
  cookie_logout) body='{"status":"ok"}'; : >"$COOKIE_LOGGED_OUT_FILE" ;;
  post_logout) status=401; body='{"detail":"Authentication required"}' ;;
esac
if [[ "${SCENARIO:-success}" == "$step" ]]; then
  case "$step" in
    cookie_login)
      body='{"malformed":true,"password":"'"$PASSWORD_VALUE"'","hash":"'"$HASH_VALUE"'","cookie":"'"$COOKIE_VALUE"'","reflected_access_token":"'"$ACCESS_VALUE"'","reflected_refresh_token":"'"$REFRESH_VALUE"'","access_id":"'"$ACCESS_ID_VALUE"'","access_secret":"'"$ACCESS_SECRET_VALUE"'"}'
      ;;
    bearer_login)
      body='{"access_token":null,"refresh_token":null,"password":"'"$PASSWORD_VALUE"'","hash":"'"$HASH_VALUE"'","cookie":"'"$COOKIE_VALUE"'","reflected_access_token":"'"$ACCESS_VALUE"'","reflected_refresh_token":"'"$REFRESH_VALUE"'","access_id":"'"$ACCESS_ID_VALUE"'","access_secret":"'"$ACCESS_SECRET_VALUE"'"}'
      ;;
    *) status=503; body='{"detail":"injected failure"}' ;;
  esac
fi
[[ -n "$output" && "$output" != /dev/null ]] && printf '%s' "$body" >"$output"
printf '%s' "$status"
EOF

cat >"$FAKE_BIN/node" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' browser >>"${BROWSER_LOG:?}"
[[ "${SCENARIO:-}" != "browser_failure" ]]
EOF

chmod +x "$FAKE_BIN/"*

assert_no_secrets() {
  local capture="$1" secret
  for secret in "$PASSWORD" "$HASH" "$COOKIE" "$ACCESS" "$REFRESH" "$ACCESS_ID" "$ACCESS_SECRET"; do
    if grep -Fq -- "$secret" "$capture"; then
      echo "secret leaked in captured output for ${SCENARIO}: ${secret}" >&2
      return 1
    fi
  done
}

run_case() {
  local scenario="$1" expected="$2"
  local case_dir="$SANDBOX/$scenario"
  mkdir -p "$case_dir"
  export SCENARIO="$scenario"
  export D1_LOG="$case_dir/d1.log"
  export CLEANUP_LOG="$case_dir/cleanup.log"
  export CURL_LOG="$case_dir/curl.log"
  export BROWSER_LOG="$case_dir/browser.log"
  export COOKIE_LOGGED_OUT_FILE="$case_dir/logged-out"
  export COOKIE_VALUE="$COOKIE" ACCESS_VALUE="$ACCESS" REFRESH_VALUE="$REFRESH"
  export PASSWORD_VALUE="$PASSWORD" HASH_VALUE="$HASH"
  export ACCESS_ID_VALUE="$ACCESS_ID" ACCESS_SECRET_VALUE="$ACCESS_SECRET"
  local capture="$case_dir/capture.log" status=0
  (
    cd "$ROOT"
    PATH="$FAKE_BIN:$PATH" \
      PUBLIC_EDGE_URL=https://api.invalid \
      API_WORKER_URL=https://worker.invalid \
      CF_ACCESS_CLIENT_ID="$ACCESS_ID" \
      CF_ACCESS_CLIENT_SECRET="$ACCESS_SECRET" \
      bash scripts/run-disposable-staff-auth-cutover.sh
  ) >"$capture" 2>&1 || status=$?
  if [[ "$expected" == success ]]; then
    [[ "$status" -eq 0 ]] || { cat "$capture"; echo "$scenario unexpectedly failed" >&2; return 1; }
  else
    [[ "$status" -ne 0 ]] || { echo "$scenario unexpectedly passed" >&2; return 1; }
  fi
  grep -qx cleanup "$CLEANUP_LOG" || { cat "$capture"; echo "$scenario did not attempt cleanup" >&2; return 1; }
  assert_no_secrets "$capture"
}

run_portal_case() {
  local scenario="$1" expected="$2"
  local case_dir="$SANDBOX/portal-$scenario"
  mkdir -p "$case_dir"
  export SCENARIO="$scenario"
  export D1_LOG="$case_dir/d1.log"
  export CLEANUP_LOG="$case_dir/cleanup.log"
  export CURL_LOG="$case_dir/curl.log"
  export BROWSER_LOG="$case_dir/browser.log"
  export PASSWORD_VALUE="$PASSWORD" HASH_VALUE="$HASH"
  export ACCESS_ID_VALUE="$ACCESS_ID" ACCESS_SECRET_VALUE="$ACCESS_SECRET"
  local capture="$case_dir/capture.log" status=0
  (
    cd "$ROOT"
    PATH="$FAKE_BIN:$PATH" \
      PUBLIC_SITE_URL=https://site.invalid \
      PUBLIC_EDGE_URL=https://api.invalid \
      CF_ACCESS_CLIENT_ID="$ACCESS_ID" \
      CF_ACCESS_CLIENT_SECRET="$ACCESS_SECRET" \
      bash scripts/run-disposable-staff-portal-check.sh
  ) >"$capture" 2>&1 || status=$?
  if [[ "$expected" == success ]]; then
    [[ "$status" -eq 0 ]] || { cat "$capture"; echo "portal $scenario unexpectedly failed" >&2; return 1; }
  else
    [[ "$status" -ne 0 ]] || { echo "portal $scenario unexpectedly passed" >&2; return 1; }
  fi
  grep -qx cleanup "$CLEANUP_LOG" || {
    cat "$capture"
    echo "portal $scenario did not attempt cleanup" >&2
    return 1
  }
  assert_no_secrets "$capture"
}

run_case success success
expected_steps=$'cookie_login\ncookie_read_7\ncookie_read_30\nbearer_login\nbearer_read\nbearer_logout\ncookie_logout\npost_logout'
[[ "$(cat "$SANDBOX/success/curl.log")" == "$expected_steps" ]] || {
  echo "success case did not cover the complete cookie and bearer lifecycle" >&2
  exit 1
}

for scenario in \
  reap_d1_failure create_d1_failure cleanup_d1_failure \
  cookie_login cookie_read_7 cookie_read_30 bearer_login \
  bearer_read bearer_logout cookie_logout post_logout
do
  run_case "$scenario" failure
done

run_portal_case success success
grep -qx browser "$SANDBOX/portal-success/browser.log"
for scenario in create_d1_failure browser_failure cleanup_d1_failure; do
  run_portal_case "$scenario" failure
done

echo "Disposable staff cutover fault-injection tests passed."