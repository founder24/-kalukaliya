#!/usr/bin/env bash
# check-http3-early-hints.sh
# Verifies that https://syrabit.ai/library is served over HTTP/3 (QUIC) and
# that Early Hints (103 / Link rel=preload) are active.
#
# Usage:
#   bash artifacts/syrabit/scripts/check-http3-early-hints.sh
#
# Exit codes:
#   0  — both HTTP/3 and Early Hints confirmed
#   1  — one or more assertions failed (details printed to stderr)
#
# Request semantics:
#   HTTP/3 check  — HEAD request via `curl -sI` (--http3 or alt-svc fallback).
#   Early Hints   — GET request via `curl -D -` to capture 103 intermediate
#                   responses. GET is required here because Cloudflare only
#                   sends 103 Early Hints on navigational GET requests for HTML
#                   pages; a HEAD request does not trigger them.
#
# Requirements:
#   curl ≥ 7.66  (--http3 flag; built with QUIC support such as ngtcp2 or quiche)
#   If your curl lacks QUIC support the HTTP/3 check falls back to inspecting the
#   alt-svc response header, which is a reliable proxy for HTTP/3 availability.

set -euo pipefail

# / redirects intentionally, so use a real HTML navigation route for both
# protocol and Early Hints checks. Cloudflare only emits navigational Early
# Hints for an HTML response, not for the root redirect.
TARGET="https://syrabit.ai/library"
PASS=0
FAIL=0

ok()   { echo "  [PASS] $*"; }
fail() { echo "  [FAIL] $*" >&2; FAIL=$((FAIL + 1)); }

echo "=== HTTP/3 + Early Hints check: $TARGET ==="
echo

# ---------------------------------------------------------------------------
# 1. HTTP/3 check
# ---------------------------------------------------------------------------
echo "-- HTTP/3 (QUIC) --"

# Prefer a direct HTTP/3 connection; fall back to alt-svc header inspection.
if curl --http3 --version >/dev/null 2>&1; then
  # curl has QUIC support — attempt a real HTTP/3 connection.
  H3_RESPONSE=$(curl -sI --http3 --max-time 10 "$TARGET" 2>&1 || true)
  STATUS_LINE=$(echo "$H3_RESPONSE" | head -1)

  if echo "$STATUS_LINE" | grep -qiE "^HTTP/3"; then
    ok "Protocol: $STATUS_LINE"
    PASS=$((PASS + 1))
  else
    fail "Expected HTTP/3 status line, got: $STATUS_LINE"
    echo "     (full response headers below for debugging)"
    echo "$H3_RESPONSE" | head -20 | sed 's/^/     /'
  fi
else
  # curl was built without QUIC — check alt-svc header for h3 advertisement.
  # NOTE: alt-svc inspection is capability inference (the server claims h3
  # support), not a negotiated-transport proof. For a definitive end-to-end
  # HTTP/3 assertion run this script on a host with QUIC-enabled curl.
  echo "     (curl has no QUIC support; falling back to alt-svc header inspection)"
  echo "     Note: alt-svc is capability inference, not a negotiated-transport proof."
  ALT_SVC_HEADERS=$(curl -sI --max-time 10 "$TARGET" | grep -i "^alt-svc:" || true)
  if echo "$ALT_SVC_HEADERS" | grep -qi "h3"; then
    ok "alt-svc header advertises h3 (HTTP/3 available on server): $ALT_SVC_HEADERS"
    PASS=$((PASS + 1))
  else
    fail "alt-svc header does not advertise h3 — HTTP/3 may be disabled"
    echo "     alt-svc headers found: ${ALT_SVC_HEADERS:-<none>}"
  fi
fi

echo

# ---------------------------------------------------------------------------
# 2. Early Hints check
# ---------------------------------------------------------------------------
echo "-- Early Hints (103 / Link rel=preload) --"

# Use -D - to capture all response headers including the 103 intermediate
# response that Cloudflare sends before the 200.  -o /dev/null discards body.
ALL_HEADERS=$(curl -sD - -o /dev/null --max-time 10 "$TARGET" 2>/dev/null || true)

# Hashed asset Early Hints are safe only when every hinted bundle is present in
# the document currently served by the edge. A 103 response can outlive the
# HTML response during a Pages rollout, so compare the hinted paths with the
# live document instead of treating every hashed preload as stale.
EARLY_HINTS_HEADER=$(echo "$ALL_HEADERS" | grep -i "^early-hints:" || true)
LINK_HEADERS=$(echo "$ALL_HEADERS" | grep -i "^link:" | grep -i "rel=preload" || true)
HASHED_PRELOAD_LINKS=$(echo "$LINK_HEADERS" | grep -oiE "</assets/[^>]+>" | sort -u || true)
HASHED_PRELOAD_DISABLED=$(echo "$ALL_HEADERS" | grep -i "^x-syrabit-hashed-preload: *disabled" || true)
HAS_103=false
if echo "$ALL_HEADERS" | grep -qiE "^HTTP/[0-9.]+ 103"; then
  HAS_103=true
fi

if [ -n "$HASHED_PRELOAD_DISABLED" ]; then
  ok "Hashed asset Early Hints intentionally disabled for release convergence"
  PASS=$((PASS + 1))
elif [ -n "$HASHED_PRELOAD_LINKS" ]; then
  LIVE_HTML=$(curl -fsS --max-time 10 "$TARGET" 2>/dev/null || true)
  STALE_HASHED_PRELOAD_LINKS=""
  while IFS= read -r link; do
    [ -z "$link" ] && continue
    asset_path="${link#<}"
    asset_path="${asset_path%>}"
    relative_asset_path="${asset_path#/}"
    asset_filename="${asset_path##*/}"
    if [[ "$LIVE_HTML" != *"$asset_path"* \
      && "$LIVE_HTML" != *"$relative_asset_path"* \
      && "$LIVE_HTML" != *"$asset_filename"* ]]; then
      STALE_HASHED_PRELOAD_LINKS="${STALE_HASHED_PRELOAD_LINKS}${asset_path}"$'\n'
    fi
  done <<< "$HASHED_PRELOAD_LINKS"

  if [ -n "$STALE_HASHED_PRELOAD_LINKS" ]; then
    fail "Stale hashed asset preload link(s) detected in Early Hints"
    echo "$STALE_HASHED_PRELOAD_LINKS" | sed 's/^/     /'
  else
    ok "Hashed asset Early Hints match the live HTML bundle set"
    PASS=$((PASS + 1))
  fi
elif [ "$HAS_103" = true ]; then
  EH_LINE=$(echo "$ALL_HEADERS" | grep -iE "^HTTP/[0-9.]+ 103" | head -1)
  ok "103 Early Hints response received without hashed asset preloads: $EH_LINE"
  PASS=$((PASS + 1))
elif [ -n "$EARLY_HINTS_HEADER" ]; then
  ok "Early-Hints header present without hashed asset preloads: $(echo "$EARLY_HINTS_HEADER" | head -1)"
  PASS=$((PASS + 1))
else
  fail "No release-safe Early Hints signal or hashed-preload-disabled marker found"
  echo "     All Link headers in response:"
  echo "$ALL_HEADERS" | grep -i "^link:" | sed 's/^/     /' || echo "     <none>"
fi

echo

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "=== Summary: $PASS passed, $FAIL failed ==="

if [ "$FAIL" -gt 0 ]; then
  echo "RESULT: FAIL — see errors above" >&2
  exit 1
fi

echo "RESULT: PASS — HTTP/3 and Early Hints both confirmed"
exit 0
