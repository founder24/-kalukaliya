# Cloudflare full-audit remediation guide

Reference for `apps/frontend/scripts/cloudflare-full-audit.js` (and the
related `cloudflare-annual-review.js` / `nightly-smoke.js` scripts, which
share the same token). Use this doc when the audit reports a scope-gap
`WARN` (`token lacks … scope`) instead of a real `PASS`/`FAIL`.

## Current status (2026-09-20)

The `CLOUDFLARE_API_TOKEN` in this project still lacks all six read scopes
below — items #2, #5, #6, #7, #9, and #17 report `WARN` on every run. This
is a manual step outside the codebase: only the Cloudflare account owner,
signed into the dashboard, can add scopes to the token. It cannot be done
from this environment or by the agent (see "Why the token can't fix
itself" below — the token is correctly forbidden from editing its own
permissions). Whoever owns the `syrabit.ai` Cloudflare account should
follow the steps below once, then re-run the audit to confirm the six
items flip to `PASS`/`FAIL`.

## Why the token can't fix itself

Cloudflare API tokens cannot read or edit their own permissions unless they
were explicitly granted the account-level "API Tokens Edit" permission
group. Granting that permission to the audit token would let it escalate
its own scope, so it is intentionally excluded. Verified directly against
the account on 2026-09-20 — `GET /user/tokens/verify` succeeds, but
`GET /user/tokens/{id}` and `GET /user/tokens/permission_groups` both return
`9109 Unauthorized to access requested resource` for the audit token. Adding
scopes to the token that `CLOUDFLARE_API_TOKEN` points at is therefore a
one-time manual step in the Cloudflare dashboard, done by whoever owns the
account:

1. Sign in to the Cloudflare dashboard → **My Profile → API Tokens**
   (`https://dash.cloudflare.com/profile/api-tokens`).
2. Find the token stored as the `CLOUDFLARE_API_TOKEN` secret in this
   project (check "Last used" ≈ the audit's last run time to confirm which
   token it is if more than one exists).
3. Click **Edit** and add the permission groups listed below (all **Read**
   only — the audit never needs write access). Save; the token value itself
   does not change, so no secret needs to be rotated in Replit.
4. Re-run `node apps/frontend/scripts/cloudflare-full-audit.js` and confirm
   the corresponding items flip from `WARN` to `PASS`/`FAIL`.

## Minimum read-only scopes required for zero scope-gap warnings

| Audit item(s) | Permission group | Scope level | Resource |
| --- | --- | --- | --- |
| #2 Bot Management | `Bot Management Read` | Zone | `GET /zones/{zone_id}/bot_management` |
| #5, #6 Logpush jobs | `Logs Read` | Zone | `GET /zones/{zone_id}/logpush/jobs` |
| #7 Origin healthcheck | `Health Checks Read` | Zone | `GET /zones/{zone_id}/healthchecks` |
| #9 Waiting Room | `Waiting Rooms Read` | Zone | `GET /zones/{zone_id}/waiting_rooms` |
| #17 mTLS client certificate | `SSL and Certificates Read` | Account | `GET /accounts/{account_id}/mtls_certificates` |
| #19 Zaraz | `Zaraz Read` | Zone | `GET /zones/{zone_id}/zaraz/config` |

These six groups are additive to the scopes the token already holds (Zone
Settings, DNS, R2, Zero Trust, Cache, Workers, Account Notifications, Speed
— all confirmed already working). No account-level "Edit" permissions are
needed for any of these checks; every one of them is read-only.

## Plan/API limitations that must remain manual checks

- **Zaraz config route (#19).** Even with `Zaraz Read` granted, `GET
  /zones/{zone_id}/zaraz/config` can return `7000`/`7003` ("no route for
  that URI") when Zaraz has never been switched on for the zone from the
  dashboard (Zaraz is opt-in per zone). This is not a token scope problem —
  it is a zone feature-activation state that the API does not expose a
  read-only "is Zaraz enabled" flag for. If the audit still reports this
  after the scope is granted, verify manually at
  `dash.cloudflare.com → Zaraz` that Zaraz is turned on for the zone, then
  re-run the audit.
- **Cache Reserve backing storage (#11).** Cache Reserve's backing storage
  is a Cloudflare-managed R2 bucket, not one visible in the account's own
  bucket list. The audit intentionally `SKIP`s this — there's no
  customer-facing API to verify it, so it remains a Cloudflare-side
  guarantee, not something a token scope can unlock.
- **Analytics Engine dataset (#16).** Retired with the old Worker
  architecture; intentionally `SKIP`ped, not a scope gap.
- **Cache Reserve zone setting / Image Resizing plan gate (#12, #18).**
  These use the `PLAN_REQUIRED` status when Cloudflare returns error code
  `1135` (feature not on the current plan). That's a billing/plan decision,
  not something any token scope changes.
- **DMARC policy (#3).** The audit correctly reads this today (no scope
  gap) — its `FAIL` reflects a real DNS record that still needs a
  `p=quarantine` policy. That is unrelated to token scopes and is tracked
  separately.

## Verifying the fix

After the scopes are added in the dashboard, run:

```sh
cd apps/frontend
node scripts/cloudflare-full-audit.js
```

Expect items #2, #5, #6, #7, #9, and #17 to move from `WARN` to `PASS` (or
a real `FAIL` if the underlying resource is actually missing/misconfigured
— that would be a genuine finding, not a scope gap). Item #19 may still
`WARN` if Zaraz has never been activated for the zone; that is the one
item on this list that stays a manual dashboard check per the note above.
