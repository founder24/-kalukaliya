---
name: Cloudflare account API contracts
description: Current API requirements that differ from older generic Cloudflare automation assumptions.
---

R2 custom-domain creation requires the owning zone identifier in the request;
legacy payloads that supply cache or TLS fields instead are rejected as
malformed.

**Why:** Cloudflare accepted the endpoint and authentication but returned a
generic malformed-JSON error until the required zone identifier was supplied.

**How to apply:** Use the current Cloudflare R2 API schema when attaching a
bucket custom domain, and verify both ownership and SSL status before publishing
the URL.

Access application writes require the resource-specific `Access: Apps and
Policies Edit` token permission. Generic “Zero Trust Edit” wording is not an
equivalent permission.

**Why:** A valid token could list Access applications and identity providers
but every write returned `auth.forbidden` until the specific Access permission
was granted.

**How to apply:** Request `Access: Apps and Policies Edit` for app and policy
automation, plus `Access: Identity Providers Read` when login-provider checks
are part of the audit.

Scheduled callers behind Access must use a Service Auth policy bound to a
Cloudflare service token; an `Everyone` bypass is not a bearer-token check.

**Why:** Access evaluates bypass before the application sees the request, so a
bypass removes that defense-in-depth layer even when the application has its
own cron secret.

**How to apply:** Store the Access client ID/secret only in the scheduler's
secret store, send both Access headers and the application's cron credential,
and make reconciliation remove any bypass policy that appears on the cron app.

An Access application's `destinations` and `self_hosted_domains` define its
effective host/path coverage; the headline `domain` can look narrow while extra
destinations still intercept unrelated subdomains.

**Why:** The staff UI app displayed `syrabit.ai/staff*` as its domain but also
contained admin API destinations, so application-authenticated login requests
to `api.syrabit.ai` were redirected to Access before reaching the Worker.

**How to apply:** Keep the staff Access app scoped to the staff UI only. Protect
scheduled admin endpoints with their separate Service Auth app, and leave
interactive API routes to Syrabit's cookie/bearer guard. After updates, wait for
propagation and verify both the protected UI redirect and an API-level 401.

Vectorize REST endpoints for ID operations use underscores:
`delete_by_ids` and `get_by_ids`. Hyphenated variants return a plain 404 even
when authentication, account, and index are valid.

**Why:** A production notes reindex reached the valid index but failed at
`delete-by-ids`; probing the equivalent underscore endpoint succeeded.

**How to apply:** Use the underscore paths in direct REST clients, and validate
the endpoint contract with an empty ID list before beginning a bulk mutation.

R2 location hints are best-effort placement preferences, not residency
guarantees. Persistent Logpush-to-R2 jobs require durable R2 destination
credentials; temporary R2 credentials expire within seven days.

**Why:** The Cloudflare API integration can create buckets and temporary
credentials, but it does not expose the dashboard's automatic R2 Logpush setup
or a persistent R2 token-creation flow.

**How to apply:** Use Cloudflare Dashboard's automatic R2 destination setup for
ongoing Logpush, and confirm the bucket's actual location before sending request
logs. Do not use seven-day credentials for a persistent job or claim a location
hint guarantees data residency.