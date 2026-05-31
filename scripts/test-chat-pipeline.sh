#!/usr/bin/env bash
set -uo pipefail
# ==============================================================================
# SYRABIT CHAT PIPELINE END-TO-END TEST
# ==============================================================================
#
# Comprehensive Cloud Shell test script that validates the full chat pipeline
# layer by layer with latency measurement and response display verification.
#
# Architecture tested:
#   Frontend (syrabit.ai) -> POST /api/v1/chat/stream or /api/v1/chat/
#   -> Backend receives message, detects language, routes to model
#   -> Embedding via Vertex AI text-embedding-005 -> cosine similarity
#   -> TopicEmbeddings in MongoDB -> if score >= 0.70 fetch RAG context
#   -> LLM generates response (Vertex AI Gemini / Sarvam AI for Assamese)
#   -> Response streamed as SSE: data: {"content": "chunk", "done": false}
#   -> Final: data: {"content": "", "done": true, "event": "syrabit_done", ...}
#   -> Frontend ChatPage.jsx reads parsed.content to display text
#
# Usage:
#   ./scripts/test-chat-pipeline.sh
#   BASE_URL="https://staging-api.syrabit.ai" ./scripts/test-chat-pipeline.sh
#   VERBOSE=1 ./scripts/test-chat-pipeline.sh
#
# Requirements: bash, curl, jq (no Python needed)
# ==============================================================================

# --- Configuration -----------------------------------------------------------

BASE_URL="${BASE_URL:-https://api.syrabit.ai}"
FRONTEND_URL="${FRONTEND_URL:-https://syrabit.ai}"
VERBOSE="${VERBOSE:-0}"

# Latency budgets (ms)
BUDGET_HEALTH=2000
BUDGET_GENERIC=5000
BUDGET_TOPIC=8000
BUDGET_STREAM_FIRST_BYTE=3000
BUDGET_STREAM_COMPLETE=15000
BUDGET_ASSAMESE=12000

# --- State -------------------------------------------------------------------

TOTAL=0
PASSED=0
FAILED=0
WARNINGS=0
CRITICAL_FAIL=0

# Latency tracking
LAT_HEALTH=0
LAT_GENERIC=0
LAT_TOPIC=0
LAT_STREAM_FIRST_BYTE=0
LAT_STREAM_COMPLETE=0
LAT_ASSAMESE=0

# --- Colors (detect terminal) ------------------------------------------------

if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    DIM='\033[2m'
    NC='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' CYAN='' BOLD='' DIM='' NC=''
fi

# --- Helpers -----------------------------------------------------------------

pass() {
    TOTAL=$((TOTAL + 1))
    PASSED=$((PASSED + 1))
    printf "  ${GREEN}✓ PASS${NC}  %s\n" "$1"
}

fail() {
    TOTAL=$((TOTAL + 1))
    FAILED=$((FAILED + 1))
    printf "  ${RED}✗ FAIL${NC}  %s\n" "$1"
}

fail_critical() {
    TOTAL=$((TOTAL + 1))
    FAILED=$((FAILED + 1))
    CRITICAL_FAIL=$((CRITICAL_FAIL + 1))
    printf "  ${RED}✗ FAIL${NC}  ${RED}[CRITICAL]${NC} %s\n" "$1"
}

warn() {
    TOTAL=$((TOTAL + 1))
    WARNINGS=$((WARNINGS + 1))
    printf "  ${YELLOW}⚠ WARN${NC}  %s\n" "$1"
}

info() {
    printf "  ${DIM}  ->  %s${NC}\n" "$1"
}

header() {
    echo ""
    printf "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    printf "${BOLD}  LAYER %s${NC}\n" "$1"
    printf "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
}

verbose() {
    if [[ "$VERBOSE" == "1" ]]; then
        printf "  ${DIM}[VERBOSE] %s${NC}\n" "$1"
    fi
}

# Get current time in milliseconds
now_ms() {
    if date +%s%N > /dev/null 2>&1; then
        echo $(( $(date +%s%N) / 1000000 ))
    else
        # macOS fallback using seconds
        echo $(( $(date +%s) * 1000 ))
    fi
}

# Generate anonymous session ID
gen_anon_id() {
    local hex
    hex=$(cat /dev/urandom | tr -dc 'a-f0-9' | fold -w 32 | head -n 1)
    echo "anon_${hex}"
}

ANON_ID=$(gen_anon_id)


# ==============================================================================
# LAYER 0: Prerequisites
# ==============================================================================

header "0: Prerequisites"

# Check curl
if command -v curl &> /dev/null; then
    CURL_VER=$(curl --version | head -1 | awk '{print $2}')
    pass "curl installed (v${CURL_VER})"
else
    fail_critical "curl not found - cannot proceed"
    echo "  Install curl and retry."
    exit 1
fi

# Check jq
if command -v jq &> /dev/null; then
    JQ_VER=$(jq --version 2>&1 | head -1)
    pass "jq installed (${JQ_VER})"
else
    fail_critical "jq not found - cannot proceed"
    echo "  Install jq and retry."
    exit 1
fi

info "Backend: ${BASE_URL}"
info "Frontend: ${FRONTEND_URL}"
info "Anon ID: ${ANON_ID}"
info "Verbose: ${VERBOSE}"

# ==============================================================================
# LAYER 1: Infrastructure Health
# ==============================================================================

header "1: Infrastructure Health"

# Backend health endpoint
T_START=$(now_ms)
HEALTH_RESP=$(curl -s -w "\n%{http_code}" --max-time 10 \
    "${BASE_URL}/api/v1/health" 2>/dev/null)
T_END=$(now_ms)
LAT_HEALTH=$((T_END - T_START))

HEALTH_BODY=$(echo "$HEALTH_RESP" | sed '$d')
HEALTH_CODE=$(echo "$HEALTH_RESP" | tail -1)

if [[ "$HEALTH_CODE" == "200" ]]; then
    pass "Backend health check (HTTP 200, ${LAT_HEALTH}ms)"
else
    fail_critical "Backend health check failed (HTTP ${HEALTH_CODE})"
    info "Cannot proceed without a healthy backend"
    # Still continue to report what we can
fi

# Check health response body
if echo "$HEALTH_BODY" | jq -e '.status' > /dev/null 2>&1; then
    HEALTH_STATUS=$(echo "$HEALTH_BODY" | jq -r '.status')
    pass "Health status field present: ${HEALTH_STATUS}"
else
    warn "Health response missing 'status' field"
fi

verbose "Health response: ${HEALTH_BODY}"

# Frontend loads
T_START=$(now_ms)
FE_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    "${FRONTEND_URL}/" 2>/dev/null)
T_END=$(now_ms)
FE_LATENCY=$((T_END - T_START))

if [[ "$FE_CODE" == "200" ]]; then
    pass "Frontend loads (HTTP 200, ${FE_LATENCY}ms)"
else
    fail "Frontend returned HTTP ${FE_CODE}"
fi


# ==============================================================================
# LAYER 2: Non-streaming Chat -- Generic Query Path
# ==============================================================================

header "2: Non-streaming Chat - Generic Query (LLM-only path)"

T_START=$(now_ms)
GENERIC_RESP=$(curl -s -w "\n%{http_code}" --max-time 15 \
    -X POST "${BASE_URL}/api/v1/chat/" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d '{"message": "hello", "lang": "en"}' 2>/dev/null)
T_END=$(now_ms)
LAT_GENERIC=$((T_END - T_START))

GENERIC_BODY=$(echo "$GENERIC_RESP" | sed '$d')
GENERIC_CODE=$(echo "$GENERIC_RESP" | tail -1)

if [[ "$GENERIC_CODE" == "200" ]]; then
    pass "Generic query HTTP 200 (${LAT_GENERIC}ms)"
elif [[ "$GENERIC_CODE" == "429" ]]; then
    warn "Generic query rate-limited (HTTP 429) - anon quota exhausted"
else
    fail "Generic query returned HTTP ${GENERIC_CODE}"
fi

verbose "Generic response: ${GENERIC_BODY}"

# Validate response JSON fields
if [[ "$GENERIC_CODE" == "200" ]]; then
    # response field
    if echo "$GENERIC_BODY" | jq -e '.response' > /dev/null 2>&1; then
        RESP_TEXT=$(echo "$GENERIC_BODY" | jq -r '.response')
        if [[ ${#RESP_TEXT} -gt 0 ]]; then
            pass "Response field present (${#RESP_TEXT} chars)"
            info "Preview: ${RESP_TEXT:0:80}..."
        else
            fail "Response field is empty string"
        fi
    else
        fail "Response JSON missing 'response' field"
    fi

    # model_used field
    if echo "$GENERIC_BODY" | jq -e '.model_used' > /dev/null 2>&1; then
        MODEL=$(echo "$GENERIC_BODY" | jq -r '.model_used')
        pass "model_used present: ${MODEL}"
    else
        fail "Response missing 'model_used' field"
    fi

    # latency_ms field
    if echo "$GENERIC_BODY" | jq -e '.latency_ms' > /dev/null 2>&1; then
        BACKEND_LAT=$(echo "$GENERIC_BODY" | jq -r '.latency_ms')
        pass "latency_ms present: ${BACKEND_LAT}ms"
    else
        warn "Response missing 'latency_ms' field"
    fi

    # sources field (should be empty for generic queries)
    if echo "$GENERIC_BODY" | jq -e '.sources' > /dev/null 2>&1; then
        SOURCES_LEN=$(echo "$GENERIC_BODY" | jq '.sources | length')
        if [[ "$SOURCES_LEN" == "0" ]]; then
            pass "sources is empty (generic query skips RAG)"
        else
            warn "sources has ${SOURCES_LEN} entries for generic query (unexpected)"
        fi
    else
        pass "No sources field (generic query, no RAG)"
    fi

    # Latency budget
    if [[ $LAT_GENERIC -le $BUDGET_GENERIC ]]; then
        pass "Latency within budget (${LAT_GENERIC}ms <= ${BUDGET_GENERIC}ms)"
    else
        warn "Latency over budget (${LAT_GENERIC}ms > ${BUDGET_GENERIC}ms)"
    fi
fi


# ==============================================================================
# LAYER 3: Non-streaming Chat -- Topic Embedding Match Path
# ==============================================================================

header "3: Non-streaming Chat - Topic Query (embedding match + RAG)"

T_START=$(now_ms)
TOPIC_RESP=$(curl -s -w "\n%{http_code}" --max-time 20 \
    -X POST "${BASE_URL}/api/v1/chat/" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d '{"message": "What is photosynthesis and how does it work in plants?", "lang": "en"}' 2>/dev/null)
T_END=$(now_ms)
LAT_TOPIC=$((T_END - T_START))

TOPIC_BODY=$(echo "$TOPIC_RESP" | sed '$d')
TOPIC_CODE=$(echo "$TOPIC_RESP" | tail -1)

if [[ "$TOPIC_CODE" == "200" ]]; then
    pass "Topic query HTTP 200 (${LAT_TOPIC}ms)"
elif [[ "$TOPIC_CODE" == "429" ]]; then
    warn "Topic query rate-limited (HTTP 429)"
else
    fail "Topic query returned HTTP ${TOPIC_CODE}"
fi

verbose "Topic response: ${TOPIC_BODY}"

if [[ "$TOPIC_CODE" == "200" ]]; then
    # Verify response content
    if echo "$TOPIC_BODY" | jq -e '.response' > /dev/null 2>&1; then
        TOPIC_TEXT=$(echo "$TOPIC_BODY" | jq -r '.response')
        if [[ ${#TOPIC_TEXT} -gt 0 ]]; then
            pass "Topic response has content (${#TOPIC_TEXT} chars)"
            info "Preview: ${TOPIC_TEXT:0:100}..."
        else
            fail "Topic response is empty"
        fi
    else
        fail "Topic response missing 'response' field"
    fi

    # Check sources (RAG)
    SOURCES_COUNT=$(echo "$TOPIC_BODY" | jq '.sources | length' 2>/dev/null || echo "0")
    if [[ "$SOURCES_COUNT" -gt 0 ]]; then
        pass "RAG returned ${SOURCES_COUNT} sources (topic matched, score >= 0.70)"
        # Show first source
        FIRST_SOURCE=$(echo "$TOPIC_BODY" | jq -r '.sources[0] // "N/A"' 2>/dev/null)
        info "First source: ${FIRST_SOURCE:0:120}"
    else
        warn "No RAG sources - topic embedding backfill may not have run yet"
        info "This is expected before first deployment with TopicEmbeddings populated"
    fi

    # Latency comparison
    if [[ $LAT_TOPIC -le $BUDGET_TOPIC ]]; then
        pass "Topic latency within budget (${LAT_TOPIC}ms <= ${BUDGET_TOPIC}ms)"
    else
        warn "Topic latency over budget (${LAT_TOPIC}ms > ${BUDGET_TOPIC}ms)"
    fi

    # Compare to generic path
    if [[ $LAT_GENERIC -gt 0 ]]; then
        OVERHEAD=$((LAT_TOPIC - LAT_GENERIC))
        info "RAG overhead vs generic: +${OVERHEAD}ms"
    fi
fi


# ==============================================================================
# LAYER 4: SSE Streaming -- CRITICAL DISPLAY TEST
# ==============================================================================

header "4: SSE Streaming - CRITICAL DISPLAY TEST"

STREAM_FILE=$(mktemp)
FIRST_BYTE_FILE=$(mktemp)
trap "rm -f $STREAM_FILE $FIRST_BYTE_FILE" EXIT

T_STREAM_START=$(now_ms)

# Run curl in background to capture SSE stream
curl -s -N --max-time 20 \
    -X POST "${BASE_URL}/api/v1/chat/stream" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d '{"message": "explain the water cycle", "lang": "en"}' \
    > "$STREAM_FILE" 2>/dev/null &
STREAM_PID=$!

# Measure time to first byte by polling file
FIRST_BYTE_DETECTED=0
for i in $(seq 1 200); do
    if [[ -s "$STREAM_FILE" ]]; then
        T_FIRST_BYTE=$(now_ms)
        LAT_STREAM_FIRST_BYTE=$((T_FIRST_BYTE - T_STREAM_START))
        FIRST_BYTE_DETECTED=1
        break
    fi
    sleep 0.1
done

# Wait for stream to complete
wait $STREAM_PID 2>/dev/null || true
T_STREAM_END=$(now_ms)
LAT_STREAM_COMPLETE=$((T_STREAM_END - T_STREAM_START))

STREAM_LINES=$(wc -l < "$STREAM_FILE" | tr -d ' ')

if [[ $STREAM_LINES -gt 0 ]]; then
    pass "Stream returned ${STREAM_LINES} lines (${LAT_STREAM_COMPLETE}ms total)"
else
    fail_critical "Stream returned 0 lines - streaming endpoint broken"
fi

# Count SSE data lines
DATA_LINES=$(grep -c "^data: " "$STREAM_FILE" 2>/dev/null | tr -d '[:space:]')
DATA_LINES=${DATA_LINES:-0}
if [[ $DATA_LINES -gt 0 ]]; then
    pass "Found ${DATA_LINES} SSE data: lines"
else
    fail_critical "No SSE 'data: ' lines found in stream"
fi

verbose "First 5 SSE lines:"
if [[ "$VERBOSE" == "1" ]]; then
    grep "^data: " "$STREAM_FILE" | head -5 | while read -r line; do
        verbose "  $line"
    done
fi

# ==========================================================
# CRITICAL: Verify 'content' field present
# This is what ChatPage.jsx reads to display text on screen
# ==========================================================

CONTENT_HITS=$(grep "^data: " "$STREAM_FILE" | grep -c '"content"' 2>/dev/null | tr -d '[:space:]')
CONTENT_HITS=${CONTENT_HITS:-0}

if [[ $CONTENT_HITS -gt 0 ]]; then
    pass "SSE uses 'content' field (${CONTENT_HITS} chunks) - FRONTEND WILL RENDER"
else
    fail_critical "SSE MISSING 'content' field - FRONTEND CANNOT DISPLAY RESPONSE"
    info "ChatPage.jsx reads parsed.content - without it, user sees nothing"
fi

# Verify NO legacy 'text' field (the broken format from before PR#324)
TEXT_HITS=$(grep "^data: " "$STREAM_FILE" | grep -c '"text"' 2>/dev/null | tr -d '[:space:]')
TEXT_HITS=${TEXT_HITS:-0}
if [[ $TEXT_HITS -eq 0 ]]; then
    pass "No legacy 'text' field in SSE (PR#324 fix confirmed)"
else
    warn "SSE still has 'text' field (${TEXT_HITS} hits) - possible regression from PR#324"
fi

# Count content chunks (non-empty)
CONTENT_CHUNKS=$(grep "^data: " "$STREAM_FILE" | sed 's/^data: //' | \
    jq -r 'select(.content != null and .content != "" and .done != true) | .content' 2>/dev/null | wc -l | tr -d ' ')
if [[ $CONTENT_CHUNKS -gt 0 ]]; then
    pass "Received ${CONTENT_CHUNKS} non-empty content chunks"
else
    fail "No non-empty content chunks found"
fi

# Reconstruct full response from chunks
FULL_RESPONSE=$(grep "^data: " "$STREAM_FILE" | sed 's/^data: //' | \
    jq -r 'select(.content != null and .content != "") | .content' 2>/dev/null | tr -d '\n')
FULL_LEN=${#FULL_RESPONSE}

if [[ $FULL_LEN -gt 0 ]]; then
    pass "Reconstructed full response: ${FULL_LEN} chars"
    info "Preview: ${FULL_RESPONSE:0:100}..."
else
    fail_critical "Could not reconstruct response from stream (empty content)"
fi

# Verify syrabit_done event
if grep -q "syrabit_done" "$STREAM_FILE" 2>/dev/null; then
    pass "syrabit_done final event present"

    DONE_LINE=$(grep "syrabit_done" "$STREAM_FILE" | head -1 | sed 's/^data: //')
    DONE_MODEL=$(echo "$DONE_LINE" | jq -r '.model // "unknown"' 2>/dev/null)
    DONE_LANG=$(echo "$DONE_LINE" | jq -r '.lang // "unknown"' 2>/dev/null)
    DONE_LATENCY=$(echo "$DONE_LINE" | jq -r '.latency_ms // "?"' 2>/dev/null)

    if [[ "$DONE_MODEL" != "unknown" && "$DONE_MODEL" != "null" ]]; then
        pass "Done event has model: ${DONE_MODEL}"
    else
        warn "Done event missing model field"
    fi

    if [[ "$DONE_LANG" != "unknown" && "$DONE_LANG" != "null" ]]; then
        pass "Done event has lang: ${DONE_LANG}"
    else
        warn "Done event missing lang field"
    fi

    if [[ "$DONE_LATENCY" != "?" && "$DONE_LATENCY" != "null" ]]; then
        pass "Done event has latency_ms: ${DONE_LATENCY}ms"
    else
        warn "Done event missing latency_ms field"
    fi
else
    fail_critical "Stream MISSING syrabit_done event - frontend will not know stream ended"
fi

# Time to first byte
if [[ $FIRST_BYTE_DETECTED -eq 1 ]]; then
    if [[ $LAT_STREAM_FIRST_BYTE -le $BUDGET_STREAM_FIRST_BYTE ]]; then
        pass "Time-to-first-byte: ${LAT_STREAM_FIRST_BYTE}ms (budget: ${BUDGET_STREAM_FIRST_BYTE}ms)"
    else
        warn "Time-to-first-byte slow: ${LAT_STREAM_FIRST_BYTE}ms (budget: ${BUDGET_STREAM_FIRST_BYTE}ms)"
    fi
else
    warn "Could not measure time-to-first-byte (file polling timeout)"
    LAT_STREAM_FIRST_BYTE=0
fi

# Time to complete
if [[ $LAT_STREAM_COMPLETE -le $BUDGET_STREAM_COMPLETE ]]; then
    pass "Time-to-complete: ${LAT_STREAM_COMPLETE}ms (budget: ${BUDGET_STREAM_COMPLETE}ms)"
else
    warn "Stream completion slow: ${LAT_STREAM_COMPLETE}ms (budget: ${BUDGET_STREAM_COMPLETE}ms)"
fi

rm -f "$STREAM_FILE"


# ==============================================================================
# LAYER 5: SSE Streaming -- Topic Query with RAG
# ==============================================================================

header "5: SSE Streaming - Topic Query with RAG"

STREAM2_FILE=$(mktemp)

curl -s -N --max-time 25 \
    -X POST "${BASE_URL}/api/v1/chat/stream" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d '{"message": "Explain Newton'\''s laws of motion", "lang": "en"}' \
    > "$STREAM2_FILE" 2>/dev/null &
STREAM2_PID=$!
wait $STREAM2_PID 2>/dev/null || true

STREAM2_DATA=$(grep -c "^data: " "$STREAM2_FILE" 2>/dev/null | tr -d '[:space:]')
STREAM2_DATA=${STREAM2_DATA:-0}

if [[ $STREAM2_DATA -gt 0 ]]; then
    pass "Topic stream: ${STREAM2_DATA} SSE data lines"
else
    fail "Topic stream returned no data lines"
fi

# Validate content field in topic stream too
STREAM2_CONTENT=$(grep "^data: " "$STREAM2_FILE" | grep -c '"content"' 2>/dev/null | tr -d '[:space:]')
STREAM2_CONTENT=${STREAM2_CONTENT:-0}
if [[ $STREAM2_CONTENT -gt 0 ]]; then
    pass "Topic stream uses 'content' field (${STREAM2_CONTENT} chunks)"
else
    fail "Topic stream missing 'content' field"
fi

# Reconstruct topic stream response
TOPIC_STREAM_FULL=$(grep "^data: " "$STREAM2_FILE" | sed 's/^data: //' | \
    jq -r 'select(.content != null and .content != "") | .content' 2>/dev/null | tr -d '\n')
TOPIC_STREAM_LEN=${#TOPIC_STREAM_FULL}

if [[ $TOPIC_STREAM_LEN -gt 0 ]]; then
    pass "Topic stream response: ${TOPIC_STREAM_LEN} chars"
    info "Preview: ${TOPIC_STREAM_FULL:0:100}..."
else
    fail "Topic stream produced no content"
fi

# Check for syrabit_done
if grep -q "syrabit_done" "$STREAM2_FILE" 2>/dev/null; then
    pass "Topic stream has syrabit_done event"
else
    fail "Topic stream missing syrabit_done event"
fi

# Check for RAG metadata in stream (sources or context in done event)
DONE2_LINE=$(grep "syrabit_done" "$STREAM2_FILE" 2>/dev/null | head -1 | sed 's/^data: //')
if echo "$DONE2_LINE" | jq -e '.sources' > /dev/null 2>&1; then
    RAG_SOURCES=$(echo "$DONE2_LINE" | jq '.sources | length' 2>/dev/null || echo "0")
    if [[ "$RAG_SOURCES" -gt 0 ]]; then
        pass "RAG metadata in stream: ${RAG_SOURCES} sources"
    else
        info "No RAG sources in done event (topic embedding backfill may be pending)"
    fi
else
    info "No sources field in done event (RAG metadata may be in response body only)"
fi

# Check for errors
ERROR_COUNT=$(grep "^data: " "$STREAM2_FILE" | grep -c '"error"' 2>/dev/null | tr -d '[:space:]')
ERROR_COUNT=${ERROR_COUNT:-0}
if [[ $ERROR_COUNT -eq 0 ]]; then
    pass "No error events in topic stream"
else
    warn "Topic stream had ${ERROR_COUNT} error event(s)"
    verbose "$(grep '"error"' "$STREAM2_FILE" | head -2)"
fi

rm -f "$STREAM2_FILE"


# ==============================================================================
# LAYER 6: Assamese Language Path
# ==============================================================================

header "6: Assamese Language Path (Sarvam AI / Vertex fallback)"

T_START=$(now_ms)
AS_RESP=$(curl -s -w "\n%{http_code}" --max-time 25 \
    -X POST "${BASE_URL}/api/v1/chat/" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d '{"message": "পোহৰ সংশ্লেষণ কি?", "lang": "as"}' 2>/dev/null)
T_END=$(now_ms)
LAT_ASSAMESE=$((T_END - T_START))

AS_BODY=$(echo "$AS_RESP" | sed '$d')
AS_CODE=$(echo "$AS_RESP" | tail -1)

if [[ "$AS_CODE" == "200" ]]; then
    pass "Assamese chat HTTP 200 (${LAT_ASSAMESE}ms)"

    AS_TEXT=$(echo "$AS_BODY" | jq -r '.response // ""' 2>/dev/null)
    AS_MODEL=$(echo "$AS_BODY" | jq -r '.model_used // "unknown"' 2>/dev/null)

    if [[ ${#AS_TEXT} -gt 0 ]]; then
        pass "Assamese response is non-empty (${#AS_TEXT} chars)"
        info "Model used: ${AS_MODEL}"
        info "Preview: ${AS_TEXT:0:80}..."

        # Note which model was used
        if echo "$AS_MODEL" | grep -qi "sarvam"; then
            info "Routed to Sarvam AI (primary Assamese model)"
        elif echo "$AS_MODEL" | grep -qi "gemini\|vertex"; then
            info "Routed to Vertex AI Gemini (fallback for Assamese)"
        else
            info "Model: ${AS_MODEL}"
        fi
    else
        fail "Assamese response is empty"
    fi

    # Latency check
    if [[ $LAT_ASSAMESE -le $BUDGET_ASSAMESE ]]; then
        pass "Assamese latency within budget (${LAT_ASSAMESE}ms <= ${BUDGET_ASSAMESE}ms)"
    else
        warn "Assamese latency over budget (${LAT_ASSAMESE}ms > ${BUDGET_ASSAMESE}ms)"
    fi
elif [[ "$AS_CODE" == "429" ]]; then
    warn "Assamese chat rate-limited (HTTP 429)"
elif [[ "$AS_CODE" == "503" ]]; then
    warn "Assamese chat unavailable (HTTP 503) - Sarvam + Vertex both down"
else
    fail "Assamese chat returned HTTP ${AS_CODE}"
fi

verbose "Assamese body: ${AS_BODY}"


# ==============================================================================
# LAYER 7: Input Validation & Security
# ==============================================================================

header "7: Input Validation & Security"

# Test 1: Empty message -> 422
EMPTY_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -X POST "${BASE_URL}/api/v1/chat/" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d '{"message": ""}' 2>/dev/null)

if [[ "$EMPTY_CODE" == "422" ]]; then
    pass "Empty message rejected (HTTP 422)"
else
    fail "Empty message not rejected (HTTP ${EMPTY_CODE}, expected 422)"
fi

# Test 2: Oversize message (2001 chars) -> 422
LONG_MSG=$(printf 'x%.0s' $(seq 1 2001))
LONG_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -X POST "${BASE_URL}/api/v1/chat/" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d "{\"message\": \"${LONG_MSG}\"}" 2>/dev/null)

if [[ "$LONG_CODE" == "422" ]]; then
    pass "Oversize message (2001 chars) rejected (HTTP 422)"
else
    fail "Oversize message not rejected (HTTP ${LONG_CODE}, expected 422)"
fi

# Test 3: Path traversal in session_id -> 422
TRAVERSAL_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -X POST "${BASE_URL}/api/v1/chat/" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d '{"message": "test", "session_id": "../../../etc/passwd"}' 2>/dev/null)

if [[ "$TRAVERSAL_CODE" == "422" ]]; then
    pass "Path traversal in session_id rejected (HTTP 422)"
else
    warn "Path traversal session_id returned HTTP ${TRAVERSAL_CODE} (expected 422)"
fi

# Test 4: Valid message length (2000 chars exactly) -> should NOT 422
VALID_MSG=$(printf 'a%.0s' $(seq 1 2000))
VALID_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 \
    -X POST "${BASE_URL}/api/v1/chat/" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d "{\"message\": \"${VALID_MSG}\", \"lang\": \"en\"}" 2>/dev/null)

if [[ "$VALID_CODE" != "422" ]]; then
    pass "Valid 2000-char message not rejected (HTTP ${VALID_CODE})"
else
    fail "Valid 2000-char message incorrectly rejected (HTTP 422)"
fi


# ==============================================================================
# LAYER 8: Frontend Integration Checks
# ==============================================================================

header "8: Frontend Integration Checks"

# /login serves 200 (not 308 redirect)
LOGIN_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -L --max-redirs 5 \
    "${FRONTEND_URL}/login" 2>/dev/null)

if [[ "$LOGIN_CODE" == "200" ]]; then
    pass "/login serves 200 (no 308 redirect)"
else
    warn "/login returned HTTP ${LOGIN_CODE} (expected 200, may need frontend redeploy)"
fi

# /about serves 200
ABOUT_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -L --max-redirs 5 \
    "${FRONTEND_URL}/about" 2>/dev/null)

if [[ "$ABOUT_CODE" == "200" ]]; then
    pass "/about serves 200"
else
    warn "/about returned HTTP ${ABOUT_CODE} (expected 200, may need frontend redeploy)"
fi

# /privacy serves 200
PRIVACY_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -L --max-redirs 5 \
    "${FRONTEND_URL}/privacy" 2>/dev/null)

if [[ "$PRIVACY_CODE" == "200" ]]; then
    pass "/privacy serves 200"
else
    warn "/privacy returned HTTP ${PRIVACY_CODE} (expected 200, may need frontend redeploy)"
fi

# Cookie consent: check frontend HTML for CookieConsent reference
FE_HTML=$(curl -s --max-time 10 "${FRONTEND_URL}/" 2>/dev/null)

if echo "$FE_HTML" | grep -qi "CookieConsent\|cookie.consent\|cookie_consent"; then
    pass "Frontend HTML references CookieConsent component"
else
    # Check if it is lazy-loaded via JS bundle reference
    if echo "$FE_HTML" | grep -qi "consent\|cookie"; then
        pass "Frontend HTML has cookie/consent reference (lazy import)"
    else
        warn "No CookieConsent reference found in frontend HTML (may be in JS bundle)"
    fi
fi

# Check built JS for syrabit_cookie_consent key
# Get main JS bundle URL from HTML
JS_BUNDLE=$(echo "$FE_HTML" | grep -oP 'src="(/assets/[^"]+\.js)"' | head -1 | sed 's/src="//;s/"//')

if [[ -n "$JS_BUNDLE" ]]; then
    JS_URL="${FRONTEND_URL}${JS_BUNDLE}"
    JS_CONTENT=$(curl -s --max-time 10 "$JS_URL" 2>/dev/null | head -c 500000)

    if echo "$JS_CONTENT" | grep -q "syrabit_cookie_consent"; then
        pass "Built JS contains 'syrabit_cookie_consent' key"
    else
        warn "syrabit_cookie_consent key not found in main JS bundle"
        info "May be in a chunk-loaded file"
    fi
else
    info "Could not extract JS bundle URL from HTML (SPA may load differently)"
fi


# ==============================================================================
# LAYER 9: Sitemap & SEO
# ==============================================================================

header "9: Sitemap & SEO"

# Sitemap index
SITEMAP_RESP=$(curl -s --max-time 10 "${BASE_URL}/api/v1/seo/sitemap.xml" 2>/dev/null)
SITEMAP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "${BASE_URL}/api/v1/seo/sitemap.xml" 2>/dev/null)

if [[ "$SITEMAP_CODE" == "200" ]]; then
    pass "Sitemap index loads (HTTP 200)"

    LASTMOD_COUNT=$(echo "$SITEMAP_RESP" | grep -c "<lastmod>" 2>/dev/null | tr -d '[:space:]')
    LASTMOD_COUNT=${LASTMOD_COUNT:-0}
    if [[ $LASTMOD_COUNT -gt 0 ]]; then
        pass "Sitemap index has ${LASTMOD_COUNT} <lastmod> entries"
    else
        fail "Sitemap index missing <lastmod> elements"
    fi
else
    fail "Sitemap index returned HTTP ${SITEMAP_CODE}"
fi

# Static sitemap
STATIC_RESP=$(curl -s --max-time 10 "${BASE_URL}/api/v1/seo/sitemap-static.xml" 2>/dev/null)
STATIC_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "${BASE_URL}/api/v1/seo/sitemap-static.xml" 2>/dev/null)

if [[ "$STATIC_CODE" == "200" ]]; then
    pass "Static sitemap loads (HTTP 200)"

    STATIC_LASTMOD=$(echo "$STATIC_RESP" | grep -c "<lastmod>" 2>/dev/null | tr -d '[:space:]')
    STATIC_LASTMOD=${STATIC_LASTMOD:-0}
    if [[ $STATIC_LASTMOD -gt 0 ]]; then
        pass "Static sitemap has ${STATIC_LASTMOD} <lastmod> entries"
    else
        fail "Static sitemap missing <lastmod> elements"
    fi

    # Check required static URLs present
    REQUIRED_URLS=("/" "/library" "/chat" "/pricing" "/about")
    for url_path in "${REQUIRED_URLS[@]}"; do
        if echo "$STATIC_RESP" | grep -q "${url_path}"; then
            pass "Static sitemap contains ${url_path}"
        else
            warn "Static sitemap missing ${url_path}"
        fi
    done
else
    fail "Static sitemap returned HTTP ${STATIC_CODE}"
fi


# ==============================================================================
# LAYER 10: Chat History
# ==============================================================================

header "10: Chat History"

HISTORY_RESP=$(curl -s -w "\n%{http_code}" --max-time 10 \
    -H "x-anon-id: ${ANON_ID}" \
    "${BASE_URL}/api/v1/chat/history" 2>/dev/null)

HISTORY_BODY=$(echo "$HISTORY_RESP" | sed '$d')
HISTORY_CODE=$(echo "$HISTORY_RESP" | tail -1)

if [[ "$HISTORY_CODE" == "200" ]]; then
    pass "Chat history endpoint accessible (HTTP 200)"

    # Check for proper structure
    if echo "$HISTORY_BODY" | jq -e '.chats' > /dev/null 2>&1; then
        CHAT_COUNT=$(echo "$HISTORY_BODY" | jq '.chats | length' 2>/dev/null || echo "0")
        pass "History has 'chats' array (${CHAT_COUNT} entries)"
    else
        # Might have different field name
        if echo "$HISTORY_BODY" | jq -e '.conversations' > /dev/null 2>&1; then
            pass "History has 'conversations' array"
        else
            fail "History response missing 'chats' or 'conversations' field"
        fi
    fi

    # Check pagination fields
    HAS_PAGINATION=0
    if echo "$HISTORY_BODY" | jq -e '.pagination' > /dev/null 2>&1; then
        HAS_PAGINATION=1
        pass "Pagination field present"
    elif echo "$HISTORY_BODY" | jq -e '.total' > /dev/null 2>&1; then
        HAS_PAGINATION=1
        pass "Total count field present (pagination support)"
    elif echo "$HISTORY_BODY" | jq -e '.page' > /dev/null 2>&1; then
        HAS_PAGINATION=1
        pass "Page field present (pagination support)"
    else
        warn "No pagination fields detected in history response"
    fi
elif [[ "$HISTORY_CODE" == "401" ]]; then
    warn "History requires auth (HTTP 401) - anon access not available"
else
    fail "Chat history returned HTTP ${HISTORY_CODE}"
fi

verbose "History body: ${HISTORY_BODY:0:500}"


# ==============================================================================
# LAYER 11: Performance Summary
# ==============================================================================

header "11: Performance Summary"

# Color-coded latency display
latency_color() {
    local val=$1
    local budget=$2
    local marginal=$(( budget * 80 / 100 ))

    if [[ $val -eq 0 ]]; then
        printf "${DIM}N/A${NC}"
    elif [[ $val -le $marginal ]]; then
        printf "${GREEN}%dms${NC}" "$val"
    elif [[ $val -le $budget ]]; then
        printf "${YELLOW}%dms${NC}" "$val"
    else
        printf "${RED}%dms${NC}" "$val"
    fi
}

echo ""
printf "  ${BOLD}%-35s %-15s %-15s${NC}\n" "Metric" "Latency" "Budget"
printf "  %-35s %-15s %-15s\n" "-----------------------------------" "---------------" "---------------"
printf "  %-35s " "Health check"
latency_color $LAT_HEALTH $BUDGET_HEALTH
printf "           %dms\n" $BUDGET_HEALTH
printf "  %-35s " "Generic chat (non-stream)"
latency_color $LAT_GENERIC $BUDGET_GENERIC
printf "           %dms\n" $BUDGET_GENERIC
printf "  %-35s " "Topic chat (non-stream)"
latency_color $LAT_TOPIC $BUDGET_TOPIC
printf "           %dms\n" $BUDGET_TOPIC
printf "  %-35s " "Stream time-to-first-byte"
latency_color $LAT_STREAM_FIRST_BYTE $BUDGET_STREAM_FIRST_BYTE
printf "           %dms\n" $BUDGET_STREAM_FIRST_BYTE
printf "  %-35s " "Stream time-to-complete"
latency_color $LAT_STREAM_COMPLETE $BUDGET_STREAM_COMPLETE
printf "           %dms\n" $BUDGET_STREAM_COMPLETE
printf "  %-35s " "Assamese chat"
latency_color $LAT_ASSAMESE $BUDGET_ASSAMESE
printf "           %dms\n" $BUDGET_ASSAMESE
echo ""

# Legend
printf "  ${DIM}Legend: ${GREEN}green${NC}${DIM}=within 80%% budget, ${YELLOW}yellow${NC}${DIM}=marginal, ${RED}red${NC}${DIM}=over budget${NC}\n"


# ==============================================================================
# FINAL SUMMARY
# ==============================================================================

echo ""
printf "${BOLD}=================================================================================${NC}\n"
printf "${BOLD}  CHAT PIPELINE TEST RESULTS${NC}\n"
printf "${BOLD}=================================================================================${NC}\n"
echo ""
printf "  Total tests:  ${BOLD}%d${NC}\n" $TOTAL
printf "  Passed:       ${GREEN}%d${NC}\n" $PASSED
printf "  Failed:       ${RED}%d${NC}\n" $FAILED
printf "  Warnings:     ${YELLOW}%d${NC}\n" $WARNINGS
echo ""
printf "  Backend:      %s\n" "$BASE_URL"
printf "  Frontend:     %s\n" "$FRONTEND_URL"
echo ""

# CRITICAL CHECK: Response displays on screen
echo ""
printf "${BOLD}  CRITICAL CHECK: Response displays on screen${NC}\n"
printf "  ─────────────────────────────────────────────\n"

if [[ $CONTENT_HITS -gt 0 && $FULL_LEN -gt 0 ]]; then
    printf "  ${GREEN}✓ VERIFIED${NC}: SSE 'content' field present and non-empty\n"
    printf "  ${GREEN}✓ VERIFIED${NC}: Frontend ChatPage.jsx CAN render the response\n"
    printf "  ${GREEN}✓ VERIFIED${NC}: User WILL see AI text on screen\n"
    DISPLAY_OK=1
else
    printf "  ${RED}✗ FAILED${NC}: SSE 'content' field missing or empty\n"
    printf "  ${RED}✗ FAILED${NC}: Frontend ChatPage.jsx CANNOT render response\n"
    printf "  ${RED}✗ FAILED${NC}: User will see BLANK screen\n"
    DISPLAY_OK=0
fi

echo ""

# Deployment readiness verdict
printf "${BOLD}  DEPLOYMENT READINESS${NC}\n"
printf "  ─────────────────────\n"

if [[ $CRITICAL_FAIL -eq 0 && ${DISPLAY_OK:-0} -eq 1 ]]; then
    printf "  ${GREEN}${BOLD}READY TO DEPLOY${NC}\n"
    printf "  All critical checks passed. Chat pipeline is functional.\n"
    printf "  - SSE content field: present\n"
    printf "  - Response rendering: verified\n"
    printf "  - syrabit_done event: confirmed\n"
    printf "  - Input validation: enforced\n"
elif [[ $CRITICAL_FAIL -eq 0 ]]; then
    printf "  ${YELLOW}${BOLD}DEPLOY WITH CAUTION${NC}\n"
    printf "  No critical failures but display verification incomplete.\n"
else
    printf "  ${RED}${BOLD}DO NOT DEPLOY${NC}\n"
    printf "  ${CRITICAL_FAIL} critical failure(s) detected.\n"
    printf "  Fix critical issues before deployment.\n"
fi

echo ""
printf "${BOLD}=================================================================================${NC}\n"

# Exit code
if [[ $CRITICAL_FAIL -gt 0 ]]; then
    exit 1
elif [[ $FAILED -gt 0 ]]; then
    # Non-critical failures: still exit 1
    exit 1
else
    exit 0
fi

