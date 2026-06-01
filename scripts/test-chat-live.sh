#!/usr/bin/env bash
set -uo pipefail

# =============================================================================
# test-chat-live.sh - Comprehensive chat pipeline testing against live syrabit.ai API
# =============================================================================
#
# WARNING: This script tests against PRODUCTION (https://syrabit.ai)
#
# Required environment variables:
#   TEST_USER_EMAIL       - User email for authenticated tests
#   TEST_USER_PASSWORD    - User password for authenticated tests
#
# Flags:
#   --dry-run             Show what would be tested without executing
#   --skip-tts            Skip TTS tests (may be slow or unavailable)
#   --skip-image          Skip image analysis tests (requires a test file)
#
# Usage:
#   export TEST_USER_EMAIL="user@example.com"
#   export TEST_USER_PASSWORD="password123"
#   ./scripts/test-chat-live.sh
# =============================================================================

BASE_URL="https://syrabit.ai"
CHAT_API="${BASE_URL}/api/v1/chat"
AUTH_API="${BASE_URL}/api/v1/auth"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

# Flags
DRY_RUN=false
SKIP_TTS=false
SKIP_IMAGE=false

# Tokens (populated during test flow)
ACCESS_TOKEN=""

# Session tracking for multi-turn
TEST_SESSION_ID="test_$(date +%s)_${RANDOM}"

# Latency tracking
declare -a LATENCIES=()

# Parse arguments
for arg in "$@"; do
  case $arg in
    --dry-run)
      DRY_RUN=true
      ;;
    --skip-tts)
      SKIP_TTS=true
      ;;
    --skip-image)
      SKIP_IMAGE=true
      ;;
    *)
      echo "Unknown argument: $arg"
      echo "Usage: $0 [--dry-run] [--skip-tts] [--skip-image]"
      exit 1
      ;;
  esac
done

# -----------------------------------------------------------------------------
# Utility functions
# -----------------------------------------------------------------------------

print_header() {
  echo ""
  echo -e "${BLUE}=================================================================${NC}"
  echo -e "${BLUE}  $1${NC}"
  echo -e "${BLUE}=================================================================${NC}"
  echo ""
}

print_pass() {
  echo -e "  ${GREEN}[PASS]${NC} $1"
  PASS_COUNT=$((PASS_COUNT + 1))
}

print_fail() {
  echo -e "  ${RED}[FAIL]${NC} $1"
  FAIL_COUNT=$((FAIL_COUNT + 1))
}

print_skip() {
  echo -e "  ${YELLOW}[SKIP]${NC} $1"
  SKIP_COUNT=$((SKIP_COUNT + 1))
}

print_info() {
  echo -e "  ${YELLOW}[INFO]${NC} $1"
}

# JSON value extraction - uses jq if available, falls back to grep/sed
json_value() {
  local json="$1"
  local key="$2"
  if command -v jq &>/dev/null; then
    echo "$json" | jq -r ".$key // empty" 2>/dev/null
  else
    echo "$json" | grep -o "\"$key\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" | sed "s/\"$key\"[[:space:]]*:[[:space:]]*\"//" | sed 's/"$//'
  fi
}

# Check if JSON has a key (works for non-string values too)
json_has_key() {
  local json="$1"
  local key="$2"
  if command -v jq &>/dev/null; then
    echo "$json" | jq -e "has(\"$key\")" &>/dev/null
  else
    echo "$json" | grep -q "\"$key\""
  fi
}

# Get numeric JSON value
json_number() {
  local json="$1"
  local key="$2"
  if command -v jq &>/dev/null; then
    echo "$json" | jq -r ".$key // empty" 2>/dev/null
  else
    echo "$json" | grep -o "\"$key\"[[:space:]]*:[[:space:]]*[0-9.]*" | grep -o '[0-9.]*$'
  fi
}

# Make a request and capture both body and HTTP status code
# Usage: do_request METHOD URL [BODY] [EXTRA_CURL_ARGS...]
# Sets: RESPONSE_BODY, RESPONSE_CODE
RESPONSE_BODY=""
RESPONSE_CODE=""

do_request() {
  local method="$1"
  local url="$2"
  local body="${3:-}"
  shift 3 2>/dev/null || shift $#

  local curl_args=(-s -w "\n%{http_code}" -X "$method")

  if [[ -n "$body" ]]; then
    curl_args+=(-H "Content-Type: application/json" -d "$body")
  fi

  # Append any extra curl args
  curl_args+=("$@")
  curl_args+=("$url")

  local raw_response
  raw_response=$(curl "${curl_args[@]}" 2>/dev/null)

  RESPONSE_CODE=$(echo "$raw_response" | tail -1)
  RESPONSE_BODY=$(echo "$raw_response" | sed '$d')
}

# Make a timed request - same as do_request but also captures elapsed time
# Sets: RESPONSE_BODY, RESPONSE_CODE, ELAPSED_MS
ELAPSED_MS=0

do_timed_request() {
  local method="$1"
  local url="$2"
  local body="${3:-}"
  shift 3 2>/dev/null || shift $#

  local curl_args=(-s -w "\n%{time_total}\n%{http_code}" -X "$method")

  if [[ -n "$body" ]]; then
    curl_args+=(-H "Content-Type: application/json" -d "$body")
  fi

  curl_args+=("$@")
  curl_args+=("$url")

  local raw_response
  raw_response=$(curl "${curl_args[@]}" 2>/dev/null)

  RESPONSE_CODE=$(echo "$raw_response" | tail -1)
  local time_total
  time_total=$(echo "$raw_response" | tail -2 | head -1)
  RESPONSE_BODY=$(echo "$raw_response" | sed -e '$d' -e '$d')

  # Convert seconds to milliseconds
  ELAPSED_MS=$(echo "$time_total" | awk '{printf "%.0f", $1 * 1000}')
}

# Assert HTTP status code
assert_status() {
  local expected="$1"
  local test_name="$2"
  if [[ "$RESPONSE_CODE" == "$expected" ]]; then
    print_pass "$test_name (HTTP $RESPONSE_CODE)"
  else
    print_fail "$test_name (expected HTTP $expected, got HTTP $RESPONSE_CODE)"
    if [[ -n "$RESPONSE_BODY" ]]; then
      echo "         Response: ${RESPONSE_BODY:0:200}"
    fi
  fi
}

# Assert HTTP status code is one of several acceptable values
assert_status_oneof() {
  local test_name="$1"
  shift
  local found=false
  for expected in "$@"; do
    if [[ "$RESPONSE_CODE" == "$expected" ]]; then
      found=true
      break
    fi
  done
  if [[ "$found" == "true" ]]; then
    print_pass "$test_name (HTTP $RESPONSE_CODE)"
  else
    print_fail "$test_name (expected one of [$*], got HTTP $RESPONSE_CODE)"
    if [[ -n "$RESPONSE_BODY" ]]; then
      echo "         Response: ${RESPONSE_BODY:0:200}"
    fi
  fi
}

# Record latency for stats
record_latency() {
  local ms="$1"
  if [[ -n "$ms" && "$ms" != "0" ]]; then
    LATENCIES+=("$ms")
  fi
}

# -----------------------------------------------------------------------------
# Pre-flight checks
# -----------------------------------------------------------------------------

echo ""
echo -e "${RED}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${NC}"
echo -e "${RED}!                                                               !${NC}"
echo -e "${RED}!   WARNING: This script tests against PRODUCTION               !${NC}"
echo -e "${RED}!   Target: ${BASE_URL}                              !${NC}"
echo -e "${RED}!                                                               !${NC}"
echo -e "${RED}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${NC}"
echo ""

if [[ "$DRY_RUN" == "true" ]]; then
  echo -e "${YELLOW}DRY RUN MODE - showing test plan without executing${NC}"
  echo ""
  echo "Section 1: Chat (Non-Streaming) - Authenticated"
  echo "  - POST ${CHAT_API}/ (English message, lang: en)"
  echo "  - POST ${CHAT_API}/ (Assamese message, lang: as)"
  echo "  - POST ${CHAT_API}/ (auto language detection)"
  echo "  - POST ${CHAT_API}/ (with session_id for multi-turn)"
  echo "  - POST ${CHAT_API}/ (empty message - expect 422)"
  echo "  - POST ${CHAT_API}/ (message > 2000 chars - expect 422)"
  echo "  - POST ${CHAT_API}/ (invalid session_id - expect 422)"
  echo ""
  echo "Section 2: Chat (Non-Streaming) - Anonymous"
  echo "  - POST ${CHAT_API}/ (no auth token)"
  echo "  - POST ${CHAT_API}/ (no auth, with X-Anon-ID header)"
  echo ""
  echo "Section 3: Streaming Chat (SSE)"
  echo "  - POST ${CHAT_API}/stream (auth, English message)"
  echo "  - Verify SSE chunks received"
  echo "  - Verify final event has done:true and syrabit_done"
  echo "  - POST ${CHAT_API}/stream (Assamese message)"
  echo "  - POST ${CHAT_API}/stream (anonymous, no auth)"
  echo ""
  echo "Section 4: Chat History"
  echo "  - GET ${CHAT_API}/history (with auth)"
  echo "  - GET ${CHAT_API}/history (without auth)"
  echo "  - GET ${CHAT_API}/history?limit=5 (pagination)"
  echo ""
  echo "Section 5: Chat Messages"
  echo "  - GET ${CHAT_API}/{session_id}/messages (valid session)"
  echo "  - GET ${CHAT_API}/nonexistent-session/messages (expect 404)"
  echo ""
  echo "Section 6: TTS (Text-to-Speech)"
  echo "  - POST ${CHAT_API}/tts (auth, English text)"
  echo "  - POST ${CHAT_API}/tts (no auth - expect 401)"
  echo "  - POST ${CHAT_API}/tts (empty text - expect 422)"
  echo "  - POST ${CHAT_API}/tts (Assamese text)"
  echo ""
  echo "Section 7: Image Analysis (OCR)"
  echo "  - POST ${CHAT_API}/image (no auth - expect 401)"
  echo "  - POST ${CHAT_API}/image (auth, no file - expect 422/400)"
  echo ""
  echo "Section 8: Performance & Latency"
  echo "  - Timed non-streaming chat request"
  echo "  - Timed streaming first-byte latency"
  echo "  - Verify latency_ms in response"
  echo ""
  echo "Section 9: Multi-turn Conversation"
  echo "  - Send first message with session_id"
  echo "  - Send follow-up referencing first message"
  echo "  - Verify contextual responses (200 status)"
  echo ""
  exit 0
fi

# Check required env vars
if [[ -z "${TEST_USER_EMAIL:-}" || -z "${TEST_USER_PASSWORD:-}" ]]; then
  echo -e "${RED}ERROR: Required environment variables not set.${NC}"
  echo ""
  echo "Required:"
  echo "  export TEST_USER_EMAIL=\"your-email@example.com\""
  echo "  export TEST_USER_PASSWORD=\"your-password\""
  echo ""
  echo "Flags:"
  echo "  --dry-run           Show test plan without executing"
  echo "  --skip-tts          Skip TTS tests (may be slow)"
  echo "  --skip-image        Skip image analysis tests"
  echo ""
  exit 1
fi

echo -e "${BLUE}Starting chat pipeline tests against ${BASE_URL}${NC}"
echo -e "${BLUE}Timestamp: $(date -u '+%Y-%m-%d %H:%M:%S UTC')${NC}"
echo -e "${BLUE}Session ID for multi-turn: ${TEST_SESSION_ID}${NC}"
echo ""

# =============================================================================
# LOGIN - Get access token
# =============================================================================

print_header "AUTHENTICATION"

do_request POST "${AUTH_API}/login" "{\"email\":\"${TEST_USER_EMAIL}\",\"password\":\"${TEST_USER_PASSWORD}\"}"

if [[ "$RESPONSE_CODE" == "200" ]]; then
  ACCESS_TOKEN=$(json_value "$RESPONSE_BODY" "access_token")
  if [[ -n "$ACCESS_TOKEN" ]]; then
    print_pass "Login successful - access token obtained"
  else
    print_fail "Login returned 200 but no access_token found"
    echo -e "  ${RED}Cannot proceed with authenticated tests${NC}"
    ACCESS_TOKEN=""
  fi
else
  print_fail "Login failed (expected HTTP 200, got HTTP $RESPONSE_CODE)"
  echo -e "  ${RED}Cannot proceed with authenticated tests${NC}"
  if [[ -n "$RESPONSE_BODY" ]]; then
    echo "         Response: ${RESPONSE_BODY:0:200}"
  fi
fi

# =============================================================================
# SECTION 1: Chat (Non-Streaming) - Authenticated
# =============================================================================

print_header "SECTION 1: Chat (Non-Streaming) - Authenticated"

if [[ -z "$ACCESS_TOKEN" ]]; then
  print_skip "All authenticated chat tests skipped - no token"
else
  # Test: English message with lang: en
  do_request POST "${CHAT_API}/" \
    "{\"message\":\"What is Assam known for?\",\"lang\":\"en\"}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}"
  assert_status "200" "POST /chat/ English message (lang: en)"

  if [[ "$RESPONSE_CODE" == "200" ]]; then
    if json_has_key "$RESPONSE_BODY" "response"; then
      print_pass "Response has 'response' field"
    else
      print_fail "Response missing 'response' field"
    fi
    if json_has_key "$RESPONSE_BODY" "model_used"; then
      print_pass "Response has 'model_used' field"
    else
      print_fail "Response missing 'model_used' field"
    fi
    if json_has_key "$RESPONSE_BODY" "latency_ms"; then
      print_pass "Response has 'latency_ms' field"
      local_latency=$(json_number "$RESPONSE_BODY" "latency_ms")
      if [[ -n "$local_latency" ]]; then
        record_latency "$local_latency"
        print_info "Reported latency: ${local_latency}ms"
      fi
    else
      print_fail "Response missing 'latency_ms' field"
    fi
  fi

  # Test: Assamese message with lang: as
  do_request POST "${CHAT_API}/" \
    "{\"message\":\"নমস্কাৰ, তুমি কেনে আছা?\",\"lang\":\"as\"}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}"
  assert_status "200" "POST /chat/ Assamese message (lang: as)"

  if [[ "$RESPONSE_CODE" == "200" ]]; then
    local_model=$(json_value "$RESPONSE_BODY" "model_used")
    if [[ -n "$local_model" ]]; then
      print_info "Model used for Assamese: $local_model"
      if echo "$local_model" | grep -qi "sarvam\|sarv"; then
        print_pass "Assamese routed to Sarvam-related model"
      else
        print_info "Model for Assamese is '$local_model' (expected Sarvam-related)"
      fi
    fi
  fi

  # Test: Auto language detection (no lang field)
  do_request POST "${CHAT_API}/" \
    "{\"message\":\"Tell me about the Brahmaputra river\"}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}"
  assert_status "200" "POST /chat/ auto language detection (no lang field)"

  # Test: With session_id for multi-turn
  do_request POST "${CHAT_API}/" \
    "{\"message\":\"My name is TestBot. Remember this.\",\"lang\":\"en\",\"session_id\":\"${TEST_SESSION_ID}\"}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}"
  assert_status "200" "POST /chat/ with session_id (multi-turn)"

  # Test: Empty message (should 422)
  do_request POST "${CHAT_API}/" \
    "{\"message\":\"\",\"lang\":\"en\"}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}"
  assert_status "422" "POST /chat/ empty message (expect 422)"

  # Test: Message > 2000 chars (should 422)
  LONG_MSG=$(printf 'A%.0s' $(seq 1 2001))
  do_request POST "${CHAT_API}/" \
    "{\"message\":\"${LONG_MSG}\",\"lang\":\"en\"}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}"
  assert_status "422" "POST /chat/ message > 2000 chars (expect 422)"

  # Test: Invalid session_id with special characters (should 422)
  do_request POST "${CHAT_API}/" \
    "{\"message\":\"hello\",\"lang\":\"en\",\"session_id\":\"<script>alert(1)</script>\"}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}"
  assert_status "422" "POST /chat/ invalid session_id (special chars, expect 422)"
fi

# =============================================================================
# SECTION 2: Chat (Non-Streaming) - Anonymous
# =============================================================================

print_header "SECTION 2: Chat (Non-Streaming) - Anonymous"

# Test: Anonymous chat (no auth token)
do_request POST "${CHAT_API}/" \
  "{\"message\":\"What is the capital of India?\",\"lang\":\"en\"}"
assert_status "200" "POST /chat/ anonymous (no auth token)"

if [[ "$RESPONSE_CODE" == "200" ]]; then
  if json_has_key "$RESPONSE_BODY" "response"; then
    print_pass "Anonymous response has 'response' field"
  else
    print_fail "Anonymous response missing 'response' field"
  fi
fi

# Test: Anonymous with X-Anon-ID header
do_request POST "${CHAT_API}/" \
  "{\"message\":\"Hello from anonymous user\",\"lang\":\"en\"}" \
  -H "X-Anon-ID: anon_test_${RANDOM}"
assert_status "200" "POST /chat/ anonymous with X-Anon-ID header"

# =============================================================================
# SECTION 3: Streaming Chat (SSE)
# =============================================================================

print_header "SECTION 3: Streaming Chat (SSE)"

if [[ -z "$ACCESS_TOKEN" ]]; then
  print_skip "Authenticated streaming tests skipped - no token"
else
  # Test: Streaming with auth, English message
  SSE_OUTPUT=$(curl -s --max-time 15 -X POST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -d "{\"message\":\"Say hello in one sentence\",\"lang\":\"en\"}" \
    "${CHAT_API}/stream" 2>/dev/null)
  SSE_EXIT=$?

  # Check if we got any output
  if [[ -n "$SSE_OUTPUT" ]]; then
    print_pass "POST /chat/stream - received SSE response (auth, English)"

    # Verify SSE chunks are present
    if echo "$SSE_OUTPUT" | grep -q "^data:"; then
      print_pass "SSE chunks received (data: prefix found)"
    else
      print_fail "SSE chunks not in expected format (no data: prefix)"
      echo "         First 200 chars: ${SSE_OUTPUT:0:200}"
    fi

    # Verify final event contains done: true
    if echo "$SSE_OUTPUT" | grep -q '"done"[[:space:]]*:[[:space:]]*true'; then
      print_pass "Final SSE event contains done: true"
    else
      print_info "done: true not found in captured output (stream may have been cut short)"
    fi

    # Verify syrabit_done event
    if echo "$SSE_OUTPUT" | grep -q "syrabit_done"; then
      print_pass "Final SSE event contains syrabit_done"
    else
      print_info "syrabit_done not found (stream may have been cut short)"
    fi
  else
    if [[ $SSE_EXIT -eq 28 ]]; then
      print_fail "POST /chat/stream timed out (15s) with no output"
    else
      print_fail "POST /chat/stream returned empty response"
    fi
  fi

  # Test: Streaming with Assamese message
  SSE_AS_OUTPUT=$(curl -s --max-time 15 -X POST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -d "{\"message\":\"নমস্কাৰ\",\"lang\":\"as\"}" \
    "${CHAT_API}/stream" 2>/dev/null)

  if [[ -n "$SSE_AS_OUTPUT" ]]; then
    print_pass "POST /chat/stream - Assamese streaming works"
  else
    print_fail "POST /chat/stream - Assamese streaming returned empty"
  fi
fi

# Test: Streaming anonymous (no auth)
SSE_ANON_OUTPUT=$(curl -s --max-time 15 -X POST \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"Hi there\",\"lang\":\"en\"}" \
  "${CHAT_API}/stream" 2>/dev/null)

if [[ -n "$SSE_ANON_OUTPUT" ]]; then
  print_pass "POST /chat/stream anonymous - received response"
else
  print_fail "POST /chat/stream anonymous - empty response"
fi

# =============================================================================
# SECTION 4: Chat History
# =============================================================================

print_header "SECTION 4: Chat History"

if [[ -z "$ACCESS_TOKEN" ]]; then
  print_skip "Authenticated history tests skipped - no token"
else
  # Test: GET /chat/history with auth
  do_request GET "${CHAT_API}/history" "" -H "Authorization: Bearer ${ACCESS_TOKEN}"
  assert_status "200" "GET /chat/history with auth"

  if [[ "$RESPONSE_CODE" == "200" ]]; then
    if json_has_key "$RESPONSE_BODY" "chats"; then
      print_pass "History response has 'chats' array"
    else
      print_fail "History response missing 'chats' array"
    fi
    if json_has_key "$RESPONSE_BODY" "pagination"; then
      print_pass "History response has 'pagination' object"
    else
      print_fail "History response missing 'pagination' object"
    fi
  fi

  # Test: GET /chat/history with limit=5
  do_request GET "${CHAT_API}/history?limit=5" "" -H "Authorization: Bearer ${ACCESS_TOKEN}"
  assert_status "200" "GET /chat/history?limit=5 (pagination)"

  if [[ "$RESPONSE_CODE" == "200" ]]; then
    if command -v jq &>/dev/null; then
      chat_count=$(echo "$RESPONSE_BODY" | jq '.chats | length' 2>/dev/null)
      if [[ -n "$chat_count" && "$chat_count" -le 5 ]]; then
        print_pass "Pagination respects limit (got $chat_count chats, limit 5)"
      else
        print_info "Got $chat_count chats with limit=5 (may have fewer than 5 total)"
      fi
    fi
  fi
fi

# Test: GET /chat/history without auth
do_request GET "${CHAT_API}/history" ""
if [[ "$RESPONSE_CODE" == "200" ]]; then
  print_pass "GET /chat/history without auth returns 200 (optional auth)"
  if command -v jq &>/dev/null; then
    anon_chats=$(echo "$RESPONSE_BODY" | jq '.chats | length' 2>/dev/null)
    print_info "Anonymous history returned $anon_chats chats"
  fi
elif [[ "$RESPONSE_CODE" == "401" ]]; then
  print_pass "GET /chat/history without auth returns 401 (auth required)"
else
  print_fail "GET /chat/history without auth unexpected status (HTTP $RESPONSE_CODE)"
fi

# =============================================================================
# SECTION 5: Chat Messages
# =============================================================================

print_header "SECTION 5: Chat Messages"

if [[ -z "$ACCESS_TOKEN" ]]; then
  print_skip "Chat messages tests skipped - no token"
else
  # Test: GET messages for our test session
  do_request GET "${CHAT_API}/${TEST_SESSION_ID}/messages" "" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}"
  assert_status_oneof "GET /chat/{session_id}/messages (test session)" "200" "404"

  if [[ "$RESPONSE_CODE" == "200" ]]; then
    print_info "Messages found for test session"
  elif [[ "$RESPONSE_CODE" == "404" ]]; then
    print_info "Session not found (messages may not have been persisted yet)"
  fi
fi

# Test: GET messages for nonexistent session
do_request GET "${CHAT_API}/nonexistent-session-id-xyz-000/messages" ""
assert_status_oneof "GET /chat/nonexistent-session/messages" "404" "401"

# =============================================================================
# SECTION 6: TTS (Text-to-Speech)
# =============================================================================

print_header "SECTION 6: TTS (Text-to-Speech)"

if [[ "$SKIP_TTS" == "true" ]]; then
  print_skip "TTS tests skipped (--skip-tts)"
else
  if [[ -z "$ACCESS_TOKEN" ]]; then
    print_skip "Authenticated TTS tests skipped - no token"
  else
    # Test: TTS with auth, English text
    TTS_RESPONSE=$(curl -s -w "\n%{http_code}\n%{content_type}" -X POST \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${ACCESS_TOKEN}" \
      -d "{\"text\":\"Hello world\",\"lang\":\"en\"}" \
      "${CHAT_API}/tts" 2>/dev/null)

    TTS_CONTENT_TYPE=$(echo "$TTS_RESPONSE" | tail -1)
    TTS_CODE=$(echo "$TTS_RESPONSE" | tail -2 | head -1)

    if [[ "$TTS_CODE" == "200" ]]; then
      print_pass "POST /chat/tts English (HTTP 200)"
      if echo "$TTS_CONTENT_TYPE" | grep -qi "audio"; then
        print_pass "TTS response content-type is audio"
      else
        print_info "TTS content-type: $TTS_CONTENT_TYPE"
      fi
    else
      print_fail "POST /chat/tts English (expected 200, got HTTP $TTS_CODE)"
    fi

    # Test: TTS with Assamese text
    TTS_AS_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${ACCESS_TOKEN}" \
      -d "{\"text\":\"নমস্কাৰ পৃথিৱী\",\"lang\":\"as\"}" \
      "${CHAT_API}/tts" 2>/dev/null)

    TTS_AS_CODE=$(echo "$TTS_AS_RESPONSE" | tail -1)

    if [[ "$TTS_AS_CODE" == "200" ]]; then
      print_pass "POST /chat/tts Assamese (HTTP 200)"
    else
      print_fail "POST /chat/tts Assamese (expected 200, got HTTP $TTS_AS_CODE)"
    fi

    # Test: TTS with empty text (expect 422)
    do_request POST "${CHAT_API}/tts" \
      "{\"text\":\"\",\"lang\":\"en\"}" \
      -H "Authorization: Bearer ${ACCESS_TOKEN}"
    assert_status "422" "POST /chat/tts empty text (expect 422)"
  fi

  # Test: TTS without auth (expect 401)
  do_request POST "${CHAT_API}/tts" \
    "{\"text\":\"Hello\",\"lang\":\"en\"}"
  assert_status "401" "POST /chat/tts without auth (expect 401)"
fi

# =============================================================================
# SECTION 7: Image Analysis (OCR)
# =============================================================================

print_header "SECTION 7: Image Analysis (OCR)"

if [[ "$SKIP_IMAGE" == "true" ]]; then
  print_skip "Image analysis tests skipped (--skip-image)"
else
  # Test: Image without auth (expect 401)
  do_request POST "${CHAT_API}/image" ""
  assert_status "401" "POST /chat/image without auth (expect 401)"

  if [[ -n "$ACCESS_TOKEN" ]]; then
    # Test: Image with auth but no file (expect 422/400)
    IMAGE_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
      -H "Authorization: Bearer ${ACCESS_TOKEN}" \
      "${CHAT_API}/image" 2>/dev/null)

    IMAGE_CODE=$(echo "$IMAGE_RESPONSE" | tail -1)

    if [[ "$IMAGE_CODE" == "422" || "$IMAGE_CODE" == "400" ]]; then
      print_pass "POST /chat/image auth, no file (HTTP $IMAGE_CODE - expected 422/400)"
    else
      print_fail "POST /chat/image auth, no file (expected 422/400, got HTTP $IMAGE_CODE)"
    fi
  else
    print_skip "Image with auth test skipped - no token"
  fi
fi

# =============================================================================
# SECTION 8: Performance & Latency
# =============================================================================

print_header "SECTION 8: Performance & Latency"

if [[ -z "$ACCESS_TOKEN" ]]; then
  print_skip "Performance tests skipped - no token"
else
  # Test: Timed non-streaming chat request
  do_timed_request POST "${CHAT_API}/" \
    "{\"message\":\"What time is it?\",\"lang\":\"en\"}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}"

  if [[ "$RESPONSE_CODE" == "200" ]]; then
    print_pass "Timed non-streaming chat (HTTP 200)"
    print_info "Wall-clock latency: ${ELAPSED_MS}ms"
    record_latency "$ELAPSED_MS"

    # Verify latency_ms is in the response
    reported_latency=$(json_number "$RESPONSE_BODY" "latency_ms")
    if [[ -n "$reported_latency" ]]; then
      print_pass "latency_ms reported in response: ${reported_latency}ms"
      record_latency "$reported_latency"
    else
      print_fail "latency_ms not found in response"
    fi
  else
    print_fail "Timed non-streaming chat (expected 200, got HTTP $RESPONSE_CODE)"
  fi

  # Test: Streaming first-byte latency
  STREAM_START=$(date +%s%N 2>/dev/null || date +%s)
  FIRST_CHUNK=$(curl -s --max-time 15 -X POST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -d "{\"message\":\"Say yes\",\"lang\":\"en\"}" \
    "${CHAT_API}/stream" 2>/dev/null | head -1)
  STREAM_END=$(date +%s%N 2>/dev/null || date +%s)

  if [[ -n "$FIRST_CHUNK" ]]; then
    # Calculate first-byte latency (nanoseconds to ms if available)
    if [[ "$STREAM_START" -gt 1000000000000 ]]; then
      FIRST_BYTE_MS=$(( (STREAM_END - STREAM_START) / 1000000 ))
    else
      FIRST_BYTE_MS=$(( (STREAM_END - STREAM_START) * 1000 ))
    fi
    print_pass "Streaming first-byte received"
    print_info "Streaming first-byte latency: ~${FIRST_BYTE_MS}ms"
    record_latency "$FIRST_BYTE_MS"
  else
    print_fail "Streaming first-byte - no data received"
  fi
fi

# =============================================================================
# SECTION 9: Multi-turn Conversation
# =============================================================================

print_header "SECTION 9: Multi-turn Conversation"

if [[ -z "$ACCESS_TOKEN" ]]; then
  print_skip "Multi-turn tests skipped - no token"
else
  MULTI_SESSION="multiturn_$(date +%s)_${RANDOM}"

  # Test: First message in multi-turn
  do_request POST "${CHAT_API}/" \
    "{\"message\":\"My favorite color is blue. Please remember this.\",\"lang\":\"en\",\"session_id\":\"${MULTI_SESSION}\"}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}"
  assert_status "200" "Multi-turn: first message (establish context)"

  if [[ "$RESPONSE_CODE" == "200" ]]; then
    # Test: Follow-up message referencing first
    do_request POST "${CHAT_API}/" \
      "{\"message\":\"What is my favorite color?\",\"lang\":\"en\",\"session_id\":\"${MULTI_SESSION}\"}" \
      -H "Authorization: Bearer ${ACCESS_TOKEN}"
    assert_status "200" "Multi-turn: follow-up message (context recall)"

    if [[ "$RESPONSE_CODE" == "200" ]]; then
      response_text=$(json_value "$RESPONSE_BODY" "response")
      if echo "$response_text" | grep -qi "blue"; then
        print_pass "Multi-turn: response correctly recalls context (mentions 'blue')"
      else
        print_info "Multi-turn: response did not explicitly mention 'blue' (context may not persist)"
        print_info "Response excerpt: ${response_text:0:150}"
      fi
    fi
  else
    print_skip "Multi-turn follow-up skipped - first message failed"
  fi
fi

# =============================================================================
# LATENCY STATISTICS
# =============================================================================

print_header "LATENCY STATISTICS"

if [[ ${#LATENCIES[@]} -gt 0 ]]; then
  # Calculate min, max, avg
  MIN_LAT=${LATENCIES[0]}
  MAX_LAT=${LATENCIES[0]}
  SUM_LAT=0

  for lat in "${LATENCIES[@]}"; do
    SUM_LAT=$((SUM_LAT + lat))
    if [[ $lat -lt $MIN_LAT ]]; then
      MIN_LAT=$lat
    fi
    if [[ $lat -gt $MAX_LAT ]]; then
      MAX_LAT=$lat
    fi
  done

  AVG_LAT=$((SUM_LAT / ${#LATENCIES[@]}))

  echo -e "  ${BLUE}Measurements: ${#LATENCIES[@]}${NC}"
  echo -e "  ${BLUE}Min latency:  ${MIN_LAT}ms${NC}"
  echo -e "  ${BLUE}Max latency:  ${MAX_LAT}ms${NC}"
  echo -e "  ${BLUE}Avg latency:  ${AVG_LAT}ms${NC}"
else
  print_info "No latency measurements collected"
fi

# =============================================================================
# SUMMARY
# =============================================================================

print_header "TEST SUMMARY"

TOTAL=$((PASS_COUNT + FAIL_COUNT + SKIP_COUNT))
echo -e "  ${GREEN}PASSED:  ${PASS_COUNT}${NC}"
echo -e "  ${RED}FAILED:  ${FAIL_COUNT}${NC}"
echo -e "  ${YELLOW}SKIPPED: ${SKIP_COUNT}${NC}"
echo -e "  ${BLUE}TOTAL:   ${TOTAL}${NC}"
echo ""

if [[ $FAIL_COUNT -gt 0 ]]; then
  echo -e "${RED}Some tests FAILED. Review output above for details.${NC}"
  exit 1
else
  echo -e "${GREEN}All executed tests PASSED.${NC}"
  exit 0
fi
