# Authenticated Cloudflare cutover validation record

## Latest production-safe check

**Recorded:** 2026-08-24 UTC  
**Public edge:** `https://api.syrabit.ai`  
**Direct API Worker:** `https://syrabit-api-prod.axomxplain.workers.dev`

### Confirmed

- The public edge and direct API Worker health endpoints returned `200`; the
  API Worker identified itself as `cloudflare-workers` with a healthy D1
  component.
- Public library and content endpoints returned the expected catalogue data.
- The bounded Workers AI chat request returned `200` as Server-Sent Events,
  carried `X-Syrabit-Route: worker-native`, emitted a source card and clean
  completion event, and contained answer content without an error event.
- Invalid payment-verification requests without a user session returned `401`,
  and malformed/forged Razorpay webhook requests returned `400`. No order,
  credit, or webhook state was created by these probes.
- The cutover validator now checks the native password-reset request's
  non-enumerating `200` response through the public edge and checks the native
  confirmation route's invalid-token `400` response without changing password
  state.
- API Worker unit tests passed (69 tests) and the API Worker type check passed.

### Password-reset route parity

The frontend now uses the native Worker contracts:

- `POST /api/v1/auth/reset-password/request` with `{ "email": "..." }`
- `POST /api/v1/auth/reset-password/confirm` with
  `{ "token": "...", "password": "..." }`

Both routes are explicitly exempted from edge JWT verification. The cutover
validator checks the request route's non-enumerating `200` response and probes
the confirmation route with an invalid token, expecting the native
`Invalid or expired reset token` `400` response. The confirmation probe is
deliberately non-mutating; a real reset token is never consumed.

### Cloud Run compatibility fallback

The route inventory intentionally retains catch-all Cloud Run bridges for
unmatched `/api/v1/admin/*` and `/api/v1/seed/*` routes. Native publishing,
content editing, RAG, and scheduled seed routes take precedence over those
bridges.

For a retained route, the public edge obtains a Google OIDC identity token from
its `GOOGLE_SA_KEY` secret and carries it as the internal
`X-Cloud-Run-Token` header over the API Worker's service binding. The API
Worker strips that internal header and uses it as Cloud Run's
`Authorization: Bearer …` credential. The caller's admin cookie, cron token,
and other application headers remain available to the Cloud Run application.
Only the explicit compatibility bridge emits
`X-Syrabit-Route: cloud-run-fallback`; native replacements continue to emit
`worker-native`.

The full-stage cutover validator includes the bounded, read-only
`GET /api/v1/admin/users?limit=1` check with the disposable admin session.
It requires the fallback marker and the established users-list response shape,
proving that a retained admin operation remains available after native
activation without creating or changing production data.

### Authenticated full-stage gate

The workspace does not contain disposable student, staff, or admin-session
credentials, so no authenticated production account was used for this record.
`scripts/validate-cloudflare-api-cutover.sh` now requires disposable
`STUDENT_TOKEN`, `STAFF_TOKEN`, `ADMIN_SESSION_TOKEN`,
`EDGE_SHARED_SECRET`, and `TRANSLATE_CRON_SECRET` for a full stage. It checks
public-edge Worker-native markers for:

- student profile, history, credits, subscription, content access, payments,
  and chat;
- staff content and read-only RAG state;
- admin publishing-route reachability, translation progress, RAG status, and
  seed-run history;
- scheduled English/Assamese seed status and authenticated internal AI
  generation; and
- forged payment-verification and Razorpay-webhook rejection paths.

The Cloudflare deployment workflow exposes the same full check only when both
`activate_native` and `validate_authenticated` are selected. Supply the
documented disposable GitHub secrets before using that gate. A successful
public-only smoke test is not evidence of full authenticated parity. Chat and
internal generation are bounded usage probes against these disposable
credentials; all other added checks avoid creating orders, payment credits,
content, seed runs, or publish jobs.

The deployment workflow requires `activate_native` and
`validate_authenticated` together, and restores `API_WORKER_LIVE=false` when
the downstream smoke job does not succeed, including when it is skipped or
cancelled.
