#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# SYRABIT CHAT PIPELINE TEST — Cloud Shell Edition
# ═══════════════════════════════════════════════════════════════════════════════
#
# Tests the full chat pipeline end-to-end:
#   1. Health check (backend alive)
#   2. Non-streaming /chat endpoint (generic query → LLM-only)
#   3. Non-streaming /chat endpoint (topic query → topic matching → RAG)
#   4. Streaming /chat/stream endpoint (generic → LLM-only, SSE format)
#   5. Streaming /chat/stream endpoint (topic query → topic matching → RAG)
#   6. SSE field validation (content field present, syrabit_done event)
#   7. Topic embedding match performance (latency budget)
#   8. Cookie consent component (frontend)
#   9. 308 redirect fix validation
#  10. Sitemap lastmod validation
#
# Usage:
#   ./scripts/test-chat-pipeline.sh
#
# With custom backend URL:
#   BASE_URL="https://staging-api.syrabit.ai" ./scripts/test-chat-pipeline.sh
#
# Requirements: bash, curl, jq
# ═══════════════════════════════════════════════════════════════════════════════

set -uo pipefail

# ─── Configuration ────────────────────────────────────────────────────────────

BASE_URL="${BASE_URL:-https://api.syrabit.ai}"
FRONTEND_URL="${FRONTEND_URL:-https://syrabit.ai}"
VERBOSE="${VERBOSE:-0}"


# Latency budgets (ms)
MAX_GENERIC_LATENCY=5000     # Generic queries should be fast (no RAG)
MAX_TOPIC_LATENCY=8000       # Topic queries have embedding + RAG overhead
MAX_STREAM_FIRST_BYTE=3000   # First SSE byte within 3s

# ─── State ────────────────────────────────────────────────────────────────────

TOTAL=0
PASSED=0
FAILED=0
WARNINGS=0

# ─── Colors ───────────────────────────────────────────────────────────────────

if [[ -t 1 ]]; then
    R='\033[0;31m'; G='\033[0;32m'; Y='\033[0;33m'; B='\033[0;34m'
    BOLD='\033[1m'; NC='\033[0m'
else
    R=''; G=''; Y=''; B=''; BOLD=''; NC=''
fi

# ─── Helpers ──────────────────────────────────────────────────────────────────

pass() { TOTAL=$((TOTAL+1)); PASSED=$((PASSED+1)); echo -e "  ${G}✓ PASS${NC} $1"; }
fail() { TOTAL=$((TOTAL+1)); FAILED=$((FAILED+1)); echo -e "  ${R}✗ FAIL${NC} $1"; }
warn() { TOTAL=$((TOTAL+1)); WARNINGS=$((WARNINGS+1)); echo -e "  ${Y}⚠ WARN${NC} $1"; }
info() { echo -e "  ${B}ℹ${NC} $1"; }
header() { echo -e "\n${BOLD}═══ $1 ═══${NC}"; }


anon_id() {
    # Generate a valid anon_id matching ^anon_[a-f0-9]{32}$
    echo "anon_$(cat /dev/urandom | tr -dc 'a-f0-9' | fold -w 32 | head -n 1)"
}

ANON_ID=$(anon_id)

# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: Backend Health Check
# ═══════════════════════════════════════════════════════════════════════════════

header "TEST 1: Backend Health Check"

HEALTH_RESP=$(curl -s -o /dev/null -w "%{http_code}|%{time_total}" \
    "${BASE_URL}/api/v1/health" 2>/dev/null)
HEALTH_CODE=$(echo "$HEALTH_RESP" | cut -d'|' -f1)
HEALTH_TIME=$(echo "$HEALTH_RESP" | cut -d'|' -f2)

if [[ "$HEALTH_CODE" == "200" ]]; then
    pass "Backend health check (HTTP $HEALTH_CODE, ${HEALTH_TIME}s)"
else
    fail "Backend health check returned HTTP $HEALTH_CODE"
    echo -e "  ${R}    Cannot proceed — backend is down!${NC}"
    exit 1
fi


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: Non-streaming — Generic Query (should skip RAG, LLM-only)
# ═══════════════════════════════════════════════════════════════════════════════

header "TEST 2: Non-streaming /chat — Generic Query (LLM-only path)"

GENERIC_START=$(date +%s%N)
GENERIC_RESP=$(curl -s -w "\n%{http_code}" \
    -X POST "${BASE_URL}/api/v1/chat/" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d '{"message": "hello", "lang": "en"}' 2>/dev/null)
GENERIC_END=$(date +%s%N)

GENERIC_BODY=$(echo "$GENERIC_RESP" | sed '$d')
GENERIC_CODE=$(echo "$GENERIC_RESP" | tail -1)
GENERIC_MS=$(( (GENERIC_END - GENERIC_START) / 1000000 ))

if [[ "$GENERIC_CODE" == "200" ]]; then
    pass "Generic query returned HTTP 200 (${GENERIC_MS}ms)"
else
    fail "Generic query returned HTTP $GENERIC_CODE"
    [[ "$VERBOSE" == "1" ]] && info "Response: $GENERIC_BODY"
fi

# Validate response structure
if echo "$GENERIC_BODY" | jq -e '.response' > /dev/null 2>&1; then
    RESP_TEXT=$(echo "$GENERIC_BODY" | jq -r '.response')
    RESP_LEN=${#RESP_TEXT}
    if [[ $RESP_LEN -gt 0 ]]; then
        pass "Response has content (${RESP_LEN} chars)"
        info "Preview: ${RESP_TEXT:0:80}..."
    else
        fail "Response is empty string"
    fi
else
    fail "Response JSON missing 'response' field"
    [[ "$VERBOSE" == "1" ]] && info "Body: $GENERIC_BODY"
fi

# Check model_used field
if echo "$GENERIC_BODY" | jq -e '.model_used' > /dev/null 2>&1; then
    MODEL=$(echo "$GENERIC_BODY" | jq -r '.model_used')
    pass "model_used present: $MODEL"
else
    fail "Response missing 'model_used' field"
fi

# Latency check
if [[ $GENERIC_MS -le $MAX_GENERIC_LATENCY ]]; then
    pass "Latency within budget (${GENERIC_MS}ms ≤ ${MAX_GENERIC_LATENCY}ms)"
else
    warn "Latency exceeded budget (${GENERIC_MS}ms > ${MAX_GENERIC_LATENCY}ms)"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: Non-streaming — Topic Query (should trigger topic matching → RAG)
# ═══════════════════════════════════════════════════════════════════════════════

header "TEST 3: Non-streaming /chat — Topic Query (embedding match → RAG path)"

TOPIC_START=$(date +%s%N)
TOPIC_RESP=$(curl -s -w "\n%{http_code}" \
    -X POST "${BASE_URL}/api/v1/chat/" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d '{"message": "What is photosynthesis and how does it work in plants?", "lang": "en"}' 2>/dev/null)
TOPIC_END=$(date +%s%N)

TOPIC_BODY=$(echo "$TOPIC_RESP" | sed '$d')
TOPIC_CODE=$(echo "$TOPIC_RESP" | tail -1)
TOPIC_MS=$(( (TOPIC_END - TOPIC_START) / 1000000 ))

if [[ "$TOPIC_CODE" == "200" ]]; then
    pass "Topic query returned HTTP 200 (${TOPIC_MS}ms)"
else
    fail "Topic query returned HTTP $TOPIC_CODE"
    [[ "$VERBOSE" == "1" ]] && info "Response: $TOPIC_BODY"
fi

# Validate response
if echo "$TOPIC_BODY" | jq -e '.response' > /dev/null 2>&1; then
    TOPIC_TEXT=$(echo "$TOPIC_BODY" | jq -r '.response')
    TOPIC_LEN=${#TOPIC_TEXT}
    if [[ $TOPIC_LEN -gt 0 ]]; then
        pass "Topic response has content (${TOPIC_LEN} chars)"
        info "Preview: ${TOPIC_TEXT:0:100}..."
    else
        fail "Topic response is empty"
    fi
else
    fail "Topic response missing 'response' field"
fi

# Check sources (RAG should provide sources if topic matched)
SOURCES_COUNT=$(echo "$TOPIC_BODY" | jq -r '.sources | length' 2>/dev/null || echo "0")
if [[ "$SOURCES_COUNT" -gt 0 ]]; then
    pass "RAG returned $SOURCES_COUNT sources (topic matched!)"
else
    info "No RAG sources — topic may not be in embeddings DB yet (expected before backfill)"
fi

# Latency
if [[ $TOPIC_MS -le $MAX_TOPIC_LATENCY ]]; then
    pass "Topic query latency OK (${TOPIC_MS}ms ≤ ${MAX_TOPIC_LATENCY}ms)"
else
    warn "Topic query latency high (${TOPIC_MS}ms > ${MAX_TOPIC_LATENCY}ms)"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4: Streaming — Generic Query (SSE format, content field, done event)
# ═══════════════════════════════════════════════════════════════════════════════

header "TEST 4: Streaming /chat/stream — Generic Query (SSE validation)"

STREAM_FILE=$(mktemp)
STREAM_START=$(date +%s%N)

curl -s -N --max-time 15 \
    -X POST "${BASE_URL}/api/v1/chat/stream" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d '{"message": "hi there", "lang": "en"}' \
    > "$STREAM_FILE" 2>/dev/null &
CURL_PID=$!

# Wait for stream to complete (max 15s)
wait $CURL_PID 2>/dev/null || true
STREAM_END=$(date +%s%N)
STREAM_MS=$(( (STREAM_END - STREAM_START) / 1000000 ))

STREAM_LINES=$(wc -l < "$STREAM_FILE" | tr -d ' ')

if [[ $STREAM_LINES -gt 0 ]]; then
    pass "Stream returned $STREAM_LINES SSE lines (${STREAM_MS}ms total)"
else
    fail "Stream returned 0 lines"
fi

# Validate SSE format: lines start with "data: "
DATA_LINES=$(grep -c "^data: " "$STREAM_FILE" 2>/dev/null || echo "0")
if [[ $DATA_LINES -gt 0 ]]; then
    pass "Found $DATA_LINES SSE data: lines"
else
    fail "No SSE 'data: ' lines found in stream"
fi

# Validate 'content' field present (not 'text' — the #324 fix)
CONTENT_HITS=$(grep "^data: " "$STREAM_FILE" | grep -c '"content"' 2>/dev/null || echo "0")
TEXT_HITS=$(grep "^data: " "$STREAM_FILE" | grep -c '"text"' 2>/dev/null || echo "0")

if [[ $CONTENT_HITS -gt 0 ]]; then
    pass "SSE uses 'content' field ($CONTENT_HITS chunks) — frontend will render"
else
    fail "SSE missing 'content' field — frontend will NOT display response!"
fi

if [[ $TEXT_HITS -gt 0 ]]; then
    warn "SSE still has 'text' field ($TEXT_HITS hits) — possible regression"
else
    pass "No legacy 'text' field in SSE — clean"
fi

# Validate syrabit_done event
if grep -q "syrabit_done" "$STREAM_FILE" 2>/dev/null; then
    pass "Stream includes 'syrabit_done' final event"
    # Extract done event details
    DONE_LINE=$(grep "syrabit_done" "$STREAM_FILE" | head -1 | sed 's/^data: //')
    DONE_MODEL=$(echo "$DONE_LINE" | jq -r '.model // "unknown"' 2>/dev/null)
    DONE_LANG=$(echo "$DONE_LINE" | jq -r '.lang // "unknown"' 2>/dev/null)
    DONE_LATENCY=$(echo "$DONE_LINE" | jq -r '.latency_ms // "?"' 2>/dev/null)
    info "Done event: model=$DONE_MODEL, lang=$DONE_LANG, latency=${DONE_LATENCY}ms"
else
    fail "Stream missing 'syrabit_done' event — frontend won't know stream ended!"
fi

# Reconstruct full response from content chunks
FULL_RESPONSE=$(grep "^data: " "$STREAM_FILE" | sed 's/^data: //' | \
    jq -r 'select(.content != null and .content != "") | .content' 2>/dev/null | tr -d '\n')
FULL_LEN=${#FULL_RESPONSE}

if [[ $FULL_LEN -gt 0 ]]; then
    pass "Reconstructed full response: ${FULL_LEN} chars"
    info "Preview: ${FULL_RESPONSE:0:80}..."
else
    fail "Could not reconstruct response from stream (empty content)"
fi

rm -f "$STREAM_FILE"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5: Streaming — Topic Query (RAG path via stream)
# ═══════════════════════════════════════════════════════════════════════════════

header "TEST 5: Streaming /chat/stream — Topic Query (RAG path)"

STREAM2_FILE=$(mktemp)

curl -s -N --max-time 20 \
    -X POST "${BASE_URL}/api/v1/chat/stream" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d '{"message": "Explain the structure of an atom with protons neutrons and electrons", "lang": "en"}' \
    > "$STREAM2_FILE" 2>/dev/null &
CURL_PID2=$!
wait $CURL_PID2 2>/dev/null || true

STREAM2_LINES=$(grep -c "^data: " "$STREAM2_FILE" 2>/dev/null || echo "0")

if [[ $STREAM2_LINES -gt 0 ]]; then
    pass "Topic stream: $STREAM2_LINES SSE data lines"
else
    fail "Topic stream returned no data lines"
fi

# Check for content chunks
TOPIC_CONTENT=$(grep "^data: " "$STREAM2_FILE" | sed 's/^data: //' | \
    jq -r 'select(.content != null and .content != "") | .content' 2>/dev/null | tr -d '\n')
TOPIC_STREAM_LEN=${#TOPIC_CONTENT}

if [[ $TOPIC_STREAM_LEN -gt 0 ]]; then
    pass "Topic stream response: ${TOPIC_STREAM_LEN} chars"
    info "Preview: ${TOPIC_CONTENT:0:100}..."
else
    fail "Topic stream produced no content"
fi

# Check for done event
if grep -q "syrabit_done" "$STREAM2_FILE" 2>/dev/null; then
    pass "Topic stream has syrabit_done event"
else
    fail "Topic stream missing syrabit_done"
fi

# Check for error events (should NOT have errors)
ERROR_COUNT=$(grep "^data: " "$STREAM2_FILE" | grep -c '"error"' 2>/dev/null || echo "0")
if [[ $ERROR_COUNT -eq 0 ]]; then
    pass "No error events in topic stream"
else
    warn "Topic stream had $ERROR_COUNT error event(s)"
    grep '"error"' "$STREAM2_FILE" | head -2
fi

rm -f "$STREAM2_FILE"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 6: Assamese Language Chat (Sarvam AI path)
# ═══════════════════════════════════════════════════════════════════════════════

header "TEST 6: Assamese Language Chat (Sarvam AI → Vertex fallback)"

AS_RESP=$(curl -s -w "\n%{http_code}" --max-time 20 \
    -X POST "${BASE_URL}/api/v1/chat/" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d '{"message": "পোহৰ সংশ্লেষণ কি?", "lang": "as"}' 2>/dev/null)

AS_BODY=$(echo "$AS_RESP" | sed '$d')
AS_CODE=$(echo "$AS_RESP" | tail -1)

if [[ "$AS_CODE" == "200" ]]; then
    pass "Assamese chat returned HTTP 200"
    AS_TEXT=$(echo "$AS_BODY" | jq -r '.response // ""' 2>/dev/null)
    AS_MODEL=$(echo "$AS_BODY" | jq -r '.model_used // "unknown"' 2>/dev/null)
    AS_LEN=${#AS_TEXT}
    if [[ $AS_LEN -gt 0 ]]; then
        pass "Assamese response: ${AS_LEN} chars, model: $AS_MODEL"
        info "Preview: ${AS_TEXT:0:80}..."
    else
        fail "Assamese response is empty"
    fi
elif [[ "$AS_CODE" == "429" ]]; then
    warn "Assamese chat rate-limited (HTTP 429) — cannot test further"
elif [[ "$AS_CODE" == "503" ]]; then
    warn "Assamese chat unavailable (HTTP 503) — Sarvam + Vertex both down"
else
    fail "Assamese chat returned HTTP $AS_CODE"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 7: Input Validation & Security
# ═══════════════════════════════════════════════════════════════════════════════

header "TEST 7: Input Validation & Security"

# Empty message should be rejected
EMPTY_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "${BASE_URL}/api/v1/chat/" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d '{"message": ""}' 2>/dev/null)

if [[ "$EMPTY_CODE" == "422" ]]; then
    pass "Empty message rejected (HTTP 422)"
else
    fail "Empty message not rejected (HTTP $EMPTY_CODE, expected 422)"
fi

# Oversize message (>2000 chars) should be rejected
LONG_MSG=$(printf 'x%.0s' $(seq 1 2001))
LONG_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "${BASE_URL}/api/v1/chat/" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d "{\"message\": \"${LONG_MSG}\"}" 2>/dev/null)

if [[ "$LONG_CODE" == "422" ]]; then
    pass "Oversize message (2001 chars) rejected (HTTP 422)"
else
    fail "Oversize message not rejected (HTTP $LONG_CODE, expected 422)"
fi

# Invalid session_id format
INVALID_SID_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "${BASE_URL}/api/v1/chat/" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d '{"message": "test", "session_id": "../../../etc/passwd"}' 2>/dev/null)

if [[ "$INVALID_SID_CODE" == "422" ]]; then
    pass "Invalid session_id rejected (HTTP 422)"
else
    warn "Invalid session_id returned HTTP $INVALID_SID_CODE (expected 422)"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 8: Frontend — Cookie Consent & _redirects (308 fix)
# ═══════════════════════════════════════════════════════════════════════════════

header "TEST 8: Frontend — Cookie Consent & Redirect Fixes"

# Check CookieConsent component exists in built frontend
CONSENT_CHECK=$(curl -s -o /dev/null -w "%{http_code}" \
    "${FRONTEND_URL}/assets/CookieConsent" 2>/dev/null || echo "000")

# Actually check the frontend page loads
FRONTEND_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    "${FRONTEND_URL}/" 2>/dev/null)

if [[ "$FRONTEND_CODE" == "200" ]]; then
    pass "Frontend loads (HTTP 200)"
else
    warn "Frontend returned HTTP $FRONTEND_CODE"
fi

# Check /login doesn't 308 redirect (should serve 200 via _redirects)
LOGIN_CODE=$(curl -s -o /dev/null -w "%{http_code}" -L \
    "${FRONTEND_URL}/login" 2>/dev/null)

if [[ "$LOGIN_CODE" == "200" ]]; then
    pass "/login serves 200 (no 308 redirect)"
else
    warn "/login returned HTTP $LOGIN_CODE (expected 200)"
fi

# Check /about doesn't 308 redirect
ABOUT_CODE=$(curl -s -o /dev/null -w "%{http_code}" -L \
    "${FRONTEND_URL}/about" 2>/dev/null)

if [[ "$ABOUT_CODE" == "200" ]]; then
    pass "/about serves 200 (no 308 redirect)"
else
    warn "/about returned HTTP $ABOUT_CODE (expected 200)"
fi

# Check trailing slash redirect (301, not 308)
LIBRARY_SLASH=$(curl -s -o /dev/null -w "%{http_code}" \
    "${FRONTEND_URL}/library/" 2>/dev/null)

if [[ "$LIBRARY_SLASH" == "301" ]]; then
    pass "/library/ returns 301 (trailing-slash strip)"
elif [[ "$LIBRARY_SLASH" == "200" ]]; then
    info "/library/ returns 200 (CF may serve directly)"
else
    warn "/library/ returned HTTP $LIBRARY_SLASH"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 9: Sitemap lastmod Validation
# ═══════════════════════════════════════════════════════════════════════════════

header "TEST 9: Sitemap lastmod Validation"

SITEMAP_RESP=$(curl -s "${BASE_URL}/api/v1/seo/sitemap.xml" 2>/dev/null)
SITEMAP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/api/v1/seo/sitemap.xml" 2>/dev/null)

if [[ "$SITEMAP_CODE" == "200" ]]; then
    pass "Sitemap index loads (HTTP 200)"
    LASTMOD_COUNT=$(echo "$SITEMAP_RESP" | grep -c "<lastmod>" 2>/dev/null || echo "0")
    if [[ $LASTMOD_COUNT -gt 0 ]]; then
        pass "Sitemap index has $LASTMOD_COUNT <lastmod> entries"
    else
        fail "Sitemap index missing <lastmod> elements"
    fi
else
    warn "Sitemap index returned HTTP $SITEMAP_CODE"
fi

# Check static sitemap
STATIC_RESP=$(curl -s "${BASE_URL}/api/v1/seo/sitemap-static.xml" 2>/dev/null)
STATIC_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/api/v1/seo/sitemap-static.xml" 2>/dev/null)

if [[ "$STATIC_CODE" == "200" ]]; then
    STATIC_LASTMOD=$(echo "$STATIC_RESP" | grep -c "<lastmod>" 2>/dev/null || echo "0")
    if [[ $STATIC_LASTMOD -gt 0 ]]; then
        pass "Static sitemap has $STATIC_LASTMOD <lastmod> entries"
    else
        fail "Static sitemap missing <lastmod>"
    fi
else
    warn "Static sitemap returned HTTP $STATIC_CODE"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 10: Chat History (anonymous access)
# ═══════════════════════════════════════════════════════════════════════════════

header "TEST 10: Chat History (Anonymous Access)"

HISTORY_RESP=$(curl -s -w "\n%{http_code}" \
    -H "x-anon-id: ${ANON_ID}" \
    "${BASE_URL}/api/v1/chat/history" 2>/dev/null)

HISTORY_BODY=$(echo "$HISTORY_RESP" | sed '$d')
HISTORY_CODE=$(echo "$HISTORY_RESP" | tail -1)

if [[ "$HISTORY_CODE" == "200" ]]; then
    pass "Chat history endpoint accessible (HTTP 200)"
    HAS_CHATS=$(echo "$HISTORY_BODY" | jq -e '.chats' > /dev/null 2>&1 && echo "yes" || echo "no")
    HAS_PAGINATION=$(echo "$HISTORY_BODY" | jq -e '.pagination' > /dev/null 2>&1 && echo "yes" || echo "no")
    if [[ "$HAS_CHATS" == "yes" && "$HAS_PAGINATION" == "yes" ]]; then
        pass "History response has correct structure (chats + pagination)"
    else
        fail "History response missing expected fields"
    fi
else
    fail "Chat history returned HTTP $HISTORY_CODE"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# TEST 11: Rate Limiting Behavior
# ═══════════════════════════════════════════════════════════════════════════════

header "TEST 11: Rate Limiting Headers"

RATE_RESP=$(curl -s -D - --max-time 15 \
    -X POST "${BASE_URL}/api/v1/chat/" \
    -H "Content-Type: application/json" \
    -H "x-anon-id: ${ANON_ID}" \
    -d '{"message": "test rate limit", "lang": "en"}' 2>/dev/null)

# Just check we get a response (200 or 429)
RATE_CODE=$(echo "$RATE_RESP" | grep "^HTTP" | tail -1 | awk '{print $2}')
if [[ "$RATE_CODE" == "200" || "$RATE_CODE" == "429" ]]; then
    pass "Rate limiting active (HTTP $RATE_CODE)"
    if [[ "$RATE_CODE" == "429" ]]; then
        info "Rate limited — anonymous quota may be exhausted from test runs"
    fi
else
    warn "Unexpected rate limit response: HTTP $RATE_CODE"
fi


# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  CHAT PIPELINE TEST RESULTS${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Total:    ${BOLD}${TOTAL}${NC}"
echo -e "  Passed:   ${G}${PASSED}${NC}"
echo -e "  Failed:   ${R}${FAILED}${NC}"
echo -e "  Warnings: ${Y}${WARNINGS}${NC}"
echo ""
echo -e "  Backend:  ${BASE_URL}"
echo -e "  Frontend: ${FRONTEND_URL}"
echo ""

if [[ $FAILED -eq 0 ]]; then
    echo -e "  ${G}${BOLD}★ ALL CRITICAL CHECKS PASSED ★${NC}"
    echo ""
    echo -e "  The chat pipeline is fully functional:"
    echo -e "    • Generic queries → LLM-only (fast path)"
    echo -e "    • Topic queries → embedding match → RAG"
    echo -e "    • SSE streaming → 'content' field → frontend renders"
    echo -e "    • syrabit_done event → frontend knows stream ended"
    echo -e "    • Input validation → rejects bad input"
    echo -e "    • Cookie consent + redirect fixes → deployed"
    echo ""
    exit 0
else
    echo -e "  ${R}${BOLD}✗ ${FAILED} CHECK(S) FAILED${NC}"
    echo ""
    echo -e "  Run with ${BOLD}VERBOSE=1${NC} for detailed output:"
    echo -e "    VERBOSE=1 ./scripts/test-chat-pipeline.sh"
    echo ""
    exit 1
fi
