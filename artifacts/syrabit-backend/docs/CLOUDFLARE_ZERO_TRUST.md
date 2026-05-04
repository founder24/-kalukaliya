# Cloudflare Zero Trust for Syrabit (Task #637)

This runbook covers the operator-side provisioning of Cloudflare Zero
Trust for the admin / internal surface area, paired with the in-repo
JWT enforcement that defends the origin even if someone learns the
direct Digital Ocean App Platform URL (Task #336 — replaces the legacy Cloud Run / Railway origins).

```
admin user
   │  (Google SSO via Access)
   ▼
team domain (syrabit.cloudflareaccess.com)
   │  ← Access Application: "Syrabit Admin" (Self-hosted, syrabit.ai/admin/*)
   │  injects: Cf-Access-Jwt-Assertion: <RS256 JWT, AUD = aud-admin-tag>
   ▼
api.syrabit.ai  (Cloudflare Worker)
   │  + X-Origin-Auth: <ORIGIN_SHARED_SECRET>
   │  + Cf-Access-Jwt-Assertion forwarded
   ▼
syrabit-backend  (FastAPI on Cloud Run)
   │
   ├── OriginSharedSecretMiddleware  → 403 if X-Origin-Auth missing
   └── get_admin_user dependency      → 401 if Cf-Access-Jwt-Assertion missing/invalid
```

The code-side enforcement is implemented in
[`cf_access.py`](../cf_access.py) and is wired into
[`auth_deps.get_admin_user`](../auth_deps.py). Tests:
[`tests/test_cf_access.py`](../tests/test_cf_access.py).

---

## 0. Operator activation checklist (Task #705)

Task #702 shipped the *code-side* gate on `/api/admin/login` (fail-closed
503 if the env vars are partially set, 401 if the JWT is missing) plus
the regression tests. **Until an operator completes the steps below in
production, the protection is a no-op** — `/api/admin/login` is still
reachable on the bare Digital Ocean App Platform URL (Task #336 — replaces the legacy Railway URL) with only the origin shared secret.

This checklist must be executed by someone with **both** Cloudflare
dashboard access **and** Digital Ocean App Platform env access (Task #336 — replaces Railway). The agent cannot perform
any of these steps. Tick each item in the rollout ticket:

- [ ] **Zero Trust team domain live.** Cloudflare dashboard → Zero Trust
  is enabled on the syrabit account and the team domain
  `https://syrabit.cloudflareaccess.com` resolves. (See §1.)
- [ ] **Google IdP confirmed.** Zero Trust → Settings → Authentication
  shows Google as a login method and a test login from an
  `@syrabit.ai` account succeeds end-to-end. (See §1.)
- [ ] **"Syrabit Admin" Access app exists.** Self-hosted Access app
  covers `syrabit.ai/admin*` **and** `api.syrabit.ai/api/admin*`,
  bound to the `syrabit-admins` group. (See §2.)
- [ ] **AUD tag copied.** From the Access app overview page, copy the
  Application Token AUD tag (sha256 hex). This is the value for
  `CF_ACCESS_AUD_ADMIN`.
- [ ] **(Optional) Internal Access app + AUD tag.** Only required once
  `/api/_internal/*` routes ship; the env var can be left unset
  until then (the dependency is a no-op when the AUD is empty).
- [ ] **All four env vars set on the Digital Ocean App Platform `syrabit-backend` app** (Task #336; use `bash scripts/digitalocean.sh var-set syrabit-backend KEY=VAL` or the App Platform UI: cloud.digitalocean.com → Apps → syrabit-backend → Settings → App-Level Environment Variables. Legacy: Railway Project →
  syrabit-backend → Variables):
  ```
  CF_ACCESS_TEAM_DOMAIN=syrabit
  CF_ACCESS_AUD_ADMIN=<admin app AUD tag>
  CF_ACCESS_AUD_INTERNAL=<internal app AUD tag, or leave unset>
  CF_ACCESS_ENFORCE=true
  ```
  > Set `CF_ACCESS_ENFORCE=true` **last**, after the AUD tag is in
  > place. If `CF_ACCESS_ENFORCE=true` but `CF_ACCESS_AUD_ADMIN` is
  > empty, the backend fails closed with a 503 on every admin request
  > (intentional — see `cf_access._fail_closed_if_misconfigured`).
- [ ] **FastAPI restarted.** Digital Ocean App Platform → syrabit-backend → Activity → "Force Rebuild and Deploy" (or `bash scripts/digitalocean.sh redeploy syrabit-backend`). Legacy: Railway → syrabit-backend → Deployments →
  Restart (or trigger a redeploy). Required because env vars are
  read at process start.
- [ ] **Diagnostics confirms enforcement is on.** From an authenticated
  admin browser session:
  ```
  GET https://api.syrabit.ai/api/admin/diagnostics
  ```
  Response must include `"admin_enforced": true` and
  `"admin_aud_configured": true`. If `admin_enforced` is `false`,
  one of the four env vars is missing or the service was not
  restarted.
- [ ] **Bare-origin bypass returns 401 on `/api/admin/login`.** Run the
  curl in §6 step 3 against the **bare Digital Ocean URL** (Task #336;
  e.g. `https://syrabit-backend-app.ondigitalocean.app`, replacing the
  legacy `https://syrabit-backend-production.up.railway.app`) with the
  `X-Origin-Auth` header set but **no** `Cf-Access-Jwt-Assertion`
  header. Expected status: `401`. This is the regression check that
  proves Task #702's gate on the login route is actually live in
  production — not just in the test suite.

Once every box is ticked, Task #705 is done. File the completed
checklist (with the diagnostics JSON and the curl output) in the
ops log so the next on-call can audit the rollout.

---

## 1. One-time Zero Trust setup (Cloudflare dashboard)

1. **Enable Zero Trust** on the Cloudflare account (free tier covers up
   to 50 seats — enough for the founding team plus operations partners).
   Pick a team domain such as `syrabit` →
   `https://syrabit.cloudflareaccess.com`.

2. **Add identity providers**:
   - **Google** (primary, for `@syrabit.ai` Workspace accounts): create
     OAuth credentials in Google Cloud, paste client id + secret into
     Zero Trust → Settings → Authentication → Login methods → Google.
   - **One-time PIN** (break-glass): leave enabled, restricted to a
     short allowlist of personal email addresses (founders only). This
     is the recovery path if Google SSO breaks.

3. **Define groups** (Zero Trust → Access → Access Groups):
   - `syrabit-admins` → emails ending in `@syrabit.ai` AND in the
     explicit allowlist of admin emails.
   - `syrabit-internal-ops` → superset including contractors granted
     ops access; require WARP device posture once WARP is rolled out.

## 2. Access applications

Create two **Self-hosted** Access apps:

| App name           | Hostname / Path                  | Allowed groups          | AUD env var               |
| ------------------ | -------------------------------- | ----------------------- | ------------------------- |
| Syrabit Admin      | `syrabit.ai/admin*`, `api.syrabit.ai/api/admin*` | `syrabit-admins`        | `CF_ACCESS_AUD_ADMIN`     |
| Syrabit Internal   | `api.syrabit.ai/api/_internal*` *(reserved — no routes mounted yet; see note below)* | `syrabit-internal-ops`  | `CF_ACCESS_AUD_INTERNAL`  |

> **Note on the Internal tier.** No `/api/_internal/*` routes are mounted
> in the backend at the time this task ships. The dependency
> `require_cf_access_internal` and the `CF_ACCESS_AUD_INTERNAL` env var
> are forward-looking hooks for the upcoming ops/feature-flag/kill-switch
> surface. The Access app can be created now (so the AUD tag is stable
> when the routes land) or deferred — both paths are safe.

For each app:

- **Session duration**: 8 hours (admin), 1 hour (internal).
- **Identity providers**: Google primary, OTP allowed only for break-glass.
- **Application Token AUD tag**: copy the value (a sha256 hex string)
  shown in the app's overview; this is the value you set in the
  backend env.
- **Service tokens**: create one named `syrabit-ci` for the GitHub
  Actions workflow that hits `/api/_internal/deploy-status` so CI can
  authenticate without an interactive browser session.

## 3. Cloudflare Tunnel (origin → CF, replaces public ingress)

Goal: stop publishing the Cloud Run URL altogether. Cloud Run only
accepts traffic from the tunnel.

1. In Zero Trust → Networks → Tunnels, create `syrabit-backend`.
2. Install `cloudflared` on the Cloud Run side car (or run as a
   separate Cloud Run job). Recommended: deploy the cloudflared
   container alongside the FastAPI container in the same revision
   using a sidecar. The Cloud Run service stops needing
   `--ingress=all`; switch it to `--ingress=internal` and let the
   tunnel be the only public path.
3. In the tunnel config, point `api.syrabit.ai` →
   `http://localhost:8080`.
4. Update the Cloudflare worker so it routes through the tunnel hostname
   (no change required if it already targets `api.syrabit.ai`).

## 4. Gateway DNS policy & CASB

- Gateway → Policies → DNS: block known phishing, malware, and
  cryptomining categories for any device that runs WARP under the
  syrabit team. Add an allow rule for `*.syrabit.ai`,
  `*.cloudflareaccess.com`, and the Google Workspace login hosts so
  the team isn't blocked from their own dashboard.
- CASB → Integrations: connect Google Workspace (org admin OAuth) and
  GitHub. Enable the "shadow IT" finding and route Critical/High
  findings into the existing notification pipeline using the webhook
  in admin/notifications.

## 5. Backend env vars (Digital Ocean App Platform production — and `.env` for local)

> Task #336 migrated this from Railway to Digital Ocean App Platform.
> Use `bash scripts/digitalocean.sh var-set syrabit-backend KEY=VAL`
> (or the App Platform UI under Apps → syrabit-backend → Settings →
> App-Level Environment Variables). The variable names below are
> unchanged.

Set on the FastAPI service in **App Platform → syrabit-backend → Settings → App-Level Environment Variables** (legacy: Railway → syrabit-backend → Variables)
(or in `.env` for local development). **All four must be set before
flipping enforcement on**:

```
CF_ACCESS_TEAM_DOMAIN=syrabit            # without the .cloudflareaccess.com suffix
CF_ACCESS_AUD_ADMIN=<admin app AUD tag>
CF_ACCESS_AUD_INTERNAL=<internal app AUD tag>
CF_ACCESS_ENFORCE=true
```

Until `CF_ACCESS_ENFORCE` is `true`, the dependency is a no-op so
existing admin sessions keep working. This is the safe rollout order:

1. Provision the team domain, IdP, Access apps (steps 1–2).
2. Roll out the backend with the env vars set but `CF_ACCESS_ENFORCE`
   unset → CF Access is live in front of the admin URL but the origin
   does **not** require it yet.
3. Confirm admins can still log in through the Access challenge.
4. Flip `CF_ACCESS_ENFORCE=true` and restart the FastAPI service.
5. Verify a curl to the bare Cloud Run URL with the
   `X-Origin-Auth` secret but **no** `Cf-Access-Jwt-Assertion` header
   returns 401 on `/api/admin/*` **including `/api/admin/login`**.
   Task #702 added the `require_cf_access_admin` dependency directly to
   the login handler so the credential check is unreachable without an
   Access JWT — closing the brute-force bypass that existed when only
   post-login admin routes were gated.

## 6. Verification (post-rollout smoke tests)

```bash
TEAM=syrabit
ADMIN_URL="https://syrabit.ai/admin/dashboard"

# 1. Unauthenticated browser hit → CF Access login challenge (302)
curl -sI "$ADMIN_URL" | head -1

# 2. Authenticated CLI access via service token (CI path)
cloudflared access curl \
  --service-token-id "$CF_SERVICE_TOKEN_ID" \
  --service-token-secret "$CF_SERVICE_TOKEN_SECRET" \
  https://api.syrabit.ai/api/_internal/health

# 3. Origin bypass attempt → must be 401 (run against the BARE Digital
#    Ocean URL, not api.syrabit.ai, so you actually skip the Cloudflare
#    edge). Task #336 — DO replaces the legacy Railway URL; the variable
#    name is kept as RAILWAY_URL only to avoid breaking embedded
#    copy-paste snippets in older runbooks.
RAILWAY_URL="https://syrabit-backend-app.ondigitalocean.app"

curl -sS -X POST \
     -H "X-Origin-Auth: $ORIGIN_SHARED_SECRET" \
     -H "Content-Type: application/json" \
     -d '{"email":"x@x","password":"x"}' \
     "$RAILWAY_URL/api/admin/login" \
  -o /dev/null -w '%{http_code}\n'    # expected: 401 (Task #702 gate)

curl -sS -H "X-Origin-Auth: $ORIGIN_SHARED_SECRET" \
     "$RAILWAY_URL/api/admin/users" \
  -o /dev/null -w '%{http_code}\n'    # expected: 401

# 4. Enforcement state introspection (no auth required for the JSON
#    body itself; reachable through the Access challenge in a browser)
curl -sS https://api.syrabit.ai/api/admin/diagnostics | jq
# expected (post-rollout):
#   "admin_enforced": true,
#   "admin_aud_configured": true,
#   "team_domain": "syrabit"
```

If step 3 returns `200` or a credentials-error JSON instead of `401`,
**stop the rollout** — it means either `CF_ACCESS_ENFORCE` is not
`true` in the running process (env var not set, or the service was not
restarted after setting it) or the AUD env var is empty. Re-check §0
and §5 before continuing.

## 7. Operational runbook

| Event                       | Action                                             |
| --------------------------- | -------------------------------------------------- |
| Admin offboarded            | Remove email from `syrabit-admins` group; their next request is 401. |
| AUD tag rotated             | Update `CF_ACCESS_AUD_ADMIN` env, restart service. |
| Team domain renamed         | Update `CF_ACCESS_TEAM_DOMAIN`, restart service.   |
| JWKS rotation               | No action — `cf_access.py` refetches on KID miss.  |
| Suspected token leak        | Revoke the user's session in Zero Trust → Sessions; revoke the matching service token if CI was the source. |
| Need temporary bypass       | Use the break-glass paths in §7.1 (60-second recovery, no Digital Ocean App Platform access required — Task #336 replaced Railway). The DO env-flip via `bash scripts/digitalocean.sh var-set syrabit-backend CF_ACCESS_ENFORCE=false` is the slow fallback. |

### 7.1 What to do if Cloudflare Access goes down (Task #706)

A Cloudflare Zero Trust outage, an IdP failure, or an AUD-tag misrotation
will lock every admin out of the dashboard at the exact moment they need
to react. The original "set `CF_ACCESS_ENFORCE=false` on the backend
host and restart" recovery (Task #336 — Digital Ocean App Platform,
formerly Railway) requires DO dashboard / `doctl` access plus a service
restart — that
can take 5–10 minutes during an active incident. The two break-glass
paths below cap recovery at **~60 seconds** and do **not** require a
FastAPI restart.

**Pre-staged inputs (one-time setup, must be done before the incident):**

1. **One-Time-PIN IdP enabled in Zero Trust** with a 2-email allowlist
   limited to founder personal email addresses (NOT `@syrabit.ai`,
   because Workspace itself may be the thing that's down). Test the OTP
   path end-to-end at provisioning time — log in once with each
   allowlisted email, confirm the inbox actually receives the code, and
   record the test in the ops log. Re-test quarterly.
2. **`CF_ACCESS_BREAK_GLASS_TOKEN`** secret set on the FastAPI service
   (one-time, never rotated mid-incident). A long random string;
   generate with `python -c "import secrets;print(secrets.token_urlsafe(48))"`.
3. **Cloudflare Worker secret** with the same value, named e.g.
   `CF_ACCESS_BREAK_GLASS_TOKEN`. The Worker conditionally injects
   `X-Cf-Access-Break-Glass: <secret>` on `/api/admin/*` requests when
   another Worker secret (e.g. `CF_ACCESS_BREAK_GLASS_ENABLED=true`) is
   set. Both secrets are editable from the Cloudflare dashboard alone.

**Recovery path A — non-backend-host (preferred, ~60s; Task #336 — formerly "non-Railway"):**

1. Cloudflare dashboard → Workers & Pages → `syrabit-edge` → Settings →
   Variables and Secrets → set `CF_ACCESS_BREAK_GLASS_ENABLED=true`,
   Save and Deploy. (Worker rolls out globally in ~10s.)
2. From a **personal browser**, hit `/api/admin/login` with the normal
   admin password. The Access challenge is bypassed for the lifetime of
   the Worker flag; the admin JWT check still runs (so password and
   2FA still apply — break-glass is *not* an authentication bypass,
   only an *Access proxy* bypass).
3. Confirm via `GET /api/admin/diagnostics` that the response shows
   `"break_glass_active": true` and `"break_glass_source": "header"`.
   This object is the authoritative state — every CRITICAL log line
   tagged `BREAK-GLASS bypass active` is also the audit trail.
4. Once Cloudflare Access is healthy again, **flip
   `CF_ACCESS_BREAK_GLASS_ENABLED` back to `false`** on the Worker.
   Re-confirm `"break_glass_active": false`.

**Recovery path B — One-Time-PIN admin login (backend host untouchable; Task #336 — formerly "Railway untouchable"):**

If both Workers and the Digital Ocean App Platform `syrabit-backend`
app are unreachable but the admin team domain is
still serving the OTP IdP, founders can log in via OTP (step 1 of §1)
and reach `/admin/*` directly. Use this when path A is also blocked.

**Recovery path C — backend-host env-flip (legacy fallback, 5–10 min; Task #336 — Digital Ocean App Platform via `bash scripts/digitalocean.sh var-set syrabit-backend CF_ACCESS_ENFORCE=false` then `redeploy syrabit-backend`; formerly the Railway dashboard env edit):**

1. Railway → syrabit-backend → Variables → set
   `CF_ACCESS_BREAK_GLASS=true`. Save (this triggers a redeploy).
2. After the new revision goes live, the bypass is active until the
   variable is unset. Same diagnostics signal as above.
3. Restore: delete `CF_ACCESS_BREAK_GLASS` and let the redeploy roll.

**Paging:** `/api/admin/diagnostics` fires the
`cf_access_break_glass_active` and `cf_access_admin_degraded` alert
types through the existing notification pipeline whenever the snapshot
is degraded on a production-provisioned environment. Subscribe the
on-call PagerDuty / Slack channel to both alert types — do **not**
silence them, since their entire purpose is to remind the team to
disable the bypass once the outage is over.

**Synthetic probe (Task #708 — required, ships in `syrabit-edge`):**
The paging rule above only runs when something actually calls
`/api/admin/diagnostics`. During a real outage no admin is browsing the
dashboard, so the alert never fires. The `syrabit-edge` Worker carries
a 1-minute cron (`* * * * *`) that hits the diagnostics endpoint from
outside the cluster using a CF Access service token + a long-lived
admin JWT. Implementation: `workers/edge-proxy/src/synthetic-probe.ts`.

Configuration (Cloudflare dashboard → Workers & Pages →
`syrabit-edge` → Settings → Variables and Secrets):

| Name                                       | Kind   | Purpose                                                                 |
| ------------------------------------------ | ------ | ----------------------------------------------------------------------- |
| `SYNTHETIC_PROBE_TARGET_URL`               | var    | Full URL to probe. Default: `${BACKEND_URL}/api/admin/diagnostics` (the `/api` prefix matches the FastAPI router mount in `server.py`; bare `/admin/diagnostics` returns 404 — Task #877). |
| `SYNTHETIC_PROBE_CF_ACCESS_CLIENT_ID`      | secret | CF Access service token client id (`*.access`).                          |
| `SYNTHETIC_PROBE_CF_ACCESS_CLIENT_SECRET`  | secret | CF Access service token secret.                                          |
| `SYNTHETIC_PROBE_ADMIN_JWT`                | secret | Long-lived admin JWT signed with `ADMIN_JWT_SECRET` (1y exp, role=admin). |
| `SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL`     | secret | Slack/PagerDuty webhook fired when the probe itself dies for >5 min.    |
| `SYNTHETIC_PROBE_WATCHDOG_THRESHOLD_MIN`   | var    | Override watchdog threshold (default `5`, i.e. 5 consecutive failures). |
| `SYNTHETIC_PROBE_DISABLED`                 | var    | Set to `true` to pause the probe without redeploying.                   |

**Probe behaviour:**

- Every minute the worker GETs the target URL with `CF-Access-Client-Id`,
  `CF-Access-Client-Secret`, `Authorization: Bearer <admin JWT>`, and
  the `X-Origin-Auth` shared secret (auto-injected from
  `BACKEND_ORIGIN_SECRET`).
- A 2xx response means the diagnostics paging logic executed — the
  break-glass / `admin_enforced=false` alerts (above) will fire on
  their own through the FastAPI pipeline if the snapshot is degraded.
- A non-2xx response (or a network error) increments a consecutive-
  failure counter persisted in the `RATE_LIMIT` KV namespace under
  `synthetic_probe:state`.
- After **5 consecutive failures** (i.e. the probe has been dark for
  ≥5 minutes) the worker POSTs a JSON alert to
  `SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL` with `alert_type:
  "synthetic_probe_dark"`. This watchdog re-fires every 5 minutes
  while the probe stays dark — a deliberate forcing function so
  on-call cannot snooze the "paging is broken" signal.

**Verification (run after rolling out the secrets):**

```bash
# 1. Force a one-shot run by triggering the cron from wrangler.
pnpm --filter syrabit-edge dlx wrangler dev --test-scheduled
# in another shell:
curl 'http://localhost:8787/__scheduled?cron=*+*+*+*+*'
# Expect a [synthetic-probe] log line with status=200 ok=true.

# 2. From a personal laptop (NOT inside the worker), confirm the
#    service token can reach diagnostics:
curl -sS -i 'https://api.syrabit.ai/api/admin/diagnostics' \
  -H "CF-Access-Client-Id: $SYNTHETIC_PROBE_CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $SYNTHETIC_PROBE_CF_ACCESS_CLIENT_SECRET" \
  -H "Authorization: Bearer $SYNTHETIC_PROBE_ADMIN_JWT"
# Expect HTTP/2 200 with a JSON body containing "cf_access" and "paging".

# 3. Simulate a probe failure: rotate the admin JWT to garbage in the
#    dashboard, wait 6 minutes, confirm the Slack/PagerDuty channel
#    received an `alert_type: "synthetic_probe_dark"` payload, then
#    restore the real JWT.
#
# 3b. Or, automated path (Task #886) — wraps the same dance into one
#     command, with an EXIT-trap that always restores state:
#       cd workers/edge-proxy
#       pnpm run test-fire:watchdog          # ~7 min, asks for confirm
#     Pre-req: `SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL` already set as a
#     Wrangler secret on `syrabit-edge`. The script aborts loudly with
#     the exact `wrangler secret put` command if it isn't.
```

**Rotation procedure (run quarterly or on suspected leak):**

1. **CF Access service token** — Cloudflare Zero Trust →
   `Access → Service Auth → Service Tokens` → `syrabit-synthetic-probe`
   → *Refresh* (creates a new client secret; the client id is stable).
   Within the 24h overlap window, update both
   `SYNTHETIC_PROBE_CF_ACCESS_CLIENT_ID` and
   `SYNTHETIC_PROBE_CF_ACCESS_CLIENT_SECRET` on the worker. Confirm the
   probe still logs `status=200`. Then `Revoke` the old token.
2. **Admin JWT** — generate a new one with the existing helper
   (`pnpm --filter syrabit-backend python scripts/mint_admin_jwt.py
   --sub synthetic-probe --ttl 31536000`), update
   `SYNTHETIC_PROBE_ADMIN_JWT` on the worker, and revoke the previous
   `jti` via the admin sessions table. Note the rotation in the ops log.
3. **Watchdog webhook URL** — rotate via the Slack/PagerDuty UI and
   update `SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL` on the worker.

**Nightly drift check (Task #899 — required, ships in
`.github/workflows/synthetic-probe-secrets-daily.yml`):**

The four secrets above (`SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL`,
`SYNTHETIC_PROBE_CF_ACCESS_CLIENT_ID`,
`SYNTHETIC_PROBE_CF_ACCESS_CLIENT_SECRET`, `SYNTHETIC_PROBE_ADMIN_JWT`)
can be silently removed during an unrelated Wrangler env reshuffle, an
accidental `wrangler secret delete`, or a misapplied env-promotion
script. If `SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL` in particular goes
missing, the next outage looks identical to "everything is healthy"
again — exactly the failure mode Task #877 was triggered by. The
quarterly `pnpm run test-fire:watchdog` drill (Task #886) catches that
at most every ~90 days and only if a human remembers; this nightly
check closes the gap to ≤24h with no human in the loop.

The cron runs at 05:13 UTC daily:

1. `npx wrangler@4 secret list --name syrabit-edge` returns the JSON
   array of secrets bound to the live worker.
2. The job verifies all four `REQUIRED_SECRETS` are present.
3. On drift, it POSTs the missing list to
   `/api/config/synthetic-probe-secrets/missing-alert` on the FastAPI
   backend, which re-dispatches through the existing
   `metrics._dispatch_alert` pipeline (email + webhook + persisted +
   push) with `alert_type: "synthetic_probe_secret_missing"`. The job
   then fails so GitHub's failed-workflow email serves as a secondary
   signal in case the backend POST itself failed.
4. A clean run (all four secrets present) is silent — no Slack post,
   no email, no backend ping.

Required GitHub repo secrets:

- `CLOUDFLARE_API_TOKEN` (already set for the edge-proxy-deploy
  workflow — `Workers Scripts:Edit` is sufficient for `secret list`).
- `CLOUDFLARE_ACCOUNT_ID` (already set).
- `SYNTHETIC_PROBE_SECRETS_CHECK_TOKEN` — long random string
  authenticating the workflow's POST to the backend. Generate with
  `python -c "import secrets;print(secrets.token_urlsafe(48))"` and
  set the same value as `SYNTHETIC_PROBE_SECRETS_CHECK_TOKEN` on the
  FastAPI service (Railway / Cloud Run env). The backend returns 503
  when the env var is unset (fails closed — visible by design).

Optional GitHub repo variable:

- `SYNTHETIC_PROBE_BACKEND_URL` — overrides the default
  `https://api.syrabit.ai` for the alert POST (e.g. staging
  refreshes). Same shape as `CF_WAF_DRIFT_BACKEND_URL`.

Manually trigger the workflow from the Actions tab (`Run workflow`) to
verify the wiring after rolling out the secrets — a green run with the
"all required secrets present" notice means the daily check is live.

If the team needs to take the probe down (e.g. extended planned
maintenance), set `SYNTHETIC_PROBE_DISABLED=true` on the worker — this
pauses the probe within ~10s without deleting any secrets. **Do not
forget to flip it back**: a paused probe means `/api/admin/diagnostics`
paging is dark.

**What break-glass does NOT do:**

- It does **not** bypass the admin JWT check. A leaked break-glass
  token by itself cannot reach admin handlers — the attacker still
  needs valid admin credentials.
- It does **not** disable origin shared-secret enforcement. The Worker
  still injects `X-Origin-Auth`, so the bare Railway URL remains
  unreachable from arbitrary clients.
- It does **not** clear automatically. The on-call must explicitly
  flip the toggle off; the CRITICAL log line on every bypassed
  request and the persistent `cf_access_break_glass_active` alert are
  the forcing functions.

## 8. Triaging a Cloudflare block (Ray ID lookup) — Task #817

When a user reports the **"Sorry, you have been blocked"** interstitial
on syrabit.ai (or any Cloudflare 1xxx error page), they will see a Ray
ID at the bottom of the page (e.g. `Cloudflare Ray ID: 9f14bccc891a6ebf`).
Use the procedure below to identify the firing rule and apply the
minimum-scope fix.

### 8.1 Detect blocks before users complain — and triage by signal first

A second synthetic probe — `workers/edge-proxy/src/cf-block-probe.ts`,
wired into the same `* * * * *` cron as the admin diagnostics probe —
hits `https://syrabit.ai/` from outside the cluster every minute. After
two consecutive failures (default — override with
`CF_BLOCK_PROBE_THRESHOLD`) it fires the watchdog webhook with one of
**two distinct alert types**, depending on what kind of failure was
detected. **Always triage by `alert_type` first** before opening the
WAF console:

| `alert_type`                    | What it means                                                                                                  | First step                                                                                                                       |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `cf_public_block_detected`      | Probe saw a Cloudflare-side mitigation: `cf-mitigated` header, a body marker, or a 403+empty+cf-ray response.   | Run §8.2 with the `last_ray_id` from the alert payload, then jump to §8.3 / §8.4 to apply the override.                          |
| `public_homepage_probe_failed`  | Probe got a non-2xx that does NOT look like a CF block, or a network error. Likely an origin / DNS / CF outage. | Check Railway / Pages dashboards, the CF status page, and the admin `/api/admin/diagnostics` probe state. **Do not** chase WAF rules. |

Detection signals (in priority order):

1. `cf-mitigated:<value>` — the canonical Cloudflare flag. Trusted at
   any HTTP status.
2. `body:<marker>` — well-known interstitial markers ("Sorry, you have
   been blocked", "Cloudflare Ray ID:", `id="cf-error-details"`,
   `Attention Required! | Cloudflare`, etc.). **Only matched on status
   ≥400** so a help-doc snippet on a 200 OK homepage cannot trigger a
   false positive.
3. `status403:empty-body+cf-ray` — defensive fallback for bare CF 403s
   without a body.
4. `non-cf:non-2xx-status:<n>` / `non-cf:fetch-error` — generic
   failures with no CF mitigation evidence; these route to
   `public_homepage_probe_failed`.

State is persisted in the `RATE_LIMIT` KV namespace under
`cf_block_probe:state` (the existing admin probe uses
`synthetic_probe:state` — they do not interfere). Disable in an
emergency with `CF_BLOCK_PROBE_DISABLED=true` in the worker secret.

### 8.2 Look up a Ray ID

```bash
# Run from the project root. Requires CF_ZONE_ID and one of
# CLOUDFLARE_ANALYTICS_TOKEN / CF_ANALYTICS_API_TOKEN (Zone Analytics:Read).
python artifacts/syrabit-backend/scripts/cf_ray_lookup.py 9f14bccc891a6ebf
# Add --json for raw output, --days N to widen the lookback window
# (default 7d; CF caps each query window to 1d so the script walks
# back day-by-day until it finds a match).
```

Output fields:

| field                            | meaning                                                          |
| -------------------------------- | ---------------------------------------------------------------- |
| `source`                         | which CF subsystem fired (see decision tree in §8.3)             |
| `ruleId`                         | the specific rule UUID — needed to override or disable           |
| `description`                    | human-readable rule label (e.g. `949110: Inbound Anomaly Score`) |
| `action`                         | `block`, `challenge`, `managed_challenge`, `jschallenge`         |
| `clientRequestPath`              | URL path that triggered the rule                                 |
| `clientCountryName` / `…ASN…`    | client country + ISP                                             |
| `userAgent`                      | client UA (real browser vs bot)                                  |
| `edgeResponseStatus`             | HTTP status returned (403 for block, 503 for challenge)          |

The same query shape the script issues (in case the script is
unavailable):

```graphql
query RayLookup($zone: String!, $since: Time!, $until: Time!, $ray: String!) {
  viewer {
    zones(filter: { zoneTag: $zone }) {
      firewallEventsAdaptive(
        filter: { datetime_geq: $since, datetime_leq: $until, rayName: $ray }
        limit: 20
        orderBy: [datetime_DESC]
      ) {
        action source ruleId description rayName datetime
        clientCountryName clientASNDescription
        clientRequestPath clientRequestHTTPHost clientRequestHTTPMethodName
        userAgent edgeResponseStatus
      }
    }
  }
}
```

### 8.3 Decision tree by `source`

| `source`             | What it means                                                         | Where to fix                                                                                                                |
| -------------------- | --------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `firewallManaged`    | Cloudflare Managed Ruleset (OWASP CRS, CF Managed Rules)              | **WAF override / exception** scoped to the path or rule ID — see §8.4. Never disable a managed ruleset wholesale.           |
| `rateLimit`          | CF zone-level rate-limit rule                                         | If false-positive, raise the threshold or scope the rule's `expression`. Cross-check our worker's `RATE_LIMIT_RPM` first.   |
| `botManagement`      | Bot Fight Mode / Super Bot Fight Mode                                 | Add a "skip" rule for the user's IP/ASN/UA (in Security → Bots → Configure). Verify the user really is a human first.       |
| `firewallCustom`     | Operator-authored Custom Firewall Rule (Security → WAF → Custom rules)| Edit the rule's predicate to exclude the false-positive surface; do **not** delete the rule.                                |
| `l7ddos`             | CF L7 DDoS Attack Protection                                          | Tune the L7 DDoS sensitivity for the zone in Security → Settings.                                                            |
| `uaBlock`            | Browser Integrity Check / "Block bad UAs"                             | If a real browser is hitting this, file a CF support ticket — do not disable BIC site-wide.                                  |
| `hot` / `securityLevel` | Generic threat-score block driven by zone Security Level setting   | Lower Security Level (Security → Settings) for the affected ASN via a custom rule, not site-wide.                            |
| `access`             | Cloudflare Access (Zero Trust) policy denial                          | Not a "block page" — Access shows a sign-in screen. See §0–§4 of this doc.                                                  |

### 8.4 Apply a Managed-Ruleset override (the most common fix)

A WAF Managed-Rule false positive — the `firewallManaged` source above
— is what fired on Ray `9f14bccc891a6ebf`. The minimum-scope fix is a
**Managed Rule override**, not a global rule disable. Two ways to apply:

**A. Cloudflare dashboard** (recommended — leaves an audit trail in the
zone history).

1. Security → WAF → **Managed rules**.
2. Find the deployed entry for the **Cloudflare OWASP Core Ruleset**
   (the source for rule 949110) and click **Edit**.
3. Under **Custom rules / Skip**, click **Add override**.
4. Set the override:
   - **Override type**: `Skip` (or `Set action: Log` if you want the
     rule to keep firing into analytics without blocking).
   - **Match expression**: `(http.host eq "syrabit.ai" and http.request.uri.path eq "/")`
     — narrowest possible scope. Widen only if the same false positive
     re-fires on additional paths.
   - **Apply to**: this specific rule ID (e.g.
     `6179ae15870a4bb7b2d480d4843b323c` — Inbound Anomaly Score
     Exceeded). **Do NOT** "skip the entire OWASP ruleset".
5. Save with a description like `Task #817 — skip OWASP 949110 on
   homepage GET; false positive on Indian Airtel mobile traffic`.

**B. Rulesets API** (when you need to script it — requires a token with
`Zone:Read` + `Account Rulesets:Edit`, which the current
`CLOUDFLARE_API_TOKEN` does **not** have; mint a scoped one in
Account → API Tokens).

```bash
# 1. Find the deployed managed ruleset binding for this zone:
curl -s -H "Authorization: Bearer $CF_RULESETS_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/rulesets/phases/http_request_firewall_managed/entrypoint" \
  | jq '.result.rules[] | select(.action_parameters.id | tostring | test("owasp"; "i"))'

# 2. PATCH that rule to add a skip override scoped to the homepage:
curl -X PATCH \
  -H "Authorization: Bearer $CF_RULESETS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "execute",
    "action_parameters": {
      "id": "<owasp-ruleset-id-from-step-1>",
      "overrides": {
        "rules": [
          {
            "id": "6179ae15870a4bb7b2d480d4843b323c",
            "enabled": false
          }
        ]
      }
    },
    "expression": "(http.host eq \"syrabit.ai\" and http.request.uri.path eq \"/\")",
    "description": "Task #817 — skip OWASP 949110 on homepage GET"
  }' \
  "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/rulesets/<entrypoint-id>/rules/<rule-binding-id>"
```

### 8.5 Rollback

The override is immediately reversible without a deploy:

- **Dashboard**: Security → WAF → Managed rules → OWASP entry → delete
  the override row added in §8.4.
- **API**: re-issue the PATCH from §8.4 with the `overrides.rules`
  array empty (or delete the entire override binding via DELETE on the
  rule binding id).

The synthetic probe (§8.1) will start passing within 1 minute of the
override being removed; the watchdog auto-clears its consecutive-failure
counter on the first successful probe.

### 8.6 Index of historical incidents

| Ray ID                | Date         | Source            | Rule  | Action taken                                                                       |
| --------------------- | ------------ | ----------------- | ----- | ---------------------------------------------------------------------------------- |
| `9f14bccc891a6ebf`    | 2026-04-24   | `firewallManaged` | 949110 (OWASP Inbound Anomaly Score Exceeded) | Operator added a Managed-Rule override scoped to `http.host eq "syrabit.ai" and http.request.uri.path eq "/"` (per §8.4). cf-block-probe added so the next occurrence pages within 2 minutes instead of waiting for a user report. |
| `9f1537aba88aaa6a` (+ same-day `9f14bccc891a6ebf` re-fire) | 2026-04-24 | `firewallManaged` | 949110 (OWASP Inbound Anomaly Score Exceeded) — `ruleId 6179ae15870a4bb7b2d480d4843b323c` | The path-scoped override added on the previous report was insufficient: a 24h `firewallEventsAdaptive` aggregation showed rule 949110 firing across **every** path (homepage, `/favicon.ico`, `/sw.js`, `/api/analytics/page-view`, `/api/content/library-bundle`, every `/degree/...` page) for IN/Bharti-Airtel desktop Chrome traffic — i.e. it is the OWASP Core "anomaly score exceeded" trip-rule and the false positive is paranoia-level wide, not request-shape narrow. Task #825: globally disabled rule 949110 inside the OWASP binding via the new orchestrator at `scripts/cf_waf_override.py step3` (see §8.7). The underlying CRS detections still run and are scored — only the threshold-trip rule is muted, so genuine attack patterns are still caught by the higher-severity OWASP rules and by the Cloudflare Managed Ruleset. Bot-management rule `874a3e315c344b1281ad4f00046aab6f` ("manage definite bots") was also observed `managed_challenge`-ing legitimate `/api/content/library-bundle` and `/api/seo/sitemap-*.xml` requests in the same window — tracked separately, **not** addressed here. |
| (no single Ray; aggregate finding) | 2026-04-24   | `firewallManaged` | `874a3e315c344b1281ad4f00046aab6f` ("manage definite bots", inside CF Managed Ruleset binding `dc90efabf280440bb8ee2fa1bffbcf59`, deployed ruleset `efb7b8c949ac4650a09736fc376e9aee`) | **Task #826.** Same 24h aggregation that closed #825 also showed this bot rule managed-challenging `/api/content/library-bundle` (legitimate first-party API) and `/api/seo/sitemap-*.xml` (search-engine sitemap fetch) — adds latency for real users and breaks scripted/legitimate API consumers. Fix: instead of disabling the rule globally (#825-style), added a **path-scoped Skip rule** in the `http_request_firewall_custom` phase via `scripts/cf_waf_override.py bot_skip`. The Skip rule's expression is `(starts_with(http.request.uri.path, "/api/") or http.request.uri.path eq "/sitemap.xml")` and it lists rule `874a3e…` under the CF Managed deployed ruleset id. Bot management remains active on every other surface (homepage, `/degree/*`, login, etc.) — only the SEO/API surface is exempted. Post-fix verification: run `python3 scripts/cf_waf_override.py aggregate --rule-id 874a3e315c344b1281ad4f00046aab6f --hours 24` ~24 h after deploy; the gate is path-aware (challenge-style events on non-exempt paths are expected and reported as informational), so success is exit 0 with the "EXEMPT PATHS — challenge-style actions" bucket showing zero rows — see §8.7.6. |

#### Per-Ray evidence captured during Task #825

Both Rays were verified via `python3 artifacts/syrabit-backend/scripts/cf_ray_lookup.py <ray-id>` against the `firewallEventsAdaptive` GraphQL dataset. All fields below come straight from that lookup; no values are inferred.

| Field                | `9f1537aba88aaa6a`                                                        | `9f14bccc891a6ebf`                                                        |
| -------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Datetime (UTC)       | `2026-04-24T12:47:36Z`                                                    | `2026-04-24T11:23:43Z`                                                    |
| Action               | `block`                                                                   | `block`                                                                   |
| Edge response status | `403`                                                                     | `403`                                                                     |
| Source               | `firewallManaged`                                                         | `firewallManaged`                                                         |
| Rule id              | `6179ae15870a4bb7b2d480d4843b323c`                                        | `6179ae15870a4bb7b2d480d4843b323c`                                        |
| Rule description     | `949110: Inbound Anomaly Score Exceeded`                                  | `949110: Inbound Anomaly Score Exceeded`                                  |
| Method               | `GET`                                                                     | `GET`                                                                     |
| Host                 | `syrabit.ai`                                                              | `syrabit.ai`                                                              |
| Path                 | `/`                                                                       | `/`                                                                       |
| Country              | `IN`                                                                      | `IN`                                                                      |
| ASN description      | `Bharti Airtel Ltd. AS for GPRS Service`                                  | `Bharti Airtel Ltd. AS for GPRS Service`                                  |
| User agent           | `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36` | `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36` |

Two real Indian mobile users on Airtel, both on desktop-class Chrome, both blocked on the bare homepage GET. No request-shape pattern; the trigger is purely the user's IP/ASN reputation feeding the OWASP anomaly score over its threshold.

**Conscious tradeoff (tracked here, not just in chat):** disabling OWASP 949110 globally meaningfully reduces anomaly-score enforcement for the entire zone. The reasoning: §8.6 above shows the rule fires across every path with no request-shape commonality, so a path-scoped override would not contain the impact. Higher-severity OWASP rules (paranoia level ≥ 2) and the Cloudflare Managed Ruleset still run and still block — only the *threshold* rule is muted. This should be revalidated quarterly: if Cloudflare ships a calibration fix or if attack volume changes, re-enable 949110 by running `python3 scripts/cf_waf_override.py rollback3`.

#### Aggregate evidence captured during Task #826

For the bot-management rule the trigger was an aggregation, not a single Ray. The same 24h `firewallEventsAdaptive` query that closed #825 (filtered to `ruleId = 874a3e315c344b1281ad4f00046aab6f`) showed every match was either:

- `clientRequestPath` starting with `/api/` (predominantly `/api/content/library-bundle` and `/api/seo/sitemap-*.xml`), or
- `clientRequestPath` equal to `/sitemap.xml`

with `action=managed_challenge`, `source=firewallManaged`, no other request-shape commonality (mix of UAs, mix of countries). That distribution is what justifies the path-scoped Skip rule (§8.7.5) over a global disable: the bot rule is correct on the public-HTML surface and the only false-positive cluster is the SEO/API surface.

**Conscious tradeoff (Task #826, tracked here):** the Skip rule turns off the "definite bots" signal *for `/api/*` and `/sitemap.xml` only*. A determined attacker could plausibly try to abuse the unscored API surface — but every `/api/*` route already sits behind the per-IP rate limit (§7) and the application-layer auth/credit checks, so the marginal protection from this single managed rule on those paths is small. Re-evaluate quarterly: if a real attack profile emerges that uses `/api/*` and bypasses the in-app limits, narrow the exemption (e.g. drop the `/api/admin/*` subpath from `BOT_SKIP_EXPRESSION`) before reverting the Skip rule wholesale.

### 8.7 Orchestrator script — `scripts/cf_waf_override.py`

The runbook curl recipes in §8.4 work but they leave no audit trail of
the pre-change action of each rule, which makes the "restore to
Execute (Block)" step in §8.5 fragile. Task #825 added a Python
orchestrator that wraps the same Cloudflare Ruleset API calls,
remembers the original action of every binding it touches, and
exposes idempotent subcommands for the full incident workflow:

```sh
export CF_WAF_OVERRIDE_TOKEN=<scoped-token>   # see token reqs below
export CF_ZONE_ID=<zone-hex>                  # already in the env

# read-only — safe at any time, prints every binding + override
python artifacts/syrabit-backend/scripts/cf_waf_override.py status

# Step 0 — flip Cloudflare Managed + OWASP bindings to action=log so
# the site is reachable within ~30s. Saves originals to
# scripts/cf_waf_override_state.json so step6 can restore.
python artifacts/syrabit-backend/scripts/cf_waf_override.py step0

# Step 3 — disable the offending OWASP rule (defaults to 949110;
# pass --rule-id <hex> for any other rule).
python artifacts/syrabit-backend/scripts/cf_waf_override.py step3

# Step 4 — change the "Leaked credential check" rate-limit rule from
# action=block to action=managed_challenge.
python artifacts/syrabit-backend/scripts/cf_waf_override.py step4

# Step 6 — restore the entrypoint bindings flipped in step0 back to
# action=execute (read from the state file).
python artifacts/syrabit-backend/scripts/cf_waf_override.py step6

# (Task #826) Path-scoped exemption for the CF Managed bot rule on
# /api/* and /sitemap.xml. Independent of the #825 step sequence —
# safe to run any time after step6, idempotent on re-runs. See
# §8.7.5 for the full body and rationale.
python artifacts/syrabit-backend/scripts/cf_waf_override.py bot_skip

# Re-verify: the OWASP binding should show the new override row in
# action_parameters.overrides.rules, both managed bindings should be
# action=execute again, and the http_request_firewall_custom phase
# should now carry the bot-skip rule.
python artifacts/syrabit-backend/scripts/cf_waf_override.py status
```

Every mutating subcommand accepts `--dry-run` to print the intended
PATCH body without sending it.

**State-file lifecycle (do not commit this file):**
`scripts/cf_waf_override_state.json` is a per-incident operational
artifact, not a durable repo file. It is gitignored on purpose. The
intended lifecycle is:

1. `step0` writes the pre-incident snapshot to the file.
2. `step3` / `step4` append their rollback markers.
3. `step6` consumes the snapshot to restore force-log.
4. **Once the incident is closed** (a `status` run shows the
   steady-state config you intend to leave in place), delete the
   file: `rm artifacts/syrabit-backend/scripts/cf_waf_override_state.json`.

Why "do not commit": if the file from a prior incident is reused
across machines or environments, its `step0_pre_change` snapshot
encodes a moment in time that may no longer match the live binding
state — and step6 would then "restore" stale values. The script's
`step0` includes a freshness check that auto-refreshes the snapshot
when no binding is currently in force-log mode (so you cannot
silently corrupt a new incident with stale data), but that guard is
belt-and-braces. The discipline is still: delete the file at step 4.

For multi-operator scenarios, the file lives on the laptop that ran
`step0`. If a different operator needs to run `step6`, they should
re-run `step0` (which produces an identical force-log result and a
fresh, locally-owned snapshot) rather than copy the file across
machines.

**Rollback subcommands** (always available, regardless of whether
step6 was run):

```sh
# Re-enable the rule disabled by step3
python artifacts/syrabit-backend/scripts/cf_waf_override.py rollback3

# Restore the rate-limit rule's original action (block, in our case)
python artifacts/syrabit-backend/scripts/cf_waf_override.py rollback4

# (Task #826) Delete the bot-skip Skip rule. Idempotent — uses
# description-tag lookup, so it works on a different operator's
# machine without depending on the local state file.
python artifacts/syrabit-backend/scripts/cf_waf_override.py rollback_bot_skip
```

#### 8.7.1 Required token scope (re-read this before §8.4 too)

The Cloudflare Ruleset API requires **Account-level** scope to PATCH
zone-phase rulesets — `Zone:Read` alone is not enough, and none of
the existing tokens on this project (analytics token, Pages token,
default API token) has it. Mint a new token at Cloudflare → My
Profile → API Tokens with **all of**:

| Scope        | Resource  | Permission |
| ------------ | --------- | ---------- |
| Zone         | Zone      | Read       |
| Zone         | WAF       | Edit       |
| Account      | Rulesets  | Edit       |
| Zone         | Analytics | Read       |

Restrict to:
- Account → the syrabit Cloudflare account
- Zone resources → `syrabit.ai` only

Store the value as the secret `CF_WAF_OVERRIDE_TOKEN`. The orchestrator
also accepts the legacy `CLOUDFLARE_API_TOKEN` env name as a fallback,
but only do this if you are **rotating** the existing
`CLOUDFLARE_API_TOKEN` secret to the new scoped value — do not paste
the new token over the analytics-only one if anything else still
expects analytics-only behaviour from `CLOUDFLARE_API_TOKEN`.

#### 8.7.2 Cloudflare API quirks (learned the hard way during Task #825)

Two non-obvious things about the Rulesets API for the
`http_request_firewall_managed` phase, both worth pinning to memory
before you reach for raw `curl`:

1. **You cannot set the binding's primary `action` to anything other
   than `execute`.** The whole point of a managed-ruleset binding is
   "run this Cloudflare-shipped ruleset", so the API rejects
   `action: "log"` (or `block`, `skip`, …) on the binding rule
   itself with `code 20016`. To turn the entire binding into a
   log-only observer (Task #825 step 0) you instead set
   `action_parameters.overrides.action = "log"`, which CF then
   applies as a global rewrite of every fired rule's action. To
   restore (Task #825 step 6) remove that key from `overrides`. The
   orchestrator script does both correctly — this note is for the
   next operator who reaches for `curl` and gets the cryptic
   `code 20016`.

2. **PATCH requires the full rule body, not a partial diff.** Sending
   just `{"action": "..."}` rejects with `code 20125` complaining the
   `expression` is blank. You must echo back `action`,
   `action_parameters`, `expression`, `description`, and `enabled`
   verbatim from a prior GET on the same binding, and only mutate
   the fields you want to change. The orchestrator does this; the
   raw recipes in §8.4 above already do this for the per-rule
   override case but the same constraint applies to every PATCH.

Live rule IDs in the syrabit.ai zone (verified Task #825, 2026-04-24
via `cf_waf_override.py status`):

| Phase                              | Binding rule id                           | Description                          |
| ---------------------------------- | ----------------------------------------- | ------------------------------------ |
| `http_request_firewall_managed`    | `dc90efabf280440bb8ee2fa1bffbcf59`        | Execute Cloudflare Managed Ruleset   |
| `http_request_firewall_managed`    | `7527dda5629f452aabd2323bb3e88346`        | Execute Cloudflare OWASP Core Ruleset |
| `http_ratelimit`                   | `e65222f0dc254546a9a82673727f3999`        | Leaked credential check              |

Cloudflare-published deployed ruleset IDs referenced inside the
bindings above (the Skip Rules `action_parameters.rules` map keys
off these — see §8.7.5):

| Deployed ruleset                | Deployed ruleset id                       |
| ------------------------------- | ----------------------------------------- |
| Cloudflare Managed Ruleset      | `efb7b8c949ac4650a09736fc376e9aee`        |
| Cloudflare OWASP Core Ruleset   | `4814384a9e5d4991b9815dcfc25d2f1f`        |

Notable rules inside those deployed rulesets that the orchestrator
targets by id (these are stable across CF tenants — same hex on
every zone):

| Rule id                                  | Lives in                  | Description                                 |
| ---------------------------------------- | ------------------------- | ------------------------------------------- |
| `6179ae15870a4bb7b2d480d4843b323c`       | OWASP Core (CRS) ruleset  | 949110 Inbound Anomaly Score Exceeded (Task #825 trip rule) |
| `874a3e315c344b1281ad4f00046aab6f`       | CF Managed Ruleset        | "Manage definite bots" (Task #826 false-positive rule)      |

Entrypoint ruleset ids (also stable):

| Phase                              | Entrypoint ruleset id                     |
| ---------------------------------- | ----------------------------------------- |
| `http_request_firewall_managed`    | `e354a966535944f6bfa99255b57a71bf`        |
| `http_ratelimit`                   | `81ad2683e67744448bbb3d634b85d34f`        |
| `http_request_firewall_custom`     | created on first `bot_skip` run (Task #826 — id printed by the script; record it here on first deploy) |

These ids are recorded here for the next incident — the orchestrator
discovers them dynamically every run, but having them in the runbook
saves a `status` round-trip when you only need a one-line `curl`.

#### 8.7.3 Mandatory verification gate before step6

**Do not skip this.** The orchestrator now enforces it (step6 will
abort with a precondition error otherwise), but the gate is here in
prose too because the gate's whole purpose is to make you stop and
read the live state with your eyes before lifting force-log:

1. Run `python3 scripts/cf_waf_override.py status`.
2. Confirm that under the OWASP Core Ruleset binding you see a line
   like `override rule=<trip-rule-id> enabled=False action=None`.
   That is step3's per-rule disable — without it, lifting force-log
   re-enables the false-positive rule at the same instant.
3. Only then run `python3 scripts/cf_waf_override.py step6`.

If step6 aborts with "OWASP rule … is NOT currently disabled", do
NOT use `--force` to bypass it — that flag exists only for the
narrow case where you have hand-applied an equivalent fix in the
dashboard that the orchestrator can't see. In all other cases the
correct response is to re-run `step3 --rule-id <trip-rule-id>` and
verify, then re-run step6.

#### 8.7.4 Continuous drift detection — `verify` subcommand

After step6 (and after `bot_skip` in §8.7.5), run
`python3 scripts/cf_waf_override.py verify` to assert the steady-
state config in one command. It checks five invariants and exits
non-zero on any drift:

1. CF Managed Ruleset binding is `action=execute`, not in force-log,
   not disabled.
2. OWASP Core Ruleset binding is `action=execute`, not in force-log,
   not disabled.
3. The OWASP binding still carries a per-rule override disabling the
   trip rule (default = the Task #825 rule, override via
   `--expect-disabled-rule`).
4. The "Leaked credential check" rate-limit rule is
   `action=managed_challenge` (not `block`), and is enabled.
5. **(Task #826)** The `http_request_firewall_custom` phase carries
   the bot-management Skip rule (looked up by description-tag
   `Task #826`) and that rule lists the bot-management rule id from
   `--expect-skip-bot-rule` (default
   `874a3e315c344b1281ad4f00046aab6f`) under the CF Managed
   deployed ruleset id, with `enabled=true`. Pass
   `--no-check-bot-skip` to relax this single invariant **only**
   when intentionally rolling back to the pre-#826 baseline; the
   verify output then prints `[SKIP] …` on that line so the omission
   is still visible in incident tickets.

Wire this into a daily cron / CI job so silent drift (a teammate
re-enabling 949110 in the dashboard, an external script stomping the
rate-limit rule, deleting the bot-skip Skip rule from the dashboard,
etc.) pages immediately rather than waiting for the next user
report. Output is line-per-invariant `[PASS]` / `[FAIL]` / `[SKIP]`,
suitable for pasting into incident tickets.

The cron is wired up by the GitHub Actions workflow
`.github/workflows/cf-waf-drift-daily.yml` (Task #828). See §8.7.7
for setup and the alert-routing contract.

**Archived `verify` output, Task #825 close-out (2026-04-24, immediately
after step6):**

```text
=== Steady-state invariant verification ===
  [PASS] CF Managed Ruleset binding action=execute, no force-log
  [PASS] OWASP binding action=execute, no force-log
  [PASS] OWASP binding has trip-rule 6179ae15870a4bb7b2d480d4843b323c disabled
  [PASS] Leaked-credential rate-limit rule action=managed_challenge

verify: all invariants hold.
```

This is the canonical post-incident steady state for #825. Any
future drift detected by `verify` should be compared against this
archive before deciding whether to roll forward (re-apply step3 /
step4) or roll back (`rollback3` / `rollback4`).

**Expected `verify` output after Task #826 close-out** (the
canonical steady state going forward — invariant 5 is now part of
it):

```text
=== Steady-state invariant verification ===
  [PASS] CF Managed Ruleset binding action=execute, no force-log
  [PASS] OWASP binding action=execute, no force-log
  [PASS] OWASP binding has trip-rule 6179ae15870a4bb7b2d480d4843b323c disabled
  [PASS] Leaked-credential rate-limit rule action=managed_challenge
  [PASS] Skip rule disables bot rule 874a3e315c344b1281ad4f00046aab6f on safe paths

verify: all invariants hold.
```

The 2026-04-24 archive above does **not** include the invariant-5
PASS line because the Skip rule did not yet exist when that snapshot
was taken; archive the new five-line output the first time you run
`verify` after `bot_skip` lands in production.

#### 8.7.5 Path-scoped bot-rule exemption — `bot_skip` subcommand (Task #826)

The bot-management rule `874a3e315c344b1281ad4f00046aab6f` ("manage
definite bots", inside the Cloudflare Managed Ruleset binding) was
observed `managed_challenge`-ing legitimate `/api/*` and
`/sitemap.xml` traffic during the Task #825 24h aggregation. Rather
than disable the rule globally (#825-style), Task #826 adds a
path-scoped Skip Rules custom-phase rule:

```sh
export CF_WAF_OVERRIDE_TOKEN=<scoped-token>
export CF_ZONE_ID=<zone-hex>

# Inspect first — confirms the bot rule binding and any pre-existing
# rules in the http_request_firewall_custom phase.
python3 artifacts/syrabit-backend/scripts/cf_waf_override.py status

# Apply the Skip rule. If the http_request_firewall_custom
# entrypoint does not exist yet (fresh zone), it is created.
python3 artifacts/syrabit-backend/scripts/cf_waf_override.py bot_skip
# → bot_skip: added new skip rule <rid> (managed-rule 874a3e…) on
#   custom-firewall entrypoint <eid>

# Verify (invariant 5 now PASSes)
python3 artifacts/syrabit-backend/scripts/cf_waf_override.py verify
```

The Skip rule body the orchestrator submits (kept here for paste-
into-incident-ticket reference; the orchestrator is the source of
truth at `BOT_SKIP_EXPRESSION` / `BOT_SKIP_DESCRIPTION`):

```json
{
  "action": "skip",
  "action_parameters": {
    "rules": {
      "efb7b8c949ac4650a09736fc376e9aee": [
        "874a3e315c344b1281ad4f00046aab6f"
      ]
    }
  },
  "expression": "(starts_with(http.request.uri.path, \"/api/\") or http.request.uri.path eq \"/sitemap.xml\")",
  "description": "Task #826: skip CF managed bot rule 874a3e315c344b1281ad4f00046aab6f for /api/* and /sitemap.xml (legitimate-traffic false-positive exemption)",
  "enabled": true
}
```

Two things worth pinning to memory:

1. **The `action_parameters.rules` map is keyed by the *deployed*
   ruleset id**, not the per-zone binding id. CF's Skip semantics
   are "when ruleset X is about to fire, skip rule Y inside it" —
   independent of how X is deployed in the zone. The deployed id
   for the CF Managed Ruleset is `efb7b8c949ac4650a09736fc376e9aee`
   (see §8.7.2). Using the binding id `dc90efabf280440bb8ee2fa1bffbcf59`
   here would silently no-op.
2. **Idempotency is anchored on the description-tag substring**
   `"Task #826"`. Re-running `bot_skip` PATCHes the existing rule
   rather than appending a duplicate; `rollback_bot_skip` finds and
   deletes the rule by the same tag, so it works on a different
   operator's machine without depending on the local state file.

To roll back (re-enables the bot rule on `/api/*` and `/sitemap.xml`):

```sh
python3 artifacts/syrabit-backend/scripts/cf_waf_override.py rollback_bot_skip
```

If you need to widen the exemption (e.g. add `/sitemap-index.xml`),
edit `BOT_SKIP_EXPRESSION` in the script and re-run `bot_skip`. The
PATCH will update the existing rule's expression in place.

#### 8.7.6 24-hour post-fix verification gate — `aggregate` subcommand

The Task #826 done-criterion ("a 24-hour post-fix WAF event
aggregation confirms the rule no longer fires on the affected
paths") is enforced by the `aggregate` subcommand:

```sh
# 24h after deploying bot_skip — gate is path-aware:
# success = exit 0 with the "EXEMPT PATHS — challenge-style
# actions" bucket showing zero rows. Either "0 events matched.
# Fix held." (rule was completely silent in the window) or
# "Fix held: zero challenge-style events on exempt paths …"
# (with non-exempt activity reported as informational).
python3 artifacts/syrabit-backend/scripts/cf_waf_override.py \
    aggregate --rule-id 874a3e315c344b1281ad4f00046aab6f --hours 24
```

Behaviour (the aggregator is **path-aware** — it knows about the
exemption expression and only fails when challenge-style events
land on the *exempt* paths; non-exempt-path activity is reported
as informational because bot management is intentionally still
active there):

- Exit 0 — **The fix held.** Either zero matches at all, or all
  matches are on non-exempt paths and/or are non-challenge
  actions (`skip`, `log`, `allow`). The output prints a
  bucketed breakdown:
  - "EXEMPT PATHS — non-challenge actions" — the post-fix steady
    state for `/api/*` and `/sitemap.xml` (skip/log only).
  - "NON-EXEMPT PATHS — challenge-style actions" — bot
    protection still working correctly on homepage, `/degree/*`,
    login, etc. **This subtotal will normally be in the
    thousands per day; that is correct behaviour, not a
    regression.**
  Archive the bucket subtotals into the incident ticket.
- Exit 1 — **Fix BROKEN.** One or more challenge-style events
  (`managed_challenge`, `challenge`, `js_challenge`, `block`)
  landed on an exempt path. The "EXEMPT PATHS — challenge-style
  actions (FIX BROKEN if any)" bucket lists exactly which
  exempt paths and counts. Re-run `status` and confirm the
  Skip rule is enabled, and references the bot rule under the
  CF Managed deployed-ruleset id (not the binding id — see
  §8.7.2).
- Exit 2 — configuration / network error (e.g. token missing,
  DNS failure). The error goes to stderr; safe to retry.

Override the exemption set with `--exempt-prefix` (repeatable)
and `--exempt-exact` (repeatable) when re-using `aggregate` for a
different rule (e.g. verifying a future Task #82x exemption). The
defaults — `['/api/']` prefix and `/sitemap.xml` exact — mirror
`BOT_SKIP_EXPRESSION` exactly, so the no-flag invocation in the
cron above is correct for the Task #826 close-out.

`aggregate` is read-only — it reuses the same GraphQL endpoint
`cf_ray_lookup.py` uses, but queries the *Groups* variant for
count-by-dimension aggregation. The token only needs Zone
Analytics:Read for this subcommand (the existing `CF_WAF_OVERRIDE_TOKEN`
already includes that scope; no separate token needed).

Wire `aggregate` into the same daily cron as `verify` so a
regression (e.g. someone deletes the Skip rule from the dashboard
and the bot rule starts challenging `/api/*` again) pages within
24 h instead of waiting for a user report. The cron is wired up by
the GitHub Actions workflow `.github/workflows/cf-waf-drift-daily.yml`
(Task #828) — see §8.7.7 for setup. The plain-cron form (kept here
for an out-of-band operator who needs to mirror the same gate from a
host without GitHub Actions access) is:

```cron
17 4 * * *  cd /opt/syrabit-backend && \
    CF_WAF_OVERRIDE_TOKEN=… CF_ZONE_ID=… \
    python3 scripts/cf_waf_override.py aggregate \
        --rule-id 874a3e315c344b1281ad4f00046aab6f --hours 24 \
    || /usr/local/bin/page-oncall "bot-skip drift detected"
```

**Operator gate (do this once when Task #826 is being deployed):**

1. Run `bot_skip` in production.
2. Wait ~24 h.
3. Run `aggregate --rule-id 874a3e315c344b1281ad4f00046aab6f --hours 24`.
4. If exit 0, archive the bucket subtotals (especially
   "EXEMPT PATHS — non-challenge actions" and "NON-EXEMPT PATHS
   — challenge-style actions") under §8.6 in the row for #826,
   alongside the #825 close-out archive in §8.7.4. If exit 1,
   inspect the "EXEMPT PATHS — challenge-style actions" bucket
   to see which exempt path is still being challenged and either
   widen `BOT_SKIP_EXPRESSION` (if a legitimate path was missed)
   or fix the Skip rule's deployed-ruleset-id mapping (if every
   exempt path is still firing — usually means the Skip rule was
   keyed off the binding id instead of the deployed-ruleset id).

#### 8.7.7 Daily drift detection workflow — `cf-waf-drift-daily` (Task #828)

The §8.7.4 (`verify`) and §8.7.6 (`aggregate`) gates were both
operator-driven until Task #828. Drift introduced via the Cloudflare
dashboard (a teammate re-enabling 949110, deleting the bot-skip Skip
rule, demoting the leaked-credential rate-limit rule back to `block`,
etc.) used to surface only when a user complained. The GitHub Actions
workflow `.github/workflows/cf-waf-drift-daily.yml` runs both gates
once per day in production and pages on-call within 24 h instead.

**What it runs**

- `python3 scripts/cf_waf_override.py verify` — full 5-invariant
  check from §8.7.4.
- `python3 scripts/cf_waf_override.py aggregate
  --rule-id 874a3e315c344b1281ad4f00046aab6f --hours 24` — the
  path-aware 24h post-fix gate from §8.7.6.

The job intentionally captures both exit codes before deciding
whether to fail (`set +e` + `${PIPESTATUS[0]}`), so a single Slack
alert carries both outputs rather than masking the second signal
when the first fails.

**Cadence and cron offset**

`schedule: 47 4 * * *` (04:47 UTC daily). Offset off both
`edge-cache-live` (03:17 UTC) and `trustpilot-aggregate-refresh`
(04:30 UTC) so a Slack burst from one workflow doesn't bury this
one, and away from the top-of-hour GitHub cron stampede. Manual
runs via `workflow_dispatch` accept overrides for `--hours` and
`--rule-id` (used when verifying a future Task #82x exemption
without editing the workflow file).

**Required repo secrets**

| Secret                       | Required | Notes                                                                                          |
| ---------------------------- | -------- | ---------------------------------------------------------------------------------------------- |
| `CF_WAF_OVERRIDE_TOKEN`      | yes      | Same scoped token §8.7 documents (Zone:Read + WAF:Edit + Account Rulesets:Edit + Zone Analytics:Read). |
| `CF_ZONE_ID`                 | yes      | syrabit.ai zone id — same value the analytics setup already uses.                              |
| `CF_WAF_DRIFT_SLACK_WEBHOOK` | optional | Slack incoming-webhook URL. When unset, the job still fails on drift but no Slack post is made. |

Add them in Settings → Secrets and variables → Actions on the
GitHub repository. The "Guard required secrets" step in the
workflow fails fast with a clear annotation if either of the two
required secrets is missing, instead of bottoming out as an opaque
401 from Cloudflare.

**Alerting contract**

- **Success path:** both subcommands exit 0. The job posts no
  Slack message and ends green. `::notice` annotations record
  "all 5 invariants hold" and "fix held" so a successful run is
  still inspectable in the Actions UI without producing inbox
  spam.
- **Drift path:** if `verify` rc != 0 (drift) OR `aggregate` rc
  != 0 (rc 1 = drift on exempt path; rc 2 = transient CF GraphQL
  / config error), the workflow posts a single Slack message to
  `CF_WAF_DRIFT_SLACK_WEBHOOK` containing both exit codes, the
  last 1500 chars of each subcommand's stdout, and a deep link
  to the GitHub Actions run. The job then fails its final step,
  so GitHub's built-in failed-workflow email goes to repo admins
  as a secondary signal even when Slack is misconfigured.
- **Slack misconfigured:** the Slack step logs a `::warning::`
  and exits 0; the job still fails on the final step, so the
  GitHub failed-workflow email path is preserved as a fallback.
- **Transient errors (`aggregate` rc=2):** treated as a failure
  on purpose. A repeated rc=2 across consecutive days is the only
  way to distinguish a CF API outage from a misconfigured token
  — masking it would let token-rotation drift sit silently.

**Manual smoke test before relying on it**

After adding the secrets, trigger the workflow manually from the
Actions tab once to confirm:

1. The "Guard required secrets" step passes.
2. `verify` prints all 5 `[PASS]` lines (matches the §8.7.4
   archived steady state).
3. `aggregate` prints "Fix held: zero challenge-style events on
   exempt paths …" or "0 events matched. Fix held." (matches the
   §8.7.6 success contract).
4. The job ends green with no Slack message.

To smoke-test the alert path itself without disturbing
production, run the workflow with a deliberately unrelated
`--rule-id` override that has zero events: this still exits 0,
so the Slack code path stays exercise-via-rollback (e.g.
temporarily disable the bot-skip rule in a staging zone with the
same shape, then re-run); do **not** flip a real production
invariant to test the alert.

**Rollback**

If the daily run is producing false positives (e.g. during a
planned dashboard change), disable the schedule by editing the
`on.schedule` block out of the workflow file or by toggling the
workflow off in the Actions tab. The job's `workflow_dispatch`
trigger remains available for ad-hoc verification while the
schedule is off. Re-enable as soon as the planned change is
re-snapshotted into §8.7.4.

**Silent-cron heartbeat (Task #831)**

The Slack/email alerting above only works when the cron actually
fires. If GitHub Actions silently stops scheduling the workflow —
repo archived, workflow disabled in the Actions tab, GitHub-side
outage, account billing lapse, secret expiry that prevents
checkout — drift surfaces only when a user complains, exactly the
failure mode this whole section was meant to remove.

To catch that, the workflow has an `if: always()` "Heartbeat to
backend (Task #831)" step that POSTs to
`POST /api/config/cf-waf-drift/heartbeat` on every run regardless
of `verify` / `aggregate` outcome. The body shape is:

```json
{
  "status": "success" | "drift" | "transport_error" | "failure",
  "verifyRc":    "0" | "1" | "2" | "",
  "aggregateRc": "0" | "1" | "2" | "",
  "runUrl":   "https://github.com/<org>/<repo>/actions/runs/<id>",
  "workflowUrl": "https://github.com/<org>/<repo>/actions/workflows/cf-waf-drift-daily.yml",
  "runId": "<id>"
}
```

Status reduction (`(verify_rc, aggregate_rc) → status`):

- `(0, 0)` → `success` (clean pass)
- `(1, *)` or `(*, 1)` → `drift` (per-run Slack alert
  already covered this; heartbeat just proves the cron ran)
- `(2, *)` or `(*, 2)` → `transport_error` (CF GraphQL/network
  transient — re-run will likely succeed)
- unset / other → `failure` (workflow plumbing itself broke
  before producing an rc)

The backend persists the latest heartbeat to a `job_locks` doc
(`cf_waf_drift_cron_health`). A leader-gated alerter
(`routes/admin_cf_waf_drift_cron_alerts.py`) polls hourly and
pages admins via in-app notification + email when no heartbeat
has arrived in >36h. The 36h threshold gives a 10h grace window
for a single missed run (transient GitHub outage) before
scolding; re-pages cap at one per 24h while still silent. A
freshly-deployed backend gets a 48h bootstrap grace window
before alerting "never observed" so a deploy that lands on the
wrong side of the daily cron doesn't immediately page.

| Secret / env var (BACKEND)        | Required | Notes                                                                                                                            |
| --------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `CF_WAF_DRIFT_HEARTBEAT_SECRET`   | yes      | Same value as the GitHub repo secret of the same name. When unset on the backend the heartbeat endpoint returns 503 (fail-closed). |
| `CF_WAF_DRIFT_SLACK_WEBHOOK`      | optional | Task #834 — when set, the silent-cron alerter also POSTs the silence + recovered alert to this Slack incoming-webhook (same value as the repo secret of the same name used by the per-run drift alert). When unset the email + in-app channels still fire unchanged. |

| Secret / variable (GITHUB)        | Required | Notes                                                                                                                            |
| --------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `CF_WAF_DRIFT_HEARTBEAT_SECRET`   | yes      | Repo secret. Same value as the backend env var.                                                                                  |
| `CF_WAF_DRIFT_BACKEND_URL` (var)  | optional | Override `https://api.syrabit.ai` (e.g. for staging refreshes).                                                                  |

Set both sides at the same time. If only the backend has the
secret, the workflow's heartbeat step logs a `::warning::` and
exits 0 — the workflow itself stays green but the silent-cron
alerter will eventually classify the cron as silent and page.
If only the GitHub side has it, the backend rejects with 401
and the alerter never sees a heartbeat (same end state).

Operator surface:

- `GET /admin/health/cf-waf-drift/cron` (admin-only) returns
  the same snapshot reduced to a status pill (`healthy`,
  `degraded`, `silent`, `never_observed`, `not_configured`).
  The dashboard branches on `status`. `degraded` here means
  "recent heartbeat reports drift / transport_error / non-zero
  rc" — the per-run Slack alert already paged on that, so the
  silence alerter does NOT re-page; the pill just informs ops.
- An in-app admin notification with `meta.kind =
  "cf_waf_drift_cron_alert"` lands in the notifications
  inbox each time the alerter fires (one per silent → silent
  re-page, one on silent → recovered).
- Same admins-list email template as the Trustpilot
  refresh-cron alerter (Task #751).
- Task #834 also fans the silence + recovered alert out to the
  same `CF_WAF_DRIFT_SLACK_WEBHOOK` Slack channel the per-run
  drift alert (Task #828) posts to, so operators can watch one
  channel for both "the cron ran and found drift" and "the
  cron stopped running entirely." Best-effort and gated on the
  env var being set on the backend — when the webhook is unset
  the email + in-app channels above still fire unchanged, and a
  failed POST is logged but never duplicates the alert or breaks
  the loop. The Slack body mirrors the per-run drift alert's
  `:rotating_light:` block style (`:white_check_mark:` on
  recovery) so the channel reads consistently.

Tunables (env vars on the backend):

- `CF_WAF_DRIFT_CRON_SILENT_THRESHOLD_S` (default `36 * 3600`)
- `CF_WAF_DRIFT_CRON_REALERT_INTERVAL_S` (default `24 * 3600`)
- `CF_WAF_DRIFT_CRON_LOOP_SLEEP_S` (default `3600`)
- `CF_WAF_DRIFT_CRON_WARMUP_S` (default `900`)
- `CF_WAF_DRIFT_CRON_BOOTSTRAP_GRACE_S` (default `48 * 3600`)

Smoke test after rollout: trigger `cf-waf-drift-daily` manually
and confirm:

1. The "Heartbeat to backend (Task #831)" step logs
   `[heartbeat] status=success http=200`.
2. `GET /admin/health/cf-waf-drift/cron` flips from
   `never_observed` → `healthy`, `lastHeartbeatAgeSeconds` is
   small, `lastVerifyRc=0` and `lastAggregateRc=0`.
3. To exercise the silence path without waiting 36h, temporarily
   set `CF_WAF_DRIFT_CRON_SILENT_THRESHOLD_S=60` and
   `CF_WAF_DRIFT_CRON_LOOP_SLEEP_S=30` on the backend, wait for
   one loop iteration past the threshold, observe the in-app
   notification, then revert. Do **not** test by disabling the
   workflow in production — that would lose drift coverage
   during the test window.

**Sibling silence-alerter Slack webhooks (Task #963)**

The cf-waf-drift silence alerter above is one of three sibling
silence alerters that all fan out to Slack via the same
"env-var-per-alerter, no-op when unset" contract. Each alerter
keeps email + in-app notifications as the primary channels and
treats Slack as a third best-effort channel — a missing or
empty webhook env var skips the POST silently, a failed POST is
logged but never duplicates the alert or breaks the loop, and
the URL itself is never published on the admin health JSON
(only the `slackConfigured` boolean + the env var name, so the
AdminHealth pill can render a "Slack ✓ / ✗" badge without
leaking the secret).

Point all three at the same on-call channel today; split them
per-channel later by setting different webhook URLs without any
code change:

| Backend env var                      | Alerter                                                                | Notes                                                                                                      |
| ------------------------------------ | ---------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `CF_WAF_DRIFT_SLACK_WEBHOOK`         | `routes/admin_cf_waf_drift_cron_alerts.py` (Task #834)                 | Same value as the GitHub repo secret of the same name used by the per-run drift alert (cf-waf-drift-daily). |
| `EDGE_PROXY_DEPLOY_SLACK_WEBHOOK`    | `routes/admin_edge_proxy_deploy_cron_alerts.py` (Task #882 alerter)    | Cron-silence + recovery pages for the `edge-proxy-deploy` GitHub Actions workflow.                          |
| `UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK` | `routes/admin_logs_cf_pull_silence_alerts.py` (Tasks #951 / #957)      | Silence + recovery pages for the unified-logs Cloudflare GraphQL pull (Task #963 — listed here so on-call sets it at the same time as the two siblings; otherwise the Slack fan-out added in Task #957 stays a no-op). |

When any of the three is unset on the backend, that alerter's
email + in-app channels still fire unchanged — the AdminHealth
pill's "Slack ✗" badge is the operator-visible signal that the
fan-out is missing. Configure the new var the same way you'd
configure the two siblings: in the backend deploy environment
(Railway), pointing at the same Slack incoming webhook URL.

## 9. What is **not** in scope here

- WARP enrollment of every team device (separate task; required before
  enforcing device-posture rules in step 1).
- Replacing the admin login form with Sign-in-with-Cloudflare
  (planned follow-up — currently the admin password stays as a second
  factor behind Access).
- Migrating end-user student auth to Access (out of scope; students
  stay on the existing Google OAuth + JWT flow).
