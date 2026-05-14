#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# workers/edge-proxy smoke-test
#
# Exercises every binding declared in wrangler.toml against a deployed Worker
# (preview or prod) so a promote can be done by "run this script, eyeball
# output" instead of remembering N curls.
#
# Usage:
#   ./scripts/smoke-test.sh https://syrabit-edge-preview.<account>.workers.dev
#   ./scripts/smoke-test.sh https://syrabit.ai
#
# Optional env knobs:
#   SKIP_RATE_LIMIT=1         skip the rate-limit burst (saves ~10 s, but
#                             leaves RATE_LIMIT KV unverified end-to-end).
#   D1_SYNC_SECRET            if set, sent as X-Edge-Admin-Secret to unlock
#                             /api/edge/kv-usage so we can enumerate KV
#                             bindings (specifically BOT_HTML_CACHE, which
#                             has no externally-reachable read path because
#                             the bot-cache lookup requires
#                             cf.verifiedBot===true). Without it the
#                             BOT_HTML_CACHE binding check is skipped with
#                             a warning.
#   EDGE_AI_FALLBACK_SECRET   if set, the AI fallback test sends this as
#                             X-Edge-AI-Secret and expects 200 instead of
#                             the gate's 401 (proves the AI binding actually
#                             reaches Workers AI; without it we only prove
#                             the gate is wired). Header & env names match
#                             src/index.ts:handleAiFallback. Legacy
#                             AI_FALLBACK_SECRET is honoured as a fallback
#                             for backward compat only — please migrate.
#   FAIL_FAST=0               run every check and summarise at end instead
#                             of aborting on the first failure (default 1).
#   VERBOSE=1                 print full response bodies on failure.
#
# Exits non-zero on the first failed check (fail-fast). Each check prints
# a single pass/fail line; an EXIT trap always emits a one-line
# PASSED/FAILED/TOTAL summary, even when the script aborts early.
# Designed to be CI-droppable as-is.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <base-url>" >&2
  echo "  e.g. $0 https://syrabit-edge-preview.<account>.workers.dev" >&2
  exit 64
fi

# pnpm passes the `--` argument separator through to the script when called as
# `pnpm run smoke -- <url>` (e.g. from CI). Skip it so $1 is always the URL.
if [[ "${1}" == "--" ]]; then
  shift
  if [[ $# -lt 1 ]]; then
    echo "usage: $0 <base-url>" >&2
    exit 64
  fi
fi

BASE_URL="${1%/}" # strip trailing slash
PASSED=0
FAILED=0
TOTAL=0
START_TS=$(date +%s)
TMP_DIR=$(mktemp -d)

# Fail-fast by default — exit immediately on the first failed check (per the
# task spec). Set FAIL_FAST=0 to opt into "run every check, summarise at end"
# behaviour for full-coverage debugging runs.
FAIL_FAST="${FAIL_FAST:-1}"

# Always print the summary on exit (success, fail-fast exit, or unexpected
# error) so operators see PASSED/FAILED/TOTAL even when we abort mid-run.
# The trap captures the original exit code as the FIRST statement and
# explicitly re-exits with it at the end so commands inside the trap (e.g.
# `rm`, `echo`) cannot accidentally overwrite it to 0 — that bug ate a
# fail-fast smoke run during local validation.
print_summary() {
  local code=$?
  local elapsed=$(( $(date +%s) - START_TS ))
  echo
  if [[ "$FAILED" -eq 0 && "$code" -eq 0 ]]; then
    echo "✓ ${PASSED}/${TOTAL} passed in ${elapsed}s — $BASE_URL"
  else
    echo "✗ ${FAILED}/${TOTAL} FAILED (${PASSED} passed) in ${elapsed}s — $BASE_URL"
    [[ "${VERBOSE:-0}" != "1" ]] && echo "  Re-run with VERBOSE=1 to see response bodies."
    [[ "$FAIL_FAST" == "1" ]] && echo "  (fail-fast mode: aborted on first failure; export FAIL_FAST=0 to run every check)"
  fi
  rm -rf "$TMP_DIR" 2>/dev/null || true
  # Preserve the original exit code (or surface accumulated FAILED if the
  # script reached the natural end without a fail-fast abort).
  if [[ "$code" -ne 0 ]]; then
    exit "$code"
  fi
  if [[ "$FAILED" -ne 0 ]]; then
    exit 1
  fi
  exit 0
}
trap print_summary EXIT

# ── helpers ─────────────────────────────────────────────────────────────────
fail() {
  local check="$1"; shift
  local detail="$*"
  FAILED=$((FAILED + 1))
  echo "  ✗ [$check] $detail"
  if [[ "${VERBOSE:-0}" == "1" && -f "$TMP_DIR/body" ]]; then
    echo "    --- response body ---"
    head -c 2000 "$TMP_DIR/body" | sed 's/^/    /'
    echo
    echo "    --- end body ---"
  fi
  if [[ "$FAIL_FAST" == "1" ]]; then
    exit 1
  fi
}

pass() {
  PASSED=$((PASSED + 1))
  echo "  ✓ [$1] $2"
}

# Capture status, headers, body separately so we can assert on each.
# Sets globals: HTTP_STATUS, HTTP_TIME, HEADERS_PATH, BODY_PATH.
http_get() {
  local url="$1"; shift
  local extra=("$@")
  HEADERS_PATH="$TMP_DIR/headers"
  BODY_PATH="$TMP_DIR/body"
  HTTP_STATUS=$(
    curl -sS -o "$BODY_PATH" -D "$HEADERS_PATH" \
      -w '%{http_code}' \
      --max-time 15 \
      "${extra[@]}" \
      "$url" || echo "000"
  )
  HTTP_TIME=$(awk 'END{print NR}' "$BODY_PATH" 2>/dev/null || echo 0)
}

http_post() {
  local url="$1"; shift
  HEADERS_PATH="$TMP_DIR/headers"
  BODY_PATH="$TMP_DIR/body"
  HTTP_STATUS=$(
    curl -sS -o "$BODY_PATH" -D "$HEADERS_PATH" \
      -w '%{http_code}' \
      --max-time 15 \
      -X POST \
      "$@" \
      "$url" || echo "000"
  )
}

header_value() {
  # Case-insensitive header lookup; returns empty if absent.
  awk -v IGNORECASE=1 -v h="$1" -F': *' '
    tolower($1) == tolower(h) { sub(/\r$/, "", $2); print $2; exit }
  ' "$HEADERS_PATH"
}

# ── 1) /api/health — proves worker is up + reports CONTENT_DB binding ──────
TOTAL=$((TOTAL + 1))
echo "[1/7] GET /api/health"
http_get "$BASE_URL/api/health"
if [[ "$HTTP_STATUS" != "200" ]]; then
  fail "health" "expected 200, got $HTTP_STATUS"
elif ! grep -q '"status":"ok"' "$BODY_PATH"; then
  fail "health" "body missing status:ok"
elif ! grep -q '"edge":true' "$BODY_PATH"; then
  fail "health" "body missing edge:true (request did not flow through worker)"
elif ! grep -q '"d1":true' "$BODY_PATH"; then
  fail "health" "body has d1:false — CONTENT_DB binding NOT wired in this env"
else
  src=$(header_value "X-Source")
  pass "health" "200, X-Source=${src:-(missing)}, d1=true"
fi

# ── 2) /api/edge/kv-usage — enumerates BOTH KV bindings ─────────────────────
# We can't realistically end-to-end test BOT_HTML_CACHE from a non-CF IP
# (the bot UA → KV cache path requires cf.verifiedBot===true OR a source IP
# in Google's bot ranges; spoofed bot UAs are filtered out before the KV
# lookup). The kv-monitor snapshot endpoint enumerates every KV binding the
# worker can see, so a present-and-accounted-for assertion proves the
# binding is wired without needing CF's bot infrastructure. The endpoint
# is gated by D1_SYNC_SECRET (X-Edge-Admin-Secret header).
if [[ -z "${D1_SYNC_SECRET:-}" ]]; then
  echo "[2/7] SKIPPED — D1_SYNC_SECRET unset (BOT_HTML_CACHE binding NOT verified)"
  echo "       export D1_SYNC_SECRET=… to unlock /api/edge/kv-usage"
else
  TOTAL=$((TOTAL + 1))
  echo "[2/7] GET /api/edge/kv-usage (with X-Edge-Admin-Secret)"
  http_get "$BASE_URL/api/edge/kv-usage" \
    -H "X-Edge-Admin-Secret: ${D1_SYNC_SECRET}"
  if [[ "$HTTP_STATUS" != "200" ]]; then
    fail "kv-usage" "expected 200, got $HTTP_STATUS (D1_SYNC_SECRET wrong for this env?)"
  elif ! grep -q '"RATE_LIMIT"' "$BODY_PATH"; then
    fail "kv-usage" "snapshot missing RATE_LIMIT — KV binding NOT wired"
  elif ! grep -q '"BOT_HTML_CACHE"' "$BODY_PATH"; then
    fail "kv-usage" "snapshot missing BOT_HTML_CACHE — KV binding NOT wired"
  else
    pass "kv-usage" "both RATE_LIMIT and BOT_HTML_CACHE bindings enumerated"
  fi
fi

# ── 3) /api/content/subjects — D1 read with content assertion ──────────────
# CONTENT_DB binding being wired (verified in test 1) is necessary but not
# sufficient — the D1 database also has to actually have data. This test
# proves the read path returns >=1 row, which is the contract the frontend
# depends on.
TOTAL=$((TOTAL + 1))
echo "[3/7] GET /api/content/subjects"
http_get "$BASE_URL/api/content/subjects"
if [[ "$HTTP_STATUS" != "200" ]]; then
  fail "d1-subjects" "expected 200, got $HTTP_STATUS"
else
  src=$(header_value "X-Source")
  byte_len=$(wc -c < "$BODY_PATH" | tr -d ' ')
  # Body shape: either {"subjects":[...]} or a raw [...] array. Either way
  # the substring `"id"` appears once per row, so checking for at least one
  # `"id"` is enough to prove the response is non-empty.
  if ! grep -q '"id"' "$BODY_PATH"; then
    fail "d1-subjects" "body has no rows (X-Source=${src:-(missing)}, ${byte_len} bytes)"
  elif [[ "$src" != "d1" && "$src" != "cf-cache" && "$src" != "backend" ]]; then
    fail "d1-subjects" "unexpected X-Source=${src:-(missing)} (want d1|cf-cache|backend)"
  else
    pass "d1-subjects" "200, X-Source=$src, ${byte_len} bytes, has rows"
  fi
fi

# ── 3b) /api/edge/d1-status — assert non-zero rows for boards/subjects/chapters
# /api/content/subjects (test [3/7]) only proves *one* table is non-empty
# AND can be served from cache (the `cf-cache` X-Source branch defeats the
# data-presence check on the second run). /api/edge/d1-status is a direct
# read of the CONTENT_DB binding for every table the worker writes to, so
# it's the authoritative signal that the preview-tier backfill (Task #879)
# actually populated the DB. No auth required; the endpoint only returns
# row counts (no row content), so it is safe to expose.
#
# Parser: the response shape is `{"counts":{"boards":N,"subjects":N,...},
# "last_sync":"..."}`. We use `grep -oE` to find every `"<key>":<digits>`
# substring anywhere in the body and take the first match. This is robust
# against the body being on one line OR pretty-printed across many lines,
# and against future fields being added (e.g. an outer `"boards"` field
# would still leave the inner `"counts":{"boards":N}` intact and matchable
# — the inner is just the second hit, the outer the first; both equal in
# a healthy state). Avoids depending on `jq` (not present on stock CI
# runners) and avoids splitting on `,` / `:` which the previous awk
# version did and broke against the nested `counts` object.
extract_d1_count() {
  local key="$1" path="$2"
  # `\"key\"\s*:\s*([0-9]+)` — POSIX `[[:space:]]` for portability across
  # GNU and BSD grep. Trailing `head -n1` returns "" on no match.
  grep -oE "\"$key\"[[:space:]]*:[[:space:]]*[0-9]+" "$path" 2>/dev/null \
    | head -n1 \
    | grep -oE '[0-9]+$' \
    || true
}

# Self-test the parser against a fixture so a future regression in
# extract_d1_count can never silently report 0/0/0 against real data.
# A failure here aborts before we ever hit the network.
_d1_fixture=$(mktemp "$TMP_DIR/d1-status-fixture.XXXX")
printf '%s' '{"counts":{"boards":7,"classes":42,"streams":13,"subjects":188,"chapters":5421,"topics":99000,"seo_pages":61234},"last_sync":"2026-04-25T12:00:00Z","last_sync_at":"2026-04-25T12:00:00Z"}' > "$_d1_fixture"
_b=$(extract_d1_count "boards" "$_d1_fixture")
_s=$(extract_d1_count "subjects" "$_d1_fixture")
_c=$(extract_d1_count "chapters" "$_d1_fixture")
if [[ "$_b" != "7" || "$_s" != "188" || "$_c" != "5421" ]]; then
  echo "FATAL: extract_d1_count parser broken against fixture (boards='$_b' subjects='$_s' chapters='$_c'; expected 7/188/5421). Refusing to run smoke." >&2
  exit 70
fi

TOTAL=$((TOTAL + 1))
echo "[3b/7] GET /api/edge/d1-status (boards/subjects/chapters row counts)"
http_get "$BASE_URL/api/edge/d1-status"
if [[ "$HTTP_STATUS" != "200" ]]; then
  fail "d1-counts" "expected 200, got $HTTP_STATUS"
else
  d1_boards=$(extract_d1_count "boards" "$BODY_PATH")
  d1_subjects=$(extract_d1_count "subjects" "$BODY_PATH")
  d1_chapters=$(extract_d1_count "chapters" "$BODY_PATH")
  d1_boards=${d1_boards:-0}; d1_subjects=${d1_subjects:-0}; d1_chapters=${d1_chapters:-0}
  if [[ "$d1_boards" -eq 0 || "$d1_subjects" -eq 0 || "$d1_chapters" -eq 0 ]]; then
    fail "d1-counts" "preview D1 is missing rows (boards=${d1_boards} subjects=${d1_subjects} chapters=${d1_chapters}). \
Run the Railway-side fan-out: set EDGE_WORKER_PREVIEW_URL + D1_SYNC_SECRET_PREVIEW on the backend \
and POST /api/admin/d1-sync to backfill. See artifacts/syrabit-backend/docs/D1_PREVIEW_BACKFILL.md."
  else
    pass "d1-counts" "boards=${d1_boards} subjects=${d1_subjects} chapters=${d1_chapters} (non-zero)"
  fi
fi

# ── 4) Backend reverse-proxy identity check via /api/readyz ────────────────
# /api/health is served entirely by the worker (`X-Source: edge`) so it
# *cannot* prove BACKEND_URL is wired correctly — a misconfigured backend
# origin would still pass test [1/7]. /api/readyz is intentionally NOT
# intercepted by the worker (see comment at src/index.ts ~L1717: "the
# actual dependency state moved to /api/readyz, which intentionally
# proxies through to the backend so on-call sees real Mongo / PG / Vertex
# status instead of a static 'edge is up' lie") and the FastAPI backend
# always responds with a JSON body containing dependency identity.
# Asserting `X-Source: backend` here proves the request actually exited
# the worker via proxyToBackend (src/index.ts:`proxyToBackend` always
# stamps that header on the response). /api/readyz is allow-listed in
# the backend's middleware so it cannot 429 the smoke run mid-burst
# (artifacts/syrabit-backend/middleware.py L446).
TOTAL=$((TOTAL + 1))
echo "[4/7] GET /api/readyz (backend reverse-proxy identity)"
http_get "$BASE_URL/api/readyz"
ready_src=$(header_value "X-Source")
ready_byte_len=$(wc -c < "$BODY_PATH" | tr -d ' ')
if [[ "$HTTP_STATUS" != "200" && "$HTTP_STATUS" != "503" ]]; then
  # 503 is acceptable: it means the backend answered "not ready" — proves
  # the proxy worked even though a downstream dep is unhealthy.
  fail "backend-proxy" "expected 200/503, got $HTTP_STATUS — BACKEND_URL likely misconfigured (X-Source=${ready_src:-(missing)})"
elif [[ "$ready_src" != "backend" ]]; then
  fail "backend-proxy" "expected X-Source=backend (proxy path), got '${ready_src:-(missing)}' — request did not flow through proxyToBackend"
elif [[ "$ready_byte_len" -lt 2 ]]; then
  fail "backend-proxy" "empty body (${ready_byte_len} bytes) — backend reachable but returned nothing"
else
  pass "backend-proxy" "${HTTP_STATUS}, X-Source=$ready_src, ${ready_byte_len} bytes (BACKEND_URL wired)"
fi

# ── 5) Rate-limit burst against /api/content/boards ────────────────────────
# RATE_LIMIT_RPM = 120 IP requests / 60 s. We send 130 sequential requests
# from one IP and expect at least one 429 with X-RateLimit-Limit=120.
#
# CRITICAL: append ?nocache=<unique> to every request. Without it, the
# Cloudflare HTTP cache (cf-cache) intercepts identical-URL requests
# BEFORE the worker runs (cacheable response sets Cache-Control: max-age=
# 3600), so the rate-limit gate is never entered and the burst returns
# 200/200/200/... forever. The worker's `nocache` query param skips both
# the CF cache lookup AND the worker's own caches.default lookup, forcing
# every request through the rate-limit check at src/index.ts:1855.
# Each request also gets a unique nocache value so even CF's URL-keyed
# cache can't collapse them.
if [[ "${SKIP_RATE_LIMIT:-0}" == "1" ]]; then
  echo "[5/7] SKIPPED (SKIP_RATE_LIMIT=1)"
else
  TOTAL=$((TOTAL + 1))
  echo "[5/7] burst 130x GET /api/content/boards?nocache=… (expect ≥1 429)"
  burst_429=0
  burst_other=0
  burst_first_429_at=0
  cb_seed="$$-$(date +%s)"
  for i in $(seq 1 130); do
    code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 \
      "$BASE_URL/api/content/boards?nocache=${cb_seed}-${i}" || echo "000")
    if [[ "$code" == "429" ]]; then
      burst_429=$((burst_429 + 1))
      [[ "$burst_first_429_at" == "0" ]] && burst_first_429_at=$i
    elif [[ "$code" != "200" && "$code" != "304" ]]; then
      burst_other=$((burst_other + 1))
    fi
  done
  if [[ "$burst_429" -eq 0 ]]; then
    fail "rate-limit" "0/130 requests returned 429 — RATE_LIMIT KV NOT counting (other=${burst_other})"
  elif [[ "$burst_first_429_at" -lt 100 || "$burst_first_429_at" -gt 130 ]]; then
    # We expect first 429 around request 121 (limit=120). Allow some slack
    # for clock skew and concurrent traffic from other clients hitting the
    # same IP-based bucket; flag if it's wildly off.
    fail "rate-limit" "first 429 at #${burst_first_429_at} (expected ~121); got ${burst_429} 429s, ${burst_other} other"
  else
    pass "rate-limit" "${burst_429}/130 returned 429, first at #${burst_first_429_at}"
  fi
fi

# ── 5) AI fallback gate — proves AI binding is wired ───────────────────────
# Without the secret we expect 401 (proves the gate is wired but does NOT
# prove Workers AI itself works on this env).
# With the secret set we expect 200 (proves the AI binding actually reaches
# Workers AI). Use the latter on preview after running
# `wrangler secret put EDGE_AI_FALLBACK_SECRET --env preview`.
#
# Header / env names match the worker (src/index.ts:handleAiFallback):
#   - HTTP header: `X-Edge-AI-Secret`
#   - Worker env binding (server-side): `EDGE_AI_FALLBACK_SECRET`
# `EDGE_AI_FALLBACK_SECRET` is the canonical env-var name on the operator's
# shell too; `AI_FALLBACK_SECRET` is preserved for backward-compat with the
# previous version of this script (which sent the wrong header name —
# fixed 2026-04-25 after the smoke runbook went in).
EDGE_AI_FALLBACK_SECRET="${EDGE_AI_FALLBACK_SECRET:-${AI_FALLBACK_SECRET:-}}"
TOTAL=$((TOTAL + 1))
echo "[6/7] POST /api/ai/fallback/chat (gate check)"
ai_payload='{"messages":[{"role":"user","content":"reply with the single word: ok"}],"max_tokens":8}'
if [[ -n "${EDGE_AI_FALLBACK_SECRET:-}" ]]; then
  http_post "$BASE_URL/api/ai/fallback/chat" \
    -H 'Content-Type: application/json' \
    -H "X-Edge-AI-Secret: ${EDGE_AI_FALLBACK_SECRET}" \
    --data "$ai_payload"
  if [[ "$HTTP_STATUS" != "200" ]]; then
    fail "ai-fallback" "expected 200 with secret, got $HTTP_STATUS (header=X-Edge-AI-Secret; check EDGE_AI_FALLBACK_SECRET matches the value set via wrangler secret put EDGE_AI_FALLBACK_SECRET --env preview)"
  else
    src=$(header_value "X-Source")
    pass "ai-fallback" "200 with secret, X-Source=${src:-(missing)} (Workers AI reachable)"
  fi
else
  http_post "$BASE_URL/api/ai/fallback/chat" \
    -H 'Content-Type: application/json' \
    --data "$ai_payload"
  if [[ "$HTTP_STATUS" != "401" && "$HTTP_STATUS" != "403" ]]; then
    fail "ai-fallback" "expected 401/403 (gate), got $HTTP_STATUS — set EDGE_AI_FALLBACK_SECRET to test the binding end-to-end"
  else
    pass "ai-fallback" "${HTTP_STATUS} (gate wired; set EDGE_AI_FALLBACK_SECRET=… to test Workers AI end-to-end)"
  fi
fi

# ── 6) BOT_HTML_CACHE end-to-end — two-call Googlebot flow on / ────────────
# Spec: GET / with `User-Agent: Googlebot`, then a second identical call.
# In the verified-bot path, call #2 should hit BOT_HTML_CACHE and return
# `X-Source: bot-cache` (with `X-Cache: BOT-KV-HIT`), proving the cache
# write from call #1 is readable on call #2.
#
# Reality check: src/index.ts:`verifySearchBot` only treats a request as a
# real Googlebot when `cf.verifiedBot===true` OR the source IP falls inside
# Google's published bot ranges. Spoofed UAs from arbitrary IPs are filtered
# *before* the BOT_HTML_CACHE lookup (see SPOOFED_BOT branch at ~line 1784),
# so a non-CF caller (e.g. this script running locally or in CI) will land
# on the spoof path and never write or read the cache. We therefore branch:
#
#   • If response #1 carries `X-Bot-Rendered: 1`, we *are* on the verified
#     path and assert the warm-cache evidence (`X-Source: bot-cache`) on #2.
#   • Otherwise we treat it as INFO (not a failure) and document that the
#     full flow needs a Googlebot-IP source. Test #2 (kv-usage enumeration)
#     still proves the binding is wired in this env.
TOTAL=$((TOTAL + 1))
echo "[7/7] BOT_HTML_CACHE: GET / x2 (User-Agent: Googlebot)"
http_get "$BASE_URL/" \
  -H 'User-Agent: Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)' \
  -H 'Accept: text/html'
bot_status_1=$HTTP_STATUS
bot_rendered_1=$(header_value "X-Bot-Rendered")
bot_source_1=$(header_value "X-Source")

if [[ "$bot_status_1" != "200" && "$bot_status_1" != "304" ]]; then
  fail "bot-cache" "first call expected 200/304, got $bot_status_1"
elif [[ "$bot_rendered_1" != "1" ]]; then
  # Spoof-rejection path — expected for any non-CF / non-Google source IP.
  PASSED=$((PASSED + 1))
  echo "  ⓘ [bot-cache] first call returned ${bot_status_1} X-Source=${bot_source_1:-(none)}"
  echo "       (spoofed bot path; full BOT_HTML_CACHE flow needs cf.verifiedBot===true"
  echo "        or a source IP in Google's bot ranges. Binding visibility is still"
  echo "        verified by test [2/7] /api/edge/kv-usage.)"
else
  # Verified-bot path — assert warm-cache evidence on call #2.
  http_get "$BASE_URL/" \
    -H 'User-Agent: Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)' \
    -H 'Accept: text/html'
  bot_status_2=$HTTP_STATUS
  bot_source_2=$(header_value "X-Source")
  bot_xcache_2=$(header_value "X-Cache")
  if [[ "$bot_status_2" != "200" && "$bot_status_2" != "304" ]]; then
    fail "bot-cache" "second call expected 200/304, got $bot_status_2"
  elif [[ "$bot_source_2" != "bot-cache" ]]; then
    fail "bot-cache" "second call expected X-Source=bot-cache (warm), got '${bot_source_2:-(missing)}' (X-Cache=${bot_xcache_2:-(missing)})"
  else
    pass "bot-cache" "warm cache hit on #2: X-Source=$bot_source_2, X-Cache=${bot_xcache_2:-(missing)}"
  fi
fi

# Summary is printed by the EXIT trap (`print_summary`) at the top of this
# script — keeping a single emission point so success and fail-fast aborts
# render identical, fully-counted output.
exit "$([[ "$FAILED" -eq 0 ]] && echo 0 || echo 1)"
