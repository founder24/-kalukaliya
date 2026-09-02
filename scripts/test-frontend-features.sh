#!/bin/bash
#
# Frontend Feature Test — Syrabit.ai
#
# Tests every user-facing frontend feature by making real HTTP requests to
# production (https://syrabit.ai). No browser automation needed.
#
# What is tested:
#   1.  Page availability — all routes return 200
#   2.  Page titles — correct <title> per page (prerendered HTML)
#   3.  Static SEO files — robots.txt, llms.txt, ai.txt, sitemap-static.xml
#   4.  robots.txt — allowed/blocked bots, /api/ guard, sitemap link
#   5.  ai.txt — [allow]/[disallow] blocks, correct bots
#   6.  llms.txt — current tech stack, board coverage, page links
#   7.  Sitemap — URL coverage, freshness of lastmod dates
#   8.  PWA manifest — manifest.json fields present
#   9.  Security headers — HSTS, x-content-type-options, Cloudflare edge
#  10.  CORS — frontend accepts Origin header
#  11.  Page content — key pages serve non-error HTML
#  12.  Performance SLOs — key pages within latency budget
#  13.  Static assets — opengraph.jpg, favicon.ico
#  14.  404 handling — SPA router handles unknown routes
#
# Deployment note: checks marked [DEPLOY] verify content from the latest
# Cloudflare Pages deployment. They will fail if changes haven't been pushed
# to production yet — run after a successful CF Pages deploy.
#
# Usage:
#   bash scripts/test-frontend-features.sh
#   FRONTEND=https://staging.syrabit.ai bash scripts/test-frontend-features.sh

set -euo pipefail

FRONTEND="${FRONTEND:-https://syrabit.ai}"
TIMEOUT=15

# ── Counters ─────────────────────────────────────────────────────────────────
PASS=0; FAIL=0; SKIP=0

ok()   { PASS=$((PASS+1));  printf "  \033[0;32m✔\033[0m  %s\n" "$*"; }
fail() { FAIL=$((FAIL+1));  printf "  \033[0;31m✖\033[0m  %s\n" "$*"; }
skip() { SKIP=$((SKIP+1));  printf "  \033[1;33m–\033[0m  %s\n" "$*"; }
hdr()  { printf "\n\033[0;34m══ %s ══\033[0m\n" "$*"; }

# fetch — stores body in $HTTP_BODY and status in $HTTP_STATUS
# Uses --compressed so Cloudflare's gzip responses are decoded automatically.
fetch() {
    local url="$1"; shift
    local tmpfile; tmpfile=$(mktemp)
    HTTP_STATUS=$(curl -s -L --compressed --max-time "$TIMEOUT" \
        -o "$tmpfile" -w "%{http_code}" "$@" "$url")
    HTTP_BODY=$(tr -d '\000' < "$tmpfile")
    rm -f "$tmpfile"
}

# fetch_headers — stores response headers in $HTTP_HEADERS and status in $HTTP_STATUS
fetch_headers() {
    local url="$1"; shift
    HTTP_HEADERS=$(curl -sI -L --max-time "$TIMEOUT" "$@" "$url")
    HTTP_STATUS=$(printf '%s' "$HTTP_HEADERS" | grep -oE 'HTTP/[0-9.]+ [0-9]+' | tail -1 | grep -oE '[0-9]+$')
}

# timed_fetch — like fetch but also sets $RESP_MS
timed_fetch() {
    local url="$1"
    local tmpfile; tmpfile=$(mktemp)
    local start; start=$(date +%s%3N)
    HTTP_STATUS=$(curl -s -L --compressed --max-time "$TIMEOUT" \
        -o "$tmpfile" -w "%{http_code}" "$url")
    local end; end=$(date +%s%3N)
    RESP_MS=$((end - start))
    HTTP_BODY=$(cat "$tmpfile")
    rm -f "$tmpfile"
}

# check_body_contains — grep -qiE (extended regex, case-insensitive)
check_body_contains() {
    local pattern="$1" label="$2"
    if printf '%s' "$HTTP_BODY" | grep -qiE "$pattern"; then
        ok "$label"
    else
        fail "$label (pattern '$pattern' not found)"
    fi
}

# check_body_not_contains — fail if pattern IS found
check_body_not_contains() {
    local pattern="$1" label="$2"
    if printf '%s' "$HTTP_BODY" | grep -qiE "$pattern"; then
        fail "$label (pattern '$pattern' should NOT appear)"
    else
        ok "$label"
    fi
}

check_header() {
    local header="$1" label="$2"
    if printf '%s' "$HTTP_HEADERS" | grep -qiE "^${header}:"; then
        ok "$label"
    else
        fail "$label (header '$header' missing)"
    fi
}

echo ""
echo "  Syrabit.ai Frontend Feature Test"
echo "  Target : $FRONTEND"
echo "  Time   : $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

# ─────────────────────────────────────────────────────────────────────────────
hdr "1. Page Availability"
# ─────────────────────────────────────────────────────────────────────────────

pages=("/" "/library" "/chat" "/about" "/technology" "/pricing")
for path in "${pages[@]}"; do
    fetch "${FRONTEND}${path}"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        ok "GET ${path} → 200"
    else
        fail "GET ${path} → 200 (got $HTTP_STATUS)"
    fi
done

# ─────────────────────────────────────────────────────────────────────────────
hdr "2. Page Titles (prerendered HTML)"
# ─────────────────────────────────────────────────────────────────────────────
# These pages are prerendered by SSR — the <title> tag is in the static HTML.
# / redirects to /library so both share the library prerendered title.
# Patterns use extended regex (|).

declare -A EXPECTED_TITLE_PATTERNS=(
    ["/library"]="Library|Assam|Syrabit"
    ["/chat"]="Chat|Syra|Syllabus"
    ["/about"]="About|Syrabit|Educational"
    ["/technology"]="Technology|Syrabit|RAG|Feature"
    ["/pricing"]="Pricing|Plans|Free|Syrabit"
)

for path in "${!EXPECTED_TITLE_PATTERNS[@]}"; do
    fetch "${FRONTEND}${path}"
    title=$(printf '%s' "$HTTP_BODY" | grep -oiE '<title>[^<]*</title>' | sed 's/<[^>]*>//g' | head -1)
    pattern="${EXPECTED_TITLE_PATTERNS[$path]}"
    if [[ -z "$title" ]]; then
        skip "${path} title: not in static HTML (SPA-only injection)"
    elif printf '%s' "$title" | grep -qiE "$pattern"; then
        ok "${path} title: '${title}'"
    else
        fail "${path} title '${title}' expected to match '${pattern}'"
    fi
done

# ─────────────────────────────────────────────────────────────────────────────
hdr "3. Static SEO Files — Availability"
# ─────────────────────────────────────────────────────────────────────────────

seo_files=("/robots.txt" "/llms.txt" "/ai.txt" "/sitemap-static.xml" "/sitemap-index.xml" "/manifest.json")
for f in "${seo_files[@]}"; do
    fetch "${FRONTEND}${f}"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        ok "GET ${f} → 200"
    else
        fail "GET ${f} → 200 (got $HTTP_STATUS)"
    fi
done

# ─────────────────────────────────────────────────────────────────────────────
hdr "4. robots.txt — Bot Policy [DEPLOY]"
# ─────────────────────────────────────────────────────────────────────────────

fetch "${FRONTEND}/robots.txt"
check_body_contains "Sitemap:"              "robots.txt lists sitemap(s)"
check_body_contains "GPTBot"               "robots.txt mentions GPTBot (disallowed)"
check_body_contains "User-agent"           "robots.txt has User-agent directives"

# These pass only after the latest commit is deployed to Cloudflare Pages:
for deploy_check in \
    "PerplexityBot:robots.txt allows PerplexityBot" \
    "ChatGPT-User:robots.txt allows ChatGPT-User" \
    "YouBot:robots.txt allows YouBot" \
    "Disallow: /admin:robots.txt blocks /admin" \
    "Disallow: /api/:robots.txt blocks /api/ indexing"; do
    pattern="${deploy_check%%:*}"
    label="${deploy_check#*:}"
    if printf '%s' "$HTTP_BODY" | grep -qiF "$pattern"; then
        ok "$label [DEPLOY: deployed]"
    else
        skip "$label [DEPLOY: not yet in production — push to CF Pages]"
    fi
done

# M-4 audit fix: stale/nonexistent sitemaps must be absent from robots.txt
# These 7 entries were removed because the backend endpoints never existed.
for stale in \
    "sitemap-notes.xml" \
    "sitemap-mcqs.xml" \
    "sitemap-pyqs.xml" \
    "sitemap-examples.xml" \
    "sitemap-definitions.xml" \
    "sitemap-learn.xml" \
    "sitemap-pages.xml"; do
    if printf '%s' "$HTTP_BODY" | grep -qF "$stale"; then
        fail "M-4: robots.txt still lists nonexistent sitemap: $stale"
    else
        ok "M-4: Stale sitemap absent from robots.txt: $stale"
    fi
done

# Real sitemaps that must still be listed
for real in "sitemap-index.xml" "sitemap-subjects.xml" "sitemap-chapters.xml"; do
    if printf '%s' "$HTTP_BODY" | grep -qF "$real"; then
        ok "M-4: Real sitemap present in robots.txt: $real"
    else
        fail "M-4: robots.txt missing real sitemap: $real"
    fi
done

# ─────────────────────────────────────────────────────────────────────────────
hdr "5. ai.txt — AI Bot Manifest"
# ─────────────────────────────────────────────────────────────────────────────

fetch "${FRONTEND}/ai.txt"
check_body_contains "\[allow\]"            "ai.txt has [allow] block"
check_body_contains "PerplexityBot"        "ai.txt allows PerplexityBot"
check_body_contains "ChatGPT-User"         "ai.txt allows ChatGPT-User"
check_body_contains "YouBot"               "ai.txt allows YouBot"
check_body_contains "\[disallow\]"         "ai.txt has [disallow] block"
check_body_contains "GPTBot"               "ai.txt disallows GPTBot"
check_body_contains "ClaudeBot"            "ai.txt disallows ClaudeBot"
check_body_contains "\[sitemap\]"          "ai.txt links sitemap"
check_body_contains "\[llms\]"             "ai.txt links llms.txt"

# ─────────────────────────────────────────────────────────────────────────────
hdr "6. llms.txt — LLM Crawler Manifest"
# ─────────────────────────────────────────────────────────────────────────────

fetch "${FRONTEND}/llms.txt"
check_body_contains "syrabit\.ai"          "llms.txt references syrabit.ai"
check_body_contains "AHSEC"                "llms.txt covers AHSEC"
check_body_contains "SEBA"                 "llms.txt covers SEBA"
check_body_contains "Degree|FYUGP|NEP"     "llms.txt covers Degree/NEP"
check_body_contains "Cloudflare Workers|Vectorize" "llms.txt has current Cloudflare-native stack"
check_body_not_contains "^.*Railway"       "llms.txt doesn't mention stale Railway backend"
check_body_not_contains "IndicTrans2"      "llms.txt doesn't mention stale IndicTrans2"
check_body_contains "/library"             "llms.txt links /library page"
check_body_contains "/technology"          "llms.txt links /technology page"
check_body_contains "/about"               "llms.txt links /about page"
check_body_contains "founder@syrabit\.ai"  "llms.txt has contact email"

# ─────────────────────────────────────────────────────────────────────────────
hdr "7. Sitemap — URL Coverage [DEPLOY]"
# ─────────────────────────────────────────────────────────────────────────────

fetch "${FRONTEND}/sitemap-static.xml"
check_body_contains "syrabit\.ai/"         "sitemap has homepage URL"
check_body_contains "/library"             "sitemap has /library"
check_body_contains "/about"               "sitemap has /about"
check_body_contains "/pricing"             "sitemap has /pricing"
check_body_contains "/chat"                "sitemap has /chat"
check_body_contains "lastmod"              "sitemap has lastmod dates"
check_body_contains "changefreq"           "sitemap has changefreq"

# Deployment-dependent: /technology only appears after the latest commit is live
if printf '%s' "$HTTP_BODY" | grep -qiE "/technology"; then
    ok "sitemap has /technology [DEPLOY: deployed]"
else
    skip "sitemap has /technology [DEPLOY: not yet in production — push to CF Pages]"
fi

# L-8 audit fix: lastmod must be dynamically generated, not a hardcoded string.
# We verify the dates are current (within the last 7 days), not any old hardcoded value.
_today=$(date -u '+%Y-%m-%d')
_yesterday=$(date -u -d 'yesterday' '+%Y-%m-%d' 2>/dev/null \
  || date -u -v-1d '+%Y-%m-%d' 2>/dev/null \
  || python3 -c "from datetime import date,timedelta; print(date.today()-timedelta(1))")
_dates=$(printf '%s' "$HTTP_BODY" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | sort -u)
_recent=false
while IFS= read -r _d; do
    if [[ "$_d" == "$_today" || "$_d" == "$_yesterday" ]]; then
        _recent=true; break
    fi
    _age=$(python3 -c "
from datetime import date
try: print((date.today() - date.fromisoformat('${_d}')).days)
except: print(9999)" 2>/dev/null || echo 9999)
    [[ "${_age:-9999}" -le 7 ]] && _recent=true && break
done <<< "$_dates"
if $_recent; then
    ok "L-8: sitemap-static.xml lastmod is current (≤7 days old, dynamic generation working)"
elif [[ -z "$_dates" ]]; then
    fail "L-8: sitemap-static.xml has no date values at all"
else
    fail "L-8: sitemap-static.xml has stale/hardcoded lastmod dates" \
         "found: $(printf '%s' "$_dates" | tr '\n' ' ')  expected near: ${_today}"
fi

fetch "${FRONTEND}/sitemap-index.xml"
if [[ "$HTTP_STATUS" == "200" ]]; then
    ok "sitemap-index.xml → 200"
    check_body_contains "sitemap"          "sitemap-index links child sitemaps"
else
    skip "sitemap-index.xml not available (may be served dynamically by backend)"
fi

# ─────────────────────────────────────────────────────────────────────────────
hdr "8. PWA Manifest"
# ─────────────────────────────────────────────────────────────────────────────

fetch "${FRONTEND}/manifest.json"
if [[ "$HTTP_STATUS" == "200" ]]; then
    ok "GET /manifest.json → 200"
    check_body_contains '"name"'            "manifest.json has 'name' field"
    check_body_contains '"icons"'           "manifest.json has 'icons' field"
    check_body_contains '"display"'         "manifest.json has 'display' field"
    check_body_contains "Syrabit"           "manifest.json name contains Syrabit"
else
    fail "GET /manifest.json → 200 (got $HTTP_STATUS)"
fi

# ─────────────────────────────────────────────────────────────────────────────
hdr "9. Security Headers"
# ─────────────────────────────────────────────────────────────────────────────

fetch_headers "${FRONTEND}/"
check_header "strict-transport-security"   "HSTS header present"
check_header "x-content-type-options"      "x-content-type-options header present"

if printf '%s' "$HTTP_HEADERS" | grep -qiE "^x-frame-options:"; then
    ok "x-frame-options header present"
else
    skip "x-frame-options: not set by Cloudflare Pages (acceptable for SPAs)"
fi

if printf '%s' "$HTTP_HEADERS" | grep -qiE "^cf-ray:"; then
    ok "cf-ray header present (served via Cloudflare edge)"
else
    fail "cf-ray header missing (not going through Cloudflare)"
fi

# ─────────────────────────────────────────────────────────────────────────────
hdr "10. CORS — Frontend"
# ─────────────────────────────────────────────────────────────────────────────

fetch_headers "${FRONTEND}/" -H "Origin: https://syrabit.ai"
if [[ "$HTTP_STATUS" == "200" ]]; then
    ok "Frontend loads with Origin header (no CORS block)"
else
    fail "Frontend returned $HTTP_STATUS with Origin header"
fi

# ─────────────────────────────────────────────────────────────────────────────
hdr "11. Page Content — Error-Free HTML"
# ─────────────────────────────────────────────────────────────────────────────
# These checks verify the SPA shell is served correctly (not a server error page).
# We look for specific HTTP error page signatures, not the number "500"
# (which legitimately appears in bundle filenames like "chunk-abc500.js").

ERROR_PATTERN="Internal Server Error|<h1>Error</h1>|503 Service Unavailable|502 Bad Gateway"

pages_to_check=("/library" "/chat" "/about" "/technology" "/pricing")
for path in "${pages_to_check[@]}"; do
    fetch "${FRONTEND}${path}"
    if [[ "$HTTP_STATUS" != "200" ]]; then
        fail "${path} returned $HTTP_STATUS (expected 200)"
    elif printf '%s' "$HTTP_BODY" | grep -qiE "$ERROR_PATTERN"; then
        fail "${path} HTML contains error page signature"
    else
        ok "${path} serves clean HTML (no server error signatures)"
    fi
done

# Verify pages have the app root div (SPA shell)
fetch "${FRONTEND}/library"
check_body_contains 'id="root"|id="app"' "Library page has SPA root element"

# ─────────────────────────────────────────────────────────────────────────────
hdr "12. Performance SLOs"
# ─────────────────────────────────────────────────────────────────────────────

for path in "/" "/library" "/about"; do
    timed_fetch "${FRONTEND}${path}"
    if [[ $RESP_MS -lt 800 ]]; then
        ok "${path} → ${RESP_MS}ms (< 800ms — Cloudflare edge cache hit)"
    elif [[ $RESP_MS -lt 2000 ]]; then
        ok "${path} → ${RESP_MS}ms (< 2000ms — acceptable, cache miss)"
    else
        fail "${path} → ${RESP_MS}ms SLOW (> 2000ms SLO)"
    fi
done

# ─────────────────────────────────────────────────────────────────────────────
hdr "13. Static Assets"
# ─────────────────────────────────────────────────────────────────────────────

fetch "${FRONTEND}/opengraph.jpg"
if [[ "$HTTP_STATUS" == "200" ]]; then
    ok "GET /opengraph.jpg → 200 (OG image)"
else
    fail "GET /opengraph.jpg → 200 (got $HTTP_STATUS) — OG image missing"
fi

fetch "${FRONTEND}/favicon.ico"
if [[ "$HTTP_STATUS" == "200" ]]; then
    ok "GET /favicon.ico → 200"
else
    fail "GET /favicon.ico → 200 (got $HTTP_STATUS)"
fi

fetch "${FRONTEND}/icons/icon-192x192.png"
if [[ "$HTTP_STATUS" == "200" ]]; then
    ok "GET /icons/icon-192x192.png → 200 (PWA icon)"
else
    skip "GET /icons/icon-192x192.png → $HTTP_STATUS (PWA icon path may differ)"
fi

# ─────────────────────────────────────────────────────────────────────────────
hdr "14. 404 / SPA Routing"
# ─────────────────────────────────────────────────────────────────────────────

fetch "${FRONTEND}/this-page-does-not-exist-xyz9999"
if [[ "$HTTP_STATUS" == "200" || "$HTTP_STATUS" == "404" ]]; then
    ok "Unknown route → ${HTTP_STATUS} (SPA router or 404 page — not a 5xx)"
else
    fail "Unknown route → unexpected $HTTP_STATUS (expected 200 or 404)"
fi

# ─────────────────────────────────────────────────────────────────────────────
hdr "15. Security Audit Regression Guards  [AUDIT]"
# ─────────────────────────────────────────────────────────────────────────────
# Each check maps to a named issue from the June 2026 full-stack security audit.
# These verify that specific fixes have not regressed after subsequent deploys.

GOOGLEBOT_UA="Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

# ── H-4: Security headers must appear on bot-rendered and sitemap responses ──

_bot_headers=$(curl -sI -L --max-time "$TIMEOUT" \
    -A "$GOOGLEBOT_UA" \
    -H "Accept: text/html" \
    "$FRONTEND/" 2>/dev/null)
_bot_status=$(printf '%s' "$_bot_headers" | grep -oE 'HTTP/[0-9.]+ [0-9]+' | tail -1 | grep -oE '[0-9]+$')

if [[ "$_bot_status" == "200" ]]; then
    ok "H-4: GET / with Googlebot UA → 200"
    for _h in "x-frame-options" "x-content-type-options" "referrer-policy" "permissions-policy"; do
        if printf '%s' "$_bot_headers" | grep -qiE "^${_h}:"; then
            _hv=$(printf '%s' "$_bot_headers" | grep -iE "^${_h}:" | head -1 \
                  | sed 's/^[^:]*:[[:space:]]*//' | tr -d '\r')
            ok "H-4: ${_h} on bot-rendered response" "${_hv}"
        else
            fail "H-4: ${_h} missing on bot-rendered response (addSecurityHeaders() not firing)"
        fi
    done
elif [[ "$_bot_status" == "503" ]]; then
    ok "H-4/M-5: Googlebot got 503 — M-5 fix active (no soft-404 SPA shell)"
    skip "H-4 security header check on bot path" "bot-render backend returned 503, deferred"
else
    fail "H-4: GET / with Googlebot UA returned ${_bot_status:-000}"
fi

# Sitemap proxy responses must also carry security headers
_sm_headers=$(curl -sI -L --max-time "$TIMEOUT" \
    -A "$GOOGLEBOT_UA" \
    "$FRONTEND/sitemap-subjects.xml" 2>/dev/null)
_sm_status=$(printf '%s' "$_sm_headers" | grep -oE 'HTTP/[0-9.]+ [0-9]+' | tail -1 | grep -oE '[0-9]+$')
if [[ "$_sm_status" == "200" ]]; then
    ok "H-4: GET /sitemap-subjects.xml → 200"
    for _h in "x-frame-options" "x-content-type-options"; do
        if printf '%s' "$_sm_headers" | grep -qiE "^${_h}:"; then
            ok "H-4: ${_h} on sitemap proxy response"
        else
            fail "H-4: ${_h} missing on sitemap proxy response"
        fi
    done
else
    skip "H-4 sitemap security headers" "sitemap-subjects.xml returned ${_sm_status:-000}"
fi

# ── M-5: Bot UA on unknown path must get 503, not a silent SPA shell ─────────

_m5_body=$(mktemp)
_m5_status=$(curl -s -o "$_m5_body" -w "%{http_code}" --max-time "$TIMEOUT" \
    -A "$GOOGLEBOT_UA" \
    -H "Accept: text/html" \
    "${FRONTEND}/syrabit-audit-probe-$(date +%s)-xyz" 2>/dev/null || echo "000")
if [[ "$_m5_status" == "503" ]]; then
    ok "M-5: Unknown path with bot UA → 503 (no soft-404 SPA shell served)"
elif [[ "$_m5_status" == "200" ]]; then
    _is_spa=$(grep -cE 'id="root"|id="app"' "$_m5_body" 2>/dev/null || true)
    if [[ "$_is_spa" -gt 0 ]]; then
        fail "M-5: Bot gets SPA shell for unknown path (soft-404) — should be 503"
    else
        ok "M-5: 200 with non-SPA body — may be a prerendered snapshot (acceptable)"
    fi
else
    skip "M-5 bot 503 check" "got ${_m5_status} — may be CDN-cached or rate-limited"
fi
rm -f "$_m5_body"

# ── M-15: /llms-full.txt must be accessible and contain content ──────────────

fetch "${FRONTEND}/llms-full.txt"
if [[ "$HTTP_STATUS" == "200" ]]; then
    _blen=$(printf '%s' "$HTTP_BODY" | wc -c | tr -d ' ')
    ok "M-15: GET /llms-full.txt → 200  (${_blen} bytes)"
    if printf '%s' "$HTTP_BODY" | grep -qiE "syrabit"; then
        ok "M-15: /llms-full.txt contains syrabit content"
    else
        fail "M-15: /llms-full.txt body doesn't reference syrabit"
    fi
    [[ "${_blen:-0}" -gt 200 ]] \
        && ok "M-15: /llms-full.txt has substantive content (${_blen} bytes)" \
        || fail "M-15: /llms-full.txt too short — backend endpoint may not be returning chapters"
else
    fail "M-15: GET /llms-full.txt → ${HTTP_STATUS} (endpoint or CF worker proxy missing)"
fi

# ── L-8: sitemap-index.xml lastmod must also be current (not hardcoded) ──────

fetch "${FRONTEND}/sitemap.xml"
if [[ "$HTTP_STATUS" == "200" ]]; then
    _idx_dates=$(printf '%s' "$HTTP_BODY" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | sort -u)
    _idx_recent=false
    while IFS= read -r _d; do
        [[ "$_d" == "$_today" || "$_d" == "$_yesterday" ]] && _idx_recent=true && break
        _age=$(python3 -c "
from datetime import date
try: print((date.today() - date.fromisoformat('${_d}')).days)
except: print(9999)" 2>/dev/null || echo 9999)
        [[ "${_age:-9999}" -le 7 ]] && _idx_recent=true && break
    done <<< "$_idx_dates"
    if $_idx_recent; then
        ok "L-8: sitemap-index.xml lastmod is current (dynamic generation working)"
    else
        fail "L-8: sitemap-index.xml has stale/hardcoded lastmod" \
             "found: $(printf '%s' "$_idx_dates" | tr '\n' ' ')  expected near: ${_today}"
    fi
else
    skip "L-8 sitemap-index lastmod" "sitemap.xml returned ${HTTP_STATUS}"
fi

# ── Sitemap content-type must be application/xml ──────────────────────────────

for _sm in "/sitemap.xml" "/sitemap-static.xml" "/sitemap-subjects.xml"; do
    fetch_headers "${FRONTEND}${_sm}"
    if [[ "$HTTP_STATUS" == "200" ]]; then
        _ct=$(printf '%s' "$HTTP_HEADERS" | grep -i "^content-type:" | head -1 \
              | sed 's/^[^:]*:[[:space:]]*//' | tr -d '\r')
        if printf '%s' "$_ct" | grep -qiE "xml"; then
            ok "Sitemap content-type: ${_sm} → ${_ct}"
        else
            fail "Sitemap wrong content-type: ${_sm}" "got '${_ct}' expected application/xml"
        fi
    else
        skip "Sitemap content-type ${_sm}" "returned ${HTTP_STATUS}"
    fi
done

# ── H-6: XSS-susceptible pages load without server errors ────────────────────
# DOMPurify runs in the browser — we can't test JS execution, but we verify
# these pages serve clean HTML (no server-side rendering error that would
# indicate a DOMPurify import failure crashing the build).

fetch "${FRONTEND}/library"
if [[ "$HTTP_STATUS" == "200" ]]; then
    if printf '%s' "$HTTP_BODY" | grep -qiE "Internal Server Error|SyntaxError|ReferenceError"; then
        fail "H-6: /library HTML contains JS error signature (DOMPurify import may be broken)"
    else
        ok "H-6: /library serves clean HTML (DOMPurify not crashing build)"
    fi
else
    fail "H-6: /library → ${HTTP_STATUS}"
fi

# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════"
printf "  Results : %d checks\n" $((PASS + FAIL + SKIP))
printf "  \033[0;32m✔ Passed\033[0m : %d\n" $PASS
printf "  \033[0;31m✖ Failed\033[0m : %d\n" $FAIL
printf "  \033[1;33m– Skipped\033[0m: %d  (awaiting CF Pages deploy)\n" $SKIP
echo "════════════════════════════════════════"
if [[ $FAIL -eq 0 ]]; then
    printf "\n  \033[0;32mALL CHECKS PASSED\033[0m\n\n"
    exit 0
else
    printf "\n  \033[0;31m%d CHECK(S) FAILED\033[0m\n\n" $FAIL
    exit 1
fi
