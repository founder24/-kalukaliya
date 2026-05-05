#!/usr/bin/env bash
# Reproducible Cloudflare-side setup for the embed-staging worker.
#
# Mirrors the production-side resources created in Task #400 onto the
# staging hostname so backend probes against embed-staging.syrabit.ai
# behave identically (no SBFM challenge, DNS resolves, route binds):
#
#   1. Proxied AAAA record for `embed-staging.syrabit.ai` → 100::
#      (the documented "discard" address; Worker route intercepts
#      the request before it would reach an origin).
#   2. WAF custom rule "Skip SBFM for embed-staging worker" matching
#      `http.host eq "embed-staging.syrabit.ai"` with action=skip and
#      `phases=["http_ratelimit","http_request_sbfm"]` plus
#      `products=["bic","hot","rateLimit","securityLevel","uaBlock",
#      "waf","zones"]` — same shape as the production rule.
#
# Idempotent: running twice is a no-op (the AAAA upsert is keyed by
# `name`, and the WAF rule is matched by description before insert).
#
# Required env:
#   CF_API_TOKEN — token with Zone:Read, DNS:Edit, Zone WAF:Edit
#   CF_ZONE_ID   — zone id for syrabit.ai
#
# Usage: scripts/setup-staging-cloudflare.sh

set -euo pipefail

: "${CF_API_TOKEN:?CF_API_TOKEN is required}"
: "${CF_ZONE_ID:?CF_ZONE_ID is required}"

NAME="embed-staging.syrabit.ai"
RULE_DESC="Skip SBFM for embed-staging worker"
API="https://api.cloudflare.com/client/v4"
AUTH=(-H "Authorization: Bearer $CF_API_TOKEN" -H "Content-Type: application/json")

echo "[1/2] Ensuring proxied AAAA $NAME → 100::"
existing="$(curl -fsS "${AUTH[@]}" \
  "$API/zones/$CF_ZONE_ID/dns_records?type=AAAA&name=$NAME" \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["result"][0]["id"] if d["result"] else "")')"
payload='{"type":"AAAA","name":"embed-staging","content":"100::","ttl":1,"proxied":true,"comment":"Embed-worker staging route placeholder (Task #413)"}'
if [[ -n "$existing" ]]; then
  curl -fsS -X PUT "${AUTH[@]}" \
    "$API/zones/$CF_ZONE_ID/dns_records/$existing" -d "$payload" >/dev/null
  echo "    updated existing record id=$existing"
else
  curl -fsS -X POST "${AUTH[@]}" \
    "$API/zones/$CF_ZONE_ID/dns_records" -d "$payload" >/dev/null
  echo "    created new record"
fi

echo "[2/2] Ensuring WAF custom rule '$RULE_DESC'"
RULESET_ID="$(curl -fsS "${AUTH[@]}" \
  "$API/zones/$CF_ZONE_ID/rulesets/phases/http_request_firewall_custom/entrypoint" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["id"])')"
existing_rule="$(curl -fsS "${AUTH[@]}" \
  "$API/zones/$CF_ZONE_ID/rulesets/$RULESET_ID" \
  | python3 -c "import json,sys;rs=json.load(sys.stdin)['result'].get('rules',[]) or [];print(next((r['id'] for r in rs if r.get('description')=='$RULE_DESC'),''))")"
rule_payload=$(cat <<JSON
{
  "description": "$RULE_DESC",
  "expression": "(http.host eq \"$NAME\")",
  "action": "skip",
  "action_parameters": {
    "ruleset": "current",
    "phases": ["http_ratelimit", "http_request_sbfm"],
    "products": ["bic", "hot", "rateLimit", "securityLevel", "uaBlock", "waf", "zones"]
  },
  "enabled": true
}
JSON
)
if [[ -n "$existing_rule" ]]; then
  curl -fsS -X PATCH "${AUTH[@]}" \
    "$API/zones/$CF_ZONE_ID/rulesets/$RULESET_ID/rules/$existing_rule" \
    -d "$rule_payload" >/dev/null
  echo "    updated existing rule id=$existing_rule"
else
  curl -fsS -X POST "${AUTH[@]}" \
    "$API/zones/$CF_ZONE_ID/rulesets/$RULESET_ID/rules" \
    -d "$rule_payload" >/dev/null
  echo "    created new rule"
fi

echo
echo "=== Cloudflare staging parity OK — proceed with: pnpm run deploy:staging ==="
