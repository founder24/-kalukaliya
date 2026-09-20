---
name: Cloudflare API tokens cannot grant themselves new scopes
description: Why scope-gap audit warnings can't be fixed from code/agent side, and what to do instead
---

A Cloudflare API token cannot read or edit its own permissions unless it
was explicitly granted the account-level "API Tokens Edit" permission
group — which a least-privilege audit/read-only token correctly never has.
Confirmed directly: `GET /user/tokens/verify` succeeds for the token, but
`GET /user/tokens/{id}` and `GET /user/tokens/permission_groups` both
return `9109 Unauthorized to access requested resource`.

**Why:** Cloudflare does not expose a self-service "add scope to the token
I'm currently using" API. Granting a token permission to edit its own
scopes would let it escalate its own access, so it's intentionally
excluded from minimal-privilege tokens.

**How to apply:** When an infra audit script reports "token lacks X: Read
scope" warnings, do not try to fix it via API calls with the same token —
it will always 9109/10000. The only fix is the Cloudflare account owner
manually editing the token's permissions in the dashboard
(dash.cloudflare.com/profile/api-tokens → Edit → add permission groups →
Save). No secret rotation is needed since the token value itself doesn't
change. See docs/dev/cf-audit-remediation.md in the syrabit project for
the full worked example (exact permission-group names for Bot Management,
Logs, Health Checks, Waiting Rooms, SSL and Certificates, Zaraz).
