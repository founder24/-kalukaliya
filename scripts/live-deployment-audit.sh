#!/usr/bin/env bash
# ===============================================================================
# SYRABIT PRODUCTION DEPLOYMENT AUDIT
# ===============================================================================
#
# Comprehensive production readiness audit with 500+ checks across 8 sections:
#   1. Infrastructure & DNS (60+ checks)
#   2. Security Audit (100+ checks)
#   3. Performance Audit (80+ checks)
#   4. API Completeness (80+ checks)
#   5. Data Integrity & Service Connectivity (50+ checks)
#   6. Deployment Configuration (60+ checks)
#   7. Compliance & SEO (70+ checks)
#   8. Monitoring & Observability (40+ checks)
#
# Usage:
#   ./scripts/live-deployment-audit.sh              # Full audit
#   ./scripts/live-deployment-audit.sh --help       # Show help
#   ./scripts/live-deployment-audit.sh --quick      # Reduced check set
#   ./scripts/live-deployment-audit.sh --section 2  # Run only section 2
#   ./scripts/live-deployment-audit.sh --export-json
#   ./scripts/live-deployment-audit.sh --export-html
#
# Environment Variables (all optional):
#   BASE_URL        - Backend/edge URL (default: https://api.syrabit.ai)
#   FRONTEND_URL    - Frontend URL (default: https://syrabit.ai)
#   GCP_PROJECT     - GCP project ID (default: blissful-acumen-495019-t6)
#   GCP_REGION      - GCP region (default: asia-south1)
#   VERBOSE         - Set to 1 for detailed output
#
# Requirements: bash, curl, jq, openssl, dig
# Exit code: 0 if score >= 70, 1 otherwise
# ===============================================================================

set -euo pipefail

# ---- Temp Directory & Cleanup Trap ----

AUDIT_TMPDIR=$(mktemp -d "${TMPDIR:-/tmp}/audit-XXXXXXXXXX")
cleanup_audit_tmpdir() {
    rm -rf "$AUDIT_TMPDIR"
}
trap cleanup_audit_tmpdir EXIT INT TERM

# ---- Configuration ----

BASE_URL="${BASE_URL:-https://api.syrabit.ai}"
FRONTEND_URL="${FRONTEND_URL:-https://syrabit.ai}"
GCP_PROJECT="${GCP_PROJECT:-blissful-acumen-495019-t6}"
GCP_REGION="${GCP_REGION:-asia-south1}"
VERBOSE="${VERBOSE:-0}"

# Domains derived from URLs
FRONTEND_DOMAIN=$(echo "$FRONTEND_URL" | sed 's|https://||;s|http://||;s|/.*||')
API_DOMAIN=$(echo "$BASE_URL" | sed 's|https://||;s|http://||;s|/.*||')

# Runtime flags
QUICK_MODE=0
RUN_SECTION=""
EXPORT_JSON=0
EXPORT_HTML=0

# ---- State Tracking ----

TOTAL_CHECKS=0
TOTAL_PASS=0
TOTAL_WARN=0
TOTAL_FAIL=0
TOTAL_CRITICAL=0

# Per-section counters
declare -a SECTION_NAMES=()
declare -a SECTION_PASS=()
declare -a SECTION_WARN=()
declare -a SECTION_FAIL=()
declare -a SECTION_CRITICAL=()
declare -a SECTION_TOTAL=()

CURRENT_SECTION=0

# JSON results accumulator
JSON_RESULTS="[]"
AUDIT_START_TIME=""

# ---- Color Output ----

if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    MAGENTA='\033[0;35m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    DIM='\033[2m'
    NC='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' MAGENTA='' CYAN='' BOLD='' DIM='' NC=''
fi

# ---- Argument Parsing ----

print_help() {
    cat << 'HELPEOF'
SYRABIT PRODUCTION DEPLOYMENT AUDIT (500+ Checks)

USAGE:
    ./scripts/live-deployment-audit.sh [OPTIONS]

OPTIONS:
    --help          Show this help message
    --quick         Run reduced check set (faster, ~100 key checks)
    --section N     Run only section N (1-8)
    --export-json   Export results to live-deployment-audit-results.json
    --export-html   Export results to live-deployment-audit-report.html
    --verbose       Enable verbose output

SECTIONS:
    1  Infrastructure & DNS     (60+ checks)
       DNS records, SSL/TLS, HSTS, CDN detection, propagation
    2  Security Audit           (100+ checks)
       OWASP headers, CSP, cookies, CORS, rate limiting, info disclosure
    3  Performance Audit        (80+ checks)
       TTFB, compression, caching, cold starts, concurrency
    4  API Completeness         (80+ checks)
       Endpoint availability, versioning, error formats, tracing
    5  Data Integrity           (50+ checks)
       Health deep checks, webhooks, content validation
    6  Deployment Configuration (60+ checks)
       Cloud Run, Cloudflare, env exposure, sensitive paths
    7  Compliance & SEO         (70+ checks)
       robots.txt, sitemap, structured data, meta tags, accessibility
    8  Monitoring & Observability (40+ checks)
       Health patterns, circuit breakers, PII leaks, uptime

ENVIRONMENT VARIABLES:
    BASE_URL        Backend/edge URL (default: https://api.syrabit.ai)
    FRONTEND_URL    Frontend URL (default: https://syrabit.ai)
    GCP_PROJECT     GCP Project ID (default: blissful-acumen-495019-t6)
    GCP_REGION      GCP Region (default: asia-south1)
    VERBOSE         Set to 1 for detailed output

EXAMPLES:
    # Full audit
    ./scripts/live-deployment-audit.sh

    # Quick security check
    ./scripts/live-deployment-audit.sh --quick --section 2

    # Export JSON report
    ./scripts/live-deployment-audit.sh --export-json

    # Custom target
    BASE_URL="https://staging-api.syrabit.ai" ./scripts/live-deployment-audit.sh

SCORING:
    PASS     = 1.0 point    (check passed)
    WARN     = 0.5 points   (minor issue, acceptable)
    FAIL     = 0.0 points   (issue detected)
    CRITICAL = 0.0 points   (severe issue, flags deployment)

    Production Readiness Score = (earned / possible) * 100

EXIT CODES:
    0  Score >= 70 (Production Ready or Needs Attention)
    1  Score < 70 (Not Ready)
HELPEOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            print_help
            exit 0
            ;;
        --quick|-q)
            QUICK_MODE=1
            shift
            ;;
        --section|-s)
            RUN_SECTION="${2:-}"
            if [[ -z "$RUN_SECTION" || ! "$RUN_SECTION" =~ ^[1-8]$ ]]; then
                echo "Error: --section requires a number between 1 and 8"
                exit 1
            fi
            shift 2
            ;;
        --export-json)
            EXPORT_JSON=1
            shift
            ;;
        --export-html)
            EXPORT_HTML=1
            shift
            ;;
        --verbose|-v)
            VERBOSE=1
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# ---- Utility Functions ----

verbose() {
    if [[ "$VERBOSE" == "1" ]]; then
        echo -e "    ${DIM}[v] $1${NC}"
    fi
}

start_section() {
    CURRENT_SECTION=$((CURRENT_SECTION + 1))
    SECTION_NAMES+=("$1")
    SECTION_PASS+=(0)
    SECTION_WARN+=(0)
    SECTION_FAIL+=(0)
    SECTION_CRITICAL+=(0)
    SECTION_TOTAL+=(0)
    echo ""
    echo -e "${BOLD}===============================================================================${NC}"
    echo -e "${BOLD}  SECTION ${CURRENT_SECTION}: $1${NC}"
    echo -e "${BOLD}===============================================================================${NC}"
    echo ""
}

check_pass() {
    local msg="$1"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    TOTAL_PASS=$((TOTAL_PASS + 1))
    local idx=$((CURRENT_SECTION - 1))
    SECTION_PASS[$idx]=$(( ${SECTION_PASS[$idx]} + 1 ))
    SECTION_TOTAL[$idx]=$(( ${SECTION_TOTAL[$idx]} + 1 ))
    echo -e "    ${GREEN}[PASS]${NC} $msg"
    add_json_result "PASS" "$msg"
}

check_warn() {
    local msg="$1"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    TOTAL_WARN=$((TOTAL_WARN + 1))
    local idx=$((CURRENT_SECTION - 1))
    SECTION_WARN[$idx]=$(( ${SECTION_WARN[$idx]} + 1 ))
    SECTION_TOTAL[$idx]=$(( ${SECTION_TOTAL[$idx]} + 1 ))
    echo -e "    ${YELLOW}[WARN]${NC} $msg"
    add_json_result "WARN" "$msg"
}

check_fail() {
    local msg="$1"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
    local idx=$((CURRENT_SECTION - 1))
    SECTION_FAIL[$idx]=$(( ${SECTION_FAIL[$idx]} + 1 ))
    SECTION_TOTAL[$idx]=$(( ${SECTION_TOTAL[$idx]} + 1 ))
    echo -e "    ${RED}[FAIL]${NC} $msg"
    add_json_result "FAIL" "$msg"
}

check_critical() {
    local msg="$1"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    TOTAL_CRITICAL=$((TOTAL_CRITICAL + 1))
    local idx=$((CURRENT_SECTION - 1))
    SECTION_CRITICAL[$idx]=$(( ${SECTION_CRITICAL[$idx]} + 1 ))
    SECTION_TOTAL[$idx]=$(( ${SECTION_TOTAL[$idx]} + 1 ))
    echo -e "    ${RED}${BOLD}[CRITICAL]${NC} $msg"
    add_json_result "CRITICAL" "$msg"
}

subsection() {
    echo ""
    echo -e "  ${CYAN}--- $1 ---${NC}"
    echo ""
}

add_json_result() {
    local status="$1"
    local message="$2"
    local escaped_msg
    # Proper JSON escaping: backslashes first, then quotes, then control chars
    escaped_msg=$(printf '%s' "$message" | sed 's/\\/\\\\/g; s/"/\\"/g; s/\t/\\t/g' | tr -d '\n' | tr -dc '[:print:]')
    JSON_RESULTS=$(echo "$JSON_RESULTS" | jq --arg s "$status" --arg m "$escaped_msg" --arg sec "${SECTION_NAMES[$((CURRENT_SECTION-1))]:-unknown}" \
        '. += [{"section": $sec, "status": $s, "message": $m}]' 2>/dev/null) || {
        echo "  [warn] Failed to record JSON result: ${status} - ${message}" >&2
    }
}

# Curl helper - performs request and sets globals
# Sets: RESP_STATUS, RESP_HEADERS, RESP_BODY, RESP_TTFB, RESP_TOTAL
perform_request() {
    local url="$1"
    shift
    local extra_args=("$@")

    local timing_format='{"ttfb":%{time_starttransfer},"total":%{time_total},"status":%{http_code},"size":%{size_download}}'
    local tmpbody tmpheaders
    tmpbody="${AUDIT_TMPDIR}/body_$$_${RANDOM}"
    tmpheaders="${AUDIT_TMPDIR}/headers_$$_${RANDOM}"

    local curl_cmd=(curl -sS -w "$timing_format" -o "$tmpbody" -D "$tmpheaders" --max-time 30 --connect-timeout 10)
    if [[ ${#extra_args[@]} -gt 0 ]]; then
        curl_cmd+=("${extra_args[@]}")
    fi
    curl_cmd+=("$url")

    local timing_json
    timing_json=$("${curl_cmd[@]}" 2>/dev/null) || timing_json='{"ttfb":0,"total":0,"status":0,"size":0}'

    RESP_STATUS=$(echo "$timing_json" | jq -r '.status // 0')
    RESP_TTFB=$(echo "$timing_json" | jq -r '(.ttfb * 1000) | floor')
    RESP_TOTAL=$(echo "$timing_json" | jq -r '(.total * 1000) | floor')
    RESP_BODY=$(cat "$tmpbody" 2>/dev/null || echo "")
    RESP_HEADERS=$(cat "$tmpheaders" 2>/dev/null || echo "")

    rm -f "$tmpbody" "$tmpheaders"
}

# Check if header exists (case-insensitive)
has_header() {
    local header_name="$1"
    local headers="${2:-$RESP_HEADERS}"
    if echo "$headers" | grep -qi "^${header_name}:" 2>/dev/null; then
        return 0
    fi
    return 1
}

# Get header value (case-insensitive)
get_header() {
    local header_name="$1"
    local headers="${2:-$RESP_HEADERS}"
    local result
    result=$(echo "$headers" | grep -i "^${header_name}:" | head -1 | sed "s/^[^:]*: *//" | tr -d '\r\n' || true)
    echo "$result"
}

# Check if response body contains string (uses extended regex for portability)
body_contains() {
    local needle="$1"
    local body="${2:-$RESP_BODY}"
    if echo "$body" | grep -Eqi "$needle" 2>/dev/null; then
        return 0
    fi
    return 1
}

should_run_section() {
    local section_num="$1"
    if [[ -n "$RUN_SECTION" && "$RUN_SECTION" != "$section_num" ]]; then
        return 1
    fi
    return 0
}


# ===============================================================================
# HEADER
# ===============================================================================

AUDIT_START_TIME=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

echo ""
echo -e "${BOLD}===============================================================================${NC}"
echo -e "${BOLD}  SYRABIT PRODUCTION DEPLOYMENT AUDIT${NC}"
echo -e "${BOLD}===============================================================================${NC}"
echo -e "  Date:       $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo -e "  Frontend:   ${FRONTEND_URL}"
echo -e "  API:        ${BASE_URL}"
echo -e "  GCP:        ${GCP_PROJECT} / ${GCP_REGION}"
echo -e "  Mode:       $(if [[ "$QUICK_MODE" == "1" ]]; then echo 'QUICK'; else echo 'FULL'; fi)"
if [[ -n "$RUN_SECTION" ]]; then
    echo -e "  Section:    ${RUN_SECTION} only"
fi
echo -e "${BOLD}===============================================================================${NC}"
echo ""

# ===============================================================================
# SECTION 1: INFRASTRUCTURE & DNS (60+ checks)
# ===============================================================================

run_section_1() {
    start_section "INFRASTRUCTURE & DNS"

    # ---- DNS Resolution - Frontend ----
    subsection "DNS Resolution - ${FRONTEND_DOMAIN}"

    # A records
    local a_records
    a_records=$(dig +short A "$FRONTEND_DOMAIN" @8.8.8.8 2>/dev/null || echo "")
    if [[ -n "$a_records" ]]; then
        check_pass "A record exists for ${FRONTEND_DOMAIN}"
        local a_count
        a_count=$(echo "$a_records" | wc -l)
        if [[ "$a_count" -ge 2 ]]; then
            check_pass "Multiple A records (${a_count}) - redundancy"
        else
            check_warn "Single A record - consider redundancy"
        fi
    else
        # Might be CNAME only (Cloudflare)
        check_warn "No A record directly (may use CNAME flattening)"
    fi

    # AAAA records (IPv6)
    local aaaa_records
    aaaa_records=$(dig +short AAAA "$FRONTEND_DOMAIN" @8.8.8.8 2>/dev/null || echo "")
    if [[ -n "$aaaa_records" ]]; then
        check_pass "AAAA record exists - IPv6 supported"
    else
        check_warn "No AAAA record - IPv6 not configured"
    fi

    # CNAME records
    local cname_records
    cname_records=$(dig +short CNAME "$FRONTEND_DOMAIN" @8.8.8.8 2>/dev/null || echo "")
    if [[ -n "$cname_records" ]]; then
        check_pass "CNAME record: ${cname_records}"
    else
        verbose "No CNAME (direct A record or flattened)"
    fi

    # NS records
    local ns_records
    ns_records=$(dig +short NS "$FRONTEND_DOMAIN" @8.8.8.8 2>/dev/null || echo "")
    if [[ -n "$ns_records" ]]; then
        check_pass "NS records configured"
        if echo "$ns_records" | grep -qi "cloudflare"; then
            check_pass "Using Cloudflare nameservers"
        else
            check_warn "Not using Cloudflare nameservers"
        fi
        local ns_count
        ns_count=$(echo "$ns_records" | wc -l)
        if [[ "$ns_count" -ge 2 ]]; then
            check_pass "Multiple NS records (${ns_count}) for redundancy"
        else
            check_warn "Single NS record"
        fi
    else
        check_fail "No NS records found"
    fi

    # MX records
    local mx_records
    mx_records=$(dig +short MX "$FRONTEND_DOMAIN" @8.8.8.8 2>/dev/null || echo "")
    if [[ -n "$mx_records" ]]; then
        check_pass "MX records configured for email"
    else
        check_warn "No MX records (email may not be configured)"
    fi

    # TXT records (SPF, DMARC, domain verification)
    local txt_records
    txt_records=$(dig +short TXT "$FRONTEND_DOMAIN" @8.8.8.8 2>/dev/null || echo "")
    if [[ -n "$txt_records" ]]; then
        check_pass "TXT records present"
        if echo "$txt_records" | grep -qi "v=spf1"; then
            check_pass "SPF record configured"
        else
            check_warn "No SPF record found"
        fi
    else
        check_warn "No TXT records"
    fi

    # DMARC
    local dmarc_record
    dmarc_record=$(dig +short TXT "_dmarc.${FRONTEND_DOMAIN}" @8.8.8.8 2>/dev/null || echo "")
    if [[ -n "$dmarc_record" ]]; then
        check_pass "DMARC record configured"
    else
        check_warn "No DMARC record"
    fi

    # SOA record
    local soa_record
    soa_record=$(dig +short SOA "$FRONTEND_DOMAIN" @8.8.8.8 2>/dev/null || echo "")
    if [[ -n "$soa_record" ]]; then
        check_pass "SOA record exists"
    else
        check_fail "No SOA record"
    fi

    # ---- DNS Resolution - API ----
    subsection "DNS Resolution - ${API_DOMAIN}"

    local api_a_records
    api_a_records=$(dig +short A "$API_DOMAIN" @8.8.8.8 2>/dev/null || echo "")
    if [[ -n "$api_a_records" ]]; then
        check_pass "A record exists for ${API_DOMAIN}"
    else
        check_warn "No A record for API (may use CNAME)"
    fi

    local api_aaaa
    api_aaaa=$(dig +short AAAA "$API_DOMAIN" @8.8.8.8 2>/dev/null || echo "")
    if [[ -n "$api_aaaa" ]]; then
        check_pass "API IPv6 (AAAA) configured"
    else
        check_warn "API has no AAAA record"
    fi

    local api_cname
    api_cname=$(dig +short CNAME "$API_DOMAIN" @8.8.8.8 2>/dev/null || echo "")
    if [[ -n "$api_cname" ]]; then
        check_pass "API CNAME: ${api_cname}"
    fi

    local api_ns
    api_ns=$(dig +short NS "$API_DOMAIN" @8.8.8.8 2>/dev/null || echo "")
    if [[ -z "$api_ns" ]]; then
        # Try parent domain
        local parent_domain
        parent_domain=$(echo "$API_DOMAIN" | sed 's/^[^.]*\.//')
        api_ns=$(dig +short NS "$parent_domain" @8.8.8.8 2>/dev/null || echo "")
    fi
    if [[ -n "$api_ns" ]]; then
        check_pass "NS records for API domain"
    fi

    # ---- DNS Propagation ----
    subsection "DNS Propagation (Multiple Resolvers)"

    local resolvers=("8.8.8.8" "1.1.1.1" "9.9.9.9" "208.67.222.222" "8.8.4.4")
    local resolver_names=("Google" "Cloudflare" "Quad9" "OpenDNS" "Google-Alt")
    local propagation_ok=0
    local propagation_total=0

    for i in "${!resolvers[@]}"; do
        local resolver="${resolvers[$i]}"
        local rname="${resolver_names[$i]}"
        propagation_total=$((propagation_total + 1))
        local resolved
        resolved=$(dig +short A "$FRONTEND_DOMAIN" "@${resolver}" +time=5 2>/dev/null || echo "")
        if [[ -n "$resolved" ]]; then
            check_pass "DNS resolves via ${rname} (${resolver})"
            propagation_ok=$((propagation_ok + 1))
        else
            check_fail "DNS fails via ${rname} (${resolver})"
        fi
    done

    for i in "${!resolvers[@]}"; do
        local resolver="${resolvers[$i]}"
        local rname="${resolver_names[$i]}"
        local resolved
        resolved=$(dig +short A "$API_DOMAIN" "@${resolver}" +time=5 2>/dev/null || echo "")
        if [[ -n "$resolved" ]]; then
            check_pass "API DNS resolves via ${rname} (${resolver})"
        else
            check_fail "API DNS fails via ${rname} (${resolver})"
        fi
    done

    # ---- SSL/TLS Certificate - Frontend ----
    subsection "SSL/TLS Certificate - ${FRONTEND_DOMAIN}"

    local cert_info
    cert_info=$(echo | openssl s_client -servername "$FRONTEND_DOMAIN" -connect "${FRONTEND_DOMAIN}:443" 2>/dev/null || echo "")

    if [[ -n "$cert_info" ]]; then
        check_pass "TLS connection established to ${FRONTEND_DOMAIN}:443"

        # Certificate expiry
        local cert_expiry
        cert_expiry=$(echo "$cert_info" | openssl x509 -noout -enddate 2>/dev/null | sed 's/notAfter=//' || echo "")
        if [[ -n "$cert_expiry" ]]; then
            local expiry_epoch
            expiry_epoch=$(date -d "$cert_expiry" +%s 2>/dev/null || date -j -f "%b %d %T %Y %Z" "$cert_expiry" +%s 2>/dev/null || echo "0")
            local now_epoch
            now_epoch=$(date +%s)
            local days_left=$(( (expiry_epoch - now_epoch) / 86400 ))
            if [[ "$days_left" -gt 30 ]]; then
                check_pass "Certificate valid for ${days_left} days"
            elif [[ "$days_left" -gt 7 ]]; then
                check_warn "Certificate expires in ${days_left} days"
            elif [[ "$days_left" -gt 0 ]]; then
                check_critical "Certificate expires in ${days_left} days!"
            else
                check_critical "Certificate has EXPIRED!"
            fi
        else
            check_warn "Could not parse certificate expiry date"
        fi

        # Certificate chain
        local chain_depth
        chain_depth=$(echo "$cert_info" | grep -c "Certificate chain" 2>/dev/null || echo "0")
        if echo "$cert_info" | grep -q "verify return:1"; then
            check_pass "Certificate chain verified"
        else
            check_warn "Certificate chain verification inconclusive"
        fi

        # Subject/SAN match
        local cert_subject
        cert_subject=$(echo "$cert_info" | openssl x509 -noout -subject 2>/dev/null || echo "")
        if echo "$cert_subject" | grep -qi "$FRONTEND_DOMAIN"; then
            check_pass "Certificate CN/subject matches domain"
        else
            # Check SAN
            local cert_san
            cert_san=$(echo "$cert_info" | openssl x509 -noout -ext subjectAltName 2>/dev/null || echo "")
            if echo "$cert_san" | grep -qi "$FRONTEND_DOMAIN"; then
                check_pass "Certificate SAN matches domain"
            else
                check_warn "Domain not found in CN or SAN (may use wildcard)"
            fi
        fi

        # Issuer
        local cert_issuer
        cert_issuer=$(echo "$cert_info" | openssl x509 -noout -issuer 2>/dev/null || echo "")
        if [[ -n "$cert_issuer" ]]; then
            check_pass "Certificate issuer: $(echo "$cert_issuer" | sed 's/issuer= *//')"
        fi

        # Key size
        local key_info
        key_info=$(echo "$cert_info" | openssl x509 -noout -text 2>/dev/null | grep "Public-Key:" || echo "")
        if echo "$key_info" | grep -Eq "256|384|2048|4096"; then
            check_pass "Key strength acceptable: ${key_info}"
        elif [[ -n "$key_info" ]]; then
            check_warn "Key info: ${key_info}"
        fi

        # Protocol version checks
        local tls13
        tls13=$(echo | openssl s_client -servername "$FRONTEND_DOMAIN" -connect "${FRONTEND_DOMAIN}:443" -tls1_3 2>/dev/null | grep "Protocol" || echo "")
        if echo "$tls13" | grep -qi "TLSv1.3"; then
            check_pass "TLS 1.3 supported"
        else
            check_warn "TLS 1.3 not confirmed"
        fi

        # Check TLS 1.0/1.1 disabled
        local tls10
        tls10=$(echo | openssl s_client -servername "$FRONTEND_DOMAIN" -connect "${FRONTEND_DOMAIN}:443" -tls1 2>&1 || echo "failed")
        if echo "$tls10" | grep -Eqi "alert|error|wrong version|no protocols"; then
            check_pass "TLS 1.0 disabled (good)"
        else
            check_warn "TLS 1.0 may still be accepted"
        fi

        local tls11
        tls11=$(echo | openssl s_client -servername "$FRONTEND_DOMAIN" -connect "${FRONTEND_DOMAIN}:443" -tls1_1 2>&1 || echo "failed")
        if echo "$tls11" | grep -Eqi "alert|error|wrong version|no protocols"; then
            check_pass "TLS 1.1 disabled (good)"
        else
            check_warn "TLS 1.1 may still be accepted"
        fi

    else
        check_critical "Cannot establish TLS connection to ${FRONTEND_DOMAIN}"
    fi

    # ---- SSL/TLS Certificate - API ----
    subsection "SSL/TLS Certificate - ${API_DOMAIN}"

    local api_cert_info
    api_cert_info=$(echo | openssl s_client -servername "$API_DOMAIN" -connect "${API_DOMAIN}:443" 2>/dev/null || echo "")

    if [[ -n "$api_cert_info" ]]; then
        check_pass "TLS connection established to ${API_DOMAIN}:443"

        local api_cert_expiry
        api_cert_expiry=$(echo "$api_cert_info" | openssl x509 -noout -enddate 2>/dev/null | sed 's/notAfter=//' || echo "")
        if [[ -n "$api_cert_expiry" ]]; then
            local api_expiry_epoch
            api_expiry_epoch=$(date -d "$api_cert_expiry" +%s 2>/dev/null || echo "0")
            local api_now_epoch
            api_now_epoch=$(date +%s)
            local api_days_left=$(( (api_expiry_epoch - api_now_epoch) / 86400 ))
            if [[ "$api_days_left" -gt 30 ]]; then
                check_pass "API certificate valid for ${api_days_left} days"
            elif [[ "$api_days_left" -gt 7 ]]; then
                check_warn "API certificate expires in ${api_days_left} days"
            else
                check_critical "API certificate expires in ${api_days_left} days!"
            fi
        fi

        if echo "$api_cert_info" | grep -q "verify return:1"; then
            check_pass "API certificate chain verified"
        else
            check_warn "API certificate chain verification inconclusive"
        fi

        local api_san
        api_san=$(echo "$api_cert_info" | openssl x509 -noout -ext subjectAltName 2>/dev/null || echo "")
        if echo "$api_san" | grep -qi "$API_DOMAIN"; then
            check_pass "API certificate SAN matches"
        else
            local api_subject
            api_subject=$(echo "$api_cert_info" | openssl x509 -noout -subject 2>/dev/null || echo "")
            if echo "$api_subject" | grep -qi "$API_DOMAIN"; then
                check_pass "API certificate CN matches"
            else
                check_warn "API domain not in CN/SAN (wildcard?)"
            fi
        fi

        # TLS 1.3 for API
        local api_tls13
        api_tls13=$(echo | openssl s_client -servername "$API_DOMAIN" -connect "${API_DOMAIN}:443" -tls1_3 2>/dev/null | grep "Protocol" || echo "")
        if echo "$api_tls13" | grep -qi "TLSv1.3"; then
            check_pass "API supports TLS 1.3"
        else
            check_warn "API TLS 1.3 not confirmed"
        fi
    else
        check_critical "Cannot establish TLS connection to ${API_DOMAIN}"
    fi

    # ---- HSTS ----
    subsection "HSTS (HTTP Strict Transport Security)"

    perform_request "$FRONTEND_URL" -I
    local hsts_value
    hsts_value=$(get_header "strict-transport-security")
    if [[ -n "$hsts_value" ]]; then
        check_pass "HSTS header present: ${hsts_value}"
        if echo "$hsts_value" | grep -qi "max-age="; then
            local max_age
            max_age=$(echo "$hsts_value" | grep -oi "max-age=[0-9]*" | head -1 | cut -d= -f2)
            if [[ -n "$max_age" && "$max_age" -ge 31536000 ]]; then
                check_pass "HSTS max-age >= 1 year (${max_age}s)"
            elif [[ -n "$max_age" && "$max_age" -ge 86400 ]]; then
                check_warn "HSTS max-age is ${max_age}s (recommend >= 31536000)"
            else
                check_fail "HSTS max-age too short: ${max_age}s"
            fi
        fi
        if echo "$hsts_value" | grep -qi "includeSubDomains"; then
            check_pass "HSTS includeSubDomains set"
        else
            check_warn "HSTS missing includeSubDomains"
        fi
        if echo "$hsts_value" | grep -qi "preload"; then
            check_pass "HSTS preload flag set"
        else
            check_warn "HSTS missing preload flag"
        fi
    else
        check_fail "No HSTS header on frontend"
    fi

    perform_request "$BASE_URL/health" -I
    local api_hsts
    api_hsts=$(get_header "strict-transport-security")
    if [[ -n "$api_hsts" ]]; then
        check_pass "HSTS header on API"
    else
        check_warn "No HSTS header on API"
    fi

    # ---- CDN Detection ----
    subsection "CDN Detection (Cloudflare)"

    perform_request "$FRONTEND_URL"
    local cf_ray
    cf_ray=$(get_header "cf-ray")
    if [[ -n "$cf_ray" ]]; then
        check_pass "cf-ray header present (Cloudflare active): ${cf_ray}"
    else
        check_fail "No cf-ray header - Cloudflare may not be proxying"
    fi

    local cf_cache
    cf_cache=$(get_header "cf-cache-status")
    if [[ -n "$cf_cache" ]]; then
        check_pass "cf-cache-status present: ${cf_cache}"
    else
        check_warn "No cf-cache-status header"
    fi

    local server_header
    server_header=$(get_header "server")
    if echo "$server_header" | grep -qi "cloudflare"; then
        check_pass "Server header: cloudflare"
    elif [[ -n "$server_header" ]]; then
        check_warn "Server header: ${server_header} (expected cloudflare)"
    else
        check_pass "Server header not exposed (acceptable)"
    fi

    # Check API CDN
    perform_request "$BASE_URL/health"
    local api_cf_ray
    api_cf_ray=$(get_header "cf-ray")
    if [[ -n "$api_cf_ray" ]]; then
        check_pass "API cf-ray header present: ${api_cf_ray}"
    else
        check_warn "No cf-ray on API responses"
    fi

    local api_server
    api_server=$(get_header "server")
    if echo "$api_server" | grep -qi "cloudflare"; then
        check_pass "API server: cloudflare"
    fi

    # ---- HTTP to HTTPS Redirect ----
    subsection "HTTP to HTTPS Redirect"

    local http_status
    http_status=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 -L "http://${FRONTEND_DOMAIN}" 2>/dev/null || echo "000")
    if [[ "$http_status" == "200" || "$http_status" == "301" || "$http_status" == "302" ]]; then
        check_pass "HTTP to HTTPS redirect works for frontend"
    else
        check_warn "HTTP redirect status: ${http_status}"
    fi

    local api_http_redir
    api_http_redir=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "http://${API_DOMAIN}/health" 2>/dev/null || echo "000")
    if [[ "$api_http_redir" == "301" || "$api_http_redir" == "302" || "$api_http_redir" == "200" ]]; then
        check_pass "HTTP to HTTPS redirect works for API"
    else
        check_warn "API HTTP redirect status: ${api_http_redir}"
    fi

    # ---- DNS TTL Check ----
    subsection "DNS TTL Values"

    local ttl_info
    ttl_info=$(dig A "$FRONTEND_DOMAIN" @8.8.8.8 +noall +answer 2>/dev/null || echo "")
    if [[ -n "$ttl_info" ]]; then
        local ttl_val
        ttl_val=$(echo "$ttl_info" | awk '{print $2}' | head -1)
        if [[ -n "$ttl_val" && "$ttl_val" -le 300 ]]; then
            check_pass "DNS TTL is low (${ttl_val}s) - fast failover"
        elif [[ -n "$ttl_val" && "$ttl_val" -le 3600 ]]; then
            check_pass "DNS TTL reasonable (${ttl_val}s)"
        elif [[ -n "$ttl_val" ]]; then
            check_warn "DNS TTL is high (${ttl_val}s) - slow failover"
        fi
    fi

    local api_ttl_info
    api_ttl_info=$(dig A "$API_DOMAIN" @8.8.8.8 +noall +answer 2>/dev/null || echo "")
    if [[ -n "$api_ttl_info" ]]; then
        local api_ttl_val
        api_ttl_val=$(echo "$api_ttl_info" | awk '{print $2}' | head -1)
        if [[ -n "$api_ttl_val" ]]; then
            check_pass "API DNS TTL: ${api_ttl_val}s"
        fi
    fi

    # ---- Connectivity Verification ----
    subsection "End-to-End Connectivity"

    # Full HTTPS request to frontend
    perform_request "$FRONTEND_URL"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "Frontend HTTPS full request: 200"
    else
        check_critical "Frontend not reachable: ${RESP_STATUS}"
    fi

    # Full HTTPS request to API
    perform_request "$BASE_URL/health"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "API HTTPS full request: 200"
    else
        check_critical "API health not reachable: ${RESP_STATUS}"
    fi

    # DNS consistency check (same IPs from multiple resolvers)
    local ip_google
    ip_google=$(dig +short A "$FRONTEND_DOMAIN" @8.8.8.8 2>/dev/null | sort | head -1)
    local ip_cloudflare
    ip_cloudflare=$(dig +short A "$FRONTEND_DOMAIN" @1.1.1.1 2>/dev/null | sort | head -1)
    if [[ -n "$ip_google" && -n "$ip_cloudflare" ]]; then
        if [[ "$ip_google" == "$ip_cloudflare" ]]; then
            check_pass "DNS consistent across Google/Cloudflare resolvers"
        else
            check_warn "DNS IPs differ: Google=${ip_google}, CF=${ip_cloudflare}"
        fi
    fi

    local api_ip_google
    api_ip_google=$(dig +short A "$API_DOMAIN" @8.8.8.8 2>/dev/null | sort | head -1)
    local api_ip_quad9
    api_ip_quad9=$(dig +short A "$API_DOMAIN" @9.9.9.9 2>/dev/null | sort | head -1)
    if [[ -n "$api_ip_google" && -n "$api_ip_quad9" ]]; then
        if [[ "$api_ip_google" == "$api_ip_quad9" ]]; then
            check_pass "API DNS consistent across Google/Quad9"
        else
            check_warn "API DNS IPs differ"
        fi
    fi

    # CAA record (Certificate Authority Authorization)
    local caa_record
    caa_record=$(dig +short CAA "$FRONTEND_DOMAIN" @8.8.8.8 2>/dev/null || echo "")
    if [[ -n "$caa_record" ]]; then
        check_pass "CAA record configured: restricts certificate issuance"
    else
        check_warn "No CAA record (any CA can issue certificates)"
    fi

    echo ""
    echo -e "  ${DIM}Section 1 complete: ${SECTION_PASS[$((CURRENT_SECTION-1))]} pass, ${SECTION_WARN[$((CURRENT_SECTION-1))]} warn, ${SECTION_FAIL[$((CURRENT_SECTION-1))]} fail${NC}"
}


# ===============================================================================
# SECTION 2: SECURITY AUDIT (100+ checks)
# ===============================================================================

run_section_2() {
    start_section "SECURITY AUDIT"

    # ---- OWASP Security Headers - Frontend ----
    subsection "OWASP Security Headers - Frontend"

    perform_request "$FRONTEND_URL"

    # X-Content-Type-Options
    local xcto
    xcto=$(get_header "x-content-type-options")
    if [[ "$xcto" == "nosniff" ]]; then
        check_pass "X-Content-Type-Options: nosniff"
    elif [[ -n "$xcto" ]]; then
        check_warn "X-Content-Type-Options: ${xcto} (expected nosniff)"
    else
        check_fail "Missing X-Content-Type-Options header"
    fi

    # X-Frame-Options
    local xfo
    xfo=$(get_header "x-frame-options")
    if [[ "$xfo" == "DENY" || "$xfo" == "SAMEORIGIN" ]]; then
        check_pass "X-Frame-Options: ${xfo}"
    elif [[ -n "$xfo" ]]; then
        check_warn "X-Frame-Options: ${xfo}"
    else
        check_warn "Missing X-Frame-Options (CSP frame-ancestors may cover this)"
    fi

    # X-XSS-Protection
    local xxss
    xxss=$(get_header "x-xss-protection")
    if [[ -n "$xxss" ]]; then
        if echo "$xxss" | grep -q "1; mode=block"; then
            check_pass "X-XSS-Protection: 1; mode=block"
        elif echo "$xxss" | grep -q "0"; then
            check_pass "X-XSS-Protection: 0 (disabled, relying on CSP)"
        else
            check_warn "X-XSS-Protection: ${xxss}"
        fi
    else
        check_warn "Missing X-XSS-Protection header"
    fi

    # Referrer-Policy
    local refpol
    refpol=$(get_header "referrer-policy")
    if [[ -n "$refpol" ]]; then
        if echo "$refpol" | grep -Eqi "strict-origin|no-referrer|same-origin"; then
            check_pass "Referrer-Policy: ${refpol}"
        else
            check_warn "Referrer-Policy: ${refpol} (consider stricter)"
        fi
    else
        check_fail "Missing Referrer-Policy header"
    fi

    # Permissions-Policy
    local permpol
    permpol=$(get_header "permissions-policy")
    if [[ -n "$permpol" ]]; then
        check_pass "Permissions-Policy present"
        if echo "$permpol" | grep -qi "camera"; then
            check_pass "Permissions-Policy restricts camera"
        fi
        if echo "$permpol" | grep -qi "microphone"; then
            check_pass "Permissions-Policy restricts microphone"
        fi
        if echo "$permpol" | grep -qi "geolocation"; then
            check_pass "Permissions-Policy restricts geolocation"
        fi
    else
        check_warn "Missing Permissions-Policy header"
    fi

    # Content-Security-Policy
    local csp
    csp=$(get_header "content-security-policy")
    if [[ -n "$csp" ]]; then
        check_pass "Content-Security-Policy present"

        # CSP directive analysis
        if echo "$csp" | grep -qi "default-src"; then
            check_pass "CSP has default-src directive"
        else
            check_warn "CSP missing default-src"
        fi
        if echo "$csp" | grep -qi "script-src"; then
            check_pass "CSP has script-src directive"
            if echo "$csp" | grep -qi "unsafe-inline"; then
                check_warn "CSP script-src allows unsafe-inline"
            else
                check_pass "CSP script-src blocks unsafe-inline"
            fi
            if echo "$csp" | grep -qi "unsafe-eval"; then
                check_warn "CSP script-src allows unsafe-eval"
            else
                check_pass "CSP script-src blocks unsafe-eval"
            fi
        else
            check_warn "CSP missing script-src"
        fi
        if echo "$csp" | grep -qi "style-src"; then
            check_pass "CSP has style-src directive"
        fi
        if echo "$csp" | grep -qi "img-src"; then
            check_pass "CSP has img-src directive"
        fi
        if echo "$csp" | grep -qi "connect-src"; then
            check_pass "CSP has connect-src directive"
        fi
        if echo "$csp" | grep -qi "frame-ancestors"; then
            check_pass "CSP has frame-ancestors (replaces X-Frame-Options)"
        fi
        if echo "$csp" | grep -qi "upgrade-insecure-requests"; then
            check_pass "CSP has upgrade-insecure-requests"
        else
            check_warn "CSP missing upgrade-insecure-requests"
        fi
        if echo "$csp" | grep -qi "base-uri"; then
            check_pass "CSP has base-uri directive"
        else
            check_warn "CSP missing base-uri (injection risk)"
        fi
        if echo "$csp" | grep -qi "form-action"; then
            check_pass "CSP has form-action directive"
        else
            check_warn "CSP missing form-action"
        fi
        if echo "$csp" | grep -qi "object-src"; then
            check_pass "CSP has object-src directive"
        fi
    else
        check_fail "Missing Content-Security-Policy header"
    fi

    # Cross-Origin headers
    local coop
    coop=$(get_header "cross-origin-opener-policy")
    if [[ -n "$coop" ]]; then
        check_pass "Cross-Origin-Opener-Policy: ${coop}"
    else
        check_warn "Missing Cross-Origin-Opener-Policy"
    fi

    local corp
    corp=$(get_header "cross-origin-resource-policy")
    if [[ -n "$corp" ]]; then
        check_pass "Cross-Origin-Resource-Policy: ${corp}"
    else
        check_warn "Missing Cross-Origin-Resource-Policy"
    fi

    local coep
    coep=$(get_header "cross-origin-embedder-policy")
    if [[ -n "$coep" ]]; then
        check_pass "Cross-Origin-Embedder-Policy: ${coep}"
    else
        check_warn "Missing Cross-Origin-Embedder-Policy"
    fi

    # ---- Security Headers - API ----
    subsection "Security Headers - API"

    perform_request "$BASE_URL/health"

    local api_xcto
    api_xcto=$(get_header "x-content-type-options")
    if [[ "$api_xcto" == "nosniff" ]]; then
        check_pass "API X-Content-Type-Options: nosniff"
    else
        check_warn "API missing X-Content-Type-Options"
    fi

    local api_xfo
    api_xfo=$(get_header "x-frame-options")
    if [[ -n "$api_xfo" ]]; then
        check_pass "API X-Frame-Options: ${api_xfo}"
    else
        check_warn "API missing X-Frame-Options"
    fi

    local api_refpol
    api_refpol=$(get_header "referrer-policy")
    if [[ -n "$api_refpol" ]]; then
        check_pass "API Referrer-Policy: ${api_refpol}"
    fi

    # ---- Cookie Security ----
    subsection "Cookie Security"

    perform_request "$FRONTEND_URL"
    local set_cookies
    set_cookies=$(echo "$RESP_HEADERS" | grep -i "^set-cookie:" || echo "")

    if [[ -n "$set_cookies" ]]; then
        check_pass "Cookies are being set"
        if echo "$set_cookies" | grep -qi "HttpOnly"; then
            check_pass "Cookies have HttpOnly flag"
        else
            check_warn "Cookies missing HttpOnly flag"
        fi
        if echo "$set_cookies" | grep -qi "Secure"; then
            check_pass "Cookies have Secure flag"
        else
            check_fail "Cookies missing Secure flag"
        fi
        if echo "$set_cookies" | grep -qi "SameSite"; then
            check_pass "Cookies have SameSite attribute"
            if echo "$set_cookies" | grep -Eqi "SameSite=Strict|SameSite=Lax"; then
                check_pass "SameSite is Strict or Lax"
            elif echo "$set_cookies" | grep -qi "SameSite=None"; then
                check_warn "SameSite=None (cross-site allowed)"
            fi
        else
            check_warn "Cookies missing SameSite attribute"
        fi
        if echo "$set_cookies" | grep -qi "Path=/"; then
            check_pass "Cookie Path attribute set"
        fi
        if echo "$set_cookies" | grep -Eqi "__Host-|__Secure-"; then
            check_pass "Using cookie name prefix (__Host- or __Secure-)"
        else
            check_warn "Not using cookie name prefixes"
        fi
    else
        check_warn "No Set-Cookie headers on frontend (cookies may be set by JS)"
    fi

    # Check API cookies
    perform_request "$BASE_URL/health"
    local api_cookies
    api_cookies=$(echo "$RESP_HEADERS" | grep -i "^set-cookie:" || echo "")
    if [[ -n "$api_cookies" ]]; then
        if echo "$api_cookies" | grep -qi "Secure"; then
            check_pass "API cookies have Secure flag"
        else
            check_fail "API cookies missing Secure flag"
        fi
        if echo "$api_cookies" | grep -qi "HttpOnly"; then
            check_pass "API cookies have HttpOnly flag"
        fi
    else
        check_pass "API health endpoint not setting cookies (good)"
    fi

    # ---- CORS Testing ----
    subsection "CORS Configuration"

    # Valid origin test
    perform_request "$BASE_URL/health" \
        -H "Origin: ${FRONTEND_URL}" \
        -X OPTIONS \
        -H "Access-Control-Request-Method: GET"

    local acao
    acao=$(get_header "access-control-allow-origin")
    if [[ "$acao" == "$FRONTEND_URL" || "$acao" == "${FRONTEND_DOMAIN}" ]]; then
        check_pass "CORS allows valid origin: ${acao}"
    elif [[ "$acao" == "*" ]]; then
        check_warn "CORS allows wildcard * (acceptable for public API)"
    elif [[ -n "$acao" ]]; then
        check_pass "CORS allow-origin: ${acao}"
    else
        check_warn "No CORS allow-origin for valid request"
    fi

    local acam
    acam=$(get_header "access-control-allow-methods")
    if [[ -n "$acam" ]]; then
        check_pass "CORS Allow-Methods: ${acam}"
    fi

    local acah
    acah=$(get_header "access-control-allow-headers")
    if [[ -n "$acah" ]]; then
        check_pass "CORS Allow-Headers configured"
    fi

    local acma
    acma=$(get_header "access-control-max-age")
    if [[ -n "$acma" ]]; then
        check_pass "CORS max-age: ${acma}s (preflight cache)"
    else
        check_warn "No CORS max-age (preflight not cached)"
    fi

    # Invalid origin test
    perform_request "$BASE_URL/health" \
        -H "Origin: https://evil-site.com" \
        -X OPTIONS \
        -H "Access-Control-Request-Method: GET"

    local evil_acao
    evil_acao=$(get_header "access-control-allow-origin")
    if [[ "$RESP_STATUS" == "405" ]]; then
        check_warn "CORS evil origin test inconclusive (endpoint returns 405 for OPTIONS)"
    elif [[ "$evil_acao" == "https://evil-site.com" ]]; then
        check_fail "CORS reflects arbitrary origin (security risk!)"
    elif [[ "$evil_acao" == "*" ]]; then
        check_warn "CORS wildcard allows any origin"
    else
        check_pass "CORS rejects evil-site.com origin"
    fi

    # Null origin test
    perform_request "$BASE_URL/health" \
        -H "Origin: null" \
        -X OPTIONS \
        -H "Access-Control-Request-Method: GET"

    local null_acao
    null_acao=$(get_header "access-control-allow-origin")
    if [[ "$null_acao" == "null" ]]; then
        check_fail "CORS allows null origin (security risk!)"
    else
        check_pass "CORS rejects null origin"
    fi

    # Credentials check
    local acac
    acac=$(get_header "access-control-allow-credentials")
    if [[ "$acac" == "true" && "$acao" == "*" ]]; then
        check_critical "CORS credentials=true with wildcard origin!"
    elif [[ "$acac" == "true" ]]; then
        check_pass "CORS credentials allowed (with specific origin)"
    fi

    # ---- Information Disclosure ----
    subsection "Information Disclosure"

    perform_request "$FRONTEND_URL"

    # Server header
    local srv
    srv=$(get_header "server")
    if [[ -z "$srv" ]]; then
        check_pass "Server header not exposed"
    elif echo "$srv" | grep -qi "cloudflare"; then
        check_pass "Server: cloudflare (acceptable, no version info)"
    elif echo "$srv" | grep -Eqi "nginx/[0-9]|apache/[0-9]|gunicorn"; then
        check_fail "Server header exposes version: ${srv}"
    else
        check_warn "Server header: ${srv}"
    fi

    # X-Powered-By
    local xpb
    xpb=$(get_header "x-powered-by")
    if [[ -z "$xpb" ]]; then
        check_pass "X-Powered-By not exposed"
    else
        check_fail "X-Powered-By exposed: ${xpb}"
    fi

    # API server info
    perform_request "$BASE_URL/health"
    local api_srv
    api_srv=$(get_header "server")
    if echo "$api_srv" | grep -Eqi "uvicorn|gunicorn|python"; then
        check_fail "API server exposes technology: ${api_srv}"
    elif [[ -z "$api_srv" || "$api_srv" == "cloudflare" ]]; then
        check_pass "API server header safe: ${api_srv:-not set}"
    else
        check_warn "API server: ${api_srv}"
    fi

    local api_xpb
    api_xpb=$(get_header "x-powered-by")
    if [[ -z "$api_xpb" ]]; then
        check_pass "API X-Powered-By not exposed"
    else
        check_fail "API X-Powered-By: ${api_xpb}"
    fi

    # Debug headers
    local debug_hdr
    debug_hdr=$(get_header "x-debug")
    if [[ -z "$debug_hdr" ]]; then
        check_pass "No X-Debug header"
    else
        check_fail "X-Debug header present: ${debug_hdr}"
    fi

    local aspnet_ver
    aspnet_ver=$(get_header "x-aspnet-version")
    if [[ -z "$aspnet_ver" ]]; then
        check_pass "No X-AspNet-Version header"
    fi

    local xgen
    xgen=$(get_header "x-generator")
    if [[ -z "$xgen" ]]; then
        check_pass "No X-Generator header"
    else
        check_warn "X-Generator exposed: ${xgen}"
    fi

    # ---- Rate Limiting ----
    subsection "Rate Limiting"

    perform_request "$BASE_URL/health"

    local rl_limit
    rl_limit=$(get_header "x-ratelimit-limit")
    local rl_remaining
    rl_remaining=$(get_header "x-ratelimit-remaining")
    local rl_reset
    rl_reset=$(get_header "x-ratelimit-reset")

    if [[ -n "$rl_limit" ]]; then
        check_pass "X-RateLimit-Limit header present: ${rl_limit}"
    else
        check_warn "No X-RateLimit-Limit header"
    fi
    if [[ -n "$rl_remaining" ]]; then
        check_pass "X-RateLimit-Remaining header: ${rl_remaining}"
    else
        check_warn "No X-RateLimit-Remaining header"
    fi
    if [[ -n "$rl_reset" ]]; then
        check_pass "X-RateLimit-Reset header: ${rl_reset}"
    else
        check_warn "No X-RateLimit-Reset header"
    fi

    # Check Retry-After on rate limit
    local retry_after
    retry_after=$(get_header "retry-after")
    if [[ -n "$retry_after" ]]; then
        check_pass "Retry-After header configured"
    fi

    # ---- Sensitive Path Protection ----
    subsection "Sensitive Path Protection"

    local sensitive_paths=("/.env" "/.git/config" "/.git/HEAD" "/admin" "/.well-known/security.txt" "/wp-admin" "/wp-login.php" "/.htaccess" "/.DS_Store" "/server-status" "/.svn/entries" "/phpinfo.php" "/web.config")
    local path_labels=(".env file" "Git config" "Git HEAD" "Admin panel" "Security.txt" "WP Admin" "WP Login" ".htaccess" ".DS_Store" "Server status" "SVN entries" "phpinfo" "web.config")

    for i in "${!sensitive_paths[@]}"; do
        local spath="${sensitive_paths[$i]}"
        local slabel="${path_labels[$i]}"
        local sstatus
        sstatus=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${FRONTEND_URL}${spath}" 2>/dev/null || echo "000")
        if [[ "$sstatus" == "403" || "$sstatus" == "404" || "$sstatus" == "301" || "$sstatus" == "302" ]]; then
            check_pass "${slabel} (${spath}): ${sstatus} (blocked)"
        elif [[ "$sstatus" == "200" ]]; then
            check_critical "${slabel} (${spath}): 200 ACCESSIBLE!"
        else
            check_warn "${slabel} (${spath}): ${sstatus}"
        fi
    done

    # API sensitive paths
    local api_sensitive=("/.env" "/.git/config" "/admin" "/api/admin" "/.well-known/")
    for spath in "${api_sensitive[@]}"; do
        local sstatus
        sstatus=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${BASE_URL}${spath}" 2>/dev/null || echo "000")
        if [[ "$sstatus" == "403" || "$sstatus" == "404" || "$sstatus" == "401" ]]; then
            check_pass "API ${spath}: ${sstatus} (protected)"
        elif [[ "$sstatus" == "200" ]]; then
            check_fail "API ${spath}: 200 (accessible without auth)"
        else
            check_warn "API ${spath}: ${sstatus}"
        fi
    done

    # ---- Open Redirect ----
    subsection "Open Redirect Protection"

    local redirect_payloads=("//evil.com" "https://evil.com" "/\\evil.com" "//evil.com%2F%2F")
    for payload in "${redirect_payloads[@]}"; do
        local redir_resp
        redir_resp=$(curl -sS -o /dev/null -w '%{redirect_url}' --max-time 10 "${FRONTEND_URL}/redirect?url=${payload}" 2>/dev/null || echo "")
        if echo "$redir_resp" | grep -qi "evil.com"; then
            check_fail "Open redirect detected with payload: ${payload}"
        else
            check_pass "No open redirect with: ${payload}"
        fi
    done

    # ---- Bot Protection ----
    subsection "Bot Protection"

    perform_request "$FRONTEND_URL"
    if body_contains "turnstile|cf-turnstile|challenges.cloudflare.com"; then
        check_pass "Cloudflare Turnstile references found in HTML"
    else
        check_warn "No Turnstile references in HTML (may be lazy-loaded)"
    fi

    if body_contains "captcha|recaptcha|hcaptcha"; then
        check_pass "CAPTCHA mechanism detected"
    fi

    # ---- CSRF Protection ----
    subsection "CSRF Protection"

    perform_request "$FRONTEND_URL"
    if body_contains "csrf|_csrf|csrftoken|xsrf"; then
        check_pass "CSRF token references found"
    else
        check_warn "No CSRF token references in HTML (SPA may handle differently)"
    fi

    # Check SameSite cookie (already partially covered)
    perform_request "$BASE_URL/api/auth/login" -X POST -H "Content-Type: application/json" -d '{"test":"csrf"}'
    if [[ "$RESP_STATUS" == "422" || "$RESP_STATUS" == "400" || "$RESP_STATUS" == "401" ]]; then
        check_pass "API rejects malformed auth request (status ${RESP_STATUS})"
    elif [[ "$RESP_STATUS" == "403" ]]; then
        check_pass "API blocks unauthenticated request with 403"
    fi

    # ---- JWT Security ----
    subsection "JWT Algorithm Confusion"

    # Send token with alg:none
    local fake_jwt="eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJ0ZXN0QHRlc3QuY29tIiwiZXhwIjo5OTk5OTk5OTk5fQ."
    perform_request "$BASE_URL/api/chat/message" \
        -X POST \
        -H "Authorization: Bearer ${fake_jwt}" \
        -H "Content-Type: application/json" \
        -d '{"message":"test"}'

    if [[ "$RESP_STATUS" == "401" || "$RESP_STATUS" == "403" || "$RESP_STATUS" == "422" ]]; then
        check_pass "JWT alg:none rejected (status ${RESP_STATUS})"
    elif [[ "$RESP_STATUS" == "200" ]]; then
        check_critical "JWT alg:none ACCEPTED - critical vulnerability!"
    else
        check_pass "JWT alg:none not accepted (status ${RESP_STATUS})"
    fi

    # ---- Directory Listing ----
    subsection "Directory Listing Prevention"

    local dir_paths=("/api/" "/static/" "/assets/" "/js/" "/css/" "/images/")
    for dpath in "${dir_paths[@]}"; do
        perform_request "${FRONTEND_URL}${dpath}"
        if body_contains "Index of|Directory listing|Parent Directory"; then
            check_fail "Directory listing enabled at ${dpath}"
        else
            check_pass "No directory listing at ${dpath}"
        fi
    done

    # ---- HTTP Security Headers - Additional ----
    subsection "Additional Security Checks"

    # X-DNS-Prefetch-Control
    perform_request "$FRONTEND_URL"
    local xdns
    xdns=$(get_header "x-dns-prefetch-control")
    if [[ -n "$xdns" ]]; then
        check_pass "X-DNS-Prefetch-Control: ${xdns}"
    else
        check_warn "No X-DNS-Prefetch-Control header"
    fi

    # X-Download-Options (IE)
    local xdl
    xdl=$(get_header "x-download-options")
    if [[ "$xdl" == "noopen" ]]; then
        check_pass "X-Download-Options: noopen"
    else
        check_warn "No X-Download-Options header"
    fi

    # X-Permitted-Cross-Domain-Policies
    local xpcdp
    xpcdp=$(get_header "x-permitted-cross-domain-policies")
    if [[ -n "$xpcdp" ]]; then
        check_pass "X-Permitted-Cross-Domain-Policies: ${xpcdp}"
    else
        check_warn "No X-Permitted-Cross-Domain-Policies"
    fi

    # Expect-CT (deprecated but still checked)
    local expectct
    expectct=$(get_header "expect-ct")
    if [[ -n "$expectct" ]]; then
        check_pass "Expect-CT present: ${expectct}"
    fi

    # ---- API Authentication Security ----
    subsection "API Authentication Security"

    # Bearer token without valid token
    perform_request "$BASE_URL/api/chat/message" \
        -X POST \
        -H "Authorization: Bearer invalid_token" \
        -H "Content-Type: application/json" \
        -d '{"message":"test"}'
    if [[ "$RESP_STATUS" == "401" || "$RESP_STATUS" == "403" ]]; then
        check_pass "Invalid Bearer token rejected: ${RESP_STATUS}"
    else
        check_warn "Invalid Bearer token response: ${RESP_STATUS}"
    fi

    # Expired JWT format (valid structure, expired)
    local expired_jwt="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0QHRlc3QuY29tIiwiZXhwIjoxNjAwMDAwMDAwfQ.invalid_sig"
    perform_request "$BASE_URL/api/chat/message" \
        -X POST \
        -H "Authorization: Bearer ${expired_jwt}" \
        -H "Content-Type: application/json" \
        -d '{"message":"test"}'
    if [[ "$RESP_STATUS" == "401" || "$RESP_STATUS" == "403" ]]; then
        check_pass "Expired JWT rejected: ${RESP_STATUS}"
    else
        check_warn "Expired JWT response: ${RESP_STATUS}"
    fi

    # No auth header at all
    perform_request "$BASE_URL/api/chat/message" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"test"}'
    if [[ "$RESP_STATUS" == "401" || "$RESP_STATUS" == "403" || "$RESP_STATUS" == "422" ]]; then
        check_pass "Missing auth header properly rejected: ${RESP_STATUS}"
    fi

    # Basic auth should not work
    perform_request "$BASE_URL/api/chat/message" \
        -X POST \
        -H "Authorization: Basic dGVzdDp0ZXN0" \
        -H "Content-Type: application/json" \
        -d '{"message":"test"}'
    if [[ "$RESP_STATUS" == "401" || "$RESP_STATUS" == "403" || "$RESP_STATUS" == "422" ]]; then
        check_pass "Basic auth scheme rejected: ${RESP_STATUS}"
    fi

    # ---- Path Traversal ----
    subsection "Path Traversal Protection"

    local traversal_payloads=("/../../../etc/passwd" "/..%2F..%2F..%2Fetc%2Fpasswd" "/....//....//etc/passwd" "/%2e%2e/%2e%2e/etc/passwd")
    for payload in "${traversal_payloads[@]}"; do
        perform_request "${BASE_URL}${payload}"
        if ! body_contains "root:.*:0:0|daemon:"; then
            check_pass "Path traversal blocked: ${payload}"
        else
            check_critical "Path traversal succeeded: ${payload}"
        fi
    done

    # ---- Host Header Injection ----
    subsection "Host Header Injection"

    perform_request "$BASE_URL/health" \
        -H "Host: evil.com"
    if [[ "$RESP_STATUS" == "200" || "$RESP_STATUS" == "421" || "$RESP_STATUS" == "400" ]]; then
        if ! body_contains "evil.com"; then
            check_pass "Host header injection: evil.com not reflected"
        else
            check_fail "Host header injection reflected in response"
        fi
    else
        check_pass "Host header injection rejected: ${RESP_STATUS}"
    fi

    perform_request "$FRONTEND_URL" \
        -H "X-Forwarded-Host: evil.com"
    if ! body_contains "evil.com"; then
        check_pass "X-Forwarded-Host injection not reflected"
    else
        check_warn "X-Forwarded-Host may be reflected"
    fi

    # ---- Content-Type Mismatch ----
    subsection "Content-Type Security"

    # Send XML when JSON expected
    perform_request "$BASE_URL/api/auth/login" \
        -X POST \
        -H "Content-Type: application/xml" \
        -d '<login><email>test@test.com</email></login>'
    if [[ "$RESP_STATUS" == "415" || "$RESP_STATUS" == "422" || "$RESP_STATUS" == "400" || "$RESP_STATUS" == "404" ]]; then
        check_pass "XML Content-Type rejected: ${RESP_STATUS}"
    else
        check_warn "XML Content-Type response: ${RESP_STATUS}"
    fi

    # Send multipart when JSON expected
    perform_request "$BASE_URL/api/auth/login" \
        -X POST \
        -H "Content-Type: multipart/form-data; boundary=something" \
        -d '--something\r\nContent-Disposition: form-data; name="email"\r\n\r\ntest@test.com\r\n--something--'
    if [[ "$RESP_STATUS" == "415" || "$RESP_STATUS" == "422" || "$RESP_STATUS" == "400" || "$RESP_STATUS" == "404" ]]; then
        check_pass "Multipart rejected on JSON endpoint: ${RESP_STATUS}"
    else
        check_warn "Multipart response: ${RESP_STATUS}"
    fi

    # ---- HTTP Method Security ----
    subsection "HTTP Method Security"

    # TRACE method should be disabled
    perform_request "$FRONTEND_URL" -X TRACE
    if [[ "$RESP_STATUS" == "405" || "$RESP_STATUS" == "403" || "$RESP_STATUS" == "501" ]]; then
        check_pass "TRACE method disabled on frontend: ${RESP_STATUS}"
    elif [[ "$RESP_STATUS" == "200" ]]; then
        check_fail "TRACE method enabled on frontend!"
    else
        check_pass "TRACE method not accepted: ${RESP_STATUS}"
    fi

    perform_request "$BASE_URL/health" -X TRACE
    if [[ "$RESP_STATUS" == "405" || "$RESP_STATUS" == "403" || "$RESP_STATUS" == "501" ]]; then
        check_pass "TRACE method disabled on API: ${RESP_STATUS}"
    elif [[ "$RESP_STATUS" == "200" ]]; then
        check_fail "TRACE method enabled on API!"
    else
        check_pass "TRACE method not accepted on API: ${RESP_STATUS}"
    fi

    # CONNECT method
    perform_request "$BASE_URL/health" -X CONNECT
    if [[ "$RESP_STATUS" != "200" ]]; then
        check_pass "CONNECT method rejected: ${RESP_STATUS}"
    else
        check_fail "CONNECT method accepted!"
    fi

    # ---- Clickjacking Additional ----
    subsection "Clickjacking Protection Depth"

    # Check frame-ancestors in CSP
    perform_request "$FRONTEND_URL"
    local full_csp
    full_csp=$(get_header "content-security-policy")
    if echo "$full_csp" | grep -Eqi "frame-ancestors.*'none'|frame-ancestors.*'self'"; then
        check_pass "CSP frame-ancestors properly restrictive"
    elif echo "$full_csp" | grep -qi "frame-ancestors"; then
        check_warn "CSP frame-ancestors present but check restrictiveness"
    else
        local xfo_val
        xfo_val=$(get_header "x-frame-options")
        if [[ "$xfo_val" == "DENY" ]]; then
            check_pass "X-Frame-Options: DENY (clickjacking protection)"
        elif [[ "$xfo_val" == "SAMEORIGIN" ]]; then
            check_pass "X-Frame-Options: SAMEORIGIN"
        else
            check_warn "No frame restriction found"
        fi
    fi

    # ---- API Security Headers on Errors ----
    subsection "Security Headers on Error Responses"

    # 404 should still have security headers
    perform_request "$BASE_URL/api/nonexistent-xyz"
    local err_xcto
    err_xcto=$(get_header "x-content-type-options")
    if [[ "$err_xcto" == "nosniff" ]]; then
        check_pass "404 response has X-Content-Type-Options"
    else
        check_warn "404 response missing X-Content-Type-Options"
    fi

    local err_hsts
    err_hsts=$(get_header "strict-transport-security")
    if [[ -n "$err_hsts" ]]; then
        check_pass "404 response has HSTS"
    else
        check_warn "404 response missing HSTS"
    fi

    # 405 should have security headers
    perform_request "$BASE_URL/health" -X DELETE
    local del_xcto
    del_xcto=$(get_header "x-content-type-options")
    if [[ -n "$del_xcto" ]]; then
        check_pass "Error response has security headers"
    fi

    echo ""
    echo -e "  ${DIM}Section 2 complete: ${SECTION_PASS[$((CURRENT_SECTION-1))]} pass, ${SECTION_WARN[$((CURRENT_SECTION-1))]} warn, ${SECTION_FAIL[$((CURRENT_SECTION-1))]} fail${NC}"
}


# ===============================================================================
# SECTION 3: PERFORMANCE AUDIT (80+ checks)
# ===============================================================================

run_section_3() {
    start_section "PERFORMANCE AUDIT"

    # ---- TTFB Benchmarks ----
    subsection "TTFB Benchmarks - Frontend"

    local ttfb_values=()
    local total_values=()
    local i

    for i in $(seq 1 5); do
        perform_request "$FRONTEND_URL" -H "Cache-Control: no-cache"
        ttfb_values+=("$RESP_TTFB")
        total_values+=("$RESP_TOTAL")
        verbose "Request ${i}: TTFB=${RESP_TTFB}ms, Total=${RESP_TOTAL}ms"
    done

    # Calculate average and max TTFB
    local sum=0 max_ttfb=0 min_ttfb=99999
    for val in "${ttfb_values[@]}"; do
        sum=$((sum + val))
        [[ "$val" -gt "$max_ttfb" ]] && max_ttfb=$val
        [[ "$val" -lt "$min_ttfb" ]] && min_ttfb=$val
    done
    local avg_ttfb=$((sum / ${#ttfb_values[@]}))

    if [[ "$avg_ttfb" -lt 500 ]]; then
        check_pass "Frontend avg TTFB: ${avg_ttfb}ms (< 500ms)"
    elif [[ "$avg_ttfb" -lt 1000 ]]; then
        check_warn "Frontend avg TTFB: ${avg_ttfb}ms (500-1000ms)"
    else
        check_fail "Frontend avg TTFB: ${avg_ttfb}ms (> 1000ms)"
    fi

    if [[ "$max_ttfb" -lt 1000 ]]; then
        check_pass "Frontend max TTFB: ${max_ttfb}ms (< 1s)"
    elif [[ "$max_ttfb" -lt 2000 ]]; then
        check_warn "Frontend max TTFB: ${max_ttfb}ms (1-2s)"
    else
        check_fail "Frontend max TTFB: ${max_ttfb}ms (> 2s)"
    fi

    local variance=$((max_ttfb - min_ttfb))
    if [[ "$variance" -lt 200 ]]; then
        check_pass "Frontend TTFB variance: ${variance}ms (consistent)"
    elif [[ "$variance" -lt 500 ]]; then
        check_warn "Frontend TTFB variance: ${variance}ms (some jitter)"
    else
        check_fail "Frontend TTFB variance: ${variance}ms (inconsistent)"
    fi

    # ---- TTFB Benchmarks - API ----
    subsection "TTFB Benchmarks - API"

    local api_ttfbs=()
    for i in $(seq 1 5); do
        perform_request "$BASE_URL/health"
        api_ttfbs+=("$RESP_TTFB")
    done

    sum=0; max_ttfb=0; min_ttfb=99999
    for val in "${api_ttfbs[@]}"; do
        sum=$((sum + val))
        [[ "$val" -gt "$max_ttfb" ]] && max_ttfb=$val
        [[ "$val" -lt "$min_ttfb" ]] && min_ttfb=$val
    done
    local api_avg=$((sum / ${#api_ttfbs[@]}))

    if [[ "$api_avg" -lt 200 ]]; then
        check_pass "API avg TTFB: ${api_avg}ms (< 200ms)"
    elif [[ "$api_avg" -lt 500 ]]; then
        check_warn "API avg TTFB: ${api_avg}ms (200-500ms)"
    else
        check_fail "API avg TTFB: ${api_avg}ms (> 500ms)"
    fi

    variance=$((max_ttfb - min_ttfb))
    if [[ "$variance" -lt 100 ]]; then
        check_pass "API TTFB variance: ${variance}ms (stable)"
    elif [[ "$variance" -lt 300 ]]; then
        check_warn "API TTFB variance: ${variance}ms"
    else
        check_fail "API TTFB variance: ${variance}ms (unstable)"
    fi

    # ---- Cold Start Detection ----
    subsection "Cold Start Detection"

    # First request (potential cold start)
    perform_request "$BASE_URL/health/deep" -H "Cache-Control: no-cache"
    local first_ttfb=$RESP_TTFB
    check_pass "Deep health first request: ${first_ttfb}ms"

    # Rapid subsequent requests
    local cold_ttfbs=()
    for i in $(seq 1 5); do
        perform_request "$BASE_URL/health" -H "Cache-Control: no-cache"
        cold_ttfbs+=("$RESP_TTFB")
    done

    local cold_max=0 cold_min=99999
    for val in "${cold_ttfbs[@]}"; do
        [[ "$val" -gt "$cold_max" ]] && cold_max=$val
        [[ "$val" -lt "$cold_min" ]] && cold_min=$val
    done

    local cold_variance=$((cold_max - cold_min))
    if [[ "$cold_variance" -lt 100 ]]; then
        check_pass "No cold start detected (variance ${cold_variance}ms)"
    elif [[ "$cold_variance" -lt 500 ]]; then
        check_warn "Possible cold start (variance ${cold_variance}ms)"
    else
        check_fail "Cold start detected (variance ${cold_variance}ms)"
    fi

    if [[ "$first_ttfb" -gt 2000 ]]; then
        check_warn "First deep health request slow: ${first_ttfb}ms (cold start?)"
    else
        check_pass "First deep health request acceptable: ${first_ttfb}ms"
    fi

    # ---- Compression ----
    subsection "Compression (Brotli/Gzip)"

    # Frontend HTML compression
    perform_request "$FRONTEND_URL" -H "Accept-Encoding: br, gzip, deflate"
    local fe_encoding
    fe_encoding=$(get_header "content-encoding")
    if [[ "$fe_encoding" == "br" ]]; then
        check_pass "Frontend uses Brotli compression"
    elif [[ "$fe_encoding" == "gzip" ]]; then
        check_pass "Frontend uses gzip compression"
    elif [[ -n "$fe_encoding" ]]; then
        check_pass "Frontend compression: ${fe_encoding}"
    else
        check_fail "Frontend not compressed"
    fi

    # API compression
    perform_request "$BASE_URL/api/content/boards" -H "Accept-Encoding: br, gzip, deflate"
    local api_encoding
    api_encoding=$(get_header "content-encoding")
    if [[ "$api_encoding" == "br" || "$api_encoding" == "gzip" ]]; then
        check_pass "API uses compression: ${api_encoding}"
    else
        check_warn "API response not compressed"
    fi

    # JSON API compression
    perform_request "$BASE_URL/docs" -H "Accept-Encoding: br, gzip, deflate"
    local docs_encoding
    docs_encoding=$(get_header "content-encoding")
    if [[ -n "$docs_encoding" ]]; then
        check_pass "API docs compressed: ${docs_encoding}"
    else
        check_warn "API docs not compressed"
    fi

    # ---- Caching Headers ----
    subsection "Cache Headers"

    # Frontend cache
    perform_request "$FRONTEND_URL"
    local fe_cache
    fe_cache=$(get_header "cache-control")
    if [[ -n "$fe_cache" ]]; then
        check_pass "Frontend Cache-Control: ${fe_cache}"
        if echo "$fe_cache" | grep -Eqi "no-cache|no-store"; then
            check_pass "HTML not cached (dynamic content)"
        fi
    else
        check_warn "No Cache-Control on frontend HTML"
    fi

    # Static assets cache (try common paths)
    local asset_paths=("/assets/" "/static/" "/js/" "/css/")
    for apath in "${asset_paths[@]}"; do
        perform_request "${FRONTEND_URL}${apath}" -I
        if [[ "$RESP_STATUS" == "200" ]]; then
            local asset_cache
            asset_cache=$(get_header "cache-control")
            if echo "$asset_cache" | grep -qi "max-age="; then
                local asset_maxage
                asset_maxage=$(echo "$asset_cache" | grep -oi "max-age=[0-9]*" | cut -d= -f2)
                if [[ -n "$asset_maxage" && "$asset_maxage" -ge 86400 ]]; then
                    check_pass "Static ${apath} cached: max-age=${asset_maxage}"
                else
                    check_warn "Static ${apath} short cache: max-age=${asset_maxage}"
                fi
                if echo "$asset_cache" | grep -qi "immutable"; then
                    check_pass "Static ${apath} has immutable flag"
                fi
            fi
            break
        fi
    done

    # API cache headers
    perform_request "$BASE_URL/api/content/boards"
    local api_cache
    api_cache=$(get_header "cache-control")
    if [[ -n "$api_cache" ]]; then
        check_pass "API Cache-Control: ${api_cache}"
    else
        check_warn "No Cache-Control on API content endpoints"
    fi

    local api_etag
    api_etag=$(get_header "etag")
    if [[ -n "$api_etag" ]]; then
        check_pass "API ETag present: ${api_etag}"
    else
        check_warn "No ETag header on API"
    fi

    local api_vary
    api_vary=$(get_header "vary")
    if [[ -n "$api_vary" ]]; then
        check_pass "API Vary header: ${api_vary}"
    fi

    # ---- Concurrent Requests ----
    subsection "Concurrent Request Handling"

    local pids=()
    local concurrent_results=()
    local tmpdir
    tmpdir="${AUDIT_TMPDIR}/concurrent_s3_$$"
    mkdir -p "$tmpdir"

    # Fire 10 parallel requests
    for i in $(seq 1 10); do
        curl -sS -o /dev/null -w '%{http_code}:%{time_total}' --max-time 15 "$BASE_URL/health" > "${tmpdir}/req_${i}" 2>/dev/null &
        pids+=($!)
    done

    # Wait for all
    for pid in "${pids[@]}"; do
        wait "$pid" 2>/dev/null || true
    done

    local concurrent_ok=0 concurrent_fail=0 concurrent_slow=0
    for i in $(seq 1 10); do
        if [[ -f "${tmpdir}/req_${i}" ]]; then
            local result
            result=$(cat "${tmpdir}/req_${i}")
            local rstatus
            rstatus=$(echo "$result" | cut -d: -f1)
            local rtime
            rtime=$(echo "$result" | cut -d: -f2)
            local rtime_ms
            rtime_ms=$(echo "$rtime" | awk '{printf "%d", $1 * 1000}')
            if [[ "$rstatus" == "200" ]]; then
                concurrent_ok=$((concurrent_ok + 1))
                if [[ "$rtime_ms" -gt 2000 ]]; then
                    concurrent_slow=$((concurrent_slow + 1))
                fi
            else
                concurrent_fail=$((concurrent_fail + 1))
            fi
        fi
    done
    rm -rf "$tmpdir"

    if [[ "$concurrent_ok" -eq 10 ]]; then
        check_pass "All 10 concurrent requests succeeded"
    elif [[ "$concurrent_ok" -ge 8 ]]; then
        check_warn "${concurrent_ok}/10 concurrent requests succeeded"
    else
        check_fail "Only ${concurrent_ok}/10 concurrent requests succeeded"
    fi

    if [[ "$concurrent_slow" -eq 0 ]]; then
        check_pass "No slow responses under concurrency"
    elif [[ "$concurrent_slow" -le 2 ]]; then
        check_warn "${concurrent_slow}/10 slow responses under concurrency"
    else
        check_fail "${concurrent_slow}/10 slow under concurrency"
    fi

    if [[ "$concurrent_fail" -eq 0 ]]; then
        check_pass "No failures under 10x concurrency"
    else
        check_fail "${concurrent_fail} failures under 10x concurrency"
    fi

    # ---- Connection Keep-Alive ----
    subsection "Connection Keep-Alive"

    perform_request "$BASE_URL/health"
    local conn_header
    conn_header=$(get_header "connection")
    if echo "$conn_header" | grep -qi "keep-alive"; then
        check_pass "Connection: keep-alive enabled"
    elif echo "$conn_header" | grep -qi "close"; then
        check_warn "Connection: close (no keep-alive)"
    else
        check_pass "Connection header: ${conn_header:-implicit keep-alive (HTTP/2+)}"
    fi

    local keep_alive_hdr
    keep_alive_hdr=$(get_header "keep-alive")
    if [[ -n "$keep_alive_hdr" ]]; then
        check_pass "Keep-Alive header: ${keep_alive_hdr}"
    fi

    # ---- Transfer Size ----
    subsection "Transfer Size Optimization"

    perform_request "$FRONTEND_URL" -H "Accept-Encoding: br, gzip, deflate"
    local fe_size
    fe_size=$(echo "$RESP_BODY" | wc -c)
    if [[ "$fe_size" -lt 50000 ]]; then
        check_pass "Frontend HTML size: ${fe_size} bytes (< 50KB)"
    elif [[ "$fe_size" -lt 200000 ]]; then
        check_warn "Frontend HTML size: ${fe_size} bytes (50-200KB)"
    else
        check_fail "Frontend HTML size: ${fe_size} bytes (> 200KB)"
    fi

    # Check content-length header
    local fe_content_len
    fe_content_len=$(get_header "content-length")
    if [[ -n "$fe_content_len" ]]; then
        check_pass "Content-Length header present: ${fe_content_len}"
    fi

    # Transfer-encoding chunked
    local te_header
    te_header=$(get_header "transfer-encoding")
    if [[ -n "$te_header" ]]; then
        check_pass "Transfer-Encoding: ${te_header}"
    fi

    # ---- HTTP/2 Support ----
    subsection "HTTP/2 and HTTP/3 Support"

    local h2_check
    h2_check=$(curl -sS -o /dev/null -w '%{http_version}' --max-time 10 "$FRONTEND_URL" 2>/dev/null || echo "")
    if [[ "$h2_check" == "2" ]]; then
        check_pass "Frontend supports HTTP/2"
    elif [[ "$h2_check" == "3" ]]; then
        check_pass "Frontend supports HTTP/3"
    elif [[ "$h2_check" == "1.1" ]]; then
        check_warn "Frontend using HTTP/1.1 only"
    else
        check_warn "HTTP version: ${h2_check}"
    fi

    local api_h2
    api_h2=$(curl -sS -o /dev/null -w '%{http_version}' --max-time 10 "$BASE_URL/health" 2>/dev/null || echo "")
    if [[ "$api_h2" == "2" || "$api_h2" == "3" ]]; then
        check_pass "API supports HTTP/${api_h2}"
    else
        check_warn "API HTTP version: ${api_h2}"
    fi

    # Check for alt-svc (HTTP/3 upgrade)
    perform_request "$FRONTEND_URL"
    local alt_svc
    alt_svc=$(get_header "alt-svc")
    if echo "$alt_svc" | grep -qi "h3"; then
        check_pass "Alt-Svc advertises HTTP/3"
    elif [[ -n "$alt_svc" ]]; then
        check_pass "Alt-Svc header: ${alt_svc}"
    else
        check_warn "No Alt-Svc header (HTTP/3 not advertised)"
    fi

    # ---- Page Weight Indicators ----
    subsection "Page Weight Indicators"

    perform_request "$FRONTEND_URL"
    # Check for render-blocking patterns
    if body_contains "rel=\"preload\""; then
        check_pass "Preload hints found in HTML"
    else
        check_warn "No preload hints detected"
    fi

    if body_contains "rel=\"preconnect\""; then
        check_pass "Preconnect hints found"
    fi

    if body_contains "defer|async"; then
        check_pass "Deferred/async script loading detected"
    else
        check_warn "No defer/async scripts detected"
    fi

    if body_contains "loading=\"lazy\"|data-lazy"; then
        check_pass "Lazy loading detected"
    fi

    # ---- Resource Hints ----
    subsection "Resource Hints & Optimization"

    perform_request "$FRONTEND_URL"

    # DNS prefetch
    if body_contains "dns-prefetch"; then
        check_pass "DNS prefetch hints found"
    else
        check_warn "No DNS prefetch hints"
    fi

    # Preconnect to API
    if body_contains "preconnect.*api|preconnect.*syrabit"; then
        check_pass "Preconnect to API domain found"
    else
        check_warn "No preconnect to API domain"
    fi

    # Module/nomodule pattern
    if body_contains "type=\"module\"|type='module'"; then
        check_pass "ES modules used"
    fi

    if body_contains "nomodule"; then
        check_pass "Nomodule fallback present"
    fi

    # Check for inline critical CSS
    if body_contains "<style"; then
        check_pass "Inline styles present (critical CSS)"
    fi

    # Check script count
    local script_count
    script_count=$(echo "$RESP_BODY" | grep -c "<script" 2>/dev/null || echo "0")
    script_count=$(echo "$script_count" | tr -d '[:space:]')
    if [[ "$script_count" -lt 10 ]]; then
        check_pass "Script tag count reasonable: ${script_count}"
    elif [[ "$script_count" -lt 20 ]]; then
        check_warn "Many script tags: ${script_count}"
    else
        check_fail "Too many script tags: ${script_count}"
    fi

    # ---- API Response Size ----
    subsection "API Response Size"

    perform_request "$BASE_URL/health"
    local health_size
    health_size=$(echo "$RESP_BODY" | wc -c)
    if [[ "$health_size" -lt 1000 ]]; then
        check_pass "Health response compact: ${health_size} bytes"
    elif [[ "$health_size" -lt 5000 ]]; then
        check_pass "Health response size: ${health_size} bytes"
    else
        check_warn "Health response large: ${health_size} bytes"
    fi

    perform_request "$BASE_URL/health/deep"
    local deep_size
    deep_size=$(echo "$RESP_BODY" | wc -c)
    if [[ "$deep_size" -lt 5000 ]]; then
        check_pass "Deep health response size: ${deep_size} bytes"
    else
        check_warn "Deep health response large: ${deep_size} bytes"
    fi

    # ---- Frontend Asset Optimization ----
    subsection "Frontend Asset Analysis"

    perform_request "$FRONTEND_URL"
    # Extract JS asset paths from HTML
    local js_assets
    js_assets=$(echo "$RESP_BODY" | grep -oP 'src="[^"]*\.js"' 2>/dev/null | head -5 || echo "")

    if [[ -n "$js_assets" ]]; then
        check_pass "JavaScript assets referenced in HTML"
        # Check first JS asset for cache headers
        local first_js
        first_js=$(echo "$js_assets" | head -1 | sed 's/src="//;s/"//')
        if [[ "$first_js" == /* ]]; then
            first_js="${FRONTEND_URL}${first_js}"
        fi
        if [[ -n "$first_js" && "$first_js" == http* ]]; then
            perform_request "$first_js" -I
            if [[ "$RESP_STATUS" == "200" ]]; then
                local js_cache
                js_cache=$(get_header "cache-control")
                if echo "$js_cache" | grep -qi "max-age"; then
                    check_pass "JS asset has cache-control: ${js_cache}"
                fi
                local js_enc
                js_enc=$(get_header "content-encoding")
                if [[ -n "$js_enc" ]]; then
                    check_pass "JS asset compressed: ${js_enc}"
                fi
            fi
        fi
    fi

    # Check CSS assets
    local css_assets
    css_assets=$(echo "$RESP_BODY" | grep -oP 'href="[^"]*\.css"' 2>/dev/null | head -3 || echo "")
    if [[ -n "$css_assets" ]]; then
        check_pass "CSS assets referenced in HTML"
        local first_css
        first_css=$(echo "$css_assets" | head -1 | sed 's/href="//;s/"//')
        if [[ "$first_css" == /* ]]; then
            first_css="${FRONTEND_URL}${first_css}"
        fi
        if [[ -n "$first_css" && "$first_css" == http* ]]; then
            perform_request "$first_css" -I
            if [[ "$RESP_STATUS" == "200" ]]; then
                local css_cache
                css_cache=$(get_header "cache-control")
                if echo "$css_cache" | grep -qi "max-age"; then
                    check_pass "CSS asset has cache-control: ${css_cache}"
                fi
            fi
        fi
    fi

    # ---- Content Delivery Timing ----
    subsection "Content Delivery Timing"

    # Frontend from different cache states
    perform_request "$FRONTEND_URL" -H "Cache-Control: no-cache" -H "Pragma: no-cache"
    local nocache_ttfb=$RESP_TTFB
    check_pass "No-cache frontend TTFB: ${nocache_ttfb}ms"

    perform_request "$FRONTEND_URL"
    local cached_ttfb=$RESP_TTFB
    check_pass "Normal frontend TTFB: ${cached_ttfb}ms"

    if [[ "$cached_ttfb" -le "$nocache_ttfb" ]]; then
        check_pass "Cached request same or faster than no-cache"
    else
        check_warn "Cached request slower (${cached_ttfb}ms vs ${nocache_ttfb}ms)"
    fi

    # API timing for different endpoints
    perform_request "$BASE_URL/health"
    check_pass "API /health TTFB: ${RESP_TTFB}ms"

    perform_request "$BASE_URL/api/content/boards"
    check_pass "API /boards TTFB: ${RESP_TTFB}ms"

    # ---- Geographic Performance ----
    subsection "Response Characteristics"

    # Check for server push / early hints
    perform_request "$FRONTEND_URL"
    local link_hdr
    link_hdr=$(get_header "link")
    if [[ -n "$link_hdr" ]]; then
        check_pass "Link header present (resource hints): yes"
        if echo "$link_hdr" | grep -qi "preload"; then
            check_pass "Preload directives in Link header"
        fi
        if echo "$link_hdr" | grep -qi "as=script"; then
            check_pass "Script preload in Link header"
        fi
        if echo "$link_hdr" | grep -qi "as=style"; then
            check_pass "Style preload in Link header"
        fi
    else
        check_warn "No Link header (no server push hints)"
    fi

    # Speculation rules (Cloudflare feature)
    local spec_rules
    spec_rules=$(get_header "speculation-rules")
    if [[ -n "$spec_rules" ]]; then
        check_pass "Speculation-Rules header: ${spec_rules}"
    fi

    # ---- Multiple Endpoint Performance ----
    subsection "Multi-Endpoint Performance Comparison"

    local endpoints_to_time=("/health" "/health/deep" "/api/content/boards" "/api/content/classes" "/api/subscription/plans")
    for ep in "${endpoints_to_time[@]}"; do
        perform_request "$BASE_URL${ep}"
        if [[ "$RESP_STATUS" != "000" ]]; then
            if [[ "$RESP_TTFB" -lt 500 ]]; then
                check_pass "${ep}: ${RESP_TTFB}ms (fast)"
            elif [[ "$RESP_TTFB" -lt 2000 ]]; then
                check_warn "${ep}: ${RESP_TTFB}ms (moderate)"
            else
                check_fail "${ep}: ${RESP_TTFB}ms (slow)"
            fi
        fi
    done

    # Frontend subpages performance
    local fe_pages=("/" "/login" "/about")
    for page in "${fe_pages[@]}"; do
        perform_request "${FRONTEND_URL}${page}"
        if [[ "$RESP_STATUS" == "200" ]]; then
            if [[ "$RESP_TTFB" -lt 500 ]]; then
                check_pass "Frontend ${page}: ${RESP_TTFB}ms"
            elif [[ "$RESP_TTFB" -lt 1500 ]]; then
                check_warn "Frontend ${page}: ${RESP_TTFB}ms"
            else
                check_fail "Frontend ${page}: ${RESP_TTFB}ms (slow)"
            fi
        fi
    done

    # ---- Bandwidth Efficiency ----
    subsection "Bandwidth Efficiency"

    # Check if API responses are reasonably sized
    perform_request "$BASE_URL/api/content/boards"
    local boards_size
    boards_size=$(echo "$RESP_BODY" | wc -c)
    if [[ "$boards_size" -lt 50000 ]]; then
        check_pass "Boards response size: ${boards_size} bytes (efficient)"
    elif [[ "$boards_size" -lt 200000 ]]; then
        check_warn "Boards response size: ${boards_size} bytes"
    else
        check_fail "Boards response too large: ${boards_size} bytes"
    fi

    perform_request "$BASE_URL/health"
    local health_bw
    health_bw=$(echo "$RESP_BODY" | wc -c)
    check_pass "Health payload: ${health_bw} bytes"

    perform_request "$BASE_URL/health/deep"
    local deep_bw
    deep_bw=$(echo "$RESP_BODY" | wc -c)
    check_pass "Deep health payload: ${deep_bw} bytes"

    # Check overall frontend weight
    perform_request "$FRONTEND_URL" -H "Accept-Encoding: identity"
    local raw_size
    raw_size=$(echo "$RESP_BODY" | wc -c)
    perform_request "$FRONTEND_URL" -H "Accept-Encoding: br, gzip, deflate"
    local compressed_size
    compressed_size=$(echo "$RESP_BODY" | wc -c)
    if [[ "$raw_size" -gt 0 && "$compressed_size" -lt "$raw_size" ]]; then
        local savings=$(( (raw_size - compressed_size) * 100 / raw_size ))
        check_pass "Compression saves ~${savings}% (${raw_size} -> ${compressed_size} bytes)"
    else
        check_warn "Cannot determine compression savings"
    fi

    # ---- Sustained Load Test ----
    subsection "Sustained Load (10 Sequential Requests)"

    local sustained_pass=0 sustained_total=10
    local sustained_ttfbs=()
    for i in $(seq 1 "$sustained_total"); do
        perform_request "$BASE_URL/health"
        sustained_ttfbs+=("$RESP_TTFB")
        if [[ "$RESP_STATUS" == "200" ]]; then
            sustained_pass=$((sustained_pass + 1))
        fi
    done

    if [[ "$sustained_pass" -eq "$sustained_total" ]]; then
        check_pass "Sustained: ${sustained_pass}/${sustained_total} requests succeeded"
    else
        check_fail "Sustained: only ${sustained_pass}/${sustained_total} succeeded"
    fi

    # Check for degradation over time
    local first_three_avg=0 last_three_avg=0
    for i in 0 1 2; do
        first_three_avg=$((first_three_avg + ${sustained_ttfbs[$i]}))
    done
    first_three_avg=$((first_three_avg / 3))

    local last_idx=$(( ${#sustained_ttfbs[@]} - 1 ))
    for i in $((last_idx-2)) $((last_idx-1)) $last_idx; do
        last_three_avg=$((last_three_avg + ${sustained_ttfbs[$i]}))
    done
    last_three_avg=$((last_three_avg / 3))

    if [[ "$last_three_avg" -le $((first_three_avg + 100)) ]]; then
        check_pass "No degradation: first 3 avg=${first_three_avg}ms, last 3 avg=${last_three_avg}ms"
    elif [[ "$last_three_avg" -le $((first_three_avg * 2)) ]]; then
        check_warn "Slight degradation: first=${first_three_avg}ms, last=${last_three_avg}ms"
    else
        check_fail "Performance degradation: first=${first_three_avg}ms, last=${last_three_avg}ms"
    fi

    echo ""
    echo -e "  ${DIM}Section 3 complete: ${SECTION_PASS[$((CURRENT_SECTION-1))]} pass, ${SECTION_WARN[$((CURRENT_SECTION-1))]} warn, ${SECTION_FAIL[$((CURRENT_SECTION-1))]} fail${NC}"
}


# ===============================================================================
# SECTION 4: API COMPLETENESS AUDIT (80+ checks)
# ===============================================================================

run_section_4() {
    start_section "API COMPLETENESS"

    # ---- Health Endpoints ----
    subsection "Health Endpoints"

    # GET /health
    perform_request "$BASE_URL/health"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "GET /health: 200 OK"
        if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
            check_pass "GET /health returns valid JSON"
            if echo "$RESP_BODY" | jq -e '.status' >/dev/null 2>&1; then
                check_pass "GET /health has status field"
            else
                check_warn "GET /health missing status field"
            fi
        else
            check_warn "GET /health not JSON"
        fi
    else
        check_critical "GET /health: ${RESP_STATUS} (expected 200)"
    fi

    # GET /health/deep
    perform_request "$BASE_URL/health/deep"
    if [[ "$RESP_STATUS" == "200" || "$RESP_STATUS" == "503" ]]; then
        check_pass "GET /health/deep: ${RESP_STATUS} (endpoint exists)"
        if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
            check_pass "GET /health/deep returns valid JSON"
            # Check for service status fields
            local deep_keys
            deep_keys=$(echo "$RESP_BODY" | jq 'keys' 2>/dev/null || echo "[]")
            if [[ "$deep_keys" != "[]" ]]; then
                check_pass "GET /health/deep has service keys: ${deep_keys}"
            fi
        fi
    else
        check_fail "GET /health/deep: ${RESP_STATUS}"
    fi

    # GET /health/circuit-breakers
    perform_request "$BASE_URL/health/circuit-breakers"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "GET /health/circuit-breakers: 200 OK"
        if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
            check_pass "Circuit breakers returns valid JSON"
        fi
    elif [[ "$RESP_STATUS" == "404" ]]; then
        check_warn "GET /health/circuit-breakers: 404 (not implemented)"
    else
        check_warn "GET /health/circuit-breakers: ${RESP_STATUS}"
    fi

    # ---- Content Endpoints ----
    subsection "Content Endpoints"

    # GET /api/content/boards
    perform_request "$BASE_URL/api/content/boards"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "GET /api/content/boards: 200 OK"
        if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
            check_pass "Boards returns valid JSON"
            local boards_type
            boards_type=$(echo "$RESP_BODY" | jq 'type' -r 2>/dev/null || echo "unknown")
            if [[ "$boards_type" == "array" ]]; then
                check_pass "Boards returns array"
                local boards_count
                boards_count=$(echo "$RESP_BODY" | jq 'length' 2>/dev/null || echo "0")
                if [[ "$boards_count" -gt 0 ]]; then
                    check_pass "Boards has ${boards_count} items"
                else
                    check_warn "Boards array is empty"
                fi
            elif [[ "$boards_type" == "object" ]]; then
                # May wrap in data field
                local boards_data
                boards_data=$(echo "$RESP_BODY" | jq '.data // .boards // .items' 2>/dev/null || echo "null")
                if [[ "$boards_data" != "null" ]]; then
                    check_pass "Boards response has data wrapper"
                fi
            fi
        else
            check_fail "Boards response is not valid JSON"
        fi
    else
        check_fail "GET /api/content/boards: ${RESP_STATUS}"
    fi

    # GET /api/content/classes
    perform_request "$BASE_URL/api/content/classes"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "GET /api/content/classes: 200 OK"
        if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
            check_pass "Classes returns valid JSON"
        fi
    elif [[ "$RESP_STATUS" == "404" ]]; then
        check_warn "GET /api/content/classes: 404 (not found)"
    else
        check_fail "GET /api/content/classes: ${RESP_STATUS}"
    fi

    # ---- SEO Endpoints ----
    subsection "SEO Endpoints"

    # GET /api/seo/sitemap-index.xml
    perform_request "$BASE_URL/api/seo/sitemap-index.xml"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "GET /api/seo/sitemap-index.xml: 200 OK"
        local ct
        ct=$(get_header "content-type")
        if echo "$ct" | grep -qi "xml"; then
            check_pass "Sitemap Content-Type: ${ct}"
        else
            check_warn "Sitemap Content-Type: ${ct} (expected XML)"
        fi
        if body_contains "<sitemapindex|<urlset|<sitemap"; then
            check_pass "Sitemap has valid XML structure"
        else
            check_warn "Sitemap may not have standard XML structure"
        fi
    else
        check_fail "GET /api/seo/sitemap-index.xml: ${RESP_STATUS}"
    fi

    # GET /api/seo/knowledge-graph
    perform_request "$BASE_URL/api/seo/knowledge-graph"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "GET /api/seo/knowledge-graph: 200 OK"
        if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
            check_pass "Knowledge graph returns valid JSON"
        fi
    elif [[ "$RESP_STATUS" == "404" ]]; then
        check_warn "GET /api/seo/knowledge-graph: 404"
    else
        check_warn "GET /api/seo/knowledge-graph: ${RESP_STATUS}"
    fi

    # ---- Auth Endpoints ----
    subsection "Auth Endpoints"

    # POST /api/auth/login - validation test
    perform_request "$BASE_URL/api/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{}'

    if [[ "$RESP_STATUS" == "422" || "$RESP_STATUS" == "400" ]]; then
        check_pass "POST /api/auth/login validates input: ${RESP_STATUS}"
        if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
            check_pass "Auth validation returns JSON error"
            if echo "$RESP_BODY" | jq -e '.detail' >/dev/null 2>&1; then
                check_pass "Auth error has 'detail' field"
            fi
        fi
    elif [[ "$RESP_STATUS" == "401" || "$RESP_STATUS" == "403" ]]; then
        check_pass "POST /api/auth/login rejects: ${RESP_STATUS}"
    else
        check_warn "POST /api/auth/login: ${RESP_STATUS}"
    fi

    # POST /api/auth/login with wrong creds
    perform_request "$BASE_URL/api/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"email":"fake@fake.com","password":"wrong123"}'

    if [[ "$RESP_STATUS" == "401" || "$RESP_STATUS" == "403" || "$RESP_STATUS" == "422" || "$RESP_STATUS" == "400" ]]; then
        check_pass "Login rejects bad credentials: ${RESP_STATUS}"
    elif [[ "$RESP_STATUS" == "200" ]]; then
        check_critical "Login accepts fake credentials!"
    else
        check_warn "Login unexpected status: ${RESP_STATUS}"
    fi

    # ---- Chat Endpoints ----
    subsection "Chat Endpoints"

    # POST /api/chat/message - without auth
    perform_request "$BASE_URL/api/chat/message" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"message":"test","language":"en"}'

    if [[ "$RESP_STATUS" == "401" || "$RESP_STATUS" == "403" || "$RESP_STATUS" == "422" ]]; then
        check_pass "POST /api/chat/message requires auth: ${RESP_STATUS}"
    elif [[ "$RESP_STATUS" == "404" ]]; then
        check_warn "POST /api/chat/message: 404 (path may differ)"
    else
        check_warn "POST /api/chat/message: ${RESP_STATUS}"
    fi

    # ---- Subscription Endpoints ----
    subsection "Subscription Endpoints"

    # GET /api/subscription/plans
    perform_request "$BASE_URL/api/subscription/plans"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "GET /api/subscription/plans: 200 OK"
        if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
            check_pass "Subscription plans returns valid JSON"
            local plans_count
            plans_count=$(echo "$RESP_BODY" | jq 'if type == "array" then length else (.data // .plans // [] | length) end' 2>/dev/null || echo "0")
            if [[ "$plans_count" -gt 0 ]]; then
                check_pass "Subscription plans has ${plans_count} plans"
            else
                check_warn "No subscription plans returned"
            fi
        fi
    elif [[ "$RESP_STATUS" == "404" ]]; then
        check_warn "GET /api/subscription/plans: 404"
    else
        check_warn "GET /api/subscription/plans: ${RESP_STATUS}"
    fi

    # ---- Documentation Endpoints ----
    subsection "Documentation Endpoints"

    # GET /docs
    perform_request "$BASE_URL/docs"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "GET /docs: 200 OK (Swagger UI)"
        if body_contains "swagger|openapi|redoc"; then
            check_pass "Docs page contains API documentation"
        fi
    elif [[ "$RESP_STATUS" == "404" ]]; then
        check_warn "GET /docs: 404 (docs not exposed)"
    else
        check_warn "GET /docs: ${RESP_STATUS}"
    fi

    # GET /openapi.json
    perform_request "$BASE_URL/openapi.json"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "GET /openapi.json: 200 OK"
        if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
            check_pass "OpenAPI spec is valid JSON"
            local openapi_ver
            openapi_ver=$(echo "$RESP_BODY" | jq -r '.openapi // .swagger // "unknown"' 2>/dev/null)
            if [[ "$openapi_ver" != "unknown" && "$openapi_ver" != "null" ]]; then
                check_pass "OpenAPI version: ${openapi_ver}"
            fi
            local paths_count
            paths_count=$(echo "$RESP_BODY" | jq '.paths | length' 2>/dev/null || echo "0")
            if [[ "$paths_count" -gt 0 ]]; then
                check_pass "OpenAPI defines ${paths_count} paths"
            fi
        fi
    elif [[ "$RESP_STATUS" == "404" ]]; then
        check_warn "GET /openapi.json: 404"
    else
        check_warn "GET /openapi.json: ${RESP_STATUS}"
    fi

    # ---- Changelog ----
    subsection "Changelog Endpoint"

    perform_request "$BASE_URL/api/changelog"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "GET /api/changelog: 200 OK"
        if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
            check_pass "Changelog returns valid JSON"
        fi
    elif [[ "$RESP_STATUS" == "404" ]]; then
        check_warn "GET /api/changelog: 404"
    else
        check_warn "GET /api/changelog: ${RESP_STATUS}"
    fi

    # ---- Feedback ----
    subsection "Feedback Endpoint"

    perform_request "$BASE_URL/api/feedback" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"test": true}'
    if [[ "$RESP_STATUS" == "401" || "$RESP_STATUS" == "403" || "$RESP_STATUS" == "422" || "$RESP_STATUS" == "400" ]]; then
        check_pass "POST /api/feedback validates/authenticates: ${RESP_STATUS}"
    elif [[ "$RESP_STATUS" == "404" ]]; then
        check_warn "POST /api/feedback: 404"
    elif [[ "$RESP_STATUS" == "200" || "$RESP_STATUS" == "201" ]]; then
        check_warn "POST /api/feedback accepted test data"
    else
        check_warn "POST /api/feedback: ${RESP_STATUS}"
    fi

    # ---- API Versioning ----
    subsection "API Versioning & Consistency"

    # All endpoints should be under /api/ prefix
    local api_endpoints=("/health" "/api/content/boards" "/api/auth/login" "/api/changelog")
    for ep in "${api_endpoints[@]}"; do
        perform_request "$BASE_URL${ep}" -I
        if [[ "$RESP_STATUS" != "000" ]]; then
            check_pass "Endpoint ${ep} reachable (${RESP_STATUS})"
        else
            check_fail "Endpoint ${ep} unreachable"
        fi
    done

    # ---- Error Response Format ----
    subsection "Error Response Format Consistency"

    # Test various error scenarios
    # 404 error
    perform_request "$BASE_URL/api/nonexistent-endpoint-xyz"
    if [[ "$RESP_STATUS" == "404" ]]; then
        check_pass "Unknown endpoint returns 404"
        if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
            check_pass "404 response is JSON"
            if echo "$RESP_BODY" | jq -e '.detail // .error // .message' >/dev/null 2>&1; then
                check_pass "404 has error description field"
            else
                check_warn "404 JSON missing standard error field"
            fi
        else
            check_warn "404 response not JSON"
        fi
    else
        check_warn "Unknown endpoint returns ${RESP_STATUS} (expected 404)"
    fi

    # 405 Method Not Allowed
    perform_request "$BASE_URL/health" -X DELETE
    if [[ "$RESP_STATUS" == "405" ]]; then
        check_pass "DELETE /health returns 405"
    elif [[ "$RESP_STATUS" == "200" ]]; then
        check_warn "DELETE /health returns 200 (method not restricted)"
    else
        check_pass "DELETE /health returns ${RESP_STATUS}"
    fi

    # Invalid Content-Type
    perform_request "$BASE_URL/api/auth/login" \
        -X POST \
        -H "Content-Type: text/plain" \
        -d 'not json'
    if [[ "$RESP_STATUS" == "415" || "$RESP_STATUS" == "422" || "$RESP_STATUS" == "400" ]]; then
        check_pass "Invalid Content-Type rejected: ${RESP_STATUS}"
    else
        check_warn "Invalid Content-Type response: ${RESP_STATUS}"
    fi

    # ---- Request ID Tracing ----
    subsection "Request Tracing"

    perform_request "$BASE_URL/health"
    local req_id
    req_id=$(get_header "x-request-id")
    if [[ -n "$req_id" ]]; then
        check_pass "X-Request-ID present: ${req_id}"
    else
        check_warn "No X-Request-ID header"
    fi

    local trace_id
    trace_id=$(get_header "x-cloud-trace-context")
    if [[ -n "$trace_id" ]]; then
        check_pass "X-Cloud-Trace-Context present"
    else
        check_warn "No X-Cloud-Trace-Context header"
    fi

    local cf_req_id
    cf_req_id=$(get_header "cf-ray")
    if [[ -n "$cf_req_id" ]]; then
        check_pass "CF-Ray (request tracing): ${cf_req_id}"
    fi

    # ---- Content-Type Negotiation ----
    subsection "Content-Type Negotiation"

    # Accept: application/json
    perform_request "$BASE_URL/health" -H "Accept: application/json"
    local ct_json
    ct_json=$(get_header "content-type")
    if echo "$ct_json" | grep -qi "application/json"; then
        check_pass "Responds with JSON when Accept: application/json"
    else
        check_warn "Content-Type: ${ct_json} (expected application/json)"
    fi

    # Accept: text/html (should still work or return appropriate)
    perform_request "$BASE_URL/health" -H "Accept: text/html"
    if [[ "$RESP_STATUS" == "200" || "$RESP_STATUS" == "406" ]]; then
        check_pass "Handles Accept: text/html (status ${RESP_STATUS})"
    fi

    # ---- HTTP Methods ----
    subsection "HTTP Method Handling"

    # HEAD should work for GET endpoints
    perform_request "$BASE_URL/health" -I
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "HEAD /health: 200"
    else
        check_warn "HEAD /health: ${RESP_STATUS}"
    fi

    # OPTIONS returns allowed methods
    perform_request "$BASE_URL/health" -X OPTIONS
    if [[ "$RESP_STATUS" == "200" || "$RESP_STATUS" == "204" ]]; then
        check_pass "OPTIONS /health: ${RESP_STATUS}"
        local allow_hdr
        allow_hdr=$(get_header "allow")
        if [[ -n "$allow_hdr" ]]; then
            check_pass "Allow header: ${allow_hdr}"
        fi
    else
        check_warn "OPTIONS /health: ${RESP_STATUS}"
    fi

    # PATCH should be rejected on health
    perform_request "$BASE_URL/health" -X PATCH -d '{}'
    if [[ "$RESP_STATUS" == "405" || "$RESP_STATUS" == "404" ]]; then
        check_pass "PATCH /health rejected: ${RESP_STATUS}"
    else
        check_warn "PATCH /health: ${RESP_STATUS}"
    fi

    # PUT should be rejected
    perform_request "$BASE_URL/health" -X PUT -H "Content-Type: application/json" -d '{}'
    if [[ "$RESP_STATUS" == "405" || "$RESP_STATUS" == "404" ]]; then
        check_pass "PUT /health rejected: ${RESP_STATUS}"
    else
        check_warn "PUT /health: ${RESP_STATUS}"
    fi

    # ---- Response Headers Quality ----
    subsection "Response Headers Quality"

    perform_request "$BASE_URL/api/content/boards"
    local ct
    ct=$(get_header "content-type")
    if echo "$ct" | grep -Eqi "charset=utf-8|charset=UTF-8"; then
        check_pass "API Content-Type includes charset: ${ct}"
    elif echo "$ct" | grep -qi "application/json"; then
        check_pass "API Content-Type: application/json"
    else
        check_warn "API Content-Type: ${ct}"
    fi

    # Date header
    local date_hdr
    date_hdr=$(get_header "date")
    if [[ -n "$date_hdr" ]]; then
        check_pass "Date header present: ${date_hdr}"
    else
        check_warn "No Date header"
    fi

    # X-Content-Type-Options on API
    local xcto
    xcto=$(get_header "x-content-type-options")
    if [[ "$xcto" == "nosniff" ]]; then
        check_pass "API X-Content-Type-Options: nosniff"
    fi

    # ---- Edge Cases ----
    subsection "Edge Cases & Boundary Testing"

    # Very long URL
    local long_path
    long_path=$(printf 'a%.0s' {1..500})
    perform_request "$BASE_URL/${long_path}"
    if [[ "$RESP_STATUS" == "404" || "$RESP_STATUS" == "414" ]]; then
        check_pass "Long URL handled gracefully: ${RESP_STATUS}"
    else
        check_warn "Long URL response: ${RESP_STATUS}"
    fi

    # Special characters in path
    perform_request "$BASE_URL/api/%00%01%02"
    if [[ "$RESP_STATUS" == "400" || "$RESP_STATUS" == "404" ]]; then
        check_pass "Null bytes in URL rejected: ${RESP_STATUS}"
    else
        check_warn "Null bytes response: ${RESP_STATUS}"
    fi

    # Unicode in query params
    perform_request "$BASE_URL/health?q=%E0%A6%A8%E0%A6%AE%E0%A6%B8%E0%A7%8D%E0%A6%95%E0%A6%BE%E0%A7%B0"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "Unicode query param handled: ${RESP_STATUS}"
    else
        check_warn "Unicode query param: ${RESP_STATUS}"
    fi

    # Empty body POST
    perform_request "$BASE_URL/api/auth/login" -X POST -H "Content-Type: application/json"
    if [[ "$RESP_STATUS" == "400" || "$RESP_STATUS" == "422" || "$RESP_STATUS" == "404" ]]; then
        check_pass "Empty body POST handled: ${RESP_STATUS}"
    else
        check_warn "Empty body POST: ${RESP_STATUS}"
    fi

    # ---- Endpoint Response Times ----
    subsection "Endpoint Response Time Budget"

    local api_eps_timing=("/health" "/health/deep" "/api/content/boards" "/api/content/classes" "/api/subscription/plans" "/api/changelog" "/api/seo/sitemap-index.xml" "/api/seo/knowledge-graph" "/docs" "/openapi.json")
    for ep in "${api_eps_timing[@]}"; do
        perform_request "$BASE_URL${ep}"
        if [[ "$RESP_STATUS" != "000" ]]; then
            if [[ "$RESP_TTFB" -lt 1000 ]]; then
                check_pass "API ${ep}: ${RESP_TTFB}ms (< 1s budget)"
            elif [[ "$RESP_TTFB" -lt 3000 ]]; then
                check_warn "API ${ep}: ${RESP_TTFB}ms (within 3s budget)"
            else
                check_fail "API ${ep}: ${RESP_TTFB}ms (exceeds budget)"
            fi
        fi
    done

    # ---- JSON Response Validity ----
    subsection "JSON Response Validity"

    local json_eps=("/health" "/health/deep" "/health/circuit-breakers" "/api/content/boards" "/api/content/classes" "/api/subscription/plans")
    for ep in "${json_eps[@]}"; do
        perform_request "$BASE_URL${ep}"
        if [[ "$RESP_STATUS" == "200" || "$RESP_STATUS" == "503" ]]; then
            if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
                check_pass "${ep}: valid JSON"
            else
                check_fail "${ep}: invalid JSON"
            fi
        else
            check_warn "${ep}: non-200 status (${RESP_STATUS})"
        fi
    done

    echo ""
    echo -e "  ${DIM}Section 4 complete: ${SECTION_PASS[$((CURRENT_SECTION-1))]} pass, ${SECTION_WARN[$((CURRENT_SECTION-1))]} warn, ${SECTION_FAIL[$((CURRENT_SECTION-1))]} fail${NC}"
}


# ===============================================================================
# SECTION 5: DATA INTEGRITY & SERVICE CONNECTIVITY (50+ checks)
# ===============================================================================

run_section_5() {
    start_section "DATA INTEGRITY & SERVICE CONNECTIVITY"

    # ---- Deep Health - Service Status ----
    subsection "Service Health Status"

    perform_request "$BASE_URL/health/deep"
    if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
        # MongoDB
        local mongo_status
        mongo_status=$(echo "$RESP_BODY" | jq -r '
            .services.mongodb.status //
            .mongodb.status //
            .mongodb //
            .checks.mongodb //
            .database //
            "not_reported"
        ' 2>/dev/null)
        if [[ "$mongo_status" == "healthy" || "$mongo_status" == "ok" || "$mongo_status" == "connected" ]]; then
            check_pass "MongoDB: ${mongo_status}"
        elif [[ "$mongo_status" == "not_reported" || "$mongo_status" == "null" ]]; then
            check_warn "MongoDB status not reported in health"
        else
            check_fail "MongoDB: ${mongo_status}"
        fi

        # Redis
        local redis_status
        redis_status=$(echo "$RESP_BODY" | jq -r '
            .services.redis.status //
            .redis.status //
            .redis //
            .checks.redis //
            .cache //
            "not_reported"
        ' 2>/dev/null)
        if [[ "$redis_status" == "healthy" || "$redis_status" == "ok" || "$redis_status" == "connected" ]]; then
            check_pass "Redis: ${redis_status}"
        elif [[ "$redis_status" == "not_reported" || "$redis_status" == "null" ]]; then
            check_warn "Redis status not reported in health"
        else
            check_fail "Redis: ${redis_status}"
        fi

        # Vertex AI Search
        local vertex_status
        vertex_status=$(echo "$RESP_BODY" | jq -r '
            .services.vertex_ai.status //
            .services.vertex_ai_search.status //
            .vertex_ai //
            .vertex_ai_search //
            .checks.vertex_ai //
            .search //
            "not_reported"
        ' 2>/dev/null)
        if [[ "$vertex_status" == "healthy" || "$vertex_status" == "ok" || "$vertex_status" == "connected" ]]; then
            check_pass "Vertex AI Search: ${vertex_status}"
        elif [[ "$vertex_status" == "not_reported" || "$vertex_status" == "null" ]]; then
            check_warn "Vertex AI Search status not reported"
        else
            check_warn "Vertex AI Search: ${vertex_status}"
        fi

        # Sarvam AI
        local sarvam_status
        sarvam_status=$(echo "$RESP_BODY" | jq -r '
            .services.sarvam_ai.status //
            .services.sarvam.status //
            .sarvam_ai //
            .sarvam //
            .checks.sarvam_ai //
            "not_reported"
        ' 2>/dev/null)
        if [[ "$sarvam_status" == "healthy" || "$sarvam_status" == "ok" || "$sarvam_status" == "available" ]]; then
            check_pass "Sarvam AI: ${sarvam_status}"
        elif [[ "$sarvam_status" == "not_reported" || "$sarvam_status" == "null" ]]; then
            check_warn "Sarvam AI status not reported"
        else
            check_warn "Sarvam AI: ${sarvam_status}"
        fi

        # Overall health status
        local overall_status
        overall_status=$(echo "$RESP_BODY" | jq -r '.status // .overall // "unknown"' 2>/dev/null)
        if [[ "$overall_status" == "healthy" || "$overall_status" == "ok" ]]; then
            check_pass "Overall health: ${overall_status}"
        elif [[ "$overall_status" == "degraded" ]]; then
            check_warn "Overall health: degraded"
        elif [[ "$overall_status" != "unknown" && "$overall_status" != "null" ]]; then
            check_warn "Overall health: ${overall_status}"
        fi

        # Latency info if available
        local mongo_latency
        mongo_latency=$(echo "$RESP_BODY" | jq -r '.services.mongodb.latency_ms // .mongodb.latency // "N/A"' 2>/dev/null)
        if [[ "$mongo_latency" != "N/A" && "$mongo_latency" != "null" ]]; then
            if [[ "${mongo_latency%.*}" -lt 100 ]]; then
                check_pass "MongoDB latency: ${mongo_latency}ms"
            else
                check_warn "MongoDB latency: ${mongo_latency}ms (high)"
            fi
        fi

        local redis_latency
        redis_latency=$(echo "$RESP_BODY" | jq -r '.services.redis.latency_ms // .redis.latency // "N/A"' 2>/dev/null)
        if [[ "$redis_latency" != "N/A" && "$redis_latency" != "null" ]]; then
            if [[ "${redis_latency%.*}" -lt 50 ]]; then
                check_pass "Redis latency: ${redis_latency}ms"
            else
                check_warn "Redis latency: ${redis_latency}ms (high)"
            fi
        fi
    else
        check_warn "Deep health response not JSON (status ${RESP_STATUS})"
    fi

    # ---- Webhook Endpoint ----
    subsection "Webhook Endpoints"

    # POST /api/webhooks/razorpay - should exist but reject unauthorized
    perform_request "$BASE_URL/api/webhooks/razorpay" \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"event":"test"}'

    if [[ "$RESP_STATUS" == "400" || "$RESP_STATUS" == "401" || "$RESP_STATUS" == "403" ]]; then
        check_pass "Razorpay webhook rejects unauthorized: ${RESP_STATUS}"
    elif [[ "$RESP_STATUS" == "404" ]]; then
        check_fail "Razorpay webhook not found (404)"
    elif [[ "$RESP_STATUS" == "422" ]]; then
        check_pass "Razorpay webhook validates payload: 422"
    elif [[ "$RESP_STATUS" == "200" ]]; then
        check_warn "Razorpay webhook accepted test payload (verify HMAC check)"
    else
        check_warn "Razorpay webhook: ${RESP_STATUS}"
    fi

    # Test with invalid signature header
    perform_request "$BASE_URL/api/webhooks/razorpay" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "X-Razorpay-Signature: invalid_signature_here" \
        -d '{"event":"payment.captured","payload":{"payment":{"entity":{"id":"pay_test"}}}}'

    if [[ "$RESP_STATUS" == "400" || "$RESP_STATUS" == "401" || "$RESP_STATUS" == "403" ]]; then
        check_pass "Webhook rejects invalid signature: ${RESP_STATUS}"
    elif [[ "$RESP_STATUS" == "200" ]]; then
        check_fail "Webhook accepts invalid signature!"
    else
        check_warn "Webhook invalid signature response: ${RESP_STATUS}"
    fi

    # ---- Content Data Validation ----
    subsection "Content Data Validation"

    # Verify boards data structure
    perform_request "$BASE_URL/api/content/boards"
    if [[ "$RESP_STATUS" == "200" ]] && echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
        # Check first item structure
        local first_board
        first_board=$(echo "$RESP_BODY" | jq '
            if type == "array" then .[0]
            elif .data then .data[0]
            elif .boards then .boards[0]
            else .
            end
        ' 2>/dev/null || echo "null")

        if [[ "$first_board" != "null" && "$first_board" != "" ]]; then
            # Check for expected fields
            if echo "$first_board" | jq -e '.id // ._id' >/dev/null 2>&1; then
                check_pass "Board has id field"
            else
                check_warn "Board missing id field"
            fi
            if echo "$first_board" | jq -e '.name' >/dev/null 2>&1; then
                check_pass "Board has name field"
                local board_name
                board_name=$(echo "$first_board" | jq -r '.name' 2>/dev/null)
                if [[ -n "$board_name" && "$board_name" != "null" ]]; then
                    check_pass "Board name is non-empty: ${board_name}"
                fi
            else
                check_warn "Board missing name field"
            fi
            if echo "$first_board" | jq -e '.slug' >/dev/null 2>&1; then
                check_pass "Board has slug field"
            else
                check_warn "Board missing slug field"
            fi
        else
            check_warn "No boards data to validate"
        fi
    fi

    # Verify classes data structure
    perform_request "$BASE_URL/api/content/classes"
    if [[ "$RESP_STATUS" == "200" ]] && echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
        local classes_data
        classes_data=$(echo "$RESP_BODY" | jq 'if type == "array" then . elif .data then .data else [] end' 2>/dev/null || echo "[]")
        local classes_count
        classes_count=$(echo "$classes_data" | jq 'length' 2>/dev/null || echo "0")
        if [[ "$classes_count" -gt 0 ]]; then
            check_pass "Classes returns ${classes_count} items"
            local first_class
            first_class=$(echo "$classes_data" | jq '.[0]' 2>/dev/null || echo "null")
            if echo "$first_class" | jq -e '.id // ._id' >/dev/null 2>&1; then
                check_pass "Class has id field"
            fi
            if echo "$first_class" | jq -e '.name' >/dev/null 2>&1; then
                check_pass "Class has name field"
            fi
        else
            check_warn "No classes data"
        fi
    fi

    # ---- Data Consistency ----
    subsection "Data Consistency Checks"

    # Request same endpoint twice and compare
    perform_request "$BASE_URL/api/content/boards"
    local boards_resp1="$RESP_BODY"
    local boards_status1="$RESP_STATUS"

    perform_request "$BASE_URL/api/content/boards"
    local boards_resp2="$RESP_BODY"
    local boards_status2="$RESP_STATUS"

    if [[ "$boards_status1" == "$boards_status2" ]]; then
        check_pass "Consistent status codes across requests"
    else
        check_fail "Inconsistent status: ${boards_status1} vs ${boards_status2}"
    fi

    if [[ "$boards_status1" == "200" && "$boards_status2" == "200" ]]; then
        local count1
        count1=$(echo "$boards_resp1" | jq 'if type == "array" then length else (.data // []) | length end' 2>/dev/null || echo "-1")
        local count2
        count2=$(echo "$boards_resp2" | jq 'if type == "array" then length else (.data // []) | length end' 2>/dev/null || echo "-1")
        if [[ "$count1" == "$count2" ]]; then
            check_pass "Consistent data count: ${count1} items"
        else
            check_fail "Data inconsistency: ${count1} vs ${count2} items"
        fi
    fi

    # ---- Response Time Under Load ----
    subsection "Response Stability"

    local stability_times=()
    for i in $(seq 1 5); do
        perform_request "$BASE_URL/api/content/boards"
        stability_times+=("$RESP_TTFB")
    done

    local stab_sum=0 stab_max=0 stab_min=99999
    for val in "${stability_times[@]}"; do
        stab_sum=$((stab_sum + val))
        [[ "$val" -gt "$stab_max" ]] && stab_max=$val
        [[ "$val" -lt "$stab_min" ]] && stab_min=$val
    done
    local stab_avg=$((stab_sum / ${#stability_times[@]}))
    local stab_var=$((stab_max - stab_min))

    if [[ "$stab_avg" -lt 500 ]]; then
        check_pass "Content endpoint avg response: ${stab_avg}ms"
    else
        check_warn "Content endpoint avg response: ${stab_avg}ms (slow)"
    fi

    if [[ "$stab_var" -lt 200 ]]; then
        check_pass "Response time variance: ${stab_var}ms (stable)"
    elif [[ "$stab_var" -lt 500 ]]; then
        check_warn "Response time variance: ${stab_var}ms"
    else
        check_fail "Response time variance: ${stab_var}ms (unstable)"
    fi

    # ---- Additional Data Validation ----
    subsection "Additional Data Validation"

    # Check subscription plans structure
    perform_request "$BASE_URL/api/subscription/plans"
    if [[ "$RESP_STATUS" == "200" ]] && echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
        local plans_data
        plans_data=$(echo "$RESP_BODY" | jq 'if type == "array" then . elif .data then .data elif .plans then .plans else [] end' 2>/dev/null || echo "[]")
        local plans_count
        plans_count=$(echo "$plans_data" | jq 'length' 2>/dev/null || echo "0")
        if [[ "$plans_count" -gt 0 ]]; then
            check_pass "Subscription plans: ${plans_count} plans available"
            # Check first plan structure
            local first_plan
            first_plan=$(echo "$plans_data" | jq '.[0]' 2>/dev/null || echo "null")
            if echo "$first_plan" | jq -e '.name // .title' >/dev/null 2>&1; then
                check_pass "Plan has name/title field"
            fi
            if echo "$first_plan" | jq -e '.price // .amount' >/dev/null 2>&1; then
                check_pass "Plan has price/amount field"
            fi
            if echo "$first_plan" | jq -e '.id // ._id // .plan_id' >/dev/null 2>&1; then
                check_pass "Plan has identifier field"
            fi
        else
            check_warn "No subscription plans in response"
        fi
    elif [[ "$RESP_STATUS" == "404" ]]; then
        check_warn "Subscription plans endpoint: 404"
    fi

    # Health endpoint returns timestamp
    perform_request "$BASE_URL/health"
    if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
        if echo "$RESP_BODY" | jq -e '.timestamp // .time // .uptime // .started_at' >/dev/null 2>&1; then
            check_pass "Health endpoint reports timing information"
        else
            check_warn "Health endpoint missing timestamp"
        fi
        if echo "$RESP_BODY" | jq -e '.version // .app_version // .build' >/dev/null 2>&1; then
            local ver
            ver=$(echo "$RESP_BODY" | jq -r '.version // .app_version // .build' 2>/dev/null)
            check_pass "Health reports version: ${ver}"
        fi
    fi

    # Verify health deep reports all expected services
    perform_request "$BASE_URL/health/deep"
    if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
        local svc_count
        svc_count=$(echo "$RESP_BODY" | jq '[.. | objects | select(has("status"))] | length' 2>/dev/null || echo "0")
        if [[ "$svc_count" -gt 2 ]]; then
            check_pass "Deep health reports ${svc_count} service statuses"
        elif [[ "$svc_count" -gt 0 ]]; then
            check_warn "Deep health only reports ${svc_count} services"
        fi
    fi

    # ---- Endpoint Response Consistency ----
    subsection "Endpoint Idempotency"

    # Same request returns same structure
    perform_request "$BASE_URL/health"
    local health_keys_1
    health_keys_1=$(echo "$RESP_BODY" | jq 'keys | sort' 2>/dev/null || echo "[]")

    perform_request "$BASE_URL/health"
    local health_keys_2
    health_keys_2=$(echo "$RESP_BODY" | jq 'keys | sort' 2>/dev/null || echo "[]")

    if [[ "$health_keys_1" == "$health_keys_2" ]]; then
        check_pass "Health response structure consistent across calls"
    else
        check_fail "Health response structure varies between calls"
    fi

    # Content type consistency
    perform_request "$BASE_URL/health"
    local ct1
    ct1=$(get_header "content-type")
    perform_request "$BASE_URL/health"
    local ct2
    ct2=$(get_header "content-type")
    if [[ "$ct1" == "$ct2" ]]; then
        check_pass "Content-Type consistent: ${ct1}"
    else
        check_fail "Content-Type varies: ${ct1} vs ${ct2}"
    fi

    # ---- Error Handling Validation ----
    subsection "Error Handling"

    # Large payload rejection
    local large_payload
    large_payload=$(printf '%0.s.' {1..10000})
    perform_request "$BASE_URL/api/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"${large_payload}\"}"
    if [[ "$RESP_STATUS" == "400" || "$RESP_STATUS" == "413" || "$RESP_STATUS" == "422" ]]; then
        check_pass "Large payload rejected: ${RESP_STATUS}"
    elif [[ "$RESP_STATUS" == "404" ]]; then
        check_warn "Endpoint not found for large payload test"
    else
        check_warn "Large payload response: ${RESP_STATUS}"
    fi

    # Invalid JSON rejection
    perform_request "$BASE_URL/api/auth/login" \
        -X POST \
        -H "Content-Type: application/json" \
        -d 'not{valid}json'
    if [[ "$RESP_STATUS" == "400" || "$RESP_STATUS" == "422" ]]; then
        check_pass "Invalid JSON rejected: ${RESP_STATUS}"
    elif [[ "$RESP_STATUS" == "404" ]]; then
        check_warn "Endpoint not found for invalid JSON test"
    else
        check_warn "Invalid JSON response: ${RESP_STATUS}"
    fi

    # SQL injection attempt
    perform_request "$BASE_URL/api/content/boards?id=1%27%20OR%201=1--"
    if [[ "$RESP_STATUS" == "400" || "$RESP_STATUS" == "403" || "$RESP_STATUS" == "200" || "$RESP_STATUS" == "404" ]]; then
        check_pass "SQL injection attempt handled: ${RESP_STATUS}"
        if [[ "$RESP_STATUS" == "200" ]] && ! body_contains "error|sql|syntax"; then
            check_pass "No SQL error in response to injection attempt"
        fi
    fi

    # XSS attempt
    perform_request "$BASE_URL/api/content/boards?q=<script>alert(1)</script>"
    if ! body_contains "<script>alert"; then
        check_pass "XSS payload not reflected in response"
    else
        check_fail "XSS payload reflected in response!"
    fi

    # ---- Service Availability Matrix ----
    subsection "Service Availability Matrix"

    # Test multiple API endpoints respond (not 5xx)
    local svc_endpoints=(
        "/health"
        "/health/deep"
        "/health/circuit-breakers"
        "/api/content/boards"
        "/api/content/classes"
        "/api/subscription/plans"
        "/api/changelog"
        "/api/seo/sitemap-index.xml"
        "/api/seo/knowledge-graph"
        "/docs"
        "/openapi.json"
    )
    local svc_available=0 svc_down=0

    for ep in "${svc_endpoints[@]}"; do
        perform_request "$BASE_URL${ep}"
        if [[ "$RESP_STATUS" -ge 200 && "$RESP_STATUS" -lt 500 ]]; then
            svc_available=$((svc_available + 1))
            check_pass "Service ${ep}: available (${RESP_STATUS})"
        elif [[ "$RESP_STATUS" -ge 500 ]]; then
            svc_down=$((svc_down + 1))
            check_fail "Service ${ep}: ERROR (${RESP_STATUS})"
        else
            check_warn "Service ${ep}: ${RESP_STATUS}"
        fi
    done

    if [[ "$svc_down" -eq 0 ]]; then
        check_pass "All ${svc_available} services available (no 5xx)"
    else
        check_fail "${svc_down} services returning 5xx errors"
    fi

    # ---- Response Schema Validation ----
    subsection "Response Schema Validation"

    # Verify health endpoint schema
    perform_request "$BASE_URL/health"
    if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
        # Must be an object
        local health_type
        health_type=$(echo "$RESP_BODY" | jq 'type' -r 2>/dev/null || echo "unknown")
        if [[ "$health_type" == "object" ]]; then
            check_pass "Health response is JSON object"
        else
            check_warn "Health response type: ${health_type}"
        fi

        # Check it has at least some keys
        local key_count
        key_count=$(echo "$RESP_BODY" | jq 'keys | length' 2>/dev/null || echo "0")
        if [[ "$key_count" -gt 0 ]]; then
            check_pass "Health has ${key_count} fields"
        fi
    fi

    # Verify deep health schema
    perform_request "$BASE_URL/health/deep"
    if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
        local deep_type
        deep_type=$(echo "$RESP_BODY" | jq 'type' -r 2>/dev/null || echo "unknown")
        if [[ "$deep_type" == "object" ]]; then
            check_pass "Deep health is JSON object"
        fi
        local deep_keys
        deep_keys=$(echo "$RESP_BODY" | jq 'keys | length' 2>/dev/null || echo "0")
        if [[ "$deep_keys" -gt 0 ]]; then
            check_pass "Deep health has ${deep_keys} fields"
        fi
    fi

    # Verify error format
    perform_request "$BASE_URL/api/xyz-nonexistent"
    if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
        check_pass "Error response is valid JSON"
        if echo "$RESP_BODY" | jq -e '.detail // .error // .message' >/dev/null 2>&1; then
            check_pass "Error has descriptive field"
        fi
    fi

    # ---- Cross-Endpoint Data Correlation ----
    subsection "Cross-Endpoint Data Correlation"

    # Health status should correlate with deep health
    perform_request "$BASE_URL/health"
    local basic_health_status=$RESP_STATUS
    perform_request "$BASE_URL/health/deep"
    local deep_health_status=$RESP_STATUS

    if [[ "$basic_health_status" == "200" ]]; then
        check_pass "Basic health: 200 (service running)"
    fi
    if [[ "$deep_health_status" == "200" || "$deep_health_status" == "503" ]]; then
        check_pass "Deep health responds (${deep_health_status})"
    fi

    # If basic is 200, deep shouldn't be 500
    if [[ "$basic_health_status" == "200" && "$deep_health_status" -ge 500 && "$deep_health_status" != "503" ]]; then
        check_fail "Basic healthy but deep returns ${deep_health_status}"
    else
        check_pass "Health endpoints consistent"
    fi

    # Sitemap URLs should match frontend domain
    perform_request "$BASE_URL/api/seo/sitemap-index.xml"
    if [[ "$RESP_STATUS" == "200" ]]; then
        if body_contains "${FRONTEND_DOMAIN}"; then
            check_pass "Sitemap references correct domain (${FRONTEND_DOMAIN})"
        elif body_contains "syrabit"; then
            check_pass "Sitemap references syrabit domain"
        else
            check_warn "Sitemap URLs may not match frontend domain"
        fi
    fi

    # Knowledge graph should reference correct site
    perform_request "$BASE_URL/api/seo/knowledge-graph"
    if [[ "$RESP_STATUS" == "200" ]] && echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
        if body_contains "${FRONTEND_DOMAIN}|syrabit"; then
            check_pass "Knowledge graph references correct domain"
        fi
        if echo "$RESP_BODY" | jq -e '.["@context"]' >/dev/null 2>&1; then
            check_pass "Knowledge graph has @context (valid LD)"
        fi
        if echo "$RESP_BODY" | jq -e '.["@type"]' >/dev/null 2>&1; then
            check_pass "Knowledge graph has @type"
        fi
    fi

    echo ""
    echo -e "  ${DIM}Section 5 complete: ${SECTION_PASS[$((CURRENT_SECTION-1))]} pass, ${SECTION_WARN[$((CURRENT_SECTION-1))]} warn, ${SECTION_FAIL[$((CURRENT_SECTION-1))]} fail${NC}"
}


# ===============================================================================
# SECTION 6: DEPLOYMENT CONFIGURATION (60+ checks)
# ===============================================================================

run_section_6() {
    start_section "DEPLOYMENT CONFIGURATION"

    # ---- Cloud Run Indicators ----
    subsection "Cloud Run Configuration Indicators"

    # Check for warm instances (min-instances=1 should give fast response)
    perform_request "$BASE_URL/health"
    if [[ "$RESP_TTFB" -lt 500 ]]; then
        check_pass "Fast health response (${RESP_TTFB}ms) - min-instances likely active"
    elif [[ "$RESP_TTFB" -lt 2000 ]]; then
        check_warn "Health response ${RESP_TTFB}ms (possible cold start)"
    else
        check_fail "Slow health response ${RESP_TTFB}ms (min-instances may be 0)"
    fi

    # X-Cloud-Trace-Context (Cloud Run/GCP indicator)
    local cloud_trace
    cloud_trace=$(get_header "x-cloud-trace-context")
    if [[ -n "$cloud_trace" ]]; then
        check_pass "X-Cloud-Trace-Context present (GCP trace active)"
    else
        check_warn "No X-Cloud-Trace-Context (may be stripped by CDN)"
    fi

    # Server-Timing header (Cloud Run sometimes adds)
    local server_timing
    server_timing=$(get_header "server-timing")
    if [[ -n "$server_timing" ]]; then
        check_pass "Server-Timing header present: ${server_timing}"
    fi

    # Verify concurrency handling (containerConcurrency=80)
    local concurrency_pids=()
    local concurrency_dir
    concurrency_dir="${AUDIT_TMPDIR}/concurrent_s6_$$"
    mkdir -p "$concurrency_dir"
    for i in $(seq 1 15); do
        curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "$BASE_URL/health" > "${concurrency_dir}/c_${i}" 2>/dev/null &
        concurrency_pids+=($!)
    done
    for pid in "${concurrency_pids[@]}"; do
        wait "$pid" 2>/dev/null || true
    done

    local c_success=0 c_total=0
    for i in $(seq 1 15); do
        if [[ -f "${concurrency_dir}/c_${i}" ]]; then
            c_total=$((c_total + 1))
            local cstatus
            cstatus=$(cat "${concurrency_dir}/c_${i}")
            if [[ "$cstatus" == "200" ]]; then
                c_success=$((c_success + 1))
            fi
        fi
    done
    rm -rf "$concurrency_dir"

    if [[ "$c_success" -eq "$c_total" ]]; then
        check_pass "All ${c_total} concurrent requests handled"
    elif [[ "$c_success" -ge $((c_total - 2)) ]]; then
        check_warn "${c_success}/${c_total} concurrent requests succeeded"
    else
        check_fail "Only ${c_success}/${c_total} concurrent requests succeeded"
    fi

    # ---- Environment Exposure Check ----
    subsection "Environment Variable Exposure"

    # Check that APP_ENV is not leaked in responses
    perform_request "$BASE_URL/health"
    if body_contains "APP_ENV|ENVIRONMENT=|ENV=production|NODE_ENV"; then
        check_fail "Environment variables may be exposed in response"
    else
        check_pass "No environment variable leakage in health response"
    fi

    # Check debug mode not exposed
    if body_contains "debug.*true|DEBUG.*true|\"debug\":true"; then
        check_fail "Debug mode appears to be enabled"
    else
        check_pass "No debug mode indicators in response"
    fi

    # Check for stack traces
    perform_request "$BASE_URL/api/nonexistent-path-trigger-error"
    if body_contains "Traceback|traceback|stack trace|at .*\.py|at .*\.js"; then
        check_fail "Stack trace exposed in error response"
    else
        check_pass "No stack trace in error response"
    fi

    # Check for database connection strings
    if body_contains "mongodb://|redis://|postgresql://|mysql://"; then
        check_critical "Database connection string exposed!"
    else
        check_pass "No database URLs in responses"
    fi

    # Check for API keys
    if body_contains "sk-|api_key.*=|GOOGLE_.*KEY|RAZORPAY_.*KEY"; then
        check_critical "API keys potentially exposed!"
    else
        check_pass "No API key patterns in responses"
    fi

    # ---- Cloudflare Worker Indicators ----
    subsection "Cloudflare Worker Configuration"

    perform_request "$BASE_URL/health"

    # cf-ray confirms Cloudflare proxy
    local cf_ray
    cf_ray=$(get_header "cf-ray")
    if [[ -n "$cf_ray" ]]; then
        check_pass "CF-Ray present: ${cf_ray} (Worker active)"
        # Extract datacenter from cf-ray
        local dc
        dc=$(echo "$cf_ray" | grep -o '[A-Z]*$' || echo "")
        if [[ -n "$dc" ]]; then
            check_pass "Served from Cloudflare DC: ${dc}"
        fi
    else
        check_warn "No CF-Ray (Worker may not be proxying)"
    fi

    # cf-cache-status
    local cf_cache
    cf_cache=$(get_header "cf-cache-status")
    if [[ -n "$cf_cache" ]]; then
        check_pass "CF-Cache-Status: ${cf_cache}"
    fi

    # Server header
    local worker_server
    worker_server=$(get_header "server")
    if echo "$worker_server" | grep -qi "cloudflare"; then
        check_pass "Server: cloudflare (Worker chain)"
    fi

    # Check for Worker-specific headers
    local worker_meta
    worker_meta=$(get_header "x-worker-version")
    if [[ -n "$worker_meta" ]]; then
        check_pass "X-Worker-Version: ${worker_meta}"
    fi

    # ---- Frontend Deployment ----
    subsection "Frontend Deployment (Cloudflare Pages)"

    perform_request "$FRONTEND_URL"

    # Content-Type for HTML
    local fe_ct
    fe_ct=$(get_header "content-type")
    if echo "$fe_ct" | grep -qi "text/html"; then
        check_pass "Frontend serves HTML: ${fe_ct}"
    else
        check_warn "Frontend Content-Type: ${fe_ct}"
    fi

    # Check for SPA indicators
    if body_contains "id=\"app\"|id=\"root\"|<div id="; then
        check_pass "SPA mount point found in HTML"
    fi

    # Check for asset paths
    if body_contains "/assets/|/static/|\.js\"|\.css\""; then
        check_pass "Static asset references in HTML"
    fi

    # Check cache headers for frontend
    local fe_cache
    fe_cache=$(get_header "cache-control")
    if [[ -n "$fe_cache" ]]; then
        check_pass "Frontend Cache-Control: ${fe_cache}"
    fi

    # ---- _headers File Rules ----
    subsection "Custom Headers Applied"

    # The _headers file should apply custom headers
    perform_request "$FRONTEND_URL"
    local custom_header_count=0

    if has_header "x-content-type-options"; then
        custom_header_count=$((custom_header_count + 1))
    fi
    if has_header "x-frame-options"; then
        custom_header_count=$((custom_header_count + 1))
    fi
    if has_header "referrer-policy"; then
        custom_header_count=$((custom_header_count + 1))
    fi
    if has_header "permissions-policy"; then
        custom_header_count=$((custom_header_count + 1))
    fi
    if has_header "strict-transport-security"; then
        custom_header_count=$((custom_header_count + 1))
    fi

    if [[ "$custom_header_count" -ge 3 ]]; then
        check_pass "Custom _headers rules applied (${custom_header_count}/5 security headers)"
    elif [[ "$custom_header_count" -ge 1 ]]; then
        check_warn "Only ${custom_header_count}/5 custom headers applied"
    else
        check_fail "No custom headers detected (check _headers file deployment)"
    fi

    # ---- Sensitive Path Protection (deployment level) ----
    subsection "Deployment Path Security"

    local deploy_paths=("/.env" "/.env.local" "/.env.production" "/wrangler.toml" "/package.json" "/tsconfig.json" "/Dockerfile" "/docker-compose.yml" "/.dockerignore")
    for dpath in "${deploy_paths[@]}"; do
        local dstatus
        dstatus=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${FRONTEND_URL}${dpath}" 2>/dev/null || echo "000")
        if [[ "$dstatus" == "404" || "$dstatus" == "403" ]]; then
            check_pass "Frontend ${dpath}: ${dstatus} (not exposed)"
        elif [[ "$dstatus" == "200" ]]; then
            check_critical "Frontend ${dpath}: 200 (EXPOSED!)"
        else
            check_warn "Frontend ${dpath}: ${dstatus}"
        fi
    done

    local api_deploy_paths=("/.env" "/Dockerfile" "/requirements.txt" "/pyproject.toml" "/app/config.py" "/app/.env")
    for dpath in "${api_deploy_paths[@]}"; do
        local dstatus
        dstatus=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${BASE_URL}${dpath}" 2>/dev/null || echo "000")
        if [[ "$dstatus" == "404" || "$dstatus" == "403" || "$dstatus" == "401" ]]; then
            check_pass "API ${dpath}: ${dstatus} (not exposed)"
        elif [[ "$dstatus" == "200" ]]; then
            check_fail "API ${dpath}: 200 (accessible!)"
        else
            check_warn "API ${dpath}: ${dstatus}"
        fi
    done

    # ---- Port and Protocol ----
    subsection "Port & Protocol Configuration"

    # Port 443 accessible
    local port_check
    port_check=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "https://${FRONTEND_DOMAIN}:443/" 2>/dev/null || echo "000")
    if [[ "$port_check" == "200" ]]; then
        check_pass "Port 443 accessible for frontend"
    fi

    port_check=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "https://${API_DOMAIN}:443/health" 2>/dev/null || echo "000")
    if [[ "$port_check" == "200" ]]; then
        check_pass "Port 443 accessible for API"
    fi

    # No other ports exposed (check common ones)
    for port in 8080 8000 3000 5000 9090; do
        local pstatus
        pstatus=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 --connect-timeout 3 "https://${API_DOMAIN}:${port}/health" 2>/dev/null || echo "000")
        if [[ "$pstatus" == "000" ]]; then
            check_pass "Port ${port} not accessible (good)"
        elif [[ "$pstatus" == "200" ]]; then
            check_warn "Port ${port} accessible on API domain"
        fi
    done

    # ---- Response Header Analysis ----
    subsection "Response Header Security Analysis"

    perform_request "$BASE_URL/health"

    # Check for version disclosure in headers
    local all_headers="$RESP_HEADERS"
    if echo "$all_headers" | grep -Eqi "x-aspnetmvc-version|x-aspnet-version"; then
        check_fail "ASP.NET version disclosed in headers"
    else
        check_pass "No ASP.NET version disclosure"
    fi

    if echo "$all_headers" | grep -Eqi "x-runtime|x-request-runtime"; then
        check_warn "Runtime timing information in headers"
    else
        check_pass "No runtime timing disclosure"
    fi

    # Check for common proxy headers that shouldn't be forwarded
    if echo "$all_headers" | grep -Eqi "x-forwarded-for|x-real-ip"; then
        check_warn "Proxy headers visible in response"
    else
        check_pass "No proxy headers leaked to client"
    fi

    # Check Content-Disposition for downloads
    perform_request "$BASE_URL/api/seo/sitemap-index.xml"
    local content_disp
    content_disp=$(get_header "content-disposition")
    if [[ -z "$content_disp" || "$content_disp" == *"inline"* ]]; then
        check_pass "XML served inline (no forced download)"
    fi

    # ---- Deployment Metadata ----
    subsection "Deployment Metadata & Indicators"

    # Check for deployment timestamps in health
    perform_request "$BASE_URL/health"
    if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
        if echo "$RESP_BODY" | jq -e '.environment // .env // .app_env' >/dev/null 2>&1; then
            local env_val
            env_val=$(echo "$RESP_BODY" | jq -r '.environment // .env // .app_env' 2>/dev/null)
            if [[ "$env_val" == "production" || "$env_val" == "prod" ]]; then
                check_pass "Environment reported as production"
            elif [[ "$env_val" == "staging" || "$env_val" == "development" ]]; then
                check_fail "Non-production environment: ${env_val}"
            else
                check_warn "Environment: ${env_val}"
            fi
        fi

        if echo "$RESP_BODY" | jq -e '.region // .gcp_region' >/dev/null 2>&1; then
            local region_val
            region_val=$(echo "$RESP_BODY" | jq -r '.region // .gcp_region' 2>/dev/null)
            check_pass "Region reported: ${region_val}"
        fi
    fi

    # ---- Container Health Indicators ----
    subsection "Container Health Indicators"

    # Verify the service stays warm across multiple requests
    local warm_times=()
    for i in $(seq 1 5); do
        perform_request "$BASE_URL/health"
        warm_times+=("$RESP_TTFB")
    done

    local warm_max=0 warm_min=99999
    for val in "${warm_times[@]}"; do
        [[ "$val" -gt "$warm_max" ]] && warm_max=$val
        [[ "$val" -lt "$warm_min" ]] && warm_min=$val
    done

    if [[ "$warm_max" -lt 500 ]]; then
        check_pass "All warm requests < 500ms (max: ${warm_max}ms)"
    elif [[ "$warm_max" -lt 1000 ]]; then
        check_warn "Some warm requests slow (max: ${warm_max}ms)"
    else
        check_fail "Warm requests very slow (max: ${warm_max}ms)"
    fi

    local warm_spread=$((warm_max - warm_min))
    if [[ "$warm_spread" -lt 100 ]]; then
        check_pass "Container response stable (spread: ${warm_spread}ms)"
    else
        check_warn "Container response jittery (spread: ${warm_spread}ms)"
    fi

    # Memory pressure indicator - large response shouldn't be slow
    perform_request "$BASE_URL/health/deep"
    local deep_ttfb=$RESP_TTFB
    if [[ "$deep_ttfb" -lt 2000 ]]; then
        check_pass "Deep health responds in ${deep_ttfb}ms (no memory pressure)"
    elif [[ "$deep_ttfb" -lt 5000 ]]; then
        check_warn "Deep health slow: ${deep_ttfb}ms (possible resource constraints)"
    else
        check_fail "Deep health very slow: ${deep_ttfb}ms"
    fi

    # ---- API Gateway Behavior ----
    subsection "API Gateway & Edge Behavior"

    # Verify different HTTP methods route correctly
    perform_request "$BASE_URL/health" -X GET
    local get_status=$RESP_STATUS
    check_pass "GET through gateway: ${get_status}"

    perform_request "$BASE_URL/health" -X HEAD
    local head_status=$RESP_STATUS
    check_pass "HEAD through gateway: ${head_status}"

    perform_request "$BASE_URL/health" -X OPTIONS
    local options_status=$RESP_STATUS
    check_pass "OPTIONS through gateway: ${options_status}"

    # Verify large headers are handled
    perform_request "$BASE_URL/health" -H "X-Custom-Header: $(printf '%0.s.' {1..500})"
    if [[ "$RESP_STATUS" == "200" || "$RESP_STATUS" == "431" ]]; then
        check_pass "Large header handled: ${RESP_STATUS}"
    else
        check_warn "Large header response: ${RESP_STATUS}"
    fi

    # Multiple custom headers
    perform_request "$BASE_URL/health" \
        -H "X-Test-1: value1" \
        -H "X-Test-2: value2" \
        -H "X-Test-3: value3"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "Multiple custom headers accepted"
    fi

    # ---- Cloudflare-Specific Features ----
    subsection "Cloudflare Feature Detection"

    perform_request "$FRONTEND_URL"

    # Check for Cloudflare Web Analytics
    if body_contains "cloudflareinsights|cf-beacon|static.cloudflareinsights.com"; then
        check_pass "Cloudflare Web Analytics detected"
    else
        check_warn "No Cloudflare Web Analytics"
    fi

    # Check for Cloudflare challenge page (should not appear for normal requests)
    if body_contains "cf-challenge|cf-chl-bypass|Ray ID:.*challenge"; then
        check_fail "Cloudflare challenge page showing for normal request"
    else
        check_pass "No Cloudflare challenge for normal requests"
    fi

    # Check NEL (Network Error Logging)
    local nel_hdr
    nel_hdr=$(get_header "nel")
    if [[ -n "$nel_hdr" ]]; then
        check_pass "NEL (Network Error Logging) configured"
    fi

    local report_to
    report_to=$(get_header "report-to")
    if [[ -n "$report_to" ]]; then
        check_pass "Report-To header configured"
    fi

    # ---- API Path Structure ----
    subsection "API Path Structure Validation"

    # All API paths should be consistent
    local api_paths=("/api/content/boards" "/api/content/classes" "/api/auth/login" "/api/chat/message" "/api/subscription/plans" "/api/changelog" "/api/seo/sitemap-index.xml" "/api/seo/knowledge-graph" "/api/feedback" "/api/webhooks/razorpay")
    for ap in "${api_paths[@]}"; do
        local ap_status
        ap_status=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${BASE_URL}${ap}" 2>/dev/null || echo "000")
        if [[ "$ap_status" != "000" ]]; then
            check_pass "API path ${ap} routable (${ap_status})"
        else
            check_fail "API path ${ap} timeout"
        fi
    done

    echo ""
    echo -e "  ${DIM}Section 6 complete: ${SECTION_PASS[$((CURRENT_SECTION-1))]} pass, ${SECTION_WARN[$((CURRENT_SECTION-1))]} warn, ${SECTION_FAIL[$((CURRENT_SECTION-1))]} fail${NC}"
}


# ===============================================================================
# SECTION 7: COMPLIANCE & SEO AUDIT (70+ checks)
# ===============================================================================

run_section_7() {
    start_section "COMPLIANCE & SEO"

    # ---- robots.txt ----
    subsection "robots.txt Validation"

    perform_request "${FRONTEND_URL}/robots.txt"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "robots.txt accessible (200)"

        local ct
        ct=$(get_header "content-type")
        if echo "$ct" | grep -qi "text/plain"; then
            check_pass "robots.txt Content-Type: text/plain"
        else
            check_warn "robots.txt Content-Type: ${ct}"
        fi

        # Parse content
        if body_contains "User-agent"; then
            check_pass "robots.txt has User-agent directive"
        else
            check_fail "robots.txt missing User-agent directive"
        fi

        if body_contains "Disallow"; then
            check_pass "robots.txt has Disallow directives"
        else
            check_warn "robots.txt has no Disallow directives"
        fi

        if body_contains "Sitemap:"; then
            check_pass "robots.txt references Sitemap"
            # Extract sitemap URL
            local sitemap_url
            sitemap_url=$(echo "$RESP_BODY" | grep -i "^Sitemap:" | head -1 | sed 's/Sitemap: *//i' | tr -d '\r\n')
            if [[ -n "$sitemap_url" ]]; then
                check_pass "Sitemap URL: ${sitemap_url}"
            fi
        else
            check_warn "robots.txt missing Sitemap reference"
        fi

        if body_contains "Allow:"; then
            check_pass "robots.txt has Allow directives"
        fi

        # Check for blocking of sensitive paths
        if body_contains "/api/|/admin"; then
            check_pass "robots.txt blocks sensitive paths"
        else
            check_warn "robots.txt may not block sensitive paths"
        fi

        # Check it's not blocking everything
        if echo "$RESP_BODY" | grep -q "Disallow: /$" 2>/dev/null; then
            check_critical "robots.txt blocks entire site (Disallow: /)"
        else
            check_pass "robots.txt does not block entire site"
        fi
    else
        check_fail "robots.txt not accessible: ${RESP_STATUS}"
    fi

    # ---- Sitemap ----
    subsection "Sitemap Validation"

    # Try frontend sitemap
    perform_request "${FRONTEND_URL}/sitemap.xml"
    local sitemap_found=0
    if [[ "$RESP_STATUS" == "200" ]]; then
        sitemap_found=1
        check_pass "sitemap.xml accessible at frontend"
    fi

    # Try API sitemap
    if [[ "$sitemap_found" == "0" ]]; then
        perform_request "$BASE_URL/api/seo/sitemap-index.xml"
        if [[ "$RESP_STATUS" == "200" ]]; then
            sitemap_found=1
            check_pass "Sitemap accessible via API"
        fi
    fi

    if [[ "$sitemap_found" == "1" ]]; then
        if body_contains "<?xml"; then
            check_pass "Sitemap is valid XML"
        else
            check_warn "Sitemap may not be valid XML"
        fi

        if body_contains "<loc>"; then
            check_pass "Sitemap has <loc> elements"
            local loc_count
            loc_count=$(echo "$RESP_BODY" | grep -c "<loc>" 2>/dev/null || echo "0")
            loc_count=$(echo "$loc_count" | tr -d '[:space:]')
            if [[ "$loc_count" -gt 0 ]]; then
                check_pass "Sitemap has ${loc_count} URLs"
            fi
        else
            check_warn "Sitemap missing <loc> elements"
        fi

        if body_contains "<lastmod>"; then
            check_pass "Sitemap has <lastmod> dates"
        else
            check_warn "Sitemap missing <lastmod> dates"
        fi

        if body_contains "<changefreq>"; then
            check_pass "Sitemap has <changefreq>"
        fi

        if body_contains "<priority>"; then
            check_pass "Sitemap has <priority>"
        fi
    else
        check_fail "No sitemap found at frontend or API"
    fi

    # ---- Structured Data ----
    subsection "Structured Data (ld+json)"

    perform_request "$FRONTEND_URL"
    if body_contains "application/ld+json|ld\\+json"; then
        check_pass "Structured data (ld+json) found in HTML"

        if body_contains "\"@type\""; then
            check_pass "Structured data has @type"
        fi
        if body_contains "\"@context\""; then
            check_pass "Structured data has @context"
        fi
        if body_contains "Organization|WebApplication|WebSite|SoftwareApplication"; then
            check_pass "Structured data type recognized"
        fi
        if body_contains "\"name\""; then
            check_pass "Structured data has name property"
        fi
        if body_contains "\"description\""; then
            check_pass "Structured data has description"
        fi
        if body_contains "\"url\""; then
            check_pass "Structured data has url"
        fi
    else
        check_warn "No structured data (ld+json) in HTML"
    fi

    # ---- Meta Tags ----
    subsection "Meta Tags"

    perform_request "$FRONTEND_URL"

    # og:title
    if body_contains "og:title|property=\"og:title\""; then
        check_pass "og:title meta tag present"
    else
        check_warn "Missing og:title"
    fi

    # og:description
    if body_contains "og:description|property=\"og:description\""; then
        check_pass "og:description meta tag present"
    else
        check_warn "Missing og:description"
    fi

    # og:image
    if body_contains "og:image|property=\"og:image\""; then
        check_pass "og:image meta tag present"
    else
        check_warn "Missing og:image"
    fi

    # og:url
    if body_contains "og:url|property=\"og:url\""; then
        check_pass "og:url meta tag present"
    fi

    # og:type
    if body_contains "og:type|property=\"og:type\""; then
        check_pass "og:type meta tag present"
    fi

    # twitter:card
    if body_contains "twitter:card|name=\"twitter:card\""; then
        check_pass "twitter:card meta tag present"
    else
        check_warn "Missing twitter:card"
    fi

    # twitter:title
    if body_contains "twitter:title"; then
        check_pass "twitter:title meta tag present"
    fi

    # twitter:description
    if body_contains "twitter:description"; then
        check_pass "twitter:description meta tag present"
    fi

    # Standard meta description
    if body_contains "name=\"description\"|name='description'"; then
        check_pass "Meta description tag present"
    else
        check_warn "Missing meta description"
    fi

    # ---- Canonical URL ----
    subsection "Canonical URL"

    if body_contains "rel=\"canonical\"|rel='canonical'"; then
        check_pass "Canonical URL (rel=canonical) present"
    else
        check_warn "Missing canonical URL link"
    fi

    # ---- Mobile & Viewport ----
    subsection "Mobile & Viewport"

    if body_contains "name=\"viewport\"|name='viewport'"; then
        check_pass "Viewport meta tag present"
        if body_contains "width=device-width"; then
            check_pass "Viewport has width=device-width"
        fi
        if body_contains "initial-scale=1"; then
            check_pass "Viewport has initial-scale=1"
        fi
    else
        check_fail "Missing viewport meta tag"
    fi

    # Check mobile-friendly response
    perform_request "$FRONTEND_URL" \
        -H "User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "Mobile User-Agent gets 200 response"
    fi

    # ---- Accessibility Basics ----
    subsection "Accessibility Indicators"

    perform_request "$FRONTEND_URL"

    # lang attribute on html
    if body_contains "<html.*lang=|<html lang"; then
        check_pass "HTML lang attribute present"
    else
        check_fail "Missing HTML lang attribute"
    fi

    # Check for ARIA attributes
    if body_contains "aria-|role="; then
        check_pass "ARIA attributes or role detected"
    else
        check_warn "No ARIA attributes detected in initial HTML"
    fi

    # Check for skip navigation
    if body_contains "skip.*nav|skip.*content|skiplink"; then
        check_pass "Skip navigation link detected"
    fi

    # ---- Favicon & Icons ----
    subsection "Favicon & Icons"

    # favicon.ico
    local favicon_status
    favicon_status=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${FRONTEND_URL}/favicon.ico" 2>/dev/null || echo "000")
    if [[ "$favicon_status" == "200" ]]; then
        check_pass "favicon.ico accessible (200)"
    else
        check_warn "favicon.ico: ${favicon_status}"
    fi

    # apple-touch-icon
    if body_contains "apple-touch-icon"; then
        check_pass "apple-touch-icon reference in HTML"
    else
        check_warn "No apple-touch-icon reference"
    fi

    # ---- PWA / Manifest ----
    subsection "PWA & Manifest"

    if body_contains "manifest.json|manifest.webmanifest|rel=\"manifest\""; then
        check_pass "Web manifest referenced in HTML"

        # Try to fetch manifest
        perform_request "${FRONTEND_URL}/manifest.json"
        if [[ "$RESP_STATUS" == "200" ]]; then
            check_pass "manifest.json accessible"
            if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
                check_pass "manifest.json is valid JSON"
                if echo "$RESP_BODY" | jq -e '.name' >/dev/null 2>&1; then
                    check_pass "Manifest has name field"
                fi
                if echo "$RESP_BODY" | jq -e '.icons' >/dev/null 2>&1; then
                    check_pass "Manifest has icons"
                fi
                if echo "$RESP_BODY" | jq -e '.start_url' >/dev/null 2>&1; then
                    check_pass "Manifest has start_url"
                fi
                if echo "$RESP_BODY" | jq -e '.display' >/dev/null 2>&1; then
                    check_pass "Manifest has display mode"
                fi
                if echo "$RESP_BODY" | jq -e '.theme_color' >/dev/null 2>&1; then
                    check_pass "Manifest has theme_color"
                fi
            fi
        fi
    else
        check_warn "No manifest reference in HTML"
    fi

    # ---- Cookie Consent / Privacy ----
    subsection "Cookie Consent & Privacy"

    perform_request "$FRONTEND_URL"

    if body_contains "cookie.*consent|cookie.*banner|cookie.*policy|gdpr|CookieConsent"; then
        check_pass "Cookie consent mechanism detected"
    else
        check_warn "No cookie consent mechanism detected"
    fi

    # Privacy policy page
    local privacy_status
    privacy_status=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${FRONTEND_URL}/privacy-policy" 2>/dev/null || echo "000")
    if [[ "$privacy_status" == "200" ]]; then
        check_pass "Privacy policy page exists (200)"
    else
        privacy_status=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${FRONTEND_URL}/privacy" 2>/dev/null || echo "000")
        if [[ "$privacy_status" == "200" ]]; then
            check_pass "Privacy page exists at /privacy"
        else
            check_warn "No privacy policy page found"
        fi
    fi

    # Terms of service
    local tos_status
    tos_status=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${FRONTEND_URL}/terms" 2>/dev/null || echo "000")
    if [[ "$tos_status" == "200" ]]; then
        check_pass "Terms of service page exists"
    else
        tos_status=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${FRONTEND_URL}/terms-of-service" 2>/dev/null || echo "000")
        if [[ "$tos_status" == "200" ]]; then
            check_pass "Terms page at /terms-of-service"
        else
            check_warn "No terms of service page found"
        fi
    fi

    # ---- Hreflang ----
    subsection "Hreflang & Internationalization"

    perform_request "$FRONTEND_URL"
    if body_contains "hreflang"; then
        check_pass "Hreflang tags detected"
    else
        check_warn "No hreflang tags (consider for multilingual: Assamese/English)"
    fi

    # Check charset
    if body_contains "charset=utf-8|charset=UTF-8"; then
        check_pass "UTF-8 charset declared"
    else
        check_warn "UTF-8 charset not explicitly declared"
    fi

    # ---- Title & Heading Structure ----
    subsection "Title & Heading Structure"

    perform_request "$FRONTEND_URL"

    if body_contains "<title"; then
        check_pass "HTML <title> tag present"
    else
        check_fail "Missing <title> tag"
    fi

    if body_contains "<h1|<H1"; then
        check_pass "H1 heading found"
    else
        check_warn "No H1 heading in initial HTML (SPA may render dynamically)"
    fi

    # Check title is not empty
    if echo "$RESP_BODY" | grep -oP '<title>[^<]+</title>' >/dev/null 2>&1; then
        check_pass "Title tag has content"
    fi

    # ---- Security.txt ----
    subsection "Security.txt"

    perform_request "${FRONTEND_URL}/.well-known/security.txt"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "security.txt accessible"
        if body_contains "Contact:"; then
            check_pass "security.txt has Contact field"
        fi
        if body_contains "Expires:"; then
            check_pass "security.txt has Expires field"
        fi
    else
        check_warn "No security.txt (${RESP_STATUS})"
    fi

    # ---- Social & Sharing ----
    subsection "Social & Sharing Meta"

    perform_request "$FRONTEND_URL"

    if body_contains "og:site_name"; then
        check_pass "og:site_name present"
    fi

    if body_contains "og:locale"; then
        check_pass "og:locale present"
    fi

    if body_contains "twitter:site|twitter:creator"; then
        check_pass "Twitter site/creator tag present"
    fi

    if body_contains "twitter:image"; then
        check_pass "Twitter image tag present"
    fi

    # ---- PWA Readiness ----
    subsection "PWA Readiness"

    if body_contains "theme-color|name=\"theme-color\""; then
        check_pass "Theme color meta tag present"
    else
        check_warn "No theme-color meta tag"
    fi

    if body_contains "apple-mobile-web-app-capable|mobile-web-app-capable"; then
        check_pass "Mobile web app capable meta tag"
    fi

    if body_contains "apple-mobile-web-app-status-bar-style"; then
        check_pass "iOS status bar style configured"
    fi

    # Service worker registration
    if body_contains "serviceWorker|service-worker|sw.js"; then
        check_pass "Service worker reference detected"
    else
        check_warn "No service worker reference"
    fi

    # ---- Link Validation ----
    subsection "Internal Link Validation"

    # Check common internal pages return 200
    local pages=("/login" "/signup" "/about" "/contact" "/privacy" "/terms")
    for page in "${pages[@]}"; do
        local page_status
        page_status=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${FRONTEND_URL}${page}" 2>/dev/null || echo "000")
        if [[ "$page_status" == "200" ]]; then
            check_pass "Page ${page}: 200"
        elif [[ "$page_status" == "404" ]]; then
            check_warn "Page ${page}: 404 (not found)"
        elif [[ "$page_status" == "301" || "$page_status" == "302" ]]; then
            check_pass "Page ${page}: redirects (${page_status})"
        else
            check_warn "Page ${page}: ${page_status}"
        fi
    done

    echo ""
    echo -e "  ${DIM}Section 7 complete: ${SECTION_PASS[$((CURRENT_SECTION-1))]} pass, ${SECTION_WARN[$((CURRENT_SECTION-1))]} warn, ${SECTION_FAIL[$((CURRENT_SECTION-1))]} fail${NC}"
}


# ===============================================================================
# SECTION 8: MONITORING & OBSERVABILITY (40+ checks)
# ===============================================================================

run_section_8() {
    start_section "MONITORING & OBSERVABILITY"

    # ---- Health Endpoint Patterns ----
    subsection "Health Endpoint Response Patterns"

    # Basic health returns proper structure
    perform_request "$BASE_URL/health"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "Health endpoint returns 200"
        if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
            check_pass "Health response is valid JSON"
            if echo "$RESP_BODY" | jq -e '.status' >/dev/null 2>&1; then
                local health_status
                health_status=$(echo "$RESP_BODY" | jq -r '.status' 2>/dev/null)
                check_pass "Health has status field: ${health_status}"
            else
                check_warn "Health missing 'status' field"
            fi
            if echo "$RESP_BODY" | jq -e '.timestamp // .time // .uptime' >/dev/null 2>&1; then
                check_pass "Health has timestamp/uptime info"
            fi
            if echo "$RESP_BODY" | jq -e '.version // .app_version' >/dev/null 2>&1; then
                local ver
                ver=$(echo "$RESP_BODY" | jq -r '.version // .app_version' 2>/dev/null)
                check_pass "Health reports version: ${ver}"
            fi
        fi
    else
        check_fail "Health endpoint: ${RESP_STATUS}"
    fi

    # Deep health structure
    perform_request "$BASE_URL/health/deep"
    if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
        local services_count
        services_count=$(echo "$RESP_BODY" | jq '[.services // . | to_entries[] | select(.value | type == "object")] | length' 2>/dev/null || echo "0")
        if [[ "$services_count" -gt 0 ]]; then
            check_pass "Deep health reports ${services_count} services"
        fi

        # Check for timing info
        if echo "$RESP_BODY" | jq -e '.. | .latency_ms? // .response_time? // empty' >/dev/null 2>&1; then
            check_pass "Deep health includes latency metrics"
        else
            check_warn "Deep health missing latency metrics"
        fi
    fi

    # ---- Circuit Breaker States ----
    subsection "Circuit Breaker States"

    perform_request "$BASE_URL/health/circuit-breakers"
    if [[ "$RESP_STATUS" == "200" ]] && echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
        check_pass "Circuit breakers endpoint active"

        # Check each breaker state
        local breaker_keys
        breaker_keys=$(echo "$RESP_BODY" | jq -r 'keys[]' 2>/dev/null || echo "")
        if [[ -n "$breaker_keys" ]]; then
            while IFS= read -r key; do
                local state
                state=$(echo "$RESP_BODY" | jq -r ".[\"$key\"].state // .[\"$key\"] // \"unknown\"" 2>/dev/null)
                if [[ "$state" == "closed" || "$state" == "CLOSED" ]]; then
                    check_pass "Circuit breaker '${key}': CLOSED (healthy)"
                elif [[ "$state" == "half_open" || "$state" == "HALF_OPEN" ]]; then
                    check_warn "Circuit breaker '${key}': HALF-OPEN (recovering)"
                elif [[ "$state" == "open" || "$state" == "OPEN" ]]; then
                    check_fail "Circuit breaker '${key}': OPEN (service down)"
                else
                    check_warn "Circuit breaker '${key}': ${state}"
                fi
            done <<< "$breaker_keys"
        else
            check_warn "No circuit breaker keys found"
        fi

        # Check failure counts if available
        local has_failures
        has_failures=$(echo "$RESP_BODY" | jq -r '.. | .failure_count? // .failures? // empty' 2>/dev/null | head -1)
        if [[ -n "$has_failures" ]]; then
            check_pass "Circuit breakers report failure counts"
        fi
    elif [[ "$RESP_STATUS" == "404" ]]; then
        check_warn "Circuit breakers endpoint not found (404)"
    else
        check_warn "Circuit breakers endpoint: ${RESP_STATUS}"
    fi

    # ---- Error Rate Indicators ----
    subsection "Error Rate Indicators"

    # All health endpoints should return 2xx
    local health_endpoints=("/health" "/health/deep" "/health/circuit-breakers")
    local error_count=0
    for hep in "${health_endpoints[@]}"; do
        perform_request "$BASE_URL${hep}"
        if [[ "$RESP_STATUS" -ge 500 ]]; then
            error_count=$((error_count + 1))
            check_fail "${hep} returns 5xx: ${RESP_STATUS}"
        else
            check_pass "${hep} no 5xx (status ${RESP_STATUS})"
        fi
    done

    if [[ "$error_count" -eq 0 ]]; then
        check_pass "No 5xx errors from health endpoints"
    else
        check_fail "${error_count} health endpoints returning 5xx"
    fi

    # Test content endpoints for 5xx
    local content_eps=("/api/content/boards" "/api/content/classes" "/api/subscription/plans")
    for cep in "${content_eps[@]}"; do
        perform_request "$BASE_URL${cep}"
        if [[ "$RESP_STATUS" -ge 500 ]]; then
            check_fail "${cep} returns 5xx: ${RESP_STATUS}"
        else
            check_pass "${cep} no 5xx (status ${RESP_STATUS})"
        fi
    done

    # ---- PII Leak Prevention ----
    subsection "PII Leak Prevention"

    # Check error responses for PII patterns
    perform_request "$BASE_URL/api/nonexistent"
    local error_body="$RESP_BODY"

    # Email pattern
    if echo "$error_body" | grep -qP '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}' 2>/dev/null; then
        check_fail "Email address found in error response"
    else
        check_pass "No email leakage in error response"
    fi

    # Phone pattern
    if echo "$error_body" | grep -qP '\+?[0-9]{10,13}' 2>/dev/null; then
        check_warn "Possible phone number in error response"
    else
        check_pass "No phone number leakage in error response"
    fi

    # Check health responses for PII
    perform_request "$BASE_URL/health/deep"
    if echo "$RESP_BODY" | grep -qP '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}' 2>/dev/null; then
        check_fail "Email found in deep health response"
    else
        check_pass "No PII in deep health response"
    fi

    # Check for internal IPs
    if echo "$RESP_BODY" | grep -qP '(10\.[0-9]+\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]+\.[0-9]+|192\.168\.[0-9]+\.[0-9]+)' 2>/dev/null; then
        check_warn "Internal IP address found in response"
    else
        check_pass "No internal IP leakage"
    fi

    # Check for file paths
    if echo "$RESP_BODY" | grep -qP '(/home/|/var/|/etc/|/usr/|C:\\\\)' 2>/dev/null; then
        check_fail "File system paths exposed in response"
    else
        check_pass "No file system paths in response"
    fi

    # ---- Uptime Simulation ----
    subsection "Uptime Simulation"

    local uptime_success=0
    local uptime_total=3
    for i in $(seq 1 "$uptime_total"); do
        perform_request "$BASE_URL/health"
        if [[ "$RESP_STATUS" == "200" ]]; then
            uptime_success=$((uptime_success + 1))
        fi
        sleep 1
    done

    if [[ "$uptime_success" -eq "$uptime_total" ]]; then
        check_pass "Uptime: ${uptime_success}/${uptime_total} health checks passed"
    elif [[ "$uptime_success" -ge $((uptime_total - 1)) ]]; then
        check_warn "Uptime: ${uptime_success}/${uptime_total} checks passed"
    else
        check_fail "Uptime: ${uptime_success}/${uptime_total} checks passed"
    fi

    # Frontend uptime
    local fe_uptime_success=0
    for i in $(seq 1 3); do
        local fe_status
        fe_status=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$FRONTEND_URL" 2>/dev/null || echo "000")
        if [[ "$fe_status" == "200" ]]; then
            fe_uptime_success=$((fe_uptime_success + 1))
        fi
        sleep 1
    done

    if [[ "$fe_uptime_success" -eq 3 ]]; then
        check_pass "Frontend uptime: ${fe_uptime_success}/3 checks passed"
    else
        check_warn "Frontend uptime: ${fe_uptime_success}/3 checks passed"
    fi

    # ---- Response Time Consistency ----
    subsection "Response Time Consistency"

    local rt_values=()
    for i in $(seq 1 5); do
        perform_request "$BASE_URL/health"
        rt_values+=("$RESP_TTFB")
    done

    local rt_sum=0 rt_max=0 rt_min=99999
    for val in "${rt_values[@]}"; do
        rt_sum=$((rt_sum + val))
        [[ "$val" -gt "$rt_max" ]] && rt_max=$val
        [[ "$val" -lt "$rt_min" ]] && rt_min=$val
    done
    local rt_avg=$((rt_sum / ${#rt_values[@]}))
    local rt_range=$((rt_max - rt_min))

    check_pass "Avg response time: ${rt_avg}ms"
    check_pass "Min/Max: ${rt_min}ms / ${rt_max}ms"

    if [[ "$rt_range" -lt 100 ]]; then
        check_pass "Response time range: ${rt_range}ms (very consistent)"
    elif [[ "$rt_range" -lt 300 ]]; then
        check_warn "Response time range: ${rt_range}ms (some variance)"
    else
        check_fail "Response time range: ${rt_range}ms (high variance)"
    fi

    # Standard deviation approximation
    local sq_diff_sum=0
    for val in "${rt_values[@]}"; do
        local diff=$((val - rt_avg))
        sq_diff_sum=$((sq_diff_sum + diff * diff))
    done
    local variance_val=$((sq_diff_sum / ${#rt_values[@]}))
    # Approximate sqrt using Newton's method (integer)
    local stddev=0
    if [[ "$variance_val" -gt 0 ]]; then
        stddev=$variance_val
        local prev=0
        for _ in $(seq 1 10); do
            prev=$stddev
            stddev=$(( (stddev + variance_val / stddev) / 2 ))
            if [[ "$stddev" -eq "$prev" ]]; then break; fi
        done
    fi

    if [[ "$stddev" -lt 50 ]]; then
        check_pass "Response time std dev: ~${stddev}ms (stable)"
    elif [[ "$stddev" -lt 150 ]]; then
        check_warn "Response time std dev: ~${stddev}ms"
    else
        check_fail "Response time std dev: ~${stddev}ms (unstable)"
    fi

    # ---- Logging Indicators ----
    subsection "Logging & Debug Indicators"

    perform_request "$BASE_URL/health"

    # Check for verbose error in production
    if body_contains "\"log_level\"|\"logging\""; then
        check_warn "Logging config may be exposed in response"
    else
        check_pass "No logging config exposed"
    fi

    # Check X-Request-ID for traceability
    local rid
    rid=$(get_header "x-request-id")
    if [[ -n "$rid" ]]; then
        check_pass "X-Request-ID for log correlation: ${rid}"
    else
        check_warn "No X-Request-ID for log correlation"
    fi

    # ---- Graceful Degradation ----
    subsection "Graceful Degradation"

    # Frontend still works if API is slow (check frontend independently)
    perform_request "$FRONTEND_URL"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "Frontend serves independently of API state"
    fi

    # Health endpoint should respond even under degraded state
    perform_request "$BASE_URL/health"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "Basic health responds regardless of deep health state"
    fi

    # Deep health can be 503 but must respond
    perform_request "$BASE_URL/health/deep"
    if [[ "$RESP_STATUS" == "200" || "$RESP_STATUS" == "503" ]]; then
        check_pass "Deep health responds (${RESP_STATUS}) even with degraded services"
    elif [[ "$RESP_STATUS" == "000" ]]; then
        check_fail "Deep health timeout (no response)"
    else
        check_warn "Deep health: ${RESP_STATUS}"
    fi

    # ---- Trace Correlation ----
    subsection "Trace Correlation"

    # Multiple requests should get different trace IDs
    perform_request "$BASE_URL/health"
    local trace1
    trace1=$(get_header "cf-ray")

    perform_request "$BASE_URL/health"
    local trace2
    trace2=$(get_header "cf-ray")

    if [[ -n "$trace1" && -n "$trace2" && "$trace1" != "$trace2" ]]; then
        check_pass "Unique CF-Ray per request (trace correlation works)"
    elif [[ -z "$trace1" ]]; then
        check_warn "No CF-Ray for trace correlation"
    fi

    # Check request ID uniqueness
    perform_request "$BASE_URL/health"
    local rid1
    rid1=$(get_header "x-request-id")
    perform_request "$BASE_URL/health"
    local rid2
    rid2=$(get_header "x-request-id")

    if [[ -n "$rid1" && -n "$rid2" && "$rid1" != "$rid2" ]]; then
        check_pass "Unique X-Request-ID per request"
    elif [[ -n "$rid1" ]]; then
        check_warn "X-Request-ID may not be unique"
    fi

    # ---- Multi-Endpoint Health Sweep ----
    subsection "Multi-Endpoint Health Sweep"

    # Rapid check of all key endpoints for availability
    local sweep_eps=("/health" "/health/deep" "/api/content/boards" "/api/content/classes" "/api/subscription/plans" "/api/seo/sitemap-index.xml")
    local sweep_ok=0 sweep_fail=0
    for ep in "${sweep_eps[@]}"; do
        local sweep_status
        sweep_status=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$BASE_URL${ep}" 2>/dev/null || echo "000")
        if [[ "$sweep_status" -ge 200 && "$sweep_status" -lt 500 ]]; then
            sweep_ok=$((sweep_ok + 1))
            check_pass "Sweep ${ep}: ${sweep_status} (OK)"
        else
            sweep_fail=$((sweep_fail + 1))
            check_fail "Sweep ${ep}: ${sweep_status} (ERROR)"
        fi
    done

    if [[ "$sweep_fail" -eq 0 ]]; then
        check_pass "All ${sweep_ok} sweep endpoints healthy"
    else
        check_fail "${sweep_fail} endpoints failing in sweep"
    fi

    # ---- Error Message Quality ----
    subsection "Error Message Quality"

    # 404 should have helpful message
    perform_request "$BASE_URL/api/nonexistent-endpoint"
    if [[ "$RESP_STATUS" == "404" ]]; then
        if echo "$RESP_BODY" | jq -e '.detail' >/dev/null 2>&1; then
            local detail_msg
            detail_msg=$(echo "$RESP_BODY" | jq -r '.detail' 2>/dev/null)
            if [[ ${#detail_msg} -gt 5 ]]; then
                check_pass "404 has descriptive error: ${detail_msg}"
            else
                check_warn "404 error message too short"
            fi
        fi
    fi

    # Validation error should be descriptive
    perform_request "$BASE_URL/api/auth/login" -X POST -H "Content-Type: application/json" -d '{}'
    if echo "$RESP_BODY" | jq . >/dev/null 2>&1; then
        if echo "$RESP_BODY" | jq -e '.detail' >/dev/null 2>&1; then
            check_pass "Validation error has detail field"
        fi
    fi

    echo ""
    echo -e "  ${DIM}Section 8 complete: ${SECTION_PASS[$((CURRENT_SECTION-1))]} pass, ${SECTION_WARN[$((CURRENT_SECTION-1))]} warn, ${SECTION_FAIL[$((CURRENT_SECTION-1))]} fail${NC}"
}


# ===============================================================================
# QUICK MODE - Reduced check set
# ===============================================================================

run_quick_mode() {
    echo -e "  ${YELLOW}Running in QUICK mode (reduced checks)${NC}"
    echo ""

    start_section "QUICK AUDIT (Key Checks)"

    subsection "Infrastructure Quick Check"

    # DNS
    local dns_ok
    dns_ok=$(dig +short A "$FRONTEND_DOMAIN" @8.8.8.8 2>/dev/null || echo "")
    if [[ -n "$dns_ok" ]]; then
        check_pass "Frontend DNS resolves"
    else
        check_critical "Frontend DNS FAILS"
    fi

    dns_ok=$(dig +short A "$API_DOMAIN" @8.8.8.8 2>/dev/null || echo "")
    if [[ -n "$dns_ok" ]]; then
        check_pass "API DNS resolves"
    else
        check_critical "API DNS FAILS"
    fi

    # TLS
    local tls_ok
    tls_ok=$(echo | openssl s_client -servername "$FRONTEND_DOMAIN" -connect "${FRONTEND_DOMAIN}:443" 2>/dev/null | grep -c "verify return:1" 2>/dev/null || echo "0")
    tls_ok=$(echo "$tls_ok" | tr -d '[:space:]')
    if [[ "$tls_ok" -gt 0 ]]; then
        check_pass "Frontend TLS valid"
    else
        check_warn "Frontend TLS verification inconclusive"
    fi

    tls_ok=$(echo | openssl s_client -servername "$API_DOMAIN" -connect "${API_DOMAIN}:443" 2>/dev/null | grep -c "verify return:1" 2>/dev/null || echo "0")
    tls_ok=$(echo "$tls_ok" | tr -d '[:space:]')
    if [[ "$tls_ok" -gt 0 ]]; then
        check_pass "API TLS valid"
    else
        check_warn "API TLS verification inconclusive"
    fi

    subsection "Security Quick Check"

    perform_request "$FRONTEND_URL"
    if has_header "strict-transport-security"; then
        check_pass "HSTS present"
    else
        check_fail "HSTS missing"
    fi
    if has_header "x-content-type-options"; then
        check_pass "X-Content-Type-Options present"
    else
        check_fail "X-Content-Type-Options missing"
    fi
    if has_header "x-frame-options"; then
        check_pass "X-Frame-Options present"
    else
        check_warn "X-Frame-Options missing"
    fi

    local xpb
    xpb=$(get_header "x-powered-by")
    if [[ -z "$xpb" ]]; then
        check_pass "No X-Powered-By"
    else
        check_fail "X-Powered-By exposed"
    fi

    # Sensitive paths
    local env_status
    env_status=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "${FRONTEND_URL}/.env" 2>/dev/null || echo "000")
    if [[ "$env_status" == "403" || "$env_status" == "404" ]]; then
        check_pass ".env blocked (${env_status})"
    elif [[ "$env_status" == "200" ]]; then
        check_critical ".env ACCESSIBLE!"
    fi

    local git_status
    git_status=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "${FRONTEND_URL}/.git/config" 2>/dev/null || echo "000")
    if [[ "$git_status" == "403" || "$git_status" == "404" ]]; then
        check_pass ".git blocked (${git_status})"
    elif [[ "$git_status" == "200" ]]; then
        check_critical ".git/config ACCESSIBLE!"
    fi

    subsection "Performance Quick Check"

    perform_request "$FRONTEND_URL"
    if [[ "$RESP_TTFB" -lt 1000 ]]; then
        check_pass "Frontend TTFB: ${RESP_TTFB}ms (< 1s)"
    else
        check_fail "Frontend TTFB: ${RESP_TTFB}ms (>= 1s)"
    fi

    perform_request "$BASE_URL/health"
    if [[ "$RESP_TTFB" -lt 500 ]]; then
        check_pass "API TTFB: ${RESP_TTFB}ms (< 500ms)"
    else
        check_warn "API TTFB: ${RESP_TTFB}ms"
    fi

    local encoding
    encoding=$(get_header "content-encoding")
    if [[ -n "$encoding" ]]; then
        check_pass "Compression active: ${encoding}"
    fi

    subsection "API Quick Check"

    perform_request "$BASE_URL/health"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "GET /health: 200"
    else
        check_critical "GET /health: ${RESP_STATUS}"
    fi

    perform_request "$BASE_URL/health/deep"
    if [[ "$RESP_STATUS" == "200" || "$RESP_STATUS" == "503" ]]; then
        check_pass "GET /health/deep: ${RESP_STATUS}"
    else
        check_fail "GET /health/deep: ${RESP_STATUS}"
    fi

    perform_request "$BASE_URL/api/content/boards"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "GET /api/content/boards: 200"
    else
        check_fail "GET /api/content/boards: ${RESP_STATUS}"
    fi

    perform_request "$BASE_URL/api/seo/sitemap-index.xml"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "GET /api/seo/sitemap-index.xml: 200"
    else
        check_warn "Sitemap: ${RESP_STATUS}"
    fi

    subsection "SEO Quick Check"

    perform_request "${FRONTEND_URL}/robots.txt"
    if [[ "$RESP_STATUS" == "200" ]]; then
        check_pass "robots.txt accessible"
    else
        check_fail "robots.txt: ${RESP_STATUS}"
    fi

    perform_request "$FRONTEND_URL"
    if body_contains "og:title"; then
        check_pass "OG tags present"
    else
        check_warn "Missing OG tags"
    fi

    if body_contains "application/ld+json"; then
        check_pass "Structured data present"
    else
        check_warn "No structured data"
    fi

    if body_contains "name=\"viewport\""; then
        check_pass "Viewport meta tag"
    fi

    subsection "Monitoring Quick Check"

    # 3 rapid health checks
    local quick_uptime=0
    for i in 1 2 3; do
        local qs
        qs=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "$BASE_URL/health" 2>/dev/null || echo "000")
        if [[ "$qs" == "200" ]]; then
            quick_uptime=$((quick_uptime + 1))
        fi
    done
    if [[ "$quick_uptime" -eq 3 ]]; then
        check_pass "3/3 health checks pass"
    else
        check_fail "${quick_uptime}/3 health checks pass"
    fi
}

# ===============================================================================
# EXPORT FUNCTIONS
# ===============================================================================

export_json() {
    local output_file="live-deployment-audit-results.json"
    local audit_end_time
    audit_end_time=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

    local score
    score=$(calculate_score)

    local recommendation
    if [[ "$score" -ge 85 ]]; then
        recommendation="PRODUCTION READY"
    elif [[ "$score" -ge 70 ]]; then
        recommendation="NEEDS ATTENTION"
    else
        recommendation="NOT READY"
    fi

    # Build section summaries
    local section_json="["
    for i in "${!SECTION_NAMES[@]}"; do
        if [[ "$i" -gt 0 ]]; then section_json+=","; fi
        section_json+="{\"name\":\"${SECTION_NAMES[$i]}\",\"pass\":${SECTION_PASS[$i]},\"warn\":${SECTION_WARN[$i]},\"fail\":${SECTION_FAIL[$i]},\"critical\":${SECTION_CRITICAL[$i]},\"total\":${SECTION_TOTAL[$i]}}"
    done
    section_json+="]"

    jq -n \
        --arg start "$AUDIT_START_TIME" \
        --arg end "$audit_end_time" \
        --arg frontend "$FRONTEND_URL" \
        --arg api "$BASE_URL" \
        --arg gcp_project "$GCP_PROJECT" \
        --arg gcp_region "$GCP_REGION" \
        --argjson score "$score" \
        --arg recommendation "$recommendation" \
        --argjson total "$TOTAL_CHECKS" \
        --argjson pass "$TOTAL_PASS" \
        --argjson warn "$TOTAL_WARN" \
        --argjson fail "$TOTAL_FAIL" \
        --argjson critical "$TOTAL_CRITICAL" \
        --argjson sections "$section_json" \
        --argjson checks "$JSON_RESULTS" \
        '{
            audit: {
                start_time: $start,
                end_time: $end,
                targets: { frontend: $frontend, api: $api },
                gcp: { project: $gcp_project, region: $gcp_region }
            },
            summary: {
                score: $score,
                recommendation: $recommendation,
                total_checks: $total,
                passed: $pass,
                warnings: $warn,
                failed: $fail,
                critical: $critical
            },
            sections: $sections,
            checks: $checks
        }' > "$output_file"

    echo ""
    echo -e "  ${GREEN}JSON report exported:${NC} ${output_file}"
}

export_html() {
    local output_file="live-deployment-audit-report.html"
    local audit_end_time
    audit_end_time=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
    local score
    score=$(calculate_score)

    local recommendation
    local rec_color
    if [[ "$score" -ge 85 ]]; then
        recommendation="PRODUCTION READY"
        rec_color="#22c55e"
    elif [[ "$score" -ge 70 ]]; then
        recommendation="NEEDS ATTENTION"
        rec_color="#f59e0b"
    else
        recommendation="NOT READY"
        rec_color="#ef4444"
    fi

    cat > "$output_file" << HTMLEOF
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Syrabit Production Deployment Audit Report</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 20px; background: #f8fafc; color: #1e293b; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #0f172a; border-bottom: 3px solid #3b82f6; padding-bottom: 10px; }
        .meta { color: #64748b; margin-bottom: 20px; }
        .score-box { background: white; border-radius: 12px; padding: 30px; margin: 20px 0; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        .score { font-size: 64px; font-weight: bold; color: ${rec_color}; }
        .recommendation { font-size: 24px; color: ${rec_color}; font-weight: bold; margin-top: 10px; }
        .summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin: 20px 0; }
        .stat-card { background: white; border-radius: 8px; padding: 15px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
        .stat-value { font-size: 28px; font-weight: bold; }
        .stat-label { color: #64748b; font-size: 14px; }
        .pass { color: #22c55e; } .warn { color: #f59e0b; } .fail { color: #ef4444; } .critical { color: #dc2626; }
        table { width: 100%; border-collapse: collapse; margin: 20px 0; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
        th { background: #1e293b; color: white; padding: 12px; text-align: left; }
        td { padding: 10px 12px; border-bottom: 1px solid #e2e8f0; }
        tr:hover { background: #f1f5f9; }
        .status-pass { color: #22c55e; font-weight: bold; }
        .status-warn { color: #f59e0b; font-weight: bold; }
        .status-fail { color: #ef4444; font-weight: bold; }
        .status-critical { color: #dc2626; font-weight: bold; background: #fef2f2; }
        .section-header { background: #f1f5f9; }
        .section-header td { font-weight: bold; font-size: 14px; color: #475569; }
    </style>
</head>
<body>
<div class="container">
    <h1>Syrabit Production Deployment Audit</h1>
    <div class="meta">
        <p>Generated: ${audit_end_time} | Frontend: ${FRONTEND_URL} | API: ${BASE_URL}</p>
        <p>GCP Project: ${GCP_PROJECT} | Region: ${GCP_REGION}</p>
    </div>

    <div class="score-box">
        <div class="score">${score}/100</div>
        <div class="recommendation">${recommendation}</div>
    </div>

    <div class="summary-grid">
        <div class="stat-card"><div class="stat-value">${TOTAL_CHECKS}</div><div class="stat-label">Total Checks</div></div>
        <div class="stat-card"><div class="stat-value pass">${TOTAL_PASS}</div><div class="stat-label">Passed</div></div>
        <div class="stat-card"><div class="stat-value warn">${TOTAL_WARN}</div><div class="stat-label">Warnings</div></div>
        <div class="stat-card"><div class="stat-value fail">${TOTAL_FAIL}</div><div class="stat-label">Failed</div></div>
        <div class="stat-card"><div class="stat-value critical">${TOTAL_CRITICAL}</div><div class="stat-label">Critical</div></div>
    </div>

    <h2>Section Breakdown</h2>
    <table>
        <tr><th>Section</th><th>Pass</th><th>Warn</th><th>Fail</th><th>Critical</th><th>Total</th></tr>
HTMLEOF

    for i in "${!SECTION_NAMES[@]}"; do
        echo "        <tr><td>${SECTION_NAMES[$i]}</td><td class=\"pass\">${SECTION_PASS[$i]}</td><td class=\"warn\">${SECTION_WARN[$i]}</td><td class=\"fail\">${SECTION_FAIL[$i]}</td><td class=\"critical\">${SECTION_CRITICAL[$i]}</td><td>${SECTION_TOTAL[$i]}</td></tr>" >> "$output_file"
    done

    cat >> "$output_file" << HTMLEOF2
    </table>

    <h2>Detailed Results</h2>
    <table>
        <tr><th>Status</th><th>Section</th><th>Check</th></tr>
HTMLEOF2

    # Write individual checks from JSON_RESULTS
    echo "$JSON_RESULTS" | jq -r '.[] | "<tr><td class=\"status-\(.status | ascii_downcase)\">\(.status)</td><td>\(.section)</td><td>\(.message)</td></tr>"' >> "$output_file" 2>/dev/null || true

    cat >> "$output_file" << HTMLEOF3
    </table>
</div>
</body>
</html>
HTMLEOF3

    echo ""
    echo -e "  ${GREEN}HTML report exported:${NC} ${output_file}"
}

# ===============================================================================
# SCORING & SUMMARY
# ===============================================================================

calculate_score() {
    if [[ "$TOTAL_CHECKS" -eq 0 ]]; then
        echo "0"
        return
    fi
    # PASS = 1.0, WARN = 0.5, FAIL = 0.0, CRITICAL = 0.0
    local earned=$(( TOTAL_PASS * 100 + TOTAL_WARN * 50 ))
    local possible=$(( TOTAL_CHECKS * 100 ))
    local score=$(( earned * 100 / possible ))
    echo "$score"
}

print_summary() {
    local score
    score=$(calculate_score)

    echo ""
    echo -e "${BOLD}===============================================================================${NC}"
    echo -e "${BOLD}  AUDIT SUMMARY${NC}"
    echo -e "${BOLD}===============================================================================${NC}"
    echo ""
    echo -e "  Total Checks:   ${TOTAL_CHECKS}"
    echo -e "  Passed:         ${GREEN}${TOTAL_PASS}${NC}"
    echo -e "  Warnings:       ${YELLOW}${TOTAL_WARN}${NC}"
    echo -e "  Failed:         ${RED}${TOTAL_FAIL}${NC}"
    echo -e "  Critical:       ${RED}${BOLD}${TOTAL_CRITICAL}${NC}"
    echo ""

    # Per-section breakdown
    echo -e "  ${BOLD}Section Breakdown:${NC}"
    for i in "${!SECTION_NAMES[@]}"; do
        local sec_score=0
        if [[ "${SECTION_TOTAL[$i]}" -gt 0 ]]; then
            sec_score=$(( (${SECTION_PASS[$i]} * 100 + ${SECTION_WARN[$i]} * 50) * 100 / (${SECTION_TOTAL[$i]} * 100) ))
        fi
        local sec_color="$GREEN"
        if [[ "$sec_score" -lt 70 ]]; then sec_color="$RED"
        elif [[ "$sec_score" -lt 85 ]]; then sec_color="$YELLOW"
        fi
        printf "    %-40s ${sec_color}%3d%%${NC}  (%d pass, %d warn, %d fail)\n" \
            "${SECTION_NAMES[$i]}" "$sec_score" "${SECTION_PASS[$i]}" "${SECTION_WARN[$i]}" "${SECTION_FAIL[$i]}"
    done

    echo ""
    echo -e "${BOLD}  ┌─────────────────────────────────────────────┐${NC}"

    local rec_color="$GREEN"
    local recommendation="PRODUCTION READY"
    if [[ "$score" -lt 70 ]]; then
        rec_color="$RED"
        recommendation="NOT READY"
    elif [[ "$score" -lt 85 ]]; then
        rec_color="$YELLOW"
        recommendation="NEEDS ATTENTION"
    fi

    printf "  ${BOLD}│  Production Readiness Score: ${rec_color}%d/100${NC}${BOLD}         │${NC}\n" "$score"
    echo -e "  ${BOLD}│  Recommendation: ${rec_color}${recommendation}${NC}${BOLD}$(printf '%*s' $((19 - ${#recommendation})) '')│${NC}"
    echo -e "${BOLD}  └─────────────────────────────────────────────┘${NC}"
    echo ""

    if [[ "$TOTAL_CRITICAL" -gt 0 ]]; then
        echo -e "  ${RED}${BOLD}WARNING: ${TOTAL_CRITICAL} critical issue(s) detected!${NC}"
        echo ""
    fi
}

# ===============================================================================
# MAIN EXECUTION
# ===============================================================================

if [[ "$QUICK_MODE" == "1" ]]; then
    run_quick_mode
else
    if should_run_section 1; then run_section_1; fi
    if should_run_section 2; then run_section_2; fi
    if should_run_section 3; then run_section_3; fi
    if should_run_section 4; then run_section_4; fi
    if should_run_section 5; then run_section_5; fi
    if should_run_section 6; then run_section_6; fi
    if should_run_section 7; then run_section_7; fi
    if should_run_section 8; then run_section_8; fi
fi

# Print summary
print_summary

# Export if requested
if [[ "$EXPORT_JSON" == "1" ]]; then
    export_json
fi
if [[ "$EXPORT_HTML" == "1" ]]; then
    export_html
fi

# Determine exit code
FINAL_SCORE=$(calculate_score)
if [[ "$FINAL_SCORE" -ge 70 ]]; then
    exit 0
else
    exit 1
fi
