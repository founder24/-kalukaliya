# Cloudflare Access break-glass runbook

Cloudflare Access normally protects `https://syrabit.ai/staff*`,
`https://api.syrabit.ai/api/v1/admin*`, and the legacy
`https://api.syrabit.ai/admin*` path with one `Syrabit Admin` self-hosted
application, an eight-hour session, and an email allowlist. Cloudflare One-Time
PIN is the default identity provider.

The separate, more-specific `Syrabit Admin Cron API` application bypasses
Access only for `/api/v1/admin/cron*`; those routes retain their application
bearer-token checks so scheduled automation does not depend on an interactive
OTP session.

Use this procedure only when an approved staff member cannot complete the
normal Access login and urgent staff work cannot wait for the identity incident
to be repaired. The application still has its own staff authentication; this
procedure does not replace or disable that authentication.

## Safety rules

- Never create an `Everyone` bypass for an emergency operator policy.
- Restrict the bypass to one known operator egress IP (`/32` for IPv4 or `/128`
  for IPv6).
- Start a 15-minute cleanup timer before creating the policy.
- Keep a second terminal ready to delete the policy.
- Confirm a request from a different network still receives the Cloudflare
  Access login redirect.
- Delete the policy immediately after the emergency action.

## API procedure

The operator needs a token with `Access: Apps and Policies Edit`. Keep the
token in `CLOUDFLARE_API_TOKEN`; never place it directly in shell history.

```bash
ACCOUNT_ID=d66e40eac539fff1db270fddf384a5ec
API=https://api.cloudflare.com/client/v4
OPERATOR_CIDR=203.0.113.10/32 # replace with the operator's verified public IP

APP_ID="$(
  curl -fsS -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
    "$API/accounts/$ACCOUNT_ID/access/apps" |
  jq -r '.result[] | select(.name == "Syrabit Admin") | .id'
)"

POLICY_ID="$(
  jq -n --arg cidr "$OPERATOR_CIDR" '{
    name: "EMERGENCY break glass - remove within 15 minutes",
    decision: "bypass",
    precedence: 99,
    include: [{ip: {ip: $cidr}}]
  }' |
  curl -fsS -X POST \
    -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
    -H "Content-Type: application/json" \
    --data @- \
    "$API/accounts/$ACCOUNT_ID/access/apps/$APP_ID/policies" |
  jq -r '.result.id'
)"
```

From the matching operator network, confirm `/staff/` reaches the application's
own login. From a different network, confirm an unauthenticated request still
returns a `302` Cloudflare Access redirect.

## Mandatory cleanup

```bash
curl -fsS -X DELETE \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "$API/accounts/$ACCOUNT_ID/access/apps/$APP_ID/policies/$POLICY_ID"

curl -fsS -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "$API/accounts/$ACCOUNT_ID/access/apps/$APP_ID/policies" |
jq -e '[.result[] | select(.decision == "bypass")] | length == 0'

curl -fsSI https://syrabit.ai/staff/ | head -n 1
# Expected: HTTP/2 302
```

If the API is unavailable, perform the same narrowly scoped add/delete from
Cloudflare One under Access > Applications > Syrabit Admin > Policies. Do not
disable or delete the Access application.

## Safe rehearsal

Rehearse with the reserved, non-routable TEST-NET address `192.0.2.1/32`.
Create the policy, confirm ordinary public traffic still receives the Access
redirect, delete the policy, and confirm there are no remaining bypass
policies. This validates creation and cleanup without granting any real client
access.