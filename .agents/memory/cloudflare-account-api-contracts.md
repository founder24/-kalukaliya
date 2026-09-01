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