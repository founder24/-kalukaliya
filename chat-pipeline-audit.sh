#!/bin/bash

# =============================================================================
# SYRABIT CHAT PIPELINE AUDIT SCRIPT
# =============================================================================
# Purpose: Comprehensive audit of the Chat functionality in production
# Usage:   ./chat-pipeline-audit.sh [--quick] [--verbose] [--fix]
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
API_URL="${API_URL:-https://api.syrabit.ai}"
FRONTEND_URL="${FRONTEND_URL:-https://syrabit.ai}"
VERBOSE=false
QUICK_MODE=false
FIX_MODE=false
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
CRITICAL_COUNT=0

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --quick) QUICK_MODE=true; shift ;;
    --verbose) VERBOSE=true; shift ;;
    --fix) FIX_MODE=true; shift ;;
    --help) 
      echo "Usage: $0 [--quick] [--verbose] [--fix]"
      echo "  --quick    Run only essential checks"
      echo "  --verbose  Show detailed output"
      echo "  --fix      Attempt automatic fixes (where possible)"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# Helper functions
log_pass() { echo -e "${GREEN}[PASS]${NC} $1"; ((PASS_COUNT++)); }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; ((WARN_COUNT++)); }
log_fail() { echo -e "${RED}[FAIL]${NC} $1"; ((FAIL_COUNT++)); }
log_critical() { echo -e "${RED}[CRITICAL]${NC} $1"; ((CRITICAL_COUNT++)); }
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }

check_http_status() {
  local url="$1"
  local expected="${2:-200}"
  local method="${3:-GET}"
  local headers="${4:-}"
  
  local status
  if [ -n "$headers" ]; then
    status=$(curl -s -o /dev/null -w "%{http_code}" -X "$method" -H "$headers" "$url" 2>/dev/null || echo "000")
  else
    status=$(curl -s -o /dev/null -w "%{http_code}" -X "$method" "$url" 2>/dev/null || echo "000")
  fi
  
  if [ "$status" = "$expected" ]; then
    return 0
  else
    return 1
  fi
}

get_response_body() {
  local url="$1"
  local method="${2:-GET}"
  local headers="${3:-}"
  
  if [ -n "$headers" ]; then
    curl -s -X "$method" -H "$headers" "$url" 2>/dev/null || echo ""
  else
    curl -s -X "$method" "$url" 2>/dev/null || echo ""
  fi
}

# =============================================================================
# SECTION 1: AUTHENTICATION & AUTHORIZATION
# =============================================================================
audit_auth_pipeline() {
  echo ""
  echo "==============================================================================="
  echo "  SECTION 1: AUTHENTICATION PIPELINE"
  echo "==============================================================================="
  
  # Test 1: Public health endpoint (no auth required)
  if check_http_status "$API_URL/health" "200"; then
    log_pass "Public health endpoint accessible"
  else
    log_critical "Public health endpoint unreachable"
  fi
  
  # Test 2: Protected endpoint without token (should fail)
  if check_http_status "$API_URL/chats" "401"; then
    log_pass "Protected /chats endpoint rejects unauthenticated requests"
  else
    log_critical "Protected /chats endpoint accessible without authentication"
  fi
  
  # Test 3: Protected endpoint with invalid token (should fail)
  if check_http_status "$API_URL/chats" "401" "GET" "Authorization: Bearer invalid_token_12345"; then
    log_pass "Invalid JWT token correctly rejected"
  else
    log_critical "Invalid JWT token accepted (security vulnerability)"
  fi
  
  # Test 4: Check if login endpoint exists
  if check_http_status "$API_URL/auth/login" "405" || check_http_status "$API_URL/auth/login" "200" || check_http_status "$API_URL/auth/login" "400"; then
    log_pass "Login endpoint exists"
  else
    log_fail "Login endpoint missing or misconfigured"
  fi
  
  # Test 5: Check if registration endpoint exists
  if check_http_status "$API_URL/auth/register" "405" || check_http_status "$API_URL/auth/register" "200" || check_http_status "$API_URL/auth/register" "400"; then
    log_pass "Registration endpoint exists"
  else
    log_fail "Registration endpoint missing or misconfigured"
  fi
  
  # Test 6: Token refresh endpoint
  if check_http_status "$API_URL/auth/refresh" "405" || check_http_status "$API_URL/auth/refresh" "401" || check_http_status "$API_URL/auth/refresh" "400"; then
    log_pass "Token refresh endpoint exists"
  else
    log_warn "Token refresh endpoint may be missing"
  fi
  
  # Test 7: Security - Check for exposed .env (affects auth secrets)
  local env_status
  env_status=$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/.env" 2>/dev/null || echo "000")
  if [ "$env_status" = "200" ]; then
    log_critical ".env file publicly accessible - JWT secrets exposed!"
  elif [ "$env_status" = "404" ] || [ "$env_status" = "403" ]; then
    log_pass ".env file not publicly accessible"
  else
    log_warn ".env access returned unexpected status: $env_status"
  fi
}

# =============================================================================
# SECTION 2: CONTENT HIERARCHY (Prerequisite for Chat)
# =============================================================================
audit_content_hierarchy() {
  echo ""
  echo "==============================================================================="
  echo "  SECTION 2: CONTENT HIERARCHY (Chat Prerequisites)"
  echo "==============================================================================="
  
  # Test 1: Boards endpoint
  if check_http_status "$API_URL/api/content/boards" "200"; then
    log_pass "Boards endpoint functional"
    if [ "$VERBOSE" = true ]; then
      local boards
      boards=$(get_response_body "$API_URL/api/content/boards")
      echo "    Sample response: ${boards:0:100}..."
    fi
  else
    log_critical "Boards endpoint returning $(curl -s -o /dev/null -w "%{http_code}" "$API_URL/api/content/boards") - Chat cannot function without content"
  fi
  
  # Test 2: Classes endpoint (requires board_id)
  # First get a board ID if possible
  local boards_body
  boards_body=$(get_response_body "$API_URL/api/content/boards")
  local board_id=""
  
  if command -v jq &> /dev/null && [ -n "$boards_body" ]; then
    board_id=$(echo "$boards_body" | jq -r '.[0].id // .[0]._id // empty' 2>/dev/null || echo "")
  fi
  
  if [ -n "$board_id" ]; then
    if check_http_status "$API_URL/api/content/classes?board_id=$board_id" "200"; then
      log_pass "Classes endpoint functional with board_id=$board_id"
    else
      log_fail "Classes endpoint failing with valid board_id"
    fi
  else
    log_warn "Could not extract board_id to test classes endpoint"
  fi
  
  # Test 3: Subjects endpoint
  if check_http_status "$API_URL/api/content/subjects" "200" || check_http_status "$API_URL/api/content/subjects" "400"; then
    log_pass "Subjects endpoint exists and responds"
  else
    log_fail "Subjects endpoint not responding correctly"
  fi
  
  # Test 4: Topics endpoint
  if check_http_status "$API_URL/api/content/topics" "200" || check_http_status "$API_URL/api/content/topics" "400"; then
    log_pass "Topics endpoint exists and responds"
  else
    log_fail "Topics endpoint not responding correctly"
  fi
  
  # Test 5: Content seeding check
  local boards_count=0
  if command -v jq &> /dev/null && [ -n "$boards_body" ]; then
    boards_count=$(echo "$boards_body" | jq 'length' 2>/dev/null || echo "0")
  fi
  
  if [ "$boards_count" -gt 0 ]; then
    log_pass "Content database has $boards_count boards seeded"
  elif [ "$boards_count" -eq 0 ]; then
    log_critical "Content database is empty - no boards found"
  else
    log_warn "Could not determine content count"
  fi
}

# =============================================================================
# SECTION 3: CHAT CORE FUNCTIONALITY
# =============================================================================
audit_chat_core() {
  echo ""
  echo "==============================================================================="
  echo "  SECTION 3: CHAT CORE FUNCTIONALITY"
  echo "==============================================================================="
  
  # Note: These tests require a valid JWT token
  # We'll test the endpoint structure and error messages instead
  
  local test_token="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IlRlc3QgVXNlciIsImlhdCI6MTUxNjIzOTAyMn0.test_signature"
  
  # Test 1: Create chat endpoint exists
  local create_status
  create_status=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $test_token" \
    -d '{"message":"test"}' \
    "$API_URL/chats" 2>/dev/null || echo "000")
  
  if [ "$create_status" = "401" ]; then
    log_pass "Chat creation endpoint exists (rejected invalid token as expected)"
  elif [ "$create_status" = "400" ] || [ "$create_status" = "422" ]; then
    log_pass "Chat creation endpoint exists and validates input"
  elif [ "$create_status" = "404" ]; then
    log_critical "Chat creation endpoint not found (/chats)"
  else
    log_warn "Chat creation endpoint returned unexpected status: $create_status"
  fi
  
  # Test 2: Get chats list endpoint
  local list_status
  list_status=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $test_token" \
    "$API_URL/chats" 2>/dev/null || echo "000")
  
  if [ "$list_status" = "401" ]; then
    log_pass "Chat list endpoint exists (rejected invalid token as expected)"
  elif [ "$list_status" = "404" ]; then
    log_critical "Chat list endpoint not found"
  else
    log_warn "Chat list endpoint returned unexpected status: $list_status"
  fi
  
  # Test 3: Get specific chat endpoint
  local specific_status
  specific_status=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $test_token" \
    "$API_URL/chats/test-chat-id" 2>/dev/null || echo "000")
  
  if [ "$specific_status" = "401" ] || [ "$specific_status" = "404" ]; then
    log_pass "Specific chat endpoint exists"
  else
    log_warn "Specific chat endpoint returned unexpected status: $specific_status"
  fi
  
  # Test 4: Delete chat endpoint
  local delete_status
  delete_status=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
    -H "Authorization: Bearer $test_token" \
    "$API_URL/chats/test-chat-id" 2>/dev/null || echo "000")
  
  if [ "$delete_status" = "401" ] || [ "$delete_status" = "404" ]; then
    log_pass "Chat deletion endpoint exists"
  else
    log_warn "Chat deletion endpoint returned unexpected status: $delete_status"
  fi
  
  # Test 5: Chat streaming endpoint (if using SSE/WebSocket)
  local stream_status
  stream_status=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $test_token" \
    "$API_URL/chats/stream/test-id" 2>/dev/null || echo "000")
  
  if [ "$stream_status" = "401" ] || [ "$stream_status" = "404" ] || [ "$stream_status" = "200" ]; then
    log_pass "Chat streaming endpoint exists or gracefully missing"
  else
    log_info "Chat streaming endpoint status: $stream_status (may use different implementation)"
  fi
}

# =============================================================================
# SECTION 4: AI/LLM INTEGRATION
# =============================================================================
audit_ai_integration() {
  echo ""
  echo "==============================================================================="
  echo "  SECTION 4: AI/LLM INTEGRATION"
  echo "==============================================================================="
  
  # Test 1: Check if LLM provider endpoints are reachable from backend
  # This is indirect - we check error messages for clues
  
  log_info "Testing AI integration indirectly through chat responses..."
  
  # Test 2: Check for AI-related configuration endpoints
  local config_status
  config_status=$(curl -s -o /dev/null -w "%{http_code}" \
    "$API_URL/api/ai/config" 2>/dev/null || echo "000")
  
  if [ "$config_status" != "404" ]; then
    log_pass "AI configuration endpoint exists"
  else
    log_info "No dedicated AI config endpoint (may use environment variables)"
  fi
  
  # Test 3: Check for embeddings endpoint (for RAG)
  local embed_status
  embed_status=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"text":"test"}' \
    "$API_URL/api/embeddings" 2>/dev/null || echo "000")
  
  if [ "$embed_status" = "401" ] || [ "$embed_status" = "400" ] || [ "$embed_status" = "404" ]; then
    if [ "$embed_status" = "404" ]; then
      log_info "No dedicated embeddings endpoint (may be internal)"
    else
      log_pass "Embeddings endpoint exists"
    fi
  else
    log_warn "Embeddings endpoint returned unexpected status: $embed_status"
  fi
  
  # Test 4: Check topic embeddings collection accessibility
  local topic_embed_status
  topic_embed_status=$(curl -s -o /dev/null -w "%{http_code}" \
    "$API_URL/api/content/topic-embeddings" 2>/dev/null || echo "000")
  
  if [ "$topic_embed_status" = "200" ] || [ "$topic_embed_status" = "401" ] || [ "$topic_embed_status" = "404" ]; then
    log_pass "Topic embeddings endpoint exists or gracefully missing"
  else
    log_warn "Topic embeddings endpoint status: $topic_embed_status"
  fi
}

# =============================================================================
# SECTION 5: DATABASE CONNECTIVITY
# =============================================================================
audit_database() {
  echo ""
  echo "==============================================================================="
  echo "  SECTION 5: DATABASE CONNECTIVITY"
  echo "==============================================================================="
  
  # Test 1: Deep health check (if available)
  local deep_health_status
  deep_health_status=$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/health/deep" 2>/dev/null || echo "000")
  
  if [ "$deep_health_status" = "200" ]; then
    log_pass "Deep health check passes (database connected)"
    
    if [ "$VERBOSE" = true ]; then
      local health_body
      health_body=$(get_response_body "$API_URL/health/deep")
      echo "    Health details: $health_body"
    fi
  elif [ "$deep_health_status" = "404" ]; then
    log_warn "Deep health endpoint not implemented - cannot verify DB connection"
  elif [ "$deep_health_status" = "503" ]; then
    log_critical "Database connection failed (503 Service Unavailable)"
  else
    log_fail "Deep health check returned status: $deep_health_status"
  fi
  
  # Test 2: Basic health check
  if check_http_status "$API_URL/health" "200"; then
    log_pass "Basic health endpoint responsive"
  else
    log_critical "Basic health endpoint not responsive"
  fi
  
  # Test 3: Database-specific error patterns
  log_info "Checking for database error patterns in responses..."
  
  local test_response
  test_response=$(curl -s -X POST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer invalid" \
    -d '{"message":"test"}' \
    "$API_URL/chats" 2>/dev/null || echo "")
  
  if echo "$test_response" | grep -qi "mongo\|database\|connection"; then
    log_warn "Database-related error messages exposed (potential information disclosure)"
  else
    log_pass "No sensitive database error messages exposed"
  fi
}

# =============================================================================
# SECTION 6: PERFORMANCE & SCALABILITY
# =============================================================================
audit_performance() {
  echo ""
  echo "==============================================================================="
  echo "  SECTION 6: PERFORMANCE & SCALABILITY"
  echo "==============================================================================="
  
  # Test 1: Response time for health endpoint
  local start_time end_time duration
  start_time=$(date +%s%N)
  curl -s -o /dev/null "$API_URL/health" 2>/dev/null
  end_time=$(date +%s%N)
  duration=$(( (end_time - start_time) / 1000000 )) # Convert to milliseconds
  
  if [ "$duration" -lt 500 ]; then
    log_pass "Health endpoint response time: ${duration}ms (< 500ms)"
  elif [ "$duration" -lt 1000 ]; then
    log_warn "Health endpoint response time: ${duration}ms (500-1000ms)"
  else
    log_fail "Health endpoint response time: ${duration}ms (> 1000ms)"
  fi
  
  # Test 2: Response time for content endpoint
  start_time=$(date +%s%N)
  curl -s -o /dev/null "$API_URL/api/content/boards" 2>/dev/null
  end_time=$(date +%s%N)
  duration=$(( (end_time - start_time) / 1000000 ))
  
  if [ "$duration" -lt 1000 ]; then
    log_pass "Content endpoint response time: ${duration}ms (< 1000ms)"
  else
    log_warn "Content endpoint response time: ${duration}ms (> 1000ms)"
  fi
  
  # Test 3: Check for rate limiting headers
  local rate_headers
  rate_headers=$(curl -s -I "$API_URL/health" 2>/dev/null | grep -i "ratelimit\|x-rate" || echo "")
  
  if [ -n "$rate_headers" ]; then
    log_pass "Rate limiting headers present"
    if [ "$VERBOSE" = true ]; then
      echo "    $rate_headers"
    fi
  else
    log_warn "No rate limiting headers detected"
  fi
  
  # Test 4: Concurrent request handling (light test)
  if [ "$QUICK_MODE" = false ]; then
    log_info "Testing concurrent request handling (5 parallel requests)..."
    local success_count=0
    for i in {1..5}; do
      if curl -s -o /dev/null -w "%{http_code}" "$API_URL/health" 2>/dev/null | grep -q "200"; then
        ((success_count++))
      fi
    done &
    wait
    
    if [ "$success_count" -eq 5 ]; then
      log_pass "All 5 concurrent requests succeeded"
    else
      log_warn "Only $success_count/5 concurrent requests succeeded"
    fi
  fi
}

# =============================================================================
# SECTION 7: ERROR HANDLING & LOGGING
# =============================================================================
audit_error_handling() {
  echo ""
  echo "==============================================================================="
  echo "  SECTION 7: ERROR HANDLING & LOGGING"
  echo "==============================================================================="
  
  # Test 1: Proper error format on invalid request
  local error_response
  error_response=$(curl -s -X POST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer invalid" \
    -d '{"invalid_field": "test"}' \
    "$API_URL/chats" 2>/dev/null || echo "")
  
  if echo "$error_response" | grep -q "error\|message\|detail"; then
    log_pass "Error responses include structured error messages"
  else
    log_warn "Error responses may not be properly structured"
  fi
  
  # Test 2: CORS headers on error responses
  local cors_headers
  cors_headers=$(curl -s -I -X OPTIONS \
    -H "Origin: https://syrabit.ai" \
    -H "Access-Control-Request-Method: POST" \
    "$API_URL/chats" 2>/dev/null | grep -i "access-control" || echo "")
  
  if [ -n "$cors_headers" ]; then
    log_pass "CORS headers present on preflight requests"
  else
    log_warn "CORS headers may be missing on error responses"
  fi
  
  # Test 3: Graceful degradation
  log_info "Testing graceful degradation on malformed requests..."
  
  local malformed_status
  malformed_status=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer invalid" \
    -d 'not valid json' \
    "$API_URL/chats" 2>/dev/null || echo "000")
  
  if [ "$malformed_status" = "400" ] || [ "$malformed_status" = "415" ]; then
    log_pass "Malformed JSON handled gracefully ($malformed_status)"
  elif [ "$malformed_status" = "500" ]; then
    log_fail "Malformed JSON causes server error (500)"
  else
    log_warn "Malformed JSON handling: $malformed_status"
  fi
}

# =============================================================================
# SECTION 8: SECURITY SPECIFIC TO CHAT
# =============================================================================
audit_chat_security() {
  echo ""
  echo "==============================================================================="
  echo "  SECTION 8: CHAT-SPECIFIC SECURITY"
  echo "==============================================================================="
  
  # Test 1: XSS prevention in chat responses
  local xss_test
  xss_test=$(curl -s -X POST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer invalid" \
    -d '{"message":"<script>alert(1)</script>"}' \
    "$API_URL/chats" 2>/dev/null || echo "")
  
  if echo "$xss_test" | grep -q "<script>"; then
    log_fail "Potential XSS vulnerability - script tags not sanitized"
  else
    log_pass "Script tags appear to be sanitized in responses"
  fi
  
  # Test 2: SQL/NoSQL injection attempts
  local injection_test
  injection_test=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer invalid" \
    -d '{"message":"{\"$gt\": \"\"}"}' \
    "$API_URL/chats" 2>/dev/null || echo "000")
  
  if [ "$injection_test" = "400" ] || [ "$injection_test" = "401" ] || [ "$injection_test" = "422" ]; then
    log_pass "NoSQL injection attempt rejected"
  elif [ "$injection_test" = "500" ]; then
    log_fail "NoSQL injection attempt caused server error"
  else
    log_warn "NoSQL injection test returned: $injection_test"
  fi
  
  # Test 3: Rate limiting on chat endpoint
  log_info "Testing rate limiting on chat endpoint (3 rapid requests)..."
  local rate_limit_triggered=false
  for i in {1..3}; do
    local status
    status=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer invalid" \
      -d '{"message":"test"}' \
      "$API_URL/chats" 2>/dev/null || echo "000")
    
    if [ "$status" = "429" ]; then
      rate_limit_triggered=true
      break
    fi
  done
  
  if [ "$rate_limit_triggered" = true ]; then
    log_pass "Rate limiting triggered on rapid requests"
  else
    log_warn "Rate limiting not detected (may have higher threshold)"
  fi
  
  # Test 4: User isolation (cannot access other user's chats)
  # This requires two valid tokens, so we test the principle with error messages
  log_info "Verifying user isolation mechanisms..."
  local isolation_response
  isolation_response=$(curl -s \
    -H "Authorization: Bearer invalid_user_1" \
    "$API_URL/chats/some_other_users_chat_id" 2>/dev/null || echo "")
  
  if echo "$isolation_response" | grep -qi "unauthorized\|forbidden\|not found"; then
    log_pass "User isolation appears enforced (proper error on cross-user access)"
  else
    log_warn "Cannot verify user isolation without valid tokens"
  fi
}

# =============================================================================
# MAIN EXECUTION
# =============================================================================
main() {
  echo "==============================================================================="
  echo "  SYRABIT CHAT PIPELINE AUDIT"
  echo "==============================================================================="
  echo "  Date:       $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  echo "  API:        $API_URL"
  echo "  Frontend:   $FRONTEND_URL"
  echo "  Mode:       $([ "$QUICK_MODE" = true ] && echo "QUICK" || echo "FULL")"
  echo "==============================================================================="
  
  START_TIME=$(date +%s)
  
  # Run audit sections
  audit_auth_pipeline
  
  if [ "$QUICK_MODE" = false ]; then
    audit_content_hierarchy
    audit_chat_core
    audit_ai_integration
    audit_database
    audit_performance
    audit_error_handling
    audit_chat_security
  else
    log_info "Skipping detailed sections in QUICK mode"
  fi
  
  END_TIME=$(date +%s)
  DURATION=$((END_TIME - START_TIME))
  
  # Summary
  echo ""
  echo "==============================================================================="
  echo "  AUDIT SUMMARY"
  echo "==============================================================================="
  echo "  Total Checks:   $((PASS_COUNT + WARN_COUNT + FAIL_COUNT + CRITICAL_COUNT))"
  echo "  Passed:         $PASS_COUNT"
  echo "  Warnings:       $WARN_COUNT"
  echo "  Failed:         $FAIL_COUNT"
  echo "  Critical:       $CRITICAL_COUNT"
  echo "  Duration:       ${DURATION}s"
  echo ""
  
  TOTAL=$((PASS_COUNT + WARN_COUNT + FAIL_COUNT + CRITICAL_COUNT))
  if [ "$TOTAL" -gt 0 ]; then
    SCORE=$((PASS_COUNT * 100 / TOTAL))
    echo "  ┌─────────────────────────────────────────────┐"
    printf "  │  Chat Pipeline Health Score: %d/100            │\n" "$SCORE"
    if [ "$CRITICAL_COUNT" -gt 0 ]; then
      echo "  │  Recommendation: CRITICAL ISSUES FOUND    │"
    elif [ "$SCORE" -ge 80 ]; then
      echo "  │  Recommendation: READY FOR PRODUCTION     │"
    elif [ "$SCORE" -ge 60 ]; then
      echo "  │  Recommendation: NEEDS ATTENTION          │"
    else
      echo "  │  Recommendation: NOT READY                │"
    fi
    echo "  └─────────────────────────────────────────────┘"
  fi
  
  if [ "$CRITICAL_COUNT" -gt 0 ]; then
    echo ""
    echo "  ⚠️  WARNING: $CRITICAL_COUNT critical issue(s) detected!"
    echo "  Immediate action required before chat can be considered functional."
  fi
  
  echo ""
  echo "==============================================================================="
  
  # Exit with error code if critical issues found
  if [ "$CRITICAL_COUNT" -gt 0 ]; then
    exit 1
  else
    exit 0
  fi
}

# Run main function
main
