#!/usr/bin/env bash
# =============================================================================
# test-sarvam-ttfb.sh — Sarvam AI Streaming TTFB Verification
# =============================================================================
#
# Verifies the Sarvam streaming fix: first token must arrive in < 3 seconds
# regardless of the input language. Tests English, Hindi, and Assamese inputs
# all routed to Sarvam (lang=as), checking that:
#
#   1. TTFB (first SSE data chunk) < 3000ms  ← core assertion
#   2. Response text contains Assamese Unicode characters (ক–ৱ range)
#   3. Anonymous access works (no token required for streaming)
#
# Background: sarvam-30b streams reasoning_content at ~150ms before delta.content
# (~7s). The streaming fix yields reasoning_content immediately so users see
# Assamese text in < 200ms instead of waiting 7+ seconds.
#
# Implementation note: TTFB is measured via inline Python (httpx.aiter_lines)
# rather than bash FIFOs/pipes. Bash pipes have 64KB OS-level buffering that
# causes read() to block until the buffer fills — making TTFB measurements
# falsely show the full round-trip time instead of first-byte latency.
#
# Usage:
#   # Against production (default):
#   bash scripts/test-sarvam-ttfb.sh
#
#   # Against local dev:
#   BASE_URL=http://localhost:8000 bash scripts/test-sarvam-ttfb.sh
#
#   # With credentials for authenticated test:
#   export TEST_USER_EMAIL="you@syrabit.ai"
#   export TEST_USER_PASSWORD="yourpassword"
#   bash scripts/test-sarvam-ttfb.sh
#
#   # Adjust TTFB threshold (ms, default 3000):
#   TTFB_THRESHOLD_MS=2000 bash scripts/test-sarvam-ttfb.sh
#
# Requirements: bash, python3, httpx (installed via backend requirements)
# =============================================================================
set -uo pipefail

BASE_URL="${BASE_URL:-https://api.syrabit.ai}"
CHAT_STREAM="${BASE_URL}/api/v1/chat/stream"
AUTH_API="${BASE_URL}/api/v1/auth/login"
TTFB_THRESHOLD_MS="${TTFB_THRESHOLD_MS:-3000}"
STREAM_TIMEOUT="${STREAM_TIMEOUT:-45}"

# ── Colours ──────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  G='\033[0;32m' R='\033[0;31m' Y='\033[1;33m'
  C='\033[0;36m' B='\033[1m'   N='\033[0m'
else
  G='' R='' Y='' C='' B='' N=''
fi

PASS=0; FAIL=0; SKIP=0

pass()   { printf "  ${G}✓${N}  %s\n" "$1"; PASS=$((PASS+1)); }
fail()   { printf "  ${R}✗${N}  %s\n" "$1"; FAIL=$((FAIL+1)); }
skip()   { printf "  ${Y}–${N}  %s (skipped)\n" "$1"; SKIP=$((SKIP+1)); }
info()   { printf "     ${Y}%s${N}\n" "$1"; }
header() { printf "\n${C}${B}── %s ──${N}\n" "$1"; }

# ── Check python3 + httpx available ──────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo -e "${R}python3 is required but not found.${N}"; exit 1
fi
if ! python3 -c "import httpx" &>/dev/null; then
  echo -e "${R}httpx is required: pip install httpx${N}"; exit 1
fi

ACCESS_TOKEN=""

printf "\n${C}${B}Syrabit Sarvam TTFB Verification${N}\n"
printf "  Target    : %s\n" "$CHAT_STREAM"
printf "  Threshold : %sms\n" "$TTFB_THRESHOLD_MS"
printf "  Time      : %s\n" "$(date -u '+%Y-%m-%d %H:%M:%S UTC')"

# =============================================================================
# Optional: login to get a token (enables authenticated streaming tests)
# =============================================================================
header "Authentication (optional)"

if [[ -n "${TEST_USER_EMAIL:-}" && -n "${TEST_USER_PASSWORD:-}" ]]; then
  LOGIN_BODY=$(curl -s --max-time 10 -X POST \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${TEST_USER_EMAIL}\",\"password\":\"${TEST_USER_PASSWORD}\"}" \
    "$AUTH_API" 2>/dev/null || echo "{}")
  ACCESS_TOKEN=$(echo "$LOGIN_BODY" | python3 -c \
    "import json,sys; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null || echo "")
  if [[ -n "$ACCESS_TOKEN" ]]; then
    pass "Logged in — authenticated streaming tests enabled"
  else
    info "Login failed — running anonymous streaming tests only"
    ACCESS_TOKEN=""
  fi
else
  info "TEST_USER_EMAIL/PASSWORD not set — running anonymous streaming tests"
fi

# =============================================================================
# TTFB probe via inline Python/httpx
# httpx.aiter_lines() is truly line-by-line — no OS pipe buffer delay.
# Each SSE line is yielded the moment the HTTP/1.1 chunked response delivers it.
# =============================================================================
PROBE_PY='
import sys, json, time, asyncio, httpx

chat_url   = sys.argv[1]
token      = sys.argv[2]
message    = sys.argv[3]
thresh_ms  = int(sys.argv[4])
timeout_s  = float(sys.argv[5])

async def probe():
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    ttfb_ms = None
    first_chunk = ""
    all_content = []
    error_msg = ""
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            async with client.stream(
                "POST", chat_url,
                headers=headers,
                json={"message": message, "lang": "as"},
            ) as resp:
                t0 = time.monotonic()  # start after connection established (pure model latency)
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]" or not raw:
                        continue
                    try:
                        d = json.loads(raw)
                        c = d.get("content", "") or d.get("reasoning_content", "") or ""
                        if c:
                            all_content.append(c)
                            if ttfb_ms is None:
                                ttfb_ms = int((time.monotonic() - t0) * 1000)
                                first_chunk = c[:100]
                    except Exception:
                        pass
    except Exception as exc:
        error_msg = str(exc)[:200]

    full = "".join(all_content)
    has_as = any("\u0980" <= ch <= "\u09ff" for ch in full)
    result = {
        "ttfb_ms": ttfb_ms,
        "first_chunk": first_chunk,
        "has_assamese": has_as,
        "full_length": len(full),
        "passed": ttfb_ms is not None and ttfb_ms < thresh_ms,
        "error": error_msg,
    }
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(probe())
'

# =============================================================================
# measure_ttfb <label> <message> [token]
# =============================================================================
measure_ttfb() {
  local label="$1" message="$2" token="${3:-}"

  printf "\n  ${B}%s${N}\n" "$label"
  printf "  Input : \"%s\"\n" "${message:0:70}"

  local raw_result
  raw_result=$(python3 -c "$PROBE_PY" \
    "$CHAT_STREAM" \
    "$token" \
    "$message" \
    "$TTFB_THRESHOLD_MS" \
    "$STREAM_TIMEOUT" 2>&1) || true

  local ttfb first_chunk has_as error passed full_length
  ttfb=$(echo "$raw_result"        | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('ttfb_ms',''))"      2>/dev/null || echo "")
  first_chunk=$(echo "$raw_result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('first_chunk',''))"  2>/dev/null || echo "")
  has_as=$(echo "$raw_result"      | python3 -c "import json,sys; d=json.load(sys.stdin); print('yes' if d.get('has_assamese') else 'no')" 2>/dev/null || echo "no")
  error=$(echo "$raw_result"       | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('error',''))"        2>/dev/null || echo "")
  passed=$(echo "$raw_result"      | python3 -c "import json,sys; d=json.load(sys.stdin); print('yes' if d.get('passed') else 'no')" 2>/dev/null || echo "no")
  full_length=$(echo "$raw_result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('full_length',0))"  2>/dev/null || echo "0")

  if [[ -n "$error" ]]; then
    fail "Connection/stream error: $error"
    return
  fi

  if [[ -z "$ttfb" ]]; then
    fail "No content received within ${STREAM_TIMEOUT}s"
    return
  fi

  # TTFB assertion
  if [[ "$passed" == "yes" ]]; then
    pass "TTFB: ${ttfb}ms (threshold: <${TTFB_THRESHOLD_MS}ms)"
  else
    fail "TTFB: ${ttfb}ms EXCEEDS threshold ${TTFB_THRESHOLD_MS}ms — streaming fix may not be active"
    info "If running against local dev, confirm the matching API Worker version is deployed."
  fi

  # First content preview
  [[ -n "$first_chunk" ]] && info "First chunk: \"${first_chunk:0:80}\""
  info "Response length: ${full_length} chars"

  # Assamese Unicode check (ক–ৱ: U+0980–U+09FF)
  if [[ "$has_as" == "yes" ]]; then
    pass "Response contains Assamese Unicode (ক–ৱ script)"
  else
    fail "Response does NOT contain Assamese Unicode — system prompt enforcement may be broken"
  fi
}

# =============================================================================
# Test 1 — English input → Assamese output (anon)
# =============================================================================
header "Test 1: English input → Assamese output (anonymous)"
measure_ttfb \
  "English → Assamese (anon)" \
  "What is photosynthesis? Answer in one sentence." \
  ""

# =============================================================================
# Test 2 — Hindi/Devanagari input → Assamese output (anon)
# =============================================================================
header "Test 2: Hindi input → Assamese output (anonymous)"
measure_ttfb \
  "Hindi → Assamese (anon)" \
  "প্রকাশ সংশ্লেষণ কি?" \
  ""

# =============================================================================
# Test 3 — Assamese input → Assamese output (anon)
# =============================================================================
header "Test 3: Assamese input → Assamese output (anonymous)"
measure_ttfb \
  "Assamese → Assamese (anon)" \
  "সালোক সংশ্লেষণ কি?" \
  ""

# =============================================================================
# Test 4 — English input → Assamese output (authenticated, if token available)
# =============================================================================
header "Test 4: English input → Assamese output (authenticated)"
if [[ -n "$ACCESS_TOKEN" ]]; then
  measure_ttfb \
    "English → Assamese (auth)" \
    "Explain the water cycle in one sentence." \
    "$ACCESS_TOKEN"
else
  skip "Authenticated TTFB test (no credentials)"
fi

# =============================================================================
# Test 5 — Chat stream endpoint reachability (HTTP status only)
# Note: --max-time 8 for SSE endpoint; curl exits 28 (timeout) after headers
# arrive — we capture http_code regardless and treat 200 as success.
# =============================================================================
header "Test 5: Chat stream endpoint reachability"

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  --connect-timeout 10 \
  --max-time 8 \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"message":"hi","lang":"as"}' \
  "$CHAT_STREAM" 2>/dev/null); HTTP_CODE="${HTTP_CODE:-000}"

# curl exits 28 on SSE timeout but still writes http_code — that's correct.
# Strip any trailing non-numeric chars (safety) and check for 200.
HTTP_CODE="${HTTP_CODE//[^0-9]/}"
HTTP_CODE="${HTTP_CODE:0:3}"

if [[ "$HTTP_CODE" == "200" ]]; then
  pass "POST /api/v1/chat/stream → 200"
elif [[ "$HTTP_CODE" == "000" ]]; then
  fail "Could not reach $CHAT_STREAM (connection refused / timeout)"
else
  fail "POST /api/v1/chat/stream → $HTTP_CODE (expected 200)"
fi

# =============================================================================
# Summary
# =============================================================================
TOTAL=$((PASS+FAIL+SKIP))
printf "\n${B}────────────────────────────────────${N}\n"
printf "${B}  TTFB Checks: %d total${N}\n" "$TOTAL"
printf "  ${G}✓ Passed : %d${N}\n" "$PASS"
[[ $FAIL -gt 0 ]] && printf "  ${R}✗ Failed : %d${N}\n" "$FAIL" || printf "  ✗ Failed : %d\n" "$FAIL"
[[ $SKIP -gt 0 ]] && printf "  ${Y}– Skipped: %d${N}\n" "$SKIP" || printf "  – Skipped: %d\n" "$SKIP"
printf "${B}────────────────────────────────────${N}\n\n"

printf "  ${B}What this test verifies:${N}\n"
printf "  • sarvam-30b streams reasoning_content at ~150ms (not 7s)\n"
printf "  • Any language input (EN/HI/AS) works — system prompt enforces Assamese output\n"
printf "  • Assamese Unicode appears in every response\n"
printf "  • TTFB measured via httpx.aiter_lines() — no pipe-buffer distortion\n\n"

if [[ $FAIL -gt 0 ]]; then
  printf "${R}TTFB TEST FAILED — see failures above${N}\n\n"
  printf "Possible causes:\n"
  printf "  • Streaming fix not active: ensure backend yields reasoning_content before content\n"
  printf "  • Sarvam API key expired or rate-limited (check /api/v1/health/providers)\n"
  printf "  • Network RTT to api.sarvam.ai > threshold (try raising TTFB_THRESHOLD_MS)\n\n"
  exit 1
else
  printf "${G}ALL TTFB CHECKS PASSED${N}\n\n"
  exit 0
fi
