#!/bin/bash
#
# Chat Pipeline Audit Script for Google Cloud Shell
# 
# Audits the Syrabit chat+auth pipeline across:
#   - English/Assamese routing and SSE format
#   - Sarvam-to-Vertex fallback behavior
#   - RAG unavailability graceful degradation
#   - Response latency (first chunk <200ms, total <500ms)
#   - Language detection and model selection
#
# Usage: bash scripts/audit-chat-pipeline-cloud-shell.sh [test-filter]
#        bash scripts/audit-chat-pipeline-cloud-shell.sh TestChatResponseSpeed
#        bash scripts/audit-chat-pipeline-cloud-shell.sh  # runs all tests

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$REPO_ROOT/apps/backend"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Audit report file
AUDIT_REPORT="/tmp/chat-pipeline-audit-$(date +%s).log"

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[✗]${NC} $*"
}

# ============================================================================
# Step 1: Check Cloud Shell environment and Python availability
# ============================================================================

echo ""
log_info "════════════════════════════════════════════════════════════════"
log_info "Chat Pipeline Audit — Google Cloud Shell"
log_info "════════════════════════════════════════════════════════════════"
echo ""

log_info "Step 1: Checking Python environment..."

# Try to find Python 3.11+ (prefer pyenv, then system)
PYTHON_CMD=""
if command -v pyenv &> /dev/null; then
    log_info "Found pyenv. Attempting to use Python 3.11..."
    if eval "$(pyenv init -)" 2>/dev/null && pyenv shell 3.11.15 2>/dev/null; then
        PYTHON_CMD="python"
        log_success "Using pyenv Python 3.11.15"
    fi
fi

# Fallback to system Python
if [ -z "$PYTHON_CMD" ]; then
    if command -v python3 &> /dev/null; then
        PYTHON_CMD="python3"
        PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
        log_success "Using system python3: $PYTHON_VERSION"
    elif command -v python &> /dev/null; then
        PYTHON_CMD="python"
        PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
        log_success "Using system python: $PYTHON_VERSION"
    else
        log_error "Python 3 not found. Please install Python 3.11+ or use pyenv."
        exit 1
    fi
fi

# Verify version (need 3.8+)
PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
log_info "Python version: $PYTHON_VERSION"

# ============================================================================
# Step 2: Install dependencies
# ============================================================================

echo ""
log_info "Step 2: Installing backend dependencies..."

cd "$BACKEND_DIR"

# Check if venv exists; create if needed
if [ ! -d "venv" ]; then
    log_info "Creating virtual environment..."
    $PYTHON_CMD -m venv venv
fi

# Activate venv
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
    log_success "Virtual environment activated"
else
    log_warn "Virtual environment creation may have failed; attempting to proceed..."
fi

# Upgrade pip/setuptools
log_info "Upgrading pip and setuptools..."
$PYTHON_CMD -m pip install --upgrade pip setuptools wheel > /dev/null 2>&1 || true

# Install requirements
if [ -f "requirements.txt" ]; then
    log_info "Installing requirements.txt..."
    $PYTHON_CMD -m pip install -r requirements.txt > /dev/null 2>&1
    log_success "requirements.txt installed"
else
    log_warn "requirements.txt not found at $BACKEND_DIR/requirements.txt"
fi

# Install test dependencies
log_info "Installing test dependencies..."
$PYTHON_CMD -m pip install pytest pytest-asyncio pytest-env pytest-cov > /dev/null 2>&1
log_success "Test dependencies installed"

# ============================================================================
# Step 3: Verify test file exists
# ============================================================================

echo ""
log_info "Step 3: Locating chat pipeline audit tests..."

TEST_FILE="$BACKEND_DIR/tests/test_pr358_chat_audit.py"
if [ ! -f "$TEST_FILE" ]; then
    log_error "Test file not found: $TEST_FILE"
    log_info "Available test files:"
    ls -la "$BACKEND_DIR/tests/" | grep -E "test_.*\.py" || log_warn "No test files found"
    exit 1
fi
log_success "Found: test_pr358_chat_audit.py"

# ============================================================================
# Step 4: Run audit tests
# ============================================================================

echo ""
log_info "Step 4: Running chat pipeline audit tests..."
echo ""

# Determine which tests to run
TEST_FILTER="${1:-}"
if [ -n "$TEST_FILTER" ]; then
    log_info "Running tests matching: $TEST_FILTER"
    PYTEST_ARGS="tests/test_pr358_chat_audit.py -k $TEST_FILTER"
else
    log_info "Running all chat pipeline audit tests"
    PYTEST_ARGS="tests/test_pr358_chat_audit.py"
fi

# Run tests with detailed output
set +e  # Don't exit on test failure
$PYTHON_CMD -m pytest $PYTEST_ARGS -v --tb=short 2>&1 | tee "$AUDIT_REPORT"
TEST_EXIT_CODE=$?
set -e

# ============================================================================
# Step 5: Parse results and generate report
# ============================================================================

echo ""
echo "════════════════════════════════════════════════════════════════"
log_info "Audit Results Summary"
echo "════════════════════════════════════════════════════════════════"
echo ""

# Extract test results
PASSED=$(grep -c " PASSED" "$AUDIT_REPORT" || true)
FAILED=$(grep -c " FAILED" "$AUDIT_REPORT" || true)
SKIPPED=$(grep -c " SKIPPED" "$AUDIT_REPORT" || true)
ERRORS=$(grep -c " ERROR" "$AUDIT_REPORT" || true)

echo "Test Results:"
echo "  ✓ Passed:  $PASSED"
echo "  ✗ Failed:  $FAILED"
echo "  ⊘ Skipped: $SKIPPED"
echo "  ⚠ Errors:  $ERRORS"
echo ""

# Summary by category
echo "Audit Categories:"
echo ""

if grep -q "TestEnglishModeChatStream" "$AUDIT_REPORT"; then
    echo "  📝 English Mode Routing:"
    if grep "TestEnglishModeChatStream" "$AUDIT_REPORT" | grep -q "PASSED"; then
        log_success "English mode (SSE format, Vertex routing, prompt enforcement)"
    else
        log_error "English mode tests failed"
    fi
fi

if grep -q "TestAssameseModeChatStream" "$AUDIT_REPORT"; then
    echo "  🌐 Assamese Mode & Fallback:"
    if grep "TestAssameseModeChatStream" "$AUDIT_REPORT" | grep -q "PASSED"; then
        log_success "Assamese routing, Sarvam-to-Vertex fallback, script prompt"
    else
        log_error "Assamese mode tests failed"
    fi
fi

if grep -q "TestRAGUnavailableFallback" "$AUDIT_REPORT"; then
    echo "  🔄 RAG Graceful Degradation:"
    if grep "TestRAGUnavailableFallback" "$AUDIT_REPORT" | grep -q "PASSED"; then
        log_success "Fallback when search unavailable/empty/exception"
    else
        log_error "RAG fallback tests failed"
    fi
fi

if grep -q "TestChatResponseSpeed" "$AUDIT_REPORT"; then
    echo "  ⚡ Response Latency:"
    if grep "TestChatResponseSpeed" "$AUDIT_REPORT" | grep -q "PASSED"; then
        log_success "First chunk <200ms, total completion <500ms"
    else
        log_error "Latency tests failed (may indicate timeout/GC issues)"
    fi
fi

echo ""

# ============================================================================
# Step 6: Detailed failure analysis
# ============================================================================

if [ "$FAILED" -gt 0 ] || [ "$ERRORS" -gt 0 ]; then
    echo "════════════════════════════════════════════════════════════════"
    log_error "FAILURES/ERRORS DETECTED"
    echo "════════════════════════════════════════════════════════════════"
    echo ""
    
    echo "Failed Tests:"
    grep -E "(FAILED|ERROR)" "$AUDIT_REPORT" | head -20 || true
    echo ""
    
    echo "Remediation Steps:"
    echo "  1. Check detailed logs: cat $AUDIT_REPORT"
    echo "  2. Review failing test file: $TEST_FILE"
    echo "  3. For latency failures: increase thresholds (environment-dependent)"
    echo "  4. For fallback test: verify patch targets match production code paths"
    echo "  5. For analytics/config failures: ensure all endpoints are implemented"
    echo ""
fi

# ============================================================================
# Step 7: Generate audit artifact
# ============================================================================

echo "════════════════════════════════════════════════════════════════"
log_info "Audit Artifacts"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "Full audit report:"
echo "  📄 $AUDIT_REPORT"
echo ""
echo "To review failures in detail:"
echo "  cat $AUDIT_REPORT | grep -A 10 'FAILED\\|ERROR'"
echo ""

# ============================================================================
# Step 8: Known issues and recommendations
# ============================================================================

echo "════════════════════════════════════════════════════════════════"
log_warn "Known Pipeline Issues (from audit docs)"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "✓ PR 358 Fixed:"
echo "  - conversation_id vs session_id field mapping"
echo "  - LogoutRequest.refresh_token null crash"
echo "  - Missing analytics endpoints"
echo "  - Missing config/trustpilot endpoints"
echo ""
echo "⚠ Still Open:"
echo "  - Latency: No asyncio.gather for parallel embedding+history"
echo "  - Streaming: No heartbeat during RAG retrieval phase (30s+ timeout risk)"
echo "  - Error handling: Raw errors leak to client (info disclosure)"
echo "  - No timeout wrapper on entire chat pipeline (potential 180s hangs)"
echo "  - Language detection: May misclassify mixed-content queries"
echo ""
echo "Recommendations:"
echo "  1. Add asyncio.gather() for parallel RAG retrieval"
echo "  2. Implement SSE keepalive comments during retrieval"
echo "  3. Sanitize error messages before sending to client"
echo "  4. Wrap entire pipeline in asyncio.wait_for(timeout=30s)"
echo "  5. Improve language detection for code and mixed-content"
echo ""

# ============================================================================
# Exit
# ============================================================================

echo "════════════════════════════════════════════════════════════════"
if [ "$TEST_EXIT_CODE" -eq 0 ]; then
    log_success "Chat pipeline audit PASSED"
    exit 0
else
    log_error "Chat pipeline audit FAILED (see details above)"
    exit 1
fi
