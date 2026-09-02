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
- When an opt-in disposable reset fixture is configured, it also confirms a
  delivered reset link, one password change, token replay rejection, and login
  with the replacement password.
- API Worker unit tests and the API Worker type check passed.

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

### Disposable mailbox password-reset proof

The invalid-token probe is safe for every cutover, but it cannot prove that
Resend delivered a link or that a real account can complete the reset. The
full proof is opt-in and uses only a dedicated disposable student fixture. It
is not enabled by default because this repository has no mailbox-reading
service.

Create a disposable account and mailbox whose address contains `cutover`
(for example, a tagged test mailbox), and never use a student or staff
account. Configure:

- Repository secrets `CUTOVER_RESET_EMAIL` and `CUTOVER_RESET_PASSWORD` for
  that fixture.
- A protected GitHub Actions environment named `cutover-reset-delivery`, with
  a required reviewer and an environment secret named `CUTOVER_RESET_LINK`.
  The link is short-lived and must never appear in workflow logs, issues, or
  chat.

When dispatching `.github/workflows/deploy-cloudflare.yml`, choose
`activate_native=true`, `validate_authenticated=true`, and
`validate_reset_delivery=true`. After the native deployment and general
authenticated smoke checks succeed, the workflow:

1. Generates a new random nonce and requests the disposable reset email
   through the just-deployed public edge.
2. Pauses before the protected `cutover-reset-delivery` environment.
3. Requires the reviewer to copy the newly delivered link into that
   environment's `CUTOVER_RESET_LINK` secret, then approve the job.
4. Accepts the link only if its `cutover_nonce` matches the nonce from the
   preceding request, so a link from an older release cannot pass.
5. Confirms the reset once, expects the exact same token to return
   `Invalid or expired reset token`, and signs in with
   `CUTOVER_RESET_PASSWORD`.

The Worker adds the nonce only when the request supplies a valid opaque
`cutover_nonce`; ordinary reset requests and their non-enumerating response
remain unchanged. The reset page forwards that query value to the confirmation
route, and the Worker compares it with the value stored beside the token before
it allows a nonce-bound reset. A successful run therefore proves:

1. The disposable account can request a reset through the public edge.
2. A delivered link reaches the Worker-native confirmation contract and
   changes the disposable account's password.
3. The token is single-use and cannot change the password a second time.

The matching nonce makes the fixture self-renewing: every release needs a new
email link, so an already-consumed or stale environment secret fails safely
instead of validating a later release. If the request, approval, link check,
confirmation, replay check, or replacement-password login fails, native edge
routing is rolled back.

### Worker-native boundary

Cloud Run was retired on 2026-09-02. The route inventory has no catch-all
backend bridge, Google OIDC token exchange, or `cloud-run-fallback` response.
The validator rejects active Worker source that reintroduces any of those
paths. All supported authenticated routes must emit
`X-Syrabit-Route: worker-native`.

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

The Cloudflare deployment workflow exposes the same full check when
`validate_authenticated` is selected. Supply the documented disposable GitHub
secrets before using that gate. A successful public-only smoke test is not
evidence of full authenticated parity. Chat and internal generation are
bounded usage probes against these disposable credentials; all other added
checks avoid creating orders, payment credits, content, seed runs, or publish
jobs.

A failed, skipped, or cancelled downstream smoke job blocks the release. It
does not switch production traffic to another backend.
