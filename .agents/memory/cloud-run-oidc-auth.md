---
name: Cloud Run OIDC auth (org policy change 2026-08)
description: GCP org policy now blocks allUsers invoker; all routes need OIDC token from CF edge
---

## Rule
Cloud Run (`syrabit-backend`) no longer accepts unauthenticated requests — GCP org policy blocks the `allUsers` invoker IAM binding. All requests MUST carry a Google OIDC identity token.

**Why:** In August 2026, a GCP organization policy was enforced that prevents setting `roles/run.invoker` for `allUsers`. `gcloud run deploy --allow-unauthenticated` now fails with `FAILED_PRECONDITION`. Without the IAM binding, Cloud Run returns 401 for all requests.

## How it works now
- The CF Worker (`api-proxy.ts`) always injects a Google OIDC identity token in `Authorization` for every proxied request (no exceptions for cron routes).
- The original caller token (user JWT or cron secret) is saved in `X-User-JWT` before overwrite.
- The backend's `_verify_cron_token()` checks both `Authorization` and `X-User-JWT` for the cron secret.

## deploy.yml changes required
- Remove `--allow-unauthenticated` from `gcloud run deploy`.
- The CI health check (`Wait for healthy revision`) must send an OIDC token: `gcloud auth print-identity-token --audiences="${SERVICE_URL}"`.

## How to apply
- Never add `--allow-unauthenticated` to `gcloud run deploy` — it will fail.
- Always keep OIDC token injection in `api-proxy.ts` for ALL routes, not just user-facing ones.
- Backend cron auth relies on `X-User-JWT` fallback in `_verify_cron_token`.
