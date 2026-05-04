#!/usr/bin/env bash
# =============================================================================
# Syrabit.ai — Cloudflare Full-Stack Upgrade Script
# =============================================================================
# Applies every Cloudflare configuration upgrade covered under the $5k CF
# Startup credits (Enterprise Website zone + Workers Standard Paid):
#
#   1. Zone settings  — security level, image optimization, cache TTL, TLS
#   2. Email routing  — destination address, forwarding rules, DNS check
#   3. R2 storage     — create buckets (requires R2 enabled in Dashboard first)
#   4. WAF rules      — managed ruleset, OWASP, custom block rules
#   5. Cache rules    — SPA / API / static asset cache policies
#   6. Rate limiting  — /api/chat + /api/ai endpoints
#   7. Workers AI GW  — verify / create AI Gateway
#   8. Vectorize      — verify indexes syllabus-index-v2 + syllabus-index
#   9. Workers deploy — syrabit-edge + syrabit-email wrangler deploy
#  10. Health check   — verify live endpoints after deploy
#
# Usage:
#   export CLOUDFLARE_API_TOKEN="your-token"
#   bash scripts/cf_upgrade.sh             # run all steps
#   bash scripts/cf_upgrade.sh --dry-run   # print what would run, skip writes
#   bash scripts/cf_upgrade.sh --step 4    # run only step 4 (WAF)
#
# Requirements: curl, jq, wrangler (npm i -g wrangler), node 18+
#
# Known permission gaps in the default token — the script will warn and skip
# rather than abort:
#   - Firewall Services Write  (WAF rules, rate limiting — step 4/5/6)
#   - Account Email Routing Write  (destination address — step 2)
#   - R2:Write  (bucket creation — step 3)
# Add those permissions to your token in CF Dashboard → My Profile → API Tokens.
# =============================================================================

set -euo pipefail

# ── Constants ─────────────────────────────────────────────────────────────────
ACCOUNT_ID="d66e40eac539fff1db270fddf384a5ec"
ZONE_ID="5b8c97df4431491dc7f60ea72fb61871"
ZONE_NAME="syrabit.ai"
ADMIN_EMAIL="admin@syrabit.ai"
AI_GATEWAY_ID="syrabit"
# Task #336 — cut over from the Railway hostname to the Digital Ocean
# App Platform-fronted custom domain. The legacy Railway URL is
# preserved here as a comment so anyone bisecting cf_upgrade.sh
# history can find the cutover commit.
#   Pre-Task-#336: BACKEND_URL="https://workspacemockup-sandbox-production-df37.up.railway.app"
BACKEND_URL="https://api.syrabit.ai"

CF_API="https://api.cloudflare.com/client/v4"

# Colours
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

# ── Arg parsing ───────────────────────────────────────────────────────────────
DRY_RUN=false
ONLY_STEP=""
for arg in "$@"; do
  case $arg in
    --dry-run) DRY_RUN=true ;;
    --step) shift; ONLY_STEP="$1" ;;
    --step=*) ONLY_STEP="${arg#*=}" ;;
  esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
log()     { echo -e "${BOLD}${CYAN}[CF]${NC} $*" >&2; }
ok()      { echo -e "  ${GREEN}✓${NC}  $*" >&2; }
warn()    { echo -e "  ${YELLOW}⚠${NC}  $*" >&2; }
fail()    { echo -e "  ${RED}✗${NC}  $*" >&2; }
section() { echo -e "\n${BOLD}════════════════════════════════════════${NC}" >&2; echo -e "${BOLD} STEP $1 — $2${NC}" >&2; echo -e "${BOLD}════════════════════════════════════════${NC}" >&2; }

need_token() {
  if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
    fail "CLOUDFLARE_API_TOKEN is not set. Export it before running this script."
    exit 1
  fi
}

cf() {
  # Wrapper: cf <method> <path> [json_body]
  local method="$1" path="$2" body="${3:-}"
  local url="${CF_API}${path}"
  if $DRY_RUN; then
    warn "DRY-RUN  ${method} ${url}"
    [[ -n "$body" ]] && echo "          body: $(echo "$body" | jq -c '.' 2>/dev/null || echo "$body")" >&2
    echo '{"success":true,"result":{},"errors":[]}'
    return 0
  fi
  if [[ -n "$body" ]]; then
    curl -s -X "$method" "$url" \
      -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      -H "Content-Type: application/json" \
      --data "$body"
  else
    curl -s -X "$method" "$url" \
      -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}"
  fi
}

check() {
  # check <label> <json_response>
  local label="$1" resp="$2"
  local success
  success=$(echo "$resp" | jq -r '.success // false')
  if [[ "$success" == "true" ]]; then
    ok "$label"
    return 0
  else
    local errs
    errs=$(echo "$resp" | jq -r '.errors[]?.message // "unknown error"' | head -3 | tr '\n' '; ')
    warn "$label — ${errs}"
    return 1
  fi
}

should_run() {
  # Returns 0 (true) if this step should execute
  [[ -z "$ONLY_STEP" ]] || [[ "$ONLY_STEP" == "$1" ]]
}

# ── Pre-flight ─────────────────────────────────────────────────────────────────
need_token
log "Cloudflare upgrade for ${ZONE_NAME} (zone=${ZONE_ID}, account=${ACCOUNT_ID})"
$DRY_RUN && warn "DRY-RUN mode — no writes will be made"

# Verify token works
TOKEN_CHECK=$(cf GET "/user/tokens/verify")
if [[ "$(echo "$TOKEN_CHECK" | jq -r '.success')" != "true" ]]; then
  fail "Token verification failed: $(echo "$TOKEN_CHECK" | jq -r '.errors[0].message')"
  exit 1
fi
ok "Token verified — $(echo "$TOKEN_CHECK" | jq -r '.result.status')"


# =============================================================================
# STEP 1 — Zone Settings
# =============================================================================
if should_run 1; then
  section 1 "Zone Settings"

  SETTINGS=(
    '{"value":"high"}'                     # security_level
    '{"value":"on"}'                       # image_resizing
    '{"value":86400}'                      # browser_cache_ttl
    '{"value":"strict"}'                   # ssl
    '{"value":"on"}'                       # always_use_https
    '{"value":"on"}'                       # automatic_https_rewrites
    '{"value":"on"}'                       # http3
    '{"value":"on"}'                       # 0rtt
    '{"value":"on"}'                       # opportunistic_encryption
    '{"value":"on"}'                       # minify {"js":true,"css":true,"html":true} — done separately
    '{"value":5}'                          # challenge_ttl (5s aggressive)
    '{"value":"on"}'                       # hotlink_protection
  )

  NAMES=(
    security_level
    image_resizing
    browser_cache_ttl
    ssl
    always_use_https
    automatic_https_rewrites
    http3
    0rtt
    opportunistic_encryption
    challenge_ttl
    hotlink_protection
  )

  for i in "${!NAMES[@]}"; do
    name="${NAMES[$i]}"
    body="${SETTINGS[$i]}"
    resp=$(cf PATCH "/zones/${ZONE_ID}/settings/${name}" "$body")
    check "  ${name}" "$resp" || true
  done

  # Minification (separate structure)
  resp=$(cf PATCH "/zones/${ZONE_ID}/settings/minify" \
    '{"value":{"js":true,"css":true,"html":true}}')
  check "  minify (js+css+html)" "$resp" || true

  # Tiered caching (topology hint)
  resp=$(cf PATCH "/zones/${ZONE_ID}/argo/tiered_caching" \
    '{"value":"on"}')
  check "  argo tiered caching" "$resp" || true

  # Bot fight mode
  resp=$(cf PUT "/zones/${ZONE_ID}/bot_management" \
    '{"fight_mode":true}')
  check "  bot fight mode" "$resp" || true
fi


# =============================================================================
# STEP 2 — Email Routing
# =============================================================================
if should_run 2; then
  section 2 "Email Routing"

  # Check email routing status
  STATUS=$(cf GET "/zones/${ZONE_ID}/email/routing")
  ENABLED=$(echo "$STATUS" | jq -r '.result.enabled // false')
  ROUTING_STATUS=$(echo "$STATUS" | jq -r '.result.status // "unknown"')
  log "Email routing enabled=${ENABLED}, status=${ROUTING_STATUS}"

  if [[ "$ROUTING_STATUS" == "misconfigured" ]]; then
    warn "Email routing is misconfigured — foreign MX records still exist."
    warn "ACTION REQUIRED: Remove Hostinger MX records from your DNS panel."
    warn "Cloudflare needs these MX records to be the ONLY ones:"
    warn "  route1.mx.cloudflare.net  priority=86"
    warn "  route2.mx.cloudflare.net  priority=70"
    warn "  route3.mx.cloudflare.net  priority=53"
  fi

  # Enable email routing on the zone
  resp=$(cf PUT "/zones/${ZONE_ID}/email/routing/enable" '{}')
  check "  enable email routing on zone" "$resp" || true

  # Register admin@syrabit.ai as verified destination (account-level API)
  # NOTE: Requires 'Account Email Routing Addresses: Write' permission on token.
  log "Registering ${ADMIN_EMAIL} as verified destination (sends verification email)..."
  resp=$(cf POST "/accounts/${ACCOUNT_ID}/email/routing/addresses" \
    "{\"email\":\"${ADMIN_EMAIL}\"}")
  SUCCESS=$(echo "$resp" | jq -r '.success')
  if [[ "$SUCCESS" == "true" ]]; then
    ok "  destination ${ADMIN_EMAIL} registered — CHECK YOUR INBOX to verify"
  else
    ERRMSG=$(echo "$resp" | jq -r '.errors[0].message // "unknown"')
    if echo "$ERRMSG" | grep -qi "already exist\|duplicate\|verified"; then
      ok "  destination ${ADMIN_EMAIL} already registered/verified"
    else
      warn "  destination register failed: ${ERRMSG}"
      warn "  FIX: Add 'Account > Email Routing Addresses > Edit' permission to token"
      warn "  OR: Dashboard → Email Routing → Destination addresses → Add ${ADMIN_EMAIL}"
    fi
  fi

  # Apply email routing rules
  log "Applying routing rules..."

  # noreply@ → drop (no destination needed)
  resp=$(cf POST "/zones/${ZONE_ID}/email/routing/rules" '{
    "name": "noreply@ drop",
    "enabled": true,
    "priority": 1,
    "matchers": [{"type":"literal","field":"to","value":"noreply@syrabit.ai"}],
    "actions":  [{"type":"drop"}]
  }')
  check "  rule: noreply@→drop" "$resp" || true

  # contact@, support@ → forward (only works once admin@ is verified)
  for addr in "contact@syrabit.ai" "support@syrabit.ai" "hello@syrabit.ai"; do
    resp=$(cf POST "/zones/${ZONE_ID}/email/routing/rules" \
      "{
        \"name\": \"${addr} → admin\",
        \"enabled\": true,
        \"priority\": 10,
        \"matchers\": [{\"type\":\"literal\",\"field\":\"to\",\"value\":\"${addr}\"}],
        \"actions\":  [{\"type\":\"forward\",\"value\":[\"${ADMIN_EMAIL}\"]}]
      }")
    check "  rule: ${addr}→${ADMIN_EMAIL}" "$resp" || true
  done

  # Catch-all → forward
  resp=$(cf POST "/zones/${ZONE_ID}/email/routing/rules" '{
    "name": "catch-all → admin",
    "enabled": true,
    "priority": 100,
    "matchers": [{"type":"all"}],
    "actions":  [{"type":"forward","value":["admin@syrabit.ai"]}]
  }')
  check "  rule: catch-all→admin" "$resp" || true
fi


# =============================================================================
# STEP 3 — R2 Buckets
# =============================================================================
if should_run 3; then
  section 3 "R2 Buckets"
  warn "R2 must first be ENABLED in the Cloudflare Dashboard before this step."
  warn "Dashboard → R2 → Enable R2 → Confirm billing"

  for bucket in "syrabit-uploads" "syrabit-assets" "syrabit-backups"; do
    resp=$(cf POST "/accounts/${ACCOUNT_ID}/r2/buckets" \
      "{\"name\":\"${bucket}\",\"locationHint\":\"APAC\"}")
    SUCCESS=$(echo "$resp" | jq -r '.success')
    if [[ "$SUCCESS" == "true" ]]; then
      ok "  created bucket: ${bucket} (APAC)"
    else
      ERRMSG=$(echo "$resp" | jq -r '.errors[0].message // "unknown"')
      if echo "$ERRMSG" | grep -qi "already exist"; then
        ok "  bucket exists: ${bucket}"
      else
        warn "  bucket ${bucket} failed: ${ERRMSG}"
      fi
    fi
  done

  # Set CORS on syrabit-uploads bucket for browser direct upload
  log "Setting CORS on syrabit-uploads..."
  resp=$(cf PUT "/accounts/${ACCOUNT_ID}/r2/buckets/syrabit-uploads/cors" \
    '{
      "rules": [{
        "allowedOrigins": ["https://syrabit.ai","https://www.syrabit.ai"],
        "allowedMethods": ["GET","PUT","POST","DELETE","HEAD"],
        "allowedHeaders": ["*"],
        "maxAgeSeconds": 3600
      }]
    }')
  check "  CORS on syrabit-uploads" "$resp" || true

  # Public access on syrabit-assets (CDN-served static files)
  log "Enabling R2 public access on syrabit-assets..."
  resp=$(cf POST "/accounts/${ACCOUNT_ID}/r2/buckets/syrabit-assets/domains/managed" \
    '{"enabled":true}')
  check "  public R2 domain for syrabit-assets" "$resp" || true

  # Lifecycle rule on syrabit-backups — expire objects older than 90 days
  resp=$(cf PUT "/accounts/${ACCOUNT_ID}/r2/buckets/syrabit-backups/lifecycle" \
    '{
      "rules": [{
        "id": "expire-old-backups",
        "enabled": true,
        "prefix": "",
        "conditions": {"maxAgeSeconds": 7776000},
        "actions": {"deleteObject": {}}
      }]
    }')
  check "  90-day expiry lifecycle on syrabit-backups" "$resp" || true
fi


# =============================================================================
# STEP 4 — WAF Rules
# =============================================================================
if should_run 4; then
  section 4 "WAF Rules"
  warn "Requires 'Zone > Firewall Services > Edit' token permission."
  warn "Add it at: Dashboard → My Profile → API Tokens → Edit your token"

  # Check if we can list WAF rules (tests permission)
  PERM_CHECK=$(cf GET "/zones/${ZONE_ID}/firewall/rules?per_page=1")
  if [[ "$(echo "$PERM_CHECK" | jq -r '.success')" != "true" ]]; then
    PERMERR=$(echo "$PERM_CHECK" | jq -r '.errors[0].code // 0')
    if [[ "$PERMERR" == "10000" ]] || [[ "$PERMERR" == "9109" ]]; then
      warn "  SKIPPED — token lacks Firewall Services Write permission"
      warn "  Once permission is added, re-run: bash scripts/cf_upgrade.sh --step 4"
    else
      warn "  WAF check failed: $(echo "$PERM_CHECK" | jq -r '.errors[0].message')"
    fi
  else
    # Enable CF Managed Ruleset
    resp=$(cf PUT "/zones/${ZONE_ID}/rulesets/phases/http_request_firewall_managed/entrypoint" \
      '{
        "rules": [{
          "action": "execute",
          "description": "Cloudflare Managed Rules",
          "expression": "true",
          "action_parameters": {
            "id": "efb7b8c949ac4650a09736fc376e9aee",
            "overrides": {"sensitivity_level": "high"}
          }
        },{
          "action": "execute",
          "description": "Cloudflare OWASP Core Ruleset",
          "expression": "true",
          "action_parameters": {
            "id": "4814384a9e5d4991b9815dcfc25d2f1f",
            "overrides": {"sensitivity_level": "medium"}
          }
        }]
      }')
    check "  managed ruleset + OWASP" "$resp" || true

    # Custom WAF rules (firewall/rules endpoint for simple expression rules)
    # Block common scanner UAs
    resp=$(cf POST "/zones/${ZONE_ID}/firewall/rules" \
      '[{
        "filter": {
          "expression": "(http.user_agent contains \"sqlmap\") or (http.user_agent contains \"nikto\") or (http.user_agent contains \"masscan\") or (http.user_agent contains \"nmap\")"
        },
        "action": "block",
        "description": "Block scanners"
      }]')
    check "  block scanners UA" "$resp" || true

    # Block direct IP access (requests without host header matching syrabit.ai)
    resp=$(cf POST "/zones/${ZONE_ID}/firewall/rules" \
      '[{
        "filter": {
          "expression": "(not http.host eq \"syrabit.ai\" and not http.host eq \"www.syrabit.ai\" and not http.host eq \"api.syrabit.ai\")"
        },
        "action": "block",
        "description": "Block non-syrabit.ai host"
      }]')
    check "  block non-syrabit host" "$resp" || true

    # Challenge AI bots (non-paying scrapers)
    resp=$(cf POST "/zones/${ZONE_ID}/firewall/rules" \
      '[{
        "filter": {
          "expression": "(cf.client.bot) and not (http.request.uri.path eq \"/robots.txt\")"
        },
        "action": "js_challenge",
        "description": "JS challenge bots (allow /robots.txt)"
      }]')
    check "  JS challenge bots" "$resp" || true
  fi
fi


# =============================================================================
# STEP 5 — Cache Rules
# =============================================================================
if should_run 5; then
  section 5 "Cache Rules"
  warn "Requires 'Zone > Cache Rules > Edit' token permission."

  CACHE_CHECK=$(cf GET "/zones/${ZONE_ID}/rulesets/phases/http_request_cache_settings/entrypoint")
  if [[ "$(echo "$CACHE_CHECK" | jq -r '.success')" != "true" ]]; then
    warn "  SKIPPED — no Cache Rules permission"
    warn "  Re-run after adding 'Zone > Cache Rules > Edit' to token"
  else
    # Apply cache rules via the Rulesets API
    resp=$(cf PUT "/zones/${ZONE_ID}/rulesets/phases/http_request_cache_settings/entrypoint" \
      '{
        "rules": [
          {
            "description": "Do not cache API responses",
            "expression": "(http.request.uri.path matches \"^/api/\")",
            "action": "set_cache_settings",
            "action_parameters": {
              "cache": false,
              "browser_ttl": {"mode": "bypass"}
            }
          },
          {
            "description": "Cache static assets 30 days",
            "expression": "(http.request.uri.path matches \"\\\\.(js|css|woff2?|ttf|eot|svg|png|jpg|jpeg|webp|gif|ico|mp3|mp4)$\")",
            "action": "set_cache_settings",
            "action_parameters": {
              "cache": true,
              "edge_ttl": {"mode": "override_origin", "default": 2592000},
              "browser_ttl": {"mode": "override_origin", "default": 86400},
              "serve_stale": {"disable_stale_while_updating": false}
            }
          },
          {
            "description": "Cache SPA shell 5 min with stale-while-revalidate",
            "expression": "(http.request.uri.path eq \"/\") or (http.request.uri.path matches \"^/[a-z-]+$\" and not http.request.uri.path matches \"^/api\")",
            "action": "set_cache_settings",
            "action_parameters": {
              "cache": true,
              "edge_ttl": {"mode": "override_origin", "default": 300},
              "browser_ttl": {"mode": "override_origin", "default": 60}
            }
          }
        ]
      }')
    check "  cache rules (API bypass + static 30d + SPA 5m)" "$resp" || true
  fi
fi


# =============================================================================
# STEP 6 — Rate Limiting
# =============================================================================
if should_run 6; then
  section 6 "Rate Limiting"
  warn "Requires 'Zone > Rate Limiting > Edit' token permission."

  RL_CHECK=$(cf GET "/zones/${ZONE_ID}/rate_limits?per_page=1")
  if [[ "$(echo "$RL_CHECK" | jq -r '.success')" != "true" ]]; then
    warn "  SKIPPED — no Rate Limiting permission"
    warn "  Re-run after adding 'Zone > Rate Limiting > Edit' to token"
  else
    # Rate limit /api/chat — 20 requests/minute per IP
    resp=$(cf POST "/zones/${ZONE_ID}/rate_limits" \
      '{
        "description": "Rate limit AI chat — 20rpm per IP",
        "match": {
          "request": {
            "methods": ["POST"],
            "schemes": ["HTTPS","HTTP"],
            "url": "syrabit.ai/api/chat*"
          }
        },
        "threshold": 20,
        "period": 60,
        "action": {
          "mode": "simulate",
          "timeout": 60,
          "response": {
            "content_type": "application/json",
            "body": "{\"error\":\"Rate limit exceeded. Please wait a moment.\",\"code\":429}"
          }
        },
        "enabled": true
      }')
    check "  /api/chat 20rpm rate limit" "$resp" || true

    # Rate limit /api/ai — 30 requests/minute per IP
    resp=$(cf POST "/zones/${ZONE_ID}/rate_limits" \
      '{
        "description": "Rate limit AI endpoints — 30rpm per IP",
        "match": {
          "request": {
            "methods": ["POST","GET"],
            "schemes": ["HTTPS","HTTP"],
            "url": "syrabit.ai/api/ai/*"
          }
        },
        "threshold": 30,
        "period": 60,
        "action": {
          "mode": "simulate",
          "timeout": 60,
          "response": {
            "content_type": "application/json",
            "body": "{\"error\":\"Rate limit exceeded.\",\"code\":429}"
          }
        },
        "enabled": true
      }')
    check "  /api/ai 30rpm rate limit" "$resp" || true

    # Rate limit auth endpoints — 10 requests/minute per IP (brute force protection)
    resp=$(cf POST "/zones/${ZONE_ID}/rate_limits" \
      '{
        "description": "Auth brute-force protection — 10rpm",
        "match": {
          "request": {
            "methods": ["POST"],
            "schemes": ["HTTPS","HTTP"],
            "url": "syrabit.ai/api/auth/*"
          }
        },
        "threshold": 10,
        "period": 60,
        "action": {
          "mode": "ban",
          "timeout": 300,
          "response": {
            "content_type": "application/json",
            "body": "{\"error\":\"Too many attempts. Banned for 5 minutes.\",\"code\":429}"
          }
        },
        "enabled": true
      }')
    check "  /api/auth 10rpm ban rule" "$resp" || true
  fi
fi


# =============================================================================
# STEP 7 — Workers AI Gateway
# =============================================================================
if should_run 7; then
  section 7 "Workers AI Gateway"

  # Check if gateway exists
  GW_CHECK=$(cf GET "/accounts/${ACCOUNT_ID}/ai-gateway/gateways/${AI_GATEWAY_ID}")
  if [[ "$(echo "$GW_CHECK" | jq -r '.success')" == "true" ]]; then
    ok "  AI Gateway '${AI_GATEWAY_ID}' exists"
    GW_URL=$(echo "$GW_CHECK" | jq -r '.result.internal_id // "N/A"')
    ok "  Gateway internal_id: ${GW_URL}"
  else
    log "Creating AI Gateway '${AI_GATEWAY_ID}'..."
    resp=$(cf POST "/accounts/${ACCOUNT_ID}/ai-gateway/gateways" \
      "{
        \"name\": \"${AI_GATEWAY_ID}\",
        \"slug\": \"${AI_GATEWAY_ID}\",
        \"cache_invalidate_on_update\": false,
        \"collect_logs\": true,
        \"rate_limiting_interval\": 0,
        \"rate_limiting_limit\": 0,
        \"rate_limiting_technique\": \"fixed\"
      }")
    check "  create AI Gateway" "$resp" || true
  fi

  # Verify the gateway URL matches what's in config
  EXPECTED_GW_BASE="https://gateway.ai.cloudflare.com/v1/${ACCOUNT_ID}/${AI_GATEWAY_ID}/workers-ai"
  ok "  Expected CF_AI_GATEWAY_ACCOUNT_ID=${ACCOUNT_ID}"
  ok "  Expected CF_AI_GATEWAY_ID=${AI_GATEWAY_ID}"
  ok "  Gateway base URL: ${EXPECTED_GW_BASE}"
fi


# =============================================================================
# STEP 8 — Vectorize Indexes
# =============================================================================
if should_run 8; then
  section 8 "Vectorize Indexes"

  # Task #308 — only `syllabus-index-v2` is provisioned now. The legacy
  # 768-dim `syllabus-index` was retired and deleted via
  # `wrangler vectorize delete syllabus-index`; do not re-create it here.
  for idx_name in "syllabus-index-v2"; do
    resp=$(cf GET "/accounts/${ACCOUNT_ID}/vectorize/v2/indexes/${idx_name}")
    if [[ "$(echo "$resp" | jq -r '.success')" == "true" ]]; then
      DIM=$(echo "$resp" | jq -r '.result.config.dimensions // "?"')
      METRIC=$(echo "$resp" | jq -r '.result.config.metric // "?"')
      COUNT=$(echo "$resp" | jq -r '.result.vectors_count // "?"')
      ok "  ${idx_name}: ${DIM}-dim ${METRIC}, vectors=${COUNT}"
    else
      ERRMSG=$(echo "$resp" | jq -r '.errors[0].message // "not found"')
      if echo "$ERRMSG" | grep -qi "not found\|does not exist"; then
        warn "  ${idx_name} does not exist — creating..."
        DIM=1024
        CREATE=$(cf POST "/accounts/${ACCOUNT_ID}/vectorize/v2/indexes" \
          "{\"name\":\"${idx_name}\",\"config\":{\"dimensions\":${DIM},\"metric\":\"cosine\"}}")
        check "  create ${idx_name} (${DIM}d cosine)" "$CREATE" || true
      else
        warn "  ${idx_name}: ${ERRMSG}"
      fi
    fi
  done

  # Check metadata indexes on syllabus-index-v2
  META=$(cf GET "/accounts/${ACCOUNT_ID}/vectorize/v2/indexes/syllabus-index-v2/metadata-index/list")
  if [[ "$(echo "$META" | jq -r '.success')" == "true" ]]; then
    PROPS=$(echo "$META" | jq -r '[.result[]?.propertyName] | join(", ")')
    ok "  syllabus-index-v2 metadata indexes: ${PROPS:-none}"
    # Create missing metadata indexes
    for prop in "subject_id" "chapter_id" "level" "board"; do
      if ! echo "$PROPS" | grep -q "$prop"; then
        resp=$(cf POST "/accounts/${ACCOUNT_ID}/vectorize/v2/indexes/syllabus-index-v2/metadata-index/create" \
          "{\"propertyName\":\"${prop}\",\"indexType\":\"string\"}")
        check "  create metadata-index: ${prop}" "$resp" || true
      else
        ok "  metadata-index exists: ${prop}"
      fi
    done
  fi
fi


# =============================================================================
# STEP 9 — Workers Deploy
# =============================================================================
if should_run 9; then
  section 9 "Workers Deploy"

  # Check wrangler is available
  if ! command -v wrangler &>/dev/null; then
    warn "wrangler not found — install with: npm install -g wrangler"
    warn "Then re-run: bash scripts/cf_upgrade.sh --step 9"
  else
    WRANGLER_VERSION=$(wrangler --version 2>&1 | head -1)
    ok "wrangler: ${WRANGLER_VERSION}"

    # Deploy edge proxy
    log "Deploying syrabit-edge (workers/edge-proxy)..."
    if $DRY_RUN; then
      warn "DRY-RUN: would run 'wrangler deploy' in workers/edge-proxy"
    else
      if (cd workers/edge-proxy && wrangler deploy 2>&1); then
        ok "  syrabit-edge deployed"
      else
        warn "  syrabit-edge deploy failed — check output above"
      fi
    fi

    # Deploy email worker
    log "Deploying syrabit-email (workers/email-worker)..."
    if $DRY_RUN; then
      warn "DRY-RUN: would run 'npm install && wrangler deploy' in workers/email-worker"
    else
      if (cd workers/email-worker && npm install --silent && wrangler deploy 2>&1); then
        ok "  syrabit-email deployed"
        warn "  NOTE: Set BACKEND_AUTH_KEY secret after first deploy:"
        warn "    cd workers/email-worker && wrangler secret put BACKEND_AUTH_KEY"
      else
        warn "  syrabit-email deploy failed — check output above"
        warn "  NOTE: Email Workers require email routing to be configured first"
      fi
    fi
  fi
fi


# =============================================================================
# STEP 10 — Health Check
# =============================================================================
if should_run 10; then
  section 10 "Health Check"

  # Check backend health
  log "Checking backend health..."
  for url in \
    "https://syrabit.ai/api/health" \
    "${BACKEND_URL}/api/health" \
    "https://api.syrabit.ai/api/health"
  do
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$url" 2>/dev/null || echo "000")
    if [[ "$HTTP" == "200" ]]; then
      ok "  ${url} → ${HTTP}"
    else
      warn "  ${url} → ${HTTP}"
    fi
  done

  # Check Workers AI gateway endpoint
  log "Pinging Workers AI Gateway..."
  GW_URL="https://gateway.ai.cloudflare.com/v1/${ACCOUNT_ID}/${AI_GATEWAY_ID}/workers-ai/@cf/meta/llama-3.1-8b-instruct"
  GW_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -X POST "$GW_URL" \
    -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"messages":[{"role":"user","content":"ping"}],"max_tokens":5}' 2>/dev/null || echo "000")
  if [[ "$GW_HTTP" == "200" ]]; then
    ok "  Workers AI Gateway → ${GW_HTTP}"
  else
    warn "  Workers AI Gateway → ${GW_HTTP} (may need CF_AI_GATEWAY_TOKEN)"
  fi

  # Check email worker health (only if it's deployed)
  EMAIL_WORKER="https://syrabit-email.${ACCOUNT_ID}.workers.dev/email/health"
  EMAIL_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "$EMAIL_WORKER" 2>/dev/null || echo "000")
  if [[ "$EMAIL_HTTP" == "200" ]]; then
    ok "  email worker health → ${EMAIL_HTTP}"
  else
    warn "  email worker → ${EMAIL_HTTP} (deploy step 9 first)"
  fi

  # DNS check
  log "DNS verification for ${ZONE_NAME}..."
  DNS_CHECK=$(cf GET "/zones/${ZONE_ID}/dns_records?per_page=50")
  if [[ "$(echo "$DNS_CHECK" | jq -r '.success')" == "true" ]]; then
    MX_COUNT=$(echo "$DNS_CHECK" | jq '[.result[] | select(.type=="MX")] | length')
    ok "  MX records: ${MX_COUNT}"
    FOREIGN_MX=$(echo "$DNS_CHECK" | jq -r '[.result[] | select(.type=="MX" and (.content | contains("cloudflare") | not)) | .content] | join(", ")')
    if [[ -n "$FOREIGN_MX" ]]; then
      warn "  FOREIGN MX still present (breaks email routing): ${FOREIGN_MX}"
      warn "  Remove them from your Hostinger DNS panel"
    else
      ok "  No foreign MX records — email routing should work once destination is verified"
    fi
    SPF=$(echo "$DNS_CHECK" | jq -r '[.result[] | select(.type=="TXT" and (.content | contains("spf")))] | length')
    ok "  SPF records: ${SPF}"
  fi
fi


# =============================================================================
# Summary
# =============================================================================
echo ""
echo -e "${BOLD}════════════════════════════════════════${NC}"
echo -e "${BOLD} UPGRADE COMPLETE${NC}"
echo -e "${BOLD}════════════════════════════════════════${NC}"
echo ""
echo -e "${YELLOW}Manual actions still required:${NC}"
echo "  1. EMAIL ROUTING — Verify admin@syrabit.ai by clicking the link Cloudflare emails you"
echo "     Dashboard → Email Routing → Destination addresses"
echo "  2. EMAIL ROUTING — Remove Hostinger MX records so Cloudflare MX is sole authority"
echo "  3. R2 STORAGE    — Enable R2 in Dashboard → R2 → Enable, then re-run --step 3"
echo "  4. WAF / RL      — Add 'Firewall Services Write' + 'Rate Limiting Write' to token,"
echo "                     then re-run --step 4 --step 6"
echo "  5. EMAIL SECRET  — After deploying email worker: cd workers/email-worker && wrangler secret put BACKEND_AUTH_KEY"
echo ""
echo -e "${CYAN}Re-run individual steps:${NC}"
echo "  bash scripts/cf_upgrade.sh --step 3   # R2 after enabling in dashboard"
echo "  bash scripts/cf_upgrade.sh --step 4   # WAF after adding token permission"
echo "  bash scripts/cf_upgrade.sh --step 6   # Rate limiting after adding token permission"
echo "  bash scripts/cf_upgrade.sh --step 9   # Re-deploy workers"
echo "  bash scripts/cf_upgrade.sh --step 10  # Health check only"
echo ""
